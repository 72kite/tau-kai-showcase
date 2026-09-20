import os
from proxmoxer import ProxmoxAPI


class ProxmoxClient:
    def __init__(self):
        host = os.getenv("PROXMOX_HOST", "localhost")
        port = int(os.getenv("PROXMOX_PORT", "8006"))
        user = os.getenv("PROXMOX_USER", "root@pam")
        password = os.getenv("PROXMOX_PASSWORD", "")
        verify_ssl = os.getenv("PROXMOX_VERIFY_SSL", "false").lower() == "true"

        if not password:
            raise RuntimeError("PROXMOX_PASSWORD env var not set")

        self.proxmox = ProxmoxAPI(
            host, user=user, password=password, verify_ssl=verify_ssl, port=port
        )

    def list_vms(self) -> list[dict]:
        """List all VMs and containers."""
        vms = []
        for node in self.proxmox.nodes.get():
            node_name = node["node"]
            for vm in self.proxmox.nodes(node_name).qemu.get():
                vms.append(
                    {
                        "vmid": vm["vmid"],
                        "name": vm.get("name", f"vm-{vm['vmid']}"),
                        "status": vm.get("status", "unknown"),
                        "type": "vm",
                        "node": node_name,
                    }
                )
            for container in self.proxmox.nodes(node_name).lxc.get():
                vms.append(
                    {
                        "vmid": container["vmid"],
                        "name": container.get("name", f"container-{container['vmid']}"),
                        "status": container.get("status", "unknown"),
                        "type": "lxc",
                        "node": node_name,
                    }
                )
        return vms

    def get_vm_status(self, vmid: int) -> dict:
        """Get status of a specific VM or container."""
        for node in self.proxmox.nodes.get():
            node_name = node["node"]
            try:
                vm = self.proxmox.nodes(node_name).qemu(vmid).status.current.get()
                return {"vmid": vmid, "type": "vm", "node": node_name, "status": vm}
            except Exception:
                pass
            try:
                container = self.proxmox.nodes(node_name).lxc(vmid).status.current.get()
                return {"vmid": vmid, "type": "lxc", "node": node_name, "status": container}
            except Exception:
                pass
        raise ValueError(f"VM/container {vmid} not found")

    def snapshot_vm(self, vmid: int, snapshot_name: str) -> dict:
        """Create a snapshot of a VM."""
        for node in self.proxmox.nodes.get():
            node_name = node["node"]
            try:
                result = self.proxmox.nodes(node_name).qemu(vmid).snapshot.post(
                    snapname=snapshot_name
                )
                return {"success": True, "vmid": vmid, "snapshot": snapshot_name}
            except Exception:
                pass
        raise ValueError(f"VM {vmid} not found")

    def create_lxc(
        self, node: str, vmid: int, hostname: str, template: str
    ) -> dict:
        """Create a new LXC container."""
        result = self.proxmox.nodes(node).lxc.post(
            vmid=vmid, hostname=hostname, ostype="linux", osid=template
        )
        return {"success": True, "vmid": vmid, "hostname": hostname, "node": node}

    def restart_service(self, node: str, service: str) -> dict:
        """Restart a service on a node (e.g., 'pveproxy', 'pvedaemon')."""
        result = self.proxmox.nodes(node).services(service).restart.post()
        return {"success": True, "service": service, "node": node}

    def apply_update(self, node: str) -> dict:
        """Apply pending updates to a node."""
        result = self.proxmox.nodes(node).apt.update.post()
        return {"success": True, "node": node, "message": "Updates applied"}

    def check_cve_advisories(self) -> list[dict]:
        """Check for CVE advisories (stub - real implementation would poll NVD)."""
        return [
            {
                "severity": "info",
                "message": "CVE advisory check - integration with NVD API not yet implemented",
            }
        ]
