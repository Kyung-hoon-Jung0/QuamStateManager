/* QA liveedit F17 -- Back from Flat View returns to Table View.
 *
 * Table View / Flat View are two panes of /bulk (all-values.js switchPane).
 * A pane switch made no history entry, so Back from Flat View left /bulk
 * altogether (real Chrome: / -> /bulk -> Flat View -> Back landed on /).
 * A user switch now pushes an entry carrying the pane beside htmx's marker,
 * and popping between two entries of the SAME page switches the pane in
 * place without handing the event to htmx (which would re-GET the page);
 * anything else still goes to htmx.
 *
 * Drives the REAL all-values.js under jsdom.
 * Run: node tests/bulk_pane_history_selfcheck.cjs   (needs jsdom)
 */
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); } catch (e) { console.error('jsdom not installed'); process.exit(2); }

const AV_JS = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'all-values.js'), 'utf8');
let fails = 0;
function ok(c, m) { if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); } }
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 20));

function world() {
  const dom = new JSDOM('<!doctype html><html><body><div id="table-pane">'
    + '<div class="bulk-segmented"><button type="button" class="bulk-seg active" data-pane="grid">Table View</button>'
    + '<button type="button" class="bulk-seg" data-pane="allvalues">Flat View</button></div>'
    + '<div class="bulk-pane" data-bulk-pane="grid">grid</div>'
    + '<div class="bulk-pane allvals-page" data-bulk-pane="allvalues" hidden><div id="av-body"></div></div>'
    + '</div></body></html>', { url: 'http://localhost/bulk?vw=800', runScripts: 'outside-only', pretendToBeVisual: true });
  const w = dom.window;
  w.fetch = () => new w.Promise(() => {});        // Flat's lazy load never answers here
  w.history.replaceState({ htmx: true }, '');      // htmx's marker on the entry
  const htmxPops = [];
  w.onpopstate = function (e) { htmxPops.push(e.state); };   // htmx's own handler (property)
  w.eval(AV_JS);
  w.AllValues.setup();
  return { w, htmxPops, pane: () => w.document.querySelector('.bulk-seg.active').getAttribute('data-pane') };
}

(async function main() {
  {
    const { w, htmxPops, pane } = world();
    ok(w.history.state.htmx === true && w.history.state.smBulkPane === 'grid',
       'the /bulk entry records its pane beside htmx\'s marker (' + JSON.stringify(w.history.state) + ')');
    const len0 = w.history.length;
    w.document.querySelector('.bulk-seg[data-pane="allvalues"]').click();
    ok(pane() === 'allvalues', 'setup: Flat View is shown');
    ok(w.history.length === len0 + 1 && w.history.state.smBulkPane === 'allvalues' && w.history.state.htmx === true,
       'F17: pressing Flat View makes a history entry (' + w.history.length + ', ' + JSON.stringify(w.history.state) + ')');
    w.history.back();
    await tick(50);
    ok(pane() === 'grid' && !w.document.querySelector('[data-bulk-pane="grid"]').hidden,
       'F17: Back from Flat View returns to Table View (' + pane() + ')');
    ok(htmxPops.length === 0, 'F17: ...in place, without handing the pop to htmx (' + htmxPops.length + ')');
    w.history.forward();
    await tick(50);
    ok(pane() === 'allvalues', 'F17: Forward returns to Flat View (' + pane() + ')');
    ok(w.history.length === len0 + 1, 'F17: Back / Forward add no entries (' + w.history.length + ')');
    // a restore-from-setup (the persisted pane) is not a user switch: no entry
    const len1 = w.history.length;
    w.AllValues.switchPane('grid', true);
    ok(w.history.length === len1, 'a restoring switch pushes nothing');
  }
  {
    // another page on screen (the /bulk markup is gone): the pop is htmx's
    const { w, htmxPops } = world();
    w.document.querySelector('.bulk-seg[data-pane="allvalues"]').click();
    w.document.getElementById('table-pane').innerHTML = '<p>diagnostics</p>';
    w.history.back();
    await tick(50);
    ok(htmxPops.length === 1, 'control: with /bulk no longer on screen the pop goes to htmx (' + htmxPops.length + ')');
  }
  console.log(fails ? 'FAILED ' + fails : 'bulk_pane_history_selfcheck: all checks passed');
  process.exit(fails ? 1 : 0);
})();
