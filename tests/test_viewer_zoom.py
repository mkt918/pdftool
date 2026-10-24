"""ビューアの拡大・移動（Ctrl+ホイール、ドラッグ、キー、ダブルクリック、高解像度への切り替え）。"""

from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from conftest import wait_until
from test_viewer_ink import _drag, _open_viewer


def _wheel(widget, pos: QPoint, delta: int, mods=Qt.KeyboardModifier.NoModifier):
    e = QWheelEvent(QPointF(pos), QPointF(widget.mapToGlobal(pos)), QPoint(0, 0), QPoint(0, delta),
                    Qt.MouseButton.NoButton, mods, Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(widget, e)


def _stage_pos(vw, view, fx=0.5, fy=0.5) -> QPoint:
    r = view.image_rect()
    return view.mapTo(vw.stage, QPoint(int(r.x() + fx * r.width()), int(r.y() + fy * r.height())))


def test_zoom_in_out_and_fit(win, tmp_path: Path):
    vw = _open_viewer(win, tmp_path, spread=False)
    base = vw.right.width()
    assert vw.zoom == 1.0 and vw.zoom_btn.text() == "全体" and not vw.zoom_out_btn.isEnabled()
    vw.zoom_in_btn.click()
    assert vw.zoom == pytest.approx(1.25) and vw.right.width() == pytest.approx(base * 1.25, abs=2)
    assert vw.zoom_btn.text() == "125%"
    QTest.keyClick(vw, Qt.Key.Key_Plus)
    assert vw.zoom == pytest.approx(1.5625)
    QTest.keyClick(vw, Qt.Key.Key_Minus)
    assert vw.zoom == pytest.approx(1.25)
    vw.zoom_btn.click()
    assert vw.zoom == 1.0 and vw.right.width() == base
    vw.set_zoom(99)
    assert vw.zoom == 6.0 and not vw.zoom_in_btn.isEnabled()
    QTest.keyClick(vw, Qt.Key.Key_0)
    assert vw.zoom == 1.0


def test_ctrl_wheel_zooms_around_the_cursor(win, tmp_path: Path):
    vw = _open_viewer(win, tmp_path, spread=False)
    r = vw.right
    fx, fy = 0.6, 0.55
    p = _stage_pos(vw, r, fx, fy)
    _wheel(vw.stage, p, 120 * 4, Qt.KeyboardModifier.ControlModifier)
    assert vw.zoom > 1.5
    # カーソルの下のページ上の点は、拡大しても同じ場所にある
    ir = r.image_rect()
    after = r.mapTo(vw.stage, QPoint(int(ir.x() + fx * ir.width()), int(ir.y() + fy * ir.height())))
    assert abs(after.y() - p.y()) <= 3                     # ページが台より細い横方向は動かさないので、縦で確かめる
    _wheel(vw.stage, p, -120 * 40, Qt.KeyboardModifier.ControlModifier)
    assert vw.zoom == 1.0


def test_plain_wheel_turns_pages_only_when_not_zoomed(win, tmp_path: Path):
    vw = _open_viewer(win, tmp_path, pages=3, spread=False)
    assert vw.row == 0
    _wheel(vw.stage, QPoint(50, 50), -120)
    assert vw.row == 1
    _wheel(vw.stage, QPoint(50, 50), 120)
    assert vw.row == 0
    vw.set_zoom(3)
    before = vw.right.pos()
    _wheel(vw.stage, QPoint(50, 50), -120)                  # 拡大中は動かす（めくらない）
    assert vw.row == 0 and vw.right.y() < before.y()


def test_drag_pans_and_stays_inside_the_page(win, tmp_path: Path):
    vw = _open_viewer(win, tmp_path, spread=False)
    vw.set_zoom(3)
    st, r = vw.stage, vw.right
    top0 = r.y()
    QTest.mousePress(st, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(300, 300))
    QTest.mouseMove(st, QPoint(300, 200))
    QTest.mouseRelease(st, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(300, 200))
    assert r.y() < top0                                    # ドラッグした向きにページが動く
    for _ in range(40):                                    # 動かしすぎても、ページの端より先へは行かない
        vw.stage.pan_by(-500, -500)
    ir = r.image_rect()
    assert r.mapTo(st, QPoint(ir.right(), ir.bottom())).x() >= st.width() - st.MARGIN - 2
    assert r.mapTo(st, QPoint(ir.right(), ir.bottom())).y() >= st.height() - st.MARGIN - 2
    for _ in range(40):
        vw.stage.pan_by(500, 500)
    assert r.mapTo(st, QPoint(ir.x(), ir.y())).x() <= st.MARGIN + 2


def test_arrow_keys_pan_when_zoomed_and_turn_when_not(win, tmp_path: Path):
    vw = _open_viewer(win, tmp_path, pages=3, spread=False)
    QTest.keyClick(vw, Qt.Key.Key_Right)
    assert vw.row == 1
    vw.set_zoom(3)
    x0 = vw.right.x()
    QTest.keyClick(vw, Qt.Key.Key_Right)
    assert vw.row == 1 and vw.right.x() < x0               # 拡大中は右へ動かす
    QTest.keyClick(vw, Qt.Key.Key_PageDown)
    assert vw.row == 2                                     # めくるのは PageDown


def test_double_click_toggles_zoom(win, tmp_path: Path):
    vw = _open_viewer(win, tmp_path, spread=False)
    p = _stage_pos(vw, vw.right)
    QTest.mouseDClick(vw.right, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                      vw.right.mapFrom(vw.stage, p))
    assert vw.zoom == pytest.approx(2.5)
    QTest.mouseDClick(vw.right, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                      vw.right.mapFrom(vw.stage, _stage_pos(vw, vw.right)))
    assert vw.zoom == 1.0


def test_spread_zoom_keeps_both_pages_side_by_side(win, tmp_path: Path):
    vw = _open_viewer(win, tmp_path, pages=4, spread=True)
    vw.cover_check.setChecked(False)
    assert wait_until(lambda: vw.left.image_rect() is not None and vw.right.image_rect() is not None)
    vw.set_zoom(2)
    assert vw.left.geometry().right() + 1 == vw.right.geometry().left()      # 綴じ目で接したまま
    assert vw.left.width() == pytest.approx(vw.right.width(), abs=2)


def test_high_resolution_is_requested_when_zoomed(win, tmp_path: Path):
    vw = _open_viewer(win, tmp_path, spread=False)
    seen = []
    orig = vw._request
    vw._request = lambda ref, cb, *a: (seen.append(a), orig(ref, cb, *a))[1]
    vw.set_zoom(3)
    assert wait_until(lambda: any(a and a[0] > 150 for a in seen))     # 描画中の 1 件が終わってから出る
    assert wait_until(lambda: len(vw._hires) == 1)
    vw.set_zoom(1)


def test_drawing_still_lands_on_the_right_spot_when_zoomed(win, tmp_path: Path):
    vw = _open_viewer(win, tmp_path, spread=False)
    vw.set_zoom(3)
    vw.set_tool("pen")
    _drag(vw.right, [(0.4, 0.1), (0.5, 0.15)])             # ページ上の位置は割合で決まる
    (st,) = vw.ink_marks()[vw.right.ref]
    assert st.points[0] == pytest.approx((0.4, 0.1), abs=0.02)
    assert st.width == pytest.approx(0.004)


def test_middle_button_pans_even_with_a_pen(win, tmp_path: Path):
    vw = _open_viewer(win, tmp_path, spread=False)
    vw.set_zoom(3)
    vw.set_tool("pen")
    y0 = vw.right.y()
    QTest.mousePress(vw.right, Qt.MouseButton.MiddleButton, Qt.KeyboardModifier.NoModifier, QPoint(100, 100))
    QTest.mouseMove(vw.right, QPoint(100, 60))
    QTest.mouseRelease(vw.right, Qt.MouseButton.MiddleButton, Qt.KeyboardModifier.NoModifier, QPoint(100, 60))
    assert vw.right.y() < y0 and vw.ink_count() == 0
