import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';

import { Api } from '../../core/api';
import { Paged, Player, Team } from '../../core/models';
import { errorMessage, isFlagged, statusLabel } from '../../core/ui';

interface Column {
  key: string;
  label: string;
  sortable: boolean;
  format?: (player: Player) => string;
}

const POSITIONS = ['All', 'GKP', 'DEF', 'MID', 'FWD'] as const;

@Component({
  selector: 'app-players',
  imports: [FormsModule, RouterLink],
  template: `
    <div class="page">
      <div class="page-head">
        <h1>Player database</h1>
        <p>Every player in the game, with the underlying stats the model reads.</p>
      </div>

      @if (preSeason()) {
        <div class="notice warn season-note">
          <strong>These are last season's totals.</strong> Until the new season kicks off, FPL
          carries the previous campaign's points, minutes and goals forward, and form reads 0.
          They will switch to live figures once matches are played.
        </div>
      }

      <div class="controls">
        <div class="field">
          <label for="pl-search">Search</label>
          <input
            id="pl-search"
            type="search"
            placeholder="Player name"
            [ngModel]="search()"
            (ngModelChange)="onSearch($event)"
          />
        </div>

        <div class="field">
          <label for="pl-team">Team</label>
          <select id="pl-team" [ngModel]="teamId()" (ngModelChange)="onTeam($event)">
            <option value="">All teams</option>
            @for (team of teams(); track team.id) {
              <option [value]="team.id">{{ team.name }}</option>
            }
          </select>
        </div>

        <div class="field">
          <label for="pl-price">Max price</label>
          <select id="pl-price" [ngModel]="maxPrice()" (ngModelChange)="onPrice($event)">
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

        <div class="field">
          <label>Availability</label>
          <button
            type="button"
            class="chip"
            [class.active]="availableOnly()"
            (click)="onAvailable()"
          >
            Fit players only
          </button>
        </div>
      </div>

      @if (error(); as message) {
        <div class="notice error">{{ message }}</div>
      } @else if (loading()) {
        <div class="notice"><span class="spinner"></span> Loading players…</div>
      } @else if (page(); as data) {
        @if (data.results.length === 0) {
          <div class="notice">No players match those filters.</div>
        } @else {
          <div class="head-row">
            <p class="small muted">Showing {{ data.results.length }} of {{ data.total }}</p>
            @if (selected().length) {
              <a class="chip active" [routerLink]="['/compare']" [queryParams]="compareParams()">
                Compare {{ selected().length }} selected →
              </a>
            }
          </div>

          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th class="pick"></th>
                  <th class="name sticky-col">Player</th>
                  @for (column of columns; track column.key) {
                    <th
                      [class.sortable]="column.sortable"
                      (click)="column.sortable && onSort(column.key)"
                    >
                      {{ column.label }}
                      @if (sortBy() === column.key) {
                        <span class="arrow">{{ order() === 'desc' ? '▼' : '▲' }}</span>
                      }
                    </th>
                  }
                </tr>
              </thead>
              <tbody>
                @for (player of data.results; track player.id) {
                  <tr>
                    <td class="pick">
                      <input
                        type="checkbox"
                        [attr.aria-label]="'Select ' + player.web_name + ' to compare'"
                        [checked]="isSelected(player.id)"
                        [disabled]="!isSelected(player.id) && selected().length >= 4"
                        (change)="toggleSelect(player.id)"
                      />
                    </td>
                    <td class="name sticky-col">
                      <span class="pname">{{ player.web_name }}</span>
                      <span class="muted small">{{ player.team_short_name }}</span>
                      @if (flagged(player.status)) {
                        <span
                          class="flag flag-{{ player.status }}"
                          [title]="player.news || statusLabel(player.status)"
                        ></span>
                      }
                      @for (duty of setPieces(player); track duty.label) {
                        <span class="sp-badge" [title]="duty.title">{{ duty.label }}</span>
                      }
                    </td>
                    @for (column of columns; track column.key) {
                      <td [class.mono]="column.key !== 'position'">
                        @if (column.key === 'position') {
                          <span class="pos pos-{{ player.position }}">{{ player.position }}</span>
                        } @else {
                          {{ column.format!(player) }}
                        }
                      </td>
                    }
                  </tr>
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
    /* Set-piece duty is a fact about role, not a rating, so it stays neutral
       rather than borrowing the good/bad colour scale used for form. */
    .sp-badge {
      margin-left: 0.25rem;
      font-size: 0.58rem;
      font-weight: 700;
      letter-spacing: 0.04em;
      padding: 0.05rem 0.22rem;
      border-radius: 3px;
      border: 1px solid var(--border);
      color: var(--muted, #9aa3ae);
      vertical-align: middle;
    }

    .season-note {
      margin-bottom: 0.85rem;
    }

    .head-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 0.75rem;
      margin-bottom: 0.5rem;
    }

    .head-row p {
      margin: 0;
    }

    th.pick,
    td.pick {
      width: 34px;
      padding-right: 0;
      text-align: center;
    }

    td.pick input {
      min-height: 0;
      width: 16px;
      height: 16px;
      accent-color: var(--accent);
    }

    .pname {
      display: inline-block;
      min-width: 92px;
      font-weight: 600;
    }

    .pname + .muted {
      margin-left: 0.4rem;
    }

    .arrow {
      margin-left: 0.2rem;
      color: var(--accent);
    }

    .more {
      margin-top: 0.8rem;
      width: 100%;
    }
  `,
})
export class PlayersPage {
  private readonly api = inject(Api);

  protected readonly positions = POSITIONS;
  protected readonly priceOptions = [4.5, 5, 5.5, 6, 6.5, 7, 8, 9, 10, 12];

  /**
   * First-choice set-piece duty. Penalties in particular are worth real points
   * that a per-90 rate misses, and FPL publishes the ordering — it was already
   * in our data, just never shown.
   */
  protected setPieces(player: Player): { label: string; title: string }[] {
    const duties: { label: string; title: string }[] = [];
    if (player.penalties_order === 1) {
      duties.push({ label: 'PEN', title: 'First-choice penalty taker' });
    }
    if (player.direct_freekicks_order === 1) {
      duties.push({ label: 'FK', title: 'First-choice direct free-kick taker' });
    }
    if (player.corners_and_indirect_freekicks_order === 1) {
      duties.push({ label: 'COR', title: 'First-choice corner taker' });
    }
    return duties;
  }

  protected readonly columns: Column[] = [
    { key: 'position', label: 'Pos', sortable: false },
    { key: 'now_cost', label: '£', sortable: true, format: (p) => p.price.toFixed(1) },
    { key: 'total_points', label: 'Pts', sortable: true, format: (p) => String(p.total_points) },
    {
      key: 'points_per_game',
      label: 'PPG',
      sortable: true,
      format: (p) => p.points_per_game.toFixed(1),
    },
    { key: 'form', label: 'Form', sortable: true, format: (p) => p.form.toFixed(1) },
    {
      key: 'selected_by_percent',
      label: 'Own %',
      sortable: true,
      format: (p) => p.selected_by_percent.toFixed(1),
    },
    { key: 'minutes', label: 'Mins', sortable: true, format: (p) => String(p.minutes) },
    { key: 'goals_scored', label: 'G', sortable: true, format: (p) => String(p.goals_scored) },
    { key: 'assists', label: 'A', sortable: true, format: (p) => String(p.assists) },
    {
      key: 'expected_goal_involvements',
      label: 'xGI',
      sortable: true,
      format: (p) => p.expected_goal_involvements.toFixed(2),
    },
    {
      key: 'defensive_contribution',
      label: 'DefCon',
      sortable: false,
      format: (p) => String(p.defensive_contribution),
    },
    { key: 'ict_index', label: 'ICT', sortable: true, format: (p) => p.ict_index.toFixed(1) },
  ];

  protected readonly search = signal('');
  protected readonly position = signal<(typeof POSITIONS)[number]>('All');
  protected readonly teamId = signal<string>('');
  protected readonly maxPrice = signal<string>('');
  protected readonly availableOnly = signal(false);
  protected readonly sortBy = signal('total_points');
  protected readonly order = signal<'asc' | 'desc'>('desc');
  protected readonly limit = signal(50);

  protected readonly teams = signal<Team[]>([]);
  protected readonly page = signal<Paged<Player> | null>(null);
  protected readonly loading = signal(true);
  protected readonly error = signal<string | null>(null);
  protected readonly selected = signal<number[]>([]);
  /** No gameweek finished yet means FPL is still serving last season's totals. */
  protected readonly preSeason = signal(false);

  protected readonly statusLabel = statusLabel;
  protected readonly flagged = isFlagged;

  private searchTimer?: ReturnType<typeof setTimeout>;

  constructor() {
    this.api.teams().subscribe({ next: (teams) => this.teams.set(teams) });
    this.api.events().subscribe({
      next: (events) => this.preSeason.set(!events.some((event) => event.finished)),
    });
    this.load();
  }

  private load(): void {
    this.loading.set(true);
    this.error.set(null);
    this.api
      .players({
        search: this.search() || undefined,
        position: this.position() === 'All' ? undefined : this.position(),
        team_id: this.teamId() ? Number(this.teamId()) : undefined,
        max_price: this.maxPrice() ? Number(this.maxPrice()) : undefined,
        available_only: this.availableOnly() || undefined,
        sort_by: this.sortBy(),
        order: this.order(),
        limit: this.limit(),
      })
      .subscribe({
        next: (data) => {
          this.page.set(data);
          this.loading.set(false);
        },
        error: (err) => {
          this.error.set(errorMessage(err));
          this.loading.set(false);
        },
      });
  }

  private reset(): void {
    this.limit.set(50);
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

  protected onTeam(value: string): void {
    this.teamId.set(value);
    this.reset();
  }

  protected onPrice(value: string): void {
    this.maxPrice.set(value);
    this.reset();
  }

  protected onAvailable(): void {
    this.availableOnly.update((v) => !v);
    this.reset();
  }

  protected onSort(key: string): void {
    if (this.sortBy() === key) {
      this.order.update((o) => (o === 'desc' ? 'asc' : 'desc'));
    } else {
      this.sortBy.set(key);
      this.order.set('desc');
    }
    this.reset();
  }

  protected showMore(): void {
    this.limit.update((n) => n + 50);
    this.load();
  }

  protected isSelected(id: number): boolean {
    return this.selected().includes(id);
  }

  /** The comparison endpoint accepts between two and four players. */
  protected toggleSelect(id: number): void {
    this.selected.update((current) =>
      current.includes(id)
        ? current.filter((x) => x !== id)
        : current.length >= 4
          ? current
          : [...current, id],
    );
  }

  protected compareParams(): { ids: string } {
    return { ids: this.selected().join(',') };
  }
}
