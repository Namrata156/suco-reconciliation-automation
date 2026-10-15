import { describe, expect, it } from 'vitest';

import { safeNext } from './auth';
import { formatRemaining, sessionPhase, sessionTimes } from './SessionWatcher';

describe('session timeout (design 7.2, P7)', () => {
  const times = sessionTimes({
    idle_timeout_seconds: 900,
    warning_seconds: 120,
    expires_at_browser_close: true,
  });

  it('warns at 13 minutes and expires at 15, from /me session', () => {
    expect(times).toEqual({ idleMs: 900_000, warnAtMs: 780_000 });
    expect(sessionPhase(12 * 60_000, times)).toBe('active');
    expect(sessionPhase(13 * 60_000, times)).toBe('warning');
    expect(sessionPhase(15 * 60_000, times)).toBe('expired');
  });

  it('falls back to 15/13 minutes without session data', () => {
    expect(sessionTimes(undefined)).toEqual({ idleMs: 900_000, warnAtMs: 780_000 });
  });

  it('formats the countdown', () => {
    expect(formatRemaining(119_500)).toBe('2:00');
    expect(formatRemaining(5_000)).toBe('0:05');
  });

  it('keeps the return address on this site only', () => {
    expect(safeNext('/upi/cases/12?x=1')).toBe('/upi/cases/12?x=1');
    expect(safeNext('//evil.example')).toBe('/');
    expect(safeNext('https://evil.example')).toBe('/');
    expect(safeNext('/login?next=/x')).toBe('/');
    expect(safeNext(null)).toBe('/');
  });
});
