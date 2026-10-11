/* v1.3.0 B2: a Chip Status Overview tile's sub line ("med 99.90% . 96.15%-99.94% . (21)")
 * wrapped INSIDE its range at 1280 px ("96.15%-" / "99.94%"). metricTile now wraps each
 * piece (a stat, the range, the count, the excluded tail) in one nowrap span, so a narrow
 * tile breaks between pieces. This drives the shipped metricTile source with stubbed
 * helpers: every piece is one span, the colour still receives the tile's band object
 * (a local named like the parameter once shadowed it), and min/max tiles keep their form.
 *   node tests/topo_tile_sub_selfcheck.cjs
 */
'use strict';
const fs = require('fs');
const path = require('path');

const src = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'chip-status.js'), 'utf8');
let fails = 0, n = 0;
function ok(c, m) { n++; if (!c) { fails++; console.log('FAIL', m); } }

const start = src.indexOf('function metricTile(');
ok(start > 0, 'metricTile is in chip-status.js');
let depth = 0, end = -1;
for (let i = src.indexOf('{', start); i < src.length; i++) {
  if (src[i] === '{') depth++;
  else if (src[i] === '}') { depth--; if (depth === 0) { end = i + 1; break; } }
}
const fnSrc = src.slice(start, end);
const seen = [];
// eslint-disable-next-line no-new-func
const metricTile = new Function('cardColor', fnSrc + '\nreturn metricTile;')(
  function (v, range) { seen.push(range); return 'c'; });
const fmt = x => x.toFixed(2) + '%';
const band = { lo: 90, hi: 99 };
const K = t => '<span class="tcs-k">' + t + '</span>';

const a = metricTile('1Q', { count: 21, median: 99.9, min: 96.15, max: 99.94, avg: 99.61, bad: 1 }, fmt, band, 'f1q', 'avg');
ok(a.sub.indexOf(K('96.15%–99.94%')) >= 0, 'the range is one unbreakable piece: ' + a.sub);
ok(a.sub.indexOf(K('med 99.90%')) >= 0, 'the median is one piece: ' + a.sub);
ok(a.sub.indexOf(K('(21)')) >= 0, 'the count is one piece: ' + a.sub);
ok(a.sub.indexOf(K('1 excluded')) >= 0, 'the excluded tail is one piece: ' + a.sub);
ok(seen[0] === band, 'the colour is computed against the tile band, not a local string: ' + JSON.stringify(seen[0]));
ok(a.value === '99.61%' && a.color === 'c', 'value + colour unchanged');

const b = metricTile('T1', { count: 3, median: 40, min: 30, max: 50, avg: 41 }, fmt, band, 't1', 'min');
ok(b.sub === K('med 40.00%') + '  ·  ' + K('avg 41.00%') + '  ·  ' + K('(3)'), 'a min tile states median, avg, count: ' + b.sub);
ok(seen[1] === band, 'min tile colour uses the band');

const c = metricTile('X', { count: 2, median: 1, min: 0, max: 2, avg: 1 }, fmt, band, 'x', 'median');
ok(c.sub.indexOf(K('avg 1.00%')) === 0 && c.sub.indexOf(K('0.00%–2.00%')) > 0, 'a median tile leads with avg + range: ' + c.sub);

const e = metricTile('E', { count: 0, bad: 2 }, fmt, band, 'e', 'avg');
ok(e.muted === true && e.sub === '2 excluded · nothing usable', 'an empty tile keeps its plain line');

if (fails) { console.log(fails + ' of ' + n + ' FAILED'); process.exit(1); }
console.log('topo_tile_sub_selfcheck: ' + n + ' assertions passed');
