"""アプリ全体の見た目（Fusion + スタイルシート）。ライト基調・青のアクセント・角丸。"""

from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap, QPolygonF
from PySide6.QtWidgets import QApplication

ACCENT = "#2563eb"
ACCENT_HOVER = "#1d4ed8"
ACCENT_SOFT = "#dbeafe"

def _make_arrow(name: str, up: bool) -> str:
    """矢印アイコンを PNG で書き出してパスを返す（QSS の border 三角は Qt で描画されないため）。"""
    d = Path(tempfile.gettempdir()) / "pdftool_theme"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{name}.png"
    pm = QPixmap(20, 20)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#6b7280"))
    pts = [(4, 13), (16, 13), (10, 6)] if up else [(4, 7), (16, 7), (10, 14)]
    p.drawPolygon(QPolygonF([QPointF(x, y) for x, y in pts]))
    p.end()
    pm.save(str(path), "PNG")
    return path.as_posix()


def _build_qss() -> str:
    arrow_up = _make_arrow("arrow_up", True)
    arrow_down = _make_arrow("arrow_down", False)
    return QSS_TEMPLATE.replace("{ARROW_UP}", arrow_up).replace("{ARROW_DOWN}", arrow_down)


QSS_TEMPLATE = f"""
* {{ font-family: "Yu Gothic UI", "Segoe UI", "Meiryo UI", sans-serif; }}
QMainWindow, QDialog {{ background: #f3f4f6; }}
QWidget {{ color: #1f2937; font-size: 13px; }}
QLabel {{ background: transparent; }}

QToolBar {{
    background: #ffffff; border: none; border-bottom: 1px solid #e5e7eb;
    padding: 6px 8px; spacing: 4px;
}}
QToolBar::separator {{ background: #e5e7eb; width: 1px; margin: 4px 6px; }}
QToolButton {{
    background: transparent; border: 1px solid transparent; border-radius: 8px;
    padding: 6px 10px; color: #1f2937;
}}
QToolButton:hover {{ background: #eef2ff; }}
QToolButton:pressed {{ background: {ACCENT_SOFT}; }}
QToolButton:disabled {{ color: #b6bcc6; }}

QListView {{
    background: #ffffff; border: none; outline: none; padding: 8px;
}}
QListView::item {{ color: #6b7280; }}

QTabWidget::pane {{ background: transparent; border: none; border-top: 1px solid #e5e7eb; top: -1px; }}
QTabWidget > QWidget > QWidget {{ background: transparent; }}
QTabBar::tab {{
    background: transparent; color: #6b7280; padding: 8px 14px; margin-right: 2px;
    border: none; border-bottom: 2px solid transparent;
}}
QTabBar::tab:hover {{ color: #1f2937; }}
QTabBar::tab:selected {{ color: {ACCENT}; border-bottom: 2px solid {ACCENT}; font-weight: bold; }}

QPushButton {{
    background: #ffffff; border: 1px solid #d1d5db; border-radius: 8px; padding: 6px 14px;
}}
QPushButton:hover {{ background: #f9fafb; border-color: #9ca3af; }}
QPushButton:pressed {{ background: #eef2ff; }}
QPushButton:default {{ background: {ACCENT}; color: #ffffff; border-color: {ACCENT}; }}
QPushButton:default:hover {{ background: {ACCENT_HOVER}; }}
QPushButton:disabled {{ background: #f3f4f6; color: #b6bcc6; border-color: #e5e7eb; }}
QPushButton:flat {{ border: none; background: transparent; color: {ACCENT}; }}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit {{
    background: #ffffff; border: 1px solid #d1d5db; border-radius: 6px; padding: 4px 8px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{ image: url({{ARROW_DOWN}}); width: 10px; height: 10px; margin-right: 8px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::down-button {{
    width: 20px; border: none; background: transparent; subcontrol-origin: border;
}}
QSpinBox::up-button, QDoubleSpinBox::up-button {{ subcontrol-position: top right; }}
QSpinBox::down-button, QDoubleSpinBox::down-button {{ subcontrol-position: bottom right; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url({{ARROW_UP}}); width: 10px; height: 10px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url({{ARROW_DOWN}}); width: 10px; height: 10px; }}
QComboBox QAbstractItemView {{
    background: #ffffff; border: 1px solid #d1d5db; selection-background-color: {ACCENT_SOFT};
    selection-color: #1f2937; outline: none;
}}

QGroupBox {{
    background: #ffffff; border: 1px solid #e5e7eb; border-radius: 10px;
    margin-top: 6px; padding: 30px 12px 12px 12px; font-weight: bold; color: #374151;
}}
QGroupBox::title {{
    subcontrol-origin: padding; subcontrol-position: top left; left: 12px; top: 8px;
    padding: 0; background: transparent;
}}
QGroupBox QLabel, QGroupBox QCheckBox, QGroupBox QRadioButton {{ font-weight: normal; }}

QCheckBox, QRadioButton {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px; height: 16px; border: 2px solid #9ca3af; background: #ffffff;
}}
QCheckBox::indicator {{ border-radius: 5px; }}
QRadioButton::indicator {{ border-radius: 9px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QRadioButton::indicator:checked {{ background: #ffffff; border: 5px solid {ACCENT}; }}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{ border-color: #d1d5db; background: #f3f4f6; }}

QSlider::groove:horizontal {{ height: 6px; background: #e5e7eb; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 3px; }}
QSlider::handle:horizontal {{
    width: 16px; height: 16px; margin: -6px 0; border-radius: 9px; background: #ffffff; border: 2px solid {ACCENT};
}}
QSlider::handle:horizontal:hover {{ background: {ACCENT_SOFT}; }}
QSlider::groove:horizontal:disabled {{ background: #eef0f3; }}
QSlider::sub-page:horizontal:disabled {{ background: #cbd5e1; }}
QSlider::handle:horizontal:disabled {{ border-color: #cbd5e1; }}
QProgressBar {{
    background: #e5e7eb; border: none; border-radius: 6px; text-align: center; height: 12px;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 6px; }}

QMenu {{
    background: #ffffff; border: 1px solid #e5e7eb; border-radius: 10px; padding: 6px;
}}
QMenu::item {{ padding: 7px 24px 7px 14px; border-radius: 6px; }}
QMenu::item:selected {{ background: {ACCENT_SOFT}; color: #111827; }}
QMenu::item:disabled {{ color: #b6bcc6; }}
QMenu::separator {{ height: 1px; background: #e5e7eb; margin: 5px 8px; }}

QScrollBar:vertical {{ background: transparent; width: 12px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #c7ccd4; border-radius: 4px; min-height: 36px; }}
QScrollBar::handle:vertical:hover {{ background: #9ca3af; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #c7ccd4; border-radius: 4px; min-width: 36px; }}
QScrollBar::handle:horizontal:hover {{ background: #9ca3af; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QSplitter::handle {{ background: transparent; }}
QStatusBar {{ background: #ffffff; border-top: 1px solid #e5e7eb; color: #6b7280; }}
QToolTip {{
    background: #111827; color: #ffffff; border: none; padding: 5px 8px; border-radius: 6px;
}}
"""


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    font = app.font()
    font.setPointSizeF(max(font.pointSizeF(), 9.5))
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    app.setFont(font)
    app.setStyleSheet(_build_qss())
