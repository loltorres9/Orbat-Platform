import asyncio
import contextlib
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import asyncpg
import discord
import httpx
from fastapi import Cookie, FastAPI, HTTPException, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from cogs.slots import APPROVAL_CHANNEL_NAME, ApprovalView
from utils import database


class SessionData(BaseModel):
    session_token: str
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


class SquadUpdateInput(BaseModel):
    name: Optional[str] = None
    display_order: Optional[int] = None


class SlotCreateInput(BaseModel):
    squad_id: int
    role_name: str
    display_order: Optional[int] = None


class SlotUpdateInput(BaseModel):
    role_name: Optional[str] = None
    display_order: Optional[int] = None
    squad_id: Optional[int] = None


class SlotRequestInput(BaseModel):
    guild_id: str


class DiscordCodeInput(BaseModel):
    code: str
    guild_id: Optional[str] = None


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


async def _session_from_token(session_token: Optional[str]):
    if not session_token:
        raise HTTPException(status_code=401, detail="Missing session.")
    session = await database.get_web_session(session_token)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired or invalid.")
    return session


def _build_avatar_url(user_data: dict) -> Optional[str]:
    avatar_hash = user_data.get("avatar")
    if not avatar_hash:
        return None
    return f"https://cdn.discordapp.com/avatars/{user_data['id']}/{avatar_hash}.png"


async def _post_approval_request(app: FastAPI, request_id: int, operation, slot, requester_display_name: str):
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

    embed = discord.Embed(
        description=(
            f"**{operation['name']}**\n"
            f"**{requester_display_name}** -> **{slot['squad_name']} - {slot['role_name']}**"
        ),
        color=discord.Color.yellow(),
    )
    embed.set_footer(text=f"Request ID: {request_id}")
    embed.timestamp = discord.utils.utcnow()

    view = ApprovalView(request_id=request_id, bot=bot)
    message = await approval_channel.send(embed=embed, view=view)
    bot.add_view(view)
    await database.update_request_message(request_id, str(message.id), str(approval_channel.id))


def create_api_app(bot) -> FastAPI:
    app = FastAPI(title="ORBAT API", version="0.1.0")
    app.state.bot = bot
    app.state.ws_hub = WebSocketHub()
    app.state.pg_listener_task = None

    cors_origins = [o.strip() for o in os.getenv("FRONTEND_ORIGINS", "*").split(",")]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    async def _pg_listener():
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
        await database.init_db()
        app.state.pg_listener_task = asyncio.create_task(_pg_listener())

    @app.on_event("shutdown")
    async def shutdown_event():
        task = app.state.pg_listener_task
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    @app.get("/api/health")
    async def health():
        return {"ok": True}

    @app.post("/api/auth/discord/exchange")
    async def auth_exchange(payload: DiscordCodeInput, response: Response):
        token_data, user_data = await _discord_oauth_exchange(payload.code)
        session_token = secrets.token_urlsafe(48)
        expires_in = int(token_data.get("expires_in", 604800))
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

        await database.create_web_session(
            session_token=session_token,
            guild_id=payload.guild_id or "0",
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
            samesite="lax",
            secure=os.getenv("COOKIE_SECURE", "true").lower() == "true",
            max_age=expires_in,
        )
        return {"ok": True, "user_id": user_data["id"]}

    @app.get("/api/auth/session", response_model=SessionData)
    async def auth_session(orbat_session: Optional[str] = Cookie(default=None)):
        session = await _session_from_token(orbat_session)
        return SessionData(**dict(session))

    @app.post("/api/auth/logout")
    async def auth_logout(response: Response, orbat_session: Optional[str] = Cookie(default=None)):
        if orbat_session:
            await database.delete_web_session(orbat_session)
        response.delete_cookie("orbat_session")
        return {"ok": True}

    @app.get("/api/operations/active")
    async def get_active_operation(guild_id: str):
        op = await database.get_active_operation(guild_id)
        if not op:
            raise HTTPException(status_code=404, detail="No active operation.")
        return dict(op)

    @app.post("/api/operations")
    async def create_operation(payload: OperationCreateInput):
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
    async def activate_operation(operation_id: int):
        op = await database.get_operation_by_id(operation_id)
        if not op:
            raise HTTPException(status_code=404, detail="Operation not found.")
        success = await database.activate_operation(op["guild_id"], operation_id)
        if not success:
            raise HTTPException(status_code=404, detail="Operation not found.")
        await database.emit_slot_update(op["guild_id"], operation_id, "operation_activated")
        return {"ok": True}

    @app.get("/api/operations/{operation_id}/orbat")
    async def get_orbat(operation_id: int):
        data = await database.get_orbat_structure(operation_id)
        if not data["operation"]:
            raise HTTPException(status_code=404, detail="Operation not found.")
        return data

    @app.post("/api/operations/{operation_id}/squads")
    async def create_squad(operation_id: int, payload: SquadCreateInput):
        op = await database.get_operation_by_id(operation_id)
        if not op:
            raise HTTPException(status_code=404, detail="Operation not found.")
        squad_id = await database.create_squad(operation_id, payload.name, payload.display_order)
        return {"id": squad_id}

    @app.patch("/api/squads/{squad_id}")
    async def update_squad(squad_id: int, payload: SquadUpdateInput):
        success = await database.update_squad(
            squad_id=squad_id,
            name=payload.name,
            display_order=payload.display_order,
        )
        if not success:
            raise HTTPException(status_code=404, detail="Squad not found or no fields updated.")
        return {"ok": True}

    @app.delete("/api/squads/{squad_id}")
    async def delete_squad(squad_id: int):
        success = await database.delete_squad(squad_id)
        if not success:
            raise HTTPException(status_code=404, detail="Squad not found.")
        return {"ok": True}

    @app.post("/api/operations/{operation_id}/slots")
    async def create_slot(operation_id: int, payload: SlotCreateInput):
        op = await database.get_operation_by_id(operation_id)
        if not op:
            raise HTTPException(status_code=404, detail="Operation not found.")
        slot_id = await database.create_slot(
            operation_id=operation_id,
            squad_id=payload.squad_id,
            role_name=payload.role_name,
            display_order=payload.display_order,
        )
        return {"id": slot_id}

    @app.patch("/api/slots/{slot_id}")
    async def update_slot(slot_id: int, payload: SlotUpdateInput):
        success = await database.update_slot(
            slot_id=slot_id,
            role_name=payload.role_name,
            display_order=payload.display_order,
            squad_id=payload.squad_id,
        )
        if not success:
            raise HTTPException(status_code=404, detail="Slot not found or no fields updated.")
        return {"ok": True}

    @app.delete("/api/slots/{slot_id}")
    async def delete_slot(slot_id: int):
        success = await database.delete_slot(slot_id)
        if not success:
            raise HTTPException(status_code=404, detail="Slot not found.")
        return {"ok": True}

    @app.post("/api/slots/{slot_id}/request")
    async def request_slot(
        slot_id: int,
        payload: SlotRequestInput,
        orbat_session: Optional[str] = Cookie(default=None),
    ):
        session = await _session_from_token(orbat_session)
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

        request_id = await database.create_request(
            guild_id=payload.guild_id,
            operation_id=slot["operation_id"],
            slot_id=slot_id,
            member_id=session["user_id"],
            member_name=session["username"],
            slot_label=f"{slot['squad_name']} - {slot['role_name']}",
            unit_role=None,
        )

        try:
            await _post_approval_request(app, request_id, operation, slot, session["username"])
        except Exception:
            await database.deny_request(request_id, "system", reason="Approval message failed")
            raise

        await database.emit_slot_update(payload.guild_id, slot["operation_id"], "request_created", slot_id)
        return {"id": request_id, "status": "pending"}

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
