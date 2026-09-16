/* docs/189 — does a pulse whose CLASS is the lab's own actually plot?
 *
 * Customer, on-site 2026-09-16: "pulses 메뉴에서 snz 는 plotting이 안돼. 왜 그래?"
 * The KRISS_CZ chip's CZ flux pulse is `quam_config.two_flux_gate.SNZTwoFluxPulse`
 * — a class the lab wrote. `waveform_synth` mirrors quam's classes only, so it
 * answered "unrecognized pulse class ..." and the page drew nothing.
 *
 * This drives the REAL page in REAL Chrome: only the browser runs
 * `refreshCommittedPlot`, which is where the fallback to the env's own
 * generate_config() waveform lives. A jsdom harness would be testing stubs of
 * the two fetches that ARE the mechanism.
 *
 *   node tests/snz_plot_probe.cjs http://127.0.0.1:5078 <pulse-path>
 */
const { spawn } = require('child_process');
const os = require('os');
const path = require('path');
const fs = require('fs');

const BASE = process.argv[2] || 'http://127.0.0.1:5078';
const PULSE = process.argv[3] || 'qubits.q1.z.operations.cz_SNZ_flux_pulse_q1_q2';
const CHROME = process.env.CHROME_PATH
    || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const PORT = 9334;

let failures = 0;
function ok(cond, what) {
    if (cond) console.log('  ok   ' + what);
    else { failures++; console.log('  FAIL ' + what); }
}
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

async function main() {
    const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'snz-probe-'));
    const url = BASE + '/pulses?path=' + encodeURIComponent(PULSE);
    const chrome = spawn(CHROME, [
        '--headless=new', '--disable-gpu', '--no-first-run',
        '--remote-debugging-port=' + PORT, '--remote-allow-origins=*',
        '--user-data-dir=' + profile,
        '--window-size=1600,1500', url,
    ], { stdio: 'ignore' });

    let targets = [];
    for (let i = 0; i < 40 && !targets.length; i++) {
        await sleep(500);
        try {
            const r = await fetch('http://127.0.0.1:' + PORT + '/json/list');
            targets = (await r.json()).filter(t => t.type === 'page');
        } catch (e) { /* not up yet */ }
    }
    if (!targets.length) { console.log('  FAIL chrome never listened'); process.exit(1); }

    const ws = new WebSocket(targets[0].webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let id = 0; const pending = new Map();
    ws.onmessage = (ev) => {
        const m = JSON.parse(ev.data);
        if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
    };
    const send = (method, params) => new Promise(res => {
        const n = ++id; pending.set(n, res);
        ws.send(JSON.stringify({ id: n, method, params: params || {} }));
    });
    async function ev(expr) {
        const r = await send('Runtime.evaluate',
            { expression: expr, awaitPromise: true, returnByValue: true });
        const res = r.result || {};
        if (res.exceptionDetails) return { error: JSON.stringify(res.exceptionDetails).slice(0, 300) };
        return res.result || {};
    }

    // open the pulse's detail pane the way a person does: click its row
    let opened = false;
    for (let i = 0; i < 40 && !opened; i++) {
        await sleep(500);
        const r = await ev(
            '(function () { var el = document.querySelector('
            + '\'[data-pulse-path="' + PULSE + '"]\');'
            + ' if (!el) return "no-row";'
            + ' if (document.querySelector("#pulse-detail-plot")) return "open";'
            + ' el.click(); return "clicked"; })()');
        opened = r.value === 'open';
    }
    ok(opened, 'the pulse detail pane opened');

    // give the committed-plot refresh (and its ground-truth fallback) time
    const state = await ev(
        'new Promise(function (res) { var t0 = Date.now(); (function k() {'
        + ' var host = document.querySelector("#pulse-detail-plot");'
        + ' var drawn = !!(host && host.querySelector(".js-plotly-plot, svg, canvas"));'
        + ' if (drawn || Date.now() - t0 > 25000) {'
        + '   var bar = document.querySelector(".pulse-plot-bar");'
        + '   var lab = bar && bar.querySelector(".pulse-plot-label");'
        + '   var err = bar && bar.querySelector(".pulse-synth-err");'
        + '   var note = bar && bar.querySelector(".pulse-verify-note");'
        + '   var traces = 0, pts = 0;'
        + '   try { var gd = host.querySelector(".js-plotly-plot") || host;'
        + '         if (gd && gd.data) { traces = gd.data.length;'
        + '           pts = (gd.data[0] && gd.data[0].y || []).length; } } catch (e) {}'
        + '   return res({ drawn: drawn, traces: traces, pts: pts,'
        + '     label: lab ? lab.textContent.trim() : "",'
        + '     err: err && !err.hidden ? err.textContent.trim() : "",'
        + '     note: note && !note.hidden ? note.textContent.trim() : "" });'
        + ' } setTimeout(k, 300); })(); })');

    const v = state.value || {};
    if (state.error) { console.log('  FAIL probe threw: ' + state.error); failures++; }
    console.log('  -- label: ' + JSON.stringify(v.label));
    console.log('  -- err:   ' + JSON.stringify(v.err));
    console.log('  -- note:  ' + JSON.stringify(v.note));
    ok(v.drawn === true, 'a waveform is drawn for the lab-owned class');
    ok(v.pts > 0, 'the drawn trace carries samples (' + v.pts + ')');
    ok(/generated config/.test(v.label),
       'the plot SAYS the curve came from the generated config, not the synth');
    ok(v.err === '', 'no leftover error line once the real waveform is in');

    // optional: SHOT=<file> writes a screenshot of the page as the reviewer
    // sees it -- docs/141's rule that a UI round is verified by looking at it.
    if (process.env.SHOT) {
        const shot = await send('Page.captureScreenshot', { format: 'png' });
        const b64 = shot && shot.result && shot.result.data;
        if (b64) { fs.writeFileSync(process.env.SHOT, Buffer.from(b64, 'base64')); }
        console.log('  --   screenshot: ' + (b64 ? process.env.SHOT : 'FAILED'));
    }

    ws.close(); chrome.kill();
    try { fs.rmSync(profile, { recursive: true, force: true }); } catch (e) {}
    console.log(failures ? '\n' + failures + ' FAILED' : '\nall checks passed');
    process.exit(failures ? 1 : 0);
}

main().catch(e => { console.log('  FAIL ' + e); process.exit(1); });
