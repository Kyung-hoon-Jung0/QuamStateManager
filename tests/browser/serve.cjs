/* Start a REAL SM dev server on its own port, holding its own COPY of the real
 * 20-qubit customer chip, and print the port + paths as JSON on stdout.
 *
 * Every verification run gets its own port, its own instance dir and its own
 * chip copy, so parallel agents cannot contaminate each other — and the
 * customer's actual folder is never opened for writing by any of them.
 *
 *   node tests/browser/serve.cjs --port 5151 --tag topology
 *
 * Leaves the server in the foreground; kill the process to stop it.
 */
'use strict';

const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const argv = process.argv.slice(2);
function arg(name, dflt) {
  const i = argv.indexOf('--' + name);
  return i >= 0 ? argv[i + 1] : dflt;
}
const PORT = arg('port', '8811');
const TAG = arg('tag', 'run');
const REPO = path.join(__dirname, '..', '..');
const PY = 'D:\\miniconda3\\envs\\cqt\\python.exe';
const CHIP = 'D:\\work\\Customer_Codes\\CQT\\CS_installations\\qualibration_graphs'
           + '\\superconducting\\quam_state';

// REFUSE to start on a port something else already holds. Without this the
// readiness probe below is satisfied by the STALE server — it GETs "/" on the
// port, gets 200 from the old process, prints READY, and every subsequent
// measurement is taken against code that is not the code under test. That
// happened, and it cost a full verification round before it was noticed.
const net = require('net');
const probe = net.createServer();
let portBusy = false;
try {
  probe.listen({ port: Number(PORT), host: '127.0.0.1', exclusive: true });
} catch (e) { portBusy = true; }
probe.on('error', () => { portBusy = true; });
// Give the bind a tick to fail, then decide.
setTimeout(() => {
  if (portBusy) {
    console.error('[srv] FATAL: port ' + PORT + ' is already in use — refusing to '
      + 'start, because a stale server there would answer the readiness probe '
      + 'and silently serve old code.');
    process.exit(2);
  }
  probe.close(() => start());
}, 300);

function start() {
const root = fs.mkdtempSync(path.join(os.tmpdir(), 'smbrowse_' + TAG + '_'));
const chip = path.join(root, 'quam_state');
fs.cpSync(CHIP, chip, { recursive: true });
const inst = path.join(root, 'instance');
fs.mkdirSync(inst, { recursive: true });

// `threaded=True` matters: the app's own pages fire several concurrent XHRs
// (tray, drift, diagnostics, version), and a single-threaded dev server
// serialises them into what looks like a hang.
// The chip is loaded over HTTP, by the real /load route, AFTER the server is
// listening — not through a test_client before app.run. The pre-run load does
// not survive into the served app here, and driving the real route is what a
// browser verification should be doing anyway.
const code = [
  'import sys, threading, time, urllib.request, urllib.parse',
  'sys.path.insert(0, r"' + REPO + '")',
  'import quam_state_manager',
  'assert "statemanager-cfb" in quam_state_manager.__file__, quam_state_manager.__file__',
  'from quam_state_manager.web.app import create_app',
  'app = create_app(instance_path=r"' + inst + '")',
  'def _load():',
  '    url = "http://127.0.0.1:' + PORT + '"',
  '    for _ in range(60):',
  '        try:',
  '            urllib.request.urlopen(url + "/", timeout=2).read(1)',
  '            break',
  '        except Exception:',
  '            time.sleep(0.5)',
  '    data = urllib.parse.urlencode({"folder": r"' + chip + '"}).encode()',
  '    req = urllib.request.Request(url + "/load", data=data,',
  '                                 headers={"Origin": url})',
  '    try:',
  '        r = urllib.request.urlopen(req, timeout=60)',
  '        print("[boot] /load ->", r.status, flush=True)',
  '    except Exception as e:',
  '        print("[boot] /load FAILED:", e, flush=True)',
  '    try:',
  '        n = urllib.request.urlopen(url + "/bulk", timeout=120).read().count(b"bulk-cell")',
  '        print("[boot] READY cells=%d" % n, flush=True)',
  '    except Exception as e:',
  '        print("[boot] readiness probe failed:", e, flush=True)',
  'threading.Thread(target=_load, daemon=True).start()',
  'app.run(host="127.0.0.1", port=' + PORT + ', threaded=True, debug=False, use_reloader=False)',
].join('\n');

const p = spawn(PY, ['-c', code], {
  cwd: REPO,
  env: Object.assign({}, process.env, { PYTHONUTF8: '1', PYTHONUNBUFFERED: '1' }),
  stdio: ['ignore', 'pipe', 'pipe'],
});
p.stdout.on('data', (d) => process.stderr.write('[srv] ' + d));
p.stderr.on('data', (d) => process.stderr.write('[srv] ' + d));
p.on('exit', (c) => { process.stderr.write('[srv] exited ' + c + '\n'); process.exit(c || 0); });

console.log(JSON.stringify({ port: Number(PORT), root, chip, instance: inst, pid: p.pid }));

}
