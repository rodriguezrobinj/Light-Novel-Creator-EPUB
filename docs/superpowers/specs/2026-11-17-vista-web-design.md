# Vista previa con Chromium (fase 1 de la opción C) — diseño

Fecha: 2026-11-17. Aprobado por el usuario en el chat.

## Objetivo

Que el EPUB se vea como en un navegador o un lector moderno, con todo su HTML y CSS (selectores compuestos como
`p + p`, fuentes incrustadas, ruby real, imágenes en el texto). Fase 1: la vista previa; la hoja de edición sobre
Chromium es la fase siguiente (opción A), herramienta por herramienta.

## Diseño

- **`webview.py`**: `EpubView` (QWebEngineView) con un perfil propio sin disco que sirve los archivos desde los bytes
  del EPUB (`epub://`). Pagina como un lector: CSS de columnas del ancho de la vista sobre el `body`; `go(n)`,
  `find_page(texto)`; modo oscuro con letra clara sin tocar las ilustraciones. El tamaño del dispositivo
  (`DEVICES`, puntos con letra de 11 en MuPDF) pasa a px de CSS ×16/11 y se encoge a la vista con el zoom.
- **Arranque**: `qtk.app()` llama a `webview.prepare()` (esquema y OpenGL compartido) antes de la QApplication. Los
  procesos de trabajo no cargan Chromium (`import webview` solo dentro de la ventana).
- **Ventana principal**: la sección elegida se dibuja en Chromium (`draw_web`); la portada y los avisos siguen en la
  hoja de MuPDF (`web_off`). Misma sección y dispositivo: ◀ ▶ pasan de página sin recargar. La búsqueda va a la
  página del resultado.
- **Editor**: el panel derecho tiene pestañas «Original» y «EPUB»; «EPUB» escribe el capítulo tal como está en la
  hoja (`edit_section` sobre una copia del libro) a un EPUB de vista previa y lo dibuja, 600 ms después de dejar de
  escribir, en la página del párrafo del cursor.
- **Sin Chromium** (otro equipo, `LNC_SIN_CHROMIUM=1` en las pruebas): MuPDF como antes.
- **Empaquetado**: `pyside6-addons` y `pyside6-webengine` 6.12; el .exe pasa a carpeta (`dist/Light Novel Creator/`),
  sin UPX, sin QML, Quick 3D, gráficos, multimedia, herramientas de desarrollo de Chromium ni idiomas que no sean
  español e inglés (de 625 a 397 MB). `PDF a EPUB.exe --comprobar-chromium informe.txt` carga una página y escribe
  `ok N`: el build lo comprueba.

## Pruebas

Sin pantalla Chromium carga pero no da píxeles: se mira el DOM con JavaScript (fuente del título, `p + p`,
columnas, página tras `go`, `find_page`, color en modo oscuro); ventana con páginas, ▶ sin recargar y portada en
MuPDF; pestaña «EPUB» del editor con lo escrito. En pantalla: capturas reales. Build: autocomprobación y arranque.
