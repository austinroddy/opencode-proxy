from __future__ import annotations

import json

import httpx
import pytest
import respx

from opencode_proxy.app import app, set_config, to_ollama_chat
from opencode_proxy.config import Config


def make_config(client_api_keys: list[str] | None = None) -> Config:
    return Config.model_validate(
        {
            "server": {"host": "127.0.0.1", "port": 11435},
            "upstream": {"base_url": "http://localhost:11434", "request_timeout_seconds": 300},
            "auth": {"client_api_keys": client_api_keys or []},
        }
    )


@pytest.fixture(autouse=True)
def default_config() -> None:
    set_config(make_config())


@pytest.mark.asyncio
async def test_models_translates_ollama_tags() -> None:
    transport = httpx.ASGITransport(app=app)
    with respx.mock(base_url="http://localhost:11434") as router:
        router.get("/api/tags").mock(
            return_value=httpx.Response(
                200,
                json={
                    "models": [
                        {"name": "llama3.1:8b", "modified_at": "2026-01-02T03:04:05.000Z"},
                    ]
                },
            )
        )
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/v1/models")

    assert response.status_code == 200
    assert response.json()["data"][0]["id"] == "llama3.1:8b"
    assert response.json()["data"][0]["object"] == "model"


@pytest.mark.asyncio
async def test_models_rejects_missing_client_api_key_when_configured() -> None:
    set_config(make_config(["team-token"]))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/models")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_models_accepts_client_api_key_when_configured() -> None:
    set_config(make_config(["team-token"]))
    transport = httpx.ASGITransport(app=app)
    with respx.mock(base_url="http://localhost:11434") as router:
        router.get("/api/tags").mock(return_value=httpx.Response(200, json={"models": []}))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/v1/models", headers={"authorization": "Bearer team-token"})

    assert response.status_code == 200


def test_openai_body_translates_to_ollama_chat() -> None:
    assert to_ollama_chat(
        {
            "model": "qwen2.5-coder:32b",
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hello"}]}],
            "temperature": 0.2,
            "max_tokens": 1000,
        }
    ) == {
        "model": "qwen2.5-coder:32b",
        "messages": [{"role": "user", "content": "hello"}],
        "options": {"temperature": 0.2, "num_predict": 1000},
    }


@pytest.mark.asyncio
async def test_non_streaming_chat_translates_response() -> None:
    transport = httpx.ASGITransport(app=app)
    with respx.mock(base_url="http://localhost:11434") as router:
        router.post("/api/chat").mock(
            return_value=httpx.Response(
                200,
                json={
                    "model": "llama3.1:8b",
                    "message": {"role": "assistant", "content": "hi"},
                    "done_reason": "stop",
                    "prompt_eval_count": 4,
                    "eval_count": 2,
                },
            )
        )
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/chat/completions",
                json={"model": "llama3.1:8b", "messages": [{"role": "user", "content": "hello"}]},
            )

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "hi"
    assert response.json()["usage"]["total_tokens"] == 6


@pytest.mark.asyncio
async def test_streaming_chat_translates_json_lines_to_sse() -> None:
    transport = httpx.ASGITransport(app=app)
    lines = "\n".join(
        [
            json.dumps({"message": {"role": "assistant", "content": "he"}, "done": False}),
            json.dumps({"message": {"role": "assistant", "content": "llo"}, "done": False}),
            json.dumps({"done": True, "done_reason": "stop", "prompt_eval_count": 3, "eval_count": 2}),
        ]
    )
    with respx.mock(base_url="http://localhost:11434") as router:
        router.post("/api/chat").mock(return_value=httpx.Response(200, text=lines))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/chat/completions",
                json={"model": "llama3.1:8b", "stream": True, "messages": [{"role": "user", "content": "hello"}]},
            )

    assert response.status_code == 200
    assert response.text.count("data: ") == 4
    assert '"content":"he"' in response.text
    assert response.text.endswith("data: [DONE]\n\n")
