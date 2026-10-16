'use strict';
// v1.5.0 (customer): a saved run figure keeps its full size whatever the panel height.
// v1.4.1 capped saved PNGs at the measured pane height (a live ResizeObserver), so dragging the
// split bar shrank or grew the figure being read; the cap and its measuring are gone.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const base = path.join(__dirname, '../quam_state_manager/web/static');
const app = fs.readFileSync(path.join(base, 'app.js'), 'utf8');
const css = fs.readFileSync(path.join(base, 'style.css'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
let n = 0;
const ok = (cond, msg) => { assert.ok(cond, msg); n++; };
ok(!/--saved-figure-height/.test(app) && !/--saved-figure-height/.test(css), 'no pane-height variable is measured or read');
ok(!/SavedFigureFit/.test(app), 'no pane observer resizes saved figures');
// every rule that styles a saved image must leave its height alone
const rules = [...css.matchAll(/([^{}]+)\{([^}]*)\}/g)]
    .filter(m => /(figure-card|ds-interactive-fig)[^,{]*\bimg\b/.test(m[1]));
ok(rules.length >= 1, 'the saved-image rules are found');
for (const m of rules) ok(!/(^|;)\s*max-height\s*:/.test(m[2]), 'no height cap on ' + m[1].trim());
const card = rules.find(m => /\.figure-card img/.test(m[1]) && /width:\s*100%/.test(m[2]));
ok(card && /height:\s*auto/.test(card[2]), 'a saved image is width-driven (width 100%, height auto)');
ok(/window\.toggleFigureZoom = function/.test(app), 'click still opens the full-size viewer');
console.log(`saved_figure_fit_selfcheck: ${n} checks passed`);
