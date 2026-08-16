const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
(async () => {
  const { browser, page } = await H.open({ port: 8866 });
  await H.goto(page, '/', 3000);
  // warm: one visit first, then profile the second
  await page.evaluate(() => document.querySelector('a[hx-get="/bulk"]').click());
  await page.waitForFunction(() => document.querySelectorAll('.bulk-cell').length > 4000, { timeout: 90000 }).catch(() => {});
  await H.sleep(2000);
  await page.evaluate(() => document.querySelector('a[hx-get="/pulses"]').click());
  await H.sleep(1500);

  const client = await page.target().createCDPSession();
  await client.send('Profiler.enable');
  await client.send('Profiler.setSamplingInterval', { interval: 200 });
  await client.send('Profiler.start');
  await page.evaluate(() => document.querySelector('a[hx-get="/bulk"]').click());
  await page.waitForFunction(() => document.querySelectorAll('.bulk-cell').length > 4000, { timeout: 90000 }).catch(() => {});
  await H.sleep(1500);
  const { profile } = await client.send('Profiler.stop');

  // aggregate self time per function
  const byId = {};
  profile.nodes.forEach(n => { byId[n.id] = n; });
  const self = {};
  const total = profile.samples.length;
  profile.samples.forEach(id => { self[id] = (self[id] || 0) + 1; });
  const dur = (profile.endTime - profile.startTime) / 1000; // ms
  const perSample = dur / total;
  const rows = Object.entries(self).map(([id, c]) => {
    const n = byId[id]; const cf = n ? n.callFrame : {};
    return {
      fn: (cf.functionName || '(anon)'),
      url: (cf.url || '').replace('http://127.0.0.1:8866', '') + ':' + (cf.lineNumber + 1),
      ms: Math.round(c * perSample),
    };
  }).filter(r => r.ms > 20).sort((a, b) => b.ms - a.ms).slice(0, 25);

  console.log(JSON.stringify({ profiledMs: Math.round(dur), samples: total, top: rows }, null, 1));
  await browser.close();
})();
