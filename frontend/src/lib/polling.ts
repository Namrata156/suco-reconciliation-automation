/**
 * Modest auto-refresh for screens that wait on the server (files being
 * processed, a reconciliation run; design 4.9 / 4.11).
 *
 * The idle-timeout rule (design 7.2, P7; Slice 1 note): the server renews
 * the 15-minute session on EVERY request, so a page that polled for ever
 * would keep an unattended session open. Polling therefore runs only while
 * the user has used the mouse or keyboard recently (POLL.stopAfterIdleMs);
 * after that it pauses and the screen offers a "Refresh" button, so the idle
 * timeout counts on from the last automatic refresh. React Query already
 * stops interval refetches while the browser tab is hidden.
 *
 * Use:
 *   const active = useUserActive();
 *   const q = useQuery({ ..., refetchInterval: (q) => pollInterval(wanted(q.state.data), active) });
 *   const polling = pollingState(wanted(q.data), active);   // for <AutoRefresh>
 * React Query re-reads refetchInterval on each render, so polling resumes
 * as soon as the user is active again.
 */
import { useEffect, useState } from 'react';

export const POLL = {
  /** "Every few seconds" (design 4.9). */
  everyMs: 5_000,
  /** Pause after this long without keyboard / mouse input. */
  stopAfterIdleMs: 2 * 60_000,
};

const INPUT_EVENTS = ['pointerdown', 'keydown', 'wheel', 'touchstart'] as const;

/** The refetch interval: POLL.everyMs while wanted and the user is active, else false. */
export function pollInterval(wanted: boolean, active: boolean, cfg = POLL): number | false {
  return wanted && active ? cfg.everyMs : false;
}

export interface Polling {
  /** The interval in use, or false. */
  interval: number | false;
  /** Wanted, but paused because the user has been idle. */
  paused: boolean;
}

export function pollingState(wanted: boolean, active: boolean, cfg = POLL): Polling {
  const interval = pollInterval(wanted, active, cfg);
  return { interval, paused: wanted && !active };
}

/** True while the user used the mouse or keyboard within cfg.stopAfterIdleMs. */
export function useUserActive(cfg = POLL): boolean {
  const [lastInputAt, setLastInputAt] = useState(() => Date.now());
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    let last = 0;
    const onInput = () => {
      const t = Date.now();
      // At most one state update per second, however busy the mouse is.
      if (t - last < 1_000) return;
      last = t;
      setLastInputAt(t);
      setNow(t);
    };
    INPUT_EVENTS.forEach((e) => window.addEventListener(e, onInput, { passive: true }));
    return () => INPUT_EVENTS.forEach((e) => window.removeEventListener(e, onInput));
  }, []);

  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), cfg.everyMs);
    return () => window.clearInterval(t);
  }, [cfg.everyMs]);

  return now - lastInputAt < cfg.stopAfterIdleMs;
}
