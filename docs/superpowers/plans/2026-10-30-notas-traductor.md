# Notas del traductor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Notas al pie del PDF como notas emergentes del EPUB, con la llamada en su sitio.

**Architecture:** `_rows` une la llamada a su línea y `_row` la deja como `<sup class="ref">N</sup>`;
`analyze` separa las filas de nota de cada página y resuelve las marcas a `<span class="nota">…</span>`
dentro del párrafo; `write_epub` las convierte en `noteref` + `aside`; `_line`/`_inline` usan `[Nota: …]`.

**Tech Stack:** Python 3.14, PyMuPDF.

**Spec:** `docs/superpowers/specs/2026-10-30-notas-traductor-design.md`

## Global Constraints

- Texto visible y comentarios en español; identificadores en inglés.
- Cada cambio de `convert.py` lleva prueba con PDF sintético en `test_convert.py`.
- Las 129 pruebas existentes siguen pasando (sin notas en el PDF, nada cambia).

## Review Focus

- Número pequeño sin nota en la página («m²»): vuelve a ser texto en su sitio (Task 1).
- Nota de varias líneas: las líneas siguientes se unen a la nota, no al cuerpo (Task 1).
- Nota con cursiva dentro y llamada antes de un punto: sin espacio de más («piedra.», Task 1).
- Editar la sección conserva la nota y su posición (Task 2).
- EPUB 2 sin `epub:type` ni `aside`; KEPUB sigue pasando `check_epub` (Task 2).

---

### Task 1: Análisis (`_rows`, `_row`, `analyze`)

**Files:** Modify `convert.py` (`_row` ~186, `_rows` ~212, `analyze` ~1061); Test `test_convert.py`.

**Produces:** `REF_MARK = re.compile(r'<sup class="ref">([^<]+)</sup>')`, `NOTE = re.compile(r'<span class="nota">(.*?)</span>')`.

- [ ] Prueba `test_footnotes_leave_body_and_keep_reference_place`: página con «…grabada en piedra» +
  «2» de 7 pt elevado 3 pt + «. Siguiente frase.», otro «m» + «2» pequeño sin nota, y al pie
  «2» 7 pt + «Su respuesta permanecía firme y» 9 pt, y debajo «<i>no cambió</i>.» 9 pt. Espera
  `piedra<span class="nota">Su respuesta permanecía firme y no cambió.</span>.` (cursiva conservada),
  «m2» como texto, y ningún bloque con «permanecía» fuera de la nota.
- [ ] Implementar y pasar la suite. Commit `fix: footnote references stay in place and footnotes leave the chapter text`.

### Task 2: Escritura y editor

**Files:** Modify `convert.py` (`_line`, `_inline`, `write_epub`, `BASE_CSS`); Test `test_convert.py`.

- [ ] Prueba `test_footnotes_become_popup_notes`: libro con dos párrafos con nota en dos secciones;
  EPUB 3 con `epub:type="noteref"` `href="#nota1"`/`#nota2"`, `aside` con `id="nota2"` en `sec002`,
  `check_epub` vacío; EPUB 2 con `div class="nota"` y sin `epub:type="footnote"`; KEPUB válido;
  `section_text` con `[Nota: …]` y `edit_section` con el mismo texto conserva el `<span class="nota">`.
- [ ] Implementar y pasar la suite. Commit `feat: translator footnotes as pop-up notes in the EPUB`.

### Task 3: Real y build

- [ ] Re Zero 04: 18 notas, ningún párrafo que empiece por «1 »/«2 », `check_epub` vacío.
- [ ] `/build-exe`.
