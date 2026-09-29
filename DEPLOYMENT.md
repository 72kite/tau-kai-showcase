# Deploying Tau — get up and running

A practical, copy-paste guide to standing up the whole Tau stack with Docker, pointing it at a
GPU, reaching it from your phone, and (optionally) SSHing in — plus the security caveats that
actually matter. This is the "dev/test on one box" path; the production home-lab (Proxmox + k3s)
is Phase 0 in `project-tau-plan.md`.

> **One-line security warning, up front:** Tau ships in **LAN-trust mode by default**
> (`TAU_REQUIRE_VOICE_APPROVAL` unset) — **anyone who can reach the web UI is an admin.** That's
> fine on a trusted home network or a private Tailscale tailnet. **Never** port-forward Tau's
> ports to the public internet, and don't put it on a Tailscale *Funnel* (public) — see
> [Security](#security).

---

## 1. Prerequisites

| Need | Notes |
|---|---|
| **Docker Desktop** | WSL2 backend on Windows. Must be *running* before any `docker` command. |
| **~15 GB disk + 8 GB RAM** | The stack builds ~14 images and pulls Ollama/SearXNG/Whisper/Piper. |
| **(optional) NVIDIA GPU + driver** | For GPU-accelerated LLM + Whisper. On Windows, `wsl -d docker-desktop nvidia-smi` must list your GPU. |
| **(optional) Tailscale account** | Free "Personal" plan — for remote access + phone voice over HTTPS. |

---

## 2. Quick start — bring up the stack

From the repo root. The stack works out of the box; every `.env` is optional (services degrade
rather than fail when one isn't configured).

**Windows, one shot:** `scripts\tau.ps1 up` (or `scripts\bring-up-tau.ps1` directly) - installs
Docker Desktop if missing, waits for the engine, detects GPU passthrough, auto-picks a model that
fits this box (§3), and pulls it. `-Cpu` forces CPU-only; `-Model <name>` overrides the auto-pick;
`-SkipModelPull` skips pulling a model. Safe to re-run any time (e.g. after a hardware change).

**Bare Linux host (Debian/Ubuntu, incl. a fresh Proxmox LXC), one shot:**
`scripts/bring-up-tau.sh` - installs Docker Engine + the compose plugin via the official apt repo
if missing, waits for the engine, detects GPU passthrough, auto-picks a model that fits this box
(§3), and pulls it. Assumes the repo is already checked out at the path it's run from (this is
what runs *after* `git push-to-deploy` populates `/opt/tau-kai` - see `project-tau-plan.md` §8.28
for how the bare repo + `post-receive` hook that does that get set up; that part is server-local
infra, not this script's job). `--cpu` forces CPU-only; `--model <name>` overrides the auto-pick;
`--skip-model-pull` skips pulling a model; `--check` is a read-only preflight (ports/disk/Docker/
GPU-driver) that makes no changes at all — run it first on a box you haven't brought Tau up on
before. Safe to re-run any time.

**Full stack (CPU):**
```bash
docker compose up -d --build
```

**Full stack (GPU — layers the GPU overlay on top):**
```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
```

**Just the core (fastest; tau-core runs "degraded" without the 10 domain servers):**
```bash
docker compose up -d --build --no-deps \
  tau-core memory-mcp-server ui-bridge-mcp-server frontend ollama
```

Check it came up:
```bash
curl -s http://localhost:8000/api/health     # {"status":"ok",...} (or "degraded" for the core subset)
```

Open the UI: **http://localhost:3000**

---

## 3. Model setup

The chat model runs in the `ollama` container and must be pulled once. **`qwen2.5:7b-instruct` is
the current recommended main model** — a 2026-07-22 stress test (see `project-tau-plan.md` §10)
found the bigger 14b quants scored *no better* on tool-selection while spilling off an 8 GB card.

```bash
docker compose exec ollama ollama pull qwen2.5:7b-instruct
```

Configure which model tau-core uses in `packages/tau-core/.env` (git-ignored):
```
OLLAMA_MODEL=qwen2.5:7b-instruct
OLLAMA_ROUTER_MODEL=qwen2.5:7b-instruct
```
Then recreate tau-core: `docker compose up -d --no-deps --force-recreate tau-core`.

---

## 4. GPU acceleration

The base compose is **CPU-only**. GPU support is the overlay `docker-compose.gpu.yml` (Ollama +
Whisper get the card). Pre-check that Docker can see the GPU, then bring the stack up **with both
files**:

```bash
docker run --rm --gpus all --entrypoint nvidia-smi ollama/ollama:latest -L   # should list your GPU
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
```

Verify the model is on the GPU: `docker compose exec ollama ollama ps` → `PROCESSOR` should read
`100% GPU` (or a `xx%/yy% CPU/GPU` split if the model is slightly larger than VRAM).

> Always pass **both** `-f` files on every `up`/`down` once you're on GPU, or Compose reverts
> Ollama to the CPU config.

---

## 5. Accessing Tau

### 5a. This machine
**http://localhost:3000** — full functionality, including microphone/voice (`localhost` is a
"secure context").

### 5b. Other devices on your LAN
1. Find this machine's LAN IP (e.g. `192.168.1.73`) — it's DHCP from your router. For a stable
   address, set a **DHCP reservation** on the router.
2. Open the firewall for the two ports (**one-time, elevated PowerShell / Administrator**):
   ```powershell
   New-NetFirewallRule -DisplayName "Tau frontend (3000)" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 3000 -Profile Any
   New-NetFirewallRule -DisplayName "Tau core API (8000)" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8000 -Profile Any
   ```
3. From any device on the LAN: **`http://<lan-ip>:3000`**.

> **Voice does NOT work over plain `http://` to a LAN IP.** Browsers only allow the microphone in a
> *secure context* (`https://` or `localhost`) — iOS Safari refuses the mic without even prompting.
> On a phone over LAN you get **text chat only**. For voice off this machine, use HTTPS via
> Tailscale (below).

### 5c. Remote access + phone voice — Tailscale (HTTPS)
[Tailscale](https://tailscale.com) puts this machine and your phone on a private mesh network and
gives this machine a **trusted HTTPS cert automatically** — which is what unlocks the microphone on
the phone, and lets you reach Tau from anywhere. Free on the Personal plan.

```powershell
$ts = "C:\Program Files\Tailscale\tailscale.exe"

# 1. Log in (one-time) — approve in the browser it opens:
& $ts up

# 2. This machine's tailnet name + IP:
& $ts status        # e.g. zangetsu-legion.tailXXXX.ts.net  /  100.x.x.x

# 3. Enable "Serve" (HTTPS) on the tailnet ONCE — the command prints an admin-console link the
#    first time; click it, then re-run:
& $ts serve --bg 3000     # serves https://<machine>.<tailnet>.ts.net -> the Tau UI
& $ts serve status        # confirm the mapping
```

Install the **Tailscale** app on the iPhone, log in with the *same account*, then open
**`https://<machine>.<tailnet>.ts.net`** — voice now works (trusted cert = secure context).

- The frontend is served **single-origin** (nginx proxies `/api/` to tau-core; the bundle is built
  with an empty `VITE_TAU_API_BASE` so its calls are relative). That's what makes this work behind
  one HTTPS hostname with no CORS or mixed-content — nothing extra to configure.
- **Before enabling Serve**, you already have private remote access over **HTTP** from any tailnet
  device: `http://<machine-tailscale-ip>:3000` (text chat; voice still needs the HTTPS URL above).

---

## 6. SSH access

**Passwordless by default, by design** — SSH auth uses identity/keys, not a password. A password is
never set unless *you* choose to add one.

- **Linux deployment (the home-lab target):** the cleanest option is **Tailscale SSH** — no
  password, no key distribution, access gated by your tailnet ACLs:
  ```bash
  sudo tailscale up --ssh
  # then from any tailnet device:  ssh <user>@<machine>.<tailnet>.ts.net
  ```
- **This Windows box:** Tailscale's SSH *server* is Linux/macOS only, so use **Windows OpenSSH
  Server** over the tailnet, with **public-key auth** (no password). In an elevated PowerShell:
  ```powershell
  Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
  Start-Service sshd; Set-Service -Name sshd -StartupType Automatic
  # add your PUBLIC key to authorized_keys, then key-based SSH works with no password:
  #   ssh <you>@<machine-tailscale-ip>
  ```
  To *add* a password later (only if you want one): `net user <you> *` (Windows) sets it; leaving it
  unset keeps SSH key-only.

> SSH should only ever be reachable over the tailnet or LAN — never exposed to the public internet.

---

## 7. Common operations

`scripts\tau.ps1` (Windows/PowerShell) wraps the compose incantations below, plus a
`doctor` preflight that checks Docker/GPU/`.env`/pulled-model/`/api/health` in one shot - see
`scripts/tau.ps1`'s header comment for the full command list. With `scripts\` on your PATH,
`scripts\tau.cmd` lets you drop the path prefix and just type `tau <command>`.

```powershell
scripts\tau.ps1 status              # containers + /api/health at a glance
scripts\tau.ps1 doctor              # preflight: Docker? GPU? .env? model pulled? health?
scripts\tau.ps1 logs tau-core -Follow
scripts\tau.ps1 model pull <model>  # pull into the running ollama container
scripts\tau.ps1 model set <model>   # write OLLAMA_MODEL directly (no hardware probe)
scripts\tau.ps1 down                # stop everything (keep data volumes)
scripts\tau.ps1 down -Volumes       # ...and wipe data (approvals, transcript, memory) too
```

The equivalent raw `docker compose` commands still work on any platform:

```bash
# Stop everything (keep data volumes):
docker compose -f docker-compose.yml -f docker-compose.gpu.yml down
# ...and wipe data (approvals, transcript, memory) too:  add -v

# Follow a service's logs:
docker compose logs -f tau-core

# Rebuild + restart one service after a code change:
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build --no-deps frontend

# Health / connected servers:
curl -s http://localhost:8000/api/health

# List / pull models:
docker compose exec ollama ollama list
docker compose exec ollama ollama pull <model>
```

---

## 8. Security

- **LAN-trust by default.** With `TAU_REQUIRE_VOICE_APPROVAL` unset, the admin gate returns
  "lan-trust" — every reachable client is an admin (unified transcript, device list, drafts,
  household inferences). Turn on voice approval to require a verified admin voiceprint for those
  surfaces.
- **Keep it private.** LAN + Tailscale only. Do **not** port-forward 3000/8000 to the internet, and
  do **not** use Tailscale *Funnel* (which exposes to the public internet) — either would hand admin
  to strangers.
- **Encryption at rest** (voiceprints, memory, approval store, transcript) turns on when
  `TAU_MASTER_KEY` is set; plaintext otherwise. Set it for any real deployment.
- **Secrets never go in git.** `.env`, `data/`, `*.authkey` and friends are git-ignored — keep it
  that way.

---

## 9. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `docker` errors "cannot connect" | Docker Desktop isn't running — start it, wait for the engine. |
| `/api/health` = `degraded` | Some domain servers aren't up. Expected for the `--no-deps` core subset; otherwise check `docker compose ps`. |
| Chat replies 503 | No model pulled, or Ollama down — `docker compose exec ollama ollama list`. |
| Phone: "microphone blocked", no prompt | Not a bug — mic needs HTTPS (§5b/§5c). Text still works. |
| Phone UI cut off / not responsive | Hard-reload (PWA cache): close the Safari tab and reopen, or re-Add-to-Home-Screen. |
| Ollama on CPU after a reboot | You brought it up without the GPU overlay — include **both** `-f` files. |
