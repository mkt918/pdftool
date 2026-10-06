"""1 ページの拡大表示ダイアログ（ダブルクリック・仕上げのプレビューで使う）。

複数ページあるときは、下に「＜ 3 / 12 ＞」のボタンが出て、前後のページへ移動できる
（キーボードの ←/→、PageUp/PageDown、Home/End も使える）。
ページの画像は `request(row, on_ready)` で別スレッドから取り寄せ、一度見たページは覚えておく。
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QImage, QKeyEvent, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

RequestFn = Callable[[int, Callable[[QImage], None]], None]


class PagePreviewDialog(QDialog):
    """1 ページの拡大表示。count > 1 かつ request を渡すと、前後ページへの移動ボタンが付く。"""

    def __init__(
        self,
        image: QImage,
        title: str,
        parent: QWidget | None = None,
        *,
        count: int = 1,
        row: int = 0,
        request: RequestFn | None = None,
        title_fn: Callable[[int], str] | None = None,
    ):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._count = max(1, count)
        self._row = row
        self._request = request
        self._title_fn = title_fn
        self._cache: dict[int, QImage] = {row: image}
        self._alive = True
        self.finished.connect(lambda *_: setattr(self, "_alive", False))

        self._label = QLabel()
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._area = QScrollArea()
        self._area.setWidget(self._label)
        self._area.setWidgetResizable(True)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._area, 1)

        self.nav_bar = QWidget()
        nav = QHBoxLayout(self.nav_bar)
        nav.setContentsMargins(8, 6, 8, 6)
        big = QFont(self.font())
        big.setPointSize(big.pointSize() + 6)
        big.setBold(True)
        self.prev_btn = QPushButton("＜")
        self.next_btn = QPushButton("＞")
        for b, tip in ((self.prev_btn, "前のページ（←）"), (self.next_btn, "次のページ（→）")):
            b.setFont(big)
            b.setMinimumSize(90, 40)
            b.setToolTip(tip)
        self.prev_btn.clicked.connect(lambda: self.go(self._row - 1))
        self.next_btn.clicked.connect(lambda: self.go(self._row + 1))
        self.page_label = QLabel()
        self.page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_label.setMinimumWidth(120)
        nav.addStretch(1)
        nav.addWidget(self.prev_btn)
        nav.addWidget(self.page_label)
        nav.addWidget(self.next_btn)
        nav.addStretch(1)
        lay.addWidget(self.nav_bar)
        self.nav_bar.setVisible(self._count > 1 and request is not None)

        self._show_image(image)
        self._refresh_nav()
        self.setWindowTitle(self._title_for(row, title))
        self.resize(min(image.width() + 40, 1000), min(image.height() + 40 + 56, 900))

    # -- 内部 ---------------------------------------------------------------
    def _title_for(self, row: int, fallback: str = "") -> str:
        return self._title_fn(row) if self._title_fn else fallback

    def _show_image(self, image: QImage) -> None:
        self._label.setPixmap(QPixmap.fromImage(image))

    def _refresh_nav(self, loading: bool = False) -> None:
        self.page_label.setText(f"{self._row + 1} / {self._count}" + ("  読み込み中…" if loading else ""))
        self.prev_btn.setEnabled(self._row > 0)
        self.next_btn.setEnabled(self._row < self._count - 1)

    # -- ページ移動 -----------------------------------------------------------
    @property
    def row(self) -> int:
        return self._row

    def go(self, row: int) -> None:
        """row 番目のページへ移動する（範囲外は端に丸める）。"""
        if self._request is None:
            return
        row = max(0, min(self._count - 1, row))
        if row == self._row:
            return
        self._row = row
        self.setWindowTitle(self._title_for(row))
        cached = self._cache.get(row)
        if cached is not None:
            self._show_image(cached)
            self._refresh_nav()
            return
        self._refresh_nav(loading=True)
        self._request(row, lambda img, r=row: self._on_ready(r, img))

    def _on_ready(self, row: int, image: QImage) -> None:
        if not self._alive:          # 読み込み中に閉じられた
            return
        self._cache[row] = image
        if row == self._row:
            self._show_image(image)
            self._refresh_nav()

    def keyPressEvent(self, e: QKeyEvent) -> None:
        k = e.key()
        if self._request is not None and self._count > 1:
            if k in (Qt.Key.Key_Left, Qt.Key.Key_PageUp):
                self.go(self._row - 1)
                return
            if k in (Qt.Key.Key_Right, Qt.Key.Key_PageDown):
                self.go(self._row + 1)
                return
            if k == Qt.Key.Key_Home:
                self.go(0)
                return
            if k == Qt.Key.Key_End:
                self.go(self._count - 1)
                return
        super().keyPressEvent(e)
