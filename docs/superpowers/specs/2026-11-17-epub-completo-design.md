# EPUB completo — diseño

Fecha: 2026-11-17. Aprobado por el usuario en el chat.

## Objetivo

Que la app pueda generar un EPUB con todo lo que lleva el EPUB de referencia hecho a mano en Sigil
(`Novelas EPUB/Date A Live - V14 [DALT & EHJR].epub`, ZeePubs): páginas preliminares, semántica EPUB 3,
metadatos completos, notas al final, fuentes de títulos, separador y recuadros propios.

Lo invisible (semántica, metadatos, archivo de Apple, CSS de Kindle) va siempre. Lo que se ve en el libro
es una opción. EPUB 2 queda como está salvo los metadatos que también admite.

## 1. Datos nuevos del libro

Campos de texto nuevos en `convert.Book` y en `app.BOOK_FIELDS` (se guardan en la revisión):

| campo | en la ventana | sale en |
|---|---|---|
| `subtitle` | Subtítulo | página de título (no como segundo `dc:title`: algunos lectores lo juntan al título) |
| `franchise` | Franquicia | colección por encima de la serie |
| `book_type` | Tipo (vacío = «Novela ligera») | `dc:type`, página de título «[Novela Ligera]» |
| `rating` | Valoración (0–5) | `calibre:rating` = 2 × valor |
| `romaji_title` | Título romanizado | `alternate-script` ja-Latn |
| `author_native` | Autor en japonés | `alternate-script` ja del autor, furigana |
| `illustrator_native` | Ilustrador en japonés | `alternate-script` ja, furigana |
| `epub_by` | Maquetación EPUB | rol `mrk` |
| `distributor` | Distribuye | rol `dst` |
| `relay` | Traducción intermedia | página de título («chn-ing: Inverse ★ Kurumi») |
| `isbn` | ISBN | `urn:isbn:` con `identifier-type` 15 (13) o 02 (10) |
| `asin` | ASIN | `urn:amazon:` |
| `source_url` | Enlace de la fuente | `dc:identifier` uri |
| `links` | Enlaces (una línea `Texto = https://…`) | página de título |
| `revised` | Última corrección | página de título |

Se editan en un diálogo nuevo «Más datos…» desde el panel «Editar datos». AniList pide además
`name { native }` del staff y rellena `romaji_title`, `author_native`, `illustrator_native` cuando están vacíos.

## 2. Metadatos

- Una persona por nombre: autor, traductor, ilustrador, corrector, edición, maquetación y distribuidor se separan
  por `&`, `,`, `/`, `;` y « y » (`_people`). El grupo (`publisher`) también: un `dc:publisher` por grupo.
- Título: `title-type main`, `alternate-script` ja (Título original) y ja-Latn (romanizado). Autor e ilustrador:
  `alternate-script` ja.
- Serie con `dcterms:identifier` uuid5 estable de su nombre; franquicia como `belongs-to-collection` que refina la
  serie, con su uuid.
- `dc:type` y `calibre:rating`.
- Sinopsis con formato mínimo: `**negrita**`, `*cursiva*`, un salto de línea = párrafo (`_rich`). En
  `dc:description` va como HTML escapado (`<b>`, `<i>`, `<br/>`), como la referencia.

## 3. Semántica (solo EPUB 3)

- Cada página: `<body epub:type="frontmatter|bodymatter|backmatter">` y su contenido en
  `<section epub:type=… role=… aria-labelledby=…>` (o `aria-label` si no hay título visible).
- Tipo de sección por su título (`_section_kind`): prólogo → `prologue`/`doc-prologue`; epílogo →
  `epilogue`/`doc-epilogue`; «Palabras del autor», «Epílogo del autor», «Nota del autor», afterword, postfacio →
  `afterword`/`doc-afterword` en backmatter; sección inicial sin capítulo → frontmatter; lo demás `chapter`/`doc-chapter`.
- Landmarks: portada, página de título, créditos, índice, inicio de la lectura, prólogo, epílogo del autor y notas,
  cuando existen.
- Índice de navegación (`nav.xhtml` y `toc.ncx`) con las páginas preliminares: Cubierta, Sinopsis, Ilustraciones,
  Página de título. La página «Contenido» visible sigue solo con la lectura.

## 4. Páginas nuevas

- **Sinopsis** (`synopsis`, casilla en Opciones → Texto, de fábrica activada): tras la portada, si hay sinopsis.
- **Ilustraciones**: la sección inicial sin capítulo que solo tiene imágenes se titula «Ilustraciones» (antes «Inicio»).
- **Página de título**: la opción `credits` pasa a llamarse «Página de título y créditos». Lleva imagen opcional
  (`title_image`, su `image_key`: el índice cambia al editar; elegida con las miniaturas de «Portada…» desde «Más datos…»), título grande,
  subtítulo, «Volumen N», «[Tipo]», créditos por rol (furigana `<ruby>` si hay nombre japonés), traducción
  intermedia, maquetación, enlaces, «Última corrección» y «EPUB creado el …».
- **Logo** (`logo`, archivo PNG/JPG en Opciones → Ilustraciones, también en «Libros nuevos»): página `colophon` tras
  la página de título. Si el archivo ya no existe, no sale.
- **Notas al final** (`endnotes`, casilla en Opciones → Texto, de fábrica apagada): todas las notas en
  `notas.xhtml` (`endnotes`, `ol.notes`, `doc-backlink`) en vez de al final de cada sección.
- **Llamada de nota** (`note_style`, Opciones → Texto): «1», «[1]» o «❮01❯».

## 5. Estilo

- `META-INF/com.apple.ibooks.display-options.xml` con `specified-fonts` true en todo EPUB.
- Reglas `@media amzn-kf8` y `amzn-mobi` en `BASE_CSS` (alto de línea de h1, sin `page-break-inside` en títulos).
- **Fuente de títulos** (`heading_font`, Opciones → Texto): «La del texto», «Eagle Lake» (caligráfica, OFL) o un
  archivo propio (`heading_font_file`). Fuente de texto nueva en `FONTS`: «Liberation Serif» (OFL, parecida a Times).
- Separador «※ ・ ※ ・ ※» en `SEPARATOR_CHOICES`; el desplegable de separador es editable: un texto que no es una
  de las opciones se usa tal cual.
- **Estilo de recuadros** (`box_style`, Opciones → Texto): «Sencillo» o «Pantalla de juego» (borde con brillo azul,
  texto blanco con sombra azul).

## Pruebas

`test_convert.py`: OPF (personas separadas, identificadores, alternate-script, colecciones, tipo, valoración,
sinopsis con formato), secciones con su `epub:type`, landmarks, páginas sinopsis/título/logo, notas al final,
fuente de títulos, archivo de Apple, separador propio, recuadro de juego, EPUB 2 sigue válido. Ventana: «Más datos…»
guarda y recupera los campos. Después `/regresion-series`, EPUBCheck, `/verificar-ventana` en pantalla real y
`/build-exe`.
