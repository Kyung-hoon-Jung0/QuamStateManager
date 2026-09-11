/* docs/184 in a REAL browser — the customer's own numbers, the real renderer,
 * the real stylesheet.
 *
 * The jsdom harness proves the arithmetic and the exclusion. It cannot tell you
 * what the tile LOOKS like, which is the thing the customer photographed. This
 * mounts Chip Status through its own public API with the screenshot's exact
 * inputs (IRB 95.70% and 59.55%, divisor 5.37), reads the rendered tiles back,
 * and captures the panel.
 *
 * argv[2]=out.json argv[3]=cdp argv[4]=base
 */
const fs = require('fs');
const OUT = process.argv[2], CDP = process.argv[3], BASE = process.argv[4];
const results = [], errors = [];
function ok(n, c, d) { results.push({ name: n, pass: !!c, detail: d === undefined ? null : d }); }

async function main() {
  const t = await (await fetch('http://127.0.0.1:' + CDP + '/json')).json();
  const page = t.find(x => x.type === 'page');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise(r => ws.onopen = r);
  let id = 0; const pend = new Map();
  ws.onmessage = e => {
    const m = JSON.parse(e.data);
    if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id); return; }
    if (m.method === 'Page.javascriptDialogOpening') {
      ws.send(JSON.stringify({ id: ++id, method: 'Page.handleJavaScriptDialog',
                               params: { accept: m.params.type === 'beforeunload' } }));
      return;
    }
    if (m.method === 'Runtime.exceptionThrown') {
      const s = String((m.params.exceptionDetails.exception || {}).description
        || m.params.exceptionDetails.text);
      if (!/unsafe-eval/.test(s)) errors.push(s.slice(0, 200));
    }
  };
  const send = (mm, p = {}) => new Promise(r => {
    const i = ++id; pend.set(i, r); ws.send(JSON.stringify({ id: i, method: mm, params: p }));
  });
  const ev = async x => {
    const rr = await send('Runtime.evaluate',
      { expression: x, awaitPromise: true, returnByValue: true });
    if (rr.result && rr.result.exceptionDetails) {
      throw new Error(String((rr.result.exceptionDetails.exception || {}).description || '').slice(0, 250));
    }
    return rr.result.result.value;
  };
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  await send('Runtime.enable'); await send('Page.enable');
  await send('Network.setCacheDisabled', { cacheDisabled: true }).catch(() => {});
  await send('Emulation.setDeviceMetricsOverride',
    { width: 1500, height: 1000, deviceScaleFactor: 1, mobile: false });

  // The page is /topology — `chip-status` is only the report route.
  await send('Page.navigate', { url: BASE + '/topology' });
  await sleep(7000);

  const ready = await ev(`({ hasMount: !!(window.ChipStatus && window.ChipStatus.mount),
                             hasHost: !!document.getElementById('topo-overview-tiles'),
                             url: location.pathname })`);
  ok('the Chip Status page is up with its tile host',
     ready && ready.hasMount && ready.hasHost, ready);
  if (!ready || !ready.hasMount) {
    fs.writeFileSync(OUT, JSON.stringify({ results, ready }, null, 1));
    console.log('rb negative: aborted — ' + JSON.stringify(ready));
    process.exit(0);
  }

  // The screenshot's chip, through the page's own mount API.
  const read = `(function () {
    var DIV = 5.37;
    function edge(pid, a, b, f) {
      return { pair_id: pid, source: a, target: b, has_cz: true, gate_kind: 'cz',
               directed: false, active: null, best_gate: 'cz', cz_fidelity: f,
               gate_fidelities: [{ metric: 'InterleavedRB', gate: 'cz',
                                   level: 'gate', value: f,
                                   average_gates_per_clifford: DIV }] };
    }
    window.ChipStatus.mount({
      topo: { nodes: [{ id: 'q1', grid_location: '0,0' },
                      { id: 'q2', grid_location: '1,0' },
                      { id: 'q3', grid_location: '0,1' },
                      { id: 'q4', grid_location: '1,1' }],
              edges: [edge('q1-q2', 'q1', 'q2', 0.9570),
                      edge('q3-q4', 'q3', 'q4', 0.5955)] },
      rawWiring: {}, defaultThresholds: {}, diagFindings: [], metricMeta: {} });
    return Array.prototype.map.call(
      document.querySelectorAll('#topo-overview-tiles .topo-card'),
      function (c) {
        var q = function (s) { var el = c.querySelector(s);
                               return el ? el.textContent.replace(/\\s+/g, ' ').trim() : ''; };
        return { title: q('.topo-card-title'), value: q('.topo-card-value'),
                 sub: q('.topo-card-sub') };
      });
  })()`;
  const tiles = await ev(read);
  ok('tiles rendered', Array.isArray(tiles) && tiles.length > 0,
     Array.isArray(tiles) ? tiles.length : tiles);

  const neg = (tiles || []).filter(function (t) { return /-\d/.test(t.value); });
  ok('no tile shows a negative fidelity on the customer’s own numbers',
     neg.length === 0, neg);

  const cliff = (tiles || []).filter(function (t) {
    return t.title.indexOf('Clifford fid. (IRB') >= 0; })[0];
  ok('the IRB-derived Clifford tile is there', !!cliff, cliff);
  ok('…showing the pair the bridge can carry', cliff && /76\.9/.test(cliff.value),
     cliff && cliff.value);
  ok('…and naming the one it cannot, with the divisor',
     cliff && /1 excluded/.test(cliff.sub) && /too noisy/.test(cliff.sub)
     && cliff.sub.indexOf('5.37') >= 0, cliff && cliff.sub);

  // The note must be READABLE, not overflowing its card.
  const fit = await ev(`(function () {
    var cards = document.querySelectorAll('#topo-overview-tiles .topo-card');
    for (var i = 0; i < cards.length; i++) {
      var ttl = cards[i].querySelector('.topo-card-title');
      if (!ttl || ttl.textContent.indexOf('Clifford fid. (IRB') < 0) continue;
      var sub = cards[i].querySelector('.topo-card-sub');
      return { cardW: Math.round(cards[i].getBoundingClientRect().width),
               overflowX: cards[i].scrollWidth > cards[i].clientWidth + 1,
               subH: sub ? Math.round(sub.getBoundingClientRect().height) : 0,
               clipped: sub ? sub.scrollHeight > sub.clientHeight + 1 : false };
    }
    return null;
  })()`);
  ok('the tile does not scroll sideways', fit && !fit.overflowX, fit);
  ok('…and its note is not clipped', fit && !fit.clipped, fit);

  await send('Page.captureScreenshot', { format: 'png' }).then(function (r) {
    if (r && r.result && r.result.data) {
      fs.writeFileSync(OUT.replace(/\.json$/, '.png'),
                       Buffer.from(r.result.data, 'base64'));
    }
  });

  fs.writeFileSync(OUT, JSON.stringify({ results, tiles, fit, errors }, null, 1));
  const bad = results.filter(r => !r.pass);
  console.log('rb negative: ' + (results.length - bad.length) + '/' + results.length
              + '  console errors: ' + errors.length);
  bad.forEach(b => console.log('  FAIL ' + b.name + ' ' + JSON.stringify(b.detail).slice(0, 350)));
  errors.slice(0, 3).forEach(e => console.log('  ERR ' + e));
  process.exit(0);
}
main().catch(e => { console.error(String(e && e.stack || e)); process.exit(1); });
