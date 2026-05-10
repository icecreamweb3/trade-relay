# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for Windows packaging with Chinese UI support.
Build: pyinstaller trade_relay.spec
"""
import sys
from pathlib import Path

block_cipher = None

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[],
    datas=[
        ("configs", "configs"),
    ],
    hiddenimports=[
        "trade_relay",
        "trade_relay.i18n",
        "trade_relay.database",
        "trade_relay.config",
        "trade_relay.auth.manager",
        "trade_relay.trading.binance_client",
        "trade_relay.trading.order_manager",
        "trade_relay.ui.app",
        "trade_relay.ui.styles",
        "trade_relay.ui.login_screen",
        "trade_relay.ui.main_screen",
        "trade_relay.ui.admin_screen",
        "trade_relay.ui.config_screen",
        "trade_relay.ui.order_log_widget",
        "trade_relay.ui.order_form_widget",
        "PyQt6",
        "PyQt6.QtWidgets",
        "PyQt6.QtCore",
        "PyQt6.QtGui",
        "bcrypt",
        "yaml",
        "binance",
        "sqlite3",
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
    name="TradeRelay",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # PyQt6: windowed app, no console popup
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # Windows: enable UTF-8 / Chinese font support
    manifest=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="TradeRelay",
)
