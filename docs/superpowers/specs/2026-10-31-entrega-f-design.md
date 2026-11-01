# Entrega F: móvil, Calibre, portada, fuente y revisión del texto — Diseño

Fecha: 2026-10-31

## Objetivo

Seis funciones pequeñas e independientes: llevar el EPUB al móvil sin cable, añadirlo a Calibre, crear
una portada cuando el PDF no trae una buena, usar una fuente propia y avisar de texto sin traducir y de
erratas típicas de las traducciones fan. Las entregas G (página de personajes) y H (EPUB como entrada)
van aparte.

## Decisiones

### 1. Enviar al móvil por Wi-Fi

- Botón «Enviar al móvil» en la ventana «EPUB listo» (solo si el archivo existe).
- `_serve(path)` levanta un `http.server.ThreadingHTTPServer` en la IP de la red local (la de la ruta
  por defecto, con un socket UDP que no envía nada) y un puerto libre. Cualquier ruta devuelve ese EPUB
  con `Content-Disposition: attachment` y su nombre. Corre en un hilo: solo lee un archivo, no usa
  PyMuPDF.
- Ventana con el QR (`segno`, Python puro, nueva dependencia fijada en `requirements.txt`) y la URL
  seleccionable. Texto: misma red Wi-Fi; la primera vez Windows pregunta en el Firewall y hay que
  permitir el acceso. Al cerrar la ventana el servidor se apaga.
- Sin red local (solo 127.x): mensaje «No hay red local: conecta el equipo a la Wi-Fi.».

### 2. Texto sin traducir (`quality_report`)

- Párrafo de 6 palabras o más donde al menos el 35 % son palabras vacías inglesas (`the`, `and`, `you`,
  `is`, `of`…) y menos que esas son españolas: aviso «Revisar», «Texto sin traducir», sin arreglo
  automático. La solución dice que se traduzca o se quite con «Editar texto…».

### 3. Añadir a Calibre

- `_calibre()` busca `calibre.exe` en el `PATH` y en `Program Files\Calibre2` (y `(x86)`).
- Botón «Añadir a Calibre» en «EPUB listo» solo si existe. Lanza `calibre.exe <epub>` sin esperar:
  Calibre lo añade a la biblioteca, también si ya estaba abierto.

### 4. Portada generada

- `convert.text_cover(book)`: JPEG de 1200×1800 dibujado con PyMuPDF. Fondo de un color elegido por la
  serie (o el título), título grande con Literata Bold, serie y «Volumen N» encima, autor abajo, texto
  claro sobre fondo oscuro. Los textos largos se reparten en líneas.
- En «Elegir portada», botón «Portada con título». La elección es `("text", "")` en `_cover_image` y se
  guarda en la revisión como las demás; se vuelve a dibujar al reabrir, con los datos de ese momento.

### 6. Erratas típicas (`quality_report`)

Avisos «Menor» con arreglo de un clic (`edit_section`):

- Pregunta o exclamación sin signo de apertura: «Qué haces?» → «¿Qué haces?». El signo va al principio
  de la oración (tras `.`, `…`, `!`, `?`, `»`, `—` o el inicio del párrafo). Si la oración ya lleva `¿`/`¡`
  no se toca.
- Palabra repetida seguida: «de de» → «de». Solo palabras de 2 letras o más y no las que se repiten a
  propósito con coma o en interjecciones («no, no», «ja ja»): solo se avisa si la palabra es una
  preposición, artículo o conjunción (`de`, `la`, `el`, `que`, `en`, `y`, `a`, `los`, `las`, `un`,
  `una`, `por`, `con`, `se`, `lo`).
- «haber si» → «a ver si».
- «¿porque» → «¿por qué».

Comillas, rayas, espacios y puntos suspensivos no entran: `_typography` ya los corrige.

### 8. Fuente propia

- El combo «Letra» gana «Otra…», que pide un `.ttf` o `.otf`. La ruta se guarda en `font` (revisión y
  ajustes) y el combo muestra el nombre del archivo.
- `write_epub(font=ruta)`: un solo `@font-face` con ese archivo en `fonts/`, media type según la
  extensión. La negrita y la cursiva las simula el lector.
- Si el archivo ya no existe al reabrir: vuelve a «Del lector» y la barra de estado lo dice.

## Pruebas

- `test_serve_gives_the_epub_over_http` (descarga con `urllib` y compara bytes, el servidor se cierra).
- `test_quality_report_finds_untranslated_text` (párrafo inglés sí, español con «OK» y nombres no).
- `test_quality_report_fixes_fansub_typos` (las cuatro reglas, arreglos aplicados con `edit_section`, y
  casos que no deben saltar: «no, no», «¿Qué?» correcto, «va a haber»).
- `test_text_cover` (JPEG 1200×1800, distinto con otra serie, título largo no falla).
- `test_custom_font_is_embedded` (`@font-face` y archivo en el EPUB, `check_epub` vacío).
- Ventana: botones nuevos de «EPUB listo» (Calibre solo si existe), «Portada con título» y «Otra…» en
  el combo de letra.
