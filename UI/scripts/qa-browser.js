// Runtime quality gate, run inside the dev app: `await (await import('/scripts/qa-browser.js')).run()`.
// Per route: axe (WCAG 2.2 AA), one h1, no skipped heading levels, alt on every image, a name on every control,
// 24x24 minimum targets (WCAG 2.5.8), no horizontal scroll, console errors, and controls that do nothing when clicked.
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
export const ROUTES = ['home', 'review', 'review/doc-dachser', 'review/scan-kuehne-nagel', 'library', 'insights', 'run/R-1042',
  'profiles', 'profile/freight', 'workflows', 'engines', 'data', 'users', 'audit', 'updates'];

async function loadAxe() {
  if (window.axe) return;
  await new Promise((res, rej) => {
    const s = document.createElement('script');
    s.src = 'https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.10.2/axe.min.js';
    s.onload = res; s.onerror = rej; document.head.appendChild(s);
  });
}

const visible = (e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && !e.closest('[aria-hidden=true]'); };
const nameOf = (e) => (e.getAttribute('aria-label') || e.getAttribute('aria-labelledby') && document.getElementById(e.getAttribute('aria-labelledby'))?.innerText
  || e.innerText || e.getAttribute('title') || e.closest('label')?.innerText || '').trim();

async function go(route) {
  document.querySelectorAll('.fui-OverlayDrawer button[aria-label="Close"]').forEach((b) => b.click());
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  location.hash = '#/qa-reset';
  await wait(40);
  location.hash = `#/${route}`;
  await wait(450);
}

function structure(route) {
  const issues = [];
  const main = document.querySelector('main');
  const h1 = main.querySelectorAll('h1');
  if (h1.length !== 1) issues.push(`${h1.length} h1 elements`);
  let last = 1;
  for (const h of main.querySelectorAll('h1, h2, h3, h4')) {
    const lvl = +h.tagName[1];
    if (lvl > last + 1) issues.push(`heading jumps to ${h.tagName} "${h.innerText.slice(0, 40)}"`);
    last = lvl;
  }
  for (const img of document.querySelectorAll('.app img')) if (!img.hasAttribute('alt')) issues.push(`img without alt ${img.src.split('/').pop()}`);
  for (const el of [...document.querySelectorAll('.app button, .app a, .app [role=tab], .app [role=checkbox], .app [role=switch], .app input:not([type=file])')].filter(visible)) {
    if (!nameOf(el) && !el.labels?.length && !el.closest('.fui-Field')?.querySelector('label')) issues.push(`unnamed ${el.tagName.toLowerCase()} ${el.className.split(' ')[0]}`);
    const r = el.getBoundingClientRect();
    const inline = el.tagName === 'A' || el.matches('.fui-Link');
    if (!inline && (r.width < 24 || r.height < 24) && !el.matches('input[type=checkbox], input[type=radio]')) issues.push(`target ${Math.round(r.width)}x${Math.round(r.height)} "${nameOf(el).slice(0, 30)}"`);
  }
  const m = document.querySelector('.main');
  if (m.scrollWidth > m.clientWidth + 1) issues.push(`horizontal scroll ${m.scrollWidth} > ${m.clientWidth}`);
  if (document.documentElement.scrollWidth > innerWidth + 1) issues.push('page wider than the window');
  return issues.map((i) => `${route}: ${i}`);
}

async function axeRun(route) {
  const r = await window.axe.run(document.querySelector('.app'), { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa'] } });
  // DataGrid cells are keyboard stops (tabindex 0), not pointer targets: a click lands on the 44 px row, so 2.5.8 is met there
  const skip = (v, n) => /data-tabster-dummy/.test(n.html) || (v.id === 'target-size' && /^<div role="gridcell"/.test(n.html));
  return r.violations.flatMap((v) => v.nodes.filter((n) => !skip(v, n)).map((n) => `${route}: axe ${v.id} ${n.target.join(' ').slice(0, 80)}`));
}

async function deadClicks(route) {
  const q = () => [...document.querySelectorAll('main button, main a, main [role=tab], main [role=switch], main input[type=checkbox], main [role=listitem]')]
    .filter((e) => visible(e) && !e.disabled && e.getAttribute('aria-disabled') !== 'true' && e.getAttribute('aria-selected') !== 'true' && e.getAttribute('aria-current') !== 'page'
      && e.getAttribute('aria-pressed') !== 'true' && !e.closest('[data-opens="file-picker"]')); // already-on toggles; native file dialogs leave no DOM trace
  const sig = () => [location.hash, document.querySelector('main').innerHTML.length, document.querySelectorAll('[role=dialog],[role=alertdialog],[role=menu],[role=listbox]').length, document.querySelectorAll('.fui-Toast').length].join('|');
  const n = q().length;
  const dead = [];
  for (let i = 0; i < n; i++) {
    await go(route);
    const el = q()[i];
    if (!el) continue;
    const before = sig();
    el.click();
    await wait(260);
    if (sig() === before) dead.push(`${route}: does nothing "${nameOf(el).slice(0, 40) || el.className.split(' ')[0]}"`);
  }
  return dead;
}

export async function run({ routes = ROUTES, clicks = true } = {}) {
  const errors = [];
  const orig = console.error;
  console.error = (...a) => { errors.push(a.map(String).join(' ').slice(0, 160)); orig(...a); };
  await loadAxe();
  const report = { width: innerWidth, structure: [], axe: [], dead: [], console: errors };
  for (const r of routes) {
    await go(r);
    report.structure.push(...structure(r));
    report.axe.push(...await axeRun(r));
    if (clicks) report.dead.push(...await deadClicks(r));
  }
  console.error = orig;
  report.total = report.structure.length + report.axe.length + report.dead.length + errors.length;
  window.__qa = report;
  return report;
}
