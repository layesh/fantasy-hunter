# Fantasy Hunter

An FPL (Fantasy Premier League) analytics platform. Positioning: **the FPL tool
that shows its work.**

Most tools in this market assert their predictions are good; almost none publish
a record. Fantasy Hunter returns the component breakdown behind every predicted
points number, records every prediction before the deadline, and grades it
afterwards on a public page.

The full product plan — competitive landscape, phasing, and the cloud
architecture this will eventually grow into — is in [`plan.md`](plan.md). This
README covers what exists today and how to run it.

---

## Quick start

Two terminals. This is all you need once the project is set up — if this is a
fresh machine, jump to [Running it from fresh](#running-it-from-fresh) first
(virtualenv, `npm install`, and one `--histories` ingest).

**Terminal 1 — backend, port 8420:**

```bash
cd fantasy-hunter-backend
.venv/Scripts/python -m uvicorn app.main:app --reload --port 8420
```

```bash
# macOS / Linux
cd fantasy-hunter-backend
.venv/bin/python -m uvicorn app.main:app --reload --port 8420
```

Health check: <http://127.0.0.1:8420/api/health> — `"status": "ok"` and a
non-zero player count. Swagger docs: <http://127.0.0.1:8420/docs>.

**Terminal 2 — frontend, port 4300:**

```bash
cd fantasy-hunter-frontend
npm start
```

Open <http://localhost:4300>. Start the backend **first** — the dev server
proxies `/api` to it, so without it every page shows *"Cannot reach the API."*

**Before you plan anything, refresh the data** (prices, news, injuries, fixtures
— takes a couple of seconds):

```bash
cd fantasy-hunter-backend
.venv/Scripts/python manage.py ingest
```

Both paths above work unchanged in PowerShell, Git Bash and cmd. Stop either
server with `Ctrl+C`.

---

## What's built so far

Everything below runs locally today, against real FPL data. Nothing here is
mocked or stubbed.

### Data

| | |
|---|---|
| **Ingestion** | `bootstrap-static`, `fixtures` and per-player season histories pulled into SQLite by `manage.py ingest`. Nothing reads the origin API on the request path. |
| **Defensive client** | Cache, retries with backoff, and last-known-good fallback — the FPL API is undocumented and does go down. |
| **Timezone correctness** | Deadlines are stored and returned as timezone-aware UTC (`UtcDateTime`) and rendered in the viewer's own zone. SQLite silently drops `tzinfo`, which had every deadline reading six hours early. |
| **Imported datasets** | Two-season club defensive records, chip-timing priors, and a pre-season predicted-XI bundle — JSON in `fantasy-hunter-backend/data/`, loaded via `manage.py … --import`. |

### Model

| | |
|---|---|
| **Predicted points** (`heuristic-v1`) | Per-fixture expected points with the **full component breakdown** exposed — availability, p(start), p(60), clean-sheet probability, xG, xA, defensive contribution, bonus. Every number on screen can be traced to its parts. |
| **Set pieces** | Penalty, direct-free-kick and corner duty from FPL's own ordering feed the goal model, discounted by profile source so a player's historical penalty goals aren't counted twice. |
| **Fixture difficulty** | Attack/defence ratings derived from results, with an automatic fall back to official FDR while the season is too young for the ratings to mean anything. Which one is in use is reported, not hidden. |
| **Club defence** | Clean sheets, goals conceded and xGC over two seasons, recency-weighted 65/35, with promoted clubs marked *unknown* rather than *bad*. |
| **Predicted-XI consensus** | Seven trusted sources reduced to a start probability per player, with match-rate quality gates, ambiguous-name refusal, and scaling by FPL's own injury flags. Pre-season only — it expires the moment real minutes exist. |

### Tools

| | |
|---|---|
| **Players / Compare / Ticker** | Searchable player database, side-by-side comparison, and a fixture ticker with a defensive-record table. |
| **My Team** | Squad rating against a benchmark, best XI, captain options, and ranked transfer suggestions for a given entry ID. |
| **Optimiser** | MILP (PuLP + CBC) in two modes — build a squad from scratch, or plan transfers across several gameweeks with budget, formation, 3-per-club, free-transfer rollover and hit costs all modelled. Supports locking players in, excluding them, and a minimum start-probability filter. |
| **Chip planner** | Wildcard, Bench Boost, Triple Captain and Free Hit scheduled by the solver inside their **real** FPL windows — both halves of the season, eight chip instances, read from the API rather than assumed. A dedicated page charts when each chip is typically played and why. |
| **Accuracy record** | `snapshot` freezes predictions before a deadline and never overwrites them; `grade` scores them against actuals afterwards. This is the product's whole positioning, so it is wired into the data model rather than bolted on. |
| **Tests** | 59, covering the model, optimiser, consensus index, chip windows and timezone handling. |

### Not built

Auth, user accounts, subscriptions, Postgres, Redis, a scheduler, and any form
of deployment. This is a **single-user local application** today, driven by
`manage.py` and two dev servers.

---

## What's next

Ordered by what would actually earn its keep, not by what's easiest.

### Near term — make it run itself

1. **Scheduled jobs.** Every refresh is currently a command someone remembers to
   type. The accuracy page stays empty unless `snapshot` runs before each
   deadline — the one job that cannot be run late. A scheduler (cron, or
   APScheduler in-process) covering `ingest` → `snapshot` → `grade` around the
   gameweek cycle is the highest-value next change.
2. **An in-season starter model.** The predicted-XI index deliberately expires
   after Gameweek 1, and nothing replaces it. Rotation risk is the single
   biggest source of wasted points, so a minutes model built from actual starts
   should take over where the consensus leaves off.
3. **Database migrations.** Schema is created by `init_db()` with no Alembic, so
   any model change means deleting `fantasy_hunter.db` and re-ingesting. That is
   fine now and will not be fine the moment there is data worth keeping.

### Phase 2 — parity (per [`plan.md`](plan.md) §4)

4. **Price-change prediction** from transfer trends — with the honest caveat the
   plan already calls for: the official app has the real transfer feed and we
   don't, so this is an estimate and should be labelled as one.
5. **Mini-league tracking** and simple league simulation.
6. **Publish the accuracy record** once several gameweeks are graded. The plan
   is explicit that the "graded accuracy" pitch must not launch with zero data
   points.

### Phase 3 — differentiation

7. **Public REST API + MCP server.** Nearly uncontested — one competitor offers
   it — and it lets an LLM query the model directly.
8. **Effective ownership and rank-threat analysis**, then a what-if simulator.
9. **Personalised weekly briefing** driven by the user's actual squad.

### Before any of that becomes a product

Auth, multi-user data isolation, Stripe subscriptions, the Postgres/Redis move,
and Terraform + CI/CD — the full target architecture is in [`plan.md`](plan.md)
§5–6. Deliberately deferred: running three runtimes and a cloud bill buys
nothing while there is one user.

**Explicitly out of scope**, per the plan's read of the market: live points,
live rank, projected bonus and price-change alerts are all free in the official
FPL app, which owns the underlying data. Competing there is a losing trade.

---

## Architecture

A **modular monolith**, not microservices. The plan calls for Spring Boot + Go +
Python eventually; running three runtimes locally buys nothing today, so module
boundaries are drawn where a service split would later go.

```
fantasy-hunter/
├── fantasy-hunter-backend/     FastAPI + SQLAlchemy + PuLP  → port 8420
│   ├── app/
│   │   ├── ingest.py           pulls the official FPL API into SQLite
│   │   ├── fpl_client.py       cache, retries, backoff, last-known-good
│   │   ├── models.py           teams, players, fixtures, predictions, grades
│   │   ├── services/           predictions · fdr · myteam · optimizer · scoring
│   │   └── routers/            the HTTP layer
│   └── manage.py               offline jobs (ingest / snapshot / grade)
└── fantasy-hunter-frontend/    Angular 20 SPA               → port 4300
    └── src/app/
        ├── core/               API client, models, shared UI helpers
        └── pages/              predictions · ticker · players · compare
                                · planner · chips · my-team · accuracy
```

Data flows one way: the FPL API is pulled into SQLite by a scheduled job, and
every request reads from SQLite — never from the origin API on the request path.
The one exception is `my-team`, which must fetch a manager's live picks.

**Ports:** the backend runs on **8420** and the frontend dev server on **4300**,
chosen to stay clear of 4200 and 4500. Port 8000 is blocked on this machine, so
avoid it. To change them, see [Changing ports](#changing-ports) below.

---

## Running it from fresh

Prerequisites: **Python 3.11+** and **Node 20.19+**. No Docker, no database
server, no cloud account.

### 1. Backend

```bash
cd fantasy-hunter-backend

python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"        # Windows
# source .venv/bin/activate && pip install -e ".[dev]" # macOS / Linux
```

Now pull the data. **Run this before starting the server** — the app is useless
against an empty database, and the prediction model needs the per-player
histories:

```bash
.venv/Scripts/python manage.py ingest --histories
```

`--histories` makes roughly 600 calls to the FPL API and takes about a minute.
It only needs to be a one-off; later refreshes can drop the flag. It creates
`fantasy_hunter.db` in the backend directory. Expect output like:

```
INFO app.ingest: bootstrap ingest: 653 rows
INFO app.ingest: fixtures ingest: 380 rows
INFO app.ingest: player history ingest: 2039 rows from 595 players
```

Start the API:

```bash
.venv/Scripts/python -m uvicorn app.main:app --reload --port 8420
```

Check it: <http://127.0.0.1:8420/api/health> should report `"status": "ok"` and a
non-zero player count. Interactive docs are at
<http://127.0.0.1:8420/docs>.

### 2. Frontend

In a **second terminal**:

```bash
cd fantasy-hunter-frontend
npm install
npm start
```

Open <http://localhost:4300>.

`npm start` runs `ng serve --port 4300` and proxies `/api` to `127.0.0.1:8420`
(see `proxy.conf.json`), so there is no CORS setup and no environment file to
switch. The backend must already be running or every page will show *"Cannot
reach the API."*

### 3. Verify

```bash
cd fantasy-hunter-backend
.venv/Scripts/python -m pytest tests -q      # 59 tests
```

In the browser, the quickest end-to-end check is **Predictions → click any
row**: the breakdown panel should expand with per-fixture component values.

---

## Day-to-day

### Keeping data current

```bash
cd fantasy-hunter-backend

.venv/Scripts/python manage.py ingest              # prices, news, fixtures — cheap, run often
.venv/Scripts/python manage.py ingest --histories  # + actual gameweek returns — after each GW finishes
```

### The accuracy loop

This is the product's differentiator, and it only works if run in order:

```bash
# BEFORE each gameweek deadline — records predictions, timestamped and immutable
.venv/Scripts/python manage.py snapshot --horizon 5

# AFTER the gameweek finishes — pull actual returns, then score against them
.venv/Scripts/python manage.py ingest --histories
.venv/Scripts/python manage.py grade
```

`snapshot` never overwrites an existing row: a published prediction stays exactly
as published. Results then appear on the **Accuracy** page.

### Other commands

```bash
.venv/Scripts/python manage.py predict 411          # one player's prediction breakdown
.venv/Scripts/python manage.py chips                # chip-timing schedule and gameweek outlook
.venv/Scripts/python manage.py defence              # club clean-sheet records, recency-weighted
.venv/Scripts/python manage.py lineups              # pre-season predicted-XI consensus index
```

Each of the last three also takes `--import <path>` to load a JSON bundle from
`fantasy-hunter-backend/data/` into the database:

```bash
.venv/Scripts/python manage.py chips   --import data/chip_timing_2026_27.json
.venv/Scripts/python manage.py defence --import data/team_defence.json
.venv/Scripts/python manage.py lineups --import data/predicted_lineups_gw1.json
```

---

## Changing ports

Three places, and they must agree:

| What | File | Current |
|---|---|---|
| Backend port | `--port` flag on the uvicorn command | `8420` |
| Frontend port | `fantasy-hunter-frontend/package.json` (`start` script) and `angular.json` (`serve.options.port`) | `4300` |
| Proxy target | `fantasy-hunter-frontend/proxy.conf.json` | `http://127.0.0.1:8420` |

`cors_origins` in `fantasy-hunter-backend/app/config.py` also lists the frontend
origin, but only matters if you call the API from a browser without the proxy.

---

## Troubleshooting

**"Cannot reach the API" on every page** — the backend is not running, or is on a
different port than `proxy.conf.json` targets.

**Every table is empty, `/api/health` shows 0 players** — ingestion has not run.
See step 1.

**Predictions look flat, or every club has a near-identical fixture ticker** —
expected before the season starts. FPL publishes team attack/defence strength as
zero until results exist, so the model falls back to the official per-fixture
difficulty. `components.fixture_model` reports which is in use, and the ticker
shows a banner. It corrects itself a few gameweeks in.

**My Team returns 409** — normal before a gameweek deadline has passed. FPL
only publishes a manager's picks afterwards; the UI explains this. The
optimiser's **Build a squad** mode needs no team ID and works regardless.

**The optimiser takes 30 seconds** — expected when scheduling three or more
chips; chip scheduling is the combinatorially hard part. The same plan with no
chips solves in under a second. Lower the *Solver budget* control or select
fewer chips.

**Port already in use** — see [Changing ports](#changing-ports).

---

## Component documentation

- [`fantasy-hunter-backend/README.md`](fantasy-hunter-backend/README.md) —
  endpoint reference, the prediction model, the optimiser formulation, and the
  known limits of each.
- [`fantasy-hunter-frontend/README.md`](fantasy-hunter-frontend/README.md) —
  page map, frontend conventions, and the deliberate empty states.
- [`plan.md`](plan.md) — product strategy, competitive landscape, and the target
  cloud architecture.
