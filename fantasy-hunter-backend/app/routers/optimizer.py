from dataclasses import asdict

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.fpl_client import FPLClient, FPLUnavailable
from app.models import LineupSource, Player, Team
from app.services.lineups import coverage, is_preseason, start_probabilities
from app.services.optimizer import (
    CHIPS,
    DEFAULT_BUDGET,
    InfeasibleError,
    OptimisationResult,
    optimise_squad,
    plan_transfers,
)
from app.services.predictions import MODEL_VERSION, upcoming_event_ids

router = APIRouter(prefix="/optimizer", tags=["optimizer"])


def _serialise(result: OptimisationResult) -> dict:
    return {"model_version": MODEL_VERSION, **asdict(result)}


def _events(session: Session, horizon: int, start_event: int | None) -> list[int]:
    events = upcoming_event_ids(session, horizon=horizon, start_event=start_event)
    if not events:
        raise HTTPException(503, "no upcoming gameweeks — run ingestion first")
    return events


@router.get("/lineups")
def lineup_index(session: Session = Depends(get_session)) -> dict:
    """The pre-season predicted-XI consensus, and what it is built from.

    Published rather than hidden: a start probability that cannot be traced
    back to its sources is just another unexplained number.
    """
    sources = [
        {
            "slug": s.slug,
            "name": s.name,
            "url": s.url,
            "match_rate": round(s.match_rate, 3),
            "trusted": s.trusted,
            "fetched_at": s.fetched_at.isoformat() if s.fetched_at else None,
            "note": s.note,
        }
        for s in session.scalars(select(LineupSource).order_by(LineupSource.slug))
    ]
    probabilities = start_probabilities(session, force=True)
    teams = {t.id: t for t in session.scalars(select(Team))}
    players = {p.id: p for p in session.scalars(select(Player))}

    return {
        "preseason": is_preseason(session),
        "sources": sources,
        "trusted_sources_per_club": {
            teams[tid].short_name: n for tid, n in coverage(session).items() if tid in teams
        },
        "players": sorted(
            (
                {
                    "player_id": pid,
                    "web_name": players[pid].web_name,
                    "team_short_name": teams[players[pid].team_id].short_name,
                    "start_probability": round(prob, 3),
                }
                for pid, prob in probabilities.items()
                if pid in players
            ),
            key=lambda row: -row["start_probability"],
        ),
    }


@router.get("/squad")
def best_squad(
    session: Session = Depends(get_session),
    horizon: int = Query(5, ge=1, le=10),
    start_event: int | None = None,
    budget: float = Query(100.0, ge=50.0, le=150.0, description="in millions"),
    lock: str | None = Query(None, description="comma-separated player ids to force into the squad"),
    exclude: str | None = Query(None, description="comma-separated player ids to bar"),
    min_start_probability: float = Query(
        0.0,
        ge=0.0,
        le=1.0,
        description=(
            "pre-season only: bar players whom fewer than this fraction of predicted-XI "
            "sources expect to start. Players with no consensus data are never barred."
        ),
    ),
) -> dict:
    """The best legal 15 for a budget — an initial squad or a wildcard draft."""
    events = _events(session, horizon, start_event)

    def ids(raw: str | None) -> list[int]:
        if not raw:
            return []
        try:
            return [int(part) for part in raw.split(",") if part.strip()]
        except ValueError:
            raise HTTPException(400, "lock and exclude must be comma-separated integers")

    try:
        result = optimise_squad(
            session,
            events,
            budget=int(round(budget * 10)),
            locked_in=ids(lock),
            excluded=ids(exclude),
            min_start_probability=min_start_probability,
        )
    except InfeasibleError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _serialise(result)


@router.post("/plan")
def transfer_plan(
    session: Session = Depends(get_session),
    horizon: int = Query(5, ge=1, le=8),
    start_event: int | None = None,
    time_limit: int = Query(30, ge=5, le=120, description="solver budget in seconds"),
    payload: dict = Body(
        ...,
        examples=[
            {
                "squad": [1, 2, 3],
                "bank": 5,
                "free_transfers": 1,
                "chips": ["wildcard", "bench_boost"],
            }
        ],
    ),
) -> dict:
    """Plan transfers and chips across the horizon, starting from a known squad."""
    squad = payload.get("squad")
    if not isinstance(squad, list) or not all(isinstance(x, int) for x in squad):
        raise HTTPException(400, "squad must be a list of 15 player ids")

    chips = payload.get("chips") or []
    unknown = [chip for chip in chips if chip not in CHIPS]
    if unknown:
        raise HTTPException(400, f"unknown chips: {unknown}. Valid: {list(CHIPS)}")

    events = _events(session, horizon, start_event)
    try:
        result = plan_transfers(
            session,
            events,
            squad,
            bank=int(payload.get("bank", 0)),
            free_transfers=int(payload.get("free_transfers", 1)),
            available_chips=chips,
            time_limit=time_limit,
        )
    except InfeasibleError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _serialise(result)


@router.get("/plan/{entry_id}")
async def transfer_plan_for_entry(
    entry_id: int,
    session: Session = Depends(get_session),
    horizon: int = Query(5, ge=1, le=8),
    time_limit: int = Query(30, ge=5, le=120),
    chips: str | None = Query(
        None, description="comma-separated chips to consider, e.g. wildcard,bench_boost"
    ),
) -> dict:
    """Same as /plan, but pulls the squad, bank and free transfers from FPL."""
    async with FPLClient() as client:
        try:
            entry = await client.entry(entry_id)
        except httpx.HTTPStatusError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                raise HTTPException(404, f"FPL entry {entry_id} not found") from exc
            raise HTTPException(502, "FPL API error") from exc
        except FPLUnavailable as exc:
            raise HTTPException(503, "FPL API unavailable and nothing cached") from exc

        current_event = entry.get("current_event")
        picks_payload = None
        if current_event:
            try:
                picks_payload = await client.entry_picks(entry_id, current_event)
            except (httpx.HTTPStatusError, FPLUnavailable):
                picks_payload = None

    if picks_payload is None:
        raise HTTPException(
            409,
            "No squad is public for this entry yet. FPL only publishes picks after a "
            "gameweek deadline has passed.",
        )

    history = picks_payload.get("entry_history", {}) or {}
    requested = [chip.strip() for chip in (chips or "").split(",") if chip.strip()]
    unknown = [chip for chip in requested if chip not in CHIPS]
    if unknown:
        raise HTTPException(400, f"unknown chips: {unknown}. Valid: {list(CHIPS)}")

    events = _events(session, horizon, None)
    try:
        result = plan_transfers(
            session,
            events,
            [pick["element"] for pick in picks_payload.get("picks", [])],
            bank=history.get("bank", 0),
            free_transfers=1,
            available_chips=requested,
            time_limit=time_limit,
        )
    except InfeasibleError as exc:
        raise HTTPException(422, str(exc)) from exc

    payload = _serialise(result)
    payload["entry"] = {
        "id": entry_id,
        "name": entry.get("name"),
        "bank": history.get("bank", 0),
        "squad_value": history.get("value"),
    }
    return payload


@router.get("/meta")
def optimizer_meta() -> dict:
    """What the optimiser supports, so the UI does not hard-code it."""
    return {
        "chips": list(CHIPS),
        "default_budget": DEFAULT_BUDGET / 10,
        "notes": [
            "The squad builder and the planner are integer programs solved with CBC, "
            "not greedy heuristics.",
            "Chip scheduling is the expensive part; considering all four chips can take "
            "the full solver budget.",
        ],
    }
