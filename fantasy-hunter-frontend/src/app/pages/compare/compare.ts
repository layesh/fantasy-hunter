import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';

import { Api } from '../../core/api';
import { Comparison, Player } from '../../core/models';
import { errorMessage, fdrClass, humanise } from '../../core/ui';

@Component({
  selector: 'app-compare',
  imports: [FormsModule, RouterLink],
  template: `
    <div class="page">
      <div class="page-head">
        <h1>Player comparison</h1>
        <p>Two to four players side by side, with the upcoming fixtures each one faces.</p>
      </div>

      <div class="controls">
        <div class="field search-field">
          <label for="cmp-search">Add a player</label>
          <input
            id="cmp-search"
            type="search"
            autocomplete="off"
            placeholder="Search by name"
            [ngModel]="query()"
            (ngModelChange)="onQuery($event)"
          />
          @if (suggestions().length) {
            <ul class="suggestions">
              @for (player of suggestions(); track player.id) {
                <li>
                  <button type="button" (click)="add(player)">
                    <strong>{{ player.web_name }}</strong>
                    <span class="muted small"
                      >{{ player.team_short_name }} · {{ player.position }} ·
                      {{ player.price.toFixed(1) }}m</span
                    >
                  </button>
                </li>
              }
            </ul>
          }
        </div>

        <div class="field">
          <label for="cmp-horizon">Gameweeks</label>
          <select id="cmp-horizon" [ngModel]="horizon()" (ngModelChange)="onHorizon(+$event)">
            @for (n of horizonOptions; track n) {
              <option [value]="n">Next {{ n }}</option>
            }
          </select>
        </div>
      </div>

      @if (ids().length) {
        <div class="chips picked">
          @for (id of ids(); track id) {
            <button type="button" class="chip" (click)="remove(id)">
              {{ nameFor(id) }} <span class="x">×</span>
            </button>
          }
        </div>
      }

      @if (error(); as message) {
        <div class="notice error">{{ message }}</div>
      } @else if (ids().length < 2) {
        <div class="notice">
          Pick at least two players to compare. You can also tick players in the
          <a routerLink="/players">player database</a> and jump straight here.
        </div>
      } @else if (loading()) {
        <div class="notice"><span class="spinner"></span> Comparing…</div>
      } @else if (comparison(); as data) {
        <div class="fixtures-row">
          @for (player of data.players; track player.id) {
            <div class="card player-card">
              <h2>{{ player.web_name }}</h2>
              <p class="small muted">
                {{ player.team_name }} · {{ player.position }} · {{ player.price.toFixed(1) }}m
              </p>
              <p class="xpts mono">
                {{ player.expected_points_total.toFixed(1) }}
                <span class="small muted">xPts next {{ data.events.length }}</span>
              </p>
              <div class="fixture-strip">
                @for (fixture of player.upcoming; track fixture.event_id + '-' + fixture.opponent) {
                  <span [class]="fdrClass(fixture.difficulty)">
                    <strong class="mono">{{ fixture.expected_points.toFixed(1) }}</strong>
                    <small>{{ fixture.opponent }} {{ fixture.is_home ? '(H)' : '(A)' }}</small>
                  </span>
                }
              </div>
            </div>
          }
        </div>

        <div class="table-wrap metrics">
          <table>
            <thead>
              <tr>
                <th class="name sticky-col">Metric</th>
                @for (player of data.players; track player.id) {
                  <th>{{ player.web_name }}</th>
                }
              </tr>
            </thead>
            <tbody>
              @for (metric of data.metrics; track metric.metric) {
                <tr>
                  <td class="name sticky-col">
                    {{ humanise(metric.metric) }}
                    @if (metric.higher_is_better === false) {
                      <span class="muted small">(lower is better)</span>
                    }
                  </td>
                  @for (player of data.players; track player.id; let i = $index) {
                    <td
                      class="mono"
                      [class.winner]="metric.winner_player_id === player.id"
                    >
                      {{ format(metric.values[i]) }}
                    </td>
                  }
                </tr>
              }
            </tbody>
          </table>
        </div>
      }
    </div>
  `,
  styles: `
    .search-field {
      position: relative;
      min-width: 220px;
    }

    .suggestions {
      position: absolute;
      top: 100%;
      left: 0;
      right: 0;
      z-index: 15;
      margin: 0.2rem 0 0;
      padding: 0;
      list-style: none;
      background: var(--surface-2);
      border: 1px solid var(--border);
      border-radius: var(--radius-sm);
      max-height: 260px;
      overflow-y: auto;
    }

    .suggestions button {
      display: flex;
      flex-direction: column;
      align-items: flex-start;
      gap: 0.1rem;
      width: 100%;
      border: none;
      border-radius: 0;
      background: transparent;
      text-align: left;
      padding: 0.45rem 0.6rem;
      font-weight: 500;
    }

    .suggestions button:hover {
      background: var(--surface);
    }

    .picked {
      margin-bottom: 0.85rem;
    }

    .picked .x {
      color: var(--muted);
      margin-left: 0.2rem;
    }

    .fixtures-row {
      display: grid;
      gap: 0.6rem;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      margin-bottom: 0.85rem;
    }

    .player-card p {
      margin: 0.1rem 0;
    }

    .xpts {
      font-size: 1.4rem;
      font-weight: 700;
      color: var(--accent);
      margin: 0.4rem 0 0.55rem !important;
    }

    .xpts .small {
      font-weight: 500;
      margin-left: 0.3rem;
    }

    .fixture-strip {
      display: flex;
      flex-wrap: wrap;
      gap: 0.25rem;
    }

    td.winner {
      color: var(--accent);
      font-weight: 700;
      background: #10241d;
    }
  `,
})
export class ComparePage {
  private readonly api = inject(Api);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);

  protected readonly horizonOptions = [1, 2, 3, 5, 6, 8];
  protected readonly horizon = signal(5);
  protected readonly ids = signal<number[]>([]);
  protected readonly query = signal('');
  protected readonly suggestions = signal<Player[]>([]);
  protected readonly names = signal<Record<number, string>>({});

  protected readonly comparison = signal<Comparison | null>(null);
  protected readonly loading = signal(false);
  protected readonly error = signal<string | null>(null);

  protected readonly fdrClass = fdrClass;
  protected readonly humanise = humanise;

  private searchTimer?: ReturnType<typeof setTimeout>;

  constructor() {
    const raw = this.route.snapshot.queryParamMap.get('ids');
    if (raw) {
      const parsed = raw
        .split(',')
        .map((value) => Number(value))
        .filter((value) => Number.isFinite(value) && value > 0)
        .slice(0, 4);
      this.ids.set(parsed);
      this.load();
    }
  }

  private load(): void {
    if (this.ids().length < 2) {
      this.comparison.set(null);
      return;
    }
    this.loading.set(true);
    this.error.set(null);
    this.api.compare(this.ids(), this.horizon()).subscribe({
      next: (data) => {
        this.comparison.set(data);
        this.names.update((current) => {
          const next = { ...current };
          for (const player of data.players) {
            next[player.id] = player.web_name;
          }
          return next;
        });
        this.loading.set(false);
      },
      error: (err) => {
        this.error.set(errorMessage(err));
        this.loading.set(false);
      },
    });
  }

  private syncUrl(): void {
    this.router.navigate([], {
      relativeTo: this.route,
      queryParams: this.ids().length ? { ids: this.ids().join(',') } : {},
      replaceUrl: true,
    });
  }

  protected onQuery(value: string): void {
    this.query.set(value);
    clearTimeout(this.searchTimer);
    if (value.trim().length < 2) {
      this.suggestions.set([]);
      return;
    }
    this.searchTimer = setTimeout(() => {
      this.api.players({ search: value.trim(), limit: 8 }).subscribe({
        next: (page) => this.suggestions.set(page.results),
      });
    }, 220);
  }

  protected add(player: Player): void {
    if (this.ids().includes(player.id) || this.ids().length >= 4) {
      return;
    }
    this.names.update((current) => ({ ...current, [player.id]: player.web_name }));
    this.ids.update((current) => [...current, player.id]);
    this.query.set('');
    this.suggestions.set([]);
    this.syncUrl();
    this.load();
  }

  protected remove(id: number): void {
    this.ids.update((current) => current.filter((value) => value !== id));
    this.syncUrl();
    this.load();
  }

  protected onHorizon(value: number): void {
    this.horizon.set(value);
    this.load();
  }

  protected nameFor(id: number): string {
    return this.names()[id] ?? `#${id}`;
  }

  protected format(value: number | string | null): string {
    if (value === null || value === undefined) return '—';
    if (typeof value === 'number') {
      return Number.isInteger(value) ? String(value) : value.toFixed(2);
    }
    return value;
  }
}
