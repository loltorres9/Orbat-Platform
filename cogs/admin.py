import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from utils import database, sheets
from cogs.slots import _build_orbat_embed, _build_select_menus, _get_unit_role, _update_orbat

ORBAT_CHANNEL_NAME = 'orbat'


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name='setup-slots',
        description='Load slots from a Google Sheet for the current operation (Admin only)',
    )
    @app_commands.describe(sheet_url='Full Google Sheets URL for this operation')
    @app_commands.default_permissions(manage_guild=True)
    async def setup_slots(self, interaction: discord.Interaction, sheet_url: str):
        await interaction.response.defer(ephemeral=True)

        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, sheets.load_slots, sheet_url)
        except ValueError as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)
            return
        except Exception as e:
            await interaction.followup.send(
                f"❌ Failed to read the sheet. Make sure you've shared it with the service account.\n`{e}`",
                ephemeral=True,
            )
            return

        await database.create_operation(
            guild_id=str(interaction.guild_id),
            name=data['operation_name'],
            sheet_url=sheet_url,
            sheet_id=data['sheet_id'],
            squad_col=data['squad_col'],
            role_col=data['role_col'],
            status_col=data['status_col'],
            assigned_col=data['assigned_col'],
        )

        slot_count = len(data['slots'])
        confirm_embed = discord.Embed(
            title='✅ Operation Loaded',
            description=(
                f"**{data['operation_name']}**\n"
                f"Found **{slot_count}** available slot(s).\n\n"
                f"Members can now use `/request-slot` to sign up."
            ),
            color=discord.Color.green(),
        )

        # Auto-post ORBAT to #orbat (create channel if needed)
        orbat_channel = discord.utils.get(
            interaction.guild.text_channels, name=ORBAT_CHANNEL_NAME
        )
        if not orbat_channel:
            try:
                orbat_channel = await interaction.guild.create_text_channel(
                    ORBAT_CHANNEL_NAME,
                    topic='Live ORBAT for the current operation',
                )
            except discord.Forbidden:
                orbat_channel = None

        if orbat_channel:
            try:
                op = await database.get_active_operation(str(interaction.guild_id))
                loop = asyncio.get_event_loop()
                all_data = await loop.run_in_executor(None, sheets.load_all_slots, sheet_url)
                pending_rows = set(await database.get_pending_slots(op['id']))
                orbat_embed = _build_orbat_embed(all_data['operation_name'], all_data['slots'], pending_rows)
                msg = await orbat_channel.send(embed=orbat_embed)
                await database.save_orbat_message(
                    str(interaction.guild_id), str(orbat_channel.id), str(msg.id)
                )
                confirm_embed.description += f"\n\n📋 ORBAT posted to {orbat_channel.mention}."
            except Exception:
                pass  # ORBAT post failure is non-fatal

        await interaction.followup.send(embed=confirm_embed, ephemeral=True)

    @app_commands.command(
        name='clear-slot',
        description='Remove a member from an approved slot (Admin only)',
    )
    @app_commands.default_permissions(manage_guild=True)
    async def clear_slot(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        op = await database.get_active_operation(str(interaction.guild_id))
        if not op:
            await interaction.followup.send("❌ No active operation.", ephemeral=True)
            return

        approved = await database.get_approved_requests(op['id'])
        if not approved:
            await interaction.followup.send(
                "ℹ️ No approved slots to clear.", ephemeral=True
            )
            return

        options = [
            discord.SelectOption(
                label=f"{req['member_name']} — {req['slot_label']}"[:100],
                value=str(req['id']),
                description=f"Row {req['sheet_row']}",
            )
            for req in approved[:25]
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
            if not req or req['status'] != 'approved':
                await sel_interaction.response.send_message(
                    "❌ That slot is no longer approved.", ephemeral=True
                )
                return

            # Clear the sheet cell
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None,
                    sheets.clear_slot,
                    op['sheet_id'],
                    req['sheet_row'],
                    req['sheet_col'],
                    req['member_name'],
                )
            except Exception as e:
                await sel_interaction.response.send_message(
                    f"⚠️ Could not update the sheet: `{e}`\nPlease clear it manually.",
                    ephemeral=True,
                )
                return

            await database.cancel_request_by_id(request_id)

            await sel_interaction.response.send_message(
                f"✅ Cleared **{req['slot_label']}** — removed **{req['member_name']}**.",
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

            # Refresh ORBAT
            asyncio.create_task(_update_orbat(bot_ref, sel_interaction.guild, op))

        select.callback = _select_callback
        view = discord.ui.View(timeout=120)
        view.add_item(select)
        await interaction.followup.send(
            "Select the slot to clear:", view=view, ephemeral=True
        )

    @app_commands.command(
        name='current-operation',
        description='Show which operation is currently active',
    )
    async def current_operation(self, interaction: discord.Interaction):
        op = await database.get_active_operation(str(interaction.guild_id))
        if not op:
            await interaction.response.send_message(
                "No active operation. An admin can load one with `/setup-slots`.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title='🎖️ Current Operation',
            description=f"**{op['name']}**\n[View Sheet]({op['sheet_url']})",
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


    @app_commands.command(
        name='assign-slot',
        description='Directly assign a member to a slot without going through approval (Admin only)',
    )
    @app_commands.describe(member='The Discord member to assign')
    @app_commands.default_permissions(manage_guild=True)
    async def assign_slot(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=True)

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
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, sheets.load_slots, op['sheet_url'])
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to load slots: `{e}`", ephemeral=True)
            return

        if not data['slots']:
            await interaction.followup.send("ℹ️ No available slots found.", ephemeral=True)
            return

        pending_rows = set(await database.get_pending_slots(op['id']))
        approved_rows = set(await database.get_approved_slots(op['id']))
        menus = _build_select_menus(data['slots'], pending_rows, approved_rows)

        if not menus:
            await interaction.followup.send("ℹ️ All slots are currently filled or pending.", ephemeral=True)
            return

        slots_by_value = {s['value']: s for s in data['slots']}
        bot_ref = self.bot

        async def _select_callback(sel_interaction: discord.Interaction):
            selected_value = sel_interaction.data['values'][0]
            slot = slots_by_value.get(selected_value)
            if not slot:
                await sel_interaction.response.send_message("❌ Slot not found.", ephemeral=True)
                return

            # Re-check at selection time
            current_approved = set(await database.get_approved_slots(op['id']))
            if slot['row'] in current_approved:
                await sel_interaction.response.send_message(
                    "❌ That slot was just filled. Please pick another.", ephemeral=True
                )
                return

            await sel_interaction.response.defer(ephemeral=True)

            # Write to sheet
            try:
                unit_role = _get_unit_role(member)
                await loop.run_in_executor(
                    None,
                    sheets.assign_slot,
                    op['sheet_id'],
                    slot['row'],
                    slot.get('col'),
                    member.display_name,
                    unit_role,
                )
            except Exception as e:
                await sel_interaction.followup.send(
                    f"⚠️ Could not update the sheet: `{e}`\nPlease update it manually.",
                    ephemeral=True,
                )
                return

            # Record directly as approved
            request_id = await database.create_request(
                guild_id=str(sel_interaction.guild_id),
                operation_id=op['id'],
                member_id=str(member.id),
                member_name=member.display_name,
                slot_label=slot['label'],
                sheet_row=slot['row'],
                sheet_col=slot.get('col'),
                unit_role=_get_unit_role(member),
            )
            await database.approve_request(request_id, sel_interaction.user.display_name)

            await sel_interaction.followup.send(
                f"✅ Assigned **{member.display_name}** to **{slot['label']}**.",
                ephemeral=True,
            )

            # DM the member
            try:
                await member.send(
                    f"✅ **Slot Assigned**\n"
                    f"An admin has assigned you to **{slot['label']}** "
                    f"for operation **{op['name']}**."
                )
            except (discord.Forbidden, discord.NotFound):
                pass

            asyncio.create_task(_update_orbat(bot_ref, sel_interaction.guild, op))

        view = discord.ui.View(timeout=120)
        for menu in menus:
            menu.callback = _select_callback
            view.add_item(menu)

        await interaction.followup.send(
            f"Select a slot to assign to **{member.display_name}**:",
            view=view,
            ephemeral=True,
        )

    @app_commands.command(
        name='sync',
        description='Force-sync slash commands with Discord (Admin only)',
    )
    @app_commands.default_permissions(manage_guild=True)
    async def sync(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        synced = await self.bot.tree.sync(guild=interaction.guild)
        await interaction.followup.send(
            f"✅ Synced **{len(synced)}** command(s) to this server.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))
