"""画像書き出しパネル: 対象ページを連番PNG/JPEGとして書き出す。

pageops（回転・並べ替え）の結果を反映するため、常に `export_refs`（編集中の
PageList 由来）を使う。出力先は「元と同じ場所」に固定（PLAN.md §0 の確定事項）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from pdftool.core.export import ExportOptions, ExportProgress, ExportResult, export_refs
from pdftool.core.pageops import parse_page_spec
from pdftool.ui.panels.base import PanelBase

if TYPE_CHECKING:
    from pdftool.ui.main_window import MainWindow


class ExportPanel(PanelBase):
    title = "画像書き出し"

    def __init__(self, window: "MainWindow", parent=None):
        super().__init__(window, parent)
        self._worker = None
        self._last_output_dir = None

        lay = QVBoxLayout(self)

        range_box = QGroupBox("対象ページ")
        range_lay = QVBoxLayout(range_box)
        self.range_combo = QComboBox()
        self.range_combo.addItem("全ページ", "all")
        self.range_combo.addItem("選択ページ", "selected")
        self.range_combo.addItem("範囲指定", "spec")
        range_lay.addWidget(self.range_combo)
        self.selection_label = QLabel()
        self.selection_label.setStyleSheet("color: #666;")
        self.selection_label.setWordWrap(True)
        range_lay.addWidget(self.selection_label)
        self.spec_edit = QLineEdit()
        self.spec_edit.setPlaceholderText("例: 1-5,8,10-12")
        self.spec_edit.setEnabled(False)
        range_lay.addWidget(self.spec_edit)
        self.range_combo.currentIndexChanged.connect(
            lambda _: self.spec_edit.setEnabled(self.range_combo.currentData() == "spec")
        )
        lay.addWidget(range_box)

        opt_box = QGroupBox("形式")
        form = QFormLayout(opt_box)
        self.dpi_spin = QSpinBox()
        self.dpi_spin.setRange(30, 600)
        self.dpi_spin.setValue(200)
        form.addRow("解像度(dpi):", self.dpi_spin)
        self.fmt_combo = QComboBox()
        self.fmt_combo.addItem("PNG", "png")
        self.fmt_combo.addItem("JPEG", "jpg")
        form.addRow("形式:", self.fmt_combo)
        self.quality_spin = QSpinBox()
        self.quality_spin.setRange(1, 100)
        self.quality_spin.setValue(90)
        self.quality_spin.setEnabled(False)
        form.addRow("JPEG品質:", self.quality_spin)
        self.fmt_combo.currentIndexChanged.connect(
            lambda _: self.quality_spin.setEnabled(self.fmt_combo.currentData() == "jpg")
        )
        lay.addWidget(opt_box)

        btn_row_lay = QVBoxLayout()
        self.run_btn = QPushButton("書き出しを実行")
        self.run_btn.clicked.connect(self._run)
        btn_row_lay.addWidget(self.run_btn)
        self.cancel_btn = QPushButton("キャンセル")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel)
        btn_row_lay.addWidget(self.cancel_btn)
        lay.addLayout(btn_row_lay)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        lay.addWidget(self.progress)

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        lay.addWidget(self.status_label)

        self.open_folder_btn = QPushButton("出力フォルダを開く")
        self.open_folder_btn.setVisible(False)
        self.open_folder_btn.clicked.connect(self._open_output_folder)
        lay.addWidget(self.open_folder_btn)

        lay.addStretch(1)
        self._update_selection_label()
        self._update_run_enabled()

    # -- 状態管理 -------------------------------------------------------
    def on_document_changed(self) -> None:
        self._update_run_enabled()

    def on_selection_changed(self, rows: list[int]) -> None:
        # 左のサムネイルで複数選択すれば、それだけでそのページが対象になる。
        # 「範囲指定」（文字入力）を選んでいる間だけは上書きしない。
        if self.range_combo.currentData() != "spec":
            target = "selected" if rows else "all"
            idx = self.range_combo.findData(target)
            if self.range_combo.currentIndex() != idx:
                self.range_combo.setCurrentIndex(idx)
        self._update_selection_label(rows)
        self._update_run_enabled()

    def _update_selection_label(self, rows: list[int] | None = None) -> None:
        if rows is None:
            rows = self.window_.view.selected_rows()
        if rows:
            self.selection_label.setText(f"サムネイルで {len(rows)} ページ選択中")
        else:
            self.selection_label.setText("未選択（全ページが対象）")

    def _update_run_enabled(self) -> None:
        running = self._worker is not None
        self.run_btn.setEnabled(self.window_.current_source() is not None and not running)

    def _set_running_ui(self, running: bool) -> None:
        """実行中は設定を変えても反映されない（開始時の値で走る）ので、触れないようにする。"""
        for w in (self.range_combo, self.spec_edit, self.dpi_spin, self.fmt_combo, self.quality_spin):
            w.setEnabled(not running)
        if not running:
            # dpi_spin/fmt_combo/spec_edit は常に有効に戻すが、quality_spin と spec_edit は
            # それぞれ「JPEG選択中か」「範囲指定モードか」に応じた本来の有効/無効に戻す
            self.quality_spin.setEnabled(self.fmt_combo.currentData() == "jpg")
            self.spec_edit.setEnabled(self.range_combo.currentData() == "spec")

    def _selected_rows(self) -> list[int]:
        """書き出す行（0 始まり。画面上の並び）。ファイル名の番号にも使う。"""
        pl = self.window_.page_list
        mode = self.range_combo.currentData()
        if mode == "all":
            return list(range(len(pl)))
        if mode == "selected":
            rows = self.window_.view.selected_rows()
            if not rows:
                raise ValueError("ページが選択されていません")
            return rows
        return parse_page_spec(self.spec_edit.text(), len(pl))

    # -- 実行 -------------------------------------------------------------
    def _run(self) -> None:
        pl = self.window_.page_list
        if pl is None:
            return
        try:
            rows = self._selected_rows()
        except ValueError as e:
            self.window_.show_error(str(e))
            return
        refs = [pl[r] for r in rows]

        options = ExportOptions(
            dpi=self.dpi_spin.value(),
            fmt=self.fmt_combo.currentData(),
            jpeg_quality=self.quality_spin.value(),
        )
        base = self.window_.current_source()
        passwords = self.window_.passwords_dict()

        self._set_running_ui(True)
        self.run_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.open_folder_btn.setVisible(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, len(refs))
        self.progress.setValue(0)
        self.status_label.setText("書き出しを開始します…")

        self._worker = self.window_.run_worker(
            export_refs, refs, base,
            options=options, passwords=passwords, page_numbers=[r + 1 for r in rows],
            on_finished=self._on_finished, on_error=self._on_error,
            on_progress=self._on_progress, on_cancelled=self._on_cancelled,
        )

    def _cancel(self) -> None:
        if self._worker:
            self._worker.cancel()
            self.status_label.setText("キャンセル中…")

    def _on_progress(self, p: ExportProgress) -> None:
        self.progress.setMaximum(p.total)
        self.progress.setValue(p.current)
        self.status_label.setText(p.message)

    def _reset_running_state(self) -> None:
        self._worker = None
        self.progress.setVisible(False)
        self.cancel_btn.setEnabled(False)
        self._set_running_ui(False)
        self._update_run_enabled()

    def _on_finished(self, result: ExportResult) -> None:
        self._reset_running_state()
        self._last_output_dir = result.output_dir
        self.open_folder_btn.setVisible(True)
        self.status_label.setText(f"完了: {len(result.files)} 件を書き出しました\n出力先: {result.output_dir}")

    def _on_error(self, message: str, exc: object) -> None:
        self._reset_running_state()
        self.status_label.setText(f"エラー: {message}")

    def _on_cancelled(self, _partial=None) -> None:
        self._reset_running_state()
        self.status_label.setText("キャンセルしました")

    def _open_output_folder(self) -> None:
        if self._last_output_dir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._last_output_dir)))
