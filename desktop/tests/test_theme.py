"""Platform wording rules (no display needed)."""
from mistcoat.gui import theme


def test_mac_commands_are_title_case(monkeypatch):
    monkeypatch.setattr(theme, "IS_MAC", True)
    assert theme.command("Protect and save") == "Protect and Save"
    assert theme.command("Choose &output folder...") == "Choose &Output Folder…"
    assert theme.command("&How Mistcoat works") == "&How Mistcoat Works"
    assert theme.command("Zoom to &fit") == "Zoom to &Fit"
    assert theme.command("Protect and save (3)") == "Protect and Save (3)"


def test_other_platforms_keep_sentence_case(monkeypatch):
    monkeypatch.setattr(theme, "IS_MAC", False)
    assert theme.command("Choose output folder...") == "Choose output folder…"


def test_window_titles(monkeypatch):
    for mac, win, linux, expected in (
            (True, False, False, "a.png"),
            (False, True, False, "a.png - Mistcoat"),
            (False, False, True, "a.png — Mistcoat")):
        monkeypatch.setattr(theme, "IS_MAC", mac)
        monkeypatch.setattr(theme, "IS_WIN", win)
        monkeypatch.setattr(theme, "IS_LINUX", linux)
        assert theme.window_title("a.png", "Mistcoat") == expected
    assert theme.window_title(None, "Mistcoat") == "Mistcoat"
