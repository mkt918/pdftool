"""UI の起動確認（offscreen）。サムネイルが遅延描画され、ツールバー操作がモデルに反映されることを見る。"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from conftest import make_image_pdf  # noqa: E402
from pdftool.ui.main_window import MainWindow  # noqa: E402
from pdftool.utils.worker import Worker  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def wait_until(cond, timeout=10.0, app=None):
    t0 = time.time()
    while time.time() - t0 < timeout:
        QApplication.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


def rendered_rows(win: MainWindow) -> list[int]:
    m = win.model
    out = []
    for r in range(m.rowCount()):
        pm = m.data(m.index(r), Qt.ItemDataRole.DecorationRole)
        if pm.cacheKey() != m._placeholder.cacheKey():
            out.append(r)
    return out


@pytest.fixture
def win(app):
    w = MainWindow()
    w._confirm_discard = lambda: True   # モーダルは offscreen で固まるので潰す
    w.resize(1000, 700)
    w.show()
    yield w
    w.thumb_worker.stop()
    w.close()


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


def test_screenshot(win: MainWindow, tmp_path: Path):
    """目視確認用に scratchpad へ PNG を出す（存在すれば）。"""
    pdf = make_image_pdf(tmp_path / "shot.pdf", pages=6)
    win.open_pdf(pdf)
    assert wait_until(lambda: len(rendered_rows(win)) == 6)
    out = os.environ.get("PDFTOOL_SCREENSHOT")
    if out:
        win.grab().save(out)
        assert Path(out).exists()
