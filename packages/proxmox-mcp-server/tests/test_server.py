import json
import pytest
from unittest.mock import patch, MagicMock

from proxmox_mcp_server.server import (
    list_vms,
    get_vm_status,
    snapshot_vm,
    create_lxc,
    restart_service,
    apply_update,
    check_cve_advisories,
)


@pytest.fixture
def mock_client(monkeypatch):
    """Inject a mock ProxmoxClient into server functions."""
    mock = MagicMock()
    monkeypatch.setattr("proxmox_mcp_server.server._proxmox_client", mock)
    return mock


def test_list_vms_returns_json(mock_client):
    """list_vms tool returns JSON string."""
    mock_client.list_vms.return_value = [
        {"vmid": 100, "name": "vm1", "status": "running", "type": "vm", "node": "node1"},
        {"vmid": 200, "name": "ct1", "status": "running", "type": "lxc", "node": "node1"},
    ]

    result = list_vms()
    data = json.loads(result)

    assert len(data) == 2
    assert data[0]["vmid"] == 100
    assert data[1]["type"] == "lxc"


def test_get_vm_status_returns_json(mock_client):
    """get_vm_status tool returns JSON string."""
    mock_client.get_vm_status.return_value = {
        "vmid": 100,
        "type": "vm",
        "node": "node1",
        "status": {"status": "running"},
    }

    result = get_vm_status(100)
    data = json.loads(result)

    assert data["vmid"] == 100
    assert data["status"]["status"] == "running"


def test_snapshot_vm_returns_success(mock_client):
    """snapshot_vm tool returns success response."""
    mock_client.snapshot_vm.return_value = {
        "success": True,
        "vmid": 100,
        "snapshot": "snap-001",
    }

    result = snapshot_vm(100, "snap-001")
    data = json.loads(result)

    assert data["success"] is True
    assert data["snapshot"] == "snap-001"


def test_create_lxc_returns_success(mock_client):
    """create_lxc tool returns success response."""
    mock_client.create_lxc.return_value = {
        "success": True,
        "vmid": 300,
        "hostname": "new-ct",
        "node": "node1",
    }

    result = create_lxc("node1", 300, "new-ct", "debian-11")
    data = json.loads(result)

    assert data["success"] is True
    assert data["vmid"] == 300


def test_restart_service_returns_success(mock_client):
    """restart_service tool returns success response."""
    mock_client.restart_service.return_value = {
        "success": True,
        "service": "pveproxy",
        "node": "node1",
    }

    result = restart_service("node1", "pveproxy")
    data = json.loads(result)

    assert data["success"] is True


def test_apply_update_returns_success(mock_client):
    """apply_update tool returns success response."""
    mock_client.apply_update.return_value = {
        "success": True,
        "node": "node1",
        "message": "Updates applied",
    }

    result = apply_update("node1")
    data = json.loads(result)

    assert data["success"] is True


def test_check_cve_advisories_returns_list(mock_client):
    """check_cve_advisories tool returns advisory list."""
    mock_client.check_cve_advisories.return_value = [
        {"severity": "info", "message": "CVE check stub"}
    ]

    result = check_cve_advisories()
    data = json.loads(result)

    assert isinstance(data, list)
