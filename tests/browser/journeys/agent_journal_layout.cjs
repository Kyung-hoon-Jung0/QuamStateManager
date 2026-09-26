/* Calibration log layout, measured: the author select's text clears its arrow,
 * the loose-lines label is not clipped, the empty line does not contradict
 * what is below it. Then the day arrows, search, Raw .md, reload, Back.
 *   SM_CDP_PORT=9413 node agent_journal_layout.cjs 5113 OUTDIR WIDTH
 */
'use strict';
const { open, sleep } = require('./cdp.cjs');
const [SM, OUT, W] = [process.argv[2], process.argv[3], +process.argv[4] || 1366];
const MEASURE = `(() => {
  const sel = document.getElementById('jr-author'); const cs = sel && getComputedStyle(sel);
  const c = document.createElement('canvas').getContext('2d'); if (cs) c.font = cs.font;
  const txt = sel ? c.measureText(sel.options[sel.selectedIndex].text).width : 0;
  const room = sel ? sel.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight) : 0;
  const loose = document.querySelector('.jr-loose > summary .jr-family');
  const empty = document.querySelector('.jr-empty');
  return JSON.stringify({ selectText: Math.round(txt), selectRoom: Math.round(room), padR: cs && cs.paddingRight,
    looseClipped: loose ? loose.scrollWidth > loose.clientWidth + 1 : null, looseText: loose && loose.textContent,
    empty: empty && empty.textContent.trim(), sw: document.documentElement.scrollWidth, iw: innerWidth });
})()`;

(async () => {
  const p = await open(`http://127.0.0.1:${SM}/journal`, W, 900);
  await sleep(2500);
  console.log('measure', await p.ev(MEASURE));
  await p.shot(`${OUT}/journal_${W}.png`);
  const click = async (sel) => { const r = await p.ev(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e) return null; e.scrollIntoView({block:'center'}); const b = e.getBoundingClientRect(); return JSON.stringify([b.x + b.width/2, b.y + b.height/2]); })()`); if (!r) return false; const xy = JSON.parse(r); await p.click(xy[0], xy[1]); return true; };
  // open the loose lines
  console.log('open loose', await click('.jr-loose > summary')); await sleep(500);
  console.log('  lines', await p.ev(`document.querySelectorAll('.jr-loose[open] .jr-lines li').length`));
  // previous day, then next day (back to today)
  console.log('prev', await click('#jr-body button[title], .jr-daynav button, button[aria-label*=revious]')); await sleep(1500);
  console.log('  day', await p.ev(`document.getElementById('jr-day').value`), '| empty:', await p.ev(`(document.querySelector('.jr-empty') || {}).textContent`));
  // search box: type a qubit
  await click('#jr-q'); await p.send('Input.insertText', { text: 'q7' }); await sleep(1500);
  console.log('search q7 ->', await p.ev(`(document.querySelector('.jr-empty') || {textContent: 'cards shown'}).textContent.trim()`));
  console.log('raw', await click('a[href*="journal/raw"], #jr-raw-link, a.jr-raw')); await sleep(1200);
  console.log('  raw shown', await p.ev(`(document.getElementById('jr-raw') || {}).innerText ? document.getElementById('jr-raw').innerText.length : 0`));
  await p.send('Page.reload'); await sleep(2500);
  console.log('reload', await p.ev(MEASURE));
  const e = p.errors(); if (e.length) console.log('ERRORS', e.join(' ## '));
  await p.close(); process.exit(0);
})();
