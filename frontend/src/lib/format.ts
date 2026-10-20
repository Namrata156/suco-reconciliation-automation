/**
 * Display formats (design P8, approved): amounts in Indian grouping
 * ("Rs 5,46,13,083.81"), dates as 14-Jul-2026, times in IST.
 *
 * Amounts arrive as decimal STRINGS (design 3.4) and are formatted without
 * ever becoming a JavaScript float, so no rupee value is rounded here.
 */

const GROUPING = new Intl.NumberFormat('en-IN', { useGrouping: true, maximumFractionDigits: 0 });
const COUNT = new Intl.NumberFormat('en-IN');
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const DECIMAL = /^(-)?(\d+)(?:\.(\d+))?$/;

/** "54613083.81" -> "5,46,13,083.81"; null/"" -> "-". Not a number -> as given. */
export function formatAmount(value: string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '-';
  const m = DECIMAL.exec(value.trim());
  if (!m) return value;
  const [, sign, int, frac = ''] = m;
  const grouped = GROUPING.format(BigInt(int ?? '0'));
  const fraction = (frac + '00').slice(0, Math.max(2, frac.length));
  return `${sign ?? ''}${grouped}.${fraction}`;
}

/** "54613083.81" -> "Rs 5,46,13,083.81"; negative -> "-Rs 1,000.00". */
export function formatRupees(value: string | null | undefined): string {
  const f = formatAmount(value);
  if (f === '-' || !DECIMAL.test((value ?? '').trim())) return f;
  return f.startsWith('-') ? `-Rs ${f.slice(1)}` : `Rs ${f}`;
}

/** Counts in Indian grouping: 70969 -> "70,969". */
export function formatCount(value: number | null | undefined): string {
  return value === null || value === undefined ? '-' : COUNT.format(value);
}

/** File sizes: 512 -> "512 B", 65536 -> "64.0 KB", 100427776 -> "95.8 MB". */
export function formatBytes(value: number | null | undefined): string {
  if (value === null || value === undefined) return '-';
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

/** "2026-07-14" (or an ISO datetime's date part) -> "14-Jul-2026". */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '-';
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso.slice(0, 10));
  if (!m) return iso;
  const [, y, mo, d] = m;
  return `${d}-${MONTHS[Number(mo) - 1] ?? mo}-${y}`;
}

const IST_PARTS = new Intl.DateTimeFormat('en-GB', {
  timeZone: 'Asia/Kolkata',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
});

/** ISO datetime -> "14-Jul-2026 10:32 IST". */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '-';
  const t = new Date(iso);
  if (Number.isNaN(t.getTime())) return iso;
  const p = Object.fromEntries(IST_PARTS.formatToParts(t).map((x) => [x.type, x.value]));
  return `${p.day}-${MONTHS[Number(p.month) - 1]}-${p.year} ${p.hour}:${p.minute} IST`;
}

/** Any JSON value as one line of text: null/"" -> "-", objects as JSON. */
export function displayValue(v: unknown): string {
  if (v === null || v === undefined || v === '') return '-';
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

/** A 2-decimal amount string as the API sends it ("35.00", "-1070.50"). */
export const isAmountString = (v: unknown): v is string => typeof v === 'string' && /^-?\d+\.\d{2}$/.test(v);

/** Yes / No / - for a nullable flag. */
export const yesNo = (v: boolean | null | undefined) =>
  v === null || v === undefined ? '-' : v ? 'Yes' : 'No';

/** Today's date in IST as ISO (the bank's business day). */
export function todayIst(now = new Date()): string {
  const p = Object.fromEntries(IST_PARTS.formatToParts(now).map((x) => [x.type, x.value]));
  return `${p.year}-${p.month}-${p.day}`;
}
