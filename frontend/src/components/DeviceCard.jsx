export default function DeviceCard({ device, onBlock, onWake }) {
  const d = device
  const isMine = d.mine
  const status = d.blocked ? 'blocked' : 'online'

  return (
    <div className={`card ${d.blocked ? 'card-blocked' : ''}`}>
      <div className="card-main">
        <div className="device-title">
          <span className={`dot ${status}`} />
          <strong>{d.hostname && d.hostname !== '-' ? d.hostname : d.ip}</strong>
          {isMine && <span className="badge badge-mine">This PC</span>}
          {d.blocked && <span className="badge badge-blocked">BLOCKED</span>}
        </div>
        <div className="device-meta">
          <span title="IP Address">🌐 {d.ip}</span>
          <span title="MAC Address">🔗 {d.mac || '?'}</span>
          <span title="Manufacturer">🏭 {d.vendor || 'Unknown'}</span>
          {d.last_seen && (
            <span title="Last seen">🕒 {String(d.last_seen).replace('T', ' ')}</span>
          )}
        </div>
      </div>

      <div className="card-actions">
        <button
          className={`btn-sm ${d.blocked ? 'btn-unblock' : 'btn-block'}`}
          onClick={onBlock}
          title={d.blocked ? 'Device unblock karo' : 'Device block karo (root chahiye)'}
        >
          {d.blocked ? '🔓 Unblock' : '🔒 Block'}
        </button>
        <button
          className="btn-sm btn-wake"
          onClick={onWake}
          disabled={!d.mac || d.mac === '?'}
          title="Wake-on-LAN magic packet bhejo"
        >
          ⚡ Wake
        </button>
      </div>
    </div>
  )
}
