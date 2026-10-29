"""共通の小さな部品。"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLayout,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class SliderSpin(QWidget):
    """スライダー + 数値欄。どちらを動かしても連動する（QDoubleSpinBox と同じ呼び方ができる）。"""

    valueChanged = Signal(float)
    SCALE = 10      # スライダーは 0.1 刻みの整数で持つ

    def __init__(self, minimum: float = 0.0, maximum: float = 50.0, value: float = 0.0,
                 suffix: str = " mm", parent: QWidget | None = None):
        super().__init__(parent)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.spin = QDoubleSpinBox()
        self.spin.setDecimals(1)
        self.spin.setSingleStep(0.5)
        self.spin.setSuffix(suffix)
        self.spin.setFixedWidth(84)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.spin)
        self.setRange(minimum, maximum)
        self.setValue(value)
        self.slider.valueChanged.connect(self._from_slider)
        self.spin.valueChanged.connect(self._from_spin)

    def setRange(self, minimum: float, maximum: float) -> None:
        self.spin.setRange(minimum, maximum)
        self.slider.setRange(round(minimum * self.SCALE), round(maximum * self.SCALE))

    def setSuffix(self, suffix: str) -> None:
        self.spin.setSuffix(suffix)

    def value(self) -> float:
        return self.spin.value()

    def setValue(self, v: float) -> None:
        self.spin.setValue(v)      # → _from_spin がスライダーを追従させる

    def _from_slider(self, i: int) -> None:
        v = i / self.SCALE
        if abs(self.spin.value() - v) > 1e-9:
            self.spin.setValue(v)      # spin 側の valueChanged が signal を出す

    def _from_spin(self, v: float) -> None:
        i = round(v * self.SCALE)
        if self.slider.value() != i:
            self.slider.blockSignals(True)
            self.slider.setValue(i)
            self.slider.blockSignals(False)
        self.valueChanged.emit(v)


class OverflowBar(QWidget):
    """部品を横 1 行に並べる。入りきらない後ろの部品は、右端の「≫」を押すと出るポップアップに移す。

    QToolBar の「≫」は QMainWindow の外では隠れた部品を出せないので、自前で作っている。
    2 行・3 行に折り返さず、幅が足りなくても操作は必ずどこかから届く。
    add() で並べた順（左が先）に残り、足りないときは後ろの部品から「≫」の中へ移る。
    """

    SPACING = 6

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._items: list[QWidget] = []
        self._wanted: dict[QWidget, bool] = {}          # False の部品は、幅に関係なく出さない
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(self.SPACING)
        self._lay.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)    # 幅の下限は minimumSizeHint で決める
        self._lay.addStretch(1)
        self.more_btn = QToolButton()
        self.more_btn.setText("≫")
        self.more_btn.setToolTip("入りきらない操作を表示します")
        self.more_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.more_btn.clicked.connect(self._open_popup)
        self.more_btn.hide()
        self._lay.addWidget(self.more_btn)
        self._popup = QFrame(self, Qt.WindowType.Popup)
        self._popup.setObjectName("overflowPopup")
        self._popup.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._pop_lay = QVBoxLayout(self._popup)           # 縦に並べる（横一列だと画面より広くなる）
        self._pop_lay.setContentsMargins(8, 8, 8, 8)
        self._pop_lay.setSpacing(6)
        self._relaying = False

    # -- 部品の追加 -----------------------------------------------------------
    def add(self, widget: QWidget) -> None:
        self._items.append(widget)
        self._wanted[widget] = True
        widget.setParent(self)
        self._relayout()

    def add_separator(self) -> QWidget:
        sep = QFrame()
        sep.setFixedSize(1, 20)
        sep.setStyleSheet("background: #e5e7eb; border: none;")
        sep.setProperty("separator", True)
        self.add(sep)
        return sep

    def set_wanted(self, widget: QWidget, on: bool) -> None:
        """widget を出す／出さない（ペンのときだけ色を出す、など）。出さない間は幅を取らない。"""
        if self._wanted.get(widget) != on:
            self._wanted[widget] = on
            self._relayout()

    def hidden_widgets(self) -> list[QWidget]:
        """幅が足りず「≫」の中に移っている部品（区切り線は除く）。"""
        return [w for w in self._items if self._wanted[w] and w.parent() is self._popup
                and not w.property("separator")]

    # -- 配置 -----------------------------------------------------------------
    def sizeHint(self) -> QSize:
        wants = [w for w in self._items if self._wanted[w]]
        w = sum(x.sizeHint().width() for x in wants) + self.SPACING * max(0, len(wants) - 1)
        return QSize(w, max((x.sizeHint().height() for x in wants), default=0))

    def minimumSizeHint(self) -> QSize:
        return QSize(self.more_btn.sizeHint().width(), self.sizeHint().height())

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._relayout()

    def _relayout(self) -> None:
        if self._relaying:
            return
        self._relaying = True
        try:
            wants = [w for w in self._items if self._wanted[w]]
            for w in self._items:
                self._lay.removeWidget(w)                  # 入れ直す前に外す（二重登録を避ける）
                self._pop_lay.removeWidget(w)
                if not self._wanted[w]:
                    w.hide()
            avail = self.width()
            sp = self.SPACING
            total = sum(w.sizeHint().width() for w in wants) + sp * max(0, len(wants) - 1)
            if total <= avail:
                shown = wants
            else:
                room = avail - self.more_btn.sizeHint().width() - sp
                shown, used = [], 0
                for w in wants:
                    need = w.sizeHint().width() + (sp if shown else 0)
                    if used + need > room:
                        break
                    shown.append(w)
                    used += need
            over = [w for w in wants if w not in shown]
            while shown and shown[-1].property("separator"):          # 区切り線で終わらせない
                over.insert(0, shown.pop())
            for w in shown:
                self._lay.insertWidget(self._lay.count() - 2, w)       # 順序どおりにバーへ（親もバーに戻る）
                w.setVisible(True)
            for w in over:
                if w.property("separator"):
                    w.hide()
                else:
                    self._pop_lay.addWidget(w, 0, Qt.AlignmentFlag.AlignLeft)   # 親がポップアップに変わる
                    w.setVisible(True)
            self.more_btn.setVisible(bool(self.hidden_widgets()))
        finally:
            self._relaying = False

    def _open_popup(self) -> None:
        if self._popup.isVisible():
            self._popup.hide()
            return
        self._popup.adjustSize()
        pos = self.more_btn.mapToGlobal(QPoint(self.more_btn.width() - self._popup.width(), self.more_btn.height() + 4))
        self._popup.move(max(self.window().x(), pos.x()), pos.y())
        self._popup.show()
