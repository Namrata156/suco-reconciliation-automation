/**
 * Phase 5 Slice 4 click-through (design section 5):
 *   An Auditor sees the day's case events and the login log, and cannot
 *   change anything.
 *
 * Steps:
 *   1. Sign in as the Auditor: the menu has Alerts and Audit trail, and no
 *      upload, users list or work lists; the header has the alerts bell.
 *   2. Audit trail > Case events > "Today": the count on screen equals the
 *      API's count for today (the day may have no case events in the
 *      database copy; then the empty message is checked instead). With
 *      events, the first row's case opens with no action offered.
 *   3. Audit trail > Login log > "Today", filtered on the Auditor's user
 *      name: the Auditor's own sign-in of step 1 is listed.
 *   4. Cannot change anything: no action buttons on a case, on settings or
 *      on pending approvals; the server refuses a case "take" (403) and
 *      there is no write on the audit lists (403 / 405).
 *
 * Prerequisites:
 *   - Django (+ the Vite dev server, or the built SPA served by Django),
 *     with the Slice 4 API
 *   - E2E_AUDITOR_USER / E2E_AUDITOR_PASSWORD: an account with the Auditor
 *     role ONLY
 *   - at least one case in the module (any status)
 *   - optional: E2E_MODULE (default upi)
 * It writes nothing but the Auditor's login / logout records, so it can run
 * against any database; the database copy is still recommended.
 *
 * Run only this spec:  npx playwright test e2e/slice4.spec.ts
 * Every wait has its own timeout (as in slice2/3), so a missing element
 * fails at once with its own message.
 */
import { expect, test, type Page } from '@playwright/test';

const MODULE = (process.env.E2E_MODULE ?? 'upi').toLowerCase();
const AUDITOR = {
  user: process.env.E2E_AUDITOR_USER ?? '',
  password: process.env.E2E_AUDITOR_PASSWORD ?? '',
};

/** Page loads and server answers. */
const LOAD = 30_000;
/** Something already on screen. */
const SHORT = 10_000;

test.describe.configure({ mode: 'serial' });
test.setTimeout(5 * 60_000);

test.beforeAll(() => {
  if (!AUDITOR.user || !AUDITOR.password) {
    throw new Error('Set E2E_AUDITOR_USER and E2E_AUDITOR_PASSWORD (an account with the Auditor role only)');
  }
});

/** Today's date in IST, as the "Today" button and the API use it. */
function todayIst(): string {
  const p = Object.fromEntries(
    new Intl.DateTimeFormat('en-GB', {
      timeZone: 'Asia/Kolkata',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    })
      .formatToParts(new Date())
      .map((x) => [x.type, x.value]),
  );
  return `${p.year}-${p.month}-${p.day}`;
}

async function login(page: Page) {
  await page.goto('/login', { timeout: LOAD });
  await page.getByLabel('Username').fill(AUDITOR.user, { timeout: SHORT });
  await page.getByLabel('Password').fill(AUDITOR.password, { timeout: SHORT });
  await page.getByRole('button', { name: 'Sign in' }).click({ timeout: SHORT });
  await expect(page.getByRole('heading', { name: /My work/ })).toBeVisible({ timeout: LOAD });
}

async function logout(page: Page) {
  await page.getByRole('button', { name: /^User menu for/ }).click({ timeout: SHORT });
  await page.getByRole('menuitem', { name: 'Sign out' }).click({ timeout: SHORT });
  await expect(page.getByRole('button', { name: 'Sign in' })).toBeVisible({ timeout: LOAD });
}

/** The CSRF cookie of the page's session, for the refused-write checks. */
async function csrf(page: Page): Promise<string> {
  const cookies = await page.context().cookies();
  return cookies.find((c) => c.name === 'csrftoken')?.value ?? '';
}

/** "12 event(s); showing 1-12" -> 12. */
async function shownCount(page: Page): Promise<number> {
  const status = page.getByText(/^\d[\d,]* event\(s\)/);
  await expect(status).toBeVisible({ timeout: LOAD });
  const text = (await status.textContent({ timeout: SHORT })) ?? '';
  return Number(text.split(' ')[0]?.replace(/,/g, '') ?? 'NaN');
}

const ACTION_BUTTONS =
  /^(Take|Release|Reassign|Submit action|Approve|Reject|Mark done|Add remark|Propose|Submit)/;

test("Auditor: the day's case events and the login log, and nothing can be changed", async ({ page }) => {
  const today = todayIst();
  await login(page);

  // 1. Menu and header for the Auditor.
  const nav = page.getByRole('navigation', { name: 'Main menu' });
  await expect(nav.getByRole('link', { name: 'Audit trail' })).toBeVisible({ timeout: SHORT });
  await expect(nav.getByRole('link', { name: 'Alerts', exact: true })).toBeVisible({ timeout: SHORT });
  await expect(nav.getByRole('link', { name: 'Upload files' })).toHaveCount(0, { timeout: SHORT });
  await expect(nav.getByRole('link', { name: 'Users and roles', exact: true })).toHaveCount(0, {
    timeout: SHORT,
  });
  await expect(nav.getByRole('link', { name: 'My cases' })).toHaveCount(0, { timeout: SHORT });
  await expect(nav.getByRole('link', { name: 'Awaiting my approval' })).toHaveCount(0, { timeout: SHORT });
  await expect(page.getByRole('link', { name: /^Alerts(,|$)/ }).first()).toBeVisible({ timeout: LOAD });

  // 2. The day's case events.
  await nav.getByRole('link', { name: 'Audit trail' }).click({ timeout: SHORT });
  await expect(page.getByRole('heading', { name: 'Audit trail', exact: true })).toBeVisible({
    timeout: LOAD,
  });
  await expect(page.getByRole('tab', { name: 'Case events', selected: true })).toBeVisible({ timeout: LOAD });
  await page.getByRole('button', { name: 'Today' }).click({ timeout: SHORT });
  await expect(page).toHaveURL(
    new RegExp(`date_from=${today}.*date_to=${today}|date_to=${today}.*date_from=${today}`),
    {
      timeout: SHORT,
    },
  );
  const caseTable = page.getByRole('table', { name: 'Case events' });
  await expect(caseTable).toBeVisible({ timeout: LOAD });
  await expect(caseTable).toHaveAttribute('aria-busy', 'false', { timeout: LOAD });

  const api = await page.request.get(
    `/api/v1/audit/case-events/?date_from=${today}&date_to=${today}&page_size=1`,
    {
      timeout: LOAD,
    },
  );
  expect(api.status(), 'case events read').toBe(200);
  const apiCount = ((await api.json()) as { count: number }).count;
  expect(await shownCount(page), 'case events on screen = API count for today').toBe(apiCount);

  if (apiCount === 0) {
    await expect(caseTable.getByText('No events match these filters.')).toBeVisible({ timeout: SHORT });
  } else {
    // The newest event's case opens, read-only for the Auditor.
    const first = caseTable.getByRole('link', { name: /^Case \d+ \(event \d+\)$/ }).first();
    await expect(first).toBeVisible({ timeout: SHORT });
    await first.click({ timeout: SHORT });
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible({ timeout: LOAD });
    const actions = page.getByRole('group', { name: 'Case actions' });
    await expect(actions.getByText('No action is open to you on this case now.')).toBeVisible({
      timeout: LOAD,
    });
    await expect(actions.getByRole('button')).toHaveCount(0, { timeout: SHORT });
    await page.goBack({ timeout: LOAD });
    await expect(page.getByRole('table', { name: 'Case events' })).toBeVisible({ timeout: LOAD });
  }

  // 3. The login log: the Auditor's own sign-in of step 1.
  await page.getByRole('tab', { name: 'Login log' }).click({ timeout: SHORT });
  await expect(page).toHaveURL(/\/audit\/user-activity/, { timeout: SHORT });
  await page.getByRole('button', { name: 'Today' }).click({ timeout: SHORT });
  const userBox = page.getByRole('textbox', { name: 'User name' });
  await userBox.fill(AUDITOR.user, { timeout: SHORT });
  await userBox.press('Enter', { timeout: SHORT });
  await expect(page).toHaveURL(new RegExp(`user=${encodeURIComponent(AUDITOR.user)}`), { timeout: SHORT });
  const loginTable = page.getByRole('table', { name: 'Login log' });
  await expect(loginTable).toHaveAttribute('aria-busy', 'false', { timeout: LOAD });
  await expect(loginTable.getByRole('row').filter({ hasText: AUDITOR.user }).first()).toBeVisible({
    timeout: LOAD,
  });
  // Only the sign-ins (LOGIN), with the filters kept in the address.
  const here = new URL(page.url());
  await page.goto(`${here.pathname}${here.search}&event_type=LOGIN`, { timeout: LOAD });
  const signIns = page.getByRole('table', { name: 'Login log' });
  await expect(signIns).toHaveAttribute('aria-busy', 'false', { timeout: LOAD });
  await expect(signIns.getByRole('row').filter({ hasText: AUDITOR.user }).first()).toBeVisible({
    timeout: LOAD,
  });
  await expect(page.getByRole('textbox', { name: 'User name' })).toHaveValue(AUDITOR.user, {
    timeout: SHORT,
  });

  // Nothing on the audit screens changes anything: filters and paging only.
  const auditButtons = await page.getByRole('main').getByRole('button').allTextContents();
  for (const b of auditButtons) expect(b.trim(), `audit button "${b}"`).not.toMatch(ACTION_BUTTONS);

  // 4. Cannot change anything.
  // Settings and pending approvals: no propose / approve buttons.
  await page.goto(`/${MODULE}/settings/response-codes`, { timeout: LOAD });
  await expect(page.getByRole('heading', { name: 'Settings', exact: true })).toBeVisible({ timeout: LOAD });
  await expect(page.getByText('Makers propose changes; Checkers approve them.')).toBeVisible({
    timeout: LOAD,
  });
  await expect(page.getByRole('button', { name: /^Propose/ })).toHaveCount(0, { timeout: SHORT });
  await page.goto(`/${MODULE}/approvals`, { timeout: LOAD });
  await expect(page.getByRole('heading', { name: 'Pending approvals', exact: true })).toBeVisible({
    timeout: LOAD,
  });
  await expect(page.getByRole('button', { name: /^(Approve|Reject)/ })).toHaveCount(0, { timeout: LOAD });

  // The server refuses a case action and has no write on the audit lists.
  const cases = await page.request.get(`/api/v1/${MODULE}/cases/?page_size=1`, { timeout: LOAD });
  expect(cases.status(), 'case queue read').toBe(200);
  const someCase = ((await cases.json()) as { results: { id: number; status: string }[] }).results[0];
  expect(someCase, 'the module has at least one case').toBeTruthy();
  const headers = { 'X-CSRFToken': await csrf(page), 'Content-Type': 'application/json' };
  const take = await page.request.post(`/api/v1/${MODULE}/cases/${someCase!.id}/take/`, {
    headers,
    data: {},
    timeout: LOAD,
  });
  expect(take.status(), 'Auditor may not take a case').toBe(403);
  const after = await page.request.get(`/api/v1/${MODULE}/cases/${someCase!.id}/`, { timeout: LOAD });
  expect(((await after.json()) as { status: string }).status, 'case unchanged').toBe(someCase!.status);
  const auditWrite = await page.request.post('/api/v1/audit/case-events/', {
    headers,
    data: {},
    timeout: LOAD,
  });
  expect([403, 405], 'no write on the audit trail').toContain(auditWrite.status());

  await logout(page);
});
