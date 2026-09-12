# core / utils 公開インタフェース

Opus フェーズで確定させた関数シグネチャ。**Sonnet フェーズではここを変更しない。**
変更が必要になったら実装を止めて報告する。

型は Python の記法。`Path | str` は `pathlib.Path` を推奨。

---

## core/document.py — PDF の読み込み・レンダリング

```python
# 例外（すべて PdfError のサブクラス。.message は日本語、.path は対象ファイル）
class PdfError(Exception)            # 基底
class PdfNotFoundError(PdfError)     # ファイルが無い
class PdfEncryptedError(PdfError)    # パスワード付き（未指定 or 誤り）
class PdfBrokenError(PdfError)       # 破損・非PDF
class PdfEmptyError(PdfError)        # 0 ページ

@dataclass(frozen=True)
class PageInfo:
    index: int        # 0 始まり
    width_pt: float   # /Rotate 適用後の見た目の幅（pt）
    height_pt: float
    rotation: int     # PDF 内の /Rotate (0/90/180/270)

@dataclass(frozen=True)
class PdfInfo:
    path: Path
    page_count: int   # 暗号化で開けなかった場合は -1
    file_size: int
    encrypted: bool

class PdfDocument:
    path: Path
    page_count: int
    file_size: int
    def __init__(self, path: Path | str, password: str | None = None)
    def close(self) -> None                       # 冪等
    def __enter__ / __exit__                      # with で使う
    def page_info(self, index: int) -> PageInfo
    def all_page_info(self) -> list[PageInfo]
    def render_page(self, index: int, *, dpi: float = 150.0, rotation: int = 0) -> PIL.Image.Image
    def render_thumbnail(self, index: int, *, width: int = 180, rotation: int = 0) -> PIL.Image.Image

def open_document(path: Path | str, password: str | None = None) -> PdfDocument
def probe(path: Path | str) -> PdfInfo    # 暗号化は例外にせず encrypted=True で返す
```

- `rotation` は「追加で回す角度」。PDF 内の /Rotate は pdfium が自動適用済み
- `render_thumbnail` は回転後の横幅が `width` px になるスケールで描画する
- 戻り値の PIL Image は RGB。UI 側で `QImage` に変換する
- **スレッド**: pdfium 呼び出しはモジュール共通の RLock で直列化される。複数スレッドから呼んでも壊れないが並列にはならない

---

## core/compress.py — 圧縮（Ghostscript）

```python
@dataclass(frozen=True)
class CompressParams:
    dpi: int
    quality: int                 # JPEG 品質 1..100
    def describe(self) -> str    # '150dpi / 品質70'

QUALITY_LADDER: tuple[CompressParams, ...]   # 高品質→低品質。二分探索の対象
PRESETS: dict[str, CompressParams]           # 'high' / 'standard' / 'small'
DEFAULT_MAX_TRIALS = 6
MB = 1024 * 1024

class GhostscriptError(PdfError)
class GhostscriptNotFoundError(GhostscriptError)
class CompressCancelled(Exception)

@dataclass(frozen=True)
class CompressProgress:
    trial: int                   # 1 始まり
    max_trials: int
    params: CompressParams
    size_bytes: int | None       # None = 試行開始時
    message: str                 # そのまま表示できる日本語

@dataclass
class CompressResult:
    output_path: Path
    original_size: int
    output_size: int
    target_size: int | None      # プリセット圧縮では None
    achieved: bool
    trials: int
    params: CompressParams
    history: list[tuple[CompressParams, int]]
    ratio: float                 # property: output / original

ProgressCallback = Callable[[CompressProgress], None]
CancelCheck = Callable[[], bool]

def format_size(n: int) -> str   # '7.2 MB' / '830 KB' / '12 B'

def compress_to_target(
    src: Path | str, target_mb: float, *,
    out_dir: Path | None = None,          # None = 元と同じフォルダ
    max_trials: int = DEFAULT_MAX_TRIALS,
    ladder: tuple[CompressParams, ...] = QUALITY_LADDER,
    gs: Path | None = None,               # None = find_ghostscript()
    progress: ProgressCallback | None = None,
    cancel: CancelCheck | None = None,
) -> CompressResult

def compress_with_params(src, params: CompressParams, *, out_dir=None, gs=None, progress=None, cancel=None) -> CompressResult
def compress_preset(src, preset: str, **kw) -> CompressResult     # preset は PRESETS のキー

# 低レベル（通常は使わない）
def build_gs_command(gs: Path, src: Path, dst: Path, params: CompressParams) -> list[str]
def run_ghostscript(gs: Path, src: Path, dst: Path, params: CompressParams, cancel: CancelCheck | None = None) -> int
```

- 出力名は `utils.naming.output_file(src, "compressed", ".pdf")` → `yy-mm-dd_元名_compressed.pdf`
- 暗号化・破損 PDF は Ghostscript を呼ぶ前に `PdfError` で弾く
- `cancel()` が True を返すと Ghostscript を kill して `CompressCancelled`
- CLI: `python -m pdftool.core.compress 入力.pdf --target 5` / `--preset standard`

---

## utils/naming.py — 出力ファイル名規則

```python
def date_prefix(today: date | None = None) -> str                   # '26-09-12'
def unique_path(path: Path) -> Path                                  # 存在すれば _2, _3 …
def output_file(source: Path, suffix: str, ext: str | None = None,
                out_dir: Path | None = None, today: date | None = None) -> Path
        # → {out_dir or source.parent}/yy-mm-dd_{source.stem}_{suffix}{ext}
def output_dir(source: Path, suffix: str, out_dir: Path | None = None, today: date | None = None) -> Path
        # → {out_dir or source.parent}/yy-mm-dd_{source.stem}_{suffix}/   （mkdir はしない）
def numbered_name(source: Path, index: int, ext: str, width: int = 3) -> str
        # → '{source.stem}_p001.png'   index は 1 始まり
```

---

## utils/gs_locator.py — Ghostscript 検出

```python
def find_ghostscript() -> Path | None       # lru_cache。環境変数 PDFTOOL_GS → PATH → Program Files\gs\gs*\bin
def ghostscript_version(gs_path: Path) -> str | None
```

---

## 未実装（Sonnet フェーズで実装。シグネチャは Phase 3 完了時に追記）

- core/export.py
- core/batch.py
