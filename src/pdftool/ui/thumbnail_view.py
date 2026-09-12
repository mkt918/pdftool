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
    QPoint,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QDropEvent, QImage, QPainter, QPixmap, QPixmapCache
from PySide6.QtWidgets import QAbstractItemView, QListView, QWidget

from pdftool.core.pageops import PageList, PageRef
from pdftool.utils.worker import ThumbnailWorker, ThumbRequest

THUMB_SIZES = {"小": 120, "中": 180, "大": 240}
DEFAULT_THUMB_WIDTH = THUMB_SIZES["中"]
PAGE_ASPECT = 1.414  # プレースホルダは A4 縦の比率
QPixmapCache.setCacheLimit(256 * 1024)  # KB。180px なら 1000 ページ以上入る


def _cache_key(ref: PageRef, width: int) -> str:
    return f"{ref.source_path}|{ref.source_index}|{ref.rotation}|{width}"


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
            self._worker.request(ThumbRequest(key, ref.source_path, ref.source_index, ref.rotation, self._width))
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


class ThumbnailView(QListView):
    """IconMode のグリッド。複数選択、D&D 並べ替え、ダブルクリック。"""

    page_activated = Signal(int)       # ダブルクリックされた行
    selection_changed = Signal(list)   # 選択行（昇順）

    def __init__(self, model: ThumbnailModel, parent: QWidget | None = None):
        super().__init__(parent)
        self._model = model
        self.setModel(model)
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
        self.setDropIndicatorShown(True)
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

    # -- D&D 並べ替え -----------------------------------------------------------
    def _drop_row(self, pos: QPoint) -> int:
        """ドロップ位置を「元リスト上の位置 to（この直前に挿入）」に変換する。"""
        idx = self.indexAt(pos)
        if not idx.isValid():
            # 空白部分: 直近のアイテムを探す（同じ行の右端 or 末尾）
            return self._model.rowCount()
        rect = self.visualRect(idx)
        return idx.row() if pos.x() < rect.center().x() else idx.row() + 1

    def dropEvent(self, event: QDropEvent) -> None:
        if event.source() is not self:
            event.ignore()
            return
        rows = self.selected_rows()
        if not rows:
            event.ignore()
            return
        to = self._drop_row(event.position().toPoint())
        n = len(rows)
        self._model.move_pages(rows, to)
        # 移動後の位置を再選択
        insert_at = to - sum(1 for r in rows if r < to)
        self.select_rows(range(insert_at, insert_at + n))
        event.setDropAction(Qt.DropAction.IgnoreAction)   # 既定の removeRows を走らせない
        event.accept()
