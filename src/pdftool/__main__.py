"""エントリポイント: `python -m pdftool [PDF...]`（exe化後は PDFユーティリティ.exe [PDF...]）"""

from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path


def _log_path() -> Path:
    return Path(tempfile.gettempdir()) / "pdftool_error.log"


def _install_excepthook() -> None:
    """exe化(--windowed)するとコンソールが無く、素の例外は何も表示されず消えてしまう。
    ログファイルに書き出し、QApplication があればダイアログでも知らせる。"""

    def hook(exc_type, exc, tb) -> None:
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        log_path = _log_path()
        try:
            log_path.write_text(text, encoding="utf-8")
        except OSError:
            pass
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox

            if QApplication.instance() is not None:
                QMessageBox.critical(
                    None, "予期しないエラー",
                    f"予期しないエラーが発生しました。アプリを再起動してください。\n\nログ: {log_path}",
                )
        except Exception:
            pass
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = hook


def main(argv: list[str] | None = None) -> int:
    _install_excepthook()
    argv = list(sys.argv[1:] if argv is None else argv)
    from PySide6.QtWidgets import QApplication

    from pdftool.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("pdftool")
    win = MainWindow()
    win.show()
    pdfs = [Path(a) for a in argv if a.lower().endswith(".pdf")]
    if pdfs and win.open_pdf(pdfs[0]):
        for p in pdfs[1:]:
            win.append_pdf(p)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
