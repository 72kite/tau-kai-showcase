import json
import os
import time
from datetime import datetime, timezone

from mcp.server.fastmcp import FastMCP

from utility_mcp_server.capabilities import (
    build_identity,
    describe_capabilities as _describe_capabilities,
)

server = FastMCP(
    "utility-mcp-server",
    host=os.getenv("FASTMCP_HOST", "127.0.0.1"),
    port=int(os.getenv("FASTMCP_PORT", "8000")),
)

# Process start, for uptime in get_system_status. Module import time is close enough to
# "server started" for a snapshot tool; nothing here needs sub-second precision.
_STARTED_MONOTONIC = time.monotonic()


def _resolve_timezone():
    """The tz to report times in: TAU_TIMEZONE if set (IANA name, e.g. 'America/New_York'),
    otherwise the system local zone. Returns None to mean 'use local time' - callers pass that
    straight to astimezone(), which interprets None as local."""
    tz_name = os.getenv("TAU_TIMEZONE", "").strip()
    if not tz_name:
        return None
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(tz_name)
    except Exception:  # noqa: BLE001 - a bad/unknown tz name degrades to local, never an error
        return None


def _now() -> datetime:
    tz = _resolve_timezone()
    return datetime.now(tz) if tz is not None else datetime.now().astimezone()


@server.tool()
def get_time() -> str:
    """Get the current wall-clock time (timezone-aware). Use this instead of guessing - Tau has
    no other way to know the time. Honors TAU_TIMEZONE, else the system local zone."""
    now = _now()
    return json.dumps(
        {
            "iso": now.isoformat(),
            "time": now.strftime("%H:%M:%S"),
            "time_12h": now.strftime("%I:%M %p").lstrip("0"),
            "timezone": now.tzname(),
            "utc_offset": now.strftime("%z"),
        }
    )


@server.tool()
def get_date() -> str:
    """Get the current date (timezone-aware). Honors TAU_TIMEZONE, else the system local zone."""
    now = _now()
    # Built by hand rather than strftime("%-d"/"%#d") - the no-leading-zero day directive is
    # platform-specific (%-d on Linux, %#d on Windows), so neither is portable.
    human = f"{now.strftime('%A, %B')} {now.day}, {now.year}"
    return json.dumps(
        {
            "iso": now.date().isoformat(),
            "weekday": now.strftime("%A"),
            "human": human,
            "timezone": now.tzname(),
        }
    )


def _hardware_snapshot() -> dict:
    """Hardware view via tau_core.hardware if it's importable in this environment (it lives in
    tau-core, which normally shares the venv). Soft dependency on purpose: the utility server
    must run standalone, so an absent tau_core degrades to 'unknown', never an import error."""
    try:
        from tau_core.hardware import probe_hardware, recommend_models

        hw = probe_hardware()
        rec = recommend_models(hw)
        return {
            # Combined VRAM across every detected GPU (Phase 10.5) - see tau_core.hardware's
            # docstring for why the sum, not the biggest single card, is what bounds what
            # Ollama can load. gpu_count/gpu_vram_mb_per_device are the breakdown behind it.
            "gpu_vram_mb": hw.gpu_vram_mb,
            "gpu_count": hw.gpu_count,
            "gpu_vram_mb_per_device": list(hw.gpu_vram_mb_per_device),
            "system_ram_mb": hw.system_ram_mb,
            "cpu_cores": hw.cpu_cores,
            "gpu_visible_to_docker": hw.gpu_visible_to_docker,
            "recommended_model": rec.main,
            "recommended_router_model": rec.router,
            "recommendation_reason": rec.reason,
        }
    except Exception:  # noqa: BLE001 - tau_core absent or probe failed; report what we can
        try:
            cpu_cores = os.cpu_count() or 0
        except Exception:  # noqa: BLE001
            cpu_cores = 0
        return {
            "gpu_vram_mb": None,
            "gpu_count": None,
            "gpu_vram_mb_per_device": None,
            "system_ram_mb": None,
            "cpu_cores": cpu_cores,
            "gpu_visible_to_docker": None,
            "recommended_model": None,
            "recommended_router_model": None,
            "recommendation_reason": "tau_core.hardware unavailable in this environment",
        }


@server.tool()
def get_system_status() -> str:
    """What hardware and AI model TAU ITSELF runs on: GPU VRAM, RAM, CPU cores, the configured
    model + router, and process uptime.

    ONLY for questions about Tau's own machine and model - "what are you running on", "which
    model are you", "how much VRAM do you have", "how long have you been up".

    NOT for the status of anything else in the home. A question about a 3D printer, a camera, a
    VM, a light, the drone, or the security system belongs to that domain's own server, even
    though it also uses the word "status". This tool knows nothing about them.

    That warning is load-bearing, not decorative: in the 2026-07-22 eval this tool actively stole
    "What's the status of the 3D printer?" - the model replied "there is no direct function
    provided to check the status of a 3D printer. However, you might be able to get similar
    information by using utility-mcp-server__get_system_status", while
    fabrication-mcp-server__get_printer_status sat unused in the same roster. A description that
    says only "snapshot of the box Tau is running on" reads as a plausible match for any
    "status" query; naming what it excludes is what stops that."""
    hw = _hardware_snapshot()
    uptime_s = time.monotonic() - _STARTED_MONOTONIC
    return json.dumps(
        {
            "current_model": os.getenv("OLLAMA_MODEL") or None,
            "current_router_model": os.getenv("OLLAMA_ROUTER_MODEL") or None,
            "hardware": hw,
            "uptime_seconds": round(uptime_s, 1),
            "snapshot_at": datetime.now(timezone.utc).isoformat(),
        }
    )


@server.tool()
def describe_capabilities() -> str:
    """Answer "what can you do?" - the list of Tau's own capabilities and servers.

    ONLY for questions about Tau's own repertoire: "what can you do", "what are your
    capabilities", "which servers do you have", "can you control the lights at all".

    NOT a way to answer a question about the home by proxy. If the user asks what a device or
    system is actually DOING right now, call that domain's tool and report a real reading - do
    not call this and describe what you could theoretically do instead. Reading a capability
    list back to someone who asked for live state is a non-answer.

    Read from the deployment's servers.yaml where available, so it reflects what is really wired
    up rather than a hard-coded list."""
    return json.dumps(_describe_capabilities())


@server.resource(uri="tau://identity")
def get_identity_resource() -> str:
    """Stable, factual identity: who TAU is, its purpose, its operating rules in plain terms, and
    the version/build it is running. Grounds 'who/what are you' and 'what version are you' answers
    in facts rather than a paraphrase of the prompt."""
    return json.dumps(build_identity())


if __name__ == "__main__":
    server.run(transport=os.getenv("MCP_TRANSPORT", "stdio"))
