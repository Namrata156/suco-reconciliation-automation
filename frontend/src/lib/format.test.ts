import { describe, expect, it } from 'vitest';

import { formatAmount, formatBytes, formatDate, formatDateTime, formatRupees } from './format';

describe('formats (design P8)', () => {
  it('uses Indian grouping without float rounding', () => {
    expect(formatRupees('54613083.81')).toBe('Rs 5,46,13,083.81');
    expect(formatRupees('0.5')).toBe('Rs 0.50');
    expect(formatRupees('-1000')).toBe('-Rs 1,000.00');
    // beyond float precision: still exact
    expect(formatAmount('90071992547409931.07')).toBe('90,07,19,92,54,74,09,931.07');
    expect(formatRupees(null)).toBe('-');
  });

  it('shows dates as 14-Jul-2026', () => {
    expect(formatDate('2026-07-14')).toBe('14-Jul-2026');
    expect(formatDate(null)).toBe('-');
  });

  it('shows times in IST', () => {
    expect(formatDateTime('2026-07-14T04:30:00Z')).toBe('14-Jul-2026 10:00 IST');
  });

  it('shows file sizes', () => {
    expect(formatBytes(512)).toBe('512 B');
    expect(formatBytes(65536)).toBe('64.0 KB');
    expect(formatBytes(100427776)).toBe('95.8 MB');
    expect(formatBytes(null)).toBe('-');
  });
});
