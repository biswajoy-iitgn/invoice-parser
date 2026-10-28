// Formatting goes through Intl for one locale (Indian grouping: 9,64,904.88); plural forms come from the locale's rules.
export const LOCALE = 'en-IN';
const PERCENT = new Intl.NumberFormat(LOCALE, { style: 'percent', maximumFractionDigits: 0 });
const MONEY = new Intl.NumberFormat(LOCALE, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const COUNT = new Intl.NumberFormat(LOCALE);
const PLURAL = new Intl.PluralRules(LOCALE);
export const pct = (n: number | null | undefined): string => (n == null ? '' : PERCENT.format(n));
export const num = (s: unknown): number => Number(String(s).replace(/[^0-9.-]/g, '')) || 0;
export const money = (n: number): string => MONEY.format(n);
/** count(3, 'value', 'values') → "3 values"; count(1, …) → "1 value". */
export const count = (n: number, one: string, other: string): string => `${COUNT.format(n)} ${PLURAL.select(n) === 'one' ? one : other}`;
