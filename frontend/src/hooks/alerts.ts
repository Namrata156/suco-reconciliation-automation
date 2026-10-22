/**
 * Alerts (design 4.18, Slice 4): the bell's unread count and the list.
 *
 * The bell is on every screen, so its polling follows lib/polling.ts:
 * ALERTS_POLL.everyMs while the user is active, nothing after 2 minutes
 * without input (the 15-minute idle timeout then runs out as designed).
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { getUnreadAlertCount, listAlerts, markAlertRead, markAllAlertsRead } from '../api/client';
import type { AlertFilters } from '../api/types';
import { ALERTS_POLL, pollInterval, useUserActive } from '../lib/polling';

export const ALERTS_KEY = ['alerts'] as const;
export const UNREAD_KEY = ['alerts', 'unread-count'] as const;

export function useUnreadAlertCount() {
  const active = useUserActive();
  return useQuery({
    queryKey: UNREAD_KEY,
    queryFn: getUnreadAlertCount,
    // No retry storm on a broken endpoint: the bell just shows no count.
    retry: false,
    staleTime: ALERTS_POLL.everyMs / 2,
    refetchInterval: () => pollInterval(true, active, ALERTS_POLL),
  });
}

export function useAlerts(filters: AlertFilters) {
  return useQuery({
    queryKey: [...ALERTS_KEY, 'list', filters],
    queryFn: () => listAlerts(filters),
    placeholderData: (prev) => prev,
  });
}

/** Mark one alert or all alerts read; the list and the bell refresh. */
export function useMarkAlertsRead() {
  const qc = useQueryClient();
  const done = () => qc.invalidateQueries({ queryKey: ALERTS_KEY });
  const one = useMutation({ mutationFn: (id: number) => markAlertRead(id), onSuccess: done });
  /** upToId: the newest alert shown, so a later arrival stays unread (contract A4). */
  const all = useMutation({ mutationFn: (upToId?: number) => markAllAlertsRead(upToId), onSuccess: done });
  return { one, all };
}
