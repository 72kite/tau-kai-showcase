# tau-desktop

Tauri desktop shell for Tau (Phase 27.D) — see
`project-tau-plan.md` §8.28's 27.D section. A native Windows/Linux
client that wraps [`packages/frontend`](../frontend/) in a real OS window instead of a browser
tab, as the first step toward a local, private replacement for Siri/Google Assistant. It does not
fork or duplicate the frontend — `tauri.conf.json`'s `frontendDist` points straight at
`../frontend/dist`, the same build already produced for the Docker kiosk image.

## What's built (Milestones 1-4, all live-verified)

- A native window running the actual frontend build, pointed at a user-configured `tau-core` host
  at runtime (not baked in at build time).
- System tray (Show/Hide/Quit) and hide-to-tray on window close — closing the window doesn't quit,
  same "always available" posture as a real assistant.
- **Global hotkey**: Ctrl+Shift+Space summons the window from anywhere, hidden or not.
- **OS-native credential storage**: the device token lives in Windows Credential Manager / Linux
  Secret Service (`keyring` crate), not webview `localStorage`.
- **Background wake-word**: "hey tau" keeps listening while the window is hidden — needed no code
  changes; measured, not assumed (see project-tau-plan.md's Milestone 2 writeup for the numbers).
- **mDNS discovery + TLS certificate pinning**: finds `tau-core` on the LAN automatically, pairs by
  having a human compare the certificate fingerprint it received against tau-core's admin
  dashboard (SSH-host-key-style trust-on-first-use), then pins it and routes traffic through a
  local pinning proxy — real enforcement, verified by actually changing the certificate and
  confirming the proxy refuses to forward. See project-tau-plan.md's Milestone 3 writeup.
- **Native OS notifications** for new pending approvals and draft-count increases — closes the
  plan doc's own long-standing "someone has to walk past a kiosk" gap. Verified correct at the JS
  level (permission + send, zero errors) in both dev mode and a properly-installed release build;
  no visible popup appeared in this session's testing because the dev machine's own Windows "Do
  not disturb" setting routes all notifications straight to Notification Center — see
  project-tau-plan.md's Milestone 4 writeup for how that was tracked down to ground truth.

**Not yet built**: an actual Linux build/test pass (the code is written to be Linux-compatible,
including the credential-storage feature split below, but untested on real Linux hardware), the
Android target, splitting into a separate repo, and a "forget this pairing" UI action. See the
plan doc's 27.D section for the full history and reasoning.

## Layout

```
src-tauri/
  tauri.conf.json    window/tray/bundle config; frontendDist -> ../../frontend/dist
  Cargo.toml         keyring's platform split lives here - see note below
  build.rs
  src/main.rs        tray, hide-to-tray, global hotkey, device-token/pin keychain commands
  src/discovery.rs   mDNS browsing for tau-core's _tau._tcp.local. advertisement
  src/pairing.rs     fetches a host's TLS cert fingerprint with NO validation (pairing only -
                     the human comparison step is the actual security check)
  src/proxy.rs       the local pinning proxy: forwards 127.0.0.1:<port> -> the paired
                     https://host:port, rejecting anything not matching the pinned fingerprint
  capabilities/
    default.json     grants notification:default to the main window - the first capabilities
                      file this app needed (see note below)
  icons/             PLACEHOLDER icons (generated, not real branding) - regenerate with
                     `cargo tauri icon <source.png>` once real artwork exists
```

**Why there's a `capabilities/` file now, when Milestones 1-3 never needed one.** Tauri v2's ACL
system gates *plugin* commands called directly from JS - it does not gate app-defined
`#[tauri::command]`s (Milestones 2-3's keychain/discovery/pairing/proxy commands) or anything
driven purely from Rust (the tray, `global-shortcut`). `tauri-plugin-notification`'s JS functions
(`@tauri-apps/plugin-notification`) are the first thing in this app that's actually a JS-invoked
plugin command, so `notification:default` is the first permission this project has had to grant.

**`Cargo.toml`'s `keyring` split is load-bearing, not stylistic.** `keyring` 3.x ships with *no
default features* — `keyring = "3"` alone compiles clean and runs with zero errors, but silently
uses a no-op mock store (`set_password()` returns `Ok(())`, `get_password()` always `NoEntry`).
This was caught by testing an actual round-trip through Windows Credential Manager, not by the
compiler. `windows-native` is enabled for Windows, `sync-secret-service` (the real D-Bus Secret
Service — gnome-keyring/kwallet — not `linux-native`'s kernel keyutils, matching what this
project's plan doc specifies) for Linux; the latter needs `libdbus-1-dev` at build time and is
unverified like the rest of the Linux path.

## The frontend-side half of this work

`packages/frontend`'s `useMCPResource.js` used to compute `API_BASE` once at module load from a
build-time env var — fine for the kiosk (one deployment, one known origin), meaningless for a
desktop app pointed at an arbitrary LAN host. It now exports `getApiBase()`/`setApiBase()`
(localStorage-backed, read fresh on every call) instead, and every hook that used to import the
old constant reads `getApiBase()` per-call. This is additive: the kiosk build never calls
`setApiBase()`, so it sees byte-for-byte the same value it always did.

Pieces gated on `isTauri()` (`src/utils/tauri.js`, checks for `__TAURI_INTERNALS__`) so the kiosk
build never touches them: `ConnectionSettings.jsx` (a "point me at tau-core" field, also reachable
from `SystemDrawer`), `App.jsx`'s first-run gate, and `utils/device.js`'s device-token cache
(`initDeviceToken()`, awaited in `main.jsx` before first render, backing `getDeviceToken()`/
`setDeviceToken()` with the OS keychain instead of `localStorage` while every existing sync caller
— `deviceHeaders()`, `useDeviceId.js` — needed zero changes).

`@tauri-apps/api` is a `packages/frontend` dependency but loaded via dynamic `import()`, not a
static one — the kiosk build's bundle only grows by a ~0.09kB code-split chunk it never fetches.

## Setup

1. Install Rust: https://rustup.rs (or `winget install Rustlang.Rustup` on Windows).
2. On Windows, the "Desktop development with C++" workload (Visual Studio Build Tools) if not
   already present; WebView2 ships with Windows 11 by default. On Linux, `libdbus-1-dev` (for the
   credential-storage feature above) plus Tauri's own usual Linux prerequisites (webkit2gtk etc.
   — see Tauri's own docs; unverified by this project so far).
3. `npm install` in this directory (pulls in `@tauri-apps/cli`).
4. In one terminal: `npm run dev` in `packages/frontend` (starts the Vite dev server on
   `localhost:3000` per `vite.config.js` — this config's `devUrl` points there, not Vite's usual
   5173 default). In another: `npm run dev` here.
5. The first time discovery/pairing actually runs a network operation, Windows will show a
   Firewall consent prompt for `tau-desktop.exe` ("Do you want to allow... on private and public
   networks?") — click Allow. This is a one-time, per-binary-path decision; it doesn't reappear on
   later launches of the same build.

## Building a production build

`npm run build` — bundles per `tauri.conf.json`'s `bundle.targets` (`msi`/`nsis` on Windows,
`deb`/`appimage` on Linux; the Linux targets are untested — written on Windows, no Linux box
available to build/run them yet).
