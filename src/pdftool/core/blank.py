"""白紙ページの検出。

GUI 非依存。PySide6 を import しないこと。

低解像度のグレースケールで描画し、外周を除いた範囲で「インクのピクセルの割合」を測る。
割合が sensitivity 以下なら白紙とみなす。スキャンの端の影やごみ（孤立した点）で
白紙判定が外れないよう、外周は無視し、色は紙の地の色を基準に判定する（trim.ink_mask）。

自動で消さずに「見つけたページ番号」を返すだけ。削除するかは呼び出し側（UI）が確認を挟む。
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from PIL import Image

from pdftool.core.document import PdfDocument
from pdftool.core.pageops import PageRef
from pdftool.core.trim import CancelCheck, ProgressCallback, ScanCancelled, ScanProgress, ink_mask

# 感度の目安（インクの割合。ページ全体に対する比）
SENSITIVITY_STRICT = 0.0002    # 0.02%: ほぼ完全な白紙だけ
SENSITIVITY_NORMAL = 0.001     # 0.1%: ページ番号だけ・薄いごみ程度は白紙扱い
SENSITIVITY_LOOSE = 0.004      # 0.4%: 小さなスタンプや数行の文字までは白紙扱い（要確認）


def ink_ratio(gray: Image.Image, edge: float = 0.04) -> float:
    """外周 edge を除いた範囲での、インクのピクセルの割合 (0..1)。"""
    mask = ink_mask(gray, edge)
    w, h = mask.size
    ex, ey = round(w * edge), round(h * edge)
    inner = mask.crop((ex, ey, w - ex, h - ey)) if (ex or ey) else mask
    n = inner.width * inner.height
    if n == 0:
        return 0.0
    ink = inner.histogram()[255]
    return ink / n


def detect_blank_pages(
    refs: Sequence[PageRef],
    *,
    sensitivity: float = SENSITIVITY_NORMAL,
    dpi: float = 40.0,
    edge: float = 0.04,
    passwords: dict[Path, str] | None = None,
    progress: ProgressCallback | None = None,
    cancel: CancelCheck | None = None,
) -> list[int]:
    """refs のうち白紙とみなすページのインデックス（昇順）を返す。

    トリミング済み(ref.crop)のページは切り取った後の見た目で判定する
    （切り捨てた余白の端のごみは数えない）。
    """
    if sensitivity < 0:
        raise ValueError("sensitivity は 0 以上で指定してください")
    passwords = passwords or {}
    docs: dict[Path, PdfDocument] = {}
    blank: list[int] = []
    total = len(refs)
    try:
        for i, ref in enumerate(refs):
            if cancel is not None and cancel():
                raise ScanCancelled()
            doc = docs.get(ref.source_path)
            if doc is None:
                doc = docs[ref.source_path] = PdfDocument(ref.source_path, password=passwords.get(ref.source_path))
            gray = doc.render_page(ref.source_index, dpi=dpi, crop=ref.crop).convert("L")
            if ink_ratio(gray, edge) <= sensitivity:
                blank.append(i)
            if progress:
                progress(ScanProgress(i + 1, total, f"白紙ページを探しています… {i + 1}/{total}"))
    finally:
        for d in docs.values():
            d.close()
    return blank
