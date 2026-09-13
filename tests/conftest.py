"""テスト用フィクスチャ。サンプル PDF は毎回 tmp_path に生成する（リポジトリにバイナリを置かない）。"""

from __future__ import annotations

import os
import random
import time
from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfWriter

# UI テストは常に offscreen（GUI テストファイルより先に import される conftest で設定する）
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _noisy_image(w: int, h: int, seed: int) -> Image.Image:
    """圧縮の効果が測れるよう、ノイズ入りのカラー画像を作る。"""
    rng = random.Random(seed)
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        base = (y * 255) // h
        for x in range(w):
            px[x, y] = (base, (x * 255) // w, rng.randrange(256))
    return img


def make_image_pdf(path: Path, pages: int = 3, size: tuple[int, int] = (600, 800)) -> Path:
    """画像を 1 ページ 1 枚貼った PDF を Pillow で生成する。150dpi 相当。"""
    imgs = [_noisy_image(*size, seed=i) for i in range(pages)]
    imgs[0].save(path, save_all=True, append_images=imgs[1:], resolution=150)
    return path


def make_blank_pdf(path: Path, pages: int = 5, width: float = 595, height: float = 842) -> Path:
    """白紙ページだけの PDF（ページ操作テスト用。軽い）。"""
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=width, height=height)
    w.write(path)
    return path


@pytest.fixture
def image_pdf(tmp_path: Path) -> Path:
    # 日本語・空白入りのファイル名で作る（非機能要件 3）
    return make_image_pdf(tmp_path / "進路 資料.pdf")


@pytest.fixture
def blank_pdf(tmp_path: Path) -> Path:
    return make_blank_pdf(tmp_path / "白紙.pdf")


@pytest.fixture
def encrypted_pdf(tmp_path: Path, image_pdf: Path) -> Path:
    out = tmp_path / "暗号化.pdf"
    w = PdfWriter(clone_from=str(image_pdf))
    w.encrypt("pw")
    w.write(out)
    return out


@pytest.fixture
def broken_pdf(tmp_path: Path) -> Path:
    out = tmp_path / "破損.pdf"
    out.write_bytes(b"%PDF-1.4\nthis is not a pdf")
    return out


@pytest.fixture
def empty_pdf(tmp_path: Path) -> Path:
    out = tmp_path / "空.pdf"
    PdfWriter().write(out)
    return out


# ---------------------------------------------------------------------------
# UI（offscreen）共通フィクスチャ
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def wait_until(cond, timeout: float = 10.0) -> bool:
    """条件が満たされるまでイベントループを回す（QThread/QThreadPool の結果待ち用）。"""
    from PySide6.QtWidgets import QApplication

    t0 = time.time()
    while time.time() - t0 < timeout:
        QApplication.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def win(app, tmp_path: Path):
    from PySide6.QtCore import QSettings

    from pdftool.ui.main_window import MainWindow

    # MainWindow は QSettings("pdftool", "PDFUtility") で実レジストリに書き込む。
    # テストでユーザーの実際の設定を汚さないよう、一時フォルダの ini ファイルへ逃がす。
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))

    w = MainWindow()
    w._confirm_discard = lambda: True  # モーダルは offscreen で固まるので潰す
    w.resize(1000, 700)
    w.show()
    yield w
    w.thumb_worker.stop()
    w.close()
