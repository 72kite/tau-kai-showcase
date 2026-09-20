# Tau UI Design Guide

## Visual Language

**E-ink aesthetic** inspired by electronic paper displays: stark black (`#000000`) on white (`#ffffff`), no gradients, shadows, or soft glows. Typography is monospaced and grid-aligned. Borders are crisp: 1-4px solid lines. Animations are binary (on/off) or stepped, never smooth easing.

---

## Idle State (3D Atom)

```
┌──────────────────────────────────────────────────┐
│                                                  │
│                                                  │
│                    ╔═════╗                       │
│                   ╱       ╲                      │
│                  │  ● ◯ ◯ │  ← nucleus, orbits  │
│                   ╲ ◯     ╱                      │
│                    ╚═════╝                       │
│                                                  │
│            ◯ ◯ ◯ (electrons rotating)            │
│                                                  │
├─────────────────┬─────────────────┬──────────────┤
│ LOCKDOWN:       │ DEVICES:        │ INCIDENTS:   │
│ INACTIVE        │ 11/12           │ 0            │
└─────────────────┴─────────────────┴──────────────┘
```

**The atom**:
- Nucleus: wireframe sphere (black outline, white fill)
- Orbits: 3 concentric circles with electrons (small hollow circles)
- Electrons rotate around orbits at varying speeds
- Rotation speed increases with "complexity" metric (device online count, call queue depth, etc.)
- Very minimal animation: clean lines, no bloom or glow effects

**Status bar** (always visible, bottom):
- 3-column grid showing lockdown status, device connectivity, incident count
- Grid lines separate cells; high-contrast text and values
- "INACTIVE" vs "ACTIVE" states use different text weights or underlines

---

## Active State: Voice Transcription

```
┌──────────────────────────────────────────────────┐
│  [ATOM shrunk to corner]      TRANSCRIPTION     │
│        ◯─◯                                       │
│       ◯   ◯                   ┌─────────────────┤
│        ◯─◯                    │ ● LISTENING     │
│                               │ How are you     │
│    (150px × 150px)            │ doing today?▌   │
│    top-right, z-index 5       │                 │
│                               │ (live text,     │
│                               │  character by   │
│                               │  character      │
│                               │  streaming)     │
│                               │                 │
│                               └─────────────────┤
├─────────────────┬─────────────────┬──────────────┤
│ LOCKDOWN:       │ DEVICES:        │ INCIDENTS:   │
│ INACTIVE        │ 11/12           │ 0            │
└─────────────────┴─────────────────┴──────────────┘
```

**Atom transition**:
- Scale down to 30% and slide to top-right corner
- Z-index below the main panel
- Border remains visible (2px solid black)
- Electrons continue rotating at reduced speed

**Transcription panel**:
- Full-width, center stage
- "TRANSCRIPTION" header with underline
- "● LISTENING" indicator (blinking dot)
- Text box: monospaced font, live character stream
- Blinking cursor after the last character
- Crisp border (2-3px), no shadow

---

## Active State: Device Panel

```
┌──────────────────────────────────────────────────┐
│  [ATOM shrunk]                                   │
│        ◯─◯                                       │
│       ◯   ◯       HOME ASSISTANT DEVICES        │
│        ◯─◯        ────────────────────────      │
│                                                  │
│    (150px)        TOTAL DEVICES:      12         │
│    top-right      ONLINE:             11        │
│                   OFFLINE:             1        │
│                                                  │
│                   ┌──────────────────────────┐  │
│                   │██████████░░ 91%          │  │
│                   └──────────────────────────┘  │
│                                                  │
├─────────────────┬─────────────────┬──────────────┤
│ LOCKDOWN:       │ DEVICES:        │ INCIDENTS:   │
│ INACTIVE        │ 11/12           │ 0            │
└─────────────────┴─────────────────┴──────────────┘
```

**Device panel**:
- Grid layout: label on left, value on right
- Bar chart: solid black fill for online devices, hollow box for offline
- Percentage shown to the right of the chart
- All borders crisp, grid lines visible between rows

---

## Active State: Security/Lockdown

```
┌──────────────────────────────────────────────────┐
│  [ATOM shrunk]                                   │
│        ◯─◯                                       │
│       ◯   ◯       SECURITY STATUS               │
│        ◯─◯        ──────────────────            │
│                                                  │
│    (150px)        LOCKDOWN:          ACTIVE  ◀─ │
│    top-right                         (underline)│
│                   INCIDENTS LOGGED:   2        │
│                                                  │
│                   ┌──────────────────────────┐  │
│                   │ ⚠ 2 incident(s) detected │  │
│                   │ (pulsing border)         │  │
│                   └──────────────────────────┘  │
│                                                  │
├─────────────────┬─────────────────┬──────────────┤
│ LOCKDOWN:       │ DEVICES:        │ INCIDENTS:   │
│ ACTIVE          │ 11/12           │ 2            │
└─────────────────┴─────────────────┴──────────────┘
```

**Lockdown state**:
- Panel border becomes 4px (thicker)
- "LOCKDOWN: ACTIVE" row pulses (alternates black text on white ↔ white text on black, 0.8s cycle)
- Warning box appears with "⚠ N incident(s) detected"
- Warning box border pulses (3-4px thickness)
- Status bar updates to show "LOCKDOWN: ACTIVE" (underlined or bold)

---

## Active State: Vision/Camera

```
┌──────────────────────────────────────────────────┐
│  [ATOM shrunk]                                   │
│        ◯─◯                                       │
│       ◯   ◯       VISION FEED                   │
│        ◯─◯        ──────────────────            │
│                                                  │
│    (150px)        ● LIVE                        │
│    top-right      ┌──────────────────────────┐  │
│                   │  [Camera snapshot or     │  │
│                   │   placeholder]           │  │
│                   │  (16:9 aspect ratio)     │  │
│                   └──────────────────────────┘  │
│                   SCENE:                        │
│                   Person detected at entry.     │
│                   Confidence: 87%               │
│                                                  │
├─────────────────┬─────────────────┬──────────────┤
│ LOCKDOWN:       │ DEVICES:        │ INCIDENTS:   │
│ INACTIVE        │ 11/12           │ 0            │
└─────────────────┴─────────────────┴──────────────┘
```

**Vision panel**:
- Camera status: "● LIVE" (dot filled) or "○ OFFLINE" (dot hollow)
- Snapshot area: 16:9 aspect ratio, black border, placeholder or actual image
- Scene description: "SCENE:" label, then monospaced text below
- All grid-aligned, no rounded corners or shadows

---

## Design Details

### Typography
- **Headers**: `TRANSCRIPTION`, `HOME ASSISTANT DEVICES`, `SECURITY STATUS` → bold, all-caps, 16-18px
- **Labels**: `LOCKDOWN:`, `DEVICES:` → bold, all-caps, 12-14px
- **Values**: right-aligned, monospaced (`Courier New`), may be underlined or bold
- **Body text**: monospaced, 14px, 1.5 line-height

### Colors
- Foreground: `#000000` (pure black)
- Background: `#ffffff` (pure white)
- No accent colors yet (reserved for future warnings/alerts)

### Borders
- **Panels**: 2-3px solid black
- **Panel headers**: 2px black bottom border (under title)
- **Input/stat boxes**: 1px solid black
- **Lockdown mode**: 4px solid black
- **Warning boxes**: 3px solid black with pulse animation

### Animations
- **Listening indicator**: blinking dot (on/off, 0.6s cycle, step-based)
- **Text cursor**: blinking (on/off, 0.6s cycle)
- **Bar fill**: 0.3s ease-out transition on value change
- **Lockdown pulse**: 0.8s alternation (text color invert), step-based
- **Warning border pulse**: 0.8s border-width change (3-4px)
- **No smooth easing**: only `step-start`, `step-end`, or binary state changes

### Responsive Notes
- Currently desktop-only (1024px+ assumed)
- Panels scale with viewport
- Atom corner position fixed at 20px from top/right
- Status bar at bottom, full-width
- Future: mobile breakpoints (panels stack vertically, smaller fonts)

---

## Implementation Notes

- **SVG animations**: use `stroke-dashoffset` for "drawing" effect on SVG borders (optional, not yet implemented)
- **No CSS frameworks**: hand-written CSS grid + flexbox only
- **Three.js 3D**: wireframe (EdgesGeometry), no textures or complex materials
- **MCP integration**: currently mocked; later connects to `ui-bridge-mcp-server` Resources via WebSocket
