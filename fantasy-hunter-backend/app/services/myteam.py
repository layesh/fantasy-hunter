""""My Team": link an FPL entry, score the squad, suggest transfers.

Uses only public endpoints (no FPL login), so selling prices are approximated by
current price — surfaced honestly in the response rather than hidden.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Player, Team
from app.services import scoring as S
from app.services.predictions import FixturePrediction, PredictionEngine

SQUAD_SIZE = 15
MAX_PER_CLUB = 3
SQUAD_SHAPE = {S.GKP: 2, S.DEF: 5, S.MID: 5, S.FWD: 3}
# Valid outfield shapes for a starting XI (min/max per position).
XI_BOUNDS = {S.GKP: (1, 1), S.DEF: (3, 5), S.MID: (2, 5), S.FWD: (1, 3)}


@dataclass
class SquadPlayer:
    player: Player
    position: int  # 1-15 pick order
    multiplier: int
    is_captain: bool
    is_vice_captain: bool
    predictions: list[FixturePrediction] = field(default_factory=list)

    @property
    def expected_points(self) -> float:
        return round(sum(p.expected_points for p in self.predictions), 2)


@dataclass
class TransferSuggestion:
    out_player_id: int
    out_name: str
    out_cost: int
    out_expected_points: float
    in_player_id: int
    in_name: str
    in_cost: int
    in_expected_points: float
    gain: float
    spare_after: int
    reason: str


def horizon_points(predictions: list[FixturePrediction]) -> float:
    return sum(p.expected_points for p in predictions)


def build_squad(
    session: Session,
    picks: list[dict],
    engine: PredictionEngine,
    event_ids: list[int],
) -> list[SquadPlayer]:
    ids = [p["element"] for p in picks]
    players = {
        p.id: p for p in session.scalars(select(Player).where(Player.id.in_(ids))).all()
    }
    fixtures_by_team = engine.fixtures_for_events(event_ids)

    squad: list[SquadPlayer] = []
    for pick in picks:
        player = players.get(pick["element"])
        if player is None:
            continue
        squad.append(
            SquadPlayer(
                player=player,
                position=pick.get("position", 0),
                multiplier=pick.get("multiplier", 1),
                is_captain=bool(pick.get("is_captain")),
                is_vice_captain=bool(pick.get("is_vice_captain")),
                predictions=engine.predict_player(player, event_ids, fixtures_by_team),
            )
        )
    return squad


def best_starting_xi(squad: list[SquadPlayer]) -> tuple[list[SquadPlayer], list[SquadPlayer]]:
    """Pick the highest-xPts legal XI; the rest are the bench, in xPts order."""
    by_position: dict[int, list[SquadPlayer]] = {}
    for sp in squad:
        by_position.setdefault(sp.player.element_type, []).append(sp)
    for group in by_position.values():
        group.sort(key=lambda s: s.expected_points, reverse=True)

    xi: list[SquadPlayer] = []
    for position, (minimum, _) in XI_BOUNDS.items():
        xi.extend(by_position.get(position, [])[:minimum])

    remaining = [sp for sp in squad if sp not in xi and sp.player.element_type != S.GKP]
    remaining.sort(key=lambda s: s.expected_points, reverse=True)
    for sp in remaining:
        if len(xi) >= 11:
            break
        position = sp.player.element_type
        current = sum(1 for x in xi if x.player.element_type == position)
        if current < XI_BOUNDS[position][1]:
            xi.append(sp)

    bench = [sp for sp in squad if sp not in xi]
    bench.sort(key=lambda s: (s.player.element_type == S.GKP, -s.expected_points))
    return xi, bench


def rate_squad(squad: list[SquadPlayer], all_players: list[Player], engine: PredictionEngine,
               event_ids: list[int]) -> dict:
    """Score the squad 0-100 against what was affordably available.

    The benchmark is the league-wide xPts distribution by position, so the rating
    means "how much of the available upside did you capture", not an opaque grade.
    """
    xi, bench = best_starting_xi(squad)
    xi_points = sum(sp.expected_points for sp in xi)

    fixtures_by_team = engine.fixtures_for_events(event_ids)
    pool: dict[int, list[float]] = {}
    for player in all_players:
        points = horizon_points(engine.predict_player(player, event_ids, fixtures_by_team))
        pool.setdefault(player.element_type, []).append(points)
    for group in pool.values():
        group.sort(reverse=True)

    # Ceiling: the best legal XI shape money-no-object.
    ceiling = 0.0
    shape = {S.GKP: 1, S.DEF: 5, S.MID: 5, S.FWD: 0}
    for position, count in shape.items():
        ceiling += sum(pool.get(position, [])[:count])
    # Fill the last outfield slot from the best remaining forward or midfielder.
    extras = sorted(
        pool.get(S.FWD, [])[:3] + pool.get(S.MID, [])[5:8] + pool.get(S.DEF, [])[5:8],
        reverse=True,
    )
    ceiling += sum(extras[:1])

    floor = ceiling * 0.45  # a plausible "average manager" baseline
    rating = 100.0 * (xi_points - floor) / (ceiling - floor) if ceiling > floor else 0.0

    return {
        "rating": round(S.clamp(rating, 0.0, 100.0), 1),
        "expected_points_xi": round(xi_points, 2),
        "benchmark_ceiling": round(ceiling, 2),
        "benchmark_floor": round(floor, 2),
        "bench_points": round(sum(sp.expected_points for sp in bench), 2),
        "explanation": (
            "Rating is your best legal XI's expected points over the horizon, "
            "scaled between an average-manager baseline and the highest-scoring "
            "legal XI available in the game."
        ),
    }


def captain_options(squad: list[SquadPlayer], top: int = 3) -> list[dict]:
    ranked = sorted(squad, key=lambda s: s.expected_points, reverse=True)[:top]
    return [
        {
            "player_id": sp.player.id,
            "web_name": sp.player.web_name,
            "expected_points": sp.expected_points,
            "doubled_points": round(sp.expected_points * 2, 2),
            "is_current_captain": sp.is_captain,
        }
        for sp in ranked
    ]


def suggest_transfers(
    session: Session,
    squad: list[SquadPlayer],
    engine: PredictionEngine,
    event_ids: list[int],
    bank: int,
    limit: int = 5,
) -> list[TransferSuggestion]:
    """Best single transfers by expected-points gain over the horizon.

    Respects budget and the three-per-club rule. Selling price is approximated
    by current price — without an authenticated session we cannot know what the
    manager paid.
    """
    squad_ids = {sp.player.id for sp in squad}
    club_counts: dict[int, int] = {}
    for sp in squad:
        club_counts[sp.player.team_id] = club_counts.get(sp.player.team_id, 0) + 1

    candidates = session.scalars(
        select(Player).where(Player.status.notin_(["u", "n"]))
    ).all()
    fixtures_by_team = engine.fixtures_for_events(event_ids)

    by_position: dict[int, list[tuple[Player, float]]] = {}
    for player in candidates:
        if player.id in squad_ids:
            continue
        points = horizon_points(engine.predict_player(player, event_ids, fixtures_by_team))
        by_position.setdefault(player.element_type, []).append((player, points))
    for group in by_position.values():
        group.sort(key=lambda pair: pair[1], reverse=True)

    raw: list[TransferSuggestion] = []
    for sp in squad:
        out_player = sp.player
        out_points = sp.expected_points
        budget = out_player.now_cost + bank
        pool = by_position.get(out_player.element_type, [])[:80]

        # Keep a few options per outgoing player so de-duplication below still
        # has something to fall back on when two of them want the same target.
        found = 0
        for candidate, points in pool:
            if candidate.now_cost > budget:
                continue
            if candidate.team_id != out_player.team_id:
                if club_counts.get(candidate.team_id, 0) >= MAX_PER_CLUB:
                    continue
            gain = points - out_points
            if gain <= 0:
                continue
            raw.append(
                TransferSuggestion(
                    out_player_id=out_player.id,
                    out_name=out_player.web_name,
                    out_cost=out_player.now_cost,
                    out_expected_points=round(out_points, 2),
                    in_player_id=candidate.id,
                    in_name=candidate.web_name,
                    in_cost=candidate.now_cost,
                    in_expected_points=round(points, 2),
                    gain=round(gain, 2),
                    spare_after=budget - candidate.now_cost,
                    reason=_transfer_reason(out_player, candidate, gain),
                )
            )
            found += 1
            if found >= 3:
                break

    # A manager can only sign a given player once, and only sell each of theirs
    # once, so the list must not repeat either side of the deal.
    raw.sort(key=lambda s: s.gain, reverse=True)
    suggestions: list[TransferSuggestion] = []
    seen_in: set[int] = set()
    seen_out: set[int] = set()
    for suggestion in raw:
        if suggestion.in_player_id in seen_in or suggestion.out_player_id in seen_out:
            continue
        seen_in.add(suggestion.in_player_id)
        seen_out.add(suggestion.out_player_id)
        suggestions.append(suggestion)
        if len(suggestions) == limit:
            break
    return suggestions


def _transfer_reason(out_player: Player, in_player: Player, gain: float) -> str:
    bits = [f"+{gain:.2f} xPts over the horizon"]
    if out_player.status != "a":
        bits.append(f"{out_player.web_name} is flagged ({out_player.status})")
    if in_player.now_cost < out_player.now_cost:
        bits.append(f"frees {(out_player.now_cost - in_player.now_cost) / 10:.1f}m")
    return "; ".join(bits)


def team_lookup(session: Session) -> dict[int, Team]:
    return {t.id: t for t in session.scalars(select(Team)).all()}
