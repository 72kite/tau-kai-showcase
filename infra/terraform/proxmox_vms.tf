# k3s server/agent VMs and the Ollama VM, all on the core-agent VLAN (var.vlan_ids.core_agent).
# Built from a plain Ubuntu cloud image + cloud-init, so the same image is reused for every role;
# Ansible (../ansible) does all role-specific configuration after boot.

resource "proxmox_virtual_environment_download_file" "ubuntu_cloud_image" {
  content_type = "import"
  datastore_id = var.vm_storage_pool
  node_name    = var.proxmox_node_name
  url          = var.ubuntu_cloud_image_url
  file_name    = "jammy-server-cloudimg-amd64.img"
  overwrite    = false
}

locals {
  k3s_server_names = [for i in range(var.k3s_server_count) : "tau-k3s-server-${i + 1}"]
  k3s_agent_names  = [for i in range(var.k3s_agent_count) : "tau-k3s-agent-${i + 1}"]
}

# --- k3s server node(s) ---
resource "proxmox_virtual_environment_vm" "k3s_server" {
  for_each  = toset(local.k3s_server_names)
  name      = each.value
  node_name = var.proxmox_node_name
  tags      = ["tau", "k3s", "server"]

  cpu {
    cores = var.k3s_vm_cores
  }
  memory {
    dedicated = var.k3s_vm_memory_mb
  }

  disk {
    datastore_id = var.vm_storage_pool
    import_from  = proxmox_virtual_environment_download_file.ubuntu_cloud_image.id
    interface    = "scsi0"
    size         = var.k3s_vm_disk_gb
  }

  network_device {
    bridge  = var.network_bridge
    vlan_id = var.vlan_ids.core_agent
  }

  initialization {
    ip_config {
      ipv4 {
        address = "dhcp"
      }
    }
    user_account {
      username = "tau"
      keys     = [var.ssh_public_key]
    }
  }

  agent {
    enabled = true
  }
}

# --- k3s agent node(s) ---
resource "proxmox_virtual_environment_vm" "k3s_agent" {
  for_each  = toset(local.k3s_agent_names)
  name      = each.value
  node_name = var.proxmox_node_name
  tags      = ["tau", "k3s", "agent"]

  cpu {
    cores = var.k3s_vm_cores
  }
  memory {
    dedicated = var.k3s_vm_memory_mb
  }

  disk {
    datastore_id = var.vm_storage_pool
    import_from  = proxmox_virtual_environment_download_file.ubuntu_cloud_image.id
    interface    = "scsi0"
    size         = var.k3s_vm_disk_gb
  }

  network_device {
    bridge  = var.network_bridge
    vlan_id = var.vlan_ids.core_agent
  }

  initialization {
    ip_config {
      ipv4 {
        address = "dhcp"
      }
    }
    user_account {
      username = "tau"
      keys     = [var.ssh_public_key]
    }
  }

  agent {
    enabled = true
  }
}

# --- Ollama VM (kept outside k3s; see infra/README.md) ---
resource "proxmox_virtual_environment_vm" "ollama" {
  name      = "tau-ollama"
  node_name = var.proxmox_node_name
  tags      = ["tau", "ollama"]

  cpu {
    cores = var.ollama_vm_cores
  }
  memory {
    dedicated = var.ollama_vm_memory_mb
  }

  disk {
    datastore_id = var.vm_storage_pool
    import_from  = proxmox_virtual_environment_download_file.ubuntu_cloud_image.id
    interface    = "scsi0"
    size         = var.ollama_vm_disk_gb
  }

  network_device {
    bridge  = var.network_bridge
    vlan_id = var.vlan_ids.core_agent
  }

  initialization {
    ip_config {
      ipv4 {
        address = "dhcp"
      }
    }
    user_account {
      username = "tau"
      keys     = [var.ssh_public_key]
    }
  }

  agent {
    enabled = true
  }

  # Only attached if enable_ollama_gpu_passthrough=true. Requires IOMMU/VT-d enabled on the
  # Proxmox host and the GPU already isolated with vfio-pci (a host-level, not Terraform, step).
  dynamic "hostpci" {
    for_each = var.enable_ollama_gpu_passthrough ? [1] : []
    content {
      device = "hostpci0"
      id     = var.ollama_gpu_pci_id
      pcie   = true
    }
  }
}
