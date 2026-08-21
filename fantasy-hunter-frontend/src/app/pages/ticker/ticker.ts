import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { Api } from '../../core/api';
import { DefenceTable, Ticker } from '../../core/models';
import { errorMessage, ratingClass } from '../../core/ui';

@Component({
  selector: 'app-ticker',
  imports: [FormsModule],
  template: `
    <div class="page">
      <div class="page-head">
        <h1>Fixture ticker</h1>
        <p>
          Difficulty for the next {{ horizon() }} gameweeks, rated 1 (hardest) to 5 (easiest) by the
          same model that drives predicted points — so the ticker and the projections can never
          disagree.
        </p>
      </div>

      <div class="controls">
        <div class="field">
          <label for="ticker-horizon">Gameweeks</label>
          <select id="ticker-horizon" [ngModel]="horizon()" (ngModelChange)="onHorizon(+$event)">
            @for (n of horizonOptions; track n) {
              <option [value]="n">Next {{ n }}</option>
            }
          </select>
        </div>

        <div class="field">
          <label>Rank by</label>
          <div class="chips">
            <button
              type="button"
              class="chip"
              [class.active]="sortBy() === 'attack'"
              (click)="onSort('attack')"
            >
              Attacking returns
            </button>
            <button
              type="button"
              class="chip"
              [class.active]="sortBy() === 'defence'"
              (click)="onSort('defence')"
            >
              Clean sheets
            </button>
          </div>
        </div>
      </div>

      @if (error(); as message) {
        <div class="notice error">{{ message }}</div>
      } @else if (loading()) {
        <div class="notice"><span class="spinner"></span> Loading fixtures…</div>
      } @else if (ticker(); as data) {
        @if (data.scale['source'] === 'official_fdr') {
          <div class="notice warn source-note">
            <strong>Using the official FDR.</strong> The Premier League only publishes team
            attack/defence strength once results exist, so before the season starts we fall back to
            the official per-fixture difficulty. That is a single number, which is why the attacking
            and clean-sheet columns currently match. Our own ratings take over a few gameweeks in.
          </div>
        }

        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th class="name sticky-col">Team</th>
                @for (event of data.events; track event) {
                  <th>GW{{ event }}</th>
                }
                <th>{{ sortBy() === 'attack' ? 'Attack' : 'Defence' }}</th>
              </tr>
            </thead>
            <tbody>
              @for (row of data.rows; track row.team_id) {
                <tr>
                  <td class="name sticky-col">
                    <strong>{{ row.team_short_name }}</strong>
                    <span class="muted small full-name">{{ row.team_name }}</span>
                  </td>

                  @for (event of data.events; track event) {
                    <td>
                      @if (row.fixtures[event] && row.fixtures[event].length) {
                        @for (fixture of row.fixtures[event]; track fixture.fixture_id) {
                          <span
                            [class]="ratingClass(rating(fixture))"
                            [title]="'Official FDR ' + fixture.official_difficulty"
                          >
                            <strong
                              >{{ fixture.opponent_short_name
                              }}{{ fixture.is_home ? '' : '' }}</strong
                            >
                            <small>{{ fixture.is_home ? 'H' : 'A' }}</small>
                          </span>
                        }
                      } @else {
                        <span class="blank">BGW</span>
                      }
                    </td>
                  }

                  <td class="mono score">
                    {{ (sortBy() === 'attack' ? row.attack_score : row.defence_score).toFixed(1) }}
                  </td>
                </tr>
              }
            </tbody>
          </table>
        </div>

        <p class="small muted legend">
          Greener is better. A double gameweek stacks two cells; a blank shows as BGW. The score
          column sums every fixture in the range, so doubles help and blanks hurt.
        </p>
      }

      @if (defence(); as table) {
        <h2 class="section">Defensive record</h2>
        <p class="small muted legend">
          Who actually keeps clean sheets, across the last two completed seasons — the backward-
          looking counterpart to the ticker above. Recency-weighted
          {{ weightSummary(table) }}, because a defence from two seasons ago tells you less than
          last season's. <strong>xGC per game</strong> is the more honest column: clean sheets are
          lumpy, underlying chances conceded are not.
        </p>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th class="name sticky-col">Club</th>
                <th>CS per 38</th>
                <th>GC / game</th>
                <th>xGC / game</th>
                <th>By season</th>
              </tr>
            </thead>
            <tbody>
              @for (club of table.clubs; track club.team_short_name) {
                <tr [class.unknown]="!club.known">
                  <td class="name sticky-col">{{ club.team_name }}</td>
                  @if (club.known) {
                    <td class="mono strong">{{ club.clean_sheets_per_38 }}</td>
                    <td class="mono">{{ club.goals_conceded_per_game }}</td>
                    <td class="mono">{{ club.expected_goals_conceded_per_game }}</td>
                    <td class="small muted">
                      @for (season of club.seasons; track season.season) {
                        <span class="season"
                          >{{ season.season }}: {{ season.clean_sheets }}CS /
                          {{ season.goals_conceded }}GC</span
                        >
                      }
                    </td>
                  } @else {
                    <td colspan="4" class="small muted">
                      No Premier League record — promoted, so there is nothing to judge them on
                      rather than a bad record.
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
    .unknown td {
      opacity: 0.72;
    }

    .season {
      display: inline-block;
      margin-right: 0.7rem;
      white-space: nowrap;
    }

    .strong {
      font-weight: 700;
      color: var(--accent);
    }

    .source-note {
      margin-bottom: 0.85rem;
    }

    .full-name {
      display: none;
      margin-left: 0.45rem;
    }

    td.score {
      font-weight: 700;
      color: var(--accent);
    }

    .fdr {
      min-width: 52px;
    }

    .fdr + .fdr {
      margin-left: 0.2rem;
    }

    .legend {
      margin-top: 0.6rem;
    }

    @media (min-width: 760px) {
      .full-name {
        display: inline;
      }
    }
  `,
})
export class TickerPage {
  private readonly api = inject(Api);

  protected readonly horizonOptions = [3, 4, 5, 6, 8, 10, 12];
  protected readonly horizon = signal(6);
  protected readonly sortBy = signal<'attack' | 'defence'>('attack');

  protected readonly ticker = signal<Ticker | null>(null);
  protected readonly defence = signal<DefenceTable | null>(null);
  protected readonly loading = signal(true);
  protected readonly error = signal<string | null>(null);

  protected readonly ratingClass = ratingClass;

  /** "65% / 35%" — the weighting, stated rather than buried. */
  protected weightSummary(table: DefenceTable): string {
    return Object.entries(table.season_weights)
      .map(([season, weight]) => `${season} ${Math.round(weight * 100)}%`)
      .join(' / ');
  }

  constructor() {
    this.load();
    // Historical record does not depend on the horizon, so it loads once
    // and survives every re-sort of the ticker above it.
    this.api.defence().subscribe({ next: (data) => this.defence.set(data) });
  }

  private load(): void {
    this.loading.set(true);
    this.error.set(null);
    this.api.ticker(this.horizon(), this.sortBy()).subscribe({
      next: (data) => {
        this.ticker.set(data);
        this.loading.set(false);
      },
      error: (err) => {
        this.error.set(errorMessage(err));
        this.loading.set(false);
      },
    });
  }

  protected onHorizon(value: number): void {
    this.horizon.set(value);
    this.load();
  }

  protected onSort(value: 'attack' | 'defence'): void {
    this.sortBy.set(value);
    this.load();
  }

  /** Colour by whichever side of the ball the user is ranking on. */
  protected rating(fixture: { attack_rating: number; defence_rating: number }): number {
    return this.sortBy() === 'attack' ? fixture.attack_rating : fixture.defence_rating;
  }
}
