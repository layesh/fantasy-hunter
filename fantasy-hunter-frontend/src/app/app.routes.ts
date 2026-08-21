import { Routes } from '@angular/router';

export const routes: Routes = [
  { path: '', pathMatch: 'full', redirectTo: 'predictions' },
  {
    path: 'predictions',
    title: 'Predicted Points — Fantasy Hunter',
    loadComponent: () => import('./pages/predictions/predictions').then((m) => m.PredictionsPage),
  },
  {
    path: 'ticker',
    title: 'Fixture Ticker — Fantasy Hunter',
    loadComponent: () => import('./pages/ticker/ticker').then((m) => m.TickerPage),
  },
  {
    path: 'players',
    title: 'Players — Fantasy Hunter',
    loadComponent: () => import('./pages/players/players').then((m) => m.PlayersPage),
  },
  {
    path: 'compare',
    title: 'Compare — Fantasy Hunter',
    loadComponent: () => import('./pages/compare/compare').then((m) => m.ComparePage),
  },
  {
    path: 'planner',
    title: 'Optimiser — Fantasy Hunter',
    loadComponent: () => import('./pages/planner/planner').then((m) => m.PlannerPage),
  },
  {
    path: 'chips',
    title: 'Chip Planner — Fantasy Hunter',
    loadComponent: () => import('./pages/chips/chips').then((m) => m.ChipsPage),
  },
  {
    path: 'my-team',
    title: 'My Team — Fantasy Hunter',
    loadComponent: () => import('./pages/my-team/my-team').then((m) => m.MyTeamPage),
  },
  {
    path: 'accuracy',
    title: 'Accuracy — Fantasy Hunter',
    loadComponent: () => import('./pages/accuracy/accuracy').then((m) => m.AccuracyPage),
  },
  { path: '**', redirectTo: 'predictions' },
];
