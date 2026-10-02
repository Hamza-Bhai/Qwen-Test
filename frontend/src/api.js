// Python API server (api_server.py port 5000) se baat karne ke liye helpers.
// Vite proxy ki wajah se /api/* seedha backend pe chala jata hai.

const BASE = '/api'

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    throw new Error(data.error || data.message || `HTTP ${res.status}`)
  }
  return data
}

export const getDevices = () => request('/devices')
export const scanNetwork = () => request('/scan', { method: 'POST' })
export const getStats = () => request('/stats')
export const blockDevice = (target) =>
  request('/block', { method: 'POST', body: JSON.stringify({ target }) })
export const unblockDevice = (target) =>
  request('/unblock', { method: 'POST', body: JSON.stringify({ target }) })
export const wakeOnLan = (mac, ip) =>
  request('/wol', { method: 'POST', body: JSON.stringify({ mac, ip }) })
