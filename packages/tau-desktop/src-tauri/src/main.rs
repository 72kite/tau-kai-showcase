// Phase 27.D: a system tray + hide-on-close window + global hotkey + OS-keychain device-token
// storage around the existing packages/frontend build (see tauri.conf.json's frontendDist). The
// frontend side (getApiBase()/setApiBase() in useMCPResource.js, ConnectionSettings.jsx, and
// getDeviceToken()/setDeviceToken() in utils/device.js) does the actual tau-core-pointing and
// token-caching work; this file is just the native shell around it.
//
// Milestone 1's main.rs compiled clean against Tauri v2.11.5 on the first try - this file follows
// the same API shape for the two new pieces (global-shortcut plugin, keyring crate), but hasn't
// been run against a real build yet at the time of writing. Fix whatever `cargo tauri dev` first
// complains about, same as last time.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod discovery;
mod pairing;
mod proxy;
mod system;

use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    AppHandle, Manager, WindowEvent,
};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};

/// Shared by the tray's "Show" item and the global hotkey, so the two "bring Tau to front" paths
/// can never drift apart.
fn show_main_window(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.set_focus();
    }
}

/// Phase 27.D Milestone 4 follow-up: click-to-focus on a notification. The frontend's
/// `useDesktopNotifications.js` registers `@tauri-apps/plugin-notification`'s `onAction` listener
/// and calls this on any click - an app-defined command (like the keychain/pairing commands
/// above), not a window-manipulation permission grant, so `capabilities/default.json` needs no new
/// entry for it, same reasoning as that file's own comment about app-defined commands.
#[tauri::command]
fn focus_main_window(app: AppHandle) {
    show_main_window(&app);
}

/// Device-token storage (Phase 27.A token, Phase 27.D storage): the OS's real credential store
/// instead of the webview's localStorage - Windows Credential Manager / Linux Secret Service via
/// the `keyring` crate, matching what project-tau-plan.md's 27.D section specified. One entry per
/// install (fixed account name: a Tau install has exactly one device token, not per-user).
const KEYRING_SERVICE: &str = "home.tau.desktop";
const KEYRING_ACCOUNT: &str = "device-token";

#[tauri::command]
fn store_device_token(token: String) -> Result<(), String> {
    keyring::Entry::new(KEYRING_SERVICE, KEYRING_ACCOUNT)
        .and_then(|entry| entry.set_password(&token))
        .map_err(|e| e.to_string())
}

#[tauri::command]
fn get_device_token() -> Result<Option<String>, String> {
    match keyring::Entry::new(KEYRING_SERVICE, KEYRING_ACCOUNT).and_then(|entry| entry.get_password()) {
        Ok(password) => Ok(Some(password)),
        // No token saved yet (fresh install, or admin hasn't approved this device) - not an
        // error, just "nothing there".
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(e) => Err(e.to_string()),
    }
}

/// Pinned TLS certificate fingerprint (Phase 27.D Milestone 3), stored the exact same way as the
/// device token above - same keychain service, a different account name. Once pinned, the local
/// proxy (see proxy.rs) refuses to forward traffic to anything presenting a different fingerprint.
const KEYRING_TLS_ACCOUNT: &str = "tls-fingerprint";

#[tauri::command]
fn pin_certificate(fingerprint: String) -> Result<(), String> {
    keyring::Entry::new(KEYRING_SERVICE, KEYRING_TLS_ACCOUNT)
        .and_then(|entry| entry.set_password(&fingerprint))
        .map_err(|e| e.to_string())
}

#[tauri::command]
fn get_pinned_fingerprint() -> Result<Option<String>, String> {
    match keyring::Entry::new(KEYRING_SERVICE, KEYRING_TLS_ACCOUNT).and_then(|entry| entry.get_password()) {
        Ok(fingerprint) => Ok(Some(fingerprint)),
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(e) => Err(e.to_string()),
    }
}

/// "Forget this pairing" (frontend: ConnectionSettings.jsx). Removes the pinned fingerprint so a
/// stale/no-longer-trusted host can't be silently reconnected to - NoEntry counts as success since
/// the end state ("nothing pinned") is what the caller wants either way.
#[tauri::command]
fn clear_pinned_fingerprint() -> Result<(), String> {
    match keyring::Entry::new(KEYRING_SERVICE, KEYRING_TLS_ACCOUNT).and_then(|entry| entry.delete_credential()) {
        Ok(()) => Ok(()),
        Err(keyring::Error::NoEntry) => Ok(()),
        Err(e) => Err(e.to_string()),
    }
}

fn main() {
    tauri::Builder::default()
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(|app, shortcut, event| {
                    // Ctrl+Shift+Space summons Tau from anywhere, even hidden in the tray - the
                    // whole point of a global hotkey. Only reacts on key-down (Pressed), not
                    // key-up, so it doesn't fire twice per press.
                    if event.state() == ShortcutState::Pressed
                        && shortcut.matches(Modifiers::CONTROL | Modifiers::SHIFT, Code::Space)
                    {
                        show_main_window(app);
                    }
                })
                .build(),
        )
        // Phase 27.D Milestone 4: native OS notifications for pending approvals/drafts - closes
        // the "someone has to walk past a kiosk" gap project-tau-plan.md's Phase 9 writeup
        // flagged as a known limitation of the browser-kiosk-only client. No custom commands of
        // ours here - the frontend calls this plugin's own JS functions directly
        // (@tauri-apps/plugin-notification), which is why this app needs a capabilities file for
        // the first time (see capabilities/default.json) - unlike global-shortcut above (driven
        // entirely from Rust) or our own store_device_token-style commands (app-defined commands
        // don't need ACL entries), a *plugin's* JS-invoked commands do.
        .plugin(tauri_plugin_notification::init())
        .invoke_handler(tauri::generate_handler![
            store_device_token,
            get_device_token,
            pin_certificate,
            get_pinned_fingerprint,
            clear_pinned_fingerprint,
            focus_main_window,
            discovery::discover_tau_core,
            pairing::fetch_pairing_fingerprint,
            proxy::start_pinning_proxy,
            system::get_system_stats,
            system::keep_awake,
            system::scan_lan_arp
        ])
        .setup(|app| {
            let show_item = MenuItem::with_id(app, "show", "Show", true, None::<&str>)?;
            let hide_item = MenuItem::with_id(app, "hide", "Hide", true, None::<&str>)?;
            let quit_item = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show_item, &hide_item, &quit_item])?;

            TrayIconBuilder::new()
                .menu(&menu)
                .show_menu_on_left_click(true)
                .icon(app.default_window_icon().unwrap().clone())
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => show_main_window(app),
                    "hide" => {
                        if let Some(window) = app.get_webview_window("main") {
                            let _ = window.hide();
                        }
                    }
                    // The only way to actually exit - closing the window just hides it, see
                    // on_window_event below, so an always-available assistant doesn't vanish
                    // because someone hit the window's own close button.
                    "quit" => app.exit(0),
                    _ => {}
                })
                .build(app)?;

            let ctrl_shift_space = Shortcut::new(Some(Modifiers::CONTROL | Modifiers::SHIFT), Code::Space);
            app.global_shortcut().register(ctrl_shift_space)?;

            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                let _ = window.hide();
                api.prevent_close();
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
