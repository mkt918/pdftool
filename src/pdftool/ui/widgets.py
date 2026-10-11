"""共通の小さな部品。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDoubleSpinBox, QHBoxLayout, QSlider, QWidget


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
