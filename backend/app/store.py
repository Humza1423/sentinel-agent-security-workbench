"""SQLite-backed campaign and evidence persistence for local reproducibility."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class CampaignRow(Base):
    __tablename__ = "campaigns"
    campaign_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    report_json: Mapped[str] = mapped_column(Text, nullable=False)


class EvidenceRow(Base):
    __tablename__ = "run_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.campaign_id", ondelete="CASCADE"), index=True
    )
    phase: Mapped[str] = mapped_column(String(16), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_json: Mapped[str] = mapped_column(Text, nullable=False)


class BankTransactionRow(Base):
    __tablename__ = "transactions"
    transaction_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    customer_id: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    merchant: Mapped[str] = mapped_column(String(128), nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    date: Mapped[str] = mapped_column(String(10), nullable=False)
    memo: Mapped[str] = mapped_column(Text, nullable=False)
    memo_provenance: Mapped[str] = mapped_column(String(32), nullable=False)


class BankSnapshotRow(Base):
    __tablename__ = "bank_snapshots"
    snapshot_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_version: Mapped[str] = mapped_column(String(32), nullable=False)
    reference_date: Mapped[str] = mapped_column(String(10), nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    contents_json: Mapped[str] = mapped_column(Text, nullable=False)


class BankMemoOverlayRow(Base):
    __tablename__ = "bank_memo_overlays"
    __table_args__ = (
        UniqueConstraint("run_id", "transaction_id", name="uq_overlay_run_transaction"),
    )
    overlay_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("bank_snapshots.snapshot_id"), nullable=False
    )
    transaction_id: Mapped[str] = mapped_column(String(32), nullable=False)
    memo: Mapped[str] = mapped_column(Text, nullable=False)
    memo_provenance: Mapped[str] = mapped_column(String(32), nullable=False)


class ObservedRunRow(Base):
    __tablename__ = "observed_runs"
    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    attempt_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    origin: Mapped[str] = mapped_column(String(24), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ObservedEventRow(Base):
    __tablename__ = "observed_events"
    __table_args__ = (UniqueConstraint("run_id", "sequence", name="uq_observed_run_sequence"),)
    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("observed_runs.run_id"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_event_id: Mapped[str | None] = mapped_column(
        ForeignKey("observed_events.event_id"), nullable=True
    )
    event_json: Mapped[str] = mapped_column(Text, nullable=False)


class ChatSessionRow(Base):
    __tablename__ = "chat_sessions"
    session_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    customer_id: Mapped[str] = mapped_column(String(32), nullable=False)
    config_json: Mapped[str] = mapped_column(Text, nullable=False)
    target_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    active_request: Mapped[str | None] = mapped_column(String(36), nullable=True)
    worker_pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mode: Mapped[str] = mapped_column(String(24), nullable=False, default="fixture_execution")


class ChatTurnRow(Base):
    __tablename__ = "chat_turns"
    __table_args__ = (UniqueConstraint("session_id", "request_id"),)
    turn_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("chat_sessions.session_id"), nullable=False)
    request_id: Mapped[str] = mapped_column(String(36), nullable=False)
    run_id: Mapped[str] = mapped_column(ForeignKey("observed_runs.run_id"), nullable=False)
    user_message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    messages_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class BudgetAccountRow(Base):
    __tablename__ = "budget_account"
    account_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    started_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    prior_micro_usd: Mapped[int | None] = mapped_column(Integer, nullable=True)
    halted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class UsageChargeRow(Base):
    __tablename__ = "usage_charges"
    charge_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[str] = mapped_column(
        String(40), nullable=False, default=lambda: datetime.now(UTC).isoformat()
    )
    assessment_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    role: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    reserved_micro_usd: Mapped[int] = mapped_column(Integer, nullable=False)
    settled_micro_usd: Mapped[int | None] = mapped_column(Integer, nullable=True)
    plan_json: Mapped[str] = mapped_column(Text, nullable=False)
    usage_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_request_id: Mapped[str | None] = mapped_column(String(200), nullable=True)


_default_db = Path(__file__).resolve().parents[1] / "workbench.db"
DATABASE_URL = os.getenv("WORKBENCH_DATABASE_URL", f"sqlite:///{_default_db}")
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)


@event.listens_for(engine, "connect")
def _enable_sqlite_foreign_keys(connection, _record):
    if DATABASE_URL.startswith("sqlite"):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


Session = sessionmaker(engine, expire_on_commit=False)


def initialize_store() -> None:
    if DATABASE_URL.startswith("sqlite") and not DATABASE_URL.endswith(":memory:"):
        Path(DATABASE_URL.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)


def save_report(report: dict) -> None:
    initialize_store()
    campaign_id = report["campaign_id"]
    with Session.begin() as session:
        session.add(
            CampaignRow(
                campaign_id=campaign_id,
                created_at=datetime.fromisoformat(report["created_at"]),
                report_json=json.dumps(report, sort_keys=True, separators=(",", ":")),
            )
        )
        for phase in ("baseline", "replay"):
            for item in report[phase]["events"]:
                session.add(
                    EvidenceRow(
                        campaign_id=campaign_id,
                        phase=phase,
                        sequence=item["sequence"],
                        event_json=json.dumps(item, sort_keys=True, separators=(",", ":")),
                    )
                )


def get_report(campaign_id: str) -> dict | None:
    initialize_store()
    with Session() as session:
        row = session.get(CampaignRow, campaign_id)
        return json.loads(row.report_json) if row else None


def get_events(campaign_id: str) -> dict[str, list[dict]] | None:
    initialize_store()
    with Session() as session:
        if session.get(CampaignRow, campaign_id) is None:
            return None
        rows = session.scalars(
            select(EvidenceRow)
            .where(EvidenceRow.campaign_id == campaign_id)
            .order_by(EvidenceRow.phase, EvidenceRow.sequence)
        ).all()
        result: dict[str, list[dict]] = {"baseline": [], "replay": []}
        for row in rows:
            result[row.phase].append(json.loads(row.event_json))
        return result


def seed_bank_transactions(fixtures: list[dict]) -> None:
    """Insert deterministic demo data once; never overwrite an existing bank snapshot."""
    initialize_store()
    with Session.begin() as session:
        if session.scalar(select(BankTransactionRow.transaction_id).limit(1)) is None:
            session.add_all(BankTransactionRow(**fixture) for fixture in fixtures)


def list_transactions() -> list[dict]:
    initialize_store()
    with Session() as session:
        rows = session.scalars(
            select(BankTransactionRow).order_by(BankTransactionRow.transaction_id)
        ).all()
        return [
            {
                "transaction_id": row.transaction_id,
                "customer_id": row.customer_id,
                "merchant": row.merchant,
                "amount": row.amount,
                "date": row.date,
                "memo": row.memo,
                "memo_provenance": row.memo_provenance,
            }
            for row in rows
        ]
