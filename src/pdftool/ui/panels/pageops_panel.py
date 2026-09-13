"""ページ操作パネル。

削除・回転・並べ替え・保存はツールバー / サムネイル上のドラッグ操作で行う。
このパネルが担当するのは、ツールバーではやりにくい3つの操作:
  - 範囲文字列（'1-5,8,10-12'）によるページ選択
  - 任意位置への PDF 挿入（末尾への結合はツールバーの「結合…」で足りる）
  - 分割（選択ページ / 全ページを別ファイルへ書き出す）
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from pdftool.core.document import PdfEncryptedError, PdfError
from pdftool.core.pageops import parse_page_spec
from pdftool.ui.panels.base import PanelBase

if TYPE_CHECKING:
    from pdftool.ui.main_window import MainWindow


class PageOpsPanel(PanelBase):
    title = "ページ操作"

    def __init__(self, window: "MainWindow", parent=None):
        super().__init__(window, parent)
        self._split_running = False

        lay = QVBoxLayout(self)

        select_box = QGroupBox("範囲指定で選択")
        select_lay = QHBoxLayout(select_box)
        self.spec_edit = QLineEdit()
        self.spec_edit.setPlaceholderText("例: 1-5,8,10-12")
        self.spec_edit.returnPressed.connect(self._select_by_spec)
        select_lay.addWidget(self.spec_edit)
        self.select_btn = QPushButton("選択")
        self.select_btn.clicked.connect(self._select_by_spec)
        select_lay.addWidget(self.select_btn)
        lay.addWidget(select_box)

        insert_box = QGroupBox("挿入（結合）")
        insert_lay = QVBoxLayout(insert_box)
        self.insert_btn = QPushButton("選択ページの前に PDF を挿入…")
        self.insert_btn.setToolTip("末尾に結合するだけならツールバーの「結合…」で十分です")
        self.insert_btn.clicked.connect(self._insert_pdf)
        insert_lay.addWidget(self.insert_btn)
        lay.addWidget(insert_box)

        split_box = QGroupBox("分割")
        split_lay = QVBoxLayout(split_box)
        self.split_selected_btn = QPushButton("選択ページを別ファイルに書き出す")
        self.split_selected_btn.clicked.connect(self._split_selected)
        split_lay.addWidget(self.split_selected_btn)
        self.split_all_btn = QPushButton("全ページを1ページずつに分割")
        self.split_all_btn.clicked.connect(self._split_all)
        split_lay.addWidget(self.split_all_btn)
        lay.addWidget(split_box)

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        lay.addWidget(self.status_label)

        lay.addStretch(1)
        self._update_enabled()

    # -- 状態管理 -------------------------------------------------------
    def on_document_changed(self) -> None:
        self._update_enabled()

    def on_selection_changed(self, rows: list[int]) -> None:
        self._update_enabled()

    def _update_enabled(self) -> None:
        pl = self.window_.page_list
        has_doc = bool(pl) and len(pl) > 0
        running = self._split_running
        self.select_btn.setEnabled(has_doc)
        self.insert_btn.setEnabled(has_doc and not running)
        self.split_all_btn.setEnabled(has_doc and not running)
        self.split_selected_btn.setEnabled(
            has_doc and not running and bool(self.window_.view.selected_rows())
        )

    # -- 選択 -------------------------------------------------------------
    def _select_by_spec(self) -> None:
        pl = self.window_.page_list
        if pl is None:
            return
        try:
            rows = parse_page_spec(self.spec_edit.text(), len(pl))
        except ValueError as e:
            self.window_.show_error(str(e))
            return
        self.window_.view.select_rows(rows)
        self.status_label.setText(f"{len(rows)} ページを選択しました")

    # -- 挿入 -------------------------------------------------------------
    def _insert_pdf(self) -> None:
        pl = self.window_.page_list
        if pl is None:
            return
        start = self.window_.settings.value("last_dir", "")
        f, _ = QFileDialog.getOpenFileName(self, "挿入する PDF を選ぶ", start, "PDF (*.pdf)")
        if not f:
            return
        self.window_.settings.setValue("last_dir", str(Path(f).parent))
        path = Path(f)
        rows = self.window_.view.selected_rows()
        at = rows[0] if rows else 0
        password = None
        while True:
            try:
                n = self.window_.model.insert_pdf(path, at, password)
                break
            except PdfEncryptedError:
                password, ok = QInputDialog.getText(
                    self, "パスワード",
                    f"{path.name} はパスワード付きです。\nパスワードを入力してください:",
                    QLineEdit.EchoMode.Password,
                )
                if not ok or not password:
                    return
            except PdfError as e:
                self.window_.show_error(e.message)
                return
        self.window_.view.select_rows(range(at, at + n))
        self.status_label.setText(f"{n} ページを挿入しました")

    # -- 分割（重い可能性があるので別スレッド） -----------------------------
    def _split_selected(self) -> None:
        pl = self.window_.page_list
        rows = self.window_.view.selected_rows()
        if pl is None or not rows:
            return
        self._start_split(pl.export_subset, rows, on_done=lambda out: f"書き出しました: {out.name}")

    def _split_all(self) -> None:
        pl = self.window_.page_list
        if pl is None or len(pl) == 0:
            return
        self._start_split(
            pl.export_split_all,
            on_done=lambda files: f"{len(files)} ファイルに分割しました: {files[0].parent}",
        )

    def _start_split(self, fn, *args, on_done) -> None:
        self._split_running = True
        self._update_enabled()
        self.status_label.setText("処理中…")

        def finished(result):
            self._split_running = False
            self._update_enabled()
            self.status_label.setText(on_done(result))

        def failed(message, exc):
            self._split_running = False
            self._update_enabled()
            self.status_label.setText(f"エラー: {message}")

        self.window_.run_worker(fn, *args, on_finished=finished, on_error=failed)
