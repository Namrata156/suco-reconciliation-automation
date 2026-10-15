/**
 * Phase 5 Slice 1 click-through (design section 5):
 *   1. A Maker takes an R01 case and submits DRC 102; a Checker approves it;
 *      the history shows both steps.
 *   2. Bulk-submit a set of LOW R01 cases.
 *   3. A bulk refusal on R13 Pending Investigation.
 *
 * Prerequisites (seeded data set, not the full database):
 *   - at least 4 OPEN, LOW, R01 cases without warning flags in module E2E_MODULE
 *   - at least 1 R13 case
 *   - two users: E2E_MAKER_USER (Maker role only) and E2E_CHECKER_USER
 *     (Checker role only), so the approval is not a self-approval
 */
import { expect, test, type Page } from '@playwright/test';

const MODULE = (process.env.E2E_MODULE ?? 'upi').toLowerCase();
const MAKER = { user: process.env.E2E_MAKER_USER ?? '', password: process.env.E2E_MAKER_PASSWORD ?? '' };
const CHECKER = {
  user: process.env.E2E_CHECKER_USER ?? '',
  password: process.env.E2E_CHECKER_PASSWORD ?? '',
};

test.describe.configure({ mode: 'serial' });

test.beforeAll(() => {
  if (!MAKER.user || !MAKER.password || !CHECKER.user || !CHECKER.password) {
    throw new Error('Set E2E_MAKER_USER, E2E_MAKER_PASSWORD, E2E_CHECKER_USER and E2E_CHECKER_PASSWORD');
  }
});

async function login(page: Page, who: { user: string; password: string }) {
  await page.goto('/login');
  await page.getByLabel('Username').fill(who.user);
  await page.getByLabel('Password').fill(who.password);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('heading', { name: /My work/ })).toBeVisible();
}

async function logout(page: Page) {
  await page.getByRole('button', { name: /^User menu for/ }).click();
  await page.getByRole('menuitem', { name: 'Sign out' }).click();
  await expect(page.getByRole('button', { name: 'Sign in' })).toBeVisible();
}

const queue = (page: Page, query: string) => page.goto(`/${MODULE}/cases?${query}`);
const casesTable = (page: Page) => page.getByRole('table', { name: 'Exception cases' });
const dialog = (page: Page) => page.getByRole('dialog');

test('Maker takes an R01 case and submits DRC 102; Checker approves; history shows it', async ({ page }) => {
  await login(page, MAKER);
  await queue(page, 'rule_code=R01&status=OPEN&priority=LOW');
  const firstCase = casesTable(page).getByRole('link').first();
  await expect(firstCase).toBeVisible();
  const rrn = (await firstCase.textContent())?.trim() ?? '';
  await firstCase.click();
  await expect(page.getByRole('heading', { name: `Case ${rrn}` })).toBeVisible();
  const caseUrl = page.url();

  // Take
  await page.getByRole('group', { name: 'Case actions' }).getByRole('button', { name: 'Take' }).click();
  await dialog(page).getByRole('button', { name: 'Take' }).click();
  await expect(page.getByRole('button', { name: 'Submit action' })).toBeVisible();

  // Submit DRC 102 (pre-filled from the rule)
  await page.getByRole('button', { name: 'Submit action' }).click();
  await expect(dialog(page).getByText('Sent as DRC 102 (filled in automatically).')).toBeVisible();
  await dialog(page).getByRole('button', { name: 'Submit for approval' }).click();
  await expect(dialog(page)).toBeHidden();
  await expect(page.getByText('Pending approval').first()).toBeVisible();
  await logout(page);

  // Checker approves
  await login(page, CHECKER);
  await page.goto(caseUrl);
  await page.getByRole('group', { name: 'Case actions' }).getByRole('button', { name: 'Approve' }).click();
  await expect(dialog(page).getByText(/DRC reply 102/)).toBeVisible();
  await dialog(page)
    .getByRole('button', { name: /^Approve/ })
    .click();
  await expect(dialog(page)).toBeHidden();

  // History: submitted by the maker, approved by the checker
  const history = page.getByRole('table', { name: 'Case history' });
  await expect(
    history.getByRole('row', { name: new RegExp(`Action submitted.*${MAKER.user}`) }),
  ).toBeVisible();
  await expect(history.getByRole('row', { name: new RegExp(`Approved.*${CHECKER.user}`) })).toBeVisible();
  await logout(page);
});

test('Maker bulk-submits a set of LOW R01 cases', async ({ page }) => {
  await login(page, MAKER);
  await queue(page, 'rule_code=R01&status=OPEN&priority=LOW');
  const boxes = casesTable(page).getByRole('checkbox', { name: /^Select case / });
  await expect(boxes.nth(2)).toBeVisible();
  const labels: string[] = [];
  for (let i = 0; i < 3; i += 1) {
    const box = boxes.nth(i);
    labels.push((await box.getAttribute('aria-label')) ?? '');
    await box.check();
  }
  const bar = page.getByRole('region', { name: 'Bulk actions' });
  await expect(bar.getByText('3 cases selected')).toBeVisible();
  await bar.getByRole('button', { name: 'Take selected' }).click();
  await expect(page.getByText('3 cases taken.')).toBeVisible();

  await queue(page, 'rule_code=R01&status=IN_PROGRESS&priority=LOW&mine=assigned');
  for (const label of labels) {
    await casesTable(page).getByRole('checkbox', { name: label, exact: true }).check();
  }
  await expect(bar.getByText('3 cases selected')).toBeVisible();
  await expect(bar.getByTestId('bulk-submit-reasons')).toHaveCount(0);
  await bar.getByRole('button', { name: 'Submit selected...' }).click();
  await dialog(page).getByRole('button', { name: 'Submit 3 for approval' }).click();
  await expect(page.getByText('3 cases submitted for approval.')).toBeVisible();
  await logout(page);
});

test('Bulk is refused for R13 Pending Investigation', async ({ page }) => {
  await login(page, MAKER);
  await queue(page, 'rule_code=R13&status=all');
  const box = casesTable(page)
    .getByRole('checkbox', { name: /^Select case / })
    .first();
  await expect(box).toBeVisible();
  await box.check();
  const bar = page.getByRole('region', { name: 'Bulk actions' });
  await expect(bar.getByTestId('bulk-submit-reasons')).toContainText(
    'Pending Investigation cases (R13) are never handled in bulk',
  );
  await expect(bar.getByRole('button', { name: 'Submit selected...' })).toBeDisabled();
  await logout(page);
});
