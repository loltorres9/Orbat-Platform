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

# Matches "1. Squad Leader" or "1- Squad Leader" but NOT "1-1 Rangers" (digit after hyphen)
_SLOT_PREFIX = re.compile(r'^\d+[.\-](?!\d)\s*')

# Radio frequency cells like "152 CHN : 1" or "343 CHN:9"
_RADIO_FREQ = re.compile(r'\d{3}\s*CHN', re.IGNORECASE)


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


def _is_slot_entry(cell: str) -> bool:
    """Cell starts with a number like '1.' or '1-'."""
    return bool(_SLOT_PREFIX.match(cell.strip()))


def _is_available(cell: str) -> bool:
    """Slot is available if it contains <Insert Name>."""
    return '<insert name>' in cell.lower()


def _extract_role(cell: str) -> str:
    """
    From "3. Team Leader Alpha - [] <Insert Name>"
    returns "Team Leader Alpha".
    """
    # Remove leading number
    role = _SLOT_PREFIX.sub('', cell.strip())
    # Remove " - [tag] anything" suffix
    role = re.sub(r'\s*[-–]\s*\[.*', '', role)
    return role.strip()


def _is_squad_header(cell: str) -> bool:
    """
    A squad header is a non-empty cell that is NOT a slot entry,
    NOT a radio frequency, and contains at least one letter.
    """
    cell = cell.strip()
    if not cell:
        return False
    if _is_slot_entry(cell):
        return False
    if _RADIO_FREQ.search(cell):
        return False
    if not re.search(r'[a-zA-Z]', cell):
        return False
    # Skip short labels like column headers ("Slots:", "Net", etc.)
    if len(cell) < 4:
        return False
    # Skip announcement sentences — squad headers don't end with punctuation
    if cell.endswith('.') or cell.endswith('!') or cell.endswith('?'):
        return False
    # Assignment marker cells (e.g. "[] <Insert Name>") are not squad headers
    if _is_available(cell):
        return False
    return True


def load_slots(sheet_url: str) -> dict:
    """
    Parse an Arma 3 ORBAT Google Sheet.

    Supports two layouts:
    - Single-cell: "3. Team Leader Alpha - [] <Insert Name>" (all in one cell)
    - Multi-cell:  "3. Team Leader Alpha" | ... | "[] <Insert Name>" (split across columns)

    For multi-cell layouts, when a slot entry is found the code searches up to 5
    columns to the right in the same row for the <Insert Name> marker.  The
    assignment is written to whichever cell contains <Insert Name>.

    Squad headers are inferred from the nearest non-slot cell above in the same column.
    """
    client = get_client()
    sheet_id = extract_sheet_id(sheet_url)
    spreadsheet = client.open_by_key(sheet_id)
    worksheet = spreadsheet.sheet1
    operation_name = spreadsheet.title

    all_values = worksheet.get_all_values()
    if not all_values:
        raise ValueError("The sheet appears to be empty.")

    num_cols = max(len(row) for row in all_values)

    # Track the most recent squad header seen in each column
    squad_per_col: dict[int, str] = {}
    seen_values: set = set()
    slots = []

    for row_idx, row in enumerate(all_values):
        for col_idx in range(num_cols):
            cell = row[col_idx].strip() if col_idx < len(row) else ''
            if not cell:
                continue

            if _is_slot_entry(cell):
                # Find the cell containing <Insert Name> — may be this cell or
                # up to 4 columns to the right (multi-cell ORBAT layouts).
                # Stop early if another slot entry is encountered: that cell belongs
                # to a different squad's column and we must not steal its assignment.
                assign_col = None
                for search_col in range(col_idx, min(col_idx + 5, num_cols)):
                    search_cell = row[search_col].strip() if search_col < len(row) else ''
                    if _is_available(search_cell):
                        assign_col = search_col
                        break
                    if search_col > col_idx and _is_slot_entry(search_cell):
                        break  # crossed into another slot — stop

                if assign_col is not None:
                    role = _extract_role(cell)
                    squad = squad_per_col.get(col_idx, 'Unknown')
                    label = f"{squad} \u2013 {role}"
                    if len(label) > 100:
                        label = label[:97] + '...'

                    sheet_row = row_idx + 1         # 1-indexed
                    assign_sheet_col = assign_col + 1  # 1-indexed
                    value = f"r{sheet_row}c{assign_sheet_col}"

                    if value in seen_values:
                        continue  # same cell already claimed — skip duplicate
                    seen_values.add(value)

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
        # These are not used for ORBAT-format sheets (per-cell updates instead)
        'squad_col': None,
        'role_col': None,
        'status_col': None,
        'assigned_col': None,
    }


def load_all_slots(sheet_url: str) -> dict:
    """
    Load ALL slots from an ORBAT sheet — including already-assigned ones.
    Each slot has an 'assigned_to' field (str or None).
    Used to build the live ORBAT display.

    A slot is considered filled when its assignment cell contains '[]' followed
    by a non-empty name that is not '<Insert Name>'.
    """
    client = get_client()
    sheet_id = extract_sheet_id(sheet_url)
    spreadsheet = client.open_by_key(sheet_id)
    worksheet = spreadsheet.sheet1
    operation_name = spreadsheet.title
    all_values = worksheet.get_all_values()
    if not all_values:
        raise ValueError("The sheet appears to be empty.")

    num_cols = max(len(row) for row in all_values)
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
                        break  # crossed into another slot's column
                    if _is_available(search_cell):
                        assign_col = search_col
                        break
                    # Filled with [] prefix (bot-assigned or manually in same format)
                    filled = re.search(r'\[\]\s*(.+)', search_cell)
                    if filled:
                        name = filled.group(1).strip()
                        if name and '<insert name>' not in name.lower():
                            assigned_to = name
                            assign_col = search_col
                            break
                    # Single-cell filled: "1. Role - [TAG] Name" where name is not <Insert Name>
                    tagged = re.search(r'-\s*\[.*?\]\s*(.+)', search_cell)
                    if tagged:
                        name = tagged.group(1).strip()
                        if name and '<insert name>' not in name.lower():
                            assigned_to = name
                            assign_col = search_col
                            break
                    # Manually filled: plain name in a cell to the right (no [] prefix)
                    if search_col > col_idx and search_cell and not _RADIO_FREQ.search(search_cell):
                        assigned_to = search_cell
                        assign_col = search_col
                        break

                assign_sheet_col = (assign_col + 1) if assign_col is not None else None
                value = (
                    f"r{sheet_row}c{assign_sheet_col}"
                    if assign_sheet_col else f"r{sheet_row}"
                )
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


def clear_slot(sheet_id: str, row: int, col: int, member_name: str):
    """
    Reverse an assignment: restore the cell to '[] <Insert Name>'.

    Tries to surgically replace just the member name after '[]'; falls back
    to a plain text replacement; and as a last resort rewrites the cell as
    '[] <Insert Name>'.
    """
    client = get_client()
    spreadsheet = client.open_by_key(sheet_id)
    worksheet = spreadsheet.sheet1

    current = worksheet.cell(row, col).value or ''
    # Replace "[] MemberName" → "[] <Insert Name>"
    new_value = re.sub(
        r'(\[\]\s*)' + re.escape(member_name),
        r'\g<1><Insert Name>',
        current,
        flags=re.IGNORECASE,
    )
    if new_value == current:
        # Fallback: replace the name anywhere in the cell
        new_value = re.sub(re.escape(member_name), '<Insert Name>', current, flags=re.IGNORECASE)
    if new_value == current:
        # Last resort: reset the cell entirely
        new_value = '[] <Insert Name>'
    worksheet.update_cell(row, col, new_value)


def assign_slot(sheet_id: str, row: int, col: int, member_name: str, unit_role: str = None):
    """
    Replace '<Insert Name>' with the member's name and, if a unit_role is
    provided, fill the [] tag with the group name.

    e.g. "[] <Insert Name>"  -> "[2nd USC] MemberName"
    or   "3. Role - [] <Insert Name>" -> "3. Role - [2nd USC] MemberName"
    """
    client = get_client()
    spreadsheet = client.open_by_key(sheet_id)
    worksheet = spreadsheet.sheet1

    current = worksheet.cell(row, col).value or ''
    new_value = re.sub(r'<Insert Name>', member_name, current, flags=re.IGNORECASE)
    if unit_role:
        new_value = re.sub(r'\[\]', f'[{unit_role}]', new_value, count=1)
    worksheet.update_cell(row, col, new_value)
