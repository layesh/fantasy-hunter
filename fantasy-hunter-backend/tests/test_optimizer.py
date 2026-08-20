"""Optimiser tests against a small synthetic league in an in-memory database.

Using a fabricated league rather than real data keeps these fast and makes the
expected answer knowable by hand.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Event, Fixture, Player, PlayerSeason, Team
from app.services import scoring as S
from app.services.optimizer import (
    MAX_PER_CLUB,
    SQUAD_SHAPE,
    SQUAD_SIZE,
    XI_BOUNDS,
    XI_SIZE,
    InfeasibleError,
    optimise_squad,
    plan_transfers,
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
    """Eight clubs, each with a full complement, playing each other in 3 GWs."""
    for team_id in range(1, 9):
        session.add(
            Team(
                id=team_id,
                code=team_id,
                name=f"Club {team_id}",
                short_name=f"C{team_id}",
                strength=0,
                strength_attack_home=0,
                strength_attack_away=0,
                strength_defence_home=0,
                strength_defence_away=0,
            )
        )

    for event_id in (1, 2, 3):
        session.add(Event(id=event_id, name=f"Gameweek {event_id}", finished=False))

    fixture_id = 1
    for event_id in (1, 2, 3):
        for home in range(1, 9, 2):
            away = home + 1
            if event_id % 2 == 0:
                home, away = away, home
            session.add(
                Fixture(
                    id=fixture_id,
                    code=fixture_id,
                    event_id=event_id,
                    team_h=home,
                    team_a=away,
                    team_h_difficulty=3,
                    team_a_difficulty=3,
                )
            )
            fixture_id += 1

    # Each club gets 2 keepers, 5 defenders, 5 midfielders and 3 forwards.
    # Price rises with quality so budget genuinely bites.
    player_id = 1
    for team_id in range(1, 9):
        for element_type, count in ((S.GKP, 2), (S.DEF, 5), (S.MID, 5), (S.FWD, 3)):
            for rank in range(count):
                quality = (9 - team_id) * 3 + (count - rank)
                session.add(
                    Player(
                        id=player_id,
                        code=player_id,
                        team_id=team_id,
                        element_type=element_type,
                        first_name="P",
                        second_name=str(player_id),
                        web_name=f"P{player_id}",
                        now_cost=40 + quality * 2,
                        status="a",
                    )
                )
                # Give everyone real history so profiles are not price priors.
                session.add(
                    PlayerSeason(
                        element_code=player_id,
                        season_name="2025/26",
                        minutes=3000,
                        starts=34,
                        goals_scored=quality // 2,
                        assists=quality // 3,
                        clean_sheets=8,
                        saves=60 if element_type == S.GKP else 0,
                        bonus=quality,
                        defensive_contribution=300,
                    )
                )
                player_id += 1
    session.commit()


def _positions(session, picks):
    lookup = {p.id: p for p in session.query(Player).all()}
    counts: dict[int, int] = {}
    for pick in picks:
        element_type = lookup[pick.player_id].element_type
        counts[element_type] = counts.get(element_type, 0) + 1
    return counts


# --- squad builder --------------------------------------------------------


def test_optimised_squad_is_legal(session):
    result = optimise_squad(session, [1, 2, 3], budget=1000)

    assert len(result.squad) == SQUAD_SIZE
    assert _positions(session, result.squad) == SQUAD_SHAPE

    lookup = {p.id: p for p in session.query(Player).all()}
    clubs: dict[int, int] = {}
    for pick in result.squad:
        team_id = lookup[pick.player_id].team_id
        clubs[team_id] = clubs.get(team_id, 0) + 1
    assert max(clubs.values()) <= MAX_PER_CLUB

    assert sum(pick.cost for pick in result.squad) <= 1000


def test_every_gameweek_gets_a_legal_starting_eleven(session):
    result = optimise_squad(session, [1, 2, 3], budget=1000)

    assert len(result.gameweeks) == 3
    for plan in result.gameweeks:
        assert len(plan.starting_xi) == XI_SIZE
        assert len(plan.bench) == SQUAD_SIZE - XI_SIZE
        counts = _positions(session, plan.starting_xi)
        for element_type, (low, high) in XI_BOUNDS.items():
            assert low <= counts.get(element_type, 0) <= high
        assert plan.captain is not None
        assert plan.captain.player_id in {p.player_id for p in plan.starting_xi}


def test_a_bigger_budget_never_scores_less(session):
    # Both budgets must be affordable: in this league the cheapest legal fifteen
    # already costs well over 80.0m once the three-per-club rule bites.
    lean = optimise_squad(session, [1, 2], budget=950)
    rich = optimise_squad(session, [1, 2], budget=1200)
    assert rich.expected_points >= lean.expected_points
    assert sum(p.cost for p in lean.squad) <= 950


def test_locked_players_are_forced_in_and_excluded_players_stay_out(session):
    baseline = optimise_squad(session, [1, 2], budget=1000)
    chosen = {pick.player_id for pick in baseline.squad}

    outsider = next(p.id for p in session.query(Player).all() if p.id not in chosen)
    locked = optimise_squad(session, [1, 2], budget=1000, locked_in=[outsider])
    assert outsider in {pick.player_id for pick in locked.squad}

    banned = next(iter(chosen))
    without = optimise_squad(session, [1, 2], budget=1000, excluded=[banned])
    assert banned not in {pick.player_id for pick in without.squad}


def test_impossible_budget_is_rejected_rather_than_fudged(session):
    with pytest.raises(InfeasibleError):
        optimise_squad(session, [1, 2], budget=100)  # 10.0m for fifteen players


# --- transfer planner -----------------------------------------------------


def _starting_squad(session) -> list[int]:
    """A legal but deliberately cheap squad, so the planner has upgrades to find."""
    squad: list[int] = []
    clubs: dict[int, int] = {}
    for element_type, count in SQUAD_SHAPE.items():
        taken = 0
        candidates = (
            session.query(Player)
            .filter(Player.element_type == element_type)
            .order_by(Player.now_cost)
            .all()
        )
        for player in candidates:
            if clubs.get(player.team_id, 0) >= MAX_PER_CLUB:
                continue
            squad.append(player.id)
            clubs[player.team_id] = clubs.get(player.team_id, 0) + 1
            taken += 1
            if taken == count:
                break
    return squad


def test_plan_keeps_the_squad_legal_every_week(session):
    squad = _starting_squad(session)
    result = plan_transfers(session, [1, 2, 3], squad, bank=200, free_transfers=1, time_limit=20)

    for plan in result.gameweeks:
        assert len(plan.starting_xi) == XI_SIZE
        assert len(plan.starting_xi) + len(plan.bench) == SQUAD_SIZE
        assert len(plan.transfers_in) == len(plan.transfers_out)
        assert plan.bank >= 0


def test_free_transfers_accumulate_but_never_exceed_the_cap(session):
    squad = _starting_squad(session)
    # No money to spend means no worthwhile transfers, so free transfers bank.
    result = plan_transfers(session, [1, 2, 3], squad, bank=0, free_transfers=1, time_limit=20)

    for plan in result.gameweeks:
        assert 0 <= plan.free_transfers_available <= 5


def test_transfers_beyond_the_free_allowance_are_charged(session):
    squad = _starting_squad(session)
    result = plan_transfers(session, [1, 2], squad, bank=600, free_transfers=1, time_limit=20)

    for plan in result.gameweeks:
        # A hit is four points, and only applies past the free allowance.
        assert plan.points_cost == pytest.approx(plan.hits * 4.0)
    assert result.points_spent_on_hits == pytest.approx(
        sum(plan.points_cost for plan in result.gameweeks)
    )


def test_a_wildcard_buys_unlimited_transfers_without_a_hit(session):
    squad = _starting_squad(session)
    result = plan_transfers(
        session,
        [1, 2, 3],
        squad,
        bank=600,
        free_transfers=1,
        available_chips=["wildcard"],
        time_limit=25,
    )

    wildcard_weeks = [plan for plan in result.gameweeks if plan.chip == "wildcard"]
    assert len(wildcard_weeks) <= 1
    for plan in wildcard_weeks:
        assert plan.hits == 0
        # A wildcard must not manufacture free transfers out of thin air.
        assert plan.free_transfers_available <= 5


def test_each_chip_is_used_at_most_once_and_one_per_gameweek(session):
    squad = _starting_squad(session)
    result = plan_transfers(
        session,
        [1, 2, 3],
        squad,
        bank=600,
        free_transfers=1,
        available_chips=["wildcard", "bench_boost", "triple_captain", "free_hit"],
        time_limit=25,
    )

    used = [plan.chip for plan in result.gameweeks if plan.chip]
    assert len(used) == len(set(used)), "a chip was played twice"


def test_chips_not_offered_are_never_played(session):
    squad = _starting_squad(session)
    result = plan_transfers(
        session,
        [1, 2, 3],
        squad,
        bank=600,
        free_transfers=1,
        available_chips=["bench_boost"],
        time_limit=20,
    )
    assert {plan.chip for plan in result.gameweeks} <= {None, "bench_boost"}


def test_wrong_sized_squad_is_rejected(session):
    with pytest.raises(InfeasibleError):
        plan_transfers(session, [1, 2], [1, 2, 3], bank=0, free_transfers=1)
