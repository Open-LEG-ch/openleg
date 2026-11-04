# SPDX-License-Identifier: AGPL-3.0-or-later
"""Kernel-enforced network boundary for disposable village processes."""

import ctypes
import errno
import socket


def deny_internet():
    """Require a kernel filter before importing any application integrations."""
    library = ctypes.CDLL("libseccomp.so.2", use_errno=True)
    library.seccomp_init.argtypes = [ctypes.c_uint32]
    library.seccomp_init.restype = ctypes.c_void_p
    library.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    library.seccomp_syscall_resolve_name.restype = ctypes.c_int
    library.seccomp_load.argtypes = [ctypes.c_void_p]
    library.seccomp_release.argtypes = [ctypes.c_void_p]

    class Compare(ctypes.Structure):
        _fields_ = [
            ("arg", ctypes.c_uint),
            ("op", ctypes.c_uint),
            ("a", ctypes.c_uint64),
            ("b", ctypes.c_uint64),
        ]

    library.seccomp_rule_add_array.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_uint,
        ctypes.POINTER(Compare),
    ]
    context = library.seccomp_init(0x7FFF0000)
    if not context:
        raise RuntimeError("sandbox network filter unavailable")
    try:
        for syscall in (b"socket", b"socketpair"):
            number = library.seccomp_syscall_resolve_name(syscall)
            # Only Unix sockets are required by PostgreSQL and the HTTP listener.
            comparison = Compare(0, 1, socket.AF_UNIX, 0)  # SCMP_CMP_NE
            if (
                number < 0
                or library.seccomp_rule_add_array(
                    context,
                    0x00050000 | errno.EPERM,
                    number,
                    1,
                    ctypes.byref(comparison),
                )
                != 0
            ):
                raise RuntimeError("sandbox network filter unavailable")
        if library.seccomp_load(context) != 0:
            raise RuntimeError("sandbox network filter unavailable")
    finally:
        library.seccomp_release(context)
