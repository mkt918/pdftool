"""ビューアの書き込み（ペン・蛍光ペン・消しゴム・直線・レイヤー切替・保存）。"""

from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from pypdf import PdfReader

from conftest import make_blank_pdf, wait_until
from pdftool.core.document import PdfDocument


def _open_viewer(win, tmp_path: Path, pages: int = 3, spread: bool = False):
    win.open_pdf(make_blank_pdf(tmp_path / "手書き.pdf", pages=pages))
    win.act_viewer.setChecked(True)
    vw = win.viewer
    vw.set_spread(spread)
    vw.show()
    win.show()
    assert wait_until(lambda: vw.right.image_rect() is not None)
    return vw


def _pt(view, fx: float, fy: float) -> QPoint:
    r = view.image_rect()
    return QPoint(int(r.x() + fx * r.width()), int(r.y() + fy * r.height()))


def _drag(view, path, hold_ms: int = 0):
    """path の点を順にたどる。最後の点で hold_ms だけ止めてから離す。"""
    QTest.mousePress(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, _pt(view, *path[0]))
    for f in path[1:]:
        QTest.mouseMove(view, _pt(view, *f))
    if hold_ms:
        QTest.qWait(hold_ms)
    QTest.mouseRelease(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, _pt(view, *path[-1]))


def test_reading_mode_does_not_draw(win, tmp_path: Path):
    vw = _open_viewer(win, tmp_path)
    assert vw.tool == "none"
    _drag(vw.right, [(0.2, 0.2), (0.6, 0.4)])
    assert vw.ink_count() == 0 and not vw.ink_dirty


def test_pen_draws_a_stroke_with_chosen_color_and_width(win, tmp_path: Path):
    vw = _open_viewer(win, tmp_path)
    vw.set_tool("pen")
    _drag(vw.right, [(0.2, 0.2), (0.4, 0.3), (0.6, 0.2)])
    (marks,) = vw.ink_marks().values()
    (st,) = marks
    assert st.kind == "pen" and len(st.points) == 3
    assert st.points[0] == pytest.approx((0.2, 0.2), abs=0.02)
    assert st.color == (220, 38, 38) and st.width == pytest.approx(0.004)
    assert vw.ink_dirty
    # 色と太さを変えると、次の線から反映される
    vw.ink_bar.slider.setValue(12)
    sw = [vw.ink_bar._swatch_lay.itemAt(k).widget() for k in range(vw.ink_bar._swatch_lay.count())]
    sw[2].click()                                         # 青
    _drag(vw.right, [(0.2, 0.6), (0.7, 0.62)])
    st2 = vw.ink_marks()[vw.right.ref][1]
    assert st2.color == (37, 99, 235) and st2.width == pytest.approx(0.012)


def test_pen_and_marker_remember_their_own_settings(win, tmp_path: Path):
    vw = _open_viewer(win, tmp_path)
    vw.set_tool("pen")
    vw.ink_bar.slider.setValue(9)
    vw.set_tool("marker")
    assert vw.ink_bar.slider.value() == 28                 # 蛍光ペンは太い既定値
    vw.ink_bar.slider.setValue(40)
    vw.set_tool("pen")
    assert vw.ink_bar.slider.value() == 9
    vw.set_tool("marker")
    _drag(vw.right, [(0.1, 0.5), (0.8, 0.5)])
    st = vw.ink_marks()[vw.right.ref][0]
    assert st.kind == "marker" and st.color == (253, 224, 71) and st.width == pytest.approx(0.04)


def test_holding_at_the_end_makes_a_straight_line(win, tmp_path: Path):
    vw = _open_viewer(win, tmp_path)
    vw.set_tool("pen")
    vw.right._hold.setInterval(60)
    wobbly = [(0.1, 0.1), (0.2, 0.25), (0.3, 0.12), (0.4, 0.3), (0.5, 0.15), (0.8, 0.5)]
    _drag(vw.right, wobbly, hold_ms=250)                  # 終点で止める → 始点と終点だけの直線
    (st,) = vw.ink_marks()[vw.right.ref]
    assert len(st.points) == 2
    assert st.points[0] == pytest.approx((0.1, 0.1), abs=0.02) and st.points[1] == pytest.approx((0.8, 0.5), abs=0.02)
    _drag(vw.right, wobbly)                               # 止めずに離せば、そのままの線
    assert len(vw.ink_marks()[vw.right.ref][1].points) == len(wobbly)


def test_straight_line_end_can_still_be_adjusted(win, tmp_path: Path):
    vw = _open_viewer(win, tmp_path)
    vw.set_tool("pen")
    vw.right._hold.setInterval(60)
    r = vw.right
    QTest.mousePress(r, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, _pt(r, 0.1, 0.1))
    QTest.mouseMove(r, _pt(r, 0.3, 0.2))
    QTest.mouseMove(r, _pt(r, 0.5, 0.2))
    QTest.qWait(250)                                      # 直線になった
    QTest.mouseMove(r, _pt(r, 0.9, 0.8))                  # そのまま動かすと終点が動く
    QTest.mouseRelease(r, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, _pt(r, 0.9, 0.8))
    (st,) = vw.ink_marks()[r.ref]
    assert len(st.points) == 2 and st.points[1] == pytest.approx((0.9, 0.8), abs=0.02)


def test_toggle_layer_hides_without_losing_ink(win, tmp_path: Path):
    vw = _open_viewer(win, tmp_path)
    vw.set_tool("pen")
    _drag(vw.right, [(0.2, 0.2), (0.6, 0.4)])
    vw.set_ink_visible(False)
    assert vw.ink_count() == 1 and not vw.ink_visible
    assert "非表示" in vw.ink_bar.layer_btn.text()
    img = vw.right.grab().toImage()
    vw.set_ink_visible(True)
    shown = vw.right.grab().toImage()
    assert img != shown                                    # 表示/非表示で見た目が変わる
    QTest.keyClick(vw, Qt.Key.Key_L)                       # キーでも一瞬で切り替わる
    assert not vw.ink_visible
    QTest.keyClick(vw, Qt.Key.Key_L)
    assert vw.ink_visible
    vw.set_ink_visible(False)
    vw.set_tool("marker")                                  # 隠れたまま描き始めると、見えるように戻る
    assert vw.ink_visible


def test_eraser_undo_and_clear_page(win, tmp_path: Path):
    vw = _open_viewer(win, tmp_path)
    vw.set_tool("pen")
    _drag(vw.right, [(0.1, 0.2), (0.9, 0.2)])
    _drag(vw.right, [(0.1, 0.7), (0.9, 0.7)])
    assert vw.ink_count() == 2
    vw.set_tool("eraser")
    _drag(vw.right, [(0.5, 0.7), (0.5, 0.7)])              # 下の線をクリックして消す
    (left,) = vw.ink_marks()[vw.right.ref]
    assert left.points[0][1] == pytest.approx(0.2, abs=0.02)
    assert vw.undo_ink() and vw.ink_count() == 2           # 消しゴムも 1 つ戻せる
    assert vw.undo_ink() and vw.ink_count() == 1           # 次は描いた線
    vw.clear_page_ink()
    assert vw.ink_count() == 0
    assert vw.undo_ink() and vw.ink_count() == 1           # ページ消去も戻せる
    vw.set_tool("none")
    assert not vw.ink_bar.swatch_box.isEnabled()


def test_ink_is_per_page_and_cleared_for_new_document(win, tmp_path: Path):
    vw = _open_viewer(win, tmp_path)
    vw.set_tool("pen")
    _drag(vw.right, [(0.2, 0.2), (0.6, 0.4)])
    first = vw.right.ref
    vw.step(1)
    assert vw.right.ref != first and vw.right.strokes == []
    vw.step(-1)
    assert len(vw.right.strokes) == 1
    win.open_pdf(make_blank_pdf(tmp_path / "別の文書.pdf", pages=2))
    assert vw.ink_count() == 0 and not vw.ink_dirty


def test_spread_mode_draws_on_the_page_under_the_pen(win, tmp_path: Path):
    vw = _open_viewer(win, tmp_path, pages=4, spread=True)
    vw.cover_check.setChecked(False)                       # 1-2 / 3-4
    assert wait_until(lambda: vw.left.image_rect() is not None and vw.right.image_rect() is not None)
    vw.set_tool("pen")
    _drag(vw.left, [(0.2, 0.2), (0.6, 0.4)])
    _drag(vw.right, [(0.3, 0.3), (0.7, 0.5)])
    marks = vw.ink_marks()
    assert set(marks) == {vw.left.ref, vw.right.ref} and vw.left.ref != vw.right.ref


def test_escape_leaves_the_tool_first(win, tmp_path: Path):
    vw = _open_viewer(win, tmp_path)
    QTest.keyClick(vw, Qt.Key.Key_P)
    assert vw.tool == "pen"
    QTest.keyClick(vw, Qt.Key.Key_Escape)
    assert vw.tool == "none" and win.act_viewer.isChecked()    # ビューアはまだ開いたまま
    QTest.keyClick(vw, Qt.Key.Key_Escape)
    assert not win.act_viewer.isChecked()


def test_save_with_ink_via_finish_panel(win, tmp_path: Path, monkeypatch):
    vw = _open_viewer(win, tmp_path)
    vw.set_tool("pen")
    _drag(vw.right, [(0.1, 0.5), (0.9, 0.5)])
    panel = win.panels[3]
    assert "1 ページ・1 本" in panel.ink_label.text()
    vw.ink_bar.save_btn.click()                            # 仕上げタブへ。書き込みの項目がオンになる
    assert win.tabs.currentWidget() is panel and panel.ink_box.isChecked()
    panel.run_btn.click()
    assert wait_until(lambda: "保存しました" in panel.status_label.text())
    out = panel._last_output
    assert out.name.endswith("_手書き_annotated.pdf")
    assert not vw.ink_dirty                                # 保存したので、終了時の確認は出ない
    assert len(PdfReader(str(out)).pages) == 3
    with PdfDocument(out) as doc:
        img = doc.render_page(0, dpi=72).convert("RGB")
    mid = img.width // 2                                  # 細い線なので、中央付近の縦方向で赤い画素を探す
    reds = [img.getpixel((mid, y)) for y in range(img.height // 2 - 6, img.height // 2 + 6)]
    assert any(r > 200 and g < 80 and b < 80 for r, g, b in reds)
    # 外れた書き込みの件数: ページを回転すると別ページ扱いになる
    win.view.select_rows([0])
    win.model.rotate_pages([0], 90)
    assert "外れた書き込み" in panel.ink_label.text()


def test_unsaved_ink_asks_before_closing_document(win, tmp_path: Path, monkeypatch):
    vw = _open_viewer(win, tmp_path)
    asked = []
    monkeypatch.setattr(win, "_confirm_discard", lambda: asked.append(1) or False)
    assert not win.has_unsaved()
    vw.set_tool("pen")
    _drag(vw.right, [(0.2, 0.2), (0.6, 0.4)])
    assert win.has_unsaved()
    assert win.open_pdf(make_blank_pdf(tmp_path / "x.pdf", pages=1)) is False and asked


def test_saving_ink_tells_the_user_clearly(win, tmp_path: Path):
    from PySide6.QtWidgets import QApplication, QMessageBox
    vw = _open_viewer(win, tmp_path)
    vw.set_tool("pen")
    _drag(vw.right, [(0.1, 0.5), (0.9, 0.5)])
    panel = win.panels[3]
    panel.ink_box.setChecked(True)
    assert "消しゴムで消せません" in panel.ink_box.findChild(type(panel.ink_label)).text()
    panel.run_btn.click()
    assert wait_until(lambda: "保存しました" in panel.status_label.text())
    assert "焼き込みました" in panel.status_label.text()
    boxes = [w for w in QApplication.topLevelWidgets() if isinstance(w, QMessageBox) and w.isVisible()]
    assert boxes and "書き込みを保存しました" in boxes[0].windowTitle()
    boxes[0].close()
