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

## core/export.py — 画像書き出し（Phase 5 で実装済み）

```python
class ExportCancelled(Exception)

@dataclass(frozen=True)
class ExportOptions:
    dpi: int = 200                # 2026-09-12 に150→200へ変更
    fmt: str = "png"             # 'png' | 'jpg'
    jpeg_quality: int = 90       # jpg のときのみ

@dataclass(frozen=True)
class ExportProgress:
    current: int                 # 1 始まり
    total: int
    path: Path                   # 今書いたファイル
    message: str                 # 日本語

@dataclass
class ExportResult:
    output_dir: Path
    files: list[Path]

def export_pages(
    src: Path | str, indices: Sequence[int] | None = None, *,    # None = 全ページ（0 始まり）
    options: ExportOptions = ExportOptions(),
    out_dir: Path | None = None,                                  # None = 元と同じ場所
    password: str | None = None,
    progress: Callable[[ExportProgress], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> ExportResult
    # 出力先: naming.output_dir(src, options.fmt) → yy-mm-dd_元名_png/
    # ファイル名: naming.numbered_name(src, 元ページ番号(1 始まり), ext) → 元名_p003.png（選択ページでも元番号を保つ）

def export_refs(
    refs: Sequence[PageRef], base: Path, *,                       # 編集中の PageList から。回転を反映
    options: ExportOptions = ExportOptions(),
    out_dir: Path | None = None,
    passwords: dict[Path, str] | None = None,
    progress=None, cancel=None,
) -> ExportResult
    # base は出力フォルダ名・ファイル名の基準（通常は page_list[0].source_path）。連番は並び順 1..n
```

- 描画は `PdfDocument.render_page(index, dpi=options.dpi, rotation=...)`。PIL の `save()` で書く
- `cancel()` が True なら `ExportCancelled`（書き終えたファイルは残す）
- 文字列指定は UI 側で `pageops.parse_page_spec()` を通してから `indices` に渡す

---

## core/batch.py — フォルダ一括処理（Phase 5 で実装済み）

```python
class BatchCancelled(Exception)

@dataclass(frozen=True)
class BatchItemResult:
    path: Path
    ok: bool
    original_size: int
    output_size: int | None      # 圧縮: 出力サイズ / 書き出し: None
    output: Path | None          # 圧縮: 出力 PDF / 書き出し: 出力フォルダ
    message: str                 # 成功なら空、失敗なら日本語の理由

@dataclass(frozen=True)
class BatchProgress:
    index: int                   # 1 始まり
    total: int
    path: Path
    message: str

@dataclass
class BatchResult:
    items: list[BatchItemResult]
    ok_count: int                # property でも可
    fail_count: int

def list_pdfs(folder: Path | str, recursive: bool = False) -> list[Path]     # 名前順。*.pdf のみ

def batch_compress(
    folder: Path | str, *, target_mb: float | None = None, preset: str | None = None,   # どちらか必須
    recursive: bool = False, out_dir: Path | None = None,
    progress: Callable[[BatchProgress], None] | None = None, cancel: Callable[[], bool] | None = None,
) -> BatchResult

def batch_export(
    folder: Path | str, *, options: ExportOptions = ExportOptions(),
    recursive: bool = False, out_dir: Path | None = None,
    progress=None, cancel=None,
) -> BatchResult
```

- 1 件失敗しても止めない。`PdfError` / `GhostscriptError` は `BatchItemResult.message` に日本語で入れる
- 暗号化 PDF はバッチではスキップ扱い（ok=False, message='パスワード付きのためスキップ'）
- `cancel()` が True なら `BatchCancelled`（それまでの items を持たせる: `e.partial: BatchResult`）

---

## UI 骨格（Phase 3 で確定。Sonnet フェーズのパネルはこの範囲だけを使う）

### utils/worker.py

```python
def pil_to_qimage(img: PIL.Image.Image) -> QImage

class Worker(QRunnable):
    signals.finished: Signal(object)     # 戻り値
    signals.error:    Signal(str, object) # (日本語メッセージ, 例外)
    signals.progress: Signal(object)     # fn が progress(x) に渡したもの
    signals.cancelled: Signal(object)    # 中断時の部分結果（例: BatchCancelled.partial）。無ければ None
    def __init__(self, fn, *args, **kwargs)
        # fn のシグネチャに 'progress'/'cancel' があれば自動注入。
        # **kw で下位関数へ丸ごと転送するだけの関数（VAR_KEYWORD あり。例: compress_preset）にも注入する
        # （fn 側が実際に受け取れるかはシグネチャで判断できないため、渡して問題なければ常に注入）
        # 例外の型名が '...Cancelled' で終わる、または cancel() が True の状態で例外が出た場合は
        # cancelled を emit する（getattr(e, "partial", None) があればそれを一緒に渡す）
    def cancel(self) -> None
    def start(self, pool: QThreadPool | None = None) -> Worker

@dataclass(frozen=True)
class ThumbRequest: key, source_path, source_index, rotation, width

class ThumbnailWorker(QThread):          # 常駐。要求は後入れ先出し
    ready:  Signal(str, QImage)
    failed: Signal(str, str)
    def set_password(self, path: Path, password: str)
    def request(self, req: ThumbRequest)
    def clear_queue(self)
    def close_documents(self)
    def stop(self)
```

- `render_thumbnail` が `PdfError` 以外の想定外例外を出したときは、そのファイルの
  キャッシュ済み `PdfDocument` を閉じて `_docs` から捨てる(2026-09-13〜)。
  1回の失敗で以後そのファイルの全ページが失敗し続けるのを防ぐため。

### ui/thumbnail_view.py

```python
THUMB_SIZES = {"小": 120, "中": 180, "大": 240}; DEFAULT_THUMB_WIDTH = 180

class ThumbnailModel(QAbstractListModel):
    page_list_changed: Signal()
    page_list: PageList | None          # property
    def set_page_list(self, pl: PageList | None)
    def set_password(self, path, password)
    def thumbnail_width() / set_thumbnail_width(width)
    # 変更操作（PageList を直接触らず必ずこちらを使う）
    def remove_pages(rows) / rotate_pages(rows, delta) / move_pages(rows, to)
    def append_pdf(path, password=None) -> int
    def insert_pdf(path, at: int, password=None) -> int
    def undo() -> bool / redo() -> bool
    # data(): DisplayRole=ページ番号, DecorationRole=QPixmap（未描画はプレースホルダ）, UserRole=PageRef

class ThumbnailDelegate(QStyledItemDelegate):
    # 選択中のセルに太い枠（BORDER_COLOR/BORDER_WIDTH）を上書き描画する（2026-09-15〜）。
    # 既定の背景ハイライトだけだとサムネイル画像に隠れて選択が分かりにくかったため

class ThumbnailView(QListView):
    page_activated: Signal(int)          # ダブルクリック
    selection_changed: Signal(list)      # 選択行（昇順）
    def selected_rows(self) -> list[int]
    def select_rows(self, rows)
    def set_thumbnail_width(self, width)
    # setItemDelegate(ThumbnailDelegate(...)) 済み
```

### ui/main_window.py

```python
class MainWindow(QMainWindow):
    gs_path: Path | None; gs_version: str | None
    settings: QSettings          # QSettings()。組織名/アプリ名は __init__ で設定済み
    model: ThumbnailModel; view: ThumbnailView; tabs: QTabWidget; panels: list[PanelBase]
    page_list: PageList | None           # property
    def current_source(self) -> Path | None
    def open_pdf(self, path) -> bool     # 暗号化ならパスワードを聞く。変更ありなら破棄確認
    def append_pdf(self, path) -> bool
    def show_error(self, message: str, title="エラー")
    def password_for(self, path: Path) -> str | None      # そのファイルを開くのに使ったパスワード
    def passwords_dict(self) -> dict[Path, str]            # export_refs 等に渡す用の一覧
    def run_worker(self, fn, *args, on_finished=None, on_error=None, on_progress=None, on_cancelled=None, **kwargs) -> Worker
    # ツールバー: act_open / act_append / act_save / act_undo / act_redo / act_rot_l / act_rot_r
    #            / act_delete / act_select_all(Ctrl+A) / size_combo（選択はQSettingsの'thumb_size'に永続化）
    # ステータスバー: lbl_doc / lbl_gs / gs_recheck_btn（Ghostscript未検出時のみ表示。押すとキャッシュを
    #   クリアして再検出し、全パネルの on_document_changed() を呼び直す）
    # 終了時（closeEvent）: 実行中の run_worker ジョブが残っていれば確認ダイアログを出し、
    #   Yesなら全ワーカーに cancel() してから待つ（Noなら閉じない）
```

### ui/panels/base.py

```python
class PanelBase(QWidget):
    title: str
    def __init__(self, window: MainWindow, parent=None)     # self.window_ に保持
    def on_document_changed(self) -> None                   # フック
    def on_selection_changed(self, rows: list[int]) -> None # フック
```

パネルの実装ルール:
- 重い処理は必ず `window_.run_worker(...)` 経由。core 関数の `progress` / `cancel` は自動で配線される
- 対象ページは `window_.view.selected_rows()`（0 始まり）。`PageRef` は `window_.page_list[row]`
- Ghostscript が無いときは `window_.gs_path is None`。圧縮パネルは自分を無効化して理由を表示する
- MainWindow の `_add_panel()` にある PlaceholderPanel を本実装に差し替える
- ジョブ実行中は入力欄も触れないようにする（`_set_running_ui(bool)` という名前で統一。
  実行開始時に `True`、`_reset_running_state()` の中で `False` を呼ぶ）。実行中に値を変えても
  開始時のスナップショットに反映されないので、変えられる見た目のまま放置すると誤解を招く


### Phase 4 で実装したパネル（ui/panels/*.py）

タブの表示順は `MainWindow.__init__` の `for panel_cls in (ExportPanel, CompressPanel, PageOpsPanel, BatchPanel)`
で決まる（2026-09-15〜、画像書き出しが最初。それまでは圧縮が最初だった）。
`win.panels[N]` のインデックスもこの順（テストの `export_panel()`/`compress_panel()` 等のヘルパー参照）。

- `ExportPanel`: 全ページ / 選択ページ / 範囲指定(`parse_page_spec`) で `export_refs` を実行（pageops の回転・並べ替えを反映）。
  左のサムネイル選択と「対象ページ」コンボは自動連動する(選択あり→選択ページ、選択解除→全ページ)。
  「範囲指定」を選んでいる間はこの自動連動を止める(手動入力を上書きしない)。既定 dpi は 200(2026-09-12〜)
- `CompressPanel`: 目標サイズ / プリセットで `window_.current_source()` を圧縮。出力先は常に元と同じフォルダ。
  目標サイズの初期値は 28MB(多くのアップロード先にある30MB制限を意識した値。2026-09-12〜)
- `PageOpsPanel`: 範囲指定選択、任意位置への PDF 挿入（`model.insert_pdf`）、分割（`page_list.export_subset` / `export_split_all`、別スレッド実行）
- `BatchPanel`: フォルダ指定で `batch_compress` / `batch_export`。結果は `QTableWidget` に一覧表示、キャンセル時は `partial` があれば表示する。
  2026-09-15〜「①対象フォルダ→②処理内容→③実行」の3ステップ構成に変更。`folder_edit`/`recursive_check`/
  各モードの設定が変わるたびに `_update_summary()` が `list_pdfs()` で対象件数を数え直し、
  `folder_status_label` に「◯件のPDFを○○します」と実行前プレビューを表示、`run_btn` の有効/無効も
  それに応じて自動更新する（フォルダ未指定・無効・PDF無し・Ghostscript無しなら押せない）。
  クリックしてからエラーダイアログを出す方式はやめ、事前に分かるようにした

いずれも `PanelBase` を継承し、`window_.run_worker()` 経由でのみ core を呼ぶ。
`QMessageBox` 等のモーダルは offscreen テストでは固まるため、テストでは `window_.show_error` を monkeypatch すること。

`MainWindow.settings`(`QSettings`)は組織名/アプリ名を `QCoreApplication.setOrganizationName/setApplicationName`
で設定してから引数無し `QSettings()` で作る。`QSettings("org", "app")` の2引数コンストラクタは常に
NativeFormat 固定で、テストで `setDefaultFormat(IniFormat)` を当てても実レジストリに書きに行ってしまうため
（実際に一度汚した→`.clear()`で除去した経緯あり）。テストの `win` フィクスチャは `tmp_path` を使い
`QSettings.setDefaultFormat(IniFormat)` + `setPath(IniFormat, UserScope, tmp_path)` で隔離すること。

---

## exe化（PyInstaller、v1完了後に追加）

- `pdftool.spec`: ビルド定義。`build.bat` から使う（`.venv\Scripts\pyinstaller --noconfirm pdftool.spec`）
- 出力: `dist\PDFユーティリティ.exe`（onefile / windowed、約80MB）
- pypdfium2 のネイティブDLL（pypdfium2_raw）は `collect_all` で明示的に同梱している
  （pyinstaller-hooks-contrib に標準フックはあるが、バージョン差異対策として明示）
- **Ghostscript は同梱する**（2026-09-13〜。PLAN.md のライセンス注意を参照）。
  `build.bat` が先に `scripts/bundle_ghostscript.py` を実行し、実機の Ghostscript インストールから
  `bin/{gswin64c.exe, gsdll64.dll}` + `Resource/` + `lib/` + `iccprofiles/` だけを `vendor/gs/` へ
  コピーする（doc/examples/アンインストーラは除外。約66MB→約42MB）。`vendor/` は git 管理しない。
  `pdftool.spec` はこれを `gs/...` としてバイナリ内に埋め込み、`gs_locator.find_ghostscript()` が
  `sys._MEIPASS/gs/bin/...` を PATH/Program Files より優先して探す（ソース実行時は `_MEIPASS` が
  無いのでこの分岐は素通りし、従来通り外部インストールを検出する＝`run.bat` は影響を受けない）
- `src/pdftool/__main__.py` に `sys.excepthook` を追加。exe化(--windowed)するとコンソールが無く
  素の例外が消えてしまうため、`%TEMP%\pdftool_error.log` に書き出し、QApplication があれば
  ダイアログでも通知する
- 実機で `dist\PDFユーティリティ.exe` の起動・日本語PDFのオープン・サムネイル描画まで動作確認済み。
  Ghostscript の同梱についても、`ProgramFiles`/`ProgramFiles(x86)` を存在しないパスに向けて外部検出を
  塞いだ状態で実際に起動し、`_MEIPASS` に展開された `gs/bin/gswin64c.exe` が実際に PDF 変換に
  成功することを確認済み（外部インストール無しで単体動作する）


---

## ドラッグ&ドロップ（2026-10-06〜）

`ThumbnailView` は Qt 既定の受け入れ判定（アイテム上のドロップを拒否する等）に頼らず、
`dragEnterEvent`/`dragMoveEvent`/`dropEvent` を自前で処理する。

- 内部ドラッグ（並べ替え）は `startDrag()` の間だけ `_internal_drag` が True。`event.source()` は使わない
  （合成イベントのテストでも判定できるように）。ドロップ時は `model.move_pages(rows, to)` →
  移動後の位置を再選択。`setDropAction(IgnoreAction)` で Qt 既定の `removeRows` を走らせない
- 外部ドラッグ（エクスプローラー等からの PDF）は `mimeData().hasUrls()` で判定し、`.pdf` のみ受け付け。
  ドロップ時に `files_dropped(paths: list, to: int, shift: bool)` を emit。`MainWindow._on_files_dropped` が
  文書あり → `insert_pdfs(paths, to)`（`model.insert_pdf` を順に、位置をずらしながら）、
  文書なし/Shift → `open_pdf(paths[0])` + 残りを末尾に結合
- `_drop_target(pos) -> (to, QLine)`: 行ごとの帯（y 範囲）の中で x がアイテム中心より左なら直前、
  右なら直後。帯が無ければ先頭より上=0・最下行より下=末尾・行間=最寄りの行。
  線は `paintEvent` で描画（`setDropIndicatorShown(False)`）。ドラッグ中は上下端でスクロール
- `MainWindow.dropEvent`（サムネイル外）は末尾に結合
- テスト: 実マウスによる QDrag は offscreen では即キャンセルされるため再現できない。
  `QDropEvent` を合成してハンドラを直接呼ぶ（`QMimeData` は所有されないので参照を保持すること。
  手放すと pytest の repr 時にクラッシュする）


---

## 追加機能（2026-10-06）: 画像取り込み・トリミング・白紙検出・スタンプ・面付け

すべて `core/` に GUI 非依存で実装（PySide6 を import しない）。

### core/document.py（追記）

```python
Crop = tuple[float, float, float, float]         # (左, 上, 右, 下) の余白。ページ幅/高さに対する割合 0..1
def validate_crop(crop: Crop | None) -> Crop | None    # 全部0→None、負数/残らない→ValueError
def rotate_crop(crop: Crop, rotation: int) -> Crop      # 時計回り rotation 度に回した見た目での余白へ換算

PdfDocument.render_page(index, *, dpi=150.0, rotation=0, crop=None)
PdfDocument.render_thumbnail(index, *, width=180, rotation=0, crop=None)   # width はトリミング後の横幅
```

トリミングの基準は「PDF 内 /Rotate を適用した、ユーザー回転(ref.rotation)前の見た目」。
保存時は `transfer_rotation_to_content()` で /Rotate を内容へ焼き込んでから mediabox/cropbox を縮め、その後に
ref.rotation を掛ける（`pageops.apply_crop_to_page`）。

### core/pageops.py（追記）

- `PageRef.crop: Crop | None = None`（frozen。`__post_init__` で `validate_crop`）
- `PageList.set_crops(mapping: dict[int, Crop | None])` — 1 回の Undo で戻せる。変化が無ければ積まない
- `prepare_pages(pages, writer, passwords=None, *, upright=False, stamps=None, progress=None, cancel=None) -> Iterator[PageObject]`
  回転・トリミング・スタンプを反映したページを writer へ追加して yield。`upright`/`stamps` のときは /Rotate を
  焼き込んで回転 0 にする（面付け・スタンプ用）。それ以外は従来通り /Rotate の加算だけ
- `write_pages(pages, dest, passwords=None, *, stamps=None, progress=None, cancel=None)`、
  `PageList.save(dest=None, *, stamps=None, progress=None, cancel=None)`
- `WriteProgress(current, total, message)` / `WriteCancelled`（名前が Cancelled で終わる→Worker が中断扱い）
- `check_dest(pages, dest)`（元ファイルの上書き拒否＋連番＋mkdir）、`_atomic_write(writer, dest)`

### core/images.py

```python
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tif", ".tiff", ".webp")
def is_image(path) -> bool
def image_to_pdf(src, dest_dir=None) -> Path       # 1画像=1ページ(複数フレームTIFF等は全フレーム)。長辺=A4長辺(842pt)
def convert_images(paths) -> list[Path]             # PDF はそのまま、画像は一時PDFに。順序保持（ワーカーで呼ぶ）
```

EXIF の向きを反映、アルファは白背景に合成。変換結果は `utils/tempfiles` に「一時PDF→元の画像」を登録し、
`utils/naming`（output_file / output_dir / numbered_name）が `origin_of()` を通すので、
**保存・書き出しの名前と場所は元の画像が基準**になる。一時フォルダはプロセス終了時(atexit)に削除。

### core/trim.py / core/blank.py

```python
class ScanCancelled(Exception); ScanProgress(current, total, message)
content_margins(doc, index, *, pad_mm=5.0, dpi=50.0, edge=0.015) -> Crop | None    # 白紙は None
auto_crops(refs, indices, *, pad_mm=5.0, unify=False, passwords=None, progress=None, cancel=None) -> dict[int, Crop|None]
manual_crops(refs, indices, *, left_mm=0, top_mm=0, right_mm=0, bottom_mm=0, passwords=None) -> dict[int, Crop|None]

SENSITIVITY_STRICT/NORMAL/LOOSE = 0.0002 / 0.001 / 0.004     # インクの割合の上限
ink_ratio(gray, edge=0.04) -> float
detect_blank_pages(refs, *, sensitivity=NORMAL, dpi=40, edge=0.04, passwords=None, progress=None, cancel=None) -> list[int]
```

検出は低解像度のグレー描画。インクの境目は紙の地の色から決める（灰色がかった紙でも判定できる）。
外周は無視し、孤立した 1px（ごみ）は捨てる。白紙判定は ref.crop 適用後の見た目。自動では削除せず、
UI（`ui/panels/cleanup_groups.py`）が見つけたページを**選択**するだけ。結果適用時に
`pl.pages is snapshot` でページ構成の変化を検出し、変わっていたら適用しない。

### core/stamp.py

```python
@dataclass(frozen=True) class TextStamp: text, position, size_pt, color, opacity, angle, margin_pt, border, bold, start, skip_first
    # text 中の {n}=ページ番号 {total}=総ページ数。position は POSITIONS（7箇所）
page_number_stamp(...) / watermark_stamp(text, ...) / label_stamp(text, ...)
render_stamp_image(stamp, text) -> RGBA        # Pillow で描画（Windows の日本語フォント）。1pt=4px
class StampPainter(writer): stamp_page(page, stamps, index, total)
```

アルファ付き(SMask)の画像 XObject として重ねる（日本語フォントの埋め込み不要）。同じ内容の画像は 1 オブジェクトを
全ページで共有。**`flate_encode()` は圧縮済みの新オブジェクトを返す**（in-place ではない。これを忘れると
透かしで 17MB になった）。

### core/impose.py

```python
@dataclass(frozen=True) class ImposeSpec: cols=2, rows=1, paper="A4", landscape=None, margin_mm=8, gutter_mm=4, right_to_left=False, border=False
PRESETS = {"2in1": ..., "4in1": ...};  PAPER_SIZES_MM
cell_rects(spec) -> list[(x, y, w, h)]          # 読む順の各枠（原点は用紙左下）
impose_pages(pages, dest, spec, passwords=None, *, stamps=None, progress=None, cancel=None) -> Path
```

ページは `prepare_pages(upright=True)` で見た目どおりに整えてから、枠に収まる最大倍率（縦横比維持）で中央に配置。
スタンプは元のページへ先に重ねる。

### UI

- `ThumbnailView.wheelEvent`: 1ノッチ=サムネイル高さの1/4（Qt 既定は IconMode で 1 行×3 前後と大きく飛ぶ）。
  タッチパッドの精密スクロール(pixelDelta)はそのまま。Ctrl+ホイールは無視
- 画像のドロップ/ダイアログ: `MainWindow._on_files_dropped` が画像を含むとき `convert_images` をワーカーで実行してから
  `_merge_files`。`open_files(paths)`（先頭を開き残りを末尾に結合。起動引数・「開く」ダイアログが使う）
- `ThumbnailModel.set_crops(mapping)`: 選択を保ったまま該当サムネイルだけ描き直す。キャッシュキーに crop を含む
- タブ順: 画像書き出し / 圧縮 / ページ操作 / **仕上げ** / バッチ（`win.panels[N]` もこの順）
- `ui/panels/cleanup_groups.py`（BlankPageGroup / TrimGroup、PageOpsPanel 内。スクロール可）、
  `ui/panels/finish_panel.py`（FinishPanel。`build_output` / `render_preview` はワーカーから呼ぶ純粋関数）、
  `ui/preview_dialog.py`（PagePreviewDialog を main_window から切り出し）

### 不具合調査で直したこと（2026-10-06）

- **CropBox が MediaBox より小さい PDF**: スタンプ位置・面付けの倍率/配置が MediaBox 全体を基準にしていて、
  スタンプが見えない範囲に出る・面付けで小さく偏って置かれていた。`prepare_pages` が /Rotate を焼き込んだ直後に
  `normalize_to_visible_box(page)`（MediaBox/CropBox を両者の共通部分＝実際に見える範囲に揃える）を呼ぶように修正
- 画像を開く/結合する流れで、ステータスバーの「画像を変換しています…」が残り続け、文書情報（`lbl_doc`）が
  隠れたままになっていた → `_merge_files` の先頭で `clearMessage()`。`statusBar().showMessage` の間は
  `addWidget`（非 permanent）のウィジェットが隠れる点に注意
- `auto_crops`: ほとんど何も残らない結果（点だけのページ等）で `validate_crop` が ValueError を出し、
  選択全体の処理がエラーになっていた → そのページだけ飛ばす
- 向きの違うページを `unify=True` でそろえると、全体を含む範囲＝トリミングなしになるのは仕様どおり（不具合ではない）
