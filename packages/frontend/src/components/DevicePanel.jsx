import React from 'react'
import './Panel.css'

export default function DevicePanel({ state }) {
  return (
    <div className="panel devices-panel">
      <div className="panel-title">HOME ASSISTANT DEVICES</div>
      <div className="panel-content">
        <div className="stat-line">
          <span className="stat-label">TOTAL DEVICES:</span>
          <span className="stat-value">{state.device_count}</span>
        </div>
        <div className="stat-line online">
          <span className="stat-label">ONLINE:</span>
          <span className="stat-value">{state.online_count}</span>
        </div>
        <div className="stat-line">
          <span className="stat-label">OFFLINE:</span>
          <span className="stat-value">{state.device_count - state.online_count}</span>
        </div>
        {state.device_count > 0 && (
          <div className="status-indicator">
            <div className="bar-chart">
              <div
                className="bar-fill"
                style={{
                  width: `${(state.online_count / state.device_count) * 100}%`,
                }}
              ></div>
            </div>
            <span className="percentage">
              {Math.round((state.online_count / state.device_count) * 100)}%
            </span>
          </div>
        )}
      </div>
    </div>
  )
}
