/* Second batch of apply-to-live lane phases. Same ctx contract. */

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
async function trayCount(c) {
  return await c.ev(`(function(){var t=document.getElementById("pending-tray");return t?parseInt(t.getAttribute("data-change-count")||"0",10):-1;})()`);
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

  // The OTHER live-write button: the tray's hx-post, which declares nothing.
  // Reachable honestly: working_dirty (saved, not applied) with change_count 0
  // on screen, while a second window stages an edit into the shared log.
  P6c_tray_button_no_seen: async (c) => {
    await openGrid(c);
    await typeCell(c, TARGET, '4312404242.5');
    let e = c.snap('P6c typed + staged');
    c.ok('P6c staging writes nothing', !e.changed, e.diff);

    const sv = await post(c, '/save', '');
    await c.sleep(1400);
    e = c.snap('P6c pressed Save to working state');
    c.ok('P6c save writes nothing live', !e.changed, e.diff);
    c.note('P6c save status', sv.status);

    await c.ev(`window.htmx && window.htmx.ajax("GET","/state/tray",{target:"#pending-tray",swap:"outerHTML"})`);
    await c.sleep(1400);
    const shown = await trayCount(c);
    const wd = await c.ev(`document.getElementById("pending-tray").getAttribute("data-working-dirty")`);
    const btn = await c.ev(`(function(){var b=document.querySelector('#pending-tray button.btn-apply-live');return b?b.outerHTML.slice(0,700):null;})()`);
    c.note('P6c tray state', { shown, working_dirty: wd });
    c.note('P6c button markup', btn);
    c.ok('P6c the tray shows the hx-post apply button', !!btn && /apply-to-live/.test(btn || ''), btn);
    c.ok('P6c that button declares NO seen_changes', !!btn && !/seen_changes/.test(btn || ''), btn);

    const st = await post(c, '/field/edit', 'dot_path=' + encodeURIComponent(TARGET2) + '&value=4400009999.5');
    c.note('P6c second-context stage status', st.status);
    await c.sleep(500);
    e = c.snap('P6c a SECOND context staged an edit (screen still shows ' + shown + ')');
    c.ok('P6c second-context staging writes nothing', !e.changed, e.diff);
    const stillShows = await trayCount(c);
    c.ok('P6c the screen is stale (still shows ' + shown + ')', stillShows === shown, { shows: stillShows });

    c.dlg.clear(); c.dlg.accept = true;
    await c.clickSel('#pending-tray button.btn-apply-live');
    await c.sleep(3500);
    e = c.snap('P6c pressed the tray "Apply to live chip" button (declares nothing)');
    c.note('P6c confirm text', c.dlg.last);
    c.note('P6c what the press wrote', e.diff);
    const wroteUnseen = e.diff.some(d => d.path === TARGET2);
    c.ok('P6c the unseen second-window edit was NOT written', !wroteUnseen,
      { wrote: e.diff, dialog: c.dlg.last });
  },

  // Press Apply while a previous apply is still in flight.
  P9_concurrent_apply: async (c) => {
    await openGrid(c);
    await typeCell(c, TARGET, '4312403131.5');
    let e = c.snap('P9 staged');
    c.ok('P9 staging writes nothing', !e.changed, e.diff);
    const r = await c.ev(`(async function(){
      var mk=function(){return fetch('/state/apply-to-live',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded','HX-Request':'true','Accept':'application/json'},body:''}).then(function(x){return x.status;});};
      var s=await Promise.all([mk(),mk(),mk()]);
      return JSON.stringify(s);
    })()`);
    await c.sleep(3500);
    e = c.snap('P9 three concurrent POSTs to /state/apply-to-live');
    c.note('P9 statuses', r);
    c.note('P9 wrote', e.diff);
    const stray = e.diff.filter(d => d.path !== TARGET && d.path !== 'qubits.q1.xy.RF_frequency');
    c.ok('P9 concurrent applies wrote nothing beyond the staged gesture', stray.length === 0, stray.slice(0, 6));
  },

  // The chip-identity gate: swap the open chip between staging and applying.
  P10_chip_swap: async (c) => {
    await openGrid(c);
    await typeCell(c, TARGET, '4312402020.5');
    let e = c.snap('P10 staged on chip A');
    c.ok('P10 staging writes nothing', !e.changed, e.diff);
    const ld = await post(c, '/load', 'folder=' + encodeURIComponent(c.OTHER_CHIP));
    await c.sleep(3000);
    c.note('P10 load-other status', ld.status);
    e = c.snap('P10 loaded a DIFFERENT chip while an edit was staged on A');
    c.ok('P10 switching chips writes nothing to chip A', !e.changed, e.diff);
    const ap = await post(c, '/state/apply-to-live', '', { Accept: 'application/json' });
    await c.sleep(3000);
    e = c.snap('P10 pressed Apply to live AFTER the chip was swapped');
    c.note('P10 apply status', { status: ap.status, head: ap.body.slice(0, 300) });
    c.ok('P10 chip A was not written by an apply pressed while chip B is open', !e.changed, e.diff);
    await post(c, '/load', 'folder=' + encodeURIComponent(c.CHIP));
    await c.sleep(3000);
    e = c.snap('P10 loaded chip A back');
    c.note('P10 reload-A wrote', e.diff);
    c.ok('P10 re-opening chip A writes nothing by itself', !e.changed, e.diff);
  },

  // A pointer field and a list cell.
  P11_pointer_and_list: async (c) => {
    await openGrid(c);
    const chip = c.readChip();
    let ptrPath = null, listPath = null;
    (function walk(o, p) {
      if (ptrPath && listPath) return;
      if (o === null || typeof o !== 'object') return;
      if (Array.isArray(o)) { if (!listPath && o.length && typeof o[0] === 'number') listPath = p; return; }
      for (const k of Object.keys(o)) {
        const v = o[k], np = p ? p + '.' + k : k;
        if (typeof v === 'string' && v.slice(0, 2) === '#/') { if (!ptrPath) ptrPath = np; }
        else walk(v, np);
        if (ptrPath && listPath) return;
      }
    })(chip.state, '');
    c.note('P11 targets', { ptrPath, listPath });
    const cases = [['pointer', ptrPath, '123.5'], ['list', listPath, '[1, 2, 3]']];
    for (const [kind, path, val] of cases) {
      if (!path) { c.note('P11 no ' + kind + ' leaf found', null); continue; }
      const r = await post(c, '/field/edit', 'dot_path=' + encodeURIComponent(path) + '&value=' + encodeURIComponent(val));
      await c.sleep(400);
      let e = c.snap('P11 staged ' + kind + ' edit at ' + path);
      c.ok('P11 ' + kind + ' staging writes nothing', !e.changed, e.diff);
      c.note('P11 ' + kind + ' stage response', { status: r.status, head: r.body.slice(0, 300) });
      const ap = await post(c, '/state/apply-to-live', '', { Accept: 'application/json' });
      await c.sleep(1800);
      e = c.snap('P11 applied ' + kind + ' edit');
      c.note('P11 ' + kind + ' apply wrote', e.diff.slice(0, 8));
      const parent = path.split('.').slice(0, -1).join('.');
      const off = e.diff.filter(d => d.path.indexOf(parent) !== 0);
      c.ok('P11 ' + kind + ': nothing outside the edited parent moved', off.length === 0, off.slice(0, 6));
    }
  },

  // The Review tray / review modal door, and Apply pressed from it.
  P12_review_modal: async (c) => {
    await openGrid(c);
    await typeCell(c, TARGET, '4312401010.5');
    let e = c.snap('P12 staged');
    c.ok('P12 staging writes nothing', !e.changed, e.diff);
    await c.ev(`window.openReview && window.openReview()`);
    await c.sleep(2000);
    const rv = await c.ev(`(function(){var b=document.querySelector('#state-review button[hx-post="/state/apply-to-live"], .sm-modal button[hx-post="/state/apply-to-live"]');return b?b.outerHTML.slice(0,600):null;})()`);
    c.note('P12 review apply button', rv);
    c.ok('P12 the review modal apply button DOES declare seen_changes', !!rv && /seen_changes/.test(rv || ''), rv);
    if (rv) {
      c.dlg.clear(); c.dlg.accept = true;
      await c.clickSel('#state-review button[hx-post="/state/apply-to-live"], .sm-modal button[hx-post="/state/apply-to-live"]');
      await c.sleep(3000);
      e = c.snap('P12 pressed Apply from the review modal');
      c.note('P12 wrote', e.diff);
      c.ok('P12 review apply wrote the staged leaf', e.diff.some(d => d.path === TARGET), e.diff.slice(0, 6));
    }
    await c.ev(`window.closeReview && window.closeReview()`);
    await c.sleep(600);
  },
};
