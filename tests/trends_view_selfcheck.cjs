/* Datasets > Trends view (P3, design ram_design.md §2b) -- what the user sees.
 *
 * Drives the SHIPPED shell (templates/_trends_data.html) and app.js's
 * window.DatasetTrends under jsdom (tests/trends_view_harness.cjs). Pinned:
 *   A. x is the run's real instant (a date axis); an undated run is left out
 *      of the axis and COUNTED in the note; with no dated run at all the chart
 *      falls back to run order and says so. Hover names the run.
 *   B. 'lines' above 200 points, 'lines+markers' at or below; in 'lines' a
 *      value with gaps on both sides gets a marker (it would draw nothing).
 *   C. the statistics layer is computed from the FULL series sent.
 *   D. a metric with no finite value in any run is SAID, not drawn empty;
 *      a bool flag is still charted.
 *   E. the count and the notes: runs, unreadable runs, still indexing.
 *   F. nothing but chart slots before any <details> opens: no <img>; the
 *      Figure timeline is built on open -- per figure, the newest 50 runs
 *      that HAVE it, newest first, then "Show older".
 *   G. Parameter Differences is asked for after the first chart, with the
 *      params URL; its version differing from the charts' is said (never
 *      mixed silently), and Reload re-picks.
 *   H. a 202 warming answer is asked again; an error is shown; empty cases.
 *   I. a few extreme values (failed fits) do not flatten the rest: the y axis
 *      is the rest's, each off-scale value is a triangle on the edge with its
 *      real value in the hover, clickable, and the label counts them; a click
 *      opens the point drawn nearest the pointer.
 * Run: node tests/trends_view_selfcheck.cjs (driven by tests/test_trends_view.py).
 */
'use strict';
try { require('jsdom'); } catch (e) { console.error('jsdom not installed'); process.exit(2); }
const H = require('./trends_view_harness.cjs');

let fails = 0, asserts = 0;
function ok(c, m) { asserts++; if (c) console.log('ok - ' + m); else { console.error('FAIL: ' + m); fails++; } }
const text = (el) => (el ? el.textContent.replace(/\s+/g, ' ').trim() : '');
function dataTraceOf(d) { return d.data.find((t) => / \/ /.test(t.name) && !/isolated/.test(t.name)); }

async function view(body, opts) {
  const W = H.world();
  (Array.isArray(body) ? body : [body]).forEach((b) => W.answers.push(b.status || b.error ? b : { body: b }));
  const root = W.mount();
  await H.until(() => (opts && opts.until) ? opts.until(W) : W.draws.length >= Math.min(12, (body.series || []).length));
  return { W, root, doc: W.w.document };
}

(async () => {
  // A. the time axis
  {
    const p = H.payload(8, [{ q: 'q1', m: 'T1', v: [1, 2, 3, 4, 5, 6, 7, 8] }]);
    p.runs[3][1] = null; p.undated = 1;
    const { W, root } = await view(p);
    const d = W.draws[0];
    const tr = dataTraceOf(d);
    ok(d.layout.xaxis.type === 'date', 'A: the x axis is a date axis (' + d.layout.xaxis.type + ')');
    ok(JSON.stringify(tr.x) === JSON.stringify(p.runs.filter((r) => r[1] !== null).map((r) => r[1])),
       'A: x = each dated run\'s own instant, in order');
    ok(tr.y.length === 7 && tr.y.indexOf(4) < 0, 'A: the undated run is not placed (its value 4 is not drawn)');
    ok(/1 run whose date\/time does not parse is not placed on the time axis/.test(text(root.querySelector('[data-role="notes"]'))),
       'A: and the note counts it (' + text(root.querySelector('[data-role="notes"]')) + ')');
    ok(tr.text[0] === '#1000' && /%\{text\}/.test(tr.hovertemplate), 'A: hover names the run (' + tr.text[0] + ', ' + tr.hovertemplate + ')');
    ok(d.layout.xaxis.ticklabeloverflow === 'allow', 'A: ticklabeloverflow allow');
  }
  {
    const p = H.payload(4, [{ q: 'q1', m: 'T1', v: [1, 2, 3, 4] }]);
    p.runs.forEach((r) => { r[1] = null; }); p.undated = 4;
    const { W, root } = await view(p);
    const d = W.draws[0];
    ok(d.layout.xaxis.type === 'category' && dataTraceOf(d).x.join(',') === '#1000,#1001,#1002,#1003',
       'A: no dated run at all -> run order on a category axis');
    ok(/No run carries a date\/time that parses/.test(text(root.querySelector('[data-role="notes"]'))), 'A: and it says so');
  }

  // B. lines above 200 points; isolated points keep a marker
  {
    const n = 250;
    const v = []; for (let i = 0; i < n; i++) v.push(i === 100 ? 7 : (i === 99 || i === 101 ? null : i % 5));
    const { W } = await view(H.payload(n, [{ q: 'q1', m: 'big', v: v }]));
    const d = W.draws[0];
    const tr = dataTraceOf(d);
    ok(tr.mode === 'lines', 'B: ' + n + ' points -> lines (' + tr.mode + ')');
    const iso = d.data.find((t) => /isolated/.test(t.name));
    ok(iso && iso.mode === 'markers' && iso.y.length === 1 && iso.y[0] === 7 && iso.text[0] === '#1100',
       'B: the value with gaps on both sides keeps a marker (' + JSON.stringify(iso && iso.y) + ')');
    ok(iso && iso.customdata[0] === 'kk:1100', 'B: and is clickable (its uid)');
  }
  {
    const v = []; for (let i = 0; i < 200; i++) v.push(i);
    const { W } = await view(H.payload(200, [{ q: 'q1', m: 'm', v: v }]));
    ok(dataTraceOf(W.draws[0]).mode === 'lines+markers', 'B: 200 points -> lines+markers');
    ok(!W.draws[0].data.some((t) => /isolated/.test(t.name)), 'B: no isolated-points trace in markers mode');
  }

  // C. statistics from the full series
  {
    const v = []; for (let i = 0; i < 40; i++) v.push(i % 3 === 0 ? null : i);
    const { W } = await view(H.payload(40, [{ q: 'q1', m: 'm', v: v }]));
    const stats = W.draws[0].data.filter((t) => !/ \/ /.test(t.name) || /moving avg/.test(t.name));
    const finite = v.filter((x) => x !== null).length;
    ok(stats.length === 3 && stats.every((t) => t.x.length === finite),
       'C: three statistics traces over all ' + finite + ' finite values (' + stats.map((t) => t.x.length) + ')');
  }

  // D. nothing finite -> said; a bool flag -> charted
  {
    const p = H.payload(5, [{ q: 'q1', m: 'failed', v: [null, null, null, null, null] },
                            { q: 'q1', m: 'flag', v: [true, false, true, true, null] }]);
    const { W, doc } = await view(p, { until: (w) => w.draws.length >= 1 });
    await H.tick(60);
    const box0 = doc.getElementById('trend-chart-0');
    ok(W.draws.every((d) => d.el !== box0) && /No finite value in any of the 5 runs/.test(box0.textContent),
       'D: an all-null metric is said, not drawn (' + box0.textContent + ')');
    const flag = W.draws.find((d) => d.el.id === 'trend-chart-1');
    ok(flag && dataTraceOf(flag).y.indexOf(false) >= 0, 'D: a bool flag is still charted');
  }

  // E. count + notes
  {
    const p = H.payload(1234, [{ q: 'q1', m: 'm', v: new Array(1234).fill(1) }], { incomplete: 2, indexing: true });
    const { root } = await view(p);
    ok(text(root.querySelector('[data-role="count"]')) === '(1,234 runs)', 'E: the run count (' + text(root.querySelector('[data-role="count"]')) + ')');
    const notes = text(root.querySelector('[data-role="notes"]'));
    ok(/2 runs whose files could not be read .* are not shown/.test(notes), 'E: unreadable runs are counted (' + notes + ')');
    ok(/still being indexed/.test(notes), 'E: a truncated scan is said');
  }

  // F. figure timeline
  {
    const n = 130;
    const p = H.payload(n, [{ q: 'q1', m: 'm', v: new Array(n).fill(1) }]);
    p.fig_keys = ['figure', 'rare'];
    p.fig_runs = [Array.from({ length: n }, (_, i) => i), [5, 90]];
    const { root, doc } = await view(p);
    ok(root.querySelectorAll('img').length === 0, 'F: no <img> before any details opens');
    const det = root.querySelector('[data-role="figtl"]');
    ok(det && !det.hidden && !det.open, 'F: the Figure timeline is shown, collapsed');
    ok(/\(2 figures\)/.test(text(root.querySelector('[data-role="figtl-count"]'))), 'F: it names how many figures');
    det.open = true; det.dispatchEvent(new doc.defaultView.Event('toggle'));
    const secs = root.querySelectorAll('.trend-figure-strip-section');
    ok(secs.length === 2 && root.querySelectorAll('img').length === 0, 'F: opening it builds one closed section per figure, still no <img>');
    const fig = secs[0];
    fig.open = true; fig.dispatchEvent(new doc.defaultView.Event('toggle'));
    const labels = () => Array.from(fig.querySelectorAll('.figure-strip-label')).map((x) => x.textContent);
    ok(labels().length === 50 && labels()[0] === '#1129' && labels()[49] === '#1080', 'F: the newest 50 first (' + labels()[0] + '..' + labels()[49] + ')');
    const img = fig.querySelector('img');
    ok(img.getAttribute('src') === '/dataset/kk:1129/fig/figure' && /toggleFigureZoom\(this\)/.test(img.getAttribute('onclick') || '')
       && img.getAttribute('loading') === 'lazy', 'F: each is the run\'s figure, lazy, opening the viewer');
    const more = fig.querySelector('.trends-figtl-more');
    ok(more && !more.hidden && /^Show 50 older \(80 not shown yet\)$/.test(more.textContent), 'F: "Show older" says how many remain (' + (more && more.textContent) + ')');
    more.click();
    ok(labels().length === 100 && labels()[50] === '#1079', 'F: Show older adds the next 50, oldest last');
    more.click();
    ok(labels().length === 130 && more.hidden, 'F: until all are shown');
    const rare = secs[1];
    rare.open = true; rare.dispatchEvent(new doc.defaultView.Event('toggle'));
    ok(Array.from(rare.querySelectorAll('.figure-strip-label')).map((x) => x.textContent).join(',') === '#1090,#1005',
       'F: a figure only some runs have lists only those runs');
  }
  {
    const { root } = await view(H.payload(3, [{ q: 'q1', m: 'm', v: [1, 2, 3] }]));
    ok(root.querySelector('[data-role="figtl"]').hidden, 'F: no figures -> no Figure timeline');
  }

  // G. parameter differences
  {
    const { W, root } = await view(H.payload(3, H.seriesOf('P', 3, 3)));
    const pd = W.ajax.filter((a) => /param-diff/.test(a.url));
    ok(pd.length === 1 && pd[0].url === '/trends/param-diff?experiment=E' && pd[0].opts.target === root.querySelector('[data-role="params"]'),
       'G: Parameter Differences is asked for once, into its box (' + JSON.stringify(pd.map((a) => a.url)) + ')');
    const box = root.querySelector('[data-role="params"]');
    const stale = root.querySelector('[data-role="stale"]');
    box.innerHTML = '<div data-trends-v="v1">same</div>';
    box.dispatchEvent(new W.w.CustomEvent('htmx:afterSwap', { bubbles: true }));
    ok(stale.hidden, 'G: the same version -> no notice');
    box.innerHTML = '<div data-trends-v="v2">newer</div>';
    box.dispatchEvent(new W.w.CustomEvent('htmx:afterSwap', { bubbles: true }));
    ok(!stale.hidden && /New runs arrived/.test(stale.textContent), 'G: a different version is said');
    let reloaded = 0;
    W.w.loadTrendData = function () { reloaded++; };
    root.querySelector('[data-role="reload"]').click();
    ok(reloaded === 1, 'G: Reload re-picks');
  }
  {
    const { W } = await view(H.payload(1, [{ q: 'q1', m: 'm', v: [1] }]));
    await H.tick(30);
    ok(W.ajax.filter((a) => /param-diff/.test(a.url)).length === 0, 'G: one run -> nothing to compare, not asked');
  }

  // H. warming, errors, empty
  {
    const W = H.world();
    W.answers.push({ status: 202, body: { warming: true } });
    W.answers.push({ body: H.payload(3, H.seriesOf('W', 1, 3)) });
    W.mount();
    await H.until(() => W.draws.length >= 1, 3000);
    ok(W.fetches.length === 2 && W.draws.length === 1, 'H: a warming answer is asked again, then drawn (' + W.fetches.length + ' asks)');
  }
  {
    const W = H.world();
    W.answers.push({ status: 500, body: { error: 'boom' } });
    const root = W.mount();
    await H.tick(60);
    ok(/Could not load trends: boom/.test(text(root)), 'H: an error is shown (' + text(root.querySelector('[data-role="charts"]')) + ')');
  }
  {
    const { root } = await view(H.payload(0, []), { until: () => true });
    await H.tick(30);
    ok(/No runs found for this experiment/.test(text(root)), 'H: no runs');
    const b = await view(H.payload(3, []), { until: () => true });
    await H.tick(30);
    ok(/No numeric fit-result metrics found/.test(text(b.root)), 'H: no metrics');
  }

  // I. a few extreme values must not flatten the rest (the y range)
  {
    // 49 values in 3k..30k and two failed fits at 68M / 23M (the KH_202608_CZ
    // 25_T1 q1 shape): the axis is the rest's, the two sit on the top edge.
    const n = 51, v = [];
    for (let i = 0; i < n; i++) v.push(3000 + ((i * 7919) % 27000));
    v[10] = 68534273; v[12] = 23427454;
    const { W, root } = await view(H.payload(n, [{ q: 'q1', m: 't1', v: v }]));
    await H.tick(30);
    const d = W.draws[0], ya = d.layout.yaxis;
    const normal = v.filter((x, i) => i !== 10 && i !== 12);
    ok(Array.isArray(ya.range) && ya.autorange === false, 'I: outliers -> an explicit y range (' + JSON.stringify(ya.range) + ')');
    ok(ya.range && ya.range[0] <= Math.min(...normal) && ya.range[1] >= Math.max(...normal) && ya.range[1] < 1e6,
       'I: the range holds every normal value and not the outliers');
    const off = d.data.filter((t) => /off-scale above/.test(t.name));
    ok(off.length === 1 && off[0].y.length === 2 && off[0].y.every((y) => y > Math.max(...normal) && y < ya.range[1]),
       'I: each off-scale value is a marker inside the top edge (' + JSON.stringify(off[0] && off[0].y) + ')');
    ok(off[0] && off[0].marker.symbol === 'triangle-up' && off[0].customdata.join(',') === 'kk:1010,kk:1012',
       'I: a triangle-up, clickable (its run uid)');
    ok(off[0] && /#1010 · 68534300 \(off-scale above\)/.test(off[0].text[0]), 'I: its hover gives the real value (' + (off[0] && off[0].text[0]) + ')');
    ok(dataTraceOf(d).y[10] === 68534273, 'I: the data trace still carries the value (nothing dropped)');
    ok(/2 values off-scale above \(▲ on the top edge\) — double-click the chart for the full range/.test(text(root.querySelector('.trend-chart-label'))),
       'I: the label counts them and says how to see all (' + text(root.querySelector('.trend-chart-label')) + ')');
    // a click near the top edge opens the OFF-SCALE run, not the normal one at the same x
    const pts = [
      { customdata: 'kk:1009', y: 5000, yaxis: { l2p: (y) => 100 - y / 200, _offset: 10 } },
      { customdata: 'kk:1010', y: 30000, yaxis: { l2p: (y) => 100 - y / 200, _offset: 10 } },
    ];
    const el = d.el;
    el.__handlers.plotly_click[0]({ points: pts, event: { clientY: 10 + 100 - 30000 / 200 } });
    const clicked = W.ajax.filter((a) => /^\/dataset\//.test(a.url)).map((a) => a.url);
    ok(clicked[clicked.length - 1] === '/dataset/kk:1010', 'I: a click opens the point drawn nearest the pointer (' + clicked.join(',') + ')');
    el.__handlers.plotly_click[0]({ points: pts, event: { clientY: 10 + 100 - 5000 / 200 } });
    const c2 = W.ajax.filter((a) => /^\/dataset\//.test(a.url)).map((a) => a.url);
    ok(c2[c2.length - 1] === '/dataset/kk:1009', 'I: ...and the normal point when the pointer is on it');
    el.__handlers.plotly_click[0]({ points: pts.slice().reverse() });
    const c3 = W.ajax.filter((a) => /^\/dataset\//.test(a.url)).map((a) => a.url);
    ok(c3.length === c2.length + 1 && c3[c3.length - 1] === '/dataset/kk:1010', 'I: no pointer height -> the first uid, as before');
  }
  {
    const n = 40, v = [];
    for (let i = 0; i < n; i++) v.push(10 + (i % 7));
    v[5] = -1e7;
    const { W, root } = await view(H.payload(n, [{ q: 'q1', m: 'm', v: v }]));
    await H.tick(30);
    const d = W.draws[0];
    const off = d.data.filter((t) => /off-scale below/.test(t.name));
    ok(off.length === 1 && off[0].marker.symbol === 'triangle-down' && off[0].y[0] < 10 && off[0].y[0] > d.layout.yaxis.range[0],
       'I: an outlier below -> a triangle-down inside the bottom edge');
    ok(/1 value off-scale below/.test(text(root.querySelector('.trend-chart-label'))), 'I: and the label says below');
  }
  const R = (v) => { const W = H.world(); return W.w.DatasetTrends.robustRange(v); };
  const base = []; for (let i = 0; i < 50; i++) base.push(100 + (i % 10));
  ok(R(base) === null, 'I: no outlier -> autorange (null)');
  // gain: the whole span must exceed 20x the rest's span -- 1 point at 20x does not, at 21x+ does
  ok(R(base.concat([100 + 9 * 19])) === null, 'I: an outlier stretching the axis <= 20x the rest -> autorange');
  ok(R(base.concat([100 + 9 * 25])) !== null, 'I: ...> 20x -> rescaled');
  // fences: Q3 + 3*IQR, both sides of it. 0..999 plus 2254, 2255, 1e6: 1003
  // values, Q1 250.5, Q3 751.5, IQR 501 -> the fence is 2254.5 exactly.
  {
    const b = []; for (let i = 0; i < 1000; i++) b.push(i);
    const r = R(b.concat([2254, 2255, 1e6]));
    ok(r && r.above.join(',') === '1001,1002', 'I: only the values beyond Q3+3*IQR are off-scale (' + JSON.stringify(r && r.above) + ')');
  }
  // share: at most 10% (and at least one) -- a second regime keeps its autorange
  {
    const b = []; for (let i = 0; i < 45; i++) b.push(10 + (i % 5));
    ok(R(b.concat([1e6, 1e6, 1e6, 1e6])) !== null, 'I: 4 of 49 (<=10%) off -> rescaled');
    ok(R(b.concat([1e6, 1e6, 1e6, 1e6, 1e6, 1e6])) === null, 'I: 6 of 51 (>10%) off -> a regime, autorange');
  }
  // too few points to judge, and flags
  ok(R([1, 2, 3, 4, 5, 6, 1e9]) === null, 'I: under 8 values -> autorange');
  ok(R([1, 2, 3, 4, 5, 6, 7, 1e9]) !== null, 'I: 8 values -> judged');
  ok(R(base.map((x, i) => (i === 3 ? true : x)).concat([1e9])) === null, 'I: a flag series -> autorange');

  if (fails) { console.error(fails + ' of ' + asserts + ' check(s) failed'); process.exit(1); }
  console.log('ALL OK (' + asserts + ' assertions)');
  process.exit(0);
})().catch((e) => { console.error('ERROR: ' + (e && e.stack || e)); process.exit(1); });
