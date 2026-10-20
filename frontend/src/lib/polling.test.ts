import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { POLL, pollingState, pollInterval, useUserActive } from './polling';

afterEach(() => {
  vi.useRealTimers();
});

describe('polling within the idle timeout (design 7.2)', () => {
  it('polls only while wanted and the user is active', () => {
    expect(pollInterval(true, true)).toBe(POLL.everyMs);
    expect(pollInterval(true, false)).toBe(false);
    expect(pollInterval(false, true)).toBe(false);
    expect(pollingState(true, false)).toEqual({ interval: false, paused: true });
    expect(pollingState(false, false)).toEqual({ interval: false, paused: false });
  });

  it('turns inactive after stopAfterIdleMs without input, active again on input', () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useUserActive());
    expect(result.current).toBe(true);
    act(() => {
      vi.advanceTimersByTime(POLL.stopAfterIdleMs + POLL.everyMs);
    });
    expect(result.current).toBe(false);
    act(() => {
      window.dispatchEvent(new Event('keydown'));
    });
    expect(result.current).toBe(true);
  });
});
