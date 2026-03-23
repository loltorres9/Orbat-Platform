import gspread
from google.oauth2.service_account import Credentials
import json
import os
import re
from typing import Optional

SCOPES = [
    'https://spreadsheets.google.com/feeds',
    'https://www.googleapis.com/auth/drive',
]


def get_client() -> gspread.Client:
    creds_json = os.getenv('GOOGLE_CREDENTIALS')
    if not creds_json:
        raise ValueError("GOOGLE_CREDENTIALS environment variable not set.")
    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return gspread.authorize(creds)


def extract_sheet_id(url: str) -> str:
    match = re.search(r'/spreadsheets/d/([a-zA-Z0-9-_]+)', url)
    if match:
        return match.group(1)
    raise ValueError(f"Could not extract sheet ID from URL. Make sure it's a valid Google Sheets link.")


def load_slots(sheet_url: str) -> dict:
    """
    Load available slots from a Google Sheet.

    Expected sheet format (column names are flexible):
    | Squad / Unit | Role / Position | Status    | Assigned To |
    | Squad 1      | Squad Lead      | Available |             |
    | Squad 1      | Rifleman (AR)   | Available |             |
    ...

    Returns a dict with operation metadata and list of available slots.
    """
    client = get_client()
    sheet_id = extract_sheet_id(sheet_url)
    spreadsheet = client.open_by_key(sheet_id)
    worksheet = spreadsheet.sheet1
    operation_name = spreadsheet.title

    all_values = worksheet.get_all_values()

    # Keywords used to auto-detect column roles
    squad_keywords = {'squad', 'unit', 'element', 'group', 'platoon', 'team', 'section', 'callsign'}
    role_keywords = {'role', 'position', 'slot', 'job', 'rank', 'billet'}
    status_keywords = {'status', 'available', 'state'}
    assigned_keywords = {'assigned', 'member', 'player', 'name', 'pilot', 'operator'}

    squad_col = role_col = status_col = assigned_col = None
    header_row_idx = None

    for i, row in enumerate(all_values):
        # Normalize cell text for keyword matching
        normalized = [
            cell.lower().replace(' ', '').replace('_', '').replace('/', '').replace('-', '')
            for cell in row
        ]
        sq_cols = [j for j, cell in enumerate(normalized) if any(kw in cell for kw in squad_keywords)]
        rl_cols = [j for j, cell in enumerate(normalized) if any(kw in cell for kw in role_keywords)]

        if sq_cols and rl_cols:
            header_row_idx = i
            squad_col = sq_cols[0]
            role_col = rl_cols[0]
            st_cols = [j for j, cell in enumerate(normalized) if any(kw in cell for kw in status_keywords)]
            as_cols = [j for j, cell in enumerate(normalized) if any(kw in cell for kw in assigned_keywords)]
            if st_cols:
                status_col = st_cols[0]
            if as_cols:
                assigned_col = as_cols[0]
            break

    if header_row_idx is None:
        raise ValueError(
            "Could not find slot columns in this sheet.\n\n"
            "Your sheet needs at minimum two columns with headers like:\n"
            "• **Squad** or **Unit** (for the group name)\n"
            "• **Role** or **Position** (for the slot name)\n\n"
            "Optional columns: **Status** (Available/Assigned) and **Assigned To**.\n"
            "See the README for a compatible template."
        )

    slots = []
    for i, row in enumerate(all_values[header_row_idx + 1:], start=header_row_idx + 2):
        if len(row) <= max(squad_col, role_col):
            continue

        squad = row[squad_col].strip()
        role = row[role_col].strip()
        if not squad or not role:
            continue

        # Skip already-assigned slots
        if assigned_col is not None and len(row) > assigned_col and row[assigned_col].strip():
            continue

        # Skip slots whose status is not "available"
        if status_col is not None and len(row) > status_col:
            status = row[status_col].strip().lower()
            if status and status not in ('available', 'open', 'free', 'yes', ''):
                continue

        label = f"{squad} \u2013 {role}"
        if len(label) > 100:
            label = label[:97] + '...'

        slots.append({
            'label': label,
            'row': i,           # 1-indexed sheet row number
            'squad': squad,
            'role': role,
            'value': f"row_{i}",  # unique identifier for Discord select menu option
        })

    return {
        'operation_name': operation_name,
        'slots': slots,
        'sheet_id': sheet_id,
        'squad_col': squad_col,
        'role_col': role_col,
        'status_col': status_col,
        'assigned_col': assigned_col,
    }


def assign_slot(sheet_id: str, row: int, member_name: str,
                assigned_col: Optional[int], status_col: Optional[int]):
    """Write the member's name (and update status) into the sheet for an approved slot."""
    client = get_client()
    spreadsheet = client.open_by_key(sheet_id)
    worksheet = spreadsheet.sheet1

    # gspread uses 1-based column indices
    if assigned_col is not None:
        worksheet.update_cell(row, assigned_col + 1, member_name)
    if status_col is not None:
        worksheet.update_cell(row, status_col + 1, 'Assigned')
