import { FormEvent, useEffect, useRef, useState } from "react";
import { api, discordLoginUrl, openOperationSocket, setSessionToken } from "./api";
import type { DiscordGuild, GuildPermissions, Operation, OrbatStructure, Session, Slot, Squad, WebAdminEntry } from "./types";

function App() {
  const SQUAD_LANES = [0, 1, 2] as const;
  const TEAM_OPTIONS = ["", "Alpha", "Bravo", "Charlie", "Delta"] as const;
  const teamLabel = (team: string) => (team && team.trim() ? team : "No Team");
  const teamGroupTitle = (team: string) => (team && team.trim() ? team : "");
  const defaultLaneLabel = (lane: number) => (lane === 0 ? "Left Wing" : lane === 1 ? "Center" : "Right Wing");
  const basePath = import.meta.env.BASE_URL || "/";
  const appHashUrl = `${window.location.origin}${basePath.endsWith("/") ? basePath : `${basePath}/`}#/app`;
  const buildAppHashUrl = () => appHashUrl;
  const getRoute = () => (window.location.hash.startsWith("#/app") ? "app" : "login");
  const isEmbedded = typeof window !== "undefined" && window.self !== window.top;

  const [route, setRoute] = useState<"login" | "app">(getRoute());
  const [guildId, setGuildId] = useState("");
  const [selectedGuildId, setSelectedGuildId] = useState("");
  const [guilds, setGuilds] = useState<DiscordGuild[]>([]);
  const [operation, setOperation] = useState<Operation | null>(null);
  const [orbat, setOrbat] = useState<OrbatStructure | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [permissions, setPermissions] = useState<GuildPermissions | null>(null);
  const [admins, setAdmins] = useState<WebAdminEntry[]>([]);
  const [status, setStatus] = useState("Disconnected");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [sessionChecked, setSessionChecked] = useState(false);
  const [sessionTokenScrubbed, setSessionTokenScrubbed] = useState(false);
  const [sessionReloadKey, setSessionReloadKey] = useState(0);
  const [showAdminModal, setShowAdminModal] = useState(false);
  const [adminOverlayEnabled, setAdminOverlayEnabled] = useState(false);
  const [showCreateOperationPanel, setShowCreateOperationPanel] = useState(false);

  const [newOperationName, setNewOperationName] = useState("");
  const [newOperationEventTime, setNewOperationEventTime] = useState("");
  const [newOperationReminderMinutes, setNewOperationReminderMinutes] = useState<15 | 30 | 45 | 60>(30);
  const [importNameOverride, setImportNameOverride] = useState("");
  const [importActivate, setImportActivate] = useState(false);
  const [importJsonText, setImportJsonText] = useState("");
  const [renameOperationName, setRenameOperationName] = useState("");
  const [copyOperationName, setCopyOperationName] = useState("");
  const [scheduleEventTime, setScheduleEventTime] = useState("");
  const [scheduleReminderMinutes, setScheduleReminderMinutes] = useState<15 | 30 | 45 | 60>(30);
  const [newSquadName, setNewSquadName] = useState("");
  const [newSquadNotes, setNewSquadNotes] = useState("");
  const [newSlotSquadId, setNewSlotSquadId] = useState<number | "">("");
  const [newSlotRole, setNewSlotRole] = useState("");
  const [newSlotTeam, setNewSlotTeam] = useState<(typeof TEAM_OPTIONS)[number]>("");
  const [newAdminUserId, setNewAdminUserId] = useState("");
  const [newAdminUsername, setNewAdminUsername] = useState("");
  const [editingSquadId, setEditingSquadId] = useState<number | null>(null);
  const [editingSquadName, setEditingSquadName] = useState("");
  const [editingSquadNotes, setEditingSquadNotes] = useState("");
  const [editingSlotId, setEditingSlotId] = useState<number | null>(null);
  const [editingSlotName, setEditingSlotName] = useState("");
  const [editingSlotTeam, setEditingSlotTeam] = useState<(typeof TEAM_OPTIONS)[number]>("");
  const [draggedSquadId, setDraggedSquadId] = useState<number | null>(null);
  const [laneNameLeft, setLaneNameLeft] = useState("Left Wing");
  const [laneNameCenter, setLaneNameCenter] = useState("Center");
  const [laneNameRight, setLaneNameRight] = useState("Right Wing");
  const reloadInFlightRef = useRef<Promise<void> | null>(null);
  const queuedReloadOperationIdRef = useRef<number | null>(null);
  const wsReloadTimerRef = useRef<number | null>(null);

  function extractSessionTokenFromLocation(): string | null {
    const href = window.location.href || "";
    const tokenMatch = href.match(/[?#&]orbat_session=([^&#]+)/i);
    if (!tokenMatch?.[1]) return null;
    try {
      return decodeURIComponent(tokenMatch[1]);
    } catch {
      return tokenMatch[1];
    }
  }

  function scrubSessionTokenFromLocation() {
    const url = new URL(window.location.href);
    let changed = false;
    if (url.searchParams.has("orbat_session")) {
      url.searchParams.delete("orbat_session");
      changed = true;
    }
    if (url.hash) {
      const hash = url.hash;
      const qIndex = hash.indexOf("?");
      if (qIndex >= 0) {
        const routePart = hash.slice(0, qIndex);
        const params = new URLSearchParams(hash.slice(qIndex + 1));
        if (params.has("orbat_session")) {
          params.delete("orbat_session");
          const rest = params.toString();
          url.hash = rest ? `${routePart}?${rest}` : routePart;
          changed = true;
        }
      } else {
        const next = hash.replace(/([&?])orbat_session=[^&?#]*/i, "").replace(/[?&]$/, "");
        if (next !== hash) {
          url.hash = next;
          changed = true;
        }
      }
    }
    if (changed) {
      window.history.replaceState({}, "", url.toString());
    }
  }

  async function refreshGuildAccess(targetGuildId: string) {
    const perms = await api.guildPermissions(targetGuildId);
    setPermissions(perms);
    if (perms.is_admin) {
      setAdmins(await api.listGuildAdmins(targetGuildId));
    } else {
      setAdmins([]);
      setShowAdminModal(false);
    }
  }

  function isNoActiveOperationError(err: unknown): boolean {
    const text = String(err);
    return text.includes("No active operation");
  }

  async function loadGuildContext(targetGuildId: string) {
    setLoading(true);
    setError(null);
    try {
      await refreshGuildAccess(targetGuildId);
      try {
        const op = await api.activeOperation(targetGuildId);
        const structure = await api.orbat(op.id);
        setOperation(structure.operation);
        setOrbat(structure);
        syncLaneNameState(structure.operation);
      } catch (err) {
        if (isNoActiveOperationError(err)) {
          setOperation(null);
          setOrbat(null);
          syncLaneNameState(null);
        } else {
          throw err;
        }
      }
      setGuildId(targetGuildId);
      setStatus("Guild loaded");
    } catch (err) {
      setError(String(err));
      setOperation(null);
      setOrbat(null);
      setPermissions(null);
      setAdmins([]);
      setStatus("Failed");
    } finally {
      setLoading(false);
    }
  }

  async function refreshGuildList() {
    const rows = await api.discordGuilds();
    setGuilds(rows);
    if (rows.length === 0) {
      setSelectedGuildId("");
      setGuildId("");
      setPermissions(null);
      setOperation(null);
      setOrbat(null);
      return;
    }
    const preferred = rows.find((g) => g.id === guildId) ?? rows[0];
    setSelectedGuildId(preferred.id);
  }

  function syncLaneNameState(op: Operation | null) {
    setLaneNameLeft((op?.lane_name_left || defaultLaneLabel(0)).trim() || defaultLaneLabel(0));
    setLaneNameCenter((op?.lane_name_center || defaultLaneLabel(1)).trim() || defaultLaneLabel(1));
    setLaneNameRight((op?.lane_name_right || defaultLaneLabel(2)).trim() || defaultLaneLabel(2));
  }

  function laneLabel(lane: number) {
    if (lane === 0) return laneNameLeft || defaultLaneLabel(0);
    if (lane === 1) return laneNameCenter || defaultLaneLabel(1);
    return laneNameRight || defaultLaneLabel(2);
  }

  async function reloadOperation(operationId: number) {
    const structure = await api.orbat(operationId);
    setOrbat(structure);
    setOperation(structure.operation);
    syncLaneNameState(structure.operation);
  }

  async function refreshOperation(operationId: number) {
    queuedReloadOperationIdRef.current = operationId;
    if (reloadInFlightRef.current) {
      await reloadInFlightRef.current;
      return;
    }

    const runner = (async () => {
      while (queuedReloadOperationIdRef.current != null) {
        const nextOperationId = queuedReloadOperationIdRef.current;
        queuedReloadOperationIdRef.current = null;
        await reloadOperation(nextOperationId);
      }
    })();

    reloadInFlightRef.current = runner;
    try {
      await runner;
    } finally {
      reloadInFlightRef.current = null;
    }
  }

  function laneBuckets(source: Squad[]) {
    const buckets: Record<number, Squad[]> = { 0: [], 1: [], 2: [] };
    for (const sq of source) {
      const lane = sq.column_index >= 0 && sq.column_index <= 2 ? sq.column_index : 0;
      buckets[lane].push(sq);
    }
    for (const lane of SQUAD_LANES) {
      buckets[lane].sort((a, b) => a.display_order - b.display_order || a.id - b.id);
    }
    return buckets;
  }

  function teamBuckets(slots: Slot[]) {
    const buckets: Record<string, Slot[]> = {};
    for (const team of TEAM_OPTIONS) buckets[team] = [];
    for (const slot of slots) {
      const rawTeam = slot.team && TEAM_OPTIONS.includes(slot.team as (typeof TEAM_OPTIONS)[number]) ? slot.team : "";
      const team = rawTeam || "";
      buckets[team].push(slot);
    }
    for (const team of TEAM_OPTIONS) {
      buckets[team].sort((a, b) => a.display_order - b.display_order || a.id - b.id);
    }
    return TEAM_OPTIONS.map((team) => ({ team, slots: buckets[team] })).filter((entry) => entry.slots.length > 0);
  }

  async function persistLaneLayout(buckets: Record<number, Squad[]>) {
    if (!permissions?.is_admin || !orbat || !operation) return;
    const updates: Array<Promise<unknown>> = [];
    for (const lane of SQUAD_LANES) {
      buckets[lane].forEach((sq, index) => {
        if (sq.column_index !== lane || sq.display_order !== index) {
          updates.push(api.updateSquad(sq.id, { column_index: lane, display_order: index }));
        }
      });
    }
    if (updates.length === 0) return;
    await Promise.all(updates);
    await refreshOperation(operation.id);
  }

  useEffect(() => {
    const onHashChange = () => setRoute(getRoute());
    window.addEventListener("hashchange", onHashChange);
    if (!window.location.hash) {
      window.location.hash = "#/login";
    } else {
      onHashChange();
    }
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  useEffect(() => {
    const token = extractSessionTokenFromLocation();
    if (token) {
      setSessionToken(token);
      if (!window.location.hash.startsWith("#/app")) {
        window.location.hash = "#/app";
      }
      setSessionReloadKey((v) => v + 1);
    }

    const url = new URL(window.location.href);
    const authError = url.searchParams.get("auth_error");
    if (authError) {
      setError(`Login failed: ${authError}`);
      url.searchParams.delete("auth_error");
      window.history.replaceState({}, "", url.toString());
    }
  }, []);

  useEffect(() => {
    const onHashToken = () => {
      const token = extractSessionTokenFromLocation();
      if (!token) return;
      setSessionToken(token);
      if (!window.location.hash.startsWith("#/app")) {
        window.location.hash = "#/app";
      }
      setSessionReloadKey((v) => v + 1);
    };
    window.addEventListener("hashchange", onHashToken);
    return () => window.removeEventListener("hashchange", onHashToken);
  }, []);

  useEffect(() => {
    // Keep token in URL until we actually established a valid session.
    // This avoids permanent logout loops when first bootstrap request fails transiently.
    if (!sessionChecked || sessionTokenScrubbed || !session) return;
    scrubSessionTokenFromLocation();
    setSessionTokenScrubbed(true);
  }, [sessionChecked, sessionTokenScrubbed, session]);

  useEffect(() => {
    setSessionChecked(false);
    let cancelled = false;
    (async () => {
      try {
        const s = await api.session();
        if (cancelled) return;
        setSession(s);
        setStatus(`Connected as ${s.username}`);
      } catch {
        if (cancelled) return;
        setSession(null);
        setStatus("Disconnected");
      } finally {
        if (cancelled) return;
        setSessionChecked(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sessionReloadKey]);

  useEffect(() => {
    if (!session) return;
    let cancelled = false;
    (async () => {
      try {
        await refreshGuildList();
      } catch (err) {
        if (!cancelled) setError(String(err));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [session]);

  useEffect(() => {
    if (!sessionChecked) {
      return;
    }
    if (!session && route !== "login") {
      window.location.hash = "#/login";
      return;
    }
    if (session && route !== "app") {
      window.location.hash = "#/app";
    }
  }, [session, route, sessionChecked]);

  useEffect(() => {
    if (!selectedGuildId) return;
    if (selectedGuildId === guildId && permissions) return;
    void loadGuildContext(selectedGuildId);
  }, [selectedGuildId]);

  useEffect(() => {
    if (!permissions?.is_admin) {
      setAdminOverlayEnabled(false);
      setShowCreateOperationPanel(false);
    }
  }, [permissions?.is_admin]);

  useEffect(() => {
    if (!operation) return;
    const ws = openOperationSocket(operation.id, async (data) => {
      // Ignore heartbeat-style connected payloads and coalesce burst updates.
      if (data && typeof data === "object" && (data as { event?: string }).event === "connected") {
        return;
      }
      if (wsReloadTimerRef.current != null) {
        window.clearTimeout(wsReloadTimerRef.current);
      }
      wsReloadTimerRef.current = window.setTimeout(() => {
        void refreshOperation(operation.id).catch(() => {
          // no-op
        });
      }, 120);
    });
    ws.onopen = () => setStatus("Realtime connected");
    ws.onclose = () => setStatus("Realtime disconnected");
    return () => {
      ws.close();
      if (wsReloadTimerRef.current != null) {
        window.clearTimeout(wsReloadTimerRef.current);
        wsReloadTimerRef.current = null;
      }
    };
  }, [operation?.id]);

  useEffect(() => {
    if (!operation) {
      setRenameOperationName("");
      setCopyOperationName("");
      setScheduleEventTime("");
      setScheduleReminderMinutes(30);
      return;
    }
    setRenameOperationName(operation.name);
    setCopyOperationName(`${operation.name} Copy`);
    setScheduleReminderMinutes(
      ([15, 30, 45, 60] as const).includes(operation.reminder_minutes as 15 | 30 | 45 | 60)
        ? (operation.reminder_minutes as 15 | 30 | 45 | 60)
        : 30
    );
    if (operation.event_time) {
      const dt = new Date(operation.event_time);
      if (!Number.isNaN(dt.getTime())) {
        const local = new Date(dt.getTime() - dt.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
        setScheduleEventTime(local);
      } else {
        setScheduleEventTime("");
      }
    } else {
      setScheduleEventTime("");
    }
  }, [operation?.id, operation?.name]);

  async function onSelectGuild(e: FormEvent) {
    e.preventDefault();
    if (!selectedGuildId) {
      setError("Please select a server.");
      return;
    }
    await loadGuildContext(selectedGuildId);
  }

  async function requestSlot(slotId: number) {
    if (!guildId || !session) {
      setError("Please log in and select a guild first.");
      return;
    }
    try {
      await api.requestSlot(slotId, guildId);
      if (operation) await refreshOperation(operation.id);
    } catch (err) {
      setError(String(err));
    }
  }

  async function releaseSlot(slotId: number) {
    if (!session) {
      setError("Please log in first.");
      return;
    }
    try {
      await api.releaseSlot(slotId);
      if (operation) await refreshOperation(operation.id);
    } catch (err) {
      setError(String(err));
    }
  }

  async function logout() {
    try {
      await api.logout();
    } finally {
      setSessionToken(null);
      setSession(null);
      setPermissions(null);
      setAdmins([]);
      setGuilds([]);
      setGuildId("");
      setSelectedGuildId("");
      setOperation(null);
      setOrbat(null);
      syncLaneNameState(null);
      setShowAdminModal(false);
      setStatus("Disconnected");
    }
  }

  function startDiscordLogin() {
    const loginUrl = discordLoginUrl(undefined, buildAppHashUrl());
    window.location.href = loginUrl;
  }

  async function createOperation() {
    if (!guildId.trim()) {
      setError("Please select a guild first.");
      return;
    }
    if (!newOperationName.trim()) {
      setError("Please enter an operation name.");
      return;
    }
    if (!permissions?.is_admin) {
      setError("You are not allowed to use the Admin Builder for this guild.");
      return;
    }
    try {
      const op = await api.createOperation({
        guild_id: guildId.trim(),
        name: newOperationName.trim(),
        event_time: newOperationEventTime || null,
        reminder_minutes: newOperationReminderMinutes,
        activate: true
      });
      await refreshOperation(op.id);
      setNewOperationName("");
      setNewOperationEventTime("");
      setNewOperationReminderMinutes(30);
    } catch (err) {
      setError(String(err));
    }
  }

  async function saveOperationSchedule() {
    if (!operation || !permissions?.is_admin) return;
    try {
      const updated = await api.updateOperationSchedule(operation.id, {
        event_time: scheduleEventTime || null,
        reminder_minutes: scheduleReminderMinutes,
      });
      setOperation(updated);
      await refreshOperation(updated.id);
    } catch (err) {
      setError(String(err));
    }
  }

  async function exportOperation() {
    if (!operation || !permissions?.is_admin) return;
    try {
      const data = await api.exportOperation(operation.id);
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      const safeName = operation.name.replace(/[^a-z0-9-_]+/gi, "_");
      a.href = url;
      a.download = `orbat-event-${safeName || operation.id}.json`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(String(err));
    }
  }

  async function importOperation() {
    if (!guildId || !permissions?.is_admin) return;
    if (!importJsonText.trim()) {
      setError("Paste exported event JSON first.");
      return;
    }
    try {
      const parsed = JSON.parse(importJsonText);
      const created = await api.importOperation(guildId, {
        data: parsed,
        name_override: importNameOverride.trim() || undefined,
        activate: importActivate,
      });
      await refreshOperation(created.id);
      setStatus(`Imported operation loaded: ${created.name}`);
      setImportJsonText("");
      setImportNameOverride("");
      setImportActivate(false);
    } catch (err) {
      setError(String(err));
    }
  }

  async function renameOperation() {
    if (!operation || !permissions?.is_admin) return;
    const name = renameOperationName.trim();
    if (!name) {
      setError("Please enter a valid operation name.");
      return;
    }
    try {
      const updated = await api.updateOperation(operation.id, { name });
      setOperation(updated);
      setRenameOperationName(updated.name);
      if (orbat) {
        setOrbat({ ...orbat, operation: updated });
      }
    } catch (err) {
      setError(String(err));
    }
  }

  async function copyOperation() {
    if (!operation || !permissions?.is_admin) return;
    const name = copyOperationName.trim();
    if (!name) {
      setError("Please enter a name for the copied operation.");
      return;
    }
    try {
      const copied = await api.copyOperation(operation.id, { name, activate: false });
      await refreshOperation(copied.id);
      setStatus(`Copied operation loaded: ${copied.name}`);
    } catch (err) {
      setError(String(err));
    }
  }

  async function addSquad() {
    if (!operation || !newSquadName.trim() || !permissions?.is_admin) return;
    try {
      await api.addSquad(operation.id, {
        name: newSquadName.trim(),
        column_index: 1,
        notes: newSquadNotes.trim() || null,
      });
      await refreshOperation(operation.id);
      setNewSquadName("");
      setNewSquadNotes("");
    } catch (err) {
      setError(String(err));
    }
  }

  async function saveLaneNames() {
    if (!operation || !permissions?.is_admin) return;
    try {
      await api.updateOperationLanes(operation.id, {
        lane_name_left: laneNameLeft.trim() || defaultLaneLabel(0),
        lane_name_center: laneNameCenter.trim() || defaultLaneLabel(1),
        lane_name_right: laneNameRight.trim() || defaultLaneLabel(2),
      });
      await refreshOperation(operation.id);
    } catch (err) {
      setError(String(err));
    }
  }

  async function deleteSquad(squadId: number) {
    if (!operation || !permissions?.is_admin) return;
    try {
      await api.deleteSquad(squadId);
      await refreshOperation(operation.id);
    } catch (err) {
      setError(String(err));
    }
  }

  function beginEditSquad(squad: Squad) {
    setEditingSquadId(squad.id);
    setEditingSquadName(squad.name);
    setEditingSquadNotes(squad.notes || "");
  }

  function cancelEditSquad() {
    setEditingSquadId(null);
    setEditingSquadName("");
    setEditingSquadNotes("");
  }

  async function saveEditSquad() {
    if (!editingSquadId || !operation || !permissions?.is_admin) return;
    if (!editingSquadName.trim()) {
      setError("Squad name cannot be empty.");
      return;
    }
    try {
      await api.updateSquad(editingSquadId, {
        name: editingSquadName.trim(),
        notes: editingSquadNotes.trim() || null,
      });
      await refreshOperation(operation.id);
      cancelEditSquad();
    } catch (err) {
      setError(String(err));
    }
  }

  async function moveSquad(squad: Squad, direction: "up" | "down") {
    if (!operation || !permissions?.is_admin || !orbat) return;
    const lane = squad.column_index >= 0 && squad.column_index <= 2 ? squad.column_index : 0;
    const ordered = [...orbat.squads]
      .filter((s) => (s.column_index >= 0 && s.column_index <= 2 ? s.column_index : 0) === lane)
      .sort((a, b) => a.display_order - b.display_order);
    const idx = ordered.findIndex((s) => s.id === squad.id);
    if (idx < 0) return;
    const swapIdx = direction === "up" ? idx - 1 : idx + 1;
    if (swapIdx < 0 || swapIdx >= ordered.length) return;
    const other = ordered[swapIdx];
    try {
      await api.updateSquad(squad.id, { display_order: other.display_order });
      await api.updateSquad(other.id, { display_order: squad.display_order });
      await refreshOperation(operation.id);
    } catch (err) {
      setError(String(err));
    }
  }

  function onSquadDragStart(squadId: number) {
    setDraggedSquadId(squadId);
  }

  function onSquadDragEnd() {
    setDraggedSquadId(null);
  }

  async function dropSquadAt(lane: number, insertIndex: number) {
    if (!operation || !permissions?.is_admin || !orbat || draggedSquadId == null) return;
    const buckets = laneBuckets(orbat.squads);
    let dragged: Squad | null = null;
    for (const currentLane of SQUAD_LANES) {
      const idx = buckets[currentLane].findIndex((sq) => sq.id === draggedSquadId);
      if (idx >= 0) {
        dragged = buckets[currentLane][idx];
        buckets[currentLane].splice(idx, 1);
        break;
      }
    }
    if (!dragged) return;
    const target = buckets[lane];
    const safeIndex = Math.max(0, Math.min(insertIndex, target.length));
    target.splice(safeIndex, 0, dragged);
    try {
      await persistLaneLayout(buckets);
    } catch (err) {
      setError(String(err));
    } finally {
      setDraggedSquadId(null);
    }
  }

  async function copySquad(squad: Squad) {
    if (!operation || !permissions?.is_admin) return;
    try {
      const created = await api.addSquad(operation.id, {
        name: `${squad.name} Copy`,
        column_index: squad.column_index,
        notes: squad.notes || null,
      });
      await Promise.all(
        squad.slots.map((slot) =>
          api.addSlot(operation.id, {
          squad_id: created.id,
          role_name: slot.role_name,
          display_order: slot.display_order,
          team: slot.team || null,
          })
        )
      );
      await refreshOperation(operation.id);
    } catch (err) {
      setError(String(err));
    }
  }

  async function addSlot() {
    if (!operation || !newSlotRole.trim() || newSlotSquadId === "" || !permissions?.is_admin) return;
    try {
      await api.addSlot(operation.id, {
        squad_id: Number(newSlotSquadId),
        role_name: newSlotRole.trim(),
        team: newSlotTeam || null,
      });
      await refreshOperation(operation.id);
      setNewSlotRole("");
      setNewSlotTeam("");
    } catch (err) {
      setError(String(err));
    }
  }

  async function deleteSlot(slotId: number) {
    if (!operation || !permissions?.is_admin) return;
    try {
      await api.deleteSlot(slotId);
      await refreshOperation(operation.id);
    } catch (err) {
      setError(String(err));
    }
  }

  function beginEditSlot(slot: Slot) {
    setEditingSlotId(slot.id);
    setEditingSlotName(slot.role_name);
    setEditingSlotTeam((slot.team as (typeof TEAM_OPTIONS)[number]) || "");
  }

  function cancelEditSlot() {
    setEditingSlotId(null);
    setEditingSlotName("");
    setEditingSlotTeam("");
  }

  async function saveEditSlot() {
    if (!editingSlotId || !operation || !permissions?.is_admin) return;
    if (!editingSlotName.trim()) {
      setError("Role name cannot be empty.");
      return;
    }
    try {
      await api.updateSlot(editingSlotId, {
        role_name: editingSlotName.trim(),
        team: editingSlotTeam || null,
      });
      await refreshOperation(operation.id);
      cancelEditSlot();
    } catch (err) {
      setError(String(err));
    }
  }

  async function moveSlot(squad: Squad, slot: Slot, direction: "up" | "down") {
    if (!operation || !permissions?.is_admin) return;
    const ordered = [...squad.slots].sort((a, b) => a.display_order - b.display_order);
    const idx = ordered.findIndex((s) => s.id === slot.id);
    if (idx < 0) return;
    const swapIdx = direction === "up" ? idx - 1 : idx + 1;
    if (swapIdx < 0 || swapIdx >= ordered.length) return;
    const other = ordered[swapIdx];
    try {
      await api.updateSlot(slot.id, { display_order: other.display_order });
      await api.updateSlot(other.id, { display_order: slot.display_order });
      await refreshOperation(operation.id);
    } catch (err) {
      setError(String(err));
    }
  }

  async function addAdmin() {
    if (!guildId.trim() || !permissions?.is_admin) return;
    if (!newAdminUserId.trim()) {
      setError("Enter a Discord user id.");
      return;
    }
    try {
      await api.addGuildAdmin(guildId.trim(), {
        user_id: newAdminUserId.trim(),
        username: newAdminUsername.trim() || undefined
      });
      setAdmins(await api.listGuildAdmins(guildId.trim()));
      setNewAdminUserId("");
      setNewAdminUsername("");
    } catch (err) {
      setError(String(err));
    }
  }

  async function removeAdmin(userId: string) {
    if (!guildId.trim() || !permissions?.is_admin) return;
    try {
      await api.removeGuildAdmin(guildId.trim(), userId);
      setAdmins(await api.listGuildAdmins(guildId.trim()));
    } catch (err) {
      setError(String(err));
    }
  }

  const squadsByLane = laneBuckets(orbat?.squads ?? []);

  if (!sessionChecked) {
    return (
      <div className="page">
        <section className="panel login-panel">
          <h1>TASK FORCE PHALANX Orbat Platform</h1>
          <p className="access-note">Connecting...</p>
        </section>
      </div>
    );
  }

  if (!session) {
    return (
      <div className="page">
        <section className="panel login-panel">
          <h1>TASK FORCE PHALANX Orbat Platform</h1>
          <div className="login-stack">
            <p className="access-note">Please sign in with Discord first.</p>
            {isEmbedded ? (
              <>
                <p className="access-note">
                  Embedded mode detected: Discord blocks iframe auth. Use a new tab for login.
                </p>
                <a
                  className="button-link"
                  href={discordLoginUrl(undefined, buildAppHashUrl())}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Login with Discord
                </a>
              </>
            ) : (
              <button type="button" className="button-link" onClick={startDiscordLogin}>
                Login with Discord
              </button>
            )}
            {error && <p className="error">{error}</p>}
          </div>
        </section>
      </div>
    );
  }

  if (route !== "app") {
    return null;
  }

  return (
    <div className="page">
      <header className="hero">
        <div className="title-wrap">
          <h1>TASK FORCE PHALANX Orbat Platform</h1>
        </div>
        <div className="row">
          {permissions?.is_admin ? (
            <button
              type="button"
              className={adminOverlayEnabled ? "ghost-btn" : ""}
              onClick={() => setAdminOverlayEnabled((v) => !v)}
            >
              {adminOverlayEnabled ? "Normal View" : "Admin Overlay"}
            </button>
          ) : null}
          <button onClick={logout}>Logout ({session.username})</button>
        </div>
      </header>

      <section className="panel">
        <h2>Select Server</h2>
        <form onSubmit={onSelectGuild} className="row">
          <select value={selectedGuildId} onChange={(e) => setSelectedGuildId(e.target.value)}>
            <option value="">Select a Discord server</option>
            {guilds.map((g) => (
              <option key={g.id} value={g.id}>
                {g.name}
              </option>
            ))}
          </select>
          <button type="submit" disabled={loading || !selectedGuildId}>
            {loading ? "Loading..." : "Load Server"}
          </button>
        </form>
        {guilds.length === 0 && <p className="access-note">No shared servers found. The bot must be present in the server.</p>}
        {permissions && (
            <p className="access-note">
              Access: {permissions.is_admin ? "Admin" : "Viewer"} ({permissions.is_discord_admin ? "Discord" : "Portal"} auth)
            </p>
          )}
      </section>

      {adminOverlayEnabled && permissions?.is_admin ? (
      <section className="panel">
        <h2>Admin Builder</h2>
        {!permissions?.is_admin && <p className="access-note">Admin access required.</p>}
        <div className="admin-sections">
          <div className="admin-section admin-section--full">
            <div className="admin-section-head">
              <h3>Operations</h3>
              <div className="admin-head-actions">
                <button
                  type="button"
                  className="ghost-btn"
                  onClick={() => setShowCreateOperationPanel((v) => !v)}
                >
                  {showCreateOperationPanel ? "Hide Create Operation" : "Create Operation"}
                </button>
                <button type="button" className="ghost-btn" onClick={() => setShowAdminModal(true)}>
                  Manage Admins
                </button>
              </div>
            </div>
            {showCreateOperationPanel ? (
              <div className="row admin-grid-4">
                <input
                  value={newOperationName}
                  onChange={(e) => setNewOperationName(e.target.value)}
                  placeholder="New operation name"
                />
                <input
                  type="datetime-local"
                  value={newOperationEventTime}
                  onChange={(e) => setNewOperationEventTime(e.target.value)}
                  placeholder="Event time"
                />
                <select
                  value={newOperationReminderMinutes}
                  onChange={(e) => setNewOperationReminderMinutes(Number(e.target.value) as 15 | 30 | 45 | 60)}
                >
                  <option value={15}>Reminder 15 min before</option>
                  <option value={30}>Reminder 30 min before</option>
                  <option value={45}>Reminder 45 min before</option>
                  <option value={60}>Reminder 60 min before</option>
                </select>
                <button className="action-btn" onClick={createOperation} disabled={!permissions?.is_admin}>Create Operation</button>
              </div>
            ) : null}
            {operation ? (
              <div className="row admin-grid-4">
                <input
                  value={renameOperationName}
                  onChange={(e) => setRenameOperationName(e.target.value)}
                  placeholder="Operation name"
                />
                <button className="action-btn" onClick={renameOperation}>Rename Operation</button>
                <input
                  value={copyOperationName}
                  onChange={(e) => setCopyOperationName(e.target.value)}
                  placeholder="Copied operation name"
                />
                <button className="action-btn ghost-btn" onClick={copyOperation}>Copy Operation</button>
              </div>
            ) : null}
          </div>

          {operation ? (
            <>
              <div className="admin-section">
                <h3>Schedule</h3>
                <div className="schedule-layout">
                  <div className="schedule-controls">
                    <input
                      type="datetime-local"
                      value={scheduleEventTime}
                      onChange={(e) => setScheduleEventTime(e.target.value)}
                      placeholder="Event time"
                    />
                    <select
                      value={scheduleReminderMinutes}
                      onChange={(e) => setScheduleReminderMinutes(Number(e.target.value) as 15 | 30 | 45 | 60)}
                    >
                      <option value={15}>Reminder 15 min before</option>
                      <option value={30}>Reminder 30 min before</option>
                      <option value={45}>Reminder 45 min before</option>
                      <option value={60}>Reminder 60 min before</option>
                    </select>
                  </div>
                  <div className="schedule-actions">
                    <button className="action-btn" onClick={saveOperationSchedule}>Save Event Schedule</button>
                    <button className="action-btn ghost-btn" onClick={exportOperation}>Export Event</button>
                  </div>
                </div>
              </div>

              <div className="admin-section">
                <h3>Import Event</h3>
                <div className="import-layout">
                  <input
                    value={importNameOverride}
                    onChange={(e) => setImportNameOverride(e.target.value)}
                    placeholder="Imported operation name override (optional)"
                  />
                  <label className="inline-check">
                    <input
                      type="checkbox"
                      checked={importActivate}
                      onChange={(e) => setImportActivate(e.target.checked)}
                    />
                    Activate after import
                  </label>
                  <button className="action-btn ghost-btn" onClick={importOperation}>Import Event JSON</button>
                </div>
                <div className="import-text-row">
                  <textarea
                    value={importJsonText}
                    onChange={(e) => setImportJsonText(e.target.value)}
                    placeholder="Paste exported event JSON here..."
                    rows={6}
                  />
                </div>
              </div>

              <div className="admin-section">
                <h3>Layout</h3>
                <div className="row admin-grid-4">
                  <input value={laneNameLeft} onChange={(e) => setLaneNameLeft(e.target.value)} placeholder="Left lane name" />
                  <input value={laneNameCenter} onChange={(e) => setLaneNameCenter(e.target.value)} placeholder="Center lane name" />
                  <input value={laneNameRight} onChange={(e) => setLaneNameRight(e.target.value)} placeholder="Right lane name" />
                  <button className="action-btn" onClick={saveLaneNames}>Save Lane Names</button>
                </div>
              </div>

              <div className="admin-section admin-section--full">
                <h3>Squads & Roles</h3>
                <div className="row admin-grid-3">
                  <input value={newSquadName} onChange={(e) => setNewSquadName(e.target.value)} placeholder="Squad name" />
                  <input value={newSquadNotes} onChange={(e) => setNewSquadNotes(e.target.value)} placeholder="Squad notes (e.g. Radio CH 1)" />
                  <button className="action-btn" onClick={addSquad}>Add Squad</button>
                </div>
                <div className="row admin-grid-4">
                  <select value={newSlotSquadId} onChange={(e) => setNewSlotSquadId(e.target.value ? Number(e.target.value) : "")}>
                    <option value="">Select squad</option>
                    {orbat?.squads.map((sq) => (
                      <option key={sq.id} value={sq.id}>
                        {sq.name}
                      </option>
                    ))}
                  </select>
                  <input value={newSlotRole} onChange={(e) => setNewSlotRole(e.target.value)} placeholder="Role name" />
                  <select value={newSlotTeam} onChange={(e) => setNewSlotTeam(e.target.value as (typeof TEAM_OPTIONS)[number])}>
                    {TEAM_OPTIONS.map((team) => (
                      <option key={team || "none"} value={team}>{teamLabel(team)}</option>
                    ))}
                  </select>
                  <button className="action-btn" onClick={addSlot}>Add Role</button>
                </div>
              </div>
            </>
          ) : null}
        </div>
      </section>
      ) : null}

      <section className="panel">
        <h2>Live ORBAT</h2>
        {operation ? (
          <>
            <p className="op-title">Operation: <strong>{operation.name}</strong></p>
            <div className="lane-grid">
              {SQUAD_LANES.map((lane) => (
                <div key={lane} className="lane-column">
                  <div className="lane-header">{laneLabel(lane)}</div>
                  {adminOverlayEnabled && permissions?.is_admin && (
                    <div
                      className={`drop-slot ${draggedSquadId != null ? "active" : ""}`}
                      onDragOver={(e) => e.preventDefault()}
                      onDrop={(e) => {
                        e.preventDefault();
                        void dropSquadAt(lane, 0);
                      }}
                    >
                      Drop squad here
                    </div>
                  )}
                  {squadsByLane[lane].map((squad, idx) => (
                    <div key={squad.id}>
                      {adminOverlayEnabled && permissions?.is_admin && (
                        <div
                          className={`drop-slot ${draggedSquadId != null ? "active" : ""}`}
                          onDragOver={(e) => e.preventDefault()}
                          onDrop={(e) => {
                            e.preventDefault();
                            void dropSquadAt(lane, idx);
                          }}
                        >
                          Insert here
                        </div>
                      )}
                      <div
                        className="squad"
                        draggable={Boolean(adminOverlayEnabled && permissions?.is_admin)}
                        onDragStart={() => onSquadDragStart(squad.id)}
                        onDragEnd={onSquadDragEnd}
                      >
                        <div className="squad-head">
                          {editingSquadId === squad.id ? (
                            <div className="row compact-row">
                              <input
                                value={editingSquadName}
                                onChange={(e) => setEditingSquadName(e.target.value)}
                                placeholder="Squad name"
                              />
                              <textarea
                                value={editingSquadNotes}
                                onChange={(e) => setEditingSquadNotes(e.target.value)}
                                placeholder="Squad notes (e.g. Radio CH 1)"
                                rows={2}
                              />
                              <button onClick={saveEditSquad}>Save</button>
                              <button className="ghost-btn" onClick={cancelEditSquad}>Cancel</button>
                            </div>
                          ) : (
                            <h3>{squad.name}</h3>
                          )}
                          {adminOverlayEnabled && permissions?.is_admin && (
                            <div className="squad-actions">
                              <button className="ghost-btn" onClick={() => moveSquad(squad, "up")}>Up</button>
                              <button className="ghost-btn" onClick={() => moveSquad(squad, "down")}>Down</button>
                              <button className="ghost-btn" onClick={() => beginEditSquad(squad)}>Rename</button>
                              <button className="ghost-btn" onClick={() => copySquad(squad)}>Copy Squad</button>
                              <button className="danger-btn" onClick={() => deleteSquad(squad.id)}>Delete Squad</button>
                            </div>
                          )}
                        </div>
                        {editingSquadId !== squad.id && squad.notes ? (
                          <p className="squad-note">{squad.notes}</p>
                        ) : null}
                          {teamBuckets(squad.slots).map((teamGroup) => (
                            <div key={teamGroup.team} className="team-group">
                              {teamGroup.team.trim() ? (
                                <div className="team-group-title">{teamGroupTitle(teamGroup.team)}</div>
                              ) : null}
                              <ul>
                              {teamGroup.slots.map((slot) => (
                                <li key={slot.id}>
                                  <span>
                                    {editingSlotId === slot.id ? (
                                      <span className="row compact-row">
                                        <input
                                          value={editingSlotName}
                                          onChange={(e) => setEditingSlotName(e.target.value)}
                                          placeholder="Role name"
                                        />
                                        <select
                                          value={editingSlotTeam}
                                          onChange={(e) => setEditingSlotTeam(e.target.value as (typeof TEAM_OPTIONS)[number])}
                                        >
                                          {TEAM_OPTIONS.map((team) => (
                                            <option key={team || "none"} value={team}>{teamLabel(team)}</option>
                                          ))}
                                        </select>
                                        <button onClick={saveEditSlot}>Save</button>
                                        <button className="ghost-btn" onClick={cancelEditSlot}>Cancel</button>
                                      </span>
                                    ) : (
                                      <>
                                        {slot.role_name}{" "}
                                        {slot.assigned_to_member_name
                                          ? `- ${slot.assigned_to_member_name}`
                                          : (slot.pending_request_count || 0) > 0
                                            ? "(pending)"
                                            : "(open)"}
                                      </>
                                    )}
                                  </span>
                                  <div className="slot-actions">
                                    {slot.assigned_to_member_id && session && slot.assigned_to_member_id === session.user_id ? (
                                      <button className="ghost-btn" onClick={() => releaseSlot(slot.id)}>
                                        Leave Slot
                                      </button>
                                    ) : null}
                                    {slot.assigned_to_member_id &&
                                    adminOverlayEnabled &&
                                    permissions?.is_admin &&
                                    (!session || slot.assigned_to_member_id !== session.user_id) ? (
                                      <button className="ghost-btn" onClick={() => releaseSlot(slot.id)}>
                                        Free Slot
                                      </button>
                                    ) : null}
                                    {!slot.assigned_to_member_name && (
                                      <button onClick={() => requestSlot(slot.id)} disabled={!session}>
                                        {session ? "Request" : "Login required"}
                                      </button>
                                    )}
                                    {adminOverlayEnabled && permissions?.is_admin && (
                                      <div className="reorder-controls">
                                        <button
                                          className="ghost-btn arrow-btn"
                                          onClick={() => moveSlot(squad, slot, "up")}
                                          title="Move role up"
                                          aria-label="Move role up"
                                        >
                                          ↑
                                        </button>
                                        <button
                                          className="ghost-btn arrow-btn"
                                          onClick={() => moveSlot(squad, slot, "down")}
                                          title="Move role down"
                                          aria-label="Move role down"
                                        >
                                          ↓
                                        </button>
                                        <button className="ghost-btn" onClick={() => beginEditSlot(slot)}>Rename</button>
                                        <button className="danger-btn" onClick={() => deleteSlot(slot.id)}>
                                          Delete Role
                                        </button>
                                      </div>
                                    )}
                                  </div>
                                </li>
                              ))}
                            </ul>
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                  {adminOverlayEnabled && permissions?.is_admin && (
                    <div
                      className={`drop-slot ${draggedSquadId != null ? "active" : ""}`}
                      onDragOver={(e) => e.preventDefault()}
                      onDrop={(e) => {
                        e.preventDefault();
                        void dropSquadAt(lane, squadsByLane[lane].length);
                      }}
                    >
                      Drop to end
                    </div>
                  )}
                </div>
              ))}
            </div>
          </>
        ) : (
          <p className="access-note">No active operation in this server yet.</p>
        )}
      </section>

      {showAdminModal && permissions?.is_admin && (
        <div className="modal-overlay" onClick={() => setShowAdminModal(false)}>
          <section className="modal-panel" onClick={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <h2>Portal Admin Access</h2>
              <button className="ghost-btn" onClick={() => setShowAdminModal(false)}>Close</button>
            </div>
            <div className="row">
              <input value={newAdminUserId} onChange={(e) => setNewAdminUserId(e.target.value)} placeholder="Discord User ID" />
              <input value={newAdminUsername} onChange={(e) => setNewAdminUsername(e.target.value)} placeholder="Display name (optional)" />
              <button onClick={addAdmin}>Add Admin</button>
            </div>
            <div className="admin-list">
              {admins.length === 0 && <p>No portal admins configured yet.</p>}
              {admins.map((admin) => (
                <div key={`${admin.guild_id}-${admin.user_id}`} className="admin-item">
                  <span>{admin.username || "Unknown"} ({admin.user_id})</span>
                  <button className="danger-btn" onClick={() => removeAdmin(admin.user_id)}>Remove</button>
                </div>
              ))}
            </div>
          </section>
        </div>
      )}

      {error && <p className="error">{error}</p>}
    </div>
  );
}

export default App;
