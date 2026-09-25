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

  if (fails) { console.error(fails + ' of ' + asserts + ' check(s) failed'); process.exit(1); }
  console.log('ALL OK (' + asserts + ' assertions)');
  process.exit(0);
})().catch((e) => { console.error('ERROR: ' + (e && e.stack || e)); process.exit(1); });
