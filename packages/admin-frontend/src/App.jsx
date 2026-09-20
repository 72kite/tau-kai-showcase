import React from 'react'
import { useSession } from './hooks/useSession'
import LoginScreen from './components/LoginScreen'
import Dashboard from './components/Dashboard'

export default function App() {
  const { token, username, checking, error, login, logout } = useSession()

  if (checking) {
    return <div className="loading-screen">Checking session…</div>
  }
  if (!token) {
    return <LoginScreen onLogin={login} error={error} />
  }
  return <Dashboard token={token} username={username} onLogout={logout} />
}
