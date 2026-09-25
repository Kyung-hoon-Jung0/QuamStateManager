/* QA datasets-r2-26 -- a data folder added/removed in the sidebar reaches an
 * open Datasets page. /workspace/add|remove answer with HX-Trigger
 * `workspaceRootsChanged` ({removed: [folder keys]}); the REAL
 * dataset-virtual.js (under jsdom) re-reads #table-pane on its date tab, puts
 * the search text back after the swap, and closes a run left open from a
 * removed folder. With no Datasets table mounted it asks for nothing.
 *
 * Run: node tests/ds_roots_changed_selfcheck.cjs
 *      (driven by tests/test_workspace_remove_datasets.py)
 */
'use strict';

const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) { console.error('jsdom not installed'); process.exit(2); }

const SRC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static',
                      'dataset-virtual.js');
let fails = 0;
function ok(cond, msg) {
  if (cond) console.log('ok - ' + msg);
  else { console.error('FAIL: ' + msg); fails++; }
}
function tick(ms) { return new Promise(r => setTimeout(r, ms || 30)); }

const TABLE = `<input type="search" id="dataset-search" value="">
  <input type="hidden" id="ds-active-date" name="date" value="2026-09-06">
  <script id="ds-rows-data" data-view="VIEW">[]</script>
  <table><tbody id="datasets-tbody"></tbody></table>`;

function boot(opts) {
  const o = opts || {};
  const dom = new JSDOM(`<!doctype html><html><body>
      <div id="table-pane">${o.table === false ? '<p>not datasets</p>'
                            : TABLE.replace('VIEW', o.view || 'datasets')}</div>
      <div id="inspector-pane">${o.detailUid
        ? '<div id="ds-detail-root" data-uid="' + o.detailUid + '">run</div>' : ''}</div>
    </body></html>`, { url: 'http://localhost/datasets', pretendToBeVisual: true });
  const w = dom.window;
  global.window = w;
  global.CSS = w.CSS;
  global.document = w.document;
  global.Event = w.Event;
  global.CustomEvent = w.CustomEvent;
  global.KeyboardEvent = w.KeyboardEvent;
  global.localStorage = w.localStorage;
  global.requestAnimationFrame = cb => setTimeout(cb, 0);
  const stub = () => new Promise(() => {});
  w.fetch = stub; global.fetch = stub;
  w.__ajax = [];
  w.htmx = {
    ajax: (m, url, cfg) => {
      w.__ajax.push({ m: m, url: url, cfg: cfg });
      // the server's fresh table: the box comes back EMPTY (a GET never
      // reads keep_q -- QA F9)
      w.document.getElementById('table-pane').innerHTML = TABLE.replace('VIEW', o.view || 'datasets');
      return Promise.resolve();
    },
  };
  global.htmx = w.htmx;
  w.__closed = 0; w.__toasts = [];
  w.closeInspector = () => { w.__closed++; w.document.getElementById('inspector-pane').innerHTML = ''; };
  w.showToast = (m, l) => w.__toasts.push(String(m));
  w.eval(fs.readFileSync(SRC, 'utf8'));
  return w;
}
function fire(w, removed) {
  w.document.getElementById('table-pane').dispatchEvent(new w.CustomEvent(
    'workspaceRootsChanged', { bubbles: true, detail: { removed: removed } }));
}

(async () => {
  {
    const w = boot({ detailUid: 'fA:40' });
    w.document.getElementById('dataset-search').value = 'rabi';
    fire(w, ['fA']);
    await tick();
    ok(w.__ajax.length === 1 && w.__ajax[0].m === 'GET' && w.__ajax[0].url === '/datasets',
       'r2-26: a removed folder makes the open Datasets table re-read itself: '
       + JSON.stringify(w.__ajax.map(a => a.url)));
    ok(w.__ajax[0] && w.__ajax[0].cfg.target === '#table-pane'
       && w.__ajax[0].cfg.values.date === '2026-09-06',
       'r2-26: ... into #table-pane, on the date tab it was on');
    ok(w.__ajax[0] && w.__ajax[0].cfg.source === '#table-pane',
       'r2-26 (review): ... sourced on #table-pane, not queued on <body>');
    ok(w.document.getElementById('dataset-search').value === 'rabi',
       'r2-26: ... and the search text is put back after the swap');
    ok(w.__closed === 1 && w.__toasts.length === 1 && /folder was removed/.test(w.__toasts[0]),
       'r2-26: the run left open from the removed folder is closed, with a word why');
  }
  {
    const w = boot({ detailUid: 'fB:40' });
    fire(w, ['fA']);
    await tick();
    ok(w.__closed === 0, 'a run from a folder still in the workspace stays open');
    ok(w.__ajax.length === 1, '... while the table still re-reads');
  }
  {
    const w = boot({ view: 'collections' });
    fire(w, []);                            // an ADD: nothing removed
    await tick();
    ok(w.__ajax.length === 1 && w.__ajax[0].url === '/collections',
       'Collections re-reads /collections (an added folder reaches it too)');
  }
  {
    const w = boot({ table: false, detailUid: 'fA:40' });
    fire(w, ['fA']);
    await tick();
    ok(w.__ajax.length === 0, 'no Datasets table mounted -> no re-read');
    ok(w.__closed === 1, '... but a detail from the removed folder still closes');
  }
  process.exit(fails ? 1 : 0);
})();
