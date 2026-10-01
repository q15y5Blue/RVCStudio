# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['studio\\app.py'],
    pathex=['studio'],
    binaries=[],
    datas=[('studio/worker.py', '.'), ('studio/settings.py', '.'), ('studio/audio_buffers.py', '.'), ('studio/routing.py', '.'), ('studio/formant.py', '.'), ('studio/engine-manifest.json', '.'), ('studio/USER_GUIDE.txt', '.'), ('studio/licenses', 'licenses')],
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
    name='RVCStudio',
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
