import asyncio
import os
import traceback

import uvicorn

from api_server import create_api_app


def _resolve_port() -> int:
    raw_port = os.getenv("PORT")
    if not raw_port:
        # On Railway web services PORT is injected. If it is missing, this is
        # very likely not running as an HTTP web service.
        raise RuntimeError(
            "PORT is not set. On Railway this usually means the service is not configured as a web service."
        )
    try:
        return int(raw_port)
    except ValueError:
        raise RuntimeError(f"Invalid PORT value '{raw_port}'.")


class _BotStub:
    def get_guild(self, _guild_id):
        return None

    def add_view(self, _view):
        return None


async def _run_bot(app, token: str):
    try:
        from bot import ORBATBot

        bot = ORBATBot()
        app.state.bot = bot
        await bot.start(token)
    except asyncio.CancelledError:
        raise
    except Exception:
        print("Discord bot crashed:")
        traceback.print_exc()


async def _main():
    token = os.getenv("DISCORD_TOKEN")

    app = create_api_app(_BotStub())

    host = "0.0.0.0"
    port = _resolve_port()
    print(f"PORT from env: {os.getenv('PORT')}")
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
        bot_task = asyncio.create_task(_run_bot(app, token), name="discord-bot")
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
