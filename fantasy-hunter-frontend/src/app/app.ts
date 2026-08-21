import { Component, inject, signal } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

import { Api } from './core/api';
import { GameweekEvent } from './core/models';
import { formatDeadline } from './core/ui';

interface NavItem {
  path: string;
  label: string;
}

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  templateUrl: './app.html',
  styleUrl: './app.scss',
})
export class App {
  private readonly api = inject(Api);

  protected readonly nav: NavItem[] = [
    { path: '/predictions', label: 'Predictions' },
    { path: '/ticker', label: 'Fixtures' },
    { path: '/players', label: 'Players' },
    { path: '/compare', label: 'Compare' },
    { path: '/planner', label: 'Optimiser' },
    { path: '/chips', label: 'Chips' },
    { path: '/my-team', label: 'My Team' },
    { path: '/accuracy', label: 'Accuracy' },
  ];

  protected readonly nextEvent = signal<GameweekEvent | null>(null);
  protected readonly offline = signal(false);
  protected readonly formatDeadline = formatDeadline;

  constructor() {
    this.api.events().subscribe({
      next: (events) => {
        const upcoming =
          events.find((e) => e.is_current && !e.finished) ??
          events.find((e) => e.is_next) ??
          events.find((e) => !e.finished) ??
          null;
        this.nextEvent.set(upcoming);
      },
      error: () => this.offline.set(true),
    });
  }
}
