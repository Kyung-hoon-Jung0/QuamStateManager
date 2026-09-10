/* The axis tick-format rule, driven against the REAL plot-theme.js.
 *
 * Customer, 2026-09-10: a Trends fidelity axis read "996m" for 0.996. An SI
 * prefix is a UNIT prefix, and a bare ratio has no unit to prefix -- but the
 * prefix cannot simply be dropped either, because Plotly's own default writes
 * 4.9e9 as "4.9B", US billions, which is the reason `~s` was there. The rule
 * lives in ONE place (PlotTheme.axisTickFormat) and both chart surfaces call
 * it; this pins the rule and the fact that they call it.
 *
 * Every expectation below was READ OFF a real chart in headless Chrome on a
 * real 5-qubit chip before it was written down -- the format strings are not
 * predictions.
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const ROOT = path.resolve(__dirname, '..');
const STATIC = path.join(ROOT, 'quam_state_manager', 'web', 'static');
const read = (f) => fs.readFileSync(path.join(STATIC, f), 'utf8');

let checks = 0, fails = 0;
function ok(cond, msg) {
    checks++;
    if (!cond) { fails++; console.log('FAIL: ' + msg); }
}

const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>',
                      { runScripts: 'outside-only', url: 'http://localhost/' });
const win = dom.window;
win.eval(read('plot-theme.js'));

const F = win.PlotTheme && win.PlotTheme.axisTickFormat;
ok(typeof F === 'function', '1a PlotTheme exports axisTickFormat');

if (typeof F === 'function') {
    /* Measured: gate_fidelity_avg 0.991..0.996 rendered 991m..996m under `~s`
       and 0.991..0.996 under ''. A ratio has no unit, so no prefix. */
    ok(F(0.9961) === '', '2a a fidelity gets no prefix');
    ok(F(0.9112) === '', '2b a readout fidelity gets no prefix');

    /* Measured: readout_amplitude spans 0.0007063..0.22 and its ticks are
       0.05..0.2. The rule takes the LARGEST magnitude for exactly this case --
       keyed on the smallest it rendered 50m..200m. */
    ok(F(0.22) === '', '2c an amplitude gets no prefix');
    ok(F(0.9512) === '', '2d x180 amplitude gets no prefix');

    /* Measured: f_01 4.75e9..5.25e9 renders 4.8G..5.2G under `~s`, and
       4.8B..5.2B under Plotly's default. The prefix is what keeps the B away,
       and it has to survive on a TYPED path too, where no unit is known
       (resonator.RF_frequency, 6.47e9..7.33e9 -> 6.6G..7.2G). */
    ok(F(5.25e9) === '~s', '3a a frequency keeps the prefix');
    ok(F(7.33e9) === '~s', '3b a typed-path frequency keeps it too');
    ok(F(1e5) === '~s', '3c the upper edge is prefixed');
    ok(F(99999) === '', '3d just inside the upper edge is not');

    /* Measured: T1 8.1e-6..2.2e-5 renders 10u 15u 20u; T2echo to 6.6e-5. */
    ok(F(2.224e-5) === '~s', '4a microseconds keep the prefix');
    ok(F(6.58e-5) === '~s', '4b so does the T2 range');
    ok(F(1e-4) === '', '4c the lower edge is not prefixed');
    ok(F(9.9e-5) === '~s', '4d just below it is');

    /* Zero is not a magnitude, and neither is nonsense. A chart of all-zero
       values exists on a real chip (mutual_flux_bias.0 across four pairs). */
    ok(F(0) === '', '5a all-zero forces no prefix');
    ok(F(-1) === '', '5b a negative maxAbs is nonsense, not a prefix');
    ok(F(NaN) === '' && F(Infinity) === '' && F(undefined) === '',
       '5c NaN / Infinity / undefined degrade quietly');

    /* Thousands stay inside the band: "2k" is the same in every convention,
       unlike "B". Measured on resonator.operations.readout.length 1000..10500. */
    ok(F(10500) === '', '6a thousands stay inside the band');
}

/* Both chart surfaces must ASK PlotTheme rather than keep their own copy --
   a second spelling of one rule is how this app grew a second fidelity
   vocabulary in the same week. */
const chipStatus = read('chip-status.js');
const app = read('app.js');
ok(/PlotTheme\.axisTickFormat\(/.test(chipStatus),
   '7a the Trends charts call the shared rule');
ok(/PlotTheme\.axisTickFormat\(/.test(app),
   '7b the Param History drawer calls the shared rule');
ok(!/tickformat:\s*'~s'/.test(chipStatus) && !/tickformat:\s*'~s'/.test(app),
   '7c neither surface hard-codes a format any more');

if (fails === 0) console.log('all checks passed (' + checks + ' assertions)');
process.exit(fails ? 1 : 0);
