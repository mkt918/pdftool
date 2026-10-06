import io
import json
from pathlib import Path

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
        {"name": updater.ASSET_NAME, "browser_download_url": "https://e/x.exe", "size": 5, "digest": "sha256:ab"},
    ]}
    info = updater.check_latest("0.1.0", opener=_opener(data))
    assert info and info.version == "9.0.0" and info.url == "https://e/x.exe" and info.sha256 == "ab"
    assert updater.check_latest("9.0.0", opener=_opener(data)) is None
    assert updater.check_latest("0.1.0", opener=_opener({"tag_name": "v9.0.0", "assets": []})) is None


def test_apply_writes_batch(tmp_path: Path, monkeypatch):
    launched = []
    monkeypatch.setattr(updater.subprocess, "Popen", lambda *a, **k: launched.append((a, k)))
    exe = tmp_path / "PDFユーティリティ.exe"
    updater.apply_and_restart(tmp_path / "new.exe", exe)
    bat = (tmp_path / "_pdftool_update.bat").read_bytes().decode("mbcs")
    assert "move /y" in bat and "PDFユーティリティ.exe" in bat and "start" in bat
    assert launched and not any(k.startswith("_PYI") for k in launched[0][1]["env"])
