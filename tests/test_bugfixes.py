"""調査で見つけた不具合の回帰テスト。"""

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from conftest import make_image_pdf, wait_until
from pdftool.core.pageops import PageList
from pdftool.core.trim import auto_crops
from pdftool.ui.main_window import MainWindow


def test_status_message_is_cleared_after_opening_images(win: MainWindow, tmp_path: Path):
    """画像変換中の「変換しています…」が残ると、文書情報(ステータスバー左)が隠れたままになる。"""
    img = tmp_path / "a.png"
    Image.new("RGB", (400, 600), (200, 200, 100)).save(img)
    win.open_files([img])
    assert wait_until(lambda: win.page_list is not None and len(win.page_list) == 1)
    assert win.statusBar().currentMessage() == ""
    assert win.lbl_doc.isVisible() and "1 ページ" in win.lbl_doc.text()


def test_auto_crops_skips_page_that_would_leave_nothing(tmp_path: Path, monkeypatch):
    """1ページだけ極端な結果になっても、全体をエラーにせずそのページだけ飛ばす。"""
    import pdftool.core.trim as T

    im = Image.new("RGB", (800, 1000), "white")
    ImageDraw.Draw(im).rectangle((100, 100, 300, 300), fill="black")
    pdf = tmp_path / "a.pdf"
    im.save(pdf, save_all=True, append_images=[im, im], resolution=100)
    refs = PageList.load_refs(pdf)
    real = T.content_margins
    monkeypatch.setattr(T, "content_margins", lambda doc, i, **kw: (0.6, 0.0, 0.6, 0.0) if i == 1 else real(doc, i, **kw))
    out = auto_crops(refs, [0, 1, 2], pad_mm=0)
    assert 1 not in out and 0 in out and 2 in out
