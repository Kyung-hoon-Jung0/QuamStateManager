"""The Delta-vs-time panel scales to the gate, not to the idle detuning."""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = r"""
const assert = require('assert'), fs = require('fs'), {JSDOM} = require('jsdom');
const dom = new JSDOM('<html></html>', {runScripts: 'outside-only'});
dom.window.eval(fs.readFileSync('quam_state_manager/web/static/cz-plan.js', 'utf8'));
const R = dom.window.CzPlan._deltaRange;
const levels = [{name: '|11>-|20>', detuning_mhz: 200, lo_mhz: 195, hi_mhz: 205},
                {name: 'iSWAP', detuning_mhz: 0, lo_mhz: -5, hi_mhz: 5},
                {name: '|11>-|02>', detuning_mhz: -216, lo_mhz: -221, hi_mhz: -211}];
// idle +1495 MHz, gate plateau +340 MHz: the range holds the plateau and every resonance,
// and the idle level is off the top of the panel.
const v = R([1495, 1495, 340, 340, 340, 1495], levels);
assert.ok(v.range[0] < -221 && v.range[1] > 340, JSON.stringify(v));
assert.ok(v.range[1] < 1495, 'the idle level must not set the range');
assert.strictEqual(v.offscale, 'above');
assert.strictEqual(v.gateKnown, true);
// a refused partner: only idle samples are known during the gate
const u = R([1495, null, null, 1495], levels);
assert.strictEqual(u.gateKnown, false);
assert.strictEqual(u.offscale, 'above');
assert.ok(u.range[1] < 1495);
// idle inside the band: nothing off scale
assert.strictEqual(R([100, -210, -210, 100], levels).offscale, null);
"""


def test_delta_panel_scales_to_the_gate_and_the_resonances():
    result = subprocess.run(['node', '-e', SCRIPT], cwd=ROOT, env=dict(os.environ),
                            capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_panel_uses_the_range():
    src = (ROOT / 'quam_state_manager/web/static/cz-plan.js').read_text(encoding='utf-8')
    assert 'var view = deltaRange(delta, panel.levels);' in src
    assert 'range: view.range, autorange: false' in src
    assert "off scale'" in src and 'during the gate unknown' in src


def test_a_defect_band_is_named_on_the_flux_plot():
    """A narrow measured defect band reads as an unlabelled line across the plot unless named."""
    src = (ROOT / 'quam_state_manager/web/static/cz-plan.js').read_text(encoding='utf-8')
    assert "bandLabels.push({ _czpDefectLabel: true" in src
    assert "text: q.name + ' defect '" in src
    assert 'var annotations = bandLabels.slice();' in src
