export type Session = {
  guild_id: string;
  user_id: string;
  username: string;
  avatar_url?: string | null;
  expires_at: string;
};

export type GuildPermissions = {
  guild_id: string;
  is_portal_admin: boolean;
  is_discord_admin: boolean;
  is_admin: boolean;
};

export type WebAdminEntry = {
  guild_id: string;
  user_id: string;
  username?: string | null;
  added_by?: string | null;
  created_at: string;
};

export type Operation = {
  id: number;
  guild_id: string;
  name: string;
  is_active: number;
  event_time?: string | null;
  reminder_minutes: number;
};

export type Slot = {
  id: number;
  role_name: string;
  display_order: number;
  assigned_to_member_id?: string | null;
  assigned_to_member_name?: string | null;
};

export type Squad = {
  id: number;
  name: string;
  display_order: number;
  slots: Slot[];
};

export type OrbatStructure = {
  operation: Operation;
  squads: Squad[];
};
