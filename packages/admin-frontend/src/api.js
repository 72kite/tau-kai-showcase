// Where tau-admin-server lives. VITE_ADMIN_API_BASE at build time, overridable at runtime via
// localStorage the same way the kiosk frontend's getApiBase() supports a Tauri override -
// useful if this ever ships in its own desktop shell pointed at a different host than it was
// built for. Empty default assumes same-origin (a reverse proxy in front of both this bundle and
// tau-admin-server on one origin), which also sidesteps CORS entirely - see README for why that's
// the recommended deployment shape over cross-origin + TAU_ADMIN_ALLOWED_ORIGINS.
const OVERRIDE_KEY = 'tau-admin-api-base-override'

export function getApiBase() {
  return (
    localStorage.getItem(OVERRIDE_KEY) ||
    import.meta.env.VITE_ADMIN_API_BASE ||
    ''
  )
}

export function setApiBase(url) {
  localStorage.setItem(OVERRIDE_KEY, url || '')
}

export class ApiError extends Error {
  constructor(status, detail) {
    super(detail || `HTTP ${status}`)
    this.status = status
    this.detail = detail
  }
}

async function request(path, { token, method = 'GET', body } = {}) {
  const headers = {}
  if (token) headers.Authorization = `Bearer ${token}`
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  const res = await fetch(`${getApiBase()}${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  const text = await res.text()
  const data = text ? JSON.parse(text) : null
  if (!res.ok) {
    throw new ApiError(res.status, data && data.detail)
  }
  return data
}

export const api = {
  login: (username, password) => request('/login', { method: 'POST', body: { username, password } }),
  logout: (token) => request('/logout', { method: 'POST', token }),
  me: (token) => request('/me', { token }),
  changePassword: (token, currentPassword, newPassword) =>
    request('/change-password', {
      method: 'POST',
      token,
      body: { current_password: currentPassword, new_password: newPassword },
    }),

  listDevices: (token) => request('/api/devices', { token }),
  approveDevice: (token, id) => request(`/api/admin/devices/${encodeURIComponent(id)}/approve`, { method: 'POST', token }),
  blockDevice: (token, id) => request(`/api/admin/devices/${encodeURIComponent(id)}/block`, { method: 'POST', token }),
  unblockDevice: (token, id) => request(`/api/admin/devices/${encodeURIComponent(id)}/unblock`, { method: 'POST', token }),
  removeDevice: (token, id) => request(`/api/admin/devices/${encodeURIComponent(id)}`, { method: 'DELETE', token }),

  systemSnapshot: (token) => request('/api/admin/system', { token }),
  peopleSnapshot: (token) => request('/api/admin/people', { token }),
  securitySnapshot: (token) => request('/api/admin/security', { token }),
  unifiedTranscript: (token) => request('/api/transcript/unified', { token }),

  listDrafts: (token) => request('/api/drafts', { token }),
  promoteDraft: (token, nodeId, approvalRequestId) =>
    request(`/api/drafts/${encodeURIComponent(nodeId)}/promote`, {
      method: 'POST',
      token,
      body: { approval_request_id: approvalRequestId || null },
    }),
  discardDraft: (token, nodeId) =>
    request(`/api/drafts/${encodeURIComponent(nodeId)}/discard`, { method: 'POST', token }),

  listWakeWords: (token) => request('/api/voice/wake-words', { token }),
  setWakeWord: (token, wakeId, approvalRequestId) =>
    request('/api/voice/wake-words', {
      method: 'POST',
      token,
      body: { wake_id: wakeId, approval_request_id: approvalRequestId || null },
    }),

  listVoices: (token) => request('/api/voice/voices', { token }),
  setVoice: (token, voiceId, approvalRequestId) =>
    request('/api/voice/voices', {
      method: 'POST',
      token,
      body: { voice_id: voiceId, approval_request_id: approvalRequestId || null },
    }),

  activityFeed: (token, limit = 50) => request(`/api/activity?limit=${encodeURIComponent(limit)}`, { token }),
  setAccessLevel: (token, personId, accessLevel, approvalRequestId) =>
    request(`/api/people/${encodeURIComponent(personId)}/access-level`, {
      method: 'POST',
      token,
      body: { access_level: accessLevel, approval_request_id: approvalRequestId || null },
    }),

  listApprovals: (token) => request('/api/approvals', { token }),
  approveAction: (token, id, note) =>
    request(`/api/approvals/${encodeURIComponent(id)}/approve`, {
      method: 'POST',
      token,
      body: { note: note || null },
    }),
  denyAction: (token, id, note) =>
    request(`/api/approvals/${encodeURIComponent(id)}/deny`, {
      method: 'POST',
      token,
      body: { note: note || null },
    }),
}
