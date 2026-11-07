import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { renderWithProviders } from '../test/fixtures';
import { DetailsSection } from './DetailsSection';

describe('DetailsSection (collapsed by default, U1)', () => {
  it('starts closed with the heading visible; a click on the heading opens it', async () => {
    renderWithProviders(
      <DetailsSection title="History" id="h-title">
        <table aria-label="Inside">
          <tbody>
            <tr>
              <td>x</td>
            </tr>
          </tbody>
        </table>
      </DetailsSection>,
    );
    const heading = screen.getByRole('heading', { name: 'History', level: 2 });
    const details = heading.closest('details')!;
    expect(details.open).toBe(false);
    expect(screen.getByRole('region', { name: 'History' })).toBeTruthy();
    await userEvent.click(heading);
    expect(details.open).toBe(true);
    expect(screen.getByRole('table', { name: 'Inside' })).toBeTruthy();
  });

  it('defaultOpen starts open', () => {
    renderWithProviders(
      <DetailsSection title="Details" id="d-title" order={3} defaultOpen>
        <p>body</p>
      </DetailsSection>,
    );
    expect(screen.getByRole('heading', { name: 'Details', level: 3 }).closest('details')!.open).toBe(true);
  });
});
