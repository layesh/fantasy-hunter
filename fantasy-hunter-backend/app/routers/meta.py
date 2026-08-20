from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.ingest import run_full_ingest
from app.models import Event, IngestRun, Player, Team

router = APIRouter(tags=["meta"])


@router.get("/health")
def health(session: Session = Depends(get_session)) -> dict:
    last = session.scalars(
        select(IngestRun).order_by(desc(IngestRun.started_at)).limit(1)
    ).first()
    return {
        "status": "ok",
        "players": session.scalar(select(func.count()).select_from(Player)) or 0,
        "last_ingest": {
            "source": last.source,
            "ok": last.ok,
            "rows": last.rows,
            "started_at": last.started_at.isoformat() if last.started_at else None,
            "detail": last.detail,
        }
        if last
        else None,
    }


@router.get("/teams")
def list_teams(session: Session = Depends(get_session)) -> list[dict]:
    teams = session.scalars(select(Team).order_by(Team.name)).all()
    return [
        {
            "id": t.id,
            "name": t.name,
            "short_name": t.short_name,
            "strength": t.strength,
            "strength_attack_home": t.strength_attack_home,
            "strength_attack_away": t.strength_attack_away,
            "strength_defence_home": t.strength_defence_home,
            "strength_defence_away": t.strength_defence_away,
        }
        for t in teams
    ]


@router.get("/events")
def list_events(session: Session = Depends(get_session)) -> list[dict]:
    events = session.scalars(select(Event).order_by(Event.id)).all()
    return [
        {
            "id": e.id,
            "name": e.name,
            "deadline_time": e.deadline_time.isoformat() if e.deadline_time else None,
            "finished": e.finished,
            "is_current": e.is_current,
            "is_next": e.is_next,
            "average_entry_score": e.average_entry_score,
        }
        for e in events
    ]


@router.post("/ingest")
async def trigger_ingest(
    background: BackgroundTasks, include_histories: bool = False
) -> dict:
    """Kick off ingestion. Histories are ~600 upstream calls, so opt in."""
    background.add_task(run_full_ingest, include_histories)
    return {"started": True, "include_histories": include_histories}
