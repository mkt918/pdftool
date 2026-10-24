"""ビューアモード: 左のサムネイル一覧の代わりに、ページを大きく読むための表示。

- 「見開き」: 本のように 2 ページを並べる（「1ページずらす」で組み合わせを変えられる。オンだと 1 ページ目だけ右側に出る）
- 「1ページ」: 1 ページずつ表示
- ←/→、PageUp/PageDown、Home/End、＜ ＞ ボタンでめくる（拡大していないときはホイールでもめくれる）
- 拡大・移動: Ctrl+ホイール／＋－キー／ボタン／ダブルクリックで拡大（カーソル位置が動かない）、ドラッグ・ホイール・
  矢印キーで移動、0 キーか「全体」で全体表示に戻る。中ボタンのドラッグはペン選択中でも移動できる。
  大きく拡大すると、見ているページだけ高解像度で描き直す
- 書き込み: ペン・蛍光ペン・消しゴム（ui/ink_tools.py）。書き込みはページの上の別レイヤーで、
  「書き込み: 表示中／非表示」（L）で一瞬で切り替えられる。線は PageRef ごとに持ち（回転・トリミングで
  ページの見た目が変わると別ページ扱い＝その書き込みは出ない）、保存は「仕上げ」タブが担当する。
  ドラッグの終わりで動かさず押し続けると、始点からの直線になる

画像は `request(ref, on_ready)` で別スレッドから取り寄せる（回転・トリミング反映済み）。
一度見たページは覚えておき、次の見開きは先読みする。
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Callable, Sequence

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QKeyEvent, QMouseEvent, QPainter, QPen, QWheelEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pdftool.core.ink import Stroke, distance_to_stroke
from pdftool.core.pageops import PageRef
from pdftool.ui.ink_tools import ERASER, HOLD_MS, NONE, InkState, InkToolBar, paint_stroke

RequestFn = Callable[..., None]      # (ref, on_ready) か (ref, on_ready, dpi)
CACHE_LIMIT = 16
HI_DPI = 300           # 拡大して見るときに描き直す解像度
HI_ZOOM = 2.0          # この倍率以上で高解像度を使う
HI_LIMIT = 4
ZOOM_STEP = 1.25


class _PageView(QWidget):
    """1 ページ分の描画領域。領域に収まる最大サイズで縦横比を保って描く。align で綴じ目側に寄せる。

    書き込みツールが選ばれているときは、ここでマウスの動きを線にする（座標はページに対する割合）。
    """

    stroke_done = Signal(object, object)       # (このビュー, Stroke)
    erased = Signal(object, int)               # (このビュー, 消した線の番号)

    def __init__(self, align: Qt.AlignmentFlag, ink: InkState, parent: QWidget | None = None):
        super().__init__(parent)
        self._align = align
        self._ink = ink
        self.ref: PageRef | None = None
        self.strokes: list[Stroke] = []
        self._image: QImage | None = None
        self._empty = True       # True: 何も表示しない（見開きの空き側）
        self._points: list[tuple[float, float]] | None = None      # 描いている途中の線
        self._straight = False
        self._hold = QTimer(self)
        self._hold.setSingleShot(True)
        self._hold.setInterval(HOLD_MS)
        self._hold.timeout.connect(self._on_hold)
        self.setMinimumSize(80, 80)

    def show_image(self, image: QImage | None, empty: bool = False) -> None:
        self._image = image
        self._empty = empty
        self.update()

    def set_page(self, ref: PageRef | None, strokes: list[Stroke]) -> None:
        if ref != self.ref:
            self._cancel_stroke()
        self.ref = ref
        self.strokes = strokes
        self.update()

    def has_page(self) -> bool:
        return not self._empty

    def image_rect(self) -> QRect | None:
        """ページ画像を表示している範囲。画像が無ければ None。"""
        return self.fit_in(self.rect())

    def fit_in(self, box: QRect) -> QRect | None:
        """box（このビューの大きさだったら）の中でページ画像が占める範囲。"""
        if self._empty or self._image is None or self._image.isNull():
            return None
        area = box.adjusted(6, 6, -6, -6)
        size = self._image.size().scaled(area.size(), Qt.AspectRatioMode.KeepAspectRatio)
        if self._align & Qt.AlignmentFlag.AlignRight:
            x = area.right() - size.width() + 1
        elif self._align & Qt.AlignmentFlag.AlignLeft:
            x = area.left()
        else:
            x = area.left() + (area.width() - size.width()) // 2
        return QRect(x, area.top() + (area.height() - size.height()) // 2, size.width(), size.height())

    def paintEvent(self, _e) -> None:
        if self._empty:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        rect = self.image_rect()
        if rect is None:
            p.setPen(QColor(150, 150, 150))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "読み込み中…")
            return
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255))
        p.drawRect(rect)
        p.drawImage(rect, self._image)
        if self._ink.visible:                       # 書き込みレイヤー（非表示にしても内容は残る）
            p.setClipRect(rect)
            for st in self.strokes:
                paint_stroke(p, st, rect)
            if self._points:
                paint_stroke(p, self._ink.new_stroke(self._points), rect)
            p.setClipping(False)
        p.setPen(QPen(QColor(205, 208, 214)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(rect.adjusted(0, 0, -1, -1))

    # -- 書き込み ---------------------------------------------------------------
    def update_cursor(self) -> None:
        tool = self._ink.tool
        if tool == NONE:
            self.unsetCursor()                     # 親（ステージ）の手のひら／矢印を使う
        else:
            self.setCursor(Qt.CursorShape.PointingHandCursor if tool == ERASER else Qt.CursorShape.CrossCursor)

    def _frac(self, pos: QPointF, clamp: bool = True) -> tuple[float, float] | None:
        rect = self.image_rect()
        if rect is None or rect.width() <= 0 or rect.height() <= 0:
            return None
        x, y = (pos.x() - rect.x()) / rect.width(), (pos.y() - rect.y()) / rect.height()
        if clamp:
            x, y = min(1.0, max(0.0, x)), min(1.0, max(0.0, y))
        return x, y

    def _cancel_stroke(self) -> None:
        self._hold.stop()
        self._points = None
        self._straight = False

    def _on_hold(self) -> None:
        """動かさず押し続けた → 始点から今の点までの直線にする。"""
        if self._points and len(self._points) > 1 and not self._straight:
            self._straight = True
            self._points = [self._points[0], self._points[-1]]
            self.update()

    def _erase_at(self, pos: QPointF) -> None:
        f, rect = self._frac(pos, clamp=False), self.image_rect()
        if f is None or rect is None or not (0 <= f[0] <= 1 and 0 <= f[1] <= 1):
            return
        aspect = rect.height() / rect.width()
        reach = max(0.012, 0.5 * max((s.width for s in self.strokes), default=0.0))     # 指す先の太さ程度は当たりにする
        for k in range(len(self.strokes) - 1, -1, -1):                                  # 上に描いた線から
            st = self.strokes[k]
            if distance_to_stroke(st, f[0], f[1], aspect) <= max(reach, st.width / 2 + 0.006):
                self.erased.emit(self, k)
                return

    def mousePressEvent(self, e: QMouseEvent) -> None:
        tool = self._ink.tool
        if tool == NONE or e.button() != Qt.MouseButton.LeftButton or self.image_rect() is None:
            e.ignore()
            return
        f = self._frac(e.position(), clamp=False)
        if f is None or not (0 <= f[0] <= 1 and 0 <= f[1] <= 1):
            return                                     # ページの外では始めない
        if tool == ERASER:
            self._erase_at(e.position())
            return
        self._points = [f]
        self._straight = False
        self._hold.start()
        self.update()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if self._ink.tool == ERASER and e.buttons() & Qt.MouseButton.LeftButton:
            self._erase_at(e.position())
            return
        if self._points is None:
            e.ignore()
            return
        f = self._frac(e.position())
        if f is None:
            return
        if self._straight:
            self._points[1] = f                        # 直線モード: 終点だけ動かす
            self._hold.stop()
        else:
            last = self._points[-1]
            if (f[0] - last[0]) ** 2 + (f[1] - last[1]) ** 2 < 2.5e-7:
                return                                 # ほぼ動いていない（長押し判定はそのまま続ける）
            self._points.append(f)
            self._hold.start()                         # 動くたびに長押しの計測をやり直す
        self.update()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        if self._points is None or e.button() != Qt.MouseButton.LeftButton:
            e.ignore()
            return
        points, self._points = self._points, None
        self._hold.stop()
        self._straight = False
        self.stroke_done.emit(self, self._ink.new_stroke(points))
        self.update()


class _Stage(QWidget):
    """ページ（左右）を置く台。全体表示の位置を基準に、倍率 zoom と移動量 pan で子の _PageView を動かす。

    子は「全体表示のときの大きさ」を zoom 倍した大きさになるだけで、ページの描き方・書き込みの座標は変わらない。
    台の端からはみ出した部分は台が切る。
    """

    zoom_changed = Signal(float)
    page_wheel = Signal(int)          # 拡大していないときのホイール: -1 前へ, +1 次へ
    ZOOM_MIN = 1.0
    ZOOM_MAX = 6.0
    MARGIN = 8

    def __init__(self, left: _PageView, right: _PageView, ink: InkState, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("stage")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("#stage { background: #e5e7eb; }")
        self.left, self.right = left, right
        for v in (left, right):
            v.setParent(self)
        self._ink = ink
        self._zoom = 1.0
        self._pan = QPointF(0.0, 0.0)
        self._drag: QPointF | None = None
        self._wheel_acc = 0
        self.refresh_cursor()

    @property
    def zoom(self) -> float:
        return self._zoom

    # -- 配置 -----------------------------------------------------------------
    def _area(self) -> QRect:
        m = self.MARGIN
        return self.rect().adjusted(m, m, -m, -m)

    def _base_rects(self) -> tuple[QRect | None, QRect]:
        a = self._area()
        if self.left.isHidden():
            return None, a
        half = a.width() // 2
        return (QRect(a.x(), a.y(), half, a.height()),
                QRect(a.x() + half, a.y(), a.width() - half, a.height()))

    def _content_box(self) -> QRectF:
        """全体表示のとき、ページ画像が占める範囲（台の座標）。"""
        lrect, rrect = self._base_rects()
        boxes = []
        for v, g in ((self.left, lrect), (self.right, rrect)):
            if g is None or not v.has_page():
                continue
            local = QRect(0, 0, g.width(), g.height())
            boxes.append((v.fit_in(local) or local).translated(g.topLeft()))
        if not boxes:
            return QRectF(self._area())
        box = boxes[0]
        for b in boxes[1:]:
            box = box.united(b)
        return QRectF(box)

    def _center(self) -> QPointF:
        a = self._area()
        return QPointF(a.x() + a.width() / 2.0, a.y() + a.height() / 2.0)

    def _clamped(self, pan: QPointF, zoom: float) -> QPointF:
        """ページが台の外へ消えない範囲に収める。台より小さい向きは動かさない。"""
        if zoom <= 1.0 + 1e-9:
            return QPointF(0.0, 0.0)
        a, c, box = self._area(), self._center(), self._content_box()

        def axis(v: float, lo_edge: float, hi_edge: float, size: float) -> float:
            if (hi_edge - lo_edge) * zoom <= size:
                return 0.0
            return min(max(v, size / 2 - hi_edge * zoom), -size / 2 - lo_edge * zoom)

        return QPointF(
            axis(pan.x(), box.left() - c.x(), box.right() + 1 - c.x(), a.width()),
            axis(pan.y(), box.top() - c.y(), box.bottom() + 1 - c.y(), a.height()),
        )

    def relayout(self) -> None:
        self._pan = self._clamped(self._pan, self._zoom)
        c, z = self._center(), self._zoom
        lrect, rrect = self._base_rects()
        for v, g in ((self.left, lrect), (self.right, rrect)):
            if g is None:
                continue
            x0 = c.x() + (g.x() - c.x()) * z + self._pan.x()
            y0 = c.y() + (g.y() - c.y()) * z + self._pan.y()
            x1, y1 = x0 + g.width() * z, y0 + g.height() * z
            v.setGeometry(round(x0), round(y0), max(1, round(x1) - round(x0)), max(1, round(y1) - round(y0)))

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self.relayout()

    # -- 拡大・移動 -----------------------------------------------------------------
    def set_zoom(self, zoom: float, anchor: QPointF | None = None) -> None:
        """anchor（台の座標。省略は中央）の下にあるページ上の点を動かさずに倍率を変える。"""
        z0 = self._zoom
        z = min(self.ZOOM_MAX, max(self.ZOOM_MIN, zoom))
        if abs(z - z0) < 1e-9:
            return
        c = self._center()
        anchor = anchor if anchor is not None else c
        ux, uy = (anchor.x() - c.x() - self._pan.x()) / z0, (anchor.y() - c.y() - self._pan.y()) / z0
        self._zoom = z
        self._pan = QPointF(anchor.x() - c.x() - ux * z, anchor.y() - c.y() - uy * z)
        self.relayout()
        self.refresh_cursor()
        self.zoom_changed.emit(z)

    def zoom_by(self, factor: float, anchor: QPointF | None = None) -> None:
        self.set_zoom(self._zoom * factor, anchor)

    def fit(self) -> None:
        self.set_zoom(1.0)

    def pan_by(self, dx: float, dy: float) -> None:
        if self._zoom > 1.0:
            self._pan = QPointF(self._pan.x() + dx, self._pan.y() + dy)
            self.relayout()

    def snap_topleft(self) -> None:
        """ページをめくったとき: 拡大したまま、新しいページの左上から見る。"""
        if self._zoom > 1.0:
            self._pan = QPointF(1e9, 1e9)
            self.relayout()

    def refresh_cursor(self) -> None:
        if self._drag is not None:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif self._zoom > 1.0 and self._ink.tool == NONE:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    # -- マウス ---------------------------------------------------------------------
    def wheelEvent(self, e: QWheelEvent) -> None:
        d = e.angleDelta()
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if d.y():
                self.zoom_by(1.15 ** (d.y() / 120.0), e.position())
        elif self._zoom > 1.0:
            dx, dy = d.x() / 120.0 * 60, d.y() / 120.0 * 60
            if e.modifiers() & Qt.KeyboardModifier.ShiftModifier and not dx:
                dx, dy = dy, 0.0
            self.pan_by(dx, dy)
        else:
            self._wheel_acc += d.y()
            steps = int(self._wheel_acc / 120)
            if steps:
                self._wheel_acc -= steps * 120
                self.page_wheel.emit(-1 if steps > 0 else 1)
        e.accept()

    def mousePressEvent(self, e: QMouseEvent) -> None:
        b = e.button()
        if b == Qt.MouseButton.MiddleButton or (b == Qt.MouseButton.LeftButton and self._zoom > 1.0):
            self._drag = e.globalPosition()
            self.refresh_cursor()
            e.accept()
        else:
            e.ignore()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if self._drag is None:
            e.ignore()
            return
        now = e.globalPosition()
        self.pan_by(now.x() - self._drag.x(), now.y() - self._drag.y())
        self._drag = now

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        if self._drag is None:
            e.ignore()
            return
        self._drag = None
        self.refresh_cursor()

    def mouseDoubleClickEvent(self, e: QMouseEvent) -> None:
        if e.button() != Qt.MouseButton.LeftButton:
            e.ignore()
            return
        if self._zoom > 1.05:
            self.fit()
        else:
            self.set_zoom(2.5, e.position())


class ViewerWidget(QWidget):
    row_changed = Signal(list)     # 表示中のページ（0 始まり）。サムネイル側の選択と同期するため
    exit_requested = Signal()
    ink_changed = Signal()         # 書き込みが増えた・減った
    save_ink_requested = Signal()

    def __init__(self, request: RequestFn, parent: QWidget | None = None):
        super().__init__(parent)
        self._request = request
        self._refs: list[PageRef] = []
        self._row = 0
        self._cache: OrderedDict[PageRef, QImage] = OrderedDict()
        self._pending: set[PageRef] = set()
        self._hires: OrderedDict[PageRef, QImage] = OrderedDict()     # 拡大して見るときの高解像度版
        self._pending_hi: set[PageRef] = set()
        self._ink = InkState()
        self._strokes: dict[PageRef, list[Stroke]] = {}    # 書き込みレイヤー（ページごとの線。消しても履歴で戻せる）
        self._history: list[tuple] = []                    # ("add"|"erase"|"clear", ref, ...) 1 つ戻す用
        self.ink_dirty = False                             # 保存していない書き込みがある
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
        # 見開きの組み合わせを 1 ページずらす（オン: 1 ページ目が単独 → 2-3, 4-5…／オフ: 1-2, 3-4…）
        self.cover_check = QCheckBox("1ページずらす")
        self.cover_check.setToolTip("見開きの組み合わせを 1 ページずらします（表紙を単独にしたいときはオン）")
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
        bl.addSpacing(12)
        self.zoom_out_btn = QPushButton("－")
        self.zoom_in_btn = QPushButton("＋")
        self.zoom_btn = QPushButton("全体")
        self.zoom_out_btn.setToolTip("縮小（－）。Ctrl+ホイールでも変えられます")
        self.zoom_in_btn.setToolTip("拡大（＋）。Ctrl+ホイール、ダブルクリックでも拡大できます")
        self.zoom_btn.setToolTip("全体表示に戻す（0）。拡大中はドラッグ・ホイール・矢印キーで動かせます")
        for b in (self.zoom_out_btn, self.zoom_in_btn):
            b.setMinimumWidth(36)
        self.zoom_btn.setMinimumWidth(64)
        bl.addWidget(self.zoom_out_btn)
        bl.addWidget(self.zoom_btn)
        bl.addWidget(self.zoom_in_btn)
        bl.addStretch(1)
        bl.addWidget(self.prev_btn)
        bl.addWidget(self.page_label)
        bl.addWidget(self.next_btn)
        lay.addWidget(bar)

        self.ink_bar = InkToolBar(self._ink)
        self.ink_bar.tool_changed.connect(self._on_tool_changed)
        self.ink_bar.visible_toggled.connect(lambda _on: self._refresh_ink_views())
        self.ink_bar.undo_clicked.connect(self.undo_ink)
        self.ink_bar.clear_page_clicked.connect(self.clear_page_ink)
        self.ink_bar.save_clicked.connect(self.save_ink_requested)
        lay.addWidget(self.ink_bar)

        self.left = _PageView(Qt.AlignmentFlag.AlignRight, self._ink)
        self.right = _PageView(Qt.AlignmentFlag.AlignLeft, self._ink)
        for v in (self.left, self.right):
            v.stroke_done.connect(self._on_stroke_done)
            v.erased.connect(self._on_erased)
        self.stage = _Stage(self.left, self.right, self._ink)        # 綴じ目で 2 ページが接する
        self.stage.zoom_changed.connect(self._on_zoom_changed)
        self.stage.page_wheel.connect(self.step)
        lay.addWidget(self.stage, 1)

        self.mode_combo.currentIndexChanged.connect(lambda _: self._on_mode_changed())
        self.cover_check.toggled.connect(lambda _: self._render())
        self.prev_btn.clicked.connect(lambda: self.step(-1))
        self.next_btn.clicked.connect(lambda: self.step(1))
        self.zoom_in_btn.clicked.connect(lambda: self.stage.zoom_by(ZOOM_STEP))
        self.zoom_out_btn.clicked.connect(lambda: self.stage.zoom_by(1 / ZOOM_STEP))
        self.zoom_btn.clicked.connect(self.stage.fit)
        self._on_zoom_changed(1.0)
        for w in (self.mode_combo, self.cover_check, self.prev_btn, self.next_btn, self.exit_btn,
                  self.zoom_in_btn, self.zoom_out_btn, self.zoom_btn):
            w.setFocusPolicy(Qt.FocusPolicy.NoFocus)    # 矢印キーを常にビューアで受ける

    # -- 拡大・移動 -------------------------------------------------------------
    @property
    def zoom(self) -> float:
        return self.stage.zoom

    def set_zoom(self, zoom: float) -> None:
        self.stage.set_zoom(zoom)

    def _on_zoom_changed(self, z: float) -> None:
        self.zoom_btn.setText("全体" if z <= 1.0 + 1e-9 else f"{round(z * 100)}%")
        self.zoom_out_btn.setEnabled(z > _Stage.ZOOM_MIN + 1e-9)
        self.zoom_in_btn.setEnabled(z < _Stage.ZOOM_MAX - 1e-9)
        self._paint_pages()                    # 倍率しだいで高解像度版に切り替える

    def _on_mode_changed(self) -> None:
        self.cover_check.setEnabled(self.spread)      # 1 ページ表示では意味がないので無効化
        self._render()

    # -- 書き込みレイヤー -------------------------------------------------------------
    @property
    def tool(self) -> str:
        return self._ink.tool

    def set_tool(self, tool: str) -> None:
        self.ink_bar.set_tool(tool)

    @property
    def ink_visible(self) -> bool:
        return self._ink.visible

    def set_ink_visible(self, on: bool) -> None:
        self.ink_bar.set_layer_visible(on)

    def ink_marks(self) -> dict[PageRef, tuple[Stroke, ...]]:
        """線のあるページだけ {PageRef: 線の列}。保存（仕上げタブ）が読む。"""
        return {r: tuple(v) for r, v in self._strokes.items() if v}

    def ink_count(self) -> int:
        return sum(len(v) for v in self._strokes.values())

    def mark_ink_saved(self) -> None:
        self.ink_dirty = False

    def clear_ink(self) -> None:
        """別の文書を開いたときなど、書き込みをすべて捨てる。"""
        self._strokes.clear()
        self._history.clear()
        self.ink_dirty = False
        self._refresh_ink_views()
        self.ink_changed.emit()

    def _current_refs(self) -> list[PageRef]:
        return [v.ref for v in (self.left, self.right) if v.ref is not None and v.isVisible()]

    def _on_tool_changed(self, _tool: str) -> None:
        for v in (self.left, self.right):
            v.update_cursor()
        self.stage.refresh_cursor()
        self._refresh_ink_views()

    def _refresh_ink_views(self) -> None:
        for v in (self.left, self.right):
            v.set_page(v.ref, self._strokes.setdefault(v.ref, []) if v.ref is not None else [])
        here = sum(len(v.strokes) for v in (self.left, self.right) if v.isVisible())
        self.ink_bar.set_counts(here, self.ink_count())

    def _changed(self) -> None:
        self.ink_dirty = True
        self._refresh_ink_views()
        self.ink_changed.emit()

    def _on_stroke_done(self, view: _PageView, stroke: Stroke) -> None:
        if view.ref is None:
            return
        self._strokes.setdefault(view.ref, []).append(stroke)
        self._history.append(("add", view.ref, stroke))
        self._changed()

    def _on_erased(self, view: _PageView, index: int) -> None:
        if view.ref is None or not 0 <= index < len(view.strokes):
            return
        stroke = self._strokes[view.ref].pop(index)
        self._history.append(("erase", view.ref, stroke, index))
        self._changed()

    def undo_ink(self) -> bool:
        """直前の書き込み（線を足した・消した・ページを消去した）を取り消す。"""
        if not self._history:
            return False
        op, ref, *rest = self._history.pop()
        lst = self._strokes.setdefault(ref, [])
        if op == "add":
            if rest[0] in lst:
                lst.remove(rest[0])
        elif op == "erase":
            lst.insert(min(rest[1], len(lst)), rest[0])
        else:                                          # "clear"
            lst[:] = rest[0]
        if ref in self._refs and ref not in self._current_refs():
            self.go(self._refs.index(ref))             # 取り消した線のあるページへ移る
        self._changed()
        return True

    def clear_page_ink(self) -> None:
        """今見ているページの書き込みをすべて消す（「1つ戻す」で復活できる）。"""
        did = False
        for ref in self._current_refs():
            lst = self._strokes.get(ref)
            if lst:
                self._history.append(("clear", ref, list(lst)))
                lst.clear()
                did = True
        if did:
            self._changed()

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
        for k in [k for k in self._hires if k not in keep]:
            del self._hires[k]
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
        self.stage.snap_topleft()

    def go(self, row: int) -> None:
        if not self._refs:
            return
        self._row = max(0, min(len(self._refs) - 1, row))
        self._render()
        self.stage.snap_topleft()

    def keyPressEvent(self, e: QKeyEvent) -> None:
        k = e.key()
        zoomed = self.stage.zoom > 1.0
        if k in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.stage.zoom_by(ZOOM_STEP)
        elif k in (Qt.Key.Key_Minus, Qt.Key.Key_Underscore):
            self.stage.zoom_by(1 / ZOOM_STEP)
        elif k == Qt.Key.Key_0:
            self.stage.fit()
        elif zoomed and k in _PAN_KEYS:                # 拡大中は矢印キーで動かす（めくるのは PageUp/Down・Space）
            dx, dy = _PAN_KEYS[k]
            self.stage.pan_by(dx, dy)
        elif k in (Qt.Key.Key_Left, Qt.Key.Key_PageUp):
            self.step(-1)
        elif k in (Qt.Key.Key_Right, Qt.Key.Key_PageDown, Qt.Key.Key_Space):
            self.step(1)
        elif k == Qt.Key.Key_Home:
            self.go(0)
        elif k == Qt.Key.Key_End:
            self.go(len(self._refs) - 1)
        elif k == Qt.Key.Key_Escape:
            if self._ink.tool != NONE:
                self.set_tool(NONE)                    # 書き込み中の Esc は、まず閲覧に戻る
            else:
                self.exit_requested.emit()
        elif e.modifiers() == Qt.KeyboardModifier.NoModifier and k in _INK_KEYS:
            _INK_KEYS[k](self)
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

    def _image_for(self, row: int | None) -> QImage | None:
        """表示に使う画像。拡大中は高解像度版（無ければ取り寄せ、届くまでは通常版）。"""
        base = self._ensure(row)
        if base is None or row is None or self.stage.zoom < HI_ZOOM:
            return base
        ref = self._refs[row]
        hi = self._hires.get(ref)
        if hi is not None:
            self._hires.move_to_end(ref)
            return hi
        if ref not in self._pending_hi:
            self._pending_hi.add(ref)
            self._request(ref, lambda im, r=ref: self._on_ready_hi(r, im), HI_DPI)
        return base

    def _on_ready_hi(self, ref: PageRef, image: QImage) -> None:
        self._pending_hi.discard(ref)
        if ref not in self._refs:
            return
        self._hires[ref] = image
        while len(self._hires) > HI_LIMIT:
            self._hires.popitem(last=False)
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
            self.left.show_image(self._image_for(left), empty=left is None)
        else:
            self.right._align = Qt.AlignmentFlag.AlignHCenter
            self.left.setVisible(False)
        self.right.show_image(self._image_for(right), empty=right is None)
        for view, row in ((self.left, left), (self.right, right)):
            ref = self._refs[row] if row is not None else None
            view.set_page(ref, self._strokes.setdefault(ref, []) if ref is not None else [])
        self.stage.relayout()                  # 左側の出し入れ・画像の大きさで、移動できる範囲が変わる
        here = sum(len(v.strokes) for v in (self.left, self.right) if v.isVisible())
        self.ink_bar.set_counts(here, self.ink_count())


_PAN_KEYS = {
    Qt.Key.Key_Left: (80, 0),
    Qt.Key.Key_Right: (-80, 0),
    Qt.Key.Key_Up: (0, 80),
    Qt.Key.Key_Down: (0, -80),
}

_INK_KEYS = {
    Qt.Key.Key_P: lambda w: w.set_tool("pen"),
    Qt.Key.Key_M: lambda w: w.set_tool("marker"),
    Qt.Key.Key_E: lambda w: w.set_tool(ERASER),
    Qt.Key.Key_L: lambda w: w.set_ink_visible(not w.ink_visible),
    Qt.Key.Key_U: lambda w: w.undo_ink(),
}
