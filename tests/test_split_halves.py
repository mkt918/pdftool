"""ページを半分に分ける（A3 見開き → A4 2 ページ）。"""

from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfReader

from conftest import make_blank_pdf, wait_until
from pdftool.core.document import PdfDocument
from pdftool.core.pageops import PageList, split_crops


def _two_color_pdf(path: Path) -> Path:
    """左半分が赤・右半分が青の横長ページ 1 枚。"""
    img = Image.new("RGB", (400, 200), (0, 0, 255))
    img.paste((255, 0, 0), (0, 0, 200, 200))
    img.save(path, resolution=72)
    return path


def _center_color(path: Path, index: int) -> tuple[int, int, int]:
    with PdfDocument(path) as doc:
        img = doc.render_page(index, dpi=36)
    return img.getpixel((img.width // 2, img.height // 2))


def test_split_crops_halves_visible_area():
    assert split_crops(None, 0, "lr") == [(0.0, 0.0, 0.5, 0.0), (0.5, 0.0, 0.0, 0.0)]
    assert split_crops(None, 0, "rl") == [(0.5, 0.0, 0.0, 0.0), (0.0, 0.0, 0.5, 0.0)]
    assert split_crops(None, 0, "tb") == [(0.0, 0.0, 0.0, 0.5), (0.0, 0.5, 0.0, 0.0)]
    # 既存のトリミング（左右 10%）があれば、残りの 80% を半分にする
    left, right = split_crops((0.1, 0.0, 0.1, 0.0), 0, "lr")
    assert left == pytest.approx((0.1, 0.0, 0.5, 0.0))
    assert right == pytest.approx((0.5, 0.0, 0.1, 0.0))


def test_split_crops_follow_rotation():
    # 90 度回したページの「見た目の左半分」は、回転前の下半分
    left, right = split_crops(None, 90, "lr")
    assert left == (0.0, 0.5, 0.0, 0.0) or left == pytest.approx((0.0, 0.5, 0.0, 0.0))
    assert right == pytest.approx((0.0, 0.0, 0.0, 0.5))


def test_split_pages_outputs_half_size_pages(tmp_path: Path):
    src = make_blank_pdf(tmp_path / "見開き.pdf", pages=2, width=1190.55, height=841.89)  # A3 横
    pl = PageList.from_pdf(src)
    assert pl.split_pages([0], "lr") == 1
    assert len(pl) == 3
    out = pl.save(tmp_path / "out.pdf")
    pages = PdfReader(str(out)).pages
    assert len(pages) == 3
    for p in pages[:2]:          # A4 縦 2 枚
        assert float(p.mediabox.width) == pytest.approx(595.27, abs=0.5)
        assert float(p.mediabox.height) == pytest.approx(841.89, abs=0.5)
    assert float(pages[2].mediabox.width) == pytest.approx(1190.55, abs=0.5)
    assert pl.undo() and len(pl) == 2


@pytest.mark.parametrize("mode, first, second", [("lr", "red", "blue"), ("rl", "blue", "red")])
def test_split_pages_reading_order(tmp_path: Path, mode, first, second):
    src = _two_color_pdf(tmp_path / "色.pdf")
    pl = PageList.from_pdf(src)
    pl.split_pages([0], mode)
    out = pl.save(tmp_path / f"{mode}.pdf")

    def name(rgb):
        return "red" if rgb[0] > 200 and rgb[2] < 60 else "blue" if rgb[2] > 200 and rgb[0] < 60 else str(rgb)

    assert [name(_center_color(out, 0)), name(_center_color(out, 1))] == [first, second]


def test_split_rejects_unknown_mode(tmp_path: Path):
    pl = PageList.from_pdf(make_blank_pdf(tmp_path / "a.pdf", pages=1))
    with pytest.raises(ValueError):
        pl.split_pages([0], "xx")


def test_panel_halve_all(win, tmp_path: Path):
    src = make_blank_pdf(tmp_path / "見開き.pdf", pages=2, width=1190.55, height=841.89)
    win.open_files([src])
    assert wait_until(lambda: win.page_list is not None and len(win.page_list) == 2)
    panel = win.panels[2]
    panel.halve_all_btn.click()
    assert len(win.page_list) == 4
    assert win.model.rowCount() == 4
    assert "分けました" in panel.status_label.text()
