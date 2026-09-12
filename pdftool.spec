# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller のビルド定義。build.bat から使う（.venv に pyinstaller が要る）。
# 手動で作り直す場合:
#   .venv\Scripts\pyinstaller --noconfirm --onefile --windowed --name "PDFユーティリティ" ^
#     --paths src --collect-all pypdfium2_raw --collect-all pypdfium2 src\pdftool\__main__.py
# pypdfium2 / pypdfium2_raw は pyinstaller-hooks-contrib に同梱フックがあるが、
# collect_all を明示しておくことでバージョン差異があっても安定して拾えるようにしている。
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []
tmp_ret = collect_all('pypdfium2_raw')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('pypdfium2')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


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
