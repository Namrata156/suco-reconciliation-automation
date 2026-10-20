/**
 * Phase 5 Slice 2 click-through (design section 5, Amendment 1):
 *   upload the 14 Jul folder into a fresh database, pick 14 Jul for CBS,
 *   run, and see 306 cases appear.
 *
 * Prerequisites:
 *   - a FRESH database (no 2026-07-14 files yet: a second upload of the same
 *     files is refused as a duplicate) with the seeded configs and rule set
 *   - Django, Redis and a Celery worker running (processing and the run are
 *     Celery tasks); the Vite dev server or the built SPA
 *   - E2E_OPS_USER / E2E_OPS_PASSWORD: a user with the Operations User role
 *   - optional: E2E_MODULE (default upi), E2E_SAMPLE_DIR (default
 *     ../sample_decrypted_files/14072026, from frontend/), E2E_EXPECTED_CASES
 *     (default 306)
 * Run only this spec (slice1 needs the Maker / Checker variables):
 *   npx playwright test e2e/slice2.spec.ts
 *
 * Automatic refresh pauses while the user is idle (src/lib/polling.ts), so
 * the waits below press "Refresh" themselves; each press reaches the server
 * and keeps the session alive (src/pages/RefreshKeepsSession.test.tsx).
 *
 * Long waits: the run can take an hour or more on a fresh database. Keep the
 * laptop on mains power and stop it sleeping for the whole run. On
 * 2026-10-19 the laptop went into Modern Standby at 22:56 (Windows System log,
 * Kernel-Power 506/507) and stayed there 17 minutes. Standby pauses the
 * browser, this test and the servers, while the server session keeps
 * counting wall-clock time, so the session had expired by the time it woke.
 * If the session ends anyway, the waits sign in again (at most
 * MAX_SIGN_INS times) and note it in the report.
 * Wait limits (minutes): E2E_PROCESSING_WAIT_MIN (default 60) for file
 * processing, E2E_RUN_WAIT_MIN (default 180) for the run, E2E_TIMEOUT_MIN
 * (default 300) for the whole test.
 */
import { readdirSync, statSync } from 'node:fs';
import path from 'node:path';

import { expect, test, type Page } from '@playwright/test';

const MODULE = (process.env.E2E_MODULE ?? 'upi').toLowerCase();
const OPS = { user: process.env.E2E_OPS_USER ?? '', password: process.env.E2E_OPS_PASSWORD ?? '' };
const SAMPLE_DIR =
  process.env.E2E_SAMPLE_DIR ?? path.resolve(process.cwd(), '..', 'sample_decrypted_files', '14072026');
const DATE = '2026-07-14';
const DATE_SHOWN = '14-Jul-2026';
const CBS_FILE = 'cbs_14072026.xls';
const EXPECTED_CASES = Number(process.env.E2E_EXPECTED_CASES ?? '306');

const MINUTE = 60_000;
const minutes = (name: string, fallback: number) => Number(process.env[name] || fallback) * MINUTE;
const PROCESSING_WAIT = minutes('E2E_PROCESSING_WAIT_MIN', 60);
const RUN_WAIT = minutes('E2E_RUN_WAIT_MIN', 180);
const MAX_SIGN_INS = 3;

test.describe.configure({ mode: 'serial' });
// The ~100 MB CBS file is uploaded, parsed and loaded, then the whole day is run.
test.setTimeout(minutes('E2E_TIMEOUT_MIN', 300));

test.beforeAll(() => {
  if (!OPS.user || !OPS.password) throw new Error('Set E2E_OPS_USER and E2E_OPS_PASSWORD');
});

function sampleFiles(): string[] {
  return readdirSync(SAMPLE_DIR)
    .map((name) => path.join(SAMPLE_DIR, name))
    .filter((p) => statSync(p).isFile())
    .sort();
}

async function login(page: Page) {
  await page.goto('/login');
  await page.getByLabel('Username').fill(OPS.user);
  await page.getByLabel('Password').fill(OPS.password);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('heading', { name: /My work/ })).toBeVisible();
}

let signIns = 0;

/**
 * Back on the login page ("Session expired"): sign in again. The SPA returns
 * to the address it was on. Recorded as a warning in the report: it should
 * only happen when the machine slept (see the header).
 */
async function recoverSession(page: Page) {
  const signIn = page.getByRole('button', { name: 'Sign in' });
  if (!(await signIn.isVisible())) return;
  signIns += 1;
  if (signIns > MAX_SIGN_INS) throw new Error(`The session ended more than ${MAX_SIGN_INS} times`);
  test.info().annotations.push({
    type: 'warning',
    description: `Session ended; signed in again at ${new Date().toISOString()} (did the machine sleep?)`,
  });
  await page.getByLabel('Username').fill(OPS.user);
  await page.getByLabel('Password').fill(OPS.password);
  await signIn.click();
  await expect(signIn).toBeHidden({ timeout: 30_000 });
}

/**
 * Presses "Refresh" until check() passes. The click has its own timeout, so
 * a page without the button (the login page) cannot hold the loop until
 * the test times out.
 */
async function refreshUntil(page: Page, check: () => Promise<void>, timeout: number) {
  await expect(async () => {
    await recoverSession(page);
    await page.getByRole('button', { name: 'Refresh', exact: true }).click({ timeout: 10_000 });
    await check();
  }).toPass({ timeout, intervals: [5_000, 10_000, 15_000] });
}

test('Operations User uploads 14 Jul, picks 14 Jul for CBS, runs, and sees the cases', async ({ page }) => {
  const files = sampleFiles();
  expect(files.map((f) => path.basename(f))).toContain(CBS_FILE);
  await login(page);

  // 1. Upload with the review table.
  await page.goto(`/${MODULE}/files/upload`);
  await page.getByLabel('Choose files').setInputFiles(files);
  await expect(page.getByText(`${files.length} file(s) chosen.`)).toBeVisible();
  await page.getByRole('button', { name: 'Check files' }).click();

  const review = page.getByRole('table', { name: 'Files to upload' });
  await expect(review).toBeVisible({ timeout: 5 * MINUTE });
  await expect(page.getByLabel(/Business date of this upload/)).toHaveValue(DATE_SHOWN);
  await expect(review.getByRole('row')).toHaveCount(files.length + 1);
  await expect(review.getByText('Not recognised')).toHaveCount(0);
  // CBS has no fixed name: recognised by its content.
  await expect(page.getByLabel(`File type for ${CBS_FILE}`)).not.toHaveValue('');

  await page.getByRole('button', { name: `Upload ${files.length} file(s)` }).click();
  await expect(page.getByText(`${files.length} accepted, 0 refused`)).toBeVisible({ timeout: 20 * MINUTE });

  // 2. CBS business date: wait for the pause, then confirm the pre-selected 14 Jul.
  await page.goto(`/${MODULE}/files?status=AWAITING_DATE_SELECTION&business_date=${DATE}`);
  const filesTable = page.getByRole('table', { name: 'Uploaded files' });
  await refreshUntil(
    page,
    () => expect(filesTable.getByRole('link', { name: CBS_FILE })).toBeVisible({ timeout: 5_000 }),
    PROCESSING_WAIT,
  );
  await filesTable.getByRole('link', { name: CBS_FILE }).click();
  await expect(page.getByRole('heading', { name: 'Business date of this CBS file' })).toBeVisible();
  await expect(page.getByLabel(DATE_SHOWN)).toBeChecked();
  await page.getByRole('button', { name: 'Confirm business date' }).click();
  await expect(page.getByText('Business date confirmed')).toBeVisible();

  // 3. The date overview: wait until every file is processed, then run.
  await page.goto(`/${MODULE}/dates/${DATE}`);
  await expect(page.getByRole('heading', { name: `Business date ${DATE_SHOWN}` })).toBeVisible();
  const runButton = page.getByRole('button', { name: /^Run reconciliation/ });
  await refreshUntil(
    page,
    async () => {
      await expect(page.getByText(/0 processing, 0 awaiting date, 0 failed/)).toBeVisible({ timeout: 5_000 });
      await expect(runButton).toBeEnabled({ timeout: 5_000 });
    },
    PROCESSING_WAIT,
  );
  await runButton.click();
  await page.getByRole('dialog').getByRole('button', { name: 'Start run' }).click();
  await expect(page.getByText('Run started')).toBeVisible();

  // Wait until the run has finished either way, then require success (a failed
  // run stops the wait at once instead of retrying until the limit).
  const lastRun = page.getByRole('group', { name: 'Last run' });
  await refreshUntil(
    page,
    () => expect(lastRun.getByText(/^(Succeeded|Failed)$/).first()).toBeVisible({ timeout: 5_000 }),
    RUN_WAIT,
  );
  await expect(lastRun.getByText('Failed', { exact: true })).toHaveCount(0);
  await expect(lastRun.getByText('Succeeded', { exact: true })).toBeVisible();

  // 4. The cases appear.
  const cases = page.getByRole('link', { name: `${EXPECTED_CASES} cases: open the list` });
  await expect(cases).toBeVisible();
  await cases.click();
  await expect(page.getByText(new RegExp(`of ${EXPECTED_CASES} cases$`))).toBeVisible();
});
