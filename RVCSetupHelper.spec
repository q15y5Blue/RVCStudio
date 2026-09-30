# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['studio\\setup_helper.py'],
    pathex=['studio'],
    binaries=[],
    datas=[('studio/engine-manifest.json', '.'), ('vendor/vbcable/VBCABLE_Driver_Pack45.zip', '.'), ('vendor/models', 'models')],
    hiddenimports=[],
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
    name='RVCSetupHelper',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
