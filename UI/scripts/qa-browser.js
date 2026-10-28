// Runtime quality gate, run inside the dev app: `await (await import('/scripts/qa-browser.js')).run()`. Hover contrast: scripts/qa-hover.mjs.
// Per route: axe (WCAG 2.2 AA), one h1, no skipped heading levels, alt on every image, a name on every control,
// 24x24 minimum targets (WCAG 2.5.8), no horizontal scroll, console errors, and controls that do nothing when clicked.
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
export const ROUTES = ['home', 'extract', 'review', 'review/doc-dachser', 'review/scan-kuehne-nagel', 'library', 'library/data', 'insights', 'run/R-1042',
  'profiles', 'profile/freight', 'workflows', 'engines', 'engines/ocr', 'engines/privacy', 'data', 'users', 'audit', 'updates'];

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
  // pages load on first visit: wait for the page's heading, then let it settle
  for (let t = 0; t < 40 && !document.querySelector('main h1'); t++) await wait(100);
  await wait(300);
  await document.fonts.ready; // measure with the real fonts: a fallback face is narrower and hides crowding
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
    // SpinButton arrows meet 2.5.8 by the equivalent exception: the number can be typed or changed with arrow keys
    if (!inline && (r.width < 24 || r.height < 24) && !el.matches('input[type=checkbox], input[type=radio], .fui-SpinButton__incrementButton, .fui-SpinButton__decrementButton')) issues.push(`target ${Math.round(r.width)}x${Math.round(r.height)} "${nameOf(el).slice(0, 30)}"`);
  }
  const m = document.querySelector('.main');
  if (m.scrollWidth > m.clientWidth + 1) issues.push(`horizontal scroll ${m.scrollWidth} > ${m.clientWidth}`);
  if (document.documentElement.scrollWidth > innerWidth + 1) issues.push('page wider than the window');
  return issues.map((i) => `${route}: ${i}`);
}

// What the rule checks above missed: things a person sees. Clipped content, cut-off placeholders and labels, overlapping text,
// and selection bars that run into their labels.
const ownText = (e) => [...e.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
const hit = (a, b, pad = 1) => a.right - pad > b.left && a.left + pad < b.right && a.bottom - pad > b.top && a.top + pad < b.bottom;
const label = (e) => (e.innerText || e.getAttribute('placeholder') || e.className.toString().split(' ')[0]).trim().replace(/\s+/g, ' ').slice(0, 30);
function visual(route) {
  const issues = [];
  const leaves = [...document.querySelectorAll('.app *')].filter((e) => visible(e) && ownText(e) && !e.closest('.sr-only, .page-img')
    && +getComputedStyle(e).opacity > 0 && getComputedStyle(e).visibility === 'visible');
  // 1. cut by a parent that clips instead of scrolling (Fluent buttons clip their content)
  for (const el of [...leaves, ...document.querySelectorAll('.app .count-pip')]) {
    const r = el.getBoundingClientRect();
    if (getComputedStyle(el).textOverflow === 'ellipsis') continue;
    for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) {
      const s = getComputedStyle(a);
      const cx = /hidden|clip/.test(s.overflowX), cy = /hidden|clip/.test(s.overflowY);
      if (!cx && !cy) { if (s.overflowX !== 'visible' || s.overflowY !== 'visible') break; continue; } // a scroller: its content scrolls, fine
      const b = a.getBoundingClientRect();
      if (hit(r, b, 0) && ((cx && (r.left < b.left - 1 || r.right > b.right + 1)) || (cy && (r.top < b.top - 1 || r.bottom > b.bottom + 1)))) issues.push(`clipped "${label(el)}"`);
      break;
    }
  }
  // 2. placeholders and values wider than their box
  const ctx = document.createElement('canvas').getContext('2d');
  for (const i of [...document.querySelectorAll('.app input:not([type=file]):not([type=checkbox]):not([type=radio]):not([type=range])')].filter(visible)) {
    const cs = getComputedStyle(i);
    ctx.font = cs.font;
    const room = i.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
    const text = i.value || i.placeholder;
    if (text && ctx.measureText(text).width > room + 1) issues.push(`${i.value ? 'value' : 'placeholder'} cut off "${text.slice(0, 30)}"`);
  }
  // 3. labels truncated where nothing else shows the whole text
  for (const e of [...document.querySelectorAll('.app button *, .app [role=tab] *, .app th, .app th *, .app .fui-Dropdown__button')].filter(visible)) {
    if (getComputedStyle(e).textOverflow === 'ellipsis' && e.scrollWidth > e.clientWidth + 1) issues.push(`truncated "${label(e)}"`);
  }
  // 4. text on text, and selection bars on their labels
  // only what is on screen: each text's box cut to its scrolling and clipping parents; a real overlap covers a quarter of the smaller text
  const seen = (e) => {
    // the text's own line boxes, not the element's padding
    const range = document.createRange();
    const rs = [...e.childNodes].filter((n) => n.nodeType === 3 && n.textContent.trim()).map((n) => { range.selectNodeContents(n); return range.getBoundingClientRect(); });
    let r = { left: Math.min(...rs.map((x) => x.left)), right: Math.max(...rs.map((x) => x.right)), top: Math.min(...rs.map((x) => x.top)), bottom: Math.max(...rs.map((x) => x.bottom)) };
    for (let a = e.parentElement; a && a !== document.body; a = a.parentElement) {
      const st = getComputedStyle(a);
      if (st.overflowX === 'visible' && st.overflowY === 'visible') continue;
      const b = a.getBoundingClientRect();
      r = { left: Math.max(r.left, b.left), right: Math.min(r.right, b.right), top: Math.max(r.top, b.top), bottom: Math.min(r.bottom, b.bottom) };
    }
    return r.right > r.left && r.bottom > r.top ? r : null;
  };
  const area = (r) => (r.right - r.left) * (r.bottom - r.top);
  const boxes = leaves.slice(0, 700).map((e) => [e, seen(e)]).filter(([, r]) => r);
  for (let i = 0; i < boxes.length; i++) {
    for (let j = i + 1; j < boxes.length; j++) {
      const [a, ra] = boxes[i], [b, rb] = boxes[j];
      if (a.contains(b) || b.contains(a) || !hit(ra, rb, 0)) continue;
      const both = { left: Math.max(ra.left, rb.left), right: Math.min(ra.right, rb.right), top: Math.max(ra.top, rb.top), bottom: Math.min(ra.bottom, rb.bottom) };
      if (area(both) > 0.25 * Math.min(area(ra), area(rb))) issues.push(`text overlaps: "${label(a)}" and "${label(b)}"`);
    }
  }
  for (const el of document.querySelectorAll('.app .is-on')) {
    const p = getComputedStyle(el, '::before');
    if (p.content === 'none' || p.position !== 'absolute') continue;
    const r = el.getBoundingClientRect();
    const x = r.left + (parseFloat(p.left) || 0), y = r.top + (parseFloat(p.top) || 0);
    // at least 4 px of clear space between the bar and the label: closer than that reads as touching
    const bar = { left: x - 4, right: x + parseFloat(p.width) + 4, top: y - 4, bottom: y + parseFloat(p.height) + 4 };
    for (const t of [...el.querySelectorAll('*')].filter((e) => ownText(e) && visible(e))) if (hit(bar, t.getBoundingClientRect(), 0)) issues.push(`selection bar touches "${label(t)}"`);
  }
  return [...new Set(issues)].map((i) => `${route}: ${i}`);
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
      && e.getAttribute('aria-pressed') !== 'true' && !e.closest('[data-opens="file-picker"]') && !e.matches('[class*=SpinButton__]')); // already-on toggles; native file dialogs leave no DOM trace; spin arrows act on pointer-down
  // full markup and control state, so a row swap, a highlight, a spinner, or a switch flip all count as a change
  const sig = () => [location.hash, document.querySelector('main').innerHTML, [...document.querySelectorAll('main input')].map((i) => (/checkbox|radio/.test(i.type) ? i.checked : i.value)).join('|'), document.querySelectorAll('[role=dialog],[role=alertdialog],[role=menu],[role=listbox]').length, document.querySelectorAll('.fui-Toast').length].join('|');
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
    report.structure.push(...visual(r));
    report.axe.push(...await axeRun(r));
    if (clicks) report.dead.push(...await deadClicks(r));
  }
  console.error = orig;
  report.total = report.structure.length + report.axe.length + report.dead.length + errors.length;
  window.__qa = report;
  return report;
}
