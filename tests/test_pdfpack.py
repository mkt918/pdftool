"""結合・保存でファイルが膨らまないこと（pypdf はオブジェクトストリームを展開して書くため、詰め直しが要る）。"""

import random
import zlib
from pathlib import Path

import pytest

from pdftool.core.pageops import PageList, write_pages
from pdftool.core.pdfpack import repack_in_place

pikepdf = pytest.importorskip("pikepdf")


def make(path, nannots, seed):
    random.seed(seed)
    objs = {}   # num -> bytes (ObjStm 内)
    # 1: catalog 2: pages 3: page 4: content 5: ObjStm 6: xref stream, 7..: annots
    annots = list(range(7, 7 + nannots))
    objs[1] = b"<</Type/Catalog/Pages 2 0 R>>"
    objs[2] = b"<</Type/Pages/Kids[3 0 R]/Count 1>>"
    arr = b" ".join(f"{n} 0 R".encode() for n in annots)
    objs[3] = b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]/Contents 4 0 R/Annots[" + arr + b"]>>"
    for n in annots:
        x, y = random.random()*500, random.random()*800
        objs[n] = f"<</Type/Annot/Subtype/Link/Rect[{x:.3f} {y:.3f} {x+20:.3f} {y+10:.3f}]/Border[0 0 0]/A<</S/URI/URI(http://example.com/{random.randrange(10**6)})>>>>".encode()
    nums = sorted(n for n in objs)
    header, body, off = [], b"", 0
    for n in nums:
        header.append(f"{n} {off}"); body += objs[n] + b"\n"; off += len(objs[n]) + 1
    head = (" ".join(header) + "\n").encode()
    objstm = zlib.compress(head + body)
    out = bytearray(b"%PDF-1.5\n")
    offsets = {}
    def add(n, data):
        offsets[n] = len(out); out.extend(f"{n} 0 obj\n".encode() + data + b"\nendobj\n")
    content = zlib.compress(b"BT /F1 12 Tf 50 800 Td (hello) Tj ET")
    add(4, f"<</Length {len(content)}/Filter/FlateDecode>>\nstream\n".encode() + content + b"\nendstream")
    add(5, f"<</Type/ObjStm/N {len(nums)}/First {len(head)}/Length {len(objstm)}/Filter/FlateDecode>>\nstream\n".encode() + objstm + b"\nendstream")
    size = max(nums) + 2
    xpos = len(out)
    # xref stream: W [1 4 2]
    rows = bytearray()
    for n in range(size):
        if n == 0: rows += bytes([0]) + (0).to_bytes(4, "big") + (65535).to_bytes(2, "big")
        elif n in offsets: rows += bytes([1]) + offsets[n].to_bytes(4, "big") + (0).to_bytes(2, "big")
        elif n == size - 1: rows += bytes([1]) + xpos.to_bytes(4, "big") + (0).to_bytes(2, "big")
        elif n in objs: rows += bytes([2]) + (5).to_bytes(4, "big") + nums.index(n).to_bytes(2, "big")
        else: rows += bytes([0]) + (0).to_bytes(4, "big") + (0).to_bytes(2, "big")
    xs = zlib.compress(bytes(rows))
    xo = f"{size - 1} 0 obj\n<</Type/XRef/Size {size}/W[1 4 2]/Root 1 0 R/Length {len(xs)}/Filter/FlateDecode>>\nstream\n".encode()
    out.extend(xo + xs + b"\nendstream\nendobj\n")
    out.extend(f"startxref\n{xpos}\n%%EOF\n".encode())
    path.write_bytes(bytes(out))


def test_merge_does_not_balloon_objstm_heavy_pdfs(tmp_path: Path):
    paths = []
    for k in range(4):
        p = tmp_path / f"o{k}.pdf"
        make(p, 3000, k)       # 小さなオブジェクトを大量に ObjStm へ入れた PDF
        paths.append(p)
    refs = []
    for p in paths:
        refs.extend(PageList.load_refs(p))
    out = write_pages(refs, tmp_path / "m.pdf")
    total = sum(p.stat().st_size for p in paths)
    assert out.stat().st_size < total * 1.3          # 詰め直し前は 6〜7 倍になる
    with pikepdf.open(out) as pdf:
        assert len(pdf.pages) == 4


def test_repack_keeps_file_when_not_smaller(tmp_path: Path):
    src = tmp_path / "a.pdf"
    make(src, 10, 1)
    before = src.read_bytes()
    repack_in_place(src)
    assert src.exists() and not (tmp_path / "a.pdf.pack").exists()
    assert src.read_bytes() == before or src.stat().st_size <= len(before)


def test_second_image_export_same_day_goes_into_same_folder(tmp_path: Path):
    from pdftool.core.export import ExportOptions, export_pages

    src = tmp_path / "a.pdf"
    make(src, 5, 2)
    r1 = export_pages(src, options=ExportOptions(dpi=40, fmt="png"))
    r2 = export_pages(src, options=ExportOptions(dpi=40, fmt="png"))
    assert r1.output_dir == r2.output_dir                          # フォルダは増えない
    assert {f.name for f in r1.files}.isdisjoint({f.name for f in r2.files})    # 既存の画像は上書きしない
    assert len(list(r1.output_dir.glob("*.png"))) == 2
