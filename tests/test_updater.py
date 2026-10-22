import io
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from pdftool.utils import updater


def test_parse_and_compare():
    assert updater.parse_version("v0.2.10") == (0, 2, 10)
    assert updater.is_newer("v0.10.0", "0.9.9")
    assert not updater.is_newer("0.1.0", "0.1.0")
    assert not updater.is_newer("garbage", "0.1.0")


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def _opener(payload):
    return lambda req, timeout=None: _Resp(json.dumps(payload).encode())


def test_check_latest_finds_asset():
    data = {"tag_name": "v9.0.0", "body": "メモ", "assets": [
        {"name": "other.zip", "browser_download_url": "x"},
        {"name": updater.ASSET_NAME, "browser_download_url": "https://e/x.zip", "size": 5, "digest": "sha256:ab"},
    ]}
    info = updater.check_latest("0.1.0", opener=_opener(data))
    assert info and info.version == "9.0.0" and info.url == "https://e/x.zip" and info.sha256 == "ab"
    assert updater.check_latest("9.0.0", opener=_opener(data)) is None
    assert updater.check_latest("0.1.0", opener=_opener({"tag_name": "v9.0.0", "assets": []})) is None


def _make_release_zip(path: Path, version: str) -> None:
    """release.py と同じ形（フォルダ名ごと）の zip を作る。"""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("PDFユーティリティ/PDFユーティリティ.exe", f"exe {version}")
        z.writestr("PDFユーティリティ/_internal/lib.txt", f"lib {version}")


def test_extract_update_finds_app_folder(tmp_path: Path):
    zp = tmp_path / "u.zip"
    _make_release_zip(zp, "2")
    root = updater.extract_update(zp, tmp_path / "_update")
    assert (root / "_internal" / "lib.txt").read_text() == "lib 2"
    assert not zp.exists()


def test_extract_update_rejects_unknown_zip(tmp_path: Path):
    zp = tmp_path / "u.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("readme.txt", "x")
    with pytest.raises(RuntimeError):
        updater.extract_update(zp, tmp_path / "_update")
    assert not (tmp_path / "_update").exists()


def test_apply_writes_batch(tmp_path: Path, monkeypatch):
    launched = []
    monkeypatch.setattr(updater.subprocess, "Popen", lambda *a, **k: launched.append((a, k)))
    zp = tmp_path / "u.zip"
    _make_release_zip(zp, "2")
    staged = updater.extract_update(zp, tmp_path / updater.STAGING_DIR)
    exe = tmp_path / "PDFユーティリティ.exe"
    updater.apply_and_restart(staged, exe)
    bat = (tmp_path / "_pdftool_update.bat").read_bytes().decode("mbcs")
    assert "_internal_old" in bat and "PDFユーティリティ.exe" in bat and "start" in bat and ":rollback" in bat
    assert launched and not any(k.startswith("_PYI") for k in launched[0][1]["env"])


@pytest.mark.skipif(sys.platform != "win32", reason="cmd のバッチを実際に走らせる")
def test_apply_batch_really_swaps_folder(tmp_path: Path, monkeypatch):
    """バッチを実際に実行して、_internal と exe が新しい版に入れ替わることを確かめる。"""
    app = tmp_path / "アプリ"
    (app / "_internal").mkdir(parents=True)
    (app / "_internal" / "lib.txt").write_text("lib 1")
    (app / "_internal" / "old_only.txt").write_text("消えるはず")
    # 起動されても何もしない実在の exe（where.exe）を、アプリの exe に見立てる
    where = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "where.exe"
    exe = app / "PDFユーティリティ.exe"
    shutil.copy2(where, exe)
    zp = app / "u.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.write(where, "PDFユーティリティ/PDFユーティリティ.exe")
        z.writestr("PDFユーティリティ/_internal/lib.txt", "lib 2")
    staged = updater.extract_update(zp, app / updater.STAGING_DIR)
    procs = []
    real_popen = subprocess.Popen
    monkeypatch.setattr(updater.subprocess, "Popen", lambda *a, **k: procs.append(real_popen(*a, **k)))
    updater.apply_and_restart(staged, exe)
    procs[0].wait(timeout=60)
    assert (app / "_internal" / "lib.txt").read_text() == "lib 2"
    assert not (app / "_internal" / "old_only.txt").exists()
    assert not (app / "_internal_old").exists() and not (app / updater.STAGING_DIR).exists()
    assert exe.exists() and not (app / "_pdftool_update.bat").exists()
