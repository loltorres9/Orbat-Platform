import type { Slot } from '../api/types'

interface Props {
  slot: Slot
  canRequest: boolean
  requesting: boolean
  onRequest: () => void
}

export function SlotRow({ slot, canRequest, requesting, onRequest }: Props) {
  const statusClass = `slot-status-${slot.status}`
  const statusDot = slot.status === 'filled' ? '\u{1F534}'
    : slot.status === 'pending' ? '\u{1F7E1}'
    : '\u{1F7E2}'

  return (
    <div className={`slot-row ${statusClass}`}>
      <span className="slot-dot">{statusDot}</span>
      <span className="slot-role">{slot.role_name}</span>

      {slot.status === 'filled' && (
        <span className="slot-assigned">{slot.assigned_to_name}</span>
      )}

      {slot.status === 'pending' && (
        <span className="slot-pending-label">
          pending {slot.pending_count > 1 ? `(${slot.pending_count})` : ''}
        </span>
      )}

      {slot.status !== 'filled' && canRequest && (
        <button
          className="btn btn-sm btn-request"
          onClick={onRequest}
          disabled={requesting}
        >
          {requesting ? '...' : 'Request'}
        </button>
      )}
    </div>
  )
}
