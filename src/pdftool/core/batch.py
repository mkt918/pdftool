"""フォルダ内一括処理（圧縮・画像書き出し）。

GUI 非依存。PySide6 を import しないこと。

対応する操作は圧縮と画像書き出しの2つ（ページ操作はバッチ対象外）。
1 件が失敗しても全体は止めず、最後にまとめて報告する。
暗号化 PDF はスキップ扱いにする（バッチでいちいちパスワードを聞かない）。

CLI:
    python -m pdftool.core.batch フォルダ --compress --target 5
    python -m pdftool.core.batch フォルダ --export --dpi 150 --fmt png
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from pdftool.core.compress import (
    CompressCancelled,
    GhostscriptError,
    compress_preset,
    compress_to_target,
)
from pdftool.core.document import PdfEncryptedError, PdfError
from pdftool.core.export import ExportCancelled, ExportOptions, export_pages


class BatchCancelled(Exception):
    """cancel コールバックにより中断された。`partial` にそれまでの結果を持つ。"""

    def __init__(self, partial: "BatchResult"):
        super().__init__("バッチ処理が中断されました")
        self.partial = partial


@dataclass(frozen=True)
class BatchItemResult:
    path: Path
    ok: bool
    original_size: int
    output_size: int | None = None   # 圧縮: 出力サイズ / 書き出し: None
    output: Path | None = None       # 圧縮: 出力 PDF / 書き出し: 出力フォルダ
    message: str = ""                # 成功なら空、失敗なら日本語の理由


@dataclass(frozen=True)
class BatchProgress:
    index: int    # 1 始まり
    total: int
    path: Path
    message: str


@dataclass
class BatchResult:
    items: list[BatchItemResult] = field(default_factory=list)

    @property
    def ok_count(self) -> int:
        return sum(1 for i in self.items if i.ok)

    @property
    def fail_count(self) -> int:
        return sum(1 for i in self.items if not i.ok)


ProgressCallback = Callable[[BatchProgress], None]
CancelCheck = Callable[[], bool]


def list_pdfs(folder: Path | str, recursive: bool = False) -> list[Path]:
    """フォルダ内の *.pdf を名前順で列挙する。"""
    folder = Path(folder)
    if not folder.is_dir():
        raise ValueError(f"フォルダが見つかりません: {folder}")
    pattern = "**/*.pdf" if recursive else "*.pdf"
    return sorted(folder.glob(pattern), key=lambda p: p.name)


def _run_batch(
    pdfs: list[Path],
    process_one: Callable[[Path], BatchItemResult],
    progress: ProgressCallback | None,
    cancel: CancelCheck | None,
) -> BatchResult:
    result = BatchResult()
    total = len(pdfs)
    for i, path in enumerate(pdfs, start=1):
        if cancel is not None and cancel():
            raise BatchCancelled(result)
        if progress:
            progress(BatchProgress(i, total, path, f"{i}/{total}: {path.name} を処理中…"))
        try:
            original_size = path.stat().st_size
        except OSError as e:
            result.items.append(BatchItemResult(path, False, 0, message=f"読み込めません: {e}"))
            continue
        try:
            item = process_one(path)
        except PdfEncryptedError:
            item = BatchItemResult(path, False, original_size, message="パスワード付きのためスキップ")
        except (PdfError, GhostscriptError) as e:
            item = BatchItemResult(path, False, original_size, message=e.message)
        except (CompressCancelled, ExportCancelled):
            raise BatchCancelled(result) from None
        except Exception as e:  # noqa: BLE001 — 1 件の想定外エラーで全体を止めない
            item = BatchItemResult(path, False, original_size, message=f"{type(e).__name__}: {e}")
        result.items.append(item)
        if progress:
            status = "完了" if item.ok else f"失敗（{item.message}）"
            progress(BatchProgress(i, total, path, f"{i}/{total}: {path.name} {status}"))
    return result


def batch_compress(
    folder: Path | str,
    *,
    target_mb: float | None = None,
    preset: str | None = None,
    recursive: bool = False,
    out_dir: Path | None = None,
    progress: ProgressCallback | None = None,
    cancel: CancelCheck | None = None,
) -> BatchResult:
    """フォルダ内の PDF をまとめて圧縮する。target_mb / preset のどちらか一方を指定する。"""
    if (target_mb is None) == (preset is None):
        raise ValueError("target_mb と preset はどちらか一方だけ指定してください")
    pdfs = list_pdfs(folder, recursive)

    def process_one(path: Path) -> BatchItemResult:
        if target_mb is not None:
            r = compress_to_target(path, target_mb, out_dir=out_dir, cancel=cancel)
        else:
            r = compress_preset(path, preset, out_dir=out_dir, cancel=cancel)
        return BatchItemResult(path, True, r.original_size, r.output_size, r.output_path)

    return _run_batch(pdfs, process_one, progress, cancel)


def batch_export(
    folder: Path | str,
    *,
    options: ExportOptions = ExportOptions(),
    recursive: bool = False,
    out_dir: Path | None = None,
    progress: ProgressCallback | None = None,
    cancel: CancelCheck | None = None,
) -> BatchResult:
    """フォルダ内の PDF をまとめて画像書き出しする。"""
    pdfs = list_pdfs(folder, recursive)

    def process_one(path: Path) -> BatchItemResult:
        original_size = path.stat().st_size
        r = export_pages(path, options=options, out_dir=out_dir, cancel=cancel)
        return BatchItemResult(path, True, original_size, None, r.output_dir)

    return _run_batch(pdfs, process_one, progress, cancel)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="フォルダ内の PDF を一括処理する")
    ap.add_argument("folder", type=Path)
    ap.add_argument("--recursive", action="store_true", help="サブフォルダも対象にする")
    ap.add_argument("--out", type=Path, default=None, help="出力先（既定: 各PDFと同じ場所）")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--compress", action="store_true")
    mode.add_argument("--export", action="store_true")
    ap.add_argument("--target", type=float, default=None, help="[--compress] 目標サイズ(MB)")
    ap.add_argument("--preset", choices=["high", "standard", "small"], default=None, help="[--compress] プリセット")
    ap.add_argument("--dpi", type=int, default=150, help="[--export] 解像度")
    ap.add_argument("--fmt", choices=["png", "jpg"], default="png", help="[--export] 画像形式")
    args = ap.parse_args(argv)

    def show(p: BatchProgress) -> None:
        print(p.message, flush=True)

    try:
        if args.compress:
            r = batch_compress(
                args.folder, target_mb=args.target, preset=args.preset,
                recursive=args.recursive, out_dir=args.out, progress=show,
            )
        else:
            options = ExportOptions(dpi=args.dpi, fmt=args.fmt)
            r = batch_export(
                args.folder, options=options, recursive=args.recursive,
                out_dir=args.out, progress=show,
            )
    except ValueError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1

    print(f"成功 {r.ok_count} / 失敗 {r.fail_count}（全 {len(r.items)} 件）")
    for item in r.items:
        if not item.ok:
            print(f"  失敗: {item.path.name} — {item.message}")
    return 0 if r.fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(_main())
