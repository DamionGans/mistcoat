"""Opening the app with files, and handing them to an already-open window.

    Mistcoat a.png b.jpg "Folder with art"
    Mistcoat --files-from selection.txt        (one path per line; "-" = stdin)
    Mistcoat --new-window a.png                (do not reuse an open window)

An art-management program can start Mistcoat this way to queue its current
selection. List files avoid Windows' ~32,000-character command-line limit.

If Mistcoat is already open (same user), the paths are passed to that window
over a local socket and it comes to the front, instead of a second window
appearing. The message is JSON: {"paths": [absolute paths...]}.
"""
from __future__ import annotations

import getpass
import hashlib
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

# Per user, so two people on one machine never share a window.
SERVER_NAME = "mistcoat-" + hashlib.sha1(
    getpass.getuser().encode("utf-8", "replace")).hexdigest()[:12]
CONNECT_MS = 400
REPLY_MS = 10_000
log = logging.getLogger("mistcoat")


@dataclass
class OpenRequest:
    paths: list[Path] = field(default_factory=list)
    new_window: bool = False
    problems: list[str] = field(default_factory=list)


def parse_open_args(argv: list[str]) -> OpenRequest:
    """Paths to queue from the app's arguments. Unknown options are ignored
    (Qt and the OS add some of their own)."""
    req = OpenRequest()
    args = list(argv[1:])
    i, only_paths = 0, False
    while i < len(args):
        a = args[i]
        i += 1
        if only_paths or not a.startswith("-") or a == "-":
            if a != "-":
                req.paths.append(Path(a))
            continue
        if a == "--":
            only_paths = True
        elif a == "--new-window":
            req.new_window = True
        elif a == "--files-from" or a.startswith("--files-from="):
            if "=" in a:
                src = a.split("=", 1)[1]
            elif i < len(args):
                src, i = args[i], i + 1
            else:
                req.problems.append("--files-from needs a file name")
                continue
            req.paths.extend(_read_list(src, req.problems))
        elif a == "-platform" and i < len(args):
            i += 1          # Qt's own option and its value
    # Absolute now: a running window has a different working directory.
    req.paths = [p if p.is_absolute() else (Path.cwd() / p) for p in req.paths]
    return req


def _read_list(src: str, problems: list[str]) -> list[Path]:
    try:
        text = sys.stdin.read() if src == "-" else Path(src).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError, AttributeError) as exc:
        problems.append(f"could not read file list {src}: {exc}")
        return []
    base = Path.cwd() if src == "-" else Path(src).resolve().parent
    out = []
    for line in text.splitlines():
        line = line.strip().strip('"')
        if line and not line.startswith("#"):
            p = Path(line)
            out.append(p if p.is_absolute() else base / p)   # relative to the list
    return out


def send_to_running(paths: list[Path]) -> bool:
    """Give the paths to an open Mistcoat window. True if one took them."""
    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if not sock.waitForConnected(CONNECT_MS):
        log.info("no open window to hand files to (%s)", sock.errorString())
        return False
    _allow_foreground()
    sock.write(json.dumps({"paths": [str(p) for p in paths]}).encode("utf-8") + b"\n")
    sock.flush()
    # waitForBytesWritten is false when everything was already sent (usual on
    # Windows), so it is not a failure signal - only the reply counts. A window
    # busy loading a large image may take a moment to answer.
    sock.waitForBytesWritten(2000)
    ok = sock.waitForReadyRead(REPLY_MS) and bytes(sock.readAll()).strip() == b"ok"
    if not ok:
        log.warning("the open window did not confirm the handoff (%s)", sock.errorString())
    sock.disconnectFromServer()
    return ok


def _allow_foreground() -> None:
    """Windows only lets the foreground app raise another app's window; let
    the running Mistcoat come to the front when it receives the files."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.AllowSetForegroundWindow(-1)   # ASFW_ANY
        except Exception:  # noqa: BLE001
            pass


class Receiver(QObject):
    """Listens for paths from later launches while the window is open."""

    received = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.UserAccessOption)
        if not self.server.listen(SERVER_NAME):
            # A stale socket from a crashed run (Unix) - clear it and retry.
            QLocalServer.removeServer(SERVER_NAME)
            self.server.listen(SERVER_NAME)
        self.server.newConnection.connect(self._on_connection)

    def _on_connection(self) -> None:
        while self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            sock.readyRead.connect(lambda s=sock: self._read(s))
            # The message may already be here, in which case readyRead has
            # fired before we connected to it and would never fire again.
            if sock.bytesAvailable():
                self._read(sock)

    def _read(self, sock: QLocalSocket) -> None:
        if not sock.canReadLine():
            return
        try:
            msg = json.loads(bytes(sock.readLine()).decode("utf-8"))
            paths = [Path(p) for p in msg.get("paths", []) if isinstance(p, str)]
        except (ValueError, AttributeError):
            sock.write(b"error\n")
        else:
            sock.write(b"ok\n")
            log.info("received %d path(s) from another launch", len(paths))
            self.received.emit(paths)
        sock.flush()
        sock.disconnectFromServer()
