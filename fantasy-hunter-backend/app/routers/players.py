from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Player, PlayerSeason, Team
from app.services import scoring as S

router = APIRouter(prefix="/players", tags=["players"])

SORTABLE = {
    "total_points": Player.total_points,
    "now_cost": Player.now_cost,
    "form": Player.form,
    "selected_by_percent": Player.selected_by_percent,
    "minutes": Player.minutes,
    "goals_scored": Player.goals_scored,
    "assists": Player.assists,
    "expected_goal_involvements": Player.expected_goal_involvements,
    "points_per_game": Player.points_per_game,
    "ict_index": Player.ict_index,
    "web_name": Player.web_name,
}


def serialise(player: Player, team: Team | None) -> dict:
    return {
        "id": player.id,
        "code": player.code,
        "web_name": player.web_name,
        "full_name": player.full_name,
        "team_id": player.team_id,
        "team_short_name": team.short_name if team else None,
        "team_name": team.name if team else None,
        "position": S.POSITION_NAMES.get(player.element_type, "?"),
        "element_type": player.element_type,
        "now_cost": player.now_cost,
        "price": round(player.now_cost / 10, 1),
        "cost_change_start": player.cost_change_start,
        "cost_change_event": player.cost_change_event,
        "selected_by_percent": player.selected_by_percent,
        "status": player.status,
        "news": player.news,
        "chance_of_playing_next_round": player.chance_of_playing_next_round,
        "total_points": player.total_points,
        "event_points": player.event_points,
        "points_per_game": player.points_per_game,
        "form": player.form,
        "minutes": player.minutes,
        "starts": player.starts,
        "goals_scored": player.goals_scored,
        "assists": player.assists,
        "clean_sheets": player.clean_sheets,
        "goals_conceded": player.goals_conceded,
        "saves": player.saves,
        "bonus": player.bonus,
        "bps": player.bps,
        "yellow_cards": player.yellow_cards,
        "red_cards": player.red_cards,
        "defensive_contribution": player.defensive_contribution,
        "expected_goals": player.expected_goals,
        "expected_assists": player.expected_assists,
        "expected_goal_involvements": player.expected_goal_involvements,
        "expected_goals_conceded": player.expected_goals_conceded,
        "influence": player.influence,
        "creativity": player.creativity,
        "threat": player.threat,
        "ict_index": player.ict_index,
        "penalties_order": player.penalties_order,
        "corners_and_indirect_freekicks_order": player.corners_and_indirect_freekicks_order,
        "direct_freekicks_order": player.direct_freekicks_order,
        "transfers_in_event": player.transfers_in_event,
        "transfers_out_event": player.transfers_out_event,
        "photo_code": player.photo.replace(".jpg", "") if player.photo else "",
    }


@router.get("")
def list_players(
    session: Session = Depends(get_session),
    search: str | None = None,
    position: str | None = Query(None, description="GKP, DEF, MID or FWD"),
    team_id: int | None = None,
    min_price: float | None = Query(None, description="in millions, e.g. 4.5"),
    max_price: float | None = None,
    available_only: bool = False,
    sort_by: str = "total_points",
    order: str = "desc",
    limit: int = Query(50, le=600),
    offset: int = 0,
) -> dict:
    if sort_by not in SORTABLE:
        raise HTTPException(400, f"sort_by must be one of {sorted(SORTABLE)}")

    stmt = select(Player)
    if search:
        pattern = f"%{search.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Player.web_name).like(pattern),
                func.lower(Player.first_name).like(pattern),
                func.lower(Player.second_name).like(pattern),
            )
        )
    if position:
        lookup = {v: k for k, v in S.POSITION_NAMES.items()}
        element_type = lookup.get(position.upper())
        if element_type is None:
            raise HTTPException(400, "position must be GKP, DEF, MID or FWD")
        stmt = stmt.where(Player.element_type == element_type)
    if team_id:
        stmt = stmt.where(Player.team_id == team_id)
    if min_price is not None:
        stmt = stmt.where(Player.now_cost >= int(round(min_price * 10)))
    if max_price is not None:
        stmt = stmt.where(Player.now_cost <= int(round(max_price * 10)))
    if available_only:
        stmt = stmt.where(Player.status == "a")

    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0

    column = SORTABLE[sort_by]
    stmt = stmt.order_by(column.asc() if order == "asc" else column.desc())
    players = session.scalars(stmt.offset(offset).limit(limit)).all()

    teams = {t.id: t for t in session.scalars(select(Team)).all()}
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "results": [serialise(p, teams.get(p.team_id)) for p in players],
    }


@router.get("/{player_id}")
def get_player(player_id: int, session: Session = Depends(get_session)) -> dict:
    player = session.get(Player, player_id)
    if player is None:
        raise HTTPException(404, "player not found")
    team = session.get(Team, player.team_id)

    seasons = session.scalars(
        select(PlayerSeason)
        .where(PlayerSeason.element_code == player.code)
        .order_by(PlayerSeason.season_name.desc())
    ).all()

    data = serialise(player, team)
    data["past_seasons"] = [
        {
            "season_name": s.season_name,
            "total_points": s.total_points,
            "minutes": s.minutes,
            "starts": s.starts,
            "goals_scored": s.goals_scored,
            "assists": s.assists,
            "clean_sheets": s.clean_sheets,
            "saves": s.saves,
            "bonus": s.bonus,
            "defensive_contribution": s.defensive_contribution,
            "expected_goals": s.expected_goals,
            "expected_assists": s.expected_assists,
            "start_cost": s.start_cost,
            "end_cost": s.end_cost,
        }
        for s in seasons
    ]
    return data
