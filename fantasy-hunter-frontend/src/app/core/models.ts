/** Response shapes from the Fantasy Hunter API. */

export type Position = 'GKP' | 'DEF' | 'MID' | 'FWD';

export interface Team {
  id: number;
  name: string;
  short_name: string;
  strength: number;
}

export interface GameweekEvent {
  id: number;
  name: string;
  deadline_time: string | null;
  finished: boolean;
  is_current: boolean;
  is_next: boolean;
  average_entry_score: number | null;
}

export interface Player {
  id: number;
  code: number;
  web_name: string;
  full_name: string;
  team_id: number;
  team_short_name: string | null;
  team_name: string | null;
  position: Position;
  element_type: number;
  now_cost: number;
  price: number;
  cost_change_start: number;
  cost_change_event: number;
  selected_by_percent: number;
  status: string;
  news: string;
  chance_of_playing_next_round: number | null;
  total_points: number;
  event_points: number;
  points_per_game: number;
  form: number;
  minutes: number;
  starts: number;
  goals_scored: number;
  assists: number;
  clean_sheets: number;
  goals_conceded: number;
  saves: number;
  bonus: number;
  bps: number;
  yellow_cards: number;
  red_cards: number;
  defensive_contribution: number;
  expected_goals: number;
  expected_assists: number;
  expected_goal_involvements: number;
  expected_goals_conceded: number;
  influence: number;
  creativity: number;
  threat: number;
  ict_index: number;
  penalties_order: number | null;
  corners_and_indirect_freekicks_order: number | null;
  direct_freekicks_order: number | null;
  transfers_in_event: number;
  transfers_out_event: number;
  photo_code: string;
  past_seasons?: PastSeason[];
}

export interface PastSeason {
  season_name: string;
  total_points: number;
  minutes: number;
  starts?: number;
  goals_scored: number;
  assists: number;
  clean_sheets?: number;
  saves?: number;
  bonus?: number;
  defensive_contribution?: number;
  expected_goals?: number;
  expected_assists?: number;
  start_cost?: number;
  end_cost?: number;
}

export interface Paged<T> {
  total: number;
  limit: number;
  offset: number;
  results: T[];
}

/* --- predictions -------------------------------------------------------- */

export interface FixtureForecast {
  fixture_id: number;
  opponent: string;
  is_home: boolean;
  difficulty: number;
  expected_minutes: number;
  expected_points: number;
}

export interface EventForecast {
  expected_points: number;
  fixtures: FixtureForecast[];
}

export interface PredictionRow {
  player_id: number;
  web_name: string;
  team_short_name: string | null;
  position: Position;
  price: number;
  status: string;
  selected_by_percent: number;
  expected_points_total: number;
  value: number;
  by_event: Record<string, EventForecast>;
}

export interface PredictionTable {
  model_version: string;
  events: number[];
  total: number;
  limit: number;
  offset: number;
  results: PredictionRow[];
}

/** The component breakdown behind a single fixture's xPts. */
export interface PredictionComponents {
  availability: number;
  p_start: number;
  p_60: number;
  attack_multiplier: number;
  expected_goals_conceded: number;
  p_clean_sheet: number;
  x_goals: number;
  x_assists: number;
  p_defensive_contribution: number;
  profile_source: string;
  fixture_model: string;
  points: Record<string, number>;
}

export interface PlayerForecastFixture {
  fixture_id: number;
  event_id: number;
  opponent_team_id: number;
  opponent: string;
  is_home: boolean;
  difficulty: number;
  expected_minutes: number;
  expected_points: number;
  components: PredictionComponents;
}

export interface PlayerForecast {
  model_version: string;
  player_id: number;
  web_name: string;
  position: Position;
  profile: {
    minutes_per_game: number;
    goals_per90: number;
    assists_per90: number;
    saves_per90: number;
    dc_per90: number;
    bonus_per90: number;
    yellow_per90: number;
    sample_minutes: number;
    source: string;
  };
  expected_points_total: number;
  fixtures: PlayerForecastFixture[];
}

export interface AccuracyRecord {
  model_version: string;
  gameweeks: {
    event_id: number;
    graded_predictions: number;
    mean_absolute_error: number;
    mean_error: number;
  }[];
  note: string;
}

/* --- fixture ticker ------------------------------------------------------ */

export interface TickerFixture {
  event_id: number;
  fixture_id: number;
  opponent_id: number;
  opponent_short_name: string;
  is_home: boolean;
  official_difficulty: number;
  attack_rating: number;
  defence_rating: number;
  kickoff_time: string | null;
}

export interface TickerRow {
  team_id: number;
  team_name: string;
  team_short_name: string;
  attack_score: number;
  defence_score: number;
  fixture_count: number;
  fixtures: Record<string, TickerFixture[]>;
}

export interface Ticker {
  events: number[];
  scale: { source: string; note: string; [k: string]: string };
  rows: TickerRow[];
}

/* --- comparison ---------------------------------------------------------- */

export interface ComparisonMetric {
  metric: string;
  /** null when the metric has no better direction (price, ownership). */
  higher_is_better: boolean | null;
  values: (number | string | null)[];
  winner_player_id: number | null;
}

export interface Comparison {
  events: number[];
  players: (Player & {
    expected_points_total: number;
    upcoming: {
      event_id: number;
      opponent: string;
      is_home: boolean;
      difficulty: number;
      expected_points: number;
    }[];
    past_seasons: PastSeason[];
  })[];
  metrics: ComparisonMetric[];
}

/* --- my team ------------------------------------------------------------- */

export interface SquadMember {
  player_id: number;
  web_name: string;
  position: Position;
  element_type: number;
  team_short_name: string | null;
  price: number;
  status: string;
  news: string;
  pick_position: number;
  is_captain: boolean;
  is_vice_captain: boolean;
  expected_points: number;
  fixtures: {
    event_id: number;
    opponent: string;
    is_home: boolean;
    difficulty: number;
    expected_minutes: number;
    expected_points: number;
  }[];
}

export interface TransferSuggestion {
  out_player_id: number;
  out_name: string;
  out_cost: number;
  out_expected_points: number;
  in_player_id: number;
  in_name: string;
  in_cost: number;
  in_expected_points: number;
  gain: number;
  spare_after: number;
  reason: string;
}

export interface MyTeam {
  model_version: string;
  entry: {
    id: number;
    name: string;
    player_name: string;
    overall_rank: number | null;
    overall_points: number | null;
    current_event: number | null;
    bank: number;
    squad_value: number | null;
    free_transfers: number | null;
  };
  horizon: { events: number[]; next_deadline: string | null };
  rating: {
    rating: number;
    expected_points_xi: number;
    benchmark_ceiling: number;
    benchmark_floor: number;
    bench_points: number;
    explanation: string;
  };
  starting_xi: SquadMember[];
  bench: SquadMember[];
  captain_options: {
    player_id: number;
    web_name: string;
    expected_points: number;
    doubled_points: number;
    is_current_captain: boolean;
  }[];
  transfer_suggestions: TransferSuggestion[];
  caveats: string[];
}

/* --- optimiser ----------------------------------------------------------- */

export type Chip = 'wildcard' | 'bench_boost' | 'triple_captain' | 'free_hit';

export interface SquadPick {
  player_id: number;
  web_name: string;
  position: Position;
  team_short_name: string | null;
  cost: number;
  expected_points: number;
  /**
   * Fraction of pre-season predicted-XI sources that start this player.
   * `null` means unknown — no consensus data — which is NOT the same as 0.
   */
  start_probability: number | null;
  /** Set-piece duty from FPL's own ordering; 1 is the club's first choice. */
  penalties_order: number | null;
  direct_freekicks_order: number | null;
  corners_order: number | null;
}

export interface GameweekPlan {
  event_id: number;
  chip: Chip | null;
  transfers_in: SquadPick[];
  transfers_out: SquadPick[];
  hits: number;
  points_cost: number;
  starting_xi: SquadPick[];
  bench: SquadPick[];
  captain: SquadPick | null;
  expected_points: number;
  bank: number;
  free_transfers_available: number;
}

export interface OptimisationResult {
  model_version: string;
  events: number[];
  squad: SquadPick[];
  gameweeks: GameweekPlan[];
  expected_points: number;
  points_spent_on_hits: number;
  status: string;
  notes: string[];
  entry?: { id: number; name: string; bank: number; squad_value: number | null };
}

export interface OptimiserMeta {
  chips: Chip[];
  default_budget: number;
  notes: string[];
}

export interface Health {
  status: string;
  players: number;
  last_ingest: {
    source: string;
    ok: boolean;
    rows: number;
    started_at: string | null;
    detail: string;
  } | null;
}

export interface ChipWindowRow {
  key: string;
  chip: string;
  half: number;
  chip_type: string;
  start_event: number;
  stop_event: number;
}

export interface ChipTimingPoint {
  event: number;
  share: number;
  reasons: string[];
  deadline: string | null;
}

export interface ChipTiming {
  key: string;
  chip: string;
  half: number;
  start_event: number;
  stop_event: number;
  peak_event: number | null;
  peak_share: number | null;
  points: ChipTimingPoint[];
  sources: string[];
}

export interface GameweekOutlookRow {
  event: number;
  double: number;
  blank: number;
  /** False while this is a prior rather than a rescheduled fixture. */
  confirmed: boolean;
  note: string;
}

export interface ChipPlan {
  first_half_ends: number;
  windows: ChipWindowRow[];
  schedule: ChipTiming[];
  outlook: GameweekOutlookRow[];
}

export interface ClubDefenceSeason {
  season: string;
  matches: number;
  clean_sheets: number;
  goals_conceded: number;
  expected_goals_conceded: number;
}

export interface ClubDefence {
  team_id: number | null;
  team_short_name: string;
  team_name: string;
  /** False for promoted clubs — no record, which is not the same as a bad one. */
  known: boolean;
  clean_sheets_per_38: number | null;
  goals_conceded_per_game: number | null;
  expected_goals_conceded_per_game: number | null;
  seasons: ClubDefenceSeason[];
}

export interface DefenceTable {
  season_weights: Record<string, number>;
  note: string;
  clubs: ClubDefence[];
}
