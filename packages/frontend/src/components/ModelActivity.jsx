import React from 'react'
import { useModelActivity } from '../hooks/useModelActivity'
import './ModelActivity.css'

/**
 * Small, non-intrusive readout of what the model is doing right now (Phase 6.C), sitting just
 * under the atom. Deliberately does NOT touch the atom itself - it's a separate line that fades
 * in only when there's something to say (a tool call in flight, or the model thinking) and is
 * absent otherwise, so idle stays clean.
 */
export default function ModelActivity({ mode, followUpSecondsLeft }) {
  const toolPhrase = useModelActivity(mode)
  // The bounded follow-up window (Phase 16) gets its own countdown text - distinct from, and
  // takes priority over, the tool-call phrase, since it's the one state where a visible clock is
  // actually useful (it tells the speaker how long they have before the mic closes again).
  // 'sleeping' (a stop-phrase closer) gets its own text too - the atom looks idle either way, so
  // without this there'd be no visible sign the wake phrase is being deliberately ignored.
  const phrase =
    mode === 'followup' && followUpSecondsLeft != null
      ? `still listening (${followUpSecondsLeft}s)`
      : mode === 'sleeping'
        ? 'resting - tap to resume'
        : toolPhrase
  return (
    <div className={`model-activity ${phrase ? 'active' : ''}`} aria-live="polite">
      {phrase && (
        <>
          <span className="model-activity-dot" />
          <span className="model-activity-text">{phrase}</span>
        </>
      )}
    </div>
  )
}
