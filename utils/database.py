import asyncpg
import os
import json
from typing import Optional

DATABASE_URL = os.getenv('DATABASE_URL')

_pool = None


async def get_pool():
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DATABASE_URL)
    return _pool


async def init_db():
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS operations (
                id SERIAL PRIMARY KEY,
                guild_id TEXT NOT NULL,
                name TEXT NOT NULL,
                sheet_url TEXT,
                sheet_id TEXT,
                squad_col INTEGER,
                role_col INTEGER,
                status_col INTEGER,
                assigned_col INTEGER,
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS requests (
                id SERIAL PRIMARY KEY,
                guild_id TEXT NOT NULL,
                operation_id INTEGER NOT NULL,
                slot_id INTEGER,
                member_id TEXT NOT NULL,
                member_name TEXT NOT NULL,
                slot_label TEXT NOT NULL,
                sheet_row INTEGER NOT NULL,
                sheet_col INTEGER,
                status TEXT DEFAULT 'pending',
                approval_message_id TEXT,
                approval_channel_id TEXT,
                approved_by TEXT,
                denial_reason TEXT,
                unit_role TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS squads (
                id SERIAL PRIMARY KEY,
                operation_id INTEGER NOT NULL REFERENCES operations(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                display_order INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(operation_id, name)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS slots (
                id SERIAL PRIMARY KEY,
                operation_id INTEGER NOT NULL REFERENCES operations(id) ON DELETE CASCADE,
                squad_id INTEGER NOT NULL REFERENCES squads(id) ON DELETE CASCADE,
                role_name TEXT NOT NULL,
                display_order INTEGER NOT NULL DEFAULT 0,
                assigned_to_member_id TEXT,
                assigned_to_member_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS web_sessions (
                id SERIAL PRIMARY KEY,
                session_token TEXT UNIQUE NOT NULL,
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                username TEXT NOT NULL,
                avatar_url TEXT,
                access_token TEXT,
                refresh_token TEXT,
                expires_at TIMESTAMP NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS orbat_messages (
                guild_id TEXT PRIMARY KEY,
                channel_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS open_slots_messages (
                guild_id TEXT PRIMARY KEY,
                channel_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id TEXT PRIMARY KEY,
                timezone TEXT NOT NULL DEFAULT 'UTC'
            )
        ''')
        # Add event scheduling columns to existing operations tables
        await db.execute('''
            ALTER TABLE operations ADD COLUMN IF NOT EXISTS
                event_time TIMESTAMP
        ''')
        await db.execute('''
            ALTER TABLE operations ADD COLUMN IF NOT EXISTS
                reminder_minutes INTEGER DEFAULT 30
        ''')
        await db.execute('''
            ALTER TABLE operations ADD COLUMN IF NOT EXISTS
                reminder_fired INTEGER DEFAULT 0
        ''')
        await db.execute('''
            ALTER TABLE operations ALTER COLUMN sheet_url DROP NOT NULL
        ''')
        await db.execute('''
            ALTER TABLE operations ALTER COLUMN sheet_id DROP NOT NULL
        ''')
        await db.execute('''
            ALTER TABLE requests ADD COLUMN IF NOT EXISTS slot_id INTEGER
        ''')
        await db.execute('''
            ALTER TABLE requests ALTER COLUMN sheet_row DROP NOT NULL
        ''')
        await db.execute('''
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conname = 'requests_slot_id_fkey'
                ) THEN
                    ALTER TABLE requests
                    ADD CONSTRAINT requests_slot_id_fkey
                    FOREIGN KEY (slot_id) REFERENCES slots(id) ON DELETE SET NULL;
                END IF;
            END
            $$;
        ''')
        await db.execute('''
            CREATE INDEX IF NOT EXISTS idx_operations_guild_active
            ON operations(guild_id, is_active, created_at DESC)
        ''')
        await db.execute('''
            CREATE INDEX IF NOT EXISTS idx_squads_operation_order
            ON squads(operation_id, display_order, id)
        ''')
        await db.execute('''
            CREATE INDEX IF NOT EXISTS idx_slots_operation_order
            ON slots(operation_id, display_order, id)
        ''')
        await db.execute('''
            CREATE INDEX IF NOT EXISTS idx_slots_squad_order
            ON slots(squad_id, display_order, id)
        ''')
        await db.execute('''
            CREATE INDEX IF NOT EXISTS idx_requests_operation_status
            ON requests(operation_id, status, created_at)
        ''')
        await db.execute('''
            CREATE INDEX IF NOT EXISTS idx_web_sessions_token
            ON web_sessions(session_token)
        ''')


async def get_active_operation(guild_id: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow(
            'SELECT * FROM operations WHERE guild_id = $1 AND is_active = 1 ORDER BY created_at DESC LIMIT 1',
            guild_id,
        )


async def get_operation_by_id(operation_id: int):
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow(
            "SELECT * FROM operations WHERE id = $1",
            operation_id,
        )


async def create_operation(guild_id: str, name: str, sheet_url: str, sheet_id: str,
                           squad_col: int, role_col: int, status_col: int, assigned_col: int) -> int:
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            'UPDATE operations SET is_active = 0 WHERE guild_id = $1',
            guild_id,
        )
        row = await db.fetchrow(
            '''INSERT INTO operations
               (guild_id, name, sheet_url, sheet_id, squad_col, role_col, status_col, assigned_col)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
               RETURNING id''',
            guild_id, name, sheet_url, sheet_id, squad_col, role_col, status_col, assigned_col,
        )
        return row['id']


async def get_pending_slots(operation_id: int) -> list:
    pool = await get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch(
            "SELECT sheet_row, sheet_col FROM requests WHERE operation_id = $1 AND status = 'pending'",
            operation_id,
        )
        return [(row['sheet_row'], row['sheet_col']) for row in rows]


async def get_approved_slots(operation_id: int) -> list:
    pool = await get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch(
            "SELECT sheet_row, sheet_col FROM requests WHERE operation_id = $1 AND status = 'approved'",
            operation_id,
        )
        return [(row['sheet_row'], row['sheet_col']) for row in rows]


async def get_slot_by_id(slot_id: int):
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow(
            """SELECT s.*, sq.name AS squad_name
               FROM slots s
               JOIN squads sq ON sq.id = s.squad_id
               WHERE s.id = $1""",
            slot_id,
        )


async def get_member_active_request(guild_id: str, operation_id: int, member_id: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow(
            """SELECT * FROM requests
               WHERE guild_id = $1 AND operation_id = $2 AND member_id = $3
               AND status IN ('pending', 'approved')""",
            guild_id, operation_id, member_id,
        )


async def create_request(guild_id: str, operation_id: int, member_id: str,
                         member_name: str, slot_label: str, sheet_row: int,
                         sheet_col: int = None, unit_role: str = None,
                         slot_id: Optional[int] = None) -> int:
    pool = await get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow(
            '''INSERT INTO requests
               (guild_id, operation_id, slot_id, member_id, member_name, slot_label, sheet_row, sheet_col, unit_role)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
               RETURNING id''',
            guild_id, operation_id, slot_id, member_id, member_name, slot_label, sheet_row, sheet_col, unit_role,
        )
        return row['id']


async def update_request_sheet_col(request_id: int, sheet_row: int, sheet_col: int):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            'UPDATE requests SET sheet_row = $1, sheet_col = $2 WHERE id = $3',
            sheet_row, sheet_col, request_id,
        )


async def update_request_slot_id(request_id: int, slot_id: int):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            "UPDATE requests SET slot_id = $1 WHERE id = $2",
            slot_id,
            request_id,
        )


async def update_request_message(request_id: int, message_id: str, channel_id: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            'UPDATE requests SET approval_message_id = $1, approval_channel_id = $2 WHERE id = $3',
            message_id, channel_id, request_id,
        )


async def get_request_by_id(request_id: int):
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow('SELECT * FROM requests WHERE id = $1', request_id)


async def get_all_pending_requests() -> list:
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch("SELECT * FROM requests WHERE status = 'pending'")


async def cancel_member_request(guild_id: str, operation_id: int, member_id: str) -> bool:
    pool = await get_pool()
    async with pool.acquire() as db:
        result = await db.execute(
            """UPDATE requests SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
               WHERE guild_id = $1 AND operation_id = $2 AND member_id = $3 AND status = 'pending'""",
            guild_id, operation_id, member_id,
        )
        return int(result.split()[-1]) > 0


async def clear_pending_requests(operation_id: int) -> int:
    pool = await get_pool()
    async with pool.acquire() as db:
        result = await db.execute(
            """UPDATE requests SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
               WHERE operation_id = $1 AND status = 'pending'""",
            operation_id,
        )
        return int(result.split()[-1])


async def get_approved_requests(operation_id: int) -> list:
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            "SELECT * FROM requests WHERE operation_id = $1 AND status = 'approved'",
            operation_id,
        )


async def get_active_requests(operation_id: int) -> list:
    """Return all pending and approved requests for an operation."""
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            "SELECT * FROM requests WHERE operation_id = $1 AND status IN ('pending', 'approved') ORDER BY status DESC, created_at",
            operation_id,
        )


async def cancel_request_by_id(request_id: int) -> bool:
    pool = await get_pool()
    async with pool.acquire() as db:
        result = await db.execute(
            """UPDATE requests SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
               WHERE id = $1 AND status = 'approved'""",
            request_id,
        )
        return int(result.split()[-1]) > 0


async def approve_request(request_id: int, approved_by: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            """UPDATE requests
               SET status = 'approved', approved_by = $1, updated_at = CURRENT_TIMESTAMP
               WHERE id = $2""",
            approved_by, request_id,
        )


async def deny_request(request_id: int, denied_by: str, reason: str = None):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            """UPDATE requests
               SET status = 'denied', approved_by = $1, denial_reason = $2, updated_at = CURRENT_TIMESTAMP
               WHERE id = $3""",
            denied_by, reason, request_id,
        )


async def save_orbat_message(guild_id: str, channel_id: str, message_id: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            '''INSERT INTO orbat_messages (guild_id, channel_id, message_id)
               VALUES ($1, $2, $3)
               ON CONFLICT (guild_id) DO UPDATE SET
                   channel_id = EXCLUDED.channel_id,
                   message_id = EXCLUDED.message_id,
                   updated_at = CURRENT_TIMESTAMP''',
            guild_id, channel_id, message_id,
        )


async def get_orbat_message(guild_id: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow(
            'SELECT channel_id, message_id FROM orbat_messages WHERE guild_id = $1',
            guild_id,
        )


async def save_open_slots_message(guild_id: str, channel_id: str, message_id: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            '''INSERT INTO open_slots_messages (guild_id, channel_id, message_id)
               VALUES ($1, $2, $3)
               ON CONFLICT (guild_id) DO UPDATE SET
                   channel_id = EXCLUDED.channel_id,
                   message_id = EXCLUDED.message_id,
                   updated_at = CURRENT_TIMESTAMP''',
            guild_id, channel_id, message_id,
        )


async def get_open_slots_message(guild_id: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow(
            'SELECT channel_id, message_id FROM open_slots_messages WHERE guild_id = $1',
            guild_id,
        )


async def get_guild_timezone(guild_id: str) -> str:
    pool = await get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow(
            'SELECT timezone FROM guild_settings WHERE guild_id = $1', guild_id
        )
        return row['timezone'] if row else 'UTC'


async def set_guild_timezone(guild_id: str, timezone: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            '''INSERT INTO guild_settings (guild_id, timezone)
               VALUES ($1, $2)
               ON CONFLICT (guild_id) DO UPDATE SET timezone = EXCLUDED.timezone''',
            guild_id, timezone,
        )


async def set_event_time(operation_id: int, event_time, reminder_minutes: int):
    # Store as naive UTC — the column is TIMESTAMP WITHOUT TIME ZONE
    if hasattr(event_time, 'tzinfo') and event_time.tzinfo is not None:
        event_time = event_time.replace(tzinfo=None)
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            '''UPDATE operations
               SET event_time = $1, reminder_minutes = $2, reminder_fired = 0
               WHERE id = $3''',
            event_time, reminder_minutes, operation_id,
        )


async def get_operations_needing_reminder():
    """Return active operations whose reminder window has arrived but not yet fired."""
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            '''SELECT * FROM operations
               WHERE is_active = 1
               AND event_time IS NOT NULL
               AND reminder_fired = 0
               AND event_time - (reminder_minutes * INTERVAL '1 minute') <= CURRENT_TIMESTAMP
               AND event_time > CURRENT_TIMESTAMP'''
        )


async def mark_reminder_fired(operation_id: int):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            'UPDATE operations SET reminder_fired = 1 WHERE id = $1',
            operation_id,
        )


async def get_competing_requests(operation_id: int, sheet_row: int, sheet_col: int, exclude_request_id: int) -> list:
    """Return all other pending requests for the same slot cell (row + col)."""
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            """SELECT * FROM requests
               WHERE operation_id = $1 AND sheet_row = $2 AND sheet_col = $3
               AND id != $4 AND status = 'pending'""",
            operation_id, sheet_row, sheet_col, exclude_request_id,
        )


async def get_approved_member_ids(operation_id: int) -> list:
    pool = await get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch(
            "SELECT member_id, slot_label FROM requests WHERE operation_id = $1 AND status = 'approved'",
            operation_id,
        )
        return [(row['member_id'], row['slot_label']) for row in rows]


async def _notify_slot_update(db, guild_id: str, operation_id: int, event: str, slot_id: Optional[int] = None):
    if not guild_id:
        guild_id = await db.fetchval(
            "SELECT guild_id FROM operations WHERE id = $1",
            operation_id,
        )
    payload = json.dumps(
        {
            "guild_id": guild_id,
            "operation_id": operation_id,
            "event": event,
            "slot_id": slot_id,
        }
    )
    await db.execute("SELECT pg_notify('slot_updates', $1)", payload)


async def emit_slot_update(guild_id: str, operation_id: int, event: str, slot_id: Optional[int] = None):
    pool = await get_pool()
    async with pool.acquire() as db:
        await _notify_slot_update(db, guild_id, operation_id, event, slot_id)


async def create_operation_v2(
    guild_id: str,
    name: str,
    event_time=None,
    reminder_minutes: int = 30,
    activate: bool = False,
) -> int:
    pool = await get_pool()
    async with pool.acquire() as db:
        async with db.transaction():
            if activate:
                await db.execute("UPDATE operations SET is_active = 0 WHERE guild_id = $1", guild_id)
            row = await db.fetchrow(
                """INSERT INTO operations (guild_id, name, event_time, reminder_minutes, is_active)
                   VALUES ($1, $2, $3, $4, $5)
                   RETURNING id""",
                guild_id,
                name,
                event_time,
                reminder_minutes,
                1 if activate else 0,
            )
            return row["id"]


async def list_operations(guild_id: str) -> list:
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            "SELECT * FROM operations WHERE guild_id = $1 ORDER BY is_active DESC, created_at DESC",
            guild_id,
        )


async def activate_operation(guild_id: str, operation_id: int) -> bool:
    pool = await get_pool()
    async with pool.acquire() as db:
        async with db.transaction():
            await db.execute("UPDATE operations SET is_active = 0 WHERE guild_id = $1", guild_id)
            result = await db.execute(
                "UPDATE operations SET is_active = 1 WHERE guild_id = $1 AND id = $2",
                guild_id,
                operation_id,
            )
            return int(result.split()[-1]) > 0


async def create_squad(operation_id: int, name: str, display_order: Optional[int] = None) -> int:
    pool = await get_pool()
    async with pool.acquire() as db:
        if display_order is None:
            display_order = await db.fetchval(
                "SELECT COALESCE(MAX(display_order), -1) + 1 FROM squads WHERE operation_id = $1",
                operation_id,
            )
        row = await db.fetchrow(
            """INSERT INTO squads (operation_id, name, display_order)
               VALUES ($1, $2, $3)
               RETURNING id""",
            operation_id,
            name,
            display_order,
        )
        await _notify_slot_update(db, "", operation_id, "squad_created")
        return row["id"]


async def list_squads(operation_id: int) -> list:
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            "SELECT * FROM squads WHERE operation_id = $1 ORDER BY display_order, id",
            operation_id,
        )


async def update_squad(squad_id: int, name: Optional[str] = None, display_order: Optional[int] = None) -> bool:
    if name is None and display_order is None:
        return False
    pool = await get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow("SELECT operation_id FROM squads WHERE id = $1", squad_id)
        if not row:
            return False
        await db.execute(
            """UPDATE squads
               SET name = COALESCE($1, name),
                   display_order = COALESCE($2, display_order)
               WHERE id = $3""",
            name,
            display_order,
            squad_id,
        )
        await _notify_slot_update(db, "", row["operation_id"], "squad_updated")
        return True


async def delete_squad(squad_id: int) -> bool:
    pool = await get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow(
            """DELETE FROM squads
               WHERE id = $1
               RETURNING operation_id""",
            squad_id,
        )
        if not row:
            return False
        await _notify_slot_update(db, "", row["operation_id"], "squad_deleted")
        return True


async def create_slot(
    operation_id: int,
    squad_id: int,
    role_name: str,
    display_order: Optional[int] = None,
    assigned_to_member_id: Optional[str] = None,
    assigned_to_member_name: Optional[str] = None,
) -> int:
    pool = await get_pool()
    async with pool.acquire() as db:
        if display_order is None:
            display_order = await db.fetchval(
                "SELECT COALESCE(MAX(display_order), -1) + 1 FROM slots WHERE squad_id = $1",
                squad_id,
            )
        row = await db.fetchrow(
            """INSERT INTO slots
               (operation_id, squad_id, role_name, display_order, assigned_to_member_id, assigned_to_member_name)
               VALUES ($1, $2, $3, $4, $5, $6)
               RETURNING id""",
            operation_id,
            squad_id,
            role_name,
            display_order,
            assigned_to_member_id,
            assigned_to_member_name,
        )
        await _notify_slot_update(db, "", operation_id, "slot_created", row["id"])
        return row["id"]


async def list_slots(operation_id: int) -> list:
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            """SELECT s.*, sq.name AS squad_name, sq.display_order AS squad_display_order
               FROM slots s
               JOIN squads sq ON sq.id = s.squad_id
               WHERE s.operation_id = $1
               ORDER BY sq.display_order, s.display_order, s.id""",
            operation_id,
        )


async def update_slot(
    slot_id: int,
    role_name: Optional[str] = None,
    display_order: Optional[int] = None,
    squad_id: Optional[int] = None,
) -> bool:
    if role_name is None and display_order is None and squad_id is None:
        return False
    pool = await get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow("SELECT operation_id FROM slots WHERE id = $1", slot_id)
        if not row:
            return False
        await db.execute(
            """UPDATE slots
               SET role_name = COALESCE($1, role_name),
                   display_order = COALESCE($2, display_order),
                   squad_id = COALESCE($3, squad_id)
               WHERE id = $4""",
            role_name,
            display_order,
            squad_id,
            slot_id,
        )
        await _notify_slot_update(db, "", row["operation_id"], "slot_updated", slot_id)
        return True


async def assign_slot(slot_id: int, member_id: str, member_name: str) -> bool:
    pool = await get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow(
            """UPDATE slots
               SET assigned_to_member_id = $1, assigned_to_member_name = $2
               WHERE id = $3
               RETURNING operation_id""",
            member_id,
            member_name,
            slot_id,
        )
        if not row:
            return False
        await _notify_slot_update(db, "", row["operation_id"], "slot_assigned", slot_id)
        return True


async def clear_slot_assignment(slot_id: int) -> bool:
    pool = await get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow(
            """UPDATE slots
               SET assigned_to_member_id = NULL, assigned_to_member_name = NULL
               WHERE id = $1
               RETURNING operation_id""",
            slot_id,
        )
        if not row:
            return False
        await _notify_slot_update(db, "", row["operation_id"], "slot_cleared", slot_id)
        return True


async def delete_slot(slot_id: int) -> bool:
    pool = await get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow(
            """DELETE FROM slots
               WHERE id = $1
               RETURNING operation_id""",
            slot_id,
        )
        if not row:
            return False
        await _notify_slot_update(db, "", row["operation_id"], "slot_deleted", slot_id)
        return True


async def get_orbat_structure(operation_id: int) -> dict:
    pool = await get_pool()
    async with pool.acquire() as db:
        operation = await db.fetchrow("SELECT * FROM operations WHERE id = $1", operation_id)
        squads = await db.fetch(
            "SELECT * FROM squads WHERE operation_id = $1 ORDER BY display_order, id",
            operation_id,
        )
        slots = await db.fetch(
            "SELECT * FROM slots WHERE operation_id = $1 ORDER BY display_order, id",
            operation_id,
        )

    squad_map = {s["id"]: {"id": s["id"], "name": s["name"], "display_order": s["display_order"], "slots": []} for s in squads}
    for slot in slots:
        bucket = squad_map.get(slot["squad_id"])
        if bucket is not None:
            bucket["slots"].append(
                {
                    "id": slot["id"],
                    "role_name": slot["role_name"],
                    "display_order": slot["display_order"],
                    "assigned_to_member_id": slot["assigned_to_member_id"],
                    "assigned_to_member_name": slot["assigned_to_member_name"],
                }
            )

    return {
        "operation": dict(operation) if operation else None,
        "squads": sorted(squad_map.values(), key=lambda x: x["display_order"]),
    }


async def create_web_session(
    session_token: str,
    guild_id: str,
    user_id: str,
    username: str,
    expires_at,
    avatar_url: Optional[str] = None,
    access_token: Optional[str] = None,
    refresh_token: Optional[str] = None,
):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            """INSERT INTO web_sessions
               (session_token, guild_id, user_id, username, avatar_url, access_token, refresh_token, expires_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
               ON CONFLICT (session_token) DO UPDATE SET
                   guild_id = EXCLUDED.guild_id,
                   user_id = EXCLUDED.user_id,
                   username = EXCLUDED.username,
                   avatar_url = EXCLUDED.avatar_url,
                   access_token = EXCLUDED.access_token,
                   refresh_token = EXCLUDED.refresh_token,
                   expires_at = EXCLUDED.expires_at""",
            session_token,
            guild_id,
            user_id,
            username,
            avatar_url,
            access_token,
            refresh_token,
            expires_at,
        )


async def get_web_session(session_token: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow(
            """SELECT * FROM web_sessions
               WHERE session_token = $1 AND expires_at > CURRENT_TIMESTAMP""",
            session_token,
        )


async def delete_web_session(session_token: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute("DELETE FROM web_sessions WHERE session_token = $1", session_token)


async def prune_expired_web_sessions():
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute("DELETE FROM web_sessions WHERE expires_at <= CURRENT_TIMESTAMP")
