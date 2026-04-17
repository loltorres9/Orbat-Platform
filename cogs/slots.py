import asyncio
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils import database

APPROVAL_CHANNEL_NAME = "slot-approval-dev"
APPROVAL_ARCHIVE_CHANNEL_NAME = "approval-archive-dev"
UNIT_ROLES = {"2nd USC", "CNTO", "PXG", "TFP", "SKUA"}


def _get_unit_role(member: discord.Member) -> Optional[str]:
    for role in member.roles:
        if role.name in UNIT_ROLES:
            return role.name
    return None


def _can_action_request(approver: discord.Member, unit_role: Optional[str]) -> bool:
    perms = approver.guild_permissions
    if perms.manage_guild or perms.administrator:
        return True
    if unit_role is None:
        return True
    return any(role.name == unit_role for role in approver.roles)


async def _build_slots_state(operation_id: int) -> tuple[list[dict], set[int], set[int]]:
    slots_rows = await database.list_slots(operation_id)
    pending_slot_ids = await database.get_pending_slot_ids(operation_id)
    approved_slot_ids = await database.get_approved_slot_ids(operation_id)
    slots = [
        {
            "id": row["id"],
            "squad": row["squad_name"],
            "role": row["role_name"],
            "label": f"{row['squad_name']} - {row['role_name']}",
            "assigned_to": row["assigned_to_member_name"],
            "display_order": row["display_order"],
            "squad_display_order": row["squad_display_order"],
        }
        for row in slots_rows
    ]
    return slots, pending_slot_ids, approved_slot_ids


def _build_orbat_embed(operation_name: str, all_slots: list[dict], pending_slot_ids: set[int], event_time=None) -> discord.Embed:
    counted = [s for s in all_slots if s["squad"].lower() != "reservists"]
    filled = sum(1 for s in counted if s["assigned_to"])
    pending = sum(1 for s in counted if not s["assigned_to"] and s["id"] in pending_slot_ids)
    open_ = sum(1 for s in counted if not s["assigned_to"] and s["id"] not in pending_slot_ids)
    total = len(counted)

    event_line = (
        f"\nOperation starts: <t:{int(event_time.timestamp())}:F> (<t:{int(event_time.timestamp())}:R>)"
        if event_time
        else ""
    )
    embed = discord.Embed(
        title=f"ORBAT - {operation_name}",
        description=f"Open: **{open_}** | Pending: **{pending}** | Filled: **{filled}/{total}**{event_line}",
        color=discord.Color.dark_blue(),
    )
    embed.timestamp = discord.utils.utcnow()
    embed.set_footer(text="Last updated")

    grouped: dict[str, list[dict]] = {}
    for slot in all_slots:
        grouped.setdefault(slot["squad"], []).append(slot)
    ordered_squads = sorted(
        grouped.keys(),
        key=lambda squad: min(s["squad_display_order"] for s in grouped[squad]),
    )

    for squad in ordered_squads[:25]:
        lines = []
        squad_slots = sorted(grouped[squad], key=lambda s: (s["display_order"], s["id"]))
        for slot in squad_slots:
            if slot["assigned_to"]:
                lines.append(f"🔴 {slot['role']} - {slot['assigned_to']}")
            elif slot["id"] in pending_slot_ids:
                lines.append(f"🟡 {slot['role']} (pending)")
            else:
                lines.append(f"🟢 {slot['role']}")
        value = "\n".join(lines) or "-"
        if len(value) > 1024:
            value = value[:1021] + "..."
        embed.add_field(name=squad, value=value, inline=True)

    return embed


async def _update_orbat(bot: commands.Bot, guild: discord.Guild, op, raise_errors: bool = False):
    stored = await database.get_orbat_message(str(guild.id))
    if not stored:
        return

    try:
        channel = guild.get_channel(int(stored["channel_id"])) or await guild.fetch_channel(int(stored["channel_id"]))
        msg = await channel.fetch_message(int(stored["message_id"]))
    except (discord.NotFound, discord.Forbidden) as exc:
        if raise_errors:
            raise RuntimeError(f"Cannot access stored ORBAT message: {exc}") from exc
        return

    slots, pending_slot_ids, _ = await _build_slots_state(op["id"])
    embed = _build_orbat_embed(op["name"], slots, pending_slot_ids, op["event_time"])

    try:
        await msg.edit(embed=embed, view=OrbatRequestButton(bot))
    except (discord.NotFound, discord.Forbidden) as exc:
        if raise_errors:
            raise RuntimeError(f"Failed to edit ORBAT message: {exc}") from exc


async def _void_approval_message(bot: commands.Bot, guild: discord.Guild, req):
    if not req.get("approval_message_id") or not req.get("approval_channel_id"):
        return
    channel = guild.get_channel(int(req["approval_channel_id"]))
    if not channel:
        return
    try:
        msg = await channel.fetch_message(int(req["approval_message_id"]))
    except (discord.NotFound, discord.Forbidden):
        return

    embed = discord.Embed(
        title="Slot Request - Cancelled",
        description=f"Request for **{req['slot_label']}** was cancelled.",
        color=discord.Color.dark_gray(),
    )
    embed.timestamp = discord.utils.utcnow()
    try:
        await msg.edit(embed=embed, view=discord.ui.View())
    except (discord.NotFound, discord.Forbidden):
        pass


async def _post_approval_message(bot: commands.Bot, interaction: discord.Interaction, op, request_id: int, slot_label: str, unit_role: Optional[str]):
    approval_channel = discord.utils.get(interaction.guild.text_channels, name=APPROVAL_CHANNEL_NAME)
    if approval_channel is None:
        approval_channel = await interaction.guild.create_text_channel(
            APPROVAL_CHANNEL_NAME,
            topic="Slot approval requests for Arma 3 operations",
        )

    color = discord.Color.yellow()
    if unit_role:
        role_obj = discord.utils.get(interaction.guild.roles, name=unit_role)
        if role_obj and role_obj.color.value:
            color = role_obj.color

    unit_line = f" | {unit_role}" if unit_role else ""
    embed = discord.Embed(
        description=f"**{op['name']}**{unit_line}\n{interaction.user.mention} -> **{slot_label}**",
        color=color,
    )
    embed.set_footer(text=f"Request ID: {request_id}")
    embed.timestamp = discord.utils.utcnow()

    view = ApprovalView(request_id=request_id, bot=bot)
    msg = await approval_channel.send(embed=embed, view=view)
    try:
        bot.add_view(view)
    except ValueError:
        pass
    await database.update_request_message(request_id, str(msg.id), str(approval_channel.id))


async def _process_slot_selection(interaction: discord.Interaction, slot: dict, operation_id: int, bot: commands.Bot):
    if slot.get("assigned_to"):
        await interaction.response.send_message("That slot is already filled. Pick another.", ephemeral=True)
        return

    existing = await database.get_member_active_request(str(interaction.guild_id), operation_id, str(interaction.user.id))
    if existing:
        await interaction.response.send_message(
            f"You already have a **{existing['status']}** request for **{existing['slot_label']}**.",
            ephemeral=True,
        )
        return

    unit_role = _get_unit_role(interaction.user)
    request_id = await database.create_request(
        guild_id=str(interaction.guild_id),
        operation_id=operation_id,
        slot_id=slot["id"],
        member_id=str(interaction.user.id),
        member_name=interaction.user.display_name,
        slot_label=slot["label"],
        unit_role=unit_role,
    )

    await interaction.response.send_message(
        f"Request submitted for **{slot['label']}**. Waiting for approval.",
        ephemeral=True,
    )

    op = await database.get_active_operation(str(interaction.guild_id))
    try:
        await _post_approval_message(bot, interaction, op, request_id, slot["label"], unit_role)
    except Exception as exc:
        await database.deny_request(request_id, "system", reason="Approval message failed")
        await interaction.followup.send(
            f"Could not post approval message (`{exc}`). The request was cancelled.",
            ephemeral=True,
        )
        return

    await database.emit_slot_update(str(interaction.guild_id), operation_id, "request_created", slot["id"])
    asyncio.create_task(_update_orbat(bot, interaction.guild, op))


class SquadSelectView(discord.ui.View):
    def __init__(
        self,
        squads: dict[str, list[dict]],
        all_slots: list[dict],
        operation_id: int,
        pending_slot_ids: set[int],
        bot: commands.Bot,
        on_select=None,
    ):
        super().__init__(timeout=300)
        self.squads = squads
        self.all_slots = all_slots
        self.operation_id = operation_id
        self.pending_slot_ids = pending_slot_ids
        self.bot = bot
        self.on_select = on_select

        options = []
        for squad_name, squad_slots in squads.items():
            open_count = sum(1 for s in squad_slots if not s["assigned_to"] and s["id"] not in pending_slot_ids)
            pending_count = sum(1 for s in squad_slots if not s["assigned_to"] and s["id"] in pending_slot_ids)
            desc = []
            if open_count:
                desc.append(f"{open_count} open")
            if pending_count:
                desc.append(f"{pending_count} pending")
            options.append(
                discord.SelectOption(
                    label=squad_name[:100],
                    value=squad_name,
                    description=" | ".join(desc)[:100] if desc else "No open slots",
                )
            )

        select = discord.ui.Select(placeholder="Choose a squad...", options=options[:25], min_values=1, max_values=1)
        select.callback = self._squad_selected
        self.add_item(select)

    async def _squad_selected(self, interaction: discord.Interaction):
        squad_name = interaction.data["values"][0]
        view = SlotSelectView(
            squad_name=squad_name,
            slots=self.squads[squad_name],
            all_slots=self.all_slots,
            operation_id=self.operation_id,
            pending_slot_ids=self.pending_slot_ids,
            bot=self.bot,
            on_select=self.on_select,
        )
        await interaction.response.edit_message(content=f"**{squad_name}** - choose a slot:", view=view)


class SlotSelectView(discord.ui.View):
    def __init__(
        self,
        squad_name: str,
        slots: list[dict],
        all_slots: list[dict],
        operation_id: int,
        pending_slot_ids: set[int],
        bot: commands.Bot,
        on_select=None,
    ):
        super().__init__(timeout=300)
        self.squad_name = squad_name
        self.slots_by_value = {str(s["id"]): s for s in slots}
        self.all_slots = all_slots
        self.operation_id = operation_id
        self.pending_slot_ids = pending_slot_ids
        self.bot = bot
        self.on_select = on_select

        options = []
        for slot in slots[:25]:
            if slot["assigned_to"]:
                continue
            pending = slot["id"] in pending_slot_ids
            options.append(
                discord.SelectOption(
                    label=slot["role"][:100],
                    value=str(slot["id"]),
                    description=("Also requested - compete for slot" if pending else "Available")[:100],
                    emoji=("🟡" if pending else "🟢"),
                )
            )
        if not options:
            options.append(discord.SelectOption(label="No open slots", value="none", description="Go back and choose another squad"))

        select = discord.ui.Select(placeholder="Choose a slot...", options=options, min_values=1, max_values=1)
        select.callback = self._slot_selected
        self.add_item(select)

        back = discord.ui.Button(label="Back to squads", style=discord.ButtonStyle.secondary)
        back.callback = self._go_back
        self.add_item(back)

    async def _slot_selected(self, interaction: discord.Interaction):
        slot_id = interaction.data["values"][0]
        if slot_id == "none":
            await interaction.response.send_message("No open slots in this squad.", ephemeral=True)
            return
        slot = self.slots_by_value.get(slot_id)
        if not slot:
            await interaction.response.send_message("Slot not found.", ephemeral=True)
            return
        if self.on_select:
            await self.on_select(interaction, slot)
        else:
            await _process_slot_selection(interaction, slot, self.operation_id, self.bot)

    async def _go_back(self, interaction: discord.Interaction):
        slots, pending_slot_ids, _ = await _build_slots_state(self.operation_id)
        available = [s for s in slots if not s["assigned_to"]]
        squads: dict[str, list[dict]] = {}
        for slot in available:
            squads.setdefault(slot["squad"], []).append(slot)
        view = SquadSelectView(squads, available, self.operation_id, pending_slot_ids, self.bot, on_select=self.on_select)
        await interaction.response.edit_message(content="Select your squad:", view=view)


class DenialModal(discord.ui.Modal, title="Deny Slot Request"):
    reason = discord.ui.TextInput(
        label="Reason (optional)",
        placeholder="Slot not available, duplicate request, ...",
        required=False,
        max_length=200,
    )

    def __init__(self, request_id: int, bot: commands.Bot):
        super().__init__()
        self.request_id = request_id
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction):
        req = await database.get_request_by_id(self.request_id)
        if not req or req["status"] != "pending":
            await interaction.response.send_message("This request is no longer pending.", ephemeral=True)
            return
        if not _can_action_request(interaction.user, req.get("unit_role")):
            await interaction.response.send_message("You cannot action this request.", ephemeral=True)
            return

        reason = (self.reason.value or "").strip() or "No reason provided"
        await database.deny_request(self.request_id, interaction.user.display_name, reason)

        embed = discord.Embed(
            title="Request Denied",
            description=f"Request **{req['slot_label']}** denied by {interaction.user.mention}.\nReason: {reason}",
            color=discord.Color.red(),
        )
        embed.timestamp = discord.utils.utcnow()
        await interaction.response.edit_message(embed=embed, view=discord.ui.View())

        guild = interaction.guild
        if req.get("member_id"):
            try:
                member = await guild.fetch_member(int(req["member_id"]))
                await member.send(
                    f"Your request for **{req['slot_label']}** was denied.\nReason: {reason}"
                )
            except (discord.Forbidden, discord.NotFound):
                pass

        if req.get("slot_id"):
            await database.emit_slot_update(str(guild.id), req["operation_id"], "request_denied", req["slot_id"])
        op = await database.get_operation_by_id(req["operation_id"])
        if op:
            asyncio.create_task(_update_orbat(self.bot, guild, op))


class ApprovalView(discord.ui.View):
    def __init__(self, request_id: int, bot: commands.Bot):
        super().__init__(timeout=None)
        self.request_id = request_id
        self.bot = bot

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        req = await database.get_request_by_id(self.request_id)
        if not req or req["status"] != "pending":
            await interaction.response.send_message("This request is no longer pending.", ephemeral=True)
            return
        if not _can_action_request(interaction.user, req.get("unit_role")):
            await interaction.response.send_message("You cannot action this request.", ephemeral=True)
            return

        slot_id = req.get("slot_id")
        if slot_id:
            slot = await database.get_slot_by_id(slot_id)
            if not slot:
                await interaction.response.send_message("Slot not found anymore.", ephemeral=True)
                return
            if slot.get("assigned_to_member_id"):
                await interaction.response.send_message("Slot already assigned.", ephemeral=True)
                return
            await database.assign_slot(slot_id, req["member_id"], req["member_name"])

        await database.approve_request(self.request_id, interaction.user.display_name)

        if slot_id:
            competing = await database.get_competing_requests_by_slot(req["operation_id"], slot_id, self.request_id)
            for competitor in competing:
                await database.deny_request(competitor["id"], interaction.user.display_name, "Another member was approved first.")
                try:
                    member = await interaction.guild.fetch_member(int(competitor["member_id"]))
                    await member.send(
                        f"Your request for **{competitor['slot_label']}** was denied because another member was approved first."
                    )
                except (discord.Forbidden, discord.NotFound):
                    pass

        embed = discord.Embed(
            title="Request Approved",
            description=f"{req['member_name']} approved for **{req['slot_label']}** by {interaction.user.mention}.",
            color=discord.Color.green(),
        )
        embed.timestamp = discord.utils.utcnow()
        await interaction.response.edit_message(embed=embed, view=discord.ui.View())

        try:
            member = await interaction.guild.fetch_member(int(req["member_id"]))
            await member.send(
                f"Your request for **{req['slot_label']}** was approved."
            )
        except (discord.Forbidden, discord.NotFound):
            pass

        if slot_id:
            await database.emit_slot_update(str(interaction.guild.id), req["operation_id"], "request_approved", slot_id)
        op = await database.get_operation_by_id(req["operation_id"])
        if op:
            asyncio.create_task(_update_orbat(self.bot, interaction.guild, op))

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger)
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(DenialModal(self.request_id, self.bot))


class OrbatRequestButton(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Request a Slot", style=discord.ButtonStyle.primary, custom_id="orbat_request_slot")
    async def request_slot_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        op = await database.get_active_operation(str(interaction.guild_id))
        if not op:
            await interaction.followup.send("No active operation. Ask an admin to create one.", ephemeral=True)
            return

        existing = await database.get_member_active_request(str(interaction.guild_id), op["id"], str(interaction.user.id))
        if existing:
            await interaction.followup.send(
                f"You already have a **{existing['status']}** request for **{existing['slot_label']}**.",
                ephemeral=True,
            )
            return

        slots, pending_slot_ids, _ = await _build_slots_state(op["id"])
        available = [s for s in slots if not s["assigned_to"]]
        if not available:
            await interaction.followup.send("All slots are currently filled.", ephemeral=True)
            return

        squads: dict[str, list[dict]] = {}
        for slot in available:
            squads.setdefault(slot["squad"], []).append(slot)
        view = SquadSelectView(squads, available, op["id"], pending_slot_ids, self.bot)
        await interaction.followup.send(
            content=f"**{op['name']} - Slot Request**\nSelect your squad:",
            view=view,
            ephemeral=True,
        )


class SlotsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="request-slot", description="Browse and request a slot for the current operation")
    async def request_slot(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        op = await database.get_active_operation(str(interaction.guild_id))
        if not op:
            await interaction.followup.send("No active operation. Ask an admin to create one.", ephemeral=True)
            return

        existing = await database.get_member_active_request(str(interaction.guild_id), op["id"], str(interaction.user.id))
        if existing:
            await interaction.followup.send(
                f"You already have a **{existing['status']}** request for **{existing['slot_label']}**.",
                ephemeral=True,
            )
            return

        slots, pending_slot_ids, _ = await _build_slots_state(op["id"])
        available = [s for s in slots if not s["assigned_to"]]
        if not available:
            await interaction.followup.send("All slots are currently filled.", ephemeral=True)
            return

        squads: dict[str, list[dict]] = {}
        for slot in available:
            squads.setdefault(slot["squad"], []).append(slot)
        view = SquadSelectView(squads, available, op["id"], pending_slot_ids, self.bot)
        await interaction.followup.send(
            content=f"**{op['name']} - Slot Request**\nSelect your squad:",
            view=view,
            ephemeral=True,
        )

    @app_commands.command(name="cancel-request", description="Cancel your pending slot request for the current operation")
    async def cancel_request(self, interaction: discord.Interaction):
        op = await database.get_active_operation(str(interaction.guild_id))
        if not op:
            await interaction.response.send_message("No active operation.", ephemeral=True)
            return

        existing = await database.get_member_active_request(str(interaction.guild_id), op["id"], str(interaction.user.id))
        if not existing or existing["status"] != "pending":
            await interaction.response.send_message("You do not have a pending request.", ephemeral=True)
            return

        cancelled = await database.cancel_member_request(str(interaction.guild_id), op["id"], str(interaction.user.id))
        if cancelled:
            await interaction.response.send_message(f"Cancelled **{existing['slot_label']}**.", ephemeral=True)
            asyncio.create_task(_void_approval_message(self.bot, interaction.guild, existing))
            if existing.get("slot_id"):
                await database.emit_slot_update(str(interaction.guild.id), op["id"], "request_cancelled", existing["slot_id"])
            asyncio.create_task(_update_orbat(self.bot, interaction.guild, op))
        else:
            await interaction.response.send_message("Could not cancel request.", ephemeral=True)

    @app_commands.command(name="change-slot", description="Forfeit your current slot and pick a new one")
    async def change_slot(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        op = await database.get_active_operation(str(interaction.guild_id))
        if not op:
            await interaction.followup.send("No active operation.", ephemeral=True)
            return

        existing = await database.get_member_active_request(str(interaction.guild_id), op["id"], str(interaction.user.id))
        if not existing:
            await interaction.followup.send("You do not have an active slot.", ephemeral=True)
            return

        if existing["status"] == "approved":
            if existing.get("slot_id"):
                await database.clear_slot_assignment(existing["slot_id"])
            await database.cancel_request_any_by_id(existing["id"])
        else:
            await database.cancel_member_request(str(interaction.guild_id), op["id"], str(interaction.user.id))
            asyncio.create_task(_void_approval_message(self.bot, interaction.guild, existing))

        slots, pending_slot_ids, _ = await _build_slots_state(op["id"])
        available = [s for s in slots if not s["assigned_to"]]
        if not available:
            await interaction.followup.send("No slots are currently available.", ephemeral=True)
            asyncio.create_task(_update_orbat(self.bot, interaction.guild, op))
            return

        squads: dict[str, list[dict]] = {}
        for slot in available:
            squads.setdefault(slot["squad"], []).append(slot)
        view = SquadSelectView(squads, available, op["id"], pending_slot_ids, self.bot)
        await interaction.followup.send("Pick a new slot:", view=view, ephemeral=True)
        asyncio.create_task(_update_orbat(self.bot, interaction.guild, op))

    @app_commands.command(name="leave-operation", description="Remove yourself from the current operation")
    async def leave_operation(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        op = await database.get_active_operation(str(interaction.guild_id))
        if not op:
            await interaction.followup.send("No active operation.", ephemeral=True)
            return

        existing = await database.get_member_active_request(str(interaction.guild_id), op["id"], str(interaction.user.id))
        if not existing:
            await interaction.followup.send("You do not have an active slot.", ephemeral=True)
            return

        if existing["status"] == "approved":
            if existing.get("slot_id"):
                await database.clear_slot_assignment(existing["slot_id"])
            await database.cancel_request_any_by_id(existing["id"])
        else:
            await database.cancel_member_request(str(interaction.guild_id), op["id"], str(interaction.user.id))
            asyncio.create_task(_void_approval_message(self.bot, interaction.guild, existing))

        await interaction.followup.send(f"You left **{existing['slot_label']}**.", ephemeral=True)
        if existing.get("slot_id"):
            await database.emit_slot_update(str(interaction.guild.id), op["id"], "member_left", existing["slot_id"])
        asyncio.create_task(_update_orbat(self.bot, interaction.guild, op))

    @app_commands.command(name="post-orbat", description="Post a live ORBAT board in a channel (Admin only)")
    @app_commands.describe(channel="Channel to post the ORBAT in (defaults to current channel)")
    @app_commands.default_permissions(manage_guild=True)
    async def post_orbat(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        await interaction.response.defer(ephemeral=True)
        op = await database.get_active_operation(str(interaction.guild_id))
        if not op:
            await interaction.followup.send("No active operation.", ephemeral=True)
            return
        target = channel or interaction.channel
        slots, pending_slot_ids, _ = await _build_slots_state(op["id"])
        embed = _build_orbat_embed(op["name"], slots, pending_slot_ids, op["event_time"])
        msg = await target.send(embed=embed, view=OrbatRequestButton(self.bot))
        await database.save_orbat_message(str(interaction.guild_id), str(target.id), str(msg.id))
        await interaction.followup.send(f"ORBAT posted to {target.mention}.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(SlotsCog(bot))
