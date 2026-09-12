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

## core/pageops.py — ページ操作（結合・分割・削除・回転・並べ替え）

```python
@dataclass(frozen=True)
class PageRef:
    source_path: Path
    source_index: int            # 元 PDF 内の 0 始まり
    rotation: int = 0            # 追加の累積回転 0/90/180/270（自動で mod 360）
    def rotated(self, delta: int) -> PageRef

def parse_page_spec(spec: str, page_count: int) -> list[int]
        # '1-5,8,10-12'（1 始まり）→ 0 始まりインデックス列。不正は ValueError（日本語）

class PageList:                  # 編集セッション。全変更操作は Undo 可能
    def __init__(self, pages: Iterable[PageRef] = (), passwords: dict[Path, str] | None = None)
    @staticmethod
    def load_refs(path: Path | str, password: str | None = None) -> list[PageRef]
    @classmethod
    def from_pdf(cls, path: Path | str, password: str | None = None) -> PageList

    pages: tuple[PageRef, ...]   # property
    __len__, __getitem__
    sources: list[Path]          # property。含まれる元ファイル（出現順）
    is_modified: bool            # property。最後の save 以降に変更があるか
    can_undo / can_redo: bool
    def undo(self) -> bool
    def redo(self) -> bool

    # 変更操作（indices は 0 始まり。重複・順不同可）
    def append_pdf(self, path, password=None) -> int          # 結合。追加ページ数を返す
    def insert_pdf(self, path, at: int, password=None) -> int
    def remove(self, indices: Iterable[int]) -> None          # 全削除は ValueError
    def rotate(self, indices: Iterable[int], delta: int) -> None   # 90 の倍数、負も可
    def move(self, indices: Iterable[int], to: int) -> None   # 選択群を「元の位置 to の直前」へ（D&D 用）
    def reorder(self, new_order: Sequence[int]) -> None       # 完全な順列

    # 書き出し（すべて新規ファイル。既存名なら _2, _3 …）
    def default_output_path(self, suffix: str = "edited") -> Path
    def save(self, dest: Path | str | None = None) -> Path    # None → yy-mm-dd_先頭元名_edited.pdf
    def export_subset(self, indices, dest=None) -> Path       # 分割（選択ページを別ファイルへ）。状態は不変
    def export_split_all(self, out_dir=None) -> list[Path]    # 1 ページ 1 ファイル → yy-mm-dd_元名_split/元名_p001.pdf

def write_pages(pages: Sequence[PageRef], dest: Path | str, passwords: dict[Path, str] | None = None) -> Path
        # 元ファイルと同じパスは PdfError で拒否。.part に書いてから rename
def merge_pdfs(paths: Sequence[Path | str], dest: Path | str | None = None) -> Path
```

- 回転は保存時に pypdf の `page.rotate()` で /Rotate に加算する（元の /Rotate と累積）
- サムネイル表示側は `PdfDocument.render_thumbnail(ref.source_index, rotation=ref.rotation)` で回転をプレビューする

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
