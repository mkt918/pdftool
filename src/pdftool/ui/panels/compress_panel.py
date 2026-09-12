"""圧縮パネル: 目標サイズ指定 / プリセットの2モードで、開いている PDF を圧縮する。

出力先は「元と同じフォルダ」に固定（PLAN.md §0 の確定事項）。
圧縮は常に元ファイル（ディスク上のオリジナル）を対象にする。編集中のページ操作
（回転・削除・並べ替え）は反映されない — 反映したい場合は先に保存してから圧縮する。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
)

from pdftool.core.compress import (
    DEFAULT_MAX_TRIALS,
    CompressProgress,
    CompressResult,
    compress_preset,
    compress_to_target,
    format_size,
)
from pdftool.ui.panels.base import PanelBase

if TYPE_CHECKING:
    from pdftool.ui.main_window import MainWindow


class CompressPanel(PanelBase):
    title = "圧縮"

    def __init__(self, window: "MainWindow", parent=None):
        super().__init__(window, parent)
        self._worker = None
        self._last_output: Path | None = None

        lay = QVBoxLayout(self)

        self.warning = QLabel()
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet("color:#b00;")
        lay.addWidget(self.warning)

        mode_box = QGroupBox("方式")
        mode_lay = QVBoxLayout(mode_box)
        group = QButtonGroup(self)
        self.radio_target = QRadioButton("目標サイズを指定")
        self.radio_preset = QRadioButton("プリセット")
        self.radio_target.setChecked(True)
        group.addButton(self.radio_target)
        group.addButton(self.radio_preset)
        self.radio_target.toggled.connect(self._sync_mode)

        mode_lay.addWidget(self.radio_target)
        target_row = QHBoxLayout()
        self.target_spin = QDoubleSpinBox()
        self.target_spin.setRange(0.1, 500.0)
        self.target_spin.setValue(5.0)
        self.target_spin.setSuffix(" MB")
        target_row.addWidget(QLabel("目標:"))
        target_row.addWidget(self.target_spin)
        target_row.addStretch(1)
        mode_lay.addLayout(target_row)

        mode_lay.addWidget(self.radio_preset)
        preset_row = QHBoxLayout()
        self.preset_combo = QComboBox()
        self.preset_combo.addItem("高品質", "high")
        self.preset_combo.addItem("標準", "standard")
        self.preset_combo.addItem("最小", "small")
        self.preset_combo.setCurrentIndex(1)
        preset_row.addWidget(self.preset_combo)
        preset_row.addStretch(1)
        mode_lay.addLayout(preset_row)
        lay.addWidget(mode_box)

        btn_row = QHBoxLayout()
        self.run_btn = QPushButton("圧縮を実行")
        self.run_btn.clicked.connect(self._run)
        btn_row.addWidget(self.run_btn)
        self.cancel_btn = QPushButton("キャンセル")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel)
        btn_row.addWidget(self.cancel_btn)
        lay.addLayout(btn_row)

        self.progress = QProgressBar()
        self.progress.setRange(0, DEFAULT_MAX_TRIALS)
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
        self._sync_mode()
        self._update_gs_state()

    # -- 状態管理 -------------------------------------------------------
    def _sync_mode(self) -> None:
        is_target = self.radio_target.isChecked()
        self.target_spin.setEnabled(is_target and self.window_.gs_path is not None)
        self.preset_combo.setEnabled(not is_target and self.window_.gs_path is not None)

    def _update_gs_state(self) -> None:
        has_gs = self.window_.gs_path is not None
        self.warning.setText(
            "" if has_gs else "Ghostscript が見つかりません。README の手順でインストールしてください。"
        )
        for w in (self.radio_target, self.radio_preset):
            w.setEnabled(has_gs)
        self._sync_mode()
        self._update_run_enabled()

    def on_document_changed(self) -> None:
        self._update_gs_state()

    def _update_run_enabled(self) -> None:
        has_doc = self.window_.current_source() is not None
        has_gs = self.window_.gs_path is not None
        running = self._worker is not None
        self.run_btn.setEnabled(has_doc and has_gs and not running)

    # -- 実行 -------------------------------------------------------------
    def _run(self) -> None:
        src = self.window_.current_source()
        if src is None:
            return
        self.run_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.open_folder_btn.setVisible(False)
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.status_label.setText("圧縮を開始します…")

        if self.radio_target.isChecked():
            self._worker = self.window_.run_worker(
                compress_to_target, src, self.target_spin.value(),
                on_finished=self._on_finished, on_error=self._on_error,
                on_progress=self._on_progress, on_cancelled=self._on_cancelled,
            )
        else:
            self._worker = self.window_.run_worker(
                compress_preset, src, self.preset_combo.currentData(),
                on_finished=self._on_finished, on_error=self._on_error,
                on_progress=self._on_progress, on_cancelled=self._on_cancelled,
            )

    def _cancel(self) -> None:
        if self._worker:
            self._worker.cancel()
            self.status_label.setText("キャンセル中…")

    def _on_progress(self, p: CompressProgress) -> None:
        self.progress.setMaximum(p.max_trials)
        self.progress.setValue(p.trial)
        self.status_label.setText(p.message)

    def _reset_running_state(self) -> None:
        self._worker = None
        self.progress.setVisible(False)
        self.cancel_btn.setEnabled(False)
        self._update_run_enabled()

    def _on_finished(self, result: CompressResult) -> None:
        self._reset_running_state()
        self._last_output = result.output_path
        self.open_folder_btn.setVisible(True)
        msg = (
            f"完了: {format_size(result.original_size)} → {format_size(result.output_size)}"
            f"（{result.ratio:.0%}）/ {result.params.describe()} / {result.trials} 試行\n"
            f"出力: {result.output_path.name}"
        )
        if result.target_size is not None and not result.achieved:
            msg += "\n※ 目標サイズには届きませんでした（達成できた最小サイズです）"
        self.status_label.setText(msg)

    def _on_error(self, message: str, exc: object) -> None:
        self._reset_running_state()
        self.status_label.setText(f"エラー: {message}")

    def _on_cancelled(self, _partial=None) -> None:
        self._reset_running_state()
        self.status_label.setText("キャンセルしました")

    def _open_output_folder(self) -> None:
        if self._last_output:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._last_output.parent)))
