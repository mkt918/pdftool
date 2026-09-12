from pathlib import Path

import pytest
from pypdf import PdfReader

from conftest import make_blank_pdf
from pdftool.core.document import PdfEncryptedError, PdfError
from pdftool.core.pageops import (
    PageList,
    PageRef,
    merge_pdfs,
    parse_page_spec,
    write_pages,
)


def sizes_of(pdf: Path) -> list[tuple[int, int]]:
    """各ページの (幅, 高さ, 回転) を読み出す。"""
    r = PdfReader(str(pdf))
    return [(int(p.mediabox.width), int(p.mediabox.height), p.rotation) for p in r.pages]


@pytest.fixture
def five(tmp_path: Path) -> Path:
    # 各ページ幅を 100,101,102,... にして順序を追跡できるようにする
    from pypdf import PdfWriter

    w = PdfWriter()
    for i in range(5):
        w.add_blank_page(width=100 + i, height=200)
    out = tmp_path / "五 頁.pdf"
    w.write(out)
    return out


def widths(pl: PageList, src: Path) -> list[int]:
    """PageList の並びを元ページ番号の列として返す。"""
    return [r.source_index for r in pl.pages]


# ---------------------------------------------------------------------------
# PageRef
# ---------------------------------------------------------------------------
def test_pageref_rotation_normalized(tmp_path: Path):
    r = PageRef(tmp_path / "a.pdf", 0, 450)
    assert r.rotation == 90
    assert r.rotated(-90).rotation == 0
    assert r.rotated(270).rotation == 0
    with pytest.raises(ValueError):
        PageRef(tmp_path / "a.pdf", 0, 45)


# ---------------------------------------------------------------------------
# parse_page_spec
# ---------------------------------------------------------------------------
def test_parse_page_spec():
    assert parse_page_spec("1-5,8,10-12", 12) == [0, 1, 2, 3, 4, 7, 9, 10, 11]
    assert parse_page_spec(" 3 , 1 - 2 ", 5) == [2, 0, 1]
    assert parse_page_spec("2、2，2", 5) == [1]
    for bad in ["", "0", "a", "3-1", "6", "1-9"]:
        with pytest.raises(ValueError):
            parse_page_spec(bad, 5)


# ---------------------------------------------------------------------------
# PageList 操作
# ---------------------------------------------------------------------------
def test_from_pdf_and_len(five: Path):
    pl = PageList.from_pdf(five)
    assert len(pl) == 5
    assert pl[0] == PageRef(five, 0)
    assert pl.sources == [five]
    assert not pl.is_modified and not pl.can_undo


def test_remove(five: Path):
    pl = PageList.from_pdf(five)
    pl.remove([1, 3])
    assert widths(pl, five) == [0, 2, 4]
    with pytest.raises(ValueError):
        pl.remove([0, 1, 2])  # 全削除は不可
    with pytest.raises(IndexError):
        pl.remove([10])


def test_rotate(five: Path):
    pl = PageList.from_pdf(five)
    pl.rotate([0, 4], 90)
    pl.rotate([0], 90)
    assert [r.rotation for r in pl.pages] == [180, 0, 0, 0, 90]
    pl.rotate([4], -90)
    assert pl[4].rotation == 0
    with pytest.raises(ValueError):
        pl.rotate([0], 30)


def test_move_forward_and_backward(five: Path):
    pl = PageList.from_pdf(five)
    pl.move([3, 4], 0)                 # 末尾 2 枚を先頭へ
    assert widths(pl, five) == [3, 4, 0, 1, 2]
    pl = PageList.from_pdf(five)
    pl.move([0, 1], 5)                 # 先頭 2 枚を末尾へ
    assert widths(pl, five) == [2, 3, 4, 0, 1]
    pl = PageList.from_pdf(five)
    pl.move([1], 3)                    # 1 を「元の 3 の直前」へ → [0,2,1,3,4]
    assert widths(pl, five) == [0, 2, 1, 3, 4]
    pl = PageList.from_pdf(five)
    pl.move([0, 2, 4], 2)              # 飛び飛び選択
    assert widths(pl, five) == [1, 0, 2, 4, 3]
    pl.move([0], 0)                    # 何も変わらない → Undo スタックに積まれない
    assert pl.can_undo and len(pl._undo) == 1


def test_reorder(five: Path):
    pl = PageList.from_pdf(five)
    pl.reorder([4, 3, 2, 1, 0])
    assert widths(pl, five) == [4, 3, 2, 1, 0]
    with pytest.raises(ValueError):
        pl.reorder([0, 0, 1, 2, 3])


def test_undo_redo(five: Path):
    pl = PageList.from_pdf(five)
    pl.remove([0])
    pl.rotate([0], 90)
    assert widths(pl, five) == [1, 2, 3, 4] and pl[0].rotation == 90
    assert pl.undo()
    assert pl[0].rotation == 0
    assert pl.undo()
    assert widths(pl, five) == [0, 1, 2, 3, 4]
    assert not pl.undo()
    assert pl.redo() and widths(pl, five) == [1, 2, 3, 4]
    pl.rotate([1], 180)                # 新しい操作で redo は消える
    assert not pl.can_redo
    assert pl.is_modified


def test_append_and_insert_pdf(five: Path, blank_pdf: Path):
    pl = PageList.from_pdf(five)
    assert pl.append_pdf(blank_pdf) == 5
    assert len(pl) == 10 and pl.sources == [five, blank_pdf]
    pl.insert_pdf(blank_pdf, 0)
    assert pl[0] == PageRef(blank_pdf, 0) and len(pl) == 15
    with pytest.raises(IndexError):
        pl.insert_pdf(blank_pdf, 99)


def test_encrypted_source_needs_password(encrypted_pdf: Path):
    with pytest.raises(PdfEncryptedError):
        PageList.from_pdf(encrypted_pdf)
    pl = PageList.from_pdf(encrypted_pdf, password="pw")
    out = pl.save()
    assert out.exists()
    assert not PdfReader(str(out)).is_encrypted
    assert len(PdfReader(str(out)).pages) == 3


# ---------------------------------------------------------------------------
# 書き出し
# ---------------------------------------------------------------------------
def test_save_writes_new_file_with_naming(five: Path):
    pl = PageList.from_pdf(five)
    pl.remove([0])
    pl.rotate([0], 90)
    pl.move([3], 0)
    out = pl.save()
    assert out.parent == five.parent
    assert out.name.endswith("_五 頁_edited.pdf")
    assert not pl.is_modified
    # 並び: 削除後 [1,2,3,4] → 1 を 90 回転 → 4 を先頭へ → [4,1,2,3]
    assert sizes_of(out) == [(104, 200, 0), (101, 200, 90), (102, 200, 0), (103, 200, 0)]
    assert sizes_of(five) == [(100 + i, 200, 0) for i in range(5)]  # 原本は無傷
    out2 = pl.save()                   # 同名回避
    assert out2 != out and out2.name.endswith("_edited_2.pdf")


def test_save_refuses_overwriting_source(five: Path):
    pl = PageList.from_pdf(five)
    with pytest.raises(PdfError):
        pl.save(five)


def test_export_subset_and_split_all(five: Path):
    pl = PageList.from_pdf(five)
    out = pl.export_subset([4, 2])
    assert [s[0] for s in sizes_of(out)] == [102, 104]
    assert len(pl) == 5                # 編集状態は変わらない
    files = pl.export_split_all()
    assert len(files) == 5
    assert files[0].parent.name.endswith("_五 頁_split")
    assert files[0].name == "五 頁_p001.pdf"
    assert sizes_of(files[4])[0][0] == 104


def test_write_pages_rotation_accumulates_with_existing_rotate(tmp_path: Path):
    """元 PDF に /Rotate 90 があるページを更に 90 回すと 180 になる。"""
    src = make_blank_pdf(tmp_path / "r.pdf", pages=1)
    pl = PageList.from_pdf(src)
    pl.rotate([0], 90)
    first = pl.save(tmp_path / "r1.pdf")
    assert sizes_of(first)[0][2] == 90
    pl2 = PageList.from_pdf(first)
    pl2.rotate([0], 90)
    second = pl2.save(tmp_path / "r2.pdf")
    assert sizes_of(second)[0][2] == 180


def test_merge_pdfs(five: Path, blank_pdf: Path):
    out = merge_pdfs([five, blank_pdf])
    assert len(PdfReader(str(out)).pages) == 10
    assert out.name.endswith("_五 頁_merged.pdf")


def test_write_pages_validation(tmp_path: Path, five: Path):
    with pytest.raises(ValueError):
        write_pages([], tmp_path / "x.pdf")
    with pytest.raises(PdfError):
        write_pages([PageRef(five, 99)], tmp_path / "x.pdf")
    with pytest.raises(PdfError):
        write_pages([PageRef(tmp_path / "ない.pdf", 0)], tmp_path / "x.pdf")
