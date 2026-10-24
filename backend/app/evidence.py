"""Observed, sanitized evidence. No LangChain/LangGraph or provider dependency."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, field_validator
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from .store import ObservedEventRow, ObservedRunRow, Session

EventName = Literal[
    "request.received",
    "target.resolved",
    "model.requested",
    "model.responded",
    "model.usage",
    "tool.requested",
    "arguments.validated",
    "tool.denied",
    "authorization.decided",
    "tool.effective_arguments",
    "repository.read_started",
    "repository.read_completed",
    "repository.read_failed",
    "context.prepared",
    "context.dispatched",
    "context.request_prepared",
    "assistant.output",
    "action.proposed",
    "action.decided",
    "action.delivered",
    "tool.completed",
    "tool.failed",
    "scorer.verdict",
    "investigation.proposed",
    "defense.proposed",
    "remediation.requested",
    "patch.generated",
    "patch.applied_in_sandbox",
    "tests.executed",
    "attack.replayed",
    "remediation.verified",
    "remediation.rejected",
]
Actor = Literal[
    "application",
    "target_factory",
    "model",
    "tool",
    "repository",
    "attacker",
    "scorer",
    "investigator",
    "defense",
    "coding",
    "remediation_executor",
    "verifier",
    "user",
]
Provenance = Literal[
    "system_trusted", "authenticated_user", "bank_record", "external_untrusted", "model_generated"
]
RunOrigin = Literal["direct_execution", "fixture_execution", "live_execution"]


class EvidenceError(RuntimeError):
    """Fail closed: callers must not reinterpret missing evidence as success."""


class UnknownRunError(EvidenceError):
    pass


class EvidenceContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class EventDraft(EvidenceContract):
    name: EventName
    actor: Actor
    payload: dict[str, JsonValue]
    provenance: dict[str, Provenance] = Field(default_factory=dict)
    parent_event_id: UUID | None = None


class RunEvent(EventDraft):
    schema_version: Literal["1.0"] = "1.0"
    redaction_version: Literal["1.0"] = "1.0"
    run_id: UUID
    attempt_id: UUID | None
    event_id: UUID
    sequence: int = Field(ge=1)
    timestamp: AwareDatetime
    payload_digest: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("timestamp")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.utcoffset() != timedelta(0):
            raise ValueError("Evidence timestamps must be UTC")
        return value


class EventPage(EvidenceContract):
    schema_version: Literal["1.0"] = "1.0"
    run_id: UUID
    attempt_id: UUID | None
    origin: RunOrigin
    events: tuple[RunEvent, ...]
    next_after_sequence: int
    has_more: bool


def canonical_json(value: JsonValue) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def payload_digest(payload: dict[str, JsonValue]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Redactor:
    sensitive_fields: tuple[str, ...] = ()
    secret_values: tuple[str, ...] = field(default=(), repr=False)

    @classmethod
    def from_environment(cls) -> Redactor:
        # Read only into memory; never serialize the redactor or its raw values.
        value = os.getenv("OPENAI_API_KEY")
        return cls(secret_values=(value,) if value else ())

    def sanitize(self, payload: dict) -> dict[str, JsonValue]:
        sensitive = {
            "apikey",
            "xapikey",
            "openaiapikey",
            "authorization",
            "proxyauthorization",
            "token",
            "accesstoken",
            "refreshtoken",
            "password",
            "secret",
            "clientsecret",
        }
        configured = {p.casefold() for p in self.sensitive_fields}
        secrets = (*self.secret_values, os.getenv("OPENAI_API_KEY", ""))

        def clean(value, path: tuple[str, ...] = (), depth: int = 0):
            if depth > 20:
                raise ValueError("Evidence payload exceeds maximum nesting depth")
            if isinstance(value, dict):
                result = {}
                for key, item in value.items():
                    if not isinstance(key, str):
                        raise TypeError("Evidence object keys must be strings")
                    normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
                    child_path = (*path, key.casefold())
                    safe_key = clean(key, path, depth + 1)
                    if safe_key in result:
                        raise ValueError("Redaction produced duplicate evidence keys")
                    if (
                        normalized in sensitive
                        or key.casefold() in configured
                        or ".".join(child_path) in configured
                    ):
                        result[safe_key] = "[REDACTED]"
                    else:
                        result[safe_key] = clean(item, child_path, depth + 1)
                return result
            if isinstance(value, list):
                return [clean(item, path, depth + 1) for item in value]
            if isinstance(value, str):
                for secret in sorted((s for s in secrets if s), key=len, reverse=True):
                    value = value.replace(secret, "[REDACTED]")
                value = re.sub(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{12,}", "[REDACTED]", value)
                value = re.sub(r"\bgh[pousr]_[A-Za-z0-9]{20,}", "[REDACTED]", value)
                value = re.sub(r"(?i)\bBearer\s+[^\s,;\"']+", "Bearer [REDACTED]", value)
                return re.sub(
                    r"(?i)\b(?:OPENAI_API_KEY|API_KEY|ACCESS_TOKEN|REFRESH_TOKEN|PASSWORD|SECRET)"
                    r"\s*[:=]\s*[\"']?[^\s,;\"']+",
                    "[REDACTED]",
                    value,
                )
            if value is None or type(value) in (bool, int):
                return value
            if type(value) is float and math.isfinite(value):
                return value
            raise ValueError("Evidence payload must contain finite JSON values only")

        cleaned = clean(payload)
        if not isinstance(cleaned, dict) or len(canonical_json(cleaned).encode()) > 65536:
            raise ValueError("Evidence payload must be an object of at most 64 KiB")
        return cleaned


@dataclass(frozen=True)
class EvidenceRecorder:
    run_id: UUID
    redactor: Redactor = field(default_factory=Redactor.from_environment, repr=False)
    session_factory: sessionmaker = field(default=Session, repr=False)

    @classmethod
    def start(
        cls,
        *,
        attempt_id: UUID | None = None,
        origin: RunOrigin = "direct_execution",
        redactor: Redactor | None = None,
        session_factory=Session,
    ) -> EvidenceRecorder:
        if origin not in ("direct_execution", "fixture_execution", "live_execution"):
            raise ValueError("Unknown run origin")
        if attempt_id is not None and not isinstance(attempt_id, UUID):
            raise ValueError("Attempt ID must be a UUID")
        recorder = cls(uuid4(), redactor or Redactor.from_environment(), session_factory)
        try:
            with session_factory.begin() as session:
                session.add(
                    ObservedRunRow(
                        run_id=str(recorder.run_id),
                        attempt_id=str(attempt_id) if attempt_id else None,
                        origin=origin,
                        created_at=datetime.now(UTC),
                        last_sequence=0,
                    )
                )
        except SQLAlchemyError:
            raise EvidenceError("Could not start evidence run") from None
        return recorder

    def append(
        self,
        name: EventName,
        actor: Actor,
        payload: dict,
        *,
        provenance: dict[str, Provenance] | None = None,
        parent_event_id: UUID | None = None,
    ) -> RunEvent:
        safe_provenance = {}
        for path, source in (provenance or {}).items():
            if not isinstance(path, str):
                raise TypeError("Provenance paths must be strings")
            safe_path = self.redactor.sanitize({"path": path})["path"]
            if safe_path in safe_provenance:
                raise ValueError("Redaction produced duplicate provenance paths")
            safe_provenance[safe_path] = source
        draft = EventDraft(
            name=name,
            actor=actor,
            payload=self.redactor.sanitize(payload),
            provenance=safe_provenance,
            parent_event_id=parent_event_id,
        )
        try:
            with self.session_factory.begin() as session:
                # The write lock covers sequence allocation and insert, including
                # when separate recorder instances append to the same run.
                sequence = session.scalar(
                    update(ObservedRunRow)
                    .where(ObservedRunRow.run_id == str(self.run_id))
                    .values(last_sequence=ObservedRunRow.last_sequence + 1)
                    .returning(ObservedRunRow.last_sequence)
                )
                if sequence is None:
                    raise UnknownRunError("Evidence run not found")
                run = session.get(ObservedRunRow, str(self.run_id))
                if parent_event_id is not None:
                    parent = session.get(ObservedEventRow, str(parent_event_id))
                    if (
                        parent is None
                        or parent.run_id != str(self.run_id)
                        or parent.sequence >= sequence
                    ):
                        raise ValueError("Parent must be an earlier event in the same run")
                event = RunEvent(
                    **draft.model_dump(),
                    run_id=self.run_id,
                    attempt_id=UUID(run.attempt_id) if run.attempt_id else None,
                    event_id=uuid4(),
                    sequence=sequence,
                    timestamp=datetime.now(UTC),
                    payload_digest=payload_digest(draft.payload),
                )
                session.add(
                    ObservedEventRow(
                        event_id=str(event.event_id),
                        run_id=str(self.run_id),
                        sequence=sequence,
                        parent_event_id=str(parent_event_id) if parent_event_id else None,
                        event_json=event.model_dump_json(),
                    )
                )
        except SQLAlchemyError:
            raise EvidenceError("Could not persist evidence event") from None
        return event

    def append_in_scope(
        self,
        name: EventName,
        actor: Actor,
        payload: dict,
        *,
        provenance: dict[str, Provenance] | None = None,
    ) -> RunEvent:
        """Use the active parent only when it belongs to this recorder's run."""
        scope = _scope.get()
        parent = scope.parent_event_id if scope and scope.recorder.run_id == self.run_id else None
        return self.append(name, actor, payload, provenance=provenance, parent_event_id=parent)

    def last_sequence(self) -> int:
        """Return the persisted tail, independent of event-page size."""
        try:
            with self.session_factory() as session:
                run = session.get(ObservedRunRow, str(self.run_id))
                if run is None:
                    raise UnknownRunError("Evidence run not found")
                return run.last_sequence
        except SQLAlchemyError:
            raise EvidenceError("Could not read evidence sequence") from None

    @contextmanager
    def bind(self, *, parent_event_id: UUID | None = None):
        token = _scope.set(EvidenceScope(self, parent_event_id))
        try:
            yield
        finally:
            _scope.reset(token)


def read_events(
    run_id: UUID,
    *,
    after_sequence: int = 0,
    limit: int = 200,
    session_factory=Session,
) -> EventPage:
    if after_sequence < 0 or not 1 <= limit <= 1000:
        raise ValueError("Invalid evidence page bounds")
    try:
        with session_factory() as session:
            run = session.get(ObservedRunRow, str(run_id))
            if run is None:
                raise UnknownRunError("Evidence run not found")
            rows = session.scalars(
                select(ObservedEventRow)
                .where(
                    ObservedEventRow.run_id == str(run_id),
                    ObservedEventRow.sequence > after_sequence,
                )
                .order_by(ObservedEventRow.sequence)
                .limit(limit + 1)
            ).all()
            events = []
            for row in rows[:limit]:
                event = RunEvent.model_validate_json(row.event_json)
                if (
                    event.run_id != run_id
                    or event.sequence != row.sequence
                    or str(event.event_id) != row.event_id
                    or (str(event.parent_event_id) if event.parent_event_id else None)
                    != row.parent_event_id
                    or (str(event.attempt_id) if event.attempt_id else None) != run.attempt_id
                    or event.payload_digest != payload_digest(event.payload)
                ):
                    raise EvidenceError("Stored evidence failed integrity validation")
                events.append(event)
            return EventPage(
                run_id=run_id,
                attempt_id=UUID(run.attempt_id) if run.attempt_id else None,
                origin=run.origin,
                events=tuple(events),
                next_after_sequence=events[-1].sequence if events else after_sequence,
                has_more=len(rows) > limit,
            )
    except (SQLAlchemyError, ValueError):
        raise EvidenceError("Could not read valid evidence") from None


@dataclass(frozen=True)
class EvidenceScope:
    recorder: EvidenceRecorder
    parent_event_id: UUID | None


_scope: ContextVar[EvidenceScope | None] = ContextVar("sentinel_evidence_scope", default=None)


def emit(
    name: EventName,
    actor: Actor,
    payload: dict,
    *,
    provenance: dict[str, Provenance] | None = None,
) -> RunEvent | None:
    scope = _scope.get()
    if scope is None:
        return None
    return scope.recorder.append(
        name,
        actor,
        payload,
        provenance=provenance,
        parent_event_id=scope.parent_event_id,
    )


@contextmanager
def child_scope(event: RunEvent | None):
    scope = _scope.get()
    if scope is None or event is None:
        yield
    else:
        with scope.recorder.bind(parent_event_id=event.event_id):
            yield
