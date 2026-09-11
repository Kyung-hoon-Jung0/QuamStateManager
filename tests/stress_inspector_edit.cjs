/* docs/183 — the qubit/pair inspector's inline edit, driven as a person drives it.
 *
 * The write-path stress round's Ctrl+Z lane found that Enter in the inspector
 * issues NO request at all: the value is silently dropped, so the inspector can
 * neither commit nor be undone. This driver reproduces it against the running
 * server and is the same file that proves the fix.
 *
 * It types with real CDP key events, because that is the whole point — a
 * synthetic `form.dispatchEvent(new Event('submit'))` DOES post, which is what
 * made this invisible to every jsdom harness.
 *
 * argv[2]=out.json argv[3]=cdp argv[4]=base argv[5]=qubit argv[6]=pair
 */
const fs = require('fs');
const OUT = process.argv[2], CDP = process.argv[3], BASE = process.argv[4];
const QUBIT = process.argv[5] || 'q1', PAIR = process.argv[6] || '';
const results = [], errors = [], dialogs = [];
function ok(n, c, d) { results.push({ name: n, pass: !!c, detail: d === undefined ? null : d }); }

async function main() {
  const t = await (await fetch('http://127.0.0.1:' + CDP + '/json')).json();
  const page = t.find(x => x.type === 'page');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise(r => ws.onopen = r);
  let id = 0; const pend = new Map();
  const posts = [];
  ws.onmessage = e => {
    const m = JSON.parse(e.data);
    if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id); return; }
    if (m.method === 'Network.requestWillBeSent') {
      const r = m.params.request;
      if (r.method === 'POST') posts.push(r.url.replace(BASE, ''));
    }
    // A confirm() blocks the page's main thread and every later CDP command
    // with it — which is how the first run of this driver after the fix hung
    // forever. Record what was asked and decline it: a dialog is information
    // (an FSP compensation plan, a chip-identity mismatch), not noise.
    if (m.method === 'Page.javascriptDialogOpening') {
      // `beforeunload` carries no message and is the BROWSER asking whether to
      // leave a page with unsaved edits — which this driver always means to do.
      // Declining it cancels the navigation, which is exactly what made the
      // next two phases report "no row to click" and "no pair given": they were
      // still on the page before.
      var isUnload = m.params.type === 'beforeunload';
      if (!isUnload) {
        dialogs.push(String(m.params.type) + ': '
                     + String(m.params.message || '').slice(0, 200));
      }
      ws.send(JSON.stringify({ id: ++id, method: 'Page.handleJavaScriptDialog',
                               params: { accept: isUnload } }));
      return;
    }
    if (m.method === 'Runtime.exceptionThrown') {
      const s = String((m.params.exceptionDetails.exception || {}).description
        || m.params.exceptionDetails.text);
      errors.push(s.slice(0, 220));
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
  await send('Network.enable'); await send('Network.setCacheDisabled', { cacheDisabled: true });
  await send('Input.enable').catch(() => {});
  await send('Emulation.setDeviceMetricsOverride',
    { width: 1600, height: 1050, deviceScaleFactor: 1, mobile: false });

  async function key(k, opts) {
    await send('Input.dispatchKeyEvent', Object.assign(
      { type: 'keyDown', key: k }, opts || {}));
    await send('Input.dispatchKeyEvent', Object.assign(
      { type: 'keyUp', key: k }, opts || {}));
    await sleep(30);
  }
  async function typeText(text) {
    for (const ch of text) {
      await send('Input.dispatchKeyEvent', { type: 'keyDown', key: ch });
      await send('Input.dispatchKeyEvent', { type: 'char', text: ch, key: ch });
      await send('Input.dispatchKeyEvent', { type: 'keyUp', key: ch });
      await sleep(20);
    }
  }

  // Focus the input of a named field and clear it, the way a person does:
  // click, select-all, type.
  async function focusField(dotPath) {
    const found = await ev(`(function () {
      var h = document.querySelector('input[name="dot_path"][value=${JSON.stringify(dotPath)}]');
      if (!h) return null;
      var f = h.closest('form.inline-edit');
      var i = f && f.querySelector('input.edit-input');
      if (!i) return null;
      i.scrollIntoView({ block: 'center' });
      i.focus(); i.select();
      return { was: i.value, formAction: f.getAttribute('hx-post') };
    })()`);
    return found;
  }
  async function fieldValue(dotPath) {
    return ev(`(function () {
      var h = document.querySelector('input[name="dot_path"][value=${JSON.stringify(dotPath)}]');
      var f = h && h.closest('form.inline-edit');
      var i = f && f.querySelector('input.edit-input');
      return i ? i.value : null;
    })()`);
  }
  const trayCount = () => ev(`(function () {
    var t = document.getElementById('pending-tray');
    return t ? t.getAttribute('data-change-count') : null;
  })()`);

  async function attempt(url, dotPath, newValue, label) {
    await send('Page.navigate', { url: BASE + url });
    await sleep(4500);
    const before = await trayCount();
    const f = await focusField(dotPath);
    if (!f) return { skipped: 'no such editable field: ' + dotPath };
    posts.length = 0;
    errors.length = 0;
    await typeText(newValue);
    await key('Enter', { text: '\r', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 });
    await sleep(1800);
    const after = await trayCount();
    return {
      label: label, path: dotPath, was: f.was, typed: newValue,
      posts: posts.slice(), trayBefore: before, trayAfter: after,
      shown: await fieldValue(dotPath),
      exceptions: errors.slice()
    };
  }

  // ── the qubit inspector, full page ───────────────────────────────────────
  const a = await attempt('/qubit/' + QUBIT, 'qubits.' + QUBIT + '.anharmonicity',
                          '221000000', 'qubit inspector, full page');
  ok('Enter in the qubit inspector sends the edit',
     !a.skipped && a.posts.some(function (u) { return /\/edit$/.test(u); }), a);
  ok('…and the tray records it',
     !a.skipped && String(a.trayAfter) !== String(a.trayBefore), a);
  ok('…with no CSP exception at the press', !a.skipped && a.exceptions.length === 0,
     a.exceptions);

  // ── the same field reached the way a person reaches it ───────────────────
  await send('Page.navigate', { url: BASE + '/qubits' });
  await sleep(4500);
  const opened = await ev(`(function () {
    var r = document.querySelector('tr.clickable-row[data-qubit-id]');
    if (!r) return false;
    r.click();
    return true;
  })()`);
  await sleep(2500);
  let b = { skipped: 'no row to click' };
  if (opened) {
    const has = await ev(`!!document.querySelector('#inspector-pane form.inline-edit')`);
    if (has) {
      // A NUMERIC field on purpose. The first dot_path on this page is
      // `grid_location` — a string like "0,1" — and typing a number into it
      // opens a type-change confirm, which is a different conversation from
      // "does Enter send anything at all".
      const path = await ev(`(function () {
        var hs = document.querySelectorAll('#inspector-pane input[name="dot_path"]');
        for (var i = 0; i < hs.length; i++) {
          var f = hs[i].closest('form.inline-edit');
          var inp = f && f.querySelector('input.edit-input');
          if (!inp) continue;
          var v = String(inp.value).replace(/,/g, '');
          if (v && isFinite(Number(v))) return hs[i].value;
        }
        return null;
      })()`);
      const f = await focusField(path);
      if (f) {
        posts.length = 0; errors.length = 0;
        const before = await trayCount();
        await typeText('1234');
        await key('Enter', { text: '\r', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 });
        await sleep(1800);
        b = { label: 'htmx partial (the real path)', path: path, posts: posts.slice(),
              trayBefore: before, trayAfter: await trayCount(),
              exceptions: errors.slice() };
      }
    }
  }
  ok('Enter works on the inspector opened as a partial, too',
     !b.skipped && (b.posts || []).some(function (u) { return /\/edit$/.test(u); }), b);

  // ── the pair inspector ───────────────────────────────────────────────────
  let c = { skipped: 'no pair given' };
  if (PAIR) {
    const pairPath = await ev(`(function () { return null; })()`);
    void pairPath;
    await send('Page.navigate', { url: BASE + '/pair/' + PAIR });
    await sleep(4500);
    const first = await ev(`(function () {
      // Scoped to qubit_pairs.* on purpose: an unscoped query found
      // qubits.q1.grid_location left over from the previous page and the
      // "pair inspector" check quietly re-tested the QUBIT one (its POST went
      // to /qubit/q1/edit, which is what gave it away). No backticks in this
      // comment: it lives inside a template literal.
      var hs = document.querySelectorAll('input[name="dot_path"][value^="qubit_pairs."]');
      for (var i = 0; i < hs.length; i++) {
        var f = hs[i].closest('form.inline-edit');
        var inp = f && f.querySelector('input.edit-input');
        if (!inp) continue;
        var v = String(inp.value).replace(/,/g, '');
        if (v && isFinite(Number(v))) return hs[i].value;
      }
      return null;
    })()`);
    if (first) {
      const f = await focusField(first);
      if (f) {
        posts.length = 0; errors.length = 0;
        const before = await trayCount();
        await typeText('0.125');   // numeric field, chosen above
        await key('Enter', { text: '\r', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 });
        await sleep(1800);
        c = { label: 'pair inspector', path: first, posts: posts.slice(),
              trayBefore: before, trayAfter: await trayCount(),
              exceptions: errors.slice() };
      }
    }
  }
  ok('Enter works in the PAIR inspector',
     !c.skipped && (c.posts || []).some(function (u) { return /\/pair\/.*\/edit$/.test(u); })
     && /^qubit_pairs\./.test(String(c.path || '')), c);

  ok('a plain numeric edit needs no dialog', dialogs.length === 0, dialogs);

  fs.writeFileSync(OUT, JSON.stringify({ results, a, b, c, dialogs }, null, 1));
  const bad = results.filter(r => !r.pass);
  console.log('inspector edit: ' + (results.length - bad.length) + '/' + results.length);
  bad.forEach(x => console.log('  FAIL ' + x.name + ' ' + JSON.stringify(x.detail).slice(0, 400)));
  process.exit(0);
}
main().catch(e => { console.error(String(e && e.stack || e)); process.exit(1); });
