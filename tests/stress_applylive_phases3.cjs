/* Third batch: the covenant floor (does anything write with NO press?) and
 * the non-Apply doors that reach the live files. */

const TARGET = 'qubits.q1.f_01';

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

module.exports = {

  // The covenant floor: a person walks around the app and presses nothing
  // that says it writes. Nothing must reach state.json / wiring.json.
  P0_soak_no_press: async (c) => {
    const pages = ['/', '/bulk', '/explorer', '/diagnostics', '/chip-status',
      '/state-history', '/param-history', '/pulses', '/instrument',
      '/qubits', '/pairs', '/datasets', '/journal', '/help'];
    for (const p of pages) {
      await c.nav(c.BASE + p);
      await c.sleep(900);
      const e = c.snap('navigated to ' + p + ' (no press)');
      c.ok('P0 ' + p + ' writes nothing', !e.changed, e.diff.slice(0, 5));
    }
    // sit on the grid through several poll cycles
    await c.nav(c.BASE + '/bulk');
    await c.until('!!document.querySelector(\'.bulk-cell\')', 15000);
    for (let i = 0; i < 4; i++) {
      await c.sleep(6000);
      const e = c.snap('idled on /bulk for ' + ((i + 1) * 6) + 's (no press)');
      c.ok('P0 idling ' + ((i + 1) * 6) + 's writes nothing', !e.changed, e.diff.slice(0, 5));
    }
    // a reload, and a hard reload
    await c.nav(c.BASE + '/bulk');
    await c.sleep(1500);
    let e = c.snap('reloaded /bulk');
    c.ok('P0 a reload writes nothing', !e.changed, e.diff.slice(0, 5));
    // the review modal, opened and closed without pressing anything
    await c.ev(`window.openReview && window.openReview()`);
    await c.sleep(2000);
    await c.ev(`window.closeReview && window.closeReview()`);
    await c.sleep(800);
    e = c.snap('opened + closed the review modal');
    c.ok('P0 opening the review modal writes nothing', !e.changed, e.diff.slice(0, 5));
  },

  // Ctrl+Z after an apply is a live write by design (docs/160). Check that
  // it is a PRESS, that it says so, and that it moves exactly one gesture.
  P13_ctrlz_after_apply: async (c) => {
    await openGrid(c);
    const before = c.readChip();
    await typeCell(c, TARGET, '4312408888.5');
    let e = c.snap('P13 staged');
    c.ok('P13 staging writes nothing', !e.changed, e.diff);
    await c.ev(`window.doStateSync('apply')`);
    await c.sleep(3000);
    e = c.snap('P13 applied');
    c.ok('P13 apply wrote', e.changed, e.diff.slice(0, 5));
    const appliedPaths = e.diff.map(d => d.path);
    c.note('P13 apply wrote', e.diff);

    // one Ctrl+Z
    await c.ev(`(function(){var el=document.body;el.focus();return 1;})()`);
    await c.send('Input.dispatchKeyEvent', { type: 'rawKeyDown', key: 'z', code: 'KeyZ', windowsVirtualKeyCode: 90, nativeVirtualKeyCode: 90, modifiers: 2 });
    await c.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'z', code: 'KeyZ', windowsVirtualKeyCode: 90, nativeVirtualKeyCode: 90, modifiers: 2 });
    await c.sleep(4000);
    e = c.snap('P13 pressed Ctrl+Z after the apply');
    c.note('P13 Ctrl+Z wrote', e.diff);
    const undone = e.diff.map(d => d.path).sort();
    c.ok('P13 Ctrl+Z reverted the whole gesture, not half of it',
      !e.changed || JSON.stringify(undone) === JSON.stringify(appliedPaths.slice().sort()),
      { applied: appliedPaths, undone });
    if (e.changed) {
      const back = e.diff.every(d => String(d.to) === String(c.jdiff(before.state, before.state).length === 0 ? d.to : d.to));
      c.note('P13 values after undo', e.diff.map(d => d.path + '=' + d.to));
    }
    const toast = await c.ev(`(function(){var t=document.querySelector('.undo-trail, #undo-trail, .toast');return t?(t.innerText||'').replace(/\\s+/g,' ').slice(0,300):null;})()`);
    c.note('P13 what the screen said', toast);
  },

  // The armed Auto-Sync session (docs/117): arming is a press, but check that
  // it cannot arm itself, and that while DISARMED an edit never reaches live.
  P14_auto_sync_default_off: async (c) => {
    await openGrid(c);
    const armed = await c.ev(`(function(){var p=document.querySelector('.auto-apply-pill,[data-auto-apply],#auto-apply-pill');return p?JSON.stringify({cls:p.className,txt:(p.innerText||'').trim().slice(0,80),aria:p.getAttribute('aria-pressed')}):null;})()`);
    c.note('P14 auto-sync pill', armed);
    await typeCell(c, TARGET, '4312407777.5');
    await c.sleep(2500);
    const e = c.snap('P14 typed an edit with Auto-Sync in its default state');
    c.ok('P14 an edit does NOT reach live with Auto-Sync off', !e.changed, e.diff.slice(0, 5));
    // clean up: discard
    await post(c, '/state/discard', '');
    await c.sleep(800);
    const e2 = c.snap('P14 discarded');
    c.ok('P14 discard writes nothing live', !e2.changed, e2.diff.slice(0, 5));
  },
};
