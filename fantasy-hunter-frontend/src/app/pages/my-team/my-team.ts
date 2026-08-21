import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { Api } from '../../core/api';
import { MyTeam, SquadMember } from '../../core/models';
import { errorMessage, fdrClass, formatDeadline, isFlagged, statusLabel } from '../../core/ui';

const STORAGE_KEY = 'fh.entry-id';

@Component({
  selector: 'app-my-team',
  imports: [FormsModule],
  template: `
    <div class="page">
      <div class="page-head">
        <h1>My team</h1>
        <p>
          Enter your FPL team ID to get your squad rated, your best XI picked, and transfers ranked
          by expected points. Your ID is in the URL when you view your points on the official site:
          <span class="mono">fantasy.premierleague.com/entry/<strong>1234567</strong>/event/1</span>
        </p>
      </div>

      <form class="controls" (ngSubmit)="load()">
        <div class="field">
          <label for="entry-id">FPL team ID</label>
          <input
            id="entry-id"
            type="text"
            inputmode="numeric"
            placeholder="e.g. 1234567"
            [ngModel]="entryId()"
            (ngModelChange)="entryId.set($event)"
            name="entryId"
          />
        </div>

        <div class="field">
          <label for="mt-horizon">Gameweeks</label>
          <select id="mt-horizon" [ngModel]="horizon()" (ngModelChange)="horizon.set(+$event)" name="horizon">
            @for (n of horizonOptions; track n) {
              <option [value]="n">Next {{ n }}</option>
            }
          </select>
        </div>

        <button class="primary" type="submit" [disabled]="!entryId() || loading()">
          {{ loading() ? 'Loading…' : 'Analyse squad' }}
        </button>
      </form>

      @if (error(); as message) {
        <div class="notice" [class.warn]="isPreSeason()" [class.error]="!isPreSeason()">
          @if (isPreSeason()) {
            <strong>Your squad is not public yet.</strong>
            <p>
              FPL only publishes a manager's picks after a gameweek deadline has passed. Once the
              first deadline goes, this page will fill in. Until then, the
              <strong>Predictions</strong> and <strong>Fixtures</strong> tabs work fully.
            </p>
          } @else {
            {{ message }}
          }
        </div>
      } @else if (loading()) {
        <div class="notice"><span class="spinner"></span> Analysing your squad…</div>
      } @else if (team(); as data) {
        <div class="grid cols-3 summary">
          <div class="card">
            <span class="muted small">Squad rating</span>
            <p class="big mono">{{ data.rating.rating.toFixed(0) }}<span class="unit">/100</span></p>
            <div class="bar"><span [style.width.%]="data.rating.rating"></span></div>
            <p class="small muted note">{{ data.rating.explanation }}</p>
          </div>

          <div class="card">
            <span class="muted small">Best XI over GW{{ data.horizon.events[0] }}–{{
              data.horizon.events[data.horizon.events.length - 1]
            }}</span>
            <p class="big mono">
              {{ data.rating.expected_points_xi.toFixed(1) }}<span class="unit"> xPts</span>
            </p>
            <p class="small muted note">
              Bench adds {{ data.rating.bench_points.toFixed(1) }} · best available XI in the game
              scores {{ data.rating.benchmark_ceiling.toFixed(0) }}
            </p>
          </div>

          <div class="card">
            <span class="muted small">{{ data.entry.name }}</span>
            <p class="big mono">
              {{ data.entry.overall_points ?? '—' }}<span class="unit"> pts</span>
            </p>
            <p class="small muted note">
              {{ data.entry.player_name }} · OR
              {{ data.entry.overall_rank ? data.entry.overall_rank.toLocaleString() : '—' }} · bank
              {{ (data.entry.bank / 10).toFixed(1) }}m · next deadline
              {{ formatDeadline(data.horizon.next_deadline) }}
            </p>
          </div>
        </div>

        <h2 class="section">Starting XI</h2>
        <div class="pitch">
          @for (group of grouped(data.starting_xi); track group.position) {
            <div class="line">
              @for (member of group.members; track member.player_id) {
                <div class="shirt card">
                  <div class="shirt-head">
                    <strong>{{ member.web_name }}</strong>
                    @if (member.is_captain) {
                      <span class="armband" title="Captain">C</span>
                    } @else if (member.is_vice_captain) {
                      <span class="armband vice" title="Vice-captain">V</span>
                    }
                    @if (flagged(member.status)) {
                      <span
                        class="flag flag-{{ member.status }}"
                        [title]="member.news || statusLabel(member.status)"
                      ></span>
                    }
                  </div>
                  <p class="small muted">
                    {{ member.team_short_name }} · {{ member.price.toFixed(1) }}m
                  </p>
                  <p class="xpts mono">{{ member.expected_points.toFixed(1) }}</p>
                  <div class="fixture-strip">
                    @for (fixture of member.fixtures; track fixture.event_id + fixture.opponent) {
                      <span [class]="fdrClass(fixture.difficulty)" [title]="'GW' + fixture.event_id">
                        <small>{{ fixture.opponent }}{{ fixture.is_home ? '' : '' }}</small>
                      </span>
                    }
                  </div>
                </div>
              }
            </div>
          }
        </div>

        <h2 class="section">Bench</h2>
        <div class="bench">
          @for (member of data.bench; track member.player_id) {
            <div class="shirt card">
              <strong>{{ member.web_name }}</strong>
              <p class="small muted">
                {{ member.position }} · {{ member.team_short_name }} ·
                {{ member.price.toFixed(1) }}m
              </p>
              <p class="xpts mono">{{ member.expected_points.toFixed(1) }}</p>
            </div>
          }
        </div>

        <div class="grid cols-2 lower">
          <div>
            <h2 class="section">Captain options</h2>
            <div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th class="name">Player</th>
                    <th>xPts</th>
                    <th>Doubled</th>
                  </tr>
                </thead>
                <tbody>
                  @for (option of data.captain_options; track option.player_id) {
                    <tr>
                      <td class="name">
                        {{ option.web_name }}
                        @if (option.is_current_captain) {
                          <span class="armband">C</span>
                        }
                      </td>
                      <td class="mono">{{ option.expected_points.toFixed(1) }}</td>
                      <td class="mono total">{{ option.doubled_points.toFixed(1) }}</td>
                    </tr>
                  }
                </tbody>
              </table>
            </div>
          </div>

          <div>
            <h2 class="section">Suggested transfers</h2>
            @if (!data.transfer_suggestions.length) {
              <div class="notice">
                No single transfer improves your expected points over this horizon.
              </div>
            } @else {
              <div class="transfers">
                @for (suggestion of data.transfer_suggestions; track suggestion.in_player_id) {
                  <div class="card transfer">
                    <div class="transfer-line">
                      <span class="out">
                        {{ suggestion.out_name }}
                        <span class="muted small"
                          >{{ (suggestion.out_cost / 10).toFixed(1) }}m ·
                          {{ suggestion.out_expected_points.toFixed(1) }} xPts</span
                        >
                      </span>
                      <span class="arrow">→</span>
                      <span class="in">
                        {{ suggestion.in_name }}
                        <span class="muted small"
                          >{{ (suggestion.in_cost / 10).toFixed(1) }}m ·
                          {{ suggestion.in_expected_points.toFixed(1) }} xPts</span
                        >
                      </span>
                    </div>
                    <div class="transfer-foot">
                      <span class="gain mono">+{{ suggestion.gain.toFixed(2) }} xPts</span>
                      <span class="small muted"
                        >{{ (suggestion.spare_after / 10).toFixed(1) }}m left in the bank</span
                      >
                    </div>
                  </div>
                }
              </div>
            }
          </div>
        </div>

        <div class="notice caveats">
          <strong>What this does not know</strong>
          <ul>
            @for (caveat of data.caveats; track caveat) {
              <li>{{ caveat }}</li>
            }
          </ul>
        </div>
      }
    </div>
  `,
  styles: `
    .summary {
      margin-bottom: 1.1rem;
    }

    .big {
      font-size: 1.9rem;
      font-weight: 700;
      margin: 0.2rem 0 0.4rem;
      color: var(--accent);
    }

    .unit {
      font-size: 0.85rem;
      color: var(--muted);
      font-weight: 500;
    }

    .note {
      margin: 0.45rem 0 0;
    }

    h2.section {
      margin: 1.2rem 0 0.5rem;
    }

    .pitch {
      display: flex;
      flex-direction: column;
      gap: 0.55rem;
    }

    .line {
      display: grid;
      gap: 0.5rem;
      grid-template-columns: repeat(auto-fit, minmax(128px, 1fr));
    }

    .bench {
      display: grid;
      gap: 0.5rem;
      grid-template-columns: repeat(auto-fit, minmax(128px, 1fr));
    }

    .shirt {
      padding: 0.55rem;
    }

    .shirt p {
      margin: 0.15rem 0;
    }

    .shirt-head {
      display: flex;
      align-items: center;
      gap: 0.3rem;
    }

    .armband {
      display: inline-grid;
      place-items: center;
      width: 17px;
      height: 17px;
      border-radius: 50%;
      background: var(--accent);
      color: #04150f;
      font-size: 0.62rem;
      font-weight: 800;
    }

    .armband.vice {
      background: var(--muted);
      color: var(--bg);
    }

    .xpts {
      font-size: 1.05rem;
      font-weight: 700;
      color: var(--accent);
    }

    .fixture-strip {
      display: flex;
      flex-wrap: wrap;
      gap: 0.18rem;
    }

    .fixture-strip .fdr {
      min-width: 34px;
      padding: 0.1rem 0.2rem;
    }

    .lower {
      margin-top: 0.4rem;
    }

    td.total {
      font-weight: 700;
      color: var(--accent);
    }

    .transfers {
      display: flex;
      flex-direction: column;
      gap: 0.5rem;
    }

    .transfer-line {
      display: flex;
      align-items: center;
      gap: 0.5rem;
      flex-wrap: wrap;
    }

    .transfer-line span.out,
    .transfer-line span.in {
      display: flex;
      flex-direction: column;
      font-weight: 600;
      font-size: 0.9rem;
    }

    .transfer-line .out {
      color: var(--danger);
    }

    .transfer-line .in {
      color: var(--accent);
    }

    .transfer-line .arrow {
      color: var(--muted);
    }

    .transfer-foot {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-top: 0.45rem;
      padding-top: 0.45rem;
      border-top: 1px solid var(--border);
    }

    .gain {
      font-weight: 700;
      color: var(--accent);
    }

    .caveats {
      margin-top: 1.2rem;
    }

    .caveats ul {
      margin: 0.4rem 0 0;
      padding-left: 1.1rem;
    }

    .caveats li {
      margin: 0.2rem 0;
    }
  `,
})
export class MyTeamPage {
  private readonly api = inject(Api);

  protected readonly horizonOptions = [1, 2, 3, 5, 6, 8];
  protected readonly horizon = signal(5);
  protected readonly entryId = signal('');

  protected readonly team = signal<MyTeam | null>(null);
  protected readonly loading = signal(false);
  protected readonly error = signal<string | null>(null);
  /** 409 means FPL has not published picks yet — a normal pre-season state,
      not a failure, so it gets a softer treatment than a real error. */
  protected readonly isPreSeason = signal(false);

  protected readonly fdrClass = fdrClass;
  protected readonly statusLabel = statusLabel;
  protected readonly flagged = isFlagged;
  protected readonly formatDeadline = formatDeadline;

  constructor() {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) {
      this.entryId.set(saved);
      this.load();
    }
  }

  protected load(): void {
    const id = Number(this.entryId());
    if (!Number.isFinite(id) || id <= 0) {
      this.error.set('That does not look like a team ID. It should be a number.');
      this.isPreSeason.set(false);
      return;
    }

    localStorage.setItem(STORAGE_KEY, String(id));
    this.loading.set(true);
    this.error.set(null);
    this.isPreSeason.set(false);

    this.api.myTeam(id, this.horizon()).subscribe({
      next: (data) => {
        this.team.set(data);
        this.loading.set(false);
      },
      error: (err) => {
        this.isPreSeason.set(err?.status === 409);
        this.error.set(errorMessage(err));
        this.team.set(null);
        this.loading.set(false);
      },
    });
  }

  /** Group the XI into goalkeeper / defence / midfield / attack rows. */
  protected grouped(members: SquadMember[]): { position: string; members: SquadMember[] }[] {
    const order = [
      { position: 'GKP', type: 1 },
      { position: 'DEF', type: 2 },
      { position: 'MID', type: 3 },
      { position: 'FWD', type: 4 },
    ];
    return order
      .map(({ position, type }) => ({
        position,
        members: members.filter((m) => m.element_type === type),
      }))
      .filter((group) => group.members.length > 0);
  }
}
