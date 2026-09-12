from pathlib import Path

import pytest
from PIL import Image

from conftest import make_image_pdf
from pdftool.core.document import PdfEncryptedError
from pdftool.core.export import (
    ExportCancelled,
    ExportOptions,
    export_pages,
    export_refs,
)
from pdftool.core.pageops import PageRef


def test_options_validation():
    ExportOptions()  # 既定値
    with pytest.raises(ValueError):
        ExportOptions(fmt="gif")
    with pytest.raises(ValueError):
        ExportOptions(dpi=0)
    with pytest.raises(ValueError):
        ExportOptions(jpeg_quality=0)
    o = ExportOptions(fmt="JPG")
    assert o.fmt == "jpg" and o.ext == ".jpg" and o.pil_format == "JPEG"


def test_export_all_pages_default_png(image_pdf: Path):
    r = export_pages(image_pdf, options=ExportOptions())  # 既定 150dpi
    assert r.output_dir.parent == image_pdf.parent
    assert r.output_dir.name.endswith("_進路 資料_png")
    assert len(r.files) == 3
    assert [f.name for f in r.files] == ["進路 資料_p001.png", "進路 資料_p002.png", "進路 資料_p003.png"]
    for f in r.files:
        assert f.exists()
        img = Image.open(f)
        assert img.format == "PNG"
        assert img.size == (600, 800)  # 元画像と同じ解像度（150dpiで作成→150dpiで書き出し）


def test_export_dpi_and_jpeg(image_pdf: Path):
    r = export_pages(image_pdf, options=ExportOptions(dpi=150, fmt="jpg", jpeg_quality=80))
    assert r.output_dir.name.endswith("_進路 資料_jpg")
    img = Image.open(r.files[0])
    assert img.format == "JPEG"
    assert img.size == (600, 800)


def test_export_selected_pages_keep_original_number(image_pdf: Path):
    r = export_pages(image_pdf, [0, 2], options=ExportOptions(dpi=72))
    assert [f.name for f in r.files] == ["進路 資料_p001.png", "進路 資料_p003.png"]


def test_export_out_dir_override(image_pdf: Path, tmp_path: Path):
    other = tmp_path / "outside"
    r = export_pages(image_pdf, [0], options=ExportOptions(dpi=72), out_dir=other)
    assert r.output_dir.parent == other
    assert r.files[0].parent == r.output_dir


def test_export_progress_and_cancel(image_pdf: Path):
    seen = []
    export_pages(image_pdf, options=ExportOptions(dpi=72), progress=seen.append)
    assert len(seen) == 3
    assert seen[-1].current == 3 and seen[-1].total == 3
    assert "書き出しました" in seen[0].message

    with pytest.raises(ExportCancelled):
        export_pages(image_pdf, options=ExportOptions(dpi=72), cancel=lambda: True)


def test_export_empty_indices_raises(image_pdf: Path):
    with pytest.raises(ValueError):
        export_pages(image_pdf, [], options=ExportOptions(dpi=72))


def test_export_invalid_index(image_pdf: Path):
    with pytest.raises(IndexError):
        export_pages(image_pdf, [99], options=ExportOptions(dpi=72))


def test_export_encrypted_needs_password(encrypted_pdf: Path):
    with pytest.raises(PdfEncryptedError):
        export_pages(encrypted_pdf, options=ExportOptions(dpi=72))
    r = export_pages(encrypted_pdf, options=ExportOptions(dpi=72), password="pw")
    assert len(r.files) == 3


def test_export_refs_sequential_numbering_and_rotation(image_pdf: Path):
    # ページは 288x384pt。dpi=72 なので scale=1.0 → 無回転なら 288x384px
    refs = [PageRef(image_pdf, 2, 90), PageRef(image_pdf, 0, 0)]
    r = export_refs(refs, image_pdf, options=ExportOptions(dpi=72))
    assert [f.name for f in r.files] == ["進路 資料_p001.png", "進路 資料_p002.png"]
    img0 = Image.open(r.files[0])
    assert img0.size == (384, 288)  # 90度回転で横長
    img1 = Image.open(r.files[1])
    assert img1.size == (288, 384)


def test_export_refs_multiple_sources(image_pdf: Path, tmp_path: Path):
    from conftest import make_image_pdf as make2

    other = make2(tmp_path / "別 資料.pdf", pages=2)
    refs = [PageRef(image_pdf, 0), PageRef(other, 1)]
    r = export_refs(refs, image_pdf, options=ExportOptions(dpi=72))
    assert len(r.files) == 2
    assert all(f.exists() for f in r.files)


def test_export_refs_empty_raises(image_pdf: Path):
    with pytest.raises(ValueError):
        export_refs([], image_pdf)
