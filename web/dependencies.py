from fastapi import Request, HTTPException

from utils import database


async def get_current_user(request: Request) -> dict:
    session_id = request.headers.get('Authorization', '').replace('Bearer ', '')
    if not session_id:
        raise HTTPException(401, 'Authentication required')

    session = await database.get_web_session(session_id)
    if not session:
        raise HTTPException(401, 'Invalid or expired session')

    return {
        'id': session['discord_user_id'],
        'username': session['discord_username'],
        'avatar': session['discord_avatar'],
        'guilds': session['guilds'] or [],
    }


def check_guild_access(user: dict, guild_id: str) -> bool:
    """Check if user is a member of the specified guild."""
    return any(g['id'] == guild_id for g in user.get('guilds', []))


def check_guild_admin(user: dict, guild_id: str) -> bool:
    """Check if user has admin permissions in the guild (manage_guild or owner)."""
    for g in user.get('guilds', []):
        if g['id'] == guild_id:
            if g.get('owner'):
                return True
            perms = int(g.get('permissions', '0'))
            # MANAGE_GUILD = 0x20, ADMINISTRATOR = 0x8
            return bool(perms & (0x20 | 0x8))
    return False
