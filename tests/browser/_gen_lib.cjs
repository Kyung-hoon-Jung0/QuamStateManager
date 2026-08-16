const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');

async function evClick(page, sel) {
  return page.evaluate((s) => {
    const e = document.querySelector(s);
    if (!e) return 'MISSING ' + s;
    e.scrollIntoView({ block: 'center' });
    e.click();
    return 'ok';
  }, sel);
}
async function setInput(page, sel, val) {
  return page.evaluate((s, v) => {
    const e = document.querySelector(s);
    if (!e) return 'MISSING ' + s;
    e.focus();
    e.value = v;
    e.dispatchEvent(new Event('input', { bubbles: true }));
    e.dispatchEvent(new Event('change', { bubbles: true }));
    return 'ok';
  }, sel, val);
}
async function info(page) {
  return page.evaluate(() => {
    const p = document.querySelector('.gen-panel.active');
    const m = document.querySelector('#gen-message');
    return {
      step: p ? p.getAttribute('data-step') : null,
      progress: (document.querySelector('#gen-progress') || {}).textContent,
      msg: m && !m.hidden ? m.innerText.trim().slice(0, 300) : null,
    };
  });
}
async function setFem(page, con, slot, kind) {
  await page.evaluate((c, s) => {
    const e = document.querySelector(`#gen-chassis-list [data-con="${c}"][data-slot="${s}"]`);
    e.scrollIntoView({ block: 'center' }); e.click();
  }, con, slot);
  await H.sleep(250);
  await page.evaluate((k) => {
    const btns = [...document.querySelectorAll('#gen-slot-menu button')];
    const b = btns.find(x => x.textContent.trim() === k);
    if (b) b.click();
  }, kind);
  await H.sleep(250);
}
// Walk to step 4 with a small chip
async function walkToQubits(page, opts) {
  const o = opts || {};
  await H.goto(page, '/', 2500);
  await page.click('a[hx-get="/generate"]');
  await H.sleep(6000);
  await evClick(page, '.gen-env-row[data-python="D:\\\\miniconda3\\\\envs\\\\cqt\\\\python.exe"]');
  await H.sleep(2000);
  await evClick(page, '#gen-next'); await H.sleep(700);
  await setInput(page, '#gen-net-host', '192.168.88.10');
  await setInput(page, '#gen-net-cluster', 'probe_cluster');
  await evClick(page, '#gen-next'); await H.sleep(1200);
  await setInput(page, '#gen-chassis-count', String(o.chassis || 1));
  await H.sleep(600);
  await setFem(page, 1, 1, 'MW-FEM');
  await setFem(page, 1, 2, 'MW-FEM');
  await setFem(page, 1, 3, 'LF-FEM');
  await evClick(page, '#gen-next'); await H.sleep(1200);
  return info(page);
}
module.exports = { H, evClick, setInput, info, setFem, walkToQubits };
