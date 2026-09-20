// Phase 27.D Milestone 3: browses for tau-core's mDNS advertisement (see
// packages/tau-core/src/tau_core/discovery.py, the server-side half of this) instead of a human
// typing an IP. Unverified against a real build at the time of writing - see main.rs's header.

use serde::Serialize;
use std::time::Duration;

const SERVICE_TYPE: &str = "_tau._tcp.local.";
// Long enough for a typical LAN's mDNS round trip (a browse is a UDP multicast query + wait for
// replies, not a single request/response), short enough that a "Discover" button in the UI
// doesn't feel hung if nothing answers.
const BROWSE_TIMEOUT: Duration = Duration::from_secs(3);

#[derive(Serialize, Clone)]
pub struct DiscoveredService {
    pub name: String,
    pub host: String,
    pub port: u16,
}

#[tauri::command]
pub async fn discover_tau_core() -> Result<Vec<DiscoveredService>, String> {
    let mdns = mdns_sd::ServiceDaemon::new().map_err(|e| e.to_string())?;
    let receiver = mdns.browse(SERVICE_TYPE).map_err(|e| e.to_string())?;

    let mut found = Vec::new();
    let deadline = tokio::time::Instant::now() + BROWSE_TIMEOUT;
    loop {
        let remaining = deadline.saturating_duration_since(tokio::time::Instant::now());
        if remaining.is_zero() {
            break;
        }
        match tokio::time::timeout(remaining, receiver.recv_async()).await {
            Ok(Ok(mdns_sd::ServiceEvent::ServiceResolved(info))) => {
                let host = info
                    .get_addresses()
                    .iter()
                    .next()
                    .map(|addr| addr.to_string())
                    .unwrap_or_default();
                found.push(DiscoveredService {
                    name: info.get_fullname().to_string(),
                    host,
                    port: info.get_port(),
                });
            }
            // Any other event (ServiceFound before resolution, a search-stopped signal, or the
            // timeout itself) - keep waiting out the remaining budget rather than treating it as
            // a failure; only an actual resolved service is useful to the pairing UI.
            _ => {}
        }
    }
    let _ = mdns.shutdown();
    Ok(found)
}
