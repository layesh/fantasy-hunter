import {
  Component,
  DestroyRef,
  WritableSignal,
  computed,
  inject,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { Subject, of } from 'rxjs';
import { catchError, debounceTime, distinctUntilChanged, map, switchMap } from 'rxjs/operators';

import { Api } from '../../core/api';
import { Chip, GameweekPlan, OptimisationResult, Player, SquadPick } from '../../core/models';
import { errorMessage } from '../../core/ui';

const STORAGE_KEY = 'fh.entry-id';
const SQUAD_SIZE = 15;

const CHIP_LABELS: Record<Chip, string> = {
  wildcard: 'Wildcard',
  bench_boost: 'Bench Boost',
  triple_captain: 'Triple Captain',
  free_hit: 'Free Hit',
};

type Mode = 'build' | 'plan';

@Component({
  selector: 'app-planner',
  imports: [FormsModule],
  template: `
    <div class="page">
      <div class="page-head">
        <h1>Optimiser</h1>
        <p>
          A real integer program, not a greedy shortlist. It respects the budget, the squad shape,
          the three-per-club rule, free transfers and the four-point cost of a hit — and it picks
          the gameweek to play each chip.
        </p>
      </div>

      <div class="chips mode-switch">
        <button type="button" class="chip" [class.active]="mode() === 'build'" (click)="setMode('build')">
          Build a squad
        </button>
        <button type="button" class="chip" [class.active]="mode() === 'plan'" (click)="setMode('plan')">
          Plan transfers &amp; chips
        </button>
      </div>

      @if (mode() === 'build') {
        <div class="controls">
          <div class="field">
            <label for="opt-budget">Budget</label>
            <select id="opt-budget" [ngModel]="budget()" (ngModelChange)="budget.set(+$event)">
              @for (b of budgets; track b) {
                <option [value]="b">{{ b.toFixed(1) }}m</option>
              }
            </select>
          </div>
          <div class="field">
            <label for="opt-horizon">Gameweeks</label>
            <select id="opt-horizon" [ngModel]="horizon()" (ngModelChange)="horizon.set(+$event)">
              @for (n of horizons; track n) {
                <option [value]="n">Next {{ n }}</option>
              }
            </select>
          </div>
          <div class="field">
            <label for="opt-start">Starter confidence</label>
            <select
              id="opt-start"
              [ngModel]="minStart()"
              (ngModelChange)="minStart.set(+$event)"
              title="Bar players that fewer than this share of predicted-XI sources expect to start"
            >
              @for (option of startThresholds; track option.value) {
                <option [value]="option.value">{{ option.label }}</option>
              }
            </select>
          </div>
          <button class="primary" type="button" [disabled]="loading()" (click)="buildSquad()">
            {{ loading() ? 'Solving…' : 'Optimise squad' }}
          </button>
        </div>

        <div class="pickers">
          <div class="field picker">
            <label for="opt-lock">Lock in <span class="muted small">— always picked</span></label>
            <div class="token-box">
              @for (player of locked(); track player.id) {
                <span class="token lock">
                  {{ player.web_name }}
                  <button type="button" (click)="removeLock(player.id)" [attr.aria-label]="'Remove ' + player.web_name">×</button>
                </span>
              }
              <input
                id="opt-lock"
                type="text"
                autocomplete="off"
                [placeholder]="locked().length ? 'Add another…' : 'Search players…'"
                [ngModel]="lockQuery()"
                (ngModelChange)="onLockQuery($event)"
              />
            </div>
            @if (lockResults().length) {
              <ul class="suggestions">
                @for (player of lockResults(); track player.id) {
                  <li>
                    <button type="button" (click)="addLock(player)">
                      <strong>{{ player.web_name }}</strong>
                      <span class="muted small">
                        {{ player.team_short_name }} · {{ player.position }} ·
                        {{ player.price.toFixed(1) }}m
                      </span>
                    </button>
                  </li>
                }
              </ul>
            }
          </div>

          <div class="field picker">
            <label for="opt-exclude">Exclude <span class="muted small">— never picked</span></label>
            <div class="token-box">
              @for (player of excluded(); track player.id) {
                <span class="token exclude">
                  {{ player.web_name }}
                  <button type="button" (click)="removeExclude(player.id)" [attr.aria-label]="'Remove ' + player.web_name">×</button>
                </span>
              }
              <input
                id="opt-exclude"
                type="text"
                autocomplete="off"
                [placeholder]="excluded().length ? 'Add another…' : 'Search players…'"
                [ngModel]="excludeQuery()"
                (ngModelChange)="onExcludeQuery($event)"
              />
            </div>
            @if (excludeResults().length) {
              <ul class="suggestions">
                @for (player of excludeResults(); track player.id) {
                  <li>
                    <button type="button" (click)="addExclude(player)">
                      <strong>{{ player.web_name }}</strong>
                      <span class="muted small">
                        {{ player.team_short_name }} · {{ player.position }} ·
                        {{ player.price.toFixed(1) }}m
                      </span>
                    </button>
                  </li>
                }
              </ul>
            }
          </div>
        </div>

        @if (lockedCost() > 0) {
          <p class="small muted lock-summary">
            {{ locked().length }} locked, costing {{ (lockedCost() / 10).toFixed(1) }}m of your
            {{ budget().toFixed(1) }}m — {{ (budget() - lockedCost() / 10).toFixed(1) }}m left for the
            other {{ 15 - locked().length }}.
          </p>
        }
      } @else {
        <div class="controls">
          <div class="field">
            <label for="plan-entry">FPL team ID</label>
            <input
              id="plan-entry"
              type="text"
              inputmode="numeric"
              placeholder="e.g. 1234567"
              [ngModel]="entryId()"
              (ngModelChange)="entryId.set($event)"
            />
          </div>
          <div class="field">
            <label for="plan-horizon">Gameweeks</label>
            <select id="plan-horizon" [ngModel]="horizon()" (ngModelChange)="horizon.set(+$event)">
              @for (n of planHorizons; track n) {
                <option [value]="n">Next {{ n }}</option>
              }
            </select>
          </div>
          <div class="field">
            <label for="plan-time">Solver budget</label>
            <select id="plan-time" [ngModel]="timeLimit()" (ngModelChange)="timeLimit.set(+$event)">
              @for (t of timeLimits; track t) {
                <option [value]="t">{{ t }}s</option>
              }
            </select>
          </div>
          <div class="field">
            <label>Chips available</label>
            <div class="chips">
              @for (chip of allChips; track chip) {
                <button
                  type="button"
                  class="chip"
                  [class.active]="isChipOn(chip)"
                  (click)="toggleChip(chip)"
                >
                  {{ chipLabel(chip) }}
                </button>
              }
            </div>
          </div>
          <button
            class="primary"
            type="button"
            [disabled]="loading() || !entryId()"
            (click)="planTransfers()"
          >
            {{ loading() ? 'Solving…' : 'Plan' }}
          </button>
        </div>

        @if (selectedChips().length >= 3) {
          <p class="small muted warn-inline">
            Scheduling three or more chips is the expensive case — expect the solver to use its
            full budget.
          </p>
        }
      }

      @if (error(); as message) {
        <div class="notice" [class.warn]="preSeason()" [class.error]="!preSeason()">
          @if (preSeason()) {
            <strong>Your squad is not public yet.</strong>
            <p>
              FPL only publishes picks after a gameweek deadline has passed. Until then, use
              <strong>Build a squad</strong> — it needs no team ID.
            </p>
          } @else {
            {{ message }}
            @if (mode() === 'build' && (locked().length || excluded().length)) {
              <p class="small">
                Your locks and exclusions may be the cause. Locking
                {{ locked().length }} player{{ locked().length === 1 ? '' : 's' }} costs
                {{ (lockedCost() / 10).toFixed(1) }}m and leaves
                {{ (budget() - lockedCost() / 10).toFixed(1) }}m for the other
                {{ 15 - locked().length }} — and no more than three players may come from one club.
              </p>
            }
          }
        </div>
      } @else if (loading()) {
        <div class="notice">
          <span class="spinner"></span>
          Solving the integer program… this can take up to {{ timeLimit() }} seconds.
        </div>
      } @else if (result(); as data) {
        <div class="grid cols-3 summary">
          <div class="card">
            <span class="muted small">
              {{ mode() === 'build' ? 'Expected points' : 'Net expected points' }}
            </span>
            <p class="big mono">{{ data.expected_points.toFixed(1) }}</p>
            <p class="small muted note">
              Over GW{{ data.events[0] }}–{{ data.events[data.events.length - 1] }}
              @if (data.points_spent_on_hits > 0) {
                · after {{ data.points_spent_on_hits.toFixed(0) }} points of hits
              }
            </p>
          </div>
          <div class="card">
            <span class="muted small">Squad cost</span>
            <p class="big mono">{{ (squadCost(data.squad) / 10).toFixed(1) }}<span class="unit">m</span></p>
            <p class="small muted note">{{ data.squad.length }} players</p>
          </div>
          <div class="card">
            <span class="muted small">Chips scheduled</span>
            <p class="big mono">{{ chipsUsed(data).length || '—' }}</p>
            <p class="small muted note">
              {{ chipsUsed(data).length ? chipsUsed(data).join(' · ') : 'None played in this range' }}
            </p>
          </div>
        </div>

        <h2 class="section">Squad</h2>
        @if (hasStartData(data)) {
          <p class="small muted legend">
            The badge is the share of pre-season predicted-XI sources that start each player —
            <span class="start-badge start-high">80%+</span> nailed,
            <span class="start-badge start-mid">50–79%</span> contested,
            <span class="start-badge start-low">under 50%</span> a bench risk. No badge means no
            source covers them, which is unknown rather than bad. Pre-season only: once real minutes
            exist this disappears.
          </p>
        }
        <div class="squad">
          @for (group of grouped(data.squad); track group.position) {
            <div class="group">
              <span class="pos pos-{{ group.position }}">{{ group.position }}</span>
              <div class="group-players">
                @for (pick of group.players; track pick.player_id) {
                  <div class="card pick">
                    <div class="pick-head">
                      <strong>{{ pick.web_name }}</strong>
                      <span class="badges">
                        @for (duty of setPieces(pick); track duty.label) {
                          <span class="sp-badge" [title]="duty.title">{{ duty.label }}</span>
                        }
                        @if (pick.start_probability !== null) {
                          <span
                            class="start-badge {{ startClass(pick.start_probability) }}"
                            [title]="startTitle(pick.start_probability)"
                            >{{ startLabel(pick.start_probability) }}</span
                          >
                        }
                      </span>
                    </div>
                    <span class="small muted"
                      >{{ pick.team_short_name }} · {{ (pick.cost / 10).toFixed(1) }}m</span
                    >
                    <span class="mono xpts">{{ pick.expected_points.toFixed(1) }}</span>
                  </div>
                }
              </div>
            </div>
          }
        </div>

        @if (mode() === 'build') {
          <div class="handoff">
            <div>
              <strong>Plan forward from this squad</strong>
              <p class="small muted">
                Treat this as your wildcard draft and let the planner schedule transfers and chips
                from here. No FPL team ID needed.
              </p>
            </div>
            <div class="handoff-controls">
              <div class="chips">
                @for (chip of allChips; track chip) {
                  <button
                    type="button"
                    class="chip"
                    [class.active]="isChipOn(chip)"
                    (click)="toggleChip(chip)"
                  >
                    {{ chipLabel(chip) }}
                  </button>
                }
              </div>
              <button type="button" [disabled]="loading()" (click)="planFromBuiltSquad(data)">
                Plan from this squad
              </button>
            </div>
          </div>
        }

        <h2 class="section">Gameweek by gameweek</h2>
        <div class="timeline">
          @for (week of data.gameweeks; track week.event_id) {
            <div class="card week">
              <div class="week-head">
                <div>
                  <strong>GW{{ week.event_id }}</strong>
                  @if (week.chip) {
                    <span class="chip-badge">{{ chipLabel(week.chip) }}</span>
                  }
                </div>
                <span class="mono week-points">{{ week.expected_points.toFixed(1) }} xPts</span>
              </div>

              <div class="week-meta small muted">
                @if (mode() === 'plan') {
                  <span>{{ week.free_transfers_available }} FT banked</span>
                  <span>{{ (week.bank / 10).toFixed(1) }}m in the bank</span>
                  @if (week.hits > 0) {
                    <span class="cost"
                      >{{ week.hits }} hit{{ week.hits > 1 ? 's' : '' }} (−{{
                        week.points_cost.toFixed(0)
                      }})</span
                    >
                  }
                }
                @if (week.captain) {
                  <span>Captain: {{ week.captain.web_name }}</span>
                }
              </div>

              @if (week.transfers_in.length) {
                <div class="moves">
                  @for (move of pairs(week); track $index) {
                    <div class="move">
                      <span class="out">{{ move.out }}</span>
                      <span class="arrow">→</span>
                      <span class="in">{{ move.in }}</span>
                    </div>
                  }
                </div>
              } @else if (mode() === 'plan') {
                <p class="small muted no-moves">No transfer — roll the free transfer.</p>
              }
            </div>
          }
        </div>

        <div class="notice caveats">
          <strong>What this assumes</strong>
          <ul>
            @for (note of data.notes; track note) {
              <li>{{ note }}</li>
            }
            <li>
              Bench points are discounted, since a benched player only scores when someone ahead
              of them does not play — except under a Bench Boost, where they count in full.
            </li>
          </ul>
        </div>
      }
    </div>
  `,
  styles: `
    .mode-switch {
      margin-bottom: 0.9rem;
    }

    .warn-inline {
      margin: -0.4rem 0 0.8rem;
    }

    .summary {
      margin-bottom: 0.6rem;
    }

    .big {
      font-size: 1.9rem;
      font-weight: 700;
      margin: 0.2rem 0 0;
      color: var(--accent);
    }

    .unit {
      font-size: 0.85rem;
      color: var(--muted);
      font-weight: 500;
    }

    .note {
      margin: 0.4rem 0 0;
    }

    h2.section {
      margin: 1.2rem 0 0.5rem;
    }

    .squad {
      display: flex;
      flex-direction: column;
      gap: 0.55rem;
    }

    .group {
      display: flex;
      align-items: flex-start;
      gap: 0.5rem;
    }

    .group > .pos {
      margin-top: 0.5rem;
    }

    .group-players {
      display: grid;
      gap: 0.4rem;
      grid-template-columns: repeat(auto-fill, minmax(132px, 1fr));
      flex: 1;
    }

    .pick {
      display: flex;
      flex-direction: column;
      gap: 0.1rem;
      padding: 0.5rem;
    }

    .pick .xpts {
      color: var(--accent);
      font-weight: 700;
    }

    .pickers {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 0.75rem;
      margin-top: 0.75rem;
      max-width: 720px;
    }

    /* The suggestion list is absolutely positioned, so the picker anchors it. */
    .picker {
      position: relative;
    }

    .token-box {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 0.3rem;
      padding: 0.3rem;
      min-height: 2.3rem;
      border: 1px solid var(--border);
      border-radius: var(--radius);
      background: var(--surface, #16181d);
    }

    .token-box input {
      flex: 1 1 7rem;
      min-width: 6rem;
      border: 0;
      outline: 0;
      background: transparent;
      color: inherit;
      padding: 0.15rem 0.2rem;
      font: inherit;
    }

    .token {
      display: inline-flex;
      align-items: center;
      gap: 0.25rem;
      padding: 0.1rem 0.2rem 0.1rem 0.45rem;
      border-radius: 999px;
      font-size: 0.78rem;
      font-weight: 600;
      white-space: nowrap;
    }

    .token.lock {
      color: #0b3d2c;
      background: var(--good, #24c58a);
    }

    .token.exclude {
      color: #fff;
      background: var(--bad, #d2544b);
    }

    .token button {
      border: 0;
      background: transparent;
      color: inherit;
      cursor: pointer;
      font-size: 1rem;
      line-height: 1;
      padding: 0 0.2rem;
      opacity: 0.75;
    }

    .token button:hover {
      opacity: 1;
    }

    .suggestions {
      position: absolute;
      z-index: 20;
      top: 100%;
      left: 0;
      right: 0;
      margin: 0.2rem 0 0;
      padding: 0.2rem;
      list-style: none;
      border: 1px solid var(--border);
      border-radius: var(--radius);
      background: var(--surface, #16181d);
      box-shadow: 0 8px 24px rgb(0 0 0 / 45%);
      max-height: 15rem;
      overflow-y: auto;
    }

    .suggestions button {
      display: flex;
      flex-direction: column;
      gap: 0.05rem;
      width: 100%;
      text-align: left;
      border: 0;
      background: transparent;
      color: inherit;
      cursor: pointer;
      padding: 0.35rem 0.45rem;
      border-radius: calc(var(--radius) - 2px);
      font: inherit;
    }

    .suggestions button:hover,
    .suggestions button:focus-visible {
      background: var(--border);
    }

    .lock-summary {
      margin-top: 0.5rem;
    }

    .legend {
      margin: -0.4rem 0 0.6rem;
      line-height: 1.7;
    }

    .legend .start-badge {
      margin: 0 0.1rem;
    }

    .pick-head {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 0.4rem;
    }

    /* Starter confidence. Green/amber/red matches the difficulty scale used
       elsewhere: green is always good, red always bad. */
    .badges {
      display: inline-flex;
      align-items: baseline;
      gap: 0.2rem;
      flex-shrink: 0;
    }

    /* Set-piece duty is a fact about role, not a rating, so it stays neutral
       rather than borrowing the good/bad colour scale. */
    .sp-badge {
      font-size: 0.6rem;
      font-weight: 700;
      letter-spacing: 0.04em;
      padding: 0.08rem 0.25rem;
      border-radius: 3px;
      border: 1px solid var(--border);
      color: var(--muted, #9aa3ae);
      background: transparent;
    }

    .start-badge {
      flex-shrink: 0;
      font-size: 0.68rem;
      font-weight: 700;
      letter-spacing: 0.02em;
      padding: 0.05rem 0.3rem;
      border-radius: 999px;
      border: 1px solid transparent;
    }

    .start-high {
      color: #0b3d2c;
      background: var(--good, #24c58a);
    }

    .start-mid {
      color: #3d2f05;
      background: var(--warn, #e8c15a);
    }

    .start-low {
      color: #fff;
      background: var(--bad, #d2544b);
    }

    .handoff {
      display: flex;
      flex-wrap: wrap;
      gap: 0.75rem;
      justify-content: space-between;
      align-items: center;
      margin-top: 1.1rem;
      padding: 0.85rem;
      border: 1px dashed var(--border);
      border-radius: var(--radius);
      background: var(--surface);
    }

    .handoff p {
      margin: 0.2rem 0 0;
      max-width: 46ch;
    }

    .handoff-controls {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 0.5rem;
    }

    .timeline {
      display: grid;
      gap: 0.6rem;
      grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
    }

    .week-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 0.5rem;
      margin-bottom: 0.4rem;
    }

    .week-points {
      color: var(--accent);
      font-weight: 700;
    }

    .chip-badge {
      margin-left: 0.4rem;
      padding: 0.12rem 0.45rem;
      border-radius: 999px;
      background: var(--accent);
      color: #04150f;
      font-size: 0.68rem;
      font-weight: 800;
      text-transform: uppercase;
      letter-spacing: 0.03em;
    }

    .week-meta {
      display: flex;
      flex-wrap: wrap;
      gap: 0.15rem 0.7rem;
      margin-bottom: 0.5rem;
    }

    .week-meta .cost {
      color: var(--danger);
    }

    .moves {
      display: flex;
      flex-direction: column;
      gap: 0.25rem;
      border-top: 1px solid var(--border);
      padding-top: 0.45rem;
    }

    .move {
      display: flex;
      align-items: center;
      gap: 0.4rem;
      font-size: 0.85rem;
    }

    .move .out {
      color: var(--danger);
    }

    .move .in {
      color: var(--accent);
      font-weight: 600;
    }

    .move .arrow {
      color: var(--muted);
    }

    .no-moves {
      margin: 0.45rem 0 0;
      border-top: 1px solid var(--border);
      padding-top: 0.45rem;
    }

    .caveats {
      margin-top: 1.2rem;
    }

    .caveats ul {
      margin: 0.4rem 0 0;
      padding-left: 1.1rem;
    }

    .caveats li {
      margin: 0.25rem 0;
    }
  `,
})
export class PlannerPage {
  private readonly api = inject(Api);
  private readonly destroyRef = inject(DestroyRef);

  protected readonly allChips: Chip[] = [
    'wildcard',
    'bench_boost',
    'triple_captain',
    'free_hit',
  ];
  protected readonly budgets = [95, 96, 97, 98, 99, 100, 101, 102, 103, 105];
  protected readonly horizons = [1, 2, 3, 5, 6, 8];
  protected readonly planHorizons = [2, 3, 4, 5, 6];
  protected readonly timeLimits = [10, 20, 30, 60, 120];

  protected readonly mode = signal<Mode>('build');
  protected readonly budget = signal(100);
  /**
   * Minimum share of predicted-XI sources that must start a player. Pre-season
   * only: before a ball is kicked the model has no minutes evidence, so without
   * this it will spend cheap slots on players nobody expects to play.
   */
  protected readonly minStart = signal(0);
  protected readonly startThresholds = [
    { value: 0, label: 'Any' },
    { value: 0.5, label: 'Half of sources' },
    { value: 0.75, label: 'Most sources' },
    { value: 1, label: 'Unanimous' },
  ];

  /** Players forced into / barred from the squad. */
  protected readonly locked = signal<Player[]>([]);
  protected readonly excluded = signal<Player[]>([]);
  protected readonly lockQuery = signal('');
  protected readonly excludeQuery = signal('');
  protected readonly lockResults = signal<Player[]>([]);
  protected readonly excludeResults = signal<Player[]>([]);

  private readonly lockTerms = new Subject<string>();
  private readonly excludeTerms = new Subject<string>();

  /** Budget already committed to locked players, in tenths of a million. */
  protected readonly lockedCost = computed(() =>
    this.locked().reduce((sum, player) => sum + player.now_cost, 0),
  );
  protected readonly horizon = signal(5);
  protected readonly timeLimit = signal(30);
  protected readonly entryId = signal('');
  protected readonly selectedChips = signal<Chip[]>([]);

  protected readonly result = signal<OptimisationResult | null>(null);
  protected readonly loading = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly preSeason = signal(false);

  constructor() {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) {
      this.entryId.set(saved);
    }
    this.searchStream(this.lockTerms, this.lockResults);
    this.searchStream(this.excludeTerms, this.excludeResults);
  }

  protected setMode(mode: Mode): void {
    this.mode.set(mode);
    this.result.set(null);
    this.error.set(null);
    this.horizon.set(mode === 'plan' ? 4 : 5);
  }

  protected chipLabel(chip: Chip): string {
    return CHIP_LABELS[chip];
  }

  protected isChipOn(chip: Chip): boolean {
    return this.selectedChips().includes(chip);
  }

  protected toggleChip(chip: Chip): void {
    this.selectedChips.update((current) =>
      current.includes(chip) ? current.filter((c) => c !== chip) : [...current, chip],
    );
  }

  /**
   * Wire both typeaheads. Debounced so a fast typist makes one request, not
   * eight, and `switchMap` so a slow earlier response cannot overwrite a
   * newer one.
   */
  private searchStream(terms: Subject<string>, into: WritableSignal<Player[]>): void {
    terms
      .pipe(
        map((term) => term.trim()),
        debounceTime(200),
        distinctUntilChanged(),
        switchMap((term) =>
          term.length < 2
            ? of<Player[]>([])
            : this.api
                .players({ search: term, limit: 6, sort_by: 'selected_by_percent', order: 'desc' })
                .pipe(
                  map((page) => page.results),
                  // A failed lookup must not kill the stream — the picker just
                  // shows nothing and the next keystroke tries again.
                  catchError(() => of<Player[]>([])),
                ),
        ),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((results) => into.set(results));
  }

  protected onLockQuery(term: string): void {
    this.lockQuery.set(term);
    this.lockTerms.next(term);
  }

  protected onExcludeQuery(term: string): void {
    this.excludeQuery.set(term);
    this.excludeTerms.next(term);
  }

  protected addLock(player: Player): void {
    // A player cannot be both locked and barred; the newer intent wins.
    this.excluded.update((list) => list.filter((p) => p.id !== player.id));
    this.locked.update((list) =>
      list.some((p) => p.id === player.id) || list.length >= SQUAD_SIZE ? list : [...list, player],
    );
    this.lockQuery.set('');
    this.lockResults.set([]);
  }

  protected addExclude(player: Player): void {
    this.locked.update((list) => list.filter((p) => p.id !== player.id));
    this.excluded.update((list) =>
      list.some((p) => p.id === player.id) ? list : [...list, player],
    );
    this.excludeQuery.set('');
    this.excludeResults.set([]);
  }

  protected removeLock(id: number): void {
    this.locked.update((list) => list.filter((p) => p.id !== id));
  }

  protected removeExclude(id: number): void {
    this.excluded.update((list) => list.filter((p) => p.id !== id));
  }

  /**
   * Set-piece duties worth showing. Only the first-choice taker is flagged —
   * a second-choice penalty taker steps up rarely enough that badging them
   * would be noise, not signal.
   */
  protected setPieces(pick: SquadPick): { label: string; title: string }[] {
    const duties: { label: string; title: string }[] = [];
    if (pick.penalties_order === 1) {
      duties.push({ label: 'PEN', title: 'First-choice penalty taker' });
    }
    if (pick.direct_freekicks_order === 1) {
      duties.push({ label: 'FK', title: 'First-choice direct free-kick taker' });
    }
    if (pick.corners_order === 1) {
      duties.push({ label: 'COR', title: 'First-choice corner taker' });
    }
    return duties;
  }

  /** True while any pick carries consensus data, i.e. we are pre-season. */
  protected hasStartData(data: OptimisationResult): boolean {
    return data.squad.some((pick) => pick.start_probability !== null);
  }

  /** Pre-season consensus that a player starts, as a share of sources. */
  protected startLabel(probability: number): string {
    return `${Math.round(probability * 100)}%`;
  }

  protected startClass(probability: number): string {
    if (probability >= 0.8) return 'start-high';
    if (probability >= 0.5) return 'start-mid';
    return 'start-low';
  }

  protected startTitle(probability: number): string {
    const pct = Math.round(probability * 100);
    if (probability === 0) return 'No predicted-XI source starts this player';
    return `${pct}% of predicted-XI sources start this player`;
  }

  protected buildSquad(): void {
    this.start();
    this.api
      .bestSquad({
        horizon: this.horizon(),
        budget: this.budget(),
        min_start_probability: this.minStart(),
        lock: this.locked().map((p) => p.id).join(',') || undefined,
        exclude: this.excluded().map((p) => p.id).join(',') || undefined,
      })
      .subscribe({ next: (data) => this.done(data), error: (err) => this.fail(err) });
  }

  protected planTransfers(): void {
    const id = Number(this.entryId());
    if (!Number.isFinite(id) || id <= 0) {
      this.error.set('That does not look like a team ID. It should be a number.');
      return;
    }
    localStorage.setItem(STORAGE_KEY, String(id));
    this.start();
    this.api
      .planForEntry(id, {
        horizon: this.horizon(),
        chips: this.selectedChips().join(','),
        time_limit: this.timeLimit(),
      })
      .subscribe({ next: (data) => this.done(data), error: (err) => this.fail(err) });
  }

  /** Hand a freshly built squad to the planner — the wildcard-draft workflow. */
  protected planFromBuiltSquad(built: OptimisationResult): void {
    const spent = this.squadCost(built.squad);
    this.start();
    this.mode.set('plan');
    this.api
      .planForSquad(
        {
          squad: built.squad.map((pick) => pick.player_id),
          bank: Math.max(0, Math.round(this.budget() * 10) - spent),
          free_transfers: 1,
          chips: this.selectedChips(),
        },
        { horizon: built.events.length, time_limit: this.timeLimit() },
      )
      .subscribe({ next: (data) => this.done(data), error: (err) => this.fail(err) });
  }

  private start(): void {
    this.loading.set(true);
    this.error.set(null);
    this.preSeason.set(false);
    this.result.set(null);
  }

  private done(data: OptimisationResult): void {
    this.result.set(data);
    this.loading.set(false);
  }

  private fail(err: unknown): void {
    const status = (err as { status?: number })?.status;
    this.preSeason.set(status === 409);
    this.error.set(errorMessage(err));
    this.loading.set(false);
  }

  protected squadCost(squad: SquadPick[]): number {
    return squad.reduce((sum, pick) => sum + pick.cost, 0);
  }

  protected chipsUsed(data: OptimisationResult): string[] {
    return data.gameweeks
      .filter((week) => week.chip)
      .map((week) => `${this.chipLabel(week.chip!)} GW${week.event_id}`);
  }

  protected grouped(squad: SquadPick[]): { position: string; players: SquadPick[] }[] {
    return (['GKP', 'DEF', 'MID', 'FWD'] as const)
      .map((position) => ({
        position,
        players: squad
          .filter((pick) => pick.position === position)
          .sort((a, b) => b.expected_points - a.expected_points),
      }))
      .filter((group) => group.players.length > 0);
  }

  /** Line transfers up as out → in pairs for display. */
  protected pairs(week: GameweekPlan): { out: string; in: string }[] {
    const count = Math.max(week.transfers_in.length, week.transfers_out.length);
    return Array.from({ length: count }, (_, i) => ({
      out: week.transfers_out[i]?.web_name ?? '—',
      in: week.transfers_in[i]?.web_name ?? '—',
    }));
  }
}
