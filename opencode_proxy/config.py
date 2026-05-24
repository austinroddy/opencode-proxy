from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, Field, HttpUrl


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 11435


class UpstreamConfig(BaseModel):
    base_url: HttpUrl
    api_key: str | None = None
    request_timeout_seconds: float = 300


class DefaultsConfig(BaseModel):
    model: str | None = None


class AuthConfig(BaseModel):
    client_api_keys: list[str] = Field(default_factory=list)


class ModelConfig(BaseModel):
    name: str
    context: int | None = None
    output: int | None = None
    tool_call: bool = True
    temperature: bool = True


class OpencodeConfig(BaseModel):
    provider_id: str
    provider_name: str
    model: str
    models: dict[str, ModelConfig] = Field(default_factory=dict)


class Config(BaseModel):
    server: ServerConfig
    upstream: UpstreamConfig
    auth: AuthConfig = Field(default_factory=AuthConfig)
    defaults: DefaultsConfig = Field(default_factory=DefaultsConfig)
    opencode: OpencodeConfig | None = None


def load_config(path: str | Path = "opp.toml") -> Config:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(
            f"Missing {config_path}. Create it from opp.example.toml or pass --config /path/to/opp.toml."
        )
    return Config.model_validate(tomllib.loads(config_path.read_text()))
