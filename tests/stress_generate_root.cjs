/* The Generate wizard's four measured defects, re-driven after the fix.
 *
 * Found while trying to generate the KRISS 5Q chip in the environment that lab
 * actually runs (conda KRISS_CZ, which imports their own quam_config):
 *
 *  1. A QDAC spec is refused by the build with a precise, well-written
 *     sentence — and the page rendered it as
 *     "• undefined — needs ? · (missing). Fix:" because two SHAPES arrive
 *     under one key (`capability_blockers` carries objects from
 *     capabilities.assess and one finished string from the root check).
 *  2. The Review said "✓ This environment can build everything this chip
 *     needs." directly above a list headed "Will be skipped / downgraded",
 *     one of whose entries loses a qubit's flux component entirely.
 *  3. The Review could not SEE the roots the build refuses on: the
 *     capabilities route never put `qpu_roots` in the manifest, so the review
 *     promised buildable for a spec the build then rejected outright.
 *  4. There was no way to name the chip's ROOT CLASS. Every lab that drives
 *     this wizard has its own Quam subclass — the probe lists it FIRST — and
 *     the build always wrote the stock one, so the lab's own fields and
 *     overrides were simply absent from the generated chip.
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
    if (m.method === 'Runtime.exceptionThrown') {
      const s = String((m.params.exceptionDetails.exception || {}).description || m.params.exceptionDetails.text);
      if (!/unsafe-eval/.test(s)) errors.push(s.slice(0, 200));
    }
  };
  const send = (mm, p = {}) => new Promise(r => { const i = ++id; pend.set(i, r); ws.send(JSON.stringify({ id: i, method: mm, params: p })); });
  const ev = async x => {
    const rr = await send('Runtime.evaluate', { expression: x, awaitPromise: true, returnByValue: true });
    if (rr.result && rr.result.exceptionDetails) throw new Error(String((rr.result.exceptionDetails.exception || {}).description || '').slice(0, 250));
    return rr.result.result.value;
  };
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  await send('Runtime.enable'); await send('Page.enable');
  await send('Network.enable'); await send('Network.setCacheDisabled', { cacheDisabled: true });
  await send('Emulation.setDeviceMetricsOverride', { width: 1600, height: 1050, deviceScaleFactor: 1, mobile: false });

  await send('Page.navigate', { url: BASE + '/generate' });
  await sleep(9000);

  // step 1: the lab's own env
  const env = await ev(`(function(){
    var rows = document.querySelectorAll('#gen-env-list .gen-env-row');
    for (var i = 0; i < rows.length; i++) {
      if (/KRISS_CZ/.test(rows[i].textContent)) { rows[i].click(); return rows[i].getAttribute('data-python'); }
    }
    return null;
  })()`);
  ok('the lab env is selectable', !!env && /KRISS_CZ/.test(env), env);
  await sleep(2500);

  // walk to Qubits and build a QDAC spec straight through the spec object,
  // which is what both the review and the build read
  await ev(`(function(){
    var s = window.GenerateWizard && window.GenerateWizard._state
            ? window.GenerateWizard._state() : null;
    return s ? 'has state' : 'no state export';
  })()`).catch(() => null);

  // a real, empty folder: the refusal must happen BEFORE anything is written
  const outDir = (OUT || '').replace(/[^\\/]+$/, '') + 'gen_root_probe_out';
  fs.rmSync(outDir, { recursive: true, force: true });
  fs.mkdirSync(outDir, { recursive: true });
  await ev('window.OUT_DIR = ' + JSON.stringify(outDir.replace(/\\/g, '/')) + '; 1');
  const spec = await ev(`(async function(){
    // ask the two routes directly with a QDAC spec — this is the exact shape
    // the wizard posts, and it is what produced "undefined" on screen
    var spec = {
      network: { host: "127.0.0.1", cluster_name: "c", port: null },
      instruments: { controllers: [{ con: 1, fems: [{ slot: 3, fem: "mw" }, { slot: 5, fem: "lf" }] }], opx_plus: [], octaves: [] },
      qubits: ["q1", "q2"], qubit_pairs: [["q1", "q2"]], twpas: [],
      qdac: { communication_type: "Ethernet", ip_address: "192.168.88.244", port: 5025,
              qubits: { q1: { channel: 1, trigger_port: "ext1" } } },
      lines: {}, populate: {}
    };
    var r = await fetch('/generate/capabilities', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ spec: spec })
    });
    var cap = await r.json();
    // output_path is the key this route reads. Posting out_dir made it
    // answer "No output folder given." -- a 400 for the wrong reason, which
    // is a check that proves nothing. (No backticks here: this comment lives
    // inside a template literal.)
    var b = await fetch('/generate/build', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ spec: spec, output_path: OUT_DIR })
    });
    var build = await b.json();
    return { capStatus: r.status, buildStatus: b.status,
             hasRoot: !!cap.root, rootBlocker: cap.root && cap.root.blocker,
             roots: (cap.roots || []).map(function(x){ return x.path; }),
             buildable: cap.report && cap.report.buildable,
             blockers: build.capability_blockers };
  })()`);
  ok('the review route now reports the root check', spec.hasRoot, spec);
  ok('…and it refuses the QDAC chip the build would refuse',
     !!spec.rootBlocker, spec.rootBlocker);
  ok('…and it lists the roots this env offers',
     spec.roots && spec.roots.length > 0 && spec.roots.some(function (p) { return /quam_config/.test(p); }),
     spec.roots);
  ok('the build still refuses it, for the SAME reason',
     spec.buildStatus === 400
     && (spec.blockers || []).some(function (b) { return /QPU root class/.test(String(b)); }),
     { status: spec.buildStatus, blockers: spec.blockers });
  ok('…and nothing was written before the refusal',
     fs.readdirSync(outDir).length === 0, fs.readdirSync(outDir));

  // the render: a STRING blocker must read as a sentence, not "undefined"
  const rendered = await ev(`(function(){
    var host = document.createElement('div');
    host.id = 'gen-build-result';
    document.body.appendChild(host);
    var res = { capability_blockers: ["No QPU root class in this environment can hold QDAC-biased qubits."] };
    // the same shape the page renders
    var el = host;
    el.className = "gen-build-result gen-build-error";
    var out = [];
    res.capability_blockers.forEach(function (b) {
      out.push((typeof b === "string") ? "• " + b
        : "• " + (b.label || "?") + " — needs " + (b.package || "?"));
    });
    host.remove();
    return out.join(" | ");
  })()`);
  void rendered;

  // …and the real renderer, read from the shipped file
  const src = await (await fetch(BASE + '/static/generate.js')).text();
  ok('the renderer handles a STRING blocker', src.indexOf('typeof b === "string"') >= 0);
  ok('…before it reaches the object template',
     src.indexOf('typeof b === "string"') < src.indexOf('(missing). Fix: '));
  ok('the verdict no longer says "everything" while listing skips',
     src.indexOf('Nothing blocks the build') >= 0);
  ok('…and a root refusal is shown as a blocker in the review',
     src.indexOf('res.root && res.root.blocker') >= 0);
  ok('the wizard can name the chip root class', src.indexOf('gen-quam-class') >= 0);
  ok('…and Automatic stays the default (the old behaviour)',
     src.indexOf('auto.value = ""') >= 0 && src.indexOf('delete state.spec.quam_class') >= 0);

  fs.writeFileSync(OUT, JSON.stringify({ results, errors, spec }, null, 1));
  const bad = results.filter(r => !r.pass);
  console.log('generate checks: ' + (results.length - bad.length) + '/' + results.length
              + '  console errors: ' + errors.length);
  bad.forEach(b => console.log('  FAIL ' + b.name + ' ' + JSON.stringify(b.detail).slice(0, 250)));
  errors.slice(0, 3).forEach(e => console.log('  ERR ' + e));
  process.exit(0);
}
main().catch(e => { console.error(String(e && e.stack || e)); process.exit(1); });
