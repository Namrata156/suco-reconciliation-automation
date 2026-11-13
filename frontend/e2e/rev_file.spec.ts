/**
 * Rev credit file click-through (plan B "Verification", owner decisions
 * 2026-11-12): on the 2 Nov 2026 date page, ONE user with no groups
 * (ROLES_ENFORCED off) does step 6 and step 7 alone:
 *   6. generate the rev credit file, download it, delete the rows the Check
 *      column flags (old-format accounts), correct the debit total, upload the
 *      checked file as the same user -> "18 approved, 4 left open";
 *   2. run the date again (the approved replies reach the SSKUPI preview);
 *   7. generate the SSKUPI file and download it.
 *
 * Prerequisites (see the run instructions in the report / CLAUDE.md):
 *   - a database where 2 Nov 2026 has been uploaded (sample_decrypted_files/02112026)
 *     and run ONCE, but no rev credit file generated yet. The spec consumes
 *     that state (it approves cases and generates files), so re-create the
 *     database from a snapshot before every run, e.g.
 *       DROP DATABASE reconciliation_e2e_rev_pw;
 *       CREATE DATABASE reconciliation_e2e_rev_pw TEMPLATE reconciliation_e2e_rev_base;
 *   - Django serving the build (E2E_BASE_URL=http://localhost:8000) and a
 *     Celery worker (the re-run is a Celery task), both on that database,
 *     with ROLES_ENFORCED unset (off)
 *   - E2E_REV_USER / E2E_REV_PASSWORD: an active user (no group needed)
 *   - optional: E2E_MODULE (default upi), E2E_REV_DATE (default 2026-11-02),
 *     E2E_REV_ROWS (default 22), E2E_REV_FLAGGED (default 4),
 *     E2E_SSKUPI_ROWS (default 61), E2E_RUN_WAIT_MIN (default 20)
 * Run only this spec:
 *   npx playwright test e2e/rev_file.spec.ts
 */
import { readFileSync } from 'node:fs';

import { expect, test, type Locator, type Page } from '@playwright/test';

const MODULE = (process.env.E2E_MODULE ?? 'upi').toLowerCase();
const USER = { user: process.env.E2E_REV_USER ?? '', password: process.env.E2E_REV_PASSWORD ?? '' };
const DATE = process.env.E2E_REV_DATE ?? '2026-11-02';
const [Y, M, D] = DATE.split('-');
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const MONTH = MONTHS[Number(M) - 1];
if (!MONTH) throw new Error(`E2E_REV_DATE must be YYYY-MM-DD, got ${DATE}`);
const DATE_SHOWN = `${D}-${MONTH}-${Y}`;
const REV_FILE = `upi rev ${D}${M}${Y}.csv`;
const NARRATION = `UPI TO RRC DRC TCC REVERSAL DATED ON ${MONTH.toUpperCase()} ${D}`;
const REV_ROWS = Number(process.env.E2E_REV_ROWS ?? '22');
const REV_FLAGGED = Number(process.env.E2E_REV_FLAGGED ?? '4');
const SSKUPI_ROWS = Number(process.env.E2E_SSKUPI_ROWS ?? '61');

const MINUTE = 60_000;
const RUN_WAIT = Number(process.env.E2E_RUN_WAIT_MIN || 20) * MINUTE;

test.describe.configure({ mode: 'serial' });
test.setTimeout(RUN_WAIT + 10 * MINUTE);

test.beforeAll(() => {
  if (!USER.user || !USER.password) throw new Error('Set E2E_REV_USER and E2E_REV_PASSWORD');
});

async function login(page: Page) {
  await page.goto('/login');
  await page.getByLabel('Username').fill(USER.user);
  await page.getByLabel('Password').fill(USER.password);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('button', { name: 'Sign in' })).toBeHidden({ timeout: 30_000 });
}

/** Presses the page's "Refresh" until check() passes (automatic refresh pauses while idle). */
async function refreshUntil(page: Page, check: () => Promise<void>, timeout: number) {
  await expect(async () => {
    await page.getByRole('button', { name: 'Refresh', exact: true }).click({ timeout: 10_000 });
    await check();
  }).toPass({ timeout, intervals: [5_000, 10_000, 15_000] });
}

/** A small RFC 4180 reader: enough for the rev file (quoted cells allowed). */
function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = '';
  let quoted = false;
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"' && text[i + 1] === '"') {
        cell += '"';
        i += 1;
      } else if (ch === '"') quoted = false;
      else cell += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === ',') {
      row.push(cell);
      cell = '';
    } else if (ch === '\n' || ch === '\r') {
      if (ch === '\r' && text[i + 1] === '\n') i += 1;
      row.push(cell);
      rows.push(row);
      row = [];
      cell = '';
    } else cell += ch;
  }
  if (cell || row.length) rows.push([...row, cell]);
  return rows.filter((r) => r.some((c) => c.trim()));
}

const csvCell = (c: string) => (/[",\r\n]/.test(c) ? `"${c.replace(/"/g, '""')}"` : c);

/** What ops do in Excel: delete the rows the Check column flags, then correct the debit total. */
function checkedFile(downloaded: string): { content: string; kept: number; removed: number } {
  const rows = parseCsv(downloaded.replace(/^\uFEFF/, ''));
  const [header, debit, ...credits] = rows;
  if (!header || !debit) throw new Error('Rev file has no header or debit row');
  const check = header.indexOf('Check');
  const kept = credits.filter((r) => !(r[check] ?? '').trim());
  // Paise as integers: no floating-point drift in the total.
  const paise = kept.reduce((sum, r) => sum + Math.round(Number(r[3]) * 100), 0);
  const newDebit = [...debit];
  newDebit[2] = (paise / 100).toFixed(2);
  const content = [header, newDebit, ...kept].map((r) => r.map(csvCell).join(',')).join('\r\n') + '\r\n';
  return { content, kept: kept.length, removed: credits.length - kept.length };
}

async function download(page: Page, button: Locator): Promise<string> {
  const [file] = await Promise.all([page.waitForEvent('download'), button.click()]);
  const path = await file.path();
  return readFileSync(path, 'utf-8');
}

test('one user generates, checks and uploads the rev credit file, re-runs, then generates the SSKUPI file', async ({
  page,
}) => {
  await login(page);
  await page.goto(`/${MODULE}/dates/${DATE}`);
  await expect(page.getByRole('heading', { name: `Business date ${DATE_SHOWN}` })).toBeVisible();

  // ---- step 6: generate -------------------------------------------------------------------
  const revStep = page.locator('#step-rev');
  await expect(revStep.getByRole('heading', { name: '6. Rev credit file' })).toBeVisible();
  const rev = revStep.getByRole('group', { name: 'Rev credit file', exact: true });
  await expect(rev.getByText(`${REV_ROWS} credit row(s) ready for ${REV_FILE}`)).toBeVisible();
  await rev.getByRole('button', { name: `Generate ${REV_FILE}` }).click();
  const genDialog = page.getByRole('dialog', { name: `Generate ${REV_FILE}?` });
  await expect(genDialog.getByText(`${REV_ROWS} credit row(s) will be in ${REV_FILE}`)).toBeVisible();
  await genDialog.getByRole('button', { name: 'Generate file' }).click();
  await expect(genDialog).toBeHidden();
  await expect(rev.getByText('Generated', { exact: true })).toBeVisible();
  await expect(rev.getByText(new RegExp(`^Generated by ${USER.user} on `))).toBeVisible();
  await expect(rev.getByText(`${REV_FLAGGED} row(s) need an account number`)).toBeVisible();

  // ---- step 6: download -------------------------------------------------------------------
  const downloaded = await download(page, rev.getByRole('button', { name: `Download ${REV_FILE}` }));
  const lines = parseCsv(downloaded.replace(/^\uFEFF/, ''));
  expect(lines[0]?.slice(0, 8)).toEqual([
    'Sr. No.', 'Account NBR', 'Debit Amount', 'Credit Amount', 'Instrument Number', 'Instrument Date', 'Narration', 'Check',
  ]);
  expect(lines[1]?.[6]).toBe(NARRATION);
  expect(lines.length - 2).toBe(REV_ROWS);
  await expect(rev.getByText('Downloaded once.')).toBeVisible();

  // ---- step 6: upload the checked file as the SAME user -------------------------------------
  const checked = checkedFile(downloaded);
  expect(checked.removed).toBe(REV_FLAGGED);
  await rev.getByLabel('Checked file (CSV)').setInputFiles({
    name: `upi rev ${D}${M}${Y} checked.csv`,
    mimeType: 'text/csv',
    buffer: Buffer.from(checked.content, 'utf-8'),
  });
  await rev.getByRole('button', { name: 'Upload checked file' }).click();
  const result = revStep.getByRole('status').filter({ hasText: `Checked file uploaded: ${REV_FILE} approved` });
  await expect(result).toBeVisible({ timeout: 2 * MINUTE });
  await expect(result.getByText(`${checked.kept} approved, ${REV_FLAGGED} left open`)).toBeVisible();
  await expect(rev.getByText('Approved', { exact: true })).toBeVisible();
  await expect(rev.getByText(new RegExp(`^Approved by ${USER.user} on `))).toBeVisible();

  // ---- step 2: run the date again ---------------------------------------------------------
  const lastRun = page.getByRole('group', { name: 'Last run' });
  const before = Number((/Last run #(\d+)/.exec(await lastRun.innerText()) ?? ['', '0'])[1]);
  await page.getByRole('button', { name: /^Run reconciliation/ }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Start run' }).click();
  await expect(page.getByText('Run started', { exact: true })).toBeVisible();
  await refreshUntil(
    page,
    async () => {
      await expect(lastRun.getByText(`Last run #${before + 1}:`)).toBeVisible({ timeout: 5_000 });
      await expect(lastRun.getByText(/^(Succeeded|Failed)$/).first()).toBeVisible({ timeout: 5_000 });
    },
    RUN_WAIT,
  );
  await expect(lastRun.getByText('Succeeded', { exact: true })).toBeVisible();

  // ---- step 7: generate and download the SSKUPI file ----------------------------------------
  const sskupiStep = page.locator('#step-sskupi');
  await expect(sskupiStep.getByRole('heading', { name: '7. SSKUPI file' })).toBeVisible();
  await expect(sskupiStep.getByText('Rev credit file not uploaded yet')).toHaveCount(0);
  const next = sskupiStep.getByRole('group', { name: 'Next SSKUPI file' });
  await expect(next.getByText(`${SSKUPI_ROWS} reply row(s) ready for`)).toBeVisible();
  await next.getByRole('button', { name: /^Generate SSKUPI/ }).click();
  const npciDialog = page.getByRole('dialog', { name: /^Generate SSKUPI.*\?$/ });
  const partial = npciDialog.getByLabel(/is a partial file$/);
  if (await partial.isVisible()) await partial.check();
  await npciDialog.getByRole('button', { name: /^Generate (partial )?file$/ }).click();
  await expect(npciDialog).toBeHidden();
  const filesTable = sskupiStep.getByRole('table', { name: `SSKUPI files generated for ${DATE_SHOWN}` });
  await expect(filesTable).toBeVisible();
  const sskupi = await download(page, filesTable.getByRole('button', { name: /^Download SSKUPI/ }).first());
  const sskupiLines = sskupi.split(/\r?\n/).filter((l) => l.trim());
  expect(sskupiLines.length - 1).toBe(SSKUPI_ROWS);
});
