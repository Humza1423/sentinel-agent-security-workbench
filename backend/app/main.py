from __future__ import annotations

import os
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from fastapi import Cookie, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from .bank import ALICE_ID
from .budget import UsageBroker, estimate
from .campaign import run_campaign
from .chat import recover_interrupted, require_session
from .chat_api import router as chat_router
from .contracts import CampaignRequest
from .evidence import EventPage, EvidenceError, UnknownRunError, read_events
from .scenarios import SCENARIOS
from .settings import load_external_env_file
from .store import ChatTurnRow, Session, get_events, get_report, save_report
from .target_contracts import RequestContext, TargetConfig, TargetManifest
from .targets import resolve_target

load_external_env_file()


@asynccontextmanager
async def lifespan(app):
    recover_interrupted()
    yield


app = FastAPI(
    title="Banking Agent Security Workbench",
    description="Synthetic, local attack/repair/replay lab for tool-using banking assistants.",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(chat_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
    allow_credentials=True,
)


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "default_mode": "scripted",
        "live_agent_configured": bool(os.getenv("OPENAI_API_KEY")),
    }


@app.get("/api/v2/usage")
def usage_summary():
    from .live_model import live_status

    return {
        **UsageBroker().summary(),
        "call_estimate": estimate(),
        "live_chat": live_status(),
        "live_adapter_available": True,
    }


@app.post("/api/v2/targets/resolve", response_model=TargetManifest)
def resolve_target_configuration(config: TargetConfig):
    """Preview registered capabilities; does not save a target or execute a model/tool."""
    context = RequestContext(
        session_id="synthetic-target-preview",
        authenticated_customer_id=ALICE_ID,
        request_id=str(uuid4()),
    )
    try:
        return resolve_target(config, context=context).manifest()
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/v2/runs/{run_id}/events", response_model=EventPage)
def observed_run_events(
    run_id: UUID,
    after_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=1000),
    sentinel_owner: str | None = Cookie(default=None),
):
    with Session() as db:
        turn = db.scalar(select(ChatTurnRow).where(ChatTurnRow.run_id == str(run_id)))
        if turn is not None:
            try:
                require_session(db, turn.session_id, sentinel_owner)
            except LookupError as exc:
                raise HTTPException(404, "Run not found") from exc
    try:
        return read_events(run_id, after_sequence=after_sequence, limit=limit)
    except UnknownRunError as exc:
        raise HTTPException(404, str(exc)) from exc
    except EvidenceError:
        raise HTTPException(503, "Evidence is unavailable or incomplete") from None


@app.get("/api/scenarios")
def scenarios():
    return [scenario.model_dump() for scenario in SCENARIOS.values()]


@app.get("/api/scenarios/{scenario_id}")
def scenario_detail(scenario_id: str):
    scenario = SCENARIOS.get(scenario_id)
    if scenario is None:
        raise HTTPException(404, "Unknown scenario")
    return scenario.model_dump()


@app.post("/api/campaigns")
def create_campaign(request: CampaignRequest):
    if request.scenario_id not in SCENARIOS:
        raise HTTPException(404, "Unknown scenario")
    try:
        report = run_campaign(request.scenario_id, request.mode)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    payload = report.model_dump(mode="json")
    save_report(payload)
    return payload


@app.get("/api/campaigns/{campaign_id}")
def campaign_status(campaign_id: str):
    report = get_report(campaign_id)
    if report is None:
        raise HTTPException(404, "Campaign not found")
    return {"campaign_id": campaign_id, "status": "complete", "scenario": report["scenario"]}


@app.get("/api/campaigns/{campaign_id}/events")
def campaign_events(campaign_id: str):
    events = get_events(campaign_id)
    if events is None:
        raise HTTPException(404, "Campaign not found")
    return events


@app.get("/api/campaigns/{campaign_id}/report")
def campaign_report(campaign_id: str):
    report = get_report(campaign_id)
    if report is None:
        raise HTTPException(404, "Campaign not found")
    return report
