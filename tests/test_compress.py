from pathlib import Path

import pytest

from pdftool.core import compress as C
from pdftool.core.compress import (
    MB,
    QUALITY_LADDER,
    CompressCancelled,
    CompressParams,
    GhostscriptNotFoundError,
    build_gs_command,
    compress_preset,
    compress_to_target,
    format_size,
)
from pdftool.core.document import PdfEncryptedError
from pdftool.utils.gs_locator import find_ghostscript

FAKE_GS = Path("C:/fake/gswin64c.exe")


# ---------------------------------------------------------------------------
# 疑似 Ghostscript: はしごの段ごとに固定サイズのファイルを吐く
# ---------------------------------------------------------------------------
def install_fake_gs(monkeypatch, sizes_by_index: dict[int, int], calls: list | None = None):
    """run_ghostscript を差し替え、ladder のインデックスに応じたサイズのファイルを作る。"""
    index_of = {p: i for i, p in enumerate(QUALITY_LADDER)}

    def fake_run(gs, src, dst, params, cancel=None):
        assert gs == FAKE_GS
        assert src.is_file()
        if cancel is not None and cancel():
            raise CompressCancelled()
        idx = index_of.get(params)          # プリセットなど、はしご外のパラメータは None
        size = sizes_by_index.get(idx, 3 * MB)
        dst.write_bytes(b"x" * size)
        if calls is not None:
            calls.append(idx)
        return size

    monkeypatch.setattr(C, "run_ghostscript", fake_run)


def linear_sizes(top: int, bottom: int) -> dict[int, int]:
    """段 0 が top バイト、最終段が bottom バイトになる単調減少のサイズ表。"""
    n = len(QUALITY_LADDER)
    return {i: int(top - (top - bottom) * i / (n - 1)) for i in range(n)}


def test_ladder_is_monotone_in_params():
    for a, b in zip(QUALITY_LADDER, QUALITY_LADDER[1:]):
        assert (b.dpi, b.quality) < (a.dpi, a.quality) or b.dpi < a.dpi or b.quality < a.quality


def test_build_gs_command_has_no_shell_and_key_flags(tmp_path: Path):
    src = tmp_path / "進路 資料.pdf"
    dst = tmp_path / "out.pdf"
    cmd = build_gs_command(FAKE_GS, src, dst, CompressParams(150, 70))
    assert isinstance(cmd, list)
    assert cmd[0] == str(FAKE_GS)
    assert cmd[-1] == str(src)
    assert "-dJPEGQ=70" in cmd
    assert "-dColorImageResolution=150" in cmd
    assert "-dMonoImageResolution=300" in cmd
    assert "-dPassThroughJPEGImages=false" in cmd
    assert "-dAutoFilterColorImages=false" in cmd
    assert f"-sOutputFile={dst}" in cmd


def test_format_size():
    assert format_size(500) == "500 B"
    assert format_size(2048) == "2 KB"
    assert format_size(int(7.25 * MB)) == "7.2 MB"


def test_target_picks_highest_quality_under_target(monkeypatch, image_pdf: Path):
    sizes = linear_sizes(12 * MB, 1 * MB)   # 段 0: 12MB … 最終段: 1MB
    calls: list[int] = []
    install_fake_gs(monkeypatch, sizes, calls)
    target = 5.0
    r = compress_to_target(image_pdf, target, gs=FAKE_GS)
    # 期待: target 以下を満たす最小インデックス
    expected = min(i for i, s in sizes.items() if s <= target * MB)
    assert r.params == QUALITY_LADDER[expected]
    assert r.achieved is True
    assert r.output_size == sizes[expected]
    assert r.trials == len(calls) <= C.DEFAULT_MAX_TRIALS
    assert r.output_path.exists()
    assert r.output_path.parent == image_pdf.parent
    assert r.output_path.name.endswith("_進路 資料_compressed.pdf")
    assert image_pdf.exists()  # 原本はそのまま
    assert len(r.history) == r.trials


def test_target_unreachable_returns_smallest(monkeypatch, image_pdf: Path):
    sizes = linear_sizes(12 * MB, 6 * MB)   # どの段でも 5MB を切れない
    install_fake_gs(monkeypatch, sizes)
    r = compress_to_target(image_pdf, 5.0, gs=FAKE_GS)
    assert r.achieved is False
    assert r.output_size == min(s for _, s in r.history)
    assert r.trials <= C.DEFAULT_MAX_TRIALS


def test_target_already_small_uses_top_of_ladder(monkeypatch, image_pdf: Path):
    sizes = linear_sizes(2 * MB, 100 * 1024)
    calls: list[int] = []
    install_fake_gs(monkeypatch, sizes, calls)
    r = compress_to_target(image_pdf, 50.0, gs=FAKE_GS)
    assert r.params == QUALITY_LADDER[0]
    assert r.trials == 1


def test_max_trials_is_respected(monkeypatch, image_pdf: Path):
    sizes = linear_sizes(12 * MB, 1 * MB)
    calls: list[int] = []
    install_fake_gs(monkeypatch, sizes, calls)
    r = compress_to_target(image_pdf, 5.0, gs=FAKE_GS, max_trials=2)
    assert len(calls) == 2 and r.trials == 2
    # 2 回で見つからなくても、試した中のどれかを必ず返す
    assert r.output_size in {sizes[i] for i in calls}
    assert r.output_path.exists()


def test_progress_callback_messages(monkeypatch, image_pdf: Path):
    install_fake_gs(monkeypatch, linear_sizes(12 * MB, 1 * MB))
    seen = []
    compress_to_target(image_pdf, 5.0, gs=FAKE_GS, progress=seen.append)
    assert seen  # 開始と終了で 2 回ずつ
    assert seen[0].size_bytes is None and "圧縮中" in seen[0].message
    assert seen[1].size_bytes is not None and "MB" in seen[1].message
    assert all(p.max_trials == C.DEFAULT_MAX_TRIALS for p in seen)


def test_cancel(monkeypatch, image_pdf: Path):
    install_fake_gs(monkeypatch, linear_sizes(12 * MB, 1 * MB))
    with pytest.raises(CompressCancelled):
        compress_to_target(image_pdf, 5.0, gs=FAKE_GS, cancel=lambda: True)


def test_preset(monkeypatch, image_pdf: Path):
    install_fake_gs(monkeypatch, linear_sizes(12 * MB, 1 * MB))
    r = compress_preset(image_pdf, "standard", gs=FAKE_GS)
    assert r.params == C.PRESETS["standard"]
    assert r.target_size is None and r.achieved and r.trials == 1
    with pytest.raises(ValueError):
        compress_preset(image_pdf, "nope", gs=FAKE_GS)


def test_encrypted_source_rejected_before_gs(monkeypatch, encrypted_pdf: Path):
    install_fake_gs(monkeypatch, linear_sizes(12 * MB, 1 * MB))
    with pytest.raises(PdfEncryptedError):
        compress_to_target(encrypted_pdf, 5.0, gs=FAKE_GS)


def test_gs_not_found(monkeypatch, image_pdf: Path):
    monkeypatch.setattr(C, "find_ghostscript", lambda: None)
    with pytest.raises(GhostscriptNotFoundError):
        compress_to_target(image_pdf, 5.0)


def test_invalid_args(image_pdf: Path):
    with pytest.raises(ValueError):
        compress_to_target(image_pdf, 0, gs=FAKE_GS)
    with pytest.raises(ValueError):
        compress_to_target(image_pdf, 1, gs=FAKE_GS, max_trials=0)


# ---------------------------------------------------------------------------
# 実 Ghostscript（インストールされている環境のみ）
# ---------------------------------------------------------------------------
@pytest.mark.skipif(find_ghostscript() is None, reason="Ghostscript 未検出")
def test_real_ghostscript_reduces_size(tmp_path: Path):
    from conftest import make_image_pdf

    src = make_image_pdf(tmp_path / "実 圧縮.pdf", pages=2, size=(1500, 2000))
    original = src.stat().st_size
    r = compress_to_target(src, original / MB * 0.5)
    assert r.output_path.exists()
    assert r.output_size < original
    assert src.stat().st_size == original
