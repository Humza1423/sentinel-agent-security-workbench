"""HTTP contracts for local synthetic banking conversations."""

import secrets
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Cookie, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from . import chat
from .evidence import EvidenceError
from .target_contracts import TargetConfig

router = APIRouter(prefix="/api/v2/sessions")


class CreateSession(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["fixture_execution", "live_execution"] = "fixture_execution"
    target: TargetConfig = Field(
        default_factory=lambda: TargetConfig(tool_profile="vulnerable_lookup_v1")
    )


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    message: str = Field(min_length=1, max_length=4000)


@router.post("")
def create(
    body: CreateSession, response: Response, sentinel_owner: str | None = Cookie(default=None)
):
    owner = sentinel_owner or secrets.token_urlsafe(32)
    try:
        result = chat.create_session(body.target, owner, body.mode)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    response.set_cookie("sentinel_owner", owner, httponly=True, samesite="strict", max_age=604800)
    return result


@router.get("/{session_id}")
def get(session_id: UUID, sentinel_owner: str | None = Cookie(default=None)):
    try:
        return chat.get_session(str(session_id), sentinel_owner)
    except LookupError as exc:
        raise HTTPException(404, "Session not found") from exc


@router.post("/{session_id}/messages")
def message(session_id: UUID, body: ChatMessage, sentinel_owner: str | None = Cookie(default=None)):
    if not body.message.strip():
        raise HTTPException(422, "Message must not be blank")
    try:
        return chat.submit_message(
            str(session_id), sentinel_owner, str(body.request_id), body.message
        )
    except LookupError as exc:
        raise HTTPException(404, "Session not found") from exc
    except chat.ChatError as exc:
        raise HTTPException(409, str(exc)) from exc
    except EvidenceError as exc:
        raise HTTPException(503, "Conversation evidence is unavailable") from exc
