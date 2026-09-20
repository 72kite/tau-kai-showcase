import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import { isTauri } from './utils/tauri'
import { initAdminToken } from './utils/adminToken'
import './styles.css'

function renderApp() {
  ReactDOM.createRoot(document.getElementById('root')).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>
  )
}

// The Tauri shell's keychain cache (utils/adminToken.js) must be loaded from the OS keychain
// before the app's first render, so useSession's `useState(() => getAdminToken())` initializer
// never reads a not-yet-loaded cache - same reasoning as the kiosk's own main.jsx. The plain
// browser build never awaits anything here - isTauri() is false, so it renders immediately.
if (isTauri()) {
  initAdminToken().finally(renderApp)
} else {
  renderApp()
}
