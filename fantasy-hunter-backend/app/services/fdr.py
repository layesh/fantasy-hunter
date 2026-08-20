"""Fixture ticker / difficulty analyser.

Alongside the official 1-5 FDR (which is coarse and rarely updated), we publish
our own continuous rating derived from the same team-strength numbers the
prediction model uses, so the ticker and the predictions can never disagree.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Fixture, Team
from app.services import scoring as S
from app.services.predictions import DIFFICULTY_MULTIPLIER, LeagueAverages, league_averages


@dataclass
class TickerFixture:
    event_id: int
    fixture_id: int
    opponent_id: int
    opponent_short_name: str
    is_home: bool
    official_difficulty: int
    attack_rating: float  # 1 (nightmare) .. 5 (dream) for scoring
    defence_rating: float  # 1 .. 5 for keeping clean sheets
    kickoff_time: str | None


@dataclass
class TickerRow:
    team_id: int
    team_name: str
    team_short_name: str
    fixtures: dict[int, list[TickerFixture]]
    attack_score: float
    defence_score: float
    fixture_count: int


def _to_five_point(multiplier: float) -> float:
    """Map a rate multiplier (0.6 hard .. 1.6 easy) onto a 1-5 scale."""
    return round(S.clamp(1.0 + (multiplier - 0.60) * 4.0, 1.0, 5.0), 2)


def _attack_multiplier(
    opponent: Team, is_home: bool, averages: LeagueAverages, difficulty: int
) -> float:
    if not averages.strengths_available:
        return DIFFICULTY_MULTIPLIER.get(difficulty, 1.0)
    opp_defence = opponent.strength_defence_away if is_home else opponent.strength_defence_home
    opp_defence = opp_defence or averages.defence
    m = (averages.defence / opp_defence) * (S.HOME_ADVANTAGE if is_home else S.AWAY_DISADVANTAGE)
    return S.clamp(m, 0.60, 1.60)


def _defence_multiplier(
    opponent: Team, is_home: bool, averages: LeagueAverages, difficulty: int
) -> float:
    """Higher is better: how likely a clean sheet is against this opponent."""
    if not averages.strengths_available:
        return DIFFICULTY_MULTIPLIER.get(difficulty, 1.0)
    opp_attack = opponent.strength_attack_away if is_home else opponent.strength_attack_home
    opp_attack = opp_attack or averages.attack
    m = (averages.attack / opp_attack) * (S.HOME_ADVANTAGE if is_home else S.AWAY_DISADVANTAGE)
    return S.clamp(m, 0.60, 1.60)


def build_ticker(session: Session, event_ids: list[int]) -> list[TickerRow]:
    teams = {t.id: t for t in session.scalars(select(Team)).all()}
    averages = league_averages(list(teams.values()))
    fixtures = session.scalars(
        select(Fixture).where(Fixture.event_id.in_(event_ids)).order_by(Fixture.event_id, Fixture.id)
    ).all()

    rows: dict[int, TickerRow] = {
        team_id: TickerRow(
            team_id=team_id,
            team_name=team.name,
            team_short_name=team.short_name,
            fixtures={event_id: [] for event_id in event_ids},
            attack_score=0.0,
            defence_score=0.0,
            fixture_count=0,
        )
        for team_id, team in teams.items()
    }

    for fixture in fixtures:
        if fixture.event_id is None:
            continue
        for team_id, is_home in ((fixture.team_h, True), (fixture.team_a, False)):
            opponent = teams.get(fixture.team_a if is_home else fixture.team_h)
            row = rows.get(team_id)
            if opponent is None or row is None:
                continue
            difficulty = fixture.team_h_difficulty if is_home else fixture.team_a_difficulty
            attack = _to_five_point(_attack_multiplier(opponent, is_home, averages, difficulty))
            defence = _to_five_point(_defence_multiplier(opponent, is_home, averages, difficulty))
            row.fixtures.setdefault(fixture.event_id, []).append(
                TickerFixture(
                    event_id=fixture.event_id,
                    fixture_id=fixture.id,
                    opponent_id=opponent.id,
                    opponent_short_name=opponent.short_name,
                    is_home=is_home,
                    official_difficulty=(
                        fixture.team_h_difficulty if is_home else fixture.team_a_difficulty
                    ),
                    attack_rating=attack,
                    defence_rating=defence,
                    kickoff_time=(
                        fixture.kickoff_time.isoformat() if fixture.kickoff_time else None
                    ),
                )
            )
            row.attack_score += attack
            row.defence_score += defence
            row.fixture_count += 1

    # A blank gameweek is a bad run of fixtures; a double is a good one. Summing
    # rather than averaging captures that, which is the whole point of a ticker.
    return sorted(rows.values(), key=lambda r: r.attack_score, reverse=True)
