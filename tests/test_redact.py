"""墨消し（黒塗り）。下の文字が本当に消えていることを確かめる。"""

from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from conftest import wait_until
from pdftool.core.document import PdfDocument
from pdftool.core.impose import PRESETS, impose_pages
from pdftool.core.pageops import PageList, PageRef, write_pages
from pdftool.core.redact import normalize_rect, normalize_rects
from pdftool.core.stamp import page_number_stamp


def make_text_pdf(path: Path, words: list[str]) -> Path:
    """1 ページに 1 語ずつ、Helvetica の本物の文字を左上寄りに置いた A4 の PDF。"""
    w = PdfWriter()
    for word in words:
        page = w.add_blank_page(width=595, height=842)
        font = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        })
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): w._add_object(font)})}
        )
        cs = DecodedStreamObject()
        cs.set_data(f"BT /F1 40 Tf 60 740 Td ({word}) Tj ET".encode("ascii"))
        page[NameObject("/Contents")] = w._add_object(cs)
    w.write(path)
    return path


# 文字（左上 x=60..約300pt, y=740..780pt）を覆う範囲（見た目のページに対する割合）
COVER = (0.05, 0.03, 0.70, 0.15)


def test_normalize_rect():
    assert normalize_rect((0.5, 0.6, 0.1, 0.2)) == (0.1, 0.2, 0.5, 0.6)
    assert normalize_rect((-1, 0, 2, 0.5)) == (0.0, 0.0, 1.0, 0.5)
    assert normalize_rect((0.1, 0.1, 0.1005, 0.5)) is None
    assert normalize_rects([(0, 0, 0.5, 0.5), (0.2, 0.2, 0.2, 0.2)]) == ((0.0, 0.0, 0.5, 0.5),)


def test_redacted_text_is_gone(tmp_path: Path):
    src = make_text_pdf(tmp_path / "名簿.pdf", ["SECRET", "PUBLIC"])
    assert "SECRET" in PdfReader(str(src)).pages[0].extract_text()
    refs = [PageRef(src, 0), PageRef(src, 1)]
    out = write_pages(refs, tmp_path / "out.pdf", redactions={0: [COVER]})
    reader = PdfReader(str(out))
    assert len(reader.pages) == 2
    assert "SECRET" not in reader.pages[0].extract_text()
    assert b"SECRET" not in out.read_bytes()
    assert "PUBLIC" in reader.pages[1].extract_text()          # 墨消ししないページはそのまま
    p0 = reader.pages[0]
    assert float(p0.mediabox.width) == pytest.approx(595, abs=1)
    assert float(p0.mediabox.height) == pytest.approx(842, abs=1)
    with PdfDocument(out) as doc:
        img = doc.render_page(0, dpi=36)
    cx, cy = int((COVER[0] + COVER[2]) / 2 * img.width), int((COVER[1] + COVER[3]) / 2 * img.height)
    assert img.getpixel((cx, cy)) == (0, 0, 0)
    assert img.getpixel((img.width // 2, int(img.height * 0.8))) == (255, 255, 255)


def test_redaction_follows_rotation_and_crop(tmp_path: Path):
    """範囲は回転・トリミング後の見た目基準。出力ページの大きさも見た目どおり。"""
    src = make_text_pdf(tmp_path / "a.pdf", ["SECRET"])
    ref = PageRef(src, 0, rotation=90, crop=(0.0, 0.0, 0.0, 0.5))   # 下半分を切ってから右へ 90 度
    out = write_pages([ref], tmp_path / "out.pdf", redactions={0: [(0.0, 0.0, 1.0, 1.0)]})
    page = PdfReader(str(out)).pages[0]
    assert float(page.mediabox.width) == pytest.approx(421, abs=2)    # 842 の半分が横幅に
    assert float(page.mediabox.height) == pytest.approx(595, abs=2)
    with PdfDocument(out) as doc:
        img = doc.render_page(0, dpi=36)
    assert img.getextrema() == ((0, 0), (0, 0), (0, 0))               # 全面黒


def test_redaction_with_stamps_and_impose(tmp_path: Path):
    src = make_text_pdf(tmp_path / "a.pdf", ["SECRET", "PUBLIC"])
    refs = [PageRef(src, 0), PageRef(src, 1)]
    out = impose_pages(refs, tmp_path / "2in1.pdf", PRESETS["2in1"],
                       stamps=[page_number_stamp("{n}", "bottom-center", 10)], redactions={0: [COVER]})
    data = out.read_bytes()
    assert b"SECRET" not in data
    assert "PUBLIC" in PdfReader(str(out)).pages[0].extract_text()


def test_pagelist_save_without_redaction_keeps_text(tmp_path: Path):
    src = make_text_pdf(tmp_path / "a.pdf", ["SECRET"])
    out = PageList.from_pdf(src).save(tmp_path / "out.pdf")
    assert "SECRET" in PdfReader(str(out)).pages[0].extract_text()


# ---------------------------------------------------------------------------
# UI（墨消しダイアログ・仕上げパネル）
# ---------------------------------------------------------------------------
def _drag(canvas, frac_from, frac_to, button=None):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    button = button or Qt.MouseButton.LeftButton
    r = canvas._page_rect()

    def pt(f):
        return QPoint(int(r.x() + f[0] * r.width()), int(r.y() + f[1] * r.height()))

    QTest.mousePress(canvas, button, Qt.KeyboardModifier.NoModifier, pt(frac_from))
    QTest.mouseMove(canvas, pt(frac_to))
    QTest.mouseRelease(canvas, button, Qt.KeyboardModifier.NoModifier, pt(frac_to))


def test_dialog_draw_and_delete(app, tmp_path: Path):
    from PySide6.QtCore import Qt

    from pdftool.ui.redact_dialog import RedactDialog

    src = make_text_pdf(tmp_path / "a.pdf", ["SECRET", "PUBLIC"])
    refs = [PageRef(src, 0), PageRef(src, 1)]
    dlg = RedactDialog(refs, {})
    dlg.show()
    app.processEvents()
    _drag(dlg.canvas, (0.1, 0.1), (0.5, 0.2))
    assert list(dlg.marks()) == [refs[0]]
    (rect,) = dlg.marks()[refs[0]]
    assert rect == pytest.approx((0.1, 0.1, 0.5, 0.2), abs=0.01)
    dlg.go(1)
    _drag(dlg.canvas, (0.3, 0.3), (0.3005, 0.3005))           # 小さすぎるクリックは無視
    assert refs[1] not in dlg.marks()
    dlg.go(0)
    assert len(dlg.canvas.rects()) == 1                       # 戻ると指定が残っている
    _drag(dlg.canvas, (0.3, 0.15), (0.3, 0.15), Qt.MouseButton.RightButton)   # 右クリックで消す
    assert dlg.marks() == {}
    dlg.close()


def test_finish_panel_redaction(win, tmp_path: Path, monkeypatch):
    from pdftool.ui import redact_dialog

    src = make_text_pdf(tmp_path / "名簿.pdf", ["SECRET", "PUBLIC"])
    win.open_pdf(src)
    panel = win.panels[3]
    pages = win.page_list.pages

    class FakeDialog:
        DialogCode = redact_dialog.RedactDialog.DialogCode

        def __init__(self, refs, marks, passwords, row=0, parent=None):
            pass

        def exec(self):
            return self.DialogCode.Accepted

        def marks(self):
            return {pages[0]: (COVER,)}

    monkeypatch.setattr("pdftool.ui.panels.finish_panel.RedactDialog", FakeDialog)
    panel.rd_edit_btn.click()
    assert panel.rd_box.isChecked() and "1 ページ・1 か所" in panel.rd_label.text()
    panel.run_btn.click()
    assert wait_until(lambda: "保存しました" in panel.status_label.text())
    out = panel._last_output
    assert out.name.endswith("_名簿_redacted.pdf")
    reader = PdfReader(str(out))
    assert "SECRET" not in reader.pages[0].extract_text()
    assert "PUBLIC" in reader.pages[1].extract_text()
    # 回転すると、そのページの指定は外れる（ずれた位置を塗らない）
    win.model.rotate_pages([0], 90)
    assert "外れた指定が 1 ページ分" in panel.rd_label.text()
