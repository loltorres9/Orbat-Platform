# ORBATBot

A Discord bot for managing Arma 3 operation slot requests. Members request slots via a two-step squad → slot picker or the **📋 Request a Slot** button on the ORBAT embed; admins and Unit Leaders approve or deny requests with a button click, and the Google Sheet is updated automatically.

---

## Features

- **Two-step slot picker** — choose your squad first, then your slot. Works via `/request-slot`, the ORBAT button, and `/change-slot`
- **📋 Request a Slot** button — persistent button on the live ORBAT embed; no command needed
- `/request-slot` — open the squad → slot picker for the current operation
- `/cancel-request` — cancel your pending slot request
- `/change-slot` — forfeit your current slot and pick a new one
- `/leave-operation` — remove yourself from the operation entirely (pending or approved)
- `/setup-slots <url>` — load a Google Sheet for the current operation; supports optional event time and reminder; auto-posts a live ORBAT to `#orbat`
- `/set-event-time <time>` — update the event start time for the current operation
- `/set-timezone <tz>` — set the server's local timezone for event time input (default: UTC)
- `/post-orbat [channel]` — manually post or re-post the live ORBAT board
- `/current-operation` — shows which operation is active and links to the sheet
- `/assign-slot <member>` — assign a member to a slot directly, bypassing approval; uses the same two-step picker (Unit Leaders scoped to their own unit; Admins unrestricted)
- `/clear-slot` — remove a member from an approved slot; restores the sheet cell including stripping the unit tag
- `/clear-requests` — cancel all pending requests for the current operation
- `/archive-old-approvals` — move pre-existing approved messages from `#slot-approvals` to `#approval-archive` (one-time migration)
- `/debug-slots` — show the raw slot data the bot reads from the sheet; useful for diagnosing missing slots
- `/sync` — force-sync slash commands with Discord; also refreshes the live ORBAT embed
- Approval channel (`#slot-approvals`) with **Approve / Deny** buttons
- Approved requests are deleted from `#slot-approvals` and archived as a compact embed in `#approval-archive`
- Denial modal with optional reason text
- DM notifications to members on submission, approval, and denial
- Slots marked 🟢 (available), 🟡 (pending / also requested — compete for slot), or 🔴 (filled) in real time
- Multiple members can request the same pending slot — the approver picks who gets it; all other competitors are auto-denied and notified
- Cancelled requests automatically void their approval message (greyed out, buttons removed)
- Event reminders — bot DMs all approved members and pings `#orbat` before the operation starts
- Live ORBAT embed shows event time as a Discord timestamp and auto-updates on every slot change
- Role-based access control — Unit Leaders get extra commands scoped to their own unit (see table below)
- Approval buttons survive bot restarts (persistent views)
- Bot syncs slash commands automatically on startup — no manual `/sync` needed
- Slot availability is re-validated at selection time, preventing race conditions
- PostgreSQL database — data persists across all restarts and redeployments

---

## Role-Based Access

| Command | Members | Unit Leaders | Admins |
|---|---|---|---|
| `/request-slot`, `/cancel-request`, `/change-slot`, `/leave-operation` | ✅ | ✅ | ✅ |
| `/clear-slot` | ❌ | ✅ (own unit only) | ✅ |
| `/assign-slot` | ❌ | ✅ (own unit only) | ✅ |
| `/clear-requests`, `/post-orbat`, `/set-event-time`, `/set-timezone` | ❌ | ❌ | ✅ |
| `/setup-slots`, `/current-operation`, `/sync`, `/debug-slots`, `/archive-old-approvals` | ❌ | ❌ | ✅ |
| Approve / Deny in `#slot-approvals` | ❌ | ✅ (own unit only) | ✅ |

**Unit roles:** `2nd USC`, `CNTO`, `PXG`, `TFP`, `SKUA`

A **Unit Leader** is any member with the `Unit Leader` Discord role. They can approve/deny requests, assign slots, and manage slots for members who share their unit role. Admins (Manage Server permission) have unrestricted access.

---

## Sheet Format

Your Google Sheet needs at minimum **two columns** with recognisable headers:

| Squad / Unit | Role / Position | Status    | Assigned To |
|--------------|-----------------|-----------|-------------|
| Squad 1      | Squad Lead      | Available |             |
| Squad 1      | Rifleman (AR)   | Available |             |
| Squad 1      | Medic           | Available |             |
| Squad 2      | Squad Lead      | Available |             |

- **Squad / Unit** — the group name (header must contain: squad, unit, element, group, platoon, team, section, or callsign)
- **Role / Position** — the slot name (header must contain: role, position, slot, job, rank, or billet)
- **Status** *(optional)* — rows where this is not `Available`, `Open`, `Free`, or blank are hidden from the menu
- **Assigned To** *(optional)* — rows with a value here are treated as already taken

The bot also supports **ORBAT-style sheets** where slots appear as cell values (e.g. `1. Squad Lead`). Available slots contain `<Insert Name>`; filled slots use formats like `[TAG] Name` or `[] Name`. When a slot is cleared the unit tag is removed and the cell is restored to `[] <Insert Name>`.

> You don't have to rename your columns exactly — the bot looks for keywords anywhere in the header cell.
> Share the sheet with your service account email before running `/setup-slots`.

---

## Setup

### 1. Discord Bot

1. Go to [Discord Developer Portal](https://discord.com/developers/applications) → **New Application**
2. Go to **Bot** → **Add Bot** → copy the **Token**
3. Go to **OAuth2 → URL Generator**:
   - Scopes: `bot`, `applications.commands`
   - Bot permissions: `Send Messages`, `Embed Links`, `Read Message History`, `Manage Channels`, `Use Slash Commands`
4. Paste the generated URL in your browser and invite the bot to your server

> **Important — command visibility:** After the bot joins, go to **Server Settings → Integrations → ORBATBot → Manage**. Make sure `@everyone` is set to ✅ (allow). If it is set to ❌, all commands will be hidden from regular members regardless of what the bot configures. Admin-only commands are restricted automatically by the bot — you do not need to configure those manually.

### 2. Google Sheets API

1. Go to [Google Cloud Console](https://console.cloud.google.com) → **New Project**
2. Enable the **Google Sheets API** and **Google Drive API**
3. Go to **Credentials → Create Credentials → Service Account**
4. Under the service account → **Keys → Add Key → JSON** — download the file
5. Share each ORBAT sheet with the service account email (found inside the JSON as `client_email`) — give it **Editor** access

### 3. Environment Variables

Copy `.env.example` to `.env` and fill in the three required values:

```
DISCORD_TOKEN=your_bot_token
GOOGLE_CREDENTIALS={...paste entire JSON key file contents here...}
DB_PASSWORD=choose_a_secure_password
```

> `DATABASE_URL` is constructed automatically by docker-compose from `DB_PASSWORD`. On Railway it is injected automatically — you do not set it manually in either case.

---

### 4a. Deploy to Railway

1. Push this repo to GitHub
2. Go to [Railway](https://railway.app) → **New Project → Deploy from GitHub** → select this repo
3. Add a **Postgres** service to your project (Railway dashboard → **+ New** → **Database → PostgreSQL**)
4. In your bot service → **Variables** — add `DISCORD_TOKEN` and `GOOGLE_CREDENTIALS`
   - `DATABASE_URL` is injected automatically from the Postgres service — no manual entry needed
5. Railway will auto-deploy on every push. The `Procfile` tells it to run `python bot.py`

> The database lives in PostgreSQL and persists across all restarts and redeployments. No volume configuration needed.

---

### 4b. Deploy to a VPS with Docker

This is the recommended self-hosted option. You need a Linux VPS with SSH access (Ubuntu 22.04 or similar).

#### Install Docker

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker
```

Verify:

```bash
docker --version
docker compose version
```

#### Clone the repo

```bash
git clone https://github.com/loltorres9/orbatbot.git
cd orbatbot
```

#### Configure environment

```bash
cp .env.example .env
nano .env
```

Fill in `DISCORD_TOKEN`, `GOOGLE_CREDENTIALS`, and `DB_PASSWORD`. Save and exit (`Ctrl+X → Y → Enter`).

#### Start the bot

```bash
docker compose up -d
```

This builds the bot image, starts a PostgreSQL 16 container, and launches the bot. Both containers restart automatically if the VPS reboots.

#### Useful commands

```bash
# View live logs
docker compose logs -f bot

# Stop the bot
docker compose down

# Update to the latest version
git pull
docker compose up -d --build

# Restart the bot only
docker compose restart bot
```

> Bot data is stored in a named Docker volume (`postgres_data`) and survives container restarts, rebuilds, and updates.

---

## Usage

### Members

Available to all server members.

```
/request-slot
```

Opens a squad picker — select your squad first, then choose your slot. You can also click the **📋 Request a Slot** button directly on the ORBAT embed for the same flow. You can only hold one slot per operation.

```
/cancel-request
```

Cancels your pending slot request and frees it for others.

```
/change-slot
```

Forfeits your current slot (pending or approved) and lets you pick a new one via the squad → slot picker. If your slot was approved, it is also cleared from the sheet.

```
/leave-operation
```

Removes you from the operation entirely. Works for both pending and approved slots. If you were approved, your slot is also cleared from the sheet. Shows a confirmation prompt before acting.

---

### Unit Leaders

Available to members with the **Unit Leader** Discord role. Scoped to their own unit only.

```
/assign-slot @member
```

Directly assigns a member of your unit to a slot — no approval message, no waiting. Uses the same squad → slot picker. The sheet is updated immediately and the member gets a DM. Blocked if the member already holds a slot; use `/clear-slot` first to reassign.

```
/clear-slot
```

Presents a dropdown of active slots. Select one to remove the member and free the slot. The sheet cell is restored to `[] <Insert Name>` (unit tag removed). The member receives a DM.

Unit Leaders only see slots belonging to members of their own unit.

Unit Leaders can also **Approve / Deny** requests in `#slot-approvals` for members of their own unit.

---

### Admins

Available to members with the **Manage Server** permission. Full access with no unit restrictions.

```
/assign-slot @member
```

Directly assigns any member to any slot — no approval message, no waiting. Uses the same squad → slot picker. The sheet is updated immediately and the member gets a DM. Blocked if the member already holds a slot; use `/clear-slot` first to reassign.

```
/setup-slots https://docs.google.com/spreadsheets/d/.../edit
```

Run this once per operation. The previous operation is archived automatically. A live ORBAT embed is posted to `#orbat` (created if it doesn't exist). Optional parameters:

- `event_time` — operation start time in `DD/MM/YYYY HH:MM` or `YYYY-MM-DD HH:MM` format (uses the server's configured timezone)
- `reminder_minutes` — how many minutes before the event to send reminders (default: 30)

```
/set-timezone Europe/Berlin
```

Sets the server's local timezone so event times you type are interpreted correctly. Only needs to be set once. Default is UTC.

```
/set-event-time 25/06/2025 20:00
```

Updates the event time for the current operation without re-running `/setup-slots`. The ORBAT embed and reminder are updated immediately.

```
/post-orbat [#channel]
```

Manually post or re-post the live ORBAT board. Defaults to the current channel.

```
/clear-requests
```

Cancels all pending requests for the current operation (e.g. to reset before an op).

```
/archive-old-approvals
```

One-time migration command. Scans `#slot-approvals` for old bot-posted approved messages (green embeds with an Approved field) and moves them to `#approval-archive`. Creates the archive channel if it doesn't exist. Use this once after upgrading from a version that edited approval messages in place.

```
/debug-slots [squad]
```

Shows the raw slot data the bot reads from the current sheet. Useful for diagnosing why a slot isn't appearing in the picker. Optionally filter by squad name.

```
/current-operation
```

Shows which sheet is currently loaded and links to it.

```
/sync
```

Force-syncs slash commands with Discord and refreshes the live ORBAT embed. Only needed if commands appear missing after a deployment.

---

### Approval flow

1. Requested slots appear in `#slot-approvals` (created automatically if it doesn't exist)
2. An admin or Unit Leader from the same unit clicks **✅ Approve** or **❌ Deny**
3. On approval:
   - The Google Sheet is updated
   - The request is deleted from `#slot-approvals`
   - A compact record is posted to `#approval-archive` (created automatically if it doesn't exist)
   - The ORBAT board refreshes
   - The member gets a DM
   - If other members had requested the same slot, they are automatically denied and notified
4. On denial: admin optionally provides a reason; member gets a DM and can request again
5. If a member cancels their request, the approval message is automatically updated to show it was cancelled (greyed out, buttons removed)

**Unit role gating:** Unit Leaders (and admins with a unit role) can only approve/deny requests from members of their own unit. Admins without a unit role can approve any request.

### Approval archive

Every approved slot request is logged to `#approval-archive` as a compact embed showing the operation, unit, member, slot, and approver. The channel is created automatically the first time an approval goes through. To migrate old approved messages that were posted before this feature existed, run `/archive-old-approvals`.

### Event reminders

When an event time is set, the bot automatically:
- DMs every approved member with their slot name and a countdown timestamp
- Posts a ping in `#orbat` tagging all approved members

Reminders fire at the configured window before the event (default 30 minutes). The reminder fires once and will not repeat.

---

## Local Development

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # fill in your values
python bot.py
```

You will need a PostgreSQL instance running locally and `DATABASE_URL` set in your `.env`. No manual command sync is needed — the bot syncs slash commands to all guilds automatically on startup.
