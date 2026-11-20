"""Offline cache and build source coverage, including excluded environments."""
import importlib.util
from pathlib import Path
import sys
import time
from types import ModuleType, SimpleNamespace

import pytest

from quam_state_manager.core.nodesim_click import input_key, library_fingerprint


@pytest.fixture
def inputs(tmp_path):
    """Use only scratch chip JSON and inert node source."""
    chip = tmp_path / "chip"
    chip.mkdir()
    for name in ("state.json", "wiring.json"):
        (chip / name).write_text("{}", encoding="ascii")
    library = tmp_path / "library"
    node = library / "calibration_graph" / "node.py"
    node.parent.mkdir(parents=True)
    node.write_text("import helper_x\n", encoding="ascii")
    return chip, node, library


def key(inputs):
    """Keep every non-source cache input fixed."""
    return input_key(*inputs, {}, 20000, "test", "python")


@pytest.mark.parametrize("relative", ["calibration_graph/helper_x.py", "lab_utils/sub/y.py"])
def test_library_edit_changes_input_key(inputs, relative):
    """Helpers outside the former two directories invalidate a cached report."""
    path = inputs[2] / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("VALUE = 1\n", encoding="ascii")
    before = key(inputs)
    path.write_text("VALUE = 222\n", encoding="ascii")
    assert key(inputs) != before


@pytest.mark.parametrize("folder", [".venv", "__pycache__", "node_modules", "site-packages", ".hidden",
                                   "nested/env"])
def test_excluded_source_edit_keeps_input_key(inputs, folder):
    """Prune named directories and any environment identified by pyvenv.cfg."""
    root = inputs[2] / folder
    root.mkdir(parents=True)
    if folder == "nested/env":
        (root / "pyvenv.cfg").write_text("home = scratch\n", encoding="ascii")
    path = root / "sub" / "ignored.py"
    path.parent.mkdir()
    path.write_text("VALUE = 1\n", encoding="ascii")
    before = key(inputs)
    path.write_text("VALUE = 222\n", encoding="ascii")
    assert key(inputs) == before


def test_node_edit_changes_input_key(inputs):
    """Node bytes remain part of the key even with unchanged size and timestamp."""
    node = inputs[1]
    before = key(inputs)
    stat = node.stat()
    node.write_text("import helper_y\n", encoding="ascii")
    import os
    os.utime(node, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert key(inputs) != before


def test_unchanged_inputs_keep_key(inputs):
    """Repeated simulation inputs still hit the same cache entry."""
    assert key(inputs) == key(inputs)


def test_library_fingerprint_sorted_and_fast(tmp_path):
    """Measure the complete stat walk on 2000 files with an unflaky time ceiling."""
    for folder in range(20):
        directory = tmp_path / f"folder_{folder:02}"
        directory.mkdir()
        for index in range(100):
            (directory / f"source_{index:03}.py").write_text("pass\n", encoding="ascii")
    start = time.perf_counter()
    sources = library_fingerprint(tmp_path)
    elapsed = time.perf_counter() - start
    print(f"2000 source files: {elapsed * 1000:.3f} ms")
    assert elapsed < 0.5
    assert len(sources) == 2000
    assert sources == sorted(sources)
    assert all("\\" not in path and not Path(path).is_absolute() for path, _, _ in sources)
    assert all((mtime, size) == (stat.st_mtime_ns, stat.st_size)
               for path, mtime, size in sources for stat in [(tmp_path / path).stat()])


@pytest.mark.parametrize("warm", [False, True])
def test_build_rejects_changed_imported_helper(tmp_path, monkeypatch, warm):
    """Cold and warm builds reject an imported lab helper edited during serialization."""
    generator = Path(__file__).resolve().parents[1] / "quam_state_manager" / "generator"
    monkeypatch.syspath_prepend(str(generator))
    spec = importlib.util.spec_from_file_location("ck_build", generator / "nodesim_build.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    assert build.library_fingerprint.__code__.co_code == library_fingerprint.__code__.co_code
    library = tmp_path / "library"
    helpers = library / "lab_utils"
    helpers.mkdir(parents=True)
    source = helpers / "ck_helper.py"
    source.write_text("VALUE = 1\n", encoding="ascii")
    monkeypatch.syspath_prepend(str(helpers))
    monkeypatch.setitem(sys.modules, "ck_helper", None)
    monkeypatch.delitem(sys.modules, "ck_helper", raising=False)
    node = library / "node.py"
    node.write_text('import ck_helper\nfrom qualibrate import QualibrationNode\n'
                    'node = QualibrationNode("fake")\n'
                    '@node.run_action\ndef create_qua_program(node):\n'
                    '    node.namespace["qua_program"] = object()\n', encoding="ascii")
    chip = tmp_path / "chip"
    chip.mkdir()
    for name in ("state.json", "wiring.json"):
        (chip / name).write_text("{}", encoding="ascii")
    monkeypatch.setattr(build, "verify_env", lambda *args: None)
    monkeypatch.setattr(build, "verify_config", lambda *args: None)
    fake_library = SimpleNamespace(__file__=str(library / "quam_config" / "__init__.py"))

    def install(job, guard, holder):
        """Replace all lab initialization with an inert library holder."""
        holder.update(library=fake_library, sources={})

    def serialize(program):
        """Edit after collection and import, before the source guard checks stats."""
        assert sys.modules["ck_helper"].VALUE == 1
        source.write_text("VALUE = 222\n", encoding="ascii")
        return "fake script"

    def fail(channel, detail):
        """Expose the exact guard channel and source path without real guard side effects."""
        raise RuntimeError(f"{channel}: {detail}")

    monkeypatch.setattr(build, "install_once", install)
    monkeypatch.setitem(sys.modules, "qualibrate", ModuleType("qualibrate"))
    monkeypatch.setitem(sys.modules, "qm", SimpleNamespace(generate_qua_script=serialize))
    monkeypatch.setitem(sys.modules, "_nodesim_node", None)
    guard = SimpleNamespace(install_classes=lambda: None, fail=fail)
    holder = {"library": fake_library, "sources": {}} if warm else {}
    job = dict(node_file=str(node), library_root=str(library), chip_copy=str(chip))
    with pytest.raises(RuntimeError, match="source.changed") as caught:
        build.build_one(job, guard, {"replaced_hosts": []}, holder)
    assert str(caught.value) == f"source.changed: {source.resolve()}"
