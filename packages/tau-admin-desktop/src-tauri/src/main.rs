// Phase 40: a minimal Tauri shell around packages/admin-frontend (see tauri.conf.json's
// frontendDist), giving the admin session token real OS-keychain storage (Windows Credential
// Manager / Linux Secret Service via the `keyring` crate) instead of the plain browser build's
// sessionStorage fallback - closing the gap Phase 38's own honest-gaps note named. Deliberately
// NOT a copy of tau-desktop's main.rs: no system tray, no hide-on-close, no global hotkey, no
// mDNS discovery/pairing/proxy - none of that is this app's job. Closing the window quits it,
// the same as any ordinary desktop app; this is a tool you open to administer Tau, not an
// always-on assistant shell.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

/// Separate keychain service from tau-desktop's "home.tau.desktop" - this app's session token
/// and the kiosk client's device token are unrelated credentials for unrelated backends
/// (tau-admin-server vs. tau-core) and must never share a keychain entry.
const KEYRING_SERVICE: &str = "home.tau.admin";
const KEYRING_ACCOUNT: &str = "session-token";

#[tauri::command]
fn store_admin_token(token: String) -> Result<(), String> {
    keyring::Entry::new(KEYRING_SERVICE, KEYRING_ACCOUNT)
        .and_then(|entry| entry.set_password(&token))
        .map_err(|e| e.to_string())
}

#[tauri::command]
fn get_admin_token() -> Result<Option<String>, String> {
    match keyring::Entry::new(KEYRING_SERVICE, KEYRING_ACCOUNT).and_then(|entry| entry.get_password()) {
        Ok(password) => Ok(Some(password)),
        // No token saved yet (fresh install, or the user logged out) - not an error.
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(e) => Err(e.to_string()),
    }
}

/// Logout (admin-frontend's useSession.logout) must also clear the keychain entry, not just its
/// own in-memory state - otherwise a stale token would still be handed back on the next launch.
#[tauri::command]
fn clear_admin_token() -> Result<(), String> {
    match keyring::Entry::new(KEYRING_SERVICE, KEYRING_ACCOUNT).and_then(|entry| entry.delete_credential()) {
        Ok(()) => Ok(()),
        Err(keyring::Error::NoEntry) => Ok(()),
        Err(e) => Err(e.to_string()),
    }
}

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            store_admin_token,
            get_admin_token,
            clear_admin_token
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
