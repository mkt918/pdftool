"""バッチパネル: フォルダ内の PDF をまとめて圧縮 or 画像書き出しする。

対応する操作は圧縮と画像書き出しの2つ（ページ操作はバッチ対象外）。
現在開いているドキュメントとは独立に、指定フォルダに対して動く。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pdftool.core.batch import BatchProgress, BatchResult, batch_compress, batch_export
from pdftool.core.compress import format_size
from pdftool.core.export import ExportOptions
from pdftool.ui.panels.base import PanelBase

if TYPE_CHECKING:
    from pdftool.ui.main_window import MainWindow


class BatchPanel(PanelBase):
    title = "バッチ"

    def __init__(self, window: "MainWindow", parent=None):
        super().__init__(window, parent)
        self._worker = None
        self._target_folder: Path | None = None

        lay = QVBoxLayout(self)

        folder_row = QHBoxLayout()
        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText("処理するフォルダ")
        folder_row.addWidget(self.folder_edit)
        browse_btn = QPushButton("参照…")
        browse_btn.clicked.connect(self._browse)
        folder_row.addWidget(browse_btn)
        lay.addLayout(folder_row)

        self.recursive_check = QCheckBox("サブフォルダも対象にする")
        lay.addWidget(self.recursive_check)

        mode_box = QGroupBox("処理内容")
        mode_lay = QVBoxLayout(mode_box)
        self.radio_compress = QRadioButton("圧縮")
        self.radio_export = QRadioButton("画像書き出し")
        self.radio_compress.setChecked(True)
        mode_lay.addWidget(self.radio_compress)
        mode_lay.addWidget(self.radio_export)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_compress_options())
        self.stack.addWidget(self._build_export_options())
        mode_lay.addWidget(self.stack)
        self.radio_compress.toggled.connect(lambda on: self.stack.setCurrentIndex(0 if on else 1))
        lay.addWidget(mode_box)

        btn_row = QHBoxLayout()
        self.run_btn = QPushButton("実行")
        self.run_btn.clicked.connect(self._run)
        btn_row.addWidget(self.run_btn)
        self.cancel_btn = QPushButton("キャンセル")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel)
        btn_row.addWidget(self.cancel_btn)
        lay.addLayout(btn_row)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        lay.addWidget(self.progress)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        lay.addWidget(self.status_label)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["ファイル名", "元サイズ", "後サイズ", "結果"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        lay.addWidget(self.table, 1)

        self.open_folder_btn = QPushButton("フォルダを開く")
        self.open_folder_btn.setVisible(False)
        self.open_folder_btn.clicked.connect(self._open_target_folder)
        lay.addWidget(self.open_folder_btn)

    # -- サブオプション -----------------------------------------------------
    def _build_compress_options(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        self.c_radio_target = QRadioButton("目標サイズ")
        self.c_radio_preset = QRadioButton("プリセット")
        self.c_radio_target.setChecked(True)
        lay.addWidget(self.c_radio_target)
        row1 = QHBoxLayout()
        self.c_target_spin = QDoubleSpinBox()
        self.c_target_spin.setRange(0.1, 500.0)
        self.c_target_spin.setValue(5.0)
        self.c_target_spin.setSuffix(" MB")
        row1.addWidget(QLabel("目標:"))
        row1.addWidget(self.c_target_spin)
        row1.addStretch(1)
        lay.addLayout(row1)
        lay.addWidget(self.c_radio_preset)
        row2 = QHBoxLayout()
        self.c_preset_combo = QComboBox()
        self.c_preset_combo.addItem("高品質", "high")
        self.c_preset_combo.addItem("標準", "standard")
        self.c_preset_combo.addItem("最小", "small")
        self.c_preset_combo.setCurrentIndex(1)
        row2.addWidget(self.c_preset_combo)
        row2.addStretch(1)
        lay.addLayout(row2)
        self.c_radio_target.toggled.connect(
            lambda on: (self.c_target_spin.setEnabled(on), self.c_preset_combo.setEnabled(not on))
        )
        return w

    def _build_export_options(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(0, 0, 0, 0)
        self.e_dpi_spin = QSpinBox()
        self.e_dpi_spin.setRange(30, 600)
        self.e_dpi_spin.setValue(150)
        form.addRow("解像度(dpi):", self.e_dpi_spin)
        self.e_fmt_combo = QComboBox()
        self.e_fmt_combo.addItem("PNG", "png")
        self.e_fmt_combo.addItem("JPEG", "jpg")
        form.addRow("形式:", self.e_fmt_combo)
        return w

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "処理するフォルダを選ぶ")
        if d:
            self.folder_edit.setText(d)

    # -- 実行 -------------------------------------------------------------
    def _run(self) -> None:
        folder = self.folder_edit.text().strip()
        if not folder:
            self.window_.show_error("フォルダを指定してください")
            return
        folder_path = Path(folder)
        if not folder_path.is_dir():
            self.window_.show_error(f"フォルダが見つかりません: {folder}")
            return
        if self.radio_compress.isChecked() and self.window_.gs_path is None:
            self.window_.show_error("Ghostscript が見つかりません。README の手順でインストールしてください。")
            return

        self.table.setRowCount(0)
        self.open_folder_btn.setVisible(False)
        self.run_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.status_label.setText("開始します…")
        self._target_folder = folder_path

        if self.radio_compress.isChecked():
            kwargs = (
                {"target_mb": self.c_target_spin.value()}
                if self.c_radio_target.isChecked()
                else {"preset": self.c_preset_combo.currentData()}
            )
            self._worker = self.window_.run_worker(
                batch_compress, folder_path,
                recursive=self.recursive_check.isChecked(), **kwargs,
                on_finished=self._on_finished, on_error=self._on_error,
                on_progress=self._on_progress, on_cancelled=self._on_cancelled,
            )
        else:
            options = ExportOptions(dpi=self.e_dpi_spin.value(), fmt=self.e_fmt_combo.currentData())
            self._worker = self.window_.run_worker(
                batch_export, folder_path, options=options,
                recursive=self.recursive_check.isChecked(),
                on_finished=self._on_finished, on_error=self._on_error,
                on_progress=self._on_progress, on_cancelled=self._on_cancelled,
            )

    def _cancel(self) -> None:
        if self._worker:
            self._worker.cancel()
            self.status_label.setText("キャンセル中…")

    def _on_progress(self, p: BatchProgress) -> None:
        self.progress.setMaximum(p.total)
        self.progress.setValue(p.index)
        self.status_label.setText(p.message)

    def _fill_table(self, result: BatchResult) -> None:
        self.table.setRowCount(len(result.items))
        for row, item in enumerate(result.items):
            self.table.setItem(row, 0, QTableWidgetItem(item.path.name))
            self.table.setItem(row, 1, QTableWidgetItem(format_size(item.original_size)))
            self.table.setItem(row, 2, QTableWidgetItem(
                format_size(item.output_size) if item.output_size is not None else "-"
            ))
            self.table.setItem(row, 3, QTableWidgetItem("OK" if item.ok else item.message))

    def _reset_running_state(self) -> None:
        self._worker = None
        self.run_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.progress.setVisible(False)

    def _on_finished(self, result: BatchResult) -> None:
        self._reset_running_state()
        self._fill_table(result)
        self.status_label.setText(
            f"完了: 成功 {result.ok_count} / 失敗 {result.fail_count}（全 {len(result.items)} 件）"
        )
        self.open_folder_btn.setVisible(True)

    def _on_error(self, message: str, exc: object) -> None:
        self._reset_running_state()
        self.status_label.setText(f"エラー: {message}")

    def _on_cancelled(self, partial: BatchResult | None = None) -> None:
        self._reset_running_state()
        if partial is not None and partial.items:
            self._fill_table(partial)
            self.status_label.setText(
                f"キャンセルしました（完了: 成功 {partial.ok_count} / 失敗 {partial.fail_count}）"
            )
            self.open_folder_btn.setVisible(True)
        else:
            self.status_label.setText("キャンセルしました")

    def _open_target_folder(self) -> None:
        if self._target_folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._target_folder)))
