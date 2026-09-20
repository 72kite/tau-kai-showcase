from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator


class ServerConfig(BaseModel):
    name: str
    transport: Literal["stdio", "streamable_http"]
    # stdio transport
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    # streamable_http transport
    url: str | None = None

    @model_validator(mode="after")
    def _validate_transport_fields(self) -> "ServerConfig":
        if self.transport == "stdio" and not self.command:
            raise ValueError(f"server '{self.name}': stdio transport requires 'command'")
        if self.transport == "streamable_http" and not self.url:
            raise ValueError(f"server '{self.name}': streamable_http transport requires 'url'")
        return self


class ServerRegistry(BaseModel):
    servers: list[ServerConfig] = Field(default_factory=list)

    def get(self, name: str) -> ServerConfig:
        for server in self.servers:
            if server.name == name:
                return server
        raise KeyError(f"No MCP server registered under name '{name}'")


def load_server_registry(path: str | Path) -> ServerRegistry:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"MCP server registry not found at {path}")
    data = yaml.safe_load(path.read_text()) or {}
    return ServerRegistry.model_validate(data)
