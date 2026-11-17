# EPUB completo — plan de implementación

> Ejecución: nativa (yo mismo, en esta sesión), revisión de rama al final.

**Goal:** que `write_epub` genere todo lo del EPUB de referencia de Sigil (ver spec).
**Architecture:** todo lo que se escribe en el EPUB vive en `convert.py` (`write_epub` y ayudantes `_people`,
`_rich`, `_section_kind`, `_title_page`); la ventana solo añade campos (`BOOK_FIELDS`, diálogo «Más datos…») y
opciones (`options.OPTIONS`, `app._write_options`).
**Tech Stack:** Python 3.14, PyMuPDF, PySide6.
**Spec:** `docs/superpowers/specs/2026-11-17-epub-completo-design.md`

## Global Constraints

- Textos visibles en español con tildes; comentarios en español; identificadores en inglés.
- EPUB 2 (`epub2=True`) no lleva `epub:type`, `role`, `aria-*`, `<section>` ni `<ruby>`: sigue pasando `check_epub`.
- Fuentes solo OFL en `fonts/` (Eagle Lake, Liberation Serif), con su licencia.
- Un libro sin los campos nuevos genera lo mismo que antes salvo semántica, metadatos invisibles, sección
  «Ilustraciones» y página de sinopsis (de fábrica activada).
- Tests: `.venv/Scripts/python.exe test_convert.py`; nunca a la vez que el build.

## Review Focus

1. Nombres con «&» o « y » dentro de un nombre de grupo («Tom & Jerry Scans» en `publisher`): se parte; aceptado,
   el grupo suele ser varios. Personas con coma en el nombre («Tachibana, Koushi»): se parte; raro, aceptado.
2. ISBN con guiones o espacios: se reconoce por las cifras (10 o 13, la última puede ser X).
3. Enlaces mal escritos (sin `=`, sin `http`): la línea sale como texto sin enlace, escapada.
4. Logo o fuente de títulos borrados del disco: el EPUB sale sin ellos, sin error.
5. Sinopsis con `<` o `&`: escapada; `*` sueltos sin pareja se quedan como texto.

Cada uno lleva su assert en el test de la tarea que lo toca.

---

### Task 1: metadatos (convert.py)
- Book: campos `subtitle franchise book_type rating romaji_title author_native illustrator_native epub_by distributor
  relay isbn asin source_url links revised` (str, "").
- `_people(text) -> list[str]`: split `\s*(?:&|,|/|;|\by\b)\s*`, sin vacíos.
- `_rich(text) -> list[str]`: párrafos HTML con `**`→`<b>`, `*`→`<i>`, escapado antes.
- OPF: personas por nombre (ids `aut1`, `trl1`…), `alternate-script`, publishers, identificadores, colecciones,
  `dc:type`, `calibre:rating`, descripción con `<br/>`.
- Test `test_epub_metadata_complete`: libro sintético, leer `content.opf` del zip, asserts de cada punto + Review
  Focus 1, 2, 5.

### Task 2: semántica, landmarks, índice, Ilustraciones, sinopsis, archivo de Apple, CSS Kindle
- `_section_kind(title, is_chapter, first) -> (body_type, epub_type, role)`.
- `_xhtml(title, body, lang, epub2, kind=None)`: envuelve en `<section>` y pone `epub:type` en `<body>`.
- Sección inicial solo de imágenes → «Ilustraciones».
- Página `synopsis.xhtml` (param `synopsis=True`).
- `META-INF/com.apple.ibooks.display-options.xml`; `@media amzn-kf8/amzn-mobi` en BASE_CSS.
- Test `test_epub_semantics`: prólogo/capítulo/«Palabras del autor», landmarks, nav con Cubierta/Sinopsis/
  Ilustraciones, archivo de Apple; EPUB 2 sin `epub:type`; `check_epub` sin errores.

### Task 3: página de título, logo, notas al final, llamada de nota
- Params `title_image=None` (índice de bloque), `logo=""` (ruta), `endnotes=False`, `note_style="1"`.
- `credits` escribe la página de título nueva (`titlepage.xhtml`); logo `logo.xhtml` (colophon).
- Test `test_epub_title_page_logo_endnotes`: furigana, enlaces (Review Focus 3), logo inexistente (Focus 4),
  `notas.xhtml` con backlinks, llamada «❮01❯».

### Task 4: fuentes, separador propio, recuadro de juego
- `FONTS["Liberation Serif"]`; params `heading_font` («Eagle Lake» o ruta), `box_style`.
- `_separator`: texto que no es de `SEPARATOR_CHOICES` se usa tal cual; nueva opción «※ ・ ※ ・ ※».
- Test `test_epub_fonts_separator_box`: @font-face de títulos, archivo en el zip, fuente borrada (Focus 4),
  separador propio, clase de recuadro.

### Task 5: ventana
- `BOOK_FIELDS` + Vars + `fields()`; diálogo «Más datos…» (botón en «Editar datos»), con selector de imagen de
  título; opciones nuevas en `options.OPTIONS` (synopsis, endnotes, note_style, heading_font, box_style, logo de
  tipo `file`); `_write_options`; `INHERITED` (franchise, book_type, epub_by, distributor, relay, links, logo,
  heading_font, box_style, note_style, endnotes, synopsis); combo de separador editable; AniList nativo.
- Test de ventana `test_more_data_dialog` (offscreen): rellenar, cerrar, `review_state` los lleva y `write_options`.

### Task 6: verificación y entrega
- Suite completa, `/regresion-series`, EPUBCheck en un libro real, `/verificar-ventana` en pantalla, `/build-exe`,
  `/commit` (preguntar antes del push).
