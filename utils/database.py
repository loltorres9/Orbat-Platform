import aiosqlite
import os

DB_PATH = os.getenv('DB_PATH', 'orbat.db')


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS operations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
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
                id INTEGER PRIMARY KEY AUTOINCREMENT,
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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (operation_id) REFERENCES operations(id)
            )
        ''')
        # Migration: add sheet_col to existing deployments
        try:
            await db.execute('ALTER TABLE requests ADD COLUMN sheet_col INTEGER')
            await db.commit()
        except Exception:
            pass  # Column already exists
        await db.commit()


async def get_active_operation(guild_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            'SELECT * FROM operations WHERE guild_id = ? AND is_active = 1 ORDER BY created_at DESC LIMIT 1',
            (guild_id,)
        ) as cursor:
            return await cursor.fetchone()


async def create_operation(guild_id: str, name: str, sheet_url: str, sheet_id: str,
                           squad_col: int, role_col: int, status_col: int, assigned_col: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            'UPDATE operations SET is_active = 0 WHERE guild_id = ?',
            (guild_id,)
        )
        cursor = await db.execute(
            '''INSERT INTO operations
               (guild_id, name, sheet_url, sheet_id, squad_col, role_col, status_col, assigned_col)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            (guild_id, name, sheet_url, sheet_id, squad_col, role_col, status_col, assigned_col)
        )
        await db.commit()
        return cursor.lastrowid


async def get_pending_slots(operation_id: int) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT sheet_row FROM requests WHERE operation_id = ? AND status = 'pending'",
            (operation_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]


async def get_approved_slots(operation_id: int) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT sheet_row FROM requests WHERE operation_id = ? AND status = 'approved'",
            (operation_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]


async def get_member_active_request(guild_id: str, operation_id: int, member_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM requests
               WHERE guild_id = ? AND operation_id = ? AND member_id = ?
               AND status IN ('pending', 'approved')""",
            (guild_id, operation_id, member_id)
        ) as cursor:
            return await cursor.fetchone()


async def create_request(guild_id: str, operation_id: int, member_id: str,
                         member_name: str, slot_label: str, sheet_row: int,
                         sheet_col: int = None) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            '''INSERT INTO requests
               (guild_id, operation_id, member_id, member_name, slot_label, sheet_row, sheet_col)
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (guild_id, operation_id, member_id, member_name, slot_label, sheet_row, sheet_col)
        )
        await db.commit()
        return cursor.lastrowid


async def update_request_message(request_id: int, message_id: str, channel_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            'UPDATE requests SET approval_message_id = ?, approval_channel_id = ? WHERE id = ?',
            (message_id, channel_id, request_id)
        )
        await db.commit()


async def get_request_by_id(request_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM requests WHERE id = ?', (request_id,)) as cursor:
            return await cursor.fetchone()


async def get_all_pending_requests() -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM requests WHERE status = 'pending'"
        ) as cursor:
            return await cursor.fetchall()


async def cancel_member_request(guild_id: str, operation_id: int, member_id: str) -> bool:
    """Cancel a member's pending request. Returns True if a request was cancelled."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """UPDATE requests SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
               WHERE guild_id = ? AND operation_id = ? AND member_id = ? AND status = 'pending'""",
            (guild_id, operation_id, member_id)
        )
        await db.commit()
        return cursor.rowcount > 0


async def clear_pending_requests(operation_id: int) -> int:
    """Cancel all pending requests for an operation. Returns count of cancelled requests."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """UPDATE requests SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
               WHERE operation_id = ? AND status = 'pending'""",
            (operation_id,)
        )
        await db.commit()
        return cursor.rowcount


async def approve_request(request_id: int, approved_by: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE requests
               SET status = 'approved', approved_by = ?, updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (approved_by, request_id)
        )
        await db.commit()


async def deny_request(request_id: int, denied_by: str, reason: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE requests
               SET status = 'denied', approved_by = ?, denial_reason = ?, updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (denied_by, reason, request_id)
        )
        await db.commit()
