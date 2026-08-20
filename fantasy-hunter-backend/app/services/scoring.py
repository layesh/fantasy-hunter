"""Official FPL scoring rules, kept in one auditable place.

If the Premier League changes the rules, this file is the only thing that moves.
"""

from __future__ import annotations

import math

GKP, DEF, MID, FWD = 1, 2, 3, 4

POSITION_NAMES = {GKP: "GKP", DEF: "DEF", MID: "MID", FWD: "FWD"}

GOAL_POINTS = {GKP: 6, DEF: 6, MID: 5, FWD: 4}
CLEAN_SHEET_POINTS = {GKP: 4, DEF: 4, MID: 1, FWD: 0}
ASSIST_POINTS = 3

APPEARANCE_POINTS = 1  # any appearance
APPEARANCE_60_POINTS = 2  # 60+ minutes (replaces, not adds to, the above)

SAVES_PER_POINT = 3
PENALTY_SAVE_POINTS = 5
PENALTY_MISS_POINTS = -2
OWN_GOAL_POINTS = -2

# --- set pieces ------------------------------------------------------------
# Roughly 105 penalties are awarded across a 380-match Premier League season,
# so ~0.14 per team per match, converted a little under four times in five.
PENALTIES_PER_TEAM_PER_MATCH = 0.14
PENALTY_CONVERSION = 0.78
# Share of a club's penalties each order takes. The second taker only steps up
# when the first is off the pitch or absent, which is why the drop is steep.
PENALTY_SHARE_BY_ORDER = {1: 0.88, 2: 0.10, 3: 0.02}
YELLOW_CARD_POINTS = -1
RED_CARD_POINTS = -3

# Two goals conceded costs a goalkeeper or defender one point.
GOALS_CONCEDED_PER_PENALTY = 2
GOALS_CONCEDED_PENALTY = -1

# Defensive contribution (introduced 2025/26): defenders need 10 clearances,
# blocks, interceptions and tackles; everyone else needs 12 of those plus ball
# recoveries.
DEFENSIVE_CONTRIBUTION_POINTS = 2
DEFENSIVE_CONTRIBUTION_THRESHOLD = {GKP: 999, DEF: 10, MID: 12, FWD: 12}

# League-wide priors used when a player or team has no usable history.
LEAGUE_AVG_GOALS_PER_TEAM_PER_GAME = 1.42
HOME_ADVANTAGE = 1.10
AWAY_DISADVANTAGE = 0.92


def poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * lam**k / math.factorial(k)


def poisson_at_least(k: int, lam: float) -> float:
    """P(X >= k) for X ~ Poisson(lam)."""
    if k <= 0:
        return 1.0
    if lam <= 0:
        return 0.0
    below = sum(poisson_pmf(i, lam) for i in range(k))
    return max(0.0, 1.0 - below)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
