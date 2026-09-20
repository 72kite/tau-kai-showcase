import React from 'react'
import './Panel.css'

export default function SecurityPanel({ state }) {
  return (
    <div className={`panel security-panel ${state.lockdown_active ? 'lockdown' : ''}`}>
      <div className="panel-title">SECURITY STATUS</div>
      <div className="panel-content">
        <div className={`stat-line ${state.lockdown_active ? 'lockdown-active' : ''}`}>
          <span className="stat-label">LOCKDOWN:</span>
          <span className="stat-value">
            {state.lockdown_active ? 'ACTIVE' : 'INACTIVE'}
          </span>
        </div>
        <div className="stat-line">
          <span className="stat-label">INCIDENTS LOGGED:</span>
          <span className="stat-value">{state.intrusion_count}</span>
        </div>
        {state.intrusion_count > 0 && (
          <div className="warning-box">
            <span>⚠ {state.intrusion_count} incident(s) detected</span>
          </div>
        )}
      </div>
    </div>
  )
}
