"""ページ操作タブに置く「白紙ページの検出」と「余白トリミング」のグループ。

どちらもページ数ぶん描画して調べるので別スレッドで実行する。結果を適用する時点で
ページ構成が変わっていたら（調べている間に削除・並べ替えをした等）インデックスがずれるので
適用せずに知らせる（PageList は変更のたびに別の tuple に置き換わるので、`pages is snapshot` で判定できる）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from pdftool.core.blank import (
    SENSITIVITY_LOOSE,
    SENSITIVITY_NORMAL,
    SENSITIVITY_STRICT,
    detect_blank_pages,
)
from pdftool.core.trim import ScanProgress, auto_crops, manual_crops

if TYPE_CHECKING:
    from pdftool.ui.main_window import MainWindow


class _ScanGroup(QGroupBox):
    """共通部分: 対象ページの決定、別スレッド実行、構成が変わっていないかの確認。"""

    def __init__(self, title: str, window: "MainWindow", parent: QWidget | None = None):
        super().__init__(title, parent)
        self.window_ = window
        self._worker = None
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)

    # -- フック（PageOpsPanel から転送される） -------------------------------
    def refresh(self) -> None:
        raise NotImplementedError

    def _targets(self) -> list[int]:
        """選択中のページ。何も選んでいなければ全ページ。"""
        pl = self.window_.page_list
        if pl is None:
            return []
        rows = self.window_.view.selected_rows()
        return rows if rows else list(range(len(pl)))

    def _target_text(self) -> str:
        pl = self.window_.page_list
        if pl is None or len(pl) == 0:
            return "PDF を開くと使えます"
        rows = self.window_.view.selected_rows()
        return f"対象: 選択中の {len(rows)} ページ" if rows else f"対象: 全 {len(pl)} ページ"

    def _start(self, fn, *args, on_done, **kwargs) -> None:
        pl = self.window_.page_list
        snapshot = pl.pages

        def finished(result):
            self._worker = None
            self._set_running(False)
            if self.window_.page_list is not pl or pl.pages is not snapshot:
                self.status_label.setText("調べている間にページの構成が変わったため、結果は適用しませんでした。もう一度実行してください。")
                return
            on_done(result)

        def failed(message, _exc):
            self._worker = None
            self._set_running(False)
            self.status_label.setText(f"エラー: {message}")

        def cancelled(*_):
            self._worker = None
            self._set_running(False)
            self.status_label.setText("キャンセルしました")

        def progress(p: ScanProgress):
            self.status_label.setText(p.message)

        self._set_running(True)
        self.status_label.setText("調べています…")
        self._worker = self.window_.run_worker(
            fn, *args, on_finished=finished, on_error=failed, on_cancelled=cancelled, on_progress=progress, **kwargs
        )

    def _set_running(self, running: bool) -> None:
        raise NotImplementedError

    def _cancel(self) -> None:
        if self._worker:
            self._worker.cancel()
            self.status_label.setText("キャンセル中…")


# ---------------------------------------------------------------------------
# 白紙ページ
# ---------------------------------------------------------------------------
class BlankPageGroup(_ScanGroup):
    def __init__(self, window: "MainWindow", parent: QWidget | None = None):
        super().__init__("白紙ページを探す", window, parent)
        lay = QVBoxLayout(self)
        hint = QLabel("スキャンした資料の裏白などを見つけて選択します。自動では消しません。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #666;")
        lay.addWidget(hint)

        row = QHBoxLayout()
        row.addWidget(QLabel("感度:"))
        self.sens_combo = QComboBox()
        self.sens_combo.addItem("厳密（ほぼ完全な白紙だけ）", SENSITIVITY_STRICT)
        self.sens_combo.addItem("標準（ページ番号だけ・汚れ程度は白紙扱い）", SENSITIVITY_NORMAL)
        self.sens_combo.addItem("ゆるめ（小さなスタンプ・数行の文字まで）", SENSITIVITY_LOOSE)
        self.sens_combo.setCurrentIndex(1)
        row.addWidget(self.sens_combo, 1)
        lay.addLayout(row)

        self.target_label = QLabel()
        lay.addWidget(self.target_label)
        btns = QHBoxLayout()
        self.find_btn = QPushButton("白紙ページを探して選択")
        self.find_btn.clicked.connect(self._find)
        btns.addWidget(self.find_btn)
        self.cancel_btn = QPushButton("中止")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel)
        btns.addWidget(self.cancel_btn)
        lay.addLayout(btns)
        lay.addWidget(self.status_label)
        self.refresh()

    def refresh(self) -> None:
        pl = self.window_.page_list
        self.target_label.setText(self._target_text())
        self.find_btn.setEnabled(bool(pl) and len(pl) > 0 and self._worker is None)

    def _set_running(self, running: bool) -> None:
        self.sens_combo.setEnabled(not running)
        self.cancel_btn.setEnabled(running)
        self.find_btn.setEnabled(not running and bool(self.window_.page_list))

    def _find(self) -> None:
        pl = self.window_.page_list
        if not pl:
            return
        targets = self._targets()
        refs = [pl[i] for i in targets]
        self._start(
            detect_blank_pages, refs,
            sensitivity=self.sens_combo.currentData(),
            passwords=self.window_.passwords_dict(),
            on_done=lambda found: self._show(targets, found),
        )

    def _show(self, targets: list[int], found: list[int]) -> None:
        rows = [targets[i] for i in found]
        if not rows:
            self.status_label.setText("白紙ページは見つかりませんでした。")
            return
        self.window_.view.select_rows(rows)
        pages = "、".join(str(r + 1) for r in rows[:12]) + ("…" if len(rows) > 12 else "")
        self.status_label.setText(
            f"{len(rows)} ページが白紙の可能性があります（{pages}）。選択しました。"
            "サムネイルで確認して、よければツールバーの「削除」（Delete キー）で消せます。"
            "間違えたら Ctrl+Z で戻せます。"
        )


# ---------------------------------------------------------------------------
# 余白トリミング
# ---------------------------------------------------------------------------
class TrimGroup(_ScanGroup):
    def __init__(self, window: "MainWindow", parent: QWidget | None = None):
        super().__init__("余白トリミング", window, parent)
        lay = QVBoxLayout(self)
        hint = QLabel(
            "余白を切り取ります。元のPDFは変わりません。保存・画像書き出し・仕上げに反映されます。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #666;")
        lay.addWidget(hint)
        self.target_label = QLabel()
        lay.addWidget(self.target_label)

        self.radio_auto = QRadioButton("自動（内容に合わせて切る）")
        self.radio_manual = QRadioButton("手動（外側から決まった長さを切る）")
        self.radio_auto.setChecked(True)
        group = QButtonGroup(self)
        group.addButton(self.radio_auto)
        group.addButton(self.radio_manual)
        lay.addWidget(self.radio_auto)

        auto_form = QFormLayout()
        self.pad_spin = QDoubleSpinBox()
        self.pad_spin.setRange(0.0, 50.0)
        self.pad_spin.setValue(5.0)
        self.pad_spin.setSuffix(" mm")
        auto_form.addRow("内容の周りに残す余白:", self.pad_spin)
        self.unify_check = QCheckBox("選択ページ全体で同じ範囲にそろえる")
        self.unify_check.setToolTip("同じ大きさのスキャン資料で、ページごとにサイズがばらつかないようにします")
        auto_form.addRow(self.unify_check)
        lay.addLayout(auto_form)

        lay.addWidget(self.radio_manual)
        man_form = QFormLayout()
        self.manual_spins: dict[str, QDoubleSpinBox] = {}
        for key, label in (("top", "上"), ("bottom", "下"), ("left", "左"), ("right", "右")):
            sp = QDoubleSpinBox()
            sp.setRange(0.0, 200.0)
            sp.setSuffix(" mm")
            self.manual_spins[key] = sp
            man_form.addRow(f"{label}:", sp)
        lay.addLayout(man_form)

        btns = QHBoxLayout()
        self.apply_btn = QPushButton("トリミングを適用")
        self.apply_btn.clicked.connect(self._apply)
        btns.addWidget(self.apply_btn)
        self.reset_btn = QPushButton("解除")
        self.reset_btn.setToolTip("対象ページのトリミングを元に戻す")
        self.reset_btn.clicked.connect(self._reset)
        btns.addWidget(self.reset_btn)
        self.cancel_btn = QPushButton("中止")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel)
        btns.addWidget(self.cancel_btn)
        lay.addLayout(btns)
        lay.addWidget(self.status_label)

        self.radio_auto.toggled.connect(self._sync_mode)
        self._sync_mode()
        self.refresh()

    def _sync_mode(self) -> None:
        auto = self.radio_auto.isChecked()
        self.pad_spin.setEnabled(auto)
        self.unify_check.setEnabled(auto)
        for sp in self.manual_spins.values():
            sp.setEnabled(not auto)

    def refresh(self) -> None:
        pl = self.window_.page_list
        self.target_label.setText(self._target_text())
        has = bool(pl) and len(pl) > 0 and self._worker is None
        self.apply_btn.setEnabled(has)
        self.reset_btn.setEnabled(has)

    def _set_running(self, running: bool) -> None:
        for w in (self.radio_auto, self.radio_manual, self.apply_btn, self.reset_btn):
            w.setEnabled(not running)
        self.cancel_btn.setEnabled(running)
        if running:
            for w in (self.pad_spin, self.unify_check, *self.manual_spins.values()):
                w.setEnabled(False)
        else:
            self._sync_mode()
            self.refresh()

    def _apply(self) -> None:
        pl = self.window_.page_list
        if not pl:
            return
        targets = self._targets()
        refs = list(pl.pages)
        passwords = self.window_.passwords_dict()
        if self.radio_auto.isChecked():
            self._start(
                auto_crops, refs, targets,
                pad_mm=self.pad_spin.value(), unify=self.unify_check.isChecked(), passwords=passwords,
                on_done=lambda m: self._applied(targets, m),
            )
        else:
            mm = {k: sp.value() for k, sp in self.manual_spins.items()}
            if not any(mm.values()):
                self.status_label.setText("切り取る長さ（mm）を入力してください。")
                return
            self._start(
                manual_crops, refs, targets,
                left_mm=mm["left"], top_mm=mm["top"], right_mm=mm["right"], bottom_mm=mm["bottom"],
                passwords=passwords,
                on_done=lambda m: self._applied(targets, m),
            )

    def _applied(self, targets: list[int], mapping: dict) -> None:
        try:
            self.window_.model.set_crops(mapping)
        except ValueError as e:
            self.status_label.setText(f"トリミングできませんでした: {e}")
            return
        skipped = len(targets) - len(mapping)
        msg = f"{len(mapping)} ページをトリミングしました。"
        if skipped > 0 and self.radio_auto.isChecked():
            msg += f"（白紙のため変更しなかったページ: {skipped}）"
        self.status_label.setText(msg + " Ctrl+Z で元に戻せます。")

    def _reset(self) -> None:
        targets = self._targets()
        pl = self.window_.page_list
        changed = {i: None for i in targets if pl is not None and pl[i].crop is not None}
        if not changed:
            self.status_label.setText("トリミングされているページはありません。")
            return
        self.window_.model.set_crops(changed)
        self.status_label.setText(f"{len(changed)} ページのトリミングを解除しました。")
