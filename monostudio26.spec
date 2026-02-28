# PyInstaller spec for MonoStudio 26 (onedir: one folder with exe + deps + data)
# Build: python build_icon.py  then  pyinstaller monostudio26.spec
# If EXE still shows default icon: rebuild with --clean; or rename/move exe to refresh Windows icon cache.

import os

block_cipher = None

# SPECPATH = directory containing this .spec file (PyInstaller sets it)
repo = os.path.abspath(SPECPATH)
icon_path = os.path.join(repo, 'monostudio_data', 'icons', 'app.ico')
if not os.path.isfile(icon_path):
    raise SystemExit(
        'app.ico not found. Run from repo root: python build_icon.py\n'
        'Expected: %s' % icon_path
    )

# Data: same layout as repo so get_app_base_path() / "monostudio_data" | "fonts" works.
# monos_blender is required for Blender DCC: launched Blender runs --python-expr that imports monos_blender.adapter.
datas = [
    (os.path.join(repo, 'monostudio_data'), 'monostudio_data'),
    (os.path.join(repo, 'fonts'), 'fonts'),
    (os.path.join(repo, 'monos_blender'), 'monos_blender'),
]

a = Analysis(
    [os.path.join(repo, 'app.py')],
    pathex=[repo],
    binaries=[],
    datas=datas,
    hiddenimports=[
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
        'PySide6.QtSvg',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MonoStudio26',
    debug=False,
    icon=icon_path,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='MonoStudio26',
)
