import React, { useRef } from 'react'
import { useFocusTrap } from '../hooks/useFocusTrap'
import DrawIn from './DrawIn'
import './ApprovalQueue.css'

/**
 * Approval Queue overlay — shows pending CDG approvals awaiting human authorization
 * (tau-core's generic PendingActionQueue, see project-tau-plan.md Phase 1 section 4).
 * Genuinely blocking by design (unlike the transcript/design panels): a pending action is
 * exactly the case where interrupting the user's attention is the correct behavior.
 */
export default function ApprovalQueue({
  pendingApprovals = [],
  onApprove,
  onDeny,
  challenge = null,
  onSpeakChallenge,
  onCancelChallenge,
}) {
  const active = pendingApprovals.length > 0 || !!challenge
  // Phase 50: trap focus inside while an approval is pending, same as SystemDrawer - but
  // deliberately NO onEscape. This overlay is genuinely blocking by design (see the docstring
  // above); a keyboard user should be able to reach APPROVE/DENY without tabbing into the app
  // underneath, but nothing should let them skip past a pending action, same as a mouse user
  // can't click through it either.
  const panelRef = useRef(null)
  useFocusTrap(panelRef, { active })

  if (!active) {
    return null
  }

  return (
    <div
      className="approval-queue-overlay"
      ref={panelRef}
      tabIndex={-1}
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="approval-queue-title"
    >
      <DrawIn className="approval-queue-panel" direction="down">
        <div className="approval-header">
          <span className="approval-icon">⚠</span>
          <span className="approval-title" id="approval-queue-title">APPROVAL REQUIRED</span>
          <span className="approval-count">{pendingApprovals.length}</span>
        </div>

        {challenge && (
          <div className="voice-challenge">
            <div className="challenge-title">VOICE VERIFICATION</div>
            <div className="challenge-instruction">
              To {challenge.decision} this action, read the phrase aloud:
            </div>
            <div className="challenge-phrase">{challenge.phrase}</div>
            <div className="challenge-note">{challenge.note}</div>
            <div className="approval-actions">
              <button
                type="button"
                className="approval-button approve"
                disabled={challenge.status === 'recording' || challenge.status === 'verifying'}
                onClick={() => onSpeakChallenge?.()}
              >
                {challenge.status === 'recording' ? '● RECORDING' : 'SPEAK PHRASE'}
              </button>
              <button type="button" className="approval-button deny" onClick={() => onCancelChallenge?.()}>
                CANCEL
              </button>
            </div>
          </div>
        )}

        <div className="approval-list">
          {pendingApprovals.map((approval, idx) => (
            <div key={approval.id || idx} className="approval-item">
              <div className="approval-action">
                <span className="action-name">{approval.tool}</span>
                <span className="action-server">@{approval.server}</span>
              </div>

              <div className="approval-reason">
                <span className="reason-label">REASON:</span>
                <span className="reason-text">{approval.reason}</span>
              </div>

              {approval.arguments && Object.entries(approval.arguments).length > 0 && (
                <div className="approval-params">
                  <span className="params-label">ARGUMENTS:</span>
                  {Object.entries(approval.arguments).map(([key, value]) => (
                    <div key={key} className="param-line">
                      <span className="param-name">{key}:</span>
                      <span className="param-value">
                        {typeof value === 'object' ? JSON.stringify(value) : String(value)}
                      </span>
                    </div>
                  ))}
                </div>
              )}

              <div className="approval-actions">
                <button
                  type="button"
                  className="approval-button approve"
                  onClick={() => onApprove?.(approval.id)}
                >
                  ✓ APPROVE
                </button>
                <button
                  type="button"
                  className="approval-button deny"
                  onClick={() => onDeny?.(approval.id)}
                >
                  ✗ DENY
                </button>
              </div>
            </div>
          ))}
        </div>
      </DrawIn>
    </div>
  )
}
