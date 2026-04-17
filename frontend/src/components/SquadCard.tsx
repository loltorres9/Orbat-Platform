import { useState } from 'react'
import { api } from '../api/client'
import type { User, OrbatSquad } from '../api/types'
import { NatoSymbol } from './NatoSymbol'
import { SlotRow } from './SlotRow'

interface Props {
  squad: OrbatSquad
  user: User | null
  guildId: string
  onSlotRequested: () => void
}

export function SquadCard({ squad, user, guildId, onSlotRequested }: Props) {
  const [expanded, setExpanded] = useState(true)
  const [requesting, setRequesting] = useState<number | null>(null)

  const filled = squad.slots.filter(s => s.status === 'filled').length
  const total = squad.slots.length

  const handleRequest = async (slotId: number) => {
    if (!user || requesting) return
    setRequesting(slotId)
    try {
      const resp = await api.post(`/api/slots/${slotId}/request`, { guild_id: guildId })
      if (resp.ok) {
        onSlotRequested()
      } else {
        const err = await resp.json()
        alert(err.detail || 'Failed to request slot')
      }
    } catch {
      alert('Network error')
    } finally {
      setRequesting(null)
    }
  }

  return (
    <div className="squad-card">
      <div className="squad-header" onClick={() => setExpanded(!expanded)}>
        <NatoSymbol color={squad.color} />
        <div className="squad-info">
          <h3 className="squad-name">{squad.name}</h3>
          <span className="squad-count">{filled}/{total}</span>
        </div>
        <span className={`chevron ${expanded ? 'chevron-up' : ''}`}>&#9660;</span>
      </div>

      {expanded && (
        <div className="squad-slots">
          {squad.slots.map(slot => (
            <SlotRow
              key={slot.id}
              slot={slot}
              canRequest={!!user}
              requesting={requesting === slot.id}
              onRequest={() => handleRequest(slot.id)}
            />
          ))}
        </div>
      )}
    </div>
  )
}
