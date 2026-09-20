import json

import pytest

from utility_mcp_server.server import (
    describe_capabilities,
    get_date,
    get_identity_resource,
    get_system_status,
    get_time,
)


def test_get_time_returns_iso_and_fields():
    data = json.loads(get_time())
    assert "iso" in data and "T" in data["iso"]
    assert ":" in data["time"]
    # tzname/offset present because _now() always returns an aware datetime.
    assert data["utc_offset"] != ""


def test_get_time_honors_tau_timezone(monkeypatch):
    monkeypatch.setenv("TAU_TIMEZONE", "America/New_York")
    data = json.loads(get_time())
    # Eastern is either EST or EDT depending on the date; both are valid, just not None/local.
    assert data["timezone"] in {"EST", "EDT"}


def test_get_time_bad_timezone_degrades_to_local(monkeypatch):
    monkeypatch.setenv("TAU_TIMEZONE", "Not/ARealZone")
    # Must not raise - a bad tz name falls back to local time.
    data = json.loads(get_time())
    assert "iso" in data


def test_get_date_returns_weekday_and_human():
    data = json.loads(get_date())
    assert data["weekday"] in {
        "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    }
    # Human form has no leading-zero day and no stray "0" from a platform directive.
    assert data["weekday"] in data["human"]
    assert ", " in data["human"]


def test_get_system_status_reports_model_and_uptime(monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5:7b-instruct")
    monkeypatch.setenv("OLLAMA_ROUTER_MODEL", "qwen2.5:7b-instruct")
    data = json.loads(get_system_status())
    assert data["current_model"] == "qwen2.5:7b-instruct"
    assert data["current_router_model"] == "qwen2.5:7b-instruct"
    assert data["uptime_seconds"] >= 0
    # hardware block always present, even if tau_core.hardware is absent (fields may be None).
    assert "cpu_cores" in data["hardware"]
    # Phase 10.5: multi-GPU breakdown rides alongside the combined gpu_vram_mb total, present
    # (possibly None/empty) whether tau_core.hardware is importable here or not.
    assert "gpu_count" in data["hardware"]
    assert "gpu_vram_mb_per_device" in data["hardware"]


def test_describe_capabilities_lists_servers():
    data = json.loads(describe_capabilities())
    assert data["server_count"] >= 1
    names = {s["name"] for s in data["servers"]}
    # utility-mcp-server describes itself; source is registry (servers.yaml found) or catalog.
    assert "utility-mcp-server" in names
    assert data["source"] in {"registry", "catalog"}
    for entry in data["servers"]:
        assert "purpose" in entry and "example_tools" in entry


def test_describe_capabilities_reads_registry_when_present(monkeypatch, tmp_path):
    reg = tmp_path / "servers.yaml"
    reg.write_text(
        "servers:\n"
        "  - name: home-assistant-mcp-server\n"
        "    command: python\n"
        "  - name: some-future-server\n"
        "    command: python\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TAU_SERVERS_CONFIG_PATH", str(reg))
    data = json.loads(describe_capabilities())
    assert data["source"] == "registry"
    names = {s["name"] for s in data["servers"]}
    assert names == {"home-assistant-mcp-server", "some-future-server"}
    # A registered server with no catalog entry is still listed, just without a purpose.
    future = next(s for s in data["servers"] if s["name"] == "some-future-server")
    assert future["purpose"] == "(no description available)"


def test_get_identity_resource_is_factual():
    data = json.loads(get_identity_resource())
    assert data["name"] == "TAU"
    assert "MCP" in data["kind"]
    assert isinstance(data["operating_rules"], list) and len(data["operating_rules"]) >= 3
    # The CDG is the load-bearing rule; it must be stated.
    assert any("CDG" in rule or "Core Directive Guard" in rule for rule in data["operating_rules"])
