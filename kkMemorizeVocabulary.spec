from pathlib import Path

root = Path(SPECPATH)
a = Analysis(
    [str(root / 'main.py')], pathex=[str(root)], binaries=[],
    datas=[(str(root / 'docs' / '项目设计说明.md'), 'docs'),
           (str(root / 'vocabulary' / 'migrations' / '*.sql'), 'vocabulary/migrations')],
    hiddenimports=['vocabulary.smoke'], hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtWebEngineQuick',
              'PySide6.QtPdf', 'PySide6.QtPdfWidgets', 'PySide6.QtQml', 'PySide6.QtQuick',
              'tkinter', 'pytest', 'pytestqt'],
    noarchive=False,
)
# Qt 6.12 uses Windows' built-in, unversioned ICU interfaces. Never ship a foreign
# ICU facade or old API-set shim discovered from another program on the build PATH.
a.binaries = [item for item in a.binaries
              if not Path(item[0]).name.lower().startswith(('api-ms-win-', 'ext-ms-', 'icudt'))
              and Path(item[0]).name.lower() not in {'icuuc.dll', 'icuin.dll'}]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='kkMemorizeVocabulary',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=False,
          icon=str(root / 'assets' / 'app.ico'))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='kkMemorizeVocabulary')
