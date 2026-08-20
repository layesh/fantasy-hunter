"""Chip legality and timing.

Two kinds of information, kept visibly separate in the response: `windows` are
rules published by FPL, `schedule` and `outlook` are priors we have assembled
and are labelled as such, down to the source on every row.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.services.chips import (
    FIRST_HALF_END,
    all_windows,
    chip_schedule,
    deadlines,
    gameweek_outlook,
)

router = APIRouter(prefix="/chips", tags=["chips"])


@router.get("")
def chip_plan(session: Session = Depends(get_session)) -> dict:
    """Everything needed to reason about chips before a season starts."""
    windows = all_windows(session)
    timings = chip_schedule(session)
    outlook = gameweek_outlook(session)
    when = deadlines(session)

    return {
        "first_half_ends": FIRST_HALF_END,
        "windows": [
            {
                "key": w.key,
                "chip": w.name,
                "half": w.half,
                "chip_type": w.chip_type,
                "start_event": w.start_event,
                "stop_event": w.stop_event,
            }
            for w in windows
        ],
        "schedule": [
            {
                "key": t.key,
                "chip": t.chip,
                "half": t.half,
                "start_event": t.start_event,
                "stop_event": t.stop_event,
                "peak_event": t.peak()[0] if t.peak() else None,
                "peak_share": round(t.peak()[1], 4) if t.peak() else None,
                "points": [
                    {
                        "event": event,
                        "share": share,
                        "reasons": t.reasons.get(event, []),
                        "deadline": when.get(event),
                    }
                    for event, share in sorted(t.distribution.items())
                ],
                "sources": t.sources,
            }
            for t in timings
        ],
        "outlook": [
            {
                "event": row.event_id,
                "double": round(row.double_likelihood, 3),
                "blank": round(row.blank_likelihood, 3),
                "confirmed": row.confirmed,
                "note": row.note,
            }
            for row in outlook
        ],
    }
