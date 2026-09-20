import React, { useState } from 'react'

export default function LoginScreen({ onLogin, error }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const submit = async (e) => {
    e.preventDefault()
    setSubmitting(true)
    await onLogin(username, password)
    setSubmitting(false)
  }

  return (
    <div className="login-screen">
      <form className="login-form" onSubmit={submit}>
        <h1>Tau Admin</h1>
        <label>
          Username
          <input
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            required
          />
        </label>
        <label>
          Password
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
          />
        </label>
        {error && <div className="login-error">{error}</div>}
        <button type="submit" disabled={submitting}>
          {submitting ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </div>
  )
}
