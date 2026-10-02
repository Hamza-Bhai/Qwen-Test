import { useEffect, useState, useCallback } from 'react'
import {
  getDevices,
  scanNetwork,
  blockDevice,
  unblockDevice,
  wakeOnLan,
} from './api.js'
import DeviceCard from './components/DeviceCard.jsx'
import StatsBar from './components/StatsBar.jsx'

export default function App() {
  const [devices, setDevices] = useState([])
  const [scanning, setScanning] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [toast, setToast] = useState('')
  const [search, setSearch] = useState('')
  const [lastScan, setLastScan] = useState(null)

  const showToast = (msg) => {
    setToast(msg)
    setTimeout(() => setToast(''), 3500)
  }

  const loadDevices = useCallback(async () => {
    try {
      setError('')
      const data = await getDevices()
      setDevices(data.devices || [])
    } catch (e) {
      setError(
        'API se connect nahi ho pa raha. Backend chala kar dekho: python3 api_server.py',
      )
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadDevices()
  }, [loadDevices])

  const handleScan = async () => {
    setScanning(true)
    setError('')
    try {
      const data = await scanNetwork()
      setDevices(data.devices || [])
      setLastScan(new Date().toLocaleTimeString())
      showToast(`Scan complete! ${data.count} device(s) mile`)
    } catch (e) {
      setError(`Scan fail: ${e.message}`)
    } finally {
      setScanning(false)
    }
  }

  const handleBlock = async (device) => {
    const target = device.blocked ? device.ip : device.mac !== '?' ? device.mac : device.ip
    try {
      if (device.blocked) {
        await unblockDevice(target)
        showToast(`${device.ip} unblock ho gaya`)
      } else {
        await blockDevice(target)
        showToast(`${device.ip} block ho gaya`)
      }
      loadDevices()
    } catch (e) {
      showToast(`Fail: ${e.message} (sudo/root chahiye ho sakta hai)`)
    }
  }

  const handleWake = async (device) => {
    if (!device.mac || device.mac === '?') {
      showToast('MAC address nahi hai, WoL possible nahi')
      return
    }
    try {
      const res = await wakeOnLan(device.mac)
      showToast(res.message || 'Magic packet bhej diya')
    } catch (e) {
      showToast(`WoL fail: ${e.message}`)
    }
  }

  const filtered = devices.filter((d) => {
    const q = search.toLowerCase()
    return (
      !q ||
      d.ip?.toLowerCase().includes(q) ||
      d.mac?.toLowerCase().includes(q) ||
      d.hostname?.toLowerCase().includes(q) ||
      d.vendor?.toLowerCase().includes(q)
    )
  })

  return (
    <div className="app">
      <header className="header">
        <div>
          <h1>📡 WiFi Network Scanner</h1>
          <p className="subtitle">
            Connected devices dekho aur manage karo
            {lastScan && <span className="muted"> · Last scan: {lastScan}</span>}
          </p>
        </div>
        <button className="btn-scan" onClick={handleScan} disabled={scanning}>
          {scanning ? (
            <>
              <span className="spinner" /> Scanning…
            </>
          ) : (
            '🔍 Scan Network'
          )}
        </button>
      </header>

      <StatsBar devices={devices} />

      <div className="toolbar">
        <input
          className="search"
          placeholder="Search IP / MAC / hostname / vendor…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <button className="btn-ghost" onClick={loadDevices}>
          ⟳ Refresh
        </button>
      </div>

      {error && <div className="error-banner">⚠️ {error}</div>}
      {toast && <div className="toast">{toast}</div>}

      <main className="list">
        {loading ? (
          <div className="empty">Loading…</div>
        ) : filtered.length === 0 ? (
          <div className="empty">
            {devices.length === 0
              ? 'Abhi koi device nahi. "Scan Network" dabao.'
              : 'Koi match nahi mila.'}
          </div>
        ) : (
          filtered.map((d) => (
            <DeviceCard
              key={`${d.ip}-${d.mac}`}
              device={d}
              onBlock={() => handleBlock(d)}
              onWake={() => handleWake(d)}
            />
          ))
        )}
      </main>

      <footer className="footer muted">
        Backend: api_server.py (port 5000) · Frontend: Vite + React (port 3000)
      </footer>
    </div>
  )
}
