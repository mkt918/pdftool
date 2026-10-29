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

from PySide6.QtCore import QCoreApplication, QSettings, QSize, Qt, QThreadPool, QUrl
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices, QDragEnterEvent, QDropEvent, QImage, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QToolBar,
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
from pdftool.ui.viewer import ViewerWidget
from pdftool.ui.thumbnail_view import THUMB_SIZES, ThumbnailModel, ThumbnailView
from pdftool.utils import updater
from pdftool.utils.gs_locator import find_ghostscript, ghostscript_version
from pdftool.utils.worker import ThumbnailWorker, Worker, pil_to_qimage

APP_TITLE = "PDF ユーティリティ"


def _render_ref_image(ref, password: str | None, dpi: int = 110) -> QImage:
    """プレビュー用に 1 ページを描画する（ワーカースレッドで呼ぶ）。回転・トリミング反映。"""
    with PdfDocument(ref.source_path, password=password) as doc:
        return pil_to_qimage(doc.render_page(ref.source_index, dpi=dpi, rotation=ref.rotation, crop=ref.crop))


class MainWindow(QMainWindow):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(APP_TITLE)
        self.resize(1260, 900)
        self.setAcceptDrops(True)

        # --- 設定の永続化（サムネイルサイズ・最後に使ったフォルダなど） ---
        # 組織名/アプリ名を先に設定してから引数無しで作る。
        # QSettings(org, app) の2引数版は常に NativeFormat 固定で、テストで
        # setDefaultFormat(IniFormat) を当てても効かない（実レジストリを触ってしまう）ため。
        QCoreApplication.setOrganizationName("pdftool")
        QCoreApplication.setApplicationName("PDFUtility")
        self.settings = QSettings()

        # --- Ghostscript 検出（無ければ圧縮だけ無効化） ---
        # 版番号は表示用だけなので、gs を実際に起動して調べるのは裏で行う（起動を待たせない）
        self.gs_path: Path | None = find_ghostscript()
        self.gs_version: str | None = None

        # --- サムネイル基盤 ---
        self.thumb_worker = ThumbnailWorker(self)
        self.thumb_worker.start()
        self.model = ThumbnailModel(self.thumb_worker, self)
        self.view = ThumbnailView(self.model)
        self.view.page_activated.connect(self._open_viewer_at)
        self.view.selection_changed.connect(self._on_selection_changed)
        self.view.files_dropped.connect(self._on_files_dropped)
        self.view.context_requested.connect(self._show_context_menu)
        self.model.page_list_changed.connect(self._on_document_changed)

        # --- 機能タブ ---
        self.tabs = QTabWidget()
        self.panels: list[PanelBase] = []
        for panel_cls in (ExportPanel, CompressPanel, PageOpsPanel, FinishPanel, BatchPanel):
            self._add_panel(panel_cls(self))

        # 左: サムネイル一覧 / ビューア の切り替え
        self.viewer = ViewerWidget(self._request_viewer_image)
        self.viewer.row_changed.connect(self._on_viewer_rows)
        self.viewer.exit_requested.connect(lambda: self.act_viewer.setChecked(False))
        self.viewer.save_ink_requested.connect(self._save_ink)
        self.viewer.ink_changed.connect(lambda: [p.on_ink_changed() for p in self.panels])
        self.left_stack = QStackedWidget()
        self.left_stack.addWidget(self.view)
        self.left_stack.addWidget(self.viewer)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.left_stack)
        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        # 起動時は「サムネイル大」が横に 3 ページ並ぶ幅（1 セル = 画像幅 + 24、余白・スクロールバー分を足す）
        left_w = 3 * (THUMB_SIZES["大"] + 24 + 2 * self.view.spacing()) + 48
        splitter.setSizes([left_w, 400])
        self.setCentralWidget(splitter)
        self.tabs.currentChanged.connect(self._on_tab_changed)

        self._build_toolbar()
        self._build_statusbar()
        self._workers: list[Worker] = []   # 実行中ワーカーの参照保持（GC 防止）
        self._bg_workers: list[Worker] = []   # ビューアの先読み描画（終了確認の対象外）
        self._closing = False
        self._on_document_changed()
        self._on_tab_changed(self.tabs.currentIndex())
        self._fetch_gs_version()

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
        self.act_viewer = QAction("ビューア", self)
        self.act_viewer.setCheckable(True)
        self.act_viewer.setStatusTip("ページを大きく表示する（見開き／1ページ）。Esc で一覧に戻る")
        self.act_viewer.toggled.connect(self._set_viewer_mode)
        tb.addAction(self.act_viewer)
        tb.addWidget(QLabel(" サムネイル: "))
        self.size_combo = QComboBox()
        for name in THUMB_SIZES:
            self.size_combo.addItem(name)
        saved_size = self.settings.value("thumb_size_v2", "大")      # 基準を「大」に変えたので別キーで保存
        self.size_combo.setCurrentText(saved_size if saved_size in THUMB_SIZES else "大")
        self.view.set_thumbnail_width(THUMB_SIZES[self.size_combo.currentText()])
        self.size_combo.currentTextChanged.connect(self._on_thumb_size_changed)
        tb.addWidget(self.size_combo)
        tb.addSeparator()
        # 右端に置き、いま押すと何が起きるかを文字と矢印で示す（隠すとき「パネルを隠す ▶」、隠れているとき「◀ パネルを表示」）
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)
        self.act_side = QAction(self)
        self.act_side.setCheckable(True)
        self.act_side.setChecked(True)
        self.act_side.setShortcut(QKeySequence("Ctrl+B"))
        self.act_side.toggled.connect(self._on_side_toggled)
        tb.addAction(self.act_side)
        self._on_side_toggled(True)

    def _on_side_toggled(self, shown: bool) -> None:
        self.tabs.setVisible(shown)
        self.act_side.setText("パネルを隠す ▶" if shown else "◀ パネルを表示")
        self.act_side.setStatusTip("右側の機能パネルを隠して、ページを広く表示します（Ctrl+B）" if shown
                                   else "右側の機能パネルを表示します（Ctrl+B）")
        self.act_side.setToolTip(self.act_side.statusTip())

    def _build_statusbar(self) -> None:
        sb = self.statusBar()
        self.lbl_doc = QLabel("PDF を開いてください（ドラッグ&ドロップ可）")
        sb.addWidget(self.lbl_doc, 1)
        self.lbl_gs = QLabel()
        self.btn_update = QPushButton()
        self.btn_update.setVisible(False)
        self.btn_update.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_update.clicked.connect(self._start_update)
        self._update_info: updater.UpdateInfo | None = None
        sb.addPermanentWidget(self.btn_update)
        sb.addPermanentWidget(self.lbl_gs)
        self.gs_recheck_btn = QPushButton("再検出")
        self.gs_recheck_btn.setFlat(True)
        self.gs_recheck_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.gs_recheck_btn.clicked.connect(self._recheck_ghostscript)
        sb.addPermanentWidget(self.gs_recheck_btn)
        self._update_gs_label()

    # ------------------------------------------------------------------
    # 自動更新（GitHub Releases）
    # ------------------------------------------------------------------
    def check_for_update(self) -> None:
        """起動時に裏で最新版を確認する。通信できなくても何も出さない。"""
        self.run_worker(updater.check_latest, on_finished=self._on_update_checked, on_error=lambda *_: None,
                        background=True)

    def _on_update_checked(self, info) -> None:
        if info is None:
            return
        self._update_info = info
        self.btn_update.setText(f"新しい版 v{info.version} に更新")
        self.btn_update.setVisible(True)

    def _start_update(self) -> None:
        info = self._update_info
        if info is None:
            return
        if not updater.is_frozen():
            QMessageBox.information(self, "更新", "開発実行中は自動更新できません（exe 版で使えます）。")
            return
        if updater.is_onefile():
            QMessageBox.information(
                self, "更新",
                f"1 ファイル版は自動更新できません。\n次のページから v{info.version} の exe をダウンロードして入れ替えてください。\n\n"
                f"{updater.RELEASES_URL}")
            QDesktopServices.openUrl(QUrl(updater.RELEASES_URL))
            return
        if self.has_unsaved() and not self._confirm_discard("更新する"):
            return
        if QMessageBox.question(
            self, "更新", f"v{info.version} をダウンロードして再起動します。よろしいですか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        exe = Path(sys.executable)
        self.btn_update.setEnabled(False)

        def progress(p) -> None:
            done, total = p
            self.btn_update.setText(f"ダウンロード中… {done * 100 // total}%" if total else "ダウンロード中…")

        def finished(path) -> None:
            updater.apply_and_restart(Path(path), exe)
            self._force_quit = True
            QApplication.quit()

        def failed(msg: str, _e) -> None:
            self.btn_update.setEnabled(True)
            self.btn_update.setText(f"新しい版 v{info.version} に更新")
            self.show_error(f"更新に失敗しました。\n{msg}")

        self.run_worker(updater.prepare_update, info, exe.parent,
                        on_finished=finished, on_error=failed, on_progress=progress)

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

    def _fetch_gs_version(self) -> None:
        if not self.gs_path:
            return
        path = self.gs_path

        def done(version) -> None:
            if self.gs_path == path:
                self.gs_version = version
                self._update_gs_label()

        self.run_worker(ghostscript_version, path, on_finished=done, on_error=lambda *_: None, background=True)

    def _recheck_ghostscript(self) -> None:
        """インストール後に再起動しなくても検出し直せるようにする。"""
        getattr(find_ghostscript, "cache_clear", lambda: None)()
        self.gs_path = find_ghostscript()
        self.gs_version = None
        self._update_gs_label()
        self._fetch_gs_version()
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
        if self.left_stack.currentWidget() is self.viewer:
            if has:
                self.viewer.set_refs(list(pl.pages))
            else:
                self.act_viewer.setChecked(False)
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
        if self.has_unsaved():
            if not self._confirm_discard("開く"):
                return False
        pl, password = self._load_with_password(lambda pw: PageList.from_pdf(path, pw), path)
        if pl is None:
            return False
        self.thumb_worker.close_documents()
        if password:
            self.model.set_password(path, password)
        self.model.set_page_list(pl)
        self.viewer.clear_ink()          # 書き込みは文書ごと。前の文書の線は持ち越さない
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
        self.statusBar().clearMessage()      # 「画像を変換しています…」が残ると文書情報が隠れる
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

    def has_unsaved(self) -> bool:
        """保存していないページ編集か、保存していない書き込み（ペン・蛍光ペン）がある。"""
        return bool(self.page_list is not None and self.page_list.is_modified) or self.viewer.ink_dirty

    def _save_ink(self) -> None:
        """ビューアの「書き込みを保存…」: 仕上げタブを開き、書き込みを含める項目をオンにする。"""
        panel = next((p for p in self.panels if isinstance(p, FinishPanel)), None)
        if panel is None:
            return
        self.act_side.setChecked(True)
        self.tabs.setCurrentWidget(panel)
        panel.ink_box.setChecked(True)

    def _confirm_discard(self, action: str = "閉じる") -> bool:
        """保存していない変更を捨てて action（閉じる／開く／更新する）してよいか尋ねる。捨てるなら True。

        ボタンは「破棄して○○」と「戻る」。Yes/No だとどちらが何をするか分かりにくいため。
        """
        box = QMessageBox(QMessageBox.Icon.Warning, "保存していない変更があります",
                          "保存していない変更（ページの編集・書き込み）があります。\n"
                          f"このまま{action}と、それらは失われます。", parent=self)
        discard = box.addButton(f"破棄して{action}", QMessageBox.ButtonRole.DestructiveRole)
        back = box.addButton("戻る", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(back)
        box.setEscapeButton(back)
        box.exec()
        return box.clickedButton() is discard

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

    def _show_context_menu(self, global_pos) -> None:
        self._build_context_menu().exec(global_pos)

    def _build_context_menu(self) -> QMenu:
        rows = self.view.selected_rows()
        menu = QMenu(self)
        if len(rows) == 1:
            menu.addAction("プレビュー", lambda: self._preview_page(rows[0]))
            menu.addSeparator()
        if rows:
            menu.addAction("左に回転", lambda: self._rotate(-90))
            menu.addAction("右に回転", lambda: self._rotate(90))
            menu.addSeparator()
            menu.addAction("先頭へ移動", lambda: self._move_selected_to(0))
            menu.addAction("末尾へ移動", lambda: self._move_selected_to(len(self.page_list)))
            menu.addSeparator()
            menu.addAction(f"{len(rows)} ページを削除", self._delete)
            menu.addSeparator()
        menu.addAction(self.act_select_all)
        menu.addAction(self.act_undo)
        menu.addAction(self.act_redo)
        return menu

    def _move_selected_to(self, to: int) -> None:
        rows = self.view.selected_rows()
        if not rows:
            return
        self.model.move_pages(rows, to)
        at = to - sum(1 for r in rows if r < to)
        self.view.select_rows(range(at, at + len(rows)))

    def _select_all(self) -> None:
        pl = self.page_list
        if pl:
            self.view.select_rows(range(len(pl)))

    def _on_thumb_size_changed(self, name: str) -> None:
        self.view.set_thumbnail_width(THUMB_SIZES[name])
        self.settings.setValue("thumb_size_v2", name)

    # ------------------------------------------------------------------
    # ビューアモード / チェックモード
    # ------------------------------------------------------------------
    def _request_viewer_image(self, ref, on_ready, dpi: int = 150) -> None:
        if self._closing:                # 終了処理の後は新しい描画を始めない（終了中の pdfium に触れて落ちるため）
            return
        self.run_worker(_render_ref_image, ref, self.passwords_dict().get(ref.source_path), dpi,
                        on_finished=on_ready, on_error=lambda *_: on_ready(None), background=True)

    def _set_viewer_mode(self, on: bool) -> None:
        pl = self.page_list
        if on and not (pl and len(pl)):
            self.act_viewer.blockSignals(True)
            self.act_viewer.setChecked(False)
            self.act_viewer.blockSignals(False)
            return
        self.size_combo.setEnabled(not on)
        if on:
            rows = self.view.selected_rows()
            self.viewer.set_refs(list(pl.pages), rows[0] if rows else 0)
            self.left_stack.setCurrentWidget(self.viewer)
            self.viewer.setFocus()
        else:
            rows = self.view.selected_rows()
            self.left_stack.setCurrentWidget(self.view)
            if rows:
                self.view.scrollTo(self.model.index(rows[0]))
            self.view.setFocus()

    def _open_viewer_at(self, row: int) -> None:
        """サムネイルのダブルクリック: そのページからビューアで開く（Esc か「一覧に戻る」で戻る）。"""
        pl = self.page_list
        if not pl or not 0 <= row < len(pl):
            return
        self.view.select_rows([row])
        self.act_viewer.setChecked(True)

    def _on_viewer_rows(self, rows: list) -> None:
        """ビューアで見ているページを一覧側の選択にも反映する（回転・削除などがそのページに効く）。"""
        if rows and self.left_stack.currentWidget() is self.viewer:
            self.view.select_rows(rows)

    def _on_tab_changed(self, index: int) -> None:
        # 画像書き出しタブでは、クリックしたページにチェックマークを付けて選択を保つ
        self.view.set_check_mode(isinstance(self.tabs.widget(index), ExportPanel))

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
        background: bool = False,
        **kwargs,
    ) -> Worker:
        """fn を別スレッドで実行する。コールバックは主スレッドで呼ばれる。戻り値の Worker.cancel() で中断。

        background=True は裏の軽い処理（先読みの描画・更新の確認など）。「実行中の処理」に数えないので、
        閉じるときの確認ダイアログを出さない。
        """
        w = Worker(fn, *args, **kwargs)
        held = self._bg_workers if background else self._workers
        held.append(w)                     # シグナルの受け手が先に GC されないよう保持

        def _done(*_):
            if w in held:
                held.remove(w)

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
        """ダブルクリックしたページを拡大表示する。下の ＜ ＞ で前後のページへ移動できる。

        開いた時点のページ構成のスナップショットを見る（見ている間に並べ替えても表示は変わらない）。
        回転・トリミングは反映して表示する。
        """
        pl = self.page_list
        if not pl or not 0 <= row < len(pl):
            return
        refs = list(pl.pages)
        passwords = self.passwords_dict()
        n = len(refs)

        def title_for(r: int) -> str:
            return f"{r + 1} / {n} ページ目 - {refs[r].source_path.name}"

        def request(r: int, on_ready) -> None:
            self.run_worker(_render_ref_image, refs[r], passwords.get(refs[r].source_path), on_finished=on_ready)

        def show(img: QImage) -> None:
            dlg = PagePreviewDialog(img, title_for(row), self, count=n, row=row, request=request, title_fn=title_for)
            dlg.show()

        self.run_worker(_render_ref_image, refs[row], passwords.get(refs[row].source_path), on_finished=show)

    # ------------------------------------------------------------------
    # D&D / 終了
    # ------------------------------------------------------------------
    @staticmethod
    def _is_droppable(local_file: str) -> bool:
        return local_file.lower().endswith(".pdf") or is_image(local_file)

    def dragEnterEvent(self, e: QDragEnterEvent) -> None:
        if any(self._is_droppable(u.toLocalFile()) for u in e.mimeData().urls()):
            # 必ず Copy を返す。提案された Move をそのまま返すと、エクスプローラーが元ファイルを削除する
            e.setDropAction(Qt.DropAction.CopyAction)
            e.accept()

    def dropEvent(self, e: QDropEvent) -> None:
        paths = [Path(u.toLocalFile()) for u in e.mimeData().urls() if self._is_droppable(u.toLocalFile())]
        if not paths:
            return
        # サムネイル以外（余白・タブ等）へのドロップは末尾に結合。未オープンなら開く。
        # Shift 押下なら（開いていても）開き直す。
        replace = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        at = len(self.page_list) if self.page_list is not None else 0
        self._on_files_dropped(paths, at, replace)
        e.setDropAction(Qt.DropAction.CopyAction)     # Move を返すと元ファイルが消される
        e.accept()

    def closeEvent(self, e: QCloseEvent) -> None:
        if getattr(self, "_force_quit", False):      # 更新による再起動（確認は済み）
            self._shutdown_threads()
            e.accept()
            return
        if self.has_unsaved() and not self._confirm_discard():
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
        self._shutdown_threads()
        super().closeEvent(e)

    def _shutdown_threads(self) -> None:
        """描画スレッドを止めてから終わる（動いたまま pdfium を片付けると異常終了することがある）。"""
        self._closing = True
        for w in list(self._workers) + list(self._bg_workers):
            w.cancel()
        self.thumb_worker.stop()
        QThreadPool.globalInstance().waitForDone(5000)
