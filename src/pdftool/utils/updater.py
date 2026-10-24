"""自動更新（GitHub Releases の最新版を確認し、アプリのフォルダを差し替えて再起動する）。

Qt に依存しない。UI からは run_worker() 経由で check_latest()/prepare_update() を呼ぶ。
差し替えは exe 版（PyInstaller）のときだけ。開発実行（python -m pdftool）では確認のみ。

配布はフォルダ形式（PDFユーティリティ.exe + _internal/）を zip にしたもの。
v0.3.3 以前の 1 ファイル版（PDFUtility.exe を探す）からは自動更新されないので、初回だけ手で入れ替える。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from pdftool import __version__

REPO = "mkt918/pdftool"
ASSET_NAME = "PDFUtility.zip"
STAGING_DIR = "_update"        # アプリのフォルダ内に新しい版を展開する場所
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
TIMEOUT = 10


@dataclass(frozen=True)
class UpdateInfo:
    version: str        # "0.2.0"
    url: str            # zip の直リンク
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


def check_latest(current: str = __version__, *, opener: Callable | None = None) -> UpdateInfo | None:
    """新しい版があれば UpdateInfo、無ければ None。通信失敗は例外を投げる（呼び出し側で握りつぶす）。"""
    import urllib.request          # 起動を軽くするため、使うときに読み込む（裏のスレッドで呼ばれる）

    opener = opener or urllib.request.urlopen
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
    """info の zip を dest へ落とす。サイズ・SHA256 が合わなければ消して例外。"""
    import urllib.request

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


def extract_update(zip_path: Path, staging: Path) -> Path:
    """zip を staging に展開し、exe と _internal/ が入ったフォルダを返す（zip は消す）。"""
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(staging)       # extractall は絶対パスや .. を取り除いて展開する
    zip_path.unlink(missing_ok=True)
    for root in (staging, *sorted(p for p in staging.iterdir() if p.is_dir())):
        if (root / "_internal").is_dir() and any(root.glob("*.exe")):
            return root
    shutil.rmtree(staging, ignore_errors=True)
    raise RuntimeError("更新ファイルの中にアプリが見つかりません")


def prepare_update(info: UpdateInfo, app_dir: Path,
                   progress: Callable[[tuple[int, int]], None] | None = None) -> Path:
    """ダウンロードして app_dir/_update に展開する。戻り値は apply_and_restart() に渡すフォルダ。"""
    zip_path = download_update(info, app_dir / f"{STAGING_DIR}.zip", progress)
    return extract_update(zip_path, app_dir / STAGING_DIR)


def apply_and_restart(staged: Path, current_exe: Path | None = None) -> None:
    """staged（新しい版のフォルダ）で現在のアプリを置き換えて起動し直す（呼び出し後、アプリは終了すること）。

    終了後に走るバッチが、_internal のロックが外れる（＝アプリが終わる）まで退避を繰り返し、
    新しい _internal と exe を入れてから起動する。途中で失敗したら元に戻して古い版を起動する。
    """
    cur = Path(current_exe or sys.executable)
    app = cur.parent
    new_exe = next(staged.glob("*.exe"))
    bat = app / "_pdftool_update.bat"
    lines = [
        "@echo off",
        "setlocal",
        f'cd /d "{app}"',
        'if exist "_internal_old" rmdir /s /q "_internal_old"',
        "set /a n=0",
        ":retry",
        "ping -n 2 127.0.0.1 >nul",
        'move "_internal" "_internal_old" >nul 2>nul',
        "if errorlevel 1 (",
        "  set /a n+=1",
        "  if %n% lss 30 goto retry",
        "  exit /b 1",
        ")",
        f'move "{staged / "_internal"}" "_internal" >nul 2>nul',
        "if errorlevel 1 goto rollback",
        f'move /y "{new_exe}" "{cur}" >nul 2>nul',
        "if errorlevel 1 (",
        '  rmdir /s /q "_internal"',
        "  goto rollback",
        ")",
        'rmdir /s /q "_internal_old"',
        f'rmdir /s /q "{app / STAGING_DIR}"',
        "goto launch",
        ":rollback",
        'move "_internal_old" "_internal" >nul 2>nul',
        ":launch",
        f'start "" "{cur}"',
        '(goto) 2>nul & del "%~f0"',
    ]
    # cmd は OEM コードページで読む（日本語パス対策。日本語 Windows では cp932 同士）
    bat.write_text("\r\n".join(lines) + "\r\n", encoding="mbcs")
    env = {k: v for k, v in os.environ.items() if not (k.startswith("_PYI") or k == "_MEIPASS2")}
    flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(["cmd", "/c", str(bat)], env=env, creationflags=flags, close_fds=True)
