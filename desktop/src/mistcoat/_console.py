"""Standard streams for the windowed Windows app in command-line mode.

The main Windows executable is a GUI program, so starting it never opens a
console window - which is exactly what a host application wants when it runs
Mistcoat as an invisible add-on. The catch: a GUI program starts with
``sys.stdout``/``sys.stderr`` set to None. This connects them to whatever
the caller provided, in order:

1. Handles the caller passed in (a pipe or file: how programs run Mistcoat
   hidden and read its JSON). Used as-is.
2. The console of the terminal it was typed into (AttachConsole), so a
   person running ``Mistcoat.exe info`` still sees the answer.
3. Nothing: the output is discarded. Use ``--json-file`` to get results.

It never creates a console of its own, so no window can flash up. On macOS
and Linux, and in the console build (mistcoat-cli.exe), the streams are
already fine and this does nothing.
"""
from __future__ import annotations

import io
import os
import sys


def ensure_std_streams() -> None:
    if sys.platform != "win32" or (sys.stdout is not None and sys.stderr is not None):
        return
    try:
        _connect_windows_streams()
    except Exception:  # noqa: BLE001 - never let stream setup break a run
        pass
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))


def _connect_windows_streams() -> None:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetStdHandle.argtypes = [wintypes.DWORD]
    k32.GetStdHandle.restype = wintypes.HANDLE
    k32.GetFileType.argtypes = [wintypes.HANDLE]
    k32.GetFileType.restype = wintypes.DWORD
    k32.AttachConsole.argtypes = [wintypes.DWORD]
    k32.AttachConsole.restype = wintypes.BOOL

    STD = {"stdin": -10, "stdout": -11, "stderr": -12}
    INVALID = wintypes.HANDLE(-1).value
    FILE_TYPE_UNKNOWN = 0
    ATTACH_PARENT_PROCESS = 0xFFFFFFFF

    def inherited(name: str):
        h = k32.GetStdHandle(STD[name] & 0xFFFFFFFF)
        if not h or h == INVALID or k32.GetFileType(h) == FILE_TYPE_UNKNOWN:
            return None
        return h

    def wrap(handle, mode: str):
        flags = os.O_RDONLY if mode == "r" else os.O_WRONLY
        fd = msvcrt.open_osfhandle(handle, flags | os.O_BINARY)
        raw = io.FileIO(fd, mode, closefd=False)
        if mode == "r":
            return io.TextIOWrapper(io.BufferedReader(raw), encoding="utf-8")
        return io.TextIOWrapper(io.BufferedWriter(raw), encoding="utf-8",
                                line_buffering=True, write_through=True)

    missing = [n for n in ("stdout", "stderr") if getattr(sys, n) is None]
    pending = []
    for name in missing:
        h = inherited(name)
        if h is not None:
            setattr(sys, name, wrap(h, "w"))
        else:
            pending.append(name)
    if sys.stdin is None:
        h = inherited("stdin")
        if h is not None:
            sys.stdin = wrap(h, "r")

    if pending and k32.AttachConsole(ATTACH_PARENT_PROCESS):
        # Started from a terminal with nothing redirected: write to it.
        for name in pending:
            setattr(sys, name, open("CONOUT$", "w", encoding="utf-8",
                                    buffering=1, errors="replace"))
