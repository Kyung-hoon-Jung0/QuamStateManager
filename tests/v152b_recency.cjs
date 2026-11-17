const assert = require('assert');
const fs = require('fs'); const {JSDOM} = require('jsdom');
const w = new JSDOM('<html><body><div id="topo-hero"></div></body></html>', {runScripts:'outside-only', url:'http://localhost/'}).window;
w.fetch=()=>new w.Promise(()=>{}); w.htmx={process(){}};
for(const f of ['app.js','topo-graph.js','chip-status.js']) w.eval(fs.readFileSync(require('path').join(__dirname,'../quam_state_manager/web/static',f),'utf8'));
const e={pair_id:'q1-2',source:'q1',target:'q2',cz_fidelity:.991,has_cz:true,gate_fidelities:[{gate:'cz',metric:'InterleavedRB',value:.991}]};
assert.equal(w.TopoGraph.pairState(e).age,'unknown');
assert.equal(w.TopoGraph.pairState(e).stale,false);
w.ChipStatus.mount({topo:{nodes:[{id:'q1',grid_location:'0,0'},{id:'q2',grid_location:'1,0'}],edges:[e]},rawWiring:{},defaultThresholds:{},diagFindings:[],metricMeta:{}});
assert.match(w.document.querySelector('.topo-pair-badge title').textContent,/age unknown/);
assert(!w.document.querySelector('.topo-pair-badge').classList.contains('topo-pair-badge-stale'));
e.cz_fidelity_updated_at=1577836800000;
assert.equal(w.TopoGraph.pairState(e).stale,true);
w.close();

// The pair badge on an IRB map shows the age of the IRB row it shows, never the
// pair's cz_fidelity time (here an older Bell state measurement).
function badge(edge) {
  const v = new JSDOM('<html><body><div id="topo-hero"></div></body></html>',
                      {runScripts: 'outside-only', url: 'http://localhost/'}).window;
  v.fetch = () => new v.Promise(() => {}); v.htmx = {process() {}};
  for (const f of ['app.js', 'topo-graph.js', 'chip-status.js'])
    v.eval(fs.readFileSync(require('path').join(__dirname, '../quam_state_manager/web/static', f), 'utf8'));
  v.ChipStatus.mount({topo: {nodes: [{id: 'q1', grid_location: '0,0'}, {id: 'q2', grid_location: '1,0'}],
                             edges: [edge]}, rawWiring: {}, defaultThresholds: {}, diagFindings: [], metricMeta: {}});
  const b = v.document.querySelector('.topo-pair-badge');
  const out = {title: b.querySelector('title').textContent, stale: b.classList.contains('topo-pair-badge-stale')};
  v.close();
  return out;
}
const DAY = 864e5, now = Date.now();
function irbEdge(irbTime) {
  return {pair_id: 'q1-2', source: 'q1', target: 'q2', cz_fidelity: .95, fidelity_source: 'macro', has_cz: true,
          cz_fidelity_updated_at: now - 69 * DAY,
          gate_fidelities: [{gate: 'cz', metric: 'Bell_State', value: .95, level: 'state', time_ms: now - 69 * DAY},
                            {gate: 'cz', metric: 'InterleavedRB', value: .97, level: 'gate', time_ms: irbTime}]};
}
let r = badge(irbEdge(now - 2 * DAY));
assert(!/69 days|age unknown/.test(r.title), r.title);
assert.equal(r.stale, false, r.title);
r = badge(irbEdge(now - 40 * DAY));
assert.match(r.title, /measured 40 days ago/);
assert.equal(r.stale, true, r.title);
r = badge(irbEdge(null));
assert(!/69 days/.test(r.title), r.title);
assert.ok(r.title.includes('age unknown: no time recorded at '
    + 'qubit_pairs.q1-2.macros.cz.fidelity.InterleavedRB_updated_at'), r.title);
assert.equal(r.stale, false, r.title);
console.log('v152b_recency: badge age follows the shown row (6 assertions)');
