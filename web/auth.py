import os
import httpx
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse

from utils import database

router = APIRouter(prefix='/api/auth', tags=['auth'])

DISCORD_CLIENT_ID = os.getenv('DISCORD_CLIENT_ID', '')
DISCORD_CLIENT_SECRET = os.getenv('DISCORD_CLIENT_SECRET', '')
DISCORD_REDIRECT_URI = os.getenv('DISCORD_REDIRECT_URI', '')
FRONTEND_URL = os.getenv('FRONTEND_URL', 'http://localhost:5173')

DISCORD_API = 'https://discord.com/api/v10'
DISCORD_OAUTH_URL = 'https://discord.com/api/oauth2/authorize'
DISCORD_TOKEN_URL = 'https://discord.com/api/oauth2/token'


@router.get('/login')
async def login():
    params = {
        'client_id': DISCORD_CLIENT_ID,
        'redirect_uri': DISCORD_REDIRECT_URI,
        'response_type': 'code',
        'scope': 'identify guilds',
    }
    url = f"{DISCORD_OAUTH_URL}?{'&'.join(f'{k}={v}' for k, v in params.items())}"
    return RedirectResponse(url)


@router.get('/callback')
async def callback(code: str):
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(DISCORD_TOKEN_URL, data={
            'client_id': DISCORD_CLIENT_ID,
            'client_secret': DISCORD_CLIENT_SECRET,
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': DISCORD_REDIRECT_URI,
        })
        if token_resp.status_code != 200:
            raise HTTPException(400, 'Failed to exchange code for token')
        tokens = token_resp.json()

        user_resp = await client.get(
            f'{DISCORD_API}/users/@me',
            headers={'Authorization': f"Bearer {tokens['access_token']}"},
        )
        if user_resp.status_code != 200:
            raise HTTPException(400, 'Failed to fetch user info')
        user = user_resp.json()

        guilds_resp = await client.get(
            f'{DISCORD_API}/users/@me/guilds',
            headers={'Authorization': f"Bearer {tokens['access_token']}"},
        )
        guilds = guilds_resp.json() if guilds_resp.status_code == 200 else []

    avatar = None
    if user.get('avatar'):
        avatar = f"https://cdn.discordapp.com/avatars/{user['id']}/{user['avatar']}.png"

    session_id = await database.create_web_session(
        discord_user_id=user['id'],
        discord_username=user.get('global_name') or user['username'],
        discord_avatar=avatar,
        access_token=tokens['access_token'],
        refresh_token=tokens.get('refresh_token', ''),
        guilds=[{'id': g['id'], 'name': g['name'], 'icon': g.get('icon'),
                 'owner': g.get('owner', False),
                 'permissions': g.get('permissions', '0')} for g in guilds],
    )

    return RedirectResponse(f"{FRONTEND_URL}/auth/callback?session={session_id}")


@router.get('/me')
async def me(request: Request):
    session_id = request.headers.get('Authorization', '').replace('Bearer ', '')
    if not session_id:
        raise HTTPException(401, 'No session')

    session = await database.get_web_session(session_id)
    if not session:
        raise HTTPException(401, 'Invalid or expired session')

    return {
        'id': session['discord_user_id'],
        'username': session['discord_username'],
        'avatar': session['discord_avatar'],
        'guilds': session['guilds'] or [],
    }


@router.post('/logout')
async def logout(request: Request):
    session_id = request.headers.get('Authorization', '').replace('Bearer ', '')
    if session_id:
        await database.delete_web_session(session_id)
    return {'ok': True}
