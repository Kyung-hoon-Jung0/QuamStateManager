const assert = require('assert');
const fs = require('fs');
const path = require('path');
const {JSDOM} = require('jsdom');
for (const staged of [true, false]) {
  const dom = new JSDOM('<body></body>', {runScripts:'outside-only', url:'http://localhost/'});
  const w = dom.window, toasts = [];
  w.showToast = text => toasts.push(text);
  w.eval(fs.readFileSync(path.join(__dirname, '../quam_state_manager/web/static/auto-apply.js'), 'utf8'));
  w.document.dispatchEvent(new w.Event('DOMContentLoaded'));
  const detail = {count:1, staged, event:'restore-1', message:'Server restore text'};
  w.document.dispatchEvent(new w.CustomEvent('autoSyncRestored', {detail}));
  w.document.dispatchEvent(new w.CustomEvent('autoSyncRestored', {detail}));
  assert.deepEqual(toasts, ['Server restore text']);
  w.close();
}
console.log('2 assertions passed');
