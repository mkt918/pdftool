"""画像ファイル（JPG/PNG/…）を PDF のページとして取り込む。

GUI 非依存。PySide6 を import しないこと。

画像は A4 の長辺(842pt)に収まる大きさのページにして 1 画像 = 1 ページの一時 PDF にする。
（Pillow の PDF 保存は JPEG をそのまま埋め込むので画質は落ちない。）
複数フレームの TIFF/GIF は全フレームをページにする。
EXIF の回転情報は反映する（スマホ写真が横倒しにならないように）。
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageOps, ImageSequence
from pypdf import PdfReader, PdfWriter

from pdftool.core.document import PdfBrokenError
from pdftool.utils import tempfiles

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tif", ".tiff", ".webp")
A4_LONG_PT = 842.0
MAX_FRAMES = 200


def is_image(path: Path | str) -> bool:
    return Path(path).suffix.lower() in IMAGE_EXTS


def _frame_to_pdf_bytes(frame: Image.Image) -> bytes:
    frame = ImageOps.exif_transpose(frame)
    if frame.mode not in ("RGB", "L"):
        # 透過は白背景に合成（PDF にアルファは持ち込まない）
        rgba = frame.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.getchannel("A"))
        frame = bg
    # ページの長辺が A4 の長辺になるように解像度を決める
    resolution = max(frame.width, frame.height) * 72.0 / A4_LONG_PT
    buf = io.BytesIO()
    frame.save(buf, "PDF", resolution=resolution)
    return buf.getvalue()


def image_to_pdf(src: Path | str, dest_dir: Path | None = None) -> Path:
    """画像 1 枚を一時 PDF にして返す。ファイル名は元の名前（拡張子だけ .pdf）。

    変換結果は `utils.tempfiles` に元画像との対応を登録するので、保存・書き出しの
    出力名や出力先は元の画像を基準に決まる。
    """
    src = Path(src)
    if not src.is_file():
        raise PdfBrokenError(f"ファイルが見つかりません: {src}", src)
    dest_dir = dest_dir or tempfiles.new_subdir()
    dest = dest_dir / (src.stem + ".pdf")
    try:
        with Image.open(src) as img:
            frames = [f.copy() for _, f in zip(range(MAX_FRAMES), ImageSequence.Iterator(img))]
        writer = PdfWriter()
        for fr in frames:
            reader = PdfReader(io.BytesIO(_frame_to_pdf_bytes(fr)))
            writer.add_page(reader.pages[0])
        with open(dest, "wb") as f:
            writer.write(f)
    except PdfBrokenError:
        raise
    except Exception as e:  # noqa: BLE001 — Pillow の例外は種類が多い
        raise PdfBrokenError(f"画像を読み込めませんでした: {src.name}（{type(e).__name__}）", src) from e
    tempfiles.register_origin(dest, src)
    return dest


def convert_images(paths: list[Path | str]) -> list[Path]:
    """PDF はそのまま、画像は一時 PDF にして、元の並び順のまま返す（ワーカースレッドで呼ぶ想定）。"""
    out: list[Path] = []
    for p in paths:
        p = Path(p)
        out.append(image_to_pdf(p) if is_image(p) else p)
    return out
