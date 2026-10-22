"""新しい版を作って GitHub Releases に公開する（アプリはこれを見て自動更新する）。

使い方: release.bat 0.2.0 ["更新内容のメモ"]
  1. src/pdftool/__init__.py の __version__ を書き換える
  2. テスト → exe ビルド（pdftool.spec）
  3. コミット・タグ・push
  4. dist のアプリのフォルダを PDFUtility.zip にまとめて Release に添付
"""

from __future__ import annotations

import re
import zipfile
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INIT = ROOT / "src" / "pdftool" / "__init__.py"
PY = ROOT / ".venv" / "Scripts" / "python.exe"
ASSET = "PDFUtility.zip"
APP_DIR = ROOT / "dist" / "PDFユーティリティ"     # pdftool.spec の COLLECT の出力


def run(*cmd: str) -> None:
    print(">", " ".join(cmd))
    subprocess.run(cmd, cwd=ROOT, check=True)


def make_zip(app_dir: Path, dest: Path) -> None:
    """アプリのフォルダを、フォルダ名ごと zip にする（展開するとそのまま使えるように）。"""
    if not (app_dir / "_internal").is_dir():
        raise SystemExit(f"ビルド結果が見つかりません: {app_dir}")
    dest.unlink(missing_ok=True)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in sorted(app_dir.rglob("*")):
            if f.is_file():
                z.write(f, Path(app_dir.name) / f.relative_to(app_dir))


def main() -> int:
    if len(sys.argv) < 2 or not re.fullmatch(r"\d+\.\d+\.\d+", sys.argv[1]):
        print("使い方: release.bat 0.2.0 [メモ]")
        return 1
    ver = sys.argv[1]
    notes = sys.argv[2] if len(sys.argv) > 2 else f"v{ver}"
    INIT.write_text(re.sub(r'__version__ = "[^"]*"', f'__version__ = "{ver}"', INIT.read_text(encoding="utf-8")),
                    encoding="utf-8")
    run(str(PY), "-m", "pytest", "-q")
    run(str(PY), "scripts/bundle_ghostscript.py")
    run(str(ROOT / ".venv" / "Scripts" / "pyinstaller.exe"), "--noconfirm", "pdftool.spec")
    asset = ROOT / "dist" / ASSET
    make_zip(APP_DIR, asset)
    print(f"添付する zip: {asset.name} ({asset.stat().st_size:,} バイト)")
    run("git", "add", "-A")
    run("git", "commit", "-m", f"v{ver}")
    run("git", "tag", f"v{ver}")
    run("git", "push", "origin", "HEAD", "--tags")
    run("gh", "release", "create", f"v{ver}", str(asset), "--title", f"v{ver}", "--notes", notes)
    print(f"公開しました: v{ver}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
