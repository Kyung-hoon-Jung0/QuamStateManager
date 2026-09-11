/* docs/182 in a REAL browser — the popup a person actually sees, and the Tags
 * button the customer asked for.
 *
 * jsdom can execute the row-building (tag_typeahead_selfcheck.cjs does), but it
 * cannot tell you whether the rows are on screen, whether the panel is wide
 * enough for `#15: note (냉각기)`, or whether a Tags button rendered between
 * Experiments and Sort. Those are the parts the customer will look at.
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
      const s = String((m.params.exceptionDetails.exception || {}).description
        || m.params.exceptionDetails.text);
      if (!/unsafe-eval/.test(s)) errors.push(s.slice(0, 200));
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

  // ── the Datasets page's Tags button ──────────────────────────────────────
  // The default is a claim about a FIRST visit, and this driver's own earlier
  // run clicks the toggle — which persists. Clear the key first, or the second
  // run measures the first run's click and calls it the default.
  await send('Page.navigate', { url: BASE + '/datasets' });
  await sleep(3000);
  await ev(`try { localStorage.removeItem('quam_tag_filter_collapsed'); } catch (e) {} 1`);
  await send('Page.navigate', { url: BASE + '/datasets' });
  await sleep(5000);

  const banner = await ev(`(function () {
    var tags = document.getElementById('tag-filter-toggle');
    var exp = document.getElementById('exp-filter-toggle');
    var sort = document.querySelector('#sort-filter-grid .exp-filter-toggle');
    function top(el) { return el ? Math.round(el.getBoundingClientRect().top) : null; }
    return {
      hasTags: !!tags,
      label: tags ? tags.textContent.replace(/\\s+/g, ' ').trim() : null,
      expTop: top(exp), tagTop: top(tags), sortTop: top(sort),
      collapsedByDefault: document.body.classList.contains('tag-filter-collapsed'),
      chipsVisible: (function () {
        var c = document.querySelector('.tag-filter-chips');
        return !!(c && c.offsetParent !== null);
      })(),
      chips: Array.prototype.map.call(
        document.querySelectorAll('.tag-filter-chips .tag-chip'),
        function (b) { return b.textContent.trim(); })
    };
  })()`);
  ok('the Datasets page has a Tags button', banner.hasTags, banner);
  ok('…labelled Tags', /Tags/.test(banner.label || ''), banner.label);
  ok('…between Experiments and Sort',
     banner.expTop != null && banner.tagTop != null && banner.sortTop != null
     && banner.expTop < banner.tagTop && banner.tagTop < banner.sortTop,
     { exp: banner.expTop, tags: banner.tagTop, sort: banner.sortTop });
  ok('…collapsed by default on Datasets, so it costs no vertical space',
     banner.collapsedByDefault && !banner.chipsVisible, banner);

  // one click opens it and the real tags are there
  await ev(`document.getElementById('tag-filter-toggle').click(); 1`);
  await sleep(400);
  const opened = await ev(`(function () {
    var c = document.querySelector('.tag-filter-chips');
    return { visible: !!(c && c.offsetParent !== null),
             chips: Array.prototype.map.call(
               document.querySelectorAll('.tag-filter-chips .tag-chip'),
               function (b) { return b.textContent.trim(); }) };
  })()`);
  ok('one click shows the chips', opened.visible, opened);
  ok('…and they are the chip’s real tags',
     opened.chips.indexOf('flagged') >= 0 && opened.chips.indexOf('baseline') >= 0,
     opened.chips);

  // ── the sidebar popup ────────────────────────────────────────────────────
  // Type for real: keydown/keypress/char/keyup per character, so the widget's
  // own input handling runs exactly as it does under a human.
  async function type(sel, text) {
    await ev(`(function(){var e=document.querySelector(${JSON.stringify(sel)});
      if(!e) return 0; e.focus(); e.value=''; return 1;})()`);
    for (const ch of text) {
      // `text` on BOTH keyDown and char inserts the character twice — the box
      // read 냉냉각각 on the first run of this driver, which is a stem that
      // matches nothing, and the popup correctly never opened.
      await send('Input.dispatchKeyEvent', { type: 'keyDown', key: ch });
      await send('Input.dispatchKeyEvent', { type: 'char', text: ch, key: ch });
      await send('Input.dispatchKeyEvent', { type: 'keyUp', key: ch });
      await sleep(35);
    }
  }

  const hasBox = await ev(`!!document.getElementById('sidebar-filter-input')`);
  ok('the sidebar search box is on the page', hasBox);

  await ev(`window.TagVocab && window.TagVocab.load(true); 1`);
  await sleep(900);

  await type('#sidebar-filter-input', 'flag');
  await sleep(700);
  const pop = await ev(`(function () {
    var p = document.getElementById(window.Typeahead.panelId);
    if (!p || p.hidden) return { open: false };
    var rows = Array.prototype.map.call(p.querySelectorAll('[role="option"], .sm-th-row'),
      function (r) { return r.textContent.replace(/\\s+/g, ' ').trim(); });
    var r = p.getBoundingClientRect();
    return { open: true, rows: rows, w: Math.round(r.width),
             overflow: p.scrollWidth > p.clientWidth + 1 };
  })()`);
  ok('typing opens the popup', pop.open, pop);
  ok('a tag shows as "#run: tag"',
     (pop.rows || []).some(function (s) { return /#1[13]: *flagged/.test(s); }),
     pop.rows);
  ok('the panel does not scroll sideways', !pop.overflow, { w: pop.w });

  // A note word. No note in this workspace contains "flag", so asking the tag
  // stem to also prove the note row would have been an assertion about the
  // fixture rather than the code.
  await type('#sidebar-filter-input', 'fridge');
  await sleep(700);
  const notePop = await ev(`(function () {
    var p = document.getElementById(window.Typeahead.panelId);
    if (!p || p.hidden) return { open: false };
    return { open: true, rows: Array.prototype.map.call(
      p.querySelectorAll('[role="option"], .sm-th-row'),
      function (r) { return r.textContent.replace(/\s+/g, ' ').trim(); }) };
  })()`);
  ok('a note shows as "#run: note (word)"',
     notePop.open && (notePop.rows || []).some(function (s) {
       return /#12: *note \(fridge\)/.test(s); }),
     notePop.rows);
  ok('…and no row carries the note’s other words',
     !(notePop.rows || []).some(function (s) {
       return /recalibrated|cycled/.test(s); }),
     notePop.rows);

  // Korean, which is how this customer writes notes
  await type('#sidebar-filter-input', '냉각');
  await sleep(700);
  const kor = await ev(`(function () {
    var p = document.getElementById(window.Typeahead.panelId);
    if (!p || p.hidden) return { open: false };
    return { open: true, rows: Array.prototype.map.call(
      p.querySelectorAll('[role="option"], .sm-th-row'),
      function (r) { return r.textContent.replace(/\\s+/g, ' ').trim(); }) };
  })()`);
  ok('a Korean note word is offered too',
     kor.open && (kor.rows || []).some(function (s) { return /#15: *note \(냉각기\)/.test(s); }),
     kor.rows);

  await send('Page.captureScreenshot', { format: 'png' }).then(function (r) {
    if (r && r.result && r.result.data) {
      fs.writeFileSync(OUT.replace(/\.json$/, '.png'),
        Buffer.from(r.result.data, 'base64'));
    }
  });

  fs.writeFileSync(OUT, JSON.stringify({ results, errors, banner, pop, notePop, kor }, null, 1));
  const bad = results.filter(r => !r.pass);
  console.log('tag search: ' + (results.length - bad.length) + '/' + results.length
              + '  console errors: ' + errors.length);
  bad.forEach(b => console.log('  FAIL ' + b.name + ' ' + JSON.stringify(b.detail).slice(0, 300)));
  errors.slice(0, 3).forEach(e => console.log('  ERR ' + e));
  process.exit(0);
}
main().catch(e => { console.error(String(e && e.stack || e)); process.exit(1); });
