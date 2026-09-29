# Tau-Kai Integrations & Roadmap

## Completed Integrations (Phase 1 & 2)

### Phase 1: Tau Core ✓
- MCP Host with LLM runtime
- Core Directive Guard (CDG) middleware
- Session/context management with memory integration
- Approval workflow primitives
- Confidence-based routing

### Phase 2: Core MCP Servers ✓
- **proxmox-mcp-server**: Proxmox VE infrastructure control
- **home-assistant-mcp-server**: IoT device orchestration
- **voice-mcp-server**: Piper TTS + faster-whisper STT
- **memory-mcp-server**: Qdrant/Chroma embedding store
- **vision-mcp-server**: Camera feeds, face/object detection, PTZ control
- **security-mcp-server**: Lockdown mode, incident logging
- **fabrication-mcp-server**: OctoPrint/Moonraker 3D printer control

---

## Upcoming Integrations

### Phase 3: UI Layer (In Progress)

#### Frontend Architecture
- **Framework**: React + Three.js
- **State Management**: MCP Resource subscriptions (WebSocket)
- **Design Language**: E-ink inspired (high-contrast, monospaced, minimal glow)

#### Components
1. **3D Atom Visualization**
   - Idle state: geometric shape (rotating/pulsing)
   - Color/animation bound to system complexity (active calls, pending approvals)
   - Flattens to 2D when voice-activated
   - Integration: reads `voice-mcp-server` transcription stream

2. **Live Transcription Overlay**
   - Streams from `voice-mcp-server` during active sessions
   - Tau's response text pulses alongside TTS playback
   - Clear listening/thinking feedback

3. **Contextual Panels**
   - **Camera panel**: Integrates `vision-mcp-server` snapshots + scene understanding
   - **HA Dashboard**: Integrates `home-assistant-mcp-server` device states (lights, thermostats, etc.)
   - **Approval Queue**: Displays pending CDG approvals; one-click authorize/deny
   - Panels swap dynamically based on active MCP domains

4. **Backend Bridge**
   - New MCP server: `ui-bridge-mcp-server`
   - Aggregates state from voice, HA, vision, security servers
   - Exposes MCP Resources for frontend subscription
   - No separate REST API; MCP is the API layer

#### Design Patterns
- Stroke animations on load (SVG/CSS)
- Grid-aligned layout (invisible guide)
- Dark mode default (AMOLED-friendly)
- Responsive (desktop focus, mobile support via PWA)

#### Remaining Work
- [ ] Implement 3D atom with Three.js
- [ ] Wire MCP Resource subscription layer
- [ ] Build contextual panel routing
- [ ] Design approval queue UI
- [ ] End-to-end visual polish

---

### Phase 4: Governed Self-Upgrade Pipeline (Foundation Built)

#### Architecture
1. **Proposal Generation**
   - Tau or "scout" sub-agent proposes a feature/change
   - Diff + rationale written to a review queue (Git branch as durable store)
   - Proposal triggers CI/CD validation (lint, type-check, tests)

2. **Three-Agent Review**
   - **Security Reviewer**: Checks for CDG violations, unsafe API calls
   - **Quality Reviewer**: Checks code style, test coverage, efficiency
   - **Intent Reviewer**: Checks if change aligns with user's explicit goals
   - Each reviewer runs as separate Ollama session with distinct prompt/role
   - Votes: approved / rejected / conditional

3. **Approval Logic**
   - **Unanimous approval** → Auto-merge to main
   - **Split votes** → Pending user decision (explicit override required)
   - **Rejection** → Proposal archived, feedback loop to Tau
   - User override: allowed + audit-logged

4. **Safety Constraints**
   - CDG ruleset **immutable** (SecurityReviewer blocks 'CDG' in any diff)
   - No self-modification of approval logic
   - All changes audit-logged

#### Remaining Integration Work
- [ ] Register `phase4-mcp-server` in `tau-core/config/servers.yaml`
- [ ] Add CDG rule: `user_override_proposal` requires human approval
- [ ] Wire CI/CD: GitHub Actions (lint → test → build → k3s deploy)
- [ ] Replace heuristic reviewers with Ollama-backed agents
- [ ] Expose pending proposals in UI-bridge approval queue
- [ ] Add rollback mechanism (revert + notify on deploy failure)
- [ ] Document proposal template (diffs, rationale, affected systems)

#### Future Enhancements
- Automatic changelog generation from merged proposals
- Proposal analytics dashboard (what changed, who reviewed, how long)
- Integration with GitHub Projects for proposal tracking
- Automated rollback on production errors (with incident logging)

---

### Phase 2.5: Memory Tree Enhancement (OpenHuman Integration - Planned)

#### Overview
Integrate OpenHuman's **Memory Tree Engine** to augment `memory-mcp-server` with structured, interpretable context storage alongside existing face/voice embeddings.

**Why**: Replace pure vector-soup embeddings with scored Markdown trees. Same memory, better auditability and searchability.

#### Changes
- **memory-mcp-server** gains `store_context_tree()` and `search_context()` tools
- Chroma DB remains for face/voice embeddings; SQLite stores context trees
- Obsidian vault sync (optional) for manual memory editing
- agentmemory backend support (multi-agent shared memory)

#### Architecture
```
memory-mcp-server/
├── embedding_store.py       # Face/voice embeddings (Chroma, unchanged)
├── tree_builder.py          # NEW: build + score context trees
├── obsidian_sync.py         # NEW: mirror trees to Obsidian vault
└── context_search.py        # NEW: full-text + semantic search
```

#### Tools
- `store_context_tree(person_id, context)` → `{"tree_id": str, "nodes": int}`
- `search_context(query, person_id=None)` → `[{"text": str, "score": float, "source": str}]`
- `get_person_context_tree(person_id)` → full tree with all nodes

#### Implementation Notes
- Port OpenHuman's Rust tree builder to Python
- Use existing Markdown scoring (relevance-based, like Obsidian)
- Store in new SQLite schema (separate from embeddings)
- Sync to Obsidian vault if user has one (~/Obsidian/tau-memory/)
- No breaking changes to existing embedding tools

#### Effort
- **Extraction from OpenHuman**: 10-15 hrs (study Rust → Python port)
- **Integration with tau**: 40-50 hrs (implement, test, CDG rules)
- **Total**: ~60 hrs (1.5 weeks, 1 engineer)

#### Related
- See OPENHUMAN_AUDIT.md for detailed module breakdown

---

### Phase 3.5: Auto-Fetch Context Enhancement (OpenHuman Integration - Planned)

#### Overview
Enhance `ui-bridge-mcp-server` with OpenHuman's **auto-fetch** pattern: periodically pull live state from all MCP servers into a pre-populated context snapshot.

**Why**: "SuperContext" pattern. LLM starts with fresh world state without cold-start reasoning.

#### Changes
- `ui-bridge-mcp-server` adds background loop (every 5 minutes)
- New MCP resource: `context_snapshot()` with:
  - Home Assistant state (lights, thermostats, devices)
  - Proxmox alerts + recent errors
  - Security events (last 30 min)
  - Phase 4 proposal errors (recent failures to learn from)
  - Recent tool call audit log (CDG decisions)

#### Architecture
```
ui-bridge-mcp-server/
├── auto_fetch.py            # NEW: 5-min loop orchestrator
├── context_builders/        # NEW: per-domain snapshot builders
│   ├── ha_snapshot.py
│   ├── proxmox_snapshot.py
│   ├── security_snapshot.py
│   └── phase4_snapshot.py
└── server.py                # Wire new resource
```

#### Resource
- `get_context_snapshot()` → pre-fetched world state (updated every 5 min)

#### Effort
- **Extraction from OpenHuman**: 5-10 hrs
- **Integration with tau**: 25-35 hrs
- **Total**: ~35-40 hrs (1 week, 1 engineer)

---

### Phase 4 Enhancement: Durable Workflows & Checkpointing (OpenHuman Integration - Planned)

#### Overview
Enhance Phase 4 self-upgrade pipeline with OpenHuman's **orchestration primitives**: checkpointed graph runs, sub-agent fleets, and durable workflows.

**Why**: Better resilience (pause/resume), better observability (per-call root cause), better multi-agent coordination.

#### Current vs. Enhanced

| Aspect | Current (Phase 4) | Enhanced (with OpenHuman) |
|--------|---|---|
| **Execution model** | Linear agent chain | Checkpointed graph (pause/resume) |
| **Multi-reviewer** | 3 agents voting | Agent fleet (specialists spawned 3 levels deep) |
| **Failure recovery** | Retry on failure | Root-cause analysis + human steering |
| **Observation** | Logs only | Per-call cost + token count |
| **Durability** | In-memory queue | Durable store (survives restart) |

#### Changes
- Phase 4 executor becomes a checkpointed graph (tinyagents-style)
- Reviewers spawn as sub-agent fleet (security → sub-security specs, quality → sub-linters, intent → sub-goal-checkers)
- Each reviewer checkpoint: "decision + rationale + stuck?"
- Proposal queue backed by SQLite (vs. in-memory)
- New UI: show checkpoint tree + stuck agents

#### Architecture
```
phase4-upgrade-pipeline/
├── src/phase4_mcp_server/
│   ├── graph_executor.py    # NEW: checkpointed runs (AsyncIO + SQLite)
│   ├── agent_fleet.py       # NEW: sub-agent spawning + steering
│   ├── checkpoint_store.py  # NEW: durable graph state
│   └── server.py            # Wire tools + resources
└── tests/
    └── test_checkpointing.py # NEW: test pause/resume
```

#### Tools (New)
- `create_proposal_graph(diff, rationale)` → `{"graph_id": str}`
- `get_graph_checkpoint(graph_id)` → current state + stuck agents
- `steer_stuck_agent(graph_id, agent_id, guidance)` → resume execution
- `get_agent_fleet_tree(graph_id)` → hierarchical reviewer structure

#### Effort
- **Extraction from OpenHuman**: 20-30 hrs (tinyagents-style executor)
- **Integration with tau**: 40-50 hrs (adapt to tau's agent model)
- **UI updates**: 20-30 hrs (checkpoint tree visualization)
- **Total**: ~100-110 hrs (2.5-3 weeks, 1-2 engineers)

#### Related
- See OPENHUMAN_AUDIT.md for orchestration module details

---

### Phase 5: Robotics & Mobile (Longer Horizon)

#### Agent-to-Agent Messaging & Federated Instances

**Overview** (from OpenHuman integration):
- E2E encrypted messaging between Tau instances (Signal protocol)
- Support guest user delegation (guest's Tau → host's Tau, with approval)
- Cross-home action logging (both instances log to local Loki)

**Integration Path**:
- New `federation-mcp-server` for A2A messaging
- CDG rule: all cross-home actions require approval
- Encryption: `python-signal` bindings
- Payment layer: Optional x402 USDC for inter-instance trading (future)

**Effort**: 80-120 hrs (extract + integration, 2-3 weeks, 2 engineers)

---

#### Robotics Server (`robotics-mcp-server`)

**Stage 1: Drone Patrol**
- **Hardware**: Outdoor quadcopter with GPS, telemetry
- **Tools**:
  - `patrol_route(route_name)` — execute pre-approved patrol route
  - `return_to_home()` — emergency return
  - `get_telemetry()` — live altitude, battery, GPS coordinates
- **Safety Model**:
  - Patrol routes pre-programmed by user (no autonomous routing)
  - Always read-only unless explicit approval for new route
  - Integration with `security-mcp-server`: lockdown mode grounds drone
- **Integration Points**:
  - Telemetry feeds into `ui-bridge-mcp-server` (map visualization)
  - Incident detection (intrusion) triggers auto-return-to-home
  - CDG enforces "approved routes only" (rules check patrol_route names against whitelist)

**Stage 2: Robot Dog**
- **Hardware**: Boston Dynamics Spot or similar mobile platform
- **Tools**:
  - `patrol_route()`, `follow_person()` — navigation
  - `get_video_feed()` — built-in camera
  - `play_sound(audio_id)` — audio playback (alerts, dialogue)
  - `log_incident()` — falls through to `security-mcp-server`
- **Safety Model**:
  - Autonomous patrol only on pre-approved areas
  - Cannot approach/block exits without approval
  - Audio playback limited to pre-recorded authorized messages
  - Always under human observation or explicit user delegation

**Stage 3: Autonomous Response**
- Only after 6+ months of clean telemetry logging
- Requires user opt-in via explicit security override
- Actions: "follow intruder" or "gather evidence" (cameras + audio)
- No physical contact actions without explicit per-incident approval

#### Integration Points
- **security-mcp-server**: intrusion detection triggers drone telemetry log / robot approach
- **vision-mcp-server**: object tracking + person identification (feeds to robot-dog target)
- **ui-bridge-mcp-server**: live robot telemetry (battery, location, status)
- **CDG rules**: lockdown mode disables all robot motion (fail-safe)

#### Project Archer: Mobile Companion App

**Phase 5a: MVP (PWA)**
- **Platform**: Web-based Progressive Web App (installable on Android/iOS via browser)
- **Transport**: VPN (Tailscale/WireGuard) to home MCP servers
- **Components**:
  - Voice input: reuses `voice-mcp-server`
  - Transcription + response UI (simplified atom or text-based)
  - HA dashboard (lights, thermostats, quick actions)
  - Camera snapshots from `vision-mcp-server`
  - Status dashboard (drone telemetry, Proxmox alerts, etc.)
- **Sync**: MCP Resource subscription via WebSocket (same as desktop frontend)

**Phase 5b: Native App (Android/iOS)**
- Native SDK wrappers for iOS (Swift) / Android (Kotlin)
- Better background audio (voice wake-up, alerts)
- Deep VPN integration
- Push notifications for approvals/incidents
- Same MCP server architecture (reuse tau-core clients)

**Deployment**:
1. Build PWA as separate deployment target
2. Host behind Tailscale sidecar (no public internet exposure)
3. Test end-to-end voice → STT → LLM → tool → response TTS
4. Add native apps (Phase 5b+)

#### Design Considerations
- **Low-bandwidth mode**: compress audio/video for mobile (e.g., 480p cameras on 4G)
- **Battery-aware**: defer heavy operations, batch updates
- **Offline graceful degradation**: cached HA state if VPN drops
- **Same CDG**: mobile client respects same approval rules (can't bypass via mobile)

---

## Infrastructure Overlays (Phase 0 Ongoing)

### Network Segmentation
- [ ] VLAN isolation: Management, IoT, Camera, Core Agent, Secrets VLANs
- [ ] Firewall rules: Tau Core → MCP servers only
- [ ] Router hardening: disable unnecessary services, 2FA on management

### Secrets Management
- [ ] Vault deployment on isolated VLAN
- [ ] Scoped API tokens per MCP server (not shared root credentials)
- [ ] Automatic secret rotation (TLS certs, API keys, DB passwords)
- [ ] Secret audit trail (who accessed what, when)

### Encryption at Rest
- [ ] memory-mcp-server: encrypt embedding store (LUKS2 / dm-crypt)
- [ ] security-mcp-server: encrypt incident logs
- [ ] Database backups: encrypted snapshots to external storage

### Observability & Audit
- [ ] Prometheus scrape configs for all MCP servers
- [ ] Loki log aggregation (CDG decisions, tool calls, approvals)
- [ ] Grafana dashboards:
  - System health (CPU, memory, network)
  - MCP call latency + error rates
  - CDG approval queue depth
  - Proposal review timelines
- [ ] Long-term retention policy (6+ months of logs for security review)

### High Availability (Future)
- [ ] Multi-node k3s cluster across Proxmox hosts
- [ ] Persistent storage (NFS/Ceph) for memory/security/fabrication servers
- [ ] Load balancing for MCP server endpoints
- [ ] Automated failover + health checks

---

## Design Ideas & Future Directions

### 1. Augmented Reality (AR) Companion
- AR overlay in glasses (e.g., Xreal Air) showing:
  - Real-time scene annotations from `vision-mcp-server` (object labels, face IDs)
  - Navigation hints for `robotics-mcp-server` patrol routes
  - Approval prompts (raised to eye level, can gesture to approve)
- Transport: Bluetooth + VPN to tau-core

### 2. Multi-Agent Reasoning
- Distinct reasoning "modes":
  - **Optimizer**: Tau proposes efficient solutions (e.g., batch VM snapshots)
  - **Safety**: Secondary agent vetos unsafe proposals (before CDG sees them)
  - **Learner**: Sub-agent suggests patterns to user ("you always adjust thermostat at 6pm, automate?")
- All three route through CDG; all logged

### 3. Natural Language Policy Interface
- User writes policies in natural language (e.g., "don't let robots leave the backyard")
- LLM compiles to CDG rules (output: structured rule file)
- Tau verifies compilation (asks clarifying questions if ambiguous)
- User approves, rule gets added
- Enables rapid policy iteration without code edits

### 4. Federated Tau Instances
- Multiple homes running Tau independently
- **Delegation scenario**: User traveling, temporary guest (friend) Tau instance accesses cameras/lights in different home
- **Network**: VPN federation (Tailscale mesh)
- **Security**: Tau Core A cannot directly call servers on Tau Core B; instead, sends approval requests through a federation MCP server
- **Audit**: All cross-home actions logged in both local Loki instances

### 5. Time-Series Predictive Maintenance
- Proxmox + fabrication servers feed telemetry (uptime, error rates, wear) into time-series DB
- Sub-agent monitors trends (e.g., "hard disk error rate rising")
- Proactively alerts user + proposes maintenance actions
- Integration: scheduled job in `proxmox-mcp-server`, feeds into Tau's context

### 6. Adaptive Confidence Thresholds
- Learn per-domain confidence baseline from user approval history
- If user frequently approves suggestions in domain X, raise threshold (auto-execute more)
- If user frequently rejects or modifies suggestions in domain Y, lower threshold (ask more)
- Tunable per user, auditable

### 7. Voice Persona Customization
- Multiple TTS voice options + accent choices (via Piper)
- Tone setting (formal, casual, humorous)
- Domain-specific personas (e.g., "robot-dog announcer" for patrol alerts)
- Integration: `voice-mcp-server` selects voice variant based on context

---

## Integration Checklist

- [x] **Phase 1**: Tau Core + CDG
- [x] **Phase 2**: Core MCP servers (proxmox, HA, voice, memory, vision, security, fabrication)
- [ ] **Phase 3**: UI layer (in progress)
  - [ ] 3D atom frontend
  - [ ] MCP Resource subscription bridge
  - [ ] Contextual panels (camera, HA, approval queue)
  - [ ] E-ink design polish
- [ ] **Phase 4**: Self-upgrade governance
  - [ ] GitHub Actions CI/CD wiring
  - [ ] Ollama-backed reviewer agents
  - [ ] Proposal UI in frontend
  - [ ] Rollback mechanism
- [ ] **Phase 5a**: Robotics server + drone
  - [ ] Drone patrol route primitives
  - [ ] Telemetry integration
  - [ ] Safety constraints (lockdown mode)
- [ ] **Phase 5b**: Mobile companion (PWA + native)
  - [ ] PWA build + Tailscale sidecar
  - [ ] Voice I/O over VPN
  - [ ] HA quick-actions dashboard
- [ ] **Infra Overlays**:
  - [ ] Network segmentation (VLANs)
  - [ ] Secrets management (Vault)
  - [ ] Encryption at rest
  - [ ] Observability (Loki/Prometheus/Grafana)

---

## Open Questions & Decisions Needed

1. **Robot dog autonomy window**: At what confidence level + telemetry history should Archer enable autonomous actions?
2. **Mobile UI design**: Simplified atom or text-based? Landscape only or portrait?
3. **Federated approval**: If guest user wants to change light in other home, does it require both Tau instances to vote?
4. **AR glasses**: Which platform? (Xreal, Meta Quest Pro, etc.)
5. **Offline MCP servers**: Should some servers (voice, memory) have local fallbacks if network is down?
