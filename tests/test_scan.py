"""白紙検出と自動トリミング（実際にレンダリングして判定する）。"""

import random
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from pdftool.core.blank import (
    SENSITIVITY_LOOSE,
    SENSITIVITY_NORMAL,
    SENSITIVITY_STRICT,
    detect_blank_pages,
    ink_ratio,
)
from pdftool.core.document import PdfDocument
from pdftool.core.pageops import PageList, PageRef
from pdftool.core.trim import ScanCancelled, auto_crops, content_margins, manual_crops

W, H = 800, 1000     # 100dpi → 576 x 720 pt


def white():
    return Image.new("RGB", (W, H), "white")


def with_box():
    im = white()
    ImageDraw.Draw(im).rectangle((200, 300, 599, 699), fill="black")
    return im


def with_text_lines():
    im = white()
    d = ImageDraw.Draw(im)
    for k in range(12):
        d.rectangle((100, 150 + k * 60, 700, 165 + k * 60), fill=(30, 30, 30))
    return im


def with_footer_number_only():
    im = white()
    ImageDraw.Draw(im).rectangle((390, 940, 410, 952), fill="black")     # 小さなページ番号
    return im


def scan_noise_page():
    """白紙のスキャン: 地の色が少し灰色、四辺に影、ごま塩ノイズ。"""
    im = Image.new("RGB", (W, H), (236, 236, 232))
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, W, 8), fill=(60, 60, 60))
    d.rectangle((0, H - 8, W, H), fill=(60, 60, 60))
    d.rectangle((0, 0, 8, H), fill=(60, 60, 60))
    rnd = random.Random(1)
    for _ in range(40):
        x, y = rnd.randrange(30, W - 30), rnd.randrange(30, H - 30)
        d.point((x, y), fill=(90, 90, 90))
    return im


def make_pdf(path: Path, images: list[Image.Image]) -> Path:
    images[0].save(path, save_all=True, append_images=images[1:], resolution=100)
    return path


def test_ink_ratio_basics():
    assert ink_ratio(white().convert("L")) == 0.0
    assert ink_ratio(with_box().convert("L")) == pytest.approx(160000 / (736 * 920), abs=0.01)


def test_detect_blank_pages_normal(tmp_path: Path):
    pdf = make_pdf(tmp_path / "s.pdf", [white(), with_text_lines(), scan_noise_page(), with_box(), with_footer_number_only()])
    refs = PageList.load_refs(pdf)
    assert detect_blank_pages(refs) == [0, 2, 4]          # 白紙・スキャン白紙・ページ番号だけ


def test_sensitivity_levels(tmp_path: Path):
    pdf = make_pdf(tmp_path / "s.pdf", [white(), with_footer_number_only(), with_text_lines()])
    refs = PageList.load_refs(pdf)
    assert detect_blank_pages(refs, sensitivity=SENSITIVITY_STRICT) == [0]
    assert detect_blank_pages(refs, sensitivity=SENSITIVITY_NORMAL) == [0, 1]
    assert detect_blank_pages(refs, sensitivity=SENSITIVITY_LOOSE) == [0, 1]
    assert 2 not in detect_blank_pages(refs, sensitivity=SENSITIVITY_LOOSE)


def test_blank_detection_respects_crop(tmp_path: Path):
    """余白のごみはトリミングで切り捨てた後なら数えない。"""
    im = white()
    ImageDraw.Draw(im).rectangle((0, 0, 799, 90), fill="black")      # 上端の黒帯（切り捨てる予定）
    pdf = make_pdf(tmp_path / "s.pdf", [im])
    ref = PageRef(pdf, 0)
    assert detect_blank_pages([ref]) == []
    assert detect_blank_pages([PageRef(pdf, 0, 0, (0.0, 0.15, 0.0, 0.0))]) == [0]


def test_detect_progress_and_cancel(tmp_path: Path):
    pdf = make_pdf(tmp_path / "s.pdf", [white(), white(), white()])
    refs = PageList.load_refs(pdf)
    seen = []
    detect_blank_pages(refs, progress=seen.append)
    assert [p.current for p in seen] == [1, 2, 3] and seen[-1].total == 3
    with pytest.raises(ScanCancelled):
        detect_blank_pages(refs, cancel=lambda: True)
    with pytest.raises(ValueError):
        detect_blank_pages(refs, sensitivity=-1)


def test_content_margins_finds_box(tmp_path: Path):
    pdf = make_pdf(tmp_path / "b.pdf", [with_box()])
    with PdfDocument(pdf) as doc:
        l, t, r, b = content_margins(doc, 0, pad_mm=0)
        assert (l, t, r, b) == pytest.approx((0.25, 0.30, 0.25, 0.30), abs=0.015)
        l2, t2, r2, b2 = content_margins(doc, 0, pad_mm=5)
        assert l2 < l and t2 < t           # 余白を残す分だけ削る量は減る
        assert l - l2 == pytest.approx(5 / 25.4 * 72 / 576, abs=0.01)


def test_content_margins_ignores_scan_shadow_and_specks(tmp_path: Path):
    im = scan_noise_page()
    ImageDraw.Draw(im).rectangle((200, 300, 599, 699), fill=(20, 20, 20))
    pdf = make_pdf(tmp_path / "n.pdf", [im])
    with PdfDocument(pdf) as doc:
        c = content_margins(doc, 0, pad_mm=0)
    assert c == pytest.approx((0.25, 0.30, 0.25, 0.30), abs=0.02)


def test_content_margins_blank_returns_none(tmp_path: Path):
    pdf = make_pdf(tmp_path / "w.pdf", [white(), scan_noise_page()])
    with PdfDocument(pdf) as doc:
        assert content_margins(doc, 0) is None
        assert content_margins(doc, 1) is None


def test_auto_crops_and_unify(tmp_path: Path):
    a = white(); ImageDraw.Draw(a).rectangle((100, 200, 499, 599), fill="black")
    b = white(); ImageDraw.Draw(b).rectangle((300, 400, 699, 799), fill="black")
    pdf = make_pdf(tmp_path / "u.pdf", [a, white(), b])
    refs = PageList.load_refs(pdf)
    per = auto_crops(refs, [0, 1, 2], pad_mm=0)
    assert 1 not in per                                   # 白紙は変更しない
    assert per[0][0] == pytest.approx(0.125, abs=0.015)
    uni = auto_crops(refs, [0, 1, 2], pad_mm=0, unify=True)
    assert uni[0] == uni[1] == uni[2]                     # 全ページ同じ範囲（和集合）
    assert uni[0][0] == pytest.approx(0.125, abs=0.015)   # 左は a の位置
    assert uni[0][2] == pytest.approx(0.125, abs=0.015)   # 右は b の位置
    with pytest.raises(ScanCancelled):
        auto_crops(refs, [0], cancel=lambda: True)


def test_manual_crops_in_mm(tmp_path: Path):
    pdf = make_pdf(tmp_path / "m.pdf", [white(), white()])
    refs = PageList.load_refs(pdf)
    out = manual_crops(refs, [0, 1], left_mm=10, top_mm=20)
    mm = 72 / 25.4
    assert out[0] == pytest.approx((10 * mm / 576, 20 * mm / 720, 0, 0))
    assert manual_crops(refs, [0]) == {0: None}           # 全部 0 なら解除
    with pytest.raises(ValueError):
        manual_crops(refs, [0], left_mm=-1)
