# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[('fonts', 'fonts'), ('dict', 'dict'), ('icon.ico', '.')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['pymupdf.layout', 'pymupdf4llm', 'onnxruntime', 'numpy', 'tkinter', 'PySide6.QtNetwork', 'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtTest', 'PySide6.QtPdf', 'PySide6.QtOpenGL', 'PySide6.QtDBus', 'PySide6.QtSvg',
              'PIL._avif', 'PIL.AvifImagePlugin'],
    noarchive=False,
    optimize=0,
)
# La ventana es de widgets: no usa OpenGL por software, ni red, ni SVG. El .exe de un archivo descomprime
# todo en cada arranque, así que cada DLL que sobra también retrasa el inicio.
UNUSED = ('opengl32sw.dll', 'Qt6Network.dll', 'Qt6Svg.dll', 'qdirect2d.dll', 'qsvg.dll', 'qsvgicon.dll',
          'plugins/tls/', 'plugins/networkinformation/')
a.binaries = [b for b in a.binaries if not any(name.lower() in b[0].lower().replace('\\', '/') for name in UNUSED)]
# pyphen trae patrones de unos 70 idiomas (6 MB): la partición de palabras solo usa el español
a.datas = [d for d in a.datas if not d[0].replace('\\', '/').startswith('pyphen/dictionaries/')
           or d[0].endswith('hyph_es.dic')]
# Lo trae hook-pyphen de pyinstaller-hooks-contrib (fijado en requirements.txt): sin él no se parten palabras
assert any(d[0].endswith('hyph_es.dic') for d in a.datas), 'Falta hyph_es.dic de pyphen: el .exe no partiría palabras'
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='PDF a EPUB',
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
    icon=['icon.ico'],
)
