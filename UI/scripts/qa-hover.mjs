// Hover readability: moves a real mouse over every button in the header, rail, command bars and toolbars, then checks
// that its icon (3:1) and label (4.5:1) still contrast with what is actually behind them, gradients included.
// Needs the dev server and Chrome. Usage: node scripts/qa-hover.mjs [http://localhost:5174] [light|dark]
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const BASE = process.argv[2] || 'http://localhost:5174';
const SCHEME = process.argv[3] || 'light';
const ROUTES = ['home', 'extract', 'library', 'library/data', 'review/doc-dachser', 'insights', 'engines'];
const CHROME = process.env.CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const profile = mkdtempSync(join(tmpdir(), 'qa-hover-'));
const port = 9400 + Math.floor(Math.random() * 400);
const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, '--no-first-run', 'about:blank'], { stdio: 'ignore' });
let targets = [];
for (let i = 0; i < 50 && !targets.length; i++) { try { targets = (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).filter((t) => t.type === 'page'); } catch { /* starting */ } await sleep(200); }
const ws = new WebSocket(targets[0].webSocketDebuggerUrl);
await new Promise((r) => ws.addEventListener('open', r));
let id = 0; const pending = new Map();
ws.addEventListener('message', (e) => { const m = JSON.parse(e.data); if (pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } });
const send = (method, params = {}) => new Promise((r) => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
const run = async (fn, ...args) => (await send('Runtime.evaluate', { expression: `(${fn})(...${JSON.stringify(args)})`, awaitPromise: true, returnByValue: true })).result?.result?.value;

// In the page: tag the buttons to hover, and measure one of them.
const tag = () => [...document.querySelectorAll('.appbar button, .rail button, .commandbar button, .page-toolbar button, .doc-head button')]
  .filter((b) => { const r = b.getBoundingClientRect(); return r.width && r.height && !b.disabled && b.getAttribute('aria-disabled') !== 'true'; }) // inactive controls are exempt (WCAG 1.4.3)
  .map((b, i) => { b.dataset.qaHover = i; const r = b.getBoundingClientRect(); return [i, r.x + r.width / 2, r.y + r.height / 2]; });
const measure = (i) => {
  const b = document.querySelector(`[data-qa-hover="${i}"]`);
  const rgba = (c) => { const m = c.match(/rgba?\(([^)]+)\)/); if (!m) return null; const [r, g, bl, a = 1] = m[1].split(/[ ,/]+/).filter(Boolean).map(Number); return [r, g, bl, a]; };
  const lum = ([r, g, bl]) => [r, g, bl].map((v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }).reduce((s, v, k) => s + v * [0.2126, 0.7152, 0.0722][k], 0);
  const ratio = (x, y) => { const [a, c] = [lum(x), lum(y)].sort((p, q) => q - p); return (a + 0.05) / (c + 0.05); };
  const over = (top, under) => top.slice(0, 3).map((v, k) => v * top[3] + under[k] * (1 - top[3]));
  // what is behind an element: its own and its ancestors' fills, composited; a gradient contributes every colour, and the worst counts
  const behind = (el) => {
    const layers = [];
    for (let e = el; e; e = e.parentElement) {
      const s = getComputedStyle(e);
      const grad = [...s.backgroundImage.matchAll(/rgba?\([^)]+\)/g)].map((m) => rgba(m[0]));
      const fill = rgba(s.backgroundColor);
      if (fill && fill[3] > 0) layers.push([fill]);
      if (grad.length && grad.some((g) => g[3] > 0.9)) { layers.push(grad.filter((g) => g[3] > 0.9)); break; }
      if (fill && fill[3] >= 1) break;
    }
    const bases = layers.length ? layers[layers.length - 1].map((c) => c.slice(0, 3)) : [[255, 255, 255]];
    return bases.map((base) => layers.slice(0, -1).reverse().reduce((acc, [c]) => over(c, acc), base));
  };
  const icon = b.querySelector('svg');
  const texts = [...b.querySelectorAll('*')].filter((e) => [...e.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim()) && e.getBoundingClientRect().width);
  const worst = (el) => Math.min(...behind(el).map((bg) => ratio(rgba(getComputedStyle(el).color), bg)));
  return { name: b.getAttribute('aria-label') || b.innerText.trim().split('\n')[0] || b.className.split(' ')[0], icon: icon && worst(icon), text: texts.length ? Math.min(...texts.map(worst)) : null };
};

const fails = [];
await send('Emulation.setDeviceMetricsOverride', { width: 1366, height: 768, deviceScaleFactor: 1, mobile: false });
await send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: SCHEME }] });
for (const route of ROUTES) {
  await send('Page.navigate', { url: `${BASE}/#/${route}` });
  await sleep(2500);
  for (const [i, x, y] of await run(tag)) {
    await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y });
    await sleep(160);
    const m = await run(measure, i);
    if (m.icon && m.icon < 3) fails.push(`${route}: "${m.name}" icon ${m.icon.toFixed(2)}:1 on hover`);
    if (m.text && m.text < 4.5) fails.push(`${route}: "${m.name}" label ${m.text.toFixed(2)}:1 on hover`);
  }
  await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: 1, y: 760 });
}
ws.close();
await new Promise((r) => { chrome.once('exit', r); chrome.kill(); });
rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
console.log(fails.length ? `FAIL (${fails.length}, ${SCHEME})\n  ${[...new Set(fails)].join('\n  ')}` : `Hover readability passed (${SCHEME})`);
process.exit(fails.length ? 1 : 0);
