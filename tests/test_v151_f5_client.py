"""Drive the real theme, map tools and figure renderer through state changes."""
import os
import subprocess
from pathlib import Path


def test_client_sequences():
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, NODE_PATH=str(root.parent / "statemanager" / "node_modules"))
    result = subprocess.run(["node", str(root / "tests" / "f5_plot_selfcheck.cjs")], cwd=root,
                            env=env, capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "assertions passed" in result.stdout


def test_legend_row_backgrounds_never_take_a_key_click():
    # one Plotly legend per lab legend row: a row's background box covers the row above, so it
    # must let clicks through to the keys there (checked in Chrome on a two-row figure legend)
    import re
    css = (Path(__file__).resolve().parents[1] / "quam_state_manager" / "web" / "static" / "style.css").read_text(encoding="utf-8")
    rule = re.search(r'\.ds-interactive-fig \.infolayer > g\[class\^="legend"\] > rect\.bg\s*\{([^}]*)\}', css)
    assert rule and re.search(r"pointer-events:\s*none", rule.group(1))
