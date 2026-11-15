# Light Novel Creator EPUB

Programa de Windows que convierte los PDF de novelas ligeras (traducciones al español) en libros EPUB que se adaptan a cualquier pantalla: celular, e-reader o computadora.

## Qué hace

- Detecta capítulos, ilustraciones, separadores de escena, cursivas y negritas del PDF.
- Quita encabezados, pies de página y números de página.
- Lee con OCR los títulos de capítulo que el PDF trae como imagen.
- Abre un editor tipo Word para corregir el texto de cada capítulo, con revisión de ortografía.
- Da un «Informe de calidad» con erratas, restos de páginas y otros problemas, y los corrige con un clic.
- Recuerda los datos de cada serie (autor, traductor, glosario, personajes) para los tomos siguientes.
- Genera EPUB 3, EPUB 2 o KEPUB (Kobo), y lo envía al celular con un código QR.

## Descargar y usar

1. Ve a [Releases](../../releases) y descarga `LightNovelCreator-portable.zip`.
2. Descomprime el zip en cualquier carpeta, también en un USB.
3. Abre `PDF a EPUB.exe` y arrastra un PDF a la ventana.

Funciona en Windows 10 y 11 de 64 bits, sin instalar nada. El primer arranque tarda unos segundos.

Windows puede mostrar «Windows protegió su PC» porque el programa no está firmado. Pulsa «Más información» y luego «Ejecutar de todas formas».

### Versión portable

La carpeta `datos`, junto al .exe, guarda las preferencias, la memoria de las series y las cachés. Si la borras, el programa usa `%APPDATA%\PDF a EPUB`, como la versión instalada. Para instalarlo en el equipo, con acceso directo en el menú Inicio, usa «Herramientas › Instalar en este equipo».

El programa guarda junto a cada PDF su revisión (`<pdf>.lnepub.json`) y el EPUB generado.

## Compilar desde el código

Requiere Python 3.14 en Windows.

```
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe app.py
```

- Pruebas: `.venv\Scripts\python.exe test_convert.py`
- Ejecutable: `.venv\Scripts\python.exe -m PyInstaller --noconfirm "PDF a EPUB.spec"` (queda en `dist\`)
- Zip portable: copia `dist\PDF a EPUB.exe`, `portable\LEEME.txt` y `LICENSE` en una carpeta, crea dentro la carpeta vacía `datos` y comprime la carpeta.

## Licencia

[GNU AGPL v3](LICENSE). Incluye:

- [PyMuPDF](https://github.com/pymupdf/PyMuPDF) (AGPL v3) y [PySide6](https://doc.qt.io/qtforpython-6/) (LGPL v3).
- Fuentes Literata y Bitter ([SIL OFL 1.1](fonts/)).
- Diccionario es_ES de [RLA-ES](https://github.com/sbosio/rla-es) (GPL v3 / LGPL v3 / MPL 1.1, ver [dict/LICENSE.md](dict/LICENSE.md)).
- Datos `spa` de [Tesseract tessdata_fast](https://github.com/tesseract-ocr/tessdata_fast) (Apache 2.0).
