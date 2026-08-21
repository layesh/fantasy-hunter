import { Component, computed, inject, signal } from '@angular/core';

import { Api } from '../../core/api';
import { ChipPlan, ChipTiming, GameweekOutlookRow } from '../../core/models';
import { errorMessage } from '../../core/ui';

const CHIP_LABELS: Record<string, string> = {
  wildcard: 'Wildcard',
  bench_boost: 'Bench Boost',
  triple_captain: 'Triple Captain',
  free_hit: 'Free Hit',
};

@Component({
  selector: 'app-chips',
  template: `
    <div class="page">
      <div class="page-head">
        <h1>Chip planner</h1>
        <p>
          Eight chips, two sets. The first four expire at the Gameweek
          {{ plan()?.first_half_ends ?? 19 }} deadline and are lost if unused; the second four run to
          the end of the season. Windows below are FPL's own rules — the timing chart is a prior we
          assembled, and every bar says where it came from.
        </p>
      </div>

      <div class="notice info">
        <strong>This is a prior, not a prediction.</strong>
        <p class="small">
          The chips worth the most are played in double and blank gameweeks, and those do not exist
          yet — they are created during the season by cup progression and postponements. Use this to
          decide roughly <em>when</em> to hold a chip for, then re-evaluate every week once real
          fixtures are rescheduled.
        </p>
      </div>

      @if (error(); as message) {
        <div class="notice error">{{ message }}</div>
      } @else if (loading()) {
        <div class="notice"><span class="spinner"></span> Loading chip windows…</div>
      } @else if (plan(); as data) {
        <div class="chips half-switch">
          @for (option of halves; track option.value) {
            <button
              type="button"
              class="chip"
              [class.active]="half() === option.value"
              (click)="half.set(option.value)"
            >
              {{ option.label }}
            </button>
          }
        </div>

        @for (timing of visible(); track timing.key) {
          <section class="card timing">
            <header>
              <div>
                <strong>{{ label(timing.chip) }}</strong>
                <span class="small muted">
                  playable GW{{ timing.start_event }}–{{ timing.stop_event }}
                </span>
              </div>
              @if (timing.peak_event !== null) {
                <span class="peak mono">
                  most likely GW{{ timing.peak_event }} · {{ pct(timing.peak_share) }}
                </span>
              }
            </header>

            @if (timing.points.length) {
              <ol class="bars">
                @for (point of timing.points; track point.event) {
                  <li [class.top]="point.event === timing.peak_event">
                    <span class="gw mono">GW{{ point.event }}</span>
                    <span class="track">
                      <span class="fill" [style.width.%]="scaled(point.share, timing)"></span>
                    </span>
                    <span class="share mono">{{ pct(point.share) }}</span>
                    <span class="why small muted">{{ point.reasons[0] || '' }}</span>
                  </li>
                }
              </ol>
              @if (timing.sources.length) {
                <p class="small muted sources">Sources: {{ timing.sources.join(' · ') }}</p>
              }
            } @else {
              <p class="small muted">No prior recorded for this chip yet.</p>
            }
          </section>
        }

        @if (outlook().length) {
          <h2 class="section">Double and blank gameweek outlook</h2>
          <p class="small muted legend">
            Prior likelihood that a gameweek becomes a double or a blank, from historical cup
            scheduling. A blank now usually <em>causes</em> a double later, because the postponed
            match is replayed midweek — which is why the two rise together in the run-in. None of
            these are confirmed fixtures.
          </p>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>GW</th>
                  <th>Double</th>
                  <th>Blank</th>
                  <th>Why</th>
                </tr>
              </thead>
              <tbody>
                @for (row of outlook(); track row.event) {
                  <tr>
                    <td class="mono">GW{{ row.event }}</td>
                    <td>
                      <span class="track mini">
                        <span class="fill good" [style.width.%]="row.double * 100"></span>
                      </span>
                      <span class="mono small">{{ pct(row.double) }}</span>
                    </td>
                    <td>
                      <span class="track mini">
                        <span class="fill bad" [style.width.%]="row.blank * 100"></span>
                      </span>
                      <span class="mono small">{{ pct(row.blank) }}</span>
                    </td>
                    <td class="small muted">{{ row.note }}</td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
        }
      }
    </div>
  `,
  styles: `
    .notice.info {
      border-left: 3px solid var(--accent);
    }

    .half-switch {
      margin-bottom: 0.9rem;
    }

    .timing {
      margin-bottom: 0.85rem;
      padding: 0.85rem;
    }

    .timing header {
      display: flex;
      flex-wrap: wrap;
      gap: 0.4rem;
      justify-content: space-between;
      align-items: baseline;
      margin-bottom: 0.6rem;
    }

    .timing header .small {
      margin-left: 0.45rem;
    }

    .peak {
      color: var(--accent);
      font-weight: 700;
      font-size: 0.85rem;
    }

    .bars {
      list-style: none;
      margin: 0;
      padding: 0;
      display: flex;
      flex-direction: column;
      gap: 0.3rem;
    }

    .bars li {
      display: grid;
      grid-template-columns: 3.4rem 1fr 3rem;
      grid-template-areas: 'gw track share' 'why why why';
      align-items: center;
      gap: 0.35rem 0.5rem;
    }

    .gw {
      grid-area: gw;
      font-size: 0.8rem;
    }
    .share {
      grid-area: share;
      text-align: right;
      font-size: 0.8rem;
    }
    .why {
      grid-area: why;
      padding-left: 3.9rem;
      line-height: 1.45;
    }

    .track {
      grid-area: track;
      display: block;
      height: 0.6rem;
      border-radius: 999px;
      background: var(--border);
      overflow: hidden;
    }

    .track.mini {
      display: inline-block;
      width: 4.5rem;
      height: 0.45rem;
      vertical-align: middle;
      margin-right: 0.35rem;
    }

    .fill {
      display: block;
      height: 100%;
      border-radius: 999px;
      background: var(--accent);
    }

    .bars li.top .fill {
      background: var(--good, #24c58a);
    }

    .fill.good {
      background: var(--good, #24c58a);
    }
    .fill.bad {
      background: var(--bad, #d2544b);
    }

    .sources {
      margin: 0.55rem 0 0;
      font-style: italic;
    }

    /* The shared table styles right-align cells for numeric columns; the
       outlook table is mostly prose, so put it back. */
    .table-wrap th,
    .table-wrap td {
      text-align: left;
    }

    .legend {
      margin: -0.4rem 0 0.7rem;
      max-width: 62ch;
      line-height: 1.65;
    }

    /* One column per bar row is unreadable on a phone; drop the reason under. */
    @media (max-width: 40rem) {
      .why {
        padding-left: 0;
      }
    }
  `,
})
export class ChipsPage {
  private readonly api = inject(Api);

  protected readonly plan = signal<ChipPlan | null>(null);
  protected readonly loading = signal(true);
  protected readonly error = signal<string | null>(null);
  protected readonly half = signal(1);

  protected readonly halves = [
    { value: 1, label: 'First half · GW1–19' },
    { value: 2, label: 'Second half · GW20–38' },
  ];

  protected readonly visible = computed(() =>
    (this.plan()?.schedule ?? []).filter((timing) => timing.half === this.half()),
  );

  /** Only the run-in weeks carry an outlook, so show it beside the second half. */
  protected readonly outlook = computed<GameweekOutlookRow[]>(() =>
    this.half() === 2 ? (this.plan()?.outlook ?? []) : [],
  );

  constructor() {
    this.api.chips().subscribe({
      next: (data) => {
        this.plan.set(data);
        this.loading.set(false);
      },
      error: (err) => {
        this.error.set(errorMessage(err));
        this.loading.set(false);
      },
    });
  }

  protected label(chip: string): string {
    return CHIP_LABELS[chip] ?? chip;
  }

  protected pct(share: number | null): string {
    return share === null ? '—' : `${Math.round(share * 100)}%`;
  }

  /**
   * Bars are scaled against the chip's own peak, not against 100%. A chip whose
   * best week is 30% would otherwise render as a row of stubs.
   */
  protected scaled(share: number, timing: ChipTiming): number {
    const peak = timing.peak_share ?? 0;
    return peak > 0 ? Math.max(3, (share / peak) * 100) : 0;
  }
}
