# Network segmentation per project-tau-plan.md Section 3: management / core-agent / IoT / camera
# VLANs, so the firewall in proxmox_firewall.tf can allow "Tau Core -> MCP servers" only, with
# no lateral movement between server VLANs.
#
# Proxmox SDN resource support in the bpg/proxmox provider varies by provider and Proxmox VE
# version. If `proxmox_virtual_environment_sdn_vnet`/`_zone` below don't match your installed
# provider version's schema (check `terraform providers schema -json` or the provider's registry
# page for your pinned version), create this same VLAN layout once by hand instead:
#   Datacenter > SDN > Zones > Add "Simple" zone "tauzone"
#   Datacenter > SDN > VNets > Add one VNet per row in var.vlan_ids, tagged with that VLAN ID,
#     all attached to zone "tauzone" and bridge var.network_bridge
#   Datacenter > SDN > Apply
# Then delete this file's resources and just reference the VLAN tag IDs directly in
# proxmox_vms.tf's network_device blocks (which this scaffold already does via var.vlan_ids,
# so no other file needs to change).

resource "proxmox_virtual_environment_sdn_zone_simple" "tauzone" {
  id    = "tauzone"
  nodes = [var.proxmox_node_name]
}

resource "proxmox_virtual_environment_sdn_vnet" "management" {
  name    = "vlan-mgmt"
  zone    = proxmox_virtual_environment_sdn_zone_simple.tauzone.id
  tag     = var.vlan_ids.management
  comment = "Proxmox/Ansible management plane"
}

resource "proxmox_virtual_environment_sdn_vnet" "core_agent" {
  name    = "vlan-core"
  zone    = proxmox_virtual_environment_sdn_zone_simple.tauzone.id
  tag     = var.vlan_ids.core_agent
  comment = "Tau Core, k3s, Vault, Ollama"
}

resource "proxmox_virtual_environment_sdn_vnet" "iot" {
  name    = "vlan-iot"
  zone    = proxmox_virtual_environment_sdn_zone_simple.tauzone.id
  tag     = var.vlan_ids.iot
  comment = "Home Assistant / IoT devices"
}

resource "proxmox_virtual_environment_sdn_vnet" "camera" {
  name    = "vlan-cam"
  zone    = proxmox_virtual_environment_sdn_zone_simple.tauzone.id
  tag     = var.vlan_ids.camera
  comment = "PTZ / vision cameras"
}

resource "proxmox_virtual_environment_sdn_apply" "this" {
  depends_on = [
    proxmox_virtual_environment_sdn_vnet.management,
    proxmox_virtual_environment_sdn_vnet.core_agent,
    proxmox_virtual_environment_sdn_vnet.iot,
    proxmox_virtual_environment_sdn_vnet.camera,
  ]
}
