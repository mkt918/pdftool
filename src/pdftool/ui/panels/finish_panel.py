"""仕上げパネル: ページ番号・透かし・ラベルの追加と、面付け（2in1/4in1）をして新しい PDF に保存する。

現在の並び・回転・トリミングを反映して、元の PDF は変えずに新規ファイルを作る。
出力先は元ファイルと同じ場所（日付_元名_stamped.pdf / _2in1.pdf …）。
プレビューは先頭ページ（スタンプを付ける最初のページ）を一時ファイルに作って表示する。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from pdftool.core.compress import format_size
from pdftool.core.document import PdfDocument
from pdftool.core.impose import PAPER_SIZES_MM, PRESETS, ImposeSpec, impose_pages
from pdftool.core.pageops import PageRef, WriteProgress, write_pages
from pdftool.core.stamp import (
    POSITION_LABELS,
    POSITIONS,
    TextStamp,
    label_stamp,
    page_number_stamp,
    watermark_stamp,
)
from pdftool.ui.panels.base import PanelBase
from pdftool.ui.preview_dialog import PagePreviewDialog
from pdftool.utils import naming, tempfiles
from pdftool.utils.worker import pil_to_qimage

if TYPE_CHECKING:
    from pdftool.ui.main_window import MainWindow

COLORS = {
    "グレー": (128, 128, 128),
    "赤": (200, 0, 0),
    "青": (0, 60, 200),
    "黒": (0, 0, 0),
}
NUMBER_FORMATS = ["{n}", "- {n} -", "{n} / {total}", "P.{n}", "第{n}ページ"]


# ---------------------------------------------------------------------------
# 出力の生成（ワーカースレッドから呼ぶ純粋な関数）
# ---------------------------------------------------------------------------
def build_output(
    refs: Sequence[PageRef],
    dest: Path,
    stamps: Sequence[TextStamp],
    impose: ImposeSpec | None,
    passwords: dict[Path, str] | None = None,
    progress=None,
    cancel=None,
) -> Path:
    if impose is not None:
        return impose_pages(refs, dest, impose, passwords, stamps=stamps or None, progress=progress, cancel=cancel)
    return write_pages(refs, dest, passwords, stamps=stamps or None, progress=progress, cancel=cancel)


def render_preview(
    refs: Sequence[PageRef],
    stamps: Sequence[TextStamp],
    impose: ImposeSpec | None,
    passwords: dict[Path, str] | None = None,
):
    """先頭の出力ページ（面付けなら先頭の 1 枚）を作ってレンダリングした QImage を返す。"""
    skip = min((s.skip_first for s in stamps), default=0)
    per = impose.per_sheet if impose else 1
    sample = list(refs[skip: skip + per]) or list(refs[:per])
    shifted = [replace(s, skip_first=max(0, s.skip_first - skip)) for s in stamps]
    tmp = tempfiles.new_subdir() / "preview.pdf"
    out = build_output(sample, tmp, shifted, impose, passwords)
    with PdfDocument(out) as doc:
        return pil_to_qimage(doc.render_page(0, dpi=90))


class FinishPanel(PanelBase):
    title = "仕上げ"

    def __init__(self, window: "MainWindow", parent: QWidget | None = None):
        super().__init__(window, parent)
        self._worker = None
        self._last_output: Path | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        lay = QVBoxLayout(body)

        intro = QLabel(
            "ページ番号・透かし・ラベルの追加や、2in1/4in1の面付けをして、新しいPDFとして保存します。"
            "今の並び・回転・トリミングが反映され、元のPDFは変わりません。"
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #666;")
        lay.addWidget(intro)
        self.target_label = QLabel()
        lay.addWidget(self.target_label)

        lay.addWidget(self._build_page_number())
        lay.addWidget(self._build_watermark())
        lay.addWidget(self._build_label())
        lay.addWidget(self._build_impose())

        btns = QHBoxLayout()
        self.preview_btn = QPushButton("プレビュー")
        self.preview_btn.setToolTip("先頭ページの仕上がりを確認します（ファイルは作りません）")
        self.preview_btn.clicked.connect(self._preview)
        btns.addWidget(self.preview_btn)
        self.run_btn = QPushButton("新しいPDFとして保存")
        self.run_btn.clicked.connect(self._run)
        btns.addWidget(self.run_btn)
        lay.addLayout(btns)
        self.cancel_btn = QPushButton("キャンセル")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel)
        lay.addWidget(self.cancel_btn)

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
        self._update_state()

    # -- 構築 ---------------------------------------------------------------
    @staticmethod
    def _checkable_box(title: str, checked: bool) -> QGroupBox:
        box = QGroupBox(title)
        box.setCheckable(True)
        box.setChecked(checked)
        return box

    @staticmethod
    def _position_combo(positions: Sequence[str], default: str) -> QComboBox:
        c = QComboBox()
        for p in positions:
            c.addItem(POSITION_LABELS[p], p)
        c.setCurrentIndex(c.findData(default))
        return c

    @staticmethod
    def _color_combo(default: str) -> QComboBox:
        c = QComboBox()
        for name, rgb in COLORS.items():
            c.addItem(name, rgb)
        c.setCurrentIndex(c.findText(default))
        return c

    def _build_page_number(self) -> QGroupBox:
        self.num_box = self._checkable_box("ページ番号を入れる", False)
        f = QFormLayout(self.num_box)
        self.num_fmt = QComboBox()
        self.num_fmt.setEditable(True)
        self.num_fmt.addItems(NUMBER_FORMATS)
        self.num_fmt.setToolTip("{n} が番号、{total} が総ページ数。自由に書き換えられます")
        f.addRow("書式:", self.num_fmt)
        self.num_pos = self._position_combo([p for p in POSITIONS if p != "center"], "bottom-center")
        f.addRow("位置:", self.num_pos)
        self.num_size = QDoubleSpinBox()
        self.num_size.setRange(6, 48)
        self.num_size.setValue(10)
        self.num_size.setSuffix(" pt")
        f.addRow("大きさ:", self.num_size)
        self.num_start = QSpinBox()
        self.num_start.setRange(0, 9999)
        self.num_start.setValue(1)
        f.addRow("開始番号:", self.num_start)
        self.num_skip = QSpinBox()
        self.num_skip.setRange(0, 999)
        self.num_skip.setToolTip("表紙など、先頭の何ページには付けないか（その次のページが開始番号になります）")
        f.addRow("先頭の除外ページ数:", self.num_skip)
        return self.num_box

    def _build_watermark(self) -> QGroupBox:
        self.wm_box = self._checkable_box("透かしを入れる", False)
        f = QFormLayout(self.wm_box)
        self.wm_text = QLineEdit("社外秘")
        f.addRow("文字:", self.wm_text)
        self.wm_size = QDoubleSpinBox()
        self.wm_size.setRange(20, 300)
        self.wm_size.setValue(80)
        self.wm_size.setSuffix(" pt")
        f.addRow("大きさ:", self.wm_size)
        self.wm_opacity = QSpinBox()
        self.wm_opacity.setRange(3, 100)
        self.wm_opacity.setValue(15)
        self.wm_opacity.setSuffix(" %")
        self.wm_opacity.setToolTip("小さいほど薄くなります（本文が読めるよう 10〜20% 程度がおすすめ）")
        f.addRow("濃さ:", self.wm_opacity)
        self.wm_angle = QSpinBox()
        self.wm_angle.setRange(-90, 90)
        self.wm_angle.setValue(45)
        self.wm_angle.setSuffix(" °")
        f.addRow("角度:", self.wm_angle)
        self.wm_color = self._color_combo("グレー")
        f.addRow("色:", self.wm_color)
        return self.wm_box

    def _build_label(self) -> QGroupBox:
        self.lb_box = self._checkable_box("ラベル（スタンプ）を入れる", False)
        f = QFormLayout(self.lb_box)
        self.lb_text = QLineEdit("配布用")
        f.addRow("文字:", self.lb_text)
        self.lb_pos = self._position_combo(POSITIONS, "top-right")
        f.addRow("位置:", self.lb_pos)
        self.lb_size = QDoubleSpinBox()
        self.lb_size.setRange(6, 72)
        self.lb_size.setValue(14)
        self.lb_size.setSuffix(" pt")
        f.addRow("大きさ:", self.lb_size)
        self.lb_color = self._color_combo("赤")
        f.addRow("色:", self.lb_color)
        self.lb_border = QCheckBox("枠で囲む")
        self.lb_border.setChecked(True)
        f.addRow(self.lb_border)
        return self.lb_box

    def _build_impose(self) -> QGroupBox:
        self.im_box = self._checkable_box("面付け（1枚の用紙に複数ページ）", False)
        f = QFormLayout(self.im_box)
        self.im_layout = QComboBox()
        self.im_layout.addItem("2in1（横向き用紙に2ページ）", "2in1")
        self.im_layout.addItem("4in1（縦向き用紙に4ページ）", "4in1")
        f.addRow("レイアウト:", self.im_layout)
        self.im_paper = QComboBox()
        self.im_paper.addItems(list(PAPER_SIZES_MM))
        f.addRow("用紙:", self.im_paper)
        self.im_rtl = QCheckBox("右から左へ並べる（縦書き・右開きの資料）")
        f.addRow(self.im_rtl)
        self.im_border = QCheckBox("各ページの周りに細い枠線を引く")
        f.addRow(self.im_border)
        return self.im_box

    # -- 設定の取り出し ------------------------------------------------------
    def _collect_stamps(self) -> list[TextStamp]:
        stamps: list[TextStamp] = []
        if self.num_box.isChecked():
            fmt = self.num_fmt.currentText().strip()
            if not fmt:
                raise ValueError("ページ番号の書式が空です（例: {n}）")
            stamps.append(page_number_stamp(
                fmt, self.num_pos.currentData(), self.num_size.value(),
                start=self.num_start.value(), skip_first=self.num_skip.value(),
            ))
        if self.wm_box.isChecked():
            text = self.wm_text.text().strip()
            if not text:
                raise ValueError("透かしの文字が空です")
            stamps.append(watermark_stamp(
                text, self.wm_size.value(), self.wm_opacity.value() / 100.0,
                float(self.wm_angle.value()), self.wm_color.currentData(),
            ))
        if self.lb_box.isChecked():
            text = self.lb_text.text().strip()
            if not text:
                raise ValueError("ラベルの文字が空です")
            st = label_stamp(text, self.lb_pos.currentData(), self.lb_size.value(), self.lb_color.currentData())
            stamps.append(replace(st, border=self.lb_border.isChecked()))
        return stamps

    def _collect_impose(self) -> ImposeSpec | None:
        if not self.im_box.isChecked():
            return None
        base = PRESETS[self.im_layout.currentData()]
        return replace(
            base, paper=self.im_paper.currentText(),
            right_to_left=self.im_rtl.isChecked(), border=self.im_border.isChecked(),
        )

    def _settings(self):
        """(stamps, impose)。何も選ばれていない・値が不正なら ValueError。"""
        stamps = self._collect_stamps()
        impose = self._collect_impose()
        if not stamps and impose is None:
            raise ValueError("ページ番号・透かし・ラベル・面付けのうち、どれかにチェックを入れてください")
        return stamps, impose

    # -- 状態 ---------------------------------------------------------------
    def on_document_changed(self) -> None:
        self._update_state()

    def _update_state(self) -> None:
        pl = self.window_.page_list
        has = bool(pl) and len(pl) > 0
        running = self._worker is not None
        self.target_label.setText(f"対象: 全 {len(pl)} ページ" if has else "PDF を開くと使えます")
        self.run_btn.setEnabled(has and not running)
        self.preview_btn.setEnabled(has and not running)

    def _set_running_ui(self, running: bool) -> None:
        for box in (self.num_box, self.wm_box, self.lb_box, self.im_box):
            box.setEnabled(not running)
        self.cancel_btn.setEnabled(running)

    # -- 実行 ---------------------------------------------------------------
    def _run(self) -> None:
        pl = self.window_.page_list
        if not pl:
            return
        try:
            stamps, impose = self._settings()
        except ValueError as e:
            self.window_.show_error(str(e))
            return
        refs = list(pl.pages)
        suffix = impose.label if impose else "stamped"
        dest = naming.output_file(refs[0].source_path, suffix, ".pdf")
        passwords = self.window_.passwords_dict()

        self._set_running_ui(True)
        self.open_folder_btn.setVisible(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, len(refs))
        self.progress.setValue(0)
        self.status_label.setText("仕上げています…")
        self._worker = self.window_.run_worker(
            build_output, refs, dest, stamps, impose, passwords,
            on_finished=self._on_finished, on_error=self._on_error,
            on_progress=self._on_progress, on_cancelled=self._on_cancelled,
        )
        self._update_state()

    def _preview(self) -> None:
        pl = self.window_.page_list
        if not pl:
            return
        try:
            stamps, impose = self._settings()
        except ValueError as e:
            self.window_.show_error(str(e))
            return
        refs = list(pl.pages)
        self.status_label.setText("プレビューを作っています…")
        self.window_.run_worker(
            render_preview, refs, stamps, impose, self.window_.passwords_dict(),
            on_finished=self._show_preview,
            on_error=lambda msg, _e: self.status_label.setText(f"エラー: {msg}"),
        )

    def _show_preview(self, image) -> None:
        self.status_label.setText("")
        dlg = PagePreviewDialog(image, "プレビュー（先頭ページ）", self.window_)
        dlg.show()

    def _cancel(self) -> None:
        if self._worker:
            self._worker.cancel()
            self.status_label.setText("キャンセル中…")

    def _on_progress(self, p: WriteProgress) -> None:
        self.progress.setMaximum(max(1, p.total))
        self.progress.setValue(p.current)
        self.status_label.setText(p.message)

    def _reset_running_state(self) -> None:
        self._worker = None
        self.progress.setVisible(False)
        self._set_running_ui(False)
        self._update_state()

    def _on_finished(self, out: Path) -> None:
        self._reset_running_state()
        self._last_output = out
        self.open_folder_btn.setVisible(True)
        self.status_label.setText(f"保存しました: {out.name}（{format_size(out.stat().st_size)}）")

    def _on_error(self, message: str, exc: object) -> None:
        self._reset_running_state()
        self.status_label.setText(f"エラー: {message}")

    def _on_cancelled(self, _partial=None) -> None:
        self._reset_running_state()
        self.status_label.setText("キャンセルしました")

    def _open_output_folder(self) -> None:
        if self._last_output:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._last_output.parent)))
