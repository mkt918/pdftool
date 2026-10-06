from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfReader

from pdftool.core.document import PdfDocument
from pdftool.core.impose import PRESETS, ImposeSpec, cell_rects, impose_pages
from pdftool.core.pageops import PageList, PageRef, WriteCancelled
from pdftool.core.stamp import page_number_stamp

COLORS = [(230, 30, 30), (30, 200, 30), (30, 30, 230), (230, 220, 30), (200, 30, 200)]


def color_pdf(path: Path, n: int, size=(600, 800)) -> Path:
    imgs = [Image.new("RGB", size, COLORS[i % len(COLORS)]) for i in range(n)]
    imgs[0].save(path, save_all=True, append_images=imgs[1:], resolution=100)   # 432x576 pt
    return path


def sheet_size(pdf: Path, i=0):
    p = PdfReader(str(pdf)).pages[i]
    return float(p.mediabox.width), float(p.mediabox.height)


def pixel_at_cell(pdf: Path, sheet: int, spec: ImposeSpec, slot: int, dpi=36):
    """sheet 枚目の slot 番目の枠の中心の色。"""
    x, y, w, h = cell_rects(spec)[slot]
    sw, sh = spec.sheet_size_pt()
    with PdfDocument(pdf) as d:
        img = d.render_page(sheet, dpi=dpi)
    s = dpi / 72
    return img.getpixel((round((x + w / 2) * s), round((sh - (y + h / 2)) * s)))


def near(a, b, tol=40):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def test_spec_validation_and_sizes():
    with pytest.raises(ValueError):
        ImposeSpec(cols=0)
    with pytest.raises(ValueError):
        ImposeSpec(paper="A0")
    with pytest.raises(ValueError):
        cell_rects(ImposeSpec(cols=2, rows=1, margin_mm=200))
    w, h = PRESETS["2in1"].sheet_size_pt()
    assert w > h and (w, h) == pytest.approx((841.9, 595.3), abs=0.2)     # A4 横
    w, h = PRESETS["4in1"].sheet_size_pt()
    assert h > w                                                          # A4 縦
    assert PRESETS["4in1"].label == "4in1"


def test_cell_rects_order_and_rtl():
    r = cell_rects(ImposeSpec(cols=2, rows=2))
    # 読む順: 左上 → 右上 → 左下 → 右下
    assert r[0][0] < r[1][0] and r[0][1] == r[1][1] and r[0][1] > r[2][1]
    rtl = cell_rects(ImposeSpec(cols=2, rows=1, right_to_left=True))
    assert rtl[0][0] > rtl[1][0]


def test_2in1_places_pages_left_to_right(tmp_path: Path):
    src = color_pdf(tmp_path / "c.pdf", 4)
    spec = PRESETS["2in1"]
    out = impose_pages(PageList.load_refs(src), tmp_path / "o.pdf", spec)
    assert len(PdfReader(str(out)).pages) == 2
    assert sheet_size(out) == pytest.approx(spec.sheet_size_pt(), abs=0.1)
    assert near(pixel_at_cell(out, 0, spec, 0), COLORS[0])
    assert near(pixel_at_cell(out, 0, spec, 1), COLORS[1])
    assert near(pixel_at_cell(out, 1, spec, 0), COLORS[2])
    assert near(pixel_at_cell(out, 1, spec, 1), COLORS[3])


def test_4in1_with_remainder(tmp_path: Path):
    src = color_pdf(tmp_path / "c.pdf", 5)
    spec = PRESETS["4in1"]
    out = impose_pages(PageList.load_refs(src), tmp_path / "o.pdf", spec)
    assert len(PdfReader(str(out)).pages) == 2
    for slot in range(4):
        assert near(pixel_at_cell(out, 0, spec, slot), COLORS[slot])
    assert near(pixel_at_cell(out, 1, spec, 0), COLORS[4])
    assert near(pixel_at_cell(out, 1, spec, 1), (255, 255, 255))         # 余りの枠は白紙


def test_right_to_left_puts_first_page_on_the_right(tmp_path: Path):
    src = color_pdf(tmp_path / "c.pdf", 2)
    spec = ImposeSpec(cols=2, rows=1, right_to_left=True)
    out = impose_pages(PageList.load_refs(src), tmp_path / "o.pdf", spec)
    # 枠は読む順に並んでいるので、スロット0（=1ページ目）が右にある
    assert near(pixel_at_cell(out, 0, spec, 0), COLORS[0])
    assert cell_rects(spec)[0][0] > cell_rects(spec)[1][0]


def test_mixed_sizes_and_rotation_fit_inside_cells(tmp_path: Path):
    a = color_pdf(tmp_path / "a.pdf", 1, size=(600, 800))
    b = color_pdf(tmp_path / "b.pdf", 1, size=(1200, 600))              # 横長
    refs = [PageRef(a, 0, 90), PageRef(b, 0)]
    spec = PRESETS["2in1"]
    out = impose_pages(refs, tmp_path / "o.pdf", spec)
    # どちらも枠の中心に置かれる（色が出る）
    assert near(pixel_at_cell(out, 0, spec, 0), COLORS[0])
    assert near(pixel_at_cell(out, 0, spec, 1), COLORS[0])
    # 枠からはみ出さない: 用紙の外周余白(8mm)の中は白
    with PdfDocument(out) as d:
        img = d.render_page(0, dpi=36)
    assert img.getpixel((2, 2)) == (255, 255, 255)


def test_crop_is_respected(tmp_path: Path):
    src = color_pdf(tmp_path / "c.pdf", 1)
    ref = PageRef(src, 0, 0, (0.0, 0.0, 0.0, 0.5))      # 下半分を切る → 432x288（横長）
    spec = PRESETS["4in1"]
    out = impose_pages([ref], tmp_path / "o.pdf", spec)
    x, y, w, h = cell_rects(spec)[0]
    sw, sh = spec.sheet_size_pt()
    with PdfDocument(out) as d:
        img = d.render_page(0, dpi=36)
    s = 36 / 72
    # 枠の上端付近（縦長のまま置かれていれば色が出る場所）。切ってあるので横長になり、中央に寄る
    top_inside = img.getpixel((round((x + w / 2) * s), round((sh - (y + h - 3)) * s)))
    assert top_inside == (255, 255, 255)


def test_border_draws_frames(tmp_path: Path):
    src = color_pdf(tmp_path / "c.pdf", 2)
    plain = impose_pages(PageList.load_refs(src), tmp_path / "p.pdf", ImposeSpec(cols=2, rows=1))
    framed = impose_pages(PageList.load_refs(src), tmp_path / "f.pdf", ImposeSpec(cols=2, rows=1, border=True))
    with PdfDocument(plain) as a, PdfDocument(framed) as b:
        ia, ib = a.render_page(0, dpi=72), b.render_page(0, dpi=72)
    from PIL import ImageChops
    assert ImageChops.difference(ia, ib).getbbox() is not None


def test_stamps_are_applied_before_imposition(tmp_path: Path):
    src = color_pdf(tmp_path / "c.pdf", 2)
    spec = PRESETS["2in1"]
    plain = impose_pages(PageList.load_refs(src), tmp_path / "p.pdf", spec)
    stamped = impose_pages(PageList.load_refs(src), tmp_path / "s.pdf", spec,
                           stamps=[page_number_stamp("{n}", size_pt=24)])
    from PIL import ImageChops
    with PdfDocument(plain) as a, PdfDocument(stamped) as b:
        assert ImageChops.difference(a.render_page(0, dpi=72), b.render_page(0, dpi=72)).getbbox() is not None


def test_progress_cancel_and_refuses_overwrite(tmp_path: Path):
    src = color_pdf(tmp_path / "c.pdf", 4)
    refs = PageList.load_refs(src)
    seen = []
    impose_pages(refs, tmp_path / "o.pdf", PRESETS["2in1"], progress=seen.append)
    assert [p.current for p in seen] == [1, 2] and seen[-1].total == 2
    with pytest.raises(WriteCancelled):
        impose_pages(refs, tmp_path / "x.pdf", PRESETS["2in1"], cancel=lambda: True)
    assert not (tmp_path / "x.pdf").exists()
    from pdftool.core.document import PdfError
    with pytest.raises(PdfError):
        impose_pages(refs, src, PRESETS["2in1"])


def test_impose_uses_visible_area_when_cropbox_is_smaller(tmp_path: Path):
    from pypdf import PdfWriter
    from pypdf.generic import RectangleObject

    base = color_pdf(tmp_path / "base.pdf", 1)                       # 432x576pt
    w = PdfWriter(clone_from=str(base))
    w.pages[0].cropbox = RectangleObject((100, 100, 332, 476))       # 見えるのは 232x376
    cb = tmp_path / "cb.pdf"
    w.write(cb)
    spec = PRESETS["2in1"]
    out = impose_pages([PageRef(cb, 0)], tmp_path / "o.pdf", spec)
    x, y, cw, ch = cell_rects(spec)[0]
    sw, sh = spec.sheet_size_pt()
    with PdfDocument(out) as d:
        img = d.render_page(0, dpi=36)
    s = 36 / 72
    # 赤い領域の外接矩形（赤=R 大・G 小）
    r, g, b = img.convert("RGB").split()
    mask = Image.eval(g, lambda v: 255 if v < 100 else 0)
    bb = mask.getbbox()
    assert bb is not None
    # 見えている 232x376 が枠いっぱい（縦方向 ch に収まる最大倍率）で置かれる
    expected_scale = min(cw / 232, ch / 376)
    assert (bb[3] - bb[1]) / s == pytest.approx(376 * expected_scale, rel=0.05)
    assert (bb[2] - bb[0]) / s == pytest.approx(232 * expected_scale, rel=0.05)
    cx = (bb[0] + bb[2]) / 2 / s
    assert cx == pytest.approx(x + cw / 2, abs=6)                    # 枠の中央
