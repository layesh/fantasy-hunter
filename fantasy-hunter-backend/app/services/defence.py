"""Club defensive records across completed seasons.

Answers "who actually keeps clean sheets?", which the fixture ticker does not:
the ticker is forward-looking difficulty, this is backward-looking record.

Recency-weighted rather than a flat average — a defence from two seasons ago
tells you less than last season's, particularly after a managerial change. The
weights are stated rather than buried so a reader can judge them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import Team, TeamSeasonDefence

# Most recent season first. A club with only one season on record is scored on
# that season alone rather than being penalised for the missing one.
SEASON_WEIGHTS = {"2025/26": 0.65, "2024/25": 0.35}


@dataclass
class ClubDefence:
    team_id: int | None
    abbr: str
    name: str
    seasons: list[dict] = field(default_factory=list)
    clean_sheets_per_38: float | None = None
    goals_conceded_per_game: float | None = None
    expected_goals_conceded_per_game: float | None = None
    seasons_on_record: int = 0

    @property
    def known(self) -> bool:
        return self.seasons_on_record > 0


def import_defence(session: Session, payload: dict) -> int:
    """Replace the stored records with a fetched bundle."""
    source = payload.get("source", "")
    session.execute(delete(TeamSeasonDefence))
    rows = 0
    for season in payload.get("seasons", []):
        for club in season.get("clubs", []):
            session.add(
                TeamSeasonDefence(
                    season_name=season["season_name"],
                    team_abbr=club["abbr"],
                    team_name=club.get("name", ""),
                    matches=int(club.get("matches", 38)),
                    clean_sheets=int(club.get("clean_sheets", 0)),
                    goals_conceded=int(club.get("goals_conceded", 0)),
                    expected_goals_conceded=float(club.get("expected_goals_conceded", 0.0)),
                    source=source,
                )
            )
            rows += 1
    session.commit()
    return rows


def club_defence(session: Session, *, current_only: bool = True) -> list[ClubDefence]:
    """Recency-weighted defensive record per club, best first.

    `current_only` restricts the result to clubs in this season's Premier
    League — including the promoted ones, which correctly come back with no
    record rather than being silently omitted.
    """
    teams = {t.short_name: t for t in session.scalars(select(Team))}
    by_abbr: dict[str, list[TeamSeasonDefence]] = {}
    for row in session.scalars(select(TeamSeasonDefence)):
        by_abbr.setdefault(row.team_abbr, []).append(row)

    abbrs = set(teams) if current_only else set(teams) | set(by_abbr)
    results: list[ClubDefence] = []

    for abbr in abbrs:
        team = teams.get(abbr)
        rows = by_abbr.get(abbr, [])
        entry = ClubDefence(
            team_id=team.id if team else None,
            abbr=abbr,
            name=team.name if team else (rows[0].team_name if rows else abbr),
            seasons=[
                {
                    "season": r.season_name,
                    "matches": r.matches,
                    "clean_sheets": r.clean_sheets,
                    "goals_conceded": r.goals_conceded,
                    "expected_goals_conceded": round(r.expected_goals_conceded, 2),
                }
                for r in sorted(rows, key=lambda r: r.season_name, reverse=True)
            ],
            seasons_on_record=len(rows),
        )

        # Renormalise over the seasons we actually have, so a club with one
        # season is not dragged toward zero by a missing one.
        weighted = [(r, SEASON_WEIGHTS.get(r.season_name, 0.0)) for r in rows]
        total = sum(w for _, w in weighted)
        if total > 0:
            entry.clean_sheets_per_38 = round(
                sum((r.clean_sheets / r.matches) * 38 * w for r, w in weighted) / total, 1
            )
            entry.goals_conceded_per_game = round(
                sum((r.goals_conceded / r.matches) * w for r, w in weighted) / total, 2
            )
            entry.expected_goals_conceded_per_game = round(
                sum((r.expected_goals_conceded / r.matches) * w for r, w in weighted) / total, 2
            )
        results.append(entry)

    # Known clubs first, best clean-sheet rate at the top; unknowns last.
    results.sort(key=lambda c: (-(c.clean_sheets_per_38 or -1), c.abbr))
    return results
