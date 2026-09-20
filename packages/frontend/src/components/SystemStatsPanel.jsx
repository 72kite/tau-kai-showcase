import React from 'react'
import { useSystemStats } from '../hooks/useSystemStats'
import './Panel.css'

function pct(used, total) {
  return total > 0 ? Math.round((used / total) * 100) : 0
}

/**
 * Desktop-shell-only (Phase 27.D follow-up), same gating as ConnectionSettings in SystemDrawer -
 * this reads the CLIENT machine's own hardware (see useSystemStats.js), which only exists for the
 * Tauri build.
 */
export default function SystemStatsPanel() {
  const { stats, error } = useSystemStats()

  return (
    <div className="panel system-stats-panel">
      <div className="panel-title">CLIENT MACHINE</div>
      <div className="panel-content">
        {error && <div className="panel-note">Unavailable: {error}</div>}
        {!stats && !error && <div className="panel-note">Reading...</div>}
        {stats && (
          <>
            <div className="stat-line">
              <span className="stat-label">HOST:</span>
              <span className="stat-value">{stats.hostname}</span>
            </div>
            <div className="stat-line">
              <span className="stat-label">CPU:</span>
              <span className="stat-value">{stats.cpu_percent.toFixed(1)}%</span>
            </div>
            <div className="status-indicator">
              <div className="bar-chart">
                <div className="bar-fill" style={{ width: `${Math.min(100, stats.cpu_percent)}%` }} />
              </div>
              <span className="percentage">{Math.round(stats.cpu_percent)}%</span>
            </div>
            <div className="stat-line">
              <span className="stat-label">MEMORY:</span>
              <span className="stat-value">
                {stats.mem_used_mb.toLocaleString()} / {stats.mem_total_mb.toLocaleString()} MB
              </span>
            </div>
            <div className="status-indicator">
              <div className="bar-chart">
                <div
                  className="bar-fill"
                  style={{ width: `${pct(stats.mem_used_mb, stats.mem_total_mb)}%` }}
                />
              </div>
              <span className="percentage">{pct(stats.mem_used_mb, stats.mem_total_mb)}%</span>
            </div>
            <div className="stat-line">
              <span className="stat-label">DISK:</span>
              <span className="stat-value">
                {stats.disk_used_gb.toFixed(0)} / {stats.disk_total_gb.toFixed(0)} GB
              </span>
            </div>
            <div className="status-indicator">
              <div className="bar-chart">
                <div
                  className="bar-fill"
                  style={{ width: `${pct(stats.disk_used_gb, stats.disk_total_gb)}%` }}
                />
              </div>
              <span className="percentage">{pct(stats.disk_used_gb, stats.disk_total_gb)}%</span>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
