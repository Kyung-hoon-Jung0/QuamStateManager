const H = require('D:/work/statemanager-cfb/tests/browser/harness.cjs');
const SEL = 'input[data-dot-path="qubits.q2.z.opx_output.output_mode"]';

(async () => {
  const { browser, page } = await H.open({ port: 8822 });
  const out = { steps: [] };
  const net = [];
  page.on('response', r => {
    const u = r.url();
    if (/\/field\/edit|\/state\/tray|\/undo|\/redo|\/field\/peek/.test(u)) net.push(r.status() + ' ' + u.replace('http://127.0.0.1:8822', ''));
  });
  out.status = await H.goto(page, '/', 3000);
  await page.click('a[hx-get="/bulk"]');
  await H.sleep(9000);

  const info0 = await page.$eval(SEL, el => ({ v: el.value, cls: el.className, outer: el.outerHTML.slice(0, 300) }));
  out.before = info0;

  // scroll into view + click like a user
  await page.$eval(SEL, el => el.scrollIntoView({ block: 'center', inline: 'center' }));
  await H.sleep(600);
  await page.click(SEL);
  await H.sleep(300);
  out.focusedIsCell = await page.evaluate(s => document.activeElement === document.querySelector(s), SEL);
  out.shotFocused = await H.shot(page, 'le3-focused');

  // select all + type "direct" + Enter
  await page.keyboard.down('Control'); await page.keyboard.press('KeyA'); await page.keyboard.up('Control');
  await page.keyboard.type('direct', { delay: 40 });
  const midTyping = await page.$eval(SEL, el => el.value);
  await page.keyboard.press('Enter');
  await H.sleep(1500);
  out.afterTypeDirect = await page.evaluate(s => {
    const el = document.querySelector(s);
    return el ? { value: el.value, cls: el.className, orig: el.getAttribute('data-orig'), baseline: el.getAttribute('data-baseline') } : 'GONE';
  }, SEL);
  out.midTyping = midTyping;
  out.trayCount = await page.evaluate(() => {
    const t = document.querySelector('#pending-tray');
    return t ? t.textContent.replace(/\s+/g, ' ').trim().slice(0, 300) : null;
  });
  out.shotAfter = await H.shot(page, 'le3-after-direct');
  out.errors = H.errors(page);
  out.net = net.slice();
  console.log(JSON.stringify(out, null, 1));
  await browser.close();
})();
