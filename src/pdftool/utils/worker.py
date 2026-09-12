"""非同期処理の基盤（PySide6）。

2 種類ある:

  Worker (QRunnable)
      任意の関数を QThreadPool で 1 回実行する。圧縮・書き出し・バッチ用。
      関数が `progress=` / `cancel=` キーワードを受け取るなら自動で注入する
      （core の compress_to_target などがこの規約）。

  ThumbnailWorker (QThread)
      サムネイル描画専用の常駐スレッド。要求はスタック（後入れ先出し）で捌く。
      画面に見えている（＝直近に要求された）ページを優先するため。
      PdfDocument はこのスレッドが自分で開いて保持する。

スレッド境界のルール:
  - core の例外はすべて `error` シグナルで文字列化して主スレッドへ渡す
  - シグナルの引数に PIL Image は載せない。QImage に変換してから emit する
"""

from __future__ import annotations

import inspect
import threading
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from PIL import Image
from PySide6.QtCore import QObject, QRunnable, QThread, QThreadPool, Signal, Slot
from PySide6.QtGui import QImage

from pdftool.core.document import PdfDocument, PdfError


def pil_to_qimage(img: Image.Image) -> QImage:
    """PIL Image (RGB/RGBA) → QImage。バッファをコピーして独立させる。"""
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    fmt = QImage.Format.Format_RGB888 if img.mode == "RGB" else QImage.Format.Format_RGBA8888
    data = img.tobytes()
    bpl = img.width * (3 if img.mode == "RGB" else 4)
    return QImage(data, img.width, img.height, bpl, fmt).copy()


# ---------------------------------------------------------------------------
# 汎用ワーカー
# ---------------------------------------------------------------------------
class WorkerSignals(QObject):
    finished = Signal(object)          # 関数の戻り値
    error = Signal(str, object)        # (日本語メッセージ, 例外オブジェクト)
    progress = Signal(object)          # 関数が progress() に渡したもの
    cancelled = Signal()


class Worker(QRunnable):
    """`Worker(fn, *args, **kwargs)` を `QThreadPool.globalInstance().start()` に渡す。

    fn のシグネチャに `progress` / `cancel` があれば、それぞれ
    `signals.progress.emit` / `self.is_cancelled` を注入する。
    """

    def __init__(self, fn: Callable[..., Any], *args, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()
        self._cancel = threading.Event()
        self.setAutoDelete(True)

    def cancel(self) -> None:
        self._cancel.set()

    def is_cancelled(self) -> bool:
        return self._cancel.is_set()

    @Slot()
    def run(self) -> None:
        kwargs = dict(self.kwargs)
        try:
            params = inspect.signature(self.fn).parameters
        except (TypeError, ValueError):
            params = {}
        if "progress" in params and "progress" not in kwargs:
            kwargs["progress"] = self.signals.progress.emit
        if "cancel" in params and "cancel" not in kwargs:
            kwargs["cancel"] = self.is_cancelled
        try:
            result = self.fn(*self.args, **kwargs)
        except Exception as e:  # noqa: BLE001 — すべて UI に届ける
            if self._cancel.is_set() or type(e).__name__ == "CompressCancelled":
                self.signals.cancelled.emit()
                return
            msg = getattr(e, "message", None) or f"{type(e).__name__}: {e}"
            traceback.print_exc()
            self.signals.error.emit(msg, e)
            return
        self.signals.finished.emit(result)

    def start(self, pool: QThreadPool | None = None) -> "Worker":
        (pool or QThreadPool.globalInstance()).start(self)
        return self


# ---------------------------------------------------------------------------
# サムネイル専用スレッド
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ThumbRequest:
    key: str                # キャッシュキー（モデル側が決める）
    source_path: Path
    source_index: int
    rotation: int
    width: int


class ThumbnailWorker(QThread):
    """サムネイルを描画して `ready(key, QImage)` を emit する常駐スレッド。"""

    ready = Signal(str, QImage)
    failed = Signal(str, str)          # (key, 日本語メッセージ)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._stack: list[ThumbRequest] = []
        self._queued: set[str] = set()
        self._cv = threading.Condition()
        self._stop = False
        self._docs: dict[Path, PdfDocument] = {}
        self._passwords: dict[Path, str] = {}

    # -- 主スレッドから呼ぶ ------------------------------------------------
    def set_password(self, path: Path, password: str) -> None:
        with self._cv:
            self._passwords[path] = password

    def request(self, req: ThumbRequest) -> None:
        with self._cv:
            if req.key in self._queued:
                return
            self._stack.append(req)
            self._queued.add(req.key)
            self._cv.notify()

    def clear_queue(self) -> None:
        """未処理の要求を捨てる（ファイルを閉じた・サイズを変えたとき）。"""
        with self._cv:
            self._stack.clear()
            self._queued.clear()

    def close_documents(self) -> None:
        """開いている PdfDocument を閉じる要求（スレッド側で処理）。"""
        with self._cv:
            self._stack.clear()
            self._queued.clear()
            self._close_pending = True
            self._cv.notify()

    def stop(self) -> None:
        with self._cv:
            self._stop = True
            self._cv.notify()
        self.wait(5000)

    # -- スレッド本体 ---------------------------------------------------------
    _close_pending = False

    def _get_doc(self, path: Path) -> PdfDocument:
        doc = self._docs.get(path)
        if doc is None:
            doc = PdfDocument(path, password=self._passwords.get(path))
            self._docs[path] = doc
        return doc

    def _close_all(self) -> None:
        for d in self._docs.values():
            d.close()
        self._docs.clear()

    def run(self) -> None:
        while True:
            with self._cv:
                while not self._stack and not self._stop and not self._close_pending:
                    self._cv.wait()
                if self._stop:
                    break
                if self._close_pending:
                    self._close_pending = False
                    self._close_all()
                    continue
                req = self._stack.pop()          # 後入れ先出し
                self._queued.discard(req.key)
            try:
                img = self._get_doc(req.source_path).render_thumbnail(
                    req.source_index, width=req.width, rotation=req.rotation
                )
                self.ready.emit(req.key, pil_to_qimage(img))
            except PdfError as e:
                self.failed.emit(req.key, e.message)
            except Exception as e:  # noqa: BLE001
                self.failed.emit(req.key, f"{type(e).__name__}: {e}")
        self._close_all()
