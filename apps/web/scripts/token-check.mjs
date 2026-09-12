#!/usr/bin/env node
// apps/web/scripts/token-check.mjs
//
// Ensures every CSS var referenced via `var(--name)` in src is either
// declared in src/styles/globals.css `@theme {}` block or is a known
// browser-native var. Catches the F1.1 class of bugs (undeclared
// aliases used by BackfillTab / Settings / TickerDrilldown).
//
// Usage:  node apps/web/scripts/token-check.mjs
// Exit 0 on PASS, 1 on any FAIL.

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';
import { execSync } from 'node:child_process';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '..');
const cssPath = resolve(root, 'src/styles/globals.css');

// 1. Collect declared vars from globals.css
const css = readFileSync(cssPath, 'utf8');
const declared = new Set();
for (const m of css.matchAll(/--([\w-]+):/g)) declared.add(m[1]);

// 2. Collect referenced vars via grep (cheaper than AST for whole tree)
let grepOut = '';
try {
  grepOut = execSync(
    `grep -rohE "var\\(--[a-zA-Z0-9_-]*\\)" ${resolve(root, 'src')}`,
    { encoding: 'utf8', shell: '/bin/bash' },
  );
} catch (e) {
  grepOut = e.stdout?.toString() || '';
}
const referenced = new Set();
for (const m of grepOut.matchAll(/var\(--([a-zA-Z0-9_-]*)\)/g)) {
  referenced.add(m[1]);
}

// Browser-native / Tailwind-known vars we tolerate (none for now, but
// extension point)
const allowed = new Set([
  // 'background',  // example: if we wanted to allow unprefixed native
]);

const missing = [...referenced].filter(
  (v) => !declared.has(v) && !allowed.has(v),
);

if (missing.length) {
  console.error('Referenced CSS vars not declared in globals.css:');
  for (const v of missing) console.error(`  --${v}`);
  console.error(`\nAdd them to the @theme {} block in src/styles/globals.css.`);
  process.exit(1);
}
console.log(`All ${referenced.size} referenced CSS vars are declared.`);
