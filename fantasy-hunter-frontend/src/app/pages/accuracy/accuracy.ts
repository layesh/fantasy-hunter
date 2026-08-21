import { Component, inject, signal } from '@angular/core';

import { Api } from '../../core/api';
import { AccuracyRecord } from '../../core/models';
import { errorMessage } from '../../core/ui';

@Component({
  selector: 'app-accuracy',
  template: `
    <div class="page">
      <div class="page-head">
        <h1>Accuracy record</h1>
        <p>
          Most FPL tools assert their predictions are good. This page is the receipt. Predictions
          are written before each deadline, never edited afterwards, and graded against what
          actually happened.
        </p>
      </div>

      @if (error(); as message) {
        <div class="notice error">{{ message }}</div>
      } @else if (loading()) {
        <div class="notice"><span class="spinner"></span> Loading record…</div>
      } @else if (record(); as data) {
        @if (data.gameweeks.length === 0) {
          <div class="notice">
            <strong>Nothing graded yet.</strong>
            <p>
              No gameweek has finished since predictions started being recorded, so there is
              genuinely no accuracy data to show. It will appear here after the first gameweek is
              graded — publishing a number before then would be exactly the thing this page exists
              to avoid.
            </p>
            <p class="small muted">
              Model <span class="mono">{{ data.model_version }}</span> · {{ data.note }}
            </p>
          </div>
        } @else {
          <div class="grid cols-3 summary">
            <div class="card">
              <span class="muted small">Gameweeks graded</span>
              <p class="big mono">{{ data.gameweeks.length }}</p>
            </div>
            <div class="card">
              <span class="muted small">Mean absolute error</span>
              <p class="big mono">{{ overallMae(data).toFixed(2) }}</p>
              <p class="small muted note">Average points between prediction and reality.</p>
            </div>
            <div class="card">
              <span class="muted small">Bias</span>
              <p class="big mono">{{ overallBias(data) > 0 ? '+' : ''
                }}{{ overallBias(data).toFixed(2) }}</p>
              <p class="small muted note">
                Positive means the model is optimistic; negative means it under-rates.
              </p>
            </div>
          </div>

          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th class="name sticky-col">Gameweek</th>
                  <th>Predictions graded</th>
                  <th>Mean absolute error</th>
                  <th>Bias</th>
                </tr>
              </thead>
              <tbody>
                @for (week of data.gameweeks; track week.event_id) {
                  <tr>
                    <td class="name sticky-col">GW{{ week.event_id }}</td>
                    <td class="mono">{{ week.graded_predictions }}</td>
                    <td class="mono">{{ week.mean_absolute_error.toFixed(2) }}</td>
                    <td class="mono">
                      {{ week.mean_error > 0 ? '+' : '' }}{{ week.mean_error.toFixed(2) }}
                    </td>
                  </tr>
                }
              </tbody>
            </table>
          </div>

          <p class="small muted note">
            Model <span class="mono">{{ data.model_version }}</span> · {{ data.note }}
          </p>
        }
      }
    </div>
  `,
  styles: `
    .summary {
      margin-bottom: 1rem;
    }

    .big {
      font-size: 1.9rem;
      font-weight: 700;
      margin: 0.2rem 0 0;
      color: var(--accent);
    }

    .note {
      margin: 0.4rem 0 0;
    }

    .notice p {
      margin: 0.5rem 0 0;
    }
  `,
})
export class AccuracyPage {
  private readonly api = inject(Api);

  protected readonly record = signal<AccuracyRecord | null>(null);
  protected readonly loading = signal(true);
  protected readonly error = signal<string | null>(null);

  constructor() {
    this.api.accuracy().subscribe({
      next: (data) => {
        this.record.set(data);
        this.loading.set(false);
      },
      error: (err) => {
        this.error.set(errorMessage(err));
        this.loading.set(false);
      },
    });
  }

  /** Weighted by how many predictions each gameweek contributed. */
  private weighted(data: AccuracyRecord, pick: (week: AccuracyRecord['gameweeks'][0]) => number) {
    const total = data.gameweeks.reduce((sum, week) => sum + week.graded_predictions, 0);
    if (!total) return 0;
    return (
      data.gameweeks.reduce((sum, week) => sum + pick(week) * week.graded_predictions, 0) / total
    );
  }

  protected overallMae(data: AccuracyRecord): number {
    return this.weighted(data, (week) => week.mean_absolute_error);
  }

  protected overallBias(data: AccuracyRecord): number {
    return this.weighted(data, (week) => week.mean_error);
  }
}
