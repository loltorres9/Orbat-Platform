from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord import app_commands
from discord.ext import commands

from cogs.slots import (
    OrbatRequestButton,
    SquadSelectView,
    _build_orbat_embed,
    _build_slots_state,
    _get_unit_role,
    _update_orbat,
    _void_approval_message,
)
from utils import database

UNIT_LEADER_ROLE = "Unit Leader"
ORBAT_CHANNEL_NAME = "orbat"
_EVENT_TIME_FORMATS = ["%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M", "%d-%m-%Y %H:%M"]

_TIMEZONE_CHOICES = [
    app_commands.Choice(name="UTC", value="UTC"),
    app_commands.Choice(name="London (GMT/BST)", value="Europe/London"),
    app_commands.Choice(name="Berlin (CET/CEST)", value="Europe/Berlin"),
    app_commands.Choice(name="Helsinki (EET/EEST)", value="Europe/Helsinki"),
    app_commands.Choice(name="New York (EST/EDT)", value="America/New_York"),
    app_commands.Choice(name="Chicago (CST/CDT)", value="America/Chicago"),
    app_commands.Choice(name="Denver (MST/MDT)", value="America/Denver"),
    app_commands.Choice(name="Los Angeles (PST/PDT)", value="America/Los_Angeles"),
]


def _is_unit_leader_or_admin(member: discord.Member) -> bool:
    if member.guild_permissions.manage_guild or member.guild_permissions.administrator:
        return True
    return any(role.name == UNIT_LEADER_ROLE for role in member.roles)


def _parse_event_time(raw: str, tz_name: str = "UTC") -> datetime:
    raw = raw.strip()
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")
    for fmt in _EVENT_TIME_FORMATS:
        try:
            local_dt = datetime.strptime(raw, fmt).replace(tzinfo=tz)
            return local_dt.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
        except ValueError:
            continue
    raise ValueError("Could not parse event_time. Use DD/MM/YYYY HH:MM or YYYY-MM-DD HH:MM.")


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="setup-slots",
        description="Deprecated: use /create-operation, /add-squad and /add-slot",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def setup_slots(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "Google Sheets import is deprecated. Use `/create-operation`, `/add-squad`, and `/add-slot`.",
            ephemeral=True,
        )

    @app_commands.command(name="create-operation", description="Create a new DB-backed operation (Admin only)")
    @app_commands.describe(
        name="Operation name",
        event_time="Optional event start time, e.g. 25/06/2026 20:00",
        reminder_minutes="Reminder window in minutes",
        activate="Set as active operation immediately",
    )
    @app_commands.choices(
        reminder_minutes=[
            app_commands.Choice(name="15 minutes before", value=15),
            app_commands.Choice(name="30 minutes before", value=30),
            app_commands.Choice(name="60 minutes before", value=60),
        ]
    )
    @app_commands.default_permissions(manage_guild=True)
    async def create_operation(
        self,
        interaction: discord.Interaction,
        name: str,
        event_time: str = None,
        reminder_minutes: int = 30,
        activate: bool = True,
    ):
        await interaction.response.defer(ephemeral=True)
        parsed_event_time = None
        if event_time:
            tz_name = await database.get_guild_timezone(str(interaction.guild_id))
            try:
                parsed_event_time = _parse_event_time(event_time, tz_name)
            except ValueError as exc:
                await interaction.followup.send(f"{exc}", ephemeral=True)
                return

        op_id = await database.create_operation_v2(
            guild_id=str(interaction.guild_id),
            name=name,
            event_time=parsed_event_time,
            reminder_minutes=reminder_minutes,
            activate=activate,
        )
        op = await database.get_operation_by_id(op_id)
        await database.emit_slot_update(str(interaction.guild_id), op_id, "operation_created")
        await interaction.followup.send(
            f"Created operation **{op['name']}** (id `{op_id}`){' and activated it' if activate else ''}.",
            ephemeral=True,
        )

    @app_commands.command(name="add-squad", description="Add a squad to the active operation (Admin only)")
    @app_commands.describe(name="Squad name", display_order="Optional explicit order")
    @app_commands.default_permissions(manage_guild=True)
    async def add_squad(self, interaction: discord.Interaction, name: str, display_order: int = None):
        await interaction.response.defer(ephemeral=True)
        op = await database.get_active_operation(str(interaction.guild_id))
        if not op:
            await interaction.followup.send("No active operation. Use `/create-operation` first.", ephemeral=True)
            return
        squad_id = await database.create_squad(op["id"], name, display_order)
        await interaction.followup.send(f"Added squad **{name}** (id `{squad_id}`).", ephemeral=True)

    @app_commands.command(name="add-slot", description="Add a slot to a squad in the active operation (Admin only)")
    @app_commands.describe(squad_name="Target squad name", role_name="Role/slot label", display_order="Optional explicit order")
    @app_commands.default_permissions(manage_guild=True)
    async def add_slot(self, interaction: discord.Interaction, squad_name: str, role_name: str, display_order: int = None):
        await interaction.response.defer(ephemeral=True)
        op = await database.get_active_operation(str(interaction.guild_id))
        if not op:
            await interaction.followup.send("No active operation. Use `/create-operation` first.", ephemeral=True)
            return
        squads = await database.list_squads(op["id"])
        squad = next((s for s in squads if s["name"].lower() == squad_name.lower()), None)
        if not squad:
            await interaction.followup.send(f"Squad **{squad_name}** not found.", ephemeral=True)
            return
        slot_id = await database.create_slot(op["id"], squad["id"], role_name, display_order=display_order)
        await interaction.followup.send(f"Added slot **{squad['name']} - {role_name}** (id `{slot_id}`).", ephemeral=True)

    @app_commands.command(name="activate-operation", description="Activate an existing operation by id (Admin only)")
    @app_commands.default_permissions(manage_guild=True)
    async def activate_operation(self, interaction: discord.Interaction, operation_id: int):
        ok = await database.activate_operation(str(interaction.guild_id), operation_id)
        if not ok:
            await interaction.response.send_message("Operation not found for this guild.", ephemeral=True)
            return
        await database.emit_slot_update(str(interaction.guild_id), operation_id, "operation_activated")
        await interaction.response.send_message(f"Activated operation `{operation_id}`.", ephemeral=True)

    @app_commands.command(name="clear-slot", description="Remove a member from an active slot (Admin or Unit Leader)")
    async def clear_slot(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if not _is_unit_leader_or_admin(interaction.user):
            await interaction.followup.send("You need Unit Leader or admin permissions.", ephemeral=True)
            return
        op = await database.get_active_operation(str(interaction.guild_id))
        if not op:
            await interaction.followup.send("No active operation.", ephemeral=True)
            return

        active = await database.get_active_requests(op["id"])
        is_admin = interaction.user.guild_permissions.manage_guild or interaction.user.guild_permissions.administrator
        if not is_admin:
            leader_unit = _get_unit_role(interaction.user)
            if not leader_unit:
                await interaction.followup.send("You need a unit role alongside Unit Leader.", ephemeral=True)
                return
            active = [r for r in active if r["unit_role"] == leader_unit]

        if not active:
            await interaction.followup.send("No active requests/slots to clear.", ephemeral=True)
            return

        options = [
            discord.SelectOption(
                label=f"{req['member_name']} - {req['slot_label']}"[:100],
                value=str(req["id"]),
                description=("approved" if req["status"] == "approved" else "pending"),
            )
            for req in active[:25]
        ]
        select = discord.ui.Select(placeholder="Select slot to clear...", options=options, min_values=1, max_values=1)

        async def _on_select(sel_interaction: discord.Interaction):
            request_id = int(sel_interaction.data["values"][0])
            req = await database.get_request_by_id(request_id)
            if not req or req["status"] not in ("pending", "approved"):
                await sel_interaction.response.send_message("Request is no longer active.", ephemeral=True)
                return
            if req["status"] == "approved" and req.get("slot_id"):
                await database.clear_slot_assignment(req["slot_id"])
            await database.cancel_request_any_by_id(request_id)
            if req["status"] == "pending":
                await _void_approval_message(self.bot, sel_interaction.guild, req)
            await sel_interaction.response.send_message(
                f"Cleared **{req['slot_label']}** for **{req['member_name']}**.",
                ephemeral=True,
            )
            if req.get("slot_id"):
                await database.emit_slot_update(str(sel_interaction.guild_id), op["id"], "slot_cleared", req["slot_id"])
            await _update_orbat(self.bot, sel_interaction.guild, op)

        select.callback = _on_select
        view = discord.ui.View(timeout=120)
        view.add_item(select)
        await interaction.followup.send("Select the slot to clear:", view=view, ephemeral=True)

    @app_commands.command(name="debug-slots", description="Show DB slot data for the active operation (Admin only)")
    @app_commands.describe(squad="Optional squad name filter")
    @app_commands.default_permissions(manage_guild=True)
    async def debug_slots(self, interaction: discord.Interaction, squad: str = None):
        await interaction.response.defer(ephemeral=True)
        op = await database.get_active_operation(str(interaction.guild_id))
        if not op:
            await interaction.followup.send("No active operation.", ephemeral=True)
            return
        slots = await database.list_slots(op["id"])
        if squad:
            slots = [s for s in slots if squad.lower() in s["squad_name"].lower()]
        if not slots:
            await interaction.followup.send("No slots found for the current filter.", ephemeral=True)
            return
        lines = [f"**{len(slots)} slot(s)** in operation **{op['name']}**"]
        for slot in slots[:40]:
            assignee = slot["assigned_to_member_name"] or "OPEN"
            lines.append(f"`{slot['id']}` {slot['squad_name']} - {slot['role_name']} -> {assignee}")
        if len(slots) > 40:
            lines.append(f"... and {len(slots) - 40} more")
        await interaction.followup.send("\n".join(lines), ephemeral=True)

    @app_commands.command(name="current-operation", description="Show current active operation (Admin only)")
    @app_commands.default_permissions(manage_guild=True)
    async def current_operation(self, interaction: discord.Interaction):
        op = await database.get_active_operation(str(interaction.guild_id))
        if not op:
            await interaction.response.send_message("No active operation.", ephemeral=True)
            return
        embed = discord.Embed(
            title="Current Operation",
            description=f"**{op['name']}**\nID: `{op['id']}`",
            color=discord.Color.blurple(),
        )
        if op["event_time"]:
            ts = int(op["event_time"].replace(tzinfo=ZoneInfo("UTC")).timestamp())
            embed.add_field(name="Event Time", value=f"<t:{ts}:F> (<t:{ts}:R>)", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="clear-requests", description="Cancel all pending requests for active operation (Admin only)")
    @app_commands.default_permissions(manage_guild=True)
    async def clear_requests(self, interaction: discord.Interaction):
        op = await database.get_active_operation(str(interaction.guild_id))
        if not op:
            await interaction.response.send_message("No active operation.", ephemeral=True)
            return
        count = await database.clear_pending_requests(op["id"])
        await interaction.response.send_message(f"Cleared **{count}** pending request(s).", ephemeral=True)
        await _update_orbat(self.bot, interaction.guild, op)

    @app_commands.command(name="set-timezone", description="Set server timezone for event inputs (Admin only)")
    @app_commands.describe(timezone="Your local timezone")
    @app_commands.choices(timezone=_TIMEZONE_CHOICES)
    @app_commands.default_permissions(manage_guild=True)
    async def set_timezone(self, interaction: discord.Interaction, timezone: str):
        await database.set_guild_timezone(str(interaction.guild_id), timezone)
        await interaction.response.send_message(f"Timezone set to **{timezone}**.", ephemeral=True)

    @app_commands.command(name="set-event-time", description="Update event start time for active operation (Admin only)")
    @app_commands.describe(event_time="Example: 25/06/2026 20:00", reminder_minutes="Reminder window")
    @app_commands.choices(
        reminder_minutes=[
            app_commands.Choice(name="15 minutes before", value=15),
            app_commands.Choice(name="30 minutes before", value=30),
            app_commands.Choice(name="60 minutes before", value=60),
        ]
    )
    @app_commands.default_permissions(manage_guild=True)
    async def set_event_time(self, interaction: discord.Interaction, event_time: str, reminder_minutes: int = 30):
        op = await database.get_active_operation(str(interaction.guild_id))
        if not op:
            await interaction.response.send_message("No active operation.", ephemeral=True)
            return
        try:
            tz_name = await database.get_guild_timezone(str(interaction.guild_id))
            parsed = _parse_event_time(event_time, tz_name)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await database.set_event_time(op["id"], parsed, reminder_minutes)
        op = await database.get_active_operation(str(interaction.guild_id))
        await _update_orbat(self.bot, interaction.guild, op)
        await interaction.response.send_message(
            f"Event time set to <t:{int(parsed.replace(tzinfo=ZoneInfo('UTC')).timestamp())}:F>.",
            ephemeral=True,
        )

    @app_commands.command(name="assign-slot", description="Assign a member directly to an open slot (Admin or Unit Leader)")
    @app_commands.describe(member="Discord member to assign")
    async def assign_slot(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=True)
        if not _is_unit_leader_or_admin(interaction.user):
            await interaction.followup.send("You need Unit Leader or admin permissions.", ephemeral=True)
            return

        is_admin = interaction.user.guild_permissions.manage_guild or interaction.user.guild_permissions.administrator
        if not is_admin:
            leader_unit = _get_unit_role(interaction.user)
            member_unit = _get_unit_role(member)
            if not leader_unit or member_unit != leader_unit:
                await interaction.followup.send("You can only assign members from your own unit.", ephemeral=True)
                return

        op = await database.get_active_operation(str(interaction.guild_id))
        if not op:
            await interaction.followup.send("No active operation.", ephemeral=True)
            return

        existing = await database.get_member_active_request(str(interaction.guild_id), op["id"], str(member.id))
        if existing:
            await interaction.followup.send(
                f"{member.display_name} already has **{existing['status']}** slot **{existing['slot_label']}**.",
                ephemeral=True,
            )
            return

        slots, pending_slot_ids, _ = await _build_slots_state(op["id"])
        available = [s for s in slots if not s["assigned_to"]]
        if not available:
            await interaction.followup.send("All slots are filled.", ephemeral=True)
            return

        squads: dict[str, list[dict]] = {}
        for slot in available:
            squads.setdefault(slot["squad"], []).append(slot)

        async def _on_slot_selected(sel_interaction: discord.Interaction, slot: dict):
            current = await database.get_slot_by_id(slot["id"])
            if not current or current["assigned_to_member_id"]:
                await sel_interaction.response.send_message("That slot was just filled.", ephemeral=True)
                return

            await database.assign_slot(slot["id"], str(member.id), member.display_name)
            request_id = await database.create_request(
                guild_id=str(sel_interaction.guild_id),
                operation_id=op["id"],
                slot_id=slot["id"],
                member_id=str(member.id),
                member_name=member.display_name,
                slot_label=slot["label"],
                unit_role=_get_unit_role(member),
            )
            await database.approve_request(request_id, sel_interaction.user.display_name)
            await database.emit_slot_update(str(sel_interaction.guild_id), op["id"], "slot_assigned", slot["id"])

            await sel_interaction.response.send_message(
                f"Assigned **{member.display_name}** to **{slot['label']}**.",
                ephemeral=True,
            )
            try:
                await member.send(
                    f"You were assigned to **{slot['label']}** in operation **{op['name']}**."
                )
            except (discord.Forbidden, discord.NotFound):
                pass
            await _update_orbat(self.bot, sel_interaction.guild, op)

        view = SquadSelectView(
            squads=squads,
            all_slots=available,
            operation_id=op["id"],
            pending_slot_ids=pending_slot_ids,
            bot=self.bot,
            on_select=_on_slot_selected,
        )
        await interaction.followup.send(f"Select a slot for **{member.display_name}**:", view=view, ephemeral=True)

    @app_commands.command(name="post-event", description="Post an event announcement embed (Admin only)")
    @app_commands.describe(channel="Target channel", mission_name="Mission title", event_time="Optional event time")
    @app_commands.default_permissions(manage_guild=True)
    async def post_event(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel = None,
        mission_name: str = None,
        event_time: str = None,
    ):
        await interaction.response.defer(ephemeral=True)
        target = channel or interaction.channel
        op = await database.get_active_operation(str(interaction.guild_id))

        if mission_name is None:
            if not op:
                await interaction.followup.send("No active operation and no mission_name provided.", ephemeral=True)
                return
            mission_name = op["name"]

        parsed = None
        if event_time:
            tz_name = await database.get_guild_timezone(str(interaction.guild_id))
            try:
                parsed = _parse_event_time(event_time, tz_name)
            except ValueError as exc:
                await interaction.followup.send(str(exc), ephemeral=True)
                return
        elif op and op["event_time"]:
            parsed = op["event_time"]

        embed = discord.Embed(title=f"Operation: {mission_name}", color=discord.Color.dark_red())
        if parsed:
            ts = int(parsed.replace(tzinfo=ZoneInfo("UTC")).timestamp())
            embed.add_field(name="Starts", value=f"<t:{ts}:F> (<t:{ts}:R>)", inline=False)
        orbat_channel = discord.utils.get(interaction.guild.text_channels, name=ORBAT_CHANNEL_NAME)
        orbat_ref = orbat_channel.mention if orbat_channel else "#orbat"
        embed.add_field(name="Sign up", value=f"Use {orbat_ref} to request your slot.", inline=False)
        embed.set_footer(text=f"Posted by {interaction.user.display_name}")
        embed.timestamp = discord.utils.utcnow()
        await target.send(embed=embed)
        await interaction.followup.send(f"Event posted in {target.mention}.", ephemeral=True)

    @app_commands.command(name="archive-old-approvals", description="Archive historical approval messages (Admin only)")
    @app_commands.default_permissions(manage_guild=True)
    async def archive_old_approvals(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        approvals_channel = discord.utils.get(interaction.guild.text_channels, name="slot-approvals")
        if not approvals_channel:
            await interaction.followup.send("No #slot-approvals channel found.", ephemeral=True)
            return
        archive_channel = discord.utils.get(interaction.guild.text_channels, name="approval-archive")
        if archive_channel is None:
            archive_channel = await interaction.guild.create_text_channel("approval-archive")

        moved = 0
        skipped = 0
        bot_id = self.bot.user.id
        async for message in approvals_channel.history(limit=500, oldest_first=True):
            if message.author.id != bot_id or not message.embeds:
                continue
            embed = message.embeds[0]
            is_green = embed.color is not None and embed.color.value == discord.Color.green().value
            has_approval = "approved" in ((embed.title or "") + (embed.description or "")).lower()
            if not (is_green or has_approval):
                continue
            try:
                await archive_channel.send(embed=embed)
                await message.delete()
                moved += 1
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                skipped += 1
        await interaction.followup.send(
            f"Archived **{moved}** message(s)." + (f" Skipped: {skipped}." if skipped else ""),
            ephemeral=True,
        )

    @app_commands.command(name="sync", description="Force-sync slash commands and refresh ORBAT (Admin only)")
    @app_commands.default_permissions(manage_guild=True)
    async def sync(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        synced = await self.bot.tree.sync(guild=interaction.guild)
        op = await database.get_active_operation(str(interaction.guild_id))
        if op:
            try:
                await _update_orbat(self.bot, interaction.guild, op, raise_errors=True)
                note = "\nORBAT refreshed."
            except Exception as exc:
                note = f"\nORBAT refresh failed: `{exc}`"
        else:
            note = ""
        await interaction.followup.send(f"Synced **{len(synced)}** command(s).{note}", ephemeral=True)

    @app_commands.command(name="post-orbat", description="Post a live ORBAT board (Admin only)")
    @app_commands.describe(channel="Target channel (defaults to current)")
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
    await bot.add_cog(AdminCog(bot))
