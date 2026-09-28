// scratch probe (not a pin): does leaving the big Json Tree through the
// SIDEBAR raise htmx:historyCacheError too? (base vs new, big30x)
'use strict';
const { open, sleep } = require('./cdp.cjs');
const PORT = +(process.env.PORT || 5364);
(async () => {
  const p = await open(`http://127.0.0.1:${PORT}/explorer`);
  for (let i = 0; i < 200; i++) { if (await p.ev(`document.querySelector('#explorer-tree-state .tree-node')?1:0`)) break; await sleep(200); }
  const mark = p.events.length;
  const r = await p.ev(`(function(){var a=document.querySelector('a[href="/pulses"][hx-get="/pulses"]'); var b=a.getBoundingClientRect(); return [b.left+b.width/2,b.top+b.height/2]})()`);
  const t0 = Date.now();
  await p.click(r[0], r[1]);
  for (let i = 0; i < 400; i++) { if (await p.ev(`(location.pathname==='/pulses' && document.querySelectorAll('tr[data-pulse-path]').length)?1:0`)) break; await sleep(100); }
  console.log('sidebar -> pulses table ms', Date.now() - t0);
  await sleep(1000);
  console.log('errors', JSON.stringify(p.errors(mark)));
  await p.close();
  process.exit(0);
})();
