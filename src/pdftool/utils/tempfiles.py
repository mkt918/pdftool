"""セッション中だけ使う一時ファイルの置き場。

画像ファイル(JPG/PNG…)をページとして取り込むときは、いったん一時 PDF に変換して
PageList に載せる。PageRef は「元ファイルのパス」を持つ設計なので、その一時 PDF が
その元ファイルの役目を果たす。

出力ファイル名・保存先は「元の画像」を基準にしたいので（一時フォルダの中に
`..._compressed.pdf` ができても困る）、`origin_of()` で元の画像パスへ戻せるようにして
`utils.naming` が経由する。
"""

from __future__ import annotations

import atexit
import shutil
import tempfile
from pathlib import Path

_session_dir: Path | None = None
_origins: dict[Path, Path] = {}


def session_dir() -> Path:
    """このプロセス専用の一時フォルダ（初回に作成。終了時に削除）。"""
    global _session_dir
    if _session_dir is None or not _session_dir.is_dir():
        _session_dir = Path(tempfile.mkdtemp(prefix="pdftool_session_"))
        atexit.register(cleanup)
    return _session_dir


def new_subdir() -> Path:
    """変換結果ごとの空フォルダ（ファイル名を元の名前のまま保つため、1件ごとに分ける）。"""
    return Path(tempfile.mkdtemp(dir=session_dir()))


def register_origin(temp_pdf: Path, original: Path) -> None:
    _origins[Path(temp_pdf)] = Path(original)


def origin_of(path: Path | str) -> Path:
    """一時 PDF なら元の画像パス、そうでなければ与えたパスをそのまま返す。"""
    p = Path(path)
    return _origins.get(p, p)


def is_temp(path: Path | str) -> bool:
    return Path(path) in _origins


def cleanup() -> None:
    global _session_dir
    if _session_dir is not None:
        shutil.rmtree(_session_dir, ignore_errors=True)
        _session_dir = None
    _origins.clear()
