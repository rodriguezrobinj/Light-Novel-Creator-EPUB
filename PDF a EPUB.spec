# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[('fonts', 'fonts'), ('dict', 'dict'), ('tessdata', 'tessdata'), ('icon.ico', '.')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # QtNetwork, QtQml, QtQuick y QtOpenGL ya no se quitan: la vista previa (QtWebEngine, Chromium) los usa
    excludes=['pymupdf.layout', 'pymupdf4llm', 'onnxruntime', 'numpy', 'tkinter', 'PySide6.QtTest', 'PySide6.QtPdf',
              'PySide6.QtDBus', 'PIL._avif', 'PIL.AvifImagePlugin'],
    noarchive=False,
    optimize=0,
)
# Lo que la ventana no usa. Chromium (la vista previa) sí usa red, OpenGL y SVG: se quedan
UNUSED = ('qdirect2d.dll', 'plugins/networkinformation/', 'qml/',
          # Módulos de Qt que trae PySide6-Addons y nadie carga: Quick 3D, controles QML, gráficos, mapas, multimedia
          'qt6quick3d', 'qt6quickcontrols2', 'qt6quickdialogs2', 'qt63d', 'qt6charts', 'qt6graphs',
          'qt6datavisualization', 'qt6location', 'qt6multimedia', 'qt6labs', 'qt6shadertools', 'qt6virtualkeyboard',
          'qt6spatialaudio', 'qt6texttospeech', 'qt6sensors', 'qt6bluetooth', 'qt6nfc', 'qt6serialport',
          'qt6remoteobjects', 'qt6scxml', 'qt6statemachine', 'qt6designer', 'qt6help', 'qt6sql', 'qt6pdf',
          # Chromium: sus herramientas de desarrollo y lo de depurar no se usan
          'devtools_resources', '.debug.')
a.binaries = [b for b in a.binaries if not any(name.lower() in b[0].lower().replace('\\', '/') for name in UNUSED)]
a.datas = [d for d in a.datas if not any(name in d[0].lower().replace('\\', '/') for name in UNUSED)]
# De los 53 idiomas de Chromium y los de Qt, los de la app: español e inglés
LOCALES = ('/es.pak', '/es-419.pak', '/en-us.pak', '_es.qm', '_en.qm')
a.datas = [d for d in a.datas if not (d[0].replace('\\', '/').startswith('PySide6/translations/')
                                      and not d[0].lower().replace('\\', '/').endswith(LOCALES))]
# pyphen trae patrones de unos 70 idiomas (6 MB): la partición de palabras solo usa el español
a.datas = [d for d in a.datas if not d[0].replace('\\', '/').startswith('pyphen/dictionaries/')
           or d[0].endswith('hyph_es.dic')]
# Lo trae hook-pyphen de pyinstaller-hooks-contrib (fijado en requirements.txt): sin él no se parten palabras
assert any(d[0].endswith('hyph_es.dic') for d in a.datas), 'Falta hyph_es.dic de pyphen: el .exe no partiría palabras'
pyz = PYZ(a.pure)

# En carpeta y no en un solo .exe: con Chromium serían ~200 MB que el .exe de un archivo descomprimía en %TEMP% en
# cada arranque. dist/Light Novel Creator/PDF a EPUB.exe, con sus archivos al lado
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='PDF a EPUB',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,  # UPX rompe las DLL de Chromium
    name='Light Novel Creator',
)
