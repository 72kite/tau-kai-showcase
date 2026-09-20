"""Self-knowledge: what Tau is, and what it can actually do right now.

Two layers, deliberately kept separate:

- CAPABILITY_CATALOG - a static, human-authored description of each domain server's *purpose*
  and a few representative tools. This is design-level self-knowledge: it doesn't change at
  runtime and it doesn't require importing every domain package (the utility server stays a
  lightweight standalone process).
- read_registered_servers() - the *authoritative* list of which servers are registered in this
  deployment, read from the same servers.yaml the host loads. Merging the two means
  describe_capabilities() reports the real registered set (not a stale hard-coded list), with a
  plain-language purpose attached wherever the catalog knows one.

"Connected right now" (as opposed to "registered") is a separate question the host/bridge
answers - it's the only component that holds live MCP sessions (see the web bridge's
/api/health). This server reports what Tau is *designed* to do; the bridge annotates what is
*live* if a caller needs that distinction.
"""

from __future__ import annotations

import os
from importlib.metadata import PackageNotFoundError, version as _dist_version
from pathlib import Path

import yaml

# Plain-language purpose + representative tools per domain server. Keep this honest and short -
# it is what the model quotes back when a user asks "what can you do", so an overclaim here
# becomes an overclaim in Tau's own mouth.
CAPABILITY_CATALOG: dict[str, dict] = {
    "home-assistant-mcp-server": {
        "purpose": "Control smart-home devices (lights, switches, locks, alarms, sensors) via Home Assistant.",
        "tools": ["get_entity_state", "call_service", "list_devices"],
    },
    "voice-mcp-server": {
        "purpose": "Speech in and out: text-to-speech (Piper), transcription (Whisper), and speaker voiceprints.",
        "tools": ["speak", "transcribe", "enroll_voiceprint", "identify_speaker", "detect_wake_word"],
    },
    "memory-mcp-server": {
        "purpose": "Long-term memory: face/voice embeddings, per-person profiles/access levels, and the Memory Tree of project/conversation context - including unverified drafts Tau writes itself, which a human reviews and promotes.",
        "tools": [
            "match_face", "match_voice", "get_person_profile", "store_memory", "search_memory",
            "draft_memory", "list_drafts", "promote_memory", "discard_draft",
        ],
    },
    "research-mcp-server": {
        "purpose": "Read the public internet: web search and page reading. Knows nothing about this home - it is for facts, docs, and anything that may have changed since the model was trained. Everything it returns is untrusted text written by strangers.",
        "tools": ["search_web", "fetch_page"],
    },
    "proxmox-mcp-server": {
        "purpose": "Manage the Proxmox virtualization host: list/snapshot VMs, create containers, apply updates, check CVE advisories.",
        "tools": ["list_vms", "get_vm_status", "snapshot_vm", "create_lxc", "apply_update"],
    },
    "security-mcp-server": {
        "purpose": "Home security posture: enter/exit lockdown, report intrusion status, log incidents.",
        "tools": ["enter_lockdown", "exit_lockdown", "get_intrusion_status", "log_incident"],
    },
    "vision-mcp-server": {
        "purpose": "See through cameras: snapshots, scene description, face detection, object tracking, PTZ control.",
        "tools": ["get_snapshot", "describe_scene", "detect_faces", "track_object", "ptz_move"],
    },
    "fabrication-mcp-server": {
        "purpose": "Drive 3D printers (OctoPrint/Moonraker): submit/cancel/pause jobs and read printer status.",
        "tools": ["get_printer_status", "submit_print_job", "pause_print", "cancel_print"],
    },
    "ui-bridge-mcp-server": {
        "purpose": "Aggregate UI state (transcript, devices, vision, security, designs) for the kiosk/tablet frontend.",
        "tools": ["update_transcription", "update_design", "get_ui_state"],
    },
    "phase4-mcp-server": {
        "purpose": "Governed self-upgrade: propose changes that three reviewer agents must approve before merge.",
        "tools": ["propose_change", "list_proposals", "user_override_proposal", "merge_proposal"],
    },
    "robotics-mcp-server": {
        "purpose": "Command the patrol drone (and, later, robot dog) along pre-approved routes; read telemetry; emergency stop.",
        "tools": ["get_telemetry", "list_patrol_routes", "patrol_route", "return_to_home", "emergency_stop"],
    },
    "utility-mcp-server": {
        "purpose": "Basic primitives and self-knowledge: current time/date, system/hardware status, and what Tau can do.",
        "tools": ["get_time", "get_date", "get_system_status", "describe_capabilities"],
    },
}


def _registry_candidates() -> list[Path]:
    """Where servers.yaml might be, most-authoritative first. TAU_SERVERS_CONFIG_PATH is what
    the host itself uses, so it wins; the rest are best-effort fallbacks for a dev checkout run
    from various cwds."""
    candidates: list[Path] = []
    env_path = os.getenv("TAU_SERVERS_CONFIG_PATH")
    if env_path:
        candidates.append(Path(env_path))
    # Relative to this file: utility-mcp-server/src/utility_mcp_server/ -> repo root -> tau-core.
    repo_root = Path(__file__).resolve().parents[3]
    candidates.append(repo_root / "tau-core" / "config" / "servers.yaml")
    candidates.append(Path.cwd() / "config" / "servers.yaml")
    candidates.append(Path.cwd() / "tau-core" / "config" / "servers.yaml")
    return candidates


def read_registered_servers() -> list[str] | None:
    """Names of servers registered in this deployment, from servers.yaml. Returns None if no
    registry file can be found/parsed - callers then fall back to the catalog's own keys, so
    self-knowledge degrades to "what I'm designed with" rather than failing outright.
    """
    for path in _registry_candidates():
        try:
            if not path.is_file():
                continue
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            servers = data.get("servers", [])
            names = [s["name"] for s in servers if isinstance(s, dict) and s.get("name")]
            if names:
                return names
        except (OSError, yaml.YAMLError, KeyError, TypeError):
            continue
    return None


def describe_capabilities() -> dict:
    """Merge the authoritative registered set with the plain-language catalog. Registered
    servers with no catalog entry are still listed (name only, purpose unknown) so the report
    never silently omits something that's actually wired up."""
    registered = read_registered_servers()
    source = "registry" if registered is not None else "catalog"
    names = registered if registered is not None else sorted(CAPABILITY_CATALOG)

    servers = []
    for name in names:
        entry = CAPABILITY_CATALOG.get(name)
        servers.append(
            {
                "name": name,
                "purpose": entry["purpose"] if entry else "(no description available)",
                "example_tools": entry["tools"] if entry else [],
            }
        )
    return {
        "source": source,  # "registry" = read from servers.yaml; "catalog" = fallback list
        "server_count": len(servers),
        "servers": servers,
    }


# Stable, factual identity. This is what the model should answer "who/what are you" from -
# facts it can point to, not a paraphrase of the system prompt.
IDENTITY = {
    "name": "TAU",
    "kind": "Self-hosted, fully-local home AI orchestrator built on the Model Context Protocol (MCP).",
    "purpose": (
        "Coordinate a set of permissioned MCP servers to run and protect a home: infrastructure "
        "(Proxmox), smart-home devices, cameras/vision, security/lockdown, 3D printing, a patrol "
        "drone, long-term memory, and voice I/O - all on local hardware, no cloud dependency for "
        "the core loop."
    ),
    "operating_rules": [
        "Every action runs through the Core Directive Guard (CDG): dangerous or destructive tools "
        "require explicit human approval, and no prompt can talk Tau past that gate.",
        "Sensitive actions are access-tiered: who may approve an elevated action is enforced by "
        "code, not by asking nicely.",
        "On a detected intrusion, Tau locks down and prepares analysis rather than acting "
        "autonomously without authorization.",
        "Self-upgrades must pass three independent reviewer agents before anything merges, and the "
        "CDG ruleset itself is never editable through that pipeline.",
        "Tau asks for input when it is unsure rather than guessing on consequential actions.",
    ],
}


def _version_info() -> dict:
    """The version/build this server is running.

    Read from *this* package's own metadata plus the environment - deliberately not soft-imported
    from tau_core.version. This server runs as a standalone container that does not have tau-core
    installed, so importing it would degrade to "no version" in exactly the deployment that
    matters, which is worse than useless: the identity would quietly claim not to know.

    The two packages are versioned in lockstep from one repo, and TAU_BUILD_SHA (set for the whole
    stack by scripts/bring-up-tau.ps1) is the value that actually identifies a deployment, so this
    agrees with what /api/health reports. Never raises: a missing version must not break identity.
    """
    info: dict = {"build": os.environ.get("TAU_BUILD_SHA") or "dev"}
    try:
        info["version"] = _dist_version("utility-mcp-server")
    except PackageNotFoundError:
        info["version"] = "0.0.0+unknown"
    except Exception:
        pass
    return info


def build_identity() -> dict:
    """IDENTITY plus the running version/build, so "what version are you" is answered from fact.

    A function rather than a constant because the build stamp is read from the environment and is
    only knowable at run time.
    """
    return {**IDENTITY, **_version_info()}
