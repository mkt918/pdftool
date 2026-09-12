from pathlib import Path

import pytest
from PIL import Image

from pdftool.core.document import (
    PdfBrokenError,
    PdfDocument,
    PdfEmptyError,
    PdfEncryptedError,
    PdfNotFoundError,
    open_document,
    probe,
)


def test_open_and_page_info(image_pdf: Path):
    with open_document(image_pdf) as doc:
        assert doc.page_count == 3
        assert doc.file_size == image_pdf.stat().st_size
        info = doc.page_info(0)
        assert info.index == 0
        # 600x800px @150dpi → 288x384pt
        assert info.width_pt == pytest.approx(288, abs=1)
        assert info.height_pt == pytest.approx(384, abs=1)
        assert info.rotation == 0
        assert len(doc.all_page_info()) == 3


def test_render_page_dpi(image_pdf: Path):
    with open_document(image_pdf) as doc:
        img = doc.render_page(1, dpi=72)
        assert isinstance(img, Image.Image)
        assert img.mode == "RGB"
        assert img.size == (288, 384)
        img150 = doc.render_page(1, dpi=150)
        assert img150.size == (600, 800)


def test_render_thumbnail_width_and_rotation(image_pdf: Path):
    with open_document(image_pdf) as doc:
        t = doc.render_thumbnail(0, width=180)
        assert t.width == 180
        assert t.height == 240
        r = doc.render_thumbnail(0, width=180, rotation=90)
        # 90 度回すと横長になり、その横幅が 180 になる
        assert r.width == 180
        assert r.height == 135


def test_index_and_arg_validation(image_pdf: Path):
    with open_document(image_pdf) as doc:
        with pytest.raises(IndexError):
            doc.page_info(3)
        with pytest.raises(ValueError):
            doc.render_page(0, dpi=0)
        with pytest.raises(ValueError):
            doc.render_page(0, rotation=45)


def test_errors(tmp_path: Path, encrypted_pdf: Path, broken_pdf: Path, empty_pdf: Path):
    with pytest.raises(PdfNotFoundError):
        open_document(tmp_path / "ない.pdf")
    with pytest.raises(PdfEncryptedError) as ei:
        open_document(encrypted_pdf)
    assert "パスワード" in ei.value.message
    with pytest.raises(PdfEncryptedError):
        open_document(encrypted_pdf, password="wrong")
    with open_document(encrypted_pdf, password="pw") as doc:
        assert doc.page_count == 3
    with pytest.raises(PdfBrokenError):
        open_document(broken_pdf)
    with pytest.raises(PdfEmptyError):
        open_document(empty_pdf)


def test_probe(image_pdf: Path, encrypted_pdf: Path, broken_pdf: Path):
    info = probe(image_pdf)
    assert info.page_count == 3 and not info.encrypted
    enc = probe(encrypted_pdf)
    assert enc.encrypted and enc.page_count == -1
    with pytest.raises(PdfBrokenError):
        probe(broken_pdf)


def test_close_is_idempotent(image_pdf: Path):
    doc = PdfDocument(image_pdf)
    doc.close()
    doc.close()
