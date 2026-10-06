from pathlib import Path

import pytest
from PIL import Image

from pdftool.core.document import PdfBrokenError, PdfDocument
from pdftool.core.images import convert_images, image_to_pdf, is_image
from pdftool.utils import naming, tempfiles


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    tempfiles.cleanup()


def make_image(path: Path, size=(1600, 1200), mode="RGB", color=(200, 30, 30)) -> Path:
    Image.new(mode, size, color).save(path)
    return path


def test_is_image():
    assert is_image("a.JPG") and is_image(Path("b.png")) and not is_image("c.pdf")


def test_landscape_photo_becomes_a4_long_side_page(tmp_path: Path):
    img = make_image(tmp_path / "写真 1.jpg", (1600, 1200))
    pdf = image_to_pdf(img)
    assert pdf.name == "写真 1.pdf" and pdf.parent != tmp_path
    with PdfDocument(pdf) as doc:
        assert doc.page_count == 1
        info = doc.page_info(0)
        assert info.width_pt == pytest.approx(842, abs=1)      # 長辺が A4 の長辺
        assert info.height_pt == pytest.approx(842 * 1200 / 1600, abs=1)


def test_png_with_alpha_is_flattened_on_white(tmp_path: Path):
    img = make_image(tmp_path / "a.png", (400, 400), "RGBA", (0, 0, 0, 0))
    pdf = image_to_pdf(img)
    with PdfDocument(pdf) as doc:
        px = doc.render_page(0, dpi=20).getpixel((5, 5))
    assert px == (255, 255, 255)


def test_exif_orientation_is_applied(tmp_path: Path):
    im = Image.new("RGB", (800, 400), (10, 10, 200))
    exif = im.getexif()
    exif[0x0112] = 6                      # 90度回転して表示するべき画像
    p = tmp_path / "rot.jpg"
    im.save(p, exif=exif)
    with PdfDocument(image_to_pdf(p)) as doc:
        info = doc.page_info(0)
    assert info.height_pt > info.width_pt  # 縦長になっている


def test_multiframe_tiff_gives_multiple_pages(tmp_path: Path):
    frames = [Image.new("RGB", (300, 400), c) for c in ((255, 0, 0), (0, 255, 0), (0, 0, 255))]
    p = tmp_path / "scan.tif"
    frames[0].save(p, save_all=True, append_images=frames[1:])
    with PdfDocument(image_to_pdf(p)) as doc:
        assert doc.page_count == 3


def test_naming_uses_original_image_not_temp_pdf(tmp_path: Path):
    img = make_image(tmp_path / "写真.jpg")
    pdf = image_to_pdf(img)
    out = naming.output_file(pdf, "compressed", ".pdf")
    assert out.parent == tmp_path and out.name.endswith("_写真_compressed.pdf")
    assert naming.numbered_name(pdf, 1, ".png") == "写真_p001.png"


def test_broken_image_raises_japanese_error(tmp_path: Path):
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"not an image")
    with pytest.raises(PdfBrokenError) as ei:
        image_to_pdf(bad)
    assert "画像を読み込めません" in ei.value.message
    with pytest.raises(PdfBrokenError):
        image_to_pdf(tmp_path / "ない.png")


def test_convert_images_keeps_order_and_passes_pdf_through(tmp_path: Path, image_pdf: Path):
    a = make_image(tmp_path / "a.png")
    out = convert_images([a, image_pdf])
    assert out[0].suffix == ".pdf" and out[0] != image_pdf
    assert out[1] == image_pdf
