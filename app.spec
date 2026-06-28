# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = ['pynput.keyboard._win32', 'pynput.mouse._win32', 'customtkinter', 'pygame', 'pygame._sdl2', 'pygame.display', 'pygame.event', 'host_ui', 'video', 'audio', 'adaptive', 'rtc_common', 'host_rtc', 'client_rtc', 'service', 'cv2', 'settings_ui', 'settings_config', 'settings_display', 'settings_interface', 'settings_security', 'settings_connection', 'settings_audio', 'settings_autostart']
hiddenimports += collect_submodules('pygame')
hiddenimports += collect_submodules('mss')
hiddenimports += collect_submodules('customtkinter')
hiddenimports += collect_submodules('cv2')


a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[],
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
    name='app',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=True,
)
