import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { User, Guild, Operation } from '../api/types'

export function Home({ user }: { user: User | null }) {
  const [selectedGuild, setSelectedGuild] = useState<Guild | null>(null)
  const [operations, setOperations] = useState<Operation[]>([])
  const [newOpName, setNewOpName] = useState('')

  useEffect(() => {
    if (selectedGuild) {
      api.get(`/api/operations/${selectedGuild.id}`)
        .then(r => r.ok ? r.json() : [])
        .then(setOperations)
    }
  }, [selectedGuild])

  if (!user) {
    return (
      <div className="hero">
        <h1>ORBAT Platform</h1>
        <p>Arma 3 Operation Slot Management</p>
        <p className="hero-sub">Sign in with Discord to view operations and request slots.</p>
        <Link to="/login" className="btn btn-discord">Login with Discord</Link>
      </div>
    )
  }

  if (!selectedGuild) {
    return (
      <div className="guild-select">
        <h2>Select a Server</h2>
        <div className="guild-grid">
          {user.guilds.map(g => (
            <button key={g.id} className="guild-card" onClick={() => setSelectedGuild(g)}>
              {g.icon ? (
                <img src={`https://cdn.discordapp.com/icons/${g.id}/${g.icon}.png?size=64`}
                     alt="" className="guild-icon" />
              ) : (
                <div className="guild-icon guild-icon-placeholder">
                  {g.name.charAt(0)}
                </div>
              )}
              <span className="guild-name">{g.name}</span>
            </button>
          ))}
        </div>
      </div>
    )
  }

  const isAdmin = selectedGuild.owner || !!(parseInt(selectedGuild.permissions) & (0x20 | 0x8))

  const handleCreate = async () => {
    if (!newOpName.trim()) return
    const resp = await api.post('/api/operations/', {
      guild_id: selectedGuild.id,
      name: newOpName.trim(),
    })
    if (resp.ok) {
      const { id } = await resp.json()
      setNewOpName('')
      setOperations(prev => [{ id, guild_id: selectedGuild.id, name: newOpName.trim(),
        description: null, is_active: 1, event_time: null, created_at: null }, ...prev])
    }
  }

  return (
    <div className="operations-page">
      <div className="page-header">
        <button className="btn btn-sm" onClick={() => setSelectedGuild(null)}>&larr; Back</button>
        <h2>{selectedGuild.name} &mdash; Operations</h2>
      </div>

      {isAdmin && (
        <div className="create-op">
          <input type="text" placeholder="New operation name..."
                 value={newOpName} onChange={e => setNewOpName(e.target.value)}
                 onKeyDown={e => e.key === 'Enter' && handleCreate()} />
          <button className="btn btn-primary" onClick={handleCreate}>Create</button>
        </div>
      )}

      <div className="op-list">
        {operations.map(op => (
          <div key={op.id} className={`op-card ${op.is_active ? 'op-active' : ''}`}>
            <div className="op-info">
              <h3>{op.name}</h3>
              {op.is_active ? <span className="badge badge-green">Active</span> : null}
            </div>
            <div className="op-actions">
              <Link to={`/orbat/${op.id}`} className="btn btn-sm">View ORBAT</Link>
              {isAdmin && (
                <Link to={`/builder/${op.id}`} className="btn btn-sm btn-primary">Edit</Link>
              )}
            </div>
          </div>
        ))}
        {operations.length === 0 && (
          <p className="empty">No operations yet. {isAdmin ? 'Create one above.' : ''}</p>
        )}
      </div>
    </div>
  )
}
