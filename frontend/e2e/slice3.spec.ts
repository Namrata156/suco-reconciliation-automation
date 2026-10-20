/**
 * Phase 5 Slice 3 click-through (design section 5):
 *   1. Open the 14 Jul results: the summary and the paged list, then the
 *      settlement (12/12 cycles PASS, unexplained Rs 0.00).
 *   2. Propose a tolerance change as Administrator and approve it as
 *      Checker; the dates settled with the older tolerance are listed, each
 *      linking to its overview (no automatic re-run, owner 2026-10-20).
 *
 * Prerequisites:
 *   - a database where 14 Jul has been run (e.g. the Slice 2 database copy)
 *     and NO tolerance change is pending for the module
 *   - Django (+ the Vite dev server, or the built SPA served by Django)
 *   - E2E_ADMIN_USER / E2E_ADMIN_PASSWORD: Administrator only
 *   - E2E_CHECKER_USER / E2E_CHECKER_PASSWORD: Checker only (so the approval
 *     is not a self-approval)
 *   - optional: E2E_MODULE (default upi)
 * It CHANGES the module's tolerance (a new ACTIVE version): run it against a
 * database copy, not dev data. It alternates the amount between Rs 1.00 and
 * Rs 2.00, so it can be run again.
 *
 * Run only this spec:  npx playwright test e2e/slice3.spec.ts
 * Every wait has its own timeout (as in slice2.spec.ts), so a missing
 * element fails at once with its own message.
 */
import { expect, test, type Page } from '@playwright/test';

const MODULE = (process.env.E2E_MODULE ?? 'upi').toLowerCase();
const ADMIN = { user: process.env.E2E_ADMIN_USER ?? '', password: process.env.E2E_ADMIN_PASSWORD ?? '' };
const CHECKER = {
  user: process.env.E2E_CHECKER_USER ?? '',
  password: process.env.E2E_CHECKER_PASSWORD ?? '',
};
const DATE = '2026-07-14';
const DATE_SHOWN = '14-Jul-2026';

/** Page loads and server answers. */
const LOAD = 30_000;
/** Something already on screen. */
const SHORT = 10_000;

test.describe.configure({ mode: 'serial' });
test.setTimeout(5 * 60_000);

test.beforeAll(() => {
  if (!ADMIN.user || !ADMIN.password || !CHECKER.user || !CHECKER.password) {
    throw new Error('Set E2E_ADMIN_USER, E2E_ADMIN_PASSWORD, E2E_CHECKER_USER and E2E_CHECKER_PASSWORD');
  }
});

async function login(page: Page, who: { user: string; password: string }) {
  await page.goto('/login', { timeout: LOAD });
  await page.getByLabel('Username').fill(who.user, { timeout: SHORT });
  await page.getByLabel('Password').fill(who.password, { timeout: SHORT });
  await page.getByRole('button', { name: 'Sign in' }).click({ timeout: SHORT });
  await expect(page.getByRole('heading', { name: /My work/ })).toBeVisible({ timeout: LOAD });
}

async function logout(page: Page) {
  await page.getByRole('button', { name: /^User menu for/ }).click({ timeout: SHORT });
  await page.getByRole('menuitem', { name: 'Sign out' }).click({ timeout: SHORT });
  await expect(page.getByRole('button', { name: 'Sign in' })).toBeVisible({ timeout: LOAD });
}

interface ToleranceRead {
  active: { version: number; amount: string } | null;
  default_if_none: { amount: string };
  pending: { id: number; version: number } | null;
}

/** GET /settings/tolerance/ with the page's own session (a read: no CSRF needed). */
async function readTolerance(page: Page): Promise<ToleranceRead> {
  const r = await page.request.get(`/api/v1/${MODULE}/settings/tolerance/`, { timeout: LOAD });
  expect(r.status(), 'tolerance read').toBe(200);
  return (await r.json()) as ToleranceRead;
}

test('14 Jul results and settlement: 12/12 cycles PASS, unexplained Rs 0.00', async ({ page }) => {
  await login(page, ADMIN);

  // The date overview carries the results summary.
  await page.goto(`/${MODULE}/dates/${DATE}`, { timeout: LOAD });
  await expect(page.getByRole('heading', { name: `Business date ${DATE_SHOWN}` })).toBeVisible({
    timeout: LOAD,
  });
  await expect(page.getByRole('heading', { name: 'Reconciliation results' })).toBeVisible({ timeout: SHORT });
  await expect(page.getByRole('table', { name: `Results per rule for ${DATE_SHOWN}` })).toBeVisible({
    timeout: LOAD,
  });

  // The paged list: one page of 50 out of ~69,000 groups.
  await page.getByRole('link', { name: 'Open the results list' }).click({ timeout: SHORT });
  await expect(page.getByRole('heading', { name: `Reconciliation results ${DATE_SHOWN}` })).toBeVisible({
    timeout: LOAD,
  });
  const results = page.getByRole('table', { name: 'Reconciliation results' });
  await expect(page.getByText(/^Showing 1-50 of [\d,]+ match groups$/)).toBeVisible({ timeout: LOAD });
  await expect(results.getByRole('row')).toHaveCount(51, { timeout: SHORT });

  // One match group's detail.
  await results.getByRole('link').first().click({ timeout: SHORT });
  await expect(page.getByRole('heading', { name: /^Match group / })).toBeVisible({ timeout: LOAD });
  await expect(page.getByRole('table', { name: 'Legs' })).toBeVisible({ timeout: SHORT });

  // The settlement of the date.
  await page.goto(`/${MODULE}/dates/${DATE}`, { timeout: LOAD });
  await page.getByRole('link', { name: 'Open the settlement' }).click({ timeout: LOAD });
  await expect(page.getByRole('heading', { name: `Settlement ${DATE_SHOWN}` })).toBeVisible({
    timeout: LOAD,
  });
  const summary = page.getByRole('group', { name: 'Settlement summary' });
  await expect(summary.getByText('Self-check: 12 of 12 cycles PASS')).toBeVisible({ timeout: SHORT });
  await expect(summary.getByText(/^Unexplained: Rs 0\.00 /)).toBeVisible({ timeout: SHORT });
  await expect(summary.getByText('Tallied')).toBeVisible({ timeout: SHORT });
  await expect(page.getByRole('table', { name: 'NTSL self-check per cycle' }).getByText('PASS')).toHaveCount(
    12,
    {
      timeout: SHORT,
    },
  );
  await expect(page.getByRole('table', { name: 'Contributing transactions' })).toBeVisible({ timeout: LOAD });
});

test('Administrator proposes a tolerance change; Checker approves it', async ({ page }) => {
  await login(page, ADMIN);
  const before = await readTolerance(page);
  if (before.pending) {
    throw new Error(
      `Tolerance v${before.pending.version} is already pending: decide it first (see the header)`,
    );
  }
  const current = (before.active ?? before.default_if_none).amount;
  const amount = current === '1.00' ? '2.00' : '1.00';
  const reason = `E2E Slice 3 click-through ${new Date().toISOString()}`;

  // 1. Administrator proposes.
  await page.goto(`/${MODULE}/settings/tolerance`, { timeout: LOAD });
  await page.getByRole('button', { name: 'Propose tolerance change' }).click({ timeout: LOAD });
  const proposeDialog = page.getByRole('dialog');
  await expect(proposeDialog).toBeVisible({ timeout: SHORT });
  await proposeDialog.getByLabel('Tolerance amount (Rs)').fill(amount, { timeout: SHORT });
  await proposeDialog.getByLabel('Reason').fill(reason, { timeout: SHORT });
  await proposeDialog.getByRole('button', { name: 'Submit tolerance proposal' }).click({ timeout: SHORT });
  await expect(proposeDialog).toBeHidden({ timeout: LOAD });

  const proposed = await readTolerance(page);
  expect(proposed.pending, 'the proposal is pending').not.toBeNull();
  const version = proposed.pending!.version;
  await expect(page.getByRole('heading', { name: `Pending: tolerance v${version}` })).toBeVisible({
    timeout: LOAD,
  });
  // The Administrator cannot approve (only Checkers decide).
  await expect(page.getByRole('button', { name: `Approve tolerance v${version}` })).toHaveCount(0, {
    timeout: SHORT,
  });
  await logout(page);

  // 2. Checker approves from the Pending approvals screen.
  await login(page, CHECKER);
  await page
    .getByRole('navigation', { name: 'Main menu' })
    .getByRole('link', { name: 'Pending approvals' })
    .click({
      timeout: SHORT,
    });
  await expect(page.getByRole('heading', { name: 'Pending approvals' })).toBeVisible({ timeout: LOAD });
  const approve = page.getByRole('button', { name: new RegExp(`^Approve Tolerance v${version}:`) });
  await approve.click({ timeout: LOAD });
  const approveDialog = page.getByRole('dialog');
  await expect(approveDialog).toBeVisible({ timeout: SHORT });
  await approveDialog.getByRole('button', { name: 'Approve', exact: true }).click({ timeout: SHORT });
  await expect(approveDialog).toBeHidden({ timeout: LOAD });

  // No automatic re-run: 14 Jul was settled with the older tolerance and is listed with a link to its overview.
  await expect(page.getByText('Tolerance approved: no date was re-run')).toBeVisible({ timeout: LOAD });
  const rerun = page.getByRole('table', { name: 'Dates to run again' }).getByRole('link', {
    name: `Open ${DATE_SHOWN} to run it again`,
  });
  await expect(rerun).toBeVisible({ timeout: SHORT });
  await expect(rerun).toHaveAttribute('href', `/${MODULE}/dates/${DATE}`, { timeout: SHORT });

  // The new version is active.
  await page.goto(`/${MODULE}/settings/tolerance`, { timeout: LOAD });
  await expect(page.getByRole('heading', { name: `Active tolerance v${version}` })).toBeVisible({
    timeout: LOAD,
  });
  const after = await readTolerance(page);
  expect(after.active?.amount).toBe(amount);
  expect(after.pending).toBeNull();
});
