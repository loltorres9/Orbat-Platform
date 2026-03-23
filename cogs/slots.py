import asyncio
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils import database, sheets

APPROVAL_CHANNEL_NAME = 'slot-approvals'

# Roles that gate who can approve/deny a request. A request submitted by a
# member with one of these roles can only be actioned by someone who shares
# that same role (or has manage_guild / administrator permissions).
UNIT_ROLES = {'2nd USC', 'CNTO', 'PXG', 'TFP'}


def _get_unit_role(member: discord.Member) -> Optional[str]:
    """Return the first UNIT_ROLES role the member has, or None."""
    for role in member.roles:
        if role.name in UNIT_ROLES:
            return role.name
    return None


def _build_orbat_embed(operation_name: str, all_slots: list, pending_rows: set) -> discord.Embed:
    """Build a live ORBAT embed grouped by squad."""
    filled = sum(1 for s in all_slots if s['assigned_to'])
    pending = sum(1 for s in all_slots if not s['assigned_to'] and s['row'] in pending_rows)
    open_ = sum(1 for s in all_slots if not s['assigned_to'] and s['row'] not in pending_rows)
    total = len(all_slots)

    embed = discord.Embed(
        title=f"🗺️ ORBAT — {operation_name}",
        description=(
            f"🟢 **{open_}** open  ·  🟡 **{pending}** pending  ·  🔴 **{filled}/{total}** filled"
        ),
        color=discord.Color.dark_blue(),
    )
    embed.timestamp = discord.utils.utcnow()
    embed.set_footer(text="Last updated")

    squads: dict[str, list] = {}
    for slot in all_slots:
        squads.setdefault(slot['squad'], []).append(slot)

    for squad_name, slots in list(squads.items())[:25]:  # Discord max 25 fields
        lines = []
        for slot in slots:
            if slot['assigned_to']:
                lines.append(f"🔴 {slot['role']} — {slot['assigned_to']}")
            elif slot['row'] in pending_rows:
                lines.append(f"🟡 {slot['role']} *(pending)*")
            else:
                lines.append(f"🟢 {slot['role']}")
        value = '\n'.join(lines)
        if len(value) > 1024:
            value = value[:1021] + '...'
        embed.add_field(name=squad_name, value=value, inline=True)

    return embed


async def _update_orbat(bot: commands.Bot, guild: discord.Guild, op):
    """Silently refresh the live ORBAT message for this guild, if one exists."""
    stored = await database.get_orbat_message(str(guild.id))
    if not stored:
        return
    channel = guild.get_channel(int(stored['channel_id']))
    if not channel:
        return
    try:
        msg = await channel.fetch_message(int(stored['message_id']))
    except (discord.NotFound, discord.Forbidden):
        return
    try:
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, sheets.load_all_slots, op['sheet_url'])
    except Exception:
        return
    pending_rows = set(await database.get_pending_slots(op['id']))
    embed = _build_orbat_embed(data['operation_name'], data['slots'], pending_rows)
    try:
        await msg.edit(embed=embed)
    except (discord.NotFound, discord.Forbidden):
        pass


def _can_action_request(approver: discord.Member, unit_role: Optional[str]) -> bool:
    """
    Returns True if *approver* is allowed to approve/deny a request that
    belongs to *unit_role*.

    Rules:
    - Admins (manage_guild or administrator) can always action any request.
    - Otherwise the approver must share the same unit role as the requester.
    - If the requester has no unit role, any Unit Leader / admin can action it.
    """
    perms = approver.guild_permissions
    if perms.manage_guild or perms.administrator:
        return True
    if unit_role is None:
        return True
    return any(r.name == unit_role for r in approver.roles)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_select_menus(
    slots: list,
    pending_rows: set,
    approved_rows: set,
) -> list[discord.ui.Select]:
    """
    Split slots into Discord Select Menus (max 25 options each, max 5 menus).

    Slots are grouped by squad name where possible. If there are more than 5
    squads the slots are chunked numerically instead.
    """
    # Group by squad, preserving insertion order
    squads: dict[str, list] = {}
    for slot in slots:
        squads.setdefault(slot['squad'], []).append(slot)

    if len(squads) <= 5:
        groups = list(squads.items())
    else:
        flat = [s for group in squads.values() for s in group]
        groups = []
        for i in range(0, len(flat), 25):
            chunk = flat[i : i + 25]
            first_squad = chunk[0]['squad']
            last_squad = chunk[-1]['squad']
            if first_squad == last_squad:
                label = first_squad
            else:
                label = f"{first_squad[:30]} … {last_squad[:30]}"
            groups.append((label, chunk))
        groups = groups[:5]

    menus = []
    for group_name, group_slots in groups:
        options = []
        for slot in group_slots[:25]:
            if slot['row'] in approved_rows:
                continue
            emoji = '🟡' if slot['row'] in pending_rows else '🟢'
            status = 'Pending approval' if slot['row'] in pending_rows else 'Available'
            # Show full "Squad – Role" as label so squad context is always visible
            full_label = f"{slot['squad']} – {slot['role']}"
            options.append(
                discord.SelectOption(
                    label=full_label[:100],
                    value=slot['value'],
                    description=status,
                    emoji=emoji,
                )
            )

        if options:
            select = discord.ui.Select(
                placeholder=f"{group_name}"[:150],
                options=options,
                min_values=1,
                max_values=1,
            )
            menus.append(select)

    return menus


# ---------------------------------------------------------------------------
# Slot request view (ephemeral, shown only to the requesting member)
# ---------------------------------------------------------------------------

class SlotRequestView(discord.ui.View):
    def __init__(
        self,
        slots: list,
        operation_id: int,
        pending_rows: set,
        approved_rows: set,
        bot: commands.Bot,
    ):
        super().__init__(timeout=300)
        self.slots_by_value = {s['value']: s for s in slots}
        self.operation_id = operation_id
        self.bot = bot

        for menu in _build_select_menus(slots, pending_rows, approved_rows):
            menu.callback = self._select_callback
            self.add_item(menu)

    async def _select_callback(self, interaction: discord.Interaction):
        selected_value = interaction.data['values'][0]
        slot = self.slots_by_value.get(selected_value)

        if not slot:
            await interaction.response.send_message(
                "❌ Slot not found. Please try `/request-slot` again.", ephemeral=True
            )
            return

        # Re-check availability at the moment of selection
        pending = set(await database.get_pending_slots(self.operation_id))
        approved = set(await database.get_approved_slots(self.operation_id))

        if slot['row'] in approved:
            await interaction.response.send_message(
                "❌ That slot was just filled. Please choose another.", ephemeral=True
            )
            return

        if slot['row'] in pending:
            await interaction.response.send_message(
                "⏳ That slot already has a pending request. Please choose another.", ephemeral=True
            )
            return

        existing = await database.get_member_active_request(
            str(interaction.guild_id), self.operation_id, str(interaction.user.id)
        )
        if existing:
            await interaction.response.send_message(
                f"⚠️ You already have a **{existing['status']}** request for **{existing['slot_label']}**.\n"
                "You can only hold one slot per operation.",
                ephemeral=True,
            )
            return

        unit_role = _get_unit_role(interaction.user)
        request_id = await database.create_request(
            guild_id=str(interaction.guild_id),
            operation_id=self.operation_id,
            member_id=str(interaction.user.id),
            member_name=interaction.user.display_name,
            slot_label=slot['label'],
            sheet_row=slot['row'],
            sheet_col=slot.get('col'),
            unit_role=unit_role,
        )

        await interaction.response.send_message(
            f"✅ Request submitted for **{slot['label']}**!\n"
            "Status: ⏳ Pending approval — you'll receive a DM when an admin reviews it.",
            ephemeral=True,
        )

        # Post to #slot-approvals (create channel if it doesn't exist)
        approval_channel = discord.utils.get(
            interaction.guild.text_channels, name=APPROVAL_CHANNEL_NAME
        )
        if not approval_channel:
            try:
                approval_channel = await interaction.guild.create_text_channel(
                    APPROVAL_CHANNEL_NAME,
                    topic='Slot approval requests for Arma 3 operations',
                )
            except discord.Forbidden:
                return  # Can't create channel; skip posting

        op = await database.get_active_operation(str(interaction.guild_id))
        embed = discord.Embed(title='📋 Slot Request', color=discord.Color.yellow())
        embed.add_field(name='Member', value=interaction.user.mention, inline=True)
        embed.add_field(name='Requested Slot', value=f"**{slot['label']}**", inline=True)
        embed.add_field(name='Operation', value=op['name'] if op else 'Unknown', inline=False)
        if unit_role:
            embed.add_field(name='Unit', value=unit_role, inline=True)
        embed.set_footer(text=f"Request ID: {request_id}")
        embed.timestamp = discord.utils.utcnow()

        approval_view = ApprovalView(request_id=request_id, bot=self.bot)
        msg = await approval_channel.send(embed=embed, view=approval_view)
        self.bot.add_view(approval_view)  # keep it alive across restarts

        await database.update_request_message(
            request_id, str(msg.id), str(approval_channel.id)
        )

        # DM the requesting member
        try:
            await interaction.user.send(
                f"✅ **Slot Request Submitted**\n"
                f"Operation: **{op['name'] if op else 'Unknown'}**\n"
                f"Slot: **{slot['label']}**\n"
                f"Status: ⏳ Pending — an admin will review your request soon."
            )
        except discord.Forbidden:
            pass


# ---------------------------------------------------------------------------
# Denial modal
# ---------------------------------------------------------------------------

class DenialModal(discord.ui.Modal, title='Deny Slot Request'):
    reason = discord.ui.TextInput(
        label='Reason for denial (optional)',
        placeholder='e.g., Slot not available, duplicate request…',
        required=False,
        max_length=200,
    )

    def __init__(
        self,
        request_id: int,
        bot: commands.Bot,
        message_id: int,
        channel_id: int,
        requester_id: int,
    ):
        super().__init__()
        self.request_id = request_id
        self.bot = bot
        self.message_id = message_id
        self.channel_id = channel_id
        self.requester_id = requester_id

    async def on_submit(self, interaction: discord.Interaction):
        reason = self.reason.value.strip() or 'No reason provided'
        await database.deny_request(self.request_id, interaction.user.display_name, reason)

        # Edit the approval embed to reflect the denial
        try:
            channel = self.bot.get_channel(self.channel_id)
            if channel:
                msg = await channel.fetch_message(self.message_id)
                embed = msg.embeds[0]
                embed.color = discord.Color.red()
                embed.add_field(
                    name='❌ Denied',
                    value=f"By {interaction.user.mention}\nReason: {reason}",
                    inline=False,
                )
                await msg.edit(embed=embed, view=None)
        except (discord.NotFound, discord.Forbidden):
            pass

        await interaction.response.send_message("❌ Request denied.", ephemeral=True)

        # DM the member
        try:
            member = await interaction.guild.fetch_member(self.requester_id)
            req = await database.get_request_by_id(self.request_id)
            await member.send(
                f"❌ **Slot Request Denied**\n"
                f"Slot: **{req['slot_label']}**\n"
                f"Reason: {reason}\n\n"
                f"You can request a different slot with `/request-slot`."
            )
        except (discord.Forbidden, discord.NotFound):
            pass


# ---------------------------------------------------------------------------
# Approval view (posted to #slot-approvals, persistent via custom_id)
# ---------------------------------------------------------------------------

class ApprovalView(discord.ui.View):
    """
    Persistent approval view. custom_ids encode the request_id so the bot can
    recover them after a restart by re-adding views for all pending requests.
    """

    def __init__(self, request_id: int, bot: commands.Bot):
        super().__init__(timeout=None)
        self.request_id = request_id
        self.bot = bot

        approve_btn = discord.ui.Button(
            label='Approve',
            style=discord.ButtonStyle.green,
            emoji='✅',
            custom_id=f"orbat_approve:{request_id}",
        )
        approve_btn.callback = self._approve_callback
        self.add_item(approve_btn)

        deny_btn = discord.ui.Button(
            label='Deny',
            style=discord.ButtonStyle.red,
            emoji='❌',
            custom_id=f"orbat_deny:{request_id}",
        )
        deny_btn.callback = self._deny_callback
        self.add_item(deny_btn)

    async def _approve_callback(self, interaction: discord.Interaction):
        req = await database.get_request_by_id(self.request_id)
        if not req:
            await interaction.response.send_message("❌ Request not found.", ephemeral=True)
            return

        if not _can_action_request(interaction.user, req['unit_role']):
            unit = req['unit_role'] or 'that unit'
            await interaction.response.send_message(
                f"🚫 You can only approve requests from your own unit. "
                f"This request is for **{unit}**.",
                ephemeral=True,
            )
            return

        if req['status'] != 'pending':
            await interaction.response.send_message(
                f"⚠️ This request has already been **{req['status']}**.", ephemeral=True
            )
            return

        await database.approve_request(self.request_id, interaction.user.display_name)

        # Update Google Sheet
        op = await database.get_active_operation(str(interaction.guild_id))
        if op:
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None,
                    sheets.assign_slot,
                    op['sheet_id'],
                    req['sheet_row'],
                    req['sheet_col'],
                    req['member_name'],
                )
            except Exception as e:
                await interaction.response.send_message(
                    f"⚠️ Approved in bot, but sheet update failed: `{e}`\n"
                    "Please update the sheet manually.",
                    ephemeral=True,
                )
                return

        # Update embed
        embed = interaction.message.embeds[0]
        embed.color = discord.Color.green()
        embed.add_field(
            name='✅ Approved', value=f"By {interaction.user.mention}", inline=False
        )
        await interaction.message.edit(embed=embed, view=None)
        await interaction.response.send_message(
            "✅ Request approved and sheet updated!", ephemeral=True
        )

        # DM the member
        try:
            member = await interaction.guild.fetch_member(int(req['member_id']))
            await member.send(
                f"✅ **Slot Request Approved!**\n"
                f"Operation: **{op['name'] if op else 'Unknown'}**\n"
                f"Slot: **{req['slot_label']}**\n"
                f"You're confirmed. See you on the field! 🎖️"
            )
        except (discord.Forbidden, discord.NotFound):
            pass

        # Refresh the live ORBAT board (fire-and-forget)
        if op:
            asyncio.create_task(_update_orbat(self.bot, interaction.guild, op))

    async def _deny_callback(self, interaction: discord.Interaction):
        req = await database.get_request_by_id(self.request_id)
        if not req:
            await interaction.response.send_message("❌ Request not found.", ephemeral=True)
            return

        if not _can_action_request(interaction.user, req['unit_role']):
            unit = req['unit_role'] or 'that unit'
            await interaction.response.send_message(
                f"🚫 You can only deny requests from your own unit. "
                f"This request is for **{unit}**.",
                ephemeral=True,
            )
            return

        if req['status'] != 'pending':
            await interaction.response.send_message(
                f"⚠️ This request has already been **{req['status']}**.", ephemeral=True
            )
            return

        modal = DenialModal(
            request_id=self.request_id,
            bot=self.bot,
            message_id=interaction.message.id,
            channel_id=interaction.channel_id,
            requester_id=int(req['member_id']),
        )
        await interaction.response.send_modal(modal)


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class SlotsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name='request-slot',
        description='Browse and request a slot for the current operation',
    )
    async def request_slot(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        try:
            op = await database.get_active_operation(str(interaction.guild_id))
            if not op:
                await interaction.followup.send(
                    "❌ No active operation. An admin needs to run `/setup-slots` first.",
                    ephemeral=True,
                )
                return

            existing = await database.get_member_active_request(
                str(interaction.guild_id), op['id'], str(interaction.user.id)
            )
            if existing:
                await interaction.followup.send(
                    f"⚠️ You already have a **{existing['status']}** request for **{existing['slot_label']}**.\n"
                    "You can only hold one slot per operation.",
                    ephemeral=True,
                )
                return

            try:
                loop = asyncio.get_event_loop()
                data = await asyncio.wait_for(
                    loop.run_in_executor(None, sheets.load_slots, op['sheet_url']),
                    timeout=30.0,
                )
            except asyncio.TimeoutError:
                await interaction.followup.send(
                    "❌ Timed out loading the sheet (>30 s). Check that the sheet is shared with the service account and try again.",
                    ephemeral=True,
                )
                return
            except Exception as e:
                await interaction.followup.send(
                    f"❌ Failed to load slots from sheet: `{e}`", ephemeral=True
                )
                return

            pending_rows = set(await database.get_pending_slots(op['id']))
            approved_rows = set(await database.get_approved_slots(op['id']))

            available = [s for s in data['slots'] if s['row'] not in approved_rows]
            if not available:
                await interaction.followup.send(
                    "❌ All slots are filled for this operation.", ephemeral=True
                )
                return

            open_count = sum(1 for s in available if s['row'] not in pending_rows)
            pending_count = sum(1 for s in available if s['row'] in pending_rows)

            embed = discord.Embed(
                title=f"🎖️ {data['operation_name']} — Slot Request",
                description=(
                    f"🟢 **{open_count}** open  ·  🟡 **{pending_count}** pending approval\n\n"
                    "Select your slot from the menu(s) below.\n"
                    "Pending slots are reserved until approved or denied."
                ),
                color=discord.Color.blurple(),
            )

            view = SlotRequestView(
                slots=available,
                operation_id=op['id'],
                pending_rows=pending_rows,
                approved_rows=approved_rows,
                bot=self.bot,
            )
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)

        except Exception as e:
            # Catch-all so the user always gets a response instead of "thinking…" forever
            try:
                await interaction.followup.send(
                    f"❌ Unexpected error: `{e}`", ephemeral=True
                )
            except Exception:
                pass


    @app_commands.command(
        name='cancel-request',
        description='Cancel your pending slot request for the current operation',
    )
    async def cancel_request(self, interaction: discord.Interaction):
        op = await database.get_active_operation(str(interaction.guild_id))
        if not op:
            await interaction.response.send_message(
                "❌ No active operation.", ephemeral=True
            )
            return

        existing = await database.get_member_active_request(
            str(interaction.guild_id), op['id'], str(interaction.user.id)
        )
        if not existing or existing['status'] != 'pending':
            await interaction.response.send_message(
                "⚠️ You don't have a pending request to cancel.", ephemeral=True
            )
            return

        cancelled = await database.cancel_member_request(
            str(interaction.guild_id), op['id'], str(interaction.user.id)
        )
        if cancelled:
            await interaction.response.send_message(
                f"✅ Your request for **{existing['slot_label']}** has been cancelled.\n"
                "You can request a different slot with `/request-slot`.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "❌ Could not cancel your request.", ephemeral=True
            )


    @app_commands.command(
        name='post-orbat',
        description='Post a live ORBAT board to a channel — updates automatically on approval (Admin only)',
    )
    @app_commands.describe(channel='Channel to post the ORBAT in (defaults to current channel)')
    @app_commands.default_permissions(manage_guild=True)
    async def post_orbat(
        self, interaction: discord.Interaction, channel: discord.TextChannel = None
    ):
        await interaction.response.defer(ephemeral=True)

        op = await database.get_active_operation(str(interaction.guild_id))
        if not op:
            await interaction.followup.send(
                "❌ No active operation. Run `/setup-slots` first.", ephemeral=True
            )
            return

        target = channel or interaction.channel

        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, sheets.load_all_slots, op['sheet_url'])
        except Exception as e:
            await interaction.followup.send(
                f"❌ Failed to load slots from sheet: `{e}`", ephemeral=True
            )
            return

        pending_rows = set(await database.get_pending_slots(op['id']))
        embed = _build_orbat_embed(data['operation_name'], data['slots'], pending_rows)

        msg = await target.send(embed=embed)
        await database.save_orbat_message(
            str(interaction.guild_id), str(target.id), str(msg.id)
        )

        await interaction.followup.send(
            f"✅ ORBAT posted to {target.mention}. It will update automatically when slots are approved.",
            ephemeral=True,
        )

async def setup(bot: commands.Bot):
    await bot.add_cog(SlotsCog(bot))
