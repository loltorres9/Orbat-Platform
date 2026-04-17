import { FormEvent, useEffect, useState } from "react";
import { api, discordLoginUrl, openOperationSocket } from "./api";
import type { Operation, OrbatStructure, Session } from "./types";

function App() {
  const [guildId, setGuildId] = useState("");
  const [operation, setOperation] = useState<Operation | null>(null);
  const [orbat, setOrbat] = useState<OrbatStructure | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [status, setStatus] = useState("Disconnected");
  const [error, setError] = useState<string | null>(null);

  const [newOperationName, setNewOperationName] = useState("");
  const [newSquadName, setNewSquadName] = useState("");
  const [newSlotSquadId, setNewSlotSquadId] = useState<number | "">("");
  const [newSlotRole, setNewSlotRole] = useState("");

  async function loadActiveOperation(targetGuildId: string) {
    setError(null);
    const op = await api.activeOperation(targetGuildId);
    setOperation(op);
    const structure = await api.orbat(op.id);
    setOrbat(structure);
  }

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
    try {
      await loadActiveOperation(guildId);
      setStatus("Loaded");
    } catch (err) {
      setError(String(err));
      setStatus("Failed");
      setOperation(null);
      setOrbat(null);
    }
  }

  async function requestSlot(slotId: number) {
    if (!guildId || !session) return;
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
      setStatus("Disconnected");
    }
  }

  async function createOperation() {
    if (!guildId || !newOperationName.trim()) return;
    try {
      const op = await api.createOperation({
        guild_id: guildId,
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

  return (
    <div className="page">
      <header>
        <h1>ORBAT Platform</h1>
        <p>{status}</p>
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
          <button type="submit">Load Active Operation</button>
        </form>
      </section>

      <section className="panel">
        <h2>Admin Builder</h2>
        <div className="row">
          <input
            value={newOperationName}
            onChange={(e) => setNewOperationName(e.target.value)}
            placeholder="New operation name"
          />
          <button onClick={createOperation}>Create Operation</button>
        </div>

        {operation && (
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

      <section className="panel">
        <h2>Live ORBAT</h2>
        {operation && <p>Operation: <strong>{operation.name}</strong></p>}
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
