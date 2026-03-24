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

# ---------------------------------------------------------------------------
# ORBAT visual-format helpers
# ---------------------------------------------------------------------------

# Matches slot entries like "1. Squad Leader" or "1- Squad Leader"
# but NOT section numbers like "1-1 Rangers" (digit immediately after hyphen).
_SLOT_PREFIX = re.compile(r'^\d+[.\-](?!\d)\s*')
# Radio-frequency cells like "152 CHN : 1" — not squad headers.
_RADIO_FREQ = re.compile(r'\d{3}\s*CHN', re.IGNORECASE)


def _is_slot_entry(cell: str) -> bool:
    return bool(_SLOT_PREFIX.match(cell.strip()))


def _is_available(cell: str) -> bool:
    return '<insert name>' in cell.lower()


def _extract_assigned_name(cell: str) -> str | None:
    """Return the player name from a filled assignment cell, or None."""
    m = re.search(r'\[.*?\]\s*(.+)', cell)
    if m:
        name = m.group(1).strip()
        if name and '<insert name>' not in name.lower():
            return name
    return None


def _extract_role(cell: str) -> str:
    """From '3. Team Leader Alpha - [] <Insert Name>' returns 'Team Leader Alpha'."""
    role = _SLOT_PREFIX.sub('', cell.strip())
    role = re.sub(r'\s*[-–]\s*\[.*', '', role)
    return role.strip()


def _is_squad_header(cell: str) -> bool:
    cell = cell.strip()
    if not cell:
        return False
    if _is_slot_entry(cell):
        return False
    if _RADIO_FREQ.search(cell):
        return False
    if not re.search(r'[a-zA-Z]', cell):
        return False
    if len(cell) < 4:
        return False
    if cell.endswith('.') or cell.endswith('!') or cell.endswith('?'):
        return False
    if _is_available(cell):
        return False
    return True


def _detect_orbat_format(all_values: list) -> bool:
    """Return True if the sheet uses the visual ORBAT format (<Insert Name> cells)."""
    for row in all_values[:60]:
        for cell in row:
            if '<insert name>' in cell.lower():
                return True
    return False


# ---------------------------------------------------------------------------
# Sheet client
# ---------------------------------------------------------------------------

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
    raise ValueError("Could not extract sheet ID. Make sure it's a valid Google Sheets link.")


# ---------------------------------------------------------------------------
# ORBAT visual-format parsers
# ---------------------------------------------------------------------------

def _orbat_load_available(all_values: list, operation_name: str, sheet_id: str) -> dict:
    """
    Parse a visual ORBAT sheet and return only available (open) slots.
    Used by load_slots() for the /request-slot menu.
    """
    num_cols = max(len(row) for row in all_values) if all_values else 0
    squad_per_col: dict[int, str] = {}
    seen_values: set = set()
    slots = []

    for row_idx, row in enumerate(all_values):
        for col_idx in range(num_cols):
            cell = row[col_idx].strip() if col_idx < len(row) else ''
            if not cell:
                continue

            if _is_slot_entry(cell):
                assign_col = None
                for search_col in range(col_idx, min(col_idx + 5, num_cols)):
                    search_cell = row[search_col].strip() if search_col < len(row) else ''
                    if search_col > col_idx and _is_slot_entry(search_cell):
                        break  # crossed into another slot's column
                    if _is_available(search_cell):
                        assign_col = search_col
                        break
                    if _extract_assigned_name(search_cell):
                        break  # slot is already filled — skip

                if assign_col is None:
                    continue

                role = _extract_role(cell)
                squad = squad_per_col.get(col_idx, 'Unknown')
                sheet_row = row_idx + 1
                assign_sheet_col = assign_col + 1
                value = f"r{sheet_row}c{assign_sheet_col}"

                if value in seen_values:
                    continue
                seen_values.add(value)

                label = f"{squad} \u2013 {role}"
                if len(label) > 100:
                    label = label[:97] + '...'

                slots.append({
                    'label': label,
                    'row': sheet_row,
                    'col': assign_sheet_col,
                    'squad': squad,
                    'role': role,
                    'value': value,
                })

            elif _is_squad_header(cell):
                squad_per_col[col_idx] = cell

    if not slots:
        raise ValueError(
            "No available slots found.\n\n"
            "The bot looks for cells containing **`<Insert Name>`** to identify open slots.\n"
            "Make sure your sheet uses that exact text for unfilled positions."
        )

    return {
        'operation_name': operation_name,
        'slots': slots,
        'sheet_id': sheet_id,
        # No column indices for visual ORBAT sheets
        'squad_col': None,
        'role_col': None,
        'status_col': None,
        'assigned_col': None,
    }


def _orbat_load_all(all_values: list, operation_name: str, sheet_id: str) -> dict:
    """
    Parse a visual ORBAT sheet and return ALL slots (filled + open).
    Each slot includes 'assigned_to' and 'col_idx' for the 2-column embed layout.
    Used by load_all_slots() for /post-orbat.
    """
    num_cols = max(len(row) for row in all_values) if all_values else 0
    squad_per_col: dict[int, str] = {}
    seen_values: set = set()
    slots = []

    for row_idx, row in enumerate(all_values):
        for col_idx in range(num_cols):
            cell = row[col_idx].strip() if col_idx < len(row) else ''
            if not cell:
                continue

            if _is_slot_entry(cell):
                role = _extract_role(cell)
                squad = squad_per_col.get(col_idx, 'Unknown')
                sheet_row = row_idx + 1
                assigned_to = None
                assign_col = None

                for search_col in range(col_idx, min(col_idx + 5, num_cols)):
                    search_cell = row[search_col].strip() if search_col < len(row) else ''
                    if search_col > col_idx and _is_slot_entry(search_cell):
                        break
                    if _is_available(search_cell):
                        assign_col = search_col
                        break
                    name = _extract_assigned_name(search_cell)
                    if name:
                        assigned_to = name
                        assign_col = search_col
                        break

                if assign_col is None:
                    continue

                assign_sheet_col = assign_col + 1
                value = f"r{sheet_row}c{assign_sheet_col}"
                if value in seen_values:
                    continue
                seen_values.add(value)

                slots.append({
                    'squad': squad,
                    'role': role,
                    'row': sheet_row,
                    'assigned_to': assigned_to,
                    'col_idx': col_idx,
                })

            elif _is_squad_header(cell):
                squad_per_col[col_idx] = cell

    return {
        'operation_name': operation_name,
        'sheet_id': sheet_id,
        'slots': slots,
    }


# ---------------------------------------------------------------------------
# Tabular-format parsers (original logic)
# ---------------------------------------------------------------------------

def _tabular_load_available(all_values: list, operation_name: str, sheet_id: str) -> dict:
    """Parse a tabular sheet (header row with Squad/Role/Status/Assigned columns)."""
    squad_keywords = {'squad', 'unit', 'element', 'group', 'platoon', 'team', 'section', 'callsign'}
    role_keywords = {'role', 'position', 'slot', 'job', 'rank', 'billet'}
    status_keywords = {'status', 'available', 'state'}
    assigned_keywords = {'assigned', 'member', 'player', 'name', 'pilot', 'operator'}

    squad_col = role_col = status_col = assigned_col = None
    header_row_idx = None

    for i, row in enumerate(all_values):
        normalized = [
            cell.lower().replace(' ', '').replace('_', '').replace('/', '').replace('-', '')
            for cell in row
        ]
        sq_cols = [j for j, c in enumerate(normalized) if any(kw in c for kw in squad_keywords)]
        rl_cols = [j for j, c in enumerate(normalized) if any(kw in c for kw in role_keywords)]

        if sq_cols and rl_cols:
            header_row_idx = i
            squad_col = sq_cols[0]
            role_col = rl_cols[0]
            st_cols = [j for j, c in enumerate(normalized) if any(kw in c for kw in status_keywords)]
            as_cols = [j for j, c in enumerate(normalized) if any(kw in c for kw in assigned_keywords)]
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

        if assigned_col is not None and len(row) > assigned_col and row[assigned_col].strip():
            continue
        if status_col is not None and len(row) > status_col:
            status = row[status_col].strip().lower()
            if status and status not in ('available', 'open', 'free', 'yes', ''):
                continue

        label = f"{squad} \u2013 {role}"
        if len(label) > 100:
            label = label[:97] + '...'

        slots.append({
            'label': label,
            'row': i,
            'squad': squad,
            'role': role,
            'value': f"row_{i}",
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


def _tabular_load_all(all_values: list, operation_name: str, sheet_id: str) -> dict:
    """Load all slots (including assigned) from a tabular sheet."""
    squad_keywords = {'squad', 'unit', 'element', 'group', 'platoon', 'team', 'section', 'callsign'}
    role_keywords = {'role', 'position', 'slot', 'job', 'rank', 'billet'}
    assigned_keywords = {'assigned', 'member', 'player', 'name', 'pilot', 'operator'}

    squad_col = role_col = assigned_col = None
    header_row_idx = None

    for i, row in enumerate(all_values):
        normalized = [
            cell.lower().replace(' ', '').replace('_', '').replace('/', '').replace('-', '')
            for cell in row
        ]
        sq_cols = [j for j, c in enumerate(normalized) if any(kw in c for kw in squad_keywords)]
        rl_cols = [j for j, c in enumerate(normalized) if any(kw in c for kw in role_keywords)]
        if sq_cols and rl_cols:
            header_row_idx = i
            squad_col = sq_cols[0]
            role_col = rl_cols[0]
            as_cols = [j for j, c in enumerate(normalized) if any(kw in c for kw in assigned_keywords)]
            if as_cols:
                assigned_col = as_cols[0]
            break

    if header_row_idx is None:
        raise ValueError("Could not find slot columns in this sheet.")

    slots = []
    for i, row in enumerate(all_values[header_row_idx + 1:], start=header_row_idx + 2):
        if len(row) <= max(squad_col, role_col):
            continue
        squad = row[squad_col].strip()
        role = row[role_col].strip()
        if not squad or not role:
            continue
        assigned_to = None
        if assigned_col is not None and len(row) > assigned_col:
            assigned_to = row[assigned_col].strip() or None
        slots.append({
            'squad': squad,
            'role': role,
            'row': i,
            'assigned_to': assigned_to,
            'col_idx': 0,  # tabular sheets have no meaningful column split
        })

    return {
        'operation_name': operation_name,
        'sheet_id': sheet_id,
        'slots': slots,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_slots(sheet_url: str) -> dict:
    """
    Load available slots from a Google Sheet.

    Supports two layouts:
    - Visual ORBAT format: numbered slots with '<Insert Name>' assignment cells
    - Tabular format: header row with Squad / Role / Status / Assigned To columns
    """
    client = get_client()
    sheet_id = extract_sheet_id(sheet_url)
    spreadsheet = client.open_by_key(sheet_id)
    worksheet = spreadsheet.sheet1
    operation_name = spreadsheet.title
    all_values = worksheet.get_all_values()

    if not all_values:
        raise ValueError("The sheet appears to be empty.")

    if _detect_orbat_format(all_values):
        return _orbat_load_available(all_values, operation_name, sheet_id)
    return _tabular_load_available(all_values, operation_name, sheet_id)


def load_all_slots(sheet_url: str) -> dict:
    """
    Load ALL slots from a Google Sheet — including already-assigned ones.
    Each slot has 'assigned_to' (str or None) and 'col_idx' for layout.
    Used to build the live ORBAT display.
    """
    client = get_client()
    sheet_id = extract_sheet_id(sheet_url)
    spreadsheet = client.open_by_key(sheet_id)
    worksheet = spreadsheet.sheet1
    operation_name = spreadsheet.title
    all_values = worksheet.get_all_values()

    if not all_values:
        raise ValueError("The sheet appears to be empty.")

    if _detect_orbat_format(all_values):
        return _orbat_load_all(all_values, operation_name, sheet_id)
    return _tabular_load_all(all_values, operation_name, sheet_id)


def assign_slot(
    sheet_id: str,
    row: int,
    member_name: str,
    assigned_col: Optional[int],
    status_col: Optional[int],
    cell_col: Optional[int] = None,
    unit_role: Optional[str] = None,
):
    """
    Write an approved slot assignment back to the sheet.

    - Tabular format: updates the 'Assigned To' column (and optionally 'Status').
    - ORBAT visual format: replaces '[] <Insert Name>' in the specific cell with
      '[unit_role] member_name' (or '[] member_name' if no unit role).
    """
    client = get_client()
    spreadsheet = client.open_by_key(sheet_id)
    worksheet = spreadsheet.sheet1

    if assigned_col is not None:
        # Tabular format
        worksheet.update_cell(row, assigned_col + 1, member_name)
        if status_col is not None:
            worksheet.update_cell(row, status_col + 1, 'Assigned')
    elif cell_col is not None:
        # ORBAT visual format — update the specific assignment cell
        current = worksheet.cell(row, cell_col).value or ''
        tag = f'[{unit_role}]' if unit_role else '[]'
        new_value = re.sub(r'\[\]', tag, current, count=1)
        new_value = re.sub(r'<Insert Name>', member_name, new_value, flags=re.IGNORECASE)
        worksheet.update_cell(row, cell_col, new_value)
