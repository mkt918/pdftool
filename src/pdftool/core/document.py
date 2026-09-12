"""PDF の読み込み・ページ情報・レンダリング（pypdfium2）。

GUI 非依存。PySide6 を import しないこと。

スレッドに関する注意:
    pdfium はスレッドセーフではない。本モジュールは全 pdfium 呼び出しを
    モジュール共通の RLock で直列化するので、複数スレッドから同時に呼んでも
    壊れないが、並列には走らない。サムネイル生成はワーカー1本で回すこと。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

# pdfium 呼び出しを直列化するロック（プロセス内で共通）
_PDFIUM_LOCK = threading.RLock()

# pdfium のエラーコード（pypdfium2.raw.FPDF_ERR_*）
_ERR_SUCCESS = 0
_ERR_FILE = 2
_ERR_FORMAT = 3
_ERR_PASSWORD = 4
_ERR_SECURITY = 5


# ---------------------------------------------------------------------------
# 例外
# ---------------------------------------------------------------------------
class PdfError(Exception):
    """PDF 処理の基底例外。message は日本語でユーザーに見せられる文。"""

    def __init__(self, message: str, path: Path | None = None):
        super().__init__(message)
        self.message = message
        self.path = path


class PdfNotFoundError(PdfError):
    """ファイルが存在しない。"""


class PdfEncryptedError(PdfError):
    """パスワード付き PDF（未指定または誤り）。"""


class PdfBrokenError(PdfError):
    """PDF として読み取れない（破損・非PDF）。"""


class PdfEmptyError(PdfError):
    """ページが 0 枚。"""


# ---------------------------------------------------------------------------
# データ型
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PageInfo:
    index: int          # 0 始まり
    width_pt: float     # ポイント（1/72 インチ）。/Rotate 適用後の見た目の幅
    height_pt: float
    rotation: int       # PDF 内に保存されている /Rotate（0/90/180/270）


@dataclass(frozen=True)
class PdfInfo:
    path: Path
    page_count: int
    file_size: int      # バイト
    encrypted: bool     # パスワードが必要か（probe 時点で開けなかった）


# ---------------------------------------------------------------------------
# 本体
# ---------------------------------------------------------------------------
def _translate_error(err: pdfium.PdfiumError, path: Path) -> PdfError:
    code = getattr(err, "err_code", None)
    if code in (_ERR_PASSWORD, _ERR_SECURITY):
        return PdfEncryptedError(f"パスワード付きの PDF です: {path.name}", path)
    if code == _ERR_FILE:
        return PdfBrokenError(f"ファイルを開けませんでした: {path.name}", path)
    if code == _ERR_SUCCESS:
        # pdfium はページ 0 枚の PDF を「Success」コード付きの失敗として返す
        return PdfEmptyError(f"ページが無い PDF です: {path.name}", path)
    return PdfBrokenError(f"PDF として読み取れません（破損の可能性）: {path.name}", path)


class PdfDocument:
    """1 つの PDF を開き、ページ情報の取得とレンダリングを行う。

    `with open_document(path) as doc:` の形で使うこと。
    """

    def __init__(self, path: Path | str, password: str | None = None):
        self.path = Path(path)
        if not self.path.is_file():
            raise PdfNotFoundError(f"ファイルが見つかりません: {self.path}", self.path)
        self.file_size = self.path.stat().st_size
        with _PDFIUM_LOCK:
            try:
                self._doc = pdfium.PdfDocument(str(self.path), password=password)
            except pdfium.PdfiumError as e:
                raise _translate_error(e, self.path) from e
            self.page_count = len(self._doc)
        if self.page_count == 0:
            self.close()
            raise PdfEmptyError(f"ページが無い PDF です: {self.path.name}", self.path)
        self._closed = False

    # -- ライフサイクル -----------------------------------------------------
    def close(self) -> None:
        if getattr(self, "_closed", True):
            return
        with _PDFIUM_LOCK:
            self._doc.close()
        self._closed = True

    def __enter__(self) -> "PdfDocument":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    # -- ページ情報 ---------------------------------------------------------
    def _check_index(self, index: int) -> None:
        if not 0 <= index < self.page_count:
            raise IndexError(f"ページ番号が範囲外です: {index} (0..{self.page_count - 1})")

    def page_info(self, index: int) -> PageInfo:
        self._check_index(index)
        with _PDFIUM_LOCK:
            page = self._doc[index]
            try:
                w, h = page.get_size()
                rot = page.get_rotation()
            finally:
                page.close()
        return PageInfo(index=index, width_pt=w, height_pt=h, rotation=rot)

    def all_page_info(self) -> list[PageInfo]:
        return [self.page_info(i) for i in range(self.page_count)]

    # -- レンダリング -------------------------------------------------------
    def _render(self, index: int, scale: float, rotation: int) -> Image.Image:
        if rotation % 90 != 0:
            raise ValueError("rotation は 90 度単位で指定してください")
        with _PDFIUM_LOCK:
            page = self._doc[index]
            try:
                bitmap = page.render(scale=scale, rotation=rotation % 360)
                try:
                    img = bitmap.to_pil()
                finally:
                    bitmap.close()
            finally:
                page.close()
        # PIL 側で独立させる（pdfium のバッファ解放後も安全に使えるように）
        return img.copy()

    def render_page(
        self, index: int, *, dpi: float = 150.0, rotation: int = 0
    ) -> Image.Image:
        """ページを指定 dpi でレンダリングする。

        rotation: 追加で回す角度（0/90/180/270）。PDF 内の /Rotate は自動適用済み。
        戻り値は RGB の PIL Image。
        """
        self._check_index(index)
        if dpi <= 0:
            raise ValueError("dpi は正の値を指定してください")
        return self._render(index, scale=dpi / 72.0, rotation=rotation)

    def render_thumbnail(
        self, index: int, *, width: int = 180, rotation: int = 0
    ) -> Image.Image:
        """横幅 width px に収まるサムネイルをレンダリングする（縦長ページなら高さが超えることもある）。

        正確には「回転後の横幅が width になる」スケールで描画する。
        """
        self._check_index(index)
        if width <= 0:
            raise ValueError("width は正の値を指定してください")
        info = self.page_info(index)
        base_w = info.height_pt if (rotation % 180) == 90 else info.width_pt
        scale = width / base_w if base_w > 0 else 1.0
        return self._render(index, scale=scale, rotation=rotation)


def open_document(path: Path | str, password: str | None = None) -> PdfDocument:
    """PdfDocument を開く。失敗時は PdfError のサブクラスを送出する。"""
    return PdfDocument(path, password=password)


def probe(path: Path | str) -> PdfInfo:
    """開かずに軽く調べる。暗号化 PDF は encrypted=True, page_count=-1 で返す。

    存在しない・破損・0 ページは例外。
    """
    p = Path(path)
    try:
        with PdfDocument(p) as doc:
            return PdfInfo(path=p, page_count=doc.page_count, file_size=doc.file_size, encrypted=False)
    except PdfEncryptedError:
        return PdfInfo(path=p, page_count=-1, file_size=p.stat().st_size, encrypted=True)
