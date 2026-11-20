"""Standalone lab-env child: guarded program build only (D-2..D-8, D-15).

No simulation, scanning, real-node run, or framework save is performed.
"""
import json
import os
from pathlib import Path
import re
import sys
import types
import traceback

# A script must work with PYTHONPATH pinned exclusively to the lab library.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from nodesim_guard import Guard, inside  # noqa: E402


def sanitize(value, prefix=""):
    """Return a fresh JSON tree and replaced key paths, never original hosts (D-2)."""
    replaced = []
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            path = prefix + "/" + str(key).replace("~", "~0").replace("/", "~1")
            lower = key.lower()
            tokens = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key).lower().split("_")
            network = prefix.rsplit("/", 1)[-1].lower() == "network"
            if any(word in lower for word in ("host", "address", "url")) or "ip" in tokens:
                result[key] = "127.0.0.1" if network else "192.0.2.1"
                replaced.append(path)
            elif network and lower == "port":
                result[key] = 1
                replaced.append(path)
            else:
                result[key], paths = sanitize(item, path)
                replaced.extend(paths)
        return result, replaced
    if isinstance(value, list):
        result = []
        for index, item in enumerate(value):
            clean, paths = sanitize(item, prefix + f"/{index}")
            result.append(clean)
            replaced.extend(paths)
        return result, replaced
    return value, replaced


def verify_env(job, guard):
    root = Path(job["scratch"]).resolve()
    state = Path(job["chip_copy"]).resolve()
    if not inside(state, root):
        guard.fail("env", "QUAM_STATE_PATH")
    expected = {
        "QUALIBRATE_CONFIG_FILE": str(root / "config.toml"),
        "QUAM_CONFIG_FILE": str(root / "config.toml"), "QUAM_STATE_PATH": str(state),
        "TMP": str(root / "tmp"), "TEMP": str(root / "tmp"),
        "MPLCONFIGDIR": str(root / "mpl"), "PYTHONPATH": str(Path(job["library_root"]).resolve()),
    }
    for key, value in expected.items():
        if os.path.normcase(os.environ.get(key, "")) != os.path.normcase(value):
            guard.fail("env", key)
    for key, value in {"PYTHONDONTWRITEBYTECODE": "1", "MPLBACKEND": "Agg", "QDAC_DISABLE": "1"}.items():
        if os.environ.get(key) != value:
            guard.fail("env", key)
    if Path.cwd().resolve() != root:
        guard.fail("env", "cwd")


def verify_config_raw(guard):
    import tomllib
    path = Path(os.environ["QUALIBRATE_CONFIG_FILE"])
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    # Preflight overlays before the framework can initialize its file logger.
    project = raw.get("qualibrate", {}).get("project")
    if project:
        overlay = path.parent / "projects" / project / "config.toml"
        if overlay.exists():
            def merge(target, source):
                for key, value in source.items():
                    if isinstance(value, dict) and isinstance(target.get(key), dict):
                        merge(target[key], value)
                    else:
                        target[key] = value
            merge(raw, tomllib.loads(overlay.read_text(encoding="utf-8")))
    section = raw.get("qualibrate", {})
    # Check explicit values before importing the framework (its logger has side effects).
    for name, value in (("storage.location", section.get("storage", {}).get("location")),
                        ("log_folder", section.get("log_folder")),
                        ("quam.state_path", raw.get("quam", {}).get("state_path"))):
        if not value or not inside(value, guard.scratch):
            guard.fail("config", name)
    if any(key.startswith("database") for key in section):
        guard.fail("config", "database")


def verify_config(job, guard):
    verify_config_raw(guard)
    path = Path(os.environ["QUALIBRATE_CONFIG_FILE"])
    # The merged config, including project overlays, must pass its own resolvers.
    from qualibrate.core.config.resolvers import get_settings, get_quam_state_path
    settings = get_settings(path)
    for name, value in (("storage.location", settings.storage.location),
                        ("log_folder", settings.log_folder),
                        ("quam.state_path", get_quam_state_path(settings))):
        if not value or not inside(value, guard.scratch):
            guard.fail("config", name)
    if settings.database is not None or settings.database_state is not None:
        guard.fail("config", "database")


def stub_class(guard, overrides):
    class Log:
        def __init__(self):
            self.records = []

        def __call__(self, *args, **kwargs):
            self.records.append(str(args))

        def __getattr__(self, name):
            if name in {"debug", "info", "warning", "error", "exception", "critical"}:
                return self
            return guard.block("node.log", name)

    class StubNode:
        instances = []

        def __class_getitem__(cls, item):
            return cls

        def __init__(self, name, parameters=None, machine=None, **kwargs):
            self.name = name
            self.parameters = None
            if parameters is not None:
                data = {**parameters.model_dump(), **overrides}
                if "simulate" in data:
                    data["simulate"] = True
                self.parameters = type(parameters).model_validate(data)
            self.machine = machine
            self.modes = types.SimpleNamespace(external=True, inspection=False, interactive=False)
            self.namespace = {}
            self.results = {}
            self.log = Log()
            self.ran = False
            self.instances.append(self)

        def run_action(self, func=None, *, skip_if=False, **kwargs):
            def decorate(action):
                if action.__name__ == "create_qua_program" and not skip_if:
                    action(self)
                    self.ran = True
                return action
            return decorate(func) if callable(func) else decorate

        def save(self, *args, **kwargs):
            guard.fail("node.save", "called: refused")

        def __getattr__(self, name):
            if any(word in name.lower() for word in ("save", "write", "store", "persist")):
                return guard.block("node.writer", name)
            raise AttributeError(name)

    return StubNode


def build(job, guard, result):
    node_file = Path(job["node_file"]).resolve()
    # Refuse without importing or executing the selected node.
    if node_file.stem.startswith("15p"):
        guard.fail("node", "unsupported execute path")
    verify_env(job, guard)
    verify_config(job, guard)
    guard.install_classes()
    source_stats = {}
    for folder in ("calibration_utils", "quam_config"):
        for path in (Path(job["library_root"]) / folder).rglob("*.py"):
            stat = path.stat()
            source_stats[str(path.resolve())] = [stat.st_mtime_ns, stat.st_size]
    replaced = result["replaced_hosts"]
    for filename in ("state.json", "wiring.json"):
        path = Path(job["chip_copy"]) / filename
        clean, paths = sanitize(json.loads(path.read_text(encoding="utf-8")), filename)
        path.write_text(json.dumps(clean), encoding="utf-8")
        replaced.extend(paths)
    library = guard.install_library(job["library_root"])
    # Pathless loads are bound to this request's freshly sanitized copy.
    original_load = library.Quam.load.__func__

    def load(cls, path=None, *args, **kwargs):
        if path is None:
            path = job["chip_copy"]
        if not isinstance(path, (str, Path)) or Path(path).resolve() != Path(job["chip_copy"]).resolve():
            guard.fail("Quam.load", str(path))
        return original_load(cls, path, *args, **kwargs)

    library.Quam.load = classmethod(load)
    import qualibrate
    stub = stub_class(guard, job.get("overrides", {}))
    qualibrate.QualibrationNode = stub
    module = types.ModuleType("_nodesim_node")
    module.__file__ = str(node_file)
    sys.modules[module.__name__] = module
    exec(compile(node_file.read_text(encoding="utf-8"), str(node_file), "exec"), module.__dict__)
    nodes = stub.instances
    if len(nodes) != 1 or not nodes[0].ran:
        raise RuntimeError("create_qua_program did not run")
    from qm import generate_qua_script
    script = generate_qua_script(nodes[0].namespace["qua_program"])
    if "SERIALIZATION VALIDATION ERROR" in script:
        raise RuntimeError("QUA serialization validation failed")
    imported = {}
    for module in list(sys.modules.values()):
        filename = getattr(module, "__file__", None)
        if filename and str(Path(filename).resolve()) in source_stats:
            path = Path(filename).resolve()
            stat = path.stat()
            current = [stat.st_mtime_ns, stat.st_size]
            if current != source_stats[str(path)]:
                guard.fail("source.changed", str(path))
            imported[str(path)] = current
    return {"program_script": script.split("\nconfig =", 1)[0],
            "replaced_hosts": replaced, "library_file": str(library.__file__),
            "logs": nodes[0].log.records, "imported_sources": imported}


def main():
    job = json.loads(sys.argv[1]) if len(sys.argv) > 1 else json.load(sys.stdin)
    guard = Guard(job["scratch"])  # P0a has no SaaS lease: empty network allow-list.
    result = dict(node=str(job["node_file"]), ok=False, program_script=None,
                  replaced_hosts=[], verdict=None, errors=[])
    try:
        if Path(job["node_file"]).stem.startswith("15p"):
            guard.fail("node", "unsupported execute path")
        verify_env(job, guard)
        verify_config_raw(guard)
        # Initialize installed console/native adapters before denying ctypes.
        # They do not read framework config or load the selected library.
        import numpy  # noqa: F401
        import colorama  # noqa: F401
        import dateutil.tz  # noqa: F401
        # Font discovery reads the Windows registry; its cache is env-pinned.
        import matplotlib.pyplot  # noqa: F401
        if os.name == "nt":
            import click._winconsole  # noqa: F401
            # Some adapters resolve these symbols lazily on the first output.
            import ctypes
            ctypes.windll.kernel32.SetConsoleTitleW
            ctypes.windll.shell32.CommandLineToArgvW
            ctypes.windll.kernel32.LocalFree
        guard.install_audit()
        # Framework initialization, including logging, is audited too.
        import qm  # noqa: F401
        import qualibrate  # noqa: F401
        import qualang_tools.multi_user  # noqa: F401
        import quam.components  # noqa: F401
        result.update(build(job, guard, result))
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
