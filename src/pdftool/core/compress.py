"""PDF の圧縮（Ghostscript 呼び出し / 目標サイズ探索）。

GUI 非依存。PySide6 を import しないこと。

仕組み:
    画像解像度 (dpi) と JPEG 品質のペアを「品質はしご」として高品質→低品質に並べ、
    出力サイズが目標以下になる最も高品質な段を二分探索で見つける。
    1 試行 = Ghostscript のフル実行なので、試行回数は max_trials で打ち切る。

Windows の Ghostscript は非 ASCII パスの扱いが版によって怪しいため、
入力を ASCII 名の一時フォルダにコピーしてから処理し、結果を最終パスへ移す。

CLI:
    python -m pdftool.core.compress 入力.pdf --target 5
    python -m pdftool.core.compress 入力.pdf --preset standard
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from pdftool.core.document import PdfError, PdfDocument
from pdftool.utils import naming
from pdftool.utils.gs_locator import find_ghostscript

# ---------------------------------------------------------------------------
# パラメータ
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CompressParams:
    dpi: int        # カラー/グレー画像の解像度。モノクロは 2 倍にする
    quality: int    # JPEG 品質 (1..100)

    def describe(self) -> str:
        return f"{self.dpi}dpi / 品質{self.quality}"


# 品質はしご。高品質 → 低品質。二分探索はこのインデックス上で行う。
# サイズはおおむね単調減少になるよう dpi と quality を同時に下げている。
QUALITY_LADDER: tuple[CompressParams, ...] = (
    CompressParams(300, 92),
    CompressParams(300, 82),
    CompressParams(220, 82),
    CompressParams(200, 75),
    CompressParams(150, 78),
    CompressParams(150, 68),
    CompressParams(120, 62),
    CompressParams(100, 58),
    CompressParams(85, 52),
    CompressParams(72, 45),
    CompressParams(60, 38),
    CompressParams(50, 30),
)

# プリセット（UI の「高品質 / 標準 / 最小」）
PRESETS: dict[str, CompressParams] = {
    "high": CompressParams(300, 88),
    "standard": CompressParams(150, 72),
    "small": CompressParams(72, 45),
}

DEFAULT_MAX_TRIALS = 6
MB = 1024 * 1024


# ---------------------------------------------------------------------------
# 例外・結果型
# ---------------------------------------------------------------------------
class GhostscriptError(PdfError):
    """Ghostscript の実行失敗。stderr を含む。"""


class GhostscriptNotFoundError(GhostscriptError):
    """Ghostscript が見つからない。"""


class CompressCancelled(Exception):
    """cancel コールバックにより中断された。"""


@dataclass(frozen=True)
class CompressProgress:
    trial: int                  # 1 始まり
    max_trials: int
    params: CompressParams
    size_bytes: int | None      # None = 試行開始時（まだ結果なし）
    message: str                # UI にそのまま表示できる日本語


@dataclass
class CompressResult:
    output_path: Path
    original_size: int
    output_size: int
    target_size: int | None     # バイト。プリセット圧縮では None
    achieved: bool              # 目標以下に収まったか（target なしなら常に True）
    trials: int
    params: CompressParams      # 採用したパラメータ
    history: list[tuple[CompressParams, int]] = field(default_factory=list)

    @property
    def ratio(self) -> float:
        """出力 / 元 のサイズ比。"""
        return self.output_size / self.original_size if self.original_size else 0.0


ProgressCallback = Callable[[CompressProgress], None]
CancelCheck = Callable[[], bool]


def format_size(n: int) -> str:
    """バイト数を '7.2 MB' / '830 KB' の形にする。"""
    if n >= MB:
        return f"{n / MB:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n} B"


# ---------------------------------------------------------------------------
# Ghostscript 実行
# ---------------------------------------------------------------------------
def build_gs_command(gs: Path, src: Path, dst: Path, params: CompressParams) -> list[str]:
    """Ghostscript のコマンドライン（リスト）。shell=True は使わない。"""
    dpi = int(params.dpi)
    q = max(1, min(100, int(params.quality)))
    return [
        str(gs),
        "-sDEVICE=pdfwrite",
        "-dCompatibilityLevel=1.5",
        "-dNOPAUSE", "-dBATCH", "-dQUIET", "-dSAFER",
        "-dAutoRotatePages=/None",
        "-dDetectDuplicateImages=true",
        # JPEG をそのまま通さず必ず再エンコードする（これが無いと JPEGQ が効かない）
        "-dPassThroughJPEGImages=false",
        # 自動フィルタ選択を切り、DCT (JPEG) + 品質指定を確実に効かせる
        "-dAutoFilterColorImages=false", "-dColorImageFilter=/DCTEncode",
        "-dAutoFilterGrayImages=false", "-dGrayImageFilter=/DCTEncode",
        f"-dJPEGQ={q}",
        # ダウンサンプル。閾値 1.0 = 目標解像度を少しでも超えていれば落とす
        "-dDownsampleColorImages=true", f"-dColorImageResolution={dpi}",
        "-dColorImageDownsampleType=/Bicubic", "-dColorImageDownsampleThreshold=1.0",
        "-dDownsampleGrayImages=true", f"-dGrayImageResolution={dpi}",
        "-dGrayImageDownsampleType=/Bicubic", "-dGrayImageDownsampleThreshold=1.0",
        "-dDownsampleMonoImages=true", f"-dMonoImageResolution={dpi * 2}",
        "-dMonoImageDownsampleType=/Subsample", "-dMonoImageDownsampleThreshold=1.0",
        f"-sOutputFile={dst}",
        str(src),
    ]


def run_ghostscript(
    gs: Path,
    src: Path,
    dst: Path,
    params: CompressParams,
    cancel: CancelCheck | None = None,
) -> int:
    """1 回 Ghostscript を実行し、出力サイズ（バイト）を返す。

    cancel() が True を返したらプロセスを kill して CompressCancelled。
    """
    cmd = build_gs_command(gs, src, dst, params)
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as e:
        raise GhostscriptError(f"Ghostscript を起動できませんでした: {e}", src) from e

    while True:
        try:
            out, err = proc.communicate(timeout=0.25)
            break
        except subprocess.TimeoutExpired:
            if cancel is not None and cancel():
                proc.kill()
                proc.communicate()
                raise CompressCancelled()

    if proc.returncode != 0 or not dst.is_file():
        msg = err.decode("utf-8", errors="replace").strip() or out.decode("utf-8", errors="replace").strip()
        raise GhostscriptError(
            f"Ghostscript が失敗しました (code {proc.returncode}): {msg[:500]}", src
        )
    return dst.stat().st_size


# ---------------------------------------------------------------------------
# 圧縮 API
# ---------------------------------------------------------------------------
def _require_gs(gs: Path | None) -> Path:
    gs = gs or find_ghostscript()
    if gs is None:
        raise GhostscriptNotFoundError(
            "Ghostscript が見つかりません。`winget install ArtifexSoftware.GhostScript` で"
            "インストールしてください。"
        )
    return gs


def _validate_source(src: Path) -> int:
    """開けるか確認し、元サイズを返す。暗号化・破損は PdfError で弾く。"""
    with PdfDocument(src) as doc:
        return doc.file_size


def _finalize(src: Path, winner_tmp: Path, out_dir: Path | None) -> Path:
    final = naming.output_file(src, "compressed", ".pdf", out_dir=out_dir)
    final.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(winner_tmp), str(final))
    return final


def _initial_guess(original: int, target: int, n: int) -> int:
    """元サイズ比から最初に試す段を推定する（外れても二分探索で回収できる）。"""
    ratio = target / original if original else 1.0
    if ratio >= 1.0:
        return 0
    # ratio 0.5 → 中間あたり、ratio 0.1 → 下の方、という粗い写像
    idx = round((1.0 - ratio) ** 0.7 * (n - 1))
    return max(0, min(n - 1, idx))


def compress_with_params(
    src: Path | str,
    params: CompressParams,
    *,
    out_dir: Path | None = None,
    gs: Path | None = None,
    progress: ProgressCallback | None = None,
    cancel: CancelCheck | None = None,
) -> CompressResult:
    """固定パラメータで 1 回だけ圧縮する（プリセット用）。"""
    src = Path(src)
    gs = _require_gs(gs)
    original = _validate_source(src)

    with tempfile.TemporaryDirectory(prefix="pdftool_") as td:
        tmp = Path(td)
        work_in = tmp / "in.pdf"
        shutil.copyfile(src, work_in)
        work_out = tmp / "out.pdf"
        if progress:
            progress(CompressProgress(1, 1, params, None, f"圧縮中: {params.describe()}"))
        size = run_ghostscript(gs, work_in, work_out, params, cancel)
        if progress:
            progress(CompressProgress(1, 1, params, size, f"完了: {format_size(size)}"))
        final = _finalize(src, work_out, out_dir)

    return CompressResult(
        output_path=final,
        original_size=original,
        output_size=size,
        target_size=None,
        achieved=True,
        trials=1,
        params=params,
        history=[(params, size)],
    )


def compress_preset(src: Path | str, preset: str, **kw) -> CompressResult:
    """PRESETS のキー ('high' / 'standard' / 'small') で圧縮する。"""
    if preset not in PRESETS:
        raise ValueError(f"不明なプリセット: {preset}（{', '.join(PRESETS)}）")
    return compress_with_params(src, PRESETS[preset], **kw)


def compress_to_target(
    src: Path | str,
    target_mb: float,
    *,
    out_dir: Path | None = None,
    max_trials: int = DEFAULT_MAX_TRIALS,
    ladder: tuple[CompressParams, ...] = QUALITY_LADDER,
    gs: Path | None = None,
    progress: ProgressCallback | None = None,
    cancel: CancelCheck | None = None,
) -> CompressResult:
    """目標サイズ (MB) 以下に収まる最も高品質なパラメータを探して圧縮する。

    - ladder 上で二分探索（サイズはインデックスに対しおおむね単調減少と仮定）
    - max_trials 回で打ち切り。目標未達なら生成できた最小サイズを採用し achieved=False
    - progress は各試行の開始時と終了時に呼ばれる
    - cancel が True を返すと CompressCancelled
    """
    src = Path(src)
    if target_mb <= 0:
        raise ValueError("目標サイズは正の値を指定してください")
    if max_trials < 1:
        raise ValueError("max_trials は 1 以上")
    if not ladder:
        raise ValueError("ladder が空です")

    gs = _require_gs(gs)
    original = _validate_source(src)
    target = int(target_mb * MB)
    n = len(ladder)

    with tempfile.TemporaryDirectory(prefix="pdftool_") as td:
        tmp = Path(td)
        work_in = tmp / "in.pdf"
        shutil.copyfile(src, work_in)

        results: dict[int, int] = {}      # ladder index -> size
        history: list[tuple[CompressParams, int]] = []
        best_ok: int | None = None        # 目標以下を満たした最小インデックス
        lo, hi = 0, n - 1
        trials = 0

        while trials < max_trials and lo <= hi:
            if trials == 0:
                mid = max(lo, min(hi, _initial_guess(original, target, n)))
            else:
                mid = (lo + hi) // 2
            params = ladder[mid]
            trials += 1
            if progress:
                progress(CompressProgress(
                    trials, max_trials, params, None,
                    f"試行 {trials}/{max_trials}: {params.describe()} で圧縮中…",
                ))
            size = run_ghostscript(gs, work_in, tmp / f"trial_{mid}.pdf", params, cancel)
            results[mid] = size
            history.append((params, size))
            ok = size <= target
            if progress:
                progress(CompressProgress(
                    trials, max_trials, params, size,
                    f"試行 {trials}/{max_trials}: {params.describe()} → "
                    f"{format_size(size)}（{'目標達成' if ok else '目標超過'}）",
                ))
            if ok:
                best_ok = mid
                hi = mid - 1
            else:
                lo = mid + 1

        if best_ok is not None:
            chosen, achieved = best_ok, True
        else:
            # 未達: 生成できた中で最小のもの
            chosen, achieved = min(results.items(), key=lambda kv: kv[1])[0], False

        final = _finalize(src, tmp / f"trial_{chosen}.pdf", out_dir)

    return CompressResult(
        output_path=final,
        original_size=original,
        output_size=results[chosen],
        target_size=target,
        achieved=achieved,
        trials=trials,
        params=ladder[chosen],
        history=history,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="PDF を圧縮する（Ghostscript）")
    ap.add_argument("pdf", type=Path)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--target", type=float, metavar="MB", help="目標サイズ (MB)")
    g.add_argument("--preset", choices=sorted(PRESETS), help="プリセット")
    ap.add_argument("--out", type=Path, default=None, help="出力フォルダ（既定: 元と同じ）")
    ap.add_argument("--max-trials", type=int, default=DEFAULT_MAX_TRIALS)
    args = ap.parse_args(argv)

    def show(p: CompressProgress) -> None:
        print(p.message, flush=True)

    t0 = time.perf_counter()
    try:
        if args.target is not None:
            r = compress_to_target(args.pdf, args.target, out_dir=args.out,
                                   max_trials=args.max_trials, progress=show)
        else:
            r = compress_preset(args.pdf, args.preset, out_dir=args.out, progress=show)
    except PdfError as e:
        print(f"エラー: {e.message}", file=sys.stderr)
        return 1
    dt = time.perf_counter() - t0
    print(f"出力: {r.output_path}")
    print(f"サイズ: {format_size(r.original_size)} → {format_size(r.output_size)} "
          f"({r.ratio:.0%}) / {r.params.describe()} / {r.trials} 試行 / {dt:.1f} 秒")
    if r.target_size is not None and not r.achieved:
        print(f"※ 目標 {format_size(r.target_size)} には届きませんでした（最小サイズを採用）")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
