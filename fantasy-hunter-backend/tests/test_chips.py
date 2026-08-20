"""Chip legality and the chip-timing prior.

The legality half is a correctness guard: an earlier build happily scheduled a
wildcard in Gameweek 1, which FPL forbids, producing a plan no manager could
execute. The prior half is a belief, and the tests check it is *presented* as
one — normalised, attributed, and never confused with fact.
"""

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models import Base, ChipTimingPrior, ChipWindow, Event, GameweekOutlook
from app.services.chips import (
    KIND_WEIGHT,
    chip_schedule,
    import_priors,
    playable_windows,
)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as s:
        _seed(s)
        yield s


def _seed(session):
    for event_id in range(1, 39):
        session.add(Event(id=event_id, name=f"Gameweek {event_id}"))
    # The real 2026/27 shape: two sets, and no wildcard or free hit in GW1.
    windows = [
        ("wildcard", "transfer", 2, 19, 1),
        ("free_hit", "transfer", 2, 19, 1),
        ("bench_boost", "team", 1, 19, 1),
        ("triple_captain", "team", 1, 19, 1),
        ("wildcard", "transfer", 20, 38, 2),
        ("free_hit", "transfer", 20, 38, 2),
        ("bench_boost", "team", 20, 38, 2),
        ("triple_captain", "team", 20, 38, 2),
    ]
    for name, chip_type, start, stop, half in windows:
        session.add(
            ChipWindow(
                name=name,
                fpl_name=name,
                chip_type=chip_type,
                start_event=start,
                stop_event=stop,
                half=half,
            )
        )
    session.commit()


def test_eight_chip_instances_exist(session):
    """Two sets of four — the post-2025/26 rule, not four chips total."""
    windows = session.scalars(select(ChipWindow)).all()
    assert len(windows) == 8
    assert {w.half for w in windows} == {1, 2}


def test_wildcard_and_free_hit_are_barred_from_gameweek_one(session):
    for name in ("wildcard", "free_hit"):
        window = session.scalar(
            select(ChipWindow).where(ChipWindow.name == name, ChipWindow.half == 1)
        )
        assert window.covers(1) is False, f"{name} must not be playable in GW1"
        assert window.covers(2) is True


def test_bench_boost_and_triple_captain_are_allowed_in_gameweek_one(session):
    for name in ("bench_boost", "triple_captain"):
        window = session.scalar(
            select(ChipWindow).where(ChipWindow.name == name, ChipWindow.half == 1)
        )
        assert window.covers(1) is True


def test_playable_windows_filters_to_the_horizon(session):
    """A plan inside one half sees one instance of each chip, not two."""
    windows = playable_windows(session, ["wildcard"], [1, 2, 3, 4, 5])
    assert len(windows) == 1
    assert windows[0].half == 1


def test_a_plan_spanning_the_halves_sees_both_instances(session):
    windows = playable_windows(session, ["wildcard"], [17, 18, 19, 20, 21])
    assert {w.half for w in windows} == {1, 2}


def test_a_horizon_outside_every_window_yields_nothing(session):
    """GW1 alone offers no wildcard at all."""
    assert playable_windows(session, ["wildcard"], [1]) == []


def _bundle(rows, outlooks=()):
    return {"season": "2026/27", "priors": rows, "outlooks": list(outlooks)}


def test_distribution_is_normalised_within_the_window(session):
    import_priors(
        session,
        _bundle(
            [
                {"chip": "bench_boost", "event": 1, "kind": "planned", "weight": 0.6},
                {"chip": "bench_boost", "event": 2, "kind": "planned", "weight": 0.3},
            ]
        ),
    )
    timing = next(t for t in chip_schedule(session) if t.key == "bench_boost:1")
    # Shares are stored rounded to four places, so compare at that precision.
    assert sum(timing.distribution.values()) == pytest.approx(1.0, abs=1e-3)
    assert timing.distribution[1] == pytest.approx(2 / 3, abs=1e-3)


def test_rows_only_inform_the_instance_whose_window_contains_them(session):
    """A GW33 row must not leak into the first-half chip."""
    import_priors(
        session,
        _bundle(
            [
                {"chip": "bench_boost", "event": 1, "weight": 0.5},
                {"chip": "bench_boost", "event": 33, "weight": 0.5},
            ]
        ),
    )
    schedule = {t.key: t for t in chip_schedule(session)}
    assert set(schedule["bench_boost:1"].distribution) == {1}
    assert set(schedule["bench_boost:2"].distribution) == {33}


def test_observed_history_outweighs_expert_opinion(session):
    """The maturing mechanism: real seasons must dominate the seed."""
    assert KIND_WEIGHT["observed"] > KIND_WEIGHT["planned"] > KIND_WEIGHT["expert"]
    import_priors(
        session,
        _bundle(
            [
                {"chip": "triple_captain", "event": 3, "kind": "expert", "weight": 0.5},
                {"chip": "triple_captain", "event": 16, "kind": "observed", "weight": 0.5},
            ]
        ),
    )
    timing = next(t for t in chip_schedule(session) if t.key == "triple_captain:1")
    assert timing.peak()[0] == 16


def test_every_prior_carries_its_basis_and_source(session):
    """An unexplained number is exactly what this product exists not to ship."""
    import_priors(
        session,
        _bundle(
            [
                {
                    "chip": "wildcard",
                    "event": 6,
                    "weight": 0.35,
                    "basis": "after the international break",
                    "source": "survey",
                }
            ]
        ),
    )
    timing = next(t for t in chip_schedule(session) if t.key == "wildcard:1")
    assert timing.reasons[6] == ["after the international break"]
    assert timing.sources == ["survey"]


def test_reimport_updates_rather_than_duplicates(session):
    rows = [{"chip": "wildcard", "event": 6, "weight": 0.3}]
    import_priors(session, _bundle(rows))
    import_priors(session, _bundle([{"chip": "wildcard", "event": 6, "weight": 0.9}]))
    stored = session.scalars(select(ChipTimingPrior)).all()
    assert len(stored) == 1
    assert stored[0].weight == pytest.approx(0.9)


def test_outlooks_are_stored_as_likelihoods_not_fixtures(session):
    import_priors(
        session,
        _bundle([], [{"event": 33, "double": 0.55, "blank": 0.2, "note": "run-in double"}]),
    )
    row = session.scalar(select(GameweekOutlook).where(GameweekOutlook.event_id == 33))
    assert row.double_likelihood == pytest.approx(0.55)
    assert row.confirmed is False, "a prior must never masquerade as a confirmed fixture"
