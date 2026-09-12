"""ページの画像書き出し（連番PNG/JPEG）。

GUI 非依存。PySide6 を import しないこと。

資料貼り付け用途。既定 200dpi / PNG。
出力先は元PDFと同じ場所に作った専用サブフォルダ（`yy-mm-dd_元名_png/` など）。
ファイル名は `元名_p001.png` のゼロ埋め連番。

CLI:
    python -m pdftool.core.export 入力.pdf
    python -m pdftool.core.export 入力.pdf --pages 1-5,8 --dpi 200 --fmt jpg
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from PIL import Image

from pdftool.core.document import PdfDocument, PdfError
from pdftool.core.pageops import PageRef, parse_page_spec
from pdftool.utils import naming

_EXT_BY_FMT = {"png": ".png", "jpg": ".jpg", "jpeg": ".jpg"}
_PIL_FORMAT = {"png": "PNG", "jpg": "JPEG", "jpeg": "JPEG"}


class ExportCancelled(Exception):
    """cancel コールバックにより中断された。それまでに書いたファイルは残る。"""


@dataclass(frozen=True)
class ExportOptions:
    dpi: int = 200
    fmt: str = "png"        # 'png' | 'jpg'
    jpeg_quality: int = 90  # jpg のときのみ使う

    def __post_init__(self):
        fmt = self.fmt.lower()
        if fmt not in _EXT_BY_FMT:
            raise ValueError(f"不明な画像形式です: {self.fmt}（png / jpg）")
        object.__setattr__(self, "fmt", fmt)
        if self.dpi <= 0:
            raise ValueError("dpi は正の値を指定してください")
        if not 1 <= self.jpeg_quality <= 100:
            raise ValueError("jpeg_quality は 1..100 で指定してください")

    @property
    def ext(self) -> str:
        return _EXT_BY_FMT[self.fmt]

    @property
    def pil_format(self) -> str:
        return _PIL_FORMAT[self.fmt]


@dataclass(frozen=True)
class ExportProgress:
    current: int    # 1 始まり
    total: int
    path: Path      # 今書いたファイル
    message: str


@dataclass
class ExportResult:
    output_dir: Path
    files: list[Path] = field(default_factory=list)


ProgressCallback = Callable[[ExportProgress], None]
CancelCheck = Callable[[], bool]


def _save_image(img: Image.Image, dest: Path, options: ExportOptions) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if options.fmt in ("jpg", "jpeg"):
        img.convert("RGB").save(dest, options.pil_format, quality=options.jpeg_quality)
    else:
        img.save(dest, options.pil_format)


def export_pages(
    src: Path | str,
    indices: Sequence[int] | None = None,
    *,
    options: ExportOptions = ExportOptions(),
    out_dir: Path | None = None,
    password: str | None = None,
    progress: ProgressCallback | None = None,
    cancel: CancelCheck | None = None,
) -> ExportResult:
    """ページを画像として書き出す。

    indices は 0 始まり。None なら全ページ。選択ページを書き出す場合でも
    ファイル名は「元のページ番号」を使う（例: 3, 7 ページを選ぶと _p003, _p007）。
    """
    src = Path(src)
    folder = naming.output_dir(src, options.fmt, out_dir)
    files: list[Path] = []
    with PdfDocument(src, password=password) as doc:
        idx = list(indices) if indices is not None else list(range(doc.page_count))
        if not idx:
            raise ValueError("書き出すページがありません")
        for i, page_index in enumerate(idx, start=1):
            if cancel is not None and cancel():
                raise ExportCancelled()
            if not 0 <= page_index < doc.page_count:
                raise IndexError(f"ページ番号が範囲外です: {page_index}")
            img = doc.render_page(page_index, dpi=options.dpi)
            dest = folder / naming.numbered_name(src, page_index + 1, options.ext)
            _save_image(img, dest, options)
            files.append(dest)
            if progress:
                progress(ExportProgress(
                    i, len(idx), dest, f"{i}/{len(idx)} ページ目を書き出しました: {dest.name}"
                ))
    return ExportResult(output_dir=folder, files=files)


def export_refs(
    refs: Sequence[PageRef],
    base: Path | str,
    *,
    options: ExportOptions = ExportOptions(),
    out_dir: Path | None = None,
    passwords: dict[Path, str] | None = None,
    progress: ProgressCallback | None = None,
    cancel: CancelCheck | None = None,
) -> ExportResult:
    """編集中の PageList（PageRef の列）から画像を書き出す。

    回転(ref.rotation)を反映して描画する。ファイル名の連番は refs の並び順で 1..n。
    複数の元 PDF が混在していても良い（結合後の書き出しに対応）。
    """
    if not refs:
        raise ValueError("書き出すページがありません")
    base = Path(base)
    passwords = passwords or {}
    folder = naming.output_dir(base, options.fmt, out_dir)
    files: list[Path] = []
    docs: dict[Path, PdfDocument] = {}
    try:
        for i, ref in enumerate(refs, start=1):
            if cancel is not None and cancel():
                raise ExportCancelled()
            doc = docs.get(ref.source_path)
            if doc is None:
                doc = PdfDocument(ref.source_path, password=passwords.get(ref.source_path))
                docs[ref.source_path] = doc
            img = doc.render_page(ref.source_index, dpi=options.dpi, rotation=ref.rotation)
            dest = folder / naming.numbered_name(base, i, options.ext)
            _save_image(img, dest, options)
            files.append(dest)
            if progress:
                progress(ExportProgress(
                    i, len(refs), dest, f"{i}/{len(refs)} ページ目を書き出しました: {dest.name}"
                ))
    finally:
        for d in docs.values():
            d.close()
    return ExportResult(output_dir=folder, files=files)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="PDF のページを画像として書き出す")
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--pages", type=str, default=None, help="1-5,8,10-12 形式。省略で全ページ")
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--fmt", choices=["png", "jpg"], default="png")
    ap.add_argument("--quality", type=int, default=90, help="jpg のときの品質 (1-100)")
    ap.add_argument("--out", type=Path, default=None, help="出力先フォルダ（既定: 元と同じ場所）")
    args = ap.parse_args(argv)

    def show(p: ExportProgress) -> None:
        print(p.message, flush=True)

    try:
        indices = None
        if args.pages:
            with PdfDocument(args.pdf) as doc:
                page_count = doc.page_count
            indices = parse_page_spec(args.pages, page_count)
        options = ExportOptions(dpi=args.dpi, fmt=args.fmt, jpeg_quality=args.quality)
        r = export_pages(args.pdf, indices, options=options, out_dir=args.out, progress=show)
    except (PdfError, ValueError) as e:
        msg = e.message if isinstance(e, PdfError) else str(e)
        print(f"エラー: {msg}", file=sys.stderr)
        return 1
    print(f"出力先: {r.output_dir}")
    print(f"{len(r.files)} 件書き出しました")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
