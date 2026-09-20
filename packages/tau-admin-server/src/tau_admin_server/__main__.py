"""`python -m tau_admin_server` - runs the admin backend standalone. Binds 0.0.0.0:8100 by default
(TAU_ADMIN_BIND_HOST/TAU_ADMIN_BIND_PORT to override); operators are expected to restrict actual
reachability at the network layer (VPN-only interface, Docker network, reverse-proxy path) rather
than rely on this service's own CORS allowlist as the boundary - see settings.py.
"""

from __future__ import annotations

import os

import uvicorn

from tau_admin_server.server import create_app

app = create_app()

if __name__ == "__main__":
    host = os.environ.get("TAU_ADMIN_BIND_HOST", "0.0.0.0")
    port = int(os.environ.get("TAU_ADMIN_BIND_PORT", "8100"))
    uvicorn.run(app, host=host, port=port)
