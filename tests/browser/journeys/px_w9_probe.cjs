/* Two w9 surfaces behind a proxy that no shipped journey walks on the KRS 5Q
 * rig (docs/226 §5.3): the landing's project env picker (w9/labwarm) and the
 * Pulses page open -> Back -> reload. Network-audited from the first byte:
 * a request outside the mount, a response >= 400 or a JS error is a FAIL.
 *
 *   SM_CDP_PORT=<cdp> SM_BASE_URL=http://127.0.0.1:<proxy>/sm node px_w9_probe.cjs --shots <dir>
 *   (root: PORT=<sm>, no SM_BASE_URL)
 */
'use strict';
const fs = require('fs');
const path = require('path');
const { open, sleep, base, prefix } = require('./cdp.cjs');

const argv = process.argv.slice(2);
const arg = (n, d) => { const i = argv.indexOf('--' + n); return i >= 0 ? argv[i + 1] : d; };
const BASE = base(process.env.PORT || '5099');
const SHOTS = arg('shots', '.');
fs.mkdirSync(SHOTS, { recursive: true });
let bad = 0;
function check(c, m, extra) { const l = (c ? 'ok   ' : 'FAIL ') + m + (extra ? ' ' + JSON.stringify(extra).slice(0, 200) : ''); console.log(l); if (!c) bad++; return c; }
async function waitFor(p, expr, ms = 30000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) { const v = await p.ev(expr); if (v && !(typeof v === 'string' && v.startsWith('EXC '))) return v; await sleep(150); }
  return null;
}
const INTACT = `JSON.stringify({ path: location.pathname + location.search, htmx: !!window.htmx, sidebar: !!document.querySelector('.sidebar-nav'), sm: window.SM ? window.SM.root : null, dataRoot: document.documentElement.getAttribute('data-root') })`;
function audit(p, mark, label) {
  const r = p.requests(mark); const e = p.errors(mark);
  check(r.leaks.length === 0, label + ': no request outside the mount', { leaks: r.leaks.slice(0, 5), total: r.total });
  check(r.bad.length === 0, label + ': no response >= 400', { bad: r.bad.slice(0, 5) });
  check(e.length === 0, label + ': no JS error', { errors: e.slice(0, 3) });
}

(async () => {
  const P = prefix();
  // ---- 1. the landing env picker ----------------------------------------
  {
    const p = await open(BASE + '/?landing=1', 1600, 950, { network: true });
    const cards = await waitFor(p, `document.querySelectorAll('.landing-card-env[data-project]').length || 0`, 40000);
    check(!!cards, 'landing: project cards with an env row', { cards });
    const st = JSON.parse(await p.ev(INTACT));
    check(st.sm === P && st.dataRoot === P, 'landing: data-root and window.SM.root carry the prefix', st);
    const mark = p.events.length;
    const opened = await p.ev(`(function(){ var b=document.querySelector('[data-env-change]'); if(!b) return 'none'; b.click(); return b.getAttribute('data-env-change'); })()`);
    check(opened && opened !== 'none', 'landing: Change... pressed on a project', { project: opened });
    const listed = await waitFor(p, `(function(){ var pk=document.getElementById('landing-env-picker'); if(!pk || pk.hidden) return 0; var l=pk.querySelector('[data-env-list]'); var ld=pk.querySelector('[data-env-loading]'); return (l && l.children.length) ? l.children.length : ((ld && (ld.hidden || getComputedStyle(ld).display==='none')) ? -1 : 0); })()`, 90000);
    check(listed !== null && listed !== 0, 'landing: the picker opened and its env list arrived (discovery through the proxy)', { entries: listed });
    await p.shot(path.join(SHOTS, '1_landing_env_picker.png'));
    audit(p, mark, 'landing picker');
    await p.ev(`(function(){ var c=document.querySelector('[data-env-close]'); if(c) c.click(); return 1; })()`);
    const mark2 = p.events.length;
    await p.ev('setTimeout(function () { location.reload(); }, 0); 1'); await sleep(1500);   // fire-and-forget (docs/226)
    const again = await waitFor(p, `document.readyState==='complete' && document.querySelectorAll('.landing-card-env[data-project]').length ? 1 : 0`, 40000);
    check(!!again, 'landing: reload comes back whole');
    audit(p, mark2, 'landing reload');
    await p.shot(path.join(SHOTS, '2_landing_reloaded.png'));
    await p.close();
  }
  // ---- 2. Pulses: open a row -> Back -> reload ---------------------------
  {
    // a prior SM page in history, so Back has somewhere under the mount to go
    const p = await open(BASE + '/qubits', 1600, 950, { network: true });
    await waitFor(p, `document.readyState==='complete' && !!document.querySelector('.sidebar-nav') ? 1 : 0`, 60000);
    await p.send('Page.navigate', { url: BASE + '/pulses' }); await sleep(1500);
    const rows = await waitFor(p, `document.querySelectorAll('tr[data-pulse-path]').length || 0`, 60000);
    check(!!rows, 'pulses: rows rendered', { rows });
    const mark = p.events.length;
    // a REAL mouse click on the row's operation cell (a synthetic .click() did not open the inspector in Chrome)
    const rowRect = JSON.parse(await p.ev(`(function(){ var r=document.querySelector('tr[data-pulse-path]'); if(!r) return 'null'; r.scrollIntoView({block:'center'}); var c=r.querySelector('td:nth-child(4)')||r; var b=c.getBoundingClientRect(); return JSON.stringify({x:Math.round(b.left+Math.min(40,b.width/2)), y:Math.round(b.top+b.height/2), path:r.getAttribute('data-pulse-path')}); })()`) || 'null');
    const first = rowRect ? rowRect.path : null;
    if (rowRect) await p.click(rowRect.x, rowRect.y);
    const detail = await waitFor(p, `(function(){ var d=document.querySelector('#inspector-pane #pulse-detail-root[data-pulse-path]'); return d ? d.getAttribute('data-pulse-path') : 0; })()`, 60000);
    check(!!detail, 'pulses: the row opens its inspector', { clicked: first, detail });
    const url1 = await p.ev('location.pathname + location.search');
    check(typeof url1 === 'string' && url1.indexOf((P || '') + '/pulses') === 0, 'pulses: the pushed address lives under the mount', { url: url1 });
    await p.shot(path.join(SHOTS, '3_pulses_open.png'));
    audit(p, mark, 'pulses open');
    const mark2 = p.events.length;
    await p.ev('history.back()'); await sleep(1500);
    const back = await waitFor(p, `document.readyState==='complete' && !!document.querySelector('.sidebar-nav') ? JSON.stringify({path: location.pathname, htmx: !!window.htmx}) : 0`, 30000);
    const bk = back ? JSON.parse(back) : null;
    check(!!bk && bk.path === (P || '') + '/qubits' && bk.htmx, 'pulses: Back lands on the previous SM page under the mount, whole', bk || { url: await p.ev('location.pathname') });
    await p.ev('history.forward()'); await sleep(1500);
    const fwdRows = await waitFor(p, `document.querySelectorAll('tr[data-pulse-path]').length || 0`, 30000);
    const url2 = await p.ev('location.pathname + location.search');
    check(!!fwdRows && typeof url2 === 'string' && url2.indexOf((P || '') + '/pulses') === 0, 'pulses: Forward brings the Pulses page back under the mount with its rows', { rows: fwdRows, url: url2 });
    audit(p, mark2, 'pulses back/forward');
    const mark3 = p.events.length;
    await p.ev('setTimeout(function () { location.reload(); }, 0); 1'); await sleep(1500);   // fire-and-forget (docs/226)
    const rel = await waitFor(p, `document.readyState==='complete' && document.querySelectorAll('tr[data-pulse-path]').length ? JSON.stringify({rows: document.querySelectorAll('tr[data-pulse-path]').length, htmx: !!window.htmx, sidebar: !!document.querySelector('.sidebar-nav')}) : 0`, 60000);
    check(!!rel, 'pulses: reload comes back whole', rel && JSON.parse(rel));
    audit(p, mark3, 'pulses reload');
    await p.shot(path.join(SHOTS, '4_pulses_reloaded.png'));
    await p.close();
  }
  console.log('SUMMARY ' + JSON.stringify({ base: BASE, prefix: P, fails: bad }));
  process.exit(bad ? 1 : 0);
})().catch(e => { console.log('FAIL probe crashed ' + (e && e.stack || e)); process.exit(2); });
