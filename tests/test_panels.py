"""4 パネル（圧縮・画像書き出し・ページ操作・バッチ）の結線確認（offscreen）。

Ghostscript を使う経路は run_ghostscript / find_ghostscript を差し替えて検証する
（本物の Ghostscript に依存しない）。
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from PIL import Image
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


def export_panel(win: MainWindow):
    return win.panels[0]


def compress_panel(win: MainWindow):
    return win.panels[1]


def pageops_panel(win: MainWindow):
    return win.panels[2]


def finish_panel(win: MainWindow):
    return win.panels[3]


def batch_panel(win: MainWindow):
    return win.panels[4]


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
    win.tabs.setCurrentWidget(panel)  # 非表示タブは isVisible() が常に False になるため
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


def test_batch_panel_missing_folder_disables_run(win: MainWindow, tmp_path: Path):
    """フォルダが無効なら、クリックしてからエラーになるのではなく事前に実行不可にする。"""
    panel = batch_panel(win)
    panel.folder_edit.setText(str(tmp_path / "ない"))
    assert not panel.run_btn.isEnabled()
    assert "見つかりません" in panel.folder_status_label.text()


def test_batch_panel_summary_updates_with_valid_folder(win: MainWindow, tmp_path: Path):
    d = tmp_path / "対象"
    d.mkdir()
    make_image_pdf(d / "a.pdf", pages=1)
    make_image_pdf(d / "b.pdf", pages=1)
    panel = batch_panel(win)
    panel.folder_edit.setText(str(d))
    assert panel.run_btn.isEnabled()
    assert "2 件" in panel.folder_status_label.text()
    assert "圧縮" in panel.folder_status_label.text()

    panel.radio_export.setChecked(True)
    assert "画像書き出し" in panel.folder_status_label.text()


def test_batch_panel_run_disabled_without_gs(win: MainWindow, tmp_path: Path):
    d = tmp_path / "対象"
    d.mkdir()
    make_image_pdf(d / "a.pdf", pages=1)
    panel = batch_panel(win)
    win.gs_path = None
    panel.folder_edit.setText(str(d))
    panel.radio_compress.setChecked(True)
    assert not panel.run_btn.isEnabled()
    assert "Ghostscript" in panel.folder_status_label.text()


# ---------------------------------------------------------------------------
# PageOpsPanel: 白紙ページ検出・余白トリミング
# ---------------------------------------------------------------------------
def _scan_pdf(path: Path):
    """白紙 / 枠だけの内容ページ / 白紙 / 内容ページ の 4 ページ。"""
    from PIL import ImageDraw

    def page(box=None):
        im = Image.new("RGB", (800, 1000), "white")
        if box:
            ImageDraw.Draw(im).rectangle(box, fill="black")
        return im

    imgs = [page(), page((200, 300, 599, 699)), page(), page((100, 200, 499, 599))]
    imgs[0].save(path, save_all=True, append_images=imgs[1:], resolution=100)
    return path


def test_blank_group_selects_blank_pages(win: MainWindow, tmp_path: Path):
    panel = pageops_panel(win)
    win.tabs.setCurrentWidget(panel)
    win.open_pdf(_scan_pdf(tmp_path / "scan.pdf"))
    g = panel.blank_group
    assert g.find_btn.isEnabled() and "全 4 ページ" in g.target_label.text()
    g.find_btn.click()
    assert wait_until(lambda: "白紙の可能性" in g.status_label.text() or "見つかりませんでした" in g.status_label.text())
    assert win.view.selected_rows() == [0, 2]
    assert "2 ページ" in g.status_label.text()
    win.act_delete.trigger()                                    # 確認して削除 → 内容ページだけ残る
    assert len(win.page_list) == 2


def test_blank_group_respects_selection_scope(win: MainWindow, tmp_path: Path):
    panel = pageops_panel(win)
    win.open_pdf(_scan_pdf(tmp_path / "scan.pdf"))
    win.view.select_rows([2, 3])                                # 選択範囲だけを調べる
    QApplication.processEvents()
    g = panel.blank_group
    assert "選択中の 2 ページ" in g.target_label.text()
    g.find_btn.click()
    assert wait_until(lambda: "白紙の可能性" in g.status_label.text())
    assert win.view.selected_rows() == [2]


def test_trim_group_auto_applies_crops_with_undo(win: MainWindow, tmp_path: Path):
    panel = pageops_panel(win)
    win.open_pdf(_scan_pdf(tmp_path / "scan.pdf"))
    g = panel.trim_group
    g.pad_spin.setValue(0)
    g.apply_btn.click()
    assert wait_until(lambda: "トリミングしました" in g.status_label.text())
    crops = [r.crop for r in win.page_list.pages]
    assert crops[0] is None and crops[2] is None                # 白紙は変更しない
    assert crops[1] == pytest.approx((0.25, 0.30, 0.25, 0.30), abs=0.02)
    assert win.page_list.can_undo
    win.act_undo.trigger()
    assert all(r.crop is None for r in win.page_list.pages)


def test_trim_group_manual_and_reset(win: MainWindow, tmp_path: Path):
    panel = pageops_panel(win)
    win.open_pdf(_scan_pdf(tmp_path / "scan.pdf"))
    win.view.select_rows([1])
    QApplication.processEvents()
    g = panel.trim_group
    g.radio_manual.setChecked(True)
    g.manual_spins["left"].setValue(20)
    g.manual_spins["top"].setValue(10)
    g.apply_btn.click()
    assert wait_until(lambda: "トリミングしました" in g.status_label.text())
    assert win.page_list[1].crop is not None and win.page_list[0].crop is None
    l, t, r, b = win.page_list[1].crop
    assert l == pytest.approx(20 * 72 / 25.4 / 576, abs=0.002) and t > 0 and r == 0 and b == 0
    g.reset_btn.click()
    assert win.page_list[1].crop is None


def test_trim_group_ignores_result_if_pages_changed(win: MainWindow, tmp_path: Path):
    panel = pageops_panel(win)
    win.open_pdf(_scan_pdf(tmp_path / "scan.pdf"))
    g = panel.trim_group
    g.apply_btn.click()
    win.model.remove_pages([0])                                 # 調べている間に構成を変える
    assert wait_until(lambda: g._worker is None)
    assert "構成が変わった" in g.status_label.text()
    assert all(r.crop is None for r in win.page_list.pages)


# ---------------------------------------------------------------------------
# FinishPanel（仕上げ）
# ---------------------------------------------------------------------------
def test_finish_panel_requires_a_choice(win: MainWindow, tmp_path: Path, monkeypatch):
    panel = finish_panel(win)
    errors = []
    monkeypatch.setattr(win, "show_error", lambda msg, title="エラー": errors.append(msg))
    win.open_pdf(make_image_pdf(tmp_path / "f.pdf", pages=2))
    panel.run_btn.click()
    assert errors and "チェックを入れてください" in errors[0]
    panel.wm_box.setChecked(True)
    panel.wm_text.setText("  ")
    panel.run_btn.click()
    assert "透かしの文字が空です" in errors[-1]


def test_finish_panel_page_numbers_saved_next_to_source(win: MainWindow, tmp_path: Path):
    from pdftool.core.document import PdfDocument

    panel = finish_panel(win)
    win.tabs.setCurrentWidget(panel)
    win.open_pdf(make_image_pdf(tmp_path / "資料.pdf", pages=3))
    win.model.remove_pages([0])                                   # 編集後の状態（2ページ）が出力される
    panel.num_box.setChecked(True)
    panel.run_btn.click()
    assert wait_until(lambda: "保存しました" in panel.status_label.text())
    out = panel._last_output
    assert out.parent == tmp_path and out.name.endswith("_資料_stamped.pdf")
    with PdfDocument(out) as d:
        assert d.page_count == 2
        img = d.render_page(0, dpi=72)
    # 下中央に番号（暗い画素）が入っている。元画像はグラデーションなので、背景との差で見るため再描画差分で確認
    with PdfDocument(tmp_path / "資料.pdf") as d0:
        base = d0.render_page(1, dpi=72)
    from PIL import ImageChops
    diff = ImageChops.difference(img.convert("RGB"), base.convert("RGB")).getbbox()
    assert diff is not None and diff[1] > img.height * 0.85
    assert panel.open_folder_btn.isVisible()


def test_finish_panel_impose_2in1(win: MainWindow, tmp_path: Path):
    from pypdf import PdfReader

    panel = finish_panel(win)
    win.open_pdf(make_image_pdf(tmp_path / "ni.pdf", pages=4))
    panel.im_box.setChecked(True)
    panel.im_layout.setCurrentIndex(0)
    panel.run_btn.click()
    assert wait_until(lambda: "保存しました" in panel.status_label.text())
    out = panel._last_output
    assert out.name.endswith("_ni_2in1.pdf")
    pdf = PdfReader(str(out))
    assert len(pdf.pages) == 2
    assert float(pdf.pages[0].mediabox.width) > float(pdf.pages[0].mediabox.height)    # 横向き


def test_finish_panel_applies_crop_and_rotation_from_editing(win: MainWindow, tmp_path: Path):
    from pdftool.core.document import PdfDocument

    panel = finish_panel(win)
    win.open_pdf(make_image_pdf(tmp_path / "cr.pdf", pages=1))
    win.model.set_crops({0: (0.0, 0.0, 0.5, 0.0)})                 # 右半分を切る → 144x384
    win.model.rotate_pages([0], 90)                                # 回転 → 384x144
    panel.lb_box.setChecked(True)
    panel.run_btn.click()
    assert wait_until(lambda: "保存しました" in panel.status_label.text())
    with PdfDocument(panel._last_output) as d:
        info = d.page_info(0)
    assert (info.width_pt, info.height_pt) == pytest.approx((384, 144), abs=0.6)


def test_finish_panel_preview_opens_dialog(win: MainWindow, tmp_path: Path):
    from pdftool.ui.preview_dialog import PagePreviewDialog

    panel = finish_panel(win)
    win.open_pdf(make_image_pdf(tmp_path / "pv.pdf", pages=3))
    panel.lb_box.setChecked(True)
    panel.preview_btn.click()
    assert wait_until(lambda: win.findChildren(PagePreviewDialog))
    assert not list(tmp_path.glob("*stamped*"))                     # プレビューではファイルを作らない


def test_finish_panel_output_for_image_goes_next_to_original(win: MainWindow, tmp_path: Path):
    from PIL import Image
    from PySide6.QtCore import QPoint

    img = tmp_path / "スキャン.png"
    Image.new("RGB", (600, 800), (240, 240, 200)).save(img)
    win.open_files([img])
    assert wait_until(lambda: win.page_list is not None and len(win.page_list) == 1)
    panel = finish_panel(win)
    panel.num_box.setChecked(True)
    panel.run_btn.click()
    assert wait_until(lambda: "保存しました" in panel.status_label.text())
    assert panel._last_output.parent == tmp_path and "スキャン_stamped" in panel._last_output.name
