# Editor completo — plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Diez mejoras de «Editar texto…»: formato nuevo, pegar con formato, borrador, seguir donde lo dejé,
panel de navegación, ver cambios, modo concentración, cambiar ilustración, sinónimos y medir la sesión.

**Architecture:** El formato nuevo entra por el texto de línea de `convert.section_text`/`edit_section`
(`<sup>`, `<sc>`, `<derecha>`), y `editor.Sheet` lo traduce a formatos Qt como ya hace con `<i>`/`<b>`.
Lo demás vive en `app.TextEditor` (barras, panel, borrador) y en la revisión `<pdf>.lnepub.json`.

**Tech Stack:** Python 3.14, PySide6, PyMuPDF; tesauro OpenThesaurus-es (`dict/th_es_v2.dat`).

**Spec:** `docs/superpowers/specs/2026-11-11-editor-completo-design.md`

## Global Constraints

- Todo texto visible en español con tildes; comentarios en español, cortos, con el porqué; identificadores en inglés.
- Cada cambio de `convert.py` lleva su test con PDF o libro sintético en `test_convert.py`.
- Pruebas de ventana offscreen con `editor_on`/`visible_window`; nada se escribe junto a los PDF del usuario.
- Mantener la API Tk de `qtk` en `app.py` (`messagebox`, `after`, `_button`, `_row`, `_label`).
- Tras terminar: `/verificar-ventana` sin `--oculto` con clics reales, `/regresion-series`, `/build-exe`.
- Nunca correr la suite y el build a la vez.

## Review Focus

- Borrador de un libro que se abrió desde otro PDF con el mismo nombre de sección: se recupera solo si las líneas cargadas coinciden (test en Task 4).
- Pegar HTML de Word con estilos raros (negrita como `font-weight:700`, `<span style>`): la negrita se reconoce por `fontWeight` (test en Task 3).
- `<sc>` dentro de una nota o un enlace: `_inline` lo deja equilibrado y `section_text` lo devuelve igual (test en Task 1).
- Sinónimo para palabra en mayúsculas («CASA») y al principio de frase («Casa»): respeta la forma (test en Task 10).
- Esc en modo concentración no pregunta «Descartar cambios» (test en Task 8).

---

### Task 1: Superíndice, versalitas y derecha en convert.py

**Files:** Modify `convert.py` (INLINE, `_balanced`, `_inline`, `_line`, `edit_section`, write_epub CSS y clase,
SAFE, LEAD), `app.py:1452` (MARKUP). Test: `test_convert.py`.

**Produces:** línea de editor `<sup>x</sup>`, `<sc>x</sc>`, prefijo `convert.RIGHT = "<derecha>"`; bloque
`align="right"`; HTML de bloque `<sup>`, `<span class="sc">`.

- [ ] Test `test_superscript_small_caps_and_right_reach_the_epub`: `edit_section` con
  `"1.<sup>er</sup> <sc>Rango S</sc>"`, `"<derecha>Firmado: Rudeus"`, `"[Nota: el <sc>gremio</sc>]"` dentro de un
  párrafo; bloques con `<sup>`, `<span class="sc">`, `align == "right"`; `section_text` devuelve las mismas líneas;
  EPUB con `class="right"`, `font-variant: small-caps`, sin problemas de `check_epub`; `_plain` sin etiquetas;
  `<sc>` sin cerrar se quita.
- [ ] Implementar: `INLINE = r"&lt;(/?)(i|b|sup|sc)&gt;"`, `_balanced` con `(i|b|sup|sc)`; al final de `_inline`
  `<sc>`→`<span class="sc">` y `</sc>`→`</span>`; `_line` hace el camino inverso con `SC_SPAN` y pone `RIGHT`
  si `align == "right"`; `edit_section` lee `RIGHT` como `CENTER`; CSS `.sc { font-variant: small-caps; }` y
  `p.right { text-indent: 0; text-align: right; }`; `css = "right" if b.align == "right" else "center" if b.align`.
- [ ] Correr el test y los de enlaces y centrado. Commit `feat: superíndice, versalitas y párrafos a la derecha en el EPUB`.

### Task 2: Formato nuevo en la hoja y la cinta

**Files:** `editor.py` (`MARKS`, `_runs`, `_insert_marked`, `_marked`, `_lines`, `_strip`, `insert_lines`,
`restore`, `toggle_center`, nuevos `toggle_sup`, `toggle_small_caps`, `toggle_right`, `is_right`,
`keyPressEvent`), `app.py` (`ribbon`, `update_tools`).

**Consumes:** `convert.RIGHT`. **Produces:** runs `(texto, cursiva, negrita, enlace, sup, sc)`; propiedad `RIGHT`.

- [ ] Test `test_editor_sheet_superscript_small_caps_right`: `Sheet.set_content` con las líneas de Task 1 →
  `content()` igual; seleccionar una palabra y `toggle_sup()`/`toggle_small_caps()` la marca; `toggle_right()`
  pone `<derecha>` y quita centrado/recuadro; Ctrl+D lo hace con `QTest.keyClick`.
- [ ] Implementar y botones «x²» (Ctrl+Mayús+=), «Vᴇʀs» en «Fuente», «⇥ Derecha» (Ctrl+D) en «Párrafo»
  con `level` para que `fit_ribbon` los compacte. Commit `feat: superíndice, versalitas y derecha en el editor`.

### Task 3: Pegar con formato

**Files:** `editor.py` (`insertFromMimeData`, `canInsertFromMimeData`).

- [ ] Test `test_editor_pastes_html_with_italics`: `QMimeData` con `setHtml('<p>Hola <i>mundo</i></p><p><span
  style="font-weight:700">Fuerte</span> [Ilustración 1]</p>')` → líneas `Hola <i>mundo</i>` y
  `<b>Fuerte</b> [Ilustración 1]` como texto (sin objeto nuevo).
- [ ] Implementar: `doc = QTextDocument(); doc.setHtml(source.html())`; líneas = `dump(doc)` partidas; cada
  línea que contenga `TOKEN` se inserta con el corchete escapado (`raw` para esa línea). Commit
  `feat: pegar desde Word o la web conserva cursiva y negrita`.

### Task 4: Borrador y recuperación

**Files:** `app.py` (`_draft_path`, `TextEditor.write_draft`, `recover_draft`, `save`, `cancel`, `__init__`),
`editor.py` (`Sheet.set_draft`).

- [ ] Test `test_editor_draft_recovers_after_crash`: editor con cambio, `write_draft()`; archivo existe; nuevo
  editor con `askyesno` → True recupera el texto y `dirty()`; con el libro cambiado (otro texto en esa sección)
  no recupera y avisa; `save()` borra el borrador; sin cambios `write_draft()` lo borra.
- [ ] Implementar (ver spec §1). Commit `feat: el editor guarda un borrador y lo recupera tras un cierre`.

### Task 5: Seguir donde lo dejé

**Files:** `app.py` (`App.editor_at`, `review_state`, `apply_state`, `TextEditor.on_destroy`, `resume_bar`).

- [ ] Test `test_editor_resume_bar_goes_where_you_left`: cerrar el editor en el capítulo 2 → `window.editor_at`;
  abrir en el capítulo 1 → barra visible con el título; «Ir allí» abre el capítulo 2 con el cursor en ese bloque;
  `review_state()["editor_at"]` lo guarda.
- [ ] Implementar. Commit `feat: el editor ofrece volver a donde lo dejaste`.

### Task 6: Panel de navegación (F9)

**Files:** `app.py` (`TextEditor.nav_panel`, `fill_nav`, `toggle_nav`, `on_command`, ribbon), `editor.py` (F9).

- [ ] Test `test_editor_navigation_panel`: capítulo con título, subtítulo, un aviso y un cambio; `toggle_nav()` →
  visible, grupos «Títulos (2)», «Avisos (1)», «Cambios (1)»; clic en una hoja → cursor en ese bloque.
- [ ] Implementar. Commit `feat: panel de navegación del capítulo en el editor`.

### Task 7: Ver los cambios

**Files:** `editor.py` (`Look.show_changes`, `highlightBlock`), `app.py` (`diff_bar`, menú «Cambios ▾»).

- [ ] Test `test_editor_shows_changes`: cambiar «gato» por «perro» en un párrafo; activar «Mostrar cambios» →
  formato de subrayado en «perro»; la barra muestra `<s>gato</s>` y `<u>perro</u>`; en un párrafo sin cambios la
  barra se oculta.
- [ ] Implementar. Commit `feat: mostrar los cambios frente al original en el editor`.

### Task 8: Modo concentración (F11)

**Files:** `app.py` (`toggle_focus`, `win.on_reject`), `editor.py` (F11).

- [ ] Test `test_editor_focus_mode`: F11 oculta la cinta y el panel del PDF; Esc vuelve sin preguntar.
- [ ] Implementar. Commit `feat: modo concentración en el editor`.

### Task 9: Cambiar ilustración desde el editor

**Files:** `app.py` (`mark_menu`/`context_menu`, `change_picture`), `editor.py` (`Sheet.refresh_pictures`).

- [ ] Test `test_editor_changes_illustration`: `replace_image` falso que cambia la imagen del bloque → la hoja
  usa la nueva (`document().resource` distinto).
- [ ] Implementar. Commit `feat: cambiar una ilustración desde el editor`.

### Task 10: Sinónimos

**Files:** `editor.py` (`synonyms`, `match_case`), `app.py` (menú contextual), `dict/th_es_v2.dat`,
`dict/README_th_es.txt`.

- [ ] Test `test_synonyms`: «casa» trae «hogar»; «casas» trae «hogar»; «bonita» trae algo de «bonito»;
  «xyzw» nada; `match_case("CASA", "hogar") == "HOGAR"`, `("Casa", "hogar") == "Hogar"`; menú contextual
  sobre «casa» tiene «Sinónimos» y elegir reemplaza.
- [ ] Implementar. Commit `feat: sinónimos en el menú del editor`.

### Task 11: Medir la sesión

**Files:** `app.py` (`App.work`, `review_state`, `apply_state`, `TextEditor.save/cancel/toggle_reviewed`,
`show_stats`, `_work_summary`).

- [ ] Test `test_editor_work_log`: abrir, cambiar un párrafo, Aceptar → `work[hoy]["paragraphs"] == 1`;
  revisado suma 1; `_work_summary` da hoy, 7 días y total.
- [ ] Implementar. Commit `feat: «Cifras» cuenta tu trabajo de hoy, de la semana y del libro`.

### Task 12: Cierre

- [ ] Suite completa. `/regresion-series`. `/verificar-ventana` sin `--oculto` con clics. `/build-exe`.
- [ ] Merge a `master` (`merge: editor completo`), memoria del roadmap.
