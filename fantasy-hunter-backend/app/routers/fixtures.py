from dataclasses import asdict

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Fixture, Team
from app.services.fdr import build_ticker
from app.services.predictions import league_averages, upcoming_event_ids

from app.services.defence import SEASON_WEIGHTS, club_defence

router = APIRouter(prefix="/fixtures", tags=["fixtures"])


@router.get("/ticker")
def ticker(
    session: Session = Depends(get_session),
    horizon: int = Query(6, ge=1, le=15),
    start_event: int | None = None,
    sort_by: str = Query("attack", pattern="^(attack|defence)$"),
) -> dict:
    """Fixture difficulty grid: one row per club, one column per gameweek."""
    event_ids = upcoming_event_ids(session, horizon=horizon, start_event=start_event)
    rows = build_ticker(session, event_ids)
    if sort_by == "defence":
        rows.sort(key=lambda r: r.defence_score, reverse=True)

    averages = league_averages(list(session.scalars(select(Team)).all()))
    return {
        "events": event_ids,
        "scale": {
            "1": "hardest",
            "5": "easiest",
            "note": "Ratings come from the same fixture model as predicted points.",
            "source": "team_strength" if averages.strengths_available else "official_fdr",
        },
        "rows": [
            {
                "team_id": r.team_id,
                "team_name": r.team_name,
                "team_short_name": r.team_short_name,
                "attack_score": round(r.attack_score, 2),
                "defence_score": round(r.defence_score, 2),
                "fixture_count": r.fixture_count,
                "fixtures": {
                    str(event_id): [asdict(f) for f in r.fixtures.get(event_id, [])]
                    for event_id in event_ids
                },
            }
            for r in rows
        ],
    }


@router.get("")
def list_fixtures(
    session: Session = Depends(get_session),
    event_id: int | None = None,
    team_id: int | None = None,
) -> list[dict]:
    stmt = select(Fixture).order_by(Fixture.event_id, Fixture.kickoff_time)
    if event_id is not None:
        stmt = stmt.where(Fixture.event_id == event_id)
    if team_id is not None:
        stmt = stmt.where((Fixture.team_h == team_id) | (Fixture.team_a == team_id))

    teams = {t.id: t for t in session.scalars(select(Team)).all()}
    return [
        {
            "id": f.id,
            "event_id": f.event_id,
            "kickoff_time": f.kickoff_time.isoformat() if f.kickoff_time else None,
            "home": teams[f.team_h].short_name if f.team_h in teams else None,
            "away": teams[f.team_a].short_name if f.team_a in teams else None,
            "team_h": f.team_h,
            "team_a": f.team_a,
            "team_h_difficulty": f.team_h_difficulty,
            "team_a_difficulty": f.team_a_difficulty,
            "team_h_score": f.team_h_score,
            "team_a_score": f.team_a_score,
            "started": f.started,
            "finished": f.finished,
        }
        for f in session.scalars(stmt).all()
    ]


@router.get("/defence")
def defensive_records(session: Session = Depends(get_session)) -> dict:
    """Club clean-sheet and goals-conceded records across completed seasons.

    Backward-looking record, as opposed to the ticker's forward-looking
    difficulty. Promoted clubs appear with `known: false` rather than being
    omitted — no record is not the same as a bad record.
    """
    clubs = club_defence(session)
    return {
        "season_weights": SEASON_WEIGHTS,
        "note": (
            "Recency-weighted across the seasons on record. Clubs with no "
            "Premier League history are returned unscored, not as zero."
        ),
        "clubs": [
            {
                "team_id": c.team_id,
                "team_short_name": c.abbr,
                "team_name": c.name,
                "known": c.known,
                "clean_sheets_per_38": c.clean_sheets_per_38,
                "goals_conceded_per_game": c.goals_conceded_per_game,
                "expected_goals_conceded_per_game": c.expected_goals_conceded_per_game,
                "seasons": c.seasons,
            }
            for c in clubs
        ],
    }
