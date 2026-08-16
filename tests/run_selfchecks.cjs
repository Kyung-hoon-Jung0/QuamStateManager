// Run every *_selfcheck.cjs and report. `npm run selfcheck`.
//
// A Node runner rather than a shell one-liner on purpose: npm executes scripts
// through cmd.exe on Windows, where `for f in …; do … done` is a syntax error —
// and native Windows is this project's canonical test platform (CLAUDE.md), so
// a POSIX-only script would break for exactly the developer it exists to help.
//
// Exit code 2 from a selfcheck means "jsdom not installed", which the pytest
// drivers treat as a skip; this runner reports it the same way rather than
// failing, so a machine without node_modules gets an honest message instead of
// a wall of red. (docs/120: a skipped pin and a passing pin looked identical in
// a pytest -q summary for long enough to hide four real failures.)
'use strict';

const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

const here = __dirname;
const files = fs.readdirSync(here)
  .filter((f) => f.endsWith('_selfcheck.cjs'))
  .sort();

let pass = 0, fail = 0, skip = 0;
const failed = [];
const suspect = [];

for (const f of files) {
  const r = spawnSync(process.execPath, [path.join(here, f)], {
    cwd: path.join(here, '..'), encoding: 'utf8',
  });
  const name = f.replace(/_selfcheck\.cjs$/, '');
  if (r.status === 2) {
    skip++;
    console.log(`skip  ${name}  (jsdom not installed)`);
  } else if (r.status === 0) {
    pass++;
    // Report what is actually KNOWN. Most of these files' `ok()` prints only on
    // failure, so "(0 assertions)" was a reporting artefact — but it reads as a
    // number, and it is indistinguishable from the state docs/120 opened with:
    // a harness that exits 0 having asserted nothing at all. So count when the
    // file volunteers a count, say "silent" when it does not, and treat exit-0
    // with NO OUTPUT WHATSOEVER as suspect — that is the one shape that cannot
    // be told apart from doing nothing.
    const n = (r.stdout.match(/^ok - /gm) || []).length;
    if (!r.stdout.trim()) {
      suspect.push(name);
      console.log(`ok?   ${name}  (exit 0 but NO output — cannot tell it asserted)`);
    } else {
      console.log(`ok    ${name}  ${n ? `(${n} assertions)` : '(silent style)'}`);
    }
  } else {
    fail++;
    failed.push(name);
    console.log(`FAIL  ${name}`);
    const lines = (r.stdout + r.stderr).split('\n')
      .filter((l) => l.startsWith('FAIL') || /Error/.test(l));
    lines.slice(0, 8).forEach((l) => console.log(`        ${l}`));
  }
}

console.log(`\n${pass} passed, ${fail} failed, ${skip} skipped, of ${files.length}`);
if (failed.length) console.log(`failed: ${failed.join(', ')}`);
if (suspect.length) console.log(`no output (check these ran): ${suspect.join(', ')}`);
process.exit(fail ? 1 : 0);
