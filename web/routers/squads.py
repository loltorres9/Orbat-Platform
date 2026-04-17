from fastapi import APIRouter, Depends, HTTPException

from utils import database
from web.dependencies import get_current_user, check_guild_admin
from web.models import SquadCreate, SquadUpdate

router = APIRouter(prefix='/api', tags=['squads'])


async def _check_operation_admin(operation_id: int, user: dict):
    op = await database.get_operation_by_id(operation_id)
    if not op:
        raise HTTPException(404, 'Operation not found')
    if not check_guild_admin(user, op['guild_id']):
        raise HTTPException(403, 'Admin permissions required')
    return op


@router.get('/operations/{operation_id}/squads')
async def list_squads(operation_id: int, user: dict = Depends(get_current_user)):
    squads = await database.get_squads_for_operation(operation_id)
    return [dict(sq) for sq in squads]


@router.post('/operations/{operation_id}/squads')
async def create_squad(operation_id: int, body: SquadCreate,
                       user: dict = Depends(get_current_user)):
    await _check_operation_admin(operation_id, user)
    squad_id = await database.create_squad(
        operation_id, body.name, body.color, body.display_order,
    )
    return {'id': squad_id}


@router.put('/squads/{squad_id}')
async def update_squad(squad_id: int, body: SquadUpdate,
                       user: dict = Depends(get_current_user)):
    squad = await database.get_squad(squad_id)
    if not squad:
        raise HTTPException(404, 'Squad not found')

    op = await database.get_operation_by_id(squad['operation_id'])
    if not check_guild_admin(user, op['guild_id']):
        raise HTTPException(403, 'Admin permissions required')

    await database.update_squad(
        squad_id, name=body.name, color=body.color, display_order=body.display_order,
    )
    return {'ok': True}


@router.delete('/squads/{squad_id}')
async def delete_squad(squad_id: int, user: dict = Depends(get_current_user)):
    squad = await database.get_squad(squad_id)
    if not squad:
        raise HTTPException(404, 'Squad not found')

    op = await database.get_operation_by_id(squad['operation_id'])
    if not check_guild_admin(user, op['guild_id']):
        raise HTTPException(403, 'Admin permissions required')

    await database.delete_squad(squad_id)
    return {'ok': True}
