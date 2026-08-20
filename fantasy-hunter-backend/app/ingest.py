"""Pulls the official FPL API into our own store.

Everything downstream (tools, predictions, grading) reads from Postgres/SQLite,
never from the origin API on the request path.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal, init_db
from app.fpl_client import FPLClient
from app.models import (
    ChipWindow,
    Event,
    Fixture,
    IngestRun,
    Player,
    PlayerGameweek,
    PlayerSeason,
    Team,
)

# FPL's internal chip names differ from the ones managers use.
CHIP_NAMES = {
    "wildcard": "wildcard",
    "freehit": "free_hit",
    "bboost": "bench_boost",
    "3xc": "triple_captain",
    "manager": "assistant_manager",
}

log = logging.getLogger(__name__)


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _f(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _i(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _upsert(session: Session, model: type, pk: int, values: dict) -> Any:
    row = session.get(model, pk)
    if row is None:
        row = model(id=pk, **values)
        session.add(row)
    else:
        for key, value in values.items():
            setattr(row, key, value)
    return row


class Ingestor:
    def __init__(self, session: Session, client: FPLClient) -> None:
        self.session = session
        self.client = client

    def _run(self, source: str) -> IngestRun:
        run = IngestRun(source=source, started_at=datetime.now(timezone.utc))
        self.session.add(run)
        self.session.flush()
        return run

    async def ingest_bootstrap(self) -> int:
        """Teams, gameweeks and the current player snapshot."""
        run = self._run("bootstrap-static")
        try:
            data = await self.client.bootstrap_static()
            rows = 0

            for t in data["teams"]:
                _upsert(
                    self.session,
                    Team,
                    t["id"],
                    {
                        "code": t["code"],
                        "name": t["name"],
                        "short_name": t["short_name"],
                        "strength": _i(t.get("strength")),
                        "strength_overall_home": _i(t.get("strength_overall_home")),
                        "strength_overall_away": _i(t.get("strength_overall_away")),
                        "strength_attack_home": _i(t.get("strength_attack_home")),
                        "strength_attack_away": _i(t.get("strength_attack_away")),
                        "strength_defence_home": _i(t.get("strength_defence_home")),
                        "strength_defence_away": _i(t.get("strength_defence_away")),
                    },
                )
                rows += 1

            # Chip windows. FPL issues two sets from 2025/26 and bars the
            # wildcard and free hit from GW1, so these rules are read from the
            # API rather than assumed.
            for chip in data.get("chips", []) or []:
                name = CHIP_NAMES.get(chip.get("name", ""), chip.get("name", ""))
                start = _i(chip.get("start_event")) or 1
                stop = _i(chip.get("stop_event")) or 38
                existing = self.session.scalar(
                    select(ChipWindow).where(
                        ChipWindow.name == name, ChipWindow.start_event == start
                    )
                )
                target = existing or ChipWindow(name=name, start_event=start)
                target.fpl_name = chip.get("name", "")
                target.chip_type = chip.get("chip_type", "")
                target.stop_event = stop
                target.half = 1 if start < 20 else 2
                if existing is None:
                    self.session.add(target)
                rows += 1

            for e in data["events"]:
                _upsert(
                    self.session,
                    Event,
                    e["id"],
                    {
                        "name": e["name"],
                        "deadline_time": _dt(e.get("deadline_time")),
                        "finished": bool(e.get("finished")),
                        "data_checked": bool(e.get("data_checked")),
                        "is_current": bool(e.get("is_current")),
                        "is_next": bool(e.get("is_next")),
                        "is_previous": bool(e.get("is_previous")),
                        "average_entry_score": e.get("average_entry_score"),
                        "highest_score": e.get("highest_score"),
                    },
                )
                rows += 1

            for p in data["elements"]:
                _upsert(
                    self.session,
                    Player,
                    p["id"],
                    {
                        "code": p["code"],
                        "team_id": p["team"],
                        "element_type": p["element_type"],
                        "first_name": p.get("first_name") or "",
                        "second_name": p.get("second_name") or "",
                        "web_name": p.get("web_name") or "",
                        "now_cost": _i(p.get("now_cost")),
                        "cost_change_start": _i(p.get("cost_change_start")),
                        "cost_change_event": _i(p.get("cost_change_event")),
                        "selected_by_percent": _f(p.get("selected_by_percent")),
                        "status": p.get("status") or "a",
                        "news": (p.get("news") or "")[:512],
                        "chance_of_playing_next_round": p.get("chance_of_playing_next_round"),
                        "total_points": _i(p.get("total_points")),
                        "event_points": _i(p.get("event_points")),
                        "points_per_game": _f(p.get("points_per_game")),
                        "form": _f(p.get("form")),
                        "minutes": _i(p.get("minutes")),
                        "starts": _i(p.get("starts")),
                        "goals_scored": _i(p.get("goals_scored")),
                        "assists": _i(p.get("assists")),
                        "clean_sheets": _i(p.get("clean_sheets")),
                        "goals_conceded": _i(p.get("goals_conceded")),
                        "saves": _i(p.get("saves")),
                        "bonus": _i(p.get("bonus")),
                        "bps": _i(p.get("bps")),
                        "yellow_cards": _i(p.get("yellow_cards")),
                        "red_cards": _i(p.get("red_cards")),
                        "defensive_contribution": _i(p.get("defensive_contribution")),
                        "expected_goals": _f(p.get("expected_goals")),
                        "expected_assists": _f(p.get("expected_assists")),
                        "expected_goal_involvements": _f(p.get("expected_goal_involvements")),
                        "expected_goals_conceded": _f(p.get("expected_goals_conceded")),
                        "influence": _f(p.get("influence")),
                        "creativity": _f(p.get("creativity")),
                        "threat": _f(p.get("threat")),
                        "ict_index": _f(p.get("ict_index")),
                        "penalties_order": p.get("penalties_order"),
                        "corners_and_indirect_freekicks_order": p.get(
                            "corners_and_indirect_freekicks_order"
                        ),
                        "direct_freekicks_order": p.get("direct_freekicks_order"),
                        "transfers_in_event": _i(p.get("transfers_in_event")),
                        "transfers_out_event": _i(p.get("transfers_out_event")),
                        "photo": p.get("photo") or "",
                    },
                )
                rows += 1

            run.ok, run.rows = True, rows
            run.finished_at = datetime.now(timezone.utc)
            self.session.commit()
            log.info("bootstrap ingest: %s rows", rows)
            return rows
        except Exception as exc:
            self.session.rollback()
            run = self.session.merge(run)
            run.ok, run.detail = False, str(exc)[:512]
            run.finished_at = datetime.now(timezone.utc)
            self.session.commit()
            raise

    async def ingest_fixtures(self) -> int:
        run = self._run("fixtures")
        try:
            data = await self.client.fixtures()
            for f in data:
                _upsert(
                    self.session,
                    Fixture,
                    f["id"],
                    {
                        "code": f["code"],
                        "event_id": f.get("event"),
                        "team_h": f["team_h"],
                        "team_a": f["team_a"],
                        "team_h_difficulty": _i(f.get("team_h_difficulty")) or 3,
                        "team_a_difficulty": _i(f.get("team_a_difficulty")) or 3,
                        "team_h_score": f.get("team_h_score"),
                        "team_a_score": f.get("team_a_score"),
                        "kickoff_time": _dt(f.get("kickoff_time")),
                        "started": bool(f.get("started")),
                        "finished": bool(f.get("finished")),
                        "minutes": _i(f.get("minutes")),
                    },
                )
            run.ok, run.rows = True, len(data)
            run.finished_at = datetime.now(timezone.utc)
            self.session.commit()
            log.info("fixtures ingest: %s rows", len(data))
            return len(data)
        except Exception as exc:
            self.session.rollback()
            run = self.session.merge(run)
            run.ok, run.detail = False, str(exc)[:512]
            run.finished_at = datetime.now(timezone.utc)
            self.session.commit()
            raise

    async def ingest_player_histories(self, player_ids: list[int] | None = None) -> int:
        """Per-player past-season totals and this season's gameweek returns.

        ~600 requests, so this is a slow, occasional job — not something the API
        tier ever triggers synchronously.
        """
        run = self._run("element-summary")
        try:
            if player_ids is None:
                player_ids = list(self.session.scalars(select(Player.id)))
            id_to_code = dict(
                self.session.execute(
                    select(Player.id, Player.code).where(Player.id.in_(player_ids))
                ).all()
            )

            summaries = await self.client.element_summaries(player_ids)
            rows = 0

            existing_seasons = {
                (code, name)
                for code, name in self.session.execute(
                    select(PlayerSeason.element_code, PlayerSeason.season_name)
                ).all()
            }
            existing_gws = {
                (pid, fid)
                for pid, fid in self.session.execute(
                    select(PlayerGameweek.player_id, PlayerGameweek.fixture_id)
                ).all()
            }

            for player_id, summary in summaries.items():
                code = id_to_code.get(player_id)
                for past in summary.get("history_past", []):
                    key = (code, past["season_name"])
                    if code is None or key in existing_seasons:
                        continue
                    existing_seasons.add(key)
                    self.session.add(
                        PlayerSeason(
                            element_code=code,
                            season_name=past["season_name"],
                            start_cost=_i(past.get("start_cost")),
                            end_cost=_i(past.get("end_cost")),
                            total_points=_i(past.get("total_points")),
                            minutes=_i(past.get("minutes")),
                            starts=_i(past.get("starts")),
                            goals_scored=_i(past.get("goals_scored")),
                            assists=_i(past.get("assists")),
                            clean_sheets=_i(past.get("clean_sheets")),
                            goals_conceded=_i(past.get("goals_conceded")),
                            saves=_i(past.get("saves")),
                            bonus=_i(past.get("bonus")),
                            bps=_i(past.get("bps")),
                            yellow_cards=_i(past.get("yellow_cards")),
                            red_cards=_i(past.get("red_cards")),
                            defensive_contribution=_i(past.get("defensive_contribution")),
                            expected_goals=_f(past.get("expected_goals")),
                            expected_assists=_f(past.get("expected_assists")),
                            expected_goals_conceded=_f(past.get("expected_goals_conceded")),
                        )
                    )
                    rows += 1

                for gw in summary.get("history", []):
                    key = (player_id, gw["fixture"])
                    if key in existing_gws:
                        continue
                    existing_gws.add(key)
                    self.session.add(
                        PlayerGameweek(
                            player_id=player_id,
                            event_id=gw.get("round"),
                            fixture_id=gw["fixture"],
                            total_points=_i(gw.get("total_points")),
                            minutes=_i(gw.get("minutes")),
                            goals_scored=_i(gw.get("goals_scored")),
                            assists=_i(gw.get("assists")),
                            clean_sheets=_i(gw.get("clean_sheets")),
                            goals_conceded=_i(gw.get("goals_conceded")),
                            saves=_i(gw.get("saves")),
                            bonus=_i(gw.get("bonus")),
                            bps=_i(gw.get("bps")),
                            defensive_contribution=_i(gw.get("defensive_contribution")),
                            was_home=bool(gw.get("was_home")),
                            opponent_team=gw.get("opponent_team"),
                            value=_i(gw.get("value")),
                        )
                    )
                    rows += 1

            run.ok, run.rows = True, rows
            run.finished_at = datetime.now(timezone.utc)
            self.session.commit()
            log.info("player history ingest: %s rows from %s players", rows, len(summaries))
            return rows
        except Exception as exc:
            self.session.rollback()
            run = self.session.merge(run)
            run.ok, run.detail = False, str(exc)[:512]
            run.finished_at = datetime.now(timezone.utc)
            self.session.commit()
            raise


async def run_full_ingest(include_histories: bool = True) -> dict[str, int]:
    init_db()
    counts: dict[str, int] = {}
    async with FPLClient() as client:
        with SessionLocal() as session:
            ingestor = Ingestor(session, client)
            counts["bootstrap"] = await ingestor.ingest_bootstrap()
            counts["fixtures"] = await ingestor.ingest_fixtures()
            if include_histories:
                counts["histories"] = await ingestor.ingest_player_histories()
    return counts
