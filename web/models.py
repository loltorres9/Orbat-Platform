from pydantic import BaseModel
from typing import Optional
from datetime import datetime


# ---- Auth ----

class UserResponse(BaseModel):
    id: str
    username: str
    avatar: Optional[str] = None
    guilds: list = []


# ---- Operations ----

class OperationCreate(BaseModel):
    guild_id: str
    name: str
    description: Optional[str] = None


class OperationUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class OperationResponse(BaseModel):
    id: int
    guild_id: str
    name: str
    description: Optional[str] = None
    is_active: int
    event_time: Optional[datetime] = None
    created_at: Optional[datetime] = None


# ---- Squads ----

class SquadCreate(BaseModel):
    name: str
    color: str = '#4A90D9'
    display_order: int = 0


class SquadUpdate(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None
    display_order: Optional[int] = None


class SquadResponse(BaseModel):
    id: int
    operation_id: int
    name: str
    color: str
    display_order: int


# ---- Slots ----

class SlotCreate(BaseModel):
    role_name: str
    display_order: int = 0


class SlotUpdate(BaseModel):
    role_name: Optional[str] = None
    display_order: Optional[int] = None


class SlotResponse(BaseModel):
    id: int
    squad_id: int
    role_name: str
    display_order: int
    assigned_to_member_id: Optional[str] = None
    assigned_to_name: Optional[str] = None
    status: str = 'available'
    pending_count: int = 0


# ---- Slot Request ----

class SlotRequestCreate(BaseModel):
    guild_id: str


class SlotRequestResponse(BaseModel):
    id: int
    slot_id: Optional[int] = None
    slot_label: str
    member_id: str
    member_name: str
    status: str
    created_at: Optional[datetime] = None


# ---- ORBAT (read-only tree view) ----

class OrbatSquad(BaseModel):
    id: int
    name: str
    color: str
    display_order: int
    slots: list[SlotResponse] = []


class OrbatResponse(BaseModel):
    operation: OperationResponse
    squads: list[OrbatSquad] = []
    total_slots: int = 0
    filled_slots: int = 0
    pending_slots: int = 0
    open_slots: int = 0
