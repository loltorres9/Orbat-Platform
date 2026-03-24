# ORBATBot

A Discord bot for managing Arma 3 operation slot requests. Members request slots via a Discord dropdown menu; admins approve or deny requests with a button click, and the Google Sheet is updated automatically.

---

## Features

- `/request-slot` — shows all available slots as a dropdown (up to 125 slots across 5 select menus, grouped by squad)
- `/cancel-request` — cancel your pending slot request
- `/change-slot` — forfeit your current slot and pick a new one
- `/leave-operation` — remove yourself from the operation entirely (pending or approved)
- `/setup-slots <url>` — admin command to load a Google Sheet for the current operation; auto-posts a live ORBAT to `#orbat`
- `/post-orbat [channel]` — manually post (or re-post) the live ORBAT board to any channel
- `/current-operation` — shows which operation is active and links to the sheet
- `/clear-slot` — admin command to remove a member from an approved slot
- `/clear-requests` — admin command to cancel all pending requests for the current operation
- `/sync` — force-sync slash commands with Discord (useful after updates)
- Approval channel (`#slot-approvals`) with **Approve / Deny** buttons
- Denial modal with optional reason text
- DM notifications to members on submission, approval, and denial
- Slots marked 🟢 (available), 🟡 (pending), or 🔴 (filled) in real time
- Live ORBAT embed auto-updates whenever a slot changes, showing open/pending/filled counts per squad
- Unit role gating — admins can only approve requests from their own unit (2nd USC, CNTO, PXG, TFP)
- Approval buttons survive bot restarts (persistent views)
- Bot syncs slash commands automatically on startup — no manual `/sync` needed
- Slot availability is re-validated at selection time, preventing race conditions
- PostgreSQL database — data persists across all restarts and redeployments

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

The bot also supports **ORBAT-style sheets** where slots appear as cell values (e.g. `1. Squad Lead`) rather than rows. Available slots contain `<Insert Name>`; filled slots use formats like `[TAG] Name` or `[] Name`.

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

### 2. Google Sheets API

1. Go to [Google Cloud Console](https://console.cloud.google.com) → **New Project**
2. Enable the **Google Sheets API** and **Google Drive API**
3. Go to **Credentials → Create Credentials → Service Account**
4. Under the service account → **Keys → Add Key → JSON** — download the file
5. Share each ORBAT sheet with the service account email (found inside the JSON as `client_email`) — give it **Editor** access

### 3. Environment Variables

Copy `.env.example` to `.env` and fill in:

```
DISCORD_TOKEN=your_bot_token
GOOGLE_CREDENTIALS={...paste entire JSON key file contents here...}
DATABASE_URL=postgresql://user:pass@host:port/dbname
```

`DATABASE_URL` is only needed for local development — Railway injects it automatically.

### 4. Deploy to Railway

1. Push this repo to GitHub
2. Go to [Railway](https://railway.app) → **New Project → Deploy from GitHub** → select this repo
3. Add a **Postgres** service to your project (Railway dashboard → **+ New** → **Database → PostgreSQL**)
4. In your bot service → **Variables** — add `DISCORD_TOKEN` and `GOOGLE_CREDENTIALS`
   - `DATABASE_URL` is injected automatically from the Postgres service — no manual entry needed
5. Railway will auto-deploy. The `Procfile` tells it to run `python bot.py`

> The database lives in PostgreSQL and persists across all restarts and redeployments. No volume configuration needed.

---

## Usage

### Admin

```
/setup-slots https://docs.google.com/spreadsheets/d/.../edit
```

Run this once per operation. The previous operation is archived automatically. A live ORBAT embed is posted to `#orbat` (created if it doesn't exist).

```
/post-orbat [#channel]
```

Manually post or re-post the live ORBAT board. Defaults to the current channel.

```
/clear-slot
```

Presents a dropdown of approved slots. Select one to remove the member and free the slot. The member receives a DM.

```
/clear-requests
```

Cancels all pending requests for the current operation (e.g. to reset before an op).

```
/current-operation
```

Shows which sheet is currently loaded and links to it.

```
/sync
```

Force-syncs slash commands with Discord. Only needed if commands appear missing after a deployment.

### Members

```
/request-slot
```

Opens a dropdown showing all available slots for the current operation, grouped by squad. Select one to submit a request. You can only hold one slot per operation.

```
/cancel-request
```

Cancels your pending slot request and frees it for others.

```
/change-slot
```

Forfeits your current slot (pending or approved) and lets you pick a new one. If your slot was approved, it is also cleared from the sheet.

```
/leave-operation
```

Removes you from the operation entirely. Works for both pending and approved slots. If you were approved, your slot is also cleared from the sheet. Shows a confirmation prompt before acting.

### Approval flow

1. Requested slots appear in `#slot-approvals` (created automatically if it doesn't exist)
2. An admin from the same unit clicks **✅ Approve** or **❌ Deny**
3. On approval: the Google Sheet is updated, the ORBAT board refreshes, and the member gets a DM
4. On denial: admin optionally provides a reason; member gets a DM and can request again

**Unit role gating:** Admins with a unit role (2nd USC, CNTO, PXG, TFP) can only approve/deny requests from members of their own unit. Admins without a unit role can approve any request.

---

## Local Development

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # fill in your values
python bot.py
```

No manual command sync is needed — the bot syncs slash commands to all guilds automatically on startup.
