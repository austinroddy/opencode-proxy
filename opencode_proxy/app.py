from __future__ import annotations

import json
import time
import uuid
from argparse import ArgumentParser
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from opencode_proxy.config import Config, load_config
from opencode_proxy.opencode_config import (
    full_config,
    merge_config,
    opencode_config_path,
    read_opencode_config,
    write_opencode_config,
)

settings: Config | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings is None:
        set_config(load_config())
    yield


app = FastAPI(title="opencode-proxy", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "upstream": str(config().upstream.base_url)}


@app.get("/v1/models")
async def models(request: Request) -> dict[str, Any]:
    require_client_auth(request)
    async with httpx.AsyncClient(timeout=config().upstream.request_timeout_seconds) as client:
        response = await client.get(upstream("/api/tags"), headers=upstream_headers())
    if response.status_code >= 400:
        raise upstream_error(response)
    return {
        "object": "list",
        "data": [
            {
                "id": model.get("name") or model.get("model"),
                "object": "model",
                "created": ollama_created(model.get("modified_at")),
                "owned_by": "ollama",
            }
            for model in response.json().get("models", [])
            if model.get("name") or model.get("model")
        ],
    }


@app.post("/v1/chat/completions", response_model=None)
async def chat_completions(request: Request):
    require_client_auth(request)
    body = await request.json()
    ollama_body = to_ollama_chat(body)
    if body.get("stream", False):
        return StreamingResponse(stream_chat(ollama_body), media_type="text/event-stream")
    return JSONResponse(await complete_chat(ollama_body))


async def complete_chat(body: dict[str, Any]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=config().upstream.request_timeout_seconds) as client:
        response = await client.post(upstream("/api/chat"), headers=upstream_headers(), json={**body, "stream": False})
    if response.status_code >= 400:
        raise upstream_error(response)
    return openai_completion(response.json(), body["model"])


async def stream_chat(body: dict[str, Any]) -> AsyncIterator[bytes]:
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    async with (
        httpx.AsyncClient(timeout=config().upstream.request_timeout_seconds) as client,
        client.stream(
            "POST",
            upstream("/api/chat"),
            headers=upstream_headers(),
            json={**body, "stream": True},
        ) as response,
    ):
        if response.status_code >= 400:
            error = await response.aread()
            yield sse(
                {
                    "error": {
                        "message": error.decode("utf-8", "replace"),
                        "type": "upstream_error",
                        "code": response.status_code,
                    }
                }
            )
            yield b"data: [DONE]\n\n"
            return

        async for line in response.aiter_lines():
            if not line.strip():
                continue
            chunk = json.loads(line)
            if chunk.get("done"):
                yield sse(
                    {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": body["model"],
                        "choices": [
                            {
                                "index": 0,
                                "delta": {},
                                "finish_reason": finish_reason(chunk.get("done_reason")),
                            }
                        ],
                        **usage(chunk),
                    }
                )
                yield b"data: [DONE]\n\n"
                return

            yield sse(
                {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": body["model"],
                    "choices": [
                        {
                            "index": 0,
                            "delta": message_delta(chunk.get("message", {})),
                            "finish_reason": None,
                        }
                    ],
                }
            )


def to_ollama_chat(body: dict[str, Any]) -> dict[str, Any]:
    model = body.get("model") or config().defaults.model
    if not model:
        raise HTTPException(status_code=400, detail="Missing required field: model")

    return strip_none(
        {
            "model": model,
            "messages": [to_ollama_message(message) for message in body.get("messages", [])],
            "tools": body.get("tools"),
            "format": body.get("response_format", {}).get("type")
            if isinstance(body.get("response_format"), dict)
            else None,
            "options": strip_none(
                {
                    "temperature": body.get("temperature"),
                    "top_p": body.get("top_p"),
                    "num_predict": body.get("max_tokens") or body.get("max_completion_tokens"),
                    "stop": body.get("stop"),
                }
            ),
        }
    )


def to_ollama_message(message: dict[str, Any]) -> dict[str, Any]:
    content = message.get("content", "")
    return strip_none(
        {
            "role": message.get("role"),
            "content": text_content(content),
            "images": image_content(content),
            "tool_calls": message.get("tool_calls"),
        }
    )


def text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") in {"text", "input_text"}
        )
    return "" if content is None else str(content)


def image_content(content: Any) -> list[str] | None:
    if not isinstance(content, list):
        return None
    images = [
        part.get("image_url", {}).get("url", "").split(",", 1)[-1]
        for part in content
        if isinstance(part, dict) and part.get("type") == "image_url"
    ]
    return images or None


def openai_completion(chunk: dict[str, Any], model: str) -> dict[str, Any]:
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": chunk.get("message", {}).get("role", "assistant"),
                    "content": chunk.get("message", {}).get("content", ""),
                    **tool_calls(chunk.get("message", {})),
                },
                "finish_reason": finish_reason(chunk.get("done_reason")),
            }
        ],
        **usage(chunk),
    }


def message_delta(message: dict[str, Any]) -> dict[str, Any]:
    return strip_none(
        {
            "role": message.get("role"),
            "content": message.get("content"),
            **tool_calls(message),
        }
    )


def tool_calls(message: dict[str, Any]) -> dict[str, Any]:
    if not message.get("tool_calls"):
        return {}
    return {"tool_calls": message["tool_calls"]}


def usage(chunk: dict[str, Any]) -> dict[str, Any]:
    if "prompt_eval_count" not in chunk and "eval_count" not in chunk:
        return {}
    return {
        "usage": {
            "prompt_tokens": chunk.get("prompt_eval_count", 0),
            "completion_tokens": chunk.get("eval_count", 0),
            "total_tokens": chunk.get("prompt_eval_count", 0) + chunk.get("eval_count", 0),
        }
    }


def finish_reason(reason: Any) -> str:
    if reason == "stop" or reason is None:
        return "stop"
    if reason == "length":
        return "length"
    return str(reason)


def strip_none(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item is not None and item != {}}


def sse(data: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(data, separators=(',', ':'))}\n\n".encode()


def upstream(path: str) -> str:
    return f"{str(config().upstream.base_url).rstrip('/')}{path}"


def upstream_headers() -> dict[str, str]:
    if not config().upstream.api_key:
        return {}
    return {"Authorization": f"Bearer {config().upstream.api_key}"}


def require_client_auth(request: Request) -> None:
    if not config().auth.client_api_keys:
        return
    token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if token in config().auth.client_api_keys:
        return
    if request.headers.get("x-api-key") in config().auth.client_api_keys:
        return
    raise HTTPException(status_code=401, detail="Invalid or missing proxy API key")


def upstream_error(response: httpx.Response) -> HTTPException:
    return HTTPException(
        status_code=response.status_code,
        detail={"upstream_status": response.status_code, "body": response.text},
    )


def ollama_created(value: Any) -> int:
    if not isinstance(value, str):
        return 0
    try:
        return int(time.mktime(time.strptime(value.split(".")[0], "%Y-%m-%dT%H:%M:%S")))
    except ValueError:
        return 0


def set_config(config: Config) -> None:
    global settings
    settings = config


def config() -> Config:
    if settings is None:
        set_config(load_config())
    return settings


def main() -> None:
    parser = ArgumentParser(description="Run an OpenAI-compatible proxy for an Ollama-shaped upstream.")
    parser.add_argument("--config", default="opp.toml", help="Path to opp.toml")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose uvicorn logging")
    subcommands = parser.add_subparsers(dest="command")

    serve = subcommands.add_parser("serve", help="Run the proxy server")
    serve.add_argument("--config", default="opp.toml", help="Path to opp.toml")
    serve.add_argument("--verbose", action="store_true", help="Enable verbose uvicorn logging")

    print_config = subcommands.add_parser("print-opencode-config", help="Print generated opencode config JSON")
    print_config.add_argument("--config", default="opp.toml", help="Path to opp.toml")

    setup = subcommands.add_parser("setup-opencode", help="Merge generated config into an opencode config file")
    setup.add_argument("--config", default="opp.toml", help="Path to opp.toml")
    setup.add_argument("--opencode-config", default=str(opencode_config_path()), help="Path to opencode.json")
    setup.add_argument("--print", action="store_true", help="Print the merged config after writing")

    args = parser.parse_args()
    if args.command in {"print-opencode-config", "setup-opencode"}:
        run_config_command(args)
        return

    set_config(load_config(args.config))
    uvicorn.run(
        app,
        host=config().server.host,
        log_level="debug" if args.verbose else "info",
        port=config().server.port,
        reload=False,
    )


def run_config_command(args: Any) -> None:
    generated = full_config(load_config(args.config))
    if args.command == "print-opencode-config":
        print(json.dumps(generated, indent=2))
        return

    path = Path(args.opencode_config)
    merged = merge_config(read_opencode_config(path), generated)
    write_opencode_config(path, merged)
    print(f"Wrote opencode config to {path}")
    if args.print:
        print(json.dumps(merged, indent=2))
