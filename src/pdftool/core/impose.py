"""面付け（2in1 / 4in1 など。1 枚の用紙に複数ページを縮小して並べる）。

GUI 非依存。PySide6 を import しないこと。

- 元ページは見た目どおり（/Rotate・ユーザー回転・トリミングを反映済み）に用紙の枠へ収める
- ページサイズがばらばらでも、枠に収まる最大の倍率（縦横比維持）で中央に置く
- 2in1 は横向き用紙に縦ページ2枚、4in1 は縦向き用紙に 2x2。それ以外は rows x cols で指定
- 並び順は左→右（既定）か右→左（縦書き資料・右開きの冊子向け）
- ページ番号などのスタンプは「元のページ」に重ねてから面付けする（stamps 引数）
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from pypdf import PageObject, PdfWriter, Transformation
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from pdftool.core.pageops import (
    PageRef,
    WriteCancelled,
    WriteProgress,
    _atomic_write,
    check_dest,
    prepare_pages,
)
from pdftool.core.redact import Rect
from pdftool.core.stamp import TextStamp
from pdftool.utils import naming

MM = 72.0 / 25.4

PAPER_SIZES_MM = {          # 縦向きの (幅, 高さ) mm
    "A4": (210.0, 297.0),
    "A3": (297.0, 420.0),
    "B5": (176.0, 250.0),
    "B4": (250.0, 353.0),
    "Letter": (215.9, 279.4),
}


@dataclass(frozen=True)
class ImposeSpec:
    cols: int = 2
    rows: int = 1
    paper: str = "A4"
    landscape: bool | None = None   # None なら cols>rows のとき横向き、それ以外は縦向き
    margin_mm: float = 8.0          # 用紙の外周の余白
    gutter_mm: float = 4.0          # ページ同士の間隔
    right_to_left: bool = False     # 1 行の中を右から左へ並べる（縦書き・右開き）
    border: bool = False            # 各ページの周りに細い枠線を引く

    def __post_init__(self):
        if self.cols < 1 or self.rows < 1:
            raise ValueError("行数・列数は 1 以上にしてください")
        if self.paper not in PAPER_SIZES_MM:
            raise ValueError(f"不明な用紙です: {self.paper}（{', '.join(PAPER_SIZES_MM)}）")
        if self.margin_mm < 0 or self.gutter_mm < 0:
            raise ValueError("余白は 0 以上で指定してください")

    @property
    def per_sheet(self) -> int:
        return self.cols * self.rows

    @property
    def label(self) -> str:
        return f"{self.per_sheet}in1"

    def sheet_size_pt(self) -> tuple[float, float]:
        w, h = PAPER_SIZES_MM[self.paper]
        landscape = self.cols > self.rows if self.landscape is None else self.landscape
        if landscape:
            w, h = h, w
        return w * MM, h * MM


PRESETS: dict[str, ImposeSpec] = {
    "2in1": ImposeSpec(cols=2, rows=1),
    "4in1": ImposeSpec(cols=2, rows=2),
}


def cell_rects(spec: ImposeSpec) -> list[tuple[float, float, float, float]]:
    """読む順に並べた各枠の (x, y, w, h)。原点は用紙の左下。"""
    sw, sh = spec.sheet_size_pt()
    m, g = spec.margin_mm * MM, spec.gutter_mm * MM
    cw = (sw - 2 * m - g * (spec.cols - 1)) / spec.cols
    ch = (sh - 2 * m - g * (spec.rows - 1)) / spec.rows
    if cw <= 0 or ch <= 0:
        raise ValueError("余白が大きすぎて、ページを置く場所がありません")
    rects = []
    for r in range(spec.rows):
        for c in range(spec.cols):
            col = spec.cols - 1 - c if spec.right_to_left else c
            x = m + col * (cw + g)
            y = sh - m - (r + 1) * ch - r * g          # r=0 が最上段
            rects.append((x, y, cw, ch))
    return rects


def _border_overlay(sheet: PageObject, writer: PdfWriter, boxes: list[tuple[float, float, float, float]]) -> None:
    ops = ["q 0.55 G 0.5 w"] + [f"{x:.2f} {y:.2f} {w:.2f} {h:.2f} re S" for x, y, w, h in boxes] + ["Q"]
    ov = PageObject.create_blank_page(None, float(sheet.mediabox.width), float(sheet.mediabox.height))
    ov[NameObject("/Resources")] = DictionaryObject()
    cs = DecodedStreamObject()
    cs.set_data("\n".join(ops).encode("ascii"))
    ov[NameObject("/Contents")] = writer._add_object(cs)
    sheet.merge_page(ov)


def impose_pages(
    pages: Sequence[PageRef],
    dest: Path | str,
    spec: ImposeSpec,
    passwords: dict[Path, str] | None = None,
    *,
    stamps: Sequence[TextStamp] | None = None,
    redactions: dict[int, Sequence[Rect]] | None = None,
    progress: Callable[[WriteProgress], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> Path:
    """pages を spec のレイアウトで面付けした新しい PDF を書き出す。戻り値は実際に書いたパス。"""
    if not pages:
        raise ValueError("書き出すページがありません")
    dest = check_dest(pages, dest)
    rects = cell_rects(spec)
    sw, sh = spec.sheet_size_pt()

    scratch = PdfWriter()          # 見た目どおりに整えた元ページの置き場
    out = PdfWriter()
    total = len(pages)
    sheet: PageObject | None = None
    boxes: list[tuple[float, float, float, float]] = []
    n_sheets = -(-total // spec.per_sheet)

    for i, page in enumerate(prepare_pages(pages, scratch, passwords, upright=True, stamps=stamps,
                                              redactions=redactions, cancel=cancel)):
        slot = i % spec.per_sheet
        if slot == 0:
            sheet = out.add_blank_page(width=sw, height=sh)
            boxes = []
        x, y, cw, ch = rects[slot]
        mb = page.mediabox
        pw, ph = float(mb.width), float(mb.height)
        scale = min(cw / pw, ch / ph)
        dx = x + (cw - pw * scale) / 2 - float(mb.left) * scale
        dy = y + (ch - ph * scale) / 2 - float(mb.bottom) * scale
        sheet.merge_transformed_page(page, Transformation().scale(scale, scale).translate(dx, dy))
        boxes.append((x + (cw - pw * scale) / 2, y + (ch - ph * scale) / 2, pw * scale, ph * scale))
        if slot == spec.per_sheet - 1 or i == total - 1:
            if spec.border:
                _border_overlay(sheet, out, boxes)
            if progress:
                progress(WriteProgress(len(out.pages), n_sheets, f"面付けしています… {len(out.pages)}/{n_sheets} 枚"))
        if cancel is not None and cancel():
            raise WriteCancelled()

    _atomic_write(out, dest)
    return dest


def default_impose_path(base: Path, spec: ImposeSpec) -> Path:
    return naming.output_file(base, spec.label, ".pdf")
