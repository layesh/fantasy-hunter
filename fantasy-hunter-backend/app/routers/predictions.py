from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Player, Prediction, PredictionGrade, Team
from app.services import scoring as S
from app.services.predictions import (
    MODEL_VERSION,
    PredictionEngine,
    snapshot_predictions,
    upcoming_event_ids,
)

router = APIRouter(prefix="/predictions", tags=["predictions"])


def _resolve_events(session: Session, horizon: int, start_event: int | None) -> list[int]:
    events = upcoming_event_ids(session, horizon=horizon, start_event=start_event)
    if not events:
        raise HTTPException(503, "no upcoming gameweeks — run ingestion first")
    return events


@router.get("")
def predicted_points(
    session: Session = Depends(get_session),
    horizon: int = Query(5, ge=1, le=10, description="number of gameweeks"),
    start_event: int | None = None,
    position: str | None = None,
    team_id: int | None = None,
    max_price: float | None = None,
    search: str | None = None,
    limit: int = Query(50, le=600),
    offset: int = 0,
) -> dict:
    """The predicted points table — one row per player, one column per gameweek."""
    event_ids = _resolve_events(session, horizon, start_event)

    stmt = select(Player)
    if position:
        lookup = {v: k for k, v in S.POSITION_NAMES.items()}
        element_type = lookup.get(position.upper())
        if element_type is None:
            raise HTTPException(400, "position must be GKP, DEF, MID or FWD")
        stmt = stmt.where(Player.element_type == element_type)
    if team_id:
        stmt = stmt.where(Player.team_id == team_id)
    if max_price is not None:
        stmt = stmt.where(Player.now_cost <= int(round(max_price * 10)))
    if search:
        stmt = stmt.where(func.lower(Player.web_name).like(f"%{search.lower()}%"))

    players = list(session.scalars(stmt).all())
    engine = PredictionEngine(session)
    predictions = engine.predict_players(players, event_ids)
    teams = {t.id: t for t in session.scalars(select(Team)).all()}

    rows = []
    for player in players:
        fixture_predictions = predictions.get(player.id, [])
        by_event: dict[int, dict] = {}
        for fp in fixture_predictions:
            slot = by_event.setdefault(
                fp.event_id, {"expected_points": 0.0, "fixtures": []}
            )
            slot["expected_points"] = round(slot["expected_points"] + fp.expected_points, 2)
            slot["fixtures"].append(
                {
                    "fixture_id": fp.fixture_id,
                    "opponent": teams[fp.opponent_team_id].short_name
                    if fp.opponent_team_id in teams
                    else "?",
                    "is_home": fp.is_home,
                    "difficulty": fp.difficulty,
                    "expected_minutes": fp.expected_minutes,
                    "expected_points": fp.expected_points,
                }
            )
        total = round(sum(fp.expected_points for fp in fixture_predictions), 2)
        team = teams.get(player.team_id)
        rows.append(
            {
                "player_id": player.id,
                "web_name": player.web_name,
                "team_short_name": team.short_name if team else None,
                "position": S.POSITION_NAMES.get(player.element_type, "?"),
                "price": round(player.now_cost / 10, 1),
                "status": player.status,
                "selected_by_percent": player.selected_by_percent,
                "expected_points_total": total,
                "value": round(total / (player.now_cost / 10), 3) if player.now_cost else 0.0,
                "by_event": {str(event_id): by_event.get(event_id, {"expected_points": 0.0, "fixtures": []})
                             for event_id in event_ids},
            }
        )

    rows.sort(key=lambda r: r["expected_points_total"], reverse=True)
    return {
        "model_version": MODEL_VERSION,
        "events": event_ids,
        "total": len(rows),
        "limit": limit,
        "offset": offset,
        "results": rows[offset : offset + limit],
    }


@router.get("/player/{player_id}")
def player_prediction(
    player_id: int,
    session: Session = Depends(get_session),
    horizon: int = Query(5, ge=1, le=10),
    start_event: int | None = None,
) -> dict:
    """Full breakdown for one player — the "show your work" view."""
    player = session.get(Player, player_id)
    if player is None:
        raise HTTPException(404, "player not found")

    event_ids = _resolve_events(session, horizon, start_event)
    engine = PredictionEngine(session)
    profile = engine.profile(player)
    fixtures_by_team = engine.fixtures_for_events(event_ids)
    fixture_predictions = engine.predict_player(player, event_ids, fixtures_by_team)
    teams = {t.id: t for t in session.scalars(select(Team)).all()}

    return {
        "model_version": MODEL_VERSION,
        "player_id": player.id,
        "web_name": player.web_name,
        "position": S.POSITION_NAMES.get(player.element_type, "?"),
        "profile": asdict(profile),
        "expected_points_total": round(
            sum(fp.expected_points for fp in fixture_predictions), 2
        ),
        "fixtures": [
            {
                **asdict(fp),
                "opponent": teams[fp.opponent_team_id].short_name
                if fp.opponent_team_id in teams
                else "?",
            }
            for fp in fixture_predictions
        ],
    }


@router.post("/snapshot")
def snapshot(
    session: Session = Depends(get_session),
    horizon: int = Query(5, ge=1, le=10),
) -> dict:
    """Freeze current predictions so they can be graded after the gameweek.

    Run this before each deadline. Existing rows are never overwritten.
    """
    event_ids = upcoming_event_ids(session, horizon=horizon)
    written = snapshot_predictions(session, event_ids)
    return {"model_version": MODEL_VERSION, "events": event_ids, "written": written}


@router.get("/accuracy")
def accuracy(session: Session = Depends(get_session)) -> dict:
    """The public accuracy record. Empty until a gameweek has been graded."""
    rows = session.execute(
        select(
            Prediction.event_id,
            func.count(PredictionGrade.id),
            func.avg(func.abs(PredictionGrade.error)),
            func.avg(PredictionGrade.error),
        )
        .join(PredictionGrade, PredictionGrade.prediction_id == Prediction.id)
        .where(Prediction.model_version == MODEL_VERSION)
        .group_by(Prediction.event_id)
        .order_by(Prediction.event_id)
    ).all()

    return {
        "model_version": MODEL_VERSION,
        "gameweeks": [
            {
                "event_id": event_id,
                "graded_predictions": count,
                "mean_absolute_error": round(mae or 0.0, 3),
                "mean_error": round(bias or 0.0, 3),
            }
            for event_id, count, mae, bias in rows
        ],
        "note": "Predictions are written before each deadline and never edited afterwards.",
    }
