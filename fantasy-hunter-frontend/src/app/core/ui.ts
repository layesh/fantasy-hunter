/** Small presentation helpers shared across pages. */

/** Official FDR (1 easiest .. 5 hardest) -> css class. */
export function fdrClass(difficulty: number): string {
  const bucket = Math.min(5, Math.max(1, Math.round(difficulty || 3)));
  return `fdr fdr-${bucket}`;
}

/**
 * Our own 1-5 rating runs the other way — 5 is the *best* fixture — so it is
 * flipped before colouring, keeping green = good across the whole app.
 */
export function ratingClass(rating: number): string {
  const flipped = 6 - Math.min(5, Math.max(1, Math.round(rating || 3)));
  return `fdr fdr-${flipped}`;
}

const STATUS_LABELS: Record<string, string> = {
  a: 'Available',
  d: 'Doubtful',
  i: 'Injured',
  s: 'Suspended',
  u: 'Unavailable',
  n: 'On loan / ineligible',
};

export function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status;
}

export function isFlagged(status: string): boolean {
  return status !== 'a';
}

/** "expected_goal_involvements" -> "Expected goal involvements" */
export function humanise(metric: string): string {
  const text = metric.replace(/_/g, ' ');
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/**
 * Deadlines are UTC instants shown in the reader's own timezone.
 *
 * `new Date()` reads an ISO string *without* an offset as local time, so a
 * missing "Z" silently shifts the deadline by the reader's UTC offset. The API
 * now always sends an offset; this assumes UTC if one is ever absent rather
 * than inheriting the browser's zone. The zone is named in the output because
 * "11:30 PM" is only useful if you know which 11:30 PM it is.
 */
export function formatDeadline(iso: string | null): string {
  if (!iso) return '—';
  const hasOffset = /(?:Z|[+-]\d{2}:?\d{2})$/.test(iso);
  const date = new Date(hasOffset ? iso : `${iso}Z`);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleString(undefined, {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
    timeZoneName: 'short',
  });
}

/** Turn an HttpErrorResponse into something worth showing a user. */
export function errorMessage(error: unknown): string {
  const err = error as { status?: number; error?: { detail?: string }; message?: string };
  if (err?.status === 0) {
    return 'Cannot reach the API. Is the backend running on port 8420?';
  }
  if (err?.error?.detail) {
    return err.error.detail;
  }
  if (err?.status) {
    return `Request failed (HTTP ${err.status}).`;
  }
  return err?.message ?? 'Something went wrong.';
}
