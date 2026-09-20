variable "proxmox_api_url" {
  description = "Proxmox API endpoint, e.g. https://pve.example.lan:8006/"
  type        = string
}

variable "proxmox_api_token_id" {
  description = "Proxmox API token ID, e.g. terraform@pve!terraform"
  type        = string
}

variable "proxmox_api_token_secret" {
  description = "Proxmox API token secret"
  type        = string
  sensitive   = true
}

variable "proxmox_tls_insecure" {
  description = "Skip TLS verification against the Proxmox API (set false once you trust the cert, e.g. internal CA)"
  type        = bool
  default     = true
}

variable "proxmox_ssh_username" {
  description = "SSH user Terraform uses for node-level operations (file upload, qemu-guest-agent wait)"
  type        = string
  default     = "root"
}

variable "proxmox_node_name" {
  description = "Name of the target Proxmox node as it appears in the cluster (single-node homelab: your host's node name)"
  type        = string
}

variable "vlan_ids" {
  description = "VLAN tag IDs for each network segment described in project-tau-plan.md Section 3"
  type = object({
    management = number
    core_agent = number
    iot        = number
    camera     = number
  })
  default = {
    management = 10
    core_agent = 20
    iot        = 30
    camera     = 40
  }
}

variable "network_bridge" {
  description = "Proxmox Linux bridge the VLAN-tagged VM NICs attach to (must already exist, e.g. vmbr0)"
  type        = string
  default     = "vmbr0"
}

variable "ssh_public_key" {
  description = "Public key injected into every VM via cloud-init for Ansible access (no password auth)"
  type        = string
}

variable "k3s_server_count" {
  description = "Number of k3s server (control-plane) nodes. 1 is fine for a home lab."
  type        = number
  default     = 1
}

variable "k3s_agent_count" {
  description = "Number of k3s agent (worker) nodes"
  type        = number
  default     = 2
}

variable "k3s_vm_cores" {
  type    = number
  default = 2
}

variable "k3s_vm_memory_mb" {
  type    = number
  default = 4096
}

variable "k3s_vm_disk_gb" {
  type    = number
  default = 40
}

variable "ubuntu_cloud_image_url" {
  description = "Ubuntu cloud image used as the VM template base, downloaded into Proxmox's local storage as a content of type 'iso'/'vztmpl' snippet by the operator (see README) before apply"
  type        = string
  default     = "https://cloud-images.ubuntu.com/jammy/current/jammy-server-cloudimg-amd64.img"
}

variable "vm_storage_pool" {
  description = "Proxmox storage pool for VM disks, e.g. local-lvm"
  type        = string
  default     = "local-lvm"
}

variable "enable_ollama_gpu_passthrough" {
  description = "Attach a PCI GPU device to the Ollama VM. Only enable if your Proxmox host has IOMMU/VT-d set up and a GPU reserved for passthrough."
  type        = bool
  default     = false
}

variable "ollama_gpu_pci_id" {
  description = "PCI address of the GPU to pass through, e.g. 0000:01:00.0 (only used if enable_ollama_gpu_passthrough is true)"
  type        = string
  default     = ""
}

variable "ollama_vm_cores" {
  type    = number
  default = 4
}

variable "ollama_vm_memory_mb" {
  type    = number
  default = 8192
}

variable "ollama_vm_disk_gb" {
  type    = number
  default = 80
}
