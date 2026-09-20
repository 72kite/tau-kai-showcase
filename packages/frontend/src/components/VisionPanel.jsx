import React from 'react'
import './Panel.css'

export default function VisionPanel({ state }) {
  return (
    <div className="panel vision-panel">
      <div className="panel-title">VISION FEED</div>
      <div className="panel-content">
        <div className="camera-status">
          <span className="status-label">
            {state.camera_active ? '● LIVE' : '○ OFFLINE'}
          </span>
        </div>
        {state.current_snapshot && (
          <div className="snapshot-placeholder">
            <img
              src={`data:image/jpeg;base64,${state.current_snapshot}`}
              alt="Camera snapshot"
            />
          </div>
        )}
        {state.scene_description && (
          <div className="scene-description">
            <span className="description-label">SCENE:</span>
            <span className="description-text">{state.scene_description}</span>
          </div>
        )}
      </div>
    </div>
  )
}
