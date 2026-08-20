# FPL Analytics Platform — Development Plan

**Owner:** Md Khairul Bashar
**Prepared for:** Frontend / Backend / DevOps team
**Goal:** Launch a world-class FPL (Fantasy Premier League) analytics SaaS that matches or beats existing players in the market, then iterate toward category leadership.

---

## 1. Why this document exists

We are not building a personal tool anymore — we are building a product to compete in an existing, crowded market. This doc defines: who we're competing against, what "table stakes" features we must ship to not look inferior, where we can differentiate, the technical architecture, the stack, and how the team should be organized to build it fast without cutting corners that will hurt us later (security, data correctness, scalability).

---

## 2. Competitive landscape (as of Aug 2026)

| Competitor | Positioning | Pricing | Strength | Weakness |
|---|---|---|---|---|
| **Fantasy Football Hub** (the reference site) | Creator-led, AI transfers, expert team reveals, community | Starter £11.99/mo · Pro £14.99/mo · Ultra £67/mo | Strong creator/community presence | iOS 3.9★ only, confusing tier ladder, no published accuracy |
| **Fantasy Football Scout** | Editorial + stats heavyweight, 15+ yrs trust, Opta data | Chief Scout £10/mo or £50/yr | Editorial depth, community, brand trust | No public API, accuracy asserted not proven |
| **Fantasy Football Fix** | Long-standing planner + Chrome extension + AI chat (ChatFPL) | Premium £6.95/mo · Lifetime £295 | Mature planning tools, real free Chrome extension | Android rated 2.5★ vs iOS 4.5★ — reliability issues |
| **FPL Review** | Analyst tool — predicted points feeding a MILP transfer solver | Patreon-gated | Most respected optimizer among serious players | Accuracy study dates to Feb 2023, dated |
| **FPL Pulse** | New entrant — live tracking + AI assistant + Monte-Carlo mini-league sim | Pro £2.99/mo | Cheapest tier, modern feature set | Brand new, no track record |
| **Onside Arena** | New entrant, publishes graded/public accuracy record, public REST + MCP server for LLMs | Pro £4.99/mo · Pro+ £9.99/mo | Only one with a *public, graded* accuracy record; deliberately avoids duplicating free official-app features | Newest brand, smallest community |

**Important market shift to design around:** the official FPL app itself now ships (free) — live points, live overall rank, live mini-league tables, projected bonus points, a price-change predictor refreshed every 15 minutes, career rank percentiles, inline fixture difficulty/ownership, and a watchlist. **Do not build your differentiation on any of these** — you'll be competing with a free first-party feature and will always lose on data freshness (they have the transfer data, we don't). Build on what the league will never ship:

- Independent, transparent, *graded* predicted-points models
- Multi-gameweek transfer/chip optimization (solver-based, not just heuristic)
- Personalized recommendations tied to the user's actual squad
- Community/editorial trust signals (if we go that route)
- Programmatic access (REST API / MCP server) — currently only Onside Arena offers this, and it's a differentiator with near-zero competition

## 3. Product strategy

**Positioning:** "The FPL tool that shows its work." Most competitors assert accuracy; almost none publish it. A public, gameweek-by-gameweek graded prediction record (à la Onside Arena) combined with Fantasy Football Hub-style personalized AI transfer suggestions is a gap: nobody currently combines *proven accuracy transparency* with *strong personalization + community*.

**MVP philosophy:** Ship the things users actually pay for (predictions, optimizer, personalized "my team" dashboard) before community/editorial features, which take longer to build trust and don't differentiate technically.

---

## 4. Feature scope

### Phase 1 — MVP (parity essentials, ~6–8 weeks)
- FPL account linking (team ID input, no OAuth needed — FPL doesn't provide one; pull public entry data)
- Player database with live prices, ownership, form, fixtures (from official API)
- Predicted points model (gameweek + multi-GW) — start heuristic, iterate to ML
- "My Team" dashboard: current squad, predicted points, suggested transfers
- Squad optimizer (MILP-based transfer/chip solver — budget, formation, 3-per-club constraints)
- Fixture difficulty analyser
- Player comparison tool
- Responsive web app (mobile-first, since most usage is matchday-mobile)

### Phase 2 — Competitive parity (~4–6 weeks)
- Multi-gameweek chip planner (wildcard, bench boost, triple captain, free hit)
- Price change prediction (transfer-trend based — we won't beat the official app's real transfer-data feed, so message this honestly, don't oversell it)
- Mini-league tracking & simple league simulation
- Team/expert reveals or content feed (if going the editorial/community route)
- Public accuracy/calibration page — publish predictions before deadline, grade after

### Phase 3 — Differentiation (ongoing after launch)
- Public REST API + MCP server (huge, mostly-uncontested opportunity — lets ChatGPT/Claude/Perplexity query your model directly)
- Effective ownership & rank-threat analysis
- What-if simulator (goals/assists/bench impact on rank)
- Community features (Discord/WhatsApp integration, similar to Hub)
- Personalized weekly briefing (email/push) per user's squad

---

## 5. Architecture overview

Single-tenant-per-request SaaS, read-heavy, bursty traffic (huge spikes near gameweek deadlines and during live matches). Design for:
- **Cache aggressively** — FPL bootstrap data changes at most every few minutes even in-play; there's no reason to hit the origin API per-request.
- **Decouple prediction/optimization compute from the request path** — these are CPU-heavy; run as scheduled/async jobs, serve pre-computed results.
- **Horizontal scalability for the API tier**, since matchday traffic is the make-or-break moment for user trust.

```
                     ┌────────────────────┐
                     │  Official FPL API   │  (bootstrap-static, fixtures,
                     │  (source of truth)  │   entry, picks, live)
                     └─────────┬───────────┘
                               │ scheduled pull (EventBridge/cron)
                     ┌─────────▼───────────┐
                     │  Ingestion Service    │  (Go or Python)
                     │  normalizes + stores  │
                     └─────────┬───────────┘
                               │
                     ┌─────────▼───────────┐
                     │   PostgreSQL (RDS)   │  players, fixtures, gw history,
                     │   + Redis (cache)    │  user squads, predictions cache
                     └─────────┬───────────┘
              ┌────────────────┼─────────────────┐
   ┌──────────▼─────────┐ ┌────▼─────────┐ ┌──────▼───────────┐
   │ Prediction/ML jobs  │ │  Core API     │ │  Optimizer Service │
   │ (Python, batch)     │ │  (Spring Boot │ │  (Python + PuLP/   │
   │ scikit-learn/xgboost│ │   or Go)      │ │   OR-Tools MILP)    │
   └──────────────────────┘ └──────┬───────┘ └────────────────────┘
                                    │ REST/JSON
                          ┌─────────▼───────────┐
                          │   Angular SPA (web)   │
                          │   + PWA for mobile     │
                          └───────────────────────┘
```

---

## 6. Tech stack

Chosen to match the team's existing strengths (Java/Spring Boot, Go, Python, Angular, AWS, Kafka/event-driven, microservices) while keeping the initial build lean — start closer to a modular monolith and only split into services where it genuinely pays off (ML/optimizer workloads are the clear case for separation).

**Backend — core API**
- Spring Boot (Java) for the core domain API (users, squads, subscriptions, auth) — matches team's deepest expertise, strong for a product with real business logic and eventual payment/subscription complexity
- Go for the high-throughput/low-latency pieces: live gameweek score polling, ingestion service — Go's concurrency model is a natural fit for polling many endpoints on a schedule under load

**Data science / ML**
- Python (FastAPI as an internal service) for the prediction models and the optimizer
  - pandas / scikit-learn / xgboost for predicted points
  - PuLP or Google OR-Tools for the MILP squad/transfer optimizer (this is what FPL Review's respected solver is built on conceptually)
- Keep this as its own deployable service — different scaling profile and release cadence from the core API

**Frontend**
- Angular (team's existing strength), mobile-first responsive design
- Consider a PWA wrapper before investing in native iOS/Android — competitor app-store ratings (FF Fix: 4.5★ iOS vs 2.5★ Android) show native mobile reliability is a real risk; a well-built PWA sidesteps store-approval and dual-codebase overhead for launch
- Chart.js or ngx-charts for stats visualizations

**Data & messaging**
- PostgreSQL (AWS RDS) — primary store
- Redis (ElastiCache) — cache layer for bootstrap-static/fixtures and computed predictions, critical for surviving deadline/matchday traffic spikes
- Kafka or AWS EventBridge for event-driven pieces (gameweek-finalized events triggering recompute of predictions, price-change jobs, notification dispatch) — matches team's existing event-driven experience, use where it earns its complexity (not everywhere)

**Infra / DevOps**
- AWS: ECS/Fargate (simpler ops than EKS at this stage) or EKS if the team wants to standardize on Kubernetes for future scale
- Terraform for infra-as-code from day one
- CloudFront + S3 for the Angular static build
- RDS Postgres (Multi-AZ once revenue justifies it), ElastiCache Redis
- CI/CD: GitHub Actions → build/test/deploy per service
- Observability: CloudWatch + (consider Grafana/Prometheus if the team wants deeper metrics) — matchday is when things break, so alerting on latency/error-rate spikes is non-negotiable before launch
- Secrets: AWS Secrets Manager

**Auth & payments (needed once this is a real SaaS, not personal-use)**
- Auth: standard email/password + OAuth (Google) via a managed auth provider (Cognito, or Auth0/Clerk if the team wants faster setup) rather than hand-rolling
- Payments/subscriptions: Stripe (Billing) — handles tiered plans (Starter/Pro/Ultra-style), proration, and dunning out of the box

---

## 7. Team structure & responsibilities

**Backend (2 engineers suggested)**
- Core domain API (Spring Boot): users, auth, subscriptions, squad linking, orchestrating calls to ML/optimizer services
- Ingestion service (Go): scheduled pulls from FPL API, live-gameweek polling during matches, data normalization into Postgres
- Own: API contracts, data model, rate-limit/backoff handling against the official FPL API (it's unofficial/undocumented — be defensive, add circuit breakers)

**Data/ML (1 engineer, can overlap with backend)**
- Predicted points model, retrained/recalibrated weekly
- MILP optimizer service (transfer suggestions, chip planning)
- Owns the public accuracy/calibration tracking — this is a core product differentiator, treat it as a first-class deliverable, not an afterthought

**Frontend (1–2 engineers)**
- Angular SPA: My Team dashboard, player comparison, fixture analyser, optimizer UI
- PWA setup, mobile-first responsive layouts
- Own: state management approach (NgRx if complexity warrants it), performance budget (matchday users are on mobile data — keep bundle size and API payloads lean)

**DevOps (1 engineer, can be fractional/shared)**
- Terraform modules for all AWS infra
- CI/CD pipelines per service
- Observability/alerting, especially matchday load handling
- Cost monitoring (RDS/ElastiCache/Fargate can run away quickly with bursty traffic — set up autoscaling with sane floors/ceilings)

---

## 8. Non-functional requirements

- **Deadline-hour resilience:** traffic spikes hardest in the ~30 minutes before each gameweek deadline and during live matches. Load-test against this specifically before launch.
- **FPL API defensiveness:** it's an unofficial, undocumented API — implement caching, retries with backoff, and graceful degradation (serve last-known-good cached data) if it's slow or down.
- **Data correctness over cleverness:** a wrong predicted-points number that leads a user to a bad transfer is a trust-breaking event in this category — validate model outputs before publishing weekly.
- **Transparency as a feature:** build the public accuracy/calibration tracking into the data model from day one (store every prediction with a timestamp, compare against actuals post-gameweek) — retrofitting this later loses the "graded from day one" credibility angle.

---

## 9. Suggested roadmap

| Phase | Duration | Outcome |
|---|---|---|
| Phase 1 — MVP | 6–8 weeks | Core dashboard, predictions, optimizer, live on a small user group |
| Phase 2 — Parity | 4–6 weeks | Chip planner, mini-leagues, price predictions, public accuracy page |
| Public launch | — | Marketing push once accuracy record has at least a few graded gameweeks — don't launch the "graded accuracy" pitch with zero data points |
| Phase 3 — Differentiation | Ongoing | Public API/MCP server, community features, personalization |

---

## 10. Open decisions for the team to make early

- Modular monolith vs. microservices from day one — recommendation above is to start modular (core API + ingestion + ML/optimizer as 3 services) rather than full microservices; split further only when a specific service's scaling needs diverge.
- Native mobile app vs. PWA-first — recommendation is PWA-first given competitor app-store reliability problems and the cost of maintaining two native codebases pre-revenue.
- Editorial/community content — decide whether this is in scope for v1 or a later differentiator; it's a significant ongoing content-ops cost, not just an engineering one.
