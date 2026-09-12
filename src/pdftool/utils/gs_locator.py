"""Ghostscript 実行ファイルの検出。

検出順:
  1. 環境変数 PDFTOOL_GS（明示指定。フルパス）
  2. PATH 上の gswin64c / gswin32c / gs
  3. Windows 標準インストール先 (C:\\Program Files\\gs\\gs*\\bin\\gswin64c.exe)
     複数バージョンがある場合はバージョン番号が最も大きいものを選ぶ
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

_CANDIDATE_NAMES = ("gswin64c", "gswin32c", "gs")
_WINDOWS_ROOTS = (
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "gs",
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "gs",
)


def _version_key(path: Path) -> tuple[int, ...]:
    """'gs10.03.1' のようなフォルダ名から比較用のタプルを作る。"""
    m = re.search(r"gs(\d+(?:\.\d+)*)", path.parent.parent.name)
    if not m:
        return (0,)
    return tuple(int(x) for x in m.group(1).split("."))


@lru_cache(maxsize=1)
def find_ghostscript() -> Path | None:
    """Ghostscript のコンソール版実行ファイルを探す。見つからなければ None。"""
    env = os.environ.get("PDFTOOL_GS")
    if env:
        p = Path(env)
        if p.is_file():
            return p

    for name in _CANDIDATE_NAMES:
        found = shutil.which(name)
        if found:
            return Path(found)

    hits: list[Path] = []
    for root in _WINDOWS_ROOTS:
        if not root.is_dir():
            continue
        hits.extend(root.glob("gs*/bin/gswin64c.exe"))
        hits.extend(root.glob("gs*/bin/gswin32c.exe"))
    if hits:
        return max(hits, key=_version_key)
    return None


def ghostscript_version(gs_path: Path) -> str | None:
    """`gs --version` の出力（例 '10.03.1'）。失敗時は None。"""
    try:
        cp = subprocess.run(
            [str(gs_path), "--version"],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    out = cp.stdout.strip()
    return out or None
