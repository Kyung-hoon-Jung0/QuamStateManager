// C15: the Apply dialog renders a 100-pair answer inside its budget (jsdom; median of 5).
// The clock starts when the answer is handed to the page and stops when the footer reads "Add to N pairs"
// (the last thing the render writes), so it is the page's own work, not the mock server's.
// Budget 50 ms (11_v2_1 C15). This measures jsdom work, not Chrome layout/paint.
'use strict';
const T = require('./rules_world.cjs');
const { ok } = T;

const BUDGET_MS = 50;

async function once() {
  const { w, srv } = T.makeWorld({ nq: 101, server: { statusOf: (pid) => {
    const n = parseInt(pid.split('-')[0].slice(1), 10);
    return n % 10 === 0 ? "Can't" : n % 7 === 0 ? 'Needs a look' : n % 13 === 0 ? 'Already has it' : 'Ready';
  } } });
  let t0 = 0, t1 = 0;
  const origFetch = w.fetch;
  w.fetch = function (url, opts) {
    return origFetch(url, opts).then(r => {
      if (String(url).indexOf('/rules/expand') < 0) return r;
      return { status: r.status, json: () => { const j = r.json(); t0 = w.performance.now(); return j; } };
    });
  };
  const mo = new w.MutationObserver(() => {
    if (!t1 && t0 && /^Add to \d+ pairs?$/.test((w.applyBtn() || {}).textContent || '')) t1 = w.performance.now();
  });
  w.GenRules.openApply({ ctx: w.ctx, rule: T.baseRule() });
  mo.observe(w.dlg(), { subtree: true, childList: true, characterData: true });
  for (let i = 0; i < 40 && !t1; i++) await T.settle(2);
  await T.settle();
  ok(w.rows().length > 60, 'rendered the rows: ' + w.rows().length);
  return t1 - t0;
}

(async () => {
  const runs = [];
  for (let i = 0; i < 5; i++) runs.push(await once());
  runs.sort((a, b) => a - b);
  const med = runs[2];
  console.log('dialog render for 100 pairs (ms): ' + runs.map(x => x.toFixed(1)).join(', ') + ' | median ' + med.toFixed(1));
  console.log('jsdom budget margin: ' + (BUDGET_MS - med).toFixed(1) + ' ms of ' + BUDGET_MS + ' ms; Chrome is measured separately');
  ok(med > 0, 'the clock ran');
  ok(med <= BUDGET_MS, 'render median ' + med.toFixed(1) + ' ms must be <= ' + BUDGET_MS + ' ms');
  T.done('rules_budget_selfcheck');
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
