import type { DiscordGuild, GuildPermissions, Operation, OrbatStructure, Session, WebAdminEntry } from "./types";

const API_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "";
const SESSION_STORAGE_KEY = "orbat_session_token";

let sessionToken: string | null = null;
if (typeof window !== "undefined") {
  sessionToken = window.localStorage.getItem(SESSION_STORAGE_KEY);
}

export function setSessionToken(token: string | null) {
  sessionToken = token;
  if (typeof window === "undefined") return;
  if (token) {
    window.localStorage.setItem(SESSION_STORAGE_KEY, token);
  } else {
    window.localStorage.removeItem(SESSION_STORAGE_KEY);
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((init?.headers as Record<string, string> | undefined) || {}),
  };
  if (sessionToken) {
    headers["X-Orbat-Session"] = sessionToken;
  }
  const res = await fetch(`${API_BASE}${path}`, {
    credentials: "include",
    headers,
    ...init
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `Request failed (${res.status})`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => req<{ ok: boolean }>("/api/health"),
  session: () => req<Session>("/api/auth/session"),
  discordGuilds: () => req<DiscordGuild[]>("/api/auth/discord/guilds"),
  logout: () => req<{ ok: boolean }>("/api/auth/logout", { method: "POST" }),
  activeOperation: (guildId: string) => req<Operation>(`/api/operations/active?guild_id=${encodeURIComponent(guildId)}`),
  orbat: (operationId: number) => req<OrbatStructure>(`/api/operations/${operationId}/orbat`),
  requestSlot: (slotId: number, guildId: string) =>
    req<{ id: number; status: string }>(`/api/slots/${slotId}/request`, {
      method: "POST",
      body: JSON.stringify({ guild_id: guildId })
    }),
  createOperation: (payload: {
    guild_id: string;
    name: string;
    event_time?: string | null;
    reminder_minutes?: number;
    activate?: boolean;
  }) =>
    req<Operation>("/api/operations", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  addSquad: (operationId: number, payload: { name: string; display_order?: number }) =>
    req<{ id: number }>(`/api/operations/${operationId}/squads`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  updateSquad: (squadId: number, payload: { name?: string; display_order?: number }) =>
    req<{ ok: boolean }>(`/api/squads/${squadId}`, {
      method: "PATCH",
      body: JSON.stringify(payload)
    }),
  deleteSquad: (squadId: number) =>
    req<{ ok: boolean }>(`/api/squads/${squadId}`, { method: "DELETE" }),
  addSlot: (operationId: number, payload: { squad_id: number; role_name: string; display_order?: number }) =>
    req<{ id: number }>(`/api/operations/${operationId}/slots`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  deleteSlot: (slotId: number) =>
    req<{ ok: boolean }>(`/api/slots/${slotId}`, { method: "DELETE" }),
  activateOperation: (operationId: number) =>
    req<{ ok: boolean }>(`/api/operations/${operationId}/activate`, { method: "POST" }),
  guildPermissions: (guildId: string) =>
    req<GuildPermissions>(`/api/guilds/${encodeURIComponent(guildId)}/me/permissions`),
  listGuildAdmins: (guildId: string) =>
    req<WebAdminEntry[]>(`/api/guilds/${encodeURIComponent(guildId)}/admins`),
  addGuildAdmin: (guildId: string, payload: { user_id: string; username?: string }) =>
    req<{ ok: boolean }>(`/api/guilds/${encodeURIComponent(guildId)}/admins`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  removeGuildAdmin: (guildId: string, userId: string) =>
    req<{ ok: boolean }>(`/api/guilds/${encodeURIComponent(guildId)}/admins/${encodeURIComponent(userId)}`, {
      method: "DELETE"
    })
};

export function discordLoginUrl(guildId?: string, returnTo?: string): string {
  const rawBase = API_BASE || window.location.origin;
  const target = returnTo || (window.location.origin + window.location.pathname);
  const params = new URLSearchParams();
  if (guildId) params.set("guild_id", guildId);
  params.set("return_to", target);
  return `${rawBase}/api/auth/discord/login?${params.toString()}`;
}

export function openOperationSocket(operationId: number, onMessage: (data: unknown) => void): WebSocket {
  const rawBase = API_BASE || window.location.origin;
  const wsBase = rawBase.replace("https://", "wss://").replace("http://", "ws://");
  const ws = new WebSocket(`${wsBase}/ws/operations/${operationId}`);
  ws.onmessage = (ev) => {
    try {
      onMessage(JSON.parse(ev.data));
    } catch {
      onMessage(ev.data);
    }
  };
  return ws;
}
