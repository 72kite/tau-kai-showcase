import React, { useEffect, useState } from 'react'
import './DataLink.css'

// How many fine lines make up the bundle. Enough to read as a ribbon of data rather than a single
// pointer; few enough that they stay individually distinguishable at 1px.
const STRAND_COUNT = 7
// Vertical spread of the bundle where it leaves the node, and where it meets the frame. Tighter at
// the node end - the strands converge on the thing they came from and fan out across what they
// produced.
const NODE_SPREAD = 10
const FRAME_SPREAD = 46

// Re-sample rate. The node this attaches to is orbiting, so the link has to follow it - but it is
// a decorative line, and there is no reason for it to cost a React render per frame. ~12/s tracks
// the motion closely enough to read as attached while leaving the frame budget to the atom.
const SAMPLE_MS = 80

function prefersReducedMotion() {
  return (
    typeof window !== 'undefined' &&
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  )
}

/**
 * The retrieval data-link: a bundle of fine parallel lines drawn from a live node on the atom's
 * outermost orbit to the edge of the retrieved-image frame, so the picture reads as something
 * that came *out of* the system rather than something pasted beside it.
 *
 * `anchorRef` is written by Atom's frame loop with the node's current viewport position (see
 * Atom.jsx); `targetRef` is the framed image element. Both are measured in viewport coordinates
 * and the SVG is position:fixed over the whole viewport, so no shared layout parent is needed and
 * the two ends can live in completely different parts of the tree.
 *
 * Draws nothing at all unless both ends resolve. A link with one end guessed is worse than no
 * link: it would point confidently at a place no data came from, which is the opposite of what
 * this element is for.
 */
export default function DataLink({ anchorRef, targetRef, active = false }) {
  const [paths, setPaths] = useState([])

  useEffect(() => {
    if (!active) {
      setPaths([])
      return undefined
    }

    const sample = () => {
      const anchor = anchorRef?.current
      const target = targetRef?.current
      if (!anchor || !target) {
        setPaths([])
        return
      }
      const rect = target.getBoundingClientRect()
      if (rect.width === 0 || rect.height === 0) {
        setPaths([])
        return
      }

      // Meet the frame on whichever vertical edge faces the node, so the bundle never crosses
      // over the image it is pointing at.
      const nodeIsLeft = anchor.x <= rect.left + rect.width / 2
      const tx = nodeIsLeft ? rect.left : rect.right
      const ty = rect.top + rect.height / 2
      const dx = tx - anchor.x

      // A near-horizontal bundle that leaves the node sideways and arrives at the frame sideways.
      // Control points at 45% of the run keep every strand's curvature identical, which is what
      // makes them read as parallel rather than as a sheaf of unrelated arcs.
      const next = []
      for (let i = 0; i < STRAND_COUNT; i++) {
        const t = STRAND_COUNT === 1 ? 0 : i / (STRAND_COUNT - 1) - 0.5
        const ay = anchor.y + t * NODE_SPREAD
        const fy = ty + t * FRAME_SPREAD
        next.push(
          `M ${anchor.x.toFixed(1)} ${ay.toFixed(1)} ` +
            `C ${(anchor.x + dx * 0.45).toFixed(1)} ${ay.toFixed(1)}, ` +
            `${(tx - dx * 0.45).toFixed(1)} ${fy.toFixed(1)}, ` +
            `${tx.toFixed(1)} ${fy.toFixed(1)}`
        )
      }
      setPaths(next)
    }

    sample()
    // Reduced motion: the link is still drawn, it just stops chasing the orbiting node. The
    // information ("this image came from there") survives; the movement doesn't.
    if (prefersReducedMotion()) return undefined
    const id = setInterval(sample, SAMPLE_MS)
    return () => clearInterval(id)
  }, [active, anchorRef, targetRef])

  if (paths.length === 0) return null

  return (
    <svg className="data-link" aria-hidden="true" focusable="false">
      {paths.map((d, i) => (
        <path key={i} d={d} />
      ))}
    </svg>
  )
}
