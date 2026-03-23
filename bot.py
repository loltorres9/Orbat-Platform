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
        await database.init_db()
        await self.load_extension('cogs.slots')
        await self.load_extension('cogs.admin')

        # Re-register approval views for all pending requests so buttons
        # continue to work after a bot restart.
        pending = await database.get_all_pending_requests()
        for req in pending:
            self.add_view(ApprovalView(request_id=req['id'], bot=self))

        await self.tree.sync()
        print(f"Synced slash commands. {len(pending)} pending request view(s) restored.")

    async def on_ready(self):
        print(f"✅ {self.user} is online!")
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
