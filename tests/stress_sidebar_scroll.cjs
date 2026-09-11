/* A new run must not throw away where the reader was.
 *
 * Customer: "dataset 왼쪽 패널이 새로운 추가 실험이 생길때, 다시 리로드 되면서
 * 기존 스크롤하는게 refresh되버림 스크롤 위치를 잃어버림."
 *
 * Only a real browser can prove this: it is about scrollTop across an htmx
 * innerHTML swap of a list inside an overflow container. The run is created on
 * DISK during the run, so the version bump and the refetch are the real ones.
 *
 * argv[2]=out.json argv[3]=cdp argv[4]=base argv[5]=a run folder to clone
 */
const fs = require('fs');
const path = require('path');
const OUT = process.argv[2];
const CDP = process.argv[3];
const BASE = process.argv[4];
const CLONE_FROM = process.argv[5];
const results = [];
function ok(n, c, d) { results.push({ name: n, pass: !!c, detail: d === undefined ? null : d }); }

async function main() {
  const t = await (await fetch('http://127.0.0.1:' + CDP + '/json')).json();
  const page = t.find(x => x.type === 'page');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise(r => ws.onopen = r);
  let id = 0; const pend = new Map();
  ws.onmessage = e => { const m = JSON.parse(e.data); if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id); } };
  const send = (mm, p = {}) => new Promise(r => { const i = ++id; pend.set(i, r); ws.send(JSON.stringify({ id: i, method: mm, params: p })); });
  const ev = async x => {
    const rr = await send('Runtime.evaluate', { expression: x, awaitPromise: true, returnByValue: true });
    if (rr.result && rr.result.exceptionDetails) throw new Error(String((rr.result.exceptionDetails.exception || {}).description || '').slice(0, 250));
    return rr.result.result.value;
  };
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  await send('Runtime.enable'); await send('Page.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 1500, height: 1000, deviceScaleFactor: 1, mobile: false });
  await send('Page.navigate', { url: BASE + '/' });
  await sleep(6000);

  const rows = await ev(`document.querySelectorAll('#sidebar-tree [data-uid]').length`);
  ok('the tree has runs to scroll through', rows > 10, { rows: rows });
  if (rows <= 10) { fs.writeFileSync(OUT, JSON.stringify({ results }, null, 1)); console.log('too few rows'); process.exit(0); }

  // scroll well down the list and remember the row at the top edge
  await ev(`(function(){ var s=document.getElementById('sidebar'); s.scrollTop = 900; return 1; })()`);
  await sleep(400);
  const before = await ev(`(function(){
    var s = document.getElementById('sidebar'), tree = document.getElementById('sidebar-tree');
    var top = s.getBoundingClientRect().top;
    var rs = tree.querySelectorAll('[data-uid]');
    for (var i = 0; i < rs.length; i++) {
      var r = rs[i].getBoundingClientRect();
      if (r.bottom > top + 1) return { scrollTop: s.scrollTop, uid: rs[i].getAttribute('data-uid'),
                                       offset: Math.round(r.top - top), rows: rs.length };
    }
    return { scrollTop: s.scrollTop, uid: null, rows: rs.length };
  })()`);
  ok('…and we are scrolled down it', before.scrollTop > 200 && !!before.uid, before);

  // a REAL new run lands on disk, and is brought in through the door a person
  // uses: the sidebar's ↻. (`sm:runs-changed` wakes the DATASETS poll, not the
  // workspace tree poll — nudging that one made the whole check vacuous.)
  if (CLONE_FROM) {
    const parent = path.dirname(CLONE_FROM);
    const dst = path.join(parent, '#999999_stress_scroll_260911');
    fs.rmSync(dst, { recursive: true, force: true });
    fs.cpSync(CLONE_FROM, dst, { recursive: true });
    // …and it has to be genuinely the NEWEST, or it lands below the anchor and
    // the check proves nothing: the clone carried its source's node.json id.
    fs.writeFileSync(path.join(dst, 'node.json'), JSON.stringify(
      { id: 999999, name: 'stress_scroll', created_at: '2026-09-11T23:59:59' }));
    ok('a new run folder was created on disk', fs.existsSync(dst), dst);
    const clicked = await ev(`(function(){
      var b = document.querySelector('.btn-workspace-refresh');
      if (!b) return 'no refresh button';
      b.click(); return 'clicked';
    })()`);
    ok('the sidebar refresh is reachable', clicked === 'clicked', clicked);
    await sleep(9000);
  }

  const after = await ev(`(function(){
    var s = document.getElementById('sidebar'), tree = document.getElementById('sidebar-tree');
    var top = s.getBoundingClientRect().top;
    var el = tree.querySelector('[data-uid="' + ${JSON.stringify(before.uid)}.replace(/"/g,'\\\\"') + '"]');
    var r = el && el.getBoundingClientRect();
    return { scrollTop: s.scrollTop, found: !!el,
             offset: r ? Math.round(r.top - top) : null,
             rows: tree.querySelectorAll('[data-uid]').length };
  })()`);
  // STRICT: `>=` cannot tell "the list grew" from "nothing happened", and a
  // check that cannot tell those apart proves nothing about restoring a
  // scroll position across a swap.
  ok('the tree actually grew by the new run',
     after.rows > before.rows, { before: before.rows, after: after.rows });
  ok('the row we were reading is still there', after.found, after);
  ok('…and it is still at the same place on screen',
     after.found && Math.abs(after.offset - before.offset) <= 4,
     { before_offset: before.offset, after_offset: after.offset,
       before_scrollTop: before.scrollTop, after_scrollTop: after.scrollTop });
  // The row was held in place BECAUSE the scroll moved: a new run is inserted
  // above, so keeping the same scrollTop would have pushed it down.
  ok('…which took moving the scroll, not leaving it alone',
     after.scrollTop !== before.scrollTop,
     { before: before.scrollTop, after: after.scrollTop, rows_added: after.rows - before.rows });

  fs.writeFileSync(OUT, JSON.stringify({ results, before, after }, null, 1));
  const bad = results.filter(r => !r.pass);
  console.log('scroll checks: ' + (results.length - bad.length) + '/' + results.length);
  bad.forEach(b => console.log('  FAIL ' + b.name + ' ' + JSON.stringify(b.detail)));
  process.exit(0);
}
main().catch(e => { console.error(String(e && e.stack || e)); process.exit(1); });
