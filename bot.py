import asyncio
import os
from datetime import timezone

import discord
import uvicorn
from discord.ext import commands, tasks
from dotenv import load_dotenv

from api_server import create_api_app
from cogs.slots import ApprovalView, OrbatRequestButton
from utils import database

load_dotenv()


class ORBATBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(
            command_prefix="!",
            intents=intents,
            description="Arma 3 ORBAT Slot Management Bot",
        )
        self.api_server = None
        self.api_task = None

    async def setup_hook(self):
        import traceback

        print("--- setup_hook start ---")

        await database.init_db()
        print("Database initialized.")

        try:
            await self.load_extension("cogs.slots")
            print("Loaded cogs.slots")
        except Exception:
            print("Failed to load cogs.slots:")
            traceback.print_exc()

        try:
            await self.load_extension("cogs.admin")
            print("Loaded cogs.admin")
        except Exception:
            print("Failed to load cogs.admin:")
            traceback.print_exc()

        registered = [c.name for c in self.tree.get_commands()]
        print(f"Commands registered in tree: {registered}")

        self.add_view(OrbatRequestButton(bot=self))

        pending = await database.get_all_pending_requests()
        for req in pending:
            self.add_view(ApprovalView(request_id=req["id"], bot=self))
        print(f"{len(pending)} pending view(s) restored.")

        self.reminder_task.start()
        print("Reminder task started.")

        print("--- setup_hook end ---")

    async def start_api_server(self):
        if self.api_task is not None:
            return
        host = os.getenv("API_HOST", "0.0.0.0")
        port = int(os.getenv("PORT", os.getenv("API_PORT", "8000")))
        api_app = create_api_app(self)
        config = uvicorn.Config(
            api_app,
            host=host,
            port=port,
            log_level=os.getenv("API_LOG_LEVEL", "info"),
            access_log=False,
        )
        self.api_server = uvicorn.Server(config)
        self.api_task = asyncio.create_task(self.api_server.serve())
        print(f"API server starting on {host}:{port}")

    @tasks.loop(minutes=1)
    async def reminder_task(self):
        ops = await database.get_operations_needing_reminder()
        for op in ops:
            await database.mark_reminder_fired(op["id"])
            members = await database.get_approved_member_ids(op["id"])
            if not members:
                continue

            event_dt = op["event_time"]
            if event_dt.tzinfo is None:
                event_dt = event_dt.replace(tzinfo=timezone.utc)
            event_ts = int(event_dt.timestamp())

            guild = discord.utils.get(self.guilds, id=int(op["guild_id"]))
            if not guild:
                continue

            for member_id, slot_label in members:
                try:
                    member = await guild.fetch_member(int(member_id))
                    await member.send(
                        f"Operation reminder - {op['name']}\n"
                        f"Your operation starts <t:{event_ts}:R> (<t:{event_ts}:F>).\n"
                        f"Your slot: {slot_label}\n"
                        "Get ready."
                    )
                except (discord.Forbidden, discord.NotFound):
                    pass

            orbat_channel = discord.utils.get(guild.text_channels, name="orbat")
            if orbat_channel:
                mentions = " ".join(f"<@{member_id}>" for member_id, _ in members)
                try:
                    await orbat_channel.send(
                        f"Operation reminder - {op['name']} starts <t:{event_ts}:R>.\n{mentions}"
                    )
                except (discord.Forbidden, discord.HTTPException):
                    pass

    @reminder_task.before_loop
    async def before_reminder_task(self):
        await self.wait_until_ready()

    async def on_ready(self):
        print(f"on_ready fired. Guilds: {[g.name for g in self.guilds]}")
        for guild in self.guilds:
            try:
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                print(f"Guild sync '{guild.name}': {len(synced)} command(s).")
            except Exception as exc:
                print(f"Guild sync failed for '{guild.name}': {exc}")

    async def on_guild_join(self, guild: discord.Guild):
        try:
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            print(f"Joined '{guild.name}' - synced {len(synced)} command(s).")
        except Exception as exc:
            print(f"Guild sync failed for '{guild.name}': {exc}")


async def _run():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN is not set. Check your .env file or Railway variables.")
    bot = ORBATBot()
    await bot.start_api_server()
    await bot.start(token)


def main():
    asyncio.run(_run())


if __name__ == "__main__":
    main()
