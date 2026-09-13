"""exe に同梱する Ghostscript 一式を vendor/gs にコピーする。

build.bat から呼ばれる。実機にインストール済みの Ghostscript（gs_locator が検出する
もの）から、実行に必要な最小限のファイルだけを抜き出す:
  bin/{gswin64c.exe, gsdll64.dll}, Resource/, lib/, iccprofiles/
（doc/ examples/ アンインストーラは不要なので含めない。約60MB→約42MBに削減）

vendor/ は git 管理しない（.gitignore 参照）。ビルドのたびに実機の Ghostscript から
作り直す想定。

Ghostscript は AGPL。exe に同梱して配布する場合、組み合わせた成果物の完全なソースを
提供する義務が生じる（本リポジトリ自体がそのソース）。docs/PLAN.md 参照。
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from pdftool.utils.gs_locator import find_ghostscript  # noqa: E402

VENDOR_DIR = ROOT / "vendor" / "gs"
NEEDED_SUBDIRS = ("Resource", "lib", "iccprofiles")
_BITNESS_DLL = {"gswin64c": "gsdll64.dll", "gswin32c": "gsdll32.dll"}


def main() -> int:
    find_ghostscript.cache_clear()
    gs = find_ghostscript()
    if gs is None:
        print("[エラー] Ghostscript が見つかりません。先に README の手順でインストールしてください。",
              file=sys.stderr)
        return 1

    dll_name = _BITNESS_DLL.get(gs.stem)
    if dll_name is None:
        print(f"[エラー] 同梱に対応していない Ghostscript です: {gs}"
              "（gswin64c.exe / gswin32c.exe のみ対応）", file=sys.stderr)
        return 1
    dll = gs.parent / dll_name
    if not dll.is_file():
        print(f"[エラー] {dll} が見つかりません。", file=sys.stderr)
        return 1

    install_root = gs.parent.parent  # .../gsX.Y.Z/bin/gswin64c.exe -> .../gsX.Y.Z

    if VENDOR_DIR.exists():
        shutil.rmtree(VENDOR_DIR)
    (VENDOR_DIR / "bin").mkdir(parents=True)
    shutil.copy2(gs, VENDOR_DIR / "bin" / gs.name)
    shutil.copy2(dll, VENDOR_DIR / "bin" / dll.name)

    for name in NEEDED_SUBDIRS:
        src = install_root / name
        if src.is_dir():
            shutil.copytree(src, VENDOR_DIR / name)
        else:
            print(f"[警告] {src} が見つかりません（無くても動く場合があります）")

    size_mb = sum(f.stat().st_size for f in VENDOR_DIR.rglob("*") if f.is_file()) / (1024 * 1024)
    print(f"Ghostscript を同梱用にコピーしました: {VENDOR_DIR}（約{size_mb:.0f}MB）")
    print(f"  元: {gs}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
