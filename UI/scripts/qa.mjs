// Static quality gate for the UI. No dependencies. Exit code 1 on any failure.
// Run: node scripts/qa.mjs
import { readFileSync, readdirSync, existsSync, statSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const SRC = join(ROOT, 'src');
const failures = [];
const warnings = [];
const fail = (area, msg) => failures.push(`${area}: ${msg}`);
const warn = (area, msg) => warnings.push(`${area}: ${msg}`);
const walk = (d) => readdirSync(d).flatMap((f) => (statSync(join(d, f)).isDirectory() ? walk(join(d, f)) : [join(d, f)]));
const files = walk(SRC).filter((f) => /\.(jsx?|css)$/.test(f));
const read = (f) => readFileSync(f, 'utf8');
const rel = (f) => f.replace(ROOT + '/', '');

// ---------- colour maths ----------
const hexRgb = (h) => { h = h.replace('#', ''); if (h.length === 3) h = [...h].map((c) => c + c).join(''); return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)); };
const rgbHex = (r) => '#' + r.map((v) => Math.round(v).toString(16).padStart(2, '0')).join('').toUpperCase();
const lin = (c) => { c /= 255; return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; };
const lum = (hex) => { const [r, g, b] = hexRgb(hex).map(lin); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
const contrast = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
const over = (fg, alpha, bg) => rgbHex(hexRgb(fg).map((v, i) => v * alpha + hexRgb(bg)[i] * (1 - alpha)));

// gradient(...) bodies with balanced parentheses, wherever they appear (CSS or inline JSX styles)
function gradients(s) {
  const out = [];
  for (const m of s.matchAll(/(linear|radial)-gradient\(/g)) {
    let depth = 0, i = m.index + m[0].length - 1;
    for (; i < s.length; i++) { if (s[i] === '(') depth++; else if (s[i] === ')' && --depth === 0) break; }
    out.push(s.slice(m.index, i + 1));
  }
  return out;
}

// ---------- 1. Harsha palette: every colour in the code is a Harsha ramp step, white, black, or Rule Black ----------
const theme = read(join(SRC, 'theme.js'));
const PALETTE = new Set([...theme.matchAll(/#[0-9A-Fa-f]{6}\b/g)].map((m) => m[0].toUpperCase()));
['#FFFFFF', '#000000', '#231F20', '#0066B3', '#303F99', '#DB2128', '#00AB4E', '#A7A9AC',
  '#00437A', '#6AA9EB', '#90C6FF', '#F1F8FF', '#005121', '#006B2E', '#9FF2B0', '#EAFCED', '#171D6C', '#AEBFF6', '#EBEDEF', '#CF081C', '#F0F3F7',
].forEach((c) => PALETTE.add(c));
const hue = (hex) => {
  const [r, g, b] = hexRgb(hex);
  const max = Math.max(r, g, b), min = Math.min(r, g, b);
  if (max - min < 24) return 'neutral';
  if (g >= r && g >= b) return 'green';
  if (r >= g && r >= b) return b > g ? 'indigo' : 'red';
  return r > 40 && b > r ? 'indigo' : 'blue';
};
for (const f of files) {
  const s = read(f);
  for (const m of s.matchAll(/#[0-9A-Fa-f]{6}\b/g)) {
    if (!PALETTE.has(m[0].toUpperCase())) fail('palette', `${rel(f)} uses ${m[0]}, not a Harsha ramp step`);
  }
  for (const m of s.matchAll(/rgba?\((\d+),\s*(\d+),\s*(\d+)/g)) {
    const hex = rgbHex([+m[1], +m[2], +m[3]]);
    if (!PALETTE.has(hex)) fail('palette', `${rel(f)} uses rgb(${m[1]}, ${m[2]}, ${m[3]}) = ${hex}, not a Harsha ramp step`);
  }
  // ---------- 2. gradients: one hue per gradient (bokashi), never cross-hue ----------
  for (const g of gradients(s)) {
    const m = [g, null, g];
    const cols = [...m[2].matchAll(/#[0-9A-Fa-f]{6}|var\(--(sky|growth|seal|steel)[a-z-]*\)|rgba?\((\d+),\s*(\d+),\s*(\d+)/g)].map((c) => {
      if (c[1]) return c[1] === 'sky' ? 'blue' : c[1] === 'seal' ? 'indigo' : c[1] === 'steel' ? 'neutral' : 'green';
      if (c[3]) return hue(rgbHex([+c[3], +c[4], +c[5]]));
      return hue(c[0]);
    }).filter((h) => h !== 'neutral');
    if (new Set(cols).size > 1) fail('gradient', `${rel(f)} mixes hues in one gradient: ${m[0].slice(0, 90)}`);
  }
}

// ---------- 3. contrast matrix (WCAG 2.2 AA: text 4.5:1, large/bold 3:1, UI 3:1) ----------
const TEXT = '#231F20', TEXT3 = '#57585A', WHITE = '#FFFFFF';
const skyGlow = over('#90C6FF', 0.55, '#F1F8FF');
const growthGlow = over('#9FF2B0', 0.55, '#EAFCED');
const headerBloom = over('#6AA9EB', 0.38, '#0066B3');
// translucent materials at their worst case
const wallPeak = over('#90C6FF', 0.55, '#F0F3F7');
const railOnPeak = over('#FFFFFF', 0.42, wallPeak);
const glassOnPeak = over('#FFFFFF', 0.5, over('#90C6FF', 0.5, wallPeak));
const acrylicOverInk = over('#FFFFFF', 0.72, '#231F20'); // command bar over dark table text
const pairs = [
  ['body text on white', TEXT, WHITE, 4.5], ['body text on page bg', TEXT, '#F0F3F7', 4.5],
  ['body text on sky wash', TEXT, '#F1F8FF', 4.5], ['body text on sky glow (peak)', TEXT, skyGlow, 4.5],
  ['body text on growth glow (peak)', TEXT, growthGlow, 4.5], ['secondary text on white', TEXT3, WHITE, 4.5],
  ['secondary text on sky glow', TEXT3, skyGlow, 4.5], ['secondary text on page bg', TEXT3, '#F0F3F7', 4.5],
  ['header text on deep sky', WHITE, '#00437A', 4.5], ['header text on brand sky', WHITE, '#0066B3', 4.5],
  ['header icons on header bloom', WHITE, headerBloom, 3], ['bell count pip', WHITE, '#CF081C', 4.5],
  ['review count pip', WHITE, '#0066B3', 4.5], ['link on white', '#0066B3', WHITE, 4.5], ['link on page bg', '#0066B3', '#F0F3F7', 4.5],
  ['danger text on danger tint', '#A80013', '#FFF4F2', 4.5], ['success text on success tint', '#006B2E', '#EAFCED', 4.5],
  ['brand badge text on brand tint', '#00599E', '#E0EFFF', 4.5], ['seal band text', WHITE, '#303F99', 4.5],
  ['seal band text (deep)', WHITE, '#171D6C', 4.5], ['growth band text', WHITE, '#006B2E', 4.5],
  ['primary button text', WHITE, '#0066B3', 4.5],
  ['rail label on Mica over wallpaper peak', '#414244', railOnPeak, 4.5], ['rail selected label', '#0066B3', over('#FFFFFF', .78, railOnPeak), 4.5],
  ['welcome text on glass over peak', TEXT, glassOnPeak, 4.5], ['welcome lead on glass', '#414244', glassOnPeak, 4.5],
  ['command bar text over scrolled ink', TEXT, acrylicOverInk, 4.5], ['page title on wallpaper peak', TEXT, wallPeak, 4.5],
  ['caption on wallpaper peak', TEXT3, wallPeak, 4.5], ['control border', '#87898C', WHITE, 3],
  ['flag box on page', '#DB2128', WHITE, 3], ['selected box on page', '#0066B3', WHITE, 3], ['PDF file icon', '#DB2128', WHITE, 3],
];
for (const [name, fg, bg, min] of pairs) {
  const c = contrast(fg, bg);
  if (c < min) fail('contrast', `${name}: ${c.toFixed(2)}:1 < ${min}:1 (${fg} on ${bg})`);
}

// ---------- 4. Fluent and Harsha writing rules ----------
const PROPER = new Set(('Harsha Engineers Excel CSV PDF PDFs AI API APIs OCR ERP Teams Fluent Biswajoy Microsoft Claude Azure AWS Google Vertex OAuth IAM HTTPS URL PostgreSQL SQL ' +
  'Server MySQL Oracle GSTIN PAN IFSC ISO CGST SGST ENS AMS MAWB DPI JSON Gujarati Hindi English German Chinese DACHSER DSV EPT EXIM GLOBAL JEENA KUEHNE NAGEL MEGALO ' +
  'Kuehne Nagel Megalo Jeena Exim Global Logistics Pvt Ltd India Air Sea Company Priya Shah Rahul Patel Meera Joshi Finance DB ERP Analytics Precision Qwen3 LoRA ' +
  'LLM PII USD INR R-1042 R-1041 Sysadmin Document Extraction Rows Columns ISPS PP-OCRv5 SwinIR PyMuPDF LAYA Docling MinerU Tesseract SageMaker Bedrock ' +
  'Home Review Documents Insights Settings Databases Profiles Automations People Updates Navigation ' +
  'Anthropic OpenAI-Compatible REST Textract Vision Read Intelligence ONNX Runtime PaddlePaddle PyTorch Transformers Ollama vLLM').split(' '));
const SMALL = new Set('a an the and or of to in on for with by at as is it be not no from per vs'.split(' '));
const strings = [];
for (const f of files.filter((x) => x.endsWith('.jsx'))) {
  const s = read(f);
  for (const m of s.matchAll(/>\s*([A-Za-z][^<>{}\n]{2,}?)\s*</g)) strings.push([f, m[1].trim()]);
  for (const m of s.matchAll(/\b(label|placeholder|title|hint|content|aria-label|secondaryContent|body|header|description|validationMessage)="([^"]{3,})"/g)) strings.push([f, m[2]]);
  for (const m of s.matchAll(/notify\('([^']{3,})'/g)) strings.push([f, m[1]]);
}
for (const [f, t] of strings) {
  if (/!/.test(t) && !/!==|!\w/.test(t)) fail('copy', `${rel(f)}: exclamation mark in "${t}"`);
  if (/\b(please|successfully|click here|are you sure)\b/i.test(t)) fail('copy', `${rel(f)}: "${t}" uses wording Fluent asks to avoid`);
  if (/·/.test(t)) fail('copy', `${rel(f)}: interpunct chain in "${t}"`);
  if (/\p{Extended_Pictographic}/u.test(t)) fail('copy', `${rel(f)}: emoji in copy "${t}"`);
  const words = t.split(/\s+/).filter((w) => /^[A-Za-z]/.test(w));
  const caps = words.slice(1).filter((w) => !SMALL.has(w.toLowerCase()) && /^[A-Z][a-z]/.test(w) && !PROPER.has(w.replace(/[^A-Za-z0-9-]/g, '')));
  if (words.length >= 2 && caps.length >= 1 && caps.length >= words.slice(1).filter((w) => !SMALL.has(w.toLowerCase())).length) {
    fail('copy', `${rel(f)}: looks like Title Case, Fluent web uses sentence case: "${t}"`);
  }
}

// ---------- 5. assets ----------
const PICT = join(ROOT, 'public', 'pictograms');
if (!existsSync(join(PICT, 'LICENSE.md'))) fail('assets', 'public/pictograms/LICENSE.md missing');
for (const f of files) {
  const s = read(f);
  for (const m of s.matchAll(/(?:picto|name)=["']([a-z_]+)["']|picto: ["']([a-z_]+)["']|\/pictograms\/([a-z_]+)\.png|(?:PICTOS?|NOTE_PICTO)\s*=\s*\{([^}]*)\}/g)) {
    const names = m[4] ? [...m[4].matchAll(/'([a-z_]+)'/g)].map((x) => x[1]) : [m[1] || m[2] || m[3]];
    for (const n of names) {
      if (!n || /^(role|lang|apikey|bearer|oauth|iam|none)$/.test(n)) continue; // radio group names, not pictures
      if (!existsSync(join(PICT, `${n}.png`))) fail('assets', `${rel(f)} references missing picture "${n}"`);
    }
  }
}
const samples = JSON.parse(read(join(SRC, 'samples.json')));
for (const d of samples) for (const p of d.pages) if (!existsSync(join(ROOT, 'public', p.src))) fail('assets', `${d.name} page image ${p.src} missing`);
if (!existsSync(join(ROOT, 'public', 'fonts', 'eurostile-extended-black.ttf'))) fail('assets', 'Harsha brand font missing');

// ---------- 6. Fluent only: no hand-drawn icons, no raw controls, no second UI kit ----------
for (const f of files.filter((x) => x.endsWith('.jsx'))) {
  const s = read(f);
  if (/<svg\b/.test(s)) fail('fluent', `${rel(f)} draws its own SVG`);
  for (const m of s.matchAll(/<(button|select|textarea)\b/g)) fail('fluent', `${rel(f)} hand-builds a <${m[1]}>; use the Fluent control`);
  for (const m of s.matchAll(/<input\b[^>]*>/g)) if (!/type="file"[^>]*hidden|hidden[^>]*type="file"/.test(m[0])) fail('fluent', `${rel(f)} hand-builds an <input>`);
}
const pkg = JSON.parse(read(join(ROOT, 'package.json')));
const ALLOWED = /^(@fluentui\/react-(components|icons|nav|charts)|react|react-dom)$/;
for (const dep of Object.keys(pkg.dependencies || {})) if (!ALLOWED.test(dep)) fail('fluent', `dependency ${dep} is not Fluent or React`);

// ---------- 7. brand face only on titles ----------
const css = read(join(SRC, 'index.css'));
for (const m of css.matchAll(/([^{}]+)\{[^}]*var\(--font-brand\)/g)) {
  const sel = m[1].trim();
  if (!/brand-title|big-number|brand-name|:root/.test(sel)) warn('type', `brand face applied outside titles: ${sel}`);
}

// ---------- report ----------
const out = (label, list) => list.length && console.log(`\n${label} (${list.length})\n  ${list.join('\n  ')}`);
console.log(`QA: ${files.length} files, ${strings.length} strings, ${pairs.length} contrast pairs, ${PALETTE.size} palette colours`);
out('FAIL', failures);
out('WARN', warnings);
if (!failures.length) console.log('\nAll static checks passed');
process.exit(failures.length ? 1 : 0);
