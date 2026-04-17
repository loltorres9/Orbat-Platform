export type Session = {
  guild_id: string;
  user_id: string;
  username: string;
  avatar_url?: string | null;
  expires_at: string;
};

export type DiscordGuild = {
  id: string;
  name: string;
  icon_url?: string | null;
  is_owner: boolean;
  can_manage: boolean;
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
  lane_name_left?: string | null;
  lane_name_center?: string | null;
  lane_name_right?: string | null;
};

export type Slot = {
  id: number;
  role_name: string;
  display_order: number;
  team?: "Alpha" | "Bravo" | "Charlie" | "Delta" | null;
  assigned_to_member_id?: string | null;
  assigned_to_member_name?: string | null;
};

export type Squad = {
  id: number;
  name: string;
  display_order: number;
  column_index: number;
  slots: Slot[];
};

export type OrbatStructure = {
  operation: Operation;
  squads: Squad[];
};
