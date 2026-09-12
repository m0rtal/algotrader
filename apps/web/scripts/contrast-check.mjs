#!/usr/bin/env node
// apps/web/scripts/contrast-check.mjs
//
// Parses `@theme {}` tokens from src/styles/globals.css and asserts every
// foreground/background pair used in the design audit meets WCAG 2.2 AA.
//
// Usage:  node apps/web/scripts/contrast-check.mjs
// Exit 0 on PASS, 1 on any FAIL.

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const cssPath = resolve(here, '../src/styles/globals.css');
const css = readFileSync(cssPath, 'utf8');

const tokenRe = /--color-([\w-]+):\s*(#[0-9a-fA-F]+)/g;
const tokens = {};
for (const m of css.matchAll(tokenRe)) tokens[m[1]] = m[2];

function lum(hex) {
  const [r, g, b] = [hex.slice(1, 3), hex.slice(3, 5), hex.slice(5, 7)].map((h) =>
    parseInt(h, 16),
  );
  const ch = (c) => {
    c /= 255;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  };
  return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b);
}
function ratio(a, b) {
  const [L1, L2] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (L1 + 0.05) / (L2 + 0.05);
}

// (label, fg-token, bg-token, threshold, kind)
// kind: 'text' -> 4.5:1 (AA body) or 3:1 (large)
//       'ui'   -> 3:1 (AA non-text)
const pairs = [
  ['body text on bg',          'text',        'bg',         4.5, 'text'],
  ['text-muted on bg',         'text-muted',  'bg',         4.5, 'text'],
  ['text-dim on bg',           'text-dim',    'bg',         4.5, 'text'],
  ['accent on bg (link/tab)',  'accent',      'bg',         4.5, 'text'],
  ['green on bg',              'green',       'bg',         4.5, 'text'],
  ['red on bg',                'red',         'bg',         4.5, 'text'],
  ['amber on bg',              'amber',       'bg',         4.5, 'text'],
  ['text-muted on surface',    'text-muted',  'surface',    4.5, 'text'],
  ['text-dim on surface',      'text-dim',    'surface',    4.5, 'text'],
  ['accent on surface',        'accent',      'surface',    3.0, 'ui'],
  ['green on surface',         'green',       'surface',    3.0, 'ui'],
  ['red on surface',           'red',         'surface',    3.0, 'ui'],
  ['text on surface',          'text',        'surface',    4.5, 'text'],
];

let fails = 0;
for (const [label, fg, bg, need, kind] of pairs) {
  const r = ratio(tokens[fg], tokens[bg]);
  const ok = r >= need;
  console.log(
    `  ${ok ? 'PASS' : 'FAIL'}  ${label.padEnd(30)} ${fg.padEnd(12)} on ${bg.padEnd(10)} ${r.toFixed(2)}:1  (need ${need}:1 ${kind})`,
  );
  if (!ok) fails++;
}

if (fails) {
  console.error(`\n${fails} contrast pair(s) failed WCAG 2.2 AA.`);
  process.exit(1);
}
console.log('\nAll contrast pairs pass WCAG 2.2 AA.');
