output "k3s_server_vms" {
  description = "Name -> VM ID for k3s server node(s). Resolve IPs via the Proxmox UI/qemu-guest-agent or your DHCP server, then populate infra/ansible/inventory/hosts.yml."
  value       = { for name, vm in proxmox_virtual_environment_vm.k3s_server : name => vm.vm_id }
}

output "k3s_agent_vms" {
  description = "Name -> VM ID for k3s agent node(s)"
  value       = { for name, vm in proxmox_virtual_environment_vm.k3s_agent : name => vm.vm_id }
}

output "ollama_vm" {
  description = "VM ID for the Ollama host"
  value       = proxmox_virtual_environment_vm.ollama.vm_id
}
