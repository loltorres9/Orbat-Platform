import asyncio
import os
from datetime import timezone

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

from utils import database
from cogs.slots import ApprovalView

load_dotenv()


class ORBATBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(
            command_prefix='!',
            intents=intents,
            description='Arma 3 ORBAT Slot Management Bot',
        )

    async def setup_hook(self):
        import traceback
        print("--- setup_hook start ---")

        await database.init_db()
        print("✅ Database initialised.")

        try:
            await self.load_extension('cogs.slots')
            print("✅ Loaded cogs.slots")
        except Exception:
            print("❌ Failed to load cogs.slots:")
            traceback.print_exc()

        try:
            await self.load_extension('cogs.admin')
            print("✅ Loaded cogs.admin")
        except Exception:
            print("❌ Failed to load cogs.admin:")
            traceback.print_exc()

        registered = [c.name for c in self.tree.get_commands()]
        print(f"Commands registered in tree: {registered}")

        # Re-register approval views for all pending requests so buttons
        # continue to work after a bot restart.
        pending = await database.get_all_pending_requests()
        for req in pending:
            self.add_view(ApprovalView(request_id=req['id'], bot=self))

        print(f"{len(pending)} pending view(s) restored.")

        self.reminder_task.start()
        print("✅ Reminder task started.")
        print("--- setup_hook end ---")

    @tasks.loop(minutes=1)
    async def reminder_task(self):
        ops = await database.get_operations_needing_reminder()
        for op in ops:
            await database.mark_reminder_fired(op['id'])
            members = await database.get_approved_member_ids(op['id'])
            if not members:
                continue

            event_dt = op['event_time']
            if event_dt.tzinfo is None:
                event_dt = event_dt.replace(tzinfo=timezone.utc)
            event_ts = int(event_dt.timestamp())

            for member_id, slot_label in members:
                for guild in self.guilds:
                    if str(guild.id) == str(op['guild_id']):
                        try:
                            member = await guild.fetch_member(int(member_id))
                            await member.send(
                                f"⏰ **Operation Reminder — {op['name']}**\n"
                                f"Your operation starts <t:{event_ts}:R> (<t:{event_ts}:F>).\n"
                                f"Your slot: **{slot_label}**\n"
                                f"Get ready!"
                            )
                        except (discord.Forbidden, discord.NotFound):
                            pass
                        break

    @reminder_task.before_loop
    async def before_reminder_task(self):
        await self.wait_until_ready()

    async def on_ready(self):
        print(f"on_ready fired. Guilds: {[g.name for g in self.guilds]}")
        # Copy global commands into each guild and sync — this is instant,
        # unlike global sync which can take up to an hour to propagate.
        for guild in self.guilds:
            try:
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                print(f"✅ Guild sync '{guild.name}': {len(synced)} command(s).")
            except Exception as e:
                print(f"❌ Guild sync failed for '{guild.name}': {e}")


    async def on_guild_join(self, guild: discord.Guild):
        """Sync commands when the bot is added to a new server while already running."""
        try:
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            print(f"✅ Joined '{guild.name}' — synced {len(synced)} command(s).")
        except Exception as e:
            print(f"❌ Guild sync failed for '{guild.name}': {e}")


def main():
    token = os.getenv('DISCORD_TOKEN')
    if not token:
        raise RuntimeError("DISCORD_TOKEN is not set. Check your .env file or Railway variables.")

    bot = ORBATBot()
    asyncio.run(bot.start(token))


if __name__ == '__main__':
    main()
