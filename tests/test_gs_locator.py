from pathlib import Path

import pytest

from pdftool.utils import gs_locator as G


@pytest.fixture(autouse=True)
def clear_cache():
    G.find_ghostscript.cache_clear()
    yield
    G.find_ghostscript.cache_clear()


def test_env_var_takes_priority(monkeypatch, tmp_path: Path):
    gs = tmp_path / "gswin64c.exe"
    gs.touch()
    monkeypatch.setenv("PDFTOOL_GS", str(gs))
    monkeypatch.setattr(G.shutil, "which", lambda name: None)
    assert G.find_ghostscript() == gs


def test_env_var_ignored_if_not_a_file(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("PDFTOOL_GS", str(tmp_path / "ない.exe"))
    monkeypatch.setattr(G.shutil, "which", lambda name: None)
    monkeypatch.setattr(G, "_WINDOWS_ROOTS", ())
    assert G.find_ghostscript() is None


def test_path_lookup(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("PDFTOOL_GS", raising=False)
    found = tmp_path / "gswin64c.exe"

    def which(name):
        return str(found) if name == "gswin64c" else None

    monkeypatch.setattr(G.shutil, "which", which)
    assert G.find_ghostscript() == found


def test_path_lookup_order_prefers_64bit(monkeypatch):
    monkeypatch.delenv("PDFTOOL_GS", raising=False)
    calls = []

    def which(name):
        calls.append(name)
        return f"C:/{name}.exe" if name in ("gswin64c", "gswin32c", "gs") else None

    monkeypatch.setattr(G.shutil, "which", which)
    result = G.find_ghostscript()
    assert result == Path("C:/gswin64c.exe")
    assert calls[0] == "gswin64c"


def test_program_files_fallback_picks_highest_version(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("PDFTOOL_GS", raising=False)
    monkeypatch.setattr(G.shutil, "which", lambda name: None)

    root = tmp_path / "gs"
    for ver in ("gs9.56.1", "gs10.03.1", "gs10.8.0"):
        d = root / ver / "bin"
        d.mkdir(parents=True)
        (d / "gswin64c.exe").touch()
    monkeypatch.setattr(G, "_WINDOWS_ROOTS", (root,))

    result = G.find_ghostscript()
    assert result == root / "gs10.8.0" / "bin" / "gswin64c.exe"


def test_not_found_returns_none(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("PDFTOOL_GS", raising=False)
    monkeypatch.setattr(G.shutil, "which", lambda name: None)
    monkeypatch.setattr(G, "_WINDOWS_ROOTS", (tmp_path / "no_such_dir",))
    assert G.find_ghostscript() is None


def test_result_is_cached(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("PDFTOOL_GS", raising=False)
    calls = {"n": 0}

    def which(name):
        calls["n"] += 1
        return str(tmp_path / "gswin64c.exe") if name == "gswin64c" else None

    monkeypatch.setattr(G.shutil, "which", which)
    G.find_ghostscript()
    G.find_ghostscript()
    assert calls["n"] == 1  # 2回目はキャッシュから


def test_ghostscript_version_success_and_failure(tmp_path: Path):
    # 存在しない実行ファイル: OSError を吸収して None
    assert G.ghostscript_version(tmp_path / "no.exe") is None
