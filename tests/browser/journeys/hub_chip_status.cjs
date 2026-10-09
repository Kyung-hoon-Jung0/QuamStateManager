/* docs/283 journey: Chip Status Trends, a metric tile's meta hover, the Param
 * History grid (and a cell's drawer) and Param History Changes, all read from
 * the chip's change ledger, walked like a person with real mouse events: open,
 * hover, click, Back, reload -- the page stays whole and the console clean.
 *
 * The rig needs: a run whose own patch set qA1 T1 (a PROVEN point), a run that
 * moved qA2 T1 with no patch (writer not proven) and a run of another chip
 * identity. Env: SM_URL, SM_CDP_PORT, SHOTS.
 * Run: node tests/browser/journeys/hub_chip_status.cjs
 */
'use strict';
const path = require('path');
const fs = require('fs');
const { open, sleep } = require('./cdp.cjs');

const URL0 = (process.env.SM_URL || 'http://127.0.0.1:5163').replace(/\/$/, '');
const SHOTS = process.env.SHOTS || path.join(process.cwd(), 'shots');
fs.mkdirSync(SHOTS, { recursive: true });

let fails = 0;
function ok(c, m) { if (!c) fails++; console.log((c ? 'ok - ' : 'FAIL: ') + m); }

async function waitFor(b, expr, ms = 30000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    const v = await b.ev(expr);
    if (v && !(typeof v === 'string' && v.startsWith('EXC'))) return v;
    await sleep(150);
  }
  return null;
}
async function center(b, sel) {
  return b.ev(`(function(){var e=document.querySelector(${JSON.stringify(sel)});if(!e)return null;
    e.scrollIntoView({block:'center',inline:'center'});var r=e.getBoundingClientRect();
    return r.width&&r.height?[r.left+r.width/2,r.top+r.height/2]:null;})()`);
}
async function clickSel(b, sel) {
  const c = await center(b, sel);
  if (!c) return false;
  await sleep(150);
  const c2 = await center(b, sel);
  await b.click(c2[0], c2[1]);
  return true;
}
async function hover(b, x, y) {
  await b.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: x - 6, y: y - 6 });
  await sleep(80);
  await b.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: x, y: y });
}
// the page grows while its lazy sections land (and the pane scrolls with
// them): hover an element only once it holds still, and again if the card
// that opened is not about it
async function hoverUntil(b, locate, expect, tries = 6) {
  let got = '';
  for (let i = 0; i < tries; i++) {
    let p0 = await locate();
    await sleep(400);
    let p1 = await locate();
    if (!p0 || !p1) { await sleep(500); continue; }
    if (Math.abs(p0[0] - p1[0]) > 1 || Math.abs(p0[1] - p1[1]) > 1) { await sleep(800); continue; }
    await hover(b, p1[0], p1[1]);
    got = await waitFor(b, expect, 4000);
    if (got) return got;
    await b.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: 5, y: 5 });
    await sleep(500);
  }
  return got;
}
// a Trends point (its trace + index) whose customdata matches `want`
async function trendPoint(b, entity, want) {
  return b.ev(`(function(){
    var hosts=[].slice.call(document.querySelectorAll('#topo-trends .js-plotly-plot'));
    for (var h=0;h<hosts.length;h++){ var host=hosts[h]; var data=host.data||[];
      for (var t=0;t<data.length;t++){ var tr=data[t]; if (tr.name!==${JSON.stringify(entity)}) continue;
        var cd=tr.customdata||[];
        for (var i=0;i<cd.length;i++){ var c=cd[i]; if(!c) continue;
          var hit=(${want})(c); if(!hit) continue;
          var groups=host.querySelectorAll('.scatterlayer .trace');
          var marks=groups[t]&&groups[t].querySelectorAll('.point');
          var mark=marks&&marks[i]; if(!mark) continue;
          var r=mark.getBoundingClientRect();
          return {x:r.left+r.width/2,y:r.top+r.height/2,uid:c[3],words:c[1]};
        } } }
    return null;})()`);
}

(async () => {
  const b = await open(URL0 + '/topology?view=trends');
  const rawShot = b.shot;
  b.shot = f => {
    let done = false;
    return Promise.race([rawShot(f).then(() => { done = true; }),
                         sleep(20000).then(() => { if (!done) console.log('(screenshot timed out: ' + f + ')'); })]);
  };
  try {
    // 1. Chip Status > Trends
    const charts = await waitFor(b, "document.querySelectorAll('#topo-trends .js-plotly-plot').length", 60000);
    ok(charts > 0, 'Trends draws its charts from the change ledger');
    ok(!(await b.ev("document.getElementById('topo-trends').textContent.includes('Older snapshot history')")),
       'no older-snapshot label: this chip has a ledger of its runs');
    ok(await b.ev("/recorded events?/.test(document.querySelector('.topo-trends-count')?.textContent||'')"),
       'the count names recorded events, not snapshots');
    await b.shot(path.join(SHOTS, '01_trends.png'));

    // an unproven point: its words say so, and a click opens nothing
    // the first chart in view once (a smooth scroll moves every point while it runs)
    await b.ev("document.querySelector('#topo-trends .js-plotly-plot').scrollIntoView({block:'center'})");
    await sleep(1500);
    const unp = await trendPoint(b, 'qA2', "function(c){return /writer not proven/.test(c[1]||'');}");
    ok(!!unp && !unp.uid, 'an unproven qA2 T1 point carries "writer not proven" and no link');
    if (unp) {
      const hv = await hoverUntil(b, async () => {
        const q = await trendPoint(b, 'qA2', "function(c){return /writer not proven/.test(c[1]||'');}");
        return q ? [q.x, q.y] : null;
      }, "(function(){var t=(document.querySelector('.hoverlayer')||{}).textContent||'';return /qA2/.test(t)?t:'';})()");
      ok(/writer not proven/.test(hv || ''), 'its hover SAYS writer not proven: ' + (hv || '').slice(0, 120));
      await b.shot(path.join(SHOTS, '02_trends_unproven_hover.png'));
      const before = await b.ev("(document.getElementById('inspector-pane')||{}).textContent||''");
      await b.click(unp.x, unp.y);
      await sleep(800);
      ok((await b.ev("(document.getElementById('inspector-pane')||{}).textContent||''")) === before,
         'a click on it opens nothing');
    }
    // a proven point: its hover names the run, a click opens it
    const pr = await trendPoint(b, 'qA1', "function(c){return !!c[3];}");
    ok(!!pr && /its own patch set it/.test(pr.words || ''), 'a proven qA1 T1 point names its run: ' + (pr && pr.words));
    if (pr) {
      // points of 21 qubits sit on top of each other: like a person, isolate
      // qA1's line with a double click on its legend entry first
      const leg = await b.ev(`(function(){var host=document.querySelector('#topo-trends .js-plotly-plot');
        var items=[].slice.call(host.querySelectorAll('.legend .traces'));
        for (var i=0;i<items.length;i++){ if ((items[i].textContent||'').trim()==='qA1'){
          var r=items[i].getBoundingClientRect(); items[i].scrollIntoView({block:'center'});
          r=items[i].getBoundingClientRect(); return [r.left+12, r.top+r.height/2]; } }
        return null;})()`);
      if (leg) {
        await b.click(leg[0], leg[1]);
        await sleep(60);
        await b.click(leg[0], leg[1]);
        await sleep(900);
      }
      ok(!!leg && await b.ev(`(function(){var host=document.querySelector('#topo-trends .js-plotly-plot');
        return host.data.filter(function(t){return t.visible!=='legendonly';}).map(function(t){return t.name;}).join(',')==='qA1';})()`),
         'a double click on the legend leaves qA1 alone on the chart');
      // Plotly opens the HOVERED point: hover until the label is this one's
      const lab = await hoverUntil(b, async () => {
        const q = await trendPoint(b, 'qA1', "function(c){return !!c[3];}");
        return q ? [q.x, q.y] : null;
      }, "(function(){var t=(document.querySelector('.hoverlayer')||{}).textContent||'';return /qA1/.test(t)&&/its own patch set it/.test(t)?t:'';})()");
      ok(!!lab, 'its hover names the run its own patch proves: ' + (lab || '').slice(0, 120));
      const at = await trendPoint(b, 'qA1', "function(c){return !!c[3];}");
      await b.click(at.x, at.y);
      const rid = pr.uid.split(':').pop();
      const opened = await waitFor(b, `(function(){var t=(document.getElementById('inspector-pane')||{}).textContent||'';
        return /run_id\\s*#${rid}\\b/.test(t.replace(/\\s+/g,' '))||t.indexOf('#${rid} ')>=0;})()`, 20000);
      ok(!!opened, 'the click opens run #' + rid + ' in the inspector');
      await b.shot(path.join(SHOTS, '03_trends_click_opens_run.png'));
    }

    // 2. a metric tile's meta hover (the T1 panel)
    await b.send('Page.navigate', { url: URL0 + '/topology?view=coherence' });
    const tile = await waitFor(b, "!!document.querySelector('[data-density-panel=\"T1\"] .heatmap-cell[data-qubit=\"qA2\"]')", 60000);
    ok(!!tile, 'the T1 panel is drawn');
    const cell = q => '[data-density-panel="T1"] .heatmap-cell[data-qubit="' + q + '"]';
    const card = q => "(function(){var p=document.getElementById('cs-meta-pop');var t=p&&p.textContent||'';"
      + "return t.indexOf('" + q + " \u2014')===0&&/Recorded:|Written by:|not in history/.test(t)?t:'';})()";
    const pop = await hoverUntil(b, () => center(b, cell('qA2')), card('qA2'));
    ok(/saved in #6/.test(pop || '') && /writer not proven/.test(pop || '') && !/Written by/.test(pop || ''),
       'qA2 T1: "saved in #6 ... writer not proven", never "Written by": ' + (pop || '').slice(0, 160));
    await b.shot(path.join(SHOTS, '04_meta_unproven.png'));
    const pop1 = await hoverUntil(b, () => center(b, cell('qA1')), card('qA1'));
    ok(/Written by: #7 07_T1 — its own patch set it/.test(pop1 || ''), 'qA1 T1 names its proven writer: ' + (pop1 || '').slice(0, 160));
    const tag = await b.ev("(document.querySelector('[data-density-panel=\"T1\"] .heatmap-cell[data-qubit=\"qA1\"] .heatmap-cell-meta')||{}).textContent||''");
    const tag2 = await b.ev("(document.querySelector('[data-density-panel=\"T1\"] .heatmap-cell[data-qubit=\"qA2\"] .heatmap-cell-meta')||{}).textContent||''");
    ok(/#7/.test(tag) && !/#/.test(tag2), 'tile tags: qA1 "' + tag + '" names #7, qA2 "' + tag2 + '" names no run');
    await b.shot(path.join(SHOTS, '05_meta_proven.png'));

    // 3. Param History grid (sidebar), and a cell's drawer
    // Param History sits under the Calibration log's collapsible group
    if (!(await center(b, '#journal-subnav a[href="/param-history"]'))) {
      await clickSel(b, 'button[aria-controls="journal-subnav"]');
      await sleep(300);
    }
    ok(await clickSel(b, '#journal-subnav a[href="/param-history"]'), 'sidebar -> Param History');
    ok(!!(await waitFor(b, "!!document.querySelector('.param-history-grid .history-cell')", 30000)), 'the grid is drawn');
    ok(!(await b.ev("document.getElementById('param-history-root').textContent.includes('Older snapshot history')")),
       'the grid reads the ledger (no older-snapshot label)');
    ok(await b.ev("/recorded changes? shown/.test(document.querySelector('.param-history-summary').textContent)"),
       'the summary counts recorded changes');
    await b.shot(path.join(SHOTS, '06_grid.png'));
    ok(await clickSel(b, '.history-cell[data-qubit="qA2"][data-prop="T1"]'), 'open the qA2 T1 cell');
    const drawer = await waitFor(b, "(function(){var e=document.getElementById('phd-data');if(!e)return null;var d=JSON.parse(e.textContent);return d.ledger?JSON.stringify(d.values.map(function(v){return [v.sub,!!v.uid];})):null;})()", 20000);
    ok(!!drawer && /writer not proven/.test(drawer) && !/true/.test(drawer),
       'the cell drawer carries the ledger words and opens no run: ' + drawer);
    await waitFor(b, "!!document.querySelector('#phd-chart .js-plotly-plot, #phd-chart svg')", 15000);
    await b.shot(path.join(SHOTS, '07_grid_drawer.png'));

    // 4. Param History Changes
    ok(await clickSel(b, '.ph-tabs button:not(.active)'), 'Changes tab');
    ok(!!(await waitFor(b, "!!document.querySelector('.ph-change-group')", 30000)), 'Changes lists events');
    const ch = await b.ev("document.querySelector('.ph-changes').textContent.replace(/\\s+/g,' ')");
    ok(/run #7 07_T1/.test(ch) && /its own patch set it/.test(ch) && /writer not proven/.test(ch),
       'run #7: qA1 T1 "its own patch set it", qA3 T1 "writer not proven"');
    // S10 C3 review P1-4: another chip's run is left out of this chip's timeline
    ok(!/#8 /.test(ch) && !/chip uncertain/.test(ch), 'the foreign-chip run is not one of this chip\'s changes');
    await b.shot(path.join(SHOTS, '08_changes.png'));

    // 5. Back, reload
    const hist = await b.send('Page.getNavigationHistory');
    const h = hist.result;
    if (h.currentIndex > 0) {
      await b.send('Page.navigateToHistoryEntry', { entryId: h.entries[h.currentIndex - 1].id });
    }
    ok(!!(await waitFor(b, "!!document.querySelector('.param-history-grid .history-cell, .ph-change-group')", 30000)),
       'Back lands on a whole page');
    await b.shot(path.join(SHOTS, '09_after_back.png'));
    await b.send('Page.reload', { ignoreCache: true });
    ok(!!(await waitFor(b, "!!document.querySelector('.param-history-grid .history-cell, .ph-change-group')", 30000)),
       'reload lands on a whole page');
    await b.shot(path.join(SHOTS, '10_after_reload.png'));
    const errs = b.errors();
    ok(errs.length === 0, 'console errors: ' + errs.length + (errs.length ? ' ' + JSON.stringify(errs.slice(0, 3)) : ''));
  } catch (e) {
    fails++;
    console.log('FAIL: ' + (e && e.stack || e));
  } finally {
    await b.close();
  }
  console.log(fails ? fails + ' FAILED' : 'ALL OK');
  process.exit(fails ? 1 : 0);
})();
