# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller のビルド定義。build.bat から使う（.venv に pyinstaller が要る）。
# build.bat は先に scripts/bundle_ghostscript.py を実行して vendor/gs/ を用意する
# （実機にインストール済みの Ghostscript から必要な分だけコピーする）。
# 手動で作り直す場合は先に `python scripts/bundle_ghostscript.py` を実行してから:
#   .venv\Scripts\pyinstaller --noconfirm pdftool.spec
# pypdfium2 / pypdfium2_raw は pyinstaller-hooks-contrib に同梱フックがあるが、
# collect_all を明示しておくことでバージョン差異があっても安定して拾えるようにしている。
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []
tmp_ret = collect_all('pypdfium2_raw')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('pypdfium2')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
for _pkg in ('pikepdf', 'lxml'):   # 保存時の可逆な詰め直し（core/pdfpack.py）
    tmp_ret = collect_all(_pkg)
    datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# 同梱 Ghostscript（vendor/gs。scripts/bundle_ghostscript.py が用意する）。
# 無ければ Ghostscript 抜きでビルドされ、実行時は外部インストールを検出するモードになる
# （gs_locator.find_ghostscript が sys._MEIPASS/gs/bin/... → PATH → Program Files の順で探す）。
_vendor_gs = Path('vendor/gs')
if _vendor_gs.is_dir():
    for _f in _vendor_gs.rglob('*'):
        if _f.is_file():
            # vendor/gs/bin/x.exe -> 展開先フォルダ 'gs/bin' に元のファイル名のまま置く
            datas.append((str(_f), str(_f.relative_to(_vendor_gs.parent).parent)))


a = Analysis(
    ['src/pdftool/__main__.py'],
    pathex=['src'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='PDFユーティリティ',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
