// Native-exclusive OS integration - three things a browser tab fundamentally cannot do, which is
// the whole reason they live here in the Rust shell rather than the shared frontend build:
//   1. Read the host machine's own hardware stats (no web API exposes CPU/RAM/disk of the device
//      the browser itself is running on).
//   2. Ask the OS not to suspend the machine (there is no web equivalent of Windows'
//      SetThreadExecutionState - the closest, the Screen Wake Lock API, only keeps the *display*
//      on, not the system, and doesn't survive the window losing focus/being hidden the way this
//      needs to for a tray-resident assistant).
//   3. Read the OS ARP table (raw LAN neighbor discovery - no browser API surfaces this at all).
use serde::Serialize;
use std::process::Command;
use sysinfo::{Disks, System};

#[derive(Serialize)]
pub struct SystemStats {
    hostname: String,
    cpu_percent: f32,
    mem_used_mb: u64,
    mem_total_mb: u64,
    disk_used_gb: f64,
    disk_total_gb: f64,
}

#[tauri::command]
pub fn get_system_stats() -> SystemStats {
    let mut sys = System::new_all();
    // A single refresh reads 0% CPU on most platforms - sysinfo's own docs recommend two
    // refreshes at least `MINIMUM_CPU_UPDATE_INTERVAL` apart to get a real delta. This command is
    // polled on demand (not per-frame), so eating that ~200ms once per call is a fine trade for an
    // honest number instead of a placeholder 0.
    sys.refresh_cpu_usage();
    std::thread::sleep(sysinfo::MINIMUM_CPU_UPDATE_INTERVAL);
    sys.refresh_cpu_usage();
    sys.refresh_memory();

    let cpu_percent = sys.global_cpu_usage();
    let mem_used_mb = sys.used_memory() / (1024 * 1024);
    let mem_total_mb = sys.total_memory() / (1024 * 1024);

    let disks = Disks::new_with_refreshed_list();
    let (disk_used, disk_total) = disks.iter().fold((0u64, 0u64), |(used, total), d| {
        let t = d.total_space();
        let a = d.available_space();
        (used + t.saturating_sub(a), total + t)
    });

    SystemStats {
        hostname: System::host_name().unwrap_or_else(|| "unknown".to_string()),
        cpu_percent,
        mem_used_mb,
        mem_total_mb,
        disk_used_gb: disk_used as f64 / 1e9,
        disk_total_gb: disk_total as f64 / 1e9,
    }
}

// Keep-awake: SetThreadExecutionState directly via FFI rather than pulling in the full `windows`
// crate for one function - ES_CONTINUOUS makes the flag "sticky" until cleared, ES_SYSTEM_REQUIRED
// is the one that actually prevents sleep. Deliberately NOT ES_DISPLAY_REQUIRED: keeping the
// screen on too would be a battery-life surprise nobody asked for - only the system suspending
// (which would kill an in-progress wake-word audio stream) needs preventing here.
#[cfg(windows)]
mod power {
    #[link(name = "kernel32")]
    extern "system" {
        fn SetThreadExecutionState(esflags: u32) -> u32;
    }
    const ES_CONTINUOUS: u32 = 0x8000_0000;
    const ES_SYSTEM_REQUIRED: u32 = 0x0000_0001;

    pub fn set_keep_awake(enabled: bool) {
        let flags = if enabled {
            ES_CONTINUOUS | ES_SYSTEM_REQUIRED
        } else {
            ES_CONTINUOUS
        };
        unsafe {
            SetThreadExecutionState(flags);
        }
    }
}

// Unverified on Linux/macOS - no equivalent wired up yet (would be systemd-inhibit on Linux,
// IOPMAssertionCreate on macOS). No-op rather than an error so the frontend's call site doesn't
// need a platform check; same "silently does nothing rather than fail" reasoning as pin_certificate
// on a platform keyring has no entry for yet.
#[cfg(not(windows))]
mod power {
    pub fn set_keep_awake(_enabled: bool) {}
}

#[tauri::command]
pub fn keep_awake(enabled: bool) {
    power::set_keep_awake(enabled);
}

#[derive(Serialize)]
pub struct ArpEntry {
    ip: String,
    mac: String,
}

// Shells out to the OS's own `arp` rather than opening a raw socket and building ARP requests by
// hand - the OS already maintains this table from ordinary LAN traffic, so reading it needs no
// elevated privileges and no new attack surface. This is a *cache* of recently-seen neighbors,
// not an active scan - devices silent since the cache entry expired won't appear until they talk
// on the network again (a ping sweep first would populate it, but isn't done here).
#[tauri::command]
pub fn scan_lan_arp() -> Result<Vec<ArpEntry>, String> {
    let output = Command::new("arp")
        .arg("-a")
        .output()
        .map_err(|e| format!("failed to run arp: {e}"))?;
    if !output.status.success() {
        return Err(format!("arp exited with {}", output.status));
    }
    let text = String::from_utf8_lossy(&output.stdout);
    Ok(parse_arp_output(&text))
}

fn parse_arp_output(text: &str) -> Vec<ArpEntry> {
    let mut entries = Vec::new();
    for line in text.lines() {
        let fields: Vec<&str> = line.split_whitespace().collect();
        // Windows: "  192.168.1.1          aa-bb-cc-dd-ee-ff     dynamic"
        // Linux/macOS: "hostname (192.168.1.1) at aa:bb:cc:dd:ee:ff [ether] on eth0"
        if fields.len() < 2 {
            continue;
        }
        let ip = fields.iter().find_map(|f| {
            let trimmed = f.trim_start_matches('(').trim_end_matches(')');
            is_ipv4(trimmed).then(|| trimmed.to_string())
        });
        let mac = fields.iter().find_map(|f| is_mac(f).then(|| f.to_string()));
        if let (Some(ip), Some(mac)) = (ip, mac) {
            entries.push(ArpEntry { ip, mac });
        }
    }
    entries
}

fn is_ipv4(s: &str) -> bool {
    s.split('.').count() == 4 && s.split('.').all(|p| p.parse::<u8>().is_ok())
}

fn is_mac(s: &str) -> bool {
    let sep_count = s.matches(['-', ':']).count();
    sep_count == 5 && s.len() >= 11 && s.len() <= 17
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reads_real_system_stats() {
        let stats = get_system_stats();
        assert!(stats.mem_total_mb > 0, "mem_total_mb should be nonzero on any real machine");
        assert!(stats.mem_used_mb <= stats.mem_total_mb);
        assert!(stats.cpu_percent >= 0.0);
        assert!(!stats.hostname.is_empty());
    }

    #[test]
    fn keep_awake_toggle_does_not_panic() {
        // Can't assert the OS actually stayed awake without a real idle-timeout wait, but this at
        // least proves the FFI call itself is sound on this machine, both directions.
        keep_awake(true);
        keep_awake(false);
    }

    #[test]
    fn scans_real_arp_table() {
        let entries = scan_lan_arp().expect("arp -a should run on this machine");
        assert!(!entries.is_empty(), "this machine's real ARP table should have at least one entry");
        for e in &entries {
            assert!(is_ipv4(&e.ip), "{} should look like an IPv4 address", e.ip);
            assert!(is_mac(&e.mac), "{} should look like a MAC address", e.mac);
        }
    }

    #[test]
    fn parses_windows_arp_format() {
        let sample = "\nInterface: 192.168.1.100 --- 0x5\n  Internet Address      Physical Address      Type\n  192.168.1.1           aa-bb-cc-dd-ee-ff     dynamic\n  192.168.1.52          11-22-33-44-55-66     dynamic\n";
        let entries = parse_arp_output(sample);
        assert_eq!(entries.len(), 2);
        assert_eq!(entries[0].ip, "192.168.1.1");
        assert_eq!(entries[0].mac, "aa-bb-cc-dd-ee-ff");
    }

    #[test]
    fn parses_linux_arp_format() {
        let sample = "example-host.lan (192.168.1.53) at 22:33:44:55:66:77 [ether] on eth0\n";
        let entries = parse_arp_output(sample);
        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0].ip, "192.168.1.53");
        assert_eq!(entries[0].mac, "22:33:44:55:66:77");
    }

    #[test]
    fn ignores_header_and_blank_lines() {
        let entries = parse_arp_output("\nInterface: 192.168.1.100 --- 0x5\n  Internet Address      Physical Address      Type\n");
        assert!(entries.is_empty());
    }
}
