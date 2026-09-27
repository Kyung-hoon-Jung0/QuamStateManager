/* Agent menu: enumerate every visible control on each surface at a width,
 * screenshot it, report console errors + horizontal overflow (docs QA round).
 *   SM_CDP_PORT=9413 node agent_enum.cjs 5113 OUTDIR [width]
 */
'use strict';
const { open, sleep } = require('./cdp.cjs');
const SM = process.argv[2] || '5113';
const OUT = process.argv[3] || '.';
const W = +(process.argv[4] || 1600);
const SURFACES = ['/agent', '/agent/setup', '/journal'];

const ENUM = `(() => {
  const vis = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const pane = document.querySelector('#table-pane') || document.body;
  const els = [...pane.querySelectorAll('button, a[href], input, select, textarea, summary, [role=button]')].filter(vis);
  const out = els.map(el => { const r = el.getBoundingClientRect();
    return [el.tagName.toLowerCase(), (el.id || ''), (el.textContent || el.value || el.placeholder || el.getAttribute('aria-label') || '').trim().replace(/\\s+/g,' ').slice(0,50),
      Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height), el.disabled ? 'DIS' : ''].join('|'); });
  const over = [...pane.querySelectorAll('*')].filter(e => { const r = e.getBoundingClientRect(); return vis(e) && r.right > innerWidth + 1; })
    .slice(0, 8).map(e => e.tagName + '.' + (e.className && e.className.baseVal === undefined ? e.className : '') + ' r=' + Math.round(e.getBoundingClientRect().right));
  return JSON.stringify({ title: document.title, sw: document.documentElement.scrollWidth, iw: innerWidth, n: out.length, controls: out, over });
})()`;

(async () => {
  for (const s of SURFACES) {
    const p = await open(`http://127.0.0.1:${SM}${s}`, W, 900);
    await sleep(2500);
    const r = JSON.parse(await p.ev(ENUM));
    const tag = s.replace(/\//g, '_') + '_' + W;
    await p.shot(`${OUT}/enum${tag}.png`);
    console.log(`== ${s} @${W}: ${r.n} controls, scrollWidth ${r.sw}/${r.iw}`);
    r.controls.forEach(c => console.log('  ' + c));
    if (r.over.length) console.log('  OVERFLOW', r.over.join(' ; '));
    const e = p.errors(); if (e.length) console.log('  ERRORS', e.join(' ## '));
    await p.close();
  }
  process.exit(0);
})();
