"""機能パネルの共通基底。各パネルはこれを継承し、MainWindow の状態を参照して core を呼ぶ。

パネルから使ってよい MainWindow の API（docs/API.md「UI 骨格」参照）:
    window.model                 … ThumbnailModel（.page_list で PageList）
    window.view.selected_rows()  … 選択行（0 始まり・昇順）
    window.current_source()      … 開いている先頭 PDF のパス（None あり）
    window.run_worker(fn, *args, on_finished=, on_error=, on_progress=, on_cancelled=, **kwargs)
    window.show_error(message)   … 日本語メッセージのダイアログ
    window.gs_path               … Ghostscript のパス（None なら圧縮不可）
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

if TYPE_CHECKING:
    from pdftool.ui.main_window import MainWindow


class PanelBase(QWidget):
    title: str = "パネル"

    def __init__(self, window: "MainWindow", parent: QWidget | None = None):
        super().__init__(parent)
        self.window_ = window

    # MainWindow から呼ばれるフック。必要なものだけ上書きする
    def on_document_changed(self) -> None:
        """PDF を開いた／閉じた／ページ構成が変わったとき。"""

    def on_selection_changed(self, rows: list[int]) -> None:
        """サムネイルの選択が変わったとき。"""

    def on_ink_changed(self) -> None:
        """ビューアの書き込み（ペン・蛍光ペン）が増減したとき。"""


class PlaceholderPanel(PanelBase):
    """Phase 4 で本実装に置き換えるまでの仮パネル。"""

    def __init__(self, window: "MainWindow", title: str, parent: QWidget | None = None):
        super().__init__(window, parent)
        self.title = title
        lay = QVBoxLayout(self)
        lbl = QLabel(f"「{title}」は Phase 4 で実装します。")
        lbl.setWordWrap(True)
        lay.addWidget(lbl)
        lay.addStretch(1)
