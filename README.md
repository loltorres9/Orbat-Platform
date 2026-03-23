# ORBATBot

A Discord bot for managing Arma 3 operation slot requests. Members request slots via a Discord dropdown menu; admins approve or deny requests with a button click, and the Google Sheet is updated automatically.

---

## Features

- `/request-slot` — shows all available slots as a dropdown (works with up to 125 slots across 5 select menus)
- `/setup-slots <url>` — admin command to load any Google Sheet for the current operation
- `/current-operation` — shows which operation is active
- Approval channel (`#slot-approvals`) with **Approve / Deny** buttons
- Denial modal with optional reason text
- DM notifications to members on submission, approval, and denial
- Slots marked 🟢 (available) or 🟡 (pending) in real time
- Approval buttons survive bot restarts (persistent views)

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
DISCORD_TOKEN=...
GOOGLE_CREDENTIALS={...paste entire JSON key file contents here...}
```

### 4. Deploy to Railway

1. Push this repo to GitHub
2. Go to [Railway](https://railway.app) → **New Project → Deploy from GitHub** → select this repo
3. In the Railway project → **Variables** — add `DISCORD_TOKEN` and `GOOGLE_CREDENTIALS`
4. Railway will auto-deploy. The `Procfile` tells it to run `python bot.py`

> **Note:** The SQLite database (`orbat.db`) is stored on the Railway volume. It persists between deployments but will reset if the service is deleted.

---

## Usage

### Admin

```
/setup-slots https://docs.google.com/spreadsheets/d/.../edit
```

Run this once per operation. The previous operation is archived automatically.

```
/current-operation
```

Shows which sheet is currently loaded.

### Members

```
/request-slot
```

Opens a dropdown showing all available slots for the current operation. Select one to submit a request.

### Approval flow

1. Requested slots appear in `#slot-approvals` (created automatically if it doesn't exist)
2. Any admin clicks **✅ Approve** or **❌ Deny**
3. On approval: the Google Sheet is updated and the member gets a DM
4. On denial: admin optionally provides a reason; member gets a DM and can request again

---

## Local Development

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # fill in your values
python bot.py
```
