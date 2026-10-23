"""手書きの書き込み（ペン・蛍光ペン）。保存した PDF に線が出ること・文字が残ることを確かめる。"""

from pathlib import Path

import pytest
from pypdf import PdfReader

from conftest import make_blank_pdf
from pdftool.core.document import PdfDocument
from pdftool.core.impose import PRESETS, impose_pages
from pdftool.core.ink import MARKER, PEN, Stroke, distance_to_stroke
from pdftool.core.pageops import PageRef, write_pages
from test_redact import COVER, make_text_pdf

RED = (255, 0, 0)
YELLOW = (255, 255, 0)


def _pixel(path: Path, index: int, fx: float, fy: float, dpi: int = 36):
    with PdfDocument(path) as doc:
        img = doc.render_page(index, dpi=dpi)
    return img.getpixel((int(fx * (img.width - 1)), int(fy * (img.height - 1))))


def test_stroke_validation():
    with pytest.raises(ValueError):
        Stroke("brush", RED, 0.01, ((0, 0),))
    with pytest.raises(ValueError):
        Stroke(PEN, RED, 0.01, ())
    with pytest.raises(ValueError):
        Stroke(PEN, RED, 0.0, ((0, 0),))


def test_distance_to_stroke():
    st = Stroke(PEN, RED, 0.01, ((0.0, 0.5), (1.0, 0.5)))
    assert distance_to_stroke(st, 0.5, 0.5) == pytest.approx(0.0)
    assert distance_to_stroke(st, 0.5, 0.6) == pytest.approx(0.1)
    assert distance_to_stroke(st, 1.5, 0.5) == pytest.approx(0.5)            # 端より先は端からの距離
    assert distance_to_stroke(st, 0.5, 0.6, aspect=2.0) == pytest.approx(0.2)  # 縦長ページは縦を幅基準に直す
    dot = Stroke(PEN, RED, 0.01, ((0.3, 0.3),))
    assert distance_to_stroke(dot, 0.3, 0.4) == pytest.approx(0.1)


def test_pen_line_is_drawn_and_page_text_is_kept(tmp_path: Path):
    src = make_text_pdf(tmp_path / "a.pdf", ["HELLO"])
    line = Stroke(PEN, RED, 0.02, ((0.1, 0.5), (0.9, 0.5)))
    out = write_pages([PageRef(src, 0)], tmp_path / "out.pdf", inks={0: [line]})
    assert "HELLO" in PdfReader(str(out)).pages[0].extract_text()    # 書き込みは重ねるだけ。本文は残る
    r, g, b = _pixel(out, 0, 0.5, 0.5)
    assert r > 200 and g < 60 and b < 60
    assert _pixel(out, 0, 0.5, 0.9) == (255, 255, 255)               # 線の無い所は白のまま


def test_marker_multiplies_so_text_stays_dark(tmp_path: Path):
    """蛍光ペンは乗算。黒い文字の上でも黒のまま、白い所だけ黄色になる。"""
    src = make_text_pdf(tmp_path / "a.pdf", ["HELLO"])
    band = Stroke(MARKER, YELLOW, 0.06, ((0.05, 0.12), (0.7, 0.12)))     # 文字（y≈0.07〜0.12）を覆う
    out = write_pages([PageRef(src, 0)], tmp_path / "out.pdf", inks={0: [band]})
    with PdfDocument(out) as doc:
        img = doc.render_page(0, dpi=72).convert("RGB")
    colors = {img.getpixel((x, y)) for x in range(40, 400, 3) for y in range(60, 130)}
    assert (255, 255, 0) in colors                                  # 白地は黄色
    assert any(c[0] < 80 and c[1] < 80 and c[2] < 80 for c in colors)   # 文字の黒は残る（黄色で上書きされない）


def test_ink_follows_rotation_and_crop(tmp_path: Path):
    """座標は回転・トリミング後の見た目基準。"""
    src = make_blank_pdf(tmp_path / "b.pdf", pages=1)                # 595x842
    ref = PageRef(src, 0, rotation=90, crop=(0.0, 0.0, 0.0, 0.5))    # 下半分を切り（595x421）、右へ 90 度 → 421x595
    dot = Stroke(PEN, RED, 0.2, ((0.9, 0.1),))                       # 見た目の右上
    out = write_pages([ref], tmp_path / "out.pdf", inks={0: [dot]})
    page = PdfReader(str(out)).pages[0]
    assert float(page.mediabox.width) == pytest.approx(421, abs=2)
    assert float(page.mediabox.height) == pytest.approx(595, abs=2)
    r, g, b = _pixel(out, 0, 0.9, 0.1)
    assert r > 200 and g < 60
    assert _pixel(out, 0, 0.1, 0.9) == (255, 255, 255)


def test_ink_on_redacted_page_and_impose(tmp_path: Path):
    src = make_text_pdf(tmp_path / "a.pdf", ["SECRET", "PUBLIC"])
    refs = [PageRef(src, 0), PageRef(src, 1)]
    mark = Stroke(PEN, (0, 0, 255), 0.02, ((0.1, 0.8), (0.9, 0.8)))
    out = write_pages(refs, tmp_path / "r.pdf", redactions={0: [COVER]}, inks={0: [mark], 1: [mark]})
    reader = PdfReader(str(out))
    assert "SECRET" not in reader.pages[0].extract_text()           # 墨消しは維持
    assert _pixel(out, 0, 0.5, 0.8)[2] > 200 and _pixel(out, 0, 0.5, 0.8)[0] < 60   # 墨消しページにも書ける
    assert "PUBLIC" in reader.pages[1].extract_text()
    two = impose_pages(refs, tmp_path / "2in1.pdf", PRESETS["2in1"], inks={1: [mark]})
    assert len(PdfReader(str(two)).pages) == 1
    with PdfDocument(two) as doc:
        img = doc.render_page(0, dpi=72).convert("RGB")
    px = img.load()
    assert any(px[x, y][2] > 200 and px[x, y][0] < 60 and px[x, y][1] < 60
               for x in range(img.width) for y in range(img.height))
