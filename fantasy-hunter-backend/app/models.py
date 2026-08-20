"""Persistence model.

Mirrors the official FPL API's shape closely enough that ingestion is a dumb
copy, but keeps our own derived tables (Prediction, PredictionGrade) separate so
the accuracy record is first-class from day one rather than a retrofit.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator


class UtcDateTime(TypeDecorator):
    """A datetime that is always stored and returned as timezone-aware UTC.

    SQLite has no native timezone type, so SQLAlchemy's `UtcDateTime`
    quietly returns a *naive* datetime. Serialised to JSON that becomes
    "2026-08-21T17:30:00" with no offset, and `new Date()` in a browser reads an
    offset-less timestamp as **local** time — so a deadline of 17:30 UTC showed
    as 17:30 to a reader in UTC+6 instead of 23:30. Six hours of the wrong
    answer, on the one number a manager cannot afford to get wrong.

    Attaching UTC on the way in and on the way out fixes every timestamp the API
    emits, rather than patching each serialiser.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        # A naive value from application code is UTC by convention.
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        # Rows written before this type existed are naive UTC; label them.
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class Base(DeclarativeBase):
    pass


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # FPL team id (1-20)
    code: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(64))
    short_name: Mapped[str] = mapped_column(String(8))

    strength: Mapped[int] = mapped_column(Integer, default=0)
    strength_overall_home: Mapped[int] = mapped_column(Integer, default=0)
    strength_overall_away: Mapped[int] = mapped_column(Integer, default=0)
    strength_attack_home: Mapped[int] = mapped_column(Integer, default=0)
    strength_attack_away: Mapped[int] = mapped_column(Integer, default=0)
    strength_defence_home: Mapped[int] = mapped_column(Integer, default=0)
    strength_defence_away: Mapped[int] = mapped_column(Integer, default=0)

    players: Mapped[list["Player"]] = relationship(back_populates="team")


class Event(Base):
    """A gameweek."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(32))
    deadline_time: Mapped[datetime | None] = mapped_column(UtcDateTime)
    finished: Mapped[bool] = mapped_column(Boolean, default=False)
    data_checked: Mapped[bool] = mapped_column(Boolean, default=False)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)
    is_next: Mapped[bool] = mapped_column(Boolean, default=False)
    is_previous: Mapped[bool] = mapped_column(Boolean, default=False)
    average_entry_score: Mapped[int | None] = mapped_column(Integer)
    highest_score: Mapped[int | None] = mapped_column(Integer)


class Player(Base):
    """Current snapshot of an FPL element."""

    __tablename__ = "players"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # FPL element id
    code: Mapped[int] = mapped_column(Integer, index=True)  # stable across seasons
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    element_type: Mapped[int] = mapped_column(Integer, index=True)  # 1 GKP .. 4 FWD

    first_name: Mapped[str] = mapped_column(String(64), default="")
    second_name: Mapped[str] = mapped_column(String(64), default="")
    web_name: Mapped[str] = mapped_column(String(64), index=True)

    now_cost: Mapped[int] = mapped_column(Integer, default=0)  # tenths of a million
    cost_change_start: Mapped[int] = mapped_column(Integer, default=0)
    cost_change_event: Mapped[int] = mapped_column(Integer, default=0)
    selected_by_percent: Mapped[float] = mapped_column(Float, default=0.0)

    status: Mapped[str] = mapped_column(String(4), default="a")  # a/d/i/s/u/n
    news: Mapped[str] = mapped_column(String(512), default="")
    chance_of_playing_next_round: Mapped[int | None] = mapped_column(Integer)

    total_points: Mapped[int] = mapped_column(Integer, default=0)
    event_points: Mapped[int] = mapped_column(Integer, default=0)
    points_per_game: Mapped[float] = mapped_column(Float, default=0.0)
    form: Mapped[float] = mapped_column(Float, default=0.0)
    minutes: Mapped[int] = mapped_column(Integer, default=0)
    starts: Mapped[int] = mapped_column(Integer, default=0)
    goals_scored: Mapped[int] = mapped_column(Integer, default=0)
    assists: Mapped[int] = mapped_column(Integer, default=0)
    clean_sheets: Mapped[int] = mapped_column(Integer, default=0)
    goals_conceded: Mapped[int] = mapped_column(Integer, default=0)
    saves: Mapped[int] = mapped_column(Integer, default=0)
    bonus: Mapped[int] = mapped_column(Integer, default=0)
    bps: Mapped[int] = mapped_column(Integer, default=0)
    yellow_cards: Mapped[int] = mapped_column(Integer, default=0)
    red_cards: Mapped[int] = mapped_column(Integer, default=0)
    defensive_contribution: Mapped[int] = mapped_column(Integer, default=0)

    expected_goals: Mapped[float] = mapped_column(Float, default=0.0)
    expected_assists: Mapped[float] = mapped_column(Float, default=0.0)
    expected_goal_involvements: Mapped[float] = mapped_column(Float, default=0.0)
    expected_goals_conceded: Mapped[float] = mapped_column(Float, default=0.0)

    influence: Mapped[float] = mapped_column(Float, default=0.0)
    creativity: Mapped[float] = mapped_column(Float, default=0.0)
    threat: Mapped[float] = mapped_column(Float, default=0.0)
    ict_index: Mapped[float] = mapped_column(Float, default=0.0)

    penalties_order: Mapped[int | None] = mapped_column(Integer)
    corners_and_indirect_freekicks_order: Mapped[int | None] = mapped_column(Integer)
    direct_freekicks_order: Mapped[int | None] = mapped_column(Integer)

    transfers_in_event: Mapped[int] = mapped_column(Integer, default=0)
    transfers_out_event: Mapped[int] = mapped_column(Integer, default=0)

    photo: Mapped[str] = mapped_column(String(32), default="")

    team: Mapped[Team] = relationship(back_populates="players")

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.second_name}".strip()


class PlayerSeason(Base):
    """A player's totals in a past season (from element-summary history_past).

    Keyed by element *code*, not element id, because ids are reassigned between
    seasons while codes are stable.
    """

    __tablename__ = "player_seasons"
    __table_args__ = (UniqueConstraint("element_code", "season_name", name="uq_player_season"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    element_code: Mapped[int] = mapped_column(Integer, index=True)
    season_name: Mapped[str] = mapped_column(String(16), index=True)

    start_cost: Mapped[int] = mapped_column(Integer, default=0)
    end_cost: Mapped[int] = mapped_column(Integer, default=0)
    total_points: Mapped[int] = mapped_column(Integer, default=0)
    minutes: Mapped[int] = mapped_column(Integer, default=0)
    starts: Mapped[int] = mapped_column(Integer, default=0)
    goals_scored: Mapped[int] = mapped_column(Integer, default=0)
    assists: Mapped[int] = mapped_column(Integer, default=0)
    clean_sheets: Mapped[int] = mapped_column(Integer, default=0)
    goals_conceded: Mapped[int] = mapped_column(Integer, default=0)
    saves: Mapped[int] = mapped_column(Integer, default=0)
    bonus: Mapped[int] = mapped_column(Integer, default=0)
    bps: Mapped[int] = mapped_column(Integer, default=0)
    yellow_cards: Mapped[int] = mapped_column(Integer, default=0)
    red_cards: Mapped[int] = mapped_column(Integer, default=0)
    defensive_contribution: Mapped[int] = mapped_column(Integer, default=0)
    expected_goals: Mapped[float] = mapped_column(Float, default=0.0)
    expected_assists: Mapped[float] = mapped_column(Float, default=0.0)
    expected_goals_conceded: Mapped[float] = mapped_column(Float, default=0.0)


class PlayerGameweek(Base):
    """A player's actual return in one gameweek (from element-summary history).

    Empty until the season is under way; this is what predictions get graded
    against.
    """

    __tablename__ = "player_gameweeks"
    __table_args__ = (UniqueConstraint("player_id", "fixture_id", name="uq_player_fixture"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id"), index=True)
    fixture_id: Mapped[int] = mapped_column(Integer, index=True)

    total_points: Mapped[int] = mapped_column(Integer, default=0)
    minutes: Mapped[int] = mapped_column(Integer, default=0)
    goals_scored: Mapped[int] = mapped_column(Integer, default=0)
    assists: Mapped[int] = mapped_column(Integer, default=0)
    clean_sheets: Mapped[int] = mapped_column(Integer, default=0)
    goals_conceded: Mapped[int] = mapped_column(Integer, default=0)
    saves: Mapped[int] = mapped_column(Integer, default=0)
    bonus: Mapped[int] = mapped_column(Integer, default=0)
    bps: Mapped[int] = mapped_column(Integer, default=0)
    defensive_contribution: Mapped[int] = mapped_column(Integer, default=0)
    was_home: Mapped[bool] = mapped_column(Boolean, default=False)
    opponent_team: Mapped[int | None] = mapped_column(Integer)
    value: Mapped[int] = mapped_column(Integer, default=0)


class Fixture(Base):
    __tablename__ = "fixtures"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[int] = mapped_column(Integer, index=True)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id"), index=True)

    team_h: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    team_a: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    team_h_difficulty: Mapped[int] = mapped_column(Integer, default=3)
    team_a_difficulty: Mapped[int] = mapped_column(Integer, default=3)
    team_h_score: Mapped[int | None] = mapped_column(Integer)
    team_a_score: Mapped[int | None] = mapped_column(Integer)

    kickoff_time: Mapped[datetime | None] = mapped_column(UtcDateTime)
    started: Mapped[bool] = mapped_column(Boolean, default=False)
    finished: Mapped[bool] = mapped_column(Boolean, default=False)
    minutes: Mapped[int] = mapped_column(Integer, default=0)


class Prediction(Base):
    """One model's predicted points for a player in a single fixture.

    Written *before* the deadline and never mutated. `components` keeps the
    breakdown that produced xpts so every number on the site can show its work.
    """

    __tablename__ = "predictions"
    __table_args__ = (
        UniqueConstraint("model_version", "player_id", "fixture_id", name="uq_prediction"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_version: Mapped[str] = mapped_column(String(32), index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True)
    fixture_id: Mapped[int] = mapped_column(Integer, index=True)

    expected_minutes: Mapped[float] = mapped_column(Float, default=0.0)
    expected_points: Mapped[float] = mapped_column(Float, default=0.0)
    components: Mapped[dict] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        UtcDateTime, server_default=func.now()
    )


class PredictionGrade(Base):
    """Prediction vs. what actually happened. Populated once a gameweek is final."""

    __tablename__ = "prediction_grades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    prediction_id: Mapped[int] = mapped_column(ForeignKey("predictions.id"), unique=True)
    actual_points: Mapped[int] = mapped_column(Integer, default=0)
    actual_minutes: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[float] = mapped_column(Float, default=0.0)  # predicted - actual
    graded_at: Mapped[datetime] = mapped_column(
        UtcDateTime, server_default=func.now()
    )


class IngestRun(Base):
    """Audit trail for ingestion, so a stale dashboard is diagnosable."""

    __tablename__ = "ingest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    started_at: Mapped[datetime] = mapped_column(UtcDateTime, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    rows: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[str] = mapped_column(String(512), default="")


class LineupSource(Base):
    """One publisher of pre-season predicted XIs.

    Pre-season only. Before a ball is kicked there is no minutes data, so the
    consensus of published predicted line-ups is the best available proxy for
    who actually starts. Once real minutes exist this whole table is dead
    weight and must not influence anything — see services/lineups.py.
    """

    __tablename__ = "lineup_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(96), default="")
    url: Mapped[str] = mapped_column(String(512), default="")
    event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id"), index=True)
    fetched_at: Mapped[datetime] = mapped_column(UtcDateTime, server_default=func.now())

    # Quality gate. A source naming players who are no longer in the club's FPL
    # squad is stale, and stale predicted XIs are worse than none — they look
    # authoritative while being wrong. Scored on import, not hand-curated.
    names_total: Mapped[int] = mapped_column(Integer, default=0)
    names_matched: Mapped[int] = mapped_column(Integer, default=0)
    match_rate: Mapped[float] = mapped_column(Float, default=0.0)
    trusted: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str] = mapped_column(String(512), default="")


class PredictedLineup(Base):
    """One named player in one source's predicted XI for one club."""

    __tablename__ = "predicted_lineups"
    __table_args__ = (
        UniqueConstraint("source_id", "team_id", "raw_name", name="uq_lineup_entry"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("lineup_sources.id"), index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id"), index=True)

    raw_name: Mapped[str] = mapped_column(String(96))
    player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"), index=True)
    # Why a name failed to resolve, so the gate is auditable rather than a black box.
    resolution: Mapped[str] = mapped_column(String(16), default="unmatched")


class ChipWindow(Base):
    """A single chip instance and the gameweeks it may legally be played in.

    Ingested from bootstrap-static's `chips`, never hardcoded. From 2025/26 FPL
    issues two sets — one expiring at GW19, one for GW20-38 — so "wildcard" is
    two separate instances with different windows, and wildcard/free hit are
    barred from GW1 entirely. Guessing these rules produces plans a manager
    cannot execute.
    """

    __tablename__ = "chip_windows"
    __table_args__ = (
        UniqueConstraint("name", "start_event", name="uq_chip_window"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(24), index=True)  # our canonical name
    fpl_name: Mapped[str] = mapped_column(String(24), default="")  # e.g. "3xc", "bboost"
    chip_type: Mapped[str] = mapped_column(String(16), default="")  # transfer | team
    start_event: Mapped[int] = mapped_column(Integer)
    stop_event: Mapped[int] = mapped_column(Integer)
    half: Mapped[int] = mapped_column(Integer, default=1)  # 1 = GW1-19, 2 = GW20-38

    @property
    def key(self) -> str:
        """Stable identifier for one playable chip instance."""
        return f"{self.name}:{self.half}"

    def covers(self, event_id: int) -> bool:
        return self.start_event <= event_id <= self.stop_event


class ChipTimingPrior(Base):
    """How often a chip is played in a given gameweek, before we have our own data.

    The first season has no history of its own, so this is seeded from published
    community and expert sources with the basis recorded against every row. As
    real seasons accumulate, observed rows are added and outweigh the seed —
    which is the mechanism by which the model matures rather than staying a
    static opinion.
    """

    __tablename__ = "chip_timing_priors"
    __table_args__ = (
        UniqueConstraint("season", "chip", "event_id", "kind", name="uq_chip_prior"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    season: Mapped[str] = mapped_column(String(16), index=True)
    chip: Mapped[str] = mapped_column(String(24), index=True)
    event_id: Mapped[int] = mapped_column(Integer, index=True)

    # planned  = pre-season survey of intent
    # expert   = published recommendation
    # observed = what actually happened (authoritative once present)
    kind: Mapped[str] = mapped_column(String(16), default="expert")
    weight: Mapped[float] = mapped_column(Float, default=0.0)  # share, 0-1
    basis: Mapped[str] = mapped_column(String(256), default="")
    source: Mapped[str] = mapped_column(String(256), default="")


class GameweekOutlook(Base):
    """Prior likelihood that a gameweek turns out to be a double or a blank.

    Doubles and blanks are created by cup progression and postponements, so
    they do not exist in the fixture list at the start of the season — but
    their *distribution* across the calendar is stable enough to plan around.
    Replaced by fact as soon as fixtures are actually rescheduled.
    """

    __tablename__ = "gameweek_outlooks"
    __table_args__ = (UniqueConstraint("season", "event_id", name="uq_gw_outlook"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    season: Mapped[str] = mapped_column(String(16), index=True)
    event_id: Mapped[int] = mapped_column(Integer, index=True)
    double_likelihood: Mapped[float] = mapped_column(Float, default=0.0)
    blank_likelihood: Mapped[float] = mapped_column(Float, default=0.0)
    # True once the real fixture list confirms it, rather than being a prior.
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str] = mapped_column(String(256), default="")


class TeamSeasonDefence(Base):
    """A club's defensive record for one completed season.

    Cannot be derived from anything we already hold: FPL's `history_past` gives
    a player's season totals but never the club he played for, so a keeper's 19
    clean sheets cannot be attributed to anyone. Sourced externally, and kept
    per season rather than pre-aggregated so a caller can weight recency itself.

    Clubs are keyed by abbreviation, not by a foreign key to `teams` — a
    relegated club has a record but no current row, and a promoted club has a
    current row but no record.
    """

    __tablename__ = "team_season_defence"
    __table_args__ = (
        UniqueConstraint("season_name", "team_abbr", name="uq_team_season_defence"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    season_name: Mapped[str] = mapped_column(String(16), index=True)
    team_abbr: Mapped[str] = mapped_column(String(8), index=True)
    team_name: Mapped[str] = mapped_column(String(64), default="")

    matches: Mapped[int] = mapped_column(Integer, default=0)
    clean_sheets: Mapped[int] = mapped_column(Integer, default=0)
    goals_conceded: Mapped[int] = mapped_column(Integer, default=0)
    expected_goals_conceded: Mapped[float] = mapped_column(Float, default=0.0)
    source: Mapped[str] = mapped_column(String(256), default="")
