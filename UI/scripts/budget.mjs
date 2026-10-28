// Bundle budget, run after `vite build`: gzip size of the entry chunk, the largest lazy chunk, and the CSS.
// The entry is mostly Fluent and React (react-dom 132 kB, tabster 74 kB minified); raise a limit only on purpose.
import { readdirSync, readFileSync } from 'node:fs';
import { gzipSync } from 'node:zlib';

const LIMITS = { entry: 260, chunk: 95, css: 12 }; // kB, gzip
const dir = 'dist/assets';
const kb = (f) => gzipSync(readFileSync(`${dir}/${f}`)).length / 1000;
const files = readdirSync(dir);
const js = files.filter((f) => f.endsWith('.js'));
const entry = js.filter((f) => f.startsWith('index-')).map(kb).reduce((a, b) => a + b, 0);
const chunk = Math.max(...js.filter((f) => !f.startsWith('index-')).map(kb));
const css = files.filter((f) => f.endsWith('.css')).map(kb).reduce((a, b) => a + b, 0);
const sizes = { entry, chunk, css };
let failed = false;
for (const [k, v] of Object.entries(sizes)) {
  const over = v > LIMITS[k];
  failed ||= over;
  console.log(`${over ? 'OVER' : 'ok  '} ${k.padEnd(5)} ${v.toFixed(1).padStart(6)} kB of ${LIMITS[k]} kB`);
}
process.exit(failed ? 1 : 0);
