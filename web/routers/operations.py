from fastapi import APIRouter, Depends, HTTPException

from utils import database
from web.dependencies import get_current_user, check_guild_admin
from web.models import OperationCreate, OperationUpdate, OperationResponse

router = APIRouter(prefix='/api/operations', tags=['operations'])


@router.get('/{guild_id}')
async def list_operations(guild_id: str, user: dict = Depends(get_current_user)):
    ops = await database.get_operations_for_guild(guild_id)
    return [
        OperationResponse(
            id=op['id'], guild_id=op['guild_id'], name=op['name'],
            description=op.get('description'), is_active=op['is_active'],
            event_time=op.get('event_time'), created_at=op.get('created_at'),
        )
        for op in ops
    ]


@router.post('/')
async def create_operation(body: OperationCreate, user: dict = Depends(get_current_user)):
    if not check_guild_admin(user, body.guild_id):
        raise HTTPException(403, 'Admin permissions required')

    op_id = await database.create_operation(body.guild_id, body.name, body.description)
    return {'id': op_id}


@router.put('/{operation_id}')
async def update_operation(operation_id: int, body: OperationUpdate,
                           user: dict = Depends(get_current_user)):
    op = await database.get_operation_by_id(operation_id)
    if not op:
        raise HTTPException(404, 'Operation not found')
    if not check_guild_admin(user, op['guild_id']):
        raise HTTPException(403, 'Admin permissions required')

    await database.update_operation(operation_id, name=body.name, description=body.description)
    return {'ok': True}


@router.post('/{operation_id}/activate')
async def activate_operation(operation_id: int, user: dict = Depends(get_current_user)):
    op = await database.get_operation_by_id(operation_id)
    if not op:
        raise HTTPException(404, 'Operation not found')
    if not check_guild_admin(user, op['guild_id']):
        raise HTTPException(403, 'Admin permissions required')

    await database.activate_operation(operation_id, op['guild_id'])
    return {'ok': True}


@router.delete('/{operation_id}')
async def delete_operation(operation_id: int, user: dict = Depends(get_current_user)):
    op = await database.get_operation_by_id(operation_id)
    if not op:
        raise HTTPException(404, 'Operation not found')
    if not check_guild_admin(user, op['guild_id']):
        raise HTTPException(403, 'Admin permissions required')

    await database.delete_operation(operation_id)
    return {'ok': True}
