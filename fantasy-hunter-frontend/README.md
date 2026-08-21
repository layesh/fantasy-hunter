# Fantasy Hunter — frontend

Angular 20 SPA (standalone components, signals, lazy-loaded routes). Mobile-first,
because matchday usage is overwhelmingly phone-on-mobile-data.

> Setting up for the first time? Start from the [root README](../README.md) — it
> covers the backend and frontend end to end. This file is the frontend
> reference.

## Run it

The backend must already be running on port 8420, otherwise every page shows
*"Cannot reach the API."* See [`../fantasy-hunter-backend/README.md`](../fantasy-hunter-backend/README.md).

```bash
npm install
npm start          # http://localhost:4300
```

`npm start` runs `ng serve --port 4300` — 4200 and 4500 are in use on this
machine — and proxies `/api` to `http://127.0.0.1:8420` via `proxy.conf.json`,
so there is no CORS setup and no environment file to switch.

To change the port, update both `package.json` (`start` script) and
`angular.json` (`serve.options.port`); to point at a different backend, update
`proxy.conf.json`.

## Pages

| Route | What it is |
|---|---|
| `/predictions` | Predicted points table. Tap any row to expand the full component breakdown — the "show your work" view |
| `/ticker` | Fixture ticker, clubs × gameweeks, rankable by attacking returns or clean sheets |
| `/players` | Player database — search, filters, sortable columns, multi-select to compare |
| `/compare` | 2–4 players side by side with winner highlighting and upcoming fixtures |
| `/planner` | Optimiser: build the best squad for a budget, or plan transfers and chips across gameweeks |
| `/my-team` | Enter an FPL entry ID: squad rating, best XI, captain options, ranked transfers |
| `/accuracy` | The public graded accuracy record |

## Conventions

- **Standalone components with inline templates.** Each page is one `.ts` file;
  shared visual primitives (tables, chips, difficulty cells, notices) live as
  global classes in `src/styles.scss` rather than being duplicated per page.
- **Signals for state**, `HttpClient` for I/O. No state-management library —
  NgRx is not warranted at this size.
- **Colour is load-bearing.** Green is always good and red always bad, on both
  the difficulty scale (1 hardest → 5 easiest) and our own ratings, which run
  the opposite way and are flipped in `core/ui.ts` before colouring.
- **Wide tables scroll inside `.table-wrap`**, never the page body. The player
  name column stays pinned via `.sticky-col`.

## Honest empty states

Three states are normal rather than broken, and the UI says so instead of
showing an error:

- **My Team before the first deadline** — FPL does not publish a manager's picks
  until a deadline passes, so the API returns 409 and the page explains it. The
  optimiser's plan mode hits the same wall, and points the user at *Build a
  squad* — which needs no team ID and works today. A built squad can then be
  handed straight to the planner, which doubles as the wildcard-draft workflow.
- **Accuracy with nothing graded** — the page says there is no data rather than
  inventing a number, which is the entire point of publishing a record.
- **Pre-season stats** — FPL carries last season's points and minutes forward
  until matches are played, so the player database labels them as such.

## Not built yet

- No PWA/service worker (planned before launch, per the project plan).
- No charts — the comparison page is tabular for now.
- No auth or subscription tiers; the whole app is open locally.
