import { useState, useEffect, useCallback } from 'react'
import { useParams } from 'react-router-dom'
import { api } from '../api/client'
import type { User, OrbatResponse } from '../api/types'
import { SquadCard } from '../components/SquadCard'

export function OrbatView({ user }: { user: User | null }) {
  const { operationId } = useParams()
  const [orbat, setOrbat] = useState<OrbatResponse | null>(null)
  const [error, setError] = useState('')

  const load = useCallback(() => {
    api.get(`/api/orbat/${operationId}`)
      .then(r => r.ok ? r.json() : Promise.reject('Failed to load'))
      .then(setOrbat)
      .catch(e => setError(String(e)))
  }, [operationId])

  useEffect(() => { load() }, [load])

  // Poll for updates every 10 seconds
  useEffect(() => {
    const interval = setInterval(load, 10000)
    return () => clearInterval(interval)
  }, [load])

  if (error) return <div className="error">{error}</div>
  if (!orbat) return <div className="loading">Loading ORBAT...</div>

  const op = orbat.operation

  return (
    <div className="orbat-page">
      <div className="orbat-header">
        <h1>{op.name}</h1>
        {op.description && <p className="orbat-desc">{op.description}</p>}
        <div className="orbat-stats">
          <span className="stat stat-open">{orbat.open_slots} open</span>
          <span className="stat stat-pending">{orbat.pending_slots} pending</span>
          <span className="stat stat-filled">{orbat.filled_slots}/{orbat.total_slots} filled</span>
        </div>
        {op.event_time && (
          <p className="orbat-time">
            Operation starts: {new Date(op.event_time).toLocaleString()}
          </p>
        )}
      </div>

      <div className="orbat-tree">
        <div className="orbat-connector-top" />
        <div className="squad-grid">
          {orbat.squads.map(squad => (
            <SquadCard
              key={squad.id}
              squad={squad}
              user={user}
              guildId={op.guild_id}
              onSlotRequested={load}
            />
          ))}
        </div>
      </div>
    </div>
  )
}
