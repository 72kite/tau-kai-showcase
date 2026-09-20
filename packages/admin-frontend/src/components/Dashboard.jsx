import React from 'react'
import ApprovalsPanel from './ApprovalsPanel'
import DevicesPanel from './DevicesPanel'
import SystemPanel from './SystemPanel'
import SecurityPanel from './SecurityPanel'
import VoicePanel from './VoicePanel'
import WakeWordPanel from './WakeWordPanel'
import DraftsPanel from './DraftsPanel'
import UserProfilesPanel from './UserProfilesPanel'
import ActivityPanel from './ActivityPanel'
import UnifiedLogPanel from './UnifiedLogPanel'

// Grouped rather than one flat grid of equal-weight cards - the panels genuinely split into "who's
// using Tau," "what Tau itself is doing/how it's configured," and "what Tau has heard/inferred,"
// and a section heading makes that legible at a glance instead of the admin having to remember
// which card is which every time. Approvals (Phase 41) sits above all three groups, full width and
// ungrouped on purpose: a pending CDG-gated action is time-sensitive in a way none of the grouped
// panels are, and it needs to be seen whether or not anything else on the page has changed.
export default function Dashboard({ token, username, onLogout }) {
  return (
    <div className="dashboard">
      <header className="dashboard-header">
        <h1>Tau Admin</h1>
        <div>
          <span>{username}</span>
          <button type="button" onClick={onLogout}>
            Log out
          </button>
        </div>
      </header>
      <main className="dashboard-body">
        <section className="dashboard-section dashboard-section-approvals">
          <ApprovalsPanel token={token} />
        </section>

        <section className="dashboard-section">
          <h2 className="dashboard-section-title">Household</h2>
          <div className="dashboard-grid">
            <DevicesPanel token={token} />
            <UserProfilesPanel token={token} />
            <ActivityPanel token={token} />
          </div>
        </section>

        <section className="dashboard-section">
          <h2 className="dashboard-section-title">System &amp; behavior</h2>
          <div className="dashboard-grid">
            <SystemPanel token={token} />
            <SecurityPanel token={token} />
            <WakeWordPanel token={token} />
            <VoicePanel token={token} />
          </div>
        </section>

        <section className="dashboard-section">
          <h2 className="dashboard-section-title">Memory &amp; logs</h2>
          <div className="dashboard-grid">
            <DraftsPanel token={token} />
            <UnifiedLogPanel token={token} />
          </div>
        </section>
      </main>
    </div>
  )
}
