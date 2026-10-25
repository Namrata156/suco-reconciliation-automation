import { screen } from '@testing-library/react';
import { Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { MeProvider } from '../auth/auth';
import { BRAND } from '../config';
import { makeMe, renderWithProviders } from '../test/fixtures';
import { AppLayout } from './AppLayout';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('app header logo (design P8, supplied 2026-10-24)', () => {
  it('shows the square bank icon with alt text next to the bank name', () => {
    // The header's own requests (alerts) stay pending; only the markup matters.
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})));
    renderWithProviders(
      <MeProvider me={makeMe(['Maker'])}>
        <Routes>
          <Route path=":module/*" element={<AppLayout />} />
        </Routes>
      </MeProvider>,
      { route: '/upi/cases' },
    );
    const logo = screen.getByRole('img', { name: 'SUCO Bank logo' });
    expect(logo.getAttribute('src')).toBe(BRAND.logoIcon);
    expect(logo.getAttribute('height')).toBe('32');
    expect(screen.getByText('SUCO Bank')).toBeTruthy();
    expect(screen.queryByText('[Bank logo]')).toBeNull();
  });
});
