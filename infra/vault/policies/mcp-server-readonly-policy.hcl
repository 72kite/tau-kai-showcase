# Template for future Phase 2 MCP servers (proxmox-mcp-server, home-assistant-mcp-server, etc.):
# copy this file, replace SERVER_NAME, and bind it to that server's own Vault role/ServiceAccount
# so each server can only read its own secrets — never another server's, and never tau-core's.
path "secret/data/SERVER_NAME/*" {
  capabilities = ["read"]
}

path "secret/metadata/SERVER_NAME/*" {
  capabilities = ["list", "read"]
}
