# Fantasy Hunter — backend

FPL analytics API. Local-only for now (SQLite, no Docker, no cloud).

> Setting up for the first time, or want to run the frontend too? Start from the
> [root README](../README.md) — it covers both halves end to end. This file is
> the backend reference.

Architecture is a **modular monolith**: one FastAPI app with `ingest`, `services`
(prediction / fixture / squad logic) and `routers` kept separate, so the
prediction and optimiser modules can be lifted into their own service later
without rewriting callers.

## Run it

Runs on **port 8420** (4200 and 4500 are in use on this machine, and 8000 is
blocked).

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"      # Windows
# source .venv/bin/activate && pip install -e ".[dev]"   # macOS/Linux

# Pull the FPL API into a local SQLite database. Run this BEFORE starting the
# server — the app is useless against an empty database.
# --histories adds per-player season history (~600 upstream calls, ~1 min).
# The prediction model needs it, so run it at least once.
.venv/Scripts/python manage.py ingest --histories

.venv/Scripts/python -m uvicorn app.main:app --reload --port 8420
```

Health check: <http://127.0.0.1:8420/api/health> ·
interactive docs: <http://127.0.0.1:8420/docs>

## Endpoints

| Endpoint | What it backs |
|---|---|
| `GET /api/health` | Service + last-ingest status |
| `GET /api/teams`, `GET /api/events` | Reference data |
| `GET /api/players` | Player database — search, position/team/price filters, sorting, paging |
| `GET /api/players/{id}` | Player profile incl. past seasons |
| `GET /api/fixtures` | Raw fixture list |
| `GET /api/fixtures/ticker?horizon=6` | Fixture ticker — one row per club, one column per gameweek |
| `GET /api/fixtures/defence` | Club clean-sheet / goals-conceded record, last two seasons |
| `GET /api/predictions?horizon=5` | Predicted points table |
| `GET /api/predictions/player/{id}` | Full per-fixture breakdown — the "show your work" view |
| `POST /api/predictions/snapshot` | Freeze predictions for later grading |
| `GET /api/predictions/accuracy` | Public accuracy record |
| `GET /api/compare?ids=411,12,154` | Player comparison, 2–4 players |
| `GET /api/my-team/{entry_id}` | Squad dashboard, rating, captain picks, transfer suggestions |
| `GET /api/optimizer/squad` | Best legal 15 for a budget (MILP) |
| `POST /api/optimizer/plan` | Multi-gameweek transfer + chip plan from a given squad |
| `GET /api/optimizer/plan/{entry_id}` | Same, pulling the squad and bank from FPL |
| `GET /api/optimizer/meta` | Supported chips and defaults |
| `GET /api/optimizer/lineups` | Pre-season predicted-XI consensus index and its sources |
| `GET /api/chips` | Chip windows (fact) plus the timing prior and DGW/BGW outlook (belief) |

## Offline jobs

```bash
python manage.py ingest [--histories]   # refresh data
python manage.py snapshot --horizon 5   # freeze predictions (run before each deadline)
python manage.py grade                  # score stored predictions against actuals
python manage.py lineups --import data/predicted_lineups_gw1.json   # pre-season predicted XIs
python manage.py chips --import data/chip_timing_2026_27.json       # chip-timing prior
python manage.py defence --import data/team_defence.json            # club defensive records
python manage.py predict 411            # print one player's breakdown
pytest tests -q                         # 55 tests
```

Order matters for the accuracy record: `snapshot` before a deadline, then
`ingest --histories` and `grade` once the gameweek has finished.

## The prediction model (`heuristic-v1`)

A transparent heuristic, not a black box — every number comes back with the
component breakdown that produced it.

1. **Expected minutes** from weighted prior-season minutes, scaled by the
   official availability flag.
2. **Per-90 rates** from weighted prior seasons, blending actual goals/assists
   with xG/xA.
3. **Fixture strength** from team attack/defence ratings vs. the league mean,
   plus home advantage.
4. Rates × minutes × fixture → expected event counts → FPL points, using the
   scoring rules in `app/services/scoring.py`.

### Known limits

State these in the UI rather than hiding them:

- **Team strength is zero pre-season.** FPL only publishes attack/defence
  ratings once results exist. Until then the model falls back to the official
  per-fixture FDR — coarser, and it makes the ticker's attack and defence
  columns identical. `components.fixture_model` reports which is in use.
- No current-season data before GW1, so everything leans on last season. Players
  new to the league have no history and fall back to a price-based prior.
- Bonus is estimated from historical BPS rate, not simulated against the other
  21 players on the pitch.
- Rotation and press-conference news beyond the official `status` flag are not
  modelled.
- `my-team` uses public endpoints only, so selling prices are approximated by
  current price.

## The pre-season predicted-XI index (`app/services/lineups.py`)

**Pre-season only.** Before a ball is kicked there is no minutes data, so the
model falls back to a price-based prior — which is how a 4.0m defender nobody
has heard of ends up in an "optimal" squad. The one signal that does exist is
that a dozen sites publish predicted line-ups, and agreement between
independent sources is a usable probability.

```bash
python manage.py lineups --import data/predicted_lineups_gw1.json
python manage.py lineups --min-probability 0.8
```

`start_probability` = the fraction of *trusted* sources naming a player in
their club's XI. Pass `min_start_probability` to `GET /api/optimizer/squad` to
bar players below a threshold; `GET /api/optimizer/lineups` publishes the index
and the sources behind it.

Three rules keep it honest:

- **It expires.** `is_preseason()` gates every read. Once GW1 finishes, actual
  starts are real evidence and this becomes noise, so it returns nothing.
- **Sources are scored, not trusted.** Every printed name is resolved against
  the club's *current* FPL squad; a source below 75% is rejected, and a single
  club's XI below 70% (or not exactly 11 names) is dropped for that club alone.
  Stale predicted XIs are worse than none — they look authoritative while
  being wrong.
- **Absence means unknown, never zero.** A club needs three trusted sources
  before any of its players are scored, and an ambiguous name ("Sangaré" when
  two squad members match) credits nobody rather than guessing.

Bundles are fetched by hand and imported, not scraped on a schedule. This runs
two or three times a season, and site-specific HTML parsers would break far
more often than they would run.

### Known limits

- The quality gate catches *wrong-squad* names, not a source that is merely
  out of date while still naming current players. Disagreement between sources
  is treated as uncertainty, which is the intended behaviour, but a
  systematically stale source will drag a probability with it.
- Sources are equally weighted; none has a track record yet. Once the accuracy
  record has data, weighting by past correctness is the obvious upgrade.
- Syndicated content must be de-duplicated by hand — the Never Manage Alone
  XI is fetched via Yahoo, and counting both would double-weight one opinion.

## Club defensive records (`app/services/defence.py`)

The ticker is forward-looking difficulty; this is the backward-looking record —
who actually keeps clean sheets.

**It cannot be derived from data we already hold.** FPL's `history_past` gives a
player's season totals but never the club he played for, so a keeper's 19 clean
sheets cannot be attributed to anyone, and a keeper who changed clubs carries
his old record with him. Sourced externally from the Premier League's own club
stats leaderboard.

```bash
python manage.py defence --import data/team_defence.json
```

Stored per season rather than pre-aggregated, so callers can weight recency
themselves; `SEASON_WEIGHTS` currently favours the most recent season 65/35.
Clubs with only one season on record are scored on that season alone rather
than dragged toward zero by the missing one.

Promoted clubs come back with `known: false` and no numbers. **No record is not
a bad record**, and rendering them as zero would make Coventry and Hull look
like the worst defences in the league rather than unknowns.

### Known limits

- Two seasons only, and a club's record travels with neither its manager nor
  its defenders — Liverpool's 14 clean sheets in 2024/25 say little about a
  back line that has since changed.
- Once the season is under way this becomes redundant for the current
  campaign: `fixtures` carries `team_h_score` / `team_a_score`, so live club
  clean sheets can be computed exactly with no external source.

## Set pieces

FPL publishes each club's penalty, direct free-kick and corner ordering in
`bootstrap-static` (`penalties_order` and friends) — the same data behind The
Scout's set-piece page. We were already ingesting all three and using none of
them; 67 players carry a penalty order.

The primary taker now gets credit for the club's penalty load:

```
0.14 penalties per team per match x 0.78 conversion x 0.88 first-taker share
```

**The catch is double-counting.** A player's historical goals already include
the penalties they took, and FPL publishes penalties *saved* and *missed* but
never *scored*, so the overlap cannot be measured. It is discounted instead:
a player with real history keeps most of that credit inside their existing
per-90 rate (`PENALTY_CREDIT_BY_SOURCE["history"] = 0.30`), while one priced
from a prior — a new signing, or a promoted-club player who has taken none here
— gets the full rate. Measured effect: **+0.3 to +0.7 xPts over five gameweeks**
for a primary taker, mean +0.12 across all 67.

Duty is surfaced as `PEN` / `FK` / `COR` badges in the player database and on
optimiser picks. Only the first choice is badged — a second-choice penalty taker
steps up rarely enough that flagging them is noise.

### Known limits

- Free-kick and corner duty are **shown but not modelled**. Corners feed assists
  through a route we cannot quantify without corner counts, and inventing a
  number there would be worse than leaving it visible-but-unscored.
- Penalty orders shift after a few games, and pre-season assignments for new
  signings are the least reliable part of the source — FPL says so themselves.

## Chips (`app/services/chips.py`)

Two kinds of information, deliberately kept apart.

**Legality is fact.** FPL publishes each chip's window in `bootstrap-static`,
and since 2025/26 there are **eight chips, not four** — one set expiring at the
GW19 deadline, a second for GW20-38 — with the wildcard and free hit barred
from GW1 entirely. These are ingested, never hardcoded. An earlier build
assumed four chips with no windows and scheduled a wildcard in GW1, which is a
plan no manager can execute; `tests/test_chips.py` now guards that.

**Timing is a belief, and is labelled one.** The chips worth the most are
played in double and blank gameweeks, which do not exist in the fixture list in
August — they are created later by cup progression and postponements. So
`chip_timing_priors` stores a seeded distribution with `basis` and `source` on
every row, blended by `KIND_WEIGHT`:

```
observed (1.0) > planned (0.6) > expert (0.35)
```

Nothing is `observed` yet. That kind only exists once a season has been played,
and it dominates the seed when it arrives — which is the mechanism by which the
model matures each season rather than staying a fixed opinion.

`gameweek_outlooks` holds the same idea for the calendar: a prior likelihood
that each gameweek becomes a double or a blank, with `confirmed` flipping to
true only when the real fixture list says so.

```bash
python manage.py chips --import data/chip_timing_2026_27.json
python manage.py chips        # print the schedule with reasons
```

### Known limits

- The second-half prior is materially weaker than the first-half one. First-half
  rows come from a survey of ~3.7k managers' actual intent; second-half rows are
  inferred from historical cup scheduling, because the fixtures do not exist yet.
- Sources are not yet weighted by track record — that needs the accuracy record
  to have data behind it.
- The optimiser schedules chips to maximise expected points within its horizon
  (max 8 gameweeks). It does not consult the timing prior, and cannot see a
  double in GW33 from GW1. The prior is decision support for a human, not a
  solver input.

## The optimiser (`app/services/optimizer.py`)

Two integer programs solved with CBC via PuLP — not greedy shortlists. Greedy
selection falls over exactly where FPL is hard: budget knife edges, the
three-per-club rule, and whether a −4 hit pays for itself.

**`optimise_squad`** picks the best legal 15 for a budget, choosing a starting XI
and captain for *every* gameweek in the range, so the squad is built for the
fixtures it will actually play. Solves in about a second.

**`plan_transfers`** starts from an existing squad and plans forward, modelling:

- free transfers accumulating (capped at five) and hits costing four points
- money carried through every buy and sell
- **wildcard** and **free hit** — unlimited transfers with no hit, and a free
  hit's squad reverting the following gameweek
- **bench boost** — the bench paid in full instead of discounted
- **triple captain** — a third helping of the captain's score

Each chip is playable at most once, and at most one per gameweek. Products of
binaries (a chip *and* a player) are linearised rather than approximated.

### Why the answer is not always "optimal"

Multi-gameweek FPL programs are genuinely hard. Proving optimality can take
minutes to gain a fraction of a point, so the solver stops once it can prove it
is within 1% of the best possible answer (`SOLVE_GAP`), with a time limit as a
backstop. Which of the two stopped it is reported in `notes` — the code checks
elapsed time rather than trusting CBC's status, because CBC will report
"Optimal" for whatever it happened to be holding when the clock ran out.

Measured: a five-gameweek plan with all four chips uses the full time budget and
lands ~1% below a 180-second solve. The same plan with no chips solves in under a
second — chip *scheduling* is the expensive part, so the API lets callers choose
which chips to consider and how long to spend (`time_limit`).

### Known limits

- Selling price is approximated by current price; the public API does not expose
  what a manager paid, so profit on a rising asset is invisible.
- The candidate pool is pruned per position (`PLANNER_POOL`); players far down
  their position never appear in an optimal squad, but this is a heuristic on top
  of an exact method.
- Bench points are discounted at a flat `BENCH_WEIGHT`, rather than modelling the
  probability that someone ahead of them fails to play.

## The accuracy record

The differentiator is publishing a *graded* record, and that only works if the
data model supports it from day one:

- `POST /api/predictions/snapshot` writes predictions with a timestamp before a
  deadline and **never overwrites an existing row** — a published prediction is
  immutable.
- After a gameweek finalises, `manage.py ingest --histories` pulls actual
  returns and `manage.py grade` scores every prediction against them.
- `GET /api/predictions/accuracy` serves mean absolute error and bias per
  gameweek. It is empty until the first gameweek has been graded — publish it
  only once there is real data behind it.
