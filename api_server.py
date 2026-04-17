import asyncio
import contextlib
import json
import os
import secrets
import traceback
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from datetime import datetime, timedelta, timezone
from typing import Optional

import asyncpg
import httpx
from fastapi import Cookie, FastAPI, Header, HTTPException, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from utils import database


class SessionData(BaseModel):
    guild_id: str
    user_id: str
    username: str
    avatar_url: Optional[str] = None
    expires_at: datetime


class OperationCreateInput(BaseModel):
    guild_id: str
    name: str
    event_time: Optional[datetime] = None
    reminder_minutes: int = 30
    activate: bool = True


class SquadCreateInput(BaseModel):
    name: str
    display_order: Optional[int] = None
    column_index: Optional[int] = None


class SquadUpdateInput(BaseModel):
    name: Optional[str] = None
    display_order: Optional[int] = None
    column_index: Optional[int] = None


class SlotCreateInput(BaseModel):
    squad_id: int
    role_name: str
    display_order: Optional[int] = None
    team: Optional[str] = None


class SlotUpdateInput(BaseModel):
    role_name: Optional[str] = None
    display_order: Optional[int] = None
    squad_id: Optional[int] = None
    team: Optional[str] = None


class OperationLaneNamesInput(BaseModel):
    lane_name_left: Optional[str] = None
    lane_name_center: Optional[str] = None
    lane_name_right: Optional[str] = None


class SlotRequestInput(BaseModel):
    guild_id: str


class DiscordCodeInput(BaseModel):
    code: str
    guild_id: Optional[str] = None


class AdminUpsertInput(BaseModel):
    user_id: str
    username: Optional[str] = None


class WebSocketHub:
    def __init__(self):
        self._connections: dict[int, set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, operation_id: int, websocket: WebSocket):
        await websocket.accept()
        async with self._lock:
            self._connections.setdefault(operation_id, set()).add(websocket)

    async def disconnect(self, operation_id: int, websocket: WebSocket):
        async with self._lock:
            peers = self._connections.get(operation_id)
            if not peers:
                return
            peers.discard(websocket)
            if not peers:
                self._connections.pop(operation_id, None)

    async def broadcast(self, operation_id: int, payload: dict):
        async with self._lock:
            peers = list(self._connections.get(operation_id, []))
        if not peers:
            return
        message = json.dumps(payload)
        stale: list[WebSocket] = []
        for peer in peers:
            try:
                await peer.send_text(message)
            except Exception:
                stale.append(peer)
        if stale:
            async with self._lock:
                bucket = self._connections.get(operation_id, set())
                for peer in stale:
                    bucket.discard(peer)


async def _discord_oauth_exchange(code: str) -> tuple[dict, dict]:
    client_id = os.getenv("DISCORD_CLIENT_ID")
    client_secret = os.getenv("DISCORD_CLIENT_SECRET")
    redirect_uri = os.getenv("DISCORD_REDIRECT_URI")
    if not client_id or not client_secret or not redirect_uri:
        raise HTTPException(
            status_code=500,
            detail="Discord OAuth is not configured. Set DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET, DISCORD_REDIRECT_URI.",
        )

    token_payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    async with httpx.AsyncClient(timeout=20) as client:
        token_response = await client.post(
            "https://discord.com/api/oauth2/token",
            data=token_payload,
            headers=headers,
        )
        if token_response.status_code >= 400:
            raise HTTPException(status_code=401, detail="Failed to exchange Discord OAuth code.")
        token_data = token_response.json()

        user_response = await client.get(
            "https://discord.com/api/users/@me",
            headers={"Authorization": f"Bearer {token_data['access_token']}"},
        )
        if user_response.status_code >= 400:
            raise HTTPException(status_code=401, detail="Failed to fetch Discord user profile.")
        user_data = user_response.json()

    return token_data, user_data


def _serialize_state(guild_id: Optional[str], return_to: Optional[str]) -> str:
    return json.dumps(
        {
            "guild_id": guild_id or "",
            "return_to": return_to or "",
        }
    )


def _deserialize_state(raw_state: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    if not raw_state:
        return None, None
    try:
        data = json.loads(raw_state)
        guild_id = data.get("guild_id") or None
        return_to = data.get("return_to") or None
        return guild_id, return_to
    except Exception:
        return None, None


def _with_error_param(target_url: str, message: str) -> str:
    try:
        parsed = urlparse(target_url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query["auth_error"] = message
        return urlunparse(parsed._replace(query=urlencode(query)))
    except Exception:
        return target_url


def _field_was_provided(payload: BaseModel, field_name: str) -> bool:
    field_set = getattr(payload, "model_fields_set", None)
    if field_set is None:
        field_set = getattr(payload, "__fields_set__", set())
    return field_name in field_set


async def _discord_member_has_admin_permissions(app: FastAPI, guild_id: str, user_id: str) -> bool:
    bot = app.state.bot
    guild = bot.get_guild(int(guild_id))
    if not guild:
        return False
    member = guild.get_member(int(user_id))
    if member is None:
        try:
            member = await guild.fetch_member(int(user_id))
        except Exception:
            return False
    perms = member.guild_permissions
    return bool(perms.administrator or perms.manage_guild)


async def _require_guild_admin(app: FastAPI, session, guild_id: str):
    if await database.is_web_admin(guild_id, session["user_id"]):
        return
    if await _discord_member_has_admin_permissions(app, guild_id, session["user_id"]):
        return
    raise HTTPException(status_code=403, detail="Admin access required for this guild.")


async def _session_from_token(session_token: Optional[str], header_token: Optional[str] = None):
    token = session_token or header_token
    if not token:
        raise HTTPException(status_code=401, detail="Missing session.")
    session = await database.get_web_session(token)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired or invalid.")
    return session


def _serialize_session(session) -> SessionData:
    return SessionData(
        guild_id=session["guild_id"],
        user_id=session["user_id"],
        username=session["username"],
        avatar_url=session["avatar_url"],
        expires_at=session["expires_at"],
    )


def _build_avatar_url(user_data: dict) -> Optional[str]:
    avatar_hash = user_data.get("avatar")
    if not avatar_hash:
        return None
    return f"https://cdn.discordapp.com/avatars/{user_data['id']}/{avatar_hash}.png"


async def _post_approval_request(
    app: FastAPI,
    request_id: int,
    operation,
    slot,
    requester_member,
    requester_user_id: str,
    unit_role_name: Optional[str],
):
    try:
        import discord
        from cogs.slots import APPROVAL_CHANNEL_NAME, ApprovalView, _resolve_unit_role_obj
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Discord components unavailable: {exc}") from exc

    bot = app.state.bot
    guild = bot.get_guild(int(operation["guild_id"]))
    if not guild:
        raise HTTPException(status_code=503, detail="Bot is not connected to that guild.")

    approval_channel = discord.utils.get(guild.text_channels, name=APPROVAL_CHANNEL_NAME)
    if approval_channel is None:
        try:
            approval_channel = await guild.create_text_channel(
                APPROVAL_CHANNEL_NAME,
                topic="Slot approval requests for Arma 3 operations",
            )
        except discord.Forbidden as exc:
            raise HTTPException(status_code=403, detail="Bot cannot create/find approval channel.") from exc

    role_obj = _resolve_unit_role_obj(guild, unit_role_name)
    color = role_obj.color if role_obj and role_obj.color.value else discord.Color.yellow()
    requester_mention = requester_member.mention if requester_member else f"<@{requester_user_id}>"
    unit_mention = role_obj.mention if role_obj else "*No unit role found*"
    embed = discord.Embed(
        description=(
            f"**{operation['name']}**\n"
            f"Requester: {requester_mention}\n"
            f"Unit: {unit_mention}\n"
            f"Requested Slot: **{slot['squad_name']} - {slot['role_name']}**"
        ),
        color=color,
    )
    embed.set_footer(text=f"Request ID: {request_id}")
    embed.timestamp = discord.utils.utcnow()

    view = ApprovalView(request_id=request_id, bot=bot)
    message = await approval_channel.send(embed=embed, view=view)
    try:
        # add_view requires a persistent view (custom_id on all items). The
        # message already has the live view attached; don't fail the request flow
        # if persistent registration is not possible.
        bot.add_view(view)
    except ValueError:
        pass
    await database.update_request_message(request_id, str(message.id), str(approval_channel.id))


def _with_session_in_return_to(target_url: str, session_token: str) -> str:
    try:
        parsed = urlparse(target_url)
        if parsed.fragment:
            fragment = parsed.fragment
            separator = "&" if "?" in fragment else "?"
            fragment = f"{fragment}{separator}orbat_session={session_token}"
            return urlunparse(parsed._replace(fragment=fragment))
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query["orbat_session"] = session_token
        return urlunparse(parsed._replace(query=urlencode(query)))
    except Exception:
        return target_url


async def _create_session_from_discord(
    *,
    response: Response,
    code: str,
    guild_id: Optional[str] = None,
) -> tuple[str, str, int]:
    token_data, user_data = await _discord_oauth_exchange(code)
    session_token = secrets.token_urlsafe(48)
    expires_in = int(token_data.get("expires_in", 604800))
    # web_sessions.expires_at is TIMESTAMP WITHOUT TIME ZONE in Postgres.
    # Store naive UTC to avoid asyncpg aware/naive datetime encoding errors.
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).replace(tzinfo=None)
    cookie_secure = os.getenv("COOKIE_SECURE", "true").lower() == "true"
    cookie_samesite = os.getenv("COOKIE_SAMESITE", "none").lower()
    if cookie_samesite not in {"lax", "strict", "none"}:
        cookie_samesite = "none"

    await database.create_web_session(
        session_token=session_token,
        guild_id=guild_id or "0",
        user_id=user_data["id"],
        username=user_data.get("global_name") or user_data["username"],
        avatar_url=_build_avatar_url(user_data),
        access_token=token_data.get("access_token"),
        refresh_token=token_data.get("refresh_token"),
        expires_at=expires_at,
    )

    response.set_cookie(
        key="orbat_session",
        value=session_token,
        httponly=True,
        samesite=cookie_samesite,
        secure=cookie_secure,
        max_age=expires_in,
    )
    return user_data["id"], session_token, expires_in


def _guild_icon_url(guild_id: str, icon_hash: Optional[str]) -> Optional[str]:
    if not icon_hash:
        return None
    return f"https://cdn.discordapp.com/icons/{guild_id}/{icon_hash}.png"


def _has_manage_permissions(permissions_value: str) -> bool:
    try:
        perms = int(permissions_value)
    except Exception:
        return False
    admin_flag = 0x8
    manage_guild_flag = 0x20
    return bool(perms & admin_flag or perms & manage_guild_flag)


def create_api_app(bot) -> FastAPI:
    app = FastAPI(title="ORBAT API", version="0.1.0")
    app.state.bot = bot
    app.state.ws_hub = WebSocketHub()
    app.state.pg_listener_task = None
    app.state.startup_warnings = []

    cors_origins = [o.strip() for o in os.getenv("FRONTEND_ORIGINS", "*").split(",")]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    async def _pg_listener():
        if not database.DATABASE_URL:
            raise RuntimeError("DATABASE_URL is not configured.")
        conn = await asyncpg.connect(database.DATABASE_URL)

        def _listener(_connection, _pid, _channel, payload):
            try:
                data = json.loads(payload)
                operation_id = int(data["operation_id"])
            except Exception:
                return
            asyncio.create_task(app.state.ws_hub.broadcast(operation_id, data))

        await conn.add_listener("slot_updates", _listener)
        try:
            while True:
                await asyncio.sleep(3600)
        finally:
            await conn.remove_listener("slot_updates", _listener)
            await conn.close()

    @app.on_event("startup")
    async def startup_event():
        if not database.DATABASE_URL:
            app.state.startup_warnings.append("DATABASE_URL is not configured.")
            print("API startup warning: DATABASE_URL is not configured.")
            return

        try:
            await database.init_db()
        except Exception as exc:
            app.state.startup_warnings.append(f"database.init_db failed: {exc}")
            print("API startup warning: database.init_db failed:")
            traceback.print_exc()
            return

        app.state.pg_listener_task = asyncio.create_task(_pg_listener())

        def _pg_listener_done(task: asyncio.Task):
            try:
                exc = task.exception()
            except asyncio.CancelledError:
                return
            if exc:
                app.state.startup_warnings.append(f"pg_listener failed: {exc}")
                print("API warning: pg_listener failed:")
                traceback.print_exception(type(exc), exc, exc.__traceback__)

        app.state.pg_listener_task.add_done_callback(_pg_listener_done)

    @app.on_event("shutdown")
    async def shutdown_event():
        task = app.state.pg_listener_task
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    @app.get("/api/health")
    async def health():
        return {"ok": True, "warnings": list(app.state.startup_warnings)}

    @app.get("/api/auth/discord/login")
    async def auth_discord_login(guild_id: Optional[str] = None, return_to: Optional[str] = None):
        client_id = os.getenv("DISCORD_CLIENT_ID")
        redirect_uri = os.getenv("DISCORD_REDIRECT_URI")
        if not client_id or not redirect_uri:
            raise HTTPException(status_code=500, detail="Discord OAuth is not configured.")

        params = urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": "identify guilds",
                "state": _serialize_state(guild_id, return_to),
                "prompt": "consent",
            }
        )
        return RedirectResponse(url=f"https://discord.com/oauth2/authorize?{params}", status_code=302)

    @app.get("/api/auth/discord/callback")
    async def auth_discord_callback(code: str, state: Optional[str] = None):
        guild_id, return_to = _deserialize_state(state)
        target = return_to or "/"
        try:
            redirect = RedirectResponse(url=target, status_code=302)
            _user_id, session_token, _expires_in = await _create_session_from_discord(
                response=redirect,
                code=code,
                guild_id=guild_id,
            )
            redirect.headers["location"] = _with_session_in_return_to(target, session_token)
            return redirect
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, str) else "oauth_http_error"
            print(f"Discord callback HTTPException: {detail}")
            return RedirectResponse(url=_with_error_param(target, detail), status_code=302)
        except Exception as exc:
            print(f"Discord callback failed: {exc}")
            traceback.print_exc()
            return RedirectResponse(url=_with_error_param(target, "oauth_callback_failed"), status_code=302)

    @app.post("/api/auth/discord/exchange")
    async def auth_exchange(payload: DiscordCodeInput, response: Response):
        user_id, session_token, expires_in = await _create_session_from_discord(
            response=response,
            code=payload.code,
            guild_id=payload.guild_id,
        )
        return {"ok": True, "user_id": user_id, "session_token": session_token, "expires_in": expires_in}

    @app.get("/api/auth/session", response_model=SessionData)
    async def auth_session(
        orbat_session: Optional[str] = Cookie(default=None),
        x_orbat_session: Optional[str] = Header(default=None, alias="X-Orbat-Session"),
    ):
        session = await _session_from_token(orbat_session, x_orbat_session)
        return _serialize_session(session)

    @app.get("/api/auth/discord/guilds")
    async def auth_discord_guilds(
        orbat_session: Optional[str] = Cookie(default=None),
        x_orbat_session: Optional[str] = Header(default=None, alias="X-Orbat-Session"),
    ):
        session = await _session_from_token(orbat_session, x_orbat_session)
        access_token = session.get("access_token")
        if not access_token:
            raise HTTPException(status_code=401, detail="Session missing Discord access token. Please login again.")

        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                "https://discord.com/api/users/@me/guilds",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if resp.status_code >= 400:
            raise HTTPException(status_code=401, detail="Failed to load Discord guilds. Please login again.")

        raw_guilds = resp.json()
        items = []
        for guild in raw_guilds:
            guild_id = str(guild.get("id", ""))
            if not guild_id:
                continue
            if app.state.bot.get_guild(int(guild_id)) is None:
                continue
            items.append(
                {
                    "id": guild_id,
                    "name": guild.get("name", "Unknown Guild"),
                    "icon_url": _guild_icon_url(guild_id, guild.get("icon")),
                    "is_owner": bool(guild.get("owner", False)),
                    "can_manage": _has_manage_permissions(str(guild.get("permissions", "0"))),
                }
            )

        items.sort(key=lambda g: g["name"].lower())
        return items

    @app.post("/api/auth/logout")
    async def auth_logout(
        response: Response,
        orbat_session: Optional[str] = Cookie(default=None),
        x_orbat_session: Optional[str] = Header(default=None, alias="X-Orbat-Session"),
    ):
        token = orbat_session or x_orbat_session
        if token:
            await database.delete_web_session(token)
        response.delete_cookie("orbat_session")
        return {"ok": True}

    @app.get("/api/guilds/{guild_id}/me/permissions")
    async def guild_permissions(
        guild_id: str,
        orbat_session: Optional[str] = Cookie(default=None),
        x_orbat_session: Optional[str] = Header(default=None, alias="X-Orbat-Session"),
    ):
        session = await _session_from_token(orbat_session, x_orbat_session)
        is_portal_admin = await database.is_web_admin(guild_id, session["user_id"])
        is_discord_admin = await _discord_member_has_admin_permissions(app, guild_id, session["user_id"])
        return {
            "guild_id": guild_id,
            "is_portal_admin": is_portal_admin,
            "is_discord_admin": is_discord_admin,
            "is_admin": bool(is_portal_admin or is_discord_admin),
        }

    @app.get("/api/guilds/{guild_id}/admins")
    async def guild_admins(
        guild_id: str,
        orbat_session: Optional[str] = Cookie(default=None),
        x_orbat_session: Optional[str] = Header(default=None, alias="X-Orbat-Session"),
    ):
        session = await _session_from_token(orbat_session, x_orbat_session)
        await _require_guild_admin(app, session, guild_id)
        rows = await database.list_web_admins(guild_id)
        return [dict(row) for row in rows]

    @app.post("/api/guilds/{guild_id}/admins")
    async def add_guild_admin(
        guild_id: str,
        payload: AdminUpsertInput,
        orbat_session: Optional[str] = Cookie(default=None),
        x_orbat_session: Optional[str] = Header(default=None, alias="X-Orbat-Session"),
    ):
        session = await _session_from_token(orbat_session, x_orbat_session)
        await _require_guild_admin(app, session, guild_id)
        await database.upsert_web_admin(
            guild_id=guild_id,
            user_id=payload.user_id,
            username=payload.username,
            added_by=session["user_id"],
        )
        return {"ok": True}

    @app.delete("/api/guilds/{guild_id}/admins/{user_id}")
    async def remove_guild_admin(
        guild_id: str,
        user_id: str,
        orbat_session: Optional[str] = Cookie(default=None),
        x_orbat_session: Optional[str] = Header(default=None, alias="X-Orbat-Session"),
    ):
        session = await _session_from_token(orbat_session, x_orbat_session)
        await _require_guild_admin(app, session, guild_id)
        if user_id == session["user_id"]:
            # Avoid accidental lockout via self-removal.
            admins = await database.list_web_admins(guild_id)
            if len(admins) <= 1:
                raise HTTPException(status_code=400, detail="Cannot remove the last portal admin.")
        deleted = await database.delete_web_admin(guild_id, user_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Admin entry not found.")
        return {"ok": True}

    @app.get("/api/operations/active")
    async def get_active_operation(guild_id: str):
        op = await database.get_active_operation(guild_id)
        if not op:
            raise HTTPException(status_code=404, detail="No active operation.")
        return dict(op)

    @app.get("/api/operations")
    async def list_operations(guild_id: str):
        rows = await database.list_operations(guild_id)
        return [dict(row) for row in rows]

    @app.post("/api/operations")
    async def create_operation(
        payload: OperationCreateInput,
        orbat_session: Optional[str] = Cookie(default=None),
        x_orbat_session: Optional[str] = Header(default=None, alias="X-Orbat-Session"),
    ):
        session = await _session_from_token(orbat_session, x_orbat_session)
        await _require_guild_admin(app, session, payload.guild_id)
        op_id = await database.create_operation_v2(
            guild_id=payload.guild_id,
            name=payload.name,
            event_time=payload.event_time,
            reminder_minutes=payload.reminder_minutes,
            activate=payload.activate,
        )
        operation = await database.get_operation_by_id(op_id)
        return dict(operation)

    @app.post("/api/operations/{operation_id}/activate")
    async def activate_operation(
        operation_id: int,
        orbat_session: Optional[str] = Cookie(default=None),
        x_orbat_session: Optional[str] = Header(default=None, alias="X-Orbat-Session"),
    ):
        session = await _session_from_token(orbat_session, x_orbat_session)
        op = await database.get_operation_by_id(operation_id)
        if not op:
            raise HTTPException(status_code=404, detail="Operation not found.")
        await _require_guild_admin(app, session, str(op["guild_id"]))
        success = await database.activate_operation(op["guild_id"], operation_id)
        if not success:
            raise HTTPException(status_code=404, detail="Operation not found.")
        await database.emit_slot_update(op["guild_id"], operation_id, "operation_activated")
        return {"ok": True}

    @app.patch("/api/operations/{operation_id}/lanes")
    async def update_operation_lanes(
        operation_id: int,
        payload: OperationLaneNamesInput,
        orbat_session: Optional[str] = Cookie(default=None),
        x_orbat_session: Optional[str] = Header(default=None, alias="X-Orbat-Session"),
    ):
        session = await _session_from_token(orbat_session, x_orbat_session)
        op = await database.get_operation_by_id(operation_id)
        if not op:
            raise HTTPException(status_code=404, detail="Operation not found.")
        await _require_guild_admin(app, session, str(op["guild_id"]))
        success = await database.update_operation_lane_names(
            operation_id=operation_id,
            lane_name_left=payload.lane_name_left,
            lane_name_center=payload.lane_name_center,
            lane_name_right=payload.lane_name_right,
        )
        if not success:
            raise HTTPException(status_code=400, detail="No lane name fields provided.")
        await database.emit_slot_update(str(op["guild_id"]), operation_id, "operation_lanes_updated")
        return {"ok": True}

    @app.get("/api/operations/{operation_id}/orbat")
    async def get_orbat(operation_id: int):
        data = await database.get_orbat_structure(operation_id)
        if not data["operation"]:
            raise HTTPException(status_code=404, detail="Operation not found.")
        return data

    @app.post("/api/operations/{operation_id}/squads")
    async def create_squad(
        operation_id: int,
        payload: SquadCreateInput,
        orbat_session: Optional[str] = Cookie(default=None),
        x_orbat_session: Optional[str] = Header(default=None, alias="X-Orbat-Session"),
    ):
        session = await _session_from_token(orbat_session, x_orbat_session)
        op = await database.get_operation_by_id(operation_id)
        if not op:
            raise HTTPException(status_code=404, detail="Operation not found.")
        await _require_guild_admin(app, session, str(op["guild_id"]))
        squad_id = await database.create_squad(
            operation_id=operation_id,
            name=payload.name,
            display_order=payload.display_order,
            column_index=payload.column_index,
        )
        return {"id": squad_id}

    @app.patch("/api/squads/{squad_id}")
    async def update_squad(
        squad_id: int,
        payload: SquadUpdateInput,
        orbat_session: Optional[str] = Cookie(default=None),
        x_orbat_session: Optional[str] = Header(default=None, alias="X-Orbat-Session"),
    ):
        session = await _session_from_token(orbat_session, x_orbat_session)
        pool = await database.get_pool()
        async with pool.acquire() as db:
            op = await db.fetchrow(
                """SELECT o.guild_id
                   FROM squads s JOIN operations o ON o.id = s.operation_id
                   WHERE s.id = $1""",
                squad_id,
            )
        if not op:
            raise HTTPException(status_code=404, detail="Squad not found.")
        await _require_guild_admin(app, session, str(op["guild_id"]))
        success = await database.update_squad(
            squad_id=squad_id,
            name=payload.name,
            display_order=payload.display_order,
            column_index=payload.column_index,
        )
        if not success:
            raise HTTPException(status_code=404, detail="Squad not found or no fields updated.")
        return {"ok": True}

    @app.delete("/api/squads/{squad_id}")
    async def delete_squad(
        squad_id: int,
        orbat_session: Optional[str] = Cookie(default=None),
        x_orbat_session: Optional[str] = Header(default=None, alias="X-Orbat-Session"),
    ):
        session = await _session_from_token(orbat_session, x_orbat_session)
        pool = await database.get_pool()
        async with pool.acquire() as db:
            op = await db.fetchrow(
                """SELECT o.guild_id
                   FROM squads s JOIN operations o ON o.id = s.operation_id
                   WHERE s.id = $1""",
                squad_id,
            )
        if not op:
            raise HTTPException(status_code=404, detail="Squad not found.")
        await _require_guild_admin(app, session, str(op["guild_id"]))
        success = await database.delete_squad(squad_id)
        if not success:
            raise HTTPException(status_code=404, detail="Squad not found.")
        return {"ok": True}

    @app.post("/api/operations/{operation_id}/slots")
    async def create_slot(
        operation_id: int,
        payload: SlotCreateInput,
        orbat_session: Optional[str] = Cookie(default=None),
        x_orbat_session: Optional[str] = Header(default=None, alias="X-Orbat-Session"),
    ):
        session = await _session_from_token(orbat_session, x_orbat_session)
        op = await database.get_operation_by_id(operation_id)
        if not op:
            raise HTTPException(status_code=404, detail="Operation not found.")
        await _require_guild_admin(app, session, str(op["guild_id"]))
        slot_id = await database.create_slot(
            operation_id=operation_id,
            squad_id=payload.squad_id,
            role_name=payload.role_name,
            display_order=payload.display_order,
            team=(payload.team.strip() if isinstance(payload.team, str) and payload.team.strip() else None),
        )
        return {"id": slot_id}

    @app.patch("/api/slots/{slot_id}")
    async def update_slot(
        slot_id: int,
        payload: SlotUpdateInput,
        orbat_session: Optional[str] = Cookie(default=None),
        x_orbat_session: Optional[str] = Header(default=None, alias="X-Orbat-Session"),
    ):
        session = await _session_from_token(orbat_session, x_orbat_session)
        slot = await database.get_slot_by_id(slot_id)
        if not slot:
            raise HTTPException(status_code=404, detail="Slot not found.")
        op = await database.get_operation_by_id(slot["operation_id"])
        if not op:
            raise HTTPException(status_code=404, detail="Operation not found.")
        await _require_guild_admin(app, session, str(op["guild_id"]))
        success = await database.update_slot(
            slot_id=slot_id,
            role_name=payload.role_name,
            display_order=payload.display_order,
            squad_id=payload.squad_id,
            team=(payload.team.strip() if isinstance(payload.team, str) and payload.team.strip() else None),
            set_team=_field_was_provided(payload, "team"),
        )
        if not success:
            raise HTTPException(status_code=404, detail="Slot not found or no fields updated.")
        return {"ok": True}

    @app.delete("/api/slots/{slot_id}")
    async def delete_slot(
        slot_id: int,
        orbat_session: Optional[str] = Cookie(default=None),
        x_orbat_session: Optional[str] = Header(default=None, alias="X-Orbat-Session"),
    ):
        session = await _session_from_token(orbat_session, x_orbat_session)
        slot = await database.get_slot_by_id(slot_id)
        if not slot:
            raise HTTPException(status_code=404, detail="Slot not found.")
        op = await database.get_operation_by_id(slot["operation_id"])
        if not op:
            raise HTTPException(status_code=404, detail="Operation not found.")
        await _require_guild_admin(app, session, str(op["guild_id"]))
        success = await database.delete_slot(slot_id)
        if not success:
            raise HTTPException(status_code=404, detail="Slot not found.")
        return {"ok": True}

    @app.post("/api/slots/{slot_id}/request")
    async def request_slot(
        slot_id: int,
        payload: SlotRequestInput,
        orbat_session: Optional[str] = Cookie(default=None),
        x_orbat_session: Optional[str] = Header(default=None, alias="X-Orbat-Session"),
    ):
        try:
            session = await _session_from_token(orbat_session, x_orbat_session)
            slot = await database.get_slot_by_id(slot_id)
            if not slot:
                raise HTTPException(status_code=404, detail="Slot not found.")
            operation = await database.get_operation_by_id(slot["operation_id"])
            if not operation:
                raise HTTPException(status_code=404, detail="Operation not found.")
            if str(operation["guild_id"]) != payload.guild_id:
                raise HTTPException(status_code=400, detail="Slot does not belong to this guild.")

            if slot["assigned_to_member_id"]:
                raise HTTPException(status_code=409, detail="Slot is already assigned.")

            existing = await database.get_member_active_request(
                guild_id=payload.guild_id,
                operation_id=slot["operation_id"],
                member_id=session["user_id"],
            )
            if existing:
                raise HTTPException(status_code=409, detail="You already have an active request.")

            import discord
            from cogs.slots import UNIT_ROLES

            guild = app.state.bot.get_guild(int(payload.guild_id))
            requester_member = None
            requester_unit_role_name = None
            if guild:
                try:
                    requester_member = guild.get_member(int(session["user_id"])) or await guild.fetch_member(
                        int(session["user_id"])
                    )
                except (discord.NotFound, discord.Forbidden):
                    requester_member = None
            if requester_member:
                for role in requester_member.roles:
                    if role.name in UNIT_ROLES:
                        requester_unit_role_name = role.name
                        break

            request_id = await database.create_request(
                guild_id=payload.guild_id,
                operation_id=slot["operation_id"],
                slot_id=slot_id,
                member_id=session["user_id"],
                member_name=session["username"],
                slot_label=f"{slot['squad_name']} - {slot['role_name']}",
                unit_role=requester_unit_role_name,
            )

            try:
                await _post_approval_request(
                    app,
                    request_id,
                    operation,
                    slot,
                    requester_member,
                    session["user_id"],
                    requester_unit_role_name,
                )
            except Exception:
                await database.deny_request(request_id, "system", reason="Approval message failed")
                raise

            await database.emit_slot_update(payload.guild_id, slot["operation_id"], "request_created", slot_id)
            return {"id": request_id, "status": "pending"}
        except HTTPException:
            raise
        except Exception as exc:
            print(f"request_slot unexpected error: {exc}")
            traceback.print_exc()
            raise HTTPException(status_code=500, detail="request_slot_failed")

    @app.websocket("/ws/operations/{operation_id}")
    async def ws_operation(websocket: WebSocket, operation_id: int):
        await app.state.ws_hub.connect(operation_id, websocket)
        try:
            await websocket.send_text(
                json.dumps(
                    {
                        "event": "connected",
                        "operation_id": operation_id,
                    }
                )
            )
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            await app.state.ws_hub.disconnect(operation_id, websocket)

    return app
