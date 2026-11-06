# Entrega H: EPUB como entrada — Diseño

Fecha: 2026-11-06

## Objetivo

Abrir un EPUB (de otro grupo, de Calibre o uno ya hecho con la app) y pasarlo por la misma revisión que un
PDF: capítulos, limpieza, estilo, perfiles de lector.

## Prueba previa

PyMuPDF abre un EPUB y lo compone en páginas, así que `analyze()` lo lee sin cambios: Nageki 1 (12
capítulos) y Kage 7 (11) en 3-4 s, sin párrafos cortados entre páginas, con autor y serie de los metadatos
y el índice quitado. No hace falta un lector XHTML propio. Límite: los EPUB de muestra los hizo esta app;
falta probar EPUB de otros programas.

## Decisiones

- Abrir, soltar sobre la ventana, «Convertir carpeta…», la carpeta vigilada, el panel de serie, el
  recopilatorio y «Abrir con» del Explorador aceptan `.epub` además de `.pdf`.
- La salida de un EPUB no puede llamarse igual que la entrada: se llama `<nombre> (LNC).epub`
  (`_out`). Un PDF sigue dando `<nombre>.epub`.
- Un `.epub` de la carpeta no es una entrada si es la salida de otro archivo: acaba en `(LNC).epub` o
  `.kepub.epub`, o hay un `.pdf` con el mismo nombre (`_sources`). Así la carpeta vigilada y el lote no
  vuelven a convertir lo que la app escribió.
- Con un EPUB abierto, la pestaña y la vista previa de la izquierda se llaman «Original» en vez de «PDF».
- Los avisos de `analyze` dicen «el EPUB» o «el PDF» según el archivo.

## Arreglos en `analyze` que salieron al probar

- Número de capítulo pequeño y sin negrita sobre el nombre (`<span class="num">` de los EPUB de la app):
  `_join_labels` lo une al título siguiente aunque no sea título, si es una etiqueta como «Capítulo 1».
  Sin esto, «Capítulo 1: Inicio» volvía como «Inicio» y un párrafo «Capítulo 1».
- Índice de varias páginas: al componer el EPUB, solo su primera página dice «Contenido». Una página justo
  tras el índice cuyas líneas son casi todas enlaces internos (`_links_only`) también se omite.
- U+FFFC, el hueco de una imagen en línea en el texto de PyMuPDF, se quita como los caracteres no válidos
  en XML: dejaba párrafos vacíos (Mitsuha 6).

## Pruebas

- `test_analyze_reads_epub`: PDF sintético → `write_epub` → `analyze` del EPUB: mismos capítulos, cursiva
  e ilustración.
- `test_epub_sources_and_output_name`: `_sources` descarta las salidas; `_out` añade «(LNC)» solo a los EPUB.
