"""Mutation audit for cz_plan_theme_selfcheck.cjs: each mutation must turn the selfcheck red.

Run by hand: python tests/cz_plan_theme_mutation_audit.py  (restores every file it touches).
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "quam_state_manager/web/static/cz-plan.js"
THEME = ROOT / "quam_state_manager/web/static/plot-theme.js"

MUTATIONS = [
    ("ring takes the curve colour", PLAN, "line: { color: C.edge, width: 2.5 }", "line: { color: color, width: 2.5 }"),
    ("loss area ignores the area token", PLAN,
     "area: function (c) { return T && T.areaFill ? T.areaFill(c) : tint(c, 0.16); }",
     "area: function (c) { return tint(c, 0.16); }"),
    ("no redraw on a theme switch", PLAN,
     "if (!changes.some(function (c) { return c.attributeName === 'data-theme'; })) return;", "return;"),
    ("operating point back to its old size", PLAN,
     "showlegend: false, marker: operatingMarker(C, C.q[k], 20),", "showlegend: false, marker: operatingMarker(C, C.q[k], 11),"),
    ("loss hue token not read", THEME, "return cssVar('--plot-loss', '#ea580c');", "return '#888888';"),
    ("loss panel back to the qubit colour", PLAN, "line: { color: C.loss, width: 1.6 },", "line: { color: C.q[k], width: 1.6 },"),
    ("no readable text floors", PLAN,
     "return window.PlotTheme && window.PlotTheme.readable ? window.PlotTheme.readable(L) : L;", "return L;"),
    ("tick floor lowered", THEME, "tick = Math.round(13 * k)", "tick = Math.round(10 * k)"),
    ("no operating-point label", PLAN, "text: '<b>op ' + opY.toFixed(4) + ' GHz</b>',", "text: '',"),
    ("ring colour hard-coded", PLAN, "edge: T && T.markEdge ? T.markEdge() : '#ffffff',", "edge: '#ffffff',"),
    ("mark-edge token not read", THEME,
     "return cssVar('--plot-mark-edge', cssVar('--pico-background-color', '#ffffff'));", "return '#ffffff';"),
    ("area token not read", THEME, "var a = parseFloat(cssVar('--plot-area-alpha', ''));", "var a = NaN;"),
    ("axis text hard-coded", THEME, "var text = cssVar('--plot-axis-text', cssVar('--pico-color', '#ccc'));\n        var grid",
     "var text = '#cccccc';\n        var grid"),
]


def main():
    survived = 0
    for name, path, old, new in MUTATIONS:
        src = path.read_text(encoding="utf-8")
        assert src.count(old) == 1, name
        try:
            path.write_text(src.replace(old, new), encoding="utf-8", newline="")
            r = subprocess.run(["node", str(ROOT / "tests/cz_plan_theme_selfcheck.cjs")], cwd=ROOT,
                               capture_output=True, text=True, encoding="utf-8", timeout=120)
            red = r.returncode != 0
        finally:
            path.write_text(src, encoding="utf-8", newline="")
        survived += not red
        print(("caught   " if red else "SURVIVED ") + name)
    print(f"{len(MUTATIONS) - survived}/{len(MUTATIONS)} mutations caught")
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
