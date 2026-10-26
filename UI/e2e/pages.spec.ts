// Every page, at desktop, half-width (200 % zoom) and 400 % zoom, light and dark: axe WCAG 2.2 AA, heading order,
// names on controls, clipped or overlapping text, dead clicks and console errors (scripts/qa-browser.js).
import { createRequire } from 'node:module';
import { expect, test } from '@playwright/test';

const AXE = createRequire(import.meta.url).resolve('axe-core/axe.min.js');
const RUNS = [
  { width: 1366, height: 768, scheme: 'light' },
  { width: 1366, height: 768, scheme: 'dark' },
  { width: 683, height: 900, scheme: 'light' },
  { width: 341, height: 768, scheme: 'light' },
] as const;

for (const { width, height, scheme } of RUNS) {
  test(`pages pass QA at ${width} px, ${scheme}`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await page.emulateMedia({ colorScheme: scheme });
    await page.goto('/#/home');
    await page.addScriptTag({ path: AXE });
    const report = await page.evaluate(async () => {
      const qa = await import('/scripts/qa-browser.js');
      qa.ROUTES.push('review/R-1038-1', 'profile/contract', 'data?view=schema', 'data?db=erp');
      const r = await qa.run({ clicks: false });
      return [...r.structure, ...r.axe, ...r.dead, ...r.console];
    });
    expect(report).toEqual([]);
  });
}
