"""No module of the package uses a name it never binds.

S10 C4 deleted the old snapshot code of the agent API and its `import os` with it while
`os.getpid()` stayed in use: the overnight grant watch raised NameError, found only by a
full-suite run. A deletion step can drop an import a surviving line still needs; this
static check fails on any undefined name, in every module, without having to reach the line."""
from __future__ import annotations

from pathlib import Path

import pytest

pyflakes_api = pytest.importorskip("pyflakes.api")

PKG = Path(__file__).resolve().parents[1] / "quam_state_manager"


class _Collect:
    def __init__(self):
        self.messages: list[str] = []

    def unexpectedError(self, filename, msg):  # noqa: N802 (pyflakes reporter API)
        self.messages.append(f"{filename}: {msg}")

    def syntaxError(self, filename, msg, lineno, offset, text):  # noqa: N802
        self.messages.append(f"{filename}:{lineno}: {msg}")

    def flake(self, message):
        if "undefined name" in str(message.message):
            self.messages.append(str(message))


def test_no_module_uses_an_undefined_name():
    rep = _Collect()
    for py in sorted(PKG.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        pyflakes_api.check(py.read_text(encoding="utf-8"), str(py), rep)
    assert rep.messages == []
