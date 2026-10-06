"""自動更新（GitHub Releases の最新版を確認し、exe を差し替えて再起動する）。

Qt に依存しない。UI からは run_worker() 経由で check_latest()/download_update() を呼ぶ。
差し替えは exe 版（PyInstaller）のときだけ。開発実行（python -m pdftool）では確認のみ。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from pdftool import __version__

REPO = "mkt918/pdftool"
ASSET_NAME = "PDFUtility.exe"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
TIMEOUT = 10


@dataclass(frozen=True)
class UpdateInfo:
    version: str        # "0.2.0"
    url: str            # exe の直リンク
    size: int
    sha256: str | None  # GitHub が返す digest（無ければ None）
    notes: str


def parse_version(text: str) -> tuple[int, ...]:
    """'v0.2.10' → (0, 2, 10)。解釈できない部分は無視する。"""
    parts: list[int] = []
    for p in text.strip().lstrip("vV").split("."):
        digits = "".join(c for c in p if c.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def is_newer(latest: str, current: str = __version__) -> bool:
    return parse_version(latest) > parse_version(current)


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def check_latest(current: str = __version__, *, opener: Callable = urllib.request.urlopen) -> UpdateInfo | None:
    """新しい版があれば UpdateInfo、無ければ None。通信失敗は例外を投げる（呼び出し側で握りつぶす）。"""
    req = urllib.request.Request(API_URL, headers={"Accept": "application/vnd.github+json", "User-Agent": "pdftool"})
    with opener(req, timeout=TIMEOUT) as r:
        data = json.load(r)
    tag = str(data.get("tag_name", ""))
    if not tag or not is_newer(tag, current):
        return None
    for a in data.get("assets", []):
        if a.get("name") == ASSET_NAME:
            digest = str(a.get("digest") or "")
            sha = digest.split(":", 1)[1] if digest.startswith("sha256:") else None
            return UpdateInfo(tag.lstrip("vV"), a["browser_download_url"], int(a.get("size", 0)), sha,
                              str(data.get("body") or ""))
    return None


def download_update(info: UpdateInfo, dest: Path, progress: Callable[[tuple[int, int]], None] | None = None) -> Path:
    """info の exe を dest へ落とす。サイズ・SHA256 が合わなければ消して例外。"""
    req = urllib.request.Request(info.url, headers={"User-Agent": "pdftool"})
    h = hashlib.sha256()
    done = 0
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r, open(dest, "wb") as f:
            total = int(r.headers.get("Content-Length") or info.size or 0)
            while chunk := r.read(1 << 20):
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if progress:
                    progress((done, total))     # Worker の progress シグナルは引数 1 つ
        if info.size and done != info.size:
            raise RuntimeError(f"ダウンロードが途中で切れました（{done}/{info.size} バイト）")
        if info.sha256 and h.hexdigest().lower() != info.sha256.lower():
            raise RuntimeError("ダウンロードしたファイルのハッシュが一致しません")
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    return dest


def apply_and_restart(new_exe: Path, current_exe: Path | None = None) -> None:
    """現在の exe を new_exe で置き換えて起動し直す（呼び出し後、アプリは終了すること）。

    終了後に走るバッチが、exe のロックが外れるまで move を繰り返し、成功したら新しい exe を起動する。
    PyInstaller の onefile は環境変数で展開先を引き継ぐため、子プロセスには渡さない。
    """
    cur = Path(current_exe or sys.executable)
    bat = cur.with_name("_pdftool_update.bat")
    bat.write_text(
        "@echo off\r\n"
        "setlocal\r\n"
        "set /a n=0\r\n"
        ":retry\r\n"
        "ping -n 2 127.0.0.1 >nul\r\n"
        f'move /y "{new_exe}" "{cur}" >nul 2>nul\r\n'
        "if errorlevel 1 (\r\n"
        "  set /a n+=1\r\n"
        "  if %n% lss 30 goto retry\r\n"
        "  exit /b 1\r\n"
        ")\r\n"
        f'start "" "{cur}"\r\n'
        '(goto) 2>nul & del "%~f0"\r\n',
        encoding="mbcs",    # cmd は OEM コードページで読む（日本語パス対策。日本語 Windows では cp932 同士）
    )
    env = {k: v for k, v in os.environ.items() if not (k.startswith("_PYI") or k == "_MEIPASS2")}
    flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(["cmd", "/c", str(bat)], env=env, creationflags=flags, close_fds=True)
