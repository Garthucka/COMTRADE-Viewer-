# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

matplotlib_datas = collect_data_files('matplotlib')
pyqt6_datas = collect_data_files('PyQt6')
hiddenimports = collect_submodules('matplotlib.backends') + [
    'matplotlib.backends.backend_qtagg',
    'PyQt6.QtCore',
    'PyQt6.QtGui',
    'PyQt6.QtWidgets',
    'comtrade',
]

a = Analysis(
    ['comtrade_viewer_v61_extended.py'],
    pathex=[],
    binaries=[],
    datas=matplotlib_datas + pyqt6_datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={'matplotlib': {'backends': ['QtAgg']}},
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
    name='comtrade_viewer_v61',
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
