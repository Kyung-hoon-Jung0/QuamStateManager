/* Agent setup: open every section, press every PREVIEW and the rig-local
 * writes (Dry run both ways, the journal folder, the lab-context preview),
 * reload, Back. Never presses a write that lands in the user's real home
 * (Connect's "Write" / codex "Write"): those files are outside the rig.
 *   SM_CDP_PORT=9413 node agent_setup_walk.cjs 5113 OUTDIR WIDTH
 */
'use strict';
const { open, sleep } = require('./cdp.cjs');
const [SM, OUT, W] = [process.argv[2], process.argv[3], +process.argv[4] || 1600];

(async () => {
  const p = await open(`http://127.0.0.1:${SM}/agent/setup`, W, 900);
  await sleep(3000);
  const at = async (sel, re, nth = 0) => {
    const r = await p.ev(`(() => { const re = ${re || 'null'}; const es = [...document.querySelectorAll(${JSON.stringify(sel)})].filter(e => e.getBoundingClientRect().width > 0 && (!re || re.test(e.textContent || e.value || '')));
      const e = es[${nth}]; if (!e) return null; e.scrollIntoView({block:'center'}); const b = e.getBoundingClientRect(); return JSON.stringify([b.x + Math.min(b.width/2, 40), b.y + b.height/2]); })()`);
    return r ? JSON.parse(r) : null;
  };
  const press = async (sel, re, nth) => { const xy = await at(sel, re, nth); if (!xy) return false; await p.click(xy[0], xy[1]); await sleep(1200); return true; };
  const secs = `[...document.querySelectorAll('#table-pane details.as-sec')].map(d => (d.open ? 'O ' : 'c ') + d.querySelector('summary').textContent.trim().slice(0, 34)).join(' | ')`;
  console.log('sections', await p.ev(secs));
  // open every closed section
  const n = await p.ev(`document.querySelectorAll('#table-pane details.as-sec').length`);
  for (let i = 0; i < n; i++) {
    const closed = await p.ev(`!document.querySelectorAll('#table-pane details.as-sec')[${i}].open`);
    if (closed) await press('#table-pane details.as-sec > summary', null, i);
  }
  console.log('opened  ', await p.ev(secs));
  await p.shot(`${OUT}/setup_all_open_${W}.png`);
  // every Preview (both connects); nothing may be written
  for (let k = 0; k < 2; k++) console.log('preview', k, await press('#table-pane button', '/Preview what SM would write/', k));
  console.log('  previews on screen', await p.ev(`document.querySelectorAll('#table-pane .as-diff, #table-pane h5 code').length`),
    '| write buttons offered', await p.ev(`[...document.querySelectorAll('#table-pane button')].filter(b => /^Write|Connect/.test(b.textContent.trim())).map(b => b.textContent.trim()).join(',')`));
  // Dry run off -> message, marker; reload keeps it; back on
  const dry = `(() => { const b = document.getElementById('as-dryrun-box'); const m = document.getElementById('as-dryrun-msg'); const s = document.querySelector('#as-dryrun > summary');
    return JSON.stringify({ checked: b && b.checked, msg: m && m.textContent.trim().slice(0, 90), sum: s && s.textContent.trim().slice(0, 30) }); })()`;
  console.log('dry before', await p.ev(dry));
  await press('#as-dryrun-box'); await sleep(800);
  console.log('dry off   ', await p.ev(dry));
  await p.send('Page.reload'); await sleep(3000);
  console.log('dry reload', await p.ev(dry));
  await press('#as-dryrun-box'); await sleep(800);
  console.log('dry on    ', await p.ev(dry));
  // lab context: what it asks, and the preview
  console.log('ctx questions', await p.ev(`[...document.querySelectorAll('#as-ctx select, #as-ctx input, #as-ctx textarea')].map(e => e.tagName + ':' + (e.id || e.name || e.type)).join(',')`));
  const pv = await press('#table-pane button', '/Preview/', 0);
  console.log('ctx preview?', pv);
  await p.shot(`${OUT}/setup_walk_end_${W}.png`);
  // back/forward journey
  await p.ev(`document.querySelector('#nav-agent') && document.querySelector('#nav-agent').click()`); await sleep(2000);
  await p.ev('history.back()'); await sleep(2500);
  console.log('back     ', await p.ev(`JSON.stringify({ path: location.pathname, live: AgentSetup._state.root === document.getElementById('as-body') })`));
  const e = p.errors(); if (e.length) console.log('ERRORS', e.join(' ## '));
  await p.close(); process.exit(0);
})();
