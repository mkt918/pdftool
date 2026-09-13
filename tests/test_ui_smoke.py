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
