"""アプリ全体の見た目（Fusion + スタイルシート）。ライト基調・青のアクセント・角丸。"""

from __future__ import annotations

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

ACCENT = "#2563eb"
ACCENT_HOVER = "#1d4ed8"
ACCENT_SOFT = "#dbeafe"

QSS = f"""
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

QTabWidget::pane {{
    background: #ffffff; border: 1px solid #e5e7eb; border-radius: 10px; top: -1px;
}}
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
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: #ffffff; border: 1px solid #d1d5db; selection-background-color: {ACCENT_SOFT};
    selection-color: #1f2937; outline: none;
}}

QGroupBox {{
    background: #ffffff; border: 1px solid #e5e7eb; border-radius: 10px;
    margin-top: 14px; padding: 12px 10px 8px 10px;
}}
QGroupBox::title {{
    subcontrol-origin: margin; left: 12px; padding: 0 6px; color: #374151; font-weight: bold;
    background: #ffffff;
}}

QCheckBox, QRadioButton {{ spacing: 6px; background: transparent; }}
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
    app.setStyleSheet(QSS)
