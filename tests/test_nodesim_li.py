"""LI import compatibility and post-import barrier pins, using only fake lab packages."""
import os
from pathlib import Path

import pytest

from tests.test_nodesim_child import Click, generator_dir, listener  # noqa: F401
from tests.test_nodesim_warm_child import session, no_worker_orphans, ok_click  # noqa: F401


WRAPS = {
    "storage": ("LocalStorageManager.save",
                "from qualibrate.core.storage.local_storage_manager import LocalStorageManager as Owner", "save"),
    "execute": ("QuantumMachine.execute", "from qm.quantum_machine import QuantumMachine as Owner", "execute"),
}


def patch_library(library, kind):
    name, owner, attr = WRAPS[kind]
    path = library / "quam_config" / "__init__.py"
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n" + owner + f"\n_previous = Owner.{attr}\n"
                     + "def wrapped(*args, **kwargs):\n    return _previous(*args, **kwargs)\n"
                     + f"Owner.{attr} = wrapped\n")
    return name


def mutate(generator):
    row = os.environ.get("SM_NODESIM_MUTATION_LI")
    if not row:
        return
    path = Path(generator) / "nodesim_guard.py"
    source = path.read_text(encoding="utf-8")
    old, new = {
        "LI-A": ("self.library_patches = self.reassert_barriers()", "self.library_patches = []"),
        "LI-B": ("            self.remember(name, owner, attribute)", "            pass"),
    }[row]
    assert old in source
    path.write_text(source.replace(old, new), encoding="utf-8")


@pytest.mark.parametrize("mode", ["cold", "warm"])
@pytest.mark.parametrize("case", ["build", "save", "execute", "rebind_storage", "rebind_root"])
def test_li_import_and_node_pins(tmp_path, request, mode, case):
    kind = "execute" if case == "execute" else "storage"
    body = {
        "build": "pass",
        "save": "from qualibrate.core.storage.local_storage_manager import LocalStorageManager\n"
                "LocalStorageManager.save(None)",
        "execute": "from qm.quantum_machine import QuantumMachine\nQuantumMachine.execute(None)",
        "rebind_storage": "from qualibrate.core.storage.local_storage_manager import LocalStorageManager\n"
                          "LocalStorageManager.save = lambda *a, **k: None",
        "rebind_root": "from quam.core.quam_classes import QuamRoot\nQuamRoot.save = lambda *a, **k: None",
    }[case]
    expected = {
        "save": "GUARD FAIL storage.save refused", "execute": "GUARD FAIL execute refused",
        "rebind_storage": "GUARD FAIL barrier LocalStorageManager.save replaced",
        "rebind_root": "GUARD FAIL barrier QuamRoot.save replaced",
    }.get(case)
    if mode == "cold":
        generator_dir = request.getfixturevalue("generator_dir")
        mutate(generator_dir)
        click = Click(tmp_path / "cold", generator_dir)
        name = patch_library(click.library, kind)
        node = click.node(body)
        with listener() as (login, _), listener() as (instance, _):
            result = click.start(node, login, instance).finish()
        if expected:
            assert result["kind"] == "guard" and result["message"] == expected, result
        else:
            assert result["kind"] == "ok", result
        logs = result.get("logs", [])
    else:
        session = request.getfixturevalue("session")
        mutate(session.generator)
        name = patch_library(session.library, kind)
        session.node()
        session.start()
        session.event("ready")
        session.lease()
        session.event("lease_ok")
        first = ok_click(session)
        if case == "build":
            result = ok_click(session)
            assert first["kind"] == result["kind"] == "ok"
        else:
            session.node(body)
            session.click()
            assert session.event("guard")["line"] == expected
            session.event("bye")
        logs = first.get("logs", [])
    line = "library patched at import, barrier restored: " + name
    assert logs.count(line) == 1, logs


def test_p3_env_folder():
    from quam_state_manager.core.nodesim_pool import python_env_name
    assert python_env_name("C:/envs/demo/Scripts/python.exe") == "demo"
    assert python_env_name("C:/envs/demo/bin/python") == "demo"
    assert python_env_name("C:/envs/demo/python.exe") == "demo"
