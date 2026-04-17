import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord import app_commands
from discord.ext import commands

from utils import database
from cogs.slots import _build_orbat_embed, _get_unit_role, _update_orbat, _void_approval_message, OrbatRequestButton, SquadSelectView

UNIT_LEADER_ROLE = 'Unit Leader'


def _is_unit_leader_or_admin(member: discord.Member) -> bool:
    if member.guild_permissions.manage_guild or member.guild_permissions.administrator:
        return True
    return any(r.name == UNIT_LEADER_ROLE for r in member.roles)

_EVENT_TIME_FORMATS = ['%d/%m/%Y %H:%M', '%Y-%m-%d %H:%M', '%d-%m-%Y %H:%M']

# Common timezone choices (max 25 for Discord)
_TIMEZONE_CHOICES = [
    app_commands.Choice(name='UTC',                       value='UTC'),
    app_commands.Choice(name='London (GMT/BST)',          value='Europe/London'),
    app_commands.Choice(name='Amsterdam/Paris/Berlin (CET/CEST)', value='Europe/Amsterdam'),
    app_commands.Choice(name='Helsinki/Kyiv (EET/EEST)',  value='Europe/Helsinki'),
    app_commands.Choice(name='Moscow (MSK)',               value='Europe/Moscow'),
    app_commands.Choice(name='Dubai (GST)',                value='Asia/Dubai'),
    app_commands.Choice(name='Karachi (PKT)',              value='Asia/Karachi'),
    app_commands.Choice(name='Bangkok (ICT)',              value='Asia/Bangkok'),
    app_commands.Choice(name='Singapore/KL (SGT)',         value='Asia/Singapore'),
    app_commands.Choice(name='Tokyo (JST)',                value='Asia/Tokyo'),
    app_commands.Choice(name='Sydney (AEST/AEDT)',         value='Australia/Sydney'),
    app_commands.Choice(name='Auckland (NZST/NZDT)',       value='Pacific/Auckland'),
    app_commands.Choice(name='New York (EST/EDT)',         value='America/New_York'),
    app_commands.Choice(name='Chicago (CST/CDT)',          value='America/Chicago'),
    app_commands.Choice(name='Denver (MST/MDT)',           value='America/Denver'),
    app_commands.Choice(name='Los Angeles (PST/PDT)',      value='America/Los_Angeles'),
]


def _parse_event_time(raw: str, tz_name: str = 'UTC') -> datetime:
    """Parse event time in the given timezone, return as naive UTC for storage."""
    raw = raw.strip()
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo('UTC')

    for fmt in _EVENT_TIME_FORMATS:
        try:
            local_dt = datetime.strptime(raw, fmt).replace(tzinfo=tz)
            # Convert to naive UTC for DB storage
            return local_dt.astimezone(ZoneInfo('UTC')).replace(tzinfo=None)
        except ValueError:
            continue
    raise ValueError(
        f"Could not parse `{raw}`.\n"
        "Use format `DD/MM/YYYY HH:MM`, e.g. `25/06/2025 19:00`"
    )

ORBAT_CHANNEL_NAME = 'orbat'


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name='setup-slots',
        description='Create a new operation or show the web builder link (Admin only)',
    )
    @app_commands.describe(
        name='Operation name',
        event_time='Event start time, e.g. 25/06/2025 19:00',
        reminder_minutes='Send reminders this many minutes before the event (15, 30, or 60)',
    )
    @app_commands.choices(reminder_minutes=[
        app_commands.Choice(name='15 minutes before', value=15),
        app_commands.Choice(name='30 minutes before', value=30),
        app_commands.Choice(name='60 minutes before', value=60),
    ])
    @app_commands.default_permissions(manage_guild=True)
    async def setup_slots(
        self,
        interaction: discord.Interaction,
        name: str,
        event_time: str = None,
        reminder_minutes: int = 30,
    ):
        await interaction.response.defer(ephemeral=True)

        parsed_event_time = None
        if event_time:
            try:
                tz_name = await database.get_guild_timezone(str(interaction.guild_id))
                parsed_event_time = _parse_event_time(event_time, tz_name)
            except ValueError as e:
                await interaction.followup.send(f"❌ {e}", ephemeral=True)
                return

        try:
            op_id = await database.create_operation(
                guild_id=str(interaction.guild_id),
                name=name,
            )
            if parsed_event_time:
                await database.set_event_time(op_id, parsed_event_time, reminder_minutes)
        except Exception as e:
            await interaction.followup.send(f"❌ Database error: `{e}`", ephemeral=True)
            return

        event_line = (
            f"\n🕐 Event time: <t:{int(parsed_event_time.timestamp())}:F> "
            f"(reminder {reminder_minutes} min before)"
            if parsed_event_time else ""
        )
        import os
        frontend_url = os.getenv('FRONTEND_URL', '')
        builder_link = f"\n\n🔧 [Open ORBAT Builder]({frontend_url}/builder/{op_id})" if frontend_url else ""

        confirm_embed = discord.Embed(
            title='✅ Operation Created',
            description=(
                f"**{name}** (ID: {op_id})\n"
                f"{event_line}\n\n"
                f"Use the web builder to add squads and slots, then run `/post-orbat` to display it."
                f"{builder_link}"
            ),
            color=discord.Color.green(),
        )

        await interaction.followup.send(embed=confirm_embed, ephemeral=True)

    @app_commands.command(
        name='clear-slot',
        description='Remove a member from an approved slot (Admin or Unit Leader)',
    )
    async def clear_slot(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if not _is_unit_leader_or_admin(interaction.user):
            await interaction.followup.send(
                "🚫 You need the **Unit Leader** role or admin permissions to use this command.",
                ephemeral=True,
            )
            return

        op = await database.get_active_operation(str(interaction.guild_id))
        if not op:
            await interaction.followup.send("❌ No active operation.", ephemeral=True)
            return

        active = await database.get_active_requests(op['id'])

        # Unit Leaders can only clear slots belonging to their own unit
        is_admin = interaction.user.guild_permissions.manage_guild or interaction.user.guild_permissions.administrator
        if not is_admin:
            leader_unit = _get_unit_role(interaction.user)
            if not leader_unit:
                await interaction.followup.send(
                    "🚫 You need a unit role (e.g. 2nd USC) alongside **Unit Leader** to use this command.",
                    ephemeral=True,
                )
                return
            active = [r for r in active if r['unit_role'] == leader_unit]

        if not active:
            await interaction.followup.send(
                "ℹ️ No active slots to clear.", ephemeral=True
            )
            return

        options = [
            discord.SelectOption(
                label=f"{req['member_name']} — {req['slot_label']}"[:100],
                value=str(req['id']),
                description=f"{'✅ approved' if req['status'] == 'approved' else '⏳ pending'}",
            )
            for req in active[:25]
        ]

        select = discord.ui.Select(
            placeholder='Select a slot to clear…',
            options=options,
            min_values=1,
            max_values=1,
        )

        bot_ref = self.bot

        async def _select_callback(sel_interaction: discord.Interaction):
            request_id = int(sel_interaction.data['values'][0])
            req = await database.get_request_by_id(request_id)
            if not req or req['status'] not in ('pending', 'approved'):
                await sel_interaction.response.send_message(
                    "❌ That request is no longer active.", ephemeral=True
                )
                return

            if req['status'] == 'approved' and req.get('slot_id'):
                await database.unassign_slot(req['slot_id'])

            await database.cancel_request_by_id(request_id)

            status_word = 'approved slot' if req['status'] == 'approved' else 'pending request'
            await sel_interaction.response.send_message(
                f"✅ Cleared {status_word} **{req['slot_label']}** for **{req['member_name']}**.",
                ephemeral=True,
            )

            # DM the member
            try:
                member = await sel_interaction.guild.fetch_member(int(req['member_id']))
                await member.send(
                    f"ℹ️ **Slot Cleared**\n"
                    f"An admin has removed you from **{req['slot_label']}**.\n"
                    f"You can request a different slot with `/request-slot`."
                )
            except (discord.Forbidden, discord.NotFound):
                pass

            # Void the approval message if it exists (for pending requests)
            if req['status'] == 'pending':
                asyncio.create_task(_void_approval_message(bot_ref, sel_interaction.guild, req))

            # Refresh ORBAT
            asyncio.create_task(_update_orbat(bot_ref, sel_interaction.guild, op))

        select.callback = _select_callback
        view = discord.ui.View(timeout=120)
        view.add_item(select)
        await interaction.followup.send(
            "Select the slot to clear:", view=view, ephemeral=True
        )

    @app_commands.command(
        name='debug-slots',
        description='Show raw slot data from the database — use to diagnose missing slots (Admin only)',
    )
    @app_commands.describe(squad='Filter to a specific squad name (optional)')
    @app_commands.default_permissions(manage_guild=True)
    async def debug_slots(self, interaction: discord.Interaction, squad: str = None):
        await interaction.response.defer(ephemeral=True)
        op = await database.get_active_operation(str(interaction.guild_id))
        if not op:
            await interaction.followup.send("❌ No active operation.", ephemeral=True)
            return

        all_slots = await database.get_orbat_slots(op['id'])
        available = [s for s in all_slots if not s['assigned_to_member_id']]

        if squad:
            available = [s for s in available if squad.lower() in s['squad_name'].lower()]

        if not available:
            await interaction.followup.send(
                f"No available slots found{f' for squad matching `{squad}`' if squad else ''}.",
                ephemeral=True,
            )
            return

        lines = [f"**{len(available)} available slot(s) found:**\n"]
        for s in available[:40]:
            lines.append(f"`slot:{s['id']}` **{s['squad_name']}** — {s['role_name']}")
        if len(available) > 40:
            lines.append(f"_…and {len(available) - 40} more_")

        await interaction.followup.send('\n'.join(lines), ephemeral=True)

    @app_commands.command(
        name='current-operation',
        description='Show which operation is currently active (Admin only)',
    )
    @app_commands.default_permissions(manage_guild=True)
    async def current_operation(self, interaction: discord.Interaction):
        op = await database.get_active_operation(str(interaction.guild_id))
        if not op:
            await interaction.response.send_message(
                "No active operation. An admin can load one with `/setup-slots`.",
                ephemeral=True,
            )
            return

        desc = f"**{op['name']}** (ID: {op['id']})"
        if op.get('description'):
            desc += f"\n{op['description']}"
        embed = discord.Embed(
            title='🎖️ Current Operation',
            description=desc,
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


    @app_commands.command(
        name='clear-requests',
        description='Cancel all pending slot requests for the current operation (Admin only)',
    )
    @app_commands.default_permissions(manage_guild=True)
    async def clear_requests(self, interaction: discord.Interaction):
        op = await database.get_active_operation(str(interaction.guild_id))
        if not op:
            await interaction.response.send_message(
                "❌ No active operation.", ephemeral=True
            )
            return

        count = await database.clear_pending_requests(op['id'])
        await interaction.response.send_message(
            f"✅ Cleared **{count}** pending request(s) for **{op['name']}**.",
            ephemeral=True,
        )
        asyncio.create_task(_update_orbat(self.bot, interaction.guild, op))


    @app_commands.command(
        name='set-timezone',
        description='Set the server timezone used for all event times (Admin only)',
    )
    @app_commands.describe(timezone='Your local timezone')
    @app_commands.choices(timezone=_TIMEZONE_CHOICES)
    @app_commands.default_permissions(manage_guild=True)
    async def set_timezone(self, interaction: discord.Interaction, timezone: str):
        await database.set_guild_timezone(str(interaction.guild_id), timezone)
        await interaction.response.send_message(
            f"✅ Server timezone set to **{timezone}**. "
            f"Event times you enter will now be interpreted as {timezone}.",
            ephemeral=True,
        )

    @app_commands.command(
        name='set-event-time',
        description='Set or update the event start time and reminder for the current operation (Admin only)',
    )
    @app_commands.describe(
        event_time='Event start time in UTC, e.g. 25/06/2025 19:00',
        reminder_minutes='Send reminders this many minutes before the event (15, 30, or 60)',
    )
    @app_commands.choices(reminder_minutes=[
        app_commands.Choice(name='15 minutes before', value=15),
        app_commands.Choice(name='30 minutes before', value=30),
        app_commands.Choice(name='60 minutes before', value=60),
    ])
    @app_commands.default_permissions(manage_guild=True)
    async def set_event_time(
        self,
        interaction: discord.Interaction,
        event_time: str,
        reminder_minutes: int = 30,
    ):
        op = await database.get_active_operation(str(interaction.guild_id))
        if not op:
            await interaction.response.send_message("❌ No active operation.", ephemeral=True)
            return

        try:
            tz_name = await database.get_guild_timezone(str(interaction.guild_id))
            parsed = _parse_event_time(event_time, tz_name)
        except ValueError as e:
            await interaction.response.send_message(f"❌ {e}", ephemeral=True)
            return

        await database.set_event_time(op['id'], parsed, reminder_minutes)

        # Re-fetch so _update_orbat picks up the new event_time
        op = await database.get_active_operation(str(interaction.guild_id))
        asyncio.create_task(_update_orbat(self.bot, interaction.guild, op))

        await interaction.response.send_message(
            f"✅ Event time set to <t:{int(parsed.timestamp())}:F> "
            f"with a **{reminder_minutes}-minute** reminder.",
            ephemeral=True,
        )

    @app_commands.command(
        name='assign-slot',
        description='Directly assign a member to a slot without approval (Admin or Unit Leader)',
    )
    @app_commands.describe(member='The Discord member to assign')
    async def assign_slot(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=True)

        if not _is_unit_leader_or_admin(interaction.user):
            await interaction.followup.send(
                "🚫 You need the **Unit Leader** role or admin permissions to use this command.",
                ephemeral=True,
            )
            return

        # Unit Leaders can only assign members of their own unit
        is_admin = interaction.user.guild_permissions.manage_guild or interaction.user.guild_permissions.administrator
        if not is_admin:
            leader_unit = _get_unit_role(interaction.user)
            if not leader_unit:
                await interaction.followup.send(
                    "🚫 You need a unit role (e.g. 2nd USC) alongside **Unit Leader** to use this command.",
                    ephemeral=True,
                )
                return
            member_unit = _get_unit_role(member)
            if member_unit != leader_unit:
                await interaction.followup.send(
                    f"🚫 You can only assign members from your own unit (**{leader_unit}**).\n"
                    f"**{member.display_name}** belongs to **{member_unit or 'no unit'}**.",
                    ephemeral=True,
                )
                return

        op = await database.get_active_operation(str(interaction.guild_id))
        if not op:
            await interaction.followup.send("❌ No active operation.", ephemeral=True)
            return

        # Check the member doesn't already have an active slot
        existing = await database.get_member_active_request(
            str(interaction.guild_id), op['id'], str(member.id)
        )
        if existing:
            await interaction.followup.send(
                f"⚠️ **{member.display_name}** already has a **{existing['status']}** slot: "
                f"**{existing['slot_label']}**.\nUse `/clear-slot` first if you want to reassign them.",
                ephemeral=True,
            )
            return

        try:
            available = await database.get_available_slots(op['id'])
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to load slots: `{e}`", ephemeral=True)
            return

        pending_ids = set(await database.get_pending_slot_ids(op['id']))
        approved_ids = set(await database.get_approved_slot_ids(op['id']))

        if not available:
            await interaction.followup.send("ℹ️ All slots are currently filled.", ephemeral=True)
            return

        for s in available:
            s['label'] = f"{s['squad_name']} \u2013 {s['role_name']}"

        squads: dict = {}
        for s in available:
            squads.setdefault(s['squad_name'], []).append(s)

        bot_ref = self.bot

        async def _on_slot_selected(sel_interaction: discord.Interaction, slot: dict):
            current_approved = set(await database.get_approved_slot_ids(op['id']))
            if slot['id'] in current_approved:
                await sel_interaction.response.send_message(
                    "❌ That slot was just filled. Please pick another.", ephemeral=True
                )
                return

            await sel_interaction.response.defer(ephemeral=True)

            assigned = await database.assign_slot_to_member(
                slot['id'], str(member.id), member.display_name
            )
            if not assigned:
                await sel_interaction.followup.send(
                    "❌ Slot was already taken.", ephemeral=True
                )
                return

            request_id = await database.create_request(
                guild_id=str(sel_interaction.guild_id),
                operation_id=op['id'],
                member_id=str(member.id),
                member_name=member.display_name,
                slot_label=slot['label'],
                slot_id=slot['id'],
                unit_role=_get_unit_role(member),
            )
            await database.approve_request(request_id, sel_interaction.user.display_name)

            await sel_interaction.followup.send(
                f"✅ Assigned **{member.display_name}** to **{slot['label']}**.",
                ephemeral=True,
            )

            try:
                await member.send(
                    f"✅ **Slot Assigned**\n"
                    f"An admin has assigned you to **{slot['label']}** "
                    f"for operation **{op['name']}**."
                )
            except (discord.Forbidden, discord.NotFound):
                pass

            asyncio.create_task(_update_orbat(bot_ref, sel_interaction.guild, op))

        view = SquadSelectView(
            squads=squads,
            all_slots=available,
            operation_id=op['id'],
            pending_ids=pending_ids,
            approved_ids=approved_ids,
            bot=self.bot,
            on_select=_on_slot_selected,
        )
        await interaction.followup.send(
            f"Select a slot to assign to **{member.display_name}**:",
            view=view,
            ephemeral=True,
        )

    @app_commands.command(
        name='post-event',
        description='Post an event announcement with mission name and start time (Admin only)',
    )
    @app_commands.describe(
        channel='Channel to post in (defaults to current channel)',
        mission_name='Mission name — defaults to the active operation name',
        event_time='Event start time, e.g. 25/06/2025 19:00 — defaults to the active operation time',
    )
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

        # Resolve mission name and event time from the active operation if not provided
        op = await database.get_active_operation(str(interaction.guild_id))

        if mission_name is None:
            if op:
                mission_name = op['name']
            else:
                await interaction.followup.send(
                    "❌ No active operation and no `mission_name` provided. "
                    "Pass a mission name or run `/setup-slots` first.",
                    ephemeral=True,
                )
                return

        parsed_time = None
        if event_time:
            try:
                tz_name = await database.get_guild_timezone(str(interaction.guild_id))
                parsed_time = _parse_event_time(event_time, tz_name)
            except ValueError as e:
                await interaction.followup.send(f"❌ {e}", ephemeral=True)
                return
        elif op and op['event_time']:
            from datetime import timezone as _tz
            parsed_time = op['event_time']
            if hasattr(parsed_time, 'tzinfo') and parsed_time.tzinfo is None:
                parsed_time = parsed_time.replace(tzinfo=_tz.utc)

        embed = discord.Embed(
            title=f"🎖️ {mission_name}",
            color=discord.Color.dark_red(),
        )

        if parsed_time:
            ts = int(parsed_time.timestamp() if hasattr(parsed_time, 'timestamp') else parsed_time)
            embed.add_field(
                name='🕐 Operation starts',
                value=f"<t:{ts}:F>  (<t:{ts}:R>)",
                inline=False,
            )

        orbat_channel = discord.utils.get(interaction.guild.text_channels, name='orbat')
        orbat_ref = orbat_channel.mention if orbat_channel else '`#orbat`'
        embed.add_field(
            name='📋 Sign up',
            value=f'Head to {orbat_ref} to view available slots and request your position.',
            inline=False,
        )
        embed.set_footer(text=f'Posted by {interaction.user.display_name}')
        embed.timestamp = discord.utils.utcnow()

        try:
            await target.send(embed=embed)
        except discord.Forbidden:
            await interaction.followup.send(
                f"❌ I don't have permission to post in {target.mention}.", ephemeral=True
            )
            return

        await interaction.followup.send(
            f"✅ Event posted in {target.mention}.", ephemeral=True
        )

    @app_commands.command(
        name='archive-old-approvals',
        description='Move already-approved messages from #slot-approvals to #approval-archive (Admin only)',
    )
    @app_commands.default_permissions(manage_guild=True)
    async def archive_old_approvals(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        approvals_channel = discord.utils.get(
            interaction.guild.text_channels, name='slot-approvals'
        )
        if not approvals_channel:
            await interaction.followup.send(
                "❌ No `#slot-approvals` channel found.", ephemeral=True
            )
            return

        archive_channel = discord.utils.get(
            interaction.guild.text_channels, name='approval-archive'
        )
        if archive_channel is None:
            try:
                archive_channel = await interaction.guild.create_text_channel('approval-archive')
            except discord.Forbidden:
                await interaction.followup.send(
                    "❌ Cannot create `#approval-archive` — grant me **Manage Channels**.",
                    ephemeral=True,
                )
                return

        moved = 0
        skipped = 0
        bot_id = self.bot.user.id

        async for message in approvals_channel.history(limit=500, oldest_first=True):
            if message.author.id != bot_id:
                continue
            if not message.embeds:
                continue
            embed = message.embeds[0]
            # Approved messages are green and have an "Approved" field
            is_green = (
                embed.color is not None
                and embed.color.value == discord.Color.green().value
            )
            has_approval_field = any(
                'approved' in (f.name or '').lower() for f in embed.fields
            )
            if not (is_green and has_approval_field):
                continue
            try:
                await archive_channel.send(embed=embed)
                await message.delete()
                moved += 1
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                skipped += 1

        await interaction.followup.send(
            f"✅ Moved **{moved}** approved message(s) to {archive_channel.mention}."
            + (f"\n⚠️ **{skipped}** could not be moved (permissions or already deleted)." if skipped else ""),
            ephemeral=True,
        )

    @app_commands.command(
        name='sync',
        description='Force-sync slash commands with Discord and refresh ORBAT (Admin only)',
    )
    @app_commands.default_permissions(manage_guild=True)
    async def sync(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        synced = await self.bot.tree.sync(guild=interaction.guild)

        op = await database.get_active_operation(str(interaction.guild_id))
        orbat_note = ""
        if op:
            try:
                await _update_orbat(self.bot, interaction.guild, op, raise_errors=True)
                orbat_note = "\n📋 ORBAT refreshed."
            except Exception as e:
                orbat_note = f"\n⚠️ ORBAT refresh failed: `{e}`"

        await interaction.followup.send(
            f"✅ Synced **{len(synced)}** command(s) to this server.{orbat_note}",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))
