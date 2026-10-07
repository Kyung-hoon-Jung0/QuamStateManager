/* Fit-overlay journey (docs/300): the Interactive tab draws the node's own fit,
 * or says why it does not.
 *
 * For each run id: open /dataset/by-run/<id>, press the Interactive tab with a
 * REAL mouse click, wait for every tile to render, then read back from the live
 * Plotly figures (gd.data / gd.layout) what is drawn: the fit traces, the
 * notes ("fit-note" annotations) and the data traces. Each tile is scrolled
 * into view and screenshotted for a human to compare with the run's own saved
 * figure. Back to Full View and a reload must leave the page whole.
 *
 * usage: SM_CDP_PORT=9602 node tests/browser/journeys/fit_overlays.cjs \
 *          --base http://127.0.0.1:5302 --runs 2916,542 --shots <dir>
 * Writes <shots>/report.json. Exit 1 on a JS error or a run that renders nothing.
 */
'use strict';
const fs = require('fs');
const path = require('path');
const { open, sleep } = require('./cdp.cjs');

const argv = process.argv.slice(2);
const arg = (n, d) => { const i = argv.indexOf('--' + n); return i >= 0 ? argv[i + 1] : d; };
const BASE = arg('base', 'http://127.0.0.1:5302');
const RUNS = arg('runs', '').split(',').filter(Boolean);
const SHOTS = arg('shots', '');
if (SHOTS) fs.mkdirSync(SHOTS, { recursive: true });

const READ = `(function(){
  var out=[]; document.querySelectorAll('#ds-interactive-container .ds-interactive-plot').forEach(function(div){
    var gd=div.querySelector('.js-plotly-plot'); var key=div.getAttribute('data-fig')||div.getAttribute('data-key')||'';
    if(!gd){ out.push({key:key, rendered:false, text:div.textContent.slice(0,120)}); return; }
    var fits=[], data=[];
    (gd.data||[]).forEach(function(t){ var n=String(t.name||''); var rec={name:n, n:(t.x||[]).length, dash:(t.line||{}).dash||null};
      if(/^fit|^circle fit/.test(n)) fits.push(rec); else if(n) data.push(rec); });
    var notes=((gd.layout||{}).annotations||[]).filter(function(a){return a.name==='fit-note';}).map(function(a){return a.text;});
    out.push({key:key, rendered:true, fits:fits, data:data, notes:notes});
  }); return JSON.stringify(out); })()`;

(async () => {
  const report = {};
  let failed = 0;
  for (const rid of RUNS) {
    const p = await open(`${BASE}/dataset/by-run/${rid}`);
    const mark = p.events.length;
    await sleep(800);
    const xy = await p.ev(`(function(){var a=document.querySelector('.dataset-tabs a[data-ds-tab="interactive"]');
      if(!a) return null; a.scrollIntoView({block:'center'}); var r=a.getBoundingClientRect(); return [r.left+r.width/2, r.top+r.height/2];})()`);
    if (!xy) { report[rid] = { error: 'no Interactive tab' }; failed++; await p.close(); continue; }
    await p.click(xy[0], xy[1]);
    // wait for every tile: rendered (Plotly root present) or a static/greyed tile
    let tiles = [];
    for (let i = 0; i < 120; i++) {
      await sleep(400);
      const st = await p.ev(`(function(){var c=document.getElementById('ds-interactive-container'); if(!c) return 'none';
        var t=c.querySelectorAll('.ds-interactive-plot'); var done=0;
        t.forEach(function(d){ if(d.querySelector('.js-plotly-plot')||d.getAttribute('data-rendered')==='0') done++; });
        return done+'/'+t.length;})()`);
      if (st && st !== 'none') {
        const [a, b] = st.split('/').map(Number);
        if (b > 0 && a === b) break;
        // tiles render lazily on scroll: bring the next unrendered one into view
        await p.ev(`(function(){var d=[].find.call(document.querySelectorAll('#ds-interactive-container .ds-interactive-plot'),
          function(x){return !x.querySelector('.js-plotly-plot');}); if(d) d.scrollIntoView({block:'center'});})()`);
      }
    }
    tiles = JSON.parse(await p.ev(READ) || '[]');
    // one screenshot per tile, scrolled into view
    const n = await p.ev(`document.querySelectorAll('#ds-interactive-container .ds-interactive-plot').length`);
    for (let k = 0; k < n && SHOTS; k++) {
      await p.ev(`(function(){var d=document.querySelectorAll('#ds-interactive-container .ds-interactive-plot')[${k}];
        var f=d.closest('.ds-interactive-fig')||d; f.scrollIntoView({block:'start'}); window.scrollBy(0,-60);})()`);
      await sleep(500);
      await p.shot(path.join(SHOTS, `run${rid}_tile${k}.png`));
    }
    // back to Full View, then a reload: the page must come back whole
    const fv = await p.ev(`(function(){var a=document.querySelector('.dataset-tabs a[data-ds-tab="full"]')||document.querySelector('.dataset-tabs a[data-ds-tab]');
      if(!a) return null; a.scrollIntoView({block:'center'}); var r=a.getBoundingClientRect(); return [r.left+r.width/2, r.top+r.height/2];})()`);
    if (fv) { await p.click(fv[0], fv[1]); await sleep(800); }
    await p.send('Page.reload'); await sleep(2500);
    const whole = await p.ev(`!!document.querySelector('#ds-detail-root') && !!document.querySelector('.dataset-tabs')`);
    const errs = p.errors(mark);
    report[rid] = { tiles, whole, errors: errs };
    const rendered = tiles.filter(t => t.rendered).length;
    if (!rendered || !whole || errs.length) failed++;
    console.log(`#${rid}: ${rendered}/${tiles.length} rendered, whole=${whole}, errors=${errs.length}`);
    tiles.forEach(t => console.log(`   ${t.key}: fits=[${(t.fits || []).map(f => f.name + '(' + f.n + ')').join(', ')}] notes=${JSON.stringify(t.notes || [])}`));
    await p.close();
  }
  if (SHOTS) fs.writeFileSync(path.join(SHOTS, 'report.json'), JSON.stringify(report, null, 1));
  process.exit(failed ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
