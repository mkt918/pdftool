"""ビューアモード: 左のサムネイル一覧の代わりに、ページを大きく読むための表示。

- 「見開き」: 本のように 2 ページを並べる（「表紙を単独」にすると 1 ページ目だけ右側に出る）
- 「1ページ」: 1 ページずつ表示
- ←/→、PageUp/PageDown、Home/End、＜ ＞ ボタンでめくる

画像は `request(ref, on_ready)` で別スレッドから取り寄せる（回転・トリミング反映済み）。
一度見たページは覚えておき、次の見開きは先読みする。
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Callable, Sequence

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QColor, QImage, QKeyEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pdftool.core.pageops import PageRef

RequestFn = Callable[[PageRef, Callable[[QImage], None]], None]
CACHE_LIMIT = 16


class _PageView(QWidget):
    """1 ページ分の描画領域。領域に収まる最大サイズで縦横比を保って描く。align で綴じ目側に寄せる。"""

    def __init__(self, align: Qt.AlignmentFlag, parent: QWidget | None = None):
        super().__init__(parent)
        self._align = align
        self._image: QImage | None = None
        self._empty = True       # True: 何も表示しない（見開きの空き側）
        self.setMinimumSize(80, 80)

    def show_image(self, image: QImage | None, empty: bool = False) -> None:
        self._image = image
        self._empty = empty
        self.update()

    def paintEvent(self, _e) -> None:
        if self._empty:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        area = self.rect().adjusted(6, 6, -6, -6)
        if self._image is None or self._image.isNull():
            p.setPen(QColor(150, 150, 150))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "読み込み中…")
            return
        size = self._image.size().scaled(area.size(), Qt.AspectRatioMode.KeepAspectRatio)
        if self._align & Qt.AlignmentFlag.AlignRight:
            x = area.right() - size.width() + 1
        elif self._align & Qt.AlignmentFlag.AlignLeft:
            x = area.left()
        else:
            x = area.left() + (area.width() - size.width()) // 2
        rect = QRect(x, area.top() + (area.height() - size.height()) // 2, size.width(), size.height())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255))
        p.drawRect(rect)
        p.drawImage(rect, self._image)
        p.setPen(QPen(QColor(205, 208, 214)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(rect.adjusted(0, 0, -1, -1))


class ViewerWidget(QWidget):
    row_changed = Signal(list)     # 表示中のページ（0 始まり）。サムネイル側の選択と同期するため
    exit_requested = Signal()

    def __init__(self, request: RequestFn, parent: QWidget | None = None):
        super().__init__(parent)
        self._request = request
        self._refs: list[PageRef] = []
        self._row = 0
        self._cache: OrderedDict[PageRef, QImage] = OrderedDict()
        self._pending: set[PageRef] = set()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        bar = QWidget()
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(10, 6, 10, 6)
        self.exit_btn = QPushButton("一覧に戻る")
        self.exit_btn.clicked.connect(self.exit_requested)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("見開き", True)
        self.mode_combo.addItem("1ページ", False)
        self.cover_check = QCheckBox("表紙を単独表示")
        self.cover_check.setChecked(True)
        self.prev_btn = QPushButton("＜")
        self.next_btn = QPushButton("＞")
        for b, tip in ((self.prev_btn, "前へ（←）"), (self.next_btn, "次へ（→）")):
            b.setMinimumWidth(56)
            b.setToolTip(tip)
        self.page_label = QLabel()
        self.page_label.setMinimumWidth(110)
        self.page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bl.addWidget(self.exit_btn)
        bl.addSpacing(12)
        bl.addWidget(self.mode_combo)
        bl.addWidget(self.cover_check)
        bl.addStretch(1)
        bl.addWidget(self.prev_btn)
        bl.addWidget(self.page_label)
        bl.addWidget(self.next_btn)
        lay.addWidget(bar)

        self.left = _PageView(Qt.AlignmentFlag.AlignRight)
        self.right = _PageView(Qt.AlignmentFlag.AlignLeft)
        stage = QWidget()
        stage.setStyleSheet("background: #e5e7eb;")
        sl = QHBoxLayout(stage)
        sl.setContentsMargins(8, 8, 8, 8)
        sl.setSpacing(0)                  # 綴じ目で 2 ページが接する
        sl.addWidget(self.left, 1)
        sl.addWidget(self.right, 1)
        lay.addWidget(stage, 1)

        self.mode_combo.currentIndexChanged.connect(lambda _: self._render())
        self.cover_check.toggled.connect(lambda _: self._render())
        self.prev_btn.clicked.connect(lambda: self.step(-1))
        self.next_btn.clicked.connect(lambda: self.step(1))
        for w in (self.mode_combo, self.cover_check, self.prev_btn, self.next_btn, self.exit_btn):
            w.setFocusPolicy(Qt.FocusPolicy.NoFocus)    # 矢印キーを常にビューアで受ける

    # -- 状態 -----------------------------------------------------------------
    @property
    def spread(self) -> bool:
        return bool(self.mode_combo.currentData())

    @property
    def cover_alone(self) -> bool:
        return self.cover_check.isChecked()

    @property
    def row(self) -> int:
        return self._row

    def set_spread(self, spread: bool) -> None:
        self.mode_combo.setCurrentIndex(0 if spread else 1)

    def set_refs(self, refs: Sequence[PageRef], row: int | None = None) -> None:
        """ページ構成を差し替える（編集のたびに呼ばれる）。row を省略すると今のページ位置を保つ。"""
        self._refs = list(refs)
        keep = set(self._refs)
        for k in [k for k in self._cache if k not in keep]:
            del self._cache[k]
        self._row = max(0, min(len(self._refs) - 1, self._row if row is None else row))
        self._render()

    # -- 見開きの組み立て ---------------------------------------------------------
    def _spread_index(self, row: int) -> int:
        if not self.spread:
            return row
        return (row + 1) // 2 if self.cover_alone else row // 2

    def _spread_count(self) -> int:
        n = len(self._refs)
        if not self.spread:
            return n
        return (n + 1) // 2 + (1 if self.cover_alone and n % 2 == 0 else 0)

    def _pages_of(self, s: int) -> tuple[int | None, int | None]:
        """見開き s の (左ページ, 右ページ)。無い側は None。"""
        n = len(self._refs)
        if not self.spread:
            return None, (s if 0 <= s < n else None)
        if self.cover_alone:
            pair = (None, 0) if s == 0 else (2 * s - 1, 2 * s)
        else:
            pair = (2 * s, 2 * s + 1)
        return tuple(p if p is not None and 0 <= p < n else None for p in pair)  # type: ignore[return-value]

    def visible_rows(self) -> list[int]:
        return [p for p in self._pages_of(self._spread_index(self._row)) if p is not None]

    # -- めくる ---------------------------------------------------------------
    def step(self, delta: int) -> None:
        if not self._refs:
            return
        s = max(0, min(self._spread_count() - 1, self._spread_index(self._row) + delta))
        rows = [p for p in self._pages_of(s) if p is not None]
        if rows:
            self._row = min(rows)
        self._render()

    def go(self, row: int) -> None:
        if not self._refs:
            return
        self._row = max(0, min(len(self._refs) - 1, row))
        self._render()

    def keyPressEvent(self, e: QKeyEvent) -> None:
        k = e.key()
        if k in (Qt.Key.Key_Left, Qt.Key.Key_PageUp):
            self.step(-1)
        elif k in (Qt.Key.Key_Right, Qt.Key.Key_PageDown, Qt.Key.Key_Space):
            self.step(1)
        elif k == Qt.Key.Key_Home:
            self.go(0)
        elif k == Qt.Key.Key_End:
            self.go(len(self._refs) - 1)
        elif k == Qt.Key.Key_Escape:
            self.exit_requested.emit()
        else:
            super().keyPressEvent(e)

    # -- 画像の取得 ---------------------------------------------------------------
    def _ensure(self, row: int | None) -> QImage | None:
        if row is None or not 0 <= row < len(self._refs):
            return None
        ref = self._refs[row]
        img = self._cache.get(ref)
        if img is not None:
            self._cache.move_to_end(ref)
            return img
        if ref not in self._pending:
            self._pending.add(ref)
            self._request(ref, lambda im, r=ref: self._on_ready(r, im))
        return None

    def _on_ready(self, ref: PageRef, image: QImage) -> None:
        self._pending.discard(ref)
        if ref not in self._refs:       # 読み込み中に消された
            return
        self._cache[ref] = image
        while len(self._cache) > CACHE_LIMIT:
            self._cache.popitem(last=False)
        self._paint_pages()

    def _render(self) -> None:
        n = len(self._refs)
        s = self._spread_index(self._row)
        left, right = self._pages_of(s)
        rows = [p for p in (left, right) if p is not None]
        if self.spread and rows:
            self._row = min(rows)             # めくった／設定を変えたとき、見開きの先頭ページを基準にする
        self.page_label.setText(
            "" if not n else (f"{rows[0] + 1} - {rows[-1] + 1} / {n}" if len(rows) == 2 else f"{rows[0] + 1} / {n}")
        )
        self.prev_btn.setEnabled(s > 0)
        self.next_btn.setEnabled(s < self._spread_count() - 1)
        self._paint_pages()
        for p in self._pages_of(s + 1):         # 次の見開きを先読み
            self._ensure(p)
        self.row_changed.emit(rows)

    def _paint_pages(self) -> None:
        left, right = self._pages_of(self._spread_index(self._row))
        if self.spread:
            self.right._align = Qt.AlignmentFlag.AlignLeft
            self.left.setVisible(True)
            self.left.show_image(self._ensure(left), empty=left is None)
        else:
            self.right._align = Qt.AlignmentFlag.AlignHCenter
            self.left.setVisible(False)
        self.right.show_image(self._ensure(right), empty=right is None)
