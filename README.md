# TASK FORCE PHALANX ORBAT Platform

TASK FORCE PHALANX ORBAT Platform is a Discord + web-based ORBAT management system for Arma operations.

It lets communities:

- build operations with squads and roles
- let users sign up for slots from a web UI
- approve or deny slot requests inside Discord
- keep the live ORBAT synced between Discord, the website, and PostgreSQL
- manage guild-specific web admins
- export and import event layouts between servers

The project is built around one backend service and one static frontend:

- `Railway` hosts the Python backend and the Discord bot
- `Postgres` stores operations, squads, slots, requests, sessions, and admin state
- `GitHub Pages` hosts the React frontend


**Architecture**

The project has two main parts:

1. `app_main.py` starts a single Railway web service that runs:
   - a `FastAPI` API
   - a `discord.py` bot in the background
   - a shared `asyncpg` database connection pool

2. `web/` contains a Vite + React frontend that:
   - logs users in with Discord OAuth
   - loads available Discord servers
   - shows the live ORBAT
   - lets admins manage operations, squads, lanes, roles, reminders, and imports/exports

Realtime updates are sent through:

- PostgreSQL `LISTEN/NOTIFY`
- FastAPI WebSockets


**Main Features**

- Discord OAuth login for web users
- guild-specific permission model
- automatic web admin access for Discord server admins
- three-lane ORBAT layout
- squad notes and team grouping
- slot request and approval workflow
- self-release and admin release of slots
- event reminder scheduling
- event JSON export/import
- Discord embed posting for ORBAT and events


**Tech Stack**

- Python 3.11+
- `discord.py`
- `FastAPI`
- `uvicorn`
- `asyncpg`
- React 18
- Vite
- PostgreSQL
- Railway
- GitHub Pages


**Repository Layout**

```text
.
|- api_server.py                 FastAPI app and web API routes
|- app_main.py                   Railway entrypoint
|- bot.py                        Discord bot bootstrap
|- cogs/                         Discord bot cogs
|- utils/database.py             Database schema + queries
|- migrations/                   Manual SQL helpers
|- web/                          React frontend
|- .github/workflows/pages.yml   GitHub Pages deployment
|- railway.json                  Railway deploy config
```


**Environment Variables**

Required:

- `DATABASE_URL`
- `DISCORD_TOKEN`
- `DISCORD_CLIENT_ID`
- `DISCORD_CLIENT_SECRET`
- `DISCORD_REDIRECT_URI`

Recommended:

- `FRONTEND_ORIGINS`
- `COOKIE_SECURE`
- `COOKIE_SAMESITE`
- `API_LOG_LEVEL`

Runtime:

- `PORT`

Notes:

- On Railway, `PORT` is injected automatically.
- `DISCORD_REDIRECT_URI` must point to:
  `https://<your-railway-domain>/api/auth/discord/callback`
- `FRONTEND_ORIGINS` should include the domain serving the frontend, for example:
  `https://loltorres9.github.io`


**Local Development**

Backend:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app_main.py
```

Frontend:

```bash
cd web
npm install
npm run dev
```

Frontend environment:

- set `VITE_API_BASE_URL` to your backend URL
- optionally set `VITE_PUBLIC_BASE_PATH` if not using the default GitHub Pages path


**Deploying the Project**

**Railway**

Use one Railway service for the backend and bot:

1. Create a new Railway project
2. Add a PostgreSQL database
3. Connect the GitHub repository
4. Set the production branch to `main`
5. Ensure the service start command is `python app_main.py`
6. Add the required environment variables
7. Deploy

Expected health check:

- `GET /api/health`

**GitHub Pages**

The frontend is deployed from GitHub Actions on `main`.

Current workflow:

- `.github/workflows/pages.yml`

Repository variable needed:

- `VITE_API_BASE_URL`

That value should point to your Railway backend domain, for example:

- `https://orbat-platform-production.up.railway.app`

GitHub Pages settings:

1. Open `Settings -> Pages`
2. Set `Source` to `GitHub Actions`
3. Let the workflow deploy from `main`


**How to Recreate This Project**

If you wanted to build this system again from scratch, the short version would be:

1. Create a Discord bot application
2. Enable a bot user and invite it to your server
3. Create a Railway project with Postgres
4. Build a Python backend that serves both:
   - Discord bot logic
   - a FastAPI web API
5. Store ORBAT data in PostgreSQL instead of Google Sheets
6. Build a React frontend that talks to the API
7. Add Discord OAuth login for the website
8. Add a request/approval flow:
   - user requests a slot in the web app
   - bot posts approval message in Discord
   - approval updates the database
   - website refreshes through WebSocket events
9. Deploy the backend to Railway
10. Deploy the frontend to GitHub Pages

That is exactly the architecture this repository now follows.


**Operational Flow**

Typical request lifecycle:

1. A user logs into the web app with Discord
2. The user selects a server
3. The user requests an open slot
4. The backend creates a pending request in Postgres
5. The bot posts the approval request in Discord
6. An eligible approver accepts or denies it
7. The slot state updates in the database
8. The web app refreshes live through WebSocket notifications


**Permissions Model**

- normal users can view and request slots
- users can leave their own assigned slot
- portal admins can manage the ORBAT in the web UI
- Discord server admins are automatically treated as web admins for that guild
- request approvals are restricted by unit role or Discord admin permissions


**Current Deployment Model**

- `main` is the active deployment branch
- Railway should point to `main`
- GitHub Pages should deploy from GitHub Actions on `main`


**Troubleshooting**

- `401` after Discord login:
  check the OAuth callback URL, frontend API base URL, and session token handoff

- Pages site builds but frontend cannot talk to backend:
  verify `VITE_API_BASE_URL`

- Railway returns `502`:
  verify the service is running as a web service and binding to Railway's `PORT`

- Discord login fails inside an iframe:
  Discord blocks embedded auth flows; use a new tab


**Manual Migration Helper**

If you need the legacy cleanup SQL manually:

```sql
\i migrations/001_drop_legacy_sheet_columns.sql
```


**Status**

The repository is now centered on the DB-native ORBAT platform. The old Google Sheets runtime flow is no longer the primary path.
