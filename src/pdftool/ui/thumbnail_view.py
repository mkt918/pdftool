"""サムネイルグリッド（QListView IconMode）。

役割: 編集対象ページを選ぶ UI。常用ビューアーは目指さない。

構成:
  ThumbnailModel  … core.pageops.PageList を QAbstractListModel として見せる。
                     DecorationRole で QPixmapCache を引き、無ければプレースホルダを返して
                     ThumbnailWorker に描画を依頼する（＝表示範囲だけ遅延描画）。
  ThumbnailView   … QListView。複数選択、D&D 並べ替え（dropEvent で PageList.move を呼ぶ）、
                     ダブルクリックで page_activated(row) を emit。

変更操作（remove/rotate/move/undo/redo）はモデル経由で行うこと。
モデルが PageList を触ったあと、必要な範囲で dataChanged / reset を出す。
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from PySide6.QtCore import (
    QAbstractListModel,
    QItemSelection,
    QItemSelectionModel,
    QModelIndex,
    QLine,
    QPoint,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QDropEvent, QImage, QPainter, QPen, QPixmap, QPixmapCache
from PySide6.QtWidgets import (
    QAbstractItemView,
    QListView,
    QStyle,
    QStyledItemDelegate,
    QWidget,
)

from pdftool.core.images import is_image
from pdftool.core.pageops import PageList, PageRef
from pdftool.utils.worker import ThumbnailWorker, ThumbRequest

THUMB_SIZES = {"小": 120, "中": 180, "大": 240}
DEFAULT_THUMB_WIDTH = THUMB_SIZES["中"]
PAGE_ASPECT = 1.414  # プレースホルダは A4 縦の比率
QPixmapCache.setCacheLimit(256 * 1024)  # KB。180px なら 1000 ページ以上入る


def _cache_key(ref: PageRef, width: int) -> str:
    return f"{ref.source_path}|{ref.source_index}|{ref.rotation}|{ref.crop}|{width}"


def _placeholder(width: int) -> QPixmap:
    pm = QPixmap(width, int(width * PAGE_ASPECT))
    pm.fill(QColor(235, 235, 235))
    p = QPainter(pm)
    p.setPen(QColor(200, 200, 200))
    p.drawRect(0, 0, pm.width() - 1, pm.height() - 1)
    p.end()
    return pm


class ThumbnailModel(QAbstractListModel):
    """PageList をリストモデルとして公開する。"""

    page_list_changed = Signal()   # 変更操作・Undo/Redo・差し替えのたびに emit

    def __init__(self, worker: ThumbnailWorker, parent=None):
        super().__init__(parent)
        self._worker = worker
        self._page_list: PageList | None = None
        self._width = DEFAULT_THUMB_WIDTH
        self._pending: set[str] = set()
        self._failed: dict[str, str] = {}
        self._placeholder = _placeholder(self._width)
        worker.ready.connect(self._on_ready)
        worker.failed.connect(self._on_failed)

    # -- PageList との接続 ---------------------------------------------------
    @property
    def page_list(self) -> PageList | None:
        return self._page_list

    def set_page_list(self, pl: PageList | None) -> None:
        self.beginResetModel()
        self._page_list = pl
        self._pending.clear()
        self._failed.clear()
        self._worker.clear_queue()
        self.endResetModel()
        self.page_list_changed.emit()

    def set_password(self, path: Path, password: str) -> None:
        self._worker.set_password(path, password)

    def thumbnail_width(self) -> int:
        return self._width

    def set_thumbnail_width(self, width: int) -> None:
        if width == self._width:
            return
        self._width = width
        self._placeholder = _placeholder(width)
        self._pending.clear()
        self._worker.clear_queue()
        if self.rowCount():
            self.dataChanged.emit(self.index(0), self.index(self.rowCount() - 1))

    # -- QAbstractListModel ---------------------------------------------------
    def rowCount(self, parent=QModelIndex()) -> int:
        if parent.isValid() or self._page_list is None:
            return 0
        return len(self._page_list)

    def flags(self, index):
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.isValid():
            return base | Qt.ItemFlag.ItemIsDragEnabled
        return base | Qt.ItemFlag.ItemIsDropEnabled

    def supportedDropActions(self):
        return Qt.DropAction.MoveAction

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or self._page_list is None:
            return None
        row = index.row()
        ref = self._page_list[row]
        if role == Qt.ItemDataRole.DisplayRole:
            return str(row + 1)
        if role == Qt.ItemDataRole.DecorationRole:
            return self._pixmap_for(ref)
        if role == Qt.ItemDataRole.ToolTipRole:
            tip = f"{row + 1} ページ目\n{ref.source_path.name} の {ref.source_index + 1} ページ"
            if ref.rotation:
                tip += f"\n回転 {ref.rotation}°"
            err = self._failed.get(_cache_key(ref, self._width))
            if err:
                tip += f"\n{err}"
            return tip
        if role == Qt.ItemDataRole.SizeHintRole:
            return QSize(self._width + 24, int(self._width * PAGE_ASPECT) + 36)
        if role == Qt.ItemDataRole.UserRole:
            return ref
        return None

    def _pixmap_for(self, ref: PageRef) -> QPixmap:
        key = _cache_key(ref, self._width)
        pm = QPixmapCache.find(key)
        if pm is not None and not pm.isNull():
            return pm
        if key not in self._pending and key not in self._failed:
            self._pending.add(key)
            self._worker.request(ThumbRequest(key, ref.source_path, ref.source_index, ref.rotation, self._width, ref.crop))
        return self._placeholder

    def _rows_for_key(self, key: str) -> list[int]:
        if self._page_list is None:
            return []
        return [i for i, r in enumerate(self._page_list.pages) if _cache_key(r, self._width) == key]

    def _on_ready(self, key: str, image: QImage) -> None:
        self._pending.discard(key)
        QPixmapCache.insert(key, QPixmap.fromImage(image))
        for row in self._rows_for_key(key):
            idx = self.index(row)
            self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DecorationRole])

    def _on_failed(self, key: str, message: str) -> None:
        self._pending.discard(key)
        self._failed[key] = message
        for row in self._rows_for_key(key):
            idx = self.index(row)
            self.dataChanged.emit(idx, idx)

    # -- 変更操作（PageList を触るのはここだけ） ----------------------------
    def _after_change(self) -> None:
        self.page_list_changed.emit()

    def _reset(self) -> None:
        self.beginResetModel()
        self.endResetModel()
        self._after_change()

    def remove_pages(self, rows: Iterable[int]) -> None:
        if self._page_list is None:
            return
        self._page_list.remove(rows)
        self._reset()

    def rotate_pages(self, rows: Iterable[int], delta: int) -> None:
        if self._page_list is None:
            return
        rows = sorted(set(rows))
        self._page_list.rotate(rows, delta)
        for r in rows:
            idx = self.index(r)
            self.dataChanged.emit(idx, idx)
        self._after_change()

    def set_crops(self, mapping: dict) -> None:
        """トリミング余白を設定/解除する。選択は保ったまま、該当サムネイルだけ描き直す。"""
        if self._page_list is None or not mapping:
            return
        self._page_list.set_crops(mapping)
        for r in mapping:
            idx = self.index(r)
            self.dataChanged.emit(idx, idx)
        self._after_change()

    def move_pages(self, rows: Iterable[int], to: int) -> None:
        if self._page_list is None:
            return
        self._page_list.move(rows, to)
        self._reset()

    def append_pdf(self, path: Path, password: str | None = None) -> int:
        if self._page_list is None:
            return 0
        n = self._page_list.append_pdf(path, password)
        if password:
            self.set_password(path, password)
        self._reset()
        return n

    def insert_pdf(self, path: Path, at: int, password: str | None = None) -> int:
        if self._page_list is None:
            return 0
        n = self._page_list.insert_pdf(path, at, password)
        if password:
            self.set_password(path, password)
        self._reset()
        return n

    def undo(self) -> bool:
        if self._page_list is None or not self._page_list.undo():
            return False
        self._reset()
        return True

    def redo(self) -> bool:
        if self._page_list is None or not self._page_list.redo():
            return False
        self._reset()
        return True


class ThumbnailDelegate(QStyledItemDelegate):
    """選択状態を分かりやすくする。

    既定の QListView は選択時に背景色を薄く変えるだけで、サムネイル画像自体が
    その背景を覆ってしまい「選択したかどうか」が見た目でほぼ分からなかった
    （2026-09-15 ユーザー指摘）。画像の上から太い枠で囲むことで、画像の内容に
    関係なく選択中だとはっきり分かるようにする。
    """

    BORDER_COLOR = QColor(0, 90, 200)  # 既定のハイライトより濃い、はっきりした青
    BORDER_WIDTH = 4

    def paint(self, painter: QPainter, option, index) -> None:
        super().paint(painter, option, index)
        if option.state & QStyle.StateFlag.State_Selected:
            painter.save()
            pen = QPen(self.BORDER_COLOR)
            pen.setWidth(self.BORDER_WIDTH)
            painter.setPen(pen)
            w = self.BORDER_WIDTH
            rect = option.rect.adjusted(w // 2, w // 2, -(w // 2) - 1, -(w // 2) - 1)
            painter.drawRect(rect)
            painter.restore()


class ThumbnailView(QListView):
    """IconMode のグリッド。複数選択、D&D 並べ替え、ダブルクリック。"""

    page_activated = Signal(int)       # ダブルクリックされた行
    selection_changed = Signal(list)   # 選択行（昇順）
    files_dropped = Signal(list, int, bool)   # (PDF パスのリスト, 挿入位置, Shift押下=開き直し)

    def __init__(self, model: ThumbnailModel, parent: QWidget | None = None):
        super().__init__(parent)
        self._model = model
        self._internal_drag = False
        self._drop_line: QLine | None = None
        self.setModel(model)
        self.setItemDelegate(ThumbnailDelegate(self))
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)   # 位置は自前の dropEvent で決める
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(True)
        self.setSpacing(6)
        self.setUniformItemSizes(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(False)   # 挿入位置は paintEvent で自前描画
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        # 保険: 万一 Qt 側が MoveAction 後処理に入っても removeRows ではなく setData（無効）になる
        self.setDragDropOverwriteMode(True)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setTextElideMode(Qt.TextElideMode.ElideNone)
        self._apply_size()
        self.doubleClicked.connect(lambda idx: self.page_activated.emit(idx.row()))
        self.selectionModel().selectionChanged.connect(lambda *_: self.selection_changed.emit(self.selected_rows()))
        model.modelReset.connect(lambda: self.selection_changed.emit([]))

    # -- ホイールスクロール ------------------------------------------------------
    # IconMode + ScrollPerPixel だと Qt 既定の1ノッチが「サムネイル1行ぶん×3」前後になり、
    # 大きく飛びすぎる。1ノッチ=サムネイル高さの約 1/4 に固定する。
    WHEEL_ROW_FRACTION = 0.25

    def wheelEvent(self, event) -> None:
        bar = self.verticalScrollBar()
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            event.ignore()
            return
        pixel = event.pixelDelta().y()
        if pixel:                                   # タッチパッド等の精密スクロールはそのまま
            bar.setValue(bar.value() - pixel)
        else:
            step = max(24, int(self.gridSize().height() * self.WHEEL_ROW_FRACTION))
            bar.setValue(bar.value() - round(event.angleDelta().y() / 120 * step))
        event.accept()

    # -- サイズ -----------------------------------------------------------------
    def _apply_size(self) -> None:
        w = self._model.thumbnail_width()
        self.setIconSize(QSize(w, int(w * PAGE_ASPECT)))
        self.setGridSize(QSize(w + 24, int(w * PAGE_ASPECT) + 36))

    def set_thumbnail_width(self, width: int) -> None:
        self._model.set_thumbnail_width(width)
        self._apply_size()
        self.reset()

    # -- 選択 -------------------------------------------------------------------
    def selected_rows(self) -> list[int]:
        return sorted({i.row() for i in self.selectionModel().selectedIndexes()})

    def select_rows(self, rows: Iterable[int]) -> None:
        sm = self.selectionModel()
        sm.clearSelection()
        sel = QItemSelection()
        for r in rows:
            idx = self._model.index(r)
            sel.select(idx, idx)
        sm.select(sel, QItemSelectionModel.SelectionFlag.Select)

    # -- D&D（並べ替え / PDF ファイルのドロップ結合） ---------------------------
    # 内部ドラッグ（サムネイルの並べ替え）は startDrag の間だけ _internal_drag が立つ。
    # 外部ドラッグ（エクスプローラーからの PDF）は mimeData の URL で判定する。
    # どちらも Qt 既定の受け入れ判定（アイテム上のドロップは拒否される等）に頼らず、
    # drag*Event で自前で accept し、挿入位置を縦線で見せる。

    def startDrag(self, supportedActions) -> None:
        self._internal_drag = True
        try:
            super().startDrag(supportedActions)
        finally:
            self._internal_drag = False
            self._clear_drop_hint()

    @staticmethod
    def _pdf_paths(mime) -> list[Path]:
        """ドロップされたファイルのうち、PDF と画像（JPG/PNG…）だけ。"""
        if mime is None or not mime.hasUrls():
            return []
        paths = [Path(u.toLocalFile()) for u in mime.urls() if u.isLocalFile()]
        return [p for p in paths if p.suffix.lower() == ".pdf" or is_image(p)]

    def _drop_target(self, pos: QPoint) -> tuple[int, QLine | None]:
        """ドロップ位置 → (挿入位置 to, 表示する縦線)。to は「元リストの to 番目の直前」。"""
        n = self._model.rowCount()
        if n == 0:
            return 0, None
        x, y = pos.x(), pos.y()
        rects = [self.visualRect(self._model.index(r)) for r in range(n)]
        band = [r for r in range(n) if rects[r].top() <= y <= rects[r].bottom()]
        if not band:
            if y > rects[-1].bottom():
                band = [n - 1]
                x = rects[-1].right() + 1          # 最下行より下 → 末尾
            elif y < rects[0].top():
                band = [0]
                x = rects[0].left() - 1            # 最上行より上 → 先頭
            else:                                  # 行間の隙間 → いちばん近い行
                nearest = min(range(n), key=lambda r: abs(rects[r].center().y() - y))
                band = [r for r in range(n) if rects[r].top() == rects[nearest].top()]
        for r in band:
            rc = rects[r]
            if x < rc.center().x():
                return r, QLine(rc.left(), rc.top(), rc.left(), rc.bottom())
        rc = rects[band[-1]]
        return band[-1] + 1, QLine(rc.right(), rc.top(), rc.right(), rc.bottom())

    def _drop_row(self, pos: QPoint) -> int:
        return self._drop_target(pos)[0]

    def _set_drop_hint(self, line: QLine | None) -> None:
        if line != self._drop_line:
            self._drop_line = line
            self.viewport().update()

    def _clear_drop_hint(self) -> None:
        self._set_drop_hint(None)

    def _autoscroll_for_drag(self, pos: QPoint) -> None:
        bar, margin = self.verticalScrollBar(), 36
        if pos.y() < margin:
            bar.setValue(bar.value() - 20)
        elif pos.y() > self.viewport().height() - margin:
            bar.setValue(bar.value() + 20)

    def _accepts(self, event) -> bool:
        return self._internal_drag or bool(self._pdf_paths(event.mimeData()))

    def dragEnterEvent(self, event) -> None:
        if self._accepts(event):
            event.setDropAction(Qt.DropAction.MoveAction if self._internal_drag else Qt.DropAction.CopyAction)
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if not self._accepts(event):
            event.ignore()
            self._clear_drop_hint()
            return
        pos = event.position().toPoint()
        self._set_drop_hint(self._drop_target(pos)[1])
        self._autoscroll_for_drag(pos)
        event.setDropAction(Qt.DropAction.MoveAction if self._internal_drag else Qt.DropAction.CopyAction)
        event.accept()

    def dragLeaveEvent(self, event) -> None:
        self._clear_drop_hint()
        super().dragLeaveEvent(event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self._drop_line is not None:
            p = QPainter(self.viewport())
            pen = QPen(ThumbnailDelegate.BORDER_COLOR)
            pen.setWidth(4)
            p.setPen(pen)
            p.drawLine(self._drop_line)
            p.end()

    def dropEvent(self, event: QDropEvent) -> None:
        pos = event.position().toPoint()
        to = self._drop_row(pos)
        self._clear_drop_hint()

        paths = self._pdf_paths(event.mimeData())
        if paths and not self._internal_drag:
            shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            self.files_dropped.emit(paths, to, shift)
            return

        if not self._internal_drag:
            event.ignore()
            return
        rows = self.selected_rows()
        if not rows:
            event.ignore()
            return
        n = len(rows)
        self._model.move_pages(rows, to)
        insert_at = to - sum(1 for r in rows if r < to)
        self.select_rows(range(insert_at, insert_at + n))
        event.setDropAction(Qt.DropAction.IgnoreAction)   # 既定の removeRows を走らせない
        event.accept()
