import React from 'react'
import { useNeuronActivity } from '../hooks/useNeuronActivity'
import './NeuronField.css'

/**
 * Neuron-style active state (Phase 6.C second pass): while Tau is working, the space the atom
 * slid aside to open (6.F) fills with a living picture of the turn instead of a static "…" -
 * one neuron per MCP server Tau is actually touching, firing down its dendrite each time that
 * server is called.
 *
 * Why here and not around the atom: the atom's outermost orbit already reaches ~85% of its
 * container's half-height, so a ring of neurons around it would collide with it - and 6.C's own
 * constraint is that nothing may break the atom's design. The response region is empty during
 * thinking anyway, so the field costs no space and gives way to the reply the moment it lands.
 *
 * Everything drawn here is backed by a real, CDG-audited tool call (see useNeuronActivity) - it
 * is the audit trail as a picture, not decoration. Denied and pending calls are drawn distinctly,
 * because those are the ones a human most wants to notice.
 */

// Fan geometry, in viewBox units. The soma sits at the left edge - the side the atom is on - and
// dendrites reach right, so the field reads as growing out of the atom rather than floating.
const VIEW_W = 170
const VIEW_H = 140
const SOMA = { x: 16, y: VIEW_H / 2 }
const DENDRITE_LEN = 112
// Kept narrow enough that the outermost dendrites stay inside VIEW_H: sin(FAN_DEG/2) *
// DENDRITE_LEN must clear the soma's distance to the edge, with room above for the label.
const FAN_DEG = 50

function nodePosition(index, count) {
  // A single call sits on the axis; several fan out evenly. Deterministic - a server's neuron
  // doesn't jump around as others come and go... beyond the re-fan, which is intentional: the
  // field should look like it's growing.
  const spread = count <= 1 ? 0 : FAN_DEG
  const deg = count <= 1 ? 0 : -spread / 2 + (index * spread) / (count - 1)
  const rad = (deg * Math.PI) / 180
  return {
    x: SOMA.x + Math.cos(rad) * DENDRITE_LEN,
    y: SOMA.y + Math.sin(rad) * DENDRITE_LEN,
  }
}

export default function NeuronField({ active }) {
  const nodes = useNeuronActivity(active)

  return (
    <div className={`neuron-field ${active ? 'active' : ''}`} aria-hidden="true">
      <svg viewBox={`0 0 ${VIEW_W} ${VIEW_H}`} preserveAspectRatio="xMidYMid meet">
        {/* Soma: the turn itself. Always beating while the field is up, so an empty field still
            reads as "working", not as "broken". */}
        <circle className="neuron-soma" cx={SOMA.x} cy={SOMA.y} r="5" />
        <circle className="neuron-soma-halo" cx={SOMA.x} cy={SOMA.y} r="5" />

        {nodes.map((node, i) => {
          const pos = nodePosition(i, nodes.length)
          return (
            <g key={node.server} className={`neuron outcome-${node.outcome}`}>
              {/* The dendrite is drawn twice: a faint permanent trace, and - keyed on firedAt so
                  React remounts it and the CSS animation restarts - a signal that redraws itself
                  from soma to node on every new call to this server. Same stroke-dashoffset
                  idiom as DesignDrawing's technical pen. */}
              <line
                className="dendrite-trace"
                x1={SOMA.x}
                y1={SOMA.y}
                x2={pos.x}
                y2={pos.y}
              />
              <line
                key={node.firedAt}
                className="dendrite-signal"
                x1={SOMA.x}
                y1={SOMA.y}
                x2={pos.x}
                y2={pos.y}
              />
              <circle className="neuron-node" cx={pos.x} cy={pos.y} r="4" />
              <circle key={`f-${node.firedAt}`} className="neuron-flash" cx={pos.x} cy={pos.y} r="4" />
              <text className="neuron-label" x={pos.x} y={pos.y - 9} textAnchor="middle">
                {node.label}
              </text>
            </g>
          )
        })}
      </svg>
    </div>
  )
}
