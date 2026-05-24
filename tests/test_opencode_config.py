from __future__ import annotations

import json

from opencode_proxy.config import Config
from opencode_proxy.opencode_config import (
    full_config,
    merge_config,
    read_opencode_config,
    write_opencode_config,
)


def make_config() -> Config:
    return Config.model_validate(
        {
            "server": {"host": "127.0.0.1", "port": 11435},
            "upstream": {"base_url": "http://localhost:11434"},
            "auth": {"client_api_keys": ["team-token"]},
            "opencode": {
                "provider_id": "company-ollama",
                "provider_name": "Company Ollama",
                "model": "qwen2.5-coder:32b",
                "models": {
                    "qwen2.5-coder:32b": {
                        "name": "Qwen 2.5 Coder 32B",
                        "context": 32768,
                        "output": 8192,
                        "input_cost": 0.1,
                        "output_cost": 0.2,
                        "tool_call": True,
                        "temperature": True,
                    }
                },
            },
        }
    )


def test_full_config_generates_opencode_provider() -> None:
    assert full_config(make_config()) == {
        "$schema": "https://opencode.ai/config.json",
        "provider": {
            "company-ollama": {
                "name": "Company Ollama",
                "npm": "@ai-sdk/openai-compatible",
                "api": "http://127.0.0.1:11435/v1",
                "models": {
                    "qwen2.5-coder:32b": {
                        "name": "Qwen 2.5 Coder 32B",
                        "tool_call": True,
                        "temperature": True,
                        "limit": {"context": 32768, "output": 8192},
                        "cost": {"input": 0.1, "output": 0.2},
                    }
                },
                "options": {"apiKey": "team-token"},
            }
        },
        "model": "company-ollama/qwen2.5-coder:32b",
    }


def test_merge_config_preserves_other_providers() -> None:
    assert merge_config(
        {
            "$schema": "https://opencode.ai/config.json",
            "provider": {"anthropic": {"name": "Anthropic"}},
            "model": "anthropic/claude",
        },
        full_config(make_config()),
    )["provider"]["anthropic"] == {"name": "Anthropic"}


def test_read_and_write_opencode_config_supports_jsonc(tmp_path) -> None:
    path = tmp_path / "opencode.json"
    path.write_text(
        """
        {
          // existing config comments are accepted on read
          "provider": {
            "anthropic": {"name": "Anthropic"}
          }
        }
        """
    )

    merged = merge_config(read_opencode_config(path), full_config(make_config()))
    write_opencode_config(path, merged)

    assert json.loads(path.read_text())["provider"]["company-ollama"]["api"] == "http://127.0.0.1:11435/v1"
    assert path.with_suffix(".json.bak").exists()
