"""1 ページの拡大表示ダイアログ（ダブルクリック・仕上げのプレビューで使う）。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QDialog, QLabel, QScrollArea, QVBoxLayout, QWidget


class PagePreviewDialog(QDialog):
    """1 ページの拡大表示（スクロールのみ。ズームは作らない）。"""

    def __init__(self, image: QImage, title: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        lbl = QLabel()
        lbl.setPixmap(QPixmap.fromImage(image))
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        area = QScrollArea()
        area.setWidget(lbl)
        area.setWidgetResizable(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(area)
        self.resize(min(image.width() + 40, 1000), min(image.height() + 40, 900))
