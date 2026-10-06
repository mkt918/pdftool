"""余白トリミング（内容の外接範囲の自動検出・手動 mm 指定）。

GUI 非依存。PySide6 を import しないこと。

検出方法:
    ページを低解像度のグレースケールで描画し、紙の地の色から少し暗いピクセルを「インク」とみなす。
    スキャン端の影や孤立した点（ごみ）に引きずられないよう、
      - 外周の数%は無視する
      - 行/列のインク数が 2 未満のものは数えない
    で内容の外接範囲を求め、その外側に pad_mm の余白を足して残す。

結果は `Crop`（左,上,右,下 の余白割合。回転前の見た目が基準）で、`PageList.set_crops()` に渡す。
"""

from __future__ import annotations

import array
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from pdftool.core.document import Crop, PdfDocument, validate_crop
from pdftool.core.pageops import PageRef

MM = 72.0 / 25.4


class ScanCancelled(Exception):
    """cancel コールバックにより中断された（Worker は "...Cancelled" を中断として扱う）。"""


@dataclass(frozen=True)
class ScanProgress:
    current: int    # 1 始まり
    total: int
    message: str


ProgressCallback = Callable[[ScanProgress], None]
CancelCheck = Callable[[], bool]


# ---------------------------------------------------------------------------
# 画像処理の共通部品（blank.py からも使う）
# ---------------------------------------------------------------------------
def ink_threshold(gray: Image.Image) -> int:
    """紙の地の色（明るい側の最頻値）から、インクとみなす境目の明るさを決める。"""
    hist = gray.histogram()
    paper = max(range(128, 256), key=lambda v: hist[v])
    return max(60, min(215, paper - 35))


def ink_mask(gray: Image.Image, edge: float = 0.0) -> Image.Image:
    """インクの位置を 255、それ以外を 0 にした二値画像。外周 edge(割合) は 0 にして無視する。"""
    th = ink_threshold(gray)
    m01 = gray.point(lambda p: 1 if p < th else 0)
    # 孤立した 1 ピクセル（ごみ・スキャンのノイズ）は捨てる。隣に 1 つでもインクがあれば残す
    # （文字の線は必ずつながっているので消えない）
    neighbors = m01.filter(ImageFilter.Kernel((3, 3), [1, 1, 1, 1, 0, 1, 1, 1, 1], scale=1))
    keep = ImageChops.darker(m01, neighbors.point(lambda v: 1 if v >= 1 else 0))   # 論理積(最小値)
    mask = keep.point(lambda p: 255 if p else 0)
    w, h = mask.size
    ex, ey = round(w * edge), round(h * edge)
    d = ImageDraw.Draw(mask)
    if ey:
        d.rectangle((0, 0, w, ey - 1), fill=0)
        d.rectangle((0, h - ey, w, h), fill=0)
    if ex:
        d.rectangle((0, 0, ex - 1, h), fill=0)
        d.rectangle((w - ex, 0, w, h), fill=0)
    return mask


def _span(profile: list[int], min_count: int) -> tuple[int, int] | None:
    idx = [i for i, v in enumerate(profile) if v >= min_count]
    return (idx[0], idx[-1] + 1) if idx else None


def content_bbox(mask: Image.Image, min_count: int = 2) -> tuple[int, int, int, int] | None:
    """二値画像のインク外接矩形 (x0,y0,x1,y1)。孤立点を避けるため行/列に min_count 個以上ある範囲だけ見る。"""
    w, h = mask.size
    f = mask.convert("F")                       # 浮動小数のまま平均して、個数を正確に戻す
    rows = array.array("f", f.resize((1, h), Image.Resampling.BOX).tobytes())
    cols = array.array("f", f.resize((w, 1), Image.Resampling.BOX).tobytes())
    rcount = [round(v * w / 255.0) for v in rows]
    ccount = [round(v * h / 255.0) for v in cols]
    ys = _span(rcount, min_count)
    xs = _span(ccount, min_count)
    if ys is None or xs is None:
        return None
    return xs[0], ys[0], xs[1], ys[1]


# ---------------------------------------------------------------------------
# 自動トリミング
# ---------------------------------------------------------------------------
def content_margins(
    doc: PdfDocument, index: int, *, pad_mm: float = 5.0, dpi: float = 50.0, edge: float = 0.015
) -> Crop | None:
    """内容の外側に pad_mm の余白を残したときの Crop。白紙（内容なし）なら None。"""
    gray = doc.render_page(index, dpi=dpi).convert("L")
    bbox = content_bbox(ink_mask(gray, edge))
    if bbox is None:
        return None
    w, h = gray.size
    pad = pad_mm / 25.4 * dpi
    x0, y0 = max(0.0, bbox[0] - pad), max(0.0, bbox[1] - pad)
    x1, y1 = min(float(w), bbox[2] + pad), min(float(h), bbox[3] + pad)
    return (x0 / w, y0 / h, (w - x1) / w, (h - y1) / h)


def auto_crops(
    refs: Sequence[PageRef],
    indices: Sequence[int],
    *,
    pad_mm: float = 5.0,
    unify: bool = False,
    passwords: dict[Path, str] | None = None,
    progress: ProgressCallback | None = None,
    cancel: CancelCheck | None = None,
) -> dict[int, Crop | None]:
    """indices のページの余白を自動検出する。`PageList.set_crops()` にそのまま渡せる辞書を返す。

    - 白紙ページは結果に含めない（今のトリミングを変えない）
    - unify=True なら、検出した全ページの余白の最小値（＝全ページの内容を含む範囲）を全ページに掛ける。
      同じ用紙サイズのスキャンで、ページごとにサイズがばらつかないようにしたいとき用
    - 検出は常に「トリミング前・回転前」の見た目で行う（既存の crop/rotation は無視）
    """
    passwords = passwords or {}
    docs: dict[Path, PdfDocument] = {}
    found: dict[int, Crop] = {}
    total = len(indices)
    try:
        for n, i in enumerate(indices, start=1):
            if cancel is not None and cancel():
                raise ScanCancelled()
            ref = refs[i]
            doc = docs.get(ref.source_path)
            if doc is None:
                doc = docs[ref.source_path] = PdfDocument(ref.source_path, password=passwords.get(ref.source_path))
            c = content_margins(doc, ref.source_index, pad_mm=pad_mm)
            if c is not None:
                found[i] = c
            if progress:
                progress(ScanProgress(n, total, f"余白を調べています… {n}/{total}"))
    finally:
        for d in docs.values():
            d.close()

    if unify and found:
        common = tuple(min(c[k] for c in found.values()) for k in range(4))
        return {i: validate_crop(common) for i in indices}
    return {i: validate_crop(c) for i, c in found.items()}


# ---------------------------------------------------------------------------
# 手動（mm 指定）
# ---------------------------------------------------------------------------
def manual_crops(
    refs: Sequence[PageRef],
    indices: Sequence[int],
    *,
    left_mm: float = 0.0,
    top_mm: float = 0.0,
    right_mm: float = 0.0,
    bottom_mm: float = 0.0,
    passwords: dict[Path, str] | None = None,
) -> dict[int, Crop | None]:
    """外側から各辺 mm ずつ削る。ページサイズごとに割合へ換算する（サイズがばらばらでも同じ mm になる）。"""
    passwords = passwords or {}
    if min(left_mm, top_mm, right_mm, bottom_mm) < 0:
        raise ValueError("トリミングの余白は 0 以上で指定してください")
    docs: dict[Path, PdfDocument] = {}
    out: dict[int, Crop | None] = {}
    try:
        for i in indices:
            ref = refs[i]
            doc = docs.get(ref.source_path)
            if doc is None:
                doc = docs[ref.source_path] = PdfDocument(ref.source_path, password=passwords.get(ref.source_path))
            info = doc.page_info(ref.source_index)
            out[i] = validate_crop((
                left_mm * MM / info.width_pt, top_mm * MM / info.height_pt,
                right_mm * MM / info.width_pt, bottom_mm * MM / info.height_pt,
            ))
    finally:
        for d in docs.values():
            d.close()
    return out
