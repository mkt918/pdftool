"""ページ操作（結合・分割・削除・回転・並べ替え）。

GUI 非依存。PySide6 を import しないこと。

設計:
    操作のたびにファイルを書くのではなく、メモリ上の「ページ参照リスト」
    (`PageRef` の列) を編集し、保存時に一度だけ pypdf で書き出す。
    `PageList` がその編集セッションで、Undo/Redo はリストのスナップショットを
    スタックに積むだけの単純方式。

    PageRef は不変 (frozen)。回転も「新しい PageRef に置き換える」ので、
    スナップショット = タプルのコピーで済む。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Iterable, Iterator, Sequence

from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject

from pdftool.core.document import (
    Crop,
    PdfBrokenError,
    PdfDocument,
    PdfEncryptedError,
    PdfError,
    validate_crop,
)
from pdftool.core.stamp import StampPainter, TextStamp
from pdftool.utils import naming

UNDO_LIMIT = 100


# ---------------------------------------------------------------------------
# ページ参照
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PageRef:
    source_path: Path   # 元 PDF（結合時は複数ファイルが混在しうる）
    source_index: int   # 元 PDF 内でのページ番号（0 始まり）
    rotation: int = 0   # 追加の累積回転 0/90/180/270（元の /Rotate には含まれない）
    crop: Crop | None = None  # トリミング余白 (左,上,右,下)=ページ幅/高さ比。回転前の見た目が基準

    def __post_init__(self):
        if self.rotation % 90 != 0:
            raise ValueError("rotation は 90 度単位")
        object.__setattr__(self, "rotation", self.rotation % 360)
        object.__setattr__(self, "crop", validate_crop(self.crop))

    def rotated(self, delta: int) -> "PageRef":
        return replace(self, rotation=(self.rotation + delta) % 360)


def _normalize_indices(indices: Iterable[int], n: int) -> list[int]:
    """重複を除き昇順にし、範囲チェックする。"""
    out = sorted(set(int(i) for i in indices))
    for i in out:
        if not 0 <= i < n:
            raise IndexError(f"ページ番号が範囲外です: {i} (0..{n - 1})")
    return out


# ---------------------------------------------------------------------------
# ページ範囲の文字列 ("1-5,8,10-12")
# ---------------------------------------------------------------------------
_RANGE_RE = re.compile(r"^\s*(\d+)\s*(?:-\s*(\d+)\s*)?$")


def parse_page_spec(spec: str, page_count: int) -> list[int]:
    """'1-5,8,10-12' 形式（1 始まり）を 0 始まりのインデックス列にする。

    - 区切りはカンマ（全角も可）。空白は無視
    - 重複は除き、出現順は保つ（並べ替え指定には使わない）
    - 範囲外・書式不正は ValueError（日本語メッセージ）
    """
    if not spec or not spec.strip():
        raise ValueError("ページ範囲が空です")
    result: list[int] = []
    seen: set[int] = set()
    for token in re.split(r"[,、，]", spec):
        if not token.strip():
            continue
        m = _RANGE_RE.match(token)
        if not m:
            raise ValueError(f"ページ範囲の書式が不正です: '{token.strip()}'（例: 1-5,8,10-12）")
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) else a
        if a < 1 or b < 1:
            raise ValueError("ページ番号は 1 以上で指定してください")
        if a > b:
            raise ValueError(f"範囲の順序が逆です: {a}-{b}")
        if b > page_count:
            raise ValueError(f"ページ {b} は存在しません（全 {page_count} ページ）")
        for p in range(a - 1, b):
            if p not in seen:
                seen.add(p)
                result.append(p)
    if not result:
        raise ValueError("ページ範囲が空です")
    return result


# ---------------------------------------------------------------------------
# 編集セッション
# ---------------------------------------------------------------------------
class PageList:
    """PageRef の列を編集する。すべての変更操作は Undo 可能。

    使い方:
        pl = PageList.from_pdf(path)
        pl.append_pdf(other)          # 結合
        pl.remove([0, 2])             # 削除
        pl.rotate([1], 90)            # 回転
        pl.move([3, 4], 0)            # 並べ替え（D&D）
        pl.save()                     # 書き出し（新規ファイル）
    """

    def __init__(self, pages: Iterable[PageRef] = (), passwords: dict[Path, str] | None = None):
        self._pages: tuple[PageRef, ...] = tuple(pages)
        self._passwords: dict[Path, str] = dict(passwords or {})
        self._undo: list[tuple[PageRef, ...]] = []
        self._redo: list[tuple[PageRef, ...]] = []
        self._saved_state: tuple[PageRef, ...] = self._pages

    # -- 生成 ---------------------------------------------------------------
    @staticmethod
    def load_refs(path: Path | str, password: str | None = None) -> list[PageRef]:
        """PDF の全ページを PageRef にする。開けない場合は PdfError。"""
        p = Path(path)
        with PdfDocument(p, password=password) as doc:
            return [PageRef(p, i) for i in range(doc.page_count)]

    @classmethod
    def from_pdf(cls, path: Path | str, password: str | None = None) -> "PageList":
        p = Path(path)
        pl = cls(cls.load_refs(p, password))
        if password:
            pl._passwords[p] = password
        return pl

    # -- 参照 ---------------------------------------------------------------
    @property
    def pages(self) -> tuple[PageRef, ...]:
        return self._pages

    def __len__(self) -> int:
        return len(self._pages)

    def __getitem__(self, i: int) -> PageRef:
        return self._pages[i]

    @property
    def sources(self) -> list[Path]:
        """含まれる元ファイル（出現順・重複なし）。"""
        seen: dict[Path, None] = {}
        for r in self._pages:
            seen.setdefault(r.source_path, None)
        return list(seen)

    @property
    def is_modified(self) -> bool:
        return self._pages != self._saved_state

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    # -- Undo/Redo -----------------------------------------------------------
    def _commit(self, new_pages: Sequence[PageRef]) -> None:
        new = tuple(new_pages)
        if new == self._pages:
            return
        self._undo.append(self._pages)
        if len(self._undo) > UNDO_LIMIT:
            del self._undo[0]
        self._redo.clear()
        self._pages = new

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(self._pages)
        self._pages = self._undo.pop()
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(self._pages)
        self._pages = self._redo.pop()
        return True

    # -- 変更操作 ---------------------------------------------------------
    def append_pdf(self, path: Path | str, password: str | None = None) -> int:
        """末尾に別 PDF の全ページを結合する。追加したページ数を返す。"""
        return self.insert_pdf(path, len(self._pages), password)

    def insert_pdf(self, path: Path | str, at: int, password: str | None = None) -> int:
        """位置 at（0..len）に別 PDF の全ページを挿入する。"""
        if not 0 <= at <= len(self._pages):
            raise IndexError(f"挿入位置が範囲外です: {at}")
        p = Path(path)
        refs = self.load_refs(p, password)
        if password:
            self._passwords[p] = password
        self._commit(self._pages[:at] + tuple(refs) + self._pages[at:])
        return len(refs)

    def remove(self, indices: Iterable[int]) -> None:
        """指定インデックスのページを削除する。全削除は不可（1 ページは残す）。"""
        idx = set(_normalize_indices(indices, len(self._pages)))
        if len(idx) >= len(self._pages):
            raise ValueError("すべてのページを削除することはできません")
        self._commit(r for i, r in enumerate(self._pages) if i not in idx)

    def rotate(self, indices: Iterable[int], delta: int) -> None:
        """指定ページを delta 度（90 の倍数、負も可）回転する。"""
        if delta % 90 != 0:
            raise ValueError("回転は 90 度単位で指定してください")
        idx = set(_normalize_indices(indices, len(self._pages)))
        self._commit(r.rotated(delta) if i in idx else r for i, r in enumerate(self._pages))

    def move(self, indices: Iterable[int], to: int) -> None:
        """指定ページ群（相対順は維持）を、元リスト上の位置 to の直前へ移動する。

        to は 0..len。ドラッグ&ドロップの「この位置に落とす」に対応する。
        """
        n = len(self._pages)
        if not 0 <= to <= n:
            raise IndexError(f"移動先が範囲外です: {to}")
        idx = _normalize_indices(indices, n)
        moving = [self._pages[i] for i in idx]
        idx_set = set(idx)
        rest = [r for i, r in enumerate(self._pages) if i not in idx_set]
        # to より前にあった移動対象の数だけ挿入位置がずれる
        insert_at = to - sum(1 for i in idx if i < to)
        self._commit(rest[:insert_at] + moving + rest[insert_at:])

    def set_crops(self, mapping: dict[int, Crop | None]) -> None:
        """ページごとにトリミング余白を設定する（None で解除）。1 回の Undo で戻せる。"""
        n = len(self._pages)
        for i in mapping:
            if not 0 <= i < n:
                raise IndexError(f"ページ番号が範囲外です: {i} (0..{n - 1})")
        self._commit(
            replace(r, crop=validate_crop(mapping[i])) if i in mapping else r
            for i, r in enumerate(self._pages)
        )

    def reorder(self, new_order: Sequence[int]) -> None:
        """new_order[k] = 新しい k 番目に来る旧インデックス（完全な順列であること）。"""
        n = len(self._pages)
        if sorted(new_order) != list(range(n)):
            raise ValueError("並べ替え指定が順列になっていません")
        self._commit(self._pages[i] for i in new_order)

    # -- 書き出し ---------------------------------------------------------
    def default_output_path(self, suffix: str = "edited") -> Path:
        """先頭ページの元ファイルを基準にした既定の出力パス。"""
        if not self._pages:
            raise ValueError("ページがありません")
        return naming.output_file(self._pages[0].source_path, suffix, ".pdf")

    def save(
        self,
        dest: Path | str | None = None,
        *,
        stamps: Sequence[TextStamp] | None = None,
        progress: Callable[[WriteProgress], None] | None = None,
        cancel: Callable[[], bool] | None = None,
    ) -> Path:
        """現在の状態を新規ファイルに書き出す。実際に書いたパスを返す。"""
        if not self._pages:
            raise ValueError("ページがありません")
        dest = Path(dest) if dest is not None else self.default_output_path()
        out = write_pages(self._pages, dest, self._passwords, stamps=stamps, progress=progress, cancel=cancel)
        self._saved_state = self._pages
        return out

    def export_subset(self, indices: Iterable[int], dest: Path | str | None = None) -> Path:
        """選択ページだけを別ファイルに書き出す（分割）。編集状態は変えない。"""
        idx = _normalize_indices(indices, len(self._pages))
        refs = [self._pages[i] for i in idx]
        dest = Path(dest) if dest is not None else naming.output_file(refs[0].source_path, "split", ".pdf")
        return write_pages(refs, dest, self._passwords)

    def export_split_all(self, out_dir: Path | str | None = None) -> list[Path]:
        """全ページを 1 ページ 1 ファイルに分割する。"""
        if not self._pages:
            raise ValueError("ページがありません")
        base = self._pages[0].source_path
        folder = naming.output_dir(base, "split", Path(out_dir) if out_dir else None)
        folder.mkdir(parents=True, exist_ok=True)
        outs: list[Path] = []
        for k, ref in enumerate(self._pages, start=1):
            outs.append(write_pages([ref], folder / naming.numbered_name(base, k, ".pdf"), self._passwords))
        return outs


# ---------------------------------------------------------------------------
# 書き出し（pypdf）
# ---------------------------------------------------------------------------
def _open_reader(path: Path, passwords: dict[Path, str]) -> PdfReader:
    try:
        reader = PdfReader(str(path))
    except FileNotFoundError as e:
        raise PdfError(f"ファイルが見つかりません: {path}", path) from e
    except Exception as e:  # pypdf の例外は種類が多いのでまとめる
        raise PdfBrokenError(f"PDF として読み取れません: {path.name}", path) from e
    if reader.is_encrypted:
        pw = passwords.get(path)
        if pw is None or not reader.decrypt(pw):
            raise PdfEncryptedError(f"パスワード付きの PDF です: {path.name}", path)
    return reader


def normalize_to_visible_box(page) -> None:
    """MediaBox/CropBox を「実際に見える範囲」（CropBox と MediaBox の共通部分）に揃える。"""
    mb, cb = page.mediabox, page.cropbox
    box = RectangleObject((
        max(float(mb.left), float(cb.left)), max(float(mb.bottom), float(cb.bottom)),
        min(float(mb.right), float(cb.right)), min(float(mb.top), float(cb.top)),
    ))
    page.mediabox = box
    page.cropbox = box
    for name in ("trimbox", "bleedbox", "artbox"):
        setattr(page, name, box)


def apply_crop_to_page(page, crop: Crop) -> None:
    """/Rotate を焼き込み済み(回転0)のページに、(左,上,右,下) の余白割合でトリミングを掛ける。"""
    l, t, r, b = crop
    box = page.cropbox
    w, h = float(box.width), float(box.height)
    new = RectangleObject((
        float(box.left) + l * w, float(box.bottom) + b * h,
        float(box.right) - r * w, float(box.top) - t * h,
    ))
    page.mediabox = new
    page.cropbox = new
    for name in ("trimbox", "bleedbox", "artbox"):
        setattr(page, name, new)


class WriteCancelled(Exception):
    """cancel コールバックにより中断された（Worker は "...Cancelled" を中断として扱う）。"""


@dataclass(frozen=True)
class WriteProgress:
    current: int    # 1 始まり
    total: int
    message: str


def prepare_pages(
    pages: Sequence[PageRef],
    writer: PdfWriter,
    passwords: dict[Path, str] | None = None,
    *,
    upright: bool = False,
    stamps: Sequence[TextStamp] | None = None,
    progress: Callable[[WriteProgress], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> Iterator:
    """PageRef を順に writer へ追加し、回転・トリミング・スタンプを反映したページを 1 枚ずつ yield する。

    upright=True なら /Rotate を内容へ焼き込み、見た目どおり回転 0 のページにして返す
    （面付けなど、ページを別の用紙へ配置する用途）。トリミング・スタンプを使うときも焼き込む
    （しおり・リンク注釈の位置は回転ページで正しくなくなりうる）。
    どちらも使わない通常保存では、従来通り /Rotate の加算だけで元ページの構造を壊さない。
    """
    passwords = passwords or {}
    painter = StampPainter(writer) if stamps else None
    bake = upright or bool(stamps)
    readers: dict[Path, PdfReader] = {}
    total = len(pages)
    for i, ref in enumerate(pages):
        if cancel is not None and cancel():
            raise WriteCancelled()
        reader = readers.get(ref.source_path)
        if reader is None:
            reader = readers[ref.source_path] = _open_reader(ref.source_path, passwords)
        if not 0 <= ref.source_index < len(reader.pages):
            raise PdfError(
                f"{ref.source_path.name} にページ {ref.source_index + 1} がありません", ref.source_path
            )
        added = writer.add_page(reader.pages[ref.source_index])
        if ref.crop or bake:
            # /Rotate を内容に焼き込んでから、見た目基準の余白で切る（ref.rotation はその後）
            added.transfer_rotation_to_content()
            # CropBox が MediaBox より小さい PDF は「見える範囲」が CropBox。スタンプの位置・面付けの
            # 倍率はその見える範囲を基準にしたいので、MediaBox を見える範囲に揃える
            normalize_to_visible_box(added)
        if ref.crop:
            apply_crop_to_page(added, ref.crop)
        if ref.rotation:
            added.rotate(ref.rotation)
            if bake:
                added.transfer_rotation_to_content()
        if painter is not None:
            painter.stamp_page(added, list(stamps), i, total)
        if progress:
            progress(WriteProgress(i + 1, total, f"ページを処理しています… {i + 1}/{total}"))
        yield added


def _atomic_write(writer: PdfWriter, dest: Path) -> None:
    tmp = dest.with_name(dest.name + ".part")
    try:
        with open(tmp, "wb") as f:
            writer.write(f)
        tmp.replace(dest)
    finally:
        if tmp.exists():
            tmp.unlink()


def check_dest(pages: Sequence[PageRef], dest: Path | str) -> Path:
    """出力先の検証: 元ファイルの上書きを拒否し、既存なら _2, _3 … を付けて、フォルダを作る。"""
    dest = Path(dest)
    sources = {r.source_path.resolve() for r in pages}
    if dest.resolve() in sources:
        raise PdfError("元ファイルと同じ名前には保存できません（原本は上書きしません）", dest)
    dest = naming.unique_path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    return dest


def write_pages(
    pages: Sequence[PageRef],
    dest: Path | str,
    passwords: dict[Path, str] | None = None,
    *,
    stamps: Sequence[TextStamp] | None = None,
    progress: Callable[[WriteProgress], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> Path:
    """PageRef の列を 1 つの PDF として書き出す。

    - 元ファイルと同じパスへの書き出しは拒否（原本を上書きしない）
    - dest が既に存在すれば `_2`, `_3` … を付けた別名にする
    - stamps を渡すと、ページ番号・透かし・ラベルを重ねる（core/stamp.py）
    - 戻り値は実際に書いたパス
    """
    if not pages:
        raise ValueError("書き出すページがありません")
    dest = check_dest(pages, dest)
    writer = PdfWriter()
    for _ in prepare_pages(pages, writer, passwords, stamps=stamps, progress=progress, cancel=cancel):
        pass
    _atomic_write(writer, dest)
    return dest


def merge_pdfs(paths: Sequence[Path | str], dest: Path | str | None = None) -> Path:
    """複数 PDF を順に結合する簡易関数（暗号化 PDF は不可）。"""
    if not paths:
        raise ValueError("結合するファイルがありません")
    refs: list[PageRef] = []
    for p in paths:
        refs.extend(PageList.load_refs(p))
    dest = Path(dest) if dest is not None else naming.output_file(Path(paths[0]), "merged", ".pdf")
    return write_pages(refs, dest)
