from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import AsyncExitStack, suppress

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import CallToolResult, ReadResourceResult, Tool

from tau_core.mcp_client.config import ServerConfig, ServerRegistry

logger = logging.getLogger(__name__)

# servers.yaml conventionally writes `command: python` for every domain server. Resolving that
# through the subprocess's inherited PATH is fragile - it silently picks up whatever "python"
# happens to be first on PATH, which is often NOT the interpreter tau-core itself is running
# under (e.g. tau-core launched via its venv's python.exe directly, without `activate` having
# modified PATH first) and may not have any of the domain server packages installed at all.
# Substituting sys.executable for the bare "python"/"python3" command guarantees every spawned
# domain server uses the exact same interpreter as the parent process.
_BARE_PYTHON_COMMANDS = {"python", "python3"}

DEFAULT_CALL_TIMEOUT_SECONDS = 30.0
DEFAULT_CONNECT_TIMEOUT_SECONDS = 20.0
# A server that is genuinely gone (image removed, crash-looping) must not cost a reconnect
# attempt on every single turn - that would trade "one dead server 502s chat" for "one dead
# server makes chat slow", which is barely better. Failed reconnects back off exponentially and
# fast-fail in between; a live server never touches this path.
RECONNECT_BACKOFF_START_SECONDS = 5.0
RECONNECT_BACKOFF_MAX_SECONDS = 60.0


def _describe(exc: BaseException) -> str:
    """A short, non-empty description of a transport failure.

    The MCP SDK's failures arrive as nested ExceptionGroups whose str() is often empty (a dead
    stdio server yields `ExceptionGroup([ExceptionGroup([McpError('Connection closed')])])`), so
    interpolating `{exc}` produced errors that literally read "failed: " - observed against the
    running bridge. Walk to the innermost cause and always fall back to the type name.
    """
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


class ServerNotConnectedError(RuntimeError):
    pass


class ServerCallError(RuntimeError):
    """A request to a connected server failed or timed out.

    The session is marked dead before this is raised, so the next call to that server
    reconnects rather than reusing a zombie.
    """


class _SessionRunner:
    """Owns ONE MCP server's session for its whole lifetime, inside its own asyncio task.

    Why a task per session instead of one shared AsyncExitStack (the pre-Phase-7 design):
    `stdio_client` and `streamablehttp_client` open anyio task groups internally, and anyio
    cancel scopes must be exited by the same task that entered them. With every session in one
    stack entered from the host's startup task, closing a *single* dead server from a request
    handler raises "attempted to exit cancel scope in a different task" - which is precisely
    why the old manager had no reconnect path and returned dead session objects forever
    (reproduced live 2026-07-15: recreating one container 502'd it until tau-core restarted).

    Here both the enter and the exit happen inside `_run`, so a session can be torn down and
    reopened on demand from any task without touching another server's session.
    """

    def __init__(self, server: ServerConfig, connect_timeout: float):
        self._server = server
        self._connect_timeout = connect_timeout
        self._shutdown = asyncio.Event()
        self._ready = asyncio.Event()
        self._task: asyncio.Task | None = None
        self.session: ClientSession | None = None
        self.error: BaseException | None = None

    @property
    def alive(self) -> bool:
        return (
            self.session is not None
            and self._task is not None
            and not self._task.done()
            and self.error is None
        )

    async def start(self) -> ClientSession:
        self._task = asyncio.create_task(self._run(), name=f"mcp-session:{self._server.name}")
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=self._connect_timeout)
        except asyncio.TimeoutError:
            await self.stop()
            raise ServerNotConnectedError(
                f"MCP server '{self._server.name}' did not initialize within "
                f"{self._connect_timeout}s"
            ) from None
        if self.error is not None or self.session is None:
            error = self.error
            await self.stop()
            raise ServerNotConnectedError(
                f"MCP server '{self._server.name}' failed to connect: "
                f"{_describe(error) if error else 'transport closed during initialize'}"
            ) from error
        return self.session

    async def _run(self) -> None:
        try:
            async with AsyncExitStack() as stack:
                read, write = await self._open_transport(stack)
                session = await stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                self.session = session
                self._ready.set()
                # Hold the transport open until someone asks for shutdown. Everything above is
                # torn down by this stack, in this task, on the way out.
                await self._shutdown.wait()
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001 - recorded and reported, never swallowed
            self.error = exc
            logger.warning(
                "MCP session for %r ended: %r", self._server.name, exc, exc_info=True
            )
        finally:
            self.session = None
            # Unblock start() whether we got there by success, failure, or a transport that
            # died before initialize() returned.
            self._ready.set()

    async def _open_transport(self, stack: AsyncExitStack):
        server = self._server
        if server.transport == "stdio":
            command = sys.executable if server.command in _BARE_PYTHON_COMMANDS else server.command
            # The MCP SDK spawns stdio servers with a minimal scrubbed environment by default
            # (get_default_environment()), NOT the parent's - so service endpoints the host
            # loaded from tau-core/.env (WHISPER_URL, PIPER_URI, HA_URL, ...) silently never
            # reached the domain servers. Pass the full parent environment through, with any
            # per-server env from servers.yaml layered on top. Deliberate trade-off for this
            # single-host deployment of first-party servers; Phase 0's Vault is the real
            # secrets-distribution answer, not env scrubbing.
            merged_env = {**os.environ, **(server.env or {})}
            params = StdioServerParameters(command=command, args=server.args, env=merged_env)
            read, write = await stack.enter_async_context(stdio_client(params))
            return read, write
        read, write, _get_session_id = await stack.enter_async_context(
            streamablehttp_client(server.url)
        )
        return read, write

    async def stop(self) -> None:
        self._shutdown.set()
        task = self._task
        if task is None:
            return
        if not task.done():
            # A server that has stopped reading its stdin can leave the transport's own
            # teardown blocked; don't let one wedged subprocess hold the host's shutdown (or a
            # reconnect) open forever.
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=self._connect_timeout)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await task
        self._task = None
        self.session = None


class MCPClientManager:
    """Owns one MCP ClientSession per configured domain server (stdio or Streamable HTTP).

    This is transport plumbing only - it does not enforce policy. Every call that reaches this
    manager is assumed to have already cleared the Core Directive Guard; see tau_core.host.

    Resilience posture (Phase 7 Tier 0), in one place because the pieces only make sense
    together:

    - A session that errors or times out is torn down immediately, and the next request to that
      server reconnects. Reads retry across that reconnect transparently; `call_tool` does not
      (see `_call` - it is a governance decision, not an oversight).
    - `connected_servers()` reports only sessions that are actually live. The old version
      returned dict keys, so it reported a server as connected forever after it died.
    - A server that fails to *connect* backs off exponentially, so a permanently-gone server
      costs a fast failure per turn rather than a connect timeout per turn.
    - Nothing here raises to abort startup: `connect_all` reports what didn't come up.
    """

    def __init__(
        self,
        registry: ServerRegistry,
        call_timeout: float = DEFAULT_CALL_TIMEOUT_SECONDS,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    ):
        self._registry = registry
        self._call_timeout = call_timeout
        self._connect_timeout = connect_timeout
        self._runners: dict[str, _SessionRunner] = {}
        # Serialises reconnects per server, so N concurrent calls to a server that just died
        # produce one reconnect rather than N racing subprocess spawns.
        self._locks: dict[str, asyncio.Lock] = {}
        # name -> (monotonic deadline before which we won't retry, current backoff length)
        self._retry_after: dict[str, tuple[float, float]] = {}
        self._closing = False

    async def __aenter__(self) -> "MCPClientManager":
        self._closing = False
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.close_all()

    async def close_all(self) -> None:
        # Latched before tearing anything down: reconnect-on-demand means an in-flight request
        # that finds a dead session during shutdown would otherwise happily spawn a replacement
        # subprocess we are no longer here to reap - an orphan outliving the bridge.
        self._closing = True
        runners = list(self._runners.values())
        self._runners.clear()
        # Tear every session down even if one of them misbehaves on the way out.
        await asyncio.gather(*(runner.stop() for runner in runners), return_exceptions=True)

    def _lock(self, name: str) -> asyncio.Lock:
        lock = self._locks.get(name)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[name] = lock
        return lock

    async def connect_all(self) -> dict[str, BaseException]:
        """Connects every registered server, concurrently, tolerating individual failures.

        Returns a name -> exception map of the servers that did NOT come up. It does not raise:
        a home AI host that refuses to boot because the 3D printer's server is down is worse
        than one that boots without printer tools (the old sequential loop aborted startup on
        the first failure, so a single bad server meant no Tau at all).
        """
        names = [server.name for server in self._registry.servers]
        results = await asyncio.gather(
            *(self.connect(name) for name in names), return_exceptions=True
        )
        failures: dict[str, BaseException] = {}
        for name, result in zip(names, results):
            if isinstance(result, BaseException):
                failures[name] = result
                logger.warning("MCP server %r failed to connect at startup: %r", name, result)
        if failures:
            logger.warning(
                "Booting with %d of %d MCP servers unavailable: %s",
                len(failures),
                len(names),
                sorted(failures),
            )
        return failures

    async def connect(self, name: str) -> ClientSession:
        server = self._registry.get(name)
        async with self._lock(name):
            existing = self._runners.pop(name, None)
            if existing is not None:
                await existing.stop()
            return await self._start_locked(server)

    async def _start_locked(self, server: ServerConfig) -> ClientSession:
        if self._closing:
            raise ServerNotConnectedError(
                f"MCP server '{server.name}' not started: the manager is shutting down"
            )
        runner = _SessionRunner(server, connect_timeout=self._connect_timeout)
        try:
            session = await runner.start()
        except Exception:
            self._note_connect_failure(server.name)
            raise
        self._runners[server.name] = runner
        self._retry_after.pop(server.name, None)
        return session

    def _note_connect_failure(self, name: str) -> None:
        _deadline, previous = self._retry_after.get(name, (0.0, 0.0))
        backoff = min(
            RECONNECT_BACKOFF_MAX_SECONDS,
            previous * 2 if previous else RECONNECT_BACKOFF_START_SECONDS,
        )
        self._retry_after[name] = (asyncio.get_running_loop().time() + backoff, backoff)
        logger.warning("MCP server %r unreachable; not retrying for %.0fs", name, backoff)

    def _retry_blocked(self, name: str) -> float:
        """Seconds left on this server's reconnect backoff, or 0.0 if a retry is due."""
        deadline, _backoff = self._retry_after.get(name, (0.0, 0.0))
        return max(0.0, deadline - asyncio.get_running_loop().time())

    async def _ensure_session(self, name: str) -> ClientSession:
        """Returns a live session for `name`, reconnecting if the last one died.

        The old `_get_session` returned whatever was in the dict, including a dead session
        object, forever.
        """
        runner = self._runners.get(name)
        if runner is not None and runner.alive:
            return runner.session  # type: ignore[return-value]

        try:
            server = self._registry.get(name)
        except KeyError as exc:
            raise ServerNotConnectedError(
                f"MCP server '{name}' is not registered; check servers.yaml"
            ) from exc

        blocked_for = self._retry_blocked(name)
        if blocked_for:
            raise ServerNotConnectedError(
                f"MCP server '{name}' is down; next reconnect attempt in {blocked_for:.0f}s"
            )

        async with self._lock(name):
            # Re-check under the lock: a concurrent caller may have reconnected already.
            runner = self._runners.get(name)
            if runner is not None and runner.alive:
                return runner.session  # type: ignore[return-value]
            if self._retry_blocked(name):
                raise ServerNotConnectedError(f"MCP server '{name}' is down (reconnect backing off)")
            if runner is not None:
                await runner.stop()
                self._runners.pop(name, None)
            logger.info("Reconnecting MCP server %r", name)
            return await self._start_locked(server)

    async def _mark_dead(self, name: str) -> None:
        runner = self._runners.pop(name, None)
        if runner is not None:
            await runner.stop()

    async def _attempt(self, name: str, what: str, coro_factory):
        session = await self._ensure_session(name)
        try:
            return await asyncio.wait_for(coro_factory(session), timeout=self._call_timeout)
        except asyncio.TimeoutError as exc:
            await self._mark_dead(name)
            raise ServerCallError(
                f"{what} on MCP server '{name}' timed out after {self._call_timeout}s"
            ) from exc
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Any transport-level failure means this session is not trustworthy; drop it so the
            # next call gets a fresh one instead of the "Session terminated" loop that needed a
            # manual tau-core restart to clear.
            await self._mark_dead(name)
            raise ServerCallError(f"{what} on MCP server '{name}' failed: {_describe(exc)}") from exc

    async def _call(self, name: str, what: str, coro_factory, *, idempotent: bool = False):
        """Runs one request against `name`, optionally retrying once on a fresh session.

        A session's death is only discoverable by using it, so the first request after a server
        is redeployed always fails - there is nothing to detect beforehand. `idempotent` says
        whether swallowing that by retrying is SAFE, and it is deliberately opt-in:

        - Reads (`list_tools`, `read_resource`) retry. Repeating them cannot change anything.
        - `call_tool` does NOT. When a call dies in flight we cannot tell "never sent" from
          "executed, then the reply was lost" - and this manager fronts door locks, a patrol
          drone, and a 3D printer. Silently re-issuing a possibly-completed physical action to
          save one error message is not a trade this project should make; the caller gets a
          clear failure and the session is already dead, so their next call reconnects.

        In practice the chat path barely notices: `build_toolset` calls `list_tools` on every
        server at the start of each turn, so a redeployed server is reconnected before the model
        ever picks a tool.
        """
        try:
            return await self._attempt(name, what, coro_factory)
        except ServerCallError:
            if not idempotent:
                raise
        logger.info("Retrying %s on %r after a dead session", what, name)
        return await self._attempt(name, what, coro_factory)

    async def list_tools(self, server_name: str) -> list[Tool]:
        result = await self._call(
            server_name, "list_tools", lambda s: s.list_tools(), idempotent=True
        )
        return result.tools

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict) -> CallToolResult:
        return await self._call(
            server_name, f"call_tool({tool_name})", lambda s: s.call_tool(tool_name, arguments)
        )

    async def read_resource(self, server_name: str, uri: str) -> ReadResourceResult:
        """Read an MCP Resource (e.g. "ui://state") from a connected server.

        Unlike call_tool, resource reads never pass through the CDG - they're informational
        (the ui-bridge-mcp-server resources this is used for are all read-only aggregate
        state). Callers that expose this to untrusted input should treat it as read-only.
        """
        return await self._call(
            server_name, f"read_resource({uri})", lambda s: s.read_resource(uri), idempotent=True
        )

    def connected_servers(self) -> list[str]:
        """Servers with a session that is actually live right now.

        Deliberately checks liveness rather than dict membership: this feeds `build_toolset`,
        and the pre-Phase-7 version reported every server ever connected, dead or not.
        """
        return [name for name, runner in self._runners.items() if runner.alive]

    def registered_servers(self) -> list[str]:
        """Every server in the registry, connected or not - for diagnostics (`/api/health`),
        where the gap between registered and connected is the thing worth seeing."""
        return [server.name for server in self._registry.servers]
