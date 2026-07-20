# -*- mode: python ; coding: utf-8 -*-
# Build: python -m PyInstaller MavioRobot.spec --distpath prod --workpath build --noconfirm

a = Analysis(
    ['launcher.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('app/web/templates', 'app/web/templates'),
        ('app/web/static', 'app/web/static'),
    ],
    hiddenimports=[
        'app.web.server',
        'app.adspower.client',
        'app.adspower.driver',
        'app.adspower.facebook_creds',
        'app.automations.buildfy',
        'app.automations.buildfy_email',
        'app.automations.cnpj_list',
        'app.automations.create_business_manager',
        'app.automations.driver_utils',
        'app.automations.facebook_business_info',
        'app.automations.facebook_business_verification',
        'app.automations.facebook_domain',
        'app.automations.facebook_language',
        'app.automations.facebook_login',
        'app.automations.facebook_pages',
        'app.automations.facebook_scope',
        'app.automations.facebook_whatsapp',
        'app.automations.orchestrator',
        'app.automations.pause',
        'app.automations.run_log',
        'app.automations.totp',
        'app.automations.tracker',
    ],
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
    [],
    exclude_binaries=True,
    name='MavioRobot',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='MavioRobot',
)
