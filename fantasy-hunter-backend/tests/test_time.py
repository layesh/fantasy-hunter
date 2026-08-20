"""Timestamps must leave the API as unambiguous UTC instants.

SQLite has no timezone type, so `DateTime(timezone=True)` returns naive
datetimes and serialises without an offset. A browser reads an offset-less ISO
string as *local* time, so the GW1 deadline of 17:30 UTC displayed as 17:30 to
a reader in UTC+6 instead of 23:30 — six hours of error on the one number a
manager cannot afford to get wrong.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models import Base, Event, Fixture


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as s:
        yield s


def _store(session, value):
    session.add(Event(id=1, name="Gameweek 1", deadline_time=value))
    session.commit()
    session.expunge_all()
    return session.get(Event, 1).deadline_time


def test_deadline_round_trips_as_aware_utc(session):
    stored = _store(session, datetime(2026, 8, 21, 17, 30, tzinfo=timezone.utc))
    assert stored.tzinfo is not None, "a naive deadline is read as local time by browsers"
    assert stored.utcoffset() == timedelta(0)
    assert stored.isoformat() == "2026-08-21T17:30:00+00:00"


def test_serialised_form_carries_an_offset(session):
    """The exact string the frontend receives must be unambiguous."""
    stored = _store(session, datetime(2026, 8, 21, 17, 30, tzinfo=timezone.utc))
    rendered = stored.isoformat()
    assert rendered.endswith("+00:00") or rendered.endswith("Z")


def test_a_non_utc_input_is_converted_not_truncated(session):
    """17:30 UTC+6 is 11:30 UTC — the instant must survive, not the wall clock."""
    dhaka = timezone(timedelta(hours=6))
    stored = _store(session, datetime(2026, 8, 21, 23, 30, tzinfo=dhaka))
    assert stored == datetime(2026, 8, 21, 17, 30, tzinfo=timezone.utc)


def test_naive_input_is_treated_as_utc(session):
    """Rows written before this type existed were naive UTC; they must be labelled."""
    stored = _store(session, datetime(2026, 8, 21, 17, 30))
    assert stored == datetime(2026, 8, 21, 17, 30, tzinfo=timezone.utc)


def test_the_same_rule_applies_to_kickoffs(session):
    """Not just deadlines — every timestamp the API emits."""
    session.add(
        Fixture(
            id=1,
            code=1,
            event_id=None,
            team_h=1,
            team_a=2,
            kickoff_time=datetime(2026, 8, 21, 19, 0, tzinfo=timezone.utc),
        )
    )
    session.commit()
    session.expunge_all()
    kickoff = session.scalar(select(Fixture)).kickoff_time
    assert kickoff.tzinfo is not None
    assert kickoff.utcoffset() == timedelta(0)


def test_none_stays_none(session):
    assert _store(session, None) is None
