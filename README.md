# ORBAT Platform

DB-native ORBAT management for Discord.

- One Railway service runs both:
  - `discord.py` bot
  - `FastAPI` backend
- Shared PostgreSQL (`asyncpg` pool)
- Optional React SPA in `web/` deploys to GitHub Pages
- Realtime browser updates via PostgreSQL `LISTEN/NOTIFY` + WebSockets

## Architecture

### Railway (single process)

- Bot commands and approval workflow
- FastAPI HTTP + WebSocket API
- Shared DB pool

### GitHub Pages (SPA)

- ORBAT viewer
- Slot request UI
- Admin ORBAT builder
- Calls Railway FastAPI

## Current Status

- Google Sheets dependency removed from runtime flow.
- DB schema includes:
  - `operations`
  - `squads`
  - `slots`
  - `requests`
  - `web_sessions`
- Slot approval in Discord now assigns directly in DB.
- ORBAT embed renders from DB state.

## Environment Variables

Required:

- `DISCORD_TOKEN`
- `DATABASE_URL`

API/OAuth (for web login):

- `DISCORD_CLIENT_ID`
- `DISCORD_CLIENT_SECRET`
- `DISCORD_REDIRECT_URI`
- `FRONTEND_ORIGINS` (comma-separated, default `*`)
- `COOKIE_SECURE` (`true` in production)

Runtime tuning:

- `PORT` (Railway usually injects this)
- `API_PORT` (fallback if `PORT` missing)
- `API_HOST` (default `0.0.0.0`)
- `API_LOG_LEVEL` (default `info`)

## Bot Commands (DB-native)

### Members

- `/request-slot`
- `/cancel-request`
- `/change-slot`
- `/leave-operation`

### Admin / Unit Leader

- `/assign-slot`
- `/clear-slot`

### Admin

- `/create-operation`
- `/add-squad`
- `/add-slot`
- `/activate-operation`
- `/post-orbat`
- `/current-operation`
- `/debug-slots`
- `/clear-requests`
- `/set-event-time`
- `/set-timezone`
- `/post-event`
- `/sync`
- `/archive-old-approvals`

### Deprecated

- `/setup-slots` now returns a deprecation message and points to DB builder commands.

## API Endpoints

Base path: `/api`

- `GET /api/health`
- `POST /api/auth/discord/exchange`
- `GET /api/auth/session`
- `POST /api/auth/logout`
- `GET /api/operations/active?guild_id=...`
- `GET /api/operations?guild_id=...`
- `POST /api/operations`
- `POST /api/operations/{operation_id}/activate`
- `GET /api/operations/{operation_id}/orbat`
- `POST /api/operations/{operation_id}/squads`
- `PATCH /api/squads/{squad_id}`
- `DELETE /api/squads/{squad_id}`
- `POST /api/operations/{operation_id}/slots`
- `PATCH /api/slots/{slot_id}`
- `DELETE /api/slots/{slot_id}`
- `POST /api/slots/{slot_id}/request`

Realtime:

- `WS /ws/operations/{operation_id}`

## Local Run

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
pip install -r requirements.txt
python bot.py
```

Optional manual migration:

```sql
-- run against your Postgres DB
\i migrations/001_drop_legacy_sheet_columns.sql
```

## Web App (`web/`)

```bash
cd web
npm install
npm run dev
```

Set `VITE_API_BASE_URL` in CI/local env to point at your Railway API origin.

## GitHub Pages

Workflow file:

- `.github/workflows/pages.yml`

Required repository secrets:

- `VITE_API_BASE_URL`
- `VITE_API_BASE_URL_CODEX`

Published URLs:

- `main`: `https://loltorres9.github.io/Orbat-Platform/`
- `codex/working-2026-04-16`: `https://loltorres9.github.io/Orbat-Platform/codex-working-2026-04-16/`

Expected secret values:

- `VITE_API_BASE_URL`: main Railway API origin
- `VITE_API_BASE_URL_CODEX`: codex branch Railway API origin

Example:

- `https://your-railway-service.up.railway.app`
