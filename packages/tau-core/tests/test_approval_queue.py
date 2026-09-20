from datetime import timedelta

import pytest

from tau_core.approval.queue import ApprovalError, ApprovalStatus, PendingActionQueue
from tau_core.hashing import hash_arguments


def test_submit_creates_pending_request():
    queue = PendingActionQueue()
    request = queue.submit(
        server="proxmox-mcp-server",
        tool="shutdown_host",
        arguments={"vmid": 101},
        reason="user asked to power down the media VM",
        requested_by="tau-core",
    )
    assert request.status is ApprovalStatus.PENDING
    assert request in queue.list_pending()


def test_approve_then_convert_to_approved_action():
    queue = PendingActionQueue()
    request = queue.submit(
        server="proxmox-mcp-server",
        tool="shutdown_host",
        arguments={"vmid": 101},
        reason="test",
        requested_by="tau-core",
    )
    queue.approve(request.id, approved_by="zion")
    approved = queue.to_approved_action(request.id)
    assert approved.server == "proxmox-mcp-server"
    assert approved.tool == "shutdown_host"
    assert approved.arguments_hash == hash_arguments({"vmid": 101})
    assert approved.approved_by == "zion"
    assert request not in queue.list_pending()


def test_deny_prevents_conversion_to_approved_action():
    queue = PendingActionQueue()
    request = queue.submit(
        server="proxmox-mcp-server",
        tool="shutdown_host",
        arguments={"vmid": 101},
        reason="test",
        requested_by="tau-core",
    )
    queue.deny(request.id, denied_by="zion", note="not now")
    with pytest.raises(ApprovalError):
        queue.to_approved_action(request.id)


def test_cannot_decide_twice():
    queue = PendingActionQueue()
    request = queue.submit(
        server="proxmox-mcp-server",
        tool="shutdown_host",
        arguments={},
        reason="test",
        requested_by="tau-core",
    )
    queue.approve(request.id, approved_by="zion")
    with pytest.raises(ApprovalError):
        queue.approve(request.id, approved_by="zion")


def test_expired_request_cannot_be_approved():
    queue = PendingActionQueue()
    request = queue.submit(
        server="proxmox-mcp-server",
        tool="shutdown_host",
        arguments={},
        reason="test",
        requested_by="tau-core",
        ttl=timedelta(seconds=-1),
    )
    assert request not in queue.list_pending()
    with pytest.raises(ApprovalError):
        queue.approve(request.id, approved_by="zion")


def test_unknown_request_id_raises():
    queue = PendingActionQueue()
    with pytest.raises(ApprovalError):
        queue.get("does-not-exist")
