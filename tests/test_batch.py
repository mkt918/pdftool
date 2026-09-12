from pathlib import Path

import pytest

from conftest import make_image_pdf
from pdftool.core import batch as B
from pdftool.core.batch import (
    BatchCancelled,
    batch_compress,
    batch_export,
    list_pdfs,
)
from pdftool.core.compress import MB
from pdftool.core.export import ExportOptions


@pytest.fixture
def folder_with_pdfs(tmp_path: Path) -> Path:
    d = tmp_path / "対象 フォルダ"
    d.mkdir()
    make_image_pdf(d / "a.pdf", pages=2)
    make_image_pdf(d / "b 資料.pdf", pages=1)
    (d / "not_a_pdf.txt").write_text("dummy")
    sub = d / "sub"
    sub.mkdir()
    make_image_pdf(sub / "c.pdf", pages=1)
    return d


def test_list_pdfs(folder_with_pdfs: Path):
    top = list_pdfs(folder_with_pdfs)
    assert [p.name for p in top] == ["a.pdf", "b 資料.pdf"]
    rec = list_pdfs(folder_with_pdfs, recursive=True)
    assert {p.name for p in rec} == {"a.pdf", "b 資料.pdf", "c.pdf"}
    with pytest.raises(ValueError):
        list_pdfs(folder_with_pdfs / "nope")


def test_batch_export(folder_with_pdfs: Path):
    result = batch_export(folder_with_pdfs, options=ExportOptions(dpi=72))
    assert result.ok_count == 2 and result.fail_count == 0
    a = next(i for i in result.items if i.path.name == "a.pdf")
    assert a.ok and a.output.is_dir() and a.output_size is None
    assert len(list(a.output.glob("*.png"))) == 2


def test_batch_compress_with_fake_gs(monkeypatch, folder_with_pdfs: Path):
    def fake_run(gs, src, dst, params, cancel=None):
        dst.write_bytes(b"x" * (1 * MB))
        return 1 * MB

    import pdftool.core.compress as C

    monkeypatch.setattr(C, "run_ghostscript", fake_run)
    monkeypatch.setattr(C, "find_ghostscript", lambda: Path("C:/fake/gs.exe"))
    result = batch_compress(folder_with_pdfs, target_mb=5.0)
    assert result.ok_count == 2 and result.fail_count == 0
    for item in result.items:
        assert item.output.exists()
        assert item.output_size == 1 * MB


def test_batch_compress_requires_target_xor_preset(folder_with_pdfs: Path):
    with pytest.raises(ValueError):
        batch_compress(folder_with_pdfs)
    with pytest.raises(ValueError):
        batch_compress(folder_with_pdfs, target_mb=5.0, preset="standard")


def test_batch_skips_encrypted_and_continues(monkeypatch, tmp_path: Path, encrypted_pdf: Path):
    d = tmp_path / "混在"
    d.mkdir()
    import shutil

    shutil.copy(encrypted_pdf, d / "enc.pdf")
    make_image_pdf(d / "ok.pdf", pages=1)

    import pdftool.core.compress as C

    def fake_run(gs, src, dst, params, cancel=None):
        dst.write_bytes(b"x" * (1 * MB))
        return 1 * MB

    monkeypatch.setattr(C, "run_ghostscript", fake_run)
    monkeypatch.setattr(C, "find_ghostscript", lambda: Path("C:/fake/gs.exe"))
    result = batch_compress(d, target_mb=5.0)
    assert result.ok_count == 1 and result.fail_count == 1
    failed = next(i for i in result.items if not i.ok)
    assert failed.path.name == "enc.pdf"
    assert "パスワード" in failed.message


def test_batch_broken_pdf_reported_not_raised(broken_pdf: Path, tmp_path: Path):
    d = tmp_path / "with_broken"
    d.mkdir()
    import shutil

    shutil.copy(broken_pdf, d / broken_pdf.name)
    make_image_pdf(d / "ok.pdf", pages=1)
    result = batch_export(d, options=ExportOptions(dpi=72))
    assert result.ok_count == 1 and result.fail_count == 1
    bad = next(i for i in result.items if not i.ok)
    assert bad.message


def test_batch_cancel_raises_with_partial(folder_with_pdfs: Path):
    # cancel はアイテム間だけでなく export_pages 内のページ単位でも呼ばれる。
    # a.pdf(2ページ)を完全に処理し終えたところ（呼び出し3回目まで）で中断させる。
    calls = {"n": 0}

    def cancel():
        calls["n"] += 1
        return calls["n"] > 3

    with pytest.raises(BatchCancelled) as ei:
        batch_export(folder_with_pdfs, options=ExportOptions(dpi=72), cancel=cancel)
    assert len(ei.value.partial.items) == 1
    assert ei.value.partial.items[0].path.name == "a.pdf"


def test_batch_out_dir_override(folder_with_pdfs: Path, tmp_path: Path):
    out = tmp_path / "集約先"
    result = batch_export(folder_with_pdfs, options=ExportOptions(dpi=72), out_dir=out)
    assert all(i.output.parent == out for i in result.items)


def test_batch_progress_messages(folder_with_pdfs: Path):
    seen = []
    batch_export(folder_with_pdfs, options=ExportOptions(dpi=72), progress=seen.append)
    assert len(seen) == 4  # 2件 x (開始+完了)
    assert seen[0].index == 1 and seen[0].total == 2
