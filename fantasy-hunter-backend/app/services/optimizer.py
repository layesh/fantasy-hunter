"""MILP squad optimiser and multi-gameweek chip/transfer planner.

Two solvers, both built on the same expected-points model that drives the rest
of the site:

  * `optimise_squad`  — pick the best legal 15 from scratch under a budget.
                        This is what a wildcard or an initial squad wants.
  * `plan_transfers`  — from an existing squad, plan transfers across several
                        gameweeks, paying for hits, banking free transfers, and
                        optionally scheduling chips.

Everything is a genuine integer program solved with CBC, not a greedy heuristic.
That matters: greedy picks fall over exactly where FPL is hard — budget knife
edges, the three-per-club rule, and deciding whether a hit pays for itself.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import pulp
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Player, Team
from app.services import scoring as S
from app.services.chips import playable_windows
from app.services.lineups import start_probabilities
from app.services.predictions import PredictionEngine

log = logging.getLogger(__name__)

SQUAD_SIZE = 15
SQUAD_SHAPE = {S.GKP: 2, S.DEF: 5, S.MID: 5, S.FWD: 3}
XI_SIZE = 11
XI_BOUNDS = {S.GKP: (1, 1), S.DEF: (3, 5), S.MID: (2, 5), S.FWD: (1, 3)}
MAX_PER_CLUB = 3
# FPL status codes that mean "will not play": injured, suspended, unavailable,
# not in squad. "a" is available and "d" is doubtful, which is a judgement call
# rather than a bar.
UNAVAILABLE = frozenset({"i", "s", "u", "n"})

DEFAULT_BUDGET = 1000  # 100.0m, in tenths
TRANSFER_HIT_COST = 4.0
MAX_FREE_TRANSFERS = 5

# A benched player only scores if someone ahead of them fails to play, so bench
# points are worth a fraction of face value — except under a bench boost.
BENCH_WEIGHT = 0.12

# Solver guardrails. These endpoints sit on the request path, so a pathological
# instance must stop rather than hang.
#
# Multi-gameweek FPL programs are genuinely hard: proving optimality can take
# minutes for a plan worth a fraction of a point more than one found in seconds.
# So the solver stops once it can *prove* it is within `SOLVE_GAP` of the best
# possible answer, which is both fast and an honest claim. The time limit is
# only a backstop.
SOLVE_TIME_LIMIT_SECONDS = 30
SOLVE_GAP = 0.01  # 1%

CHIPS = ("wildcard", "bench_boost", "triple_captain", "free_hit")

# Candidate pools. The squad builder can afford a wide net; the multi-gameweek
# planner carries a variable per player *per gameweek*, so it needs a tighter
# one to stay inside the time limit. Players far down their position never
# appear in an optimal answer.
SQUAD_POOL = {S.GKP: 25, S.DEF: 70, S.MID: 80, S.FWD: 45}
PLANNER_POOL = {S.GKP: 12, S.DEF: 35, S.MID: 40, S.FWD: 22}

_GAP_NOTE = (
    f"Solved to within {SOLVE_GAP:.0%} of the theoretical best. Proving the last fraction of "
    "a point can take minutes and would not change the recommendation."
)
_TIME_LIMIT_NOTE = (
    f"Stopped at the {SOLVE_TIME_LIMIT_SECONDS}s solver limit, so this is the best plan "
    "found rather than a proven optimum."
)


class InfeasibleError(RuntimeError):
    """No legal solution exists for the constraints given."""


@dataclass
class OptimiserInput:
    """Everything both solvers need, prepared once."""

    events: list[int]
    players: dict[int, Player]
    xp: dict[int, dict[int, float]]  # player id -> event id -> expected points
    start_probability: dict[int, float] = field(default_factory=dict)

    def total_xp(self, player_id: int) -> float:
        return sum(self.xp.get(player_id, {}).values())


@dataclass
class SquadPick:
    player_id: int
    web_name: str
    position: str
    team_short_name: str | None
    cost: int
    expected_points: float
    # Pre-season consensus that this player starts. None once the season is
    # under way, or when no sources cover their club.
    start_probability: float | None = None
    # Set-piece duty, from FPL's own ordering. 1 = the club's first choice.
    penalties_order: int | None = None
    direct_freekicks_order: int | None = None
    corners_order: int | None = None


@dataclass
class GameweekPlan:
    event_id: int
    chip: str | None
    transfers_in: list[SquadPick]
    transfers_out: list[SquadPick]
    hits: int
    points_cost: float
    starting_xi: list[SquadPick]
    bench: list[SquadPick]
    captain: SquadPick | None
    expected_points: float
    bank: int
    free_transfers_available: int


@dataclass
class OptimisationResult:
    events: list[int]
    squad: list[SquadPick]
    gameweeks: list[GameweekPlan] = field(default_factory=list)
    expected_points: float = 0.0
    points_spent_on_hits: float = 0.0
    status: str = "Optimal"
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Data preparation
# --------------------------------------------------------------------------


def build_input(
    session: Session,
    event_ids: list[int],
    *,
    pool_per_position: dict[int, int] | None = None,
    always_include: set[int] | None = None,
    min_start_probability: float = 0.0,
) -> OptimiserInput:
    """Score every player, then prune to a solvable candidate pool.

    A full 595-player, multi-gameweek program is needlessly slow; the players
    ranked far down their position never appear in an optimal squad. Anyone
    already owned is always kept, so an existing squad stays representable.
    """
    engine = PredictionEngine(session)
    players = list(session.scalars(select(Player)).all())
    fixtures_by_team = engine.fixtures_for_events(event_ids)

    xp: dict[int, dict[int, float]] = {}
    for player in players:
        per_event: dict[int, float] = {event_id: 0.0 for event_id in event_ids}
        for forecast in engine.predict_player(player, event_ids, fixtures_by_team):
            per_event[forecast.event_id] = (
                per_event.get(forecast.event_id, 0.0) + forecast.expected_points
            )
        xp[player.id] = per_event

    pool_per_position = pool_per_position or SQUAD_POOL
    always_include = always_include or set()

    # Pre-season only. The model has no minutes evidence before a ball is
    # kicked, so without this it will happily spend cheap slots on players no
    # source expects to start — the exact failure this guards against.
    probabilities = start_probabilities(session)

    by_position: dict[int, list[Player]] = {}
    for player in players:
        # Injured, suspended and departed players are never worth buying —
        # only "d" (doubtful) is a real decision. Keep any of them only if the
        # caller already owns or explicitly locked them.
        if player.status in UNAVAILABLE and player.id not in always_include:
            continue
        # A player with no consensus data is *unknown*, not a non-starter, so
        # absence from the index never excludes anyone.
        chance = probabilities.get(player.id)
        if (
            min_start_probability > 0.0
            and chance is not None
            and chance < min_start_probability
            and player.id not in always_include
        ):
            continue
        by_position.setdefault(player.element_type, []).append(player)

    kept: dict[int, Player] = {}
    for element_type, group in by_position.items():
        group.sort(key=lambda p: sum(xp[p.id].values()), reverse=True)
        for player in group[: pool_per_position.get(element_type, 50)]:
            kept[player.id] = player

    for player in players:
        if player.id in always_include:
            kept[player.id] = player

    return OptimiserInput(
        events=event_ids, players=kept, xp=xp, start_probability=probabilities
    )


def _pick(
    player: Player,
    teams: dict[int, Team],
    points: float,
    start_probability: float | None = None,
) -> SquadPick:
    team = teams.get(player.team_id)
    return SquadPick(
        player_id=player.id,
        web_name=player.web_name,
        position=S.POSITION_NAMES.get(player.element_type, "?"),
        team_short_name=team.short_name if team else None,
        cost=player.now_cost,
        expected_points=round(points, 2),
        start_probability=start_probability,
        penalties_order=player.penalties_order,
        direct_freekicks_order=player.direct_freekicks_order,
        corners_order=player.corners_and_indirect_freekicks_order,
    )


def _solve(
    problem: pulp.LpProblem, label: str, time_limit: int = SOLVE_TIME_LIMIT_SECONDS
) -> tuple[str, bool]:
    """Solve, and report honestly whether the answer is a proven optimum.

    CBC can report "Optimal" for the best solution it happened to hold when the
    time limit expired, so elapsed time is checked too rather than trusting the
    status alone.
    """
    solver = pulp.PULP_CBC_CMD(msg=False, timeLimit=time_limit, gapRel=SOLVE_GAP)
    started = time.monotonic()
    problem.solve(solver)
    elapsed = time.monotonic() - started

    status = pulp.LpStatus[problem.status]
    if status == "Infeasible":
        raise InfeasibleError(
            f"{label} has no legal solution — check the budget and squad constraints."
        )
    if status == "Undefined" or problem.status == pulp.LpStatusNotSolved:
        raise InfeasibleError(f"{label} could not be solved.")

    proven = status == "Optimal" and elapsed < time_limit * 0.95
    log.info(
        "%s: %s in %.1fs (objective %.2f, proven optimal: %s)",
        label,
        status,
        elapsed,
        pulp.value(problem.objective) or 0.0,
        proven,
    )
    return status, proven


def _on(variable: pulp.LpVariable) -> bool:
    """CBC returns floats; treat anything above a half as selected."""
    return (variable.value() or 0) > 0.5


# --------------------------------------------------------------------------
# Solver 1: build the best squad from scratch
# --------------------------------------------------------------------------


def optimise_squad(
    session: Session,
    event_ids: list[int],
    *,
    budget: int = DEFAULT_BUDGET,
    locked_in: list[int] | None = None,
    excluded: list[int] | None = None,
    min_start_probability: float = 0.0,
) -> OptimisationResult:
    """Best legal 15 for the budget, maximising expected points over the horizon.

    Picks a starting XI and captain for every gameweek in the range, so the
    squad is chosen for the fixtures it will actually play, not just raw talent.
    """
    locked_in = locked_in or []
    excluded = set(excluded or [])
    data = build_input(
        session,
        event_ids,
        always_include=set(locked_in),
        min_start_probability=min_start_probability,
    )
    teams = {t.id: t for t in session.scalars(select(Team)).all()}

    ids = [pid for pid in data.players if pid not in excluded]
    if len(ids) < SQUAD_SIZE:
        raise InfeasibleError("Not enough eligible players to fill a squad.")

    problem = pulp.LpProblem("fh_squad", pulp.LpMaximize)

    squad = pulp.LpVariable.dicts("squad", ids, cat="Binary")
    start = pulp.LpVariable.dicts("start", (ids, event_ids), cat="Binary")
    captain = pulp.LpVariable.dicts("captain", (ids, event_ids), cat="Binary")

    # Objective: starters at face value, the captain again, bench discounted.
    problem += pulp.lpSum(
        data.xp[pid][event] * (start[pid][event] + captain[pid][event])
        + data.xp[pid][event] * BENCH_WEIGHT * (squad[pid] - start[pid][event])
        for pid in ids
        for event in event_ids
    )

    problem += pulp.lpSum(squad[pid] for pid in ids) == SQUAD_SIZE
    problem += (
        pulp.lpSum(data.players[pid].now_cost * squad[pid] for pid in ids) <= budget,
        "budget",
    )

    for element_type, count in SQUAD_SHAPE.items():
        problem += (
            pulp.lpSum(squad[pid] for pid in ids if data.players[pid].element_type == element_type)
            == count
        )

    for team_id in {data.players[pid].team_id for pid in ids}:
        problem += (
            pulp.lpSum(squad[pid] for pid in ids if data.players[pid].team_id == team_id)
            <= MAX_PER_CLUB
        )

    for pid in locked_in:
        if pid in squad:
            problem += squad[pid] == 1

    for event in event_ids:
        problem += pulp.lpSum(start[pid][event] for pid in ids) == XI_SIZE
        problem += pulp.lpSum(captain[pid][event] for pid in ids) == 1
        for pid in ids:
            problem += start[pid][event] <= squad[pid]
            problem += captain[pid][event] <= start[pid][event]
        for element_type, (low, high) in XI_BOUNDS.items():
            in_position = [pid for pid in ids if data.players[pid].element_type == element_type]
            problem += pulp.lpSum(start[pid][event] for pid in in_position) >= low
            problem += pulp.lpSum(start[pid][event] for pid in in_position) <= high

    status, proven = _solve(problem, "squad optimisation")

    chosen = [pid for pid in ids if _on(squad[pid])]
    result = OptimisationResult(
        events=event_ids,
        squad=[
            _pick(
                data.players[pid],
                teams,
                data.total_xp(pid),
                data.start_probability.get(pid),
            )
            for pid in chosen
        ],
        status=status,
    )
    result.squad.sort(key=lambda p: (p.position, -p.expected_points))

    for event in event_ids:
        xi = [pid for pid in chosen if _on(start[pid][event])]
        bench = [pid for pid in chosen if pid not in xi]
        captain_id = next((pid for pid in xi if _on(captain[pid][event])), None)
        points = sum(data.xp[pid][event] for pid in xi) + (
            data.xp[captain_id][event] if captain_id else 0.0
        )
        result.gameweeks.append(
            GameweekPlan(
                event_id=event,
                chip=None,
                transfers_in=[],
                transfers_out=[],
                hits=0,
                points_cost=0.0,
                starting_xi=[_pick(data.players[pid], teams, data.xp[pid][event]) for pid in xi],
                bench=[_pick(data.players[pid], teams, data.xp[pid][event]) for pid in bench],
                captain=_pick(data.players[captain_id], teams, data.xp[captain_id][event])
                if captain_id
                else None,
                expected_points=round(points, 2),
                bank=budget - sum(data.players[pid].now_cost for pid in chosen),
                free_transfers_available=0,
            )
        )

    result.expected_points = round(sum(gw.expected_points for gw in result.gameweeks), 2)
    result.notes.append(
        f"Squad cost {sum(data.players[pid].now_cost for pid in chosen) / 10:.1f}m "
        f"of a {budget / 10:.1f}m budget."
    )
    result.notes.append(_GAP_NOTE if proven else _TIME_LIMIT_NOTE)
    return result


# --------------------------------------------------------------------------
# Solver 2: multi-gameweek transfer and chip plan
# --------------------------------------------------------------------------


def plan_transfers(
    session: Session,
    event_ids: list[int],
    current_squad: list[int],
    *,
    bank: int = 0,
    free_transfers: int = 1,
    available_chips: list[str] | None = None,
    max_hits_per_gameweek: int = 3,
    time_limit: int = SOLVE_TIME_LIMIT_SECONDS,
) -> OptimisationResult:
    """Plan transfers across several gameweeks, optionally scheduling chips.

    Models the things that make FPL planning genuinely hard:
      * free transfers accumulating (capped at five) and hits costing four points
      * budget carried through every buy and sell
      * wildcard and free hit removing the hit cost, with a free hit's squad
        reverting the following gameweek
      * bench boost paying out the bench in full, triple captain tripling
    """
    if len(current_squad) != SQUAD_SIZE:
        raise InfeasibleError(f"A squad must have {SQUAD_SIZE} players, got {len(current_squad)}.")

    requested = [chip for chip in (available_chips or []) if chip in CHIPS]
    # One chip name can be two playable instances — FPL issues a set for GW1-19
    # and another for GW20-38 — and each has a window it may not be played
    # outside of. Both facts come from the API, not from assumption.
    windows = playable_windows(session, requested, event_ids)
    chips = [window.key for window in windows]
    window_by_key = {window.key: window for window in windows}

    data = build_input(
        session,
        event_ids,
        pool_per_position=PLANNER_POOL,
        always_include=set(current_squad),
    )
    teams = {t.id: t for t in session.scalars(select(Team)).all()}

    missing = [pid for pid in current_squad if pid not in data.players]
    if missing:
        raise InfeasibleError(f"Unknown players in the current squad: {missing}")

    ids = list(data.players)
    first = event_ids[0]
    problem = pulp.LpProblem("fh_transfer_plan", pulp.LpMaximize)

    squad = pulp.LpVariable.dicts("squad", (ids, event_ids), cat="Binary")
    buy = pulp.LpVariable.dicts("buy", (ids, event_ids), cat="Binary")
    sell = pulp.LpVariable.dicts("sell", (ids, event_ids), cat="Binary")
    start = pulp.LpVariable.dicts("start", (ids, event_ids), cat="Binary")
    captain = pulp.LpVariable.dicts("captain", (ids, event_ids), cat="Binary")
    hits = pulp.LpVariable.dicts("hits", event_ids, lowBound=0, cat="Integer")
    free_left = pulp.LpVariable.dicts(
        "free_transfers", event_ids, lowBound=0, upBound=MAX_FREE_TRANSFERS, cat="Integer"
    )
    money = pulp.LpVariable.dicts("bank", event_ids, lowBound=0, cat="Continuous")

    chip_used = {
        key: pulp.LpVariable.dicts(f"chip_{key.replace(':', '_')}", event_ids, cat="Binary")
        for key in chips
    }

    def chip_var(name: str, event: int):
        """The variable for playing `name` in `event`, or 0 if that is illegal.

        Only one instance of a chip name can be legal in any given gameweek,
        because the two windows do not overlap.
        """
        for key, window in window_by_key.items():
            if window.name == name and window.covers(event):
                return chip_used[key][event]
        return 0

    # A chip cannot be played outside its window. Without this the solver will
    # cheerfully schedule a wildcard in GW1, which FPL does not allow.
    for key, window in window_by_key.items():
        for event in event_ids:
            if not window.covers(event):
                problem += chip_used[key][event] == 0, f"window_{key.replace(':', '_')}_{event}"
    # Products of two binaries have to be linearised; these stand in for
    # "bench boosted this player" and "triple captained this player".
    boosted = pulp.LpVariable.dicts("boosted", (ids, event_ids), cat="Binary")
    tripled = pulp.LpVariable.dicts("tripled", (ids, event_ids), cat="Binary")

    # --- objective --------------------------------------------------------
    problem += (
        pulp.lpSum(
            data.xp[pid][event] * (start[pid][event] + captain[pid][event] + tripled[pid][event])
            + data.xp[pid][event] * BENCH_WEIGHT * (squad[pid][event] - start[pid][event])
            + data.xp[pid][event] * (1.0 - BENCH_WEIGHT) * boosted[pid][event]
            for pid in ids
            for event in event_ids
        )
        - TRANSFER_HIT_COST * pulp.lpSum(hits[event] for event in event_ids)
        # A banked free transfer has no value in the final gameweek, which
        # leaves the count free to settle anywhere and report nonsense. This
        # nudge is far too small to change any real decision, but it pins the
        # reported figure to the true one.
        + 0.001 * pulp.lpSum(free_left[event] for event in event_ids)
    )

    owned_now = set(current_squad)
    for event_index, event in enumerate(event_ids):
        previous = event_ids[event_index - 1] if event_index else None

        # --- squad continuity ---
        for pid in ids:
            held_before = (
                squad[pid][previous] if previous is not None else (1 if pid in owned_now else 0)
            )
            problem += squad[pid][event] == held_before + buy[pid][event] - sell[pid][event]
            # Cannot buy someone already held, or sell someone not held.
            problem += buy[pid][event] + held_before <= 1
            problem += sell[pid][event] <= held_before

        problem += pulp.lpSum(squad[pid][event] for pid in ids) == SQUAD_SIZE
        for element_type, count in SQUAD_SHAPE.items():
            problem += (
                pulp.lpSum(
                    squad[pid][event]
                    for pid in ids
                    if data.players[pid].element_type == element_type
                )
                == count
            )
        for team_id in {data.players[pid].team_id for pid in ids}:
            problem += (
                pulp.lpSum(squad[pid][event] for pid in ids if data.players[pid].team_id == team_id)
                <= MAX_PER_CLUB
            )

        # --- money ---
        # Selling price is approximated by current price: the public API does
        # not expose what the manager paid.
        spend = pulp.lpSum(data.players[pid].now_cost * buy[pid][event] for pid in ids)
        raise_ = pulp.lpSum(data.players[pid].now_cost * sell[pid][event] for pid in ids)
        money_before = money[previous] if previous is not None else bank
        problem += money[event] == money_before + raise_ - spend

        # --- lineup ---
        problem += pulp.lpSum(start[pid][event] for pid in ids) == XI_SIZE
        problem += pulp.lpSum(captain[pid][event] for pid in ids) == 1
        for pid in ids:
            problem += start[pid][event] <= squad[pid][event]
            problem += captain[pid][event] <= start[pid][event]
        for element_type, (low, high) in XI_BOUNDS.items():
            in_position = [pid for pid in ids if data.players[pid].element_type == element_type]
            problem += pulp.lpSum(start[pid][event] for pid in in_position) >= low
            problem += pulp.lpSum(start[pid][event] for pid in in_position) <= high

        # --- chips ---
        wildcard = chip_var("wildcard", event)
        free_hit = chip_var("free_hit", event)
        bench_boost = chip_var("bench_boost", event)
        triple = chip_var("triple_captain", event)

        for pid in ids:
            # Bench boost pays the bench in full; only bench players qualify.
            problem += boosted[pid][event] <= squad[pid][event] - start[pid][event]
            problem += boosted[pid][event] <= bench_boost
            # Triple captain adds a third helping, and only to the captain.
            problem += tripled[pid][event] <= captain[pid][event]
            problem += tripled[pid][event] <= triple
        problem += pulp.lpSum(tripled[pid][event] for pid in ids) <= triple

        if chips:
            problem += (
                pulp.lpSum(chip_used[chip][event] for chip in chips) <= 1,
                f"one_chip_gw{event}",
            )

        # --- transfers, free transfers and hits ---
        made = pulp.lpSum(buy[pid][event] for pid in ids)
        problem += made == pulp.lpSum(sell[pid][event] for pid in ids)

        free_before = free_left[previous] if previous is not None else free_transfers
        unlimited = wildcard + free_hit  # both make transfers free and unbounded

        problem += hits[event] >= made - free_before - SQUAD_SIZE * unlimited
        problem += hits[event] <= max_hits_per_gameweek + SQUAD_SIZE * unlimited
        problem += hits[event] <= SQUAD_SIZE * (1 - unlimited)

        # Free transfers roll over, capped at five. These are all upper bounds
        # and the objective mildly prefers more, so the solver settles on the
        # smallest of them — which is the rule. The middle bound matters on a
        # wildcard or free hit: those transfers are free, but they must not let
        # you bank transfers you never earned.
        problem += free_left[event] <= free_before - made + 1 + SQUAD_SIZE * unlimited
        problem += free_left[event] <= free_before + 1
        problem += free_left[event] <= MAX_FREE_TRANSFERS

        # A free hit's squad is temporary: the next gameweek reverts to the one
        # before the chip was played.
        if not isinstance(free_hit, int) and event_index + 1 < len(event_ids):
            nxt = event_ids[event_index + 1]
            for pid in ids:
                held_before = (
                    squad[pid][previous] if previous is not None else (1 if pid in owned_now else 0)
                )
                problem += squad[pid][nxt] - held_before <= 1 - free_hit
                problem += held_before - squad[pid][nxt] <= 1 - free_hit

    for key in chips:
        problem += pulp.lpSum(chip_used[key][event] for event in event_ids) <= 1

    status, proven = _solve(problem, "transfer plan", time_limit)

    # --- read the solution back ------------------------------------------
    result = OptimisationResult(events=event_ids, squad=[], status=status)
    total_hits = 0.0

    for event in event_ids:
        held = [pid for pid in ids if _on(squad[pid][event])]
        xi = [pid for pid in held if _on(start[pid][event])]
        bench_ids = [pid for pid in held if pid not in xi]
        captain_id = next((pid for pid in xi if _on(captain[pid][event])), None)

        chip_this_week = next(
            (window_by_key[key].name for key in chips if _on(chip_used[key][event])),
            None,
        )
        hit_count = int(round(hits[event].value() or 0))
        total_hits += hit_count

        points = sum(data.xp[pid][event] for pid in xi)
        if captain_id:
            points += data.xp[captain_id][event]
            if chip_this_week == "triple_captain":
                points += data.xp[captain_id][event]
        if chip_this_week == "bench_boost":
            points += sum(data.xp[pid][event] for pid in bench_ids)

        result.gameweeks.append(
            GameweekPlan(
                event_id=event,
                chip=chip_this_week,
                transfers_in=[
                    _pick(data.players[pid], teams, data.xp[pid][event])
                    for pid in ids
                    if _on(buy[pid][event])
                ],
                transfers_out=[
                    _pick(data.players[pid], teams, data.xp[pid][event])
                    for pid in ids
                    if _on(sell[pid][event])
                ],
                hits=hit_count,
                points_cost=round(hit_count * TRANSFER_HIT_COST, 2),
                starting_xi=[_pick(data.players[pid], teams, data.xp[pid][event]) for pid in xi],
                bench=[_pick(data.players[pid], teams, data.xp[pid][event]) for pid in bench_ids],
                captain=_pick(data.players[captain_id], teams, data.xp[captain_id][event])
                if captain_id
                else None,
                expected_points=round(points, 2),
                bank=int(round(money[event].value() or 0)),
                free_transfers_available=int(round(free_left[event].value() or 0)),
            )
        )

    final = [pid for pid in ids if _on(squad[pid][event_ids[-1]])]
    result.squad = [
        _pick(
            data.players[pid],
            teams,
            data.total_xp(pid),
            data.start_probability.get(pid),
        )
        for pid in final
    ]
    result.squad.sort(key=lambda p: (p.position, -p.expected_points))

    result.points_spent_on_hits = round(total_hits * TRANSFER_HIT_COST, 2)
    result.expected_points = round(
        sum(gw.expected_points for gw in result.gameweeks) - result.points_spent_on_hits, 2
    )
    result.notes.append(
        "Selling prices are approximated by current price — the public FPL API "
        "does not expose what you paid."
    )
    result.notes.append(_GAP_NOTE if proven else _TIME_LIMIT_NOTE)
    return result
