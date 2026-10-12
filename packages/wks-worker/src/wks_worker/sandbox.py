"""Linux decoder sandbox: Landlock file scopes and seccomp network/process isolation.

Applied after Python/bootstrap imports and before opening source content. It does
not sandbox the broker that must reach PostgreSQL/storage and authorized providers.
"""

import ctypes
import os
import sys
from pathlib import Path

from wks_core.domain.models import Error


def availability():
    if sys.platform != "linux":
        return {"network": False, "filesystem": False}
    try:
        ctypes.CDLL("libseccomp.so.2")
        network = True
    except OSError:
        network = False
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    return {"network": network, "filesystem": libc.syscall(444, ctypes.c_void_p(), 0, 1) >= 1}


def restrict(directory, model_paths=(), filesystem_required=True):
    if sys.platform != "linux":
        raise Error("processing.sandbox_unavailable", "Decoder sandbox requires Linux")
    filesystem = restrict_filesystem(directory, model_paths, True) if filesystem_required else False
    restrict_network()
    return {
        "network": "seccomp",
        "filesystem": "landlock" if filesystem else "disabled_by_policy",
        "environment": "broker_credentials_removed",
    }


def restrict_filesystem(directory, model_paths=(), filesystem_required=True):
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    abi = libc.syscall(444, ctypes.c_void_p(), 0, 1)
    if abi < 1:
        if filesystem_required:
            raise Error(
                "processing.sandbox_unavailable", "Landlock is required for strict decoding"
            )
        return False
    handled = (1 << 13) - 1
    if abi >= 2:
        handled |= 1 << 13
    if abi >= 3:
        handled |= 1 << 14

    class Ruleset(ctypes.Structure):
        _fields_ = [("handled_access_fs", ctypes.c_uint64)]

    class PathRule(ctypes.Structure):
        _pack_ = 1
        _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]

    attribute = Ruleset(handled)
    descriptor = libc.syscall(444, ctypes.byref(attribute), ctypes.sizeof(attribute), 0)
    if descriptor < 0:
        raise Error("processing.sandbox_unavailable", "Cannot create decoder filesystem scope")

    def allow(path, writable=False):
        path = Path(path).resolve()
        if not path.exists():
            return
        access = (
            handled
            if writable and path.is_dir()
            else (1 | 4 | 8)
            if path.is_dir()
            else 4 | (2 if writable else 0)
        )
        fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
        try:
            rule = PathRule(access, fd)
            if libc.syscall(445, descriptor, 1, ctypes.byref(rule), 0) < 0:
                raise Error("processing.sandbox_unavailable", "Cannot restrict decoder filesystem")
        finally:
            os.close(fd)

    try:
        for path in (
            "/usr",
            "/lib",
            "/lib64",
            "/etc/ld.so.cache",
            "/etc/fonts",
            "/etc/mime.types",
            "/etc/httpd/mime.types",
            "/etc/httpd/conf/mime.types",
            "/etc/apache/mime.types",
            "/etc/apache2/mime.types",
            "/etc/localtime",
            "/proc/cpuinfo",
            "/proc/meminfo",
            "/dev/urandom",
            "/dev/random",
            sys.prefix,
            sys.base_prefix,
            sys.base_exec_prefix,
            Path(__file__).resolve().parents[3],
            *model_paths,
        ):
            if path:
                allow(path)
        allow(directory, writable=True)
        allow("/dev/null", writable=True)
        if libc.prctl(38, 1, 0, 0, 0) != 0 or libc.syscall(446, descriptor, 0) < 0:
            raise Error(
                "processing.sandbox_unavailable", "Cannot activate decoder filesystem scope"
            )
    finally:
        os.close(descriptor)
    return True


def restrict_network():
    library = "libseccomp.so.2"
    try:
        seccomp = ctypes.CDLL(library)
        seccomp.seccomp_init.argtypes = [ctypes.c_uint32]
        seccomp.seccomp_init.restype = ctypes.c_void_p
        seccomp.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
        seccomp.seccomp_syscall_resolve_name.restype = ctypes.c_int
        seccomp.seccomp_rule_add.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_int,
            ctypes.c_uint,
        ]
        seccomp.seccomp_load.argtypes = [ctypes.c_void_p]
        seccomp.seccomp_release.argtypes = [ctypes.c_void_p]
        context = seccomp.seccomp_init(0x7FFF0000)
        if not context:
            raise RuntimeError()
        try:
            # All Internet/Unix sockets are disabled; decoders need no sockets.
            for name in (
                "socket",
                "socketpair",
                "connect",
                "bind",
                "ptrace",
                "process_vm_readv",
                "process_vm_writev",
                "bpf",
                "perf_event_open",
                "mount",
                "umount2",
                "open_by_handle_at",
            ):
                number = seccomp.seccomp_syscall_resolve_name(name.encode())
                if number >= 0 and seccomp.seccomp_rule_add(context, 0x00050001, number, 0) < 0:
                    raise RuntimeError()
            if seccomp.seccomp_load(context) < 0:
                raise RuntimeError()
        finally:
            seccomp.seccomp_release(context)
    except (OSError, RuntimeError):
        raise Error("processing.sandbox_unavailable", "Seccomp is required for decoding") from None
