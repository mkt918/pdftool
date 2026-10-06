"""UI の起動確認（offscreen）。サムネイルが遅延描画され、ツールバー操作がモデルに反映されることを見る。"""

from __future__ import annotations

import os
import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from conftest import make_image_pdf, wait_until
from pdftool.ui.main_window import MainWindow
from pdftool.utils.worker import ThumbnailWorker, ThumbRequest, Worker


def rendered_rows(win: MainWindow) -> list[int]:
    m = win.model
    out = []
    for r in range(m.rowCount()):
        pm = m.data(m.index(r), Qt.ItemDataRole.DecorationRole)
        if pm.cacheKey() != m._placeholder.cacheKey():
            out.append(r)
    return out


def test_open_and_thumbnails(win: MainWindow, tmp_path: Path):
    pdf = make_image_pdf(tmp_path / "起動 確認.pdf", pages=4)
    assert win.open_pdf(pdf)
    assert win.model.rowCount() == 4
    assert wait_until(lambda: len(rendered_rows(win)) == 4)
    pm = win.model.data(win.model.index(0), Qt.ItemDataRole.DecorationRole)
    assert pm.width() == 180  # 既定「中」
    assert "起動 確認.pdf" in win.windowTitle()
    assert "4 ページ" in win.lbl_doc.text()


def test_toolbar_ops_and_undo(win: MainWindow, tmp_path: Path):
    pdf = make_image_pdf(tmp_path / "ops.pdf", pages=4)
    win.open_pdf(pdf)
    win.view.select_rows([1, 2])
    QApplication.processEvents()
    win.act_rot_r.trigger()
    assert [r.rotation for r in win.page_list.pages] == [0, 90, 90, 0]
    assert win.act_undo.isEnabled()
    # 回転サムネは横長になる
    assert wait_until(lambda: win.model.data(win.model.index(1), Qt.ItemDataRole.DecorationRole).height() < 180)
    win.view.select_rows([0])
    win.act_delete.trigger()
    assert len(win.page_list) == 3
    win.act_undo.trigger()
    assert len(win.page_list) == 4
    win.act_redo.trigger()
    assert len(win.page_list) == 3
    assert "変更あり" in win.lbl_doc.text()


def test_thumbnail_size_switch(win: MainWindow, tmp_path: Path):
    pdf = make_image_pdf(tmp_path / "size.pdf", pages=2)
    win.open_pdf(pdf)
    win.size_combo.setCurrentText("小")
    assert wait_until(lambda: all(
        win.model.data(win.model.index(r), Qt.ItemDataRole.DecorationRole).width() == 120 for r in range(2)
    ))


def test_thumbnail_size_persists_via_settings(win: MainWindow, tmp_path: Path):
    """サムネイルサイズは QSettings に保存され、次に開いたウィンドウにも引き継がれる。"""
    win.size_combo.setCurrentText("大")
    assert win.settings.value("thumb_size") == "大"

    win2 = MainWindow()
    try:
        assert win2.size_combo.currentText() == "大"
        assert win2.model.thumbnail_width() == 240
    finally:
        win2.thumb_worker.stop()
        win2.close()


def test_select_all_shortcut(win: MainWindow, tmp_path: Path):
    pdf = make_image_pdf(tmp_path / "select_all.pdf", pages=4)
    win.open_pdf(pdf)
    win.act_select_all.trigger()
    assert win.view.selected_rows() == [0, 1, 2, 3]


def test_ghostscript_recheck_button(win: MainWindow, monkeypatch):
    import pdftool.ui.main_window as MWmod

    monkeypatch.setattr(MWmod, "find_ghostscript", lambda: None)
    win.gs_path = None
    win._update_gs_label()
    assert win.gs_recheck_btn.isVisible()

    monkeypatch.setattr(MWmod, "find_ghostscript", lambda: Path("C:/fake/gs.exe"))
    monkeypatch.setattr(MWmod, "ghostscript_version", lambda p: "10.00.0")
    win.gs_recheck_btn.click()
    assert win.gs_path == Path("C:/fake/gs.exe")
    assert not win.gs_recheck_btn.isVisible()
    assert "Ghostscript 10.00.0" in win.lbl_gs.text()


def test_close_with_running_worker_prompts_and_cancels(win: MainWindow, monkeypatch):
    class FakeCancelled(Exception):
        pass

    started = []
    cancelled = []

    def slow_job(cancel=None):
        started.append(True)
        for _ in range(200):
            if cancel and cancel():
                cancelled.append(True)
                raise FakeCancelled()
            time.sleep(0.02)

    win.run_worker(slow_job)
    assert wait_until(lambda: started, timeout=3)

    from PySide6.QtGui import QCloseEvent
    from PySide6.QtWidgets import QMessageBox

    asked = []

    def fake_question(*a, **k):
        asked.append(True)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", fake_question)

    ev = QCloseEvent()
    win.closeEvent(ev)
    assert asked  # 確認ダイアログが出た
    assert ev.isAccepted()
    assert wait_until(lambda: cancelled, timeout=3)


def test_append_and_move(win: MainWindow, tmp_path: Path):
    a = make_image_pdf(tmp_path / "a.pdf", pages=2)
    b = make_image_pdf(tmp_path / "b.pdf", pages=2)
    win.open_pdf(a)
    assert win.append_pdf(b)
    assert len(win.page_list) == 4 and win.page_list.sources == [a, b]
    win.model.move_pages([2, 3], 0)
    assert [r.source_path.name for r in win.page_list.pages] == ["b.pdf", "b.pdf", "a.pdf", "a.pdf"]


def test_worker_injects_progress_and_cancel(app):
    seen = []
    results = []

    def job(x, progress=None, cancel=None):
        for i in range(3):
            progress(i)
        return x * 2

    w = Worker(job, 21)
    w.signals.progress.connect(seen.append)
    w.signals.finished.connect(results.append)
    w.start()
    assert wait_until(lambda: results == [42])
    assert seen == [0, 1, 2]

    errors = []

    def bad():
        raise RuntimeError("boom")

    w2 = Worker(bad)
    w2.signals.error.connect(lambda msg, e: errors.append(msg))
    w2.start()
    assert wait_until(lambda: errors == ["RuntimeError: boom"])


def test_worker_injects_via_var_keyword(app):
    """compress_preset のように **kw で下位へ丸ごと転送するだけの関数でも注入されること。"""
    seen_progress = []
    seen_cancel = []

    def wrapper(x, **kw):
        def inner(x, progress=None, cancel=None):
            progress("p")
            seen_cancel.append(cancel())
            return x + 1
        return inner(x, **kw)

    results = []
    w = Worker(wrapper, 10)
    w.signals.progress.connect(seen_progress.append)
    w.signals.finished.connect(results.append)
    w.start()
    assert wait_until(lambda: results == [11])
    assert seen_progress == ["p"]
    assert seen_cancel == [False]


def test_worker_cancelled_signal_carries_partial(app):
    class FakeCancelled(Exception):
        def __init__(self, partial):
            super().__init__("cancelled")
            self.partial = partial

    def job(cancel=None):
        if cancel():
            raise FakeCancelled(partial={"done": 1})

    payloads = []
    w = Worker(job)
    w.cancel()  # 実行前にキャンセル要求しておく
    w.signals.cancelled.connect(lambda p: payloads.append(p))
    w.start()
    assert wait_until(lambda: len(payloads) == 1)
    assert payloads[0] == {"done": 1}


def test_thumbnail_worker_evicts_document_after_unexpected_error(app, tmp_path: Path, monkeypatch):
    """render_thumbnail が想定外の例外を出したら、キャッシュ済みドキュメントを捨てて次回に備える。"""
    import pdftool.core.document as docmod

    pdf = make_image_pdf(tmp_path / "evict.pdf", pages=1)
    worker = ThumbnailWorker()
    worker.start()
    try:
        calls = {"n": 0}
        original = docmod.PdfDocument.render_thumbnail

        def flaky(self, *a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            return original(self, *a, **k)

        monkeypatch.setattr(docmod.PdfDocument, "render_thumbnail", flaky)

        failed = []
        worker.failed.connect(lambda key, msg: failed.append((key, msg)))
        worker.request(ThumbRequest("k1", pdf, 0, 0, 120))
        assert wait_until(lambda: failed, timeout=5)
        assert "RuntimeError" in failed[0][1]
        assert pdf not in worker._docs  # 壊れた可能性のあるハンドルは捨てられている

        ready = []
        worker.ready.connect(lambda key, img: ready.append(key))
        worker.request(ThumbRequest("k2", pdf, 0, 0, 120))
        assert wait_until(lambda: ready, timeout=5)  # 作り直して成功する
    finally:
        worker.stop()


def test_screenshot(win: MainWindow, tmp_path: Path):
    """目視確認用に scratchpad へ PNG を出す（存在すれば）。"""
    pdf = make_image_pdf(tmp_path / "shot.pdf", pages=6)
    win.open_pdf(pdf)
    assert wait_until(lambda: len(rendered_rows(win)) == 6)
    out = os.environ.get("PDFTOOL_SCREENSHOT")
    if out:
        win.grab().save(out)
        assert Path(out).exists()


# ---------------------------------------------------------------------------
# ドラッグ&ドロップ（並べ替え / PDF ファイルのドロップ結合）
# ---------------------------------------------------------------------------
def _url_drop(view, paths, point, modifiers=Qt.KeyboardModifier.NoModifier):
    from PySide6.QtCore import QMimeData, QPointF, QUrl
    from PySide6.QtGui import QDropEvent

    mime = QMimeData()
    view._test_mime = mime   # 所有されないので生存を保証
    mime.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
    ev = QDropEvent(QPointF(point), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, modifiers)
    view.dropEvent(ev)
    return ev


def _center(view, row):
    return view.visualRect(view.model().index(row)).center()


def test_drop_target_picks_insert_position(win: MainWindow, tmp_path: Path):
    win.open_pdf(make_image_pdf(tmp_path / "a.pdf", pages=4))
    win.show()
    QApplication.processEvents()
    v = win.view
    r1 = v.visualRect(v.model().index(1))
    from PySide6.QtCore import QPoint

    # 1 番目の左半分 → 1 の直前、右半分 → 2 の直前
    assert v._drop_target(r1.center() - QPoint(20, 0))[0] == 1
    assert v._drop_target(r1.center() + QPoint(20, 0))[0] == 2
    # 最後のアイテムの右・最下行より下 → 末尾
    last = v.visualRect(v.model().index(3))
    assert v._drop_target(QPoint(last.right() + 1, last.center().y()))[0] == 4
    assert v._drop_target(QPoint(5, last.bottom() + 200))[0] == 4
    # 先頭より上 → 0
    assert v._drop_target(QPoint(5, -50))[0] == 0


def test_dropping_pdf_files_inserts_at_drop_position(win: MainWindow, tmp_path: Path):
    a = make_image_pdf(tmp_path / "a.pdf", pages=3)
    b = make_image_pdf(tmp_path / "b.pdf", pages=2)
    win.open_pdf(a)
    win.show()
    QApplication.processEvents()
    from PySide6.QtCore import QPoint

    # a の 1 ページ目(0) の右半分に落とす → 0 の直後 = 位置 1 に b の 2 ページが入る
    p = _center(win.view, 0) + QPoint(20, 0)
    _url_drop(win.view, [b], p)
    names = [r.source_path.name for r in win.page_list.pages]
    assert names == ["a.pdf", "b.pdf", "b.pdf", "a.pdf", "a.pdf"]
    assert win.view.selected_rows() == [1, 2]   # 結合したページが選択される


def test_dropping_multiple_pdfs_keeps_order(win: MainWindow, tmp_path: Path):
    a = make_image_pdf(tmp_path / "a.pdf", pages=1)
    b = make_image_pdf(tmp_path / "b.pdf", pages=1)
    c = make_image_pdf(tmp_path / "c.pdf", pages=1)
    win.open_pdf(a)
    win.show()
    QApplication.processEvents()
    from PySide6.QtCore import QPoint

    last = win.view.visualRect(win.view.model().index(0))
    _url_drop(win.view, [b, c], QPoint(last.right() + 1, last.center().y()))
    assert [r.source_path.name for r in win.page_list.pages] == ["a.pdf", "b.pdf", "c.pdf"]


def test_dropping_pdf_without_document_opens_it(win: MainWindow, tmp_path: Path):
    a = make_image_pdf(tmp_path / "a.pdf", pages=2)
    b = make_image_pdf(tmp_path / "b.pdf", pages=1)
    from PySide6.QtCore import QPoint

    _url_drop(win.view, [a, b], QPoint(10, 10))
    assert [r.source_path.name for r in win.page_list.pages] == ["a.pdf", "a.pdf", "b.pdf"]


def test_shift_drop_replaces_document(win: MainWindow, tmp_path: Path):
    a = make_image_pdf(tmp_path / "a.pdf", pages=2)
    b = make_image_pdf(tmp_path / "b.pdf", pages=1)
    win.open_pdf(a)
    win.show()
    QApplication.processEvents()
    _url_drop(win.view, [b], _center(win.view, 0), Qt.KeyboardModifier.ShiftModifier)
    assert [r.source_path.name for r in win.page_list.pages] == ["b.pdf"]


def test_non_pdf_drop_is_ignored(win: MainWindow, tmp_path: Path):
    a = make_image_pdf(tmp_path / "a.pdf", pages=2)
    txt = tmp_path / "x.txt"
    txt.write_text("hi")
    win.open_pdf(a)
    win.show()
    QApplication.processEvents()
    ev = _url_drop(win.view, [txt], _center(win.view, 0))
    assert not ev.isAccepted()
    assert len(win.page_list) == 2


def test_wheel_scroll_moves_a_fraction_of_a_row(win: MainWindow, tmp_path: Path):
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtGui import QWheelEvent

    win.open_pdf(make_image_pdf(tmp_path / "w.pdf", pages=40))
    win.resize(900, 500)
    win.show()
    QApplication.processEvents()
    bar = win.view.verticalScrollBar()
    assert bar.maximum() > 0
    row_h = win.view.gridSize().height()
    bar.setValue(0)
    ev = QWheelEvent(QPointF(100, 100), QPointF(100, 100), QPoint(0, 0), QPoint(0, -120),
                     Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                     Qt.ScrollPhase.NoScrollPhase, False)
    win.view.wheelEvent(ev)
    moved = bar.value()
    assert 0 < moved <= row_h * 0.5          # 1ノッチで1行ぶん以上は飛ばない


def test_dropping_images_converts_and_inserts(win: MainWindow, tmp_path: Path):
    from PIL import Image
    from PySide6.QtCore import QPoint

    a = make_image_pdf(tmp_path / "a.pdf", pages=2)
    img = tmp_path / "写真.jpg"
    Image.new("RGB", (1200, 900), (10, 120, 200)).save(img)
    win.open_pdf(a)
    win.show()
    QApplication.processEvents()
    last = win.view.visualRect(win.view.model().index(1))
    _url_drop(win.view, [img], QPoint(last.right() + 1, last.center().y()))
    assert wait_until(lambda: len(win.page_list) == 3)
    assert win.page_list[2].source_path.name == "写真.pdf"          # 一時PDFに変換されたページ
    assert win.view.selected_rows() == [2]
    # 保存先・ファイル名は元の画像を基準にする（一時フォルダに出さない）
    from pdftool.utils import naming
    assert naming.output_file(win.page_list[2].source_path, "x", ".pdf").parent == tmp_path


def test_dropping_only_images_without_document_opens_them(win: MainWindow, tmp_path: Path):
    from PIL import Image
    from PySide6.QtCore import QPoint

    imgs = []
    for i, c in enumerate(((255, 0, 0), (0, 255, 0))):
        p = tmp_path / f"p{i}.png"
        Image.new("RGB", (400, 600), c).save(p)
        imgs.append(p)
    _url_drop(win.view, imgs, QPoint(10, 10))
    assert wait_until(lambda: win.page_list is not None and len(win.page_list) == 2)
    assert [r.source_path.name for r in win.page_list.pages] == ["p0.pdf", "p1.pdf"]


# ---------------------------------------------------------------------------
# ページの並べ替え（マウス操作を自前処理。Qt 標準のドラッグは使わない）
# ---------------------------------------------------------------------------
def _mouse(view, kind, pos, button=Qt.MouseButton.LeftButton, buttons=Qt.MouseButton.LeftButton,
           mods=Qt.KeyboardModifier.NoModifier):
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent

    t = {"press": QEvent.Type.MouseButtonPress, "move": QEvent.Type.MouseMove,
         "release": QEvent.Type.MouseButtonRelease}[kind]
    ev = QMouseEvent(t, QPointF(pos), QPointF(view.viewport().mapToGlobal(pos)), button, buttons, mods)
    QApplication.sendEvent(view.viewport(), ev)


def _drag(view, src_row, dst_pos, steps=6):
    """src_row を押して dst_pos まで動かし、離す（途中の移動も送る）。"""
    from PySide6.QtCore import QPoint

    start = view.visualRect(view.model().index(src_row)).center()
    _mouse(view, "press", start)
    for k in range(1, steps + 1):
        mid = QPoint(start.x() + (dst_pos.x() - start.x()) * k // steps, start.y() + (dst_pos.y() - start.y()) * k // steps)
        _mouse(view, "move", mid)
    _mouse(view, "release", dst_pos, buttons=Qt.MouseButton.NoButton)


def _end_of_list(view):
    from PySide6.QtCore import QPoint

    last = view.visualRect(view.model().index(view.model().rowCount() - 1))
    return QPoint(last.right() + 1, last.center().y())


def test_mouse_drag_reorders_pages(win: MainWindow, tmp_path: Path):
    win.open_pdf(make_image_pdf(tmp_path / "a.pdf", pages=4))
    win.show()
    QApplication.processEvents()
    _drag(win.view, 0, _end_of_list(win.view))
    assert [r.source_index for r in win.page_list.pages] == [1, 2, 3, 0]
    assert win.view.selected_rows() == [3]
    assert not win.view.reordering
    from PySide6.QtWidgets import QAbstractItemView
    assert win.view.state() == QAbstractItemView.State.NoState      # 範囲選択中の状態を引きずらない
    win.act_undo.trigger()
    assert [r.source_index for r in win.page_list.pages] == [0, 1, 2, 3]


def test_mouse_drag_moves_all_selected_pages(win: MainWindow, tmp_path: Path):
    win.open_pdf(make_image_pdf(tmp_path / "a.pdf", pages=5))
    win.show()
    QApplication.processEvents()
    win.view.select_rows([0, 1])
    QApplication.processEvents()
    _drag(win.view, 0, _end_of_list(win.view))                      # 選択済みの 0 を掴む → 0,1 がまとめて動く
    assert [r.source_index for r in win.page_list.pages] == [2, 3, 4, 0, 1]
    assert win.view.selected_rows() == [3, 4]


def test_mouse_drag_shows_hint_and_escape_cancels(win: MainWindow, tmp_path: Path):
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtCore import QEvent

    win.open_pdf(make_image_pdf(tmp_path / "a.pdf", pages=4))
    win.show()
    QApplication.processEvents()
    v = win.view
    start = v.visualRect(v.model().index(0)).center()
    _mouse(v, "press", start)
    _mouse(v, "move", start + QPoint(40, 0))
    _mouse(v, "move", _end_of_list(v))
    assert v.reordering and v._drop_line is not None                # 挿入位置の線が出ている
    QApplication.sendEvent(v, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
    assert not v.reordering and v._drop_line is None
    _mouse(v, "release", _end_of_list(v), buttons=Qt.MouseButton.NoButton)
    assert [r.source_index for r in win.page_list.pages] == [0, 1, 2, 3]   # 取り消したので動かない


def test_small_jitter_is_a_click_not_a_reorder(win: MainWindow, tmp_path: Path):
    from PySide6.QtCore import QPoint

    win.open_pdf(make_image_pdf(tmp_path / "a.pdf", pages=4))
    win.show()
    QApplication.processEvents()
    v = win.view
    p = v.visualRect(v.model().index(2)).center()
    _mouse(v, "press", p)
    _mouse(v, "move", p + QPoint(2, 1))
    _mouse(v, "release", p + QPoint(2, 1), buttons=Qt.MouseButton.NoButton)
    assert [r.source_index for r in win.page_list.pages] == [0, 1, 2, 3]
    assert v.selected_rows() == [2]


def test_dragging_from_empty_space_does_not_reorder(win: MainWindow, tmp_path: Path):
    from PySide6.QtCore import QPoint

    win.open_pdf(make_image_pdf(tmp_path / "a.pdf", pages=2))
    win.show()
    QApplication.processEvents()
    v = win.view
    blank = QPoint(v.viewport().width() - 10, v.viewport().height() - 10)
    _mouse(v, "press", blank)
    _mouse(v, "move", blank - QPoint(200, 150))
    assert not v.reordering                                         # 余白からは並べ替えにならない（範囲選択）
    _mouse(v, "release", blank - QPoint(200, 150), buttons=Qt.MouseButton.NoButton)
    assert [r.source_index for r in win.page_list.pages] == [0, 1]


def test_qt_native_drag_is_disabled_so_no_forbidden_cursor(win: MainWindow):
    """Qt 標準のドラッグを使うと実機で「×」になったので、標準のドラッグは無効にしてある。"""
    from PySide6.QtWidgets import QAbstractItemView

    assert not win.view.dragEnabled()
    assert win.view.dragDropMode() == QAbstractItemView.DragDropMode.DropOnly


# ---------------------------------------------------------------------------
# プレビュー（ダブルクリック）の ＜ ＞ ページ移動
# ---------------------------------------------------------------------------
def _open_preview(win: MainWindow, row: int):
    from pdftool.ui.preview_dialog import PagePreviewDialog

    win.view.page_activated.emit(row)
    assert wait_until(lambda: win.findChildren(PagePreviewDialog))
    return win.findChildren(PagePreviewDialog)[-1]


def test_preview_has_prev_next_buttons_and_navigates(win: MainWindow, tmp_path: Path):
    win.open_pdf(make_image_pdf(tmp_path / "pv.pdf", pages=4))
    dlg = _open_preview(win, 1)
    assert dlg.nav_bar.isVisibleTo(dlg) and dlg.row == 1
    assert dlg.page_label.text() == "2 / 4" and "2 / 4 ページ目" in dlg.windowTitle()
    assert dlg.prev_btn.isEnabled() and dlg.next_btn.isEnabled()
    dlg.next_btn.click()
    assert wait_until(lambda: dlg.page_label.text() == "3 / 4")      # 読み込み後に表示が更新される
    assert "3 / 4 ページ目" in dlg.windowTitle()
    dlg.next_btn.click()
    assert wait_until(lambda: dlg.page_label.text() == "4 / 4")
    assert not dlg.next_btn.isEnabled()                              # 最後のページでは「＞」は押せない
    for _ in range(3):
        dlg.prev_btn.click()
    assert wait_until(lambda: dlg.page_label.text() == "1 / 4")
    assert not dlg.prev_btn.isEnabled()


def test_preview_keyboard_navigation_and_cache(win: MainWindow, tmp_path: Path):
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QKeyEvent

    win.open_pdf(make_image_pdf(tmp_path / "pv.pdf", pages=5))
    dlg = _open_preview(win, 0)

    def key(k):
        QApplication.sendEvent(dlg, QKeyEvent(QEvent.Type.KeyPress, k, Qt.KeyboardModifier.NoModifier))

    key(Qt.Key.Key_Right)
    assert wait_until(lambda: dlg.row == 1 and "2 / 5" == dlg.page_label.text())
    key(Qt.Key.Key_End)
    assert wait_until(lambda: dlg.page_label.text() == "5 / 5")
    key(Qt.Key.Key_Home)
    assert dlg.row == 0 and dlg.page_label.text() == "1 / 5"          # 一度見たページは待たずに表示（キャッシュ）
    key(Qt.Key.Key_Left)
    assert dlg.row == 0                                              # 先頭より前には行かない


def test_preview_reflects_crop_and_rotation_and_ignores_late_results(win: MainWindow, tmp_path: Path):
    win.open_pdf(make_image_pdf(tmp_path / "pv.pdf", pages=3))
    win.model.set_crops({0: (0.0, 0.0, 0.5, 0.0)})                    # 右半分を切る
    win.model.rotate_pages([0], 90)
    dlg = _open_preview(win, 0)
    pm = dlg._label.pixmap()
    assert pm.width() > pm.height()                                  # 144x384 を回転 → 横長
    dlg.next_btn.click()
    dlg.close()                                                      # 読み込み中に閉じても落ちない
    QApplication.processEvents()
    assert wait_until(lambda: not win._workers)


def test_single_page_preview_without_nav(win: MainWindow, tmp_path: Path):
    from pdftool.ui.preview_dialog import PagePreviewDialog
    from PySide6.QtGui import QImage

    dlg = PagePreviewDialog(QImage(50, 50, QImage.Format.Format_RGB32), "t", win)
    assert not dlg.nav_bar.isVisibleTo(dlg)                          # 仕上げのプレビューなど 1 枚だけのときはボタン無し
    dlg.close()


def test_viewport_accepts_drops_so_external_files_reach_the_view(win: MainWindow):
    """実機で判明: viewport が acceptDrops=False だと、外部ファイルのドラッグが一覧に届かず親ウィンドウへ素通りする。"""
    assert win.view.acceptDrops() and win.view.viewport().acceptDrops()


def test_window_level_drop_always_returns_copy_never_move(win: MainWindow, tmp_path: Path):
    """Move を返すと、エクスプローラーが元のファイルを削除してしまう。"""
    from PySide6.QtCore import QMimeData, QPointF, QUrl
    from PySide6.QtGui import QDragEnterEvent, QDropEvent
    from PySide6.QtCore import QPoint

    pdf = make_image_pdf(tmp_path / "x.pdf", pages=1)
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(pdf))])
    acts = Qt.DropAction.MoveAction | Qt.DropAction.CopyAction
    enter = QDragEnterEvent(QPoint(5, 5), acts, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    win.dragEnterEvent(enter)
    assert enter.isAccepted() and enter.dropAction() == Qt.DropAction.CopyAction
    drop = QDropEvent(QPointF(5, 5), acts, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    win.dropEvent(drop)
    assert drop.dropAction() == Qt.DropAction.CopyAction
    assert wait_until(lambda: win.page_list is not None and len(win.page_list) == 1)
    assert pdf.exists()


def test_context_menu_selects_clicked_page_and_actions_work(win: MainWindow, tmp_path: Path):
    win.open_pdf(make_image_pdf(tmp_path / "a.pdf", pages=4))
    win.show()
    QApplication.processEvents()
    v = win.view
    got: list = []
    v.context_requested.disconnect(win._show_context_menu)   # 本物のメニューは modal で止まるので外す
    v.context_requested.connect(got.append)
    from PySide6.QtGui import QContextMenuEvent

    pos = v.visualRect(v.model().index(2)).center()
    QApplication.sendEvent(
        v.viewport(), QContextMenuEvent(QContextMenuEvent.Reason.Mouse, pos, v.viewport().mapToGlobal(pos))
    )
    assert got and v.selected_rows() == [2]               # 未選択ページの右クリック → そのページを選択
    texts = [a.text() for a in win._build_context_menu().actions()]
    assert "プレビュー" in texts and any("削除" in t for t in texts)
    win._move_selected_to(0)
    assert [r.source_index for r in win.page_list.pages] == [2, 0, 1, 3]
    assert v.selected_rows() == [0]
    win._delete()
    assert len(win.page_list) == 3
