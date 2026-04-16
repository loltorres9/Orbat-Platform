import asyncio
import os
import traceback

import uvicorn

from api_server import create_api_app
from bot import ORBATBot


async def _run_bot(bot: ORBATBot, token: str):
    try:
        await bot.start(token)
    except asyncio.CancelledError:
        raise
    except Exception:
        print("Discord bot crashed:")
        traceback.print_exc()


async def _main():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN is not set. Check your .env file or Railway variables.")

    bot = ORBATBot()
    app = create_api_app(bot)

    host = os.getenv("API_HOST", "0.0.0.0")
    port = int(os.getenv("PORT", os.getenv("API_PORT", "8000")))
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level=os.getenv("API_LOG_LEVEL", "info"),
        access_log=False,
    )
    server = uvicorn.Server(config)

    bot_task = asyncio.create_task(_run_bot(bot, token), name="discord-bot")
    try:
        await server.serve()
    finally:
        if not bot_task.done():
            bot_task.cancel()
            try:
                await bot_task
            except asyncio.CancelledError:
                pass


def main():
    asyncio.run(_main())


if __name__ == "__main__":
    main()
