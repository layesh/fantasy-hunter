"""Unit tests for the scoring maths and squad logic.

These use fabricated rows, so they run without the FPL API or an ingested
database.
"""

import pytest

from app.models import Fixture, Player, PlayerSeason, Team
from app.services import scoring as S
from app.services.myteam import best_starting_xi
from app.services.predictions import (
    DIFFICULTY_MULTIPLIER,
    LeagueAverages,
    attack_multiplier,
    build_player_profile,
    expected_goals_conceded,
    predict_fixture,
)


def make_team(team_id: int, strengths: int = 0) -> Team:
    return Team(
        id=team_id,
        code=team_id,
        name=f"Team {team_id}",
        short_name=f"T{team_id}",
        strength=strengths,
        strength_overall_home=strengths,
        strength_overall_away=strengths,
        strength_attack_home=strengths,
        strength_attack_away=strengths,
        strength_defence_home=strengths,
        strength_defence_away=strengths,
    )


def make_player(player_id: int, element_type: int, cost: int = 50, team_id: int = 1) -> Player:
    return Player(
        id=player_id,
        code=player_id,
        team_id=team_id,
        element_type=element_type,
        first_name="Test",
        second_name=f"Player{player_id}",
        web_name=f"P{player_id}",
        now_cost=cost,
        status="a",
    )


def make_season(code: int, season: str, **kwargs) -> PlayerSeason:
    defaults = dict(
        element_code=code,
        season_name=season,
        minutes=3000,
        starts=34,
        goals_scored=0,
        assists=0,
        clean_sheets=0,
        saves=0,
        bonus=0,
        bps=0,
        yellow_cards=0,
        red_cards=0,
        goals_conceded=0,
        defensive_contribution=0,
        expected_goals=0.0,
        expected_assists=0.0,
        expected_goals_conceded=0.0,
        start_cost=50,
        end_cost=50,
        total_points=0,
    )
    defaults.update(kwargs)
    return PlayerSeason(**defaults)


def make_fixture(fixture_id: int, home: int, away: int, h_diff: int = 3, a_diff: int = 3) -> Fixture:
    return Fixture(
        id=fixture_id,
        code=fixture_id,
        event_id=1,
        team_h=home,
        team_a=away,
        team_h_difficulty=h_diff,
        team_a_difficulty=a_diff,
    )


# --- scoring primitives ---------------------------------------------------


def test_poisson_at_least_is_a_survival_function():
    assert S.poisson_at_least(0, 2.0) == 1.0
    assert S.poisson_at_least(1, 0.0) == 0.0
    # P(X>=1) = 1 - e^-lambda
    assert S.poisson_at_least(1, 1.0) == pytest.approx(1 - 2.718281828 ** -1, rel=1e-4)
    # Monotonically decreasing in k.
    values = [S.poisson_at_least(k, 8.0) for k in range(1, 15)]
    assert values == sorted(values, reverse=True)


def test_clean_sheet_probability_falls_as_expected_goals_rise():
    assert S.poisson_pmf(0, 0.5) > S.poisson_pmf(0, 2.0)


# --- fixture model --------------------------------------------------------


def test_fixture_model_falls_back_to_official_fdr_when_strengths_are_zero():
    """FPL zeroes team strength pre-season; the model must still discriminate."""
    averages = LeagueAverages(attack=1100.0, defence=1100.0, strengths_available=False)
    opponent = make_team(2, strengths=0)

    easy = attack_multiplier(opponent, True, averages, difficulty=2)
    hard = attack_multiplier(opponent, True, averages, difficulty=5)

    assert easy == DIFFICULTY_MULTIPLIER[2]
    assert hard == DIFFICULTY_MULTIPLIER[5]
    assert easy > hard

    # A harder fixture means conceding more.
    assert expected_goals_conceded(
        make_team(1), opponent, True, averages, 5
    ) > expected_goals_conceded(make_team(1), opponent, True, averages, 2)


def test_team_strength_used_when_available():
    averages = LeagueAverages(attack=1100.0, defence=1100.0, strengths_available=True)
    weak_defence = make_team(2, strengths=1100)
    weak_defence.strength_defence_away = 800
    strong_defence = make_team(3, strengths=1100)
    strong_defence.strength_defence_away = 1400

    assert attack_multiplier(weak_defence, True, averages, 3) > attack_multiplier(
        strong_defence, True, averages, 3
    )


# --- player profiles ------------------------------------------------------


def test_defensive_contribution_ignores_seasons_that_never_tracked_it():
    """DC only exists from 2025/26. Older zeroes must not dilute the rate."""
    player = make_player(1, S.DEF)
    seasons = [
        make_season(1, "2025/26", minutes=3000, defensive_contribution=300),
        make_season(1, "2024/25", minutes=3000, defensive_contribution=0),
        make_season(1, "2023/24", minutes=3000, defensive_contribution=0),
    ]
    profile = build_player_profile(player, seasons, [])
    # 300 in 3000 minutes is 9 per 90 — not diluted toward 3.
    assert profile.dc_per90 == pytest.approx(9.0, rel=1e-6)


def test_thin_history_falls_back_to_a_price_based_prior():
    cheap = build_player_profile(make_player(1, S.MID, cost=45), [], [])
    premium = build_player_profile(make_player(2, S.MID, cost=100), [], [])

    assert cheap.source == "prior"
    assert premium.minutes_per_game > cheap.minutes_per_game
    assert premium.goals_per90 > cheap.goals_per90


def test_recent_seasons_outweigh_older_ones():
    player = make_player(1, S.FWD)
    improving = build_player_profile(
        player,
        [
            make_season(1, "2025/26", minutes=3000, goals_scored=30),
            make_season(1, "2024/25", minutes=3000, goals_scored=0),
        ],
        [],
    )
    declining = build_player_profile(
        player,
        [
            make_season(1, "2025/26", minutes=3000, goals_scored=0),
            make_season(1, "2024/25", minutes=3000, goals_scored=30),
        ],
        [],
    )
    assert improving.goals_per90 > declining.goals_per90


# --- points assembly ------------------------------------------------------


def test_injured_player_scores_nothing():
    player = make_player(1, S.MID)
    player.status = "i"
    profile = build_player_profile(
        player, [make_season(1, "2025/26", minutes=3000, goals_scored=20)], []
    )
    averages = LeagueAverages(1100.0, 1100.0, strengths_available=False)
    result = predict_fixture(
        player, profile, make_fixture(1, 1, 2), make_team(1), make_team(2), True, averages
    )
    assert result.expected_minutes == 0.0
    assert result.expected_points == 0.0


def test_defender_gets_clean_sheet_points_and_striker_does_not():
    averages = LeagueAverages(1100.0, 1100.0, strengths_available=False)
    seasons = [make_season(1, "2025/26", minutes=3000)]

    defender = make_player(1, S.DEF)
    forward = make_player(2, S.FWD)
    fixture = make_fixture(1, 1, 2, h_diff=2)

    def points(player):
        profile = build_player_profile(player, seasons, [])
        return predict_fixture(
            player, profile, fixture, make_team(1), make_team(2), True, averages
        ).components["points"]

    assert points(defender)["clean_sheet"] > 0
    assert points(forward)["clean_sheet"] == 0
    # Only keepers and defenders are punished for goals conceded.
    assert points(defender)["goals_conceded"] < 0
    assert points(forward)["goals_conceded"] == 0


def test_easier_fixture_produces_more_points():
    player = make_player(1, S.FWD)
    profile = build_player_profile(
        player, [make_season(1, "2025/26", minutes=3000, goals_scored=20)], []
    )
    averages = LeagueAverages(1100.0, 1100.0, strengths_available=False)

    easy = predict_fixture(
        player, profile, make_fixture(1, 1, 2, h_diff=2), make_team(1), make_team(2), True, averages
    )
    hard = predict_fixture(
        player, profile, make_fixture(2, 1, 2, h_diff=5), make_team(1), make_team(2), True, averages
    )
    assert easy.expected_points > hard.expected_points


# --- squad logic ----------------------------------------------------------


class FakeSquadPlayer:
    def __init__(self, player, points):
        self.player = player
        self.position = 0
        self.multiplier = 1
        self.is_captain = False
        self.is_vice_captain = False
        self.predictions = []
        self._points = points

    @property
    def expected_points(self):
        return self._points


def test_best_starting_xi_is_legal_and_maximal():
    squad = []
    # 2 GKP, 5 DEF, 5 MID, 3 FWD, with descending expected points per position.
    for element_type, count in ((S.GKP, 2), (S.DEF, 5), (S.MID, 5), (S.FWD, 3)):
        for i in range(count):
            squad.append(
                FakeSquadPlayer(make_player(len(squad) + 1, element_type), 10.0 - i)
            )

    xi, bench = best_starting_xi(squad)

    assert len(xi) == 11
    assert len(bench) == 4
    counts = {}
    for sp in xi:
        counts[sp.player.element_type] = counts.get(sp.player.element_type, 0) + 1
    assert counts[S.GKP] == 1
    assert 3 <= counts[S.DEF] <= 5
    assert 2 <= counts[S.MID] <= 5
    assert 1 <= counts[S.FWD] <= 3
    # Nobody on the bench should out-score a same-position starter.
    for benched in bench:
        starters = [s for s in xi if s.player.element_type == benched.player.element_type]
        if starters:
            assert benched.expected_points <= max(s.expected_points for s in starters)


# --- set pieces -------------------------------------------------------------


def test_penalty_share_follows_the_designated_order():
    """Only the club's takers get a share, and the primary takes most of it."""
    from app.services import scoring as S
    from app.services.predictions import _penalty_share

    class Fake:
        def __init__(self, order):
            self.penalties_order = order

    assert _penalty_share(Fake(1)) == S.PENALTY_SHARE_BY_ORDER[1]
    assert _penalty_share(Fake(2)) < _penalty_share(Fake(1))
    assert _penalty_share(Fake(None)) == 0.0
    # Shares are a split of one club's penalties, never more than all of them.
    assert sum(S.PENALTY_SHARE_BY_ORDER.values()) <= 1.0


def test_penalty_credit_is_discounted_when_history_already_contains_it():
    """A player's past goals include the penalties they took.

    FPL publishes penalties saved and missed but not scored, so the overlap
    cannot be measured — crediting the full rate on top of an established
    scorer's per-90 would double-count. A player with no history has no such
    overlap and keeps the full rate.
    """
    from app.services.predictions import PENALTY_CREDIT_BY_SOURCE

    assert PENALTY_CREDIT_BY_SOURCE["prior"] == 1.0
    assert PENALTY_CREDIT_BY_SOURCE["history"] < PENALTY_CREDIT_BY_SOURCE["prior"]
    assert PENALTY_CREDIT_BY_SOURCE["history"] == PENALTY_CREDIT_BY_SOURCE["current"]


def test_a_seasons_penalty_load_is_realistic():
    """Sanity-check the base rate: a primary taker should score a few a year."""
    from app.services import scoring as S

    per_match = (
        S.PENALTIES_PER_TEAM_PER_MATCH
        * S.PENALTY_CONVERSION
        * S.PENALTY_SHARE_BY_ORDER[1]
    )
    over_a_season = per_match * 38
    assert 2.0 < over_a_season < 6.0, over_a_season
