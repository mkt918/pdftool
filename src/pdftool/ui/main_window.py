"""メインウィンドウ。全体レイアウトとタブ切り替え、ファイルの開閉、ワーカーの起動口。

レイアウト:
    [ツールバー: 開く / 結合 / 保存 / 元に戻す / やり直し / 左回転 / 右回転 / 削除 / サムネ:小中大]
    [ サムネイルグリッド (左, 伸縮) | 機能タブ (右: 圧縮 / 画像書き出し / ページ操作 / バッチ) ]
    [ステータスバー: ファイル名・ページ数・選択数 | Ghostscript の状態]

スレッド境界:
    - サムネイル描画: ThumbnailWorker（常駐 QThread）。モデルが要求を積む
    - 圧縮・書き出しなど: run_worker() → QThreadPool。コールバックは主スレッドで呼ばれる
    - core の PageList は主スレッドだけが触る。ワーカーに渡すのは PageRef のタプルなど不変値
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QCoreApplication, QSettings, QSize, Qt, QThreadPool
from PySide6.QtGui import QAction, QCloseEvent, QDragEnterEvent, QDropEvent, QImage, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from pdftool.core.compress import format_size
from pdftool.core.document import PdfDocument, PdfEncryptedError, PdfError
from pdftool.core.images import convert_images, is_image
from pdftool.core.pageops import PageList
from pdftool.ui.panels.base import PanelBase
from pdftool.ui.preview_dialog import PagePreviewDialog
from pdftool.ui.panels.batch_panel import BatchPanel
from pdftool.ui.panels.compress_panel import CompressPanel
from pdftool.ui.panels.export_panel import ExportPanel
from pdftool.ui.panels.finish_panel import FinishPanel
from pdftool.ui.panels.pageops_panel import PageOpsPanel
from pdftool.ui.thumbnail_view import THUMB_SIZES, ThumbnailModel, ThumbnailView
from pdftool.utils.gs_locator import find_ghostscript, ghostscript_version
from pdftool.utils.worker import ThumbnailWorker, Worker, pil_to_qimage

APP_TITLE = "PDF ユーティリティ"


class MainWindow(QMainWindow):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(APP_TITLE)
        self.resize(1200, 800)
        self.setAcceptDrops(True)

        # --- 設定の永続化（サムネイルサイズ・最後に使ったフォルダなど） ---
        # 組織名/アプリ名を先に設定してから引数無しで作る。
        # QSettings(org, app) の2引数版は常に NativeFormat 固定で、テストで
        # setDefaultFormat(IniFormat) を当てても効かない（実レジストリを触ってしまう）ため。
        QCoreApplication.setOrganizationName("pdftool")
        QCoreApplication.setApplicationName("PDFUtility")
        self.settings = QSettings()

        # --- Ghostscript 検出（無ければ圧縮だけ無効化） ---
        self.gs_path: Path | None = find_ghostscript()
        self.gs_version: str | None = ghostscript_version(self.gs_path) if self.gs_path else None

        # --- サムネイル基盤 ---
        self.thumb_worker = ThumbnailWorker(self)
        self.thumb_worker.start()
        self.model = ThumbnailModel(self.thumb_worker, self)
        self.view = ThumbnailView(self.model)
        self.view.page_activated.connect(self._preview_page)
        self.view.selection_changed.connect(self._on_selection_changed)
        self.view.files_dropped.connect(self._on_files_dropped)
        self.model.page_list_changed.connect(self._on_document_changed)

        # --- 機能タブ ---
        self.tabs = QTabWidget()
        self.panels: list[PanelBase] = []
        for panel_cls in (ExportPanel, CompressPanel, PageOpsPanel, FinishPanel, BatchPanel):
            self._add_panel(panel_cls(self))

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.view)
        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([820, 380])
        self.setCentralWidget(splitter)

        self._build_toolbar()
        self._build_statusbar()
        self._workers: list[Worker] = []   # 実行中ワーカーの参照保持（GC 防止）
        self._on_document_changed()

    # ------------------------------------------------------------------
    # 構築
    # ------------------------------------------------------------------
    def _add_panel(self, panel: PanelBase) -> None:
        self.panels.append(panel)
        self.tabs.addTab(panel, panel.title)

    def _build_toolbar(self) -> None:
        tb = QToolBar("メイン")
        tb.setMovable(False)
        tb.setIconSize(QSize(20, 20))
        self.addToolBar(tb)

        def act(text: str, slot: Callable, shortcut: str | None = None, tip: str | None = None) -> QAction:
            a = QAction(text, self)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            if tip:
                a.setStatusTip(tip)
            a.triggered.connect(slot)
            tb.addAction(a)
            return a

        self.act_open = act("開く", self.open_dialog, "Ctrl+O", "PDF を開く")
        self.act_append = act("結合…", self.append_dialog, None, "別の PDF を末尾に結合する")
        self.act_save = act("保存", self.save_dialog, "Ctrl+S", "編集結果を新しい PDF として保存する")
        tb.addSeparator()
        self.act_undo = act("元に戻す", self.model.undo, "Ctrl+Z")
        self.act_redo = act("やり直し", self.model.redo, "Ctrl+Y")
        tb.addSeparator()
        self.act_rot_l = act("左回転", lambda: self._rotate(-90), "Ctrl+Left", "選択ページを左に 90° 回転")
        self.act_rot_r = act("右回転", lambda: self._rotate(90), "Ctrl+Right", "選択ページを右に 90° 回転")
        self.act_delete = act("削除", self._delete, "Delete", "選択ページを削除")
        self.act_select_all = act(
            "全選択", self._select_all, QKeySequence.StandardKey.SelectAll, "すべてのページを選択"
        )
        tb.addSeparator()
        tb.addWidget(QLabel(" サムネイル: "))
        self.size_combo = QComboBox()
        for name in THUMB_SIZES:
            self.size_combo.addItem(name)
        saved_size = self.settings.value("thumb_size", "中")
        self.size_combo.setCurrentText(saved_size if saved_size in THUMB_SIZES else "中")
        self.view.set_thumbnail_width(THUMB_SIZES[self.size_combo.currentText()])
        self.size_combo.currentTextChanged.connect(self._on_thumb_size_changed)
        tb.addWidget(self.size_combo)

    def _build_statusbar(self) -> None:
        sb = self.statusBar()
        self.lbl_doc = QLabel("PDF を開いてください（ドラッグ&ドロップ可）")
        sb.addWidget(self.lbl_doc, 1)
        self.lbl_gs = QLabel()
        sb.addPermanentWidget(self.lbl_gs)
        self.gs_recheck_btn = QPushButton("再検出")
        self.gs_recheck_btn.setFlat(True)
        self.gs_recheck_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.gs_recheck_btn.clicked.connect(self._recheck_ghostscript)
        sb.addPermanentWidget(self.gs_recheck_btn)
        self._update_gs_label()

    def _update_gs_label(self) -> None:
        if self.gs_path:
            self.lbl_gs.setText(f"Ghostscript {self.gs_version or ''}".strip())
            self.lbl_gs.setStyleSheet("")
            self.lbl_gs.setToolTip(str(self.gs_path))
            self.gs_recheck_btn.setVisible(False)
        else:
            self.lbl_gs.setText("Ghostscript 未検出 — 圧縮機能は使えません")
            self.lbl_gs.setStyleSheet("color: #b00;")
            self.lbl_gs.setToolTip("README の手順でインストールしてください（winget または公式インストーラ）")
            self.gs_recheck_btn.setVisible(True)

    def _recheck_ghostscript(self) -> None:
        """インストール後に再起動しなくても検出し直せるようにする。"""
        getattr(find_ghostscript, "cache_clear", lambda: None)()
        self.gs_path = find_ghostscript()
        self.gs_version = ghostscript_version(self.gs_path) if self.gs_path else None
        self._update_gs_label()
        for p in self.panels:
            p.on_document_changed()
        self.statusBar().showMessage(
            "Ghostscript を検出しました。" if self.gs_path else "Ghostscript が見つかりませんでした。", 5000
        )

    # ------------------------------------------------------------------
    # 状態
    # ------------------------------------------------------------------
    @property
    def page_list(self) -> PageList | None:
        return self.model.page_list

    def current_source(self) -> Path | None:
        pl = self.page_list
        return pl[0].source_path if pl and len(pl) else None

    def password_for(self, path: Path) -> str | None:
        """指定ファイルの、開くときに使ったパスワード（無ければ None）。"""
        return self.thumb_worker._passwords.get(path)

    def passwords_dict(self) -> dict[Path, str]:
        """現在把握している全ファイルのパスワード一覧（export_refs などに渡す用）。"""
        return dict(self.thumb_worker._passwords)

    def _on_document_changed(self) -> None:
        pl = self.page_list
        has = pl is not None and len(pl) > 0
        for a in (self.act_save, self.act_append, self.act_rot_l, self.act_rot_r, self.act_delete):
            a.setEnabled(has)
        self.act_undo.setEnabled(bool(pl and pl.can_undo))
        self.act_redo.setEnabled(bool(pl and pl.can_redo))
        if has:
            srcs = pl.sources
            name = srcs[0].name + (f" ほか {len(srcs) - 1} 件" if len(srcs) > 1 else "")
            mod = "（変更あり）" if pl.is_modified else ""
            self.lbl_doc.setText(f"{name}  {len(pl)} ページ{mod}")
            self.setWindowTitle(f"{name} - {APP_TITLE}")
        else:
            self.lbl_doc.setText("PDF を開いてください（ドラッグ&ドロップ可）")
            self.setWindowTitle(APP_TITLE)
        for p in self.panels:
            p.on_document_changed()

    def _on_selection_changed(self, rows: list[int]) -> None:
        pl = self.page_list
        if pl and len(pl):
            base = self.lbl_doc.text().split("  |")[0]
            self.lbl_doc.setText(base + (f"  |  {len(rows)} ページ選択" if rows else ""))
        for p in self.panels:
            p.on_selection_changed(rows)

    # ------------------------------------------------------------------
    # ファイル操作
    # ------------------------------------------------------------------
    def show_error(self, message: str, title: str = "エラー") -> None:
        QMessageBox.critical(self, title, message)

    def _ask_password(self, path: Path) -> str | None:
        pw, ok = QInputDialog.getText(
            self, "パスワード", f"{path.name} はパスワード付きです。\nパスワードを入力してください:",
            QLineEdit.EchoMode.Password,
        )
        return pw if ok and pw else None

    def _load_with_password(self, loader: Callable[[str | None], object], path: Path):
        """暗号化なら聞き直す。キャンセルで None。"""
        password: str | None = None
        while True:
            try:
                return loader(password), password
            except PdfEncryptedError:
                password = self._ask_password(path)
                if password is None:
                    return None, None
            except PdfError as e:
                self.show_error(e.message)
                return None, None

    def open_pdf(self, path: Path | str) -> bool:
        path = Path(path)
        if self.page_list is not None and self.page_list.is_modified:
            if not self._confirm_discard():
                return False
        pl, password = self._load_with_password(lambda pw: PageList.from_pdf(path, pw), path)
        if pl is None:
            return False
        self.thumb_worker.close_documents()
        if password:
            self.model.set_password(path, password)
        self.model.set_page_list(pl)
        return True

    def append_pdf(self, path: Path | str) -> bool:
        path = Path(path)
        if self.page_list is None:
            return self.open_pdf(path)
        n, password = self._load_with_password(lambda pw: self.model.append_pdf(path, pw), path)
        return n is not None

    def insert_pdfs(self, paths: list[Path], at: int) -> int:
        """PDF を at 番目の直前へ順に結合する（ドロップ結合用）。結合したページ数を返す。

        パスワード付き・破損など読めないファイルはそこで止め、それまでの分は結合済みのまま残す。
        """
        pl = self.page_list
        if pl is None or len(pl) == 0:
            return 0
        start = at = max(0, min(at, len(pl)))
        for path in paths:
            n, _pw = self._load_with_password(lambda pw, p=path, a=at: self.model.insert_pdf(p, a, pw), path)
            if n is None:
                break
            at += n
        total = at - start
        if total:
            self.view.select_rows(range(start, at))
            self.statusBar().showMessage(f"{total} ページを結合しました（{len(paths)} ファイル）", 6000)
        return total

    def _on_files_dropped(self, paths: list, at: int, replace: bool) -> None:
        """PDF・画像ファイルのドロップ/選択。文書を開いていれば結合、Shift 押下か未オープンなら開く。

        画像（JPG/PNG…）は一時 PDF に変換してから同じ流れに乗せる。変換は時間がかかりうるので別スレッド。
        """
        paths = [Path(p) for p in paths]
        if not paths:
            return
        if not any(is_image(p) for p in paths):
            self._merge_files(paths, at, replace)
            return
        self.statusBar().showMessage("画像を変換しています…")
        self.run_worker(
            convert_images, paths,
            on_finished=lambda converted: self._merge_files(converted, at, replace),
        )

    def _merge_files(self, paths: list[Path], at: int, replace: bool) -> None:
        if self.page_list is None or len(self.page_list) == 0 or replace:
            if self.open_pdf(paths[0]) and len(paths) > 1:
                self.insert_pdfs(paths[1:], len(self.page_list))
        else:
            self.insert_pdfs(paths, at)

    def open_files(self, paths: list) -> None:
        """起動引数など: 先頭を開き、残りを末尾に結合する（PDF・画像どちらも可）。"""
        self._on_files_dropped(paths, 0, True)

    _FILE_FILTER = "PDF・画像 (*.pdf *.jpg *.jpeg *.png *.bmp *.gif *.tif *.tiff *.webp);;PDF (*.pdf);;画像 (*.jpg *.jpeg *.png *.bmp *.gif *.tif *.tiff *.webp)"

    def open_dialog(self) -> None:
        start = self.settings.value("last_dir", "")
        files, _ = QFileDialog.getOpenFileNames(self, "PDF・画像を開く", start, self._FILE_FILTER)
        if files:
            self.settings.setValue("last_dir", str(Path(files[0]).parent))
            self.open_files(files)         # 複数選ぶと 1 つ目を開き、残りを末尾に結合

    def append_dialog(self) -> None:
        start = self.settings.value("last_dir", "")
        files, _ = QFileDialog.getOpenFileNames(self, "結合する PDF・画像を選ぶ", start, self._FILE_FILTER)
        if not files:
            return
        self.settings.setValue("last_dir", str(Path(files[0]).parent))
        n = len(self.page_list) if self.page_list is not None else 0
        self._on_files_dropped(files, n, False)

    def save_dialog(self) -> None:
        pl = self.page_list
        if not pl:
            return
        default = pl.default_output_path()
        f, _ = QFileDialog.getSaveFileName(self, "名前を付けて保存", str(default), "PDF (*.pdf)")
        if not f:
            return
        try:
            out = pl.save(f)
        except PdfError as e:
            self.show_error(e.message)
            return
        self._on_document_changed()
        self.statusBar().showMessage(f"保存しました: {out.name}（{format_size(out.stat().st_size)}）", 8000)

    def _confirm_discard(self) -> bool:
        r = QMessageBox.question(
            self, "確認", "保存していない変更があります。破棄しますか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        return r == QMessageBox.StandardButton.Yes

    # ------------------------------------------------------------------
    # ページ操作（ツールバー）
    # ------------------------------------------------------------------
    def _rotate(self, delta: int) -> None:
        rows = self.view.selected_rows()
        if rows:
            self.model.rotate_pages(rows, delta)
            self.view.select_rows(rows)

    def _delete(self) -> None:
        rows = self.view.selected_rows()
        if not rows:
            return
        try:
            self.model.remove_pages(rows)
        except ValueError as e:
            self.show_error(str(e))

    def _select_all(self) -> None:
        pl = self.page_list
        if pl:
            self.view.select_rows(range(len(pl)))

    def _on_thumb_size_changed(self, name: str) -> None:
        self.view.set_thumbnail_width(THUMB_SIZES[name])
        self.settings.setValue("thumb_size", name)

    # ------------------------------------------------------------------
    # ワーカー起動口（パネルから使う）
    # ------------------------------------------------------------------
    def run_worker(
        self,
        fn: Callable,
        *args,
        on_finished: Callable[[object], None] | None = None,
        on_error: Callable[[str, object], None] | None = None,
        on_progress: Callable[[object], None] | None = None,
        on_cancelled: Callable[[], None] | None = None,
        **kwargs,
    ) -> Worker:
        """fn を別スレッドで実行する。コールバックは主スレッドで呼ばれる。戻り値の Worker.cancel() で中断。"""
        w = Worker(fn, *args, **kwargs)
        self._workers.append(w)

        def _done(*_):
            if w in self._workers:
                self._workers.remove(w)

        if on_finished:
            w.signals.finished.connect(on_finished)
        w.signals.error.connect(on_error or (lambda msg, _e: self.show_error(msg)))
        if on_progress:
            w.signals.progress.connect(on_progress)
        if on_cancelled:
            w.signals.cancelled.connect(on_cancelled)
        for sig in (w.signals.finished, w.signals.error, w.signals.cancelled):
            sig.connect(_done)
        w.start()
        return w

    # ------------------------------------------------------------------
    # プレビュー（ダブルクリック）
    # ------------------------------------------------------------------
    def _preview_page(self, row: int) -> None:
        pl = self.page_list
        if not pl or not 0 <= row < len(pl):
            return
        ref = pl[row]
        pw = self.thumb_worker._passwords.get(ref.source_path)

        def render():
            with PdfDocument(ref.source_path, password=pw) as doc:
                return pil_to_qimage(doc.render_page(ref.source_index, dpi=110, rotation=ref.rotation))

        def show(img: QImage):
            dlg = PagePreviewDialog(img, f"{row + 1} ページ目 - {ref.source_path.name}", self)
            dlg.show()

        self.run_worker(render, on_finished=show)

    # ------------------------------------------------------------------
    # D&D / 終了
    # ------------------------------------------------------------------
    @staticmethod
    def _is_droppable(local_file: str) -> bool:
        return local_file.lower().endswith(".pdf") or is_image(local_file)

    def dragEnterEvent(self, e: QDragEnterEvent) -> None:
        if any(self._is_droppable(u.toLocalFile()) for u in e.mimeData().urls()):
            e.acceptProposedAction()

    def dropEvent(self, e: QDropEvent) -> None:
        paths = [Path(u.toLocalFile()) for u in e.mimeData().urls() if self._is_droppable(u.toLocalFile())]
        if not paths:
            return
        # サムネイル以外（余白・タブ等）へのドロップは末尾に結合。未オープンなら開く。
        # Shift 押下なら（開いていても）開き直す。
        replace = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        at = len(self.page_list) if self.page_list is not None else 0
        self._on_files_dropped(paths, at, replace)
        e.acceptProposedAction()

    def closeEvent(self, e: QCloseEvent) -> None:
        if self.page_list is not None and self.page_list.is_modified and not self._confirm_discard():
            e.ignore()
            return
        if self._workers:
            r = QMessageBox.question(
                self, "確認",
                f"実行中の処理が {len(self._workers)} 件あります。中断して終了しますか？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if r != QMessageBox.StandardButton.Yes:
                e.ignore()
                return
            for w in list(self._workers):
                w.cancel()
        self.thumb_worker.stop()
        QThreadPool.globalInstance().waitForDone(5000)
        super().closeEvent(e)
