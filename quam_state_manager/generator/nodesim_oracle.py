"""Standalone lab-env child: D-6 oracle, the REAL node builds its own program.

Offline test aid only, never a click path. The real ``QualibrationNode`` runs the
node file under the same D-7 pins and D-8 guard stack as ``nodesim_build``. The
oracle changes three things and nothing else:

* the node is constructed with the D-4 overrides (``model_validate``, ``simulate``
  forced True) and ``modes.external = True`` (the D-5 semantics the stub uses);
* ``machine.connect()`` is the guard's stub: inside the node's own
  ``simulate_qua_program`` action, once ``qua_program`` exists, it raises
  ``OracleStop`` (the simulate action is skipped from that point and no later
  action runs); anywhere else it is a GUARD FAIL;
* ``QualibrationNode.save`` / ``load_from_id`` are GUARD FAIL (the storage save
  stays blocked underneath by ``Guard.install_classes``).

No simulation, scanning, framework save or QM connection is performed.
"""
import json
import os
from pathlib import Path
import sys
import traceback

# A script must work with PYTHONPATH pinned exclusively to the lab library.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import nodesim_build  # noqa: E402
from nodesim_guard import Guard  # noqa: E402

SIMULATE_ACTION = "simulate_qua_program"


class OracleStop(BaseException):
    """Raised by the stub connect inside the simulate action; not an error."""


def in_simulate_action(node_file):
    frame = sys._getframe(1)
    target = os.path.normcase(str(Path(node_file).resolve()))
    while frame is not None:
        code = frame.f_code
        if code.co_name == SIMULATE_ACTION and os.path.normcase(str(Path(code.co_filename).resolve())) == target:
            return True
        frame = frame.f_back
    return False


def install_real_node(guard, overrides, node_file, instances):
    from qualibrate import QualibrationNode
    from qualibrate.core.models.run_mode import RunModes
    original_init = QualibrationNode.__init__

    def init(self, *args, **kwargs):
        if args:
            # name is the only positional the node files use; parameters come by keyword.
            kwargs["name"], args = args[0], args[1:]
        parameters = kwargs.get("parameters")
        if parameters is not None:
            data = {**parameters.model_dump(), **overrides}
            if "simulate" in data:
                data["simulate"] = True
            kwargs["parameters"] = type(parameters).model_validate(data)
        kwargs["modes"] = RunModes(inspection=False, interactive=False, external=True)
        original_init(self, *args, **kwargs)
        instances.append(self)

    QualibrationNode.__init__ = init
    QualibrationNode.save = guard.block("node.save", "called: refused")
    QualibrationNode.load_from_id = guard.block("node.load_from_id", "refused")
    return QualibrationNode


def oracle_connect(guard, node_file, instances):
    def connect(*args, **kwargs):
        captured = bool(instances) and "qua_program" in instances[-1].namespace
        if captured and in_simulate_action(node_file):
            raise OracleStop()
        guard.fail("Quam.connect", "refused")
    return connect


def build_real(job, guard, result):
    node_file = Path(job["node_file"]).resolve()
    if node_file.stem.startswith("15p"):
        guard.fail("node", "unsupported execute path")
    nodesim_build.verify_env(job, guard)
    nodesim_build.verify_config(job, guard)
    guard.install_classes()
    replaced = result["replaced_hosts"]
    for filename in ("state.json", "wiring.json"):
        path = Path(job["chip_copy"]) / filename
        clean, paths = nodesim_build.sanitize(json.loads(path.read_text(encoding="utf-8")), filename)
        path.write_text(json.dumps(clean), encoding="utf-8")
        replaced.extend(paths)
    library = guard.install_library(job["library_root"])
    original_load = library.Quam.load.__func__

    def load(cls, path=None, *args, **kwargs):
        if path is None:
            path = job["chip_copy"]
        if not isinstance(path, (str, Path)) or Path(path).resolve() != Path(job["chip_copy"]).resolve():
            guard.fail("Quam.load", str(path))
        return original_load(cls, path, *args, **kwargs)

    library.Quam.load = classmethod(load)
    instances = []
    install_real_node(guard, job.get("overrides", {}), node_file, instances)
    library.Quam.connect = oracle_connect(guard, node_file, instances)
    import types
    module = types.ModuleType("_nodesim_oracle_node")
    module.__file__ = str(node_file)
    sys.modules[module.__name__] = module
    stopped = False
    try:
        exec(compile(node_file.read_text(encoding="utf-8"), str(node_file), "exec"), module.__dict__)
    except OracleStop:
        stopped = True
    if guard.verdict:
        raise RuntimeError("guard refused an operation")
    if len(instances) != 1:
        raise RuntimeError(f"expected one QualibrationNode, got {len(instances)}")
    node = instances[0]
    if "qua_program" not in node.namespace:
        raise RuntimeError("create_qua_program did not run")
    if not stopped:
        raise RuntimeError("the simulate action did not reach machine.connect")
    from qm import generate_qua_script
    script = generate_qua_script(node.namespace["qua_program"])
    if "SERIALIZATION VALIDATION ERROR" in script:
        raise RuntimeError("QUA serialization validation failed")
    return {"program_script": script.split("\nconfig =", 1)[0], "replaced_hosts": replaced,
            "library_file": str(library.__file__), "stopped_in": SIMULATE_ACTION,
            "parameters": json.loads(node.parameters.model_dump_json())}


def main():
    job = json.loads(sys.argv[1]) if len(sys.argv) > 1 else json.load(sys.stdin)
    if job.get("mode") == "stub":
        # The stub side of the comparison: nodesim_build.main unchanged.
        sys.argv = [sys.argv[0], json.dumps(job)]
        return nodesim_build.main()
    guard = Guard(job["scratch"])  # No SaaS lease: empty network allow-list.
    result = dict(node=str(job["node_file"]), ok=False, program_script=None,
                  replaced_hosts=[], verdict=None, errors=[], mode="oracle")
    try:
        if Path(job["node_file"]).stem.startswith("15p"):
            guard.fail("node", "unsupported execute path")
        nodesim_build.verify_env(job, guard)
        nodesim_build.verify_config_raw(guard)
        # D-39: the builder's own pre-import list (D-33), one definition.
        nodesim_build.preimport_trusted()
        guard.install_audit()
        import qm  # noqa: F401
        import qualibrate  # noqa: F401
        import qualang_tools.multi_user  # noqa: F401
        import quam.components  # noqa: F401
        result.update(build_real(job, guard, result))
        result["ok"] = True
    except BaseException as exc:
        result["errors"].append(f"{type(exc).__name__}: {exc}")
        result["traceback"] = traceback.format_exc()
    if guard.verdict:
        result.update(ok=False, verdict=guard.verdict)
    print(json.dumps(result), flush=True)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
