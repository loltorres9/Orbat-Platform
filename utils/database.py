import asyncpg
import os

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
                sheet_url TEXT NOT NULL,
                sheet_id TEXT NOT NULL,
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


async def get_active_operation(guild_id: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow(
            'SELECT * FROM operations WHERE guild_id = $1 AND is_active = 1 ORDER BY created_at DESC LIMIT 1',
            guild_id,
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
            "SELECT sheet_row FROM requests WHERE operation_id = $1 AND status = 'pending'",
            operation_id,
        )
        return [row['sheet_row'] for row in rows]


async def get_approved_slots(operation_id: int) -> list:
    pool = await get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch(
            "SELECT sheet_row FROM requests WHERE operation_id = $1 AND status = 'approved'",
            operation_id,
        )
        return [row['sheet_row'] for row in rows]


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
                         sheet_col: int = None, unit_role: str = None) -> int:
    pool = await get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow(
            '''INSERT INTO requests
               (guild_id, operation_id, member_id, member_name, slot_label, sheet_row, sheet_col, unit_role)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
               RETURNING id''',
            guild_id, operation_id, member_id, member_name, slot_label, sheet_row, sheet_col, unit_role,
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


async def set_event_time(operation_id: int, event_time, reminder_minutes: int):
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


async def get_approved_member_ids(operation_id: int) -> list:
    pool = await get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch(
            "SELECT member_id, slot_label FROM requests WHERE operation_id = $1 AND status = 'approved'",
            operation_id,
        )
        return [(row['member_id'], row['slot_label']) for row in rows]
