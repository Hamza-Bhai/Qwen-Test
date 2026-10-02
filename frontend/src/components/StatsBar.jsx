export default function StatsBar({ devices }) {
  const total = devices.length
  const blocked = devices.filter((d) => d.blocked).length
  const online = total - blocked
  const vendors = new Set(
    devices.map((d) => d.vendor).filter((v) => v && v !== 'Unknown'),
  ).size

  const stats = [
    { label: 'Total Devices', value: total, cls: 'stat-blue' },
    { label: 'Online', value: online, cls: 'stat-green' },
    { label: 'Blocked', value: blocked, cls: 'stat-red' },
    { label: 'Known Vendors', value: vendors, cls: 'stat-purple' },
  ]

  return (
    <div className="stats">
      {stats.map((s) => (
        <div key={s.label} className={`stat ${s.cls}`}>
          <div className="stat-value">{s.value}</div>
          <div className="stat-label">{s.label}</div>
        </div>
      ))}
    </div>
  )
}
