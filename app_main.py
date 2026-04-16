import asyncio
import os
import traceback

import uvicorn

from api_server import create_api_app
from bot import ORBATBot


def _resolve_port() -> int:
    raw_port = os.getenv("PORT") or os.getenv("API_PORT") or "8000"
    try:
        return int(raw_port)
    except ValueError:
        print(f"Invalid PORT value '{raw_port}', falling back to 8000.")
        return 8000


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

    bot = ORBATBot()
    app = create_api_app(bot)

    host = os.getenv("API_HOST", "0.0.0.0")
    port = _resolve_port()
    print(f"Starting API server on {host}:{port}")
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level=os.getenv("API_LOG_LEVEL", "info"),
        access_log=False,
    )
    server = uvicorn.Server(config)

    bot_task = None
    if token:
        bot_task = asyncio.create_task(_run_bot(bot, token), name="discord-bot")
    else:
        print("DISCORD_TOKEN is not set. API is running without Discord bot.")
    try:
        await server.serve()
    finally:
        if bot_task and not bot_task.done():
            bot_task.cancel()
            try:
                await bot_task
            except asyncio.CancelledError:
                pass


def main():
    asyncio.run(_main())


if __name__ == "__main__":
    main()
