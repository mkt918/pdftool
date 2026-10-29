"""ビューアの書き込みツール（ペン・蛍光ペン・消しゴム）の部品。

- InkState: 今のツールと、ペン／蛍光ペンごとの色・太さ、書き込みレイヤーの表示/非表示
- paint_stroke: 線を QPainter で描く（ビューアの表示と、太さのプレビューで共通）
- InkToolBar: ツール選択・色・太さ・レイヤー表示切替などの部品（ビューアの 1 行のバーに並べる）

線の座標と太さは、ページに対する割合（core/ink.py）。画面の大きさが変わっても同じ見た目になる。
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QButtonGroup,
    QColorDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QWidget,
)

from pdftool.core.ink import MARKER, PEN, Stroke
from pdftool.ui.theme import ACCENT
from pdftool.ui.widgets import OverflowBar

NONE = "none"          # 閲覧のみ（書き込まない）
ERASER = "eraser"
TOOLS = (NONE, PEN, MARKER, ERASER)
TOOL_LABELS = {NONE: "閲覧", PEN: "ペン", MARKER: "蛍光ペン", ERASER: "消しゴム"}
TOOL_KEYS = {NONE: "", PEN: "P", MARKER: "M", ERASER: "E"}

PALETTES = {
    PEN: ["#111827", "#dc2626", "#2563eb", "#16a34a", "#f59e0b", "#9333ea"],
    MARKER: ["#fde047", "#f9a8d4", "#86efac", "#7dd3fc", "#fdba74", "#c4b5fd"],
}
DEFAULTS = {PEN: ("#dc2626", 4), MARKER: ("#fde047", 28)}      # (色, 太さ 1〜60)
WIDTH_RANGE = (1, 60)
WIDTH_SCALE = 1000.0           # 太さ 1 = ページ幅の 1/1000
HOLD_MS = 550                  # 動かさず押し続けると直線になるまでの時間


class InkState:
    """ビューア全体で共有する書き込みツールの状態。"""

    def __init__(self) -> None:
        self.tool = NONE
        self.visible = True
        self.color = {k: QColor(c) for k, (c, _) in DEFAULTS.items()}
        self.width = {k: w for k, (_, w) in DEFAULTS.items()}

    @property
    def drawing(self) -> bool:
        return self.tool in (PEN, MARKER)

    def new_stroke(self, points) -> Stroke:
        c = self.color[self.tool]
        return Stroke(self.tool, (c.red(), c.green(), c.blue()), self.width[self.tool] / WIDTH_SCALE, tuple(points))


def paint_stroke(p: QPainter, st: Stroke, rect: QRect | QRectF) -> None:
    """st を rect（ページの表示範囲）に描く。蛍光ペンは乗算で重ね、下の文字を隠さない。"""
    r = QRectF(rect)
    pts = [QPointF(r.x() + x * r.width(), r.y() + y * r.height()) for x, y in st.points]
    pen = QPen(QColor(*st.color), max(1.0, st.width * r.width()))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap if st.kind == PEN else Qt.PenCapStyle.SquareCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    if st.kind == MARKER:
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
    p.setPen(pen)
    if len(pts) == 1:
        p.drawPoint(pts[0])
    else:
        path = QPainterPath(pts[0])               # 1 本の経路で描くので、同じ線の重なりは濃くならない
        for pt in pts[1:]:
            path.lineTo(pt)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
    p.restore()


class _Swatch(QPushButton):
    def __init__(self, color: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.color = QColor(color)
        self.setFixedSize(18, 18)
        self.setCheckable(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(color)
        self.setStyleSheet(
            f"QPushButton {{ background: {color}; border: 2px solid #d1d5db; border-radius: 9px; padding: 0; }}"
            f"QPushButton:hover {{ border-color: #9ca3af; }}"
            f"QPushButton:checked {{ border: 3px solid {ACCENT}; }}"
        )


class _WidthPreview(QWidget):
    """今の色・太さで引いた線の見本（ページの表示幅を約 900px として描く）。"""

    def __init__(self, state: InkState, parent: QWidget | None = None):
        super().__init__(parent)
        self._state = state
        self.setFixedSize(48, 26)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#ffffff"))
        p.setPen(QPen(QColor("#e5e7eb")))
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        kind = self._state.tool if self._state.drawing else PEN
        c = self._state.color[kind]
        # 蛍光ペンは下に文字風の線を引いて、乗算の見え方も見せる
        if kind == MARKER:
            p.setPen(QPen(QColor("#374151"), 2))
            p.drawLine(int(self.width() * 0.12), self.height() // 2, int(self.width() * 0.88), self.height() // 2)
        px = min(self.height() - 6.0, self._state.width[kind] * 900.0 / WIDTH_SCALE)     # 画面でページ幅 900px のときの太さ
        st = Stroke(kind, (c.red(), c.green(), c.blue()), px / self.width(), ((0.12, 0.5), (0.5, 0.38), (0.88, 0.5)))
        p.setClipRect(self.rect().adjusted(1, 1, -1, -1))
        paint_stroke(p, st, QRectF(self.rect()))
        p.end()


class InkToolBar(QObject):
    """ツール・色・太さ・書き込みレイヤーの表示切替・取り消しなどの部品。

    部品は populate() でビューアの 1 行のバー（OverflowBar）に並べる。幅が足りないぶんは、
    右端の「≫」のポップアップにたたむ（2 行・3 行に折り返さない）。
    """

    tool_changed = Signal(str)
    visible_toggled = Signal(bool)
    undo_clicked = Signal()
    clear_page_clicked = Signal()
    save_clicked = Signal()

    def __init__(self, state: InkState, parent: QObject | None = None):
        super().__init__(parent)
        self.state = state

        # ツール（閲覧・ペン・蛍光ペン・消しゴム）
        self.tools_box = QWidget()
        self.tools_box.setObjectName("inkTools")           # 選んでいるツールを青く示す（theme.py）
        lay = QHBoxLayout(self.tools_box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.tool_buttons: dict[str, QPushButton] = {}
        group = QButtonGroup(self)
        group.setExclusive(True)
        for tool in TOOLS:
            b = QPushButton()
            b.setText(TOOL_LABELS[tool])
            b.setCheckable(True)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setToolTip(TOOL_LABELS[tool] + (f"（{TOOL_KEYS[tool]}）" if TOOL_KEYS[tool] else "（Esc）"))
            group.addButton(b)
            b.clicked.connect(lambda _=False, t=tool: self.set_tool(t))
            lay.addWidget(b)
            self.tool_buttons[tool] = b
        self.tool_buttons[NONE].setChecked(True)

        # 色と太さ（ペン・蛍光ペンのときだけ使える）
        self.color_box = QWidget()
        cl = QHBoxLayout(self.color_box)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(4)
        self.swatch_box = QWidget()
        self._swatch_lay = QHBoxLayout(self.swatch_box)
        self._swatch_lay.setContentsMargins(0, 0, 0, 0)
        self._swatch_lay.setSpacing(3)
        cl.addWidget(self.swatch_box)
        self.custom_btn = QPushButton("＋")
        self.custom_btn.setFixedWidth(30)
        self.custom_btn.setToolTip("好きな色を選ぶ")
        self.custom_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.custom_btn.clicked.connect(self._pick_custom)
        cl.addWidget(self.custom_btn)
        self.width_label = QLabel("太さ")
        cl.addWidget(self.width_label)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(*WIDTH_RANGE)
        self.slider.setFixedWidth(64)
        self.slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.slider.valueChanged.connect(self._on_width)
        cl.addWidget(self.slider)
        self.preview = _WidthPreview(state)
        cl.addWidget(self.preview)

        # 書き込みレイヤーの操作
        self.layer_btn = QPushButton()
        self.layer_btn.setCheckable(True)
        self.layer_btn.setChecked(True)
        self.layer_btn.setToolTip("書き込みの表示／非表示を切り替えます（L）。非表示でも書き込みは消えません")
        self.layer_btn.toggled.connect(self._on_layer_toggled)
        self.undo_btn = QPushButton("1つ戻す")
        self.undo_btn.setToolTip("直前の書き込みを取り消します（U）")
        self.undo_btn.clicked.connect(self.undo_clicked)
        self.clear_btn = QPushButton("ページ消去")
        self.clear_btn.setToolTip("いま見ているページの書き込みをすべて消します（「1つ戻す」で戻せます）")
        self.clear_btn.clicked.connect(self.clear_page_clicked)
        self.save_btn = QPushButton("書き込みを保存…")
        self.save_btn.setToolTip("「仕上げ」タブで、書き込みを含めた新しいPDFとして保存します")
        self.save_btn.clicked.connect(self.save_clicked)
        self.action_buttons = (self.layer_btn, self.undo_btn, self.clear_btn, self.save_btn)
        for b in self.action_buttons:
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._refresh_layer_text()
        self.refresh()

    def populate(self, bar: OverflowBar) -> None:
        """バーの右側に、ツール → 色と太さ → 書き込みの操作 の順に並べる（足りない幅は後ろから「≫」へ）。"""
        self._bar = bar
        bar.add_separator()
        bar.add(self.tools_box)
        bar.add(self.color_box)
        bar.set_wanted(self.color_box, self.state.drawing)       # 色と太さは、ペン・蛍光ペンのときだけ出す（1 行に収めるため）
        bar.add_separator()
        for b in self.action_buttons:
            bar.add(b)

    # -- 状態の反映 -----------------------------------------------------------
    def set_tool(self, tool: str) -> None:
        if tool not in TOOLS:
            return
        self.state.tool = tool
        self.tool_buttons[tool].setChecked(True)
        if tool in (PEN, MARKER) and not self.state.visible:
            self.set_layer_visible(True)         # 隠れたまま描くと何も見えないので、自動で表示に戻す
        self.refresh()
        self.tool_changed.emit(tool)

    def set_layer_visible(self, on: bool) -> None:
        self.layer_btn.setChecked(on)

    def _on_layer_toggled(self, on: bool) -> None:
        self.state.visible = on
        self._refresh_layer_text()
        self.visible_toggled.emit(on)

    def _refresh_layer_text(self) -> None:
        self.layer_btn.setText("書き込み: 表示中" if self.layer_btn.isChecked() else "書き込み: 非表示")

    def refresh(self) -> None:
        """ツールに合わせて色の候補・太さ・有効/無効を更新する。"""
        kind = self.state.tool if self.state.drawing else None
        for k in range(self._swatch_lay.count()):
            w = self._swatch_lay.itemAt(k).widget()
            if w is not None:
                w.deleteLater()
        while self._swatch_lay.count():
            self._swatch_lay.takeAt(0)
        if kind:
            cur = self.state.color[kind]
            colors = list(PALETTES[kind])
            if cur.name() not in {QColor(c).name() for c in colors}:
                colors[-1] = cur.name()                  # 好きな色を選んだときは、最後の枠をその色にする
            for c in colors:
                sw = _Swatch(c)
                sw.setChecked(QColor(c).name() == cur.name())
                sw.clicked.connect(lambda _=False, col=c: self._set_color(QColor(col)))
                self._swatch_lay.addWidget(sw)
            self.slider.blockSignals(True)
            self.slider.setValue(self.state.width[kind])
            self.slider.blockSignals(False)
        for w in (self.swatch_box, self.custom_btn, self.slider, self.width_label):
            w.setEnabled(kind is not None)
        bar = getattr(self, "_bar", None)
        if bar is not None:
            bar.set_wanted(self.color_box, kind is not None)
        self.preview.update()

    def _set_color(self, color: QColor) -> None:
        if self.state.drawing:
            self.state.color[self.state.tool] = color
            self.refresh()

    def _pick_custom(self) -> None:
        if not self.state.drawing:
            return
        c = QColorDialog.getColor(self.state.color[self.state.tool], self, "色を選ぶ")
        if c.isValid():
            self._set_color(c)

    def _on_width(self, v: int) -> None:
        if self.state.drawing:
            self.state.width[self.state.tool] = v
            self.preview.update()

    def set_counts(self, page_strokes: int, total_strokes: int) -> None:
        self.clear_btn.setEnabled(page_strokes > 0)
        self.undo_btn.setEnabled(total_strokes > 0)
        self.save_btn.setEnabled(total_strokes > 0)
