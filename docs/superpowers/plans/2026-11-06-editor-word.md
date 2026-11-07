# Editor de texto estilo Word — plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** El editor «Texto · Capítulo…» se edita como Word (cursiva, negrita, títulos, ilustraciones y
separadores se ven como quedan) y la página del PDF se ve grande, con zoom, sin cambiar lo que se guarda.

**Architecture:** Módulo nuevo `editor.py` con la hoja (`Sheet`, un `QTextEdit`) y la traducción en los dos
sentidos entre el formato de `convert.section_text` y un `QTextDocument` (`load`, `dump`). `app.TextEditor`
monta la ventana (cinta, hoja, panel del PDF con `QSplitter`, barra de estado) y guarda con
`convert.edit_section` como hoy.

**Tech Stack:** Python 3.14, PySide6 (QTextEdit, QTextDocument, QSyntaxHighlighter), PyMuPDF (solo en app.py).

**Spec:** `docs/superpowers/specs/2026-11-06-editor-word-design.md`

## Global Constraints

- `convert.py` no cambia; el texto que llega a `edit_section` tiene el formato de `section_text`.
- Abrir y aceptar sin cambios no toca el libro: cada párrafo sin cambios se escribe con su línea original.
- Textos visibles en español con tildes; comentarios en español, cortos, el porqué; identificadores en inglés.
- Colores del tema (`theme.py`) en los dos temas; nada de colores fijos en `editor.py` salvo los que da el tema.
- PyMuPDF solo en `app.py`: `editor.py` recibe las imágenes ya como `QImage`.
- Cada cambio de la ventana con su prueba sin pantalla en `test_convert.py`; `/verificar-ventana` y `/build-exe`.

## Review Focus

1. Un párrafo partido con Enter y vuelto a unir con Retroceso debe guardar el texto original sin cambios de
   página (prueba en Tarea 1: `dump` usa la línea original si el párrafo volvió a quedar igual).
2. Pegar texto de una web con negrita y colores: solo texto, sin tamaños raros (Tarea 2).
3. Copiar y pegar una ilustración en la misma hoja: no se duplica; cortar y pegar la mueve (Tarea 2).
4. Escribir «<i>» a mano dentro de un párrafo: se guarda tal cual (edit_section lo leerá como cursiva, igual
   que hoy); no rompe el anidado de las marcas que `dump` genera (Tarea 1).
5. Hoja vacía (título sin texto debajo) y libro sin PDF: el editor abre, el panel dice que no hay PDF
   (Tarea 4).

---

### Task 1: `editor.py` — cargar y volcar el formato de section_text

**Files:**
- Create: `editor.py`
- Test: `test_convert.py` (junto a `test_text_editor_*`)

**Interfaces:**
- Produces: `editor.SOURCE`, `editor.HEADING`, `editor.OBJECT` (propiedades de formato);
  `editor.load(document, lines, pictures)` donde `lines = [(k, línea_de_section_text)]` y
  `pictures = {N: QImage}`; `editor.dump(document) -> str` (líneas separadas por `"\n\n"`);
  `editor.source_of(block) -> int | None`.

- [ ] **Step 1: Prueba de ida y vuelta (falla: no hay módulo)**

```python
def test_editor_sheet_round_trip():
    """La hoja WYSIWYG vuelve a dar el texto de section_text tal cual: aceptar sin cambios no toca el libro."""
    import qtk
    from PySide6.QtGui import QImage, QTextDocument

    import editor

    qtk.app()
    lines = [(2, "Uno <i>dos</i> tres"), (3, "<b>uno <i>dos</i></b>"), (4, "# Capítulo <i>1</i>"),
             (5, "[Ilustración 5]"), (6, "* * *"), (7, "[Separador 7]"), (8, "A & B < C [Nota: aclaración]"),
             (9, "<i>a</i><b>b</b>")]
    doc = QTextDocument()
    picture = QImage(40, 20, QImage.Format.Format_RGB32)
    editor.load(doc, lines, {5: picture, 7: picture})
    dumped = editor.dump(doc)
    sources = [editor.source_of(doc.findBlockByNumber(n)) for n in range(doc.blockCount())]
    assert dumped == "\n\n".join(line for _, line in lines), dumped
    assert sources == [2, 3, 4, 5, 6, 7, 8, 9], sources
```

- [ ] **Step 2:** `.venv/Scripts/python.exe -X utf8 -c "import test_convert as t; t.test_editor_sheet_round_trip()"` → `ModuleNotFoundError: editor`.

- [ ] **Step 3: `editor.py`** (código completo en el archivo; puntos clave):
  - `load`: por cada línea, un párrafo; `# ` → propiedad `HEADING`; `[Ilustración N]`/`[Separador N]` →
    `QTextImageFormat` con nombre `bloque:N`, propiedad `OBJECT = "Ilustración N"` y recurso `QImage` (o una
    imagen con el texto si no hay `QImage`); línea de solo símbolos → párrafo centrado; párrafo → trozos de
    texto con cursiva/negrita según `<i>`/`<b>`. Cada párrafo guarda `SOURCE = k` y `ORIGINAL = línea`.
  - `dump`: por párrafo, si su texto canónico es el mismo que al cargar → la línea original; si no, `# ` +
    marcas o marcas del texto; objetos → `[Ilustración N]` en su línea; vacíos fuera.
  - Marcas: pila de abiertas; al cambiar el formato se cierra lo justo y se abre lo que falta (negrita fuera,
    cursiva dentro).

- [ ] **Step 4:** la prueba pasa.

- [ ] **Step 5:** commit `feat: hoja del editor que carga y guarda el formato del texto`.

### Task 2: `editor.Sheet` — teclado, cinta y portapapeles

**Files:** Modify `editor.py`; Test `test_convert.py`.

**Interfaces:**
- Produces: `editor.Sheet(QTextEdit)` con `set_content(lines, pictures)`, `content() -> str`,
  `toggle_italic()`, `toggle_bold()`, `toggle_heading()`, `join_next()`, `insert_break()`,
  `current_source() -> int | None`, `set_zoom(percent)`, señal `formatChanged`.

- [ ] **Step 1: Prueba de acciones (falla)**

```python
def test_editor_sheet_actions_paste_and_undo():
    """Cursiva, negrita y título como en Word; Enter parte y el párrafo nuevo sigue apuntando a su bloque;
    unir deja un espacio; pegar trae solo texto; una ilustración no se duplica; cada acción, un Ctrl+Z."""
    import qtk
    from PySide6.QtCore import QMimeData
    from PySide6.QtGui import QImage, QTextCursor

    import editor

    qtk.app()
    sheet = editor.Sheet()
    sheet.set_content([(1, "Fin."), (2, "Dijo hola, y se fue."), (3, "[Ilustración 3]")],
                      {3: QImage(40, 20, QImage.Format.Format_RGB32)})

    def select(start, end):
        c = sheet.textCursor()
        c.setPosition(start)
        c.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        sheet.setTextCursor(c)

    seen = []
    select(0, 3)
    sheet.toggle_italic()
    seen.append(sheet.content().split("\n\n")[0])          # "<i>Fin</i>."
    sheet.toggle_italic()
    seen.append(sheet.content().split("\n\n")[0])          # otra vez: la quita
    select(0, 0)
    sheet.toggle_heading()
    seen.append(sheet.content().split("\n\n")[0])          # "# Fin."
    sheet.undo()
    sheet.join_next()
    seen.append(sheet.content().split("\n\n")[0])          # "Fin. Dijo hola, y se fue."
    sheet.undo()
    c = sheet.textCursor()
    c.setPosition(len("Fin.\nDijo"))
    sheet.setTextCursor(c)
    sheet.split_here()
    seen.append(sheet.content().split("\n\n")[1:3])        # partido en "Dijo" / "hola, y se fue."
    seen.append(sheet.current_source())                    # el trozo nuevo sigue siendo del bloque 2
    sheet.undo()
    mime = QMimeData()
    mime.setHtml("<p style='font-size:30pt'><b>Pegado</b> de fuera</p>")
    mime.setText("Pegado de fuera")
    sheet.moveCursor(QTextCursor.MoveOperation.End)
    sheet.insertFromMimeData(mime)
    seen.append(sheet.content().split("\n\n")[-1])         # solo texto: sin negrita
    sheet.undo()
    sheet.selectAll()
    copy = sheet.createMimeDataFromSelection()
    sheet.moveCursor(QTextCursor.MoveOperation.End)
    sheet.insertFromMimeData(copy)
    seen.append(sheet.content().count("[Ilustración 3]"))  # la ilustración no se duplica
    assert seen == ["<i>Fin</i>.", "Fin.", "# Fin.", "Fin. Dijo hola, y se fue.",
                    ["Dijo", "hola, y se fue."], 2, "Pegado de fuera", 1], seen
```

- [ ] **Step 2:** falla (`AttributeError: Sheet`).
- [ ] **Step 3:** `Sheet` en `editor.py`: atajos (Ctrl+K/I, Ctrl+N/B, Ctrl+T, Ctrl+J), Shift+Enter = Enter,
  `insertFromMimeData` (interno: líneas de `dump` del fragmento sin objetos repetidos; externo: texto plano,
  una línea por párrafo), `createMimeDataFromSelection` (texto plano + `application/x-lnc-hoja` con `dump`
  del fragmento), cada acción entre `beginEditBlock`/`endEditBlock`.
- [ ] **Step 4:** pasa.
- [ ] **Step 5:** commit `feat: acciones, atajos y pegar de la hoja del editor`.

### Task 3: aspecto — títulos, notas, ortografía y hoja

**Files:** Modify `editor.py`, `theme.py`; Test `test_convert.py`.

**Interfaces:**
- Produces: `editor.Look(QSyntaxHighlighter)` con `set_unknown(words)` y `set_colors(colors, tags)`;
  `Sheet.misspelled() -> list[str]`; regla QSS `QTextEdit#sheet` y `QFrame#desk` en `theme.QSS`.

- [ ] **Step 1: Prueba (falla)**

```python
def test_editor_sheet_looks_and_spelling():
    """Los títulos se ven grandes y en negrita sin guardar negrita; las palabras desconocidas salen subrayadas."""
    import qtk

    import editor

    qtk.app()
    sheet = editor.Sheet()
    sheet.set_content([(1, "# Título"), (2, "Ella dijó que sí.")], {})
    sheet.look.set_unknown({"dijó"})
    qtk.pump()
    heading = sheet.document().findBlockByNumber(0).layout().formats()
    bold = any(f.format.fontWeight() >= 600 for f in heading)
    assert bold and sheet.content() == "# Título\n\nElla dijó que sí.", (bold, sheet.content())
    assert sheet.misspelled() == ["dijó"], sheet.misspelled()
```

- [ ] **Step 2–4:** implementar `Look` (formatos de pantalla: título +2 de tamaño y negrita en color
  `heading`; `[Nota: …]` en `mark`; separadores escritos en `scene`; desconocidas con subrayado ondulado del
  color `broken`), QSS de la hoja (`sheet`, `sheet_line`) y del escritorio (`line`).
- [ ] **Step 5:** commit `feat: títulos, notas y ortografía a la vista en la hoja del editor`.

### Task 4: `app.TextEditor` con cinta, hoja y panel del PDF grande

**Files:** Modify `app.py` (clase `TextEditor`, constantes del editor viejo), `test_convert.py` (reescribir
`test_text_editor_*`, `test_editor_page_buttons_below_the_page`, la parte del editor de la prueba de
ortografía).

**Interfaces:**
- Consumes: `editor.Sheet`, `editor.dump`.
- Produces: `TextEditor(app, item, start, end)` con `win`, `sheet`, `canvas`, `page_label`, `status`,
  `shown`, `box`, `dirty()`, `save()`, `cancel()`, `draw_source()`, `turn(delta)`, `zoom(delta)`,
  `find_box(page, block)`.

- [ ] **Step 1: Pruebas (fallan)** — reescritas con la hoja:

```python
def test_text_editor_opens_section_and_saves_only_changes():
    ...  # la hoja muestra section_text (content() igual), aceptar sin cambios no toca páginas,
         # cancelar con cambios pregunta (como la prueba de ahora)

def test_text_editor_is_big_and_follows_the_cursor():
    """El panel del PDF ocupa al menos el 40 % del ancho del editor, recuadra el párrafo del cursor, muestra
    la ilustración bajo el cursor, ◀ ▶ hojean, el zoom agranda la página y sin PDF no falla."""
```

- [ ] **Step 2:** fallan.
- [ ] **Step 3:** reescribir `TextEditor`: ventana maximizada; cinta (`QToolButton` con texto y atajo en
  el tooltip, grupos con título debajo); `QSplitter` hoja | panel; panel = `QScrollArea` con `qtk.Preview`
  de tamaño fijo, ◀ página ▶, − zoom +; Ctrl+rueda; barra de estado con texto y zoom del texto; Aceptar y
  Cancelar. Quitar `EDITOR_PANEL`, `STYLE_TAGS`, `EDITOR_TAGS`, `_is_paragraph`, `_open_marks` y lo que
  quede sin uso.
- [ ] **Step 4:** suite completa en verde.
- [ ] **Step 5:** commit `feat: editor de texto estilo Word con la página del PDF grande`.

### Task 5: verificar, build y merge

- [ ] `/verificar-ventana` con un guion que abra el editor en los dos temas, a 1366×728 y con `--escala 1.25`,
  escriba, ponga cursiva, mueva una ilustración, acepte y compruebe el EPUB.
- [ ] `/build-exe`.
- [ ] `/commit`: merge a `master`.
