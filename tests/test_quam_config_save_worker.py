"""The real class-save worker in a subprocess: discover without SM's lab folder,
the chip copy loaded with the draft, and the amplitude field check. A small
stand-in ``quam`` package keeps the run independent of the installed stack."""
import os
import sys
from pathlib import Path

import pytest

from quam_state_manager.core import config_generator, lab_worker
from quam_state_manager.core import pulse_class_drafts as D
from quam_state_manager.core import pulse_class_template as T
from quam_state_manager.core import quam_config_save as Q
from quam_state_manager.core.loader import QuamStore

CLASS = "FlatPulse"
QCLASS = f"quam_config.{CLASS}.{CLASS}"

PULSES = '''import dataclasses


@dataclasses.dataclass
class Pulse:
    id: str = None
    length: int = 100

    def calculate_waveform(self):
        return self.waveform_function()
'''

QUAM_CLASSES = '''import dataclasses

quam_dataclass = dataclasses.dataclass
'''

# A root class whose load builds every __class__ in the state and refuses a
# key the class has no field for, as the real loader does for typed classes.
ROOT = '''import dataclasses
import importlib
import json
from pathlib import Path


def _build(node):
    if isinstance(node, list):
        return [_build(v) for v in node]
    if not isinstance(node, dict):
        return node
    kwargs = {k: _build(v) for k, v in node.items() if k != "__class__"}
    path = node.get("__class__")
    if not path:
        return kwargs
    module, name = path.rsplit(".", 1)
    cls = getattr(importlib.import_module(module), name)
    if not dataclasses.is_dataclass(cls):
        return kwargs
    names = {f.name for f in dataclasses.fields(cls)}
    extra = sorted(set(kwargs) - names)
    if extra:
        raise AttributeError(f"Unexpected attribute {extra[0]} for {name}")
    return cls(**kwargs)


class Gate:
    pass


class Quam:
    @classmethod
    def load(cls, folder):
        for name in ("wiring.json", "state.json"):
            p = Path(folder) / name
            if p.exists():
                state = json.loads(p.read_text(encoding="utf-8"))
                state.pop("__class__", None)
                _build(state)
        return cls()
'''


def _source(fields):
    return T.render(T.ClassSpec(CLASS, [T.FieldSpec(*f) for f in fields], "amplitude * np.ones(n)"))


GOOD = [("amplitude", "float", 0.1), ("length", "int", 40), ("width", "int", 5)]


@pytest.fixture
def lab(tmp_path, monkeypatch):
    if not sys.executable or not Path(sys.executable).is_file():
        pytest.skip("no Python interpreter to run the lab worker with")
    stub = tmp_path / "stub"
    for rel, text in {"quam/__init__.py": "", "quam/components/__init__.py": "",
                      "quam/components/pulses.py": PULSES, "quam/core/__init__.py": "",
                      "quam/core/quam_classes.py": QUAM_CLASSES}.items():
        (stub / rel).parent.mkdir(parents=True, exist_ok=True)
        (stub / rel).write_text(text, encoding="utf-8")
    folder = tmp_path / "source" / "quam_config"
    folder.mkdir(parents=True)
    (folder / "__init__.py").write_text(ROOT, encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(stub), str(folder.parent)]))
    monkeypatch.setattr(config_generator, "known_interpreter", lambda *a: True)
    monkeypatch.setattr(config_generator, "get_lab_code_dir", lambda *a: str(folder.parent))
    spawned = []
    real = lab_worker.run

    def run(argv, **kwargs):
        spawned.append(kwargs.get("prepend_lab"))
        return real(argv, **kwargs)
    monkeypatch.setattr(lab_worker, "run", run)
    instance = tmp_path / "instance"
    store = QuamStore.from_dicts({"__class__": "quam_config.Quam", "qubit_pairs": {"pair": {"macros": {
        "cz_flat": {"__class__": "quam_config.Gate",
                    "pulse": {"__class__": QCLASS, "length": 40, "width": 5}},
        "cz": "#./cz_flat"}}}}, {})

    def save(fields=GOOD, source=None, use_store=True):
        D.save_draft(instance, "scope", CLASS, source or _source(fields))
        return Q.save_class(sys.executable, instance, "scope", CLASS,
                            confirmed_folder=str(folder.resolve()),
                            store=store if use_store else None, history_folder=tmp_path / "history")
    return folder, spawned, save


def test_discover_runs_without_sm_lab_folder_and_finds_the_environment_package(lab):
    folder, spawned, save = lab
    assert Q.destination(sys.executable)["folder"] == str(folder.resolve())
    assert spawned == [False]


def test_the_chip_copy_is_loaded_with_the_draft(lab):
    folder, spawned, save = lab
    result = save()
    assert result["ok"], result
    assert (folder / f"{CLASS}.py").read_text(encoding="utf-8") == _source(GOOD)
    assert spawned == [False, True]
    assert result["chip_warning"] is None


def test_a_draft_that_drops_a_field_the_chip_sets_is_refused(lab):
    folder, spawned, save = lab
    assert save(use_store=False)["ok"]
    with pytest.raises(D.DraftError, match="Unexpected attribute width"):
        save(fields=GOOD[:2])
    assert (folder / f"{CLASS}.py").read_text(encoding="utf-8") == _source(GOOD)
    assert spawned == [False, True, False, True, True]


def test_a_pulse_class_without_an_amplitude_field_is_refused(lab):
    folder, spawned, save = lab
    source = _source(GOOD).replace("amplitude", "amp")
    with pytest.raises(D.DraftError, match="needs an amplitude field"):
        save(source=source, use_store=False)
    assert not (folder / f"{CLASS}.py").exists()
