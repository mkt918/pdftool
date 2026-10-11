from datetime import date
from pathlib import Path

from pdftool.utils.naming import (
    date_prefix,
    numbered_name,
    output_dir,
    output_file,
    unique_path,
)


def test_date_prefix():
    assert date_prefix(date(2026, 9, 12)) == "26-09-12"
    assert date_prefix(date(2005, 1, 3)) == "05-01-03"


def test_unique_path_no_conflict(tmp_path: Path):
    p = tmp_path / "a.pdf"
    assert unique_path(p) == p


def test_unique_path_conflict_chain(tmp_path: Path):
    (tmp_path / "a.pdf").touch()
    assert unique_path(tmp_path / "a.pdf") == tmp_path / "a_2.pdf"
    (tmp_path / "a_2.pdf").touch()
    assert unique_path(tmp_path / "a.pdf") == tmp_path / "a_3.pdf"


def test_output_file_default(tmp_path: Path):
    src = tmp_path / "進路 資料.pdf"
    out = output_file(src, "compressed", ".pdf", today=date(2026, 9, 12))
    assert out == tmp_path / "26-09-12_進路 資料_compressed.pdf"


def test_output_file_ext_normalization_and_default_ext(tmp_path: Path):
    src = tmp_path / "x.pdf"
    assert output_file(src, "p", "png", today=date(2026, 1, 1)).suffix == ".png"
    assert output_file(src, "edited", today=date(2026, 1, 1)).suffix == ".pdf"  # ext=None → 元と同じ


def test_output_file_custom_out_dir(tmp_path: Path):
    src = tmp_path / "a.pdf"
    other = tmp_path / "out"
    out = output_file(src, "compressed", ".pdf", out_dir=other, today=date(2026, 9, 12))
    assert out.parent == other


def test_output_file_avoids_existing(tmp_path: Path):
    src = tmp_path / "a.pdf"
    existing = tmp_path / "26-09-12_a_compressed.pdf"
    existing.touch()
    out = output_file(src, "compressed", ".pdf", today=date(2026, 9, 12))
    assert out == tmp_path / "26-09-12_a_compressed_2.pdf"


def test_output_dir_naming_and_conflict(tmp_path: Path):
    src = tmp_path / "資料.pdf"
    d = output_dir(src, "png", today=date(2026, 9, 12))
    assert d == tmp_path / "26-09-12_資料_png"
    d.mkdir()
    d2 = output_dir(src, "png", today=date(2026, 9, 12))
    assert d2 == tmp_path / "26-09-12_資料_png_2"


def test_numbered_name():
    src = Path("元 資料.pdf")
    assert numbered_name(src, 1, ".png") == "元 資料_p001.png"
    assert numbered_name(src, 42, "jpg") == "元 資料_p042.jpg"
    assert numbered_name(src, 7, ".png", width=2) == "元 資料_p07.png"


def test_output_dir_reuses_existing_folder_when_asked(tmp_path):
    from datetime import date

    src = tmp_path / "資料.pdf"
    d1 = output_dir(src, "png", today=date(2026, 9, 12), reuse_existing=True)
    d1.mkdir()
    assert output_dir(src, "png", today=date(2026, 9, 12), reuse_existing=True) == d1     # 同じ日・同じ PDF
    assert output_dir(src, "png", today=date(2026, 9, 13), reuse_existing=True) != d1     # 日が変われば別
    assert output_dir(src, "png", today=date(2026, 9, 12)).name.endswith("_2")           # 既定は従来どおり
