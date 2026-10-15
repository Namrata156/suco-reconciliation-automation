/**
 * Session timeout (design 7.2, P7): warns 2 minutes before the 15-minute
 * idle timeout and, once it has passed (or any request answers 401), returns
 * to the login page keeping the address, so the user lands back where they
 * were.
 */
import { Button, Group, Modal, Text } from '@mantine/core';
import { useCallback, useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router';

import { keepAlive, logout, sessionClock } from '../api/client';
import type { Me } from '../api/types';
import { SESSION } from '../config';
import { loginPath, useMe, useResetSession } from './auth';

export type SessionPhase = 'active' | 'warning' | 'expired';

export interface SessionTimes {
  idleMs: number;
  warnAtMs: number;
}

/** Timings from /me "session" (900 s idle, warn 120 s before = 13 min); config fallback. */
export function sessionTimes(session: Me['session'] | undefined): SessionTimes {
  const idle = session?.idle_timeout_seconds;
  const warn = session?.warning_seconds;
  if (!idle || idle <= 0) return { idleMs: SESSION.idleMs, warnAtMs: SESSION.warnAtMs };
  const warnBefore = warn && warn > 0 && warn < idle ? warn : (SESSION.idleMs - SESSION.warnAtMs) / 1000;
  return { idleMs: idle * 1000, warnAtMs: (idle - warnBefore) * 1000 };
}

export function sessionPhase(idleMs: number, cfg: SessionTimes = SESSION): SessionPhase {
  if (idleMs >= cfg.idleMs) return 'expired';
  if (idleMs >= cfg.warnAtMs) return 'warning';
  return 'active';
}

export function formatRemaining(ms: number): string {
  const s = Math.max(0, Math.ceil(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

export function SessionWatcher() {
  const me = useMe();
  const times = sessionTimes(me.session);
  const navigate = useNavigate();
  const location = useLocation();
  const reset = useResetSession();
  const [now, setNow] = useState(() => Date.now());
  const [busy, setBusy] = useState(false);

  const here = location.pathname + location.search;

  const expire = useCallback(() => {
    reset();
    navigate(loginPath(here, true), { replace: true });
  }, [navigate, here, reset]);

  useEffect(() => sessionClock.onExpired(expire), [expire]);
  useEffect(() => sessionClock.onActivity(() => setNow(Date.now())), []);
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), SESSION.checkEveryMs);
    return () => window.clearInterval(t);
  }, []);

  const idle = now - sessionClock.lastRequestAt();
  const phase = sessionPhase(idle, times);

  useEffect(() => {
    if (phase === 'expired') expire();
  }, [phase, expire]);

  const stay = async () => {
    setBusy(true);
    try {
      await keepAlive();
    } catch {
      // a 401 has already raised the expiry signal
    } finally {
      setBusy(false);
    }
  };

  const signOut = async () => {
    try {
      await logout();
    } finally {
      reset();
      navigate('/login', { replace: true });
    }
  };

  return (
    <Modal
      opened={phase === 'warning'}
      onClose={stay}
      title="Your session is about to end"
      closeOnClickOutside={false}
      centered
    >
      <Text role="status" aria-live="polite">
        You have been inactive. You will be signed out in {formatRemaining(times.idleMs - idle)}.
      </Text>
      <Group justify="flex-end" mt="md">
        <Button variant="default" onClick={signOut}>
          Sign out now
        </Button>
        <Button onClick={stay} loading={busy} data-autofocus>
          Stay signed in
        </Button>
      </Group>
    </Modal>
  );
}
