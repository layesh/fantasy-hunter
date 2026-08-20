"""Tests for the pre-season predicted-XI consensus index.

The index exists to stop the optimiser recommending players nobody expects to
start. Its failure modes are all quiet ones — a name silently unmatched, a
stale source silently trusted, a 0.0 that means "no data" being read as "will
not play" — so these tests lean on exactly those.
"""

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models import Base, Event, LineupSource, Player, PredictedLineup, Team
from app.services import scoring as S
from app.services.lineups import (
    MIN_SOURCES_FOR_CONSENSUS,
    coverage,
    import_lineups,
    is_preseason,
    normalise,
    start_probabilities,
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
    session.add(Event(id=1, name="Gameweek 1", finished=False, is_next=True))
    session.add(Team(id=1, code=1, name="Arsenal", short_name="ARS"))
    session.add(Team(id=2, code=2, name="Everton", short_name="EVE"))

    # Names chosen to exercise the matcher: an accent, a Danish ø, an
    # apostrophe, an initialised web_name, and two players sharing a surname.
    people = [
        (1, 1, "David", "Raya", "Raya", S.GKP),
        (2, 1, "Martin", "Ødegaard", "Ødegaard", S.MID),
        (3, 1, "Bruno", "Fernandes", "B.Fernandes", S.MID),
        (4, 1, "Gabriel", "Magalhães", "Gabriel", S.DEF),
        (5, 2, "Jake", "O'Brien", "O'Brien", S.DEF),
        (6, 2, "Jordan", "Pickford", "Pickford", S.GKP),
        # Deliberate surname collision within one club.
        (7, 2, "Michael", "Keane", "Keane", S.DEF),
        (8, 2, "Will", "Keane", "W.Keane", S.FWD),
    ]
    # Enough Arsenal bodies that a legal eleven of real names exists.
    people += [
        (10 + i, 1, "Fill", f"Filler{i}", f"Filler{i}", S.MID) for i in range(8)
    ]
    for pid, team_id, first, second, web, element_type in people:
        session.add(
            Player(
                id=pid,
                code=100 + pid,
                team_id=team_id,
                element_type=element_type,
                first_name=first,
                second_name=second,
                web_name=web,
                now_cost=50,
                status="a",
            )
        )
    session.commit()


def _legal_xi():
    """Eleven Arsenal names that all resolve, so the quality gate passes."""
    return ["Raya", "Ødegaard", "Gabriel"] + [f"Filler{i}" for i in range(8)]


def _bundle(slug, lineups, **extra):
    return {
        "event": 1,
        "sources": [{"slug": slug, "name": slug, "lineups": lineups, **extra}],
    }


def test_normalise_folds_accents_and_punctuation():
    assert normalise("Ødegaard") == "odegaard"
    assert normalise("Magalhães") == "magalhaes"
    assert normalise("O'Brien") == "o brien"
    assert normalise("Groß") == "gross"
    assert normalise("B.Fernandes") == "b fernandes"


def test_matches_accented_and_initialised_names(session):
    reports = import_lineups(
        session, _bundle("src", {"ARS": ["Raya", "Odegaard", "Bruno Fernandes", "Gabriel"]})
    )
    # Four names, all resolvable despite accents and the initialised web_name.
    assert reports[0].matched == 4


def test_ambiguous_surname_is_never_guessed(session):
    """Two Keanes at one club: neither may be credited."""
    reports = import_lineups(session, _bundle("src", {"EVE": ["Keane"]}))
    assert reports[0].matched == 0, "an ambiguous surname must resolve to nobody"
    # Pickford is unambiguous and still resolves, so the rule is targeted.
    reports = import_lineups(session, _bundle("src2", {"EVE": ["Pickford"]}))
    assert reports[0].matched == 1


def test_incomplete_xi_is_dropped(session):
    reports = import_lineups(session, _bundle("src", {"ARS": ["Raya", "Gabriel"]}))
    assert reports[0].teams_kept == 0
    assert any("ARS" in dropped for dropped in reports[0].teams_dropped)


def test_stale_source_is_not_trusted(session):
    """A source naming players outside the club's squad fails the gate."""
    stale = ["Ghost%d" % i for i in range(11)]
    reports = import_lineups(session, _bundle("stale", {"ARS": stale}))
    assert reports[0].matched == 0
    assert reports[0].trusted is False
    source = session.scalar(select(LineupSource).where(LineupSource.slug == "stale"))
    assert source.trusted is False


def test_untrusted_sources_do_not_reach_the_index(session):
    import_lineups(session, _bundle("stale", {"ARS": ["Ghost%d" % i for i in range(11)]}))
    assert start_probabilities(session, force=True) == {}


def test_probability_is_the_fraction_of_covering_sources(session):
    """Raya in 3 of 3, Ødegaard in 2 of 3."""
    FILL = [f"Filler{i}" for i in range(8)]
    xi = ["Raya", "Ødegaard", "Gabriel"] + FILL
    # Same eleven with Ødegaard swapped out for the remaining midfielder.
    benched = ["Raya", "B.Fernandes", "Gabriel"] + FILL
    for slug, named in (("a", xi), ("b", xi), ("c", benched)):
        import_lineups(session, _bundle(slug, {"ARS": named}))

    # Shares are stored rounded to four places, so compare at that precision.
    probabilities = start_probabilities(session, force=True)
    assert probabilities[1] == pytest.approx(1.0, abs=1e-3)  # Raya, 3/3
    assert probabilities[2] == pytest.approx(2 / 3, abs=1e-3)  # Ødegaard, 2/3


def test_thin_coverage_yields_no_consensus(session):
    """Below the minimum source count, no number is published at all."""
    assert MIN_SOURCES_FOR_CONSENSUS > 1
    for slug in range(MIN_SOURCES_FOR_CONSENSUS - 1):
        import_lineups(
            session,
            _bundle(str(slug), {"ARS": _legal_xi()}),
        )
    assert start_probabilities(session, force=True) == {}


def test_absence_from_the_index_is_unknown_not_zero(session):
    """Everton is uncovered, so no Everton player gets a number."""
    for slug in "abc":
        import_lineups(
            session,
            _bundle(slug, {"ARS": _legal_xi()}),
        )
    probabilities = start_probabilities(session, force=True)
    assert 1 in probabilities, "a covered club must be scored"
    assert 6 not in probabilities, "an uncovered club must be absent, not zero"
    assert coverage(session).get(2) is None


def test_index_expires_once_the_season_starts(session):
    for slug in "abc":
        import_lineups(
            session,
            _bundle(slug, {"ARS": _legal_xi()}),
        )
    assert is_preseason(session) is True
    assert start_probabilities(session) != {}

    event = session.get(Event, 1)
    event.finished = True
    session.commit()

    assert is_preseason(session) is False
    assert start_probabilities(session) == {}, "predicted XIs must not outlive real minutes"


def test_reimport_replaces_rather_than_accumulates(session):
    xi = _legal_xi()
    import_lineups(session, _bundle("src", {"ARS": xi}))
    first = len(list(session.scalars(select(PredictedLineup))))
    import_lineups(session, _bundle("src", {"ARS": xi}))
    second = len(list(session.scalars(select(PredictedLineup))))
    assert first == second, "a predicted XI is a snapshot, not an accumulating log"


def _flag(session, player_id, status, chance=None):
    player = session.get(Player, player_id)
    player.status = status
    player.chance_of_playing_next_round = chance
    session.commit()


def _covered(session):
    for slug in "abc":
        import_lineups(session, _bundle(slug, {"ARS": _legal_xi()}))


def test_an_injured_player_scores_zero_however_many_sources_named_him(session):
    """Predicted XIs are published before team news lands.

    One real source had a keeper starting who was ruled out until November.
    A confirmed injury is harder evidence than a prediction written before it.
    """
    _covered(session)
    assert start_probabilities(session, force=True)[1] == pytest.approx(1.0)
    _flag(session, 1, "i", 0)
    assert start_probabilities(session, force=True)[1] == 0.0


def test_suspension_and_departure_also_zero_the_share(session):
    _covered(session)
    for status in ("s", "u", "n"):
        _flag(session, 1, status)
        assert start_probabilities(session, force=True)[1] == 0.0, status


def test_a_doubt_is_scaled_by_fpls_own_chance_not_zeroed(session):
    """"75% chance of playing" is a real number and beats guessing."""
    _covered(session)
    _flag(session, 1, "d", 75)
    assert start_probabilities(session, force=True)[1] == pytest.approx(0.75, abs=1e-3)


def test_availability_scales_rather_than_replaces_consensus(session):
    """A doubtful player only two of three sources start is worse than both."""
    xi = _legal_xi()
    benched = ["Raya", "B.Fernandes", "Gabriel"] + [f"Filler{i}" for i in range(8)]
    for slug, named in (("a", xi), ("b", xi), ("c", benched)):
        import_lineups(session, _bundle(slug, {"ARS": named}))
    _flag(session, 2, "d", 50)  # Ødegaard: 2 of 3 sources, now a 50% doubt
    assert start_probabilities(session, force=True)[2] == pytest.approx(2 / 3 * 0.5, abs=1e-3)
