'use strict';
// Drive the shipped script with the ACTUAL root-only / no-config fragment.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const script = fs.readFileSync(path.join(__dirname, '../quam_state_manager/web/static/project-time.js'), 'utf8');
const tick = () => new Promise(resolve => setTimeout(resolve, 5));
async function until(predicate) {
  for (let i = 0; i < 400; i++) { if (predicate()) return; await tick(); }
  assert.fail('Timed out waiting for the component');
}
function world(body) {
  const dom = new JSDOM('<html><body>' + body + '</body></html>', {
    runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://127.0.0.1/'
  });
  const w = dom.window, posts = [];
  w.fetch = async (url, options = {}) => {
    const form = Object.fromEntries(new URLSearchParams(options.body || ''));
    if (options.method === 'POST') posts.push({ url, form });
    let reply = {};
    if (url.startsWith('/project-time/clock')) reply = {
      os_zone: { utc_offset: '+09:00' }, ntp: { synced: true },
      ntp_text: 'Time sync: on', now_ms: Date.now()
    };
    if (url === '/project-time/zone') reply = {
      ok: true, view: { project: form.project, zone: form.zone, state: 'picked' },
      display: { project: form.project, zone: form.zone }
    };
    return { ok: true, status: 200, json: async () => reply };
  };
  w.eval(script);
  return { dom, w, d: w.document, posts };
}
(async () => {
  const W = world(input.fresh);
  try {
    await until(() => W.w.ProjectTime._state().clock);
    assert.equal(W.d.querySelector('#landing-env-picker'), null);
    assert.equal(W.d.querySelector('[data-tz-project]').textContent, input.scope);
    W.d.querySelector('[data-tz-change]').click();
    const search = W.d.querySelector('[data-tz-input]');
    search.value = 'los ang';
    search.dispatchEvent(new W.w.Event('input', { bubbles: true }));
    W.d.querySelector('[data-zone="America/Los_Angeles"]').click();
    await until(() => W.d.querySelector('[data-pt-answer="view"]'));
    assert.equal(W.posts.length, 0, 'OS compare must precede saving');
    W.d.querySelector('[data-pt-answer="view"]').click();
    await until(() => W.d.querySelector('[data-pt-answer="matches"]'));
    assert.deepEqual(W.posts[0].form.project, input.scope);
    assert.equal(W.posts[0].form.os_answer, 'view');
    assert.match(W.d.querySelector('#pt-zone-dialog').textContent, /Does it match your watch\?/);
    W.d.querySelector('[data-pt-answer="matches"]').click();
    await until(() => W.posts.some(p => p.url === '/project-time/watch'));
    assert.equal(W.posts.find(p => p.url === '/project-time/watch').form.project, input.scope);
  } finally { W.dom.window.close(); }
  const R = world(input.saved);
  try {
    await until(() => R.w.ProjectTime._state().clock);
    assert.match(R.d.querySelector('[data-tz-current]').textContent, /America\/Los_Angeles/);
    assert.equal(R.d.querySelector('[data-tz-state]').textContent, 'saved');
    assert.equal(R.d.querySelector('dialog[open]'), null, 'Reload shows the saved pick without asking');
    assert.equal(R.posts.length, 0);
  } finally { R.dom.window.close(); }
  console.log('without projects component: 11 assertions passed for ' + input.scope);
})().catch(error => { console.error(error); process.exit(1); });
