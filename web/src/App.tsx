import { FormEvent, useEffect, useState } from "react";
import { api, discordLoginUrl, openOperationSocket } from "./api";
import type { GuildPermissions, Operation, OrbatStructure, Session, WebAdminEntry } from "./types";

function App() {
  const [guildId, setGuildId] = useState("");
  const [operation, setOperation] = useState<Operation | null>(null);
  const [orbat, setOrbat] = useState<OrbatStructure | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [permissions, setPermissions] = useState<GuildPermissions | null>(null);
  const [admins, setAdmins] = useState<WebAdminEntry[]>([]);
  const [status, setStatus] = useState("Disconnected");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [showAdminPanel, setShowAdminPanel] = useState(false);

  const [newOperationName, setNewOperationName] = useState("");
  const [newSquadName, setNewSquadName] = useState("");
  const [newSlotSquadId, setNewSlotSquadId] = useState<number | "">("");
  const [newSlotRole, setNewSlotRole] = useState("");
  const [newAdminUserId, setNewAdminUserId] = useState("");
  const [newAdminUsername, setNewAdminUsername] = useState("");

  async function loadActiveOperation(targetGuildId: string) {
    const op = await api.activeOperation(targetGuildId);
    setOperation(op);
    const structure = await api.orbat(op.id);
    setOrbat(structure);
  }

  async function refreshGuildAccess(targetGuildId: string) {
    if (!session) {
      setPermissions(null);
      setAdmins([]);
      return;
    }
    const perms = await api.guildPermissions(targetGuildId);
    setPermissions(perms);
    if (perms.is_admin) {
      setAdmins(await api.listGuildAdmins(targetGuildId));
    } else {
      setAdmins([]);
    }
  }

  useEffect(() => {
    const url = new URL(window.location.href);
    const authError = url.searchParams.get("auth_error");
    if (authError) {
      setError(`Login failed: ${authError}`);
      url.searchParams.delete("auth_error");
      window.history.replaceState({}, "", url.toString());
    }
  }, []);

  useEffect(() => {
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
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!operation) return;
    const ws = openOperationSocket(operation.id, async () => {
      try {
        const structure = await api.orbat(operation.id);
        setOrbat(structure);
      } catch {
        // no-op
      }
    });
    ws.onopen = () => setStatus("Realtime connected");
    ws.onclose = () => setStatus("Realtime disconnected");
    return () => ws.close();
  }, [operation?.id]);

  async function onConnect(e: FormEvent) {
    e.preventDefault();
    if (!guildId.trim()) {
      setError("Please enter a Discord Guild ID.");
      return;
    }
    setError(null);
    setLoading(true);
    try {
      await loadActiveOperation(guildId.trim());
      await refreshGuildAccess(guildId.trim());
      setStatus("Loaded");
    } catch (err) {
      setError(String(err));
      setStatus("Failed");
      setOperation(null);
      setOrbat(null);
      setPermissions(null);
      setAdmins([]);
    } finally {
      setLoading(false);
    }
  }

  async function requestSlot(slotId: number) {
    if (!guildId || !session) {
      setError("Please log in and select a guild first.");
      return;
    }
    try {
      await api.requestSlot(slotId, guildId);
      if (operation) setOrbat(await api.orbat(operation.id));
    } catch (err) {
      setError(String(err));
    }
  }

  async function logout() {
    try {
      await api.logout();
    } finally {
      setSession(null);
      setPermissions(null);
      setAdmins([]);
      setStatus("Disconnected");
    }
  }

  async function createOperation() {
    if (!guildId.trim()) {
      setError("Please enter and load a guild first.");
      return;
    }
    if (!newOperationName.trim()) {
      setError("Please enter an operation name.");
      return;
    }
    if (!session) {
      setError("Please log in with Discord first.");
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
        activate: true
      });
      setOperation(op);
      setOrbat(await api.orbat(op.id));
      setNewOperationName("");
    } catch (err) {
      setError(String(err));
    }
  }

  async function addSquad() {
    if (!operation || !newSquadName.trim()) return;
    try {
      await api.addSquad(operation.id, { name: newSquadName.trim() });
      setOrbat(await api.orbat(operation.id));
      setNewSquadName("");
    } catch (err) {
      setError(String(err));
    }
  }

  async function addSlot() {
    if (!operation || !newSlotRole.trim() || newSlotSquadId === "") return;
    try {
      await api.addSlot(operation.id, {
        squad_id: Number(newSlotSquadId),
        role_name: newSlotRole.trim()
      });
      setOrbat(await api.orbat(operation.id));
      setNewSlotRole("");
    } catch (err) {
      setError(String(err));
    }
  }

  async function addAdmin() {
    if (!guildId.trim()) {
      setError("Load a guild first.");
      return;
    }
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
    if (!guildId.trim()) return;
    try {
      await api.removeGuildAdmin(guildId.trim(), userId);
      setAdmins(await api.listGuildAdmins(guildId.trim()));
    } catch (err) {
      setError(String(err));
    }
  }

  return (
    <div className="page">
      <header className="hero">
        <div className="title-wrap">
          <h1>ORBAT Platform</h1>
          <p className="subtitle">{status}</p>
        </div>
        <div className="row">
          {session ? (
            <button onClick={logout}>Logout ({session.username})</button>
          ) : (
            <a className="button-link" href={discordLoginUrl(guildId || undefined)}>
              Login with Discord
            </a>
          )}
        </div>
      </header>

      <section className="panel">
        <h2>Connect Guild</h2>
        <form onSubmit={onConnect} className="row">
          <input value={guildId} onChange={(e) => setGuildId(e.target.value)} placeholder="Discord Guild ID" />
          <button type="submit" disabled={loading}>{loading ? "Loading..." : "Load Active Operation"}</button>
        </form>
        {permissions && (
          <p className="access-note">
            Access: {permissions.is_admin ? "Admin" : "Viewer"} ({permissions.is_discord_admin ? "Discord" : "Portal"} auth)
          </p>
        )}
      </section>

      <section className="panel">
        <h2>Admin Builder</h2>
        {!permissions?.is_admin && <p className="access-note">Admin access required. Manage admins below.</p>}
        <div className="row">
          <input
            value={newOperationName}
            onChange={(e) => setNewOperationName(e.target.value)}
            placeholder="New operation name"
          />
          <button onClick={createOperation} disabled={!permissions?.is_admin}>Create Operation</button>
          <button
            type="button"
            className="ghost-btn"
            onClick={() => setShowAdminPanel((v) => !v)}
            disabled={!guildId.trim()}
          >
            {showAdminPanel ? "Hide Admins" : "Manage Admins"}
          </button>
        </div>

        {operation && permissions?.is_admin && (
          <>
            <div className="row">
              <input value={newSquadName} onChange={(e) => setNewSquadName(e.target.value)} placeholder="Squad name" />
              <button onClick={addSquad}>Add Squad</button>
            </div>
            <div className="row">
              <select value={newSlotSquadId} onChange={(e) => setNewSlotSquadId(e.target.value ? Number(e.target.value) : "")}>
                <option value="">Select squad</option>
                {orbat?.squads.map((sq) => (
                  <option key={sq.id} value={sq.id}>
                    {sq.name}
                  </option>
                ))}
              </select>
              <input value={newSlotRole} onChange={(e) => setNewSlotRole(e.target.value)} placeholder="Role name" />
              <button onClick={addSlot}>Add Slot</button>
            </div>
          </>
        )}
      </section>

      {showAdminPanel && (
        <section className="panel">
          <h2>Portal Admin Access</h2>
          {!permissions?.is_admin && (
            <p className="access-note">You need admin rights in this guild to modify portal admins.</p>
          )}
          <div className="row">
            <input value={newAdminUserId} onChange={(e) => setNewAdminUserId(e.target.value)} placeholder="Discord User ID" />
            <input value={newAdminUsername} onChange={(e) => setNewAdminUsername(e.target.value)} placeholder="Display name (optional)" />
            <button onClick={addAdmin} disabled={!permissions?.is_admin}>Add Admin</button>
          </div>
          <div className="admin-list">
            {admins.length === 0 && <p>No portal admins configured yet.</p>}
            {admins.map((admin) => (
              <div key={`${admin.guild_id}-${admin.user_id}`} className="admin-item">
                <span>
                  {admin.username || "Unknown"} ({admin.user_id})
                </span>
                <button onClick={() => removeAdmin(admin.user_id)} disabled={!permissions?.is_admin}>Remove</button>
              </div>
            ))}
          </div>
        </section>
      )}

      <section className="panel">
        <h2>Live ORBAT</h2>
        {operation && <p className="op-title">Operation: <strong>{operation.name}</strong></p>}
        {orbat?.squads.map((squad) => (
          <div key={squad.id} className="squad">
            <h3>{squad.name}</h3>
            <ul>
              {squad.slots.map((slot) => (
                <li key={slot.id}>
                  <span>
                    {slot.role_name} {slot.assigned_to_member_name ? `- ${slot.assigned_to_member_name}` : "(open)"}
                  </span>
                  {!slot.assigned_to_member_name && (
                    <button onClick={() => requestSlot(slot.id)} disabled={!session}>
                      {session ? "Request" : "Login required"}
                    </button>
                  )}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </section>

      {error && <p className="error">{error}</p>}
    </div>
  );
}

export default App;
