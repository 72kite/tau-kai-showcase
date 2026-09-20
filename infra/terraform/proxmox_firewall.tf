# Datacenter-level firewall: core-agent VLAN may initiate to any other VLAN (Tau Core reaching
# out to MCP servers on IoT/camera segments); nothing may initiate into core-agent except
# management (for Ansible/kubectl/SSH from the operator). No VLAN-to-VLAN lateral movement
# otherwise. This is the Terraform-expressible half of Section 3's "network segmentation" item;
# see infra/README.md for the manual Proxmox GUI fallback if your provider version doesn't
# support proxmox_virtual_environment_firewall_rules at the datacenter scope.

resource "proxmox_virtual_environment_firewall_options" "datacenter" {
  enabled = true
}

resource "proxmox_virtual_environment_firewall_rules" "core_agent_egress" {
  node_name = var.proxmox_node_name

  rule {
    security_group = null
    type           = "out"
    action         = "ACCEPT"
    comment        = "core-agent VLAN may reach IoT VLAN (Home Assistant etc.)"
    dest           = "+vlan-iot"
    source         = "+vlan-core"
  }

  rule {
    type    = "out"
    action  = "ACCEPT"
    comment = "core-agent VLAN may reach camera VLAN (vision-mcp-server)"
    dest    = "+vlan-cam"
    source  = "+vlan-core"
  }

  rule {
    type    = "in"
    action  = "ACCEPT"
    comment = "management VLAN may reach core-agent VLAN (operator SSH/kubectl/Ansible)"
    dest    = "+vlan-core"
    source  = "+vlan-mgmt"
  }

  rule {
    type    = "in"
    action  = "DROP"
    comment = "deny IoT VLAN initiating into core-agent"
    dest    = "+vlan-core"
    source  = "+vlan-iot"
  }

  rule {
    type    = "in"
    action  = "DROP"
    comment = "deny camera VLAN initiating into core-agent"
    dest    = "+vlan-core"
    source  = "+vlan-cam"
  }

  rule {
    type    = "in"
    action  = "DROP"
    comment = "deny IoT <-> camera lateral movement"
    dest    = "+vlan-cam"
    source  = "+vlan-iot"
  }
}
