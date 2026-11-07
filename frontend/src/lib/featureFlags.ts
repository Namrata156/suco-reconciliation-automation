/**
 * Switches for screens that are built but not shown yet. Tests force one on
 * with vi.mock('.../lib/featureFlags', ...).
 */

/**
 * NTSL "GST on fees" display on the Settlement screen (warning only):
 * the per-cycle column, the GST rows, the summary lines and the top notice.
 * Hidden by owner 2026-11-06; backend check still runs.
 */
export const SHOW_GST_ON_FEES = false;
