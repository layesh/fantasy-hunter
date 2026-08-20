"""Chip legality, and a prior for when each chip is worth playing.

Two separate jobs, deliberately kept apart:

**Legality** is fact. FPL publishes each chip's window in `bootstrap-static`,
and from 2025/26 there are two sets — one expiring at the GW19 deadline, one
for GW20-38 — with the wildcard and free hit barred from GW1. A plan that
ignores this recommends moves a manager cannot make.

**Timing** is a belief, and is treated as one. The chips that matter most are
played in double and blank gameweeks, which do not exist in the fixture list at
the start of a season: they are created later by cup progression and
postponements. So the schedule below is a *prior* — seeded from published
community and expert sources, with the basis recorded against every row — not a
prediction dressed up as fact. Observed rows, once we have real seasons behind
us, outrank the seed. That is the mechanism by which this matures.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ChipTimingPrior, ChipWindow, Event, GameweekOutlook

# Observed history beats a pre-season survey of intent, which beats an expert's
# recommendation. Weights are relative, and only matter when kinds disagree.
KIND_WEIGHT = {"observed": 1.0, "planned": 0.6, "expert": 0.35}

FIRST_HALF_END = 19


@dataclass
class ChipTiming:
    """One chip instance's distribution across the gameweeks it may be played."""

    chip: str
    half: int
    start_event: int
    stop_event: int
    # event id -> probability share, summing to 1 across the window
    distribution: dict[int, float] = field(default_factory=dict)
    reasons: dict[int, list[str]] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.chip}:{self.half}"

    def peak(self) -> tuple[int, float] | None:
        if not self.distribution:
            return None
        event = max(self.distribution, key=lambda e: self.distribution[e])
        return event, self.distribution[event]


def playable_windows(
    session: Session, chip_names: list[str], event_ids: list[int]
) -> list[ChipWindow]:
    """Chip instances that are legal in at least one gameweek of the horizon.

    A plan spanning the halves (say GW17-22) legitimately sees two instances of
    the same chip; a plan inside one half sees at most one.
    """
    if not chip_names:
        return []
    windows = session.scalars(select(ChipWindow).where(ChipWindow.name.in_(chip_names))).all()
    return [w for w in windows if any(w.covers(event) for event in event_ids)]


def all_windows(session: Session) -> list[ChipWindow]:
    return list(
        session.scalars(select(ChipWindow).order_by(ChipWindow.half, ChipWindow.name)).all()
    )


def import_priors(session: Session, payload: dict) -> dict[str, int]:
    """Load a seeded chip-timing bundle. Re-importing a season replaces it."""
    season = payload["season"]
    counts = {"priors": 0, "outlooks": 0}

    for row in payload.get("priors", []):
        existing = session.scalar(
            select(ChipTimingPrior).where(
                ChipTimingPrior.season == season,
                ChipTimingPrior.chip == row["chip"],
                ChipTimingPrior.event_id == row["event"],
                ChipTimingPrior.kind == row.get("kind", "expert"),
            )
        )
        target = existing or ChipTimingPrior(
            season=season,
            chip=row["chip"],
            event_id=row["event"],
            kind=row.get("kind", "expert"),
        )
        target.weight = float(row["weight"])
        target.basis = row.get("basis", "")
        target.source = row.get("source", "")
        if existing is None:
            session.add(target)
        counts["priors"] += 1

    for row in payload.get("outlooks", []):
        existing = session.scalar(
            select(GameweekOutlook).where(
                GameweekOutlook.season == season, GameweekOutlook.event_id == row["event"]
            )
        )
        target = existing or GameweekOutlook(season=season, event_id=row["event"])
        target.double_likelihood = float(row.get("double", 0.0))
        target.blank_likelihood = float(row.get("blank", 0.0))
        target.confirmed = bool(row.get("confirmed", False))
        target.note = row.get("note", "")
        if existing is None:
            session.add(target)
        counts["outlooks"] += 1

    session.commit()
    return counts


def _season(session: Session, season: str | None) -> str:
    if season:
        return season
    row = session.scalar(select(ChipTimingPrior.season).limit(1))
    return row or ""


def chip_schedule(session: Session, *, season: str | None = None) -> list[ChipTiming]:
    """Blend the stored priors into one distribution per chip instance.

    Rows of different kinds are combined by `KIND_WEIGHT`, then normalised
    across each chip's legal window so the numbers read as "share of the time
    this is the right week", not as raw vote counts.
    """
    season = _season(session, season)
    windows = all_windows(session)
    rows = list(
        session.scalars(select(ChipTimingPrior).where(ChipTimingPrior.season == season)).all()
    )

    by_chip: dict[str, list[ChipTimingPrior]] = defaultdict(list)
    for row in rows:
        by_chip[row.chip].append(row)

    timings: list[ChipTiming] = []
    for window in windows:
        timing = ChipTiming(
            chip=window.name,
            half=window.half,
            start_event=window.start_event,
            stop_event=window.stop_event,
        )
        scores: dict[int, float] = defaultdict(float)
        for row in by_chip.get(window.name, []):
            # A row only informs the instance whose window contains it.
            if not window.covers(row.event_id):
                continue
            scores[row.event_id] += row.weight * KIND_WEIGHT.get(row.kind, 0.3)
            if row.basis:
                timing.reasons.setdefault(row.event_id, []).append(row.basis)
            if row.source and row.source not in timing.sources:
                timing.sources.append(row.source)

        total = sum(scores.values())
        if total > 0:
            timing.distribution = {
                event: round(score / total, 4) for event, score in sorted(scores.items())
            }
        timings.append(timing)

    timings.sort(key=lambda t: (t.half, t.chip))
    return timings


def gameweek_outlook(session: Session, *, season: str | None = None) -> list[GameweekOutlook]:
    season = _season(session, season)
    return list(
        session.scalars(
            select(GameweekOutlook)
            .where(GameweekOutlook.season == season)
            .order_by(GameweekOutlook.event_id)
        ).all()
    )


def deadlines(session: Session) -> dict[int, str | None]:
    return {
        event.id: event.deadline_time.isoformat() if event.deadline_time else None
        for event in session.scalars(select(Event))
    }


def load_bundle(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)
