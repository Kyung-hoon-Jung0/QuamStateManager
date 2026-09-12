/* Fourth batch: the staleness conflict, the force door, and the in-flight race. */

const fs = require('fs');
const path = require('path');
const TARGET = 'qubits.q1.f_01';
const TARGET2 = 'qubits.q2.f_01';

function esc(s) { return s.replace(/"/g, '\\"'); }
async function openGrid(c) {
  await c.nav(c.BASE + '/bulk');
  await c.until('!!document.querySelector(\'.bulk-cell\')', 15000);
}
async function typeCell(c, dp, value) {
  const sel = '.bulk-cell[data-dot-path="' + esc(dp) + '"]';
  const okf = await c.ev(`(function(){var e=document.querySelector("${esc(sel)}");if(!e)return 0;e.scrollIntoView({block:'center'});e.focus();return 1;})()`);
  if (!okf) throw new Error('cell not found: ' + dp);
  await c.ev(`(function(){var e=document.querySelector("${esc(sel)}");e.value='';e.dispatchEvent(new Event('input',{bubbles:true}));return 1;})()`);
  for (const ch of value) await c.typeChar(ch);
  await c.sleep(150);
  await c.ev(`(function(){var e=document.querySelector("${esc(sel)}");e.blur();return 1;})()`);
  await c.sleep(900);
}
async function post(c, url, body, headers) {
  const h = Object.assign({ 'Content-Type': 'application/x-www-form-urlencoded', 'HX-Request': 'true' }, headers || {});
  return await c.ev(`(async function(){
    var r = await fetch(${JSON.stringify(url)}, {method:'POST', headers:${JSON.stringify(h)}, body:${JSON.stringify(body || '')}});
    var t = await r.text();
    return JSON.stringify({status:r.status, body:t.slice(0,2500)});
  })()`).then(s => JSON.parse(s));
}
async function trayCount(c) {
  return await c.ev(`(function(){var t=document.getElementById("pending-tray");return t?parseInt(t.getAttribute("data-change-count")||"0",10):-1;})()`);
}

module.exports = {

  // Something OUTSIDE SM rewrites the live file, then the user presses Apply.
  P15_stale_live_conflict: async (c) => {
    await openGrid(c);
    await typeCell(c, TARGET, '4312405050.5');
    let e = c.snap('P15 staged an edit');
    c.ok('P15 staging writes nothing', !e.changed, e.diff);

    // an outside writer moves a DIFFERENT leaf on the live chip
    const p = path.join(c.CHIP, 'state.json');
    const j = JSON.parse(fs.readFileSync(p, 'utf8'));
    j.qubits.q3.f_01 = 4330055777.5;
    fs.writeFileSync(p, JSON.stringify(j, null, 4));
    e = c.snap('an OUTSIDE writer changed qubits.q3.f_01 on the live chip');
    c.ok('P15 the outside write is visible in the diff', e.changed && e.diff.some(d => d.path === 'qubits.q3.f_01'), e.diff.slice(0, 4));

    const ap = await post(c, '/state/apply-to-live', '', { Accept: 'application/json' });
    await c.sleep(2500);
    e = c.snap('P15 pressed Apply to live over a drifted live chip');
    c.note('P15 apply response', { status: ap.status, head: ap.body.slice(0, 300) });
    c.ok('P15 the drifted apply is refused, not silently forced', ap.status === 409, { status: ap.status });
    c.ok('P15 nothing was written by the refused apply', !e.changed, e.diff.slice(0, 6));

    // now the explicit force door: ↑ Keep mine — overwrite live
    const pf = await c.ev(`(async function(){var r=await fetch('/state/overwrite-live/preflight');return JSON.stringify({s:r.status,b:(await r.text()).slice(0,600)});})()`).then(s => JSON.parse(s));
    c.note('P15 overwrite preflight', pf);
    e = c.snap('P15 read the overwrite preflight (a GET, no press)');
    c.ok('P15 the preflight writes nothing', !e.changed, e.diff.slice(0, 4));

    const fr = await post(c, '/state/apply-to-live?force=1', '', { Accept: 'application/json' });
    await c.sleep(2500);
    e = c.snap('P15 pressed force=1 (Keep mine - overwrite live)');
    c.note('P15 forced apply', { status: fr.status, wrote: e.diff.slice(0, 8) });
    c.ok('P15 the forced apply wrote the user edit', e.diff.some(d => d.path === TARGET), e.diff.slice(0, 8));
    c.ok('P15 the forced apply DISCARDED the outside write (as a push must)',
      e.diff.some(d => d.path === 'qubits.q3.f_01'), e.diff.slice(0, 8));
  },

  // Stage B while an apply of A is in flight, and press again.
  P16_stage_during_inflight: async (c) => {
    await openGrid(c);
    await typeCell(c, TARGET, '4312406060.5');
    let e = c.snap('P16 staged A');
    c.ok('P16 staging writes nothing', !e.changed, e.diff);
    const seen = await trayCount(c);
    const r = await c.ev(`(async function(){
      var out = {};
      var p1 = fetch('/state/apply-to-live',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded','HX-Request':'true','Accept':'application/json'},body:'seen_changes=${seen}'}).then(function(x){out.a=x.status;});
      // while that is in flight, a second context stages B, then presses again
      var p2 = (async function(){
        var s = await fetch('/field/edit',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded','HX-Request':'true'},body:'dot_path=${TARGET2}&value=4400006060.5'});
        out.stageB = s.status;
        var x = await fetch('/state/apply-to-live',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded','HX-Request':'true','Accept':'application/json'},body:'seen_changes=${seen}'});
        out.b = x.status;
      })();
      await Promise.all([p1,p2]);
      return JSON.stringify(out);
    })()`).then(s => JSON.parse(s));
    await c.sleep(3500);
    e = c.snap('P16 apply of A raced with a stage-of-B + a second press');
    c.note('P16 statuses', r);
    c.note('P16 wrote', e.diff);
    const wroteB = e.diff.some(d => d.path === TARGET2);
    c.ok('P16 the B edit (never on this screen) did not ride the race onto live',
      !wroteB || r.b !== 409, { wroteB, statuses: r, diff: e.diff.slice(0, 6) });
    c.note('P16 final tray', await trayCount(c));
  },
};
