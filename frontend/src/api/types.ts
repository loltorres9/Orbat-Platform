export interface User {
  id: string
  username: string
  avatar: string | null
  guilds: Guild[]
}

export interface Guild {
  id: string
  name: string
  icon: string | null
  owner: boolean
  permissions: string
}

export interface Operation {
  id: number
  guild_id: string
  name: string
  description: string | null
  is_active: number
  event_time: string | null
  created_at: string | null
}

export interface Squad {
  id: number
  operation_id: number
  name: string
  color: string
  display_order: number
}

export interface Slot {
  id: number
  squad_id: number
  role_name: string
  display_order: number
  assigned_to_member_id: string | null
  assigned_to_name: string | null
  status: 'available' | 'pending' | 'filled'
  pending_count: number
}

export interface OrbatSquad extends Squad {
  slots: Slot[]
}

export interface OrbatResponse {
  operation: Operation
  squads: OrbatSquad[]
  total_slots: number
  filled_slots: number
  pending_slots: number
  open_slots: number
}
