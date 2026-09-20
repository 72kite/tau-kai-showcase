"""mDNS advertisement of tau-core's TLS listener (Phase 27.D Milestone 3) - lets the desktop
client find tau-core on the LAN instead of a human typing an IP/port. Only started when TLS is on
(`TauCoreSettings.tls_enabled`); the plain-HTTP listener was never meant to be what discovery
bootstraps into - pairing needs the TLS endpoint's certificate fingerprint to mean anything.

Uses `zeroconf.asyncio.AsyncZeroconf`, not the synchronous `Zeroconf` API - **found the hard way**:
the synchronous API spins up its own internal event-loop machinery, which raises
`zeroconf._exceptions.EventLoopBlocked` when constructed from inside a coroutine already running
on an asyncio loop (exactly what `server.py`'s FastAPI lifespan is), crashing the plain-HTTP
uvicorn listener's startup entirely - not a "when it matters" bug, it broke the very first live
test. `AsyncZeroconf`/`AsyncServiceInfo` are zeroconf's own documented answer to this.
"""

from __future__ import annotations

import logging
import socket

from zeroconf import ServiceInfo
from zeroconf.asyncio import AsyncZeroconf

logger = logging.getLogger(__name__)

SERVICE_TYPE = "_tau._tcp.local."


async def start_advertising(tls_port: int, instance_name: str = "tau-core") -> AsyncZeroconf:
    """Starts advertising this host's TLS listener via mDNS/DNS-SD. Returns the `AsyncZeroconf`
    instance - the caller owns its lifetime and must `await .async_close()` on shutdown, which
    unregisters the service so it doesn't linger as a stale, unreachable discovery result.
    """
    aiozc = AsyncZeroconf()
    hostname = socket.gethostname()
    try:
        address = socket.inet_aton(socket.gethostbyname(hostname))
    except OSError:
        # No resolvable local address is unusual but must not crash startup over a discovery
        # nicety - the TLS listener itself still works fine for a manually-entered address.
        logger.warning("Could not resolve a local address to advertise for mDNS discovery")
        address = socket.inet_aton("127.0.0.1")

    info = ServiceInfo(
        SERVICE_TYPE,
        f"{instance_name}.{SERVICE_TYPE}",
        addresses=[address],
        port=tls_port,
        properties={},
        server=f"{hostname}.local.",
    )
    await aiozc.async_register_service(info)
    logger.info("Advertising tau-core via mDNS as %s on port %d", info.name, tls_port)
    return aiozc
