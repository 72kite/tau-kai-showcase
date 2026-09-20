"""Run the tau-core web bridge: `python -m tau_core.web`.

Binds to 127.0.0.1 by default (Phase 7 Tier 1 #9) - `/api/chat`, the tool passthrough, and
approve/deny have no authentication at all, so binding every interface out of the box meant a
fresh checkout run directly on a laptop (the "macOS/Linux (bash)"/"Windows (PowerShell)"
quick-starts in README.md) was reachable from the whole LAN with zero auth by default, whether
or not that was ever intended.

Set TAU_WEB_HOST=0.0.0.0 explicitly when you DO want that - e.g. running this directly (not via
Docker) as a kiosk backend for tablets on the same VLAN, having read README.md's "Do NOT expose
ports 8000/3000 to the public internet" note. The Docker compose stack sets it explicitly for a
different reason: a container binding 127.0.0.1 would bind its OWN loopback, which the
`8000:8000` port mapping cannot reach at all - see docker-compose.yml's tau-core service.
"""

import asyncio
import os

import uvicorn
from dotenv import load_dotenv

from tau_core import tls
from tau_core.config import TauCoreSettings
from tau_core.web.server import create_app

# Load tau-core/.env into the process environment BEFORE building the app: the domain servers
# this bridge spawns over stdio inherit our environment, and several read their service
# endpoints from env vars (WHISPER_URL, PIPER_URI, HA_URL, ...). Each package's own
# load_dotenv() searches from its own install location, not tau-core's, so a var set only in
# tau-core/.env never reaches the subprocess without this (found the hard way: transcribe
# failing with "WHISPER_URL must be set" despite it sitting right there in .env).
load_dotenv()

app = create_app()


async def _run() -> None:
    host = os.getenv("TAU_WEB_HOST", "127.0.0.1")
    port = int(os.getenv("TAU_WEB_PORT", "8000"))
    servers = [uvicorn.Server(uvicorn.Config(app, host=host, port=port))]

    # Phase 27.D Milestone 3: a second, TLS-terminated listener for the desktop client's
    # discovery/pairing flow - the plain-HTTP listener above is completely unaffected, still the
    # only thing the kiosk/browser clients ever talk to. Off by default (TauCoreSettings.
    # tls_enabled), so an existing deployment's behavior doesn't shift just by upgrading.
    settings = TauCoreSettings()
    if settings.tls_enabled:
        tls.get_or_create_cert(settings.tls_cert_path, settings.tls_key_path)
        servers.append(
            uvicorn.Server(
                uvicorn.Config(
                    app,
                    host=host,
                    port=settings.tls_port,
                    ssl_certfile=str(settings.tls_cert_path),
                    ssl_keyfile=str(settings.tls_key_path),
                    # Both Server instances wrap the SAME FastAPI app; each would otherwise send
                    # its own independent ASGI lifespan.startup/shutdown, running server.py's
                    # lifespan (MCP connect_all, the scheduler, mDNS registration) TWICE - once
                    # per listener - which is wrong on every count (double-connects, a duplicate
                    # scheduler, and a second mDNS registration racing/conflicting with the
                    # first's service name). The plain-HTTP server above (first in `servers`,
                    # default lifespan="auto") is the one and only owner of app lifecycle; this
                    # second listener just serves routes against the app's already-initialized
                    # state.
                    lifespan="off",
                )
            )
        )

    await asyncio.gather(*(server.serve() for server in servers))


if __name__ == "__main__":
    asyncio.run(_run())
