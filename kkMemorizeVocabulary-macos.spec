from pathlib import Path

root = Path(SPECPATH)
a = Analysis(
    [str(root / 'main.py')], pathex=[str(root)], binaries=[],
    datas=[(str(root / 'docs' / '项目设计说明.md'), 'docs'),
           (str(root / 'vocabulary' / 'migrations' / '*.sql'), 'vocabulary/migrations')],
    hiddenimports=['vocabulary.smoke'], hookspath=[], hooksconfig=[], runtime_hooks=[],
    excludes=['PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtWebEngineQuick',
              'PySide6.QtPdf', 'PySide6.QtPdfWidgets', 'PySide6.QtQml', 'PySide6.QtQuick',
              'tkinter', 'pytest', 'pytestqt'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='kkMemorizeVocabulary',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='kkMemorizeVocabulary')
app = BUNDLE(coll, name='kkMemorizeVocabulary.app',
             bundle_identifier='com.whycanotkk.kkMemorizeVocabulary',
             info_plist={'CFBundleName': 'kkMemorizeVocabulary',
                         'CFBundleDisplayName': 'kkMemorizeVocabulary',
                         'NSHighResolutionCapable': True})
