import asyncio
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils import database

APPROVAL_CHANNEL_NAME = 'slot-approvals'

# Roles that gate who can approve/deny a request. A request submitted by a
# member with one of these roles can only be actioned by someone who shares
# that same role (or has manage_guild / administrator permissions).
UNIT_ROLES = {'2nd USC', 'CNTO', 'PXG', 'TFP', 'SKUA'}


def _get_unit_role(member: discord.Member) -> Optional[str]:
    """Return the first UNIT_ROLES role the member has, or None."""
    for role in member.roles:
        if role.name in UNIT_ROLES:
            return role.name
    return None


def _build_orbat_embed(operation_name: str, all_slots: list, pending_ids: set, event_time=None) -> discord.Embed:
    """Build a live ORBAT embed grouped by squad."""
    counted = [s for s in all_slots if s['squad_name'].lower() != 'reservists']
    filled = sum(1 for s in counted if s['assigned_to_name'])
    pending = sum(1 for s in counted if not s['assigned_to_name'] and s['id'] in pending_ids)
    open_ = sum(1 for s in counted if not s['assigned_to_name'] and s['id'] not in pending_ids)
    total = len(counted)

    event_line = (
        f"\n🕐 **Operation starts:** <t:{int(event_time.timestamp())}:F>  (<t:{int(event_time.timestamp())}:R>)"
        if event_time else ""
    )
    embed = discord.Embed(
        title=f"🗺️ ORBAT — {operation_name}",
        description=(
            f"🟢 **{open_}** open  ·  🟡 **{pending}** pending  ·  🔴 **{filled}/{total}** filled"
            f"{event_line}"
        ),
        color=discord.Color.dark_blue(),
    )
    embed.timestamp = discord.utils.utcnow()
    embed.set_footer(text="Last updated")

    squads: dict[str, list] = {}
    squad_order: dict[str, int] = {}
    for slot in all_slots:
        name = slot['squad_name']
        squads.setdefault(name, []).append(slot)
        if name not in squad_order:
            squad_order[name] = slot['squad_display_order']

    ordered = sorted(squads.keys(), key=lambda s: squad_order[s])

    def _make_value(slots: list) -> str:
        lines = []
        for slot in slots:
            if slot['assigned_to_name']:
                lines.append(f"🔴 {slot['role_name']} — {slot['assigned_to_name']}")
            elif slot['id'] in pending_ids:
                lines.append(f"🟡 {slot['role_name']} *(pending)*")
            else:
                lines.append(f"🟢 {slot['role_name']}")
        value = '\n'.join(lines)
        return value[:1021] + '...' if len(value) > 1024 else value

    if len(ordered) > 1:
        col_values = [squad_order[s] for s in ordered]
        mid = (min(col_values) + max(col_values)) / 2
        left = [s for s in ordered if squad_order[s] <= mid]
        right = [s for s in ordered if squad_order[s] > mid]
    else:
        left, right = ordered, []

    if left and right:
        max_rows = min(max(len(left), len(right)), 8)
        for i in range(max_rows):
            lname = left[i] if i < len(left) else '\u200b'
            rname = right[i] if i < len(right) else '\u200b'
            lval = _make_value(squads[lname]) if lname in squads else '\u200b'
            rval = _make_value(squads[rname]) if rname in squads else '\u200b'
            embed.add_field(name=lname, value=lval, inline=True)
            embed.add_field(name=rname, value=rval, inline=True)
            embed.add_field(name='\u200b', value='\u200b', inline=True)
    else:
        for name in ordered[:25]:
            embed.add_field(name=name, value=_make_value(squads[name]), inline=True)

    return embed


async def _update_orbat(bot: commands.Bot, guild: discord.Guild, op, raise_errors: bool = False):
    """Refresh the live ORBAT message for this guild.

    By default errors are suppressed (fire-and-forget background use).
    Pass raise_errors=True to let exceptions propagate (e.g. from /sync).
    """
    stored = await database.get_orbat_message(str(guild.id))
    if not stored:
        return

    try:
        channel = (
            guild.get_channel(int(stored['channel_id']))
            or await guild.fetch_channel(int(stored['channel_id']))
        )
    except (discord.NotFound, discord.Forbidden) as e:
        if raise_errors:
            raise RuntimeError(f"Cannot access ORBAT channel: {e}") from e
        return

    try:
        msg = await channel.fetch_message(int(stored['message_id']))
    except (discord.NotFound, discord.Forbidden) as e:
        if raise_errors:
            raise RuntimeError(
                "The stored ORBAT message no longer exists. "
                "Run `/post-orbat` in your ORBAT channel to set up a fresh one."
            ) from e
        return

    try:
        all_slots = await database.get_orbat_slots(op['id'])
        pending_ids = set(await database.get_pending_slot_ids(op['id']))
    except Exception as e:
        if raise_errors:
            raise RuntimeError(f"Failed to load ORBAT: {e}") from e
        return

    embed = _build_orbat_embed(op['name'], all_slots, pending_ids, op['event_time'])

    try:
        await msg.edit(embed=embed, view=OrbatRequestButton(bot))
    except (discord.NotFound, discord.Forbidden) as e:
        if raise_errors:
            raise RuntimeError(f"Failed to edit ORBAT message: {e}") from e


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


# ---------------------------------------------------------------------------
# Shared slot submission logic
# ---------------------------------------------------------------------------

async def _process_slot_selection(
    interaction: discord.Interaction,
    slot: dict,
    operation_id: int,
    bot: commands.Bot,
):
    """
    Validate and submit a slot request. Handles all DB writes, approval post,
    DM, and ORBAT refresh. Caller must NOT have deferred the interaction yet.
    """
    approved = set(await database.get_approved_slot_ids(operation_id))

    if slot['id'] in approved:
        await interaction.response.send_message(
            "❌ That slot was just filled. Please choose another.", ephemeral=True
        )
        return

    existing = await database.get_member_active_request(
        str(interaction.guild_id), operation_id, str(interaction.user.id)
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
        operation_id=operation_id,
        member_id=str(interaction.user.id),
        member_name=interaction.user.display_name,
        slot_label=slot['label'],
        slot_id=slot['id'],
        unit_role=unit_role,
    )

    await interaction.response.send_message(
        f"✅ Request submitted for **{slot['label']}**!\n"
        "Status: ⏳ Pending approval — you'll receive a DM when an admin reviews it.",
        ephemeral=True,
    )

    # Post to #slot-approvals
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
            return

    op = await database.get_active_operation(str(interaction.guild_id))

    # Use the unit's Discord role colour, fall back to yellow
    color = discord.Color.yellow()
    if unit_role:
        role_obj = discord.utils.get(interaction.guild.roles, name=unit_role)
        if role_obj and role_obj.color.value:
            color = role_obj.color

    op_name = op['name'] if op else 'Unknown'
    unit_line = f"  ·  **{unit_role}**" if unit_role else ""
    embed = discord.Embed(
        description=(
            f"**{op_name}**{unit_line}\n"
            f"{interaction.user.mention} → **{slot['label']}**"
        ),
        color=color,
    )
    embed.set_footer(text=f"Request ID: {request_id}")
    embed.timestamp = discord.utils.utcnow()

    approval_view = ApprovalView(request_id=request_id, bot=bot)
    try:
        msg = await approval_channel.send(embed=embed, view=approval_view)
    except Exception as e:
        # Roll back the DB record so the slot doesn't stay blocked indefinitely
        await database.deny_request(request_id, 'system', reason='Approval message failed to send')
        # Best-effort follow-up since response was already sent
        try:
            await interaction.followup.send(
                f"⚠️ Your request was received but the approval message could not be posted (`{e}`). "
                "The slot has been freed — please try requesting again.",
                ephemeral=True,
            )
        except Exception:
            pass
        return
    bot.add_view(approval_view)

    await database.update_request_message(
        request_id, str(msg.id), str(approval_channel.id)
    )

    try:
        await interaction.user.send(
            f"✅ **Slot Request Submitted**\n"
            f"Operation: **{op['name'] if op else 'Unknown'}**\n"
            f"Slot: **{slot['label']}**\n"
            f"Status: ⏳ Pending — an admin will review your request soon."
        )
    except discord.Forbidden:
        pass

    if op:
        asyncio.create_task(_update_orbat(bot, interaction.guild, op))


async def _void_approval_message(bot: commands.Bot, guild: discord.Guild, req):
    """Edit the approval message to show the request was cancelled, and disable the buttons."""
    if not req.get('approval_message_id') or not req.get('approval_channel_id'):
        return
    channel = guild.get_channel(int(req['approval_channel_id']))
    if not channel:
        return
    try:
        msg = await channel.fetch_message(int(req['approval_message_id']))
    except (discord.NotFound, discord.Forbidden):
        return

    # Rebuild the embed in grey with a cancelled notice
    original = msg.embeds[0] if msg.embeds else None
    cancelled_embed = discord.Embed(
        title='📋 Slot Request — Cancelled',
        color=discord.Color.dark_gray(),
    )
    if original:
        for field in original.fields:
            cancelled_embed.add_field(name=field.name, value=field.value, inline=field.inline)
    cancelled_embed.set_footer(text='Member cancelled their request')
    cancelled_embed.timestamp = discord.utils.utcnow()

    try:
        await msg.edit(embed=cancelled_embed, view=discord.ui.View())
    except (discord.NotFound, discord.Forbidden):
        pass


# ---------------------------------------------------------------------------
# Squad and slot selection views (two-step ephemeral flow)
# ---------------------------------------------------------------------------

class SquadSelectView(discord.ui.View):
    """Step 1: choose a squad."""

    def __init__(
        self,
        squads: dict,
        all_slots: list,
        operation_id: int,
        pending_ids: set,
        approved_ids: set,
        bot: commands.Bot,
        on_select=None,
    ):
        super().__init__(timeout=300)
        self.squads = squads
        self.all_slots = all_slots
        self.operation_id = operation_id
        self.pending_ids = pending_ids
        self.approved_ids = approved_ids
        self.bot = bot
        self.on_select = on_select

        options = []
        for squad_name, slots in squads.items():
            open_c = sum(1 for s in slots if s['id'] not in pending_ids)
            pend_c = sum(1 for s in slots if s['id'] in pending_ids)
            parts = []
            if open_c:
                parts.append(f"🟢 {open_c} open")
            if pend_c:
                parts.append(f"🟡 {pend_c} pending")
            options.append(discord.SelectOption(
                label=squad_name[:100],
                value=squad_name,
                description=' · '.join(parts)[:100] if parts else 'Available',
            ))

        select = discord.ui.Select(
            placeholder='Choose a squad…',
            options=options[:25],
            min_values=1,
            max_values=1,
        )
        select.callback = self._squad_selected
        self.add_item(select)

    async def _squad_selected(self, interaction: discord.Interaction):
        squad_name = interaction.data['values'][0]
        squad_slots = self.squads[squad_name]

        view = SlotSelectView(
            squad_name=squad_name,
            slots=squad_slots,
            all_slots=self.all_slots,
            operation_id=self.operation_id,
            pending_ids=self.pending_ids,
            approved_ids=self.approved_ids,
            bot=self.bot,
            on_select=self.on_select,
        )
        await interaction.response.edit_message(
            content=f"**{squad_name}** — select your slot:",
            view=view,
        )


class SlotSelectView(discord.ui.View):
    """Step 2: choose a slot within the selected squad."""

    def __init__(
        self,
        squad_name: str,
        slots: list,
        all_slots: list,
        operation_id: int,
        pending_ids: set,
        approved_ids: set,
        bot: commands.Bot,
        on_select=None,
    ):
        super().__init__(timeout=300)
        self.squad_name = squad_name
        self.all_slots = all_slots
        self.slots_by_value = {str(s['id']): s for s in slots}
        self.operation_id = operation_id
        self.pending_ids = pending_ids
        self.approved_ids = approved_ids
        self.bot = bot
        self.on_select = on_select

        options = []
        for slot in slots[:25]:
            emoji = '🟡' if slot['id'] in pending_ids else '🟢'
            status = 'Also requested — compete for slot' if slot['id'] in pending_ids else 'Available'
            options.append(discord.SelectOption(
                label=slot['role_name'][:100],
                value=str(slot['id']),
                description=status,
                emoji=emoji,
            ))

        select = discord.ui.Select(
            placeholder='Choose a slot…',
            options=options,
            min_values=1,
            max_values=1,
        )
        select.callback = self._slot_selected
        self.add_item(select)

        back = discord.ui.Button(label='← Back to squads', style=discord.ButtonStyle.secondary)
        back.callback = self._go_back
        self.add_item(back)

    async def _slot_selected(self, interaction: discord.Interaction):
        selected_value = interaction.data['values'][0]
        slot = self.slots_by_value.get(selected_value)
        if not slot:
            await interaction.response.send_message(
                '❌ Slot not found. Please try again.', ephemeral=True
            )
            return
        if self.on_select:
            await self.on_select(interaction, slot)
        else:
            await _process_slot_selection(interaction, slot, self.operation_id, self.bot)

    async def _go_back(self, interaction: discord.Interaction):
        pending_ids = set(await database.get_pending_slot_ids(self.operation_id))
        approved_ids = set(await database.get_approved_slot_ids(self.operation_id))
        available = [s for s in self.all_slots if s['id'] not in approved_ids]

        squads: dict = {}
        for slot in available:
            squads.setdefault(slot['squad_name'], []).append(slot)

        view = SquadSelectView(
            squads, self.all_slots, self.operation_id, pending_ids, approved_ids, self.bot,
            on_select=self.on_select,
        )
        await interaction.response.edit_message(content='Select your squad:', view=view)


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

        # Refresh the live ORBAT board (fire-and-forget)
        op = await database.get_active_operation(str(interaction.guild_id))
        if op:
            asyncio.create_task(_update_orbat(self.bot, interaction.guild, op))

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

        # Assign the slot in the database
        if req.get('slot_id'):
            assigned = await database.assign_slot_to_member(
                req['slot_id'], req['member_id'], req['member_name']
            )
            if not assigned:
                await interaction.response.send_message(
                    "❌ Slot was already filled by someone else.", ephemeral=True
                )
                return

        await database.approve_request(self.request_id, interaction.user.display_name)
        op = await database.get_active_operation(str(interaction.guild_id))

        await interaction.response.send_message(
            "✅ Request approved!", ephemeral=True
        )

        # Remove the request from #slot-approvals
        try:
            await interaction.message.delete()
        except (discord.NotFound, discord.Forbidden):
            pass

        # Post a compact record to #approval-archive (create the channel if needed)
        archive_channel = discord.utils.get(
            interaction.guild.text_channels, name='approval-archive'
        )
        if archive_channel is None:
            try:
                archive_channel = await interaction.guild.create_text_channel('approval-archive')
            except discord.Forbidden:
                archive_channel = None

        if archive_channel:
            op_name = op['name'] if op else 'Unknown'
            unit_line = f"  ·  **{req['unit_role']}**" if req['unit_role'] else ""
            archive_embed = discord.Embed(
                description=(
                    f"**{op_name}**{unit_line}\n"
                    f"<@{req['member_id']}> → **{req['slot_label']}**"
                ),
                color=discord.Color.green(),
            )
            archive_embed.add_field(
                name='✅ Approved by', value=interaction.user.mention, inline=True
            )
            archive_embed.timestamp = discord.utils.utcnow()
            await archive_channel.send(embed=archive_embed)

        # DM the approved member
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

        # Auto-deny any competing requests for the same slot
        if op and req.get('slot_id'):
            competitors = await database.get_competing_requests(
                op['id'], req['slot_id'], self.request_id
            )
            for comp in competitors:
                await database.deny_request(
                    comp['id'],
                    interaction.user.display_name,
                    reason='Slot was awarded to another member',
                )
                # Update their approval message
                if comp['approval_channel_id'] and comp['approval_message_id']:
                    try:
                        ch = interaction.guild.get_channel(int(comp['approval_channel_id']))
                        if ch:
                            msg = await ch.fetch_message(int(comp['approval_message_id']))
                            comp_embed = msg.embeds[0].copy() if msg.embeds else discord.Embed()
                            comp_embed.color = discord.Color.dark_gray()
                            comp_embed.add_field(
                                name='❌ Denied',
                                value=f"Slot awarded to **{req['member_name']}**",
                                inline=False,
                            )
                            await msg.edit(embed=comp_embed, view=None)
                    except (discord.NotFound, discord.Forbidden):
                        pass
                # DM the competing member
                try:
                    comp_member = await interaction.guild.fetch_member(int(comp['member_id']))
                    await comp_member.send(
                        f"❌ **Slot Request Denied**\n"
                        f"Operation: **{op['name']}**\n"
                        f"Slot: **{comp['slot_label']}**\n"
                        f"This slot was awarded to another member. "
                        f"You can request a different slot with `/request-slot`."
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
# Live ORBAT view — slot select menus + fallback button, rebuilt on each update
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Persistent "Request a Slot" button attached to the ORBAT embed
# ---------------------------------------------------------------------------

class OrbatRequestButton(discord.ui.View):
    """Persistent view with a single button attached to the live ORBAT embed."""

    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label='📋 Request a Slot',
        style=discord.ButtonStyle.primary,
        custom_id='orbat_request_slot',
    )
    async def request_slot_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)

        try:
            op = await database.get_active_operation(str(interaction.guild_id))
            if not op:
                await interaction.followup.send(
                    "❌ No active operation.", ephemeral=True
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
                available = await database.get_available_slots(op['id'])
            except Exception as e:
                await interaction.followup.send(
                    f"❌ Failed to load slots: `{e}`", ephemeral=True
                )
                return

            pending_ids = set(await database.get_pending_slot_ids(op['id']))
            approved_ids = set(await database.get_approved_slot_ids(op['id']))

            if not available:
                await interaction.followup.send(
                    "❌ All slots are filled for this operation.", ephemeral=True
                )
                return

            # Add computed fields for the picker views
            for s in available:
                s['label'] = f"{s['squad_name']} \u2013 {s['role_name']}"

            open_count = sum(1 for s in available if s['id'] not in pending_ids)
            pending_count = sum(1 for s in available if s['id'] in pending_ids)

            squads: dict = {}
            for s in available:
                squads.setdefault(s['squad_name'], []).append(s)

            view = SquadSelectView(
                squads=squads,
                all_slots=available,
                operation_id=op['id'],
                pending_ids=pending_ids,
                approved_ids=approved_ids,
                bot=self.bot,
            )
            await interaction.followup.send(
                content=(
                    f"🎖️ **{op['name']} — Slot Request**\n"
                    f"🟢 **{open_count}** open  ·  🟡 **{pending_count}** pending\n\n"
                    "Select your squad:"
                ),
                view=view,
                ephemeral=True,
            )

        except Exception as e:
            try:
                await interaction.followup.send(f"❌ Unexpected error: `{e}`", ephemeral=True)
            except Exception:
                pass


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
                available = await database.get_available_slots(op['id'])
            except Exception as e:
                await interaction.followup.send(
                    f"❌ Failed to load slots: `{e}`", ephemeral=True
                )
                return

            pending_ids = set(await database.get_pending_slot_ids(op['id']))
            approved_ids = set(await database.get_approved_slot_ids(op['id']))

            if not available:
                await interaction.followup.send(
                    "❌ All slots are filled for this operation.", ephemeral=True
                )
                return

            for s in available:
                s['label'] = f"{s['squad_name']} \u2013 {s['role_name']}"

            open_count = sum(1 for s in available if s['id'] not in pending_ids)
            pending_count = sum(1 for s in available if s['id'] in pending_ids)

            squads: dict = {}
            for s in available:
                squads.setdefault(s['squad_name'], []).append(s)

            view = SquadSelectView(
                squads=squads,
                all_slots=available,
                operation_id=op['id'],
                pending_ids=pending_ids,
                approved_ids=approved_ids,
                bot=self.bot,
            )
            await interaction.followup.send(
                content=(
                    f"🎖️ **{op['name']} — Slot Request**\n"
                    f"🟢 **{open_count}** open  ·  🟡 **{pending_count}** pending\n\n"
                    "Select your squad:"
                ),
                view=view,
                ephemeral=True,
            )

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
            asyncio.create_task(_void_approval_message(self.bot, interaction.guild, existing))
            asyncio.create_task(_update_orbat(self.bot, interaction.guild, op))
        else:
            await interaction.response.send_message(
                "❌ Could not cancel your request.", ephemeral=True
            )


    @app_commands.command(
        name='change-slot',
        description='Request a different slot, forfeiting your current one',
    )
    async def change_slot(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        op = await database.get_active_operation(str(interaction.guild_id))
        if not op:
            await interaction.followup.send("❌ No active operation.", ephemeral=True)
            return

        existing = await database.get_member_active_request(
            str(interaction.guild_id), op['id'], str(interaction.user.id)
        )
        if not existing:
            await interaction.followup.send(
                "ℹ️ You don't have an active slot. Use `/request-slot` to sign up.",
                ephemeral=True,
            )
            return

        # Build confirmation view
        embed = discord.Embed(
            title='⚠️ Change Slot — Confirmation',
            description=(
                f"You currently hold **{existing['slot_label']}** "
                f"({existing['status']}).\n\n"
                "Continuing will **forfeit this slot** and let you pick a new one.\n"
                "Are you sure?"
            ),
            color=discord.Color.orange(),
        )

        bot_ref = self.bot

        class ConfirmView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=60)
                self.confirmed = False

            @discord.ui.button(label='Yes, forfeit my slot', style=discord.ButtonStyle.danger, emoji='⚠️')
            async def confirm(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
                self.confirmed = True
                self.stop()

                if existing['status'] == 'approved':
                    if existing.get('slot_id'):
                        await database.unassign_slot(existing['slot_id'])
                    await database.cancel_request_by_id(existing['id'])
                else:
                    await database.cancel_member_request(
                        str(btn_interaction.guild_id), op['id'], str(btn_interaction.user.id)
                    )
                    asyncio.create_task(_void_approval_message(bot_ref, btn_interaction.guild, existing))

                try:
                    available = await database.get_available_slots(op['id'])
                except Exception as e:
                    await btn_interaction.response.send_message(
                        f"❌ Failed to load slots: `{e}`", ephemeral=True
                    )
                    return

                pending_ids = set(await database.get_pending_slot_ids(op['id']))
                approved_ids = set(await database.get_approved_slot_ids(op['id']))

                if not available:
                    await btn_interaction.response.send_message(
                        "❌ No slots are available right now.", ephemeral=True
                    )
                    asyncio.create_task(_update_orbat(bot_ref, btn_interaction.guild, op))
                    return

                for s in available:
                    s['label'] = f"{s['squad_name']} \u2013 {s['role_name']}"

                open_count = sum(1 for s in available if s['id'] not in pending_ids)
                pending_count = sum(1 for s in available if s['id'] in pending_ids)

                squads: dict = {}
                for s in available:
                    squads.setdefault(s['squad_name'], []).append(s)

                picker_view = SquadSelectView(
                    squads=squads,
                    all_slots=available,
                    operation_id=op['id'],
                    pending_ids=pending_ids,
                    approved_ids=approved_ids,
                    bot=bot_ref,
                )
                await btn_interaction.response.send_message(
                    content=(
                        f"🎖️ **{op['name']} — Pick a New Slot**\n"
                        f"🟢 **{open_count}** open  ·  🟡 **{pending_count}** pending\n\n"
                        "Select your squad:"
                    ),
                    view=picker_view,
                    ephemeral=True,
                )
                asyncio.create_task(_update_orbat(bot_ref, btn_interaction.guild, op))

            @discord.ui.button(label='Cancel', style=discord.ButtonStyle.secondary, emoji='✖️')
            async def cancel(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
                self.stop()
                await btn_interaction.response.send_message(
                    "No changes made.", ephemeral=True
                )

        view = ConfirmView()
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    @app_commands.command(
        name='leave-operation',
        description='Remove yourself from the current operation entirely',
    )
    async def leave_operation(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        op = await database.get_active_operation(str(interaction.guild_id))
        if not op:
            await interaction.followup.send("❌ No active operation.", ephemeral=True)
            return

        existing = await database.get_member_active_request(
            str(interaction.guild_id), op['id'], str(interaction.user.id)
        )
        if not existing:
            await interaction.followup.send(
                "ℹ️ You don't have an active slot in this operation.", ephemeral=True
            )
            return

        status_label = existing['status'].capitalize()
        embed = discord.Embed(
            title='⚠️ Leave Operation — Confirmation',
            description=(
                f"You currently hold **{existing['slot_label']}** ({status_label}).\n\n"
                "This will **remove you from the operation** and free up your slot.\n"
                "Are you sure?"
            ),
            color=discord.Color.orange(),
        )

        bot_ref = self.bot

        class ConfirmLeaveView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=60)

            @discord.ui.button(label='Yes, leave operation', style=discord.ButtonStyle.danger, emoji='🚪')
            async def confirm(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
                self.stop()

                if existing['status'] == 'approved':
                    if existing.get('slot_id'):
                        await database.unassign_slot(existing['slot_id'])
                    await database.cancel_request_by_id(existing['id'])
                else:
                    await database.cancel_member_request(
                        str(btn_interaction.guild_id), op['id'], str(btn_interaction.user.id)
                    )
                    asyncio.create_task(_void_approval_message(bot_ref, btn_interaction.guild, existing))

                await btn_interaction.response.send_message(
                    f"✅ You've been removed from **{existing['slot_label']}**.\n"
                    "You can sign up again with `/request-slot` if you change your mind.",
                    ephemeral=True,
                )
                asyncio.create_task(_update_orbat(bot_ref, btn_interaction.guild, op))

            @discord.ui.button(label='Cancel', style=discord.ButtonStyle.secondary, emoji='✖️')
            async def cancel(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
                self.stop()
                await btn_interaction.response.send_message("No changes made.", ephemeral=True)

        await interaction.followup.send(embed=embed, view=ConfirmLeaveView(), ephemeral=True)

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
            all_slots = await database.get_orbat_slots(op['id'])
        except Exception as e:
            await interaction.followup.send(
                f"❌ Failed to load slots: `{e}`", ephemeral=True
            )
            return

        pending_ids = set(await database.get_pending_slot_ids(op['id']))
        embed = _build_orbat_embed(op['name'], all_slots, pending_ids, op['event_time'])

        msg = await target.send(embed=embed, view=OrbatRequestButton(self.bot))
        await database.save_orbat_message(
            str(interaction.guild_id), str(target.id), str(msg.id)
        )

        await interaction.followup.send(
            f"✅ ORBAT posted to {target.mention}. It will update automatically when slots are approved.",
            ephemeral=True,
        )

async def setup(bot: commands.Bot):
    await bot.add_cog(SlotsCog(bot))
