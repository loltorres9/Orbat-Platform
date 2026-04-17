import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from web.auth import router as auth_router
from web.routers.operations import router as operations_router
from web.routers.squads import router as squads_router
from web.routers.slots import router as slots_router
from web.routers.orbat import router as orbat_router


def create_app(bot=None) -> FastAPI:
    app = FastAPI(title='ORBAT Platform API', version='1.0.0')

    frontend_url = os.getenv('FRONTEND_URL', 'http://localhost:5173')
    from urllib.parse import urlparse
    parsed = urlparse(frontend_url)
    frontend_origin = f"{parsed.scheme}://{parsed.netloc}"
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[frontend_origin, 'http://localhost:5173', 'http://localhost:4173'],
        allow_credentials=True,
        allow_methods=['*'],
        allow_headers=['*'],
    )

    if bot:
        app.state.bot = bot

    app.include_router(auth_router)
    app.include_router(operations_router)
    app.include_router(squads_router)
    app.include_router(slots_router)
    app.include_router(orbat_router)

    @app.get('/api/health')
    async def health():
        return {'status': 'ok'}

    return app
