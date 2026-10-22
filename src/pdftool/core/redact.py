"""墨消し（黒塗り）。

GUI 非依存。PySide6 を import しないこと。

黒い四角を上に重ねるだけだと、下の文字は PDF に残っていてコピー・検索で読めてしまう。
そこで墨消しするページは、見た目どおり（回転・トリミング反映済み）に画像へレンダリングし、
黒塗りを描き込んでから、その画像 1 枚だけのページに置き換える。
元の文字・画像・注釈はそのページから完全に無くなる（代わりにそのページは文字選択・検索ができなくなる）。
墨消しの無いページは今まで通り元のまま書き出す。

範囲は「見た目のページ」に対する割合 (左, 上, 右, 下) で持つ（0〜1、左上が原点）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence

from PIL import Image, ImageDraw
from pypdf import PageObject, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject

from pdftool.core.document import PdfDocument

Rect = tuple[float, float, float, float]   # (左, 上, 右, 下) 見た目のページに対する割合

REDACT_DPI = 200.0
MIN_SIZE = 0.003                            # これより細い範囲は誤クリックとみなして捨てる


def normalize_rect(rect: Iterable[float]) -> Rect | None:
    """0〜1 に収め、左<右・上<下に並べ直す。小さすぎれば None。"""
    x0, y0, x1, y1 = (min(1.0, max(0.0, float(v))) for v in rect)
    l, r = sorted((x0, x1))
    t, b = sorted((y0, y1))
    if r - l < MIN_SIZE or b - t < MIN_SIZE:
        return None
    return (l, t, r, b)


def normalize_rects(rects: Iterable[Iterable[float]]) -> tuple[Rect, ...]:
    out = []
    for r in rects:
        n = normalize_rect(r)
        if n is not None:
            out.append(n)
    return tuple(out)


def paint_redactions(img: Image.Image, rects: Sequence[Rect]) -> Image.Image:
    """img に黒塗りを描き込んで返す（img 自体を書き換える）。端の取りこぼしが無いよう外側へ丸める。"""
    d = ImageDraw.Draw(img)
    w, h = img.size
    for l, t, r, b in rects:
        d.rectangle((int(l * w), int(t * h), min(w, -int(-r * w)) - 1, min(h, -int(-b * h)) - 1), fill=(0, 0, 0))
    return img


def render_redacted(
    path: Path, index: int, rotation: int, crop, rects: Sequence[Rect],
    password: str | None = None, dpi: float = REDACT_DPI,
) -> Image.Image:
    """見た目どおりにレンダリングして黒塗りを描いた RGB 画像。"""
    with PdfDocument(path, password=password) as doc:
        img = doc.render_page(index, dpi=dpi, rotation=rotation, crop=crop)
    return paint_redactions(img.convert("RGB"), rects)


def _is_gray(img: Image.Image) -> bool:
    r, g, b = img.split()
    return r.tobytes() == g.tobytes() == b.tobytes()


def add_image_page(writer: PdfWriter, img: Image.Image, dpi: float = REDACT_DPI) -> PageObject:
    """img を 1 枚だけ貼ったページを writer に追加して返す（ページの大きさは img と dpi から決める）。"""
    w_pt, h_pt = img.width * 72.0 / dpi, img.height * 72.0 / dpi
    gray = _is_gray(img)
    data = img.convert("L").tobytes() if gray else img.tobytes()
    xobj = DecodedStreamObject()
    xobj.set_data(data)
    xobj.update({
        NameObject("/Type"): NameObject("/XObject"),
        NameObject("/Subtype"): NameObject("/Image"),
        NameObject("/Width"): NumberObject(img.width),
        NameObject("/Height"): NumberObject(img.height),
        NameObject("/ColorSpace"): NameObject("/DeviceGray" if gray else "/DeviceRGB"),
        NameObject("/BitsPerComponent"): NumberObject(8),
    })
    ref = writer._add_object(xobj.flate_encode())
    page = writer.add_blank_page(width=w_pt, height=h_pt)
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/XObject"): DictionaryObject({NameObject("/PdfToolRedacted"): ref})}
    )
    cs = DecodedStreamObject()
    cs.set_data(f"q {w_pt:.3f} 0 0 {h_pt:.3f} 0 0 cm /PdfToolRedacted Do Q".encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(cs)
    return page
