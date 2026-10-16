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
