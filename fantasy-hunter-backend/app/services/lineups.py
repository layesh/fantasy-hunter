"""Pre-season predicted-XI consensus index.

Before the season starts there is no minutes data, so the model has nothing to
say about who actually plays — it falls back to a price-based prior, which is
how a 4.0m defender nobody has heard of ends up in an "optimal" squad. The one
signal that does exist pre-season is that a dozen sites publish predicted
line-ups. Agreement between independent sources is a usable probability.

Three things keep this honest:

1. **It expires.** The index is only consulted before the first gameweek
   finishes. After that, actual starts are real evidence and this is noise.
2. **Sources are scored, not trusted.** A source naming players who are not in
   the club's current FPL squad is stale, and stale predicted XIs are worse
   than none — they look authoritative while being wrong. Match rate against
   the live squad is computed on import and low scorers are dropped.
3. **Ambiguity resolves to "unknown", never to a guess.** If "Sangare" could be
   two players, neither is credited.
"""

from __future__ import annotations

import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import Event, LineupSource, Player, PredictedLineup, Team

# A source must name this fraction of a club's XI correctly for that club's
# entry to count. Eleven names with three unrecognised is a transfer or two
# behind; six unrecognised is a different season.
MIN_TEAM_MATCH_RATE = 0.70
# And this fraction across all clubs for the source itself to be trusted.
MIN_SOURCE_MATCH_RATE = 0.75

XI_SIZE = 11
# Below this many covering sources a consensus number is not meaningful.
MIN_SOURCES_FOR_CONSENSUS = 3


def normalise(name: str) -> str:
    """Fold accents, case and punctuation so 'Ødegaard' == 'Odegaard'."""
    text = name.strip().lower()
    text = text.replace("ø", "o").replace("ß", "ss").replace("đ", "d").replace("ł", "l")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    for ch in ".'`’-_":
        text = text.replace(ch, " ")
    return " ".join(text.split())


def _keys(player: Player) -> set[str]:
    """Every name form a source might plausibly use for this player."""
    first = normalise(player.first_name)
    second = normalise(player.second_name)
    web = normalise(player.web_name)
    keys = {web, second, f"{first} {second}".strip()}
    if second:
        # Sources often use only the final surname token: "Strand Larsen" -> "Larsen".
        keys.add(second.split()[-1])
    if web:
        keys.add(web.split()[-1])
    # "B.Fernandes" normalises to "b fernandes"; also index the bare surname.
    return {k for k in keys if k}


@dataclass
class SourceReport:
    slug: str
    name: str
    matched: int = 0
    total: int = 0
    teams_kept: int = 0
    teams_dropped: list[str] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)
    trusted: bool = False

    @property
    def rate(self) -> float:
        return self.matched / self.total if self.total else 0.0


def _resolve(raw: str, index: dict[str, set[int]]) -> tuple[int | None, str]:
    """Resolve one printed name against one club's squad.

    Returns (player_id, reason). Ambiguity is never guessed through.
    """
    key = normalise(raw)
    if not key:
        return None, "empty"

    hit = index.get(key)
    if hit and len(hit) == 1:
        return next(iter(hit)), "exact"
    if hit:
        return None, "ambiguous"

    # Fall back to the last token: "Jay da Silva" -> "silva".
    tail = key.split()[-1]
    hit = index.get(tail)
    if hit and len(hit) == 1:
        return next(iter(hit)), "surname"
    if hit:
        return None, "ambiguous"

    # Finally, a containment match, but only if exactly one candidate matches.
    candidates = {
        pid
        for name, pids in index.items()
        if (key in name or name in key) and len(name) > 3
        for pid in pids
    }
    if len(candidates) == 1:
        return next(iter(candidates)), "partial"
    return None, "ambiguous" if candidates else "unmatched"


def _squad_index(session: Session, team_id: int) -> dict[str, set[int]]:
    index: dict[str, set[int]] = defaultdict(set)
    for player in session.scalars(select(Player).where(Player.team_id == team_id)):
        for key in _keys(player):
            index[key].add(player.id)
    return index


def import_lineups(session: Session, payload: dict) -> list[SourceReport]:
    """Load a predicted-lineup bundle, scoring every source as it goes."""
    event_id = payload.get("event")
    teams = {t.short_name: t for t in session.scalars(select(Team))}
    indexes = {t.id: _squad_index(session, t.id) for t in teams.values()}

    reports: list[SourceReport] = []
    for raw_source in payload.get("sources", []):
        slug = raw_source["slug"]
        report = SourceReport(slug=slug, name=raw_source.get("name", slug))

        source = session.scalar(select(LineupSource).where(LineupSource.slug == slug))
        if source is None:
            source = LineupSource(slug=slug)
            session.add(source)
            session.flush()
        source.name = raw_source.get("name", slug)
        source.url = raw_source.get("url", "")
        source.note = raw_source.get("note", "")
        source.event_id = event_id
        source.fetched_at = datetime.now(timezone.utc)

        # Re-importing a source replaces it wholesale; a predicted XI is a
        # snapshot, not an accumulating log.
        session.execute(delete(PredictedLineup).where(PredictedLineup.source_id == source.id))

        for short_name, names in raw_source.get("lineups", {}).items():
            team = teams.get(short_name)
            if team is None:
                report.teams_dropped.append(f"{short_name} (unknown club)")
                continue

            resolved: list[tuple[str, int | None, str]] = []
            for raw_name in names:
                pid, reason = _resolve(raw_name, indexes[team.id])
                resolved.append((raw_name, pid, reason))

            matched = sum(1 for _, pid, _ in resolved if pid is not None)
            report.total += len(resolved)
            report.matched += matched
            report.unmatched.extend(
                f"{short_name}:{raw}" for raw, pid, _ in resolved if pid is None
            )

            # An XI that is not eleven names, or that is mostly unrecognised,
            # is dropped for this club only — the source may be fine elsewhere.
            rate = matched / len(resolved) if resolved else 0.0
            if len(names) != XI_SIZE or rate < MIN_TEAM_MATCH_RATE:
                report.teams_dropped.append(f"{short_name} ({matched}/{len(names)})")
                continue

            report.teams_kept += 1
            for raw_name, pid, reason in resolved:
                session.add(
                    PredictedLineup(
                        source_id=source.id,
                        team_id=team.id,
                        event_id=event_id,
                        raw_name=raw_name,
                        player_id=pid,
                        resolution=reason if pid else "unmatched",
                    )
                )

        source.names_total = report.total
        source.names_matched = report.matched
        source.match_rate = report.rate
        source.trusted = report.rate >= MIN_SOURCE_MATCH_RATE and report.teams_kept > 0
        report.trusted = source.trusted
        reports.append(report)

    session.commit()
    return reports


def is_preseason(session: Session) -> bool:
    """True until the first gameweek has finished.

    The index is a stand-in for evidence we do not have yet. The moment real
    minutes exist it stops being the best available answer and starts being a
    stale one, so every consumer gates on this.
    """
    first = session.get(Event, 1)
    return first is None or not bool(first.finished)


def _availability(player: Player) -> float:
    """Scale a consensus share by what FPL says about the player right now.

    Predicted XIs are published before team news lands, so a source can name a
    player who has since been ruled out — one had a keeper starting who is out
    until November. A confirmed injury is harder evidence than a prediction
    written before it, so the flag overrides the consensus rather than
    averaging with it.
    """
    if player.status in {"i", "s", "u", "n"}:
        return 0.0
    if player.status == "d":
        # "75% chance of playing" is FPL's own number; trust it over a guess.
        chance = player.chance_of_playing_next_round
        return (chance / 100.0) if chance is not None else 0.75
    return 1.0


def start_probabilities(session: Session, *, force: bool = False) -> dict[int, float]:
    """Fraction of trusted sources naming each player in their club's XI.

    Scaled by current availability, so a player ruled out after the predicted
    XIs were published drops to zero rather than carrying a stale share.

    Only clubs covered by at least MIN_SOURCES_FOR_CONSENSUS trusted sources
    appear. A player absent from the result is *unknown*, not a non-starter —
    callers must distinguish the two.
    """
    if not force and not is_preseason(session):
        return {}

    trusted = {
        s.id for s in session.scalars(select(LineupSource).where(LineupSource.trusted.is_(True)))
    }
    if not trusted:
        return {}

    # Sources covering each club, and how often each player was named.
    covering: dict[int, set[int]] = defaultdict(set)
    named: dict[int, set[int]] = defaultdict(set)
    for row in session.scalars(select(PredictedLineup)):
        if row.source_id not in trusted:
            continue
        covering[row.team_id].add(row.source_id)
        if row.player_id is not None:
            named[row.player_id].add(row.source_id)

    result: dict[int, float] = {}
    for player in session.scalars(select(Player)):
        sources = covering.get(player.team_id)
        if not sources or len(sources) < MIN_SOURCES_FOR_CONSENSUS:
            continue
        share = len(named.get(player.id, ())) / len(sources)
        result[player.id] = round(share * _availability(player), 4)
    return result


def coverage(session: Session) -> dict[int, int]:
    """Trusted sources per club, so callers can tell 0.0 from 'no data'."""
    trusted = {
        s.id for s in session.scalars(select(LineupSource).where(LineupSource.trusted.is_(True)))
    }
    covering: dict[int, set[int]] = defaultdict(set)
    for row in session.scalars(select(PredictedLineup)):
        if row.source_id in trusted:
            covering[row.team_id].add(row.source_id)
    return {team_id: len(sources) for team_id, sources in covering.items()}
