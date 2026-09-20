# tau-admin-desktop

Tauri desktop shell for the admin control panel (Phase 40) — wraps
[`packages/admin-frontend`](../admin-frontend/) in a real OS window, giving its login session a
genuine OS-keychain-backed "secure sector" instead of the plain-browser build's `sessionStorage`
fallback. Same pattern as [`tau-desktop`](../tau-desktop/) (the kiosk client), deliberately a
**separate** app/identifier/keychain-service from it — the two share nothing except being Tauri
shells around this project's UIs, and mixing their credentials would be a real bug, not a
convenience.

Much smaller than `tau-desktop` on purpose: no system tray, no hide-on-close, no global hotkey, no
mDNS discovery/pairing/proxy. None of that is this app's job — it's a tool you open to administer
Tau and close when done, not an always-on assistant shell.

## What's built, live-verified 2026-09-11

- A native window running the real `admin-frontend` build (`frontendDist` → `../admin-frontend/dist`,
  `devUrl` → `localhost:3100` for dev mode), confirmed by actually launching the built `.exe`,
  screenshotting the window, and watching the real login screen render against a live dev server.
- **OS-native session-token storage**: `store_admin_token`/`get_admin_token`/`clear_admin_token`
  (Windows Credential Manager / Linux Secret Service via the `keyring` crate, service
  `home.tau.admin`). Verified two ways: `cargo build` + launch + `Get-Process` confirmed a real,
  responsive "Tau Admin" window; a standalone throwaway Rust binary using the identical
  crate/version/feature-flag combination did a real set → get (exact match) → delete →
  verify-deleted round trip through actual Windows Credential Manager, not a mock.
- `admin-frontend`'s `utils/adminToken.js`/`utils/tauri.js` gate on `isTauri()` exactly like the
  kiosk's `utils/device.js` does: a synchronous in-memory cache loaded once via `initAdminToken()`
  (awaited in `main.jsx` before first render), so `useSession`'s `useState(() => getAdminToken())`
  initializer never reads a not-yet-loaded cache. The plain browser build never awaits anything
  here and is byte-for-byte unaffected (`isTauri()` false → same `sessionStorage` behavior as
  before this package existed).

**`Cargo.toml`'s `keyring` platform split is load-bearing, not stylistic** — identical reasoning
and identical bug class as `tau-desktop`'s own README documents: `keyring = "3"` with no features
silently no-ops instead of failing to compile. Copied the same fix, not reinvented.

## Setup

1. Rust (rustup.rs) + Windows "Desktop development with C++" workload / Linux `libdbus-1-dev`,
   same prerequisites as `tau-desktop`.
2. `npm install` in this directory.
3. In one terminal: `npm run dev` in `packages/admin-frontend` (Vite on `localhost:3100`). In
   another: `npm run dev` here.
4. Point `packages/admin-frontend`'s dev server at a real `tau-admin-server` (`VITE_ADMIN_API_BASE`
   in a `.env.local` there) and set `TAU_ADMIN_ALLOWED_ORIGINS` on that server to include
   `http://localhost:3100` (dev) and `tauri://localhost`/`http://tauri.localhost` (the production
   bundle's webview origin on Windows/Linux respectively) or the login request will fail CORS.

## Building a production build

`npm run build` — bundles per `tauri.conf.json`'s `bundle.targets` (`msi`/`nsis` on Windows,
`deb`/`appimage` on Linux; Linux untested, same honest caveat as `tau-desktop`).
