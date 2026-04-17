import json
import asyncpg
import os
import secrets
from datetime import datetime, timedelta

DATABASE_URL = os.getenv('DATABASE_URL')

_pool = None


async def _init_connection(conn):
    await conn.set_type_codec('jsonb', encoder=json.dumps, decoder=json.loads, schema='pg_catalog')


async def get_pool():
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DATABASE_URL, init=_init_connection)
    return _pool


async def init_db():
    pool = await get_pool()
    async with pool.acquire() as db:
        # ---- Existing tables ----
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
                member_id TEXT NOT NULL,
                member_name TEXT NOT NULL,
                slot_label TEXT NOT NULL,
                sheet_row INTEGER,
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

        # ---- Migration: add columns to existing tables ----
        await db.execute('ALTER TABLE operations ADD COLUMN IF NOT EXISTS event_time TIMESTAMP')
        await db.execute('ALTER TABLE operations ADD COLUMN IF NOT EXISTS reminder_minutes INTEGER DEFAULT 30')
        await db.execute('ALTER TABLE operations ADD COLUMN IF NOT EXISTS reminder_fired INTEGER DEFAULT 0')
        await db.execute('ALTER TABLE operations ADD COLUMN IF NOT EXISTS description TEXT')
        await db.execute('ALTER TABLE requests ADD COLUMN IF NOT EXISTS slot_id INTEGER')
        # Make sheet columns nullable for web-created operations
        await db.execute('ALTER TABLE operations ALTER COLUMN sheet_url DROP NOT NULL')
        await db.execute('ALTER TABLE operations ALTER COLUMN sheet_id DROP NOT NULL')
        await db.execute('ALTER TABLE requests ALTER COLUMN sheet_row DROP NOT NULL')

        # ---- New tables ----
        await db.execute('''
            CREATE TABLE IF NOT EXISTS squads (
                id SERIAL PRIMARY KEY,
                operation_id INTEGER NOT NULL REFERENCES operations(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                color TEXT DEFAULT '#4A90D9',
                display_order INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS slots (
                id SERIAL PRIMARY KEY,
                squad_id INTEGER NOT NULL REFERENCES squads(id) ON DELETE CASCADE,
                role_name TEXT NOT NULL,
                display_order INTEGER DEFAULT 0,
                assigned_to_member_id TEXT,
                assigned_to_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS web_sessions (
                id TEXT PRIMARY KEY,
                discord_user_id TEXT NOT NULL,
                discord_username TEXT NOT NULL,
                discord_avatar TEXT,
                access_token TEXT NOT NULL,
                refresh_token TEXT,
                guilds JSONB,
                expires_at TIMESTAMP NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')


# ===========================================================================
# Operations
# ===========================================================================

async def get_active_operation(guild_id: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow(
            'SELECT * FROM operations WHERE guild_id = $1 AND is_active = 1 ORDER BY created_at DESC LIMIT 1',
            guild_id,
        )


async def create_operation(guild_id: str, name: str, description: str = None) -> int:
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            'UPDATE operations SET is_active = 0 WHERE guild_id = $1',
            guild_id,
        )
        row = await db.fetchrow(
            '''INSERT INTO operations (guild_id, name, description)
               VALUES ($1, $2, $3)
               RETURNING id''',
            guild_id, name, description,
        )
        return row['id']


async def get_operation_by_id(operation_id: int):
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow('SELECT * FROM operations WHERE id = $1', operation_id)


async def get_operations_for_guild(guild_id: str) -> list:
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            'SELECT * FROM operations WHERE guild_id = $1 ORDER BY created_at DESC',
            guild_id,
        )


async def activate_operation(operation_id: int, guild_id: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute('UPDATE operations SET is_active = 0 WHERE guild_id = $1', guild_id)
        await db.execute('UPDATE operations SET is_active = 1 WHERE id = $1', operation_id)


async def update_operation(operation_id: int, name: str = None, description: str = None):
    pool = await get_pool()
    async with pool.acquire() as db:
        if name is not None:
            await db.execute('UPDATE operations SET name = $1 WHERE id = $2', name, operation_id)
        if description is not None:
            await db.execute('UPDATE operations SET description = $1 WHERE id = $2', description, operation_id)


async def delete_operation(operation_id: int):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute('DELETE FROM operations WHERE id = $1', operation_id)


# ===========================================================================
# Squads
# ===========================================================================

async def create_squad(operation_id: int, name: str, color: str = '#4A90D9',
                       display_order: int = 0) -> int:
    pool = await get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow(
            '''INSERT INTO squads (operation_id, name, color, display_order)
               VALUES ($1, $2, $3, $4)
               RETURNING id''',
            operation_id, name, color, display_order,
        )
        return row['id']


async def update_squad(squad_id: int, name: str = None, color: str = None,
                       display_order: int = None):
    pool = await get_pool()
    async with pool.acquire() as db:
        if name is not None:
            await db.execute('UPDATE squads SET name = $1 WHERE id = $2', name, squad_id)
        if color is not None:
            await db.execute('UPDATE squads SET color = $1 WHERE id = $2', color, squad_id)
        if display_order is not None:
            await db.execute('UPDATE squads SET display_order = $1 WHERE id = $2', display_order, squad_id)


async def delete_squad(squad_id: int):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute('DELETE FROM squads WHERE id = $1', squad_id)


async def get_squad(squad_id: int):
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow('SELECT * FROM squads WHERE id = $1', squad_id)


async def get_squads_for_operation(operation_id: int) -> list:
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            'SELECT * FROM squads WHERE operation_id = $1 ORDER BY display_order, id',
            operation_id,
        )


# ===========================================================================
# Slots
# ===========================================================================

async def create_slot(squad_id: int, role_name: str, display_order: int = 0) -> int:
    pool = await get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow(
            '''INSERT INTO slots (squad_id, role_name, display_order)
               VALUES ($1, $2, $3)
               RETURNING id''',
            squad_id, role_name, display_order,
        )
        return row['id']


async def update_slot(slot_id: int, role_name: str = None, display_order: int = None):
    pool = await get_pool()
    async with pool.acquire() as db:
        if role_name is not None:
            await db.execute('UPDATE slots SET role_name = $1 WHERE id = $2', role_name, slot_id)
        if display_order is not None:
            await db.execute('UPDATE slots SET display_order = $1 WHERE id = $2', display_order, slot_id)


async def delete_slot(slot_id: int):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute('DELETE FROM slots WHERE id = $1', slot_id)


async def get_slot(slot_id: int):
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow('SELECT * FROM slots WHERE id = $1', slot_id)


async def get_slots_for_squad(squad_id: int) -> list:
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            'SELECT * FROM slots WHERE squad_id = $1 ORDER BY display_order, id',
            squad_id,
        )


# ===========================================================================
# ORBAT queries (replace Google Sheets reads)
# ===========================================================================

async def get_orbat_slots(operation_id: int) -> list:
    """All slots with squad info, for ORBAT display and Discord embed."""
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch('''
            SELECT s.id, s.role_name, s.display_order, s.assigned_to_member_id,
                   s.assigned_to_name, s.squad_id,
                   sq.name AS squad_name, sq.display_order AS squad_display_order,
                   sq.color AS squad_color
            FROM slots s
            JOIN squads sq ON s.squad_id = sq.id
            WHERE sq.operation_id = $1
            ORDER BY sq.display_order, sq.id, s.display_order, s.id
        ''', operation_id)


async def get_available_slots(operation_id: int) -> list:
    """Unassigned slots for the request picker."""
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch('''
            SELECT s.id, s.role_name, s.display_order, s.squad_id,
                   sq.name AS squad_name, sq.display_order AS squad_display_order
            FROM slots s
            JOIN squads sq ON s.squad_id = sq.id
            WHERE sq.operation_id = $1
              AND s.assigned_to_member_id IS NULL
            ORDER BY sq.display_order, sq.id, s.display_order, s.id
        ''', operation_id)


async def get_slot_with_squad(slot_id: int):
    """Get a slot with its squad info."""
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow('''
            SELECT s.*, sq.name AS squad_name, sq.operation_id
            FROM slots s
            JOIN squads sq ON s.squad_id = sq.id
            WHERE s.id = $1
        ''', slot_id)


# ===========================================================================
# Slot assignment (replace Google Sheets writes)
# ===========================================================================

async def assign_slot_to_member(slot_id: int, member_id: str, member_name: str) -> bool:
    """Assign a member to a slot. Returns False if slot is already taken."""
    pool = await get_pool()
    async with pool.acquire() as db:
        result = await db.execute(
            '''UPDATE slots
               SET assigned_to_member_id = $1, assigned_to_name = $2,
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = $3 AND assigned_to_member_id IS NULL''',
            member_id, member_name, slot_id,
        )
        return int(result.split()[-1]) > 0


async def unassign_slot(slot_id: int):
    """Clear a slot assignment."""
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            '''UPDATE slots
               SET assigned_to_member_id = NULL, assigned_to_name = NULL,
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = $1''',
            slot_id,
        )


# ===========================================================================
# Requests
# ===========================================================================

async def get_pending_slot_ids(operation_id: int) -> list:
    """Return slot_ids that have pending requests."""
    pool = await get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch(
            "SELECT DISTINCT slot_id FROM requests WHERE operation_id = $1 AND status = 'pending' AND slot_id IS NOT NULL",
            operation_id,
        )
        return [row['slot_id'] for row in rows]


async def get_approved_slot_ids(operation_id: int) -> list:
    """Return slot_ids that have approved requests."""
    pool = await get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch(
            "SELECT DISTINCT slot_id FROM requests WHERE operation_id = $1 AND status = 'approved' AND slot_id IS NOT NULL",
            operation_id,
        )
        return [row['slot_id'] for row in rows]


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
                         member_name: str, slot_label: str, slot_id: int,
                         unit_role: str = None) -> int:
    pool = await get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow(
            '''INSERT INTO requests
               (guild_id, operation_id, member_id, member_name, slot_label, slot_id, unit_role)
               VALUES ($1, $2, $3, $4, $5, $6, $7)
               RETURNING id''',
            guild_id, operation_id, member_id, member_name, slot_label, slot_id, unit_role,
        )
        return row['id']


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


async def get_competing_requests(operation_id: int, slot_id: int, exclude_request_id: int) -> list:
    """Return all other pending requests for the same slot."""
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            """SELECT * FROM requests
               WHERE operation_id = $1 AND slot_id = $2
               AND id != $3 AND status = 'pending'""",
            operation_id, slot_id, exclude_request_id,
        )


# ===========================================================================
# Messages
# ===========================================================================

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


# ===========================================================================
# Guild settings
# ===========================================================================

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


# ===========================================================================
# Event scheduling & reminders
# ===========================================================================

async def set_event_time(operation_id: int, event_time, reminder_minutes: int):
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


async def get_approved_member_ids(operation_id: int) -> list:
    pool = await get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch(
            "SELECT member_id, slot_label FROM requests WHERE operation_id = $1 AND status = 'approved'",
            operation_id,
        )
        return [(row['member_id'], row['slot_label']) for row in rows]


# ===========================================================================
# Web sessions
# ===========================================================================

async def create_web_session(discord_user_id: str, discord_username: str,
                             discord_avatar: str, access_token: str,
                             refresh_token: str, guilds: list,
                             ttl_hours: int = 24) -> str:
    session_id = secrets.token_urlsafe(32)
    expires_at = datetime.utcnow() + timedelta(hours=ttl_hours)
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            '''INSERT INTO web_sessions
               (id, discord_user_id, discord_username, discord_avatar,
                access_token, refresh_token, guilds, expires_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8)''',
            session_id, discord_user_id, discord_username, discord_avatar,
            access_token, refresh_token, guilds, expires_at,
        )
    return session_id


async def get_web_session(session_id: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow(
            'SELECT * FROM web_sessions WHERE id = $1 AND expires_at > CURRENT_TIMESTAMP',
            session_id,
        )
        return row


async def delete_web_session(session_id: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute('DELETE FROM web_sessions WHERE id = $1', session_id)


async def cleanup_expired_sessions():
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute('DELETE FROM web_sessions WHERE expires_at <= CURRENT_TIMESTAMP')
