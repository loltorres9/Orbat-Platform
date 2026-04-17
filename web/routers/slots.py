import asyncio

import discord
from fastapi import APIRouter, Depends, HTTPException, Request

from utils import database
from web.dependencies import get_current_user, check_guild_admin, check_guild_access
from web.models import SlotCreate, SlotUpdate, SlotRequestCreate

router = APIRouter(prefix='/api', tags=['slots'])


@router.get('/squads/{squad_id}/slots')
async def list_slots(squad_id: int, user: dict = Depends(get_current_user)):
    slots = await database.get_slots_for_squad(squad_id)
    return [dict(s) for s in slots]


@router.post('/squads/{squad_id}/slots')
async def create_slot(squad_id: int, body: SlotCreate,
                      user: dict = Depends(get_current_user)):
    squad = await database.get_squad(squad_id)
    if not squad:
        raise HTTPException(404, 'Squad not found')
    op = await database.get_operation_by_id(squad['operation_id'])
    if not check_guild_admin(user, op['guild_id']):
        raise HTTPException(403, 'Admin permissions required')

    slot_id = await database.create_slot(squad_id, body.role_name, body.display_order)
    return {'id': slot_id}


@router.put('/slots/{slot_id}')
async def update_slot(slot_id: int, body: SlotUpdate,
                      user: dict = Depends(get_current_user)):
    slot = await database.get_slot_with_squad(slot_id)
    if not slot:
        raise HTTPException(404, 'Slot not found')
    op = await database.get_operation_by_id(slot['operation_id'])
    if not check_guild_admin(user, op['guild_id']):
        raise HTTPException(403, 'Admin permissions required')

    await database.update_slot(slot_id, role_name=body.role_name, display_order=body.display_order)
    return {'ok': True}


@router.delete('/slots/{slot_id}')
async def delete_slot(slot_id: int, user: dict = Depends(get_current_user)):
    slot = await database.get_slot_with_squad(slot_id)
    if not slot:
        raise HTTPException(404, 'Slot not found')
    op = await database.get_operation_by_id(slot['operation_id'])
    if not check_guild_admin(user, op['guild_id']):
        raise HTTPException(403, 'Admin permissions required')

    await database.delete_slot(slot_id)
    return {'ok': True}


@router.post('/slots/{slot_id}/request')
async def request_slot(slot_id: int, body: SlotRequestCreate, request: Request,
                       user: dict = Depends(get_current_user)):
    """Submit a slot request from the web UI."""
    slot = await database.get_slot_with_squad(slot_id)
    if not slot:
        raise HTTPException(404, 'Slot not found')

    op = await database.get_operation_by_id(slot['operation_id'])
    if not op:
        raise HTTPException(404, 'Operation not found')

    if not check_guild_access(user, body.guild_id):
        raise HTTPException(403, 'You must be a member of this server')

    if slot['assigned_to_member_id']:
        raise HTTPException(409, 'Slot is already filled')

    existing = await database.get_member_active_request(
        body.guild_id, op['id'], user['id'],
    )
    if existing:
        raise HTTPException(409, f"You already have a {existing['status']} request for {existing['slot_label']}")

    slot_label = f"{slot['squad_name']} \u2013 {slot['role_name']}"
    request_id = await database.create_request(
        guild_id=body.guild_id,
        operation_id=op['id'],
        member_id=user['id'],
        member_name=user['username'],
        slot_label=slot_label,
        slot_id=slot_id,
    )

    # Post approval message to Discord if bot is available
    bot = getattr(request.app.state, 'bot', None)
    if bot:
        asyncio.create_task(_post_approval_to_discord(bot, body.guild_id, op, request_id, user, slot_label))

    return {'id': request_id, 'slot_label': slot_label}


async def _post_approval_to_discord(bot, guild_id: str, op, request_id: int,
                                    user: dict, slot_label: str):
    """Post the approval embed to #slot-approvals in Discord."""
    try:
        guild = bot.get_guild(int(guild_id))
        if not guild:
            return

        channel = discord.utils.get(guild.text_channels, name='slot-approvals')
        if not channel:
            try:
                channel = await guild.create_text_channel(
                    'slot-approvals',
                    topic='Slot approval requests for Arma 3 operations',
                )
            except discord.Forbidden:
                return

        embed = discord.Embed(
            description=(
                f"**{op['name']}**\n"
                f"<@{user['id']}> → **{slot_label}**\n"
                f"*(submitted via web)*"
            ),
            color=discord.Color.yellow(),
        )
        embed.set_footer(text=f"Request ID: {request_id}")
        embed.timestamp = discord.utils.utcnow()

        from cogs.slots import ApprovalView
        view = ApprovalView(request_id=request_id, bot=bot)
        msg = await channel.send(embed=embed, view=view)
        bot.add_view(view)

        await database.update_request_message(request_id, str(msg.id), str(channel.id))
    except Exception:
        pass
