from dataclasses import asdict

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.fpl_client import FPLClient, FPLUnavailable
from app.models import Event, Player, Team
from app.services import scoring as S
from app.services.myteam import (
    best_starting_xi,
    build_squad,
    captain_options,
    rate_squad,
    suggest_transfers,
)
from app.services.predictions import MODEL_VERSION, PredictionEngine, upcoming_event_ids

router = APIRouter(prefix="/my-team", tags=["my-team"])


def _sp(sp, teams: dict[int, Team]) -> dict:
    team = teams.get(sp.player.team_id)
    return {
        "player_id": sp.player.id,
        "web_name": sp.player.web_name,
        "position": S.POSITION_NAMES.get(sp.player.element_type, "?"),
        "element_type": sp.player.element_type,
        "team_short_name": team.short_name if team else None,
        "price": round(sp.player.now_cost / 10, 1),
        "status": sp.player.status,
        "news": sp.player.news,
        "pick_position": sp.position,
        "is_captain": sp.is_captain,
        "is_vice_captain": sp.is_vice_captain,
        "expected_points": sp.expected_points,
        "fixtures": [
            {
                "event_id": fp.event_id,
                "opponent": teams[fp.opponent_team_id].short_name
                if fp.opponent_team_id in teams
                else "?",
                "is_home": fp.is_home,
                "difficulty": fp.difficulty,
                "expected_minutes": fp.expected_minutes,
                "expected_points": fp.expected_points,
            }
            for fp in sp.predictions
        ],
    }


@router.get("/{entry_id}")
async def my_team(
    entry_id: int,
    session: Session = Depends(get_session),
    horizon: int = Query(5, ge=1, le=10),
) -> dict:
    """Everything for the My Team dashboard, from a public FPL entry id."""
    async with FPLClient() as client:
        try:
            entry = await client.entry(entry_id)
        except httpx.HTTPStatusError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                raise HTTPException(404, f"FPL entry {entry_id} not found")
            raise HTTPException(502, "FPL API error") from exc
        except FPLUnavailable as exc:
            raise HTTPException(503, "FPL API unavailable and nothing cached") from exc

        # Picks only exist once a gameweek's deadline has passed.
        last_event = entry.get("current_event")
        picks_payload: dict | None = None
        if last_event:
            try:
                picks_payload = await client.entry_picks(entry_id, last_event)
            except (httpx.HTTPStatusError, FPLUnavailable):
                picks_payload = None

    if picks_payload is None:
        raise HTTPException(
            409,
            "No squad is public for this entry yet. FPL only publishes picks after a "
            "gameweek deadline has passed.",
        )

    event_ids = upcoming_event_ids(session, horizon=horizon)
    if not event_ids:
        raise HTTPException(503, "no gameweeks loaded — run ingestion first")

    engine = PredictionEngine(session)
    squad = build_squad(session, picks_payload.get("picks", []), engine, event_ids)
    if not squad:
        raise HTTPException(503, "player data not loaded — run ingestion first")

    xi, bench = best_starting_xi(squad)
    entry_history = picks_payload.get("entry_history", {}) or {}
    bank = entry_history.get("bank", 0)

    all_players = list(session.scalars(select(Player)).all())
    rating = rate_squad(squad, all_players, engine, event_ids)
    transfers = suggest_transfers(session, squad, engine, event_ids, bank)
    teams = {t.id: t for t in session.scalars(select(Team)).all()}
    next_event = session.scalars(select(Event).where(Event.id == event_ids[0])).first()

    return {
        "model_version": MODEL_VERSION,
        "entry": {
            "id": entry_id,
            "name": entry.get("name"),
            "player_name": f"{entry.get('player_first_name', '')} "
            f"{entry.get('player_last_name', '')}".strip(),
            "overall_rank": entry.get("summary_overall_rank"),
            "overall_points": entry.get("summary_overall_points"),
            "current_event": last_event,
            "bank": bank,
            "squad_value": entry_history.get("value"),
            "free_transfers": picks_payload.get("entry_history", {}).get("event_transfers"),
        },
        "horizon": {
            "events": event_ids,
            "next_deadline": next_event.deadline_time.isoformat()
            if next_event and next_event.deadline_time
            else None,
        },
        "rating": rating,
        "starting_xi": [_sp(sp, teams) for sp in xi],
        "bench": [_sp(sp, teams) for sp in bench],
        "captain_options": captain_options(squad),
        "transfer_suggestions": [asdict(t) for t in transfers],
        "caveats": [
            "Selling prices are approximated by current price — the public FPL API "
            "does not expose what you paid.",
            "Suggestions consider one transfer at a time and do not model hits.",
        ],
    }
