"""Predicted points, model `heuristic-v1`.

Deliberately a transparent heuristic rather than a black box: every fixture's
xPts comes back with the component breakdown that produced it, so the UI can
show its work and the accuracy page can explain *why* a miss happened.

Approach, per player per fixture:
  1. expected minutes  <- weighted prior-season minutes, current-season form,
                          availability flags
  2. per-90 rates      <- weighted prior seasons, blending actuals with xG/xA
  3. fixture strength  <- opponent attack/defence strength vs. league mean,
                          plus home/away
  4. rates x minutes x fixture -> event counts -> FPL points

Known limits (state them, don't hide them):
  - Pre-season, there is no current-season data at all, so everything leans on
    last season. New signings from outside the league have no history and fall
    back to a price-based prior.
  - Bonus is estimated from historical BPS rate, not simulated against the
    actual other 21 players on the pitch.
  - Rotation and injury news beyond the official `status` flag is not modelled.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Event, Fixture, Player, PlayerGameweek, PlayerSeason, Prediction, Team
from app.services import scoring as S

log = logging.getLogger(__name__)

MODEL_VERSION = "heuristic-v1"

# Older seasons tell us less about a player than recent ones.
SEASON_DECAY = 0.55
MAX_SEASONS = 3
GAMES_PER_SEASON = 38

# How far a fixture can swing a player's underlying rates.
FIXTURE_MULTIPLIER_BOUNDS = (0.60, 1.60)


@dataclass
class PlayerProfile:
    """A player's per-90 baseline, independent of any particular fixture."""

    player_id: int
    element_type: int
    minutes_per_game: float
    goals_per90: float
    assists_per90: float
    saves_per90: float
    dc_per90: float
    bonus_per90: float
    yellow_per90: float
    sample_minutes: float
    source: str  # "history" | "current" | "prior"
    # Share of the club's penalties this player is expected to take.
    penalty_share: float = 0.0


@dataclass
class FixturePrediction:
    fixture_id: int
    event_id: int
    opponent_team_id: int
    is_home: bool
    difficulty: int
    expected_minutes: float
    expected_points: float
    components: dict = field(default_factory=dict)


# --------------------------------------------------------------------------
# Step 1 & 2: player baselines
# --------------------------------------------------------------------------


def _rate(total: float, minutes: float) -> float:
    return (total / minutes) * 90.0 if minutes > 0 else 0.0


# A player's historical goals already contain any penalties they took, so
# crediting the full penalty rate again would double-count them. FPL does not
# publish penalties *scored*, only saved and missed, so the overlap cannot be
# measured directly — it is discounted instead. A player with real history keeps
# most of the credit inside their existing rate; one priced from a prior (a new
# signing, or a promoted-club player) has none of it, so they get the full rate.
PENALTY_CREDIT_BY_SOURCE = {
    "history": 0.30,
    "current": 0.30,
    "thin": 0.65,
    "prior": 1.00,
}


def _penalty_share(player: Player) -> float:
    """How much of the club's penalty load this player is expected to take."""
    return S.PENALTY_SHARE_BY_ORDER.get(player.penalties_order or 0, 0.0)


def build_player_profile(
    player: Player,
    past_seasons: list[PlayerSeason],
    current_gameweeks: list[PlayerGameweek],
) -> PlayerProfile:
    """Blend prior seasons and current-season returns into per-90 rates."""

    weighted: dict[str, float] = {
        "minutes": 0.0,
        "goals": 0.0,
        "assists": 0.0,
        "xg": 0.0,
        "xa": 0.0,
        "saves": 0.0,
        "dc": 0.0,
        "bonus": 0.0,
        "yellow": 0.0,
        "games": 0.0,
    }
    # Defensive contribution only became a stat in 2025/26, and xG/xA in 2022/23.
    # Averaging them over seasons that never recorded them would drag the rate to
    # zero, so each gets its own minutes denominator.
    dc_minutes = 0.0
    xg_minutes = 0.0

    # Most recent season first.
    seasons = sorted(past_seasons, key=lambda s: s.season_name, reverse=True)[:MAX_SEASONS]
    for index, season in enumerate(seasons):
        w = SEASON_DECAY**index
        weighted["minutes"] += season.minutes * w
        weighted["goals"] += season.goals_scored * w
        weighted["assists"] += season.assists * w
        weighted["xg"] += season.expected_goals * w
        weighted["xa"] += season.expected_assists * w
        weighted["saves"] += season.saves * w
        weighted["bonus"] += season.bonus * w
        weighted["yellow"] += season.yellow_cards * w
        weighted["games"] += GAMES_PER_SEASON * w
        if season.defensive_contribution > 0:
            weighted["dc"] += season.defensive_contribution * w
            dc_minutes += season.minutes * w
        if season.expected_goals > 0 or season.expected_assists > 0:
            xg_minutes += season.minutes * w

    # Current season, if under way, is worth more than any past season.
    current_minutes = sum(gw.minutes for gw in current_gameweeks)
    if current_gameweeks:
        w = 1.6
        weighted["minutes"] += current_minutes * w
        weighted["goals"] += sum(gw.goals_scored for gw in current_gameweeks) * w
        weighted["assists"] += sum(gw.assists for gw in current_gameweeks) * w
        weighted["saves"] += sum(gw.saves for gw in current_gameweeks) * w
        weighted["dc"] += sum(gw.defensive_contribution for gw in current_gameweeks) * w
        weighted["bonus"] += sum(gw.bonus for gw in current_gameweeks) * w
        weighted["games"] += len(current_gameweeks) * w
        dc_minutes += current_minutes * w
        xg_minutes += current_minutes * w

    minutes = weighted["minutes"]

    if minutes < 180:
        # Too little to trust. Fall back to a price-based prior: the market is a
        # decent guess at how much a player we know nothing about will feature.
        price = player.now_cost / 10.0
        prior_minutes = S.clamp(20.0 + 50.0 * ((price - 4.0) / 6.0), 8.0, 78.0)
        goals, assists = _positional_prior(player.element_type, price)
        return PlayerProfile(
            player_id=player.id,
            element_type=player.element_type,
            minutes_per_game=prior_minutes,
            goals_per90=goals,
            assists_per90=assists,
            saves_per90=2.8 if player.element_type == S.GKP else 0.0,
            dc_per90=_positional_dc_prior(player.element_type),
            bonus_per90=0.15,
            yellow_per90=0.15,
            sample_minutes=minutes,
            source="prior" if minutes == 0 else "thin",
            penalty_share=_penalty_share(player),
        )

    # xG/xA are more stable than raw goals/assists, but raw output carries real
    # finishing signal. Split the difference.
    if xg_minutes > 0:
        goals_per90 = 0.5 * _rate(weighted["goals"], minutes) + 0.5 * _rate(
            weighted["xg"], xg_minutes
        )
        assists_per90 = 0.5 * _rate(weighted["assists"], minutes) + 0.5 * _rate(
            weighted["xa"], xg_minutes
        )
    else:
        goals_per90 = _rate(weighted["goals"], minutes)
        assists_per90 = _rate(weighted["assists"], minutes)

    return PlayerProfile(
        player_id=player.id,
        element_type=player.element_type,
        minutes_per_game=minutes / weighted["games"] if weighted["games"] else 0.0,
        goals_per90=goals_per90,
        assists_per90=assists_per90,
        saves_per90=_rate(weighted["saves"], minutes),
        dc_per90=(
            _rate(weighted["dc"], dc_minutes)
            if dc_minutes >= 180
            else _positional_dc_prior(player.element_type)
        ),
        bonus_per90=_rate(weighted["bonus"], minutes),
        yellow_per90=_rate(weighted["yellow"], minutes),
        sample_minutes=minutes,
        penalty_share=_penalty_share(player),
        source="current" if current_minutes > 0 else "history",
    )


def _positional_prior(element_type: int, price: float) -> tuple[float, float]:
    """Rough goals/90 and assists/90 for a player we have no history for."""
    premium = S.clamp((price - 4.5) / 6.0, 0.0, 1.0)
    table = {
        S.GKP: (0.0, 0.0),
        S.DEF: (0.05, 0.08),
        S.MID: (0.15, 0.15),
        S.FWD: (0.32, 0.12),
    }
    goals, assists = table.get(element_type, (0.1, 0.1))
    return goals * (0.5 + premium), assists * (0.5 + premium)


def _positional_dc_prior(element_type: int) -> float:
    return {S.GKP: 0.0, S.DEF: 7.5, S.MID: 8.0, S.FWD: 4.0}.get(element_type, 0.0)


def availability_factor(player: Player) -> float:
    """0.0 (out) .. 1.0 (fully fit), from the official status flag."""
    if player.status in {"i", "s", "u", "n"}:
        return 0.0
    if player.status == "d":
        chance = player.chance_of_playing_next_round
        return (chance / 100.0) if chance is not None else 0.5
    chance = player.chance_of_playing_next_round
    if chance is not None and chance < 100:
        return chance / 100.0
    return 1.0


# --------------------------------------------------------------------------
# Step 3: fixture context
# --------------------------------------------------------------------------


@dataclass
class LeagueAverages:
    attack: float
    defence: float
    # FPL zeroes out every team's strength rating during the off-season and only
    # populates it once results exist. When that happens we fall back to the
    # official per-fixture difficulty, which *is* published pre-season.
    strengths_available: bool = True


# Official FDR (1 easiest .. 5 hardest) mapped onto a rate multiplier. The FDR
# already accounts for home and away, so no venue adjustment is applied on top.
DIFFICULTY_MULTIPLIER = {1: 1.35, 2: 1.18, 3: 1.00, 4: 0.84, 5: 0.68}


def league_averages(teams: list[Team]) -> LeagueAverages:
    if not teams:
        return LeagueAverages(attack=1100.0, defence=1100.0, strengths_available=False)
    attack = sum(t.strength_attack_home + t.strength_attack_away for t in teams) / (2 * len(teams))
    defence = sum(t.strength_defence_home + t.strength_defence_away for t in teams) / (
        2 * len(teams)
    )
    available = attack > 0 and defence > 0
    return LeagueAverages(
        attack=attack or 1100.0, defence=defence or 1100.0, strengths_available=available
    )


def attack_multiplier(
    opponent: Team, is_home: bool, averages: LeagueAverages, difficulty: int = 3
) -> float:
    """How much easier than average it is to score in this fixture."""
    if not averages.strengths_available:
        return DIFFICULTY_MULTIPLIER.get(difficulty, 1.0)

    opp_defence = opponent.strength_defence_away if is_home else opponent.strength_defence_home
    if opp_defence <= 0:
        opp_defence = averages.defence
    multiplier = averages.defence / opp_defence
    multiplier *= S.HOME_ADVANTAGE if is_home else S.AWAY_DISADVANTAGE
    return S.clamp(multiplier, *FIXTURE_MULTIPLIER_BOUNDS)


def expected_goals_conceded(
    team: Team, opponent: Team, is_home: bool, averages: LeagueAverages, difficulty: int = 3
) -> float:
    """Goals our player's team is expected to concede in this fixture."""
    if not averages.strengths_available:
        # A harder fixture means a stronger opponent, so more goals against us.
        return max(0.2, S.LEAGUE_AVG_GOALS_PER_TEAM_PER_GAME / DIFFICULTY_MULTIPLIER.get(difficulty, 1.0))

    own_defence = team.strength_defence_home if is_home else team.strength_defence_away
    opp_attack = opponent.strength_attack_away if is_home else opponent.strength_attack_home
    own_defence = own_defence or averages.defence
    opp_attack = opp_attack or averages.attack

    xgc = S.LEAGUE_AVG_GOALS_PER_TEAM_PER_GAME
    xgc *= S.clamp(opp_attack / averages.attack, *FIXTURE_MULTIPLIER_BOUNDS)
    xgc *= S.clamp(averages.defence / own_defence, *FIXTURE_MULTIPLIER_BOUNDS)
    xgc *= S.AWAY_DISADVANTAGE if not is_home else 1.0 / S.HOME_ADVANTAGE
    return max(0.2, xgc)


# --------------------------------------------------------------------------
# Step 4: assemble points
# --------------------------------------------------------------------------


def predict_fixture(
    player: Player,
    profile: PlayerProfile,
    fixture: Fixture,
    team: Team,
    opponent: Team,
    is_home: bool,
    averages: LeagueAverages,
) -> FixturePrediction:
    position = player.element_type
    avail = availability_factor(player)
    expected_minutes = profile.minutes_per_game * avail

    # Split expected minutes into "started" vs "came off the bench", because
    # appearance and clean-sheet points hinge on reaching 60 minutes.
    p_start = S.clamp((expected_minutes - 12.0) / 73.0, 0.0, 1.0)
    p_sub_appearance = S.clamp((1.0 - p_start) * (expected_minutes / 30.0), 0.0, 0.6)
    p_60 = p_start * 0.88
    minutes_share = expected_minutes / 90.0

    difficulty = fixture.team_h_difficulty if is_home else fixture.team_a_difficulty
    att_mult = attack_multiplier(opponent, is_home, averages, difficulty)
    xgc = expected_goals_conceded(team, opponent, is_home, averages, difficulty)
    p_clean_sheet = S.poisson_pmf(0, xgc)

    x_goals = profile.goals_per90 * minutes_share * att_mult
    x_assists = profile.assists_per90 * minutes_share * att_mult

    # Set pieces. Being a club's designated penalty taker is worth real points
    # that a per-90 rate alone misses — most obviously for a player new to the
    # league who has taken none here yet. Penalties are only won while the
    # player is on the pitch, and a stronger attacking matchup wins more of them.
    x_penalty_goals = (
        S.PENALTIES_PER_TEAM_PER_MATCH
        * S.PENALTY_CONVERSION
        * profile.penalty_share
        * minutes_share
        * att_mult
        * PENALTY_CREDIT_BY_SOURCE.get(profile.source, 0.3)
    )
    x_goals += x_penalty_goals

    pts_appearance = p_start * S.APPEARANCE_60_POINTS + p_sub_appearance * S.APPEARANCE_POINTS
    pts_goals = x_goals * S.GOAL_POINTS.get(position, 4)
    pts_assists = x_assists * S.ASSIST_POINTS
    pts_clean_sheet = p_clean_sheet * p_60 * S.CLEAN_SHEET_POINTS.get(position, 0)

    pts_conceded = 0.0
    if position in (S.GKP, S.DEF):
        # -1 per 2 conceded, only while on the pitch.
        pts_conceded = (
            -(xgc * minutes_share) / S.GOALS_CONCEDED_PER_PENALTY * abs(S.GOALS_CONCEDED_PENALTY)
        )

    pts_saves = 0.0
    if position == S.GKP:
        # Save volume rises against stronger attacks, roughly with xGC.
        save_rate = profile.saves_per90 * S.clamp(xgc / S.LEAGUE_AVG_GOALS_PER_TEAM_PER_GAME, 0.7, 1.4)
        pts_saves = (save_rate * minutes_share) / S.SAVES_PER_POINT

    threshold = S.DEFENSIVE_CONTRIBUTION_THRESHOLD.get(position, 999)
    expected_dc = profile.dc_per90 * minutes_share
    p_dc = S.poisson_at_least(threshold, expected_dc) if threshold < 999 else 0.0
    pts_dc = p_dc * S.DEFENSIVE_CONTRIBUTION_POINTS

    pts_bonus = profile.bonus_per90 * minutes_share
    pts_cards = profile.yellow_per90 * minutes_share * S.YELLOW_CARD_POINTS

    total = (
        pts_appearance
        + pts_goals
        + pts_assists
        + pts_clean_sheet
        + pts_conceded
        + pts_saves
        + pts_dc
        + pts_bonus
        + pts_cards
    )
    total = max(0.0, total) if avail == 0 else total

    components = {
        "availability": round(avail, 3),
        "p_start": round(p_start, 3),
        "p_60": round(p_60, 3),
        "attack_multiplier": round(att_mult, 3),
        "expected_goals_conceded": round(xgc, 3),
        "p_clean_sheet": round(p_clean_sheet, 3),
        "x_goals": round(x_goals, 3),
        "x_penalty_goals": round(x_penalty_goals, 3),
        "penalty_share": round(profile.penalty_share, 2),
        "x_assists": round(x_assists, 3),
        "p_defensive_contribution": round(p_dc, 3),
        "profile_source": profile.source,
        "fixture_model": "team_strength" if averages.strengths_available else "official_fdr",
        "points": {
            "appearance": round(pts_appearance, 2),
            "goals": round(pts_goals, 2),
            "assists": round(pts_assists, 2),
            "clean_sheet": round(pts_clean_sheet, 2),
            "goals_conceded": round(pts_conceded, 2),
            "saves": round(pts_saves, 2),
            "defensive_contribution": round(pts_dc, 2),
            "bonus": round(pts_bonus, 2),
            "cards": round(pts_cards, 2),
        },
    }

    return FixturePrediction(
        fixture_id=fixture.id,
        event_id=fixture.event_id or 0,
        opponent_team_id=opponent.id,
        is_home=is_home,
        difficulty=fixture.team_h_difficulty if is_home else fixture.team_a_difficulty,
        expected_minutes=round(expected_minutes, 1),
        expected_points=round(total, 2),
        components=components,
    )


# --------------------------------------------------------------------------
# Engine
# --------------------------------------------------------------------------


class PredictionEngine:
    """Loads everything once, then scores players against upcoming fixtures."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.teams: dict[int, Team] = {
            t.id: t for t in session.scalars(select(Team)).all()
        }
        self.averages = league_averages(list(self.teams.values()))
        self._profiles: dict[int, PlayerProfile] = {}
        self._seasons: dict[int, list[PlayerSeason]] | None = None
        self._gameweeks: dict[int, list[PlayerGameweek]] | None = None

    def _load_history(self) -> None:
        if self._seasons is not None:
            return
        seasons: dict[int, list[PlayerSeason]] = {}
        for row in self.session.scalars(select(PlayerSeason)).all():
            seasons.setdefault(row.element_code, []).append(row)
        self._seasons = seasons

        gameweeks: dict[int, list[PlayerGameweek]] = {}
        for row in self.session.scalars(select(PlayerGameweek)).all():
            gameweeks.setdefault(row.player_id, []).append(row)
        self._gameweeks = gameweeks

    def profile(self, player: Player) -> PlayerProfile:
        if player.id not in self._profiles:
            self._load_history()
            self._profiles[player.id] = build_player_profile(
                player,
                (self._seasons or {}).get(player.code, []),
                (self._gameweeks or {}).get(player.id, []),
            )
        return self._profiles[player.id]

    def fixtures_for_events(self, event_ids: list[int]) -> dict[int, list[Fixture]]:
        """Team id -> its fixtures across the requested gameweeks."""
        rows = self.session.scalars(
            select(Fixture).where(Fixture.event_id.in_(event_ids))
        ).all()
        by_team: dict[int, list[Fixture]] = {}
        for fixture in rows:
            by_team.setdefault(fixture.team_h, []).append(fixture)
            by_team.setdefault(fixture.team_a, []).append(fixture)
        return by_team

    def predict_player(
        self, player: Player, event_ids: list[int], fixtures_by_team: dict[int, list[Fixture]]
    ) -> list[FixturePrediction]:
        team = self.teams.get(player.team_id)
        if team is None:
            return []
        profile = self.profile(player)
        out: list[FixturePrediction] = []
        for fixture in sorted(
            fixtures_by_team.get(player.team_id, []), key=lambda f: (f.event_id or 0, f.id)
        ):
            is_home = fixture.team_h == player.team_id
            opponent = self.teams.get(fixture.team_a if is_home else fixture.team_h)
            if opponent is None:
                continue
            out.append(
                predict_fixture(player, profile, fixture, team, opponent, is_home, self.averages)
            )
        return out

    def predict_players(
        self, players: list[Player], event_ids: list[int]
    ) -> dict[int, list[FixturePrediction]]:
        fixtures_by_team = self.fixtures_for_events(event_ids)
        return {p.id: self.predict_player(p, event_ids, fixtures_by_team) for p in players}


# --------------------------------------------------------------------------
# Persisting predictions (the accuracy record)
# --------------------------------------------------------------------------


def upcoming_event_ids(session: Session, horizon: int, start_event: int | None = None) -> list[int]:
    """The next `horizon` gameweeks, starting at the current/next one."""
    if start_event is None:
        current = session.scalars(
            select(Event).where(Event.is_current.is_(True)).limit(1)
        ).first()
        nxt = session.scalars(select(Event).where(Event.is_next.is_(True)).limit(1)).first()
        anchor = current or nxt
        if anchor is None:
            unfinished = session.scalars(
                select(Event).where(Event.finished.is_(False)).order_by(Event.id).limit(1)
            ).first()
            anchor = unfinished
        start_event = anchor.id if anchor else 1
    return list(range(start_event, min(start_event + horizon, 39)))


def snapshot_predictions(session: Session, event_ids: list[int] | None = None) -> int:
    """Freeze the current predictions so they can be graded after the fact.

    Existing rows for the same (model, player, fixture) are left untouched — a
    prediction published before a deadline must never be quietly rewritten.
    """
    engine = PredictionEngine(session)
    if event_ids is None:
        event_ids = upcoming_event_ids(session, horizon=5)

    players = session.scalars(select(Player)).all()
    predictions = engine.predict_players(list(players), event_ids)

    existing = {
        (pid, fid)
        for pid, fid in session.execute(
            select(Prediction.player_id, Prediction.fixture_id).where(
                Prediction.model_version == MODEL_VERSION
            )
        ).all()
    }

    written = 0
    now = datetime.now(timezone.utc)
    for player_id, fixture_predictions in predictions.items():
        for fp in fixture_predictions:
            if (player_id, fp.fixture_id) in existing:
                continue
            session.add(
                Prediction(
                    model_version=MODEL_VERSION,
                    player_id=player_id,
                    event_id=fp.event_id,
                    fixture_id=fp.fixture_id,
                    expected_minutes=fp.expected_minutes,
                    expected_points=fp.expected_points,
                    components=fp.components,
                    created_at=now,
                )
            )
            written += 1
    session.commit()
    log.info("snapshotted %s predictions for events %s", written, event_ids)
    return written
