"""Offline jobs. These are what a scheduler will call later; for now, run by hand.

    python manage.py ingest [--histories]
    python manage.py snapshot [--horizon 5]
    python manage.py grade
    python manage.py predict <player_id>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from sqlalchemy import select

from app.db import SessionLocal, init_db
from app.ingest import run_full_ingest
from app.models import Event, Player, PlayerGameweek, Prediction, PredictionGrade
from app.services import scoring as S
from app.services.defence import club_defence, import_defence
from app.services.chips import (
    chip_schedule,
    gameweek_outlook,
    import_priors,
    load_bundle,
)
from app.services.lineups import (
    coverage,
    import_lineups,
    is_preseason,
    start_probabilities,
)
from app.services.predictions import (
    MODEL_VERSION,
    PredictionEngine,
    snapshot_predictions,
    upcoming_event_ids,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def cmd_ingest(args: argparse.Namespace) -> None:
    counts = asyncio.run(run_full_ingest(include_histories=args.histories))
    print("ingested:", counts)


def cmd_snapshot(args: argparse.Namespace) -> None:
    init_db()
    with SessionLocal() as session:
        events = upcoming_event_ids(session, horizon=args.horizon)
        written = snapshot_predictions(session, events)
        print(f"snapshotted {written} predictions for gameweeks {events}")


def cmd_grade(_: argparse.Namespace) -> None:
    """Compare stored predictions against actual returns for finished gameweeks."""
    init_db()
    with SessionLocal() as session:
        finished = {
            e.id for e in session.scalars(select(Event).where(Event.finished.is_(True))).all()
        }
        if not finished:
            print("no finished gameweeks to grade yet")
            return

        actuals = {
            (gw.player_id, gw.fixture_id): gw
            for gw in session.scalars(select(PlayerGameweek)).all()
        }
        already = {
            pid for (pid,) in session.execute(select(PredictionGrade.prediction_id)).all()
        }

        graded = 0
        predictions = session.scalars(
            select(Prediction).where(
                Prediction.model_version == MODEL_VERSION,
                Prediction.event_id.in_(finished),
            )
        ).all()
        for prediction in predictions:
            if prediction.id in already:
                continue
            actual = actuals.get((prediction.player_id, prediction.fixture_id))
            if actual is None:
                continue
            session.add(
                PredictionGrade(
                    prediction_id=prediction.id,
                    actual_points=actual.total_points,
                    actual_minutes=actual.minutes,
                    error=round(prediction.expected_points - actual.total_points, 3),
                )
            )
            graded += 1
        session.commit()
        print(f"graded {graded} predictions across gameweeks {sorted(finished)}")


def cmd_predict(args: argparse.Namespace) -> None:
    init_db()
    with SessionLocal() as session:
        from app.models import Player

        player = session.get(Player, args.player_id)
        if player is None:
            sys.exit(f"no player with id {args.player_id}")
        events = upcoming_event_ids(session, horizon=args.horizon)
        engine = PredictionEngine(session)
        fixtures_by_team = engine.fixtures_for_events(events)
        print(f"{player.web_name} ({MODEL_VERSION}) over gameweeks {events}")
        print(f"  profile: {engine.profile(player)}")
        total = 0.0
        for fp in engine.predict_player(player, events, fixtures_by_team):
            total += fp.expected_points
            venue = "H" if fp.is_home else "A"
            print(
                f"  GW{fp.event_id} vs {fp.opponent_team_id}{venue}: "
                f"{fp.expected_points:.2f} xPts ({fp.expected_minutes:.0f} mins)"
            )
        print(f"  total: {total:.2f}")


def cmd_lineups(args: argparse.Namespace) -> None:
    """Import a predicted-XI bundle and/or report the resulting index."""
    init_db()
    with SessionLocal() as session:
        if args.path:
            with open(args.path, encoding="utf-8") as handle:
                payload = json.load(handle)
            reports = import_lineups(session, payload)
            print(f"{'source':<22}{'names':<12}{'rate':<8}{'clubs':<7}trusted")
            print("-" * 62)
            for report in sorted(reports, key=lambda r: -r.rate):
                print(
                    f"{report.slug:<22}{report.matched}/{report.total:<9}"
                    f"{report.rate:>5.0%}   {report.teams_kept:<7}"
                    f"{'yes' if report.trusted else 'NO'}"
                )
                if report.teams_dropped:
                    print(f"    dropped: {', '.join(report.teams_dropped[:8])}")
            print()

        if not is_preseason(session):
            print("Gameweek 1 has finished — the index is no longer consulted.")

        probabilities = start_probabilities(session, force=True)
        if not probabilities:
            print("No consensus index. Import a bundle first.")
            return

        by_team = coverage(session)
        print(f"Consensus index: {len(probabilities)} players across {len(by_team)} clubs")
        rows = [
            (session.get(Player, pid), prob)
            for pid, prob in probabilities.items()
            if prob >= args.min_probability
        ]
        rows.sort(key=lambda r: (-r[1], -r[0].selected_by_percent))
        print(f"\n{'player':<18}{'club':<6}{'pos':<5}{'cost':>6}  {'start%':>7}  owned")
        print("-" * 56)
        for player, prob in rows[:40]:
            position = S.POSITION_NAMES.get(player.element_type, '?')
            print(
                f"{player.web_name:<18}{player.team.short_name:<6}"
                f"{position:<5}{player.now_cost / 10:>5.1f}m"
                f"{prob:>8.0%}  {player.selected_by_percent:>5.1f}%"
            )


def cmd_chips(args: argparse.Namespace) -> None:
    """Import the chip-timing prior and/or print the resulting schedule."""
    init_db()
    with SessionLocal() as session:
        if args.path:
            counts = import_priors(session, load_bundle(args.path))
            print(
                f"imported {counts['priors']} priors, "
                f"{counts['outlooks']} gameweek outlooks"
            )
            print()

        for timing in chip_schedule(session):
            peak = timing.peak()
            head = f"{timing.key:<22}GW{timing.start_event}-{timing.stop_event}"
            if peak is None:
                print(f"{head}  no prior")
                continue
            print(f"{head}  peak GW{peak[0]} ({peak[1]:.0%})")
            for event, share in sorted(
                timing.distribution.items(), key=lambda kv: -kv[1]
            )[:4]:
                bar = "#" * max(1, round(share * 30))
                reason = (timing.reasons.get(event) or [""])[0]
                print(f"    GW{event:<3}{share:>5.0%} {bar:<30} {reason[:70]}")
            print()

        outlooks = gameweek_outlook(session)
        if outlooks:
            print("Gameweek outlook (prior likelihood, not fixture fact)")
            print(f"  {'gw':<5}{'double':<9}{'blank':<9}note")
            for row in outlooks:
                print(
                    f"  {row.event_id:<5}{row.double_likelihood:<9.0%}"
                    f"{row.blank_likelihood:<9.0%}{row.note[:60]}"
                )


def cmd_defence(args: argparse.Namespace) -> None:
    """Import club defensive records and/or print the recency-weighted table."""
    init_db()
    with SessionLocal() as session:
        if args.path:
            rows = import_defence(session, load_bundle(args.path))
            print(f"imported {rows} club-season records")
            print()

        print(f"{'club':<28}{'CS/38':>7}{'GC/g':>7}{'xGC/g':>7}   by season")
        print("-" * 78)
        for club in club_defence(session):
            if not club.known:
                print(f"{club.name:<28}{'—':>7}{'—':>7}{'—':>7}   no Premier League record")
                continue
            detail = "  ".join(
                f"{s['season']}: {s['clean_sheets']}CS/{s['goals_conceded']}GC"
                for s in club.seasons
            )
            print(
                f"{club.name:<28}{club.clean_sheets_per_38:>7.1f}"
                f"{club.goals_conceded_per_game:>7.2f}"
                f"{club.expected_goals_conceded_per_game:>7.2f}   {detail}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="pull the FPL API into the local database")
    p_ingest.add_argument(
        "--histories",
        action="store_true",
        help="also pull per-player season history (~600 upstream calls, slow)",
    )
    p_ingest.set_defaults(func=cmd_ingest)

    p_snap = sub.add_parser("snapshot", help="freeze predictions for later grading")
    p_snap.add_argument("--horizon", type=int, default=5)
    p_snap.set_defaults(func=cmd_snapshot)

    p_grade = sub.add_parser("grade", help="score stored predictions against actuals")
    p_grade.set_defaults(func=cmd_grade)

    p_predict = sub.add_parser("predict", help="print one player's prediction breakdown")
    p_predict.add_argument("player_id", type=int)
    p_predict.add_argument("--horizon", type=int, default=5)
    p_predict.set_defaults(func=cmd_predict)

    p_lineups = sub.add_parser(
        "lineups", help="import/report the pre-season predicted-XI consensus index"
    )
    p_lineups.add_argument(
        "--import", dest="path", help="path to a predicted-lineup JSON bundle"
    )
    p_lineups.add_argument(
        "--min-probability",
        type=float,
        default=0.0,
        help="only report players at or above this consensus start probability",
    )
    p_lineups.set_defaults(func=cmd_lineups)

    p_chips = sub.add_parser("chips", help="import/report the chip-timing prior")
    p_chips.add_argument("--import", dest="path", help="path to a chip-timing JSON bundle")
    p_chips.set_defaults(func=cmd_chips)

    p_def = sub.add_parser("defence", help="import/report club defensive records")
    p_def.add_argument("--import", dest="path", help="path to a team-defence JSON bundle")
    p_def.set_defaults(func=cmd_defence)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
