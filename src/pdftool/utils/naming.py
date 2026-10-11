"""出力ファイル名の規則を一元管理する。

規則: `yy-mm-dd_元ファイル名_処理内容.拡張子`
  例: 26-09-12_進路資料_compressed.pdf
      26-09-12_進路資料_png/進路資料_p001.png

原本は絶対に上書きしない。同名が存在する場合は `_2`, `_3`... を付与する。
各機能はここを経由して出力先を決めること。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from pdftool.utils.tempfiles import origin_of


def date_prefix(today: date | None = None) -> str:
    """'26-09-12' 形式の日付プレフィックス。"""
    d = today or date.today()
    return d.strftime("%y-%m-%d")


def unique_path(path: Path) -> Path:
    """`path` が既に存在すれば `stem_2.ext`, `stem_3.ext` ... と番号を付けて返す。"""
    if not path.exists():
        return path
    n = 2
    while True:
        candidate = path.with_name(f"{path.stem}_{n}{path.suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def output_file(
    source: Path,
    suffix: str,
    ext: str | None = None,
    out_dir: Path | None = None,
    today: date | None = None,
) -> Path:
    """処理結果ファイルのパスを決める。

    Args:
        source:  元ファイル
        suffix:  処理内容（'compressed' など）
        ext:     拡張子（'.pdf'）。None なら元と同じ
        out_dir: 出力先フォルダ。None なら元と同じフォルダ
    """
    source = origin_of(source)   # 画像から作った一時PDFなら元の画像を基準にする
    ext = ext if ext is not None else source.suffix
    if ext and not ext.startswith("."):
        ext = "." + ext
    directory = out_dir if out_dir is not None else source.parent
    name = f"{date_prefix(today)}_{source.stem}_{suffix}{ext}"
    return unique_path(directory / name)


def output_dir(
    source: Path,
    suffix: str,
    out_dir: Path | None = None,
    today: date | None = None,
    reuse_existing: bool = False,
) -> Path:
    """画像書き出しなど、複数ファイルを入れる専用サブフォルダのパスを決める。

    フォルダは作成しない（呼び出し側で mkdir する）。
    既存なら `_2`, `_3`... を付ける。ただし reuse_existing=True で、同名のフォルダが
    既にあれば（同じ日に同じ PDF を書き出したとき）そのフォルダをそのまま使う。
    その場合、中のファイルは呼び出し側で unique_path を通して上書きを避けること。
    """
    source = origin_of(source)
    parent = out_dir if out_dir is not None else source.parent
    folder = parent / f"{date_prefix(today)}_{source.stem}_{suffix}"
    if reuse_existing and folder.is_dir():
        return folder
    return unique_path(folder)


def numbered_name(source: Path, index: int, ext: str, width: int = 3) -> str:
    """連番ファイル名: `元名_p001.png`（index は1始まりのページ番号）。"""
    if not ext.startswith("."):
        ext = "." + ext
    return f"{origin_of(source).stem}_p{index:0{width}d}{ext}"
