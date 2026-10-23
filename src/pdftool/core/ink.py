"""手書きの書き込み（ペン・蛍光ペン）。

GUI 非依存。PySide6 を import しないこと。

書き込みは「ページの上の別レイヤー」として線（Stroke）の列で持ち、元のページは書き換えない。
保存するときだけ、線をベクターのまま PDF のページへ重ねる（画像にしないので拡大してもきれいで、
ファイルも小さい）。蛍光ペンは「乗算」で重ねるので、下の文字が隠れずに読める。

座標は「見た目のページ（回転・トリミング反映後）」に対する割合（x, y とも 0〜1、左上が原点）、
太さはページの幅に対する割合。ページの大きさが違っても同じ見た目になる。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from pypdf import PageObject, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject

Point = tuple[float, float]

PEN = "pen"
MARKER = "marker"
KINDS = (PEN, MARKER)


@dataclass(frozen=True)
class Stroke:
    kind: str                       # PEN / MARKER
    color: tuple[int, int, int]     # 0〜255
    width: float                    # ページ幅に対する割合
    points: tuple[Point, ...]       # 1 点だけなら点（ちょん）

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"不明な書き込みの種類です: {self.kind}")
        if not self.points:
            raise ValueError("線に点がありません")
        if self.width <= 0:
            raise ValueError("線の太さは正の値にしてください")


def distance_to_stroke(stroke: Stroke, x: float, y: float, aspect: float = 1.0) -> float:
    """点 (x, y) から線までの距離（ページ幅を 1 とした割合）。aspect = 高さ/幅（縦方向の割合を幅基準に直す）。"""
    pts = [(px, py * aspect) for px, py in stroke.points]
    qx, qy = x, y * aspect
    if len(pts) == 1:
        return ((pts[0][0] - qx) ** 2 + (pts[0][1] - qy) ** 2) ** 0.5
    best = float("inf")
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        dx, dy = x1 - x0, y1 - y0
        seg = dx * dx + dy * dy
        t = 0.0 if seg == 0 else max(0.0, min(1.0, ((qx - x0) * dx + (qy - y0) * dy) / seg))
        d = ((x0 + t * dx - qx) ** 2 + (y0 + t * dy - qy) ** 2) ** 0.5
        best = min(best, d)
    return best


def stroke_ops(strokes: Sequence[Stroke], left: float, bottom: float, pw: float, ph: float) -> list[str]:
    """線を描く PDF のコンテンツ命令。蛍光ペンは ExtGState /PdfToolMul（乗算）を使う。"""
    ops: list[str] = []
    for st in strokes:
        r, g, b = (c / 255.0 for c in st.color)
        pts = [(left + x * pw, bottom + (1.0 - y) * ph) for x, y in st.points]
        if len(pts) == 1:                       # 点は長さ 0 の線だと表示が不安定なので、ごく短い線にする
            pts = [pts[0], (pts[0][0] + 0.01, pts[0][1])]
        cap = 1 if st.kind == PEN else 2        # ペン: 丸、蛍光ペン: 角
        ops.append("q")
        if st.kind == MARKER:
            ops.append("/PdfToolMul gs")
        ops.append(f"{r:.4f} {g:.4f} {b:.4f} RG {st.width * pw:.3f} w {cap} J 1 j")
        ops.append(f"{pts[0][0]:.3f} {pts[0][1]:.3f} m")
        ops.extend(f"{x:.3f} {y:.3f} l" for x, y in pts[1:])
        ops.append("S Q")
    return ops


def draw_strokes(writer: PdfWriter, page: PageObject, strokes: Sequence[Stroke]) -> None:
    """page（回転 0 に焼き込み済み・mediabox が見た目）に線を重ねる。"""
    if not strokes:
        return
    mb = page.mediabox
    left, bottom = float(mb.left), float(mb.bottom)
    pw, ph = float(mb.width), float(mb.height)
    mul = DictionaryObject({
        NameObject("/Type"): NameObject("/ExtGState"),
        NameObject("/BM"): NameObject("/Multiply"),
        NameObject("/CA"): NumberObject(1),
    })
    overlay = PageObject.create_blank_page(None, pw, ph)
    overlay.mediabox = mb
    overlay[NameObject("/Resources")] = DictionaryObject({
        NameObject("/ExtGState"): DictionaryObject({NameObject("/PdfToolMul"): writer._add_object(mul)}),
    })
    cs = DecodedStreamObject()
    cs.set_data("\n".join(stroke_ops(strokes, left, bottom, pw, ph)).encode("ascii"))
    overlay[NameObject("/Contents")] = writer._add_object(cs)
    page.merge_page(overlay)
