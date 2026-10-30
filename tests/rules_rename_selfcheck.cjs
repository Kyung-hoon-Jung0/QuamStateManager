// A qubit rename reaches the rules (source, excluded / reviewed keys, source_names), by the token rule.
//  N1 q1 -> qA: source, keys, names follow
//  N2 q10 is left alone when q1 is renamed
//  N3 a swap q1 <-> q2 is simultaneous (never q1 -> q2 -> q1)
'use strict';
const T = require('./rules_world.cjs');
const { ok } = T;
const J = JSON.stringify;

(async () => {
  const { w } = T.makeAddWorld({ nq: 3 });
  const rule = () => ({ id: 'r1', source: 'q1-2', excluded: ['1-2', '10-11'], reviewed: { '1-2': 'sig-a', '10-11': 'sig-b' },
    source_names: { high: 'cz_bm_high_q1_q10', low: 'cz_bm_low_q10_q1' }, gate: { name: 'cz_bm' } });
  const pairs = [['q1', 'q2'], ['q10', 'q11']];
  const ids = ['q1', 'q2', 'q10', 'q11'];
  let out = w.GenRules.renameRules([rule()], { q1: 'qA' }, ids, pairs)[0];
  ok(out.source === 'qA-2', 'N1 source: ' + out.source);
  ok(J(out.excluded) === J(['2-A', '10-11']), 'N1/N2 excluded keys, q10 pair untouched: ' + J(out.excluded));
  ok(J(Object.keys(out.reviewed).sort()) === J(['10-11', '2-A']) && out.reviewed['2-A'] === 'sig-a' && out.reviewed['10-11'] === 'sig-b', 'N1 reviewed keys with their signatures: ' + J(out.reviewed));
  ok(out.source_names.high === 'cz_bm_high_qA_q10' && out.source_names.low === 'cz_bm_low_q10_qA', 'N2 names: q1 token renamed, q10 kept: ' + J(out.source_names));
  out = w.GenRules.renameRules([rule()], { q1: 'q2', q2: 'q1' }, ids, pairs)[0];
  ok(out.source === 'q2-1' && out.excluded[0] === '1-2' && out.source_names.high === 'cz_bm_high_q2_q10', 'N3 swap is simultaneous: ' + J(out));
  const same = [rule()];
  ok(J(w.GenRules.renameRules(same, {}, ids, pairs)) === J(same), 'N0 an empty map changes nothing');
  T.done('rules_rename_selfcheck');
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
