from fastapi import APIRouter, HTTPException

from utils import database
from web.models import OrbatResponse, OrbatSquad, SlotResponse, OperationResponse

router = APIRouter(prefix='/api/orbat', tags=['orbat'])


@router.get('/{operation_id}', response_model=OrbatResponse)
async def get_orbat(operation_id: int):
    """Public read-only ORBAT view — no auth required."""
    op = await database.get_operation_by_id(operation_id)
    if not op:
        raise HTTPException(404, 'Operation not found')

    all_slots = await database.get_orbat_slots(operation_id)
    pending_ids = set(await database.get_pending_slot_ids(operation_id))

    squad_map: dict[int, OrbatSquad] = {}
    for row in all_slots:
        sq_id = row['squad_id']
        if sq_id not in squad_map:
            squad_map[sq_id] = OrbatSquad(
                id=sq_id,
                name=row['squad_name'],
                color=row['squad_color'],
                display_order=row['squad_display_order'],
                slots=[],
            )

        if row['assigned_to_member_id']:
            status = 'filled'
        elif row['id'] in pending_ids:
            status = 'pending'
        else:
            status = 'available'

        # Count pending requests for this slot
        pending_count = 0
        if row['id'] in pending_ids:
            reqs = await database.get_competing_requests(operation_id, row['id'], -1)
            pending_count = len(reqs) + (1 if status == 'pending' else 0)

        squad_map[sq_id].slots.append(SlotResponse(
            id=row['id'],
            squad_id=sq_id,
            role_name=row['role_name'],
            display_order=row['display_order'],
            assigned_to_member_id=row['assigned_to_member_id'],
            assigned_to_name=row['assigned_to_name'],
            status=status,
            pending_count=pending_count,
        ))

    squads = sorted(squad_map.values(), key=lambda s: s.display_order)

    total = len(all_slots)
    filled = sum(1 for s in all_slots if s['assigned_to_member_id'])
    pending = sum(1 for s in all_slots if not s['assigned_to_member_id'] and s['id'] in pending_ids)
    open_count = total - filled - pending

    return OrbatResponse(
        operation=OperationResponse(
            id=op['id'], guild_id=op['guild_id'], name=op['name'],
            description=op.get('description'), is_active=op['is_active'],
            event_time=op.get('event_time'), created_at=op.get('created_at'),
        ),
        squads=squads,
        total_slots=total,
        filled_slots=filled,
        pending_slots=pending,
        open_slots=open_count,
    )


@router.get('/guild/{guild_id}')
async def get_active_orbat(guild_id: str):
    """Get the active operation's ORBAT for a guild."""
    op = await database.get_active_operation(guild_id)
    if not op:
        raise HTTPException(404, 'No active operation for this guild')
    return await get_orbat(op['id'])
