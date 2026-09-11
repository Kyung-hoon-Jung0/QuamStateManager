// stress_agent_mcp.cjs -- drive quam_state_manager.mcp as a REAL MCP client
// over stdio. Read-only tools by default; the write path is exercised only to
// reproduce its REFUSALS.
//
//   node tests/stress_agent_mcp.cjs <scenario>
//
// Scenarios: main | nosm | readonly | chippin | twowin | unseen | origin
'use strict';
const { spawn } = require('child_process');

const PY = process.env.SM_PY || 'D:\\miniconda3\\envs\\cqt\\python.exe';
const REPO = 'D:\\work\\statemanager-agent';
const S = 'C:\\Users\\KyunghoonJung\\AppData\\Local\\Temp\\claude\\D--work-statemanager\\dd0fa2c3-e492-405d-8783-2c62cd30ba4a\\scratchpad';
const INST = S + '\\inst_5433';

class Mcp {
  constructor(env = {}, argv = null) {
    this.env = Object.assign({}, process.env, {
      PYTHONUTF8: '1', PYTHONPATH: REPO, SM_INSTANCE: INST,
    }, env);
    for (const k of Object.keys(this.env)) if (this.env[k] === null) delete this.env[k];
    this.p = spawn(PY, argv || ['-m', 'quam_state_manager.mcp'], { env: this.env, stdio: ['pipe', 'pipe', 'pipe'] });
    this.buf = '';
    this.stderr = '';
    this.waiters = new Map();
    this.id = 0;
    this.exited = null;
    this.p.stdout.on('data', (d) => {
      this.buf += d.toString('utf8');
      let i;
      while ((i = this.buf.indexOf('\n')) >= 0) {
        const line = this.buf.slice(0, i).trim();
        this.buf = this.buf.slice(i + 1);
        if (!line) continue;
        let msg; try { msg = JSON.parse(line); } catch (e) { this.badline = line; continue; }
        const w = this.waiters.get(msg.id);
        if (w) { this.waiters.delete(msg.id); w(msg); }
      }
    });
    this.p.stderr.on('data', (d) => { this.stderr += d.toString('utf8'); });
    this.p.on('exit', (c) => { this.exited = c; for (const w of this.waiters.values()) w({ __exit: c }); this.waiters.clear(); });
  }
  send(method, params, ms = 60000) {
    const id = ++this.id;
    const t0 = Date.now();
    return new Promise((res) => {
      const timer = setTimeout(() => { this.waiters.delete(id); res({ __timeout: ms, __ms: Date.now() - t0 }); }, ms);
      this.waiters.set(id, (m) => { clearTimeout(timer); m.__ms = Date.now() - t0; res(m); });
      this.p.stdin.write(JSON.stringify({ jsonrpc: '2.0', id, method, params }) + '\n');
    });
  }
  notify(method, params) { this.p.stdin.write(JSON.stringify({ jsonrpc: '2.0', method, params }) + '\n'); }
  raw(s) { this.p.stdin.write(s); }
  async call(name, args, ms = 60000) {
    const r = await this.send('tools/call', { name, arguments: args || {} }, ms);
    if (r.__timeout || r.__exit !== undefined) return r;
    const txt = ((r.result || {}).content || [{}])[0].text;
    let parsed = null; try { parsed = JSON.parse(txt); } catch (e) { }
    return { isError: (r.result || {}).isError, text: txt, json: parsed, err: r.error, ms: r.__ms };
  }
  kill() { try { this.p.kill(); } catch (e) { } }
}

const out = [];
function log(...a) { const s = a.map((x) => (typeof x === 'string' ? x : JSON.stringify(x))).join(' '); out.push(s); console.log(s); }
function J(x, n = 400) { const s = JSON.stringify(x); return s && s.length > n ? s.slice(0, n) + '…' : s; }

async function handshake(m, clientName = 'claude-code') {
  const init = await m.send('initialize', {
    protocolVersion: '2025-06-18',
    capabilities: {},
    clientInfo: { name: clientName, version: '1.0.0' },
  });
  m.notify('notifications/initialized', {});
  return init;
}

// ---------------------------------------------------------------- main
async function scMain() {
  const m = new Mcp();
  const init = await handshake(m);
  log('[init] ms=' + init.__ms, J(init, 900));
  const ping = await m.send('ping', {});
  log('[ping]', J(ping));
  const tl = await m.send('tools/list', {});
  const tools = (tl.result || {}).tools || [];
  log('[tools/list] n=' + tools.length + ' ms=' + tl.__ms);
  log('[tools] ' + tools.map((t) => t.name).join(' '));
  // schema sanity: every tool has description + inputSchema.object
  const bad = tools.filter((t) => !t.description || !t.inputSchema || t.inputSchema.type !== 'object');
  log('[schema] malformed=' + bad.length + (bad.length ? ' ' + bad.map((b) => b.name) : ''));

  for (const [name, args] of [
    ['sm_status', {}],
    ['state_get', { path: '' }],
    ['state_get', { path: 'qubits.q1.xy.operations.x180.amplitude' }],
    ['state_get', { path: 'qubits.q1' }],
    ['state_search', { query: 'x180 amplitude', limit: 5 }],
    ['tray', {}],
    ['versions', { n: 3 }],
    ['field_history', { path: 'qubits.q1.xy.operations.x180.amplitude' }],
    ['runs', { n: 3 }],
    ['diagnostics', {}],
    ['families', {}],
    ['family_manual', { family: 'power_rabi' }],
    ['journal_read', {}],
    ['plan_status', {}],
    ['approvals', {}],
  ]) {
    const r = await m.call(name, args);
    log(`[call ${name}] ${J(args, 120)} isError=${r.isError} ms=${r.ms} -> ${J(r.json !== null ? r.json : r.text, 430)}`);
  }

  // negative surface
  const unk = await m.send('tools/call', { name: 'not_a_tool', arguments: {} });
  log('[unknown tool]', J(unk));
  const badargs = await m.call('state_get', {});
  log('[missing required arg]', 'isError=' + badargs.isError, J(badargs.text, 200));
  const badpath = await m.call('state_get', { path: 'qubits.qZZ.nope' });
  log('[bad path]', 'isError=' + badpath.isError, J(badpath.text || badpath.json, 260));
  const badmeth = await m.send('nonsense/method', {});
  log('[bad method]', J(badmeth));
  m.raw('this is not json\n');
  const after = await m.send('ping', {});
  log('[after garbage line] ping ->', J(after), 'alive=' + (m.exited === null));
  log('[stderr] ' + JSON.stringify(m.stderr.slice(0, 400)));
  m.kill();
}

// ------------------------------------------------------------- no SM
async function scNosm() {
  const m = new Mcp({ SM_INSTANCE: S + '\\inst_NOSUCH' });
  const init = await handshake(m);
  log('[init with no SM] ms=' + init.__ms, J(init, 300));
  const tl = await m.send('tools/list', {});
  log('[tools/list with no SM] n=' + ((tl.result || {}).tools || []).length + ' ms=' + tl.__ms);
  const t0 = Date.now();
  const r = await m.call('sm_status', {}, 40000);
  log('[sm_status with no SM] ms=' + (Date.now() - t0) + ' isError=' + r.isError + ' timeout=' + !!r.__timeout);
  log('[message] ' + JSON.stringify(r.text || ''));
  const r2 = await m.call('state_get', { path: 'qubits' }, 40000);
  log('[state_get with no SM] isError=' + r2.isError + ' ' + JSON.stringify((r2.text || '').slice(0, 300)));
  log('[stderr] ' + JSON.stringify(m.stderr.slice(0, 600)));
  m.kill();
}

// ----------------------------------------------------------- readonly
async function scReadonly() {
  const m = new Mcp({ SM_MCP_MODE: 'readonly' });
  await handshake(m);
  const tl = await m.send('tools/list', {});
  const names = ((tl.result || {}).tools || []).map((t) => t.name);
  log('[readonly tools] n=' + names.length + ' ' + names.join(' '));
  const WRITE = ['state_edit', 'apply_to_live', 'undo', 'take_live', 'note_set', 'journal_append', 'run_node', 'run_wait', 'undo_mine', 'plan_propose'];
  log('[readonly leak] ' + JSON.stringify(names.filter((n) => WRITE.includes(n))));
  const r = await m.call('state_edit', { path: 'qubits.q1.xy.operations.x180.amplitude', value: 0.123 });
  log('[readonly state_edit] isError=' + r.isError + ' ' + JSON.stringify(r.text));
  const r2 = await m.call('apply_to_live', {});
  log('[readonly apply_to_live] isError=' + r2.isError + ' ' + JSON.stringify(r2.text));
  const r3 = await m.call('sm_status', {});
  log('[readonly sm_status] isError=' + r3.isError + ' chip=' + ((r3.json || {}).name));
  m.kill();
}

// ------------------------------------------------------------ SM_CHIP
async function scChippin() {
  for (const pin of ['chip_5433', 'some_other_chip']) {
    const m = new Mcp({ SM_CHIP: pin });
    await handshake(m);
    const a = await m.call('sm_status', {});
    const b = await m.call('state_get', { path: 'qubits.q1.f_01' });
    const c = await m.call('versions', { n: 1 });
    log(`[SM_CHIP=${pin}] sm_status isError=${a.isError} -> ${J(a.json ? { chip: a.json.chip || a.json.name, loaded: a.json.loaded } : a.text, 300)}`);
    log(`[SM_CHIP=${pin}] state_get isError=${b.isError} -> ${J(b.json || b.text, 300)}`);
    log(`[SM_CHIP=${pin}] versions  isError=${c.isError} -> ${J((c.json && c.json.versions) ? c.json.versions.length + ' versions' : (c.json || c.text), 260)}`);
    m.kill();
  }
}

// ---------------------------------------------------------- two windows
async function scTwowin() {
  const m = new Mcp();
  await handshake(m);
  const r = await m.call('sm_status', {});
  log('[two windows, no pin] chip=' + ((r.json || {}).name) + ' path=' + ((r.json || {}).path));
  const m2 = new Mcp({ SM_CHIP: 'chip_B5434' });
  await handshake(m2);
  const r2 = await m2.call('sm_status', {});
  log('[two windows, SM_CHIP=chip_B5434] isError=' + r2.isError + ' -> ' + J(r2.json || r2.text, 500));
  const m3 = new Mcp({ SM_URL: 'http://127.0.0.1:5434' });
  await handshake(m3);
  const r3 = await m3.call('sm_status', {});
  log('[SM_URL=5434] chip=' + ((r3.json || {}).name) + ' path=' + ((r3.json || {}).path));
  m.kill(); m2.kill(); m3.kill();
}

// ------------------------------------------- the unseen-edit refusal
async function scUnseen() {
  const m = new Mcp();
  await handshake(m);
  log('[status before] ' + J((await m.call('sm_status', {})).json, 200));
  // 1. apply before ever looking at the tray
  const a0 = await m.call('apply_to_live', {});
  log('[apply before tray] ' + J(a0.json || a0.text, 400));
  // 2. the agent stages one edit and READS the tray (so _seen is set)
  const e = await m.call('state_edit', { path: process.env.EDIT_PATH || 'qubits.q1.xy.operations.x180.amplitude', value: process.env.EDIT_VALUE || 0.1234, reason: 'mcp connectivity probe (staged only)' });
  log('[state_edit] isError=' + e.isError + ' -> ' + J(e.json || e.text, 500));
  const t = await m.call('tray', {});
  log('[tray after agent edit] ' + J(t.json, 500));
  return m;   // caller injects the human edit then presses apply
}

module.exports = { Mcp, handshake, log, scUnseen };

async function main() {
  const sc = process.argv[2] || 'main';
  const map = { main: scMain, nosm: scNosm, readonly: scReadonly, chippin: scChippin, twowin: scTwowin };
  if (sc === 'unseen') { const m = await scUnseen(); m.kill(); return; }
  if (!map[sc]) { console.error('unknown scenario ' + sc); process.exit(2); }
  await map[sc]();
}
if (require.main === module) main().then(() => process.exit(0)).catch((e) => { console.error(e); process.exit(1); });
