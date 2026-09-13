"""4 パネル（圧縮・画像書き出し・ページ操作・バッチ）の結線確認（offscreen）。

Ghostscript を使う経路は run_ghostscript / find_ghostscript を差し替えて検証する
（本物の Ghostscript に依存しない）。
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

import pdftool.core.compress as C
from conftest import make_image_pdf, wait_until
from pdftool.core.compress import MB
from pdftool.ui.main_window import MainWindow


def fake_gs(monkeypatch, size_bytes: int = 1 * MB):
    def fake_run(gs, src, dst, params, cancel=None):
        dst.write_bytes(b"x" * size_bytes)
        return size_bytes

    monkeypatch.setattr(C, "run_ghostscript", fake_run)
    monkeypatch.setattr(C, "find_ghostscript", lambda: Path("C:/fake/gs.exe"))


def compress_panel(win: MainWindow):
    return win.panels[0]


def export_panel(win: MainWindow):
    return win.panels[1]


def pageops_panel(win: MainWindow):
    return win.panels[2]


def batch_panel(win: MainWindow):
    return win.panels[3]


# ---------------------------------------------------------------------------
# CompressPanel
# ---------------------------------------------------------------------------
def test_compress_panel_disabled_without_gs(win: MainWindow, tmp_path: Path):
    panel = compress_panel(win)
    win.gs_path = None
    panel._update_gs_state()
    assert not panel.radio_target.isEnabled()
    assert "Ghostscript" in panel.warning.text()
    win.open_pdf(make_image_pdf(tmp_path / "x.pdf", pages=1))
    assert not panel.run_btn.isEnabled()  # gs が無いので開いても実行不可


def test_compress_panel_preset_run(monkeypatch, win: MainWindow, tmp_path: Path):
    fake_gs(monkeypatch)
    win.gs_path = Path("C:/fake/gs.exe")
    panel = compress_panel(win)
    panel._update_gs_state()
    pdf = make_image_pdf(tmp_path / "圧縮 対象.pdf", pages=2)
    win.open_pdf(pdf)
    panel.radio_preset.setChecked(True)
    panel.run_btn.click()
    assert wait_until(lambda: "完了" in panel.status_label.text() or "エラー" in panel.status_label.text())
    assert "完了" in panel.status_label.text()
    assert panel.open_folder_btn.isVisible()
    assert panel.run_btn.isEnabled()


def test_compress_panel_locks_inputs_while_running(monkeypatch, win: MainWindow, tmp_path: Path):
    def slow_run(gs, src, dst, params, cancel=None):
        time.sleep(0.3)
        dst.write_bytes(b"x" * MB)
        return MB

    monkeypatch.setattr(C, "run_ghostscript", slow_run)
    monkeypatch.setattr(C, "find_ghostscript", lambda: Path("C:/fake/gs.exe"))
    win.gs_path = Path("C:/fake/gs.exe")
    panel = compress_panel(win)
    panel._update_gs_state()
    win.open_pdf(make_image_pdf(tmp_path / "ロック確認.pdf", pages=1))
    panel.run_btn.click()
    assert not panel.radio_target.isEnabled()
    assert not panel.target_spin.isEnabled()
    assert not panel.preset_combo.isEnabled()
    assert wait_until(lambda: "完了" in panel.status_label.text(), timeout=5)
    assert panel.radio_target.isEnabled()
    assert panel.target_spin.isEnabled()  # 目標サイズモードのまま戻る
    assert not panel.preset_combo.isEnabled()


def test_compress_panel_target_run_and_cancel_ui_reset(monkeypatch, win: MainWindow, tmp_path: Path):
    fake_gs(monkeypatch)
    win.gs_path = Path("C:/fake/gs.exe")
    panel = compress_panel(win)
    panel._update_gs_state()
    win.open_pdf(make_image_pdf(tmp_path / "目標.pdf", pages=1))
    panel.radio_target.setChecked(True)
    panel.target_spin.setValue(2.0)
    panel.run_btn.click()
    assert not panel.run_btn.isEnabled()
    assert wait_until(lambda: panel.run_btn.isEnabled())
    assert "完了" in panel.status_label.text()


# ---------------------------------------------------------------------------
# ExportPanel
# ---------------------------------------------------------------------------
def test_export_panel_all_pages(win: MainWindow, tmp_path: Path):
    panel = export_panel(win)
    win.open_pdf(make_image_pdf(tmp_path / "書出 対象.pdf", pages=3))
    panel.range_combo.setCurrentIndex(panel.range_combo.findData("all"))
    panel.dpi_spin.setValue(72)
    panel.run_btn.click()
    assert wait_until(lambda: "完了" in panel.status_label.text())
    assert panel._last_output_dir is not None
    assert len(list(panel._last_output_dir.glob("*.png"))) == 3


def test_export_panel_selection_auto_syncs_with_thumbnail_view(win: MainWindow, tmp_path: Path):
    """左のサムネイルで複数選択するだけで、対象ページが自動的に切り替わること。"""
    panel = export_panel(win)
    win.open_pdf(make_image_pdf(tmp_path / "選択 書出.pdf", pages=3))
    assert panel.range_combo.currentData() == "all"
    assert "未選択" in panel.selection_label.text()

    win.view.select_rows([0, 2])
    QApplication.processEvents()
    assert panel.range_combo.currentData() == "selected"
    assert "2 ページ選択中" in panel.selection_label.text()
    panel.run_btn.click()
    assert wait_until(lambda: "完了" in panel.status_label.text())
    assert len(list(panel._last_output_dir.glob("*.png"))) == 2

    # 選択を解除すると自動で「全ページ」に戻る
    win.view.select_rows([])
    QApplication.processEvents()
    assert panel.range_combo.currentData() == "all"
    assert "未選択" in panel.selection_label.text()


def test_export_panel_spec_mode_not_overridden_by_selection(win: MainWindow, tmp_path: Path):
    """「範囲指定」を選んでいる間はサムネイル選択で上書きされないこと。"""
    panel = export_panel(win)
    win.open_pdf(make_image_pdf(tmp_path / "範囲固定.pdf", pages=3))
    panel.range_combo.setCurrentIndex(panel.range_combo.findData("spec"))
    win.view.select_rows([0])
    QApplication.processEvents()
    assert panel.range_combo.currentData() == "spec"


def test_export_panel_spec_mode(win: MainWindow, tmp_path: Path):
    panel = export_panel(win)
    win.open_pdf(make_image_pdf(tmp_path / "範囲 書出.pdf", pages=5))
    panel.range_combo.setCurrentIndex(panel.range_combo.findData("spec"))
    panel.spec_edit.setText("1,3-4")
    panel.run_btn.click()
    assert wait_until(lambda: "完了" in panel.status_label.text())
    assert len(list(panel._last_output_dir.glob("*.png"))) == 3


# ---------------------------------------------------------------------------
# PageOpsPanel
# ---------------------------------------------------------------------------
def test_pageops_panel_select_by_spec(win: MainWindow, tmp_path: Path):
    panel = pageops_panel(win)
    win.open_pdf(make_image_pdf(tmp_path / "選択.pdf", pages=5))
    panel.spec_edit.setText("1,3-4")
    panel.select_btn.click()
    assert win.view.selected_rows() == [0, 2, 3]
    assert "3 ページ" in panel.status_label.text()


def test_pageops_panel_split_selected(win: MainWindow, tmp_path: Path):
    panel = pageops_panel(win)
    win.open_pdf(make_image_pdf(tmp_path / "分割 対象.pdf", pages=4))
    win.view.select_rows([1, 2])
    QApplication.processEvents()
    assert panel.split_selected_btn.isEnabled()
    panel.split_selected_btn.click()
    assert wait_until(lambda: "書き出しました" in panel.status_label.text())
    assert len(win.page_list) == 4  # 分割は編集状態を変えない


def test_pageops_panel_split_all(win: MainWindow, tmp_path: Path):
    panel = pageops_panel(win)
    win.open_pdf(make_image_pdf(tmp_path / "全分割.pdf", pages=3))
    panel.split_all_btn.click()
    assert wait_until(lambda: "3 ファイル" in panel.status_label.text())


def test_pageops_panel_insert_pdf(win: MainWindow, tmp_path: Path):
    panel = pageops_panel(win)
    win.open_pdf(make_image_pdf(tmp_path / "base.pdf", pages=2))
    other = make_image_pdf(tmp_path / "挿入 元.pdf", pages=2)
    win.view.select_rows([1])
    n = win.model.insert_pdf(other, 1)  # ダイアログ経由の代わりに直接呼ぶ
    assert n == 2
    assert len(win.page_list) == 4
    assert win.page_list[1].source_path == other


# ---------------------------------------------------------------------------
# BatchPanel
# ---------------------------------------------------------------------------
def test_batch_panel_compress(monkeypatch, win: MainWindow, tmp_path: Path):
    fake_gs(monkeypatch)
    win.gs_path = Path("C:/fake/gs.exe")
    d = tmp_path / "対象"
    d.mkdir()
    make_image_pdf(d / "a.pdf", pages=1)
    make_image_pdf(d / "b.pdf", pages=1)

    panel = batch_panel(win)
    win.tabs.setCurrentWidget(panel)  # 非表示タブは isVisible() が常に False になるため
    panel.folder_edit.setText(str(d))
    panel.radio_compress.setChecked(True)
    panel.c_radio_preset.setChecked(True)
    panel.run_btn.click()
    assert wait_until(lambda: "完了" in panel.status_label.text())
    assert panel.table.rowCount() == 2
    assert panel.open_folder_btn.isVisible()


def test_batch_panel_export(win: MainWindow, tmp_path: Path):
    d = tmp_path / "画像対象"
    d.mkdir()
    make_image_pdf(d / "a.pdf", pages=2)

    panel = batch_panel(win)
    panel.folder_edit.setText(str(d))
    panel.radio_export.setChecked(True)
    panel.e_dpi_spin.setValue(72)
    panel.run_btn.click()
    assert wait_until(lambda: "完了" in panel.status_label.text())
    assert panel.table.rowCount() == 1
    assert panel.table.item(0, 3).text() == "OK"


def test_batch_panel_missing_folder_shows_error(win: MainWindow, tmp_path: Path, monkeypatch):
    panel = batch_panel(win)
    errors = []
    monkeypatch.setattr(win, "show_error", lambda msg, title="エラー": errors.append(msg))
    panel.folder_edit.setText(str(tmp_path / "ない"))
    panel.run_btn.click()
    assert errors and "フォルダが見つかりません" in errors[0]
