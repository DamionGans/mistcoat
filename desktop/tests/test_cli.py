"""The command line as other programs use it: JSON, exit codes, outputs."""
from __future__ import annotations

import io
import json

import numpy as np
import pytest
from PIL import Image

from mistcoat import cli

FAST = ["--quick", "--no-harden", "--json"]


@pytest.fixture
def art(tmp_path):
    rng = np.random.default_rng(3)
    path = tmp_path / "piece.png"
    Image.fromarray((rng.random((48, 64, 3)) * 255).astype(np.uint8)).save(path)
    return path


def run(capsys, *args):
    code = cli.main([str(a) for a in args])
    out = capsys.readouterr().out
    return code, json.loads(out) if out.strip() else None


def test_protect_to_exact_file(capsys, art, tmp_path):
    dest = tmp_path / "out" / "result.jpg"
    code, doc = run(capsys, "protect", art, "--output-file", dest, *FAST)
    assert code == cli.EXIT_OK and doc["ok"]
    r = doc["results"][0]
    assert r["status"] == "ok" and r["output"] == str(dest)
    assert Image.open(dest).format == "JPEG"       # extension picks the format
    assert doc["summary"] == {"total": 1, "succeeded": 1, "skipped": 0, "failed": 0}


def test_default_output_is_protected_folder_next_to_original(capsys, art):
    code, doc = run(capsys, "protect", art, *FAST)
    assert code == 0
    assert doc["results"][0]["output"] == str(art.parent / "Protected" / "piece_protected.png")


@pytest.mark.parametrize("mode,status,code", [
    ("rename", "ok", 0), ("skip", "skipped", 0), ("error", "failed", 1), ("overwrite", "ok", 0)])
def test_on_exists(capsys, art, tmp_path, mode, status, code):
    dest = tmp_path / "taken.png"
    dest.write_bytes(b"existing")
    rc, doc = run(capsys, "protect", art, "--output-file", dest, "--on-exists", mode, *FAST)
    r = doc["results"][0]
    assert (rc, r["status"]) == (code, status)
    if mode == "rename":
        assert r["output"] == str(tmp_path / "taken (2).png")
        assert dest.read_bytes() == b"existing"
    if mode == "overwrite":
        assert dest.read_bytes() != b"existing"


def test_never_overwrites_the_original(capsys, art):
    before = art.read_bytes()
    rc, doc = run(capsys, "protect", art, "--output-file", art, "--on-exists", "overwrite", *FAST)
    assert rc == cli.EXIT_FAILED and "original" in doc["results"][0]["error"]
    assert art.read_bytes() == before


def test_exit_codes(capsys, art, tmp_path):
    assert run(capsys, "protect", tmp_path / "nothing*.png", "--json")[0] == cli.EXIT_NO_INPUT
    rc, doc = run(capsys, "protect", art, "--preset", "nonsense", "--json")
    assert rc == cli.EXIT_USAGE and doc["ok"] is False
    other = tmp_path / "other.png"
    Image.new("RGB", (32, 24)).save(other)
    rc, doc = run(capsys, "protect", art, other, "--output-file", tmp_path / "x.png", "--json")
    assert rc == cli.EXIT_USAGE and "exactly one" in doc["error"]


def test_info_and_passes(capsys):
    rc, doc = run(capsys, "info", "--json")
    assert rc == 0 and doc["ok"] and "png" in doc["input_formats"]
    assert {p["key"] for p in doc["presets"]} >= {"light", "balanced", "strong", "maximum"}
    assert "using" in doc["gpu"]
    rc, doc = run(capsys, "passes", "--json")
    assert rc == 0 and len(doc["passes"]) == 5


def test_json_file_without_stdout(capsys, art, tmp_path):
    report = tmp_path / "report.json"
    rc = cli.main(["protect", str(art), "--output-file", str(tmp_path / "o.png"),
                   "--quick", "--no-harden", "--quiet", "--json-file", str(report)])
    assert rc == 0 and capsys.readouterr().out == ""
    assert json.loads(report.read_text(encoding="utf-8"))["ok"]


def test_serve(monkeypatch, capsys, art, tmp_path):
    requests = [
        {"id": 1, "command": "ping"},
        {"id": 2, "command": "protect", "input": str(art), "output_file": str(tmp_path / "s.png"),
         "quick": True, "no_harden": True, "progress": True},
        {"id": 3, "command": "protect", "input": str(tmp_path / "missing.png")},
        {"id": 4, "command": "quit"},
    ]
    monkeypatch.setattr("sys.stdin", io.StringIO("\n".join(json.dumps(r) for r in requests) + "\n"))
    assert cli.main(["serve"]) == 0
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    replies = {m["id"]: m for m in lines if "id" in m and m.get("event") != "progress"}
    assert lines[0]["event"] == "ready"
    assert replies[1]["result"] == "pong"
    assert replies[2]["ok"] and (tmp_path / "s.png").exists()
    assert any(m.get("event") == "progress" and m["id"] == 2 for m in lines)
    assert replies[3]["ok"] is False and "not found" in replies[3]["error"]
    assert replies[4]["result"] == "bye"


@pytest.mark.parametrize("argv,is_cli", [
    (["Mistcoat"], False),
    (["Mistcoat", "a.png"], False),
    (["Mistcoat", "--files-from", "list.txt"], False),
    (["Mistcoat", "--selftest", "x.json"], False),
    (["Mistcoat", "protect", "a.png"], True),
    (["Mistcoat", "--no-gpu", "info"], True),
    (["Mistcoat", "--version"], True),
])
def test_cli_or_window(argv, is_cli):
    assert cli.is_cli_invocation(argv) is is_cli


def test_open_args_and_file_lists(tmp_path, monkeypatch):
    from mistcoat.gui.handoff import parse_open_args
    monkeypatch.chdir(tmp_path)
    listing = tmp_path / "lists" / "sel.txt"
    listing.parent.mkdir()
    listing.write_text("# comment\n\n\"C:/abs/one.png\"\nrel/two.png\n", encoding="utf-8")
    req = parse_open_args(["Mistcoat", "a.png", "--files-from", str(listing),
                           "--new-window", "--", "-dash.png"])
    names = [p.name for p in req.paths]
    assert names == ["a.png", "one.png", "two.png", "-dash.png"]
    assert req.new_window and not req.problems
    assert all(p.is_absolute() for p in req.paths)
    assert req.paths[2] == listing.parent / "rel" / "two.png"   # relative to the list
    assert parse_open_args(["Mistcoat", "--files-from", str(tmp_path / "nope.txt")]).problems


def test_second_launch_hands_files_to_open_window(tmp_path, monkeypatch):
    """A later launch with files queues them in the open window and exits."""
    import os
    import subprocess
    import sys
    import threading

    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from mistcoat.gui import handoff
    monkeypatch.setattr(handoff, "SERVER_NAME", f"mistcoat-test-{os.getpid()}")
    app = QApplication.instance() or QApplication([])
    receiver = handoff.Receiver(app)
    got = []
    receiver.received.connect(got.extend)

    sent = {}
    code = (f"import sys; from pathlib import Path\n"
            f"from PySide6.QtCore import QCoreApplication\n"
            f"from mistcoat.gui import handoff\n"
            f"handoff.SERVER_NAME = {handoff.SERVER_NAME!r}\n"
            f"app = QCoreApplication([])\n"
            f"sys.exit(0 if handoff.send_to_running([Path(r'{tmp_path / 'a.png'}')]) else 1)\n")
    t = threading.Thread(target=lambda: sent.update(code=subprocess.run(
        [sys.executable, "-c", code], timeout=60).returncode))
    t.start()
    while t.is_alive():
        app.processEvents()
        t.join(0.02)
    assert sent["code"] == 0
    app.processEvents()
    assert [p.name for p in got] == ["a.png"]
    receiver.server.close()
