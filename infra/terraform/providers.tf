# Authenticates with a scoped Proxmox API token, never the root account.
#
# Create the token once by hand before running Terraform:
#   Datacenter > Permissions > API Tokens > Add
#     User: a dedicated "terraform@pve" user (not root@pam)
#     Token ID: terraform
#     Uncheck "Privilege Separation" only if you understand the tradeoff;
#     otherwise grant the token's paired role the minimum needed:
#       VM.Allocate, VM.Config.*, VM.Audit, Datastore.AllocateSpace,
#       SDN.Allocate (if using Proxmox SDN resources below), Sys.Modify (firewall).
provider "proxmox" {
  endpoint  = var.proxmox_api_url
  api_token = "${var.proxmox_api_token_id}=${var.proxmox_api_token_secret}"
  insecure  = var.proxmox_tls_insecure

  ssh {
    agent    = false
    username = var.proxmox_ssh_username
  }
}
