import pytest
from unittest.mock import Mock, patch, MagicMock
from proxmox_mcp_server.client import ProxmoxClient


@pytest.fixture
def mock_proxmox_api():
    """Mock the ProxmoxAPI to avoid real network calls."""
    with patch("proxmox_mcp_server.client.ProxmoxAPI") as mock_api:
        yield mock_api


def test_client_requires_password():
    """ProxmoxClient raises if PROXMOX_PASSWORD is not set."""
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(RuntimeError, match="PROXMOX_PASSWORD"):
            ProxmoxClient()


def test_list_vms_returns_vms_and_containers(mock_proxmox_api):
    """list_vms returns both QEMU VMs and LXC containers from all nodes."""
    mock_instance = MagicMock()
    mock_proxmox_api.return_value = mock_instance

    # Mock nodes
    mock_instance.nodes.get.return_value = [
        {"node": "node1"},
    ]

    # Mock QEMU VMs on node1
    mock_instance.nodes("node1").qemu.get.return_value = [
        {"vmid": 100, "name": "web-server", "status": "running"},
        {"vmid": 101, "name": "db-server", "status": "stopped"},
    ]

    # Mock LXC containers on node1
    mock_instance.nodes("node1").lxc.get.return_value = [
        {"vmid": 200, "name": "app-container", "status": "running"},
    ]

    with patch.dict("os.environ", {"PROXMOX_PASSWORD": "test"}):
        client = ProxmoxClient()
        client.proxmox = mock_instance

        vms = client.list_vms()

    assert len(vms) == 3
    assert vms[0]["type"] == "vm"
    assert vms[0]["vmid"] == 100
    assert vms[2]["type"] == "lxc"
    assert vms[2]["vmid"] == 200


def test_get_vm_status_finds_qemu_vm(mock_proxmox_api):
    """get_vm_status returns status of a QEMU VM."""
    mock_instance = MagicMock()
    mock_proxmox_api.return_value = mock_instance

    mock_instance.nodes.get.return_value = [{"node": "node1"}]
    mock_instance.nodes("node1").qemu(100).status.current.get.return_value = {
        "status": "running"
    }
    mock_instance.nodes("node1").lxc(100).status.current.get.side_effect = Exception(
        "Not LXC"
    )

    with patch.dict("os.environ", {"PROXMOX_PASSWORD": "test"}):
        client = ProxmoxClient()
        client.proxmox = mock_instance

        status = client.get_vm_status(100)

    assert status["vmid"] == 100
    assert status["type"] == "vm"
    assert status["status"]["status"] == "running"


def test_get_vm_status_finds_lxc_container(mock_proxmox_api):
    """get_vm_status returns status of an LXC container."""
    mock_instance = MagicMock()
    mock_proxmox_api.return_value = mock_instance

    mock_instance.nodes.get.return_value = [{"node": "node1"}]
    mock_instance.nodes("node1").qemu(200).status.current.get.side_effect = Exception(
        "Not QEMU"
    )
    mock_instance.nodes("node1").lxc(200).status.current.get.return_value = {
        "status": "running"
    }

    with patch.dict("os.environ", {"PROXMOX_PASSWORD": "test"}):
        client = ProxmoxClient()
        client.proxmox = mock_instance

        status = client.get_vm_status(200)

    assert status["vmid"] == 200
    assert status["type"] == "lxc"
    assert status["status"]["status"] == "running"


def test_get_vm_status_raises_if_not_found(mock_proxmox_api):
    """get_vm_status raises ValueError if VM is not found."""
    mock_instance = MagicMock()
    mock_proxmox_api.return_value = mock_instance

    mock_instance.nodes.get.return_value = [{"node": "node1"}]
    mock_instance.nodes("node1").qemu(999).status.current.get.side_effect = Exception(
        "Not found"
    )
    mock_instance.nodes("node1").lxc(999).status.current.get.side_effect = Exception(
        "Not found"
    )

    with patch.dict("os.environ", {"PROXMOX_PASSWORD": "test"}):
        client = ProxmoxClient()
        client.proxmox = mock_instance

        with pytest.raises(ValueError, match="not found"):
            client.get_vm_status(999)


def test_snapshot_vm(mock_proxmox_api):
    """snapshot_vm creates a snapshot."""
    mock_instance = MagicMock()
    mock_proxmox_api.return_value = mock_instance

    mock_instance.nodes.get.return_value = [{"node": "node1"}]
    mock_instance.nodes("node1").qemu(100).snapshot.post.return_value = {
        "upid": "..."
    }

    with patch.dict("os.environ", {"PROXMOX_PASSWORD": "test"}):
        client = ProxmoxClient()
        client.proxmox = mock_instance

        result = client.snapshot_vm(100, "snap-001")

    assert result["success"] is True
    assert result["vmid"] == 100
    assert result["snapshot"] == "snap-001"


def test_create_lxc(mock_proxmox_api):
    """create_lxc creates a new container."""
    mock_instance = MagicMock()
    mock_proxmox_api.return_value = mock_instance

    mock_instance.nodes("node1").lxc.post.return_value = {"upid": "..."}

    with patch.dict("os.environ", {"PROXMOX_PASSWORD": "test"}):
        client = ProxmoxClient()
        client.proxmox = mock_instance

        result = client.create_lxc("node1", 300, "new-container", "debian-11")

    assert result["success"] is True
    assert result["vmid"] == 300
    assert result["hostname"] == "new-container"


def test_restart_service(mock_proxmox_api):
    """restart_service restarts a service."""
    mock_instance = MagicMock()
    mock_proxmox_api.return_value = mock_instance

    mock_instance.nodes("node1").services("pveproxy").restart.post.return_value = {}

    with patch.dict("os.environ", {"PROXMOX_PASSWORD": "test"}):
        client = ProxmoxClient()
        client.proxmox = mock_instance

        result = client.restart_service("node1", "pveproxy")

    assert result["success"] is True
    assert result["service"] == "pveproxy"


def test_apply_update(mock_proxmox_api):
    """apply_update applies pending updates."""
    mock_instance = MagicMock()
    mock_proxmox_api.return_value = mock_instance

    mock_instance.nodes("node1").apt.update.post.return_value = {}

    with patch.dict("os.environ", {"PROXMOX_PASSWORD": "test"}):
        client = ProxmoxClient()
        client.proxmox = mock_instance

        result = client.apply_update("node1")

    assert result["success"] is True
    assert "Updates applied" in result["message"]


def test_check_cve_advisories(mock_proxmox_api):
    """check_cve_advisories returns advisory list."""
    with patch.dict("os.environ", {"PROXMOX_PASSWORD": "test"}):
        client = ProxmoxClient()

        advisories = client.check_cve_advisories()

    assert isinstance(advisories, list)
    assert len(advisories) > 0
