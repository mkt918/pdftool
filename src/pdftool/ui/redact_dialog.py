"""墨消しの範囲を指定するダイアログ。

ページを大きく表示し、ドラッグで黒塗りの範囲を描く。右クリックでその範囲を消す。
＜ ＞ や ←/→ でページを移動できる。範囲は PageRef ごとに「見た目のページに対する割合」で持つ
（core/redact.py の Rect）。回転・トリミングでページの見た目が変わると、その PageRef は別物に
なるので、指定は自然に外れる（ずれた位置を黒塗りしないため）。
"""

from __future__ import annotations

from typing import Sequence

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QKeyEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from pdftool.core.document import PdfDocument
from pdftool.core.pageops import PageRef
from pdftool.core.redact import Rect, normalize_rect
from pdftool.utils.worker import pil_to_qimage

VIEW_DPI = 110.0


class RedactCanvas(QWidget):
    """ページ画像の上に範囲を描くウィジェット。画像は縦横比を保って中央に表示する。"""

    rects_changed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self._rects: list[Rect] = []
        self._drag_from: QPointF | None = None
        self._drag_to: QPointF | None = None
        self.setMinimumSize(300, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.CrossCursor)

    # -- データ -----------------------------------------------------------
    def set_page(self, image: QImage | None, rects: Sequence[Rect]) -> None:
        self._pixmap = QPixmap.fromImage(image) if image is not None else None
        self._rects = list(rects)
        self._drag_from = self._drag_to = None
        self.update()

    def rects(self) -> list[Rect]:
        return list(self._rects)

    def clear(self) -> None:
        self._rects.clear()
        self.update()
        self.rects_changed.emit()

    # -- 座標 -------------------------------------------------------------
    def _page_rect(self) -> QRectF:
        if self._pixmap is None or self._pixmap.isNull():
            return QRectF()
        pw, ph = self._pixmap.width(), self._pixmap.height()
        scale = min(self.width() / pw, self.height() / ph)
        w, h = pw * scale, ph * scale
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def _to_frac(self, pos: QPointF) -> tuple[float, float]:
        r = self._page_rect()
        return (pos.x() - r.x()) / r.width(), (pos.y() - r.y()) / r.height()

    def _to_widget(self, rect: Rect) -> QRectF:
        r = self._page_rect()
        l, t, rr, b = rect
        return QRectF(r.x() + l * r.width(), r.y() + t * r.height(), (rr - l) * r.width(), (b - t) * r.height())

    # -- マウス -----------------------------------------------------------
    def mousePressEvent(self, e) -> None:
        if self._page_rect().isEmpty():
            return
        pos = e.position()
        if e.button() == Qt.MouseButton.RightButton:
            for k in range(len(self._rects) - 1, -1, -1):     # 上に描いたものから
                if self._to_widget(self._rects[k]).contains(pos):
                    del self._rects[k]
                    self.update()
                    self.rects_changed.emit()
                    return
            return
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_from = self._drag_to = pos
            self.update()

    def mouseMoveEvent(self, e) -> None:
        if self._drag_from is not None:
            self._drag_to = e.position()
            self.update()

    def mouseReleaseEvent(self, e) -> None:
        if self._drag_from is None or e.button() != Qt.MouseButton.LeftButton:
            return
        rect = normalize_rect((*self._to_frac(self._drag_from), *self._to_frac(e.position())))
        self._drag_from = self._drag_to = None
        if rect is not None:
            self._rects.append(rect)
            self.rects_changed.emit()
        self.update()

    # -- 描画 -------------------------------------------------------------
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#e5e7eb"))
        page = self._page_rect()
        if page.isEmpty():
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "読み込み中…")
            return
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawPixmap(page, self._pixmap, QRectF(self._pixmap.rect()))
        pen = QPen(QColor(239, 68, 68))
        pen.setWidth(2)
        for rect in self._rects:
            r = self._to_widget(rect)
            p.fillRect(r, QColor(0, 0, 0, 200))        # 少し透かして、何を消すのか分かるように
            p.setPen(pen)
            p.drawRect(r)
        if self._drag_from is not None and self._drag_to is not None:
            p.setPen(QPen(QColor(37, 99, 235), 2, Qt.PenStyle.DashLine))
            p.fillRect(QRectF(self._drag_from, self._drag_to).normalized(), QColor(0, 0, 0, 90))
            p.drawRect(QRectF(self._drag_from, self._drag_to).normalized())


class RedactDialog(QDialog):
    """全ページを順に見ながら墨消し範囲を指定する。閉じた後 marks() で結果を受け取る。"""

    def __init__(
        self,
        refs: Sequence[PageRef],
        marks: dict[PageRef, tuple[Rect, ...]],
        passwords: dict | None = None,
        row: int = 0,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("墨消しする範囲を指定")
        self._refs = list(refs)
        self._marks = {r: tuple(v) for r, v in marks.items() if v and r in set(self._refs)}
        self._passwords = passwords or {}
        self._row = max(0, min(len(self._refs) - 1, row))
        self._cache: dict[int, QImage] = {}

        lay = QVBoxLayout(self)
        hint = QLabel("ドラッグで黒塗りする範囲を囲みます。右クリックでその範囲を取り消します。"
                      "墨消ししたページは画像になり、下の文字は完全に消えます。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #666;")
        lay.addWidget(hint)
        self.canvas = RedactCanvas()
        self.canvas.rects_changed.connect(self._store)
        lay.addWidget(self.canvas, 1)

        nav = QHBoxLayout()
        self.prev_btn = QPushButton("＜ 前")
        self.prev_btn.clicked.connect(lambda: self.go(self._row - 1))
        self.next_btn = QPushButton("次 ＞")
        self.next_btn.clicked.connect(lambda: self.go(self._row + 1))
        self.page_label = QLabel()
        self.page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_label.setMinimumWidth(180)
        self.clear_btn = QPushButton("このページの指定を消す")
        self.clear_btn.clicked.connect(self.canvas.clear)
        self.ok_btn = QPushButton("決定")
        self.ok_btn.setDefault(True)
        self.ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("キャンセル")
        cancel_btn.clicked.connect(self.reject)
        nav.addWidget(self.prev_btn)
        nav.addWidget(self.page_label)
        nav.addWidget(self.next_btn)
        nav.addStretch(1)
        nav.addWidget(self.clear_btn)
        nav.addWidget(cancel_btn)
        nav.addWidget(self.ok_btn)
        lay.addLayout(nav)

        self.resize(820, 900)
        self._show_row()

    # -- 結果 -------------------------------------------------------------
    def marks(self) -> dict[PageRef, tuple[Rect, ...]]:
        return dict(self._marks)

    def _store(self) -> None:
        ref = self._refs[self._row]
        rects = tuple(self.canvas.rects())
        if rects:
            self._marks[ref] = rects
        else:
            self._marks.pop(ref, None)
        self._refresh_nav()

    # -- ページ移動 ---------------------------------------------------------
    @property
    def row(self) -> int:
        return self._row

    def go(self, row: int) -> None:
        row = max(0, min(len(self._refs) - 1, row))
        if row != self._row:
            self._row = row
            self._show_row()

    def _render(self, row: int) -> QImage | None:
        img = self._cache.get(row)
        if img is None:
            ref = self._refs[row]
            try:
                with PdfDocument(ref.source_path, password=self._passwords.get(ref.source_path)) as doc:
                    pil = doc.render_page(ref.source_index, dpi=VIEW_DPI, rotation=ref.rotation, crop=ref.crop)
            except Exception:  # noqa: BLE001 — 表示できないページは空のまま（保存時にエラーになる）
                return None
            img = self._cache[row] = pil_to_qimage(pil)
        return img

    def _show_row(self) -> None:
        ref = self._refs[self._row]
        self.canvas.set_page(self._render(self._row), self._marks.get(ref, ()))
        self._refresh_nav()

    def _refresh_nav(self) -> None:
        pages = len(self._marks)
        spots = sum(len(v) for v in self._marks.values())
        here = len(self._marks.get(self._refs[self._row], ()))
        self.page_label.setText(
            f"{self._row + 1} / {len(self._refs)}（このページ {here} か所・全体 {pages} ページ {spots} か所）"
        )
        self.prev_btn.setEnabled(self._row > 0)
        self.next_btn.setEnabled(self._row < len(self._refs) - 1)
        self.clear_btn.setEnabled(here > 0)

    def keyPressEvent(self, e: QKeyEvent) -> None:
        k = e.key()
        if k in (Qt.Key.Key_Left, Qt.Key.Key_PageUp):
            self.go(self._row - 1)
        elif k in (Qt.Key.Key_Right, Qt.Key.Key_PageDown):
            self.go(self._row + 1)
        elif k == Qt.Key.Key_Home:
            self.go(0)
        elif k == Qt.Key.Key_End:
            self.go(len(self._refs) - 1)
        else:
            super().keyPressEvent(e)
