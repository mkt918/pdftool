"""エントリポイント: `python -m pdftool [PDF...]`"""

from __future__ import annotations

import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
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
