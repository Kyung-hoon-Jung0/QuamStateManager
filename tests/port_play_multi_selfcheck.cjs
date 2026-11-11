'use strict';
// Port play "Play together": rows, the 3-port limit, duplicates, and the `extra` payload.
const fs = require('fs'), path = require('path'), assert = require('assert');
const {JSDOM} = require('jsdom');
const root = path.join(__dirname, '..'), staticDir = path.join(root, 'quam_state_manager/web/static');
const html = fs.readFileSync(path.join(root, 'quam_state_manager/web/templates/_generate.html'), 'utf8');
const dom = new JSDOM('<body><div id="table-pane">' + html + '</div></body>', {
  runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/generate'});
const w = dom.window;
const posted = [];
w.fetch = (u, o) => { if (o && o.body) posted.push({url: String(u), body: JSON.parse(o.body)}); return Promise.resolve({json: () => ({ok: false, error: 'mock refusal'})}); };
w.htmx = {ajax() {}, trigger() {}, process() {}, on() {}};
w.IntersectionObserver = w.ResizeObserver = class {observe() {} disconnect() {} unobserve() {}};
w.NumberInput = {fit() {}, attach() {}, format() {}, strip(s) {return String(s || '');}};
w.armPlainResize = () => {};
w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
const body = w.document.body; body.remove();
w.eval(fs.readFileSync(path.join(staticDir, 'port-play.js'), 'utf8'));
w.document.documentElement.appendChild(body);
for (const file of ['app.js', 'generate.js']) w.eval(fs.readFileSync(path.join(staticDir, file), 'utf8'));
const G = w.QuamGen, d = w.document;
G.state.spec.instruments.controllers = [1, 2].map(con => ({con, fems: [{slot: 1, fem: 'mw'}, {slot: 3, fem: 'lf'}]}));
G.state.spec.qubits = []; G.state.spec.lines = []; G.state.env = 'mock-python';
G._test.renderChassis();
d.getElementById('gen-port-play-toggle').click();
const rack = d.getElementById('gen-port-play-rack');
const CTRL = () => [1, 2].map(con => ({con, fems: [{slot: 1, fem: 'mw'}, {slot: 3, fem: 'lf'}]}));
function openAt(con, slot, port) {
  G.state.spec.instruments.controllers = CTRL(); G._test.renderChassis();
  if (d.getElementById('gen-port-play-panel').hidden) d.getElementById('gen-port-play-toggle').click();
  d.getElementById('gen-port-play-rack').querySelector(`.iw-port[data-con="${con}"][data-slot="${slot}"][data-port="${port}"][data-io="output"] button`).click();
  return d.getElementById('port-play-dialog');
}
function lastPost() { return posted[posted.length - 1]; }
let n = 0; const ok = (c, m) => { assert(c, m); n++; };

const play = async (p) => { p.querySelector('[data-play]').click(); await new Promise(r => setTimeout(r, 5)); };
(async () => {
// ---- LF popup ----
let popup = openAt(2, 3, 5);
const sec = popup.querySelector('[data-together]');
ok(sec && sec.classList.contains('ui-item'), 'Play together section');
const add = sec.querySelector('[data-add-port]');
popup.querySelector('[data-hwok]').checked = true;
await play(popup);
ok(lastPost().url === '/port-play/confirm' && !('extra' in lastPost().body) && !('delay_ns' in lastPost().body), 'no rows -> no extra key');
G.state.spec.instruments.controllers = CTRL();
add.click(); add.click();
ok(sec.querySelectorAll('[data-row]').length === 3, 'opener + two rows');
ok(!/At most/.test(add.title) && !add.disabled, 'S1 D-1: no 3-port cap');
const all = sec.querySelectorAll('[data-row]');
ok(all[0].querySelector('[data-pin]').value === 'con2/3/5', 'opener is row 1');
const rows = [all[1], all[2]];
ok(rows[0].querySelector('[data-pin]').value !== rows[1].querySelector('[data-pin]').value, 'rows start on different ports');
// first row -> an LF port with an offset, second row -> an MW port
const pick = (row, pin) => { const s = row.querySelector('[data-pin]'); s.value = pin; s.dispatchEvent(new w.Event('change')); };
pick(rows[0], 'con1/3/2'); rows[0].querySelector('[data-offset]').value = '0.2';
pick(rows[1], 'con1/1/4');
ok(rows[1].querySelector('[data-lo]') && !rows[1].querySelector('[data-offset]'), 'MW row shows tone fields');
await play(popup);
const extra = lastPost().body.extra;
ok(Array.isArray(extra) && extra.length === 2, 'payload carries two extra ports');
ok(extra[0].kind === 'lf' && extra[0].con === 'con1' && extra[0].slot === 3 && extra[0].port === 2 && extra[0].offset === 0.2, 'LF extra');
ok(extra[1].kind === 'mw' && extra[1].port === 4 && extra[1].lo_hz === 5e9 && extra[1].if_hz === 5e7, 'MW extra');
// D-10: LF + MW -> an LF delay per LF port, default from the MW band (band 1 -> 141 ns)
const delays = () => Array.from(popup.querySelectorAll('[data-lf-delay]'));
ok(delays().length === 2 && delays().every(i => !i.closest('[data-delay-wrap]').hidden && i.value === '141'), 'LF delays shown, 141 from band 1');
ok(!sec.querySelector('[data-delay-note]').hidden && /141 ns/.test(sec.querySelector('[data-delay-note]').textContent), 'delay note states the default');
ok(lastPost().body.delay_ns === 141 && extra[0].delay_ns === 141 && !('delay_ns' in extra[1]), 'delays sent for LF ports only');
const band = rows[1].querySelector('[data-band]'); band.value = '2'; band.dispatchEvent(new w.Event('change', {bubbles: true}));
ok(delays().every(i => i.value === '161'), 'band 2 -> 161 ns');
delays()[0].value = '150'; delays()[0].dispatchEvent(new w.Event('input', {bubbles: true}));
band.value = '1'; band.dispatchEvent(new w.Event('change', {bubbles: true}));
ok(delays()[0].value === '150' && delays()[1].value === '141', 'an edited delay is kept, the other follows the band');
await play(popup);
ok(lastPost().body.delay_ns === 150, 'edited delay sent');
const before = posted.length; delays()[1].value = '1.5';
await play(popup);
ok(/LF delay/.test(popup.querySelector('[data-message]').textContent) && posted.length === before, 'bad delay refused client-side');
delays()[1].value = '141';
// duplicate refused, no request sent
const count = posted.length;
pick(rows[1], 'con1/3/2');
await play(popup);
ok(/twice/.test(popup.querySelector('[data-message]').textContent) && posted.length === count, 'duplicate refused client-side');
rows[1].querySelector('[data-remove]').click();
ok(sec.querySelectorAll('[data-row]').length === 2 && !add.disabled, 'Remove drops a row');
await play(popup);
ok(lastPost().body.extra.length === 1 && !('delay_ns' in lastPost().body) && !('delay_ns' in lastPost().body.extra[0]), 'one LF extra after Remove, no delay');
ok(delays().every(i => i.closest('[data-delay-wrap]').hidden), 'LF-only play hides the delay');
rows[0].querySelector('[data-remove]').click();
await play(popup);
ok(!('extra' in lastPost().body), 'all removed -> no extra key');
w.sessionStorage.removeItem('quam_gen_port_play_set');
popup.querySelector('[data-close]').click();

// ---- MW popup ----
popup = openAt(1, 1, 4);
const msec = popup.querySelector('[data-together]');
ok(msec, 'MW popup has Play together');
msec.querySelector('[data-add-port]').click();
popup.querySelector('[data-hwok]').checked = true;
await play(popup);
ok(lastPost().body.kind === 'mw' && lastPost().body.extra.length === 1, 'MW play with one extra');
ok(!msec.innerHTML.includes('style='), 'no inline styles');
popup.querySelector('[data-close]').click();
console.log('port play multi selfcheck ok (' + n + ' assertions)'); process.exit(0);
})().catch(e => { console.error(e); process.exit(1); });
