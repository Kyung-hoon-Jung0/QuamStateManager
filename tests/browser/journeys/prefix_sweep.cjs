/* Every sidebar page, behind the proxy (docs/226): open -> reload -> page intact,
 * and NOTHING requested outside the mount.
 *
 * The one journey that does not care WHY a URL leaked: with the Network domain
 * on from the first byte, every same-origin request whose path is outside the
 * prefix is counted (behind the rig's proxies such a request reaches the
 * platform's 404, never SM), and so is every response >= 400, every JS
 * exception and console error, and every sidebar href the server rendered
 * outside the prefix. A page is intact when its pane has content, htmx
 * loaded (the page's scripts ran) and the sidebar is there -- after the
 * first load AND after a reload.
 *
 *   SM_CDP_PORT=9639 SM_BASE_URL=http://127.0.0.1:5341/sm \
 *     node tests/browser/journeys/prefix_sweep.cjs --out DIR [--pages "/bulk,/pulses"] [--settle 5000]
 * Root (no SM_BASE_URL): --port 5099. Pages default to the sidebar of `/`.
 * Writes DIR/sweep.jsonl + one PNG per page (first load); exit 1 on any finding.
 */
'use strict';
const fs = require('fs');
const path = require('path');
const { open, sleep, base, smPath, prefix } = require('./cdp.cjs');

const argv = process.argv.slice(2);
const arg = (n, d) => { const i = argv.indexOf('--' + n); return i >= 0 ? argv[i + 1] : d; };
const BASE = base(arg('port', '5099'));
const OUT = arg('out', '.');
const SETTLE = +arg('settle', 5000);
fs.mkdirSync(OUT, { recursive: true });
const JSONL = path.join(OUT, 'sweep.jsonl');
fs.writeFileSync(JSONL, '');

const STATE = `JSON.stringify({ path: location.pathname + location.search,
  title: document.title,
  pane: (document.getElementById('table-pane') || document.querySelector('main') || document.body || { textContent: '' }).textContent.trim().length,
  htmx: typeof window.htmx === 'object' && !!window.htmx,
  sidebar: !!document.querySelector('.sidebar-nav'),
  dataRoot: document.documentElement.getAttribute('data-root'),
  smRoot: window.SM ? window.SM.root : null })`;
const SIDEBAR = `JSON.stringify(Array.from(document.querySelectorAll('.sidebar-nav a[href]')).map(function (a) { return a.getAttribute('href'); }))`;

// When `/` cannot be read (SM does not serve its mount at all -- measured on an
// unmodified SM behind a NON-stripping proxy: every page 404s), sweep the known
// sidebar routes anyway: zero pages must never read as zero failures.
const FALLBACK_PAGES = ['/', '/qualibrate', '/generate', '/config', '/regenerate', '/instrument', '/qubits', '/pairs',
  '/resonators', '/flux', '/diagnostics', '/topology', '/chip-status/report', '/diff', '/bulk', '/explorer',
  '/pulses', '/zline', '/agent', '/agent/setup', '/journal', '/state-history', '/param-history', '/datasets',
  '/collections', '/trends'];

function outside(href) {
  const P = prefix();
  return !!P && typeof href === 'string' && href.charAt(0) === '/' && href.charAt(1) !== '/'
    && !(href === P || href.indexOf(P + '/') === 0 || href.indexOf(P + '?') === 0);
}

(async () => {
  let pages = arg('pages', '');
  let sidebarLeaks = [];
  let landingBroken = false;
  if (pages) pages = pages.split(',');
  else {
    const p = await open(BASE + '/'); await sleep(2500);
    const hrefs = JSON.parse(await p.ev(SIDEBAR) || '[]');
    await p.close();
    sidebarLeaks = hrefs.filter(outside);
    const seen = new Set();
    pages = [];
    hrefs.forEach((h) => { const r = smPath(h); if (r && r.charAt(0) === '/' && !seen.has(r)) { seen.add(r); pages.push(r); } });
    if (!pages.length) { landingBroken = true; pages = FALLBACK_PAGES.slice(); }
  }
  console.log('base', BASE, 'prefix', JSON.stringify(prefix()), 'pages', pages.length,
    'sidebar hrefs outside the prefix', sidebarLeaks.length,
    landingBroken ? 'LANDING BROKEN: no sidebar at ' + BASE + '/ -- sweeping the fallback route list' : '');
  let bad = 0; const leakCount = {}; let leaksTotal = 0, badTotal = 0, errTotal = 0;
  for (const route of pages) {
    const rec = { route, url: BASE + route };
    let p;
    try {
      p = await open(BASE + route, 1600, 950, { network: true }); await sleep(SETTLE);
      rec.first = JSON.parse(await p.ev(STATE));
      const e0 = p.errors(0), r0 = p.requests(0);
      await p.shot(path.join(OUT, 'page' + route.replace(/[^\w.-]+/g, '_') + '.png'));
      const mark = p.events.length;
      await p.send('Page.reload'); await sleep(SETTLE);
      rec.reload = JSON.parse(await p.ev(STATE));
      const e1 = p.errors(mark), r1 = p.requests(mark);
      rec.errors = e0.concat(e1); rec.leaks = r0.leaks.concat(r1.leaks); rec.bad = r0.bad.concat(r1.bad);
      rec.requests = r0.total + r1.total;
      // a base.html page (it carries the sidebar) must also have run its scripts;
      // a standalone document (the printable report) needs content only
      const whole = (s) => !!s && s.pane > 0 && (!s.sidebar || s.htmx);
      rec.intact = whole(rec.first) && whole(rec.reload);
    } catch (e) { rec.crash = String(e).slice(0, 200); rec.errors = rec.errors || []; rec.leaks = rec.leaks || []; rec.bad = rec.bad || []; }
    try { if (p) await p.close(); } catch (e) { /* closed */ }
    rec.leaks.forEach((l) => { const k = l.replace(/\?.*$/, ''); leakCount[k] = (leakCount[k] || 0) + 1; });
    leaksTotal += rec.leaks.length; badTotal += rec.bad.length; errTotal += rec.errors.length;
    const fail = !rec.intact || rec.crash || rec.errors.length || rec.leaks.length || rec.bad.length;
    if (fail) bad++;
    fs.appendFileSync(JSONL, JSON.stringify(rec) + '\n');
    console.log((fail ? 'FAIL ' : 'ok   ') + route.padEnd(28), 'intact', !!rec.intact, 'req', rec.requests,
      'leaks', rec.leaks.length, 'bad', rec.bad.length, 'errors', rec.errors.length,
      rec.leaks.length ? 'e.g. ' + rec.leaks[0] : (rec.bad.length ? 'e.g. ' + rec.bad[0] : (rec.errors.length ? 'e.g. ' + rec.errors[0].slice(0, 90) : '')),
      rec.crash ? 'CRASH ' + rec.crash : '');
  }
  const top = Object.entries(leakCount).sort((a, b) => b[1] - a[1]).slice(0, 12);
  const summary = { base: BASE, prefix: prefix(), pages: pages.length, pages_failed: bad, leaks: leaksTotal, bad_responses: badTotal,
    errors: errTotal, sidebar_hrefs_outside: sidebarLeaks.length, landing_broken: landingBroken, top_leaks: top };
  fs.appendFileSync(JSONL, JSON.stringify({ summary }) + '\n');
  console.log('SUMMARY ' + JSON.stringify(summary));
  process.exit(bad || sidebarLeaks.length || landingBroken || !pages.length ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(2); });
