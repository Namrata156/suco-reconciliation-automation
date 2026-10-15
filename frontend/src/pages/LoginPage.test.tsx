import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '../api/client';
import { makeMe, renderWithProviders } from '../test/fixtures';
import { LoginForm, loginErrorMessage } from './LoginPage';

function mockFetch(responses: { status: number; body?: unknown }[]) {
  const fn = vi.fn();
  for (const r of responses) {
    fn.mockResolvedValueOnce(
      new Response(r.body === undefined ? null : JSON.stringify(r.body), {
        status: r.status,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
  }
  vi.stubGlobal('fetch', fn);
  return fn;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('LoginForm (design 4.1)', () => {
  it('requires username and password', async () => {
    const onSuccess = vi.fn();
    renderWithProviders(<LoginForm onSuccess={onSuccess} />);
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByText('Enter your username')).toBeTruthy();
    expect(screen.getByText('Enter your password')).toBeTruthy();
    expect(onSuccess).not.toHaveBeenCalled();
  });

  it('fetches a CSRF token, then logs in with X-CSRFToken', async () => {
    document.cookie = 'csrftoken=abc123';
    const fetch = mockFetch([
      { status: 200, body: { csrf_token: 'abc123' } },
      { status: 200, body: makeMe(['Maker']) },
    ]);
    const onSuccess = vi.fn();
    renderWithProviders(<LoginForm onSuccess={onSuccess} />);
    await userEvent.type(screen.getByLabelText(/Username/), 'maker1');
    await userEvent.type(screen.getByLabelText(/Password/), 'secret');
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    await waitFor(() => expect(onSuccess).toHaveBeenCalled());
    expect(fetch.mock.calls[0]?.[0]).toBe('/api/v1/auth/csrf/');
    const [url, init] = fetch.mock.calls[1] as [string, RequestInit];
    expect(url).toBe('/api/v1/auth/login/');
    expect((init.headers as Record<string, string>)['X-CSRFToken']).toBe('abc123');
    expect(init.credentials).toBe('same-origin');
    expect(JSON.parse(init.body as string)).toEqual({ username: 'maker1', password: 'secret' });
  });

  it('says only "Invalid credentials" on a failed login', async () => {
    mockFetch([
      { status: 200, body: {} },
      { status: 400, body: { detail: 'Invalid credentials' } },
    ]);
    renderWithProviders(<LoginForm onSuccess={vi.fn()} />);
    await userEvent.type(screen.getByLabelText(/Username/), 'maker1');
    await userEvent.type(screen.getByLabelText(/Password/), 'wrong');
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByText('Invalid credentials')).toBeTruthy();
  });

  it('maps the other answers', () => {
    expect(
      loginErrorMessage(
        new ApiError(403, { detail: 'This account has no role in the reconciliation system.' }, ''),
      ),
    ).toMatch(/no role/);
    expect(loginErrorMessage(new ApiError(403, { detail: 'CSRF Failed: token missing.' }, ''))).toMatch(
      /Reload the page/,
    );
    expect(loginErrorMessage(new TypeError('network'))).toMatch(/could not be reached/);
  });
});
