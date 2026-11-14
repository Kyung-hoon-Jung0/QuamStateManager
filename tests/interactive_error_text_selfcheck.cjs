/* A figure tile whose route answers {error: "..."} shows that text as TEXT: markup in it
 * is never parsed. Pinned under jsdom with the real app.js (_fetchInteractiveFig).
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const SRC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js');
let failures = 0;
function check(name, cond, detail) {
  if (cond) console.log('  ok  ' + name);
  else { failures++; console.error('FAIL  ' + name + (detail ? ' -- ' + detail : '')); }
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function tile(errorValue) {
  const dom = new JSDOM('<!doctype html><html><body><div id="c"><div id="t"></div></div></body></html>',
    { runScripts: 'outside-only', url: 'http://localhost/' });
  const w = dom.window;
  w.fetch = function () {
    return Promise.resolve({ json: () => Promise.resolve({ error: errorValue }) });
  };
  w.matchMedia = w.matchMedia || function () { return { matches: false, addListener() {}, removeListener() {} }; };
  w.requestAnimationFrame = w.requestAnimationFrame || function (f) { return setTimeout(f, 0); };
  global.window = w; global.document = w.document;
  w.eval(fs.readFileSync(SRC, 'utf8'));
  const div = w.document.getElementById('t');
  w.eval('_fetchInteractiveFig(document.getElementById("t"), "r1", "fig", 0, null)');
  await sleep(30);
  return div;
}

(async function main() {
  let div = await tile('Not drawn: <img src=x onerror=alert(1)>');
  check('markup in the error is text, no element created', div.querySelector('img') === null);
  check('the markup is shown literally', div.textContent === 'Not drawn: <img src=x onerror=alert(1)>', div.textContent);
  check('still one paragraph', div.querySelectorAll('p').length === 1);
  div = await tile('a & b "q" \u00e9\nline2');
  check('ampersand, quotes, unicode, newline stay literal', div.textContent === 'a & b "q" \u00e9\nline2', JSON.stringify(div.textContent));
  div = await tile('Not drawn: ValueError: x');
  check('plain text unchanged', div.textContent === 'Not drawn: ValueError: x');
  check('tile may retry', div.getAttribute('data-rendered') === '0');
  console.log(failures ? failures + ' failure(s)' : 'all ok');
  process.exit(failures ? 1 : 0);
})();
