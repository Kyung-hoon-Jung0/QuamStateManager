"""Child-only barriers for node program construction (D-8, D-10).

Python audit events do not cover native transports. The class barriers and
sanitized copy remain mandatory even when the audit tests pass.
"""
import os
from pathlib import Path
import sys


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
    def __init__(self, scratch, allowed_endpoints=()):
        self.scratch = Path(scratch).resolve()
        self.allowed_endpoints = frozenset((str(h), int(p)) for h, p in allowed_endpoints)
        self.verdict = None
        self.writes = []

    def fail(self, channel, detail):
        exc = GuardViolation(channel, detail)
        if self.verdict is None:
            self.verdict = exc.verdict
            print(self.verdict, flush=True)
        raise exc

    def write(self, path):
        if isinstance(path, int):
            self.fail("write", f"fd:{path}")
        path = str(Path(os.fsdecode(path)).resolve())
        if not inside(path, self.scratch):
            self.fail("write", path)
        self.writes.append(path)

    def audit(self, event, args):
        if event == "socket.connect":
            address = args[1]
            if not isinstance(address, tuple) or tuple(address[:2]) not in self.allowed_endpoints:
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
        elif (event.startswith(("subprocess.", "ctypes.", "winreg.", "os.exec", "os.spawn", "os.startfile"))
              or event in {"os.system", "os.kill", "os.killpg", "os.posix_spawn", "os.fork", "os.forkpty"}):
            self.fail(event, str(args[0]) if args else "refused")

    def install_audit(self):
        sys.addaudithook(self.audit)

    def block(self, channel, detail):
        def refused(*args, **kwargs):
            self.fail(channel, detail)
        return refused

    def install_classes(self, lease=None):
        """Patch real classes, including aliases imported before this call.

        P0a has no lease; therefore every QMM construction is refused.
        """
        from qm import QuantumMachinesManager
        original = QuantumMachinesManager.__init__

        def init(instance, *args, **kwargs):
            if lease is None or args or kwargs != lease:
                self.fail("QuantumMachinesManager", "unleased")
            original(instance, **kwargs)

        QuantumMachinesManager.__init__ = init
        QuantumMachinesManager.open_qm = self.block("open_qm", "refused")
        from qm.quantum_machine import QuantumMachine
        QuantumMachine.execute = self.block("execute", "refused")
        from qm.api.v2.qm_api import QmApi
        QmApi.execute = self.block("execute", "refused")
        from qm.api.v2.qmm_api import QmmApi
        QmmApi.open_qm = self.block("open_qm", "refused")
        import qualang_tools.multi_user as multi_user
        import importlib
        module = importlib.import_module(multi_user.qm_session.__module__)
        multi_user.qm_session = self.block("qm_session", "refused")
        # Also replace the defining module, not only the package export.
        module.qm_session = multi_user.qm_session
        from quam.components import BasicQuam
        BasicQuam.connect = self.block("Quam.connect", "refused")
        from quam.core.quam_classes import QuamRoot
        QuamRoot.save = self.block("machine.save", "refused")
        from qualibrate.core.storage.local_storage_manager import LocalStorageManager
        LocalStorageManager.save = self.block("storage.save", "refused")

    def install_library(self, library_root):
        import quam_config
        if not inside(quam_config.__file__, library_root):
            self.fail("library", str(Path(quam_config.__file__).resolve()))
        quam_config.Quam.connect = self.block("Quam.connect", "refused")
        quam_config.Quam.save = self.block("machine.save", "refused")
        return quam_config
