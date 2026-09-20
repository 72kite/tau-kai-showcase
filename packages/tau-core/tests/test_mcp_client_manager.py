import sys
from pathlib import Path

from tau_core.mcp_client import MCPClientManager, ServerConfig, ServerRegistry, load_server_registry

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def echo_registry() -> ServerRegistry:
    return ServerRegistry(
        servers=[
            ServerConfig(
                name="echo",
                transport="stdio",
                command=sys.executable,
                args=[str(EXAMPLES_DIR / "echo_mcp_server.py")],
            )
        ]
    )


async def test_connect_and_call_echo_tool():
    registry = echo_registry()
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        assert manager.connected_servers() == ["echo"]

        tools = await manager.list_tools("echo")
        assert any(t.name == "echo" for t in tools)

        result = await manager.call_tool("echo", "echo", {"text": "hello tau"})
        assert result.content[0].text == "hello tau"


async def test_read_resource():
    registry = echo_registry()
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()

        result = await manager.read_resource("echo", "echo://status")
        assert result.contents[0].text == "ok"


def test_load_server_registry_from_repo_config():
    registry = load_server_registry(CONFIG_DIR / "servers.yaml")
    assert registry.get("home-assistant-mcp-server").transport == "stdio"
    assert registry.get("robotics-mcp-server").transport == "stdio"


def test_production_registry_excludes_test_fixture_servers():
    """echo/dangerous are test fixtures (examples/); registering them in servers.yaml puts an
    attractive-but-useless `echo` tool in front of the LLM in a real deployment - live testing
    showed models reaching for it unprompted. They belong in servers.test.yaml only."""
    registry = load_server_registry(CONFIG_DIR / "servers.yaml")
    registered = {server.name for server in registry.servers}
    assert "echo" not in registered
    assert "dangerous" not in registered


def test_load_test_fixture_registry():
    registry = load_server_registry(CONFIG_DIR / "servers.test.yaml")
    assert registry.get("echo").transport == "stdio"
    assert registry.get("dangerous").transport == "stdio"
