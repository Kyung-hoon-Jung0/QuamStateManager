"""Windows job ownership: an owner's death closes and stops its process tree."""
import ctypes
from ctypes import wintypes


class _Limits(ctypes.Structure):
    _fields_ = [("user", ctypes.c_int64), ("job_user", ctypes.c_int64),
                ("flags", wintypes.DWORD), ("min_ws", ctypes.c_size_t),
                ("max_ws", ctypes.c_size_t), ("active", wintypes.DWORD),
                ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                ("scheduling", wintypes.DWORD)]


class _Extended(ctypes.Structure):
    _fields_ = [("basic", _Limits), ("io", ctypes.c_uint64 * 6),
                ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]


class Job:
    def __init__(self):
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.api.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.api.OpenThread.restype = wintypes.HANDLE
        self.api.ResumeThread.argtypes = [wintypes.HANDLE]
        self.api.ResumeThread.restype = wintypes.DWORD
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = _Extended()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def assign(self, process_handle):
        if not self.api.AssignProcessToJobObject(self.handle, int(process_handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def resume(self, pid):
        import psutil
        tid = psutil.Process(pid).threads()[0].id
        thread = self.api.OpenThread(2, False, tid)  # THREAD_SUSPEND_RESUME
        if not thread:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if self.api.ResumeThread(thread) == 0xFFFFFFFF:
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            self.api.CloseHandle(thread)

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None
