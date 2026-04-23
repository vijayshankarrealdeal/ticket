from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field
from typing import Any


# -------- auth --------

class SignupIn(BaseModel):
    name: str
    email: EmailStr
    password: str = Field(min_length=4)


class SigninIn(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: str
    name: str
    email: str
    role: str
    provider: str
    workspaceId: str
    signedInAt: str | None = None
    avatar_url: str | None = None


class AuthOut(BaseModel):
    user: UserOut
    token: str
    accessToken: str  # alias the frontend reads


# -------- messages / conversations --------

class MessageIn(BaseModel):
    text: str | None = None
    attachments: list[dict[str, Any]] = []
    replyTo: dict[str, Any] | None = None
    tempId: str | None = None


class NoteIn(BaseModel):
    text: str


# -------- tickets --------

class TicketCreateIn(BaseModel):
    customer: str | None = None           # name (free-text for the modal)
    customerId: str | None = None
    task: str = "general_enquiry"
    source: str = "WhatsApp"
    channel: str | None = None
    team: str = "Support"
    subject: str | None = None
    description: str | None = None
    priority: str = "normal"
    orderId: str | None = None
    tags: list[str] = []
    shareWithCustomer: bool = False


class TicketPatchIn(BaseModel):
    status: str | None = None
    assignee: str | None = None
    assigneeId: str | None = None
    team: str | None = None
    task: str | None = None
    source: str | None = None
    voc: str | None = None
    resolution_notes: str | None = None
    resolutionNotes: str | None = None
    customFields: dict[str, Any] | None = None


class ChildTicketIn(BaseModel):
    assigneeEmail: str | None = None
    team: str | None = None
    objective: str | None = None
    prompt: str | None = None
    resolution: str | None = None


class TicketResolveIn(BaseModel):
    voc: str | None = None
    resolutionNotes: str | None = None
    disposition: str | None = None


# -------- teams --------

class TeamMessageIn(BaseModel):
    text: str | None = None
    blocks: list[dict[str, Any]] = []
    attachments: list[dict[str, Any]] = []
    replyTo: str | None = None


class ReactionIn(BaseModel):
    emoji: str


# -------- platforms / agents --------

class AgentIn(BaseModel):
    name: str
    model: str = "gpt-4.1"
    persona: str = ""
    instructions: str = ""
    temperature: float = 0.3
    tools: list[dict[str, Any]] = []
