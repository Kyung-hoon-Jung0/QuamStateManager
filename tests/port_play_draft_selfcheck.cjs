'use strict';
// /port-play draft source: the page's own script, port-play.js and app.js under jsdom.
// argv[2] = page fragment with no chip, argv[3] = page fragment with a chip.
const fs = require('fs'), path = require('path'), assert = require('assert');
const {JSDOM} = require('jsdom');
const root = path.join(__dirname, '..'), staticDir = path.join(root, 'quam_state_manager/web/static');
let n = 0;
const ok = (c, m) => { assert(c, m); n++; };
const DRAFT = {v: 2, env: 'C:/draft/python.exe', spec: {
  instruments: {controllers: [{con: 1, fems: [{slot: 2, fem: 'mw'}, {slot: 5, fem: 'lf'}, {slot: 6, fem: 'lf'}]}]},
  network: {host: '10.9.9.9', cluster_name: 'draftc'}}};
const SCRIPT_RE = /<script>([\s\S]*?)<\/script>/;

function page(file, draft) {
  const html = fs.readFileSync(file, 'utf8');
  const dom = new JSDOM('<body><div id="table-pane">' + html.replace(SCRIPT_RE, '') + '</div></body>',
    {runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/port-play'});
  const w = dom.window;
  if (draft !== undefined) w.sessionStorage.setItem('quam_generate_draft', typeof draft === 'string' ? draft : JSON.stringify(draft));
  const before = w.sessionStorage.getItem('quam_generate_draft');
  w.fetch = () => new Promise(() => {});
  w.htmx = {ajax() {}, trigger() {}, process() {}, on() {}};
  w.IntersectionObserver = w.ResizeObserver = class {observe() {} disconnect() {} unobserve() {}};
  w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  w.eval(fs.readFileSync(path.join(staticDir, 'app.js'), 'utf8'));
  w.eval(fs.readFileSync(path.join(staticDir, 'port-play.js'), 'utf8'));
  const opened = [];
  w.PortPlay.open = (port, source) => opened.push({port, source});
  w.Bundles = undefined;   // the page bundle is already evaluated above
  w.eval(SCRIPT_RE.exec(html)[1]);
  const d = w.document;
  const fems = () => Array.from(d.querySelectorAll('#port-play-rack .iw-port[data-play="1"]'))
    .map(c => c.getAttribute('data-slot')).filter((v, i, a) => a.indexOf(v) === i).sort().join(',');
  return {w, d, opened, fems, before};
}
const visible = el => el && !el.hidden;
const firstPort = P => P.d.querySelector('#port-play-rack .iw-port[data-play="1"]');

// No chip, no draft: the dead-end sentence, no rack.
let P = page(process.argv[2]);
ok(visible(P.d.querySelector('[data-pp-nochip]')) && P.d.getElementById('pp-body').hidden, 'no chip no draft -> sentence only');

// No chip + valid draft: the rack is drawn from the draft's spec, the draft is the only source.
P = page(process.argv[2], DRAFT);
ok(!P.d.getElementById('pp-body').hidden && P.d.querySelector('[data-pp-nochip]').hidden, 'draft replaces the dead end');
ok(P.fems() === '2,5,6', 'rack from the draft FEMs: ' + P.fems());
ok(!P.d.querySelector('[data-pp-source-row]').hidden, 'source row shown');
ok(Array.from(P.d.querySelectorAll('#pp-source option')).map(o => o.value).join() === 'draft', 'only the draft offered');
ok(P.d.getElementById('pp-host').value === '10.9.9.9' && P.d.getElementById('pp-cluster').value === 'draftc', 'host prefilled from draft');
firstPort(P).dispatchEvent(new P.w.MouseEvent('click', {bubbles: true}));
ok(P.opened.length === 1, 'a port click opens the popup');
const src = P.opened[0].source;
ok(src.python() === 'C:/draft/python.exe', 'python() = draft env');
P.d.getElementById('pp-host').value = '1.2.3.4';
const sp = src.spec();
ok(sp.network.host === '1.2.3.4' && sp.instruments.controllers[0].fems.length === 3, 'spec() = draft spec + host field');
ok(src.chipPort('con1/2/1') === null, 'no chip prefill in draft mode');
ok(P.w.sessionStorage.getItem('quam_generate_draft') === P.before, 'the stored draft is never written');

// Draft without env: python() falls back to the page select.
P = page(process.argv[2], Object.assign({}, DRAFT, {env: null}));
firstPort(P).dispatchEvent(new P.w.MouseEvent('click', {bubbles: true}));
ok(P.opened[0].source.python() === P.d.getElementById('pp-env').value, 'no draft env -> page select');

// Corrupt or wrong-version drafts are ignored (and left in place).
for (const bad of ['{not json', JSON.stringify(Object.assign({}, DRAFT, {v: 1})), JSON.stringify({v: 2})]) {
  P = page(process.argv[2], bad);
  ok(P.d.getElementById('pp-body').hidden && visible(P.d.querySelector('[data-pp-nochip]')), 'bad draft ignored: ' + bad.slice(0, 20));
  ok(P.w.sessionStorage.getItem('quam_generate_draft') === bad, 'bad draft not removed');
}

// Chip + draft: a choice, chip first; switching redraws from the draft and back.
P = page(process.argv[3], DRAFT);
const pick = P.d.getElementById('pp-source');
ok(!P.d.querySelector('[data-pp-source-row]').hidden && pick.value === 'chip', 'chip + draft -> choice, chip default');
ok(P.fems() === '1,3', 'chip rack: ' + P.fems());
ok(P.d.getElementById('pp-host').value === '10.0.0.7', 'chip host');
pick.value = 'draft'; pick.dispatchEvent(new P.w.Event('change'));
ok(P.fems() === '2,5,6' && P.d.getElementById('pp-host').value === '10.9.9.9', 'switch to draft redraws: ' + P.fems());
ok(!P.d.querySelector('[data-pp-draft-note]').hidden, 'draft note shown');
pick.value = 'chip'; pick.dispatchEvent(new P.w.Event('change'));
ok(P.fems() === '1,3' && P.d.getElementById('pp-host').value === '10.0.0.7', 'switch back to chip');

// Chip without draft: no choice row.
P = page(process.argv[3]);
ok(P.d.querySelector('[data-pp-source-row]').hidden && P.fems() === '1,3', 'chip only -> no choice');

console.log('port play draft selfcheck ok (' + n + ' assertions)');
process.exit(0);
