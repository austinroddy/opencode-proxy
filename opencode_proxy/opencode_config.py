from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import json5

from opencode_proxy.config import Config, ModelConfig


def provider_block(config: Config) -> dict[str, Any]:
    if config.opencode is None:
        raise ValueError("Missing [opencode] config in opp.toml")

    return {
        "name": config.opencode.provider_name,
        "npm": "@ai-sdk/openai-compatible",
        "api": opencode_api_url(config),
        "models": {model_id: model_block(model) for model_id, model in config.opencode.models.items()},
        "options": {
            "apiKey": opencode_api_key(config),
        },
    }


def full_config(config: Config) -> dict[str, Any]:
    if config.opencode is None:
        raise ValueError("Missing [opencode] config in opp.toml")

    return {
        "$schema": "https://opencode.ai/config.json",
        "provider": {
            config.opencode.provider_id: provider_block(config),
        },
        "model": selected_model(config),
    }


def merge_config(existing: dict[str, Any], generated: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(existing)
    result["$schema"] = result.get("$schema", generated["$schema"])
    result["provider"] = {
        **result.get("provider", {}),
        **generated["provider"],
    }
    result["model"] = generated["model"]
    return result


def read_opencode_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    content = path.read_text().strip()
    if not content:
        return {}
    return dict(json5.loads(content))


def write_opencode_config(path: Path, config: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.with_suffix(f"{path.suffix}.bak").write_text(path.read_text())
    path.write_text(json.dumps(config, indent=2) + "\n")


def opencode_config_path() -> Path:
    return Path.home() / ".config" / "opencode" / "opencode.json"


def opencode_api_url(config: Config) -> str:
    if config.opencode is None:
        raise ValueError("Missing [opencode] config in opp.toml")
    if config.opencode.api_url:
        return config.opencode.api_url

    host = "127.0.0.1" if config.server.host == "0.0.0.0" else config.server.host
    return f"http://{host}:{config.server.port}/v1"


def opencode_api_key(config: Config) -> str:
    if config.opencode is not None and config.opencode.api_key:
        return config.opencode.api_key
    if config.auth.client_api_keys:
        return config.auth.client_api_keys[0]
    return "local-proxy"


def selected_model(config: Config) -> str:
    if config.opencode is None:
        raise ValueError("Missing [opencode] config in opp.toml")
    if "/" in config.opencode.model:
        return config.opencode.model
    return f"{config.opencode.provider_id}/{config.opencode.model}"


def model_block(model: ModelConfig) -> dict[str, Any]:
    result: dict[str, Any] = {
        "name": model.name,
        "tool_call": model.tool_call,
        "temperature": model.temperature,
    }
    if model.context is not None or model.output is not None:
        result["limit"] = {
            **({"context": model.context} if model.context is not None else {}),
            **({"output": model.output} if model.output is not None else {}),
        }
    if model.input_cost is not None or model.output_cost is not None:
        result["cost"] = {
            **({"input": model.input_cost} if model.input_cost is not None else {}),
            **({"output": model.output_cost} if model.output_cost is not None else {}),
            **({"cache_read": model.cache_read_cost} if model.cache_read_cost is not None else {}),
            **({"cache_write": model.cache_write_cost} if model.cache_write_cost is not None else {}),
        }
    return result
