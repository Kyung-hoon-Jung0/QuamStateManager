'use strict';
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const root = path.join(__dirname, '..');
const read = name => fs.readFileSync(path.join(root, 'quam_state_manager/web', name), 'utf8');
module.exports = function () {
  const dom = new JSDOM('<div id="table-pane">' + read('templates/_generate.html') + '</div>',
    { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' });
  const w = dom.window;
  w.NumberInput = { fit() {}, attach() {}, format() {}, strip: String };
  w.armPlainResize = () => {};
  w.renderInstrumentWiring = () => {};
  w.fetch = () => new w.Promise(() => {});
  const app = read('static/app.js');
  const start = app.indexOf('window.ValueDelta = (function () {');
  w.eval(app.slice(start, app.indexOf('\n})();', start) + 6));
  w.eval(read('static/generate.js'));
  return { dom, w, g: w.QuamGen, t: w.QuamGen._test, doc: w.document };
};
