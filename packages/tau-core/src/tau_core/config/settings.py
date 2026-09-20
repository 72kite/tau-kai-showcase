from __future__ import annotations

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PACKAGE_ROOT = Path(__file__).resolve().parents[3]

# Origins on the home network, which is where every legitimate client of this bridge lives: the
# kiosk tablets, the dev machine, and anything reaching in over Tailscale (100.64.0.0/10, the
# CGNAT range Tailscale assigns). Deliberately a regex and not a list - an install's kiosk origin
# is whatever IP or .local name that tablet happens to use, and hard-coding a list would break
# real deployments and push people straight back to "*".
#
# What it excludes is the point: https://evil.com does not match, so a public page cannot use a
# household browser as a proxy into the (largely unauthenticated) bridge. It is a same-origin
# control, not an authentication one - anything that can reach the port directly is unaffected,
# which is why network segmentation (Phase 0) remains the real perimeter.
LAN_ORIGIN_REGEX = (
    r"^https?://("
    r"localhost|127\.\d+\.\d+\.\d+|\[::1\]|"           # loopback
    r"10\.\d+\.\d+\.\d+|"                               # 10.0.0.0/8
    r"192\.168\.\d+\.\d+|"                              # 192.168.0.0/16
    r"172\.(1[6-9]|2\d|3[01])\.\d+\.\d+|"               # 172.16.0.0/12
    r"100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d+\.\d+|" # 100.64.0.0/10 (Tailscale)
    r"169\.254\.\d+\.\d+|"                              # link-local
    r"[A-Za-z0-9-]+(\.local|\.lan|\.home|\.internal)"   # mDNS / common LAN suffixes
    r")(:\d+)?$"
)


class TauCoreSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TAU_", env_file=".env", extra="ignore")

    cdg_rules_path: Path = PACKAGE_ROOT / "config" / "cdg_rules.yaml"
    servers_config_path: Path = PACKAGE_ROOT / "config" / "servers.yaml"
    # Where the pending-approval queue persists (Phase 7 Tier 0 item 5). Durable BY DEFAULT,
    # deliberately: the queue is the mechanism the whole architecture rests on, and an install
    # that silently drops every pending human decision on restart unless someone remembered to
    # set an env var is the same "unsafe unless configured" pattern the Hermes audit criticised
    # elsewhere. Compose points this at a volume. Set TAU_APPROVAL_STORE_PATH="" for a purely
    # in-memory queue (tests do this via conftest).
    approval_store_path: Path | None = PACKAGE_ROOT / "data" / "approvals.json"
    # Where device-approval state (Phase 27.A) persists - which devices an admin has approved,
    # and the sha256 hash of each one's minted bearer token. Durable by default for the same
    # reason as approvals above: an approved device losing its token on every restart would force
    # re-approval on every deploy. Set TAU_DEVICE_STORE_PATH="" for a purely in-memory registry
    # (tests do this via conftest); unrelated to whether device-token enforcement is turned on
    # (TAU_REQUIRE_DEVICE_TOKEN, added when the enforcement chokepoint lands).
    device_store_path: Path | None = PACKAGE_ROOT / "data" / "devices.json"
    # Where the default/anonymous session's conversation history persists (Phase 10.3 #11,
    # session persistence) - the REPL, the eval harness, and any client sending no
    # X-Tau-Device-Id all share this one session. Durable by default, same reasoning as
    # approval_store_path above: before this, a tau-core restart silently dropped every
    # in-progress conversation (SessionRegistry's own docstring said so). Set
    # TAU_SESSION_STORE_PATH="" for the old in-memory-only behavior.
    session_store_path: Path | None = PACKAGE_ROOT / "data" / "sessions.json"
    # Where the PER-DEVICE session registry (Phase 12) persists - one file holding every
    # identified device's own rolling history, keyed by device id, since SessionRegistry's
    # sessions all share a single JSON file rather than one file each (see
    # tau_core.session.store.save_session_map). Set TAU_DEVICE_SESSION_STORE_PATH="" to disable.
    device_session_store_path: Path | None = PACKAGE_ROOT / "data" / "device_sessions.json"
    # Phase 27.D Milestone 3: a second, TLS-terminated listener alongside the existing plain-HTTP
    # bridge, purely to give the desktop client's mDNS-discovery/pairing flow a real secure
    # transport to pair over. Off by default - this is additive, not a replacement for the
    # existing "LAN is the perimeter" plain-HTTP posture every current deployment (kiosk, browser)
    # already relies on; turning it on changes nothing for them, since they never point at
    # TAU_TLS_PORT. See tau_core.tls for cert generation and tau_core.discovery for the mDNS
    # advertisement this setting also gates.
    tls_enabled: bool = False
    tls_port: int = 8443
    tls_cert_path: Path = PACKAGE_ROOT / "data" / "tls" / "cert.pem"
    tls_key_path: Path = PACKAGE_ROOT / "data" / "tls" / "key.pem"
    # The proposing scheduler (Phase 10.3 #10 / §5.1's CVE-polling job, tau_core.scheduler) - runs
    # read-only tool polls on a timer and drafts a memory (no-approval-needed, human-reviewable)
    # when one finds something worth attention. On by default: every tool call it can make is
    # read-only and everything it writes is an unverified draft nobody has to act on, so there is
    # no unsafe direction to default toward the way there is for approval/session durability - the
    # worst case of leaving this on is an unreviewed draft sitting in the queue, and each job
    # self-skips whenever its target server isn't connected. Set TAU_SCHEDULER_ENABLED=false to
    # turn it off entirely.
    scheduler_enabled: bool = True
    # Intent-match hint threshold, not a safety control (the CDG is the safety layer and runs
    # regardless). Calibrated 2026-07-11 against median-of-3 router scores: correct read-only
    # calls score 0.5-0.9, mismatched ones 0.0-0.4, so 0.5 separates the two distributions
    # (0.6 sat inside the correct-call band and randomly blocked good calls).
    routing_confidence_threshold: float = 0.5
    session_max_messages: int = 50
    # How many per-device conversation sessions the bridge keeps in memory at once (Phase 12,
    # per-device sessions). Each identified device gets its own rolling history so one device's
    # conversation never enters the model's context on another device's turn. A home has a
    # handful of kiosks, so this cap is only a defence against unbounded growth from a client
    # spraying random X-Tau-Device-Id values; the least-recently-active session is evicted past
    # it (that device simply starts a fresh conversation on its next turn).
    session_max_devices: int = 64
    # Timeouts (Phase 7 Tier 0). Before these, there was not one timeout in tau_core/src outside
    # hardware.py: a hung Ollama or a wedged MCP server meant a request that never returned and
    # a worker that never came back. Every one of these bounds a *hang*, not slowness - they are
    # deliberately generous, because a local model on CPU is slow but not broken.
    #
    # One MCP tool call / list_tools / read_resource. Exceeding it marks that session dead, so
    # the next call reconnects instead of reusing a wedged one.
    mcp_call_timeout_seconds: float = 30.0
    # Opening + initializing one MCP session (spawning a stdio subprocess, or an HTTP handshake).
    mcp_connect_timeout_seconds: float = 20.0
    # One HTTP request to Ollama.
    llm_request_timeout_seconds: float = 120.0
    # One whole chat turn, which may span several model requests plus tool calls; this is the
    # ceiling on POST /api/chat, which returns 504 rather than hanging a kiosk tab forever.
    llm_turn_timeout_seconds: float = 300.0
    # The language Tau replies in by default (TAU_REPLY_LANGUAGE). English by default: smaller
    # local models (e.g. qwen2.5:7b) drift into Thai/Chinese when tool output accumulates, so
    # the reply language is a hard, configured default rather than "mirror the user" - the system
    # prompt pins it. Set this to another language name (e.g. "Spanish") to change the default;
    # a user can still explicitly ask for a different language mid-conversation. This is the seam
    # for adding more "language packs" later without touching prompt code.
    reply_language: str = "English"
    # Post-turn background learning (the reliable half of "everything per-speaker"). §8.B's
    # draft_memory was designed to be ungated-but-reviewable specifically so auto-memory could be
    # safe (see cdg_rules.yaml's memory-tree-draft-is-allowed), but it depends on MAIN_SYSTEM_PROMPT
    # asking the conversational model to notice something's worth remembering mid-turn - which the
    # plan doc already measured as unreliable on small local models, and which the tool-free fast
    # path (Phase 18) can never do at all, since it has no tools. This runs a small, separate
    # classifier over every finished turn (both paths) instead, and calls draft_memory directly
    # when it finds something - same draft tier, same human-review gate, just a trigger that
    # doesn't depend on the main model's attention. On by default, same reasoning as
    # scheduler_enabled above: the worst case is an unreviewed draft nobody acts on, not an unsafe
    # action. Runs as a background task AFTER the reply is already returned, so it never adds to
    # turn latency. Set TAU_BACKGROUND_MEMORY_LEARNING_ENABLED=false to turn it off.
    background_memory_learning_enabled: bool = True
    # Voice identity (multi-user; see project-tau-plan.md "multi-user voice identification").
    # Chroma reports L2 distance for voiceprint matches - LOWER is closer, and this ceiling
    # needs live calibration against real enrolled voices before being trusted; until then it
    # errs toward "unknown speaker" (which maps to lowest access, never false acceptance).
    voice_match_max_distance: float = 0.75
    # When true, approval decisions over the web bridge require a verified voice challenge
    # (HTTP 428 tells the UI to run the challenge flow); the verified person's access level is
    # then checked against tau_core.access.tiers, so elevated actions (e.g. exiting lockdown)
    # need an 'admin'-tier identity while routine approvals accept any verified voice (403 on
    # insufficient tier). Default off so a fresh install (nobody enrolled yet) can't lock
    # itself out.
    require_voice_approval: bool = False
    # Phase 27.A: when true, _device_id() (web/server.py) requires an identified device to
    # present a valid X-Tau-Device-Token bound to an admin-approved DeviceRegistry entry, 403'ing
    # otherwise instead of the current silent "device id grants nothing, but blocked ones are
    # refused" behaviour. Anonymous callers (no X-Tau-Device-Id header at all) are unaffected
    # either way; this only governs callers who identified themselves.
    #
    # Defaulted off from 2026-08-19 to 2026-09-16 deliberately (§8.28's 27.A order-of-work step
    # 6): enforcement needed to land on every device-aware route and soak in real use first, which
    # was time, not code. Phase 48 flips it now that it has - see project-tau-plan.md's Phase 48
    # recap. A device approved before this flip keeps its existing token and is unaffected; a
    # fresh install (or any device an admin hasn't approved yet) now gets 403 instead of silent
    # access, which is the point - approve real devices from the admin dashboard before relying on
    # this default in a live deployment. Set TAU_REQUIRE_DEVICE_TOKEN=false to go back to the
    # loud-but-open posture.
    require_device_token: bool = True
    # Phase 38: a static shared secret the standalone tau-admin-server uses to call this bridge's
    # admin-data surface (/api/devices, /api/admin/*, /api/transcript/unified, /api/drafts*,
    # /api/voice/wake-words) on behalf of a human it has already authenticated with a real
    # username/password login. Checked in _require_admin() alongside the voice-token path - either
    # credential satisfies the gate, since they now represent two different places a human proved
    # who they are (a spoken challenge here, a password on tau-admin-server). None (the default)
    # disables this path entirely; every _require_admin call site still works exactly as before
    # (voice-token or LAN-trust) when it's unset. Compared with hmac.compare_digest, same as every
    # other token check in this codebase. Set TAU_ADMIN_SERVICE_TOKEN to a long random value and
    # give tau-admin-server the identical value - this is a service-to-service secret, never
    # entered by a person, so it belongs in each process's .env, not a UI.
    admin_service_token: str | None = None
    # Rate limits (Phase 7 Tier 1 #9). Both endpoint groups are unauthenticated and each call is
    # expensive (a full LLM turn; a real STT/embedding inference) - a bound is a bar to entry,
    # not a security control. Voice's default is much higher because /api/voice/wake is a
    # continuous hands-free poll (~1 request/1.3s per listening device - see useWakeWord.js),
    # not an occasional human action like chat or enrollment; 120/60s gives a listening device
    # comfortable headroom while still capping a runaway/flooding client.
    chat_rate_limit_max: int = 20
    chat_rate_limit_window_seconds: float = 60.0
    voice_rate_limit_max: int = 120
    voice_rate_limit_window_seconds: float = 60.0
    # Which browser origins may call this bridge (TAU_ALLOWED_ORIGINS, comma-separated).
    #
    # Empty (the default) means "any private/LAN origin", enforced by the regex below rather than
    # by a fixed list. That default replaces `allow_origins=["*"]`, which was a real hole even on
    # a LAN-only deployment: most endpoints here have no auth, so with `*` any public website a
    # household browser happened to visit could script that browser into reading /api/approvals
    # and /api/activity, and into POSTing to /api/chat and /api/tools - the browser is inside the
    # network even when the attacker is not. A regex keeps every legitimate kiosk origin working
    # (they are all LAN addresses, and their exact host/port varies per install) while a page on
    # the public internet simply fails the CORS check.
    #
    # Set this explicitly to lock the bridge to known origins (e.g.
    # "http://tau.local:3000,http://192.168.1.20:3000"). Set it to "*" to restore the old
    # allow-anything behaviour - create_app logs a warning when you do.
    allowed_origins: str = ""
    # Phase 23: narrow each turn's toolset to the MCP servers the turn is plausibly about, instead
    # of handing the model all ~73 tools every time (TAU_SCOPE_SERVERS_PER_TURN).
    #
    # Default ON since 2026-07-28, on the strength of a one-variable A/B (§8.25 has the scores).
    # It roughly doubled tool-selection accuracy with no category regressing, and `no-tool` held
    # steady - meaning the narrower roster didn't induce over-calling, the risk worth watching.
    # The mechanism shows in the failures it fixed: given 73 tool schemas the model would pick the
    # right tool and then emit it as prose instead of a structured call; given ~3 servers' worth
    # it emits the call properly.
    #
    # Set TAU_SCOPE_SERVERS_PER_TURN=false to get the old every-server behaviour back (the code
    # path is unchanged and still tested - see test_flag_off_is_a_no_op).
    scope_servers_per_turn: bool = True

    def cors_origin_config(self) -> tuple[list[str], str | None]:
        """(allow_origins, allow_origin_regex) for CORSMiddleware, from `allowed_origins`.

        Exactly one of the two is meaningful at a time: an explicit list disables the regex, and
        the LAN default supplies a regex with an empty list.
        """
        configured = [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]
        if configured == ["*"]:
            return ["*"], None
        if configured:
            return configured, None
        return [], LAN_ORIGIN_REGEX

    @field_validator(
        "approval_store_path",
        "device_store_path",
        "session_store_path",
        "device_session_store_path",
        mode="before",
    )
    @classmethod
    def _blank_path_means_disabled(cls, value):
        """Maps an empty TAU_*_STORE_PATH to None (in-memory), not to Path(".").

        Without this, the documented "set it empty to disable persistence" escape hatch would
        coerce to `Path('.')` - the current working directory - and the store would try to write
        its JSON file *over a directory*. An opt-out that silently does something else is worse
        than no opt-out.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value
