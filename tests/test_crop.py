from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter

from conftest import make_blank_pdf, make_image_pdf
from pdftool.core.document import PdfDocument, rotate_crop, validate_crop
from pdftool.core.pageops import PageList, PageRef, write_pages


def size_of(pdf: Path, i=0):
    p = PdfReader(str(pdf)).pages[i]
    return float(p.mediabox.width), float(p.mediabox.height), p.rotation


def test_validate_crop():
    assert validate_crop(None) is None
    assert validate_crop((0, 0, 0, 0)) is None
    assert validate_crop((0.1, 0.2, 0.0, 0.0)) == (0.1, 0.2, 0.0, 0.0)
    with pytest.raises(ValueError):
        validate_crop((-0.1, 0, 0, 0))
    with pytest.raises(ValueError):
        validate_crop((0.5, 0, 0.5, 0))


def test_rotate_crop_cycles_back():
    c = (0.1, 0.2, 0.3, 0.4)
    assert rotate_crop(c, 90) == (0.4, 0.1, 0.2, 0.3)
    assert rotate_crop(c, 180) == (0.3, 0.4, 0.1, 0.2)
    assert rotate_crop(c, 270) == (0.2, 0.3, 0.4, 0.1)
    r = c
    for _ in range(4):
        r = rotate_crop(r, 90)
    assert r == c


def test_pageref_normalizes_crop(tmp_path: Path):
    assert PageRef(tmp_path / "a.pdf", 0, 0, (0, 0, 0, 0)).crop is None
    assert PageRef(tmp_path / "a.pdf", 0, 90, (0.1, 0, 0, 0)).rotated(90).crop == (0.1, 0.0, 0.0, 0.0)


def test_render_applies_crop_and_rotation(image_pdf: Path):
    crop = (0.1, 0.2, 0.3, 0.0)   # 左10% 上20% 右30%
    with PdfDocument(image_pdf) as doc:
        full = doc.render_page(0, dpi=72)
        cropped = doc.render_page(0, dpi=72, crop=crop)
        assert cropped.width == pytest.approx(full.width * 0.6, abs=2)
        assert cropped.height == pytest.approx(full.height * 0.8, abs=2)
        rot = doc.render_page(0, dpi=72, rotation=90, crop=crop)
        # 90度回すと 幅と高さが入れ替わる
        assert rot.width == pytest.approx(full.height * 0.8, abs=2)
        assert rot.height == pytest.approx(full.width * 0.6, abs=2)
        th = doc.render_thumbnail(0, width=100, crop=crop)
        assert th.width == pytest.approx(100, abs=2)


def test_write_pages_applies_crop(image_pdf: Path, tmp_path: Path):
    ref = PageRef(image_pdf, 0, 0, (0.1, 0.2, 0.3, 0.0))
    out = write_pages([ref], tmp_path / "o.pdf")
    w, h, _ = size_of(out)
    assert w == pytest.approx(288 * 0.6, abs=0.5)
    assert h == pytest.approx(384 * 0.8, abs=0.5)


def test_write_pages_crop_with_source_rotate_and_user_rotation(tmp_path: Path):
    # 元PDFに /Rotate 90 が付いたページ（見た目は 384x288 の横長）
    src = make_blank_pdf(tmp_path / "r.pdf", pages=1, width=288, height=384)
    wtr = PdfWriter(clone_from=str(src))
    wtr.pages[0].rotate(90)
    rpdf = tmp_path / "rot.pdf"
    wtr.write(rpdf)
    # 見た目の左 25% / 上 50% を落とす
    out = write_pages([PageRef(rpdf, 0, 0, (0.25, 0.5, 0.0, 0.0))], tmp_path / "o1.pdf")
    with PdfDocument(out) as d:
        info = d.page_info(0)
    assert info.width_pt == pytest.approx(384 * 0.75, abs=0.5)
    assert info.height_pt == pytest.approx(288 * 0.5, abs=0.5)
    # さらにユーザー回転 90 → 幅と高さが入れ替わる
    out2 = write_pages([PageRef(rpdf, 0, 90, (0.25, 0.5, 0.0, 0.0))], tmp_path / "o2.pdf")
    with PdfDocument(out2) as d:
        info2 = d.page_info(0)
    assert info2.width_pt == pytest.approx(288 * 0.5, abs=0.5)
    assert info2.height_pt == pytest.approx(384 * 0.75, abs=0.5)


def test_crop_keeps_visible_content_aligned(tmp_path: Path):
    """切った後の見た目が、元をレンダリングして同じ箇所を切り取ったものと一致する。"""
    src = make_image_pdf(tmp_path / "g.pdf", pages=1, size=(400, 600))
    crop = (0.2, 0.1, 0.1, 0.3)
    out = write_pages([PageRef(src, 0, 0, crop)], tmp_path / "c.pdf")
    with PdfDocument(src) as a, PdfDocument(out) as b:
        want = a.render_page(0, dpi=72, crop=crop)
        got = b.render_page(0, dpi=72)
    assert abs(want.width - got.width) <= 2 and abs(want.height - got.height) <= 2
    wp, gp = want.getpixel((5, 5)), got.getpixel((5, 5))
    assert all(abs(x - y) < 40 for x, y in zip(wp, gp))


def test_set_crops_with_undo(blank_pdf: Path):
    pl = PageList.from_pdf(blank_pdf)
    pl.set_crops({1: (0.1, 0.1, 0.1, 0.1), 3: (0.2, 0, 0, 0)})
    assert pl[1].crop == (0.1, 0.1, 0.1, 0.1) and pl[3].crop == (0.2, 0.0, 0.0, 0.0) and pl[0].crop is None
    pl.set_crops({1: None})
    assert pl[1].crop is None and pl[3].crop is not None
    assert pl.undo() and pl[1].crop is not None
    with pytest.raises(IndexError):
        pl.set_crops({99: (0.1, 0, 0, 0)})
    # 変化が無ければ Undo スタックを汚さない
    n = len(pl._undo)
    pl.set_crops({0: None})
    assert len(pl._undo) == n
