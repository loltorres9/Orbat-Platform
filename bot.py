import asyncio
import os

import discord
from discord.ext import commands
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

        try:
            synced = await self.tree.sync()
            print(f"✅ Global sync: {len(synced)} command(s). {len(pending)} pending view(s) restored.")
        except Exception:
            print("❌ Global tree.sync() failed:")
            traceback.print_exc()

        print("--- setup_hook end ---")

    async def on_ready(self):
        print(f"on_ready fired. Guilds: {[g.name for g in self.guilds]}")
        # Guild-specific syncs are instant — no waiting for global propagation.
        for guild in self.guilds:
            try:
                synced = await self.tree.sync(guild=guild)
                print(f"✅ Guild sync '{guild.name}': {len(synced)} command(s).")
            except Exception as e:
                print(f"❌ Guild sync failed for '{guild.name}': {e}")
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name='for /request-slot',
            )
        )


def main():
    token = os.getenv('DISCORD_TOKEN')
    if not token:
        raise RuntimeError("DISCORD_TOKEN is not set. Check your .env file or Railway variables.")

    bot = ORBATBot()
    asyncio.run(bot.start(token))


if __name__ == '__main__':
    main()
