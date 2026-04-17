import { useState, useEffect, useCallback } from 'react'
import { useParams } from 'react-router-dom'
import { api } from '../api/client'
import type { User, Operation, OrbatResponse, OrbatSquad } from '../api/types'

export function AdminBuilder({ user }: { user: User | null }) {
  const { operationId } = useParams()
  const [orbat, setOrbat] = useState<OrbatResponse | null>(null)
  const [op, setOp] = useState<Operation | null>(null)
  const [newSquadName, setNewSquadName] = useState('')
  const [newSlotRole, setNewSlotRole] = useState<Record<number, string>>({})
  const [editingOp, setEditingOp] = useState(false)
  const [opName, setOpName] = useState('')

  const load = useCallback(() => {
    api.get(`/api/orbat/${operationId}`)
      .then(r => r.ok ? r.json() : Promise.reject('Failed'))
      .then((data: OrbatResponse) => {
        setOrbat(data)
        setOp(data.operation)
        setOpName(data.operation.name)
      })
  }, [operationId])

  useEffect(() => { load() }, [load])

  if (!user) return <div className="error">Please log in to access the builder.</div>
  if (!orbat || !op) return <div className="loading">Loading...</div>

  const handleAddSquad = async () => {
    if (!newSquadName.trim()) return
    const resp = await api.post(`/api/operations/${operationId}/squads`, {
      name: newSquadName.trim(),
      display_order: orbat.squads.length,
    })
    if (resp.ok) {
      setNewSquadName('')
      load()
    }
  }

  const handleDeleteSquad = async (squadId: number) => {
    if (!confirm('Delete this squad and all its slots?')) return
    await api.delete(`/api/squads/${squadId}`)
    load()
  }

  const handleAddSlot = async (squadId: number) => {
    const role = newSlotRole[squadId]?.trim()
    if (!role) return
    const squad = orbat.squads.find(s => s.id === squadId)
    const resp = await api.post(`/api/squads/${squadId}/slots`, {
      role_name: role,
      display_order: squad ? squad.slots.length : 0,
    })
    if (resp.ok) {
      setNewSlotRole(prev => ({ ...prev, [squadId]: '' }))
      load()
    }
  }

  const handleDeleteSlot = async (slotId: number) => {
    await api.delete(`/api/slots/${slotId}`)
    load()
  }

  const handleActivate = async () => {
    await api.post(`/api/operations/${operationId}/activate`)
    load()
  }

  const handleUpdateName = async () => {
    await api.put(`/api/operations/${operationId}`, { name: opName })
    setEditingOp(false)
    load()
  }

  return (
    <div className="builder-page">
      <div className="builder-header">
        {editingOp ? (
          <div className="inline-edit">
            <input value={opName} onChange={e => setOpName(e.target.value)}
                   onKeyDown={e => e.key === 'Enter' && handleUpdateName()} />
            <button className="btn btn-sm btn-primary" onClick={handleUpdateName}>Save</button>
            <button className="btn btn-sm" onClick={() => setEditingOp(false)}>Cancel</button>
          </div>
        ) : (
          <h1 onClick={() => setEditingOp(true)} className="editable">{op.name}</h1>
        )}
        <div className="builder-actions">
          {!op.is_active && (
            <button className="btn btn-primary" onClick={handleActivate}>Activate Operation</button>
          )}
          {op.is_active ? <span className="badge badge-green">Active</span> : null}
        </div>
      </div>

      <div className="builder-squads">
        {orbat.squads.map((squad: OrbatSquad) => (
          <div key={squad.id} className="builder-squad">
            <div className="builder-squad-header">
              <h3>{squad.name}</h3>
              <button className="btn btn-sm btn-danger" onClick={() => handleDeleteSquad(squad.id)}>
                Delete Squad
              </button>
            </div>

            <div className="builder-slots">
              {squad.slots.map(slot => (
                <div key={slot.id} className="builder-slot">
                  <span className="slot-role">{slot.role_name}</span>
                  {slot.assigned_to_name && (
                    <span className="slot-assigned">{slot.assigned_to_name}</span>
                  )}
                  <button className="btn-icon btn-delete" onClick={() => handleDeleteSlot(slot.id)}
                          title="Remove slot">&times;</button>
                </div>
              ))}
            </div>

            <div className="builder-add-slot">
              <input type="text" placeholder="Role name (e.g. Squad Lead, Rifleman)"
                     value={newSlotRole[squad.id] || ''}
                     onChange={e => setNewSlotRole(prev => ({ ...prev, [squad.id]: e.target.value }))}
                     onKeyDown={e => e.key === 'Enter' && handleAddSlot(squad.id)} />
              <button className="btn btn-sm btn-primary" onClick={() => handleAddSlot(squad.id)}>
                + Slot
              </button>
            </div>
          </div>
        ))}
      </div>

      <div className="builder-add-squad">
        <input type="text" placeholder="New squad name..."
               value={newSquadName} onChange={e => setNewSquadName(e.target.value)}
               onKeyDown={e => e.key === 'Enter' && handleAddSquad()} />
        <button className="btn btn-primary" onClick={handleAddSquad}>+ Add Squad</button>
      </div>
    </div>
  )
}
