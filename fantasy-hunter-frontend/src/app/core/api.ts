import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import {
  AccuracyRecord,
  ChipPlan,
  DefenceTable,
  Comparison,
  GameweekEvent,
  Health,
  MyTeam,
  OptimisationResult,
  OptimiserMeta,
  Paged,
  Player,
  PlayerForecast,
  PredictionTable,
  Team,
  Ticker,
} from './models';

/** Dev server proxies /api to the FastAPI backend — see proxy.conf.json. */
const BASE = '/api';

function params(source: Record<string, string | number | boolean | undefined | null>): HttpParams {
  let result = new HttpParams();
  for (const [key, value] of Object.entries(source)) {
    if (value !== undefined && value !== null && value !== '') {
      result = result.set(key, String(value));
    }
  }
  return result;
}

@Injectable({ providedIn: 'root' })
export class Api {
  private readonly http = inject(HttpClient);

  health(): Observable<Health> {
    return this.http.get<Health>(`${BASE}/health`);
  }

  teams(): Observable<Team[]> {
    return this.http.get<Team[]>(`${BASE}/teams`);
  }

  events(): Observable<GameweekEvent[]> {
    return this.http.get<GameweekEvent[]>(`${BASE}/events`);
  }

  players(query: {
    search?: string;
    position?: string;
    team_id?: number;
    max_price?: number;
    min_price?: number;
    available_only?: boolean;
    sort_by?: string;
    order?: string;
    limit?: number;
    offset?: number;
  }): Observable<Paged<Player>> {
    return this.http.get<Paged<Player>>(`${BASE}/players`, { params: params(query) });
  }

  player(id: number): Observable<Player> {
    return this.http.get<Player>(`${BASE}/players/${id}`);
  }

  predictions(query: {
    horizon?: number;
    position?: string;
    team_id?: number;
    max_price?: number;
    search?: string;
    limit?: number;
    offset?: number;
  }): Observable<PredictionTable> {
    return this.http.get<PredictionTable>(`${BASE}/predictions`, { params: params(query) });
  }

  playerForecast(id: number, horizon: number): Observable<PlayerForecast> {
    return this.http.get<PlayerForecast>(`${BASE}/predictions/player/${id}`, {
      params: params({ horizon }),
    });
  }

  accuracy(): Observable<AccuracyRecord> {
    return this.http.get<AccuracyRecord>(`${BASE}/predictions/accuracy`);
  }

  ticker(horizon: number, sortBy: 'attack' | 'defence'): Observable<Ticker> {
    return this.http.get<Ticker>(`${BASE}/fixtures/ticker`, {
      params: params({ horizon, sort_by: sortBy }),
    });
  }

  compare(ids: number[], horizon: number): Observable<Comparison> {
    return this.http.get<Comparison>(`${BASE}/compare`, {
      params: params({ ids: ids.join(','), horizon }),
    });
  }

  myTeam(entryId: number, horizon: number): Observable<MyTeam> {
    return this.http.get<MyTeam>(`${BASE}/my-team/${entryId}`, { params: params({ horizon }) });
  }

  defence(): Observable<DefenceTable> {
    return this.http.get<DefenceTable>(`${BASE}/fixtures/defence`);
  }

  chips(): Observable<ChipPlan> {
    return this.http.get<ChipPlan>(`${BASE}/chips`);
  }

  optimiserMeta(): Observable<OptimiserMeta> {
    return this.http.get<OptimiserMeta>(`${BASE}/optimizer/meta`);
  }

  bestSquad(query: {
    horizon: number;
    budget: number;
    lock?: string;
    exclude?: string;
    min_start_probability?: number;
  }): Observable<OptimisationResult> {
    return this.http.get<OptimisationResult>(`${BASE}/optimizer/squad`, {
      params: params(query),
    });
  }

  planForEntry(
    entryId: number,
    query: { horizon: number; chips: string; time_limit: number },
  ): Observable<OptimisationResult> {
    return this.http.get<OptimisationResult>(`${BASE}/optimizer/plan/${entryId}`, {
      params: params(query),
    });
  }

  planForSquad(
    body: { squad: number[]; bank: number; free_transfers: number; chips: string[] },
    query: { horizon: number; time_limit: number },
  ): Observable<OptimisationResult> {
    return this.http.post<OptimisationResult>(`${BASE}/optimizer/plan`, body, {
      params: params(query),
    });
  }
}
