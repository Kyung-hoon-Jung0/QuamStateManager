/* Real-browser harness for SM verification (docs/122).
 *
 * jsdom cannot see the class of bug this project keeps hitting: a stylesheet
 * rule beating a presentation attribute (docs/93 hit it three times), a
 * computed font-size, a Plotly axis that only exists after WebGL renders, an
 * IntersectionObserver that an occluded window silently stops. So this drives
 * the REAL Chrome already installed on this machine, against a REAL dev server
 * holding the REAL 20-qubit customer chip.
 *
 * Usage from a verification script:
 *
 *   const H = require('./harness.cjs');
 *   const { browser, page } = await H.open({ port: 5151 });
 *   await H.goto(page, '/bulk');
 *   const v = await page.$eval('#x', el => getComputedStyle(el).fontSize);
 *   await H.shot(page, 'bulk-grid');
 *   await browser.close();
 *
 * Non-negotiables baked in here:
 *  - `--disable-backgrounding-occluded-windows`: without it an occluded Chrome
 *    sets document.hidden and STOPS IntersectionObserver, so lazy-mounted
 *    sections never build and a verification reports a false negative
 *    (docs/118's measurement trap, learned the hard way).
 *  - every console error and pageerror is captured, because the failure mode
 *    that matters most here is "one uncaught TypeError and the whole module
 *    silently never defines".
 */
'use strict';

const fs = require('fs');
const path = require('path');
const puppeteer = require('puppeteer-core');

const CHROME = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const SHOTS = path.join(__dirname, '_shots');

async function open(opts) {
  const o = opts || {};
  if (!fs.existsSync(SHOTS)) fs.mkdirSync(SHOTS, { recursive: true });
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: o.headless === false ? false : 'new',
    args: [
      '--disable-backgrounding-occluded-windows',
      '--disable-renderer-backgrounding',
      '--disable-background-timer-throttling',
      '--no-sandbox',
      '--window-size=1600,1000',
    ],
    defaultViewport: { width: 1600, height: 1000 },
  });
  const page = await browser.newPage();
  page._errors = [];
  page._console = [];
  page.on('pageerror', (e) => page._errors.push(String(e && e.message || e)));
  page.on('console', (m) => {
    page._console.push(m.type() + ': ' + m.text());
    if (m.type() === 'error') page._errors.push('console: ' + m.text());
  });
  page.on('requestfailed', (r) => {
    // A 404 on a static asset is how a whole module goes missing.
    page._errors.push('requestfailed: ' + r.url() + ' ' + (r.failure() || {}).errorText);
  });
  // 8811+, deliberately: 5000-5150 sits inside a Windows/Hyper-V excluded port
  // range on this machine ("An attempt was made to access a socket in a way
  // forbidden by its access permissions"), which looks like the app failing.
  page._base = 'http://127.0.0.1:' + (o.port || 8811);
  return { browser, page };
}

async function goto(page, urlPath, waitMs) {
  const r = await page.goto(page._base + urlPath,
    { waitUntil: 'networkidle2', timeout: 60000 });
  await sleep(waitMs == null ? 400 : waitMs);
  return r && r.status();
}

function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

async function shot(page, name) {
  const f = path.join(SHOTS, name.replace(/[^\w.-]/g, '_') + '.png');
  await page.screenshot({ path: f, fullPage: false });
  return f;
}

/* The check that jsdom structurally cannot do: what the browser ACTUALLY
   computed, after the cascade, for a property. */
async function computed(page, selector, prop) {
  return page.$eval(selector, (el, p) => getComputedStyle(el)[p], prop);
}

/* Type into a real input the way a user does — focus, select-all, type, and
   fire the events the app listens for. Returns the value that survived. */
async function typeInto(page, selector, text, opts) {
  const o = opts || {};
  await page.focus(selector);
  await page.$eval(selector, (el) => { el.select ? el.select() : null; });
  await page.keyboard.down('Control'); await page.keyboard.press('KeyA');
  await page.keyboard.up('Control');
  await page.keyboard.type(text, { delay: o.delay == null ? 12 : o.delay });
  if (o.commit !== false) await page.keyboard.press('Enter');
  await sleep(o.settle == null ? 500 : o.settle);
  return page.$eval(selector, (el) => el.value);
}

function errors(page) {
  // Chrome's own noise that says nothing about the app.
  const IGNORE = [/favicon/i, /DevTools/i, /Autofill\./i];
  return page._errors.filter((e) => !IGNORE.some((re) => re.test(e)));
}

module.exports = { open, goto, shot, computed, typeInto, sleep, errors, SHOTS, CHROME };
