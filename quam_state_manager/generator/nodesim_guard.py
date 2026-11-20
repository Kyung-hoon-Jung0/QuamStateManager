"""Child-only barriers for node program construction (D-8, D-10).

Python audit events do not cover native transports. The class barriers and
sanitized copy remain mandatory even when the audit tests pass.
"""
import os
from pathlib import Path
import sys
import weakref

#: the one registry key a post-build HTTP client may read (its proxy settings)
PROXY_KEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
#: winreg access mask KEY_READ; any other mask is a write or a wider read
KEY_READ = 0x20019
LOOPBACK = ("127.0.0.1", "::1")


def library_fingerprint(library):
    """Sorted source stats shared by the cache and child, without importing lab packages."""
    sources = []
    pending = [(os.fspath(library), "")]
    while pending:
        directory, prefix = pending.pop()
        with os.scandir(directory) as scan:
            entries = list(scan)
        if any(entry.name == "pyvenv.cfg" for entry in entries):
            continue
        for entry in entries:
            name = entry.name
            relpath = prefix + name
            if entry.is_dir(follow_symlinks=False):
                if not name.startswith(".") and name not in {"__pycache__", "node_modules", "site-packages"}:
                    pending.append((entry.path, relpath + "/"))
            elif entry.is_file() and name.endswith(".py"):
                stat = entry.stat()
                sources.append((relpath, stat.st_mtime_ns, stat.st_size))
    return sorted(sources)


class GuardViolation(RuntimeError):
    """A refused operation, with a stable machine-readable verdict."""

    def __init__(self, channel, detail):
        self.verdict = f"GUARD FAIL {channel} {str(detail).replace(chr(10), ' ').replace(chr(13), ' ')}"
        super().__init__(self.verdict)


def inside(path, root):
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except (ValueError, TypeError, OSError):
        return False


class Guard:
    """Audit-hook and class barriers for one child (D-8).

    The build runs with the allow-list fixed at construction (empty for a node
    build). After the build, SM-owned code may widen it exactly once with
    ``allow`` (the cloud login endpoint and the run folder) and then set the
    cloud instance lease exactly once with ``set_lease``. Nothing ever widens
    ``open_qm``, ``execute`` or ``qm_session``.
    """

    def __init__(self, scratch, allowed_endpoints=()):
        self.scratch = Path(scratch).resolve()
        self.allowed_endpoints = frozenset((str(h), int(p)) for h, p in allowed_endpoints)
        self.write_roots = (self.scratch,)
        self.verdict = None
        self.writes = []
        self.build_ended = False
        self.allowed = False
        self.lease_params = None
        self._loopback = []
        self._proxy_keys = set()
        self.barriers = {}
        self.barrier_assignments = []
        self.library_patches = []

    def end_build(self):
        """Mark the node build as finished; ``allow`` is refused before this."""
        self.build_ended = True

    def allow(self, endpoints, write_roots=()):
        """Widen the allow-list once, after the build: resolved ``(ip, port)``
        pairs and extra write roots. A second call, or one before
        ``end_build``, is a guard failure."""
        if not self.build_ended:
            self.fail("allow", "before build end")
        if self.allowed:
            self.fail("allow", "called twice")
        self.allowed = True
        self.allowed_endpoints = frozenset(self.allowed_endpoints
                                           | {(str(h), int(p)) for h, p in endpoints})
        self.write_roots = self.write_roots + tuple(Path(root).resolve() for root in write_roots)

    def set_lease(self, params, endpoints):
        """Set the one QuantumMachinesManager lease (the spawned instance's
        connection parameters) and allow that instance's resolved endpoints.
        Only after ``allow`` and only once."""
        if not self.allowed:
            self.fail("lease", "before allow")
        if self.lease_params is not None:
            self.fail("lease", "set twice")
        self.lease_params = params
        self.allowed_endpoints = frozenset(self.allowed_endpoints
                                           | {(str(h), int(p)) for h, p in endpoints})

    def lease(self):
        return self.lease_params

    def _own_loopback(self, address):
        """True when *address* is a loopback socket this process bound after
        ``allow`` (the event loop's self-pipe on Windows); it reaches no host."""
        if not self.allowed or address[0] not in LOOPBACK:
            return False
        live = []
        found = False
        for ref in self._loopback:
            sock = ref()
            if sock is None:
                continue
            live.append(ref)
            try:
                if tuple(sock.getsockname()[:2]) == tuple(address[:2]):
                    found = True
            except OSError:
                pass
        self._loopback = live
        return found

    def _registry_read(self, event, args):
        """Post-build: the HTTP client's read-only proxy lookup is allowed."""
        if not self.allowed:
            return False
        if event == "winreg.OpenKey":
            return len(args) >= 3 and str(args[1]) == PROXY_KEY and args[2] == KEY_READ
        if event == "winreg.OpenKey/result":
            self._proxy_keys.add(args[0])
            return True
        if event == "winreg.QueryValue":
            return bool(args) and args[0] in self._proxy_keys
        return False

    def fail(self, channel, detail):
        exc = GuardViolation(channel, detail)
        if self.verdict is None:
            self.verdict = exc.verdict
            print(self.verdict, flush=True)
        raise exc

    def write(self, path):
        if isinstance(path, int):
            self.fail("write", f"fd:{path}")
        raw = os.fsdecode(path)
        # D-33: exactly os.devnull is a sink; it reaches no file and no host.
        if os.path.normcase(raw) == os.path.normcase(os.devnull):
            return
        path = str(Path(raw).resolve())
        if not any(inside(path, root) for root in self.write_roots):
            self.fail("write", path)
        self.writes.append(path)

    def audit(self, event, args):
        if event == "socket.bind":
            address = args[1] if len(args) > 1 else None
            if self.allowed and isinstance(address, tuple) and address[0] in LOOPBACK:
                try:
                    self._loopback.append(weakref.ref(args[0]))
                except TypeError:
                    pass
        elif event == "socket.connect":
            address = args[1]
            if not isinstance(address, tuple) or (tuple(address[:2]) not in self.allowed_endpoints
                                                  and not self._own_loopback(address)):
                detail = f"{address[0]}:{address[1]}" if isinstance(address, tuple) else str(address)
                self.fail("socket.connect", detail)
        elif event == "open":
            path, mode, flags = args
            if (mode and any(c in mode for c in "wax+")) or flags & (
                os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
            ):
                self.write(path)
        elif event in {"os.rename", "os.remove", "os.rmdir", "os.mkdir", "os.chmod",
                       "os.utime", "os.truncate", "os.link", "os.symlink", "shutil.copyfile"}:
            # Refuse dir_fd paths rather than resolve them against the wrong cwd.
            fd_indices = {"os.rename": (2, 3), "os.remove": (1,), "os.rmdir": (1,),
                          "os.mkdir": (2,), "os.chmod": (2,), "os.utime": (3,),
                          "os.link": (2, 3), "os.symlink": (2,)}
            if any(index < len(args) and args[index] not in (None, -1)
                   for index in fd_indices.get(event, ())):
                self.fail("write", "dir_fd")
            count = 2 if event in {"os.rename", "os.link", "os.symlink"} else 1
            paths = args[1:2] if event == "shutil.copyfile" else args[:count]
            for path in paths:
                self.write(path)
        elif event.startswith("winreg.") and self._registry_read(event, args):
            return
        elif (event.startswith(("subprocess.", "ctypes.", "winreg.", "os.exec", "os.spawn", "os.startfile"))
              or event in {"os.system", "os.kill", "os.killpg", "os.posix_spawn", "os.fork", "os.forkpty"}):
            self.fail(event, str(args[0]) if args else "refused")

    def install_audit(self):
        sys.addaudithook(self.audit)

    def block(self, channel, detail):
        def refused(*args, **kwargs):
            self.fail(channel, detail)
        return refused

    # R3-1: every name the sim step constructs, captured before any library or node code runs.
    SIM_NAMES = (("qm", "QuantumMachinesManager"), ("qm", "SimulationConfig"))
    # Cold sim step only; captured when the child imported qm_saas before the audit hook.
    SAAS_NAMES = (("qm_saas", "QmSaas"), ("qm_saas", "QOPVersion"), ("qm_saas", "ClusterConfig"))

    def capture_sim_classes(self):
        """Capture every sim-step class into ONE dict, keyed "<module>.<name>" (FI4-1, R3-1).

        The sim step constructs only these objects; ``check_sim_bindings`` pins each module binding.
        qm_saas is never imported here: its names join only when the module is already loaded.
        """
        import importlib
        self.sim_classes, self.sim_owners = {}, {}
        for module_name, attribute in self.SIM_NAMES + self.SAAS_NAMES:
            if (module_name, attribute) in self.SAAS_NAMES and module_name not in sys.modules:
                continue
            module = importlib.import_module(module_name)
            key = module_name + "." + attribute
            self.sim_owners[key] = (module, attribute)
            self.sim_classes[key] = vars(module)[attribute]

    def install_classes(self, lease=None):
        """Patch real classes, including aliases imported before this call.

        *lease* is a dict or a callable read at construction time. None means
        the guard's own late lease (``set_lease``), which stays unset in a plain
        build, so every QMM construction is refused there.
        """
        self.capture_sim_classes()
        QuantumMachinesManager = self.sim_classes["qm.QuantumMachinesManager"]
        original = QuantumMachinesManager.__init__
        source = self.lease if lease is None else lease

        def init(instance, *args, **kwargs):
            current = source() if callable(source) else source
            if current is None or args or kwargs != current:
                self.fail("QuantumMachinesManager", "unleased")
            original(instance, **kwargs)

        from qm.quantum_machine import QuantumMachine
        from qm.api.v2.qm_api import QmApi
        from qm.api.v2.qmm_api import QmmApi
        import qualang_tools.multi_user as multi_user
        import importlib
        module = importlib.import_module(multi_user.qm_session.__module__)
        from quam.components import BasicQuam
        from quam.core.quam_classes import QuamRoot
        from qualibrate.core.storage.local_storage_manager import LocalStorageManager
        session = self.block("qm_session", "refused")
        self.barrier_assignments = [
            ("__init__", QuantumMachinesManager, "__init__", init),
            ("open_qm", QuantumMachinesManager, "open_qm", self.block("open_qm", "refused")),
            ("QuantumMachine.execute", QuantumMachine, "execute", self.block("execute", "refused")),
            ("QmApi.execute", QmApi, "execute", self.block("execute", "refused")),
            ("QmmApi.open_qm", QmmApi, "open_qm", self.block("open_qm", "refused")),
            ("qm_session", multi_user, "qm_session", session),
            ("qm_session.definition", module, "qm_session", session),
            ("BasicQuam.connect", BasicQuam, "connect", self.block("Quam.connect", "refused")),
            ("QuamRoot.save", QuamRoot, "save", self.block("machine.save", "refused")),
            ("LocalStorageManager.save", LocalStorageManager, "save", self.block("storage.save", "refused")),
        ]
        self.reassert_barriers()

    def remember(self, name, owner, attribute):
        """Pin the installed descriptor for checks after node construction."""
        self.barriers[name] = (owner, attribute, vars(owner)[attribute])

    def check_barriers(self):
        """Refuse a node that changed a barrier after library installation."""
        for name, (owner, attribute, expected) in self.barriers.items():
            if vars(owner).get(attribute) is not expected:
                self.fail("barrier", name + " replaced")

    def reassert_barriers(self):
        """Restore all barrier bindings and pin them before node code runs."""
        changed = []
        for name, owner, attribute, barrier in self.barrier_assignments:
            if name not in {"Quam.connect", "Quam.save"} and vars(owner).get(attribute) is not barrier:
                changed.append(name)
            setattr(owner, attribute, barrier)
            self.remember(name, owner, attribute)
        return changed

    def check_sim_bindings(self):
        """Refuse a node that rebound any captured sim-step name (FI4-1, R3-1); return the captured dict."""
        self.check_barriers()
        for key, (module, attribute) in self.sim_owners.items():
            if vars(module).get(attribute) is not self.sim_classes[key]:
                self.fail("barrier", key + " replaced")
        return dict(self.sim_classes)

    def install_library(self, library_root):
        import quam_config
        if not inside(quam_config.__file__, library_root):
            self.fail("library", str(Path(quam_config.__file__).resolve()))
        self.barrier_assignments.extend([
            ("Quam.connect", quam_config.Quam, "connect", self.block("Quam.connect", "refused")),
            ("Quam.save", quam_config.Quam, "save", self.block("machine.save", "refused")),
        ])
        self.library_patches = self.reassert_barriers()
        if self.library_patches:
            print("library patched at import, barrier restored: " + ", ".join(self.library_patches), flush=True)
        return quam_config
