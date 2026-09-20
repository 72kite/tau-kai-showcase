import React from 'react'
import './DrawIn.css'

/**
 * Generative "drawn" reveal (Phase 6.C second pass): anything wrapped in this draws itself onto
 * the page like an artist sketching it, instead of just appearing.
 *
 * The mechanism is a wipe (`clip-path: inset(...)`) led by a thin ink pen-line, rather than the
 * per-element `stroke-dashoffset` animation DesignDrawing uses on Tau's SVG output. That's a
 * deliberate choice: stroke-drawing only works on stroked SVG geometry, and most of this UI is
 * text and bordered boxes. A wipe reveals *any* content - text, borders, canvases, generated SVG -
 * with one primitive, and it can't fight a component's existing border the way an SVG outline
 * traced over it would.
 *
 * `delay` staggers siblings so a group of panels draws in sequence, like a hand moving down the
 * page, rather than all at once. `direction` picks the pen's travel.
 *
 * Reduced motion is fully honored: content appears at once, with no wipe and no pen.
 */
export default function DrawIn({
  children,
  className = '',
  delay = 0,
  direction = 'right',
  as: Tag = 'div',
}) {
  return (
    <Tag
      className={`draw-in draw-${direction} ${className}`}
      style={delay ? { '--draw-delay': `${delay}ms` } : undefined}
    >
      {children}
    </Tag>
  )
}
