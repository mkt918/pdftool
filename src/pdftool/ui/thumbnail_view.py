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
    QRect,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QDropEvent, QImage, QPainter, QPen, QPixmap, QPixmapCache
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QListView,
    QStyle,
    QStyledItemDelegate,
    QWidget,
)

from pdftool.core.images import is_image
from pdftool.core.pageops import PageList, PageRef
from pdftool.utils.worker import ThumbnailWorker, ThumbRequest

THUMB_SIZES = {"小": 120, "中": 180, "大": 240}
DEFAULT_THUMB_WIDTH = THUMB_SIZES["大"]   # 基準サイズ（起動時は 3 ページが横に並ぶ幅に合わせる）
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

    BORDER_COLOR = QColor(37, 99, 235)  # theme.ACCENT。既定のハイライトより濃い、はっきりした青
    BORDER_WIDTH = 4

    def __init__(self, parent=None):
        super().__init__(parent)
        self.check_mode = False   # True: 選択中のページの右上にチェックマークを描く（画像書き出し用）

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
            if self.check_mode:
                d = 26
                badge = QRect(option.rect.right() - d - 8, option.rect.top() + 8, d, d)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setPen(QPen(QColor(255, 255, 255), 2))
                painter.setBrush(self.BORDER_COLOR)
                painter.drawEllipse(badge)
                pen = QPen(QColor(255, 255, 255), 3)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                painter.setPen(pen)
                x, y = badge.left(), badge.top()
                painter.drawPolyline([QPoint(x + 7, y + 14), QPoint(x + 11, y + 18), QPoint(x + 19, y + 9)])
            painter.restore()


class ThumbnailView(QListView):
    """IconMode のグリッド。複数選択、ドラッグで並べ替え、PDF/画像ファイルのドロップ、ダブルクリック。"""

    page_activated = Signal(int)       # ダブルクリックされた行
    selection_changed = Signal(list)   # 選択行（昇順）
    files_dropped = Signal(list, int, bool)   # (PDF パスのリスト, 挿入位置, Shift押下=開き直し)
    context_requested = Signal(QPoint)        # 右クリック（グローバル座標）。右クリック先は選択済みにしてから emit

    def contextMenuEvent(self, e) -> None:
        idx = self.indexAt(e.pos())
        if idx.isValid() and idx.row() not in self.selected_rows():
            self.select_rows([idx.row()])      # 未選択のページを右クリック → そのページだけ選択
        if self.model().rowCount():
            self.context_requested.emit(e.globalPos())
        e.accept()

    def __init__(self, model: ThumbnailModel, parent: QWidget | None = None):
        super().__init__(parent)
        self._model = model
        self._drop_line: QLine | None = None
        # 並べ替え（マウスで自前処理。Qt 標準のドラッグは使わない）
        self._press_pos: QPoint | None = None
        self._press_row = -1
        self._reordering = False
        self._reorder_rows: list[int] = []
        self._reorder_pos = QPoint()
        self._autoscroll_timer = QTimer(self)
        self._autoscroll_timer.setInterval(30)
        self._autoscroll_timer.timeout.connect(self._tick_autoscroll)
        self.setModel(model)
        self._delegate = ThumbnailDelegate(self)
        self.setItemDelegate(self._delegate)
        self._check_mode = False
        self._toggle_off_on_release = False
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)   # 位置は自前の dropEvent で決める
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(True)
        self.setSpacing(6)
        self.setUniformItemSizes(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        # ページの並べ替えは Qt 標準のドラッグ(Windows では OLE)を使わず、マウス操作を自前で処理する
        # （標準のドラッグだと実機で「×」になり落とせなかった）。Qt の D&D は外部ファイルの受け取り専用。
        self.setDragEnabled(False)
        self.setAcceptDrops(True)
        # QAbstractScrollArea ではドロップを実際に受けるのは viewport。view 側だけ True だと viewport が
        # False のままになり、外部ファイルのドラッグが一覧に届かず親ウィンドウへ素通りした（実機で判明）
        self.viewport().setAcceptDrops(True)
        self.setDropIndicatorShown(False)   # 挿入位置は paintEvent で自前描画
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
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

    # -- PDF・画像ファイルのドロップ（外部ドラッグ。Qt 標準の D&D） -------------------
    # 受け入れ判定は Qt 既定（アイテム上のドロップを拒否する等）に頼らず自前で accept し、
    # 挿入位置を縦線で見せる。ドロップ動作は必ず Copy（Move だとエクスプローラーが元ファイルを消す）。

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

    def dragEnterEvent(self, event) -> None:
        if self._pdf_paths(event.mimeData()):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if not self._pdf_paths(event.mimeData()):
            event.ignore()
            self._clear_drop_hint()
            return
        pos = event.position().toPoint()
        self._set_drop_hint(self._drop_target(pos)[1])
        self._autoscroll_for_drag(pos)
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()

    def dragLeaveEvent(self, event) -> None:
        self._clear_drop_hint()
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        to = self._drop_row(event.position().toPoint())
        self._clear_drop_hint()
        paths = self._pdf_paths(event.mimeData())
        if not paths:
            event.ignore()
            return
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        self.files_dropped.emit(paths, to, shift)

    # -- ページの並べ替え（マウスで自前処理） ------------------------------------------
    # 押す → しきい値以上動かす → 挿入位置の線と「Nページ」の目印が出る → 離すと移動。Esc で取り消し。
    # 選択済みのページを押してドラッグすると、選択中のページがまとめて動く。

    @property
    def reordering(self) -> bool:
        return self._reordering

    # -- チェックモード（画像書き出しタブ） ---------------------------------------------
    # 普通にクリックするだけで「選択に追加」し、他のページをクリックしても選択を保つ。
    # 選択済みのページをクリックして離すと選択から外れる。選択済みのページからドラッグすると並べ替え。
    @property
    def check_mode(self) -> bool:
        return self._check_mode

    def set_check_mode(self, on: bool) -> None:
        self._check_mode = on
        self._delegate.check_mode = on
        self.viewport().update()

    def mousePressEvent(self, e) -> None:
        self._press_pos = None
        self._toggle_off_on_release = False
        if e.button() == Qt.MouseButton.LeftButton:
            idx = self.indexAt(e.position().toPoint())
            if idx.isValid():
                self._press_pos = e.position().toPoint()
                self._press_row = idx.row()
                plain = not (e.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier))
                if self._check_mode and plain:
                    if idx.row() in self.selected_rows():
                        self._toggle_off_on_release = True      # 動かさずに離したら外す
                    else:
                        self.selectionModel().select(idx, QItemSelectionModel.SelectionFlag.Select)
                    e.accept()
                    return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:
        pos = e.position().toPoint()
        if self._reordering:
            self._reorder_pos = pos
            self._update_reorder()
            e.accept()
            return
        if self._press_pos is not None and (e.buttons() & Qt.MouseButton.LeftButton):
            if (pos - self._press_pos).manhattanLength() >= QApplication.startDragDistance():
                if self._press_row in self.selected_rows():
                    self._begin_reorder(pos)
                    e.accept()
                    return
                self._press_pos = None          # 選択されていないページからは動かさない（普通の範囲選択へ）
            else:
                e.accept()                      # 小さな手ぶれでは選択範囲を広げない
                return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        if self._reordering and e.button() == Qt.MouseButton.LeftButton:
            self._end_reorder(apply=True)
            e.accept()
            return
        if self._toggle_off_on_release and e.button() == Qt.MouseButton.LeftButton:
            idx = self.indexAt(e.position().toPoint())
            if idx.isValid() and idx.row() == self._press_row:
                self.selectionModel().select(idx, QItemSelectionModel.SelectionFlag.Deselect)
            self._toggle_off_on_release = False
            self._press_pos = None
            e.accept()
            return
        self._press_pos = None
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e) -> None:
        if self._reordering and e.key() == Qt.Key.Key_Escape:
            self._end_reorder(apply=False)
            e.accept()
            return
        super().keyPressEvent(e)

    def _begin_reorder(self, pos: QPoint) -> None:
        self._toggle_off_on_release = False
        self._reordering = True
        self._reorder_rows = self.selected_rows()
        self._reorder_pos = pos
        self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
        self._autoscroll_timer.start()
        self._update_reorder()

    def _update_reorder(self) -> None:
        self._set_drop_hint(self._drop_target(self._reorder_pos)[1])
        self.viewport().update()

    def _tick_autoscroll(self) -> None:
        """カーソルが上下端にある間は、動かさなくてもスクロールし続ける。"""
        if not self._reordering:
            self._autoscroll_timer.stop()
            return
        self._autoscroll_for_drag(self._reorder_pos)
        self._update_reorder()

    def _end_reorder(self, apply: bool) -> None:
        if not self._reordering:
            return
        self._reordering = False
        self._autoscroll_timer.stop()
        self.viewport().unsetCursor()
        rows = self._reorder_rows
        to = self._drop_target(self._reorder_pos)[0]
        self._clear_drop_hint()
        self._press_pos = None
        # super().mouseReleaseEvent を通さないので、Qt 側の「範囲選択中」状態を自分で戻す
        self.setState(QAbstractItemView.State.NoState)
        self.viewport().update()
        if not apply or not rows:
            return
        n = len(rows)
        self._model.move_pages(rows, to)
        insert_at = to - sum(1 for r in rows if r < to)
        self.select_rows(range(insert_at, insert_at + n))

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self._drop_line is None and not self._reordering:
            return
        p = QPainter(self.viewport())
        if self._drop_line is not None:
            pen = QPen(ThumbnailDelegate.BORDER_COLOR)
            pen.setWidth(4)
            p.setPen(pen)
            p.drawLine(self._drop_line)
        if self._reordering:
            text = f"{len(self._reorder_rows)} ページを移動"
            fm = p.fontMetrics()
            w, h = fm.horizontalAdvance(text) + 20, fm.height() + 12
            rect = QRect(self._reorder_pos.x() + 14, self._reorder_pos.y() + 14, w, h)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 90, 200, 230))
            p.drawRoundedRect(rect, 6, 6)
            p.setPen(QColor(255, 255, 255))
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        p.end()
