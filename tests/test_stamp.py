from pathlib import Path

import pytest
from pypdf import PdfReader

from conftest import make_blank_pdf
from pdftool.core.document import PdfDocument
from pdftool.core.pageops import PageList, PageRef, WriteCancelled, write_pages
from pdftool.core.stamp import (
    TextStamp,
    label_stamp,
    page_number_stamp,
    render_stamp_image,
    watermark_stamp,
)


def dark_bbox(img, thresh=160):
    """画像の中で暗い（<thresh）ピクセルの外接矩形。無ければ None。"""
    g = img.convert("L").point(lambda p: 255 if p < thresh else 0)
    return g.getbbox()


def render(pdf: Path, i=0, dpi=72):
    with PdfDocument(pdf) as d:
        return d.render_page(i, dpi=dpi)


def test_stamp_validation():
    with pytest.raises(ValueError):
        TextStamp("x", position="middle")
    with pytest.raises(ValueError):
        TextStamp("x", opacity=2)
    with pytest.raises(ValueError):
        TextStamp("  ")
    with pytest.raises(ValueError):
        TextStamp("x", size_pt=0)


def test_render_stamp_image_japanese_and_alpha():
    img = render_stamp_image(label_stamp("配布用"), "配布用")
    assert img.mode == "RGBA" and img.getbbox() is not None
    assert img.getchannel("A").getextrema()[1] == 255
    faint = render_stamp_image(watermark_stamp("ひみつ", opacity=0.2), "ひみつ")
    assert faint.getchannel("A").getextrema()[1] <= 52          # 255*0.2


def test_page_number_bottom_center(blank_pdf: Path, tmp_path: Path):
    refs = PageList.load_refs(blank_pdf)                        # 595x842 の白紙 5 枚
    out = write_pages(refs, tmp_path / "n.pdf", stamps=[page_number_stamp("{n}", size_pt=14)])
    box = dark_bbox(render(out, 0))
    assert box is not None
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    assert abs(cx - 595 / 2) < 15 and cy > 842 - 60             # 下中央
    assert len(PdfReader(str(out)).pages) == 5


def test_numbering_start_total_and_skip_first(blank_pdf: Path, tmp_path: Path):
    refs = PageList.load_refs(blank_pdf)
    st = TextStamp("{n}/{total}", position="top-right", size_pt=14, start=1, skip_first=1)
    out = write_pages(refs, tmp_path / "s.pdf", stamps=[st])
    assert dark_bbox(render(out, 0)) is None                    # 表紙(先頭1枚)はスタンプ無し
    b1 = dark_bbox(render(out, 1))
    assert b1 is not None and b1[0] > 595 / 2 and b1[1] < 80    # 右上
    # 2 ページ目が「1/5」、3 ページ目が「2/5」: 文字幅が同じ桁数なので外接矩形の幅が一致する
    b2 = dark_bbox(render(out, 2))
    assert (b1[2] - b1[0]) == pytest.approx(b2[2] - b2[0], abs=2)


def test_watermark_is_shared_across_pages(tmp_path: Path):
    src = make_blank_pdf(tmp_path / "w.pdf", pages=12)
    refs = PageList.load_refs(src)
    out = write_pages(refs, tmp_path / "wm.pdf", stamps=[watermark_stamp("CONFIDENTIAL")])
    img = render(out, 3)
    assert dark_bbox(img, thresh=250) is not None              # うっすら描かれている
    assert dark_bbox(img, thresh=160) is None                  # 濃くはない（透かし）
    # 12 ページ分で画像 XObject は 1 組（rgb + smask）だけ。ファイルサイズも肥大しない
    xo = set()
    for pg in PdfReader(str(out)).pages:
        for ref in pg["/Resources"]["/XObject"].values():
            xo.add(ref.idnum)
    assert len(xo) == 1
    assert out.stat().st_size < 400_000


def test_stamp_with_rotated_page_stays_upright(tmp_path: Path):
    src = make_blank_pdf(tmp_path / "r.pdf", pages=1, width=300, height=500)
    ref = PageRef(src, 0, 90)                                   # 見た目は 500x300 の横長
    out = write_pages([ref], tmp_path / "o.pdf", stamps=[page_number_stamp("{n}", position="bottom-left", size_pt=14)])
    with PdfDocument(out) as d:
        info = d.page_info(0)
        img = d.render_page(0, dpi=72)
    assert (info.width_pt, info.height_pt) == pytest.approx((500, 300), abs=0.5)
    box = dark_bbox(img)
    assert box[0] < 60 and box[1] > 300 - 60                    # 回転後の見た目の左下


def test_stamp_with_crop(tmp_path: Path):
    src = make_blank_pdf(tmp_path / "c.pdf", pages=1, width=400, height=600)
    ref = PageRef(src, 0, 0, (0.25, 0.0, 0.25, 0.5))            # 200x300 になる
    out = write_pages([ref], tmp_path / "o.pdf", stamps=[page_number_stamp("{n}", size_pt=14)])
    with PdfDocument(out) as d:
        info = d.page_info(0)
        box = dark_bbox(d.render_page(0, dpi=72))
    assert (info.width_pt, info.height_pt) == pytest.approx((200, 300), abs=0.5)
    assert abs((box[0] + box[2]) / 2 - 100) < 15 and box[3] > 300 - 40


def test_japanese_label_renders(blank_pdf: Path, tmp_path: Path):
    out = write_pages(PageList.load_refs(blank_pdf)[:1], tmp_path / "j.pdf", stamps=[label_stamp("配布用")])
    assert dark_bbox(render(out, 0), thresh=200) is not None


def test_progress_and_cancel(blank_pdf: Path, tmp_path: Path):
    refs = PageList.load_refs(blank_pdf)
    seen = []
    write_pages(refs, tmp_path / "p.pdf", progress=seen.append)
    assert [p.current for p in seen] == [1, 2, 3, 4, 5]
    with pytest.raises(WriteCancelled):
        write_pages(refs, tmp_path / "q.pdf", cancel=lambda: True)
    assert not list(tmp_path.glob("q*.part"))                   # 中途半端なファイルを残さない


def test_pagelist_save_with_stamps(blank_pdf: Path):
    pl = PageList.from_pdf(blank_pdf)
    pl.remove([0])
    out = pl.save(stamps=[page_number_stamp()])
    assert len(PdfReader(str(out)).pages) == 4
    assert dark_bbox(render(out, 0)) is not None
