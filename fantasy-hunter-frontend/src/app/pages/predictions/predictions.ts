import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { Api } from '../../core/api';
import { PlayerForecast, PredictionRow, PredictionTable } from '../../core/models';
import { errorMessage, fdrClass, humanise, isFlagged, statusLabel } from '../../core/ui';

const POSITIONS = ['All', 'GKP', 'DEF', 'MID', 'FWD'] as const;

@Component({
  selector: 'app-predictions',
  imports: [FormsModule],
  template: `
    <div class="page">
      <div class="page-head">
        <h1>Predicted points</h1>
        <p>
          Expected points per gameweek from model
          <span class="mono">{{ table()?.model_version ?? '…' }}</span
          >. Every number is a breakdown, not a guess — tap a row to see exactly where it comes
          from.
        </p>
      </div>

      <div class="controls">
        <div class="field">
          <label for="pred-search">Search</label>
          <input
            id="pred-search"
            type="search"
            placeholder="Player name"
            [ngModel]="search()"
            (ngModelChange)="onSearch($event)"
          />
        </div>

        <div class="field">
          <label for="pred-horizon">Gameweeks</label>
          <select id="pred-horizon" [ngModel]="horizon()" (ngModelChange)="onHorizon(+$event)">
            @for (n of horizonOptions; track n) {
              <option [value]="n">Next {{ n }}</option>
            }
          </select>
        </div>

        <div class="field">
          <label for="pred-price">Max price</label>
          <select id="pred-price" [ngModel]="maxPrice()" (ngModelChange)="onPrice($event)">
            <option value="">Any</option>
            @for (p of priceOptions; track p) {
              <option [value]="p">{{ p }}m</option>
            }
          </select>
        </div>

        <div class="field">
          <label>Position</label>
          <div class="chips">
            @for (pos of positions; track pos) {
              <button
                type="button"
                class="chip"
                [class.active]="position() === pos"
                (click)="onPosition(pos)"
              >
                {{ pos }}
              </button>
            }
          </div>
        </div>
      </div>

      @if (error(); as message) {
        <div class="notice error">{{ message }}</div>
      } @else if (loading()) {
        <div class="notice"><span class="spinner"></span> Calculating predictions…</div>
      } @else if (table(); as data) {
        @if (data.results.length === 0) {
          <div class="notice">No players match those filters.</div>
        } @else {
          <p class="small muted count">
            Showing {{ data.results.length }} of {{ data.total }} players · gameweeks
            {{ data.events[0] }}–{{ data.events[data.events.length - 1] }}
          </p>

          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th class="name sticky-col">Player</th>
                  <th>Pos</th>
                  <th>£</th>
                  @for (event of data.events; track event) {
                    <th>GW{{ event }}</th>
                  }
                  <th>Total</th>
                  <th>Value</th>
                </tr>
              </thead>
              <tbody>
                @for (row of data.results; track row.player_id) {
                  <tr [class.selected]="expanded() === row.player_id" (click)="toggle(row)">
                    <td class="name sticky-col">
                      <span class="pname">{{ row.web_name }}</span>
                      <span class="muted small">{{ row.team_short_name }}</span>
                      @if (flagged(row.status)) {
                        <span
                          class="flag flag-{{ row.status }}"
                          [title]="statusLabel(row.status)"
                        ></span>
                      }
                    </td>
                    <td>
                      <span class="pos pos-{{ row.position }}">{{ row.position }}</span>
                    </td>
                    <td class="mono">{{ row.price.toFixed(1) }}</td>

                    @for (event of data.events; track event) {
                      <td>
                        @if (row.by_event[event] && row.by_event[event].fixtures.length) {
                          @for (fixture of row.by_event[event].fixtures; track fixture.fixture_id) {
                            <span [class]="fdrClass(fixture.difficulty)">
                              <strong class="mono">{{
                                fixture.expected_points.toFixed(1)
                              }}</strong>
                              <small
                                >{{ fixture.opponent }}
                                {{ fixture.is_home ? '(H)' : '(A)' }}</small
                              >
                            </span>
                          }
                        } @else {
                          <span class="blank">BGW</span>
                        }
                      </td>
                    }

                    <td class="mono total">{{ row.expected_points_total.toFixed(1) }}</td>
                    <td class="mono muted">{{ row.value.toFixed(2) }}</td>
                  </tr>

                  @if (expanded() === row.player_id) {
                    <tr class="detail-row">
                      <td [attr.colspan]="data.events.length + 5">
                        @if (forecastLoading()) {
                          <span class="spinner"></span> Loading breakdown…
                        } @else if (forecast(); as detail) {
                          <div class="breakdown">
                            <div class="breakdown-head">
                              <h2>{{ detail.web_name }} — how this is calculated</h2>
                              <p class="small muted">
                                Baseline from {{ detail.profile.sample_minutes.toFixed(0) }} minutes
                                of history ({{ sourceLabel(detail.profile.source) }}) ·
                                {{ detail.profile.minutes_per_game.toFixed(0) }} expected minutes
                                per game
                                @if (detail.fixtures.length) {
                                  · fixture model:
                                  {{
                                    detail.fixtures[0].components.fixture_model === 'official_fdr'
                                      ? 'official FDR (team strength unpublished pre-season)'
                                      : 'team strength'
                                  }}
                                }
                              </p>
                            </div>

                            <div class="rates">
                              @for (rate of rateChips(detail); track rate.label) {
                                <div class="rate">
                                  <span class="muted small">{{ rate.label }}</span>
                                  <strong class="mono">{{ rate.value }}</strong>
                                </div>
                              }
                            </div>

                            <div class="fixture-cards">
                              @for (fixture of detail.fixtures; track fixture.fixture_id) {
                                <div class="fixture-card">
                                  <div class="fixture-card-head">
                                    <span [class]="fdrClass(fixture.difficulty)">
                                      <strong class="mono">{{
                                        fixture.expected_points.toFixed(2)
                                      }}</strong>
                                      <small
                                        >{{ fixture.opponent }}
                                        {{ fixture.is_home ? '(H)' : '(A)' }}</small
                                      >
                                    </span>
                                    <span class="small muted"
                                      >GW{{ fixture.event_id }} ·
                                      {{ fixture.expected_minutes.toFixed(0) }} mins ·
                                      {{ (fixture.components.p_start * 100).toFixed(0) }}% to
                                      start</span
                                    >
                                  </div>

                                  <ul class="components">
                                    @for (
                                      part of componentList(fixture.components.points);
                                      track part.key
                                    ) {
                                      <li>
                                        <span>{{ humanise(part.key) }}</span>
                                        <span
                                          class="mono"
                                          [class.negative]="part.value < 0"
                                          >{{ part.value > 0 ? '+' : '' }}{{ part.value.toFixed(2) }}</span
                                        >
                                      </li>
                                    }
                                  </ul>

                                  <p class="small muted assumptions">
                                    Clean sheet
                                    {{ (fixture.components.p_clean_sheet * 100).toFixed(0) }}% ·
                                    xG {{ fixture.components.x_goals.toFixed(2) }} · xA
                                    {{ fixture.components.x_assists.toFixed(2) }}
                                  </p>
                                </div>
                              }
                            </div>
                          </div>
                        }
                      </td>
                    </tr>
                  }
                }
              </tbody>
            </table>
          </div>

          @if (data.results.length < data.total) {
            <button class="more" type="button" (click)="showMore()">
              Show more ({{ data.total - data.results.length }} remaining)
            </button>
          }
        }
      }
    </div>
  `,
  styles: `
    .count {
      margin: 0 0 0.5rem;
    }

    .pname {
      display: inline-block;
      min-width: 90px;
      font-weight: 600;
    }

    .pname + .muted {
      margin-left: 0.4rem;
    }

    tbody tr {
      cursor: pointer;
    }

    td.total {
      font-weight: 700;
      color: var(--accent);
    }

    /* Table cells are right-aligned by default; the breakdown is prose and
       must not inherit that. */
    .detail-row td {
      white-space: normal;
      text-align: left;
      background: var(--surface-2);
      padding: 0.9rem;
    }

    .detail-row:hover td {
      background: var(--surface-2);
    }

    .breakdown-head h2 {
      margin-bottom: 0.15rem;
    }

    .rates {
      display: flex;
      flex-wrap: wrap;
      gap: 0.5rem;
      margin: 0.75rem 0;
    }

    .rate {
      display: flex;
      flex-direction: column;
      gap: 0.1rem;
      padding: 0.4rem 0.6rem;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius-sm);
      min-width: 92px;
    }

    .fixture-cards {
      display: grid;
      gap: 0.6rem;
      grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    }

    .fixture-card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius-sm);
      padding: 0.6rem;
    }

    .fixture-card-head {
      display: flex;
      flex-direction: column;
      gap: 0.3rem;
      margin-bottom: 0.5rem;
    }

    .fixture-card-head > span:first-child {
      align-self: flex-start;
    }

    ul.components {
      list-style: none;
      margin: 0;
      padding: 0;
      font-size: 0.82rem;
    }

    ul.components li {
      display: flex;
      justify-content: space-between;
      gap: 1rem;
      padding: 0.16rem 0;
      border-bottom: 1px dashed var(--border);
    }

    ul.components li:last-child {
      border-bottom: none;
    }

    .negative {
      color: var(--danger);
    }

    .assumptions {
      margin: 0.5rem 0 0;
    }

    .more {
      margin-top: 0.8rem;
      width: 100%;
    }
  `,
})
export class PredictionsPage {
  private readonly api = inject(Api);

  protected readonly positions = POSITIONS;
  protected readonly horizonOptions = [1, 2, 3, 5, 6, 8, 10];
  protected readonly priceOptions = [4.5, 5, 5.5, 6, 6.5, 7, 8, 9, 10, 12];

  protected readonly search = signal('');
  protected readonly position = signal<(typeof POSITIONS)[number]>('All');
  protected readonly horizon = signal(5);
  protected readonly maxPrice = signal<string>('');
  protected readonly limit = signal(40);

  protected readonly table = signal<PredictionTable | null>(null);
  protected readonly loading = signal(true);
  protected readonly error = signal<string | null>(null);

  protected readonly expanded = signal<number | null>(null);
  protected readonly forecast = signal<PlayerForecast | null>(null);
  protected readonly forecastLoading = signal(false);

  protected readonly fdrClass = fdrClass;
  protected readonly statusLabel = statusLabel;
  protected readonly flagged = isFlagged;
  protected readonly humanise = humanise;

  private searchTimer?: ReturnType<typeof setTimeout>;

  constructor() {
    this.load();
  }

  private load(): void {
    this.loading.set(true);
    this.error.set(null);
    this.api
      .predictions({
        horizon: this.horizon(),
        position: this.position() === 'All' ? undefined : this.position(),
        max_price: this.maxPrice() ? Number(this.maxPrice()) : undefined,
        search: this.search() || undefined,
        limit: this.limit(),
      })
      .subscribe({
        next: (data) => {
          this.table.set(data);
          this.loading.set(false);
        },
        error: (err) => {
          this.error.set(errorMessage(err));
          this.loading.set(false);
        },
      });
  }

  private reset(): void {
    this.limit.set(40);
    this.expanded.set(null);
    this.load();
  }

  protected onSearch(value: string): void {
    this.search.set(value);
    clearTimeout(this.searchTimer);
    this.searchTimer = setTimeout(() => this.reset(), 250);
  }

  protected onPosition(pos: (typeof POSITIONS)[number]): void {
    this.position.set(pos);
    this.reset();
  }

  protected onHorizon(value: number): void {
    this.horizon.set(value);
    this.reset();
  }

  protected onPrice(value: string): void {
    this.maxPrice.set(value);
    this.reset();
  }

  protected showMore(): void {
    this.limit.update((n) => n + 40);
    this.load();
  }

  protected toggle(row: PredictionRow): void {
    if (this.expanded() === row.player_id) {
      this.expanded.set(null);
      return;
    }
    this.expanded.set(row.player_id);
    this.forecast.set(null);
    this.forecastLoading.set(true);
    this.api.playerForecast(row.player_id, this.horizon()).subscribe({
      next: (detail) => {
        this.forecast.set(detail);
        this.forecastLoading.set(false);
      },
      error: () => this.forecastLoading.set(false),
    });
  }

  protected componentList(points: Record<string, number>): { key: string; value: number }[] {
    return Object.entries(points)
      .filter(([, value]) => value !== 0)
      .map(([key, value]) => ({ key, value }))
      .sort((a, b) => Math.abs(b.value) - Math.abs(a.value));
  }

  protected rateChips(detail: PlayerForecast): { label: string; value: string }[] {
    const p = detail.profile;
    const chips = [
      { label: 'Goals /90', value: p.goals_per90.toFixed(2) },
      { label: 'Assists /90', value: p.assists_per90.toFixed(2) },
      { label: 'Bonus /90', value: p.bonus_per90.toFixed(2) },
    ];
    if (detail.position === 'GKP') {
      chips.push({ label: 'Saves /90', value: p.saves_per90.toFixed(2) });
    } else {
      chips.push({ label: 'Def. contrib /90', value: p.dc_per90.toFixed(1) });
    }
    return chips;
  }

  protected sourceLabel(source: string): string {
    switch (source) {
      case 'prior':
        return 'no history — price-based prior';
      case 'thin':
        return 'thin history — price-based prior';
      case 'current':
        return 'this season + past seasons';
      default:
        return 'past seasons';
    }
  }
}
