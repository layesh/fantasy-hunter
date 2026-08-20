from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Player, PlayerSeason, Team
from app.routers.players import serialise
from app.services.predictions import PredictionEngine, upcoming_event_ids

router = APIRouter(prefix="/compare", tags=["compare"])

# Metrics the UI renders as a side-by-side; "higher_is_better" drives the
# winner highlight so the frontend never has to know which way round each is.
# None means the metric has no better direction — price and ownership are
# context, not a contest, and highlighting the priciest player as the "winner"
# would be actively misleading.
METRICS: list[tuple[str, bool | None]] = [
    ("price", None),
    ("total_points", True),
    ("points_per_game", True),
    ("form", True),
    ("minutes", True),
    ("starts", True),
    ("goals_scored", True),
    ("assists", True),
    ("clean_sheets", True),
    ("saves", True),
    ("bonus", True),
    ("bps", True),
    ("defensive_contribution", True),
    ("expected_goals", True),
    ("expected_assists", True),
    ("expected_goal_involvements", True),
    ("expected_goals_conceded", False),
    ("ict_index", True),
    ("selected_by_percent", None),
    ("yellow_cards", False),
]


@router.get("")
def compare_players(
    session: Session = Depends(get_session),
    ids: str = Query(..., description="comma-separated player ids, 2-4 of them"),
    horizon: int = Query(5, ge=1, le=10),
) -> dict:
    try:
        player_ids = [int(part) for part in ids.split(",") if part.strip()]
    except ValueError:
        raise HTTPException(400, "ids must be comma-separated integers")
    if not 2 <= len(player_ids) <= 4:
        raise HTTPException(400, "compare between 2 and 4 players")

    players = list(session.scalars(select(Player).where(Player.id.in_(player_ids))).all())
    found = {p.id for p in players}
    missing = [pid for pid in player_ids if pid not in found]
    if missing:
        raise HTTPException(404, f"unknown player ids: {missing}")
    players.sort(key=lambda p: player_ids.index(p.id))

    event_ids = upcoming_event_ids(session, horizon=horizon)
    engine = PredictionEngine(session)
    predictions = engine.predict_players(players, event_ids)
    teams = {t.id: t for t in session.scalars(select(Team)).all()}

    columns = []
    for player in players:
        data = serialise(player, teams.get(player.team_id))
        fixture_predictions = predictions.get(player.id, [])
        data["expected_points_total"] = round(
            sum(fp.expected_points for fp in fixture_predictions), 2
        )
        data["upcoming"] = [
            {
                "event_id": fp.event_id,
                "opponent": teams[fp.opponent_team_id].short_name
                if fp.opponent_team_id in teams
                else "?",
                "is_home": fp.is_home,
                "difficulty": fp.difficulty,
                "expected_points": fp.expected_points,
            }
            for fp in fixture_predictions
        ]
        seasons = session.scalars(
            select(PlayerSeason)
            .where(PlayerSeason.element_code == player.code)
            .order_by(PlayerSeason.season_name.desc())
            .limit(3)
        ).all()
        data["past_seasons"] = [
            {
                "season_name": s.season_name,
                "total_points": s.total_points,
                "minutes": s.minutes,
                "goals_scored": s.goals_scored,
                "assists": s.assists,
            }
            for s in seasons
        ]
        columns.append(data)

    rows = []
    for metric, higher_is_better in [*METRICS, ("expected_points_total", True)]:
        values = [column.get(metric) for column in columns]
        numeric = [v for v in values if isinstance(v, (int, float))]
        best = None
        if numeric and higher_is_better is not None:
            target = max(numeric) if higher_is_better else min(numeric)
            # Only call a winner when someone is actually ahead.
            if numeric.count(target) < len(numeric):
                best = next(
                    columns[i]["id"] for i, v in enumerate(values) if v == target
                )
        rows.append(
            {
                "metric": metric,
                "higher_is_better": higher_is_better,
                "values": values,
                "winner_player_id": best,
            }
        )

    return {"events": event_ids, "players": columns, "metrics": rows}
