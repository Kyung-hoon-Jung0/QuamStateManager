/* docs/298 journey: Chip Status Trends and the metric meta after a NEW RUN and
 * after STAGED EDITS, on a rig whose history is a copy of a real archive's runs
 * (the newest ones kept back in a pool). Real mouse events throughout.
 *
 * 1. Trends: a recorded point's words and click target, before anything moves;
 * 2. a run folder is copied in (the next one of the pool, whose own patch sets
 *    a qubit's f_01): after SM ingests it, the chart carries its point with the
 *    value the run saved, "its own patch set it", and a click opens THAT run;
 * 3. a staged edit of a field no surface shows: every point and word the same;
 * 4. a staged edit of the very value: Trends still draws only recorded points,
 *    and the metric tile's meta says the value on screen is not in the ledger.
 *
 * Env: SM_URL, SM_CDP_PORT, SHOTS, RIG (the rig folder: data/, pool/,
 * pool_order.json), QUBIT (default q6), FIELD_UNRELATED (a dot path of that
 * qubit no surface draws).
 * Run: node tests/browser/journeys/hub_after_a_run.cjs
 */
'use strict';
const path = require('path');
const fs = require('fs');
const { open, sleep } = require('./cdp.cjs');

const URL0 = (process.env.SM_URL || 'http://127.0.0.1:5301').replace(/\/$/, '');
const SHOTS = process.env.SHOTS || path.join(process.cwd(), 'shots');
const RIG = process.env.RIG;
const Q = process.env.QUBIT || 'q6';
const UNRELATED = process.env.FIELD_UNRELATED || `qubits.${Q}.z.joint_offset`;
fs.mkdirSync(SHOTS, { recursive: true });

let fails = 0;
function ok(c, m) { if (!c) fails++; console.log((c ? 'ok - ' : 'FAIL: ') + m); }

async function waitFor(b, expr, ms = 30000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    const v = await b.ev(expr);
    if (v && !(typeof v === 'string' && v.startsWith('EXC'))) return v;
    await sleep(200);
  }
  return null;
}
async function hover(b, x, y) {
  await b.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: x - 6, y: y - 6 });
  await sleep(80);
  await b.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: x, y: y });
}
async function center(b, sel) {
  return b.ev(`(function(){var e=document.querySelector(${JSON.stringify(sel)});if(!e)return null;
    e.scrollIntoView({block:'center',inline:'center'});var r=e.getBoundingClientRect();
    return r.width&&r.height?[r.left+r.width/2,r.top+r.height/2]:null;})()`);
}
async function clickSel(b, sel) {
  const c = await center(b, sel);
  if (!c) return false;
  await sleep(600);
  const c2 = await center(b, sel);
  await b.click(c2[0], c2[1]);
  return true;
}
// the f_01 chart's trace of one qubit: x, y and the words/uid each point carries
function trace(b, metric, entity) {
  return b.ev(`(function(){
    var box=document.querySelector('#topo-trends .topo-trend-box[data-trend-metric=${JSON.stringify(metric)}]');
    var host=box&&box.querySelector('.js-plotly-plot'); if(!host) return null;
    var t=(host.data||[]).filter(function(t){return t.name===${JSON.stringify(entity)};})[0];
    if(!t) return null;
    return JSON.stringify({x:t.x,y:t.y,cd:t.customdata});})()`);
}
async function pointAt(b, metric, entity, want) {
  return b.ev(`(function(){
    var box=document.querySelector('#topo-trends .topo-trend-box[data-trend-metric=${JSON.stringify(metric)}]');
    var host=box&&box.querySelector('.js-plotly-plot'); if(!host) return null;
    var data=host.data||[];
    for (var t=0;t<data.length;t++){ var tr=data[t]; if (tr.name!==${JSON.stringify(entity)}) continue;
      var cd=tr.customdata||[];
      for (var i=cd.length-1;i>=0;i--){ var c=cd[i]; if(!c) continue;
        if(!(${want})(c, tr.y[i])) continue;
        // a trace hidden from the legend draws no group: count the drawn ones
        var drawn=0; for (var k=0;k<t;k++){ var v=data[k].visible; if(v!=='legendonly'&&v!==false) drawn++; }
        var groups=host.querySelectorAll('.scatterlayer .trace');
        var marks=groups[drawn]&&groups[drawn].querySelectorAll('.point');
        // a gap (a null value) draws no mark either: count the drawn points before it
        var mi=0; for (var q=0;q<i;q++){ if(tr.y[q]!==null&&tr.y[q]!==undefined) mi++; }
        var mark=marks&&marks[mi];
        if(!mark) return {miss:'no mark',i:i,uid:c[3],words:c[1],value:tr.y[i]};
        var r=mark.getBoundingClientRect();
        return {x:r.left+r.width/2,y:r.top+r.height/2,uid:c[3],words:c[1],value:tr.y[i],i:i};
      } }
    return null;})()`);
}
async function trendsWith(b, metric) {
  await go(b, URL0 + '/topology?view=trends');
  const ready = await waitFor(b, "document.querySelectorAll('#topo-trends .js-plotly-plot').length", 90000);
  if (!ready) return false;
  // the metric's pill, pressed like a person when its chart is not drawn yet
  // (the page settles first: its lazy sections move the pill while they land)
  const drawn = `!!document.querySelector('#topo-trends .topo-trend-box[data-trend-metric="${metric}"] .js-plotly-plot')`;
  for (let i = 0; i < 3 && !(await b.ev(drawn)); i++) {
    if (await b.ev(`(document.querySelector('#topo-trends button[data-trend-metric="${metric}"]')||{}).getAttribute&&document.querySelector('#topo-trends button[data-trend-metric="${metric}"]').getAttribute('aria-pressed')==='true'`)) {
      if (await waitFor(b, drawn, 30000)) break;
    }
    await sleep(800);
    await clickSel(b, `#topo-trends button[data-trend-metric="${metric}"]`);
    if (await waitFor(b, drawn, 30000)) break;
  }
  await b.ev(`(function(){var e=document.querySelector('#topo-trends .topo-trend-box[data-trend-metric="${metric}"]');if(e)e.scrollIntoView({block:'center'});})()`);
  await sleep(1500);
  return true;
}
async function isolate(b, metric, entity) {
  const leg = await b.ev(`(function(){var box=document.querySelector('#topo-trends .topo-trend-box[data-trend-metric="${metric}"]');
    var host=box&&box.querySelector('.js-plotly-plot'); if(!host) return null;
    var items=[].slice.call(host.querySelectorAll('.legend .traces'));
    for (var i=0;i<items.length;i++){ if ((items[i].textContent||'').trim()===${JSON.stringify(entity)}){
      items[i].scrollIntoView({block:'center'}); var r=items[i].getBoundingClientRect();
      return [r.left+12, r.top+r.height/2]; } }
    return null;})()`);
  if (!leg) return false;
  await b.click(leg[0], leg[1]); await sleep(60); await b.click(leg[0], leg[1]);
  await sleep(1200);
  return true;
}
// leaving a page with staged edits asks "leave this page?" (SM's own
// beforeunload): like a person, press Leave
async function go(b, url) {
  const mark = b.events.length;
  const nav = b.send('Page.navigate', { url });
  for (let i = 0; i < 40; i++) {
    if (b.events.slice(mark).some(e => e.method === 'Page.javascriptDialogOpening')) {
      await b.send('Page.handleJavaScriptDialog', { accept: true });
      break;
    }
    if (await Promise.race([nav.then(() => true), sleep(150).then(() => false)])) break;
  }
  await nav;
}
async function post(b, url, form) {
  return b.ev(`(async function(){var f=new URLSearchParams(${JSON.stringify(form)});
    var r=await fetch(${JSON.stringify(url)},{method:'POST',body:f,headers:{'HX-Request':'true'}});return r.status;})()`);
}

(async () => {
  const order = JSON.parse(fs.readFileSync(path.join(RIG, 'pool_order.json'), 'utf8'));
  const next = order[0];
  const node = JSON.parse(fs.readFileSync(path.join(RIG, 'pool', next, 'node.json'), 'utf8'));
  const rid = node.id;
  const p = (node.patches || []).find(x => x.path === `/quam/qubits/${Q}/f_01`);
  const b = await open(URL0 + '/topology?view=trends');
  try {
    // 1. before: the newest recorded f_01 point of the qubit
    ok(await trendsWith(b, 'f_01'), 'Trends draws its charts');
    ok(!!p, `the next run of the pool (#${rid}) sets ${Q} f_01 by its own patch (${p && p.value})`);
    const before = await trace(b, 'f_01', Q);
    ok(!!before, `the f_01 chart has a ${Q} line`);
    await b.shot(path.join(SHOTS, '01_trends_before.png'));

    // 2. a new run lands (its folder copied in, as a run would save it)
    fs.cpSync(path.join(RIG, 'pool', next), path.join(RIG, 'data', next), { recursive: true });
    const landed = await waitFor(b, `(async function(){var r=await fetch('/param-history/changes?prefix=qubits.${Q}.f_01',{headers:{'HX-Request':'true'}});
      var t=await r.text();return /run #${rid}\\b/.test(t)?'yes':'';})()`, 180000);
    ok(!!landed, `SM ingested run #${rid}`);
    ok(await trendsWith(b, 'f_01'), 'Trends redrawn after the run');
    await isolate(b, 'f_01', Q);
    const pt = await pointAt(b, 'f_01', Q, `function(c,y){return /#${rid}\\b/.test(c[1]||'');}`);
    ok(!!pt && pt.x, `a ${Q} point names run #${rid}: ${pt && pt.words}`);
    ok(!!pt && Math.abs(pt.value - p.value) < 1e-3, `its value is the one the run saved: ${pt && pt.value} vs ${p.value}`);
    ok(!!pt && /its own patch set it/.test(pt.words || ''), 'its words: its own patch set it');
    ok(!!pt && String(pt.uid || '').endsWith(':' + rid), `its click target is run #${rid}: ${pt && pt.uid}`);
    if (pt && pt.x) {
      let lab = '';
      for (let i = 0; i < 6 && !lab; i++) {
        const q = await pointAt(b, 'f_01', Q, `function(c,y){return /#${rid}\\b/.test(c[1]||'');}`);
        await hover(b, q.x, q.y);
        // each chart has its own hover layer: read the f_01 chart's
        lab = await waitFor(b, `(function(){var box=document.querySelector('#topo-trends .topo-trend-box[data-trend-metric="f_01"]');
          var t=((box&&box.querySelector('.hoverlayer'))||{}).textContent||'';return /#${rid}\\b/.test(t)?t:'';})()`, 3000);
      }
      ok(/its own patch set it/.test(lab || ''), 'its hover says so: ' + (lab || '').slice(0, 140));
      await b.shot(path.join(SHOTS, '02_trends_new_run_hover.png'));
      const q = await pointAt(b, 'f_01', Q, `function(c,y){return /#${rid}\\b/.test(c[1]||'');}`);
      await b.click(q.x, q.y);
      const opened = await waitFor(b, `(function(){var t=((document.getElementById('inspector-pane')||{}).textContent||'').replace(/\\s+/g,' ');
        return t.indexOf('#${rid}')>=0?t.slice(0,200):'';})()`, 30000);
      ok(!!opened, `a click opens run #${rid} in the inspector`);
      await b.shot(path.join(SHOTS, '03_trends_click_opens_new_run.png'));
    }
    const afterRun = await trace(b, 'f_01', Q);

    // 3. a staged edit of a field no surface draws: the same points, the same words
    ok(await post(b, '/field/edit', { dot_path: UNRELATED, value: '0.0123' }) === 200, `stage an edit of ${UNRELATED}`);
    ok(await trendsWith(b, 'f_01'), 'Trends after the unrelated edit');
    ok((await trace(b, 'f_01', Q)) === afterRun, 'every point, value and word of the line is unchanged');
    await b.shot(path.join(SHOTS, '04_trends_after_unrelated_edit.png'));

    // 4. a staged edit of the very value
    ok(await post(b, '/field/edit', { dot_path: `qubits.${Q}.f_01`, value: '4200000000' }) === 200, `stage an edit of ${Q} f_01`);
    ok(await trendsWith(b, 'f_01'), 'Trends after the edit of the drawn value');
    ok((await trace(b, 'f_01', Q)) === afterRun, 'Trends still draws only recorded points (the staged value is not one)');
    await go(b, URL0 + '/topology?view=frequencies');
    const cellSel = `.heatmap-cell[data-qubit="${Q}"]`;
    const panel = await waitFor(b, `(function(){var p=document.querySelector('[data-density-panel="f_01"]');return p&&p.querySelector(${JSON.stringify(cellSel)})?'y':'';})()`, 60000);
    ok(!!panel, 'the f_01 panel is drawn');
    const sel = `[data-density-panel="f_01"] ${cellSel}`;
    let card = '';
    for (let i = 0; i < 6 && !card; i++) {
      const c0 = await center(b, sel);
      if (!c0) { await sleep(800); continue; }
      await sleep(400);
      const c1 = await center(b, sel);
      await hover(b, c1[0], c1[1]);
      // journey race: the meta is fetched on the first hover; wait past the card's placeholder line
      card = await waitFor(b, `(function(){var p=document.getElementById('cs-meta-pop');var t=p&&p.textContent||'';return t.indexOf('${Q}')===0&&!/Loading when this was measured/.test(t)?t:'';})()`, 15000);
      if (!card) { await b.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: 5, y: 5 }); await sleep(600); }
    }
    ok(/Not in this chip.s change ledger yet/.test(card || ''), 'the meta says the value on screen is not in the ledger: ' + (card || '').slice(0, 200));
    ok(new RegExp('#' + rid + '\\b').test(card || ''), `...and names the ledger's newest change, run #${rid}`);
    await b.shot(path.join(SHOTS, '05_meta_after_edit.png'));
    // leaving a page with staged edits asks "unsaved changes?" (SM's own
    // beforeunload); headless Chrome blocks that prompt and logs it
    const errs = b.errors().filter(m => !/beforeunload/.test(m));
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
