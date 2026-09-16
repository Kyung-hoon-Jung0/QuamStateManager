/* docs/188 — drive the report's "Download HTML" serializer in REAL Chrome.
 *
 * `ChipReport.buildStandalone()` is the only part of the printable report that
 * is CODE rather than markup, and until this probe the pins only GREPPED for
 * its presence — the shape docs/187 named as this project's recurring failure
 * (a handler that never runs). It cannot be driven under jsdom honestly: it
 * waits for the ComponentMap's SVG and then FETCHES both stylesheets, so a
 * harness would be testing its own stubs.
 *
 * Usage (a server must already be serving a chip):
 *   node tests/report_download_probe.cjs http://127.0.0.1:5077
 *
 * Exits non-zero on the first failed check. Not wired into pytest: it needs a
 * running server and a real Chrome, so it is a round-verification tool, like
 * the cdp_* drivers.
 */
const { spawn } = require('child_process');
const os = require('os');
const path = require('path');
const fs = require('fs');

const BASE = process.argv[2] || 'http://127.0.0.1:5077';
const CHROME = process.env.CHROME_PATH
    || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const PORT = 9333;

let failures = 0;
function ok(cond, what) {
    if (cond) { console.log('  ok   ' + what); }
    else { failures++; console.log('  FAIL ' + what); }
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function cdpTargets() {
    const r = await fetch('http://127.0.0.1:' + PORT + '/json/list');
    return r.json();
}

async function main() {
    const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'rep-probe-'));
    const chrome = spawn(CHROME, [
        '--headless=new', '--disable-gpu', '--no-first-run',
        '--remote-debugging-port=' + PORT,
        '--remote-allow-origins=*',
        '--user-data-dir=' + profile,
        BASE + '/chip-status/report',
    ], { stdio: 'ignore' });

    let targets = [];
    for (let i = 0; i < 40 && !targets.length; i++) {
        await sleep(500);
        try { targets = (await cdpTargets()).filter(t => t.type === 'page'); }
        catch (e) { /* not listening yet */ }
    }
    if (!targets.length) { console.log('  FAIL chrome never listened'); process.exit(1); }

    const ws = new WebSocket(targets[0].webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });

    let id = 0;
    const pending = new Map();
    ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data);
        if (msg.id && pending.has(msg.id)) {
            pending.get(msg.id)(msg);
            pending.delete(msg.id);
        }
    };
    function send(method, params) {
        const n = ++id;
        return new Promise((res) => {
            pending.set(n, res);
            ws.send(JSON.stringify({ id: n, method, params: params || {} }));
        });
    }
    async function evaluate(expr) {
        const r = await send('Runtime.evaluate', {
            expression: expr, awaitPromise: true, returnByValue: true,
        });
        if (r.result && r.result.exceptionDetails) {
            return { error: JSON.stringify(r.result.exceptionDetails) };
        }
        return r.result && r.result.result ? r.result.result : {};
    }

    // the page is already navigating; wait for the serializer to exist
    let ready = false;
    for (let i = 0; i < 40 && !ready; i++) {
        await sleep(400);
        const r = await evaluate('typeof window.ChipReport');
        ready = r.value === 'object';
    }
    ok(ready, 'ChipReport is on the page');

    // the map must actually have drawn, or the download would bake a
    // "Loading chip layout..." placeholder into the archived file
    const svg = await evaluate(
        'new Promise(r => { var t0 = Date.now(); (function k(){'
        + 'if (document.querySelector("#component-map svg") || Date.now()-t0 > 8000)'
        + ' return r(!!document.querySelector("#component-map svg"));'
        + ' setTimeout(k, 150); })(); })');
    ok(svg.value === true, 'the topology map drew before the download runs');

    const res = await evaluate(
        'window.ChipReport.buildStandalone().then(function (h) { return {'
        + ' len: h.length,'
        + ' doctype: h.slice(0, 15),'
        + ' scripts: (h.match(/<script/g) || []).length,'
        + ' links: (h.match(/<link rel="stylesheet"/g) || []).length,'
        + ' styles: (h.match(/<style/g) || []).length,'
        + ' svg: (h.match(/<svg/g) || []).length,'
        + ' dl: h.indexOf("rep-download"),'
        + ' rb: h.indexOf("2Q Clifford (SRB)"),'
        + ' irb: h.indexOf("2Q gate (IRB)"),'
        + ' ports: h.indexOf("Wiring &mdash; ports") >= 0 || h.indexOf("Wiring — ports") >= 0,'
        + ' echo: h.indexOf("T2 echo") >= 0,'
        + ' print: h.indexOf("window.print()") >= 0 };'
        + ' }, function (e) { return { error: String(e) }; })');

    const v = res.value || {};
    if (res.error || v.error) {
        console.log('  FAIL buildStandalone threw: ' + (res.error || v.error));
        failures++;
    } else {
        ok(v.doctype.indexOf('<!DOCTYPE html>') === 0, 'the file is a whole document');
        ok(v.len > 100000, 'the file carries the stylesheets inline (' + v.len + ' chars)');
        ok(v.scripts === 0, 'every script is stripped');
        ok(v.links === 0, 'no stylesheet LINK survives (they would 404 offline)');
        ok(v.styles >= 1, 'the stylesheet is inlined as <style>');
        ok(v.svg >= 1, 'the drawn map is baked in');
        ok(v.dl === -1, 'the Download button is removed (it is dead in a saved file)');
        ok(v.print === true, 'Print still works in the saved file');
        // docs/188: the sections the customer asked for must be IN the archive,
        // not only on the live page
        ok(v.rb >= 0, 'the archived file names the per-Clifford level');
        ok(v.irb >= 0, 'the archived file names the per-gate level');
        ok(v.ports === true, 'the archived file carries the wiring ports');
        ok(v.echo === true, 'the archived file carries T2 echo');
    }

    // Does the serializer actually WAIT for the map? The checks above cannot
    // say: they let the SVG draw first, so removing `whenDrawn` changes
    // nothing they observe (the sweep found exactly that, and it was a finding
    // about this probe, not the product). Make the wait observable instead:
    // take the drawn SVG away, start the download, and put an SVG back after
    // 800ms. A serializer that waits returns LATE and carries the map; one
    // that does not returns at once and archives the placeholder.
    const late = await evaluate(
        'new Promise(function (res) {'
        + '  var host = document.querySelector("#component-map");'
        + '  var old = host.querySelector("svg");'
        + '  if (old) old.remove();'
        + '  var t0 = Date.now();'
        + '  setTimeout(function () {'
        + '    var s = document.createElementNS("http://www.w3.org/2000/svg", "svg");'
        + '    s.setAttribute("data-late", "1");'
        + '    host.appendChild(s);'
        + '  }, 800);'
        + '  window.ChipReport.buildStandalone().then(function (h) {'
        + '    res({ ms: Date.now() - t0, late: h.indexOf("data-late") >= 0 });'
        + '  }, function (e) { res({ error: String(e) }); });'
        + '})');
    const L = late.value || {};
    ok(L.ms >= 700, 'the download waits for the map to draw (' + L.ms + 'ms)');
    ok(L.late === true, 'a map that drew late is still baked in');

    ws.close();
    chrome.kill();
    try { fs.rmSync(profile, { recursive: true, force: true }); } catch (e) {}
    console.log(failures ? '\n' + failures + ' FAILED' : '\nall checks passed');
    process.exit(failures ? 1 : 0);
}

main().catch(e => { console.log('  FAIL ' + e); process.exit(1); });
