"""Per-domain FastAPI routers for tau-core's HTTP bridge, split out of web/server.py's
create_app() in Phase 49. Each module exposes a build_<domain>_router(deps) -> APIRouter
factory; create_app() builds one SharedDeps and includes every router on the app."""
