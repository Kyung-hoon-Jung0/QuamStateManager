"""R3B MINOR 10: every release declaration is 1.5.1."""
from pathlib import Path
import re

from quam_state_manager import __version__

ROOT = Path(__file__).resolve().parents[1]


def test_package_declares_the_release_version():
    assert __version__ == "1.5.2"


def test_project_declares_the_release_version():
    source = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    project = source.split("[project]", 1)[1].split("\n[", 1)[0]
    version = re.search(r'^version\s*=\s*"([^"]+)"', project, re.M)
    assert version and version.group(1) == "1.5.2"
