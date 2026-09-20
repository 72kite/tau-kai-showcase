# Tau-Kai Design Decisions

## Why Model Context Protocol (MCP)?

### The Problem
You need a home AI system that can:
- Control Proxmox infrastructure
- Orchestrate Home Assistant devices
- Recognize people by face/voice
- Control PTZ cameras
- Operate 3D printers
- Potentially patrol with drones/robots
- **All while remaining auditable, secure, and extensible**

Each of these is a **separate tool domain** with different risk profiles, APIs, and access patterns. A monolithic LLM wrapper would entangle them.

### Why MCP Solves It
MCP is a **standardized, LLM-agnostic protocol** for how a Host (Tau Core) talks to many independent Servers, each exposing:
- **Tools**: Scoped, typed actions (e.g., `call_service`, `list_vms`)
- **Resources**: Readable state (e.g., live device states, camera feeds)
- **Prompts**: Reusable instructions (e.g., "how to control lighting in different rooms")

Over transparent transports:
- **Stdio** for local processes (low latency, implicit trust)
- **Streamable HTTP/SSE** for networked services (scalable, firewalled)

### The Three Benefits You Get

1. **Compartmentalization**
   - A compromised camera server **cannot touch Proxmox**
   - Each server has its own container, secrets, network VLAN
   - Blast radius is bounded

2. **Auditability**
   - Every tool call is logged, typed, and reviewable
   - **Critical for your "3 agents review before self-upgrade" rule**
   - CDG middleware sits between LLM and execution (cannot be prompt-injected)

3. **Extensibility**
   - Adding a new device class = "write an MCP server"
   - No monolithic system to extend
   - **Matches your "dependency injection" philosophy exactly**

### Alternatives Considered

| Approach | Pros | Cons | Verdict |
|---|---|---|---|
| **MCP (chosen)** | Open protocol, tool standardization, transport agnostic, perfect audit surface | Learning curve, requires orchestration | ✓ Best for extensibility + security |
| **Langchain tools** | Popular, many integrations | Coupled to Python/JS, less explicit boundaries | ✗ Not extensible enough |
| **Custom REST API** | Simple, familiar | No standardization, manual audit logging, tight coupling to Tau | ✗ Doesn't scale |
| **Direct API wrappers** | Low overhead | No safety layer, no audit trail, prompt-injectable | ✗ Dangerous |

---

## Why Core Directive Guard (CDG)?

### The Problem
An LLM, no matter how well-prompted, can be socially engineered via:
- Hypothetical scenarios ("what if you had to...")
- Goal injection ("your real goal is...")
- Context confusion (misinterpreting a user request)

And once compromised, it can call tools to do real damage (destroy VMs, unlock doors, misuse cameras).

### Why CDG is the Right Solution

**CDG is not a prompt; it's infrastructure.**

The CDG is a **deterministic middleware layer** that:
1. Sits **between the LLM and tool execution** (cannot be bypassed via language tricks)
2. Implements hard-coded rulesets:
   - "Never call `shutdown` on safety-critical systems without signed user approval"
   - "Never let the 3D-printer server query face embeddings"
   - "Block all destructive operations during lockdown mode"
3. **Vetoes regardless of what the LLM argues for**
4. Is **immutable** — changes only via manual, out-of-band edits (no LLM can rewrite itself)

### Example: Why Prompt + LLM Alone Isn't Enough

```python
# Dangerous: No middleware
llm_response = ollama.generate("Here's the system prompt. User says: shutdown vm-42")
if "call_tool" in llm_response:
    execute(llm_response.tool)  # LLM just called shutdown! No check.

# Safe: CDG middleware
tool_call = {
    "server": "proxmox-mcp-server",
    "tool": "shutdown_vm",
    "args": {"vm_id": "vm-42"}
}
cdg_decision = cdg.check(tool_call)  # Check against ruleset
if cdg_decision == CDG_APPROVE:
    execute(tool_call)
elif cdg_decision == CDG_ASK_USER:
    approval = await get_user_approval(tool_call)  # Block until approved
    if approval:
        execute(tool_call)
else:  # CDG_REJECT
    reject(tool_call)  # Veto, log incident
```

### Why CDG Ruleset is Immutable

If the LLM could modify CDG rules, it could:
1. Add a "skip approval for shutdown on Monday" rule
2. Wait until Monday
3. Execute dangerous action without approval

**Solution**: CDG changes are out-of-band only:
- Stored in version control (separate file: `cdg_rules.yaml`)
- Require manual edit + commit (human code review)
- Cannot be changed via `user_override_proposal` or any LLM mechanism
- Phase 4 self-upgrade pipeline **explicitly blocks** CDG diffs (SecurityReviewer rejects any change touching `cdg_rules.yaml`)

---

## Why Kubernetes (k3s) for MCP Servers?

### The Problem
You said:
- MCP servers should be "redundant" (one failure doesn't crash the whole system)
- Servers should be "independently killable/restartable"
- You need secrets management + observability at scale

### Why k3s

| Aspect | Why k3s |
|---|---|
| **Lightweight** | Designed for single-node / small clusters (perfect for home lab) |
| **Native orchestration** | Pod restart on crash, rolling updates, resource limits |
| **Storage persistence** | Local PVs for embedding stores + incident logs |
| **Networking** | Service discovery + network policies (VLAN-like segmentation inside cluster) |
| **Secrets management** | k3s can integrate with Vault (or use etcd encryption at rest) |
| **Observability** | Prometheus scrape configs built-in; Loki agents per pod |
| **Backwards compatible** | Standard Kubernetes API; migrate to full cluster later if needed |

### Alternative: Direct Docker/systemd

| Aspect | k3s | Docker/systemd |
|---|---|---|
| Restart on crash | ✓ Pod restart + liveness probes | Manual or basic systemd restart |
| Inter-server networking | ✓ Service DNS | ✗ Manual port mapping |
| Resource limits | ✓ Native | ✗ Via cgroups (manual) |
| Secrets rotation | ✓ Via Vault sidecar injection | ✗ Manual |
| Log aggregation | ✓ Loki agents per pod | ✗ Manual syslog setup |
| Rolling updates | ✓ Native | ✗ Manual deployment scripts |

**Verdict**: k3s is the minimum orchestration layer that gives you resilience + auditability.

---

## Why Ollama (Not Cloud APIs)?

### The Problem
You need:
- **Self-hosted** LLM (no data leaving your home)
- **Multiple models** (one strong tool-caller, one small/fast for routing)
- **Local control** over model updates + governance

### Why Ollama

| Aspect | Ollama | Cloud (OpenAI/Anthropic) | On-Prem (vLLM) |
|---|---|---|---|
| **Self-hosted** | ✓ Yes | ✗ No | ✓ Yes |
| **Easy setup** | ✓ Single binary | ✗ Requires API key | ✓ Slightly complex |
| **Multi-model** | ✓ Pull/swap models easily | ~ One model per API call | ✓ Yes |
| **Model selection** | ✓ Community models | ✓ Best commercial models | ✓ Community models |
| **Cost** | ✓ Amortized to hardware | ✗ Per-token billing | ✓ Amortized to hardware |
| **Latency** | ✓ Local (ms) | ~ Networked (100+ms) | ✓ Local (ms) |
| **Audit trail** | ✓ Full logs locally | ✗ Cloud provider sees queries | ✓ Full logs locally |

### Model Selection Strategy

**Tau Core uses two models:**

1. **Primary (tool-calling)**: e.g., `mistral-large` or `hermes-2` variant
   - Strong reasoning, good tool invocation
   - Used for main conversation + tool selection
   - Slower OK (runs less frequently)

2. **Fast router**: e.g., `mistral-7b` or `neural-chat`
   - Quick classification: "is this user input complex or simple?"
   - Routes to confidence scorer
   - Must be fast (scores every call)

---

## Why 3-Agent Review (Phase 4)?

### The Problem
Tau needs to propose changes (features, bug fixes, configuration updates) to itself, but **you have a hard rule**: "three agents must review before anything is applied."

This prevents:
- **Accidental bugs** escaping into production
- **Prompt injection** cascading into code changes
- **Drift from intent** (feature creep, unintended scope)

### Why Three Distinct Agents

| Agent | Role | Checks |
|---|---|---|
| **Security** | "Is this diff safe?" | Blocks CDG changes, dangerous APIs, missing approval gates |
| **Quality** | "Is this code good?" | Style, test coverage, efficiency, type safety |
| **Intent** | "Does this match user goals?" | Reads proposal rationale against known user preferences |

Each votes: **approved / rejected / conditional**

- **Unanimous approval** → Auto-merge
- **Split** → Pending explicit user override (logged)
- **Unanimous reject** → Proposal archived, feedback to Tau

### Why Not Just "Ask User" or "Run Tests Only"?

| Approach | Pros | Cons | Verdict |
|---|---|---|---|
| **3-agent review (chosen)** | Multi-perspective, scalable, auditable, no human bottleneck unless split | Setup complexity | ✓ Best for autonomous systems |
| **Ask user for every change** | Direct control | Bottleneck, user fatigue, might say "yes" without reading | ✗ Doesn't scale |
| **Tests + CI only** | Automated, fast | Tests catch bugs, not intent drift or prompt injection | ✗ Insufficient |
| **LLM self-review** | Single agent, simple | One point of failure (LLM could be compromised) | ✗ Not robust |

---

## Why Frontend is Separate React App (Not Embedded in Python)?

### The Problem
Tau Core is Python (Ollama integration, MCP client). But the UI needs:
- Real-time animations (3D atom)
- Responsive touch input (mobile)
- Live WebSocket subscriptions (MCP Resources)
- Independent versioning + deployment

### Why Separate Frontend

| Aspect | Separate (chosen) | Embedded |
|---|---|---|
| **Framework choice** | React/Vue/Svelte all viable | Limited to Python web (FastAPI, Django, etc.) |
| **Real-time performance** | WebSocket subscriptions, smooth 60fps | Harder to achieve in Python web |
| **Mobile** | PWA or native iOS/Android | Much harder from Python |
| **Deployment** | Front-end to CDN, back-end to k3s | Monolithic deployment |
| **Scaling** | Front-end cacheable; back-end scales independently | Coupled scaling |
| **Development velocity** | Separate teams possible | Same team, higher cognitive load |

### Architecture

```
┌──────────────────┐
│ React Frontend   │
│ (3D atom, etc.)  │
└────────┬─────────┘
         │ WebSocket
         ↓
┌──────────────────────────────────────┐
│ ui-bridge-mcp-server                 │
│ (MCP Resource aggregator)            │
└─────────┬────────────────────────────┘
          │ MCP Client
          ↓
  ┌──────────────────┐
  │ tau-core         │
  │ (LLM + CDG)      │
  └──────────────────┘
```

---

## Why E-Ink Aesthetic (Not Glassmorphism)?

### The Problem
Home AI system should feel:
- Calming, not stimulating (you live in this space)
- Readable on glare-filled rooms (kitchen, bathroom)
- Respectful of attention (not demanding)
- Futuristic but grounded (not Silicon Valley hype)

### Why E-Ink Inspired

| Aspect | E-Ink | Glassmorphism | Dark Mode (standard) |
|---|---|---|---|
| **High contrast** | ✓ Black/white only | ✗ Semi-transparent blurs | ✓ Dark/light clear |
| **Readable outdoors/bright** | ✓ By design | ✗ Glare issues | ✗ Eye strain |
| **Battery-friendly** | ✓ Static = low power | ✗ Blur effects expensive | ✓ Mostly OK |
| **Calming** | ✓ No glow, no animation spam | ✗ Too "modern" | ✓ Good |
| **Distinct aesthetic** | ✓ Unique, memorable | ✗ Generic trend | ~ Generic |

### Implementation Details

- **Color palette**: Pure black (`#000000`), pure white (`#FFFFFF`), minimal accent (e.g., `#FF6B6B`)
- **Typography**: Monospaced (JetBrains Mono, Roboto Mono) or clean geometric sans (Inter, Outfit)
- **Layout**: Invisible grid (8px or 4px), strict alignment
- **Animation**: Stroke animations on load (SVG `stroke-dasharray`), no jitter or easing (linear motion)
- **Components**: Minimal borders, maximum whitespace, clear hierarchy

---

## Why Piper (Not Google Cloud TTS)?

### Comparison

| Aspect | Piper | Google Cloud TTS | Offline |
|---|---|---|---|
| **Self-hosted** | ✓ Local binary | ✗ Cloud API | ✓ N/A |
| **Cost** | Free (amortized) | $16/million chars | Free |
| **Latency** | <100ms local | 200-500ms networked | Local |
| **Voice quality** | Good (improving) | Excellent (commercial) | Good |
| **Languages** | 10+ (community models) | 100+ | 10+ |
| **Privacy** | Full local | Cloud sees all audio | Full local |
| **Integration** | Wyoming protocol (standard) | REST API | N/A |

**Verdict**: Piper is good-enough quality + free + private. For home use, it's the obvious choice.

---

## Why faster-whisper (Not Cloud STT)?

### Comparison

| Aspect | faster-whisper | Google Cloud STT | Cloud Speech API |
|---|---|---|---|
| **Self-hosted** | ✓ Yes | ✗ No | ✗ No |
| **Cost** | Free | $1.44/hour | $1.50/hour |
| **Accuracy** | Excellent (OpenAI model) | Better | Better |
| **Latency** | 100-500ms | 200-1000ms | 200-1000ms |
| **Privacy** | Full local | Cloud sees audio | Cloud sees audio |
| **Bandwidth** | None (local) | Requires upload | Requires upload |
| **Offline capable** | ✓ Yes | ✗ No | ✗ No |

**Verdict**: faster-whisper is the clear winner for home AI (accuracy + privacy + speed).

---

## Summary: Design Pillars

1. **MCP** = compartmentalized, auditable, extensible
2. **CDG** = deterministic safety layer (cannot be prompt-injected)
3. **k3s** = resilient, scalable orchestration
4. **Ollama** = self-hosted, multi-model LLM
5. **3-agent review** = robust governance without human bottleneck
6. **Separate frontend** = mobile + real-time capable
7. **E-ink aesthetic** = calm, readable, distinctive
8. **Piper + faster-whisper** = private, low-latency voice I/O

All choices prioritize **your autonomy** (self-hosted, no cloud lock-in) + **auditability** (every decision logged and reviewable) + **extensibility** (add new servers without touching core).
