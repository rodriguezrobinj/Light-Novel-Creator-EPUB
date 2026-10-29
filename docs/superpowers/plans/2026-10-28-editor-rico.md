# Editor de texto rico — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convertir el cuadro «Editar texto…» en un editor con marcas coloreadas, botones de formato y la página del PDF sincronizada al lado.

**Architecture:** Todo en `app.py`. El editor actual (`App.edit_text`, un `Toplevel` con un `tk.Text` plano) pasa a una clase `TextEditor`. El formato del texto no cambia: lo sigue leyendo `convert.edit_section`. Resaltado con tags de `tk.Text`, acciones como pasos de deshacer, y panel lateral dibujado con PyMuPDF desde `App.pdf_doc`.

**Tech Stack:** Python 3.14, tkinter/ttk, PyMuPDF (`pymupdf`). Sin dependencias nuevas.

**Spec:** `docs/superpowers/specs/2026-10-28-editor-rico-design.md`

## Global Constraints

- `convert.py` no cambia. El editor produce texto en el formato de `convert.section_text` y lo aplica `App.apply_edit` → `convert.edit_section`.
- Todo texto visible (ventana, botones, avisos) en español con tildes correctas.
- Comentarios y docstrings en español, cortos, que digan el porqué. Identificadores en inglés.
- Única dependencia: `pymupdf`. Nada de Pillow ni de otras.
- Python del venv: `.venv/Scripts/python.exe`.
- Suite completa: `.venv/Scripts/python.exe test_convert.py` (sale con 1 si hay algún `FAIL`).
- No ejecutar a la vez la suite y el build o el arranque del .exe: la carpeta temporal de la suite desaparece y fallan tests al azar.
- Nunca escribir salidas de prueba junto a los PDF de `Novelas PDF/`.
- Commits en inglés, Conventional Commits, describiendo el comportamiento visible. Rama: `feat/editor-rico` (ya creada).
- Tras el último cambio, `/build-exe`: el usuario prueba con el .exe.

**Una sola prueba, sin la suite** (los tests usan `TMP` y `app.SETTINGS`, que prepara el `__main__` de la suite):

```bash
.venv/Scripts/python.exe -c "import sys, tempfile, pathlib; sys.path.insert(0, '.'); import test_convert as t, app; t.TMP = pathlib.Path(tempfile.mkdtemp()); app.SETTINGS = t.TMP / 'a.json'; t.NOMBRE_DEL_TEST(); print('ok')"
```

Si falla, sale un `AssertionError` con lo que se vio.

## Review Focus

1. **Sección vacía** (un título seguido de otro): el editor abre vacío y el panel muestra la página del título, sin errores. Lo cubre el test de la tarea 4.
2. **Libro sin el PDF** (`App.pdf_doc` es `None`, revisión de un PDF movido): el panel muestra un aviso y no falla. Lo cubre el test de la tarea 4.
3. **Cursiva o negrita sobre una selección de varias líneas**: no hace nada (pitido). Si no, la marca quedaría sin cerrar y `edit_section` la borraría. Lo cubre el test de la tarea 3.
4. **Cerrar la ventana principal con el editor abierto**: los `after` pendientes se cancelan y no saltan errores de Tcl. Lo cubren los tests de las tareas 2 y 4, que cierran la ventana con el editor abierto.
5. **Secciones ya editadas**: `edit_section` da a todos los párrafos nuevos la página del bloque anterior, así que al reabrir una sección editada el panel muestra casi siempre la página del título. Es un límite conocido de `convert.py`, fuera de alcance: solo hay que comprobar que no falla.

---

### Task 1: Clase `TextEditor` con el comportamiento actual, origen de cada línea y aceptar/cancelar con cuidado

**Files:**
- Modify: `app.py` (constante nueva junto a `ICON`, función `_is_paragraph` antes de `class App`, clase `TextEditor` después de `class App`, `App.edit_text` en la línea ~1233)
- Test: `test_convert.py` (test nuevo después de `test_window_edited_text_survives_reopening_and_reaches_epub`)

**Interfaces:**
- Produces:
  - `TextEditor(app: App, item: str, start: int, end: int)`. `item` es el id de la fila del título en la lista (el índice de su bloque, como texto). `start..end-1` son los bloques que se editan.
  - Atributos: `win` (`tk.Toplevel`), `text` (`tk.Text`), `body` (`ttk.Frame` que contiene el texto), `original` (texto al abrir).
  - Métodos: `dirty() -> bool`, `save()`, `cancel(_=None)`.
  - Cada línea que viene del bloque `k` lleva el tag `src{k}`.
  - `App.edit_text()` devuelve el `TextEditor`, o `None` si no hay sección que editar.
  - `_is_paragraph(line: str) -> bool` (módulo).

- [ ] **Step 1: Write the failing test**

Añadir en `test_convert.py`, después de `test_window_edited_text_survives_reopening_and_reaches_epub`:

```python
def test_text_editor_opens_section_and_saves_only_changes():
    """El editor muestra la sección en el formato de section_text; aceptar sin cambios no toca el libro
    (los párrafos conservan su página) y cancelar con cambios pregunta antes de descartarlos."""
    import app

    root, window = visible_window()
    window.pdf = make_novel()
    window.show_book(convert.analyze(window.pdf))
    before = [(b.kind, b.html, b.page) for b in window.book.blocks]
    window.tree.selection_set("1")  # "Capitulo 1: Inicio": su sección son los bloques 2..5
    editor = window.edit_text()
    shown = editor.text.get("1.0", "end-1c") == convert.section_text(window.book, 2, 6)
    sources = [t for t in editor.text.tag_names("1.0") if t.startswith("src")]
    editor.save()
    same = [(b.kind, b.html, b.page) for b in window.book.blocks] == before
    editor = window.edit_text()
    editor.text.insert("1.0", "Hola ")
    original, answers = app.messagebox.askyesno, []
    try:
        app.messagebox.askyesno = lambda *args, **kwargs: answers.append(args) or False
        editor.cancel()
        kept = bool(editor.win.winfo_exists())
        app.messagebox.askyesno = lambda *args, **kwargs: True
        editor.cancel()
        closed = not editor.win.winfo_exists()
    finally:
        app.messagebox.askyesno = original
    window.close()
    assert shown and sources == ["src2"] and same, (shown, sources, same)
    assert len(answers) == 1 and kept and closed, (answers, kept, closed)
```

- [ ] **Step 2: Run test to verify it fails**

Run (comando de «Una sola prueba» con `NOMBRE_DEL_TEST` = `test_text_editor_opens_section_and_saves_only_changes`).
Expected: FAIL con `AttributeError: 'NoneType' object has no attribute 'text'`, porque `edit_text` aún no devuelve el editor.

- [ ] **Step 2b: Add the module helpers to `app.py`**

Añadir debajo de `ICON = …` (al final del bloque de constantes):

```python
MARK = re.compile(r"</?[ib]>")  # marcas de cursiva y negrita del editor de texto
```

Añadir justo antes de `class App:`:

```python
def _is_paragraph(line):
    """Línea del editor de texto que es un párrafo: ni título, ni marca de ilustración o separador, ni
    separador escrito. Solo los párrafos se unen, se parten o pasan a título."""
    line = line.strip()
    return (bool(line) and not line.startswith("# ") and not convert.TOKEN.fullmatch(line)
            and not convert.SCENE_BREAK.fullmatch(line))
```

- [ ] **Step 3: Write minimal implementation**

Añadir la clase en `app.py` justo antes de `if __name__ == "__main__":` (línea ~1723, al final del archivo, después de `class App`):

```python
class TextEditor:
    """Editor del texto de una sección, de su título al siguiente: erratas, párrafos unidos o partidos,
    títulos que el análisis no vio ("# Título"), ilustraciones movidas. Mismo formato que
    convert.section_text; al aceptar, App.apply_edit lo aplica con convert.edit_section."""

    def __init__(self, app, item, start, end):
        self.app, self.item, self.start, self.end = app, item, start, end
        self.win = win = tk.Toplevel(app.root)
        win.title(f"Texto · {app.tree.set(item, 'title')}")
        win.transient(app.root)
        width, height = min(1250, win.winfo_screenwidth() - 60), min(760, win.winfo_screenheight() - 110)
        win.geometry(f"{width}x{height}+20+20")
        win.protocol("WM_DELETE_WINDOW", self.cancel)
        win.bind("<Escape>", self.cancel)
        self.tools = ttk.Frame(win, padding=(8, 8, 8, 0))  # botones de formato (tarea 3)
        self.tools.pack(fill="x")
        ttk.Label(win, text="Un párrafo por línea · «# Título» crea un título · [Ilustración N] y [Separador N]"
                            " se pueden mover o borrar", padding=(8, 4, 8, 0)).pack(anchor="w")
        buttons = ttk.Frame(win)
        buttons.pack(side="bottom", pady=8)
        ttk.Button(buttons, text="Aceptar", command=self.save).pack(side="left", padx=4)
        ttk.Button(buttons, text="Cancelar", command=self.cancel).pack(side="left", padx=4)
        self.body = ttk.Frame(win, padding=8)
        self.body.pack(fill="both", expand=True)
        self.text = text = tk.Text(self.body, wrap="word", undo=True)
        self.scroll = ttk.Scrollbar(self.body, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=self.scroll.set)
        self.scroll.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)
        # Cada bloque con su tag src<k>: los tags se mueven con el texto y dicen de qué bloque viene cada línea
        for k in range(start, end):
            if k > start:
                text.insert("end", "\n\n")
            text.insert("end", convert.section_text(app.book, k, k + 1), (f"src{k}",))
        self.original = text.get("1.0", "end-1c")
        text.edit_reset()
        text.focus()

    def dirty(self):
        return self.text.get("1.0", "end-1c") != self.original

    def save(self):
        """Aplica el texto solo si cambió: edit_section rehace los párrafos y perderían su página del PDF."""
        if self.dirty():
            self.app.apply_edit([self.start, self.end, self.text.get("1.0", "end")], self.item)
        self.win.destroy()

    def cancel(self, _=None):
        if self.dirty() and not messagebox.askyesno(
                "Descartar cambios", "El texto tiene cambios sin guardar. ¿Descartarlos?", parent=self.win):
            return
        self.win.destroy()
```

Sustituir el cuerpo de `App.edit_text` (desde `win = tk.Toplevel(self.root)` hasta el final del método, incluida la función interna `save` y los dos botones) para que quede así:

```python
    def edit_text(self):
        """Abre el editor del texto de la sección seleccionada (de su título al siguiente)."""
        selected = self.tree.selection()
        if not self.book or not selected or self.is_image(selected[0]):
            return None
        item = selected[0]
        start = int(item) + 1
        end = next((k for k in range(start, len(self.book.blocks)) if self.book.blocks[k].kind == "heading"),
                   len(self.book.blocks))
        return TextEditor(self, item, start, end)
```

Las teclas `e`/`E` llaman a `self.edit_text()` desde un `lambda`. Ahora devuelve un objeto y no `"break"`, así que Tk sigue procesando la tecla igual que antes.

- [ ] **Step 4: Run test to verify it passes**

Run: el comando de «Una sola prueba» con `test_text_editor_opens_section_and_saves_only_changes`, y luego con `test_window_edited_text_survives_reopening_and_reaches_epub`.
Expected: `ok` las dos veces.

- [ ] **Step 5: Run the full suite**

Run: `.venv/Scripts/python.exe test_convert.py`
Expected: exit 0, ninguna línea `FAIL`.

- [ ] **Step 6: Commit**

```bash
git add app.py test_convert.py
git commit -m "feat: text editor asks before discarding changes and keeps pages when nothing changed

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Resaltado de títulos, cursivas, negritas, marcas y separadores

**Files:**
- Modify: `app.py` (constantes junto a `MARK`, `TextEditor.__init__`, métodos nuevos de `TextEditor`)
- Test: `test_convert.py` (test nuevo después del de la tarea 1)

**Interfaces:**
- Consumes: `TextEditor`, `TextEditor.text`, `TextEditor.win`, `MARK` (tarea 1).
- Produces:
  - Tags de `tk.Text`: `italic`, `bold`, `bolditalic`, `heading`, `mark`, `token`, `scene`.
  - `TextEditor.highlight()`, `TextEditor.refresh()` (lo amplía la tarea 4).
  - `TextEditor.later(name: str, ms: int, fn)`: ejecuta `fn` tras `ms` y cancela la llamada anterior con el mismo `name`.
  - `TextEditor.pending: dict`.

- [ ] **Step 1: Write the failing test**

```python
def test_text_editor_highlights_marks():
    """Títulos, cursivas, negritas, marcas de ilustración y separadores se distinguen de un vistazo."""
    import app

    root, window = visible_window()
    window.pdf = make_novel()
    window.show_book(convert.analyze(window.pdf))
    editor = app.TextEditor(window, "1", 0, len(window.book.blocks))  # todo el libro: ilustración incluida
    text = editor.text
    content = text.get("1.0", "end-1c")

    def tags(needle):
        return set(text.tag_names(f"1.0+{content.index(needle)}c"))

    found = {"título": "heading" in tags("# Capitulo 1"), "ilustración": "token" in tags("[Ilustración 0]"),
             "marca": "mark" in tags("<i>que"), "marca sin cursiva": "italic" not in tags("<i>que"),
             "cursiva": "italic" in tags("que era"), "texto normal": not {"italic", "heading"} & tags("Ella")}
    text.insert("end", "\n\n<b>uno <i>dos</i></b>\n\n* * *")
    editor.highlight()
    content = text.get("1.0", "end-1c")
    found["negrita"] = "bold" in tags("uno")
    found["negrita y cursiva"] = "bolditalic" in tags("dos</i>")
    found["separador escrito"] = "scene" in tags("* * *")
    window.close()  # con el editor abierto: sus after pendientes no deben dar errores
    assert all(found.values()), found
```

- [ ] **Step 2: Run test to verify it fails**

Run: «Una sola prueba» con `test_text_editor_highlights_marks`.
Expected: FAIL con un `AssertionError` donde salen en `False` `título`, `ilustración`, `marca` y `cursiva`.

- [ ] **Step 3: Write minimal implementation**

Añadir junto a `MARK` en `app.py`:

```python
# Aspecto de las marcas en el editor de texto. En este orden de prioridad: si dos tags fijan lo mismo, manda el último
EDITOR_TAGS = {"italic": {}, "bold": {}, "bolditalic": {}, "heading": {"foreground": "#1f6fd1"},
               "mark": {"foreground": "#a8a8a8"}, "token": {"background": "#e3efe5", "foreground": "#2e7d32"},
               "scene": {"foreground": "#7a5c00", "justify": "center"}}
STYLE_TAGS = {1: "italic", 2: "bold", 3: "bolditalic"}  # cursiva suma 1 y negrita 2: Tk no mezcla dos fuentes
```

En `TextEditor.__init__`, justo después de `text.pack(side="left", fill="both", expand=True)`:

```python
        family = tkfont.nametofont("TkDefaultFont").actual("family")
        self.fonts = {name: tkfont.Font(root=win, family=family, **options) for name, options in (
            ("text", {"size": 11}), ("heading", {"size": 14, "weight": "bold"}),
            ("italic", {"size": 11, "slant": "italic"}), ("bold", {"size": 11, "weight": "bold"}),
            ("bolditalic", {"size": 11, "weight": "bold", "slant": "italic"}))}  # referencias: sin ellas, Tk las borra
        text.configure(font=self.fonts["text"], spacing1=2, spacing3=2, padx=8)
        for tag, options in EDITOR_TAGS.items():
            text.tag_configure(tag, **options, **({"font": self.fonts[tag]} if tag in self.fonts else {}))
        self.pending = {}  # trabajo diferido: nombre -> id de after
        text.bind("<<Modified>>", self.on_modified)
        win.bind("<Destroy>", self.on_destroy)
```

Al final de `__init__`, después de `text.focus()`:

```python
        self.highlight()
```

Métodos nuevos en `TextEditor`:

```python
    def later(self, name, ms, fn):
        """Ejecuta fn tras ms milisegundos sin otro aviso del mismo nombre: no repinta en cada tecla."""
        if name in self.pending:
            self.win.after_cancel(self.pending[name])
        self.pending[name] = self.win.after(ms, lambda: (self.pending.pop(name, None), fn()))

    def on_modified(self, _=None):
        if self.text.edit_modified():
            self.text.edit_modified(False)  # rearma el aviso para el siguiente cambio
            self.later("refresh", 300, self.refresh)

    def on_destroy(self, event):
        if event.widget is self.win:  # <Destroy> llega también por cada hijo
            for job in self.pending.values():
                self.win.after_cancel(job)
            self.pending.clear()

    def refresh(self):
        self.highlight()

    def highlight(self):
        """Pone los tags de aspecto según las marcas. Solo cambia tags, no texto: no entra en deshacer."""
        text = self.text
        for tag in EDITOR_TAGS:
            text.tag_remove(tag, "1.0", "end")
        for n, line in enumerate(text.get("1.0", "end-1c").split("\n"), start=1):
            bare = line.strip()
            if bare.startswith("# "):
                text.tag_add("heading", f"{n}.0", f"{n}.end")
            elif convert.TOKEN.fullmatch(bare):
                text.tag_add("token", f"{n}.0", f"{n}.end")
            elif bare and convert.SCENE_BREAK.fullmatch(bare):
                text.tag_add("scene", f"{n}.0", f"{n}.end")
            flags = [0] * len(line)
            for flag, mark in ((1, "i"), (2, "b")):
                for m in re.finditer(rf"<{mark}>(.*?)</{mark}>", line):
                    for c in range(m.start(1), m.end(1)):
                        flags[c] |= flag
            run = 0
            for c in range(1, len(line) + 1):
                if c == len(line) or flags[c] != flags[run]:
                    if flags[run]:
                        text.tag_add(STYLE_TAGS[flags[run]], f"{n}.{run}", f"{n}.{c}")
                    run = c
            for m in MARK.finditer(line):
                text.tag_add("mark", f"{n}.{m.start()}", f"{n}.{m.end()}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: «Una sola prueba» con `test_text_editor_highlights_marks`, y luego con `test_text_editor_opens_section_and_saves_only_changes`.
Expected: `ok` las dos veces.

- [ ] **Step 5: Run the full suite**

Run: `.venv/Scripts/python.exe test_convert.py`
Expected: exit 0, ninguna línea `FAIL`.

- [ ] **Step 6: Commit**

```bash
git add app.py test_convert.py
git commit -m "feat: text editor colors titles, italics, bold, illustrations and scene breaks

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Barra de herramientas y atajos (cursiva, negrita, título, separador, unir, partir)

**Files:**
- Modify: `app.py` (`TextEditor.__init__`, métodos nuevos de `TextEditor`)
- Test: `test_convert.py` (test nuevo después del de la tarea 2)

**Interfaces:**
- Consumes: `TextEditor.tools`, `TextEditor.text`, `TextEditor.win`, `TextEditor.highlight()`, `_is_paragraph` (tareas 1 y 2).
- Produces:
  - `TextEditor.step(change) -> "break"`: ejecuta `change()` como un solo paso de deshacer y vuelve a resaltar.
  - `TextEditor.toggle_mark(mark: str)` (`"i"` o `"b"`), `toggle_heading()`, `insert_break()`, `join_next()`, `split_here()`. No devuelven nada y no abren pasos de deshacer: los llama `step`.

- [ ] **Step 1: Write the failing test**

```python
def test_text_editor_actions_and_undo():
    """Cursiva, título, unir y partir párrafos sin escribir marcas; cada acción se deshace con un Ctrl+Z."""
    import app

    root, window = visible_window()
    window.pdf = make_novel()
    window.show_book(convert.analyze(window.pdf))
    editor = app.TextEditor(window, "6", 7, 10)  # "Fin." / "Dijo <i>hola</i>, y se fue." / "Palabra separada."
    text = editor.text

    def line(n):
        return text.get(f"{n}.0", f"{n}.end")

    def select(first, last):
        text.tag_remove("sel", "1.0", "end")
        text.tag_add("sel", first, last)

    seen = []
    select("1.0", "1.3")
    editor.step(lambda: editor.toggle_mark("i"))
    seen.append(line(1))                       # envuelve "Fin"
    select("1.3", "1.6")
    editor.step(lambda: editor.toggle_mark("i"))
    seen.append(line(1))                       # otra vez: la quita
    select("1.0", "3.2")
    editor.step(lambda: editor.toggle_mark("i"))
    seen.append((line(1), line(3)))            # varias líneas: no hace nada
    text.tag_remove("sel", "1.0", "end")
    text.mark_set("insert", "1.2")
    editor.step(editor.toggle_heading)
    seen.append(line(1))
    editor.step(editor.join_next)
    seen.append(line(1))                       # un título no se une
    editor.step(editor.toggle_heading)
    text.mark_set("insert", "1.2")
    editor.step(editor.join_next)
    seen.append(line(1))
    text.edit_undo()
    seen.append((line(1), line(3)))            # un solo Ctrl+Z deshace la unión entera
    text.mark_set("insert", "3.9")             # "Dijo <i>h|ola</i>"
    editor.step(editor.split_here)
    seen.append((line(3), line(5)))            # la cursiva se cierra y se vuelve a abrir
    text.mark_set("insert", "1.0")
    editor.step(editor.insert_break)
    seen.append(line(3))
    bound = all(text.bind(key) for key in ("<Control-i>", "<Control-b>", "<Control-t>", "<Control-j>",
                                           "<Control-Return>"))
    window.close()
    assert seen == ["<i>Fin</i>.", "Fin.", ("Fin.", "Dijo <i>hola</i>, y se fue."), "# Fin.", "# Fin.",
                    "Fin. Dijo <i>hola</i>, y se fue.", ("Fin.", "Dijo <i>hola</i>, y se fue."),
                    ("Dijo <i>h</i>", "<i>ola</i>, y se fue."), "* * *"], seen
    assert bound
```

- [ ] **Step 2: Run test to verify it fails**

Run: «Una sola prueba» con `test_text_editor_actions_and_undo`.
Expected: FAIL con `AttributeError: 'TextEditor' object has no attribute 'step'`.

- [ ] **Step 3: Write minimal implementation**

En `TextEditor.__init__`, justo después de `self.pending = {}` y los dos `bind` de la tarea 2:

```python
        actions = (("Cursiva", "Ctrl+I", ("<Control-i>", "<Control-I>"), lambda: self.toggle_mark("i")),
                   ("Negrita", "Ctrl+B", ("<Control-b>", "<Control-B>"), lambda: self.toggle_mark("b")),
                   ("Título ↔ párrafo", "Ctrl+T", ("<Control-t>", "<Control-T>"), self.toggle_heading),
                   ("Separador", "", (), self.insert_break),
                   ("Unir con el siguiente", "Ctrl+J", ("<Control-j>", "<Control-J>"), self.join_next),
                   ("Partir aquí", "Ctrl+Enter", ("<Control-Return>",), self.split_here))
        for label, shortcut, keys, change in actions:
            ttk.Button(self.tools, text=f"{label}  {shortcut}".strip(),
                       command=lambda change=change: self.step(change)).pack(side="left", padx=(0, 4))
            for key in keys:  # las dos formas: con Bloq Mayús Tk recibe la mayúscula
                text.bind(key, lambda _, change=change: self.step(change))
```

Métodos nuevos en `TextEditor`:

```python
    def step(self, change):
        """Una acción del editor como un solo paso de deshacer. Devuelve "break" para que Tk no haga
        además lo suyo: Ctrl+I inserta un tabulador y Ctrl+T intercambia dos letras."""
        text = self.text
        text.configure(autoseparators=False)  # con separadores automáticos, borrar e insertar serían dos pasos
        text.edit_separator()
        try:
            change()
        finally:
            text.edit_separator()
            text.configure(autoseparators=True)
        self.highlight()
        text.focus_set()
        return "break"

    def toggle_mark(self, mark):
        """Envuelve la selección en <i>/<b>, o quita las marcas si ya la envuelven. Sin selección, deja las
        marcas vacías con el cursor dentro. No cruza párrafos: la marca quedaría sin cerrar."""
        text, opening, closing = self.text, f"<{mark}>", f"</{mark}>"
        if not text.tag_ranges("sel"):
            text.insert("insert", opening + closing)
            text.mark_set("insert", f"insert-{len(closing)}c")
            return
        first, last = text.index("sel.first"), text.index("sel.last")
        if first.split(".")[0] != last.split(".")[0]:
            self.win.bell()
            return
        if (text.get(f"{first}-{len(opening)}c", first) == opening
                and text.get(last, f"{last}+{len(closing)}c") == closing):
            text.delete(last, f"{last}+{len(closing)}c")
            text.delete(f"{first}-{len(opening)}c", first)
        else:
            text.insert(last, closing)
            text.insert(first, opening)

    def toggle_heading(self):
        """Pone o quita "# " delante de la línea del cursor. No en una línea vacía: "# " solo se leería
        como el separador "#"."""
        text = self.text
        if text.get("insert linestart", "insert linestart+2c") == "# ":
            text.delete("insert linestart", "insert linestart+2c")
        elif _is_paragraph(text.get("insert linestart", "insert lineend")):
            text.insert("insert linestart", "# ")
        else:
            self.win.bell()

    def insert_break(self):
        """Separador de escena escrito, como párrafo propio tras la línea del cursor."""
        end = self.text.index("insert lineend")
        self.text.insert(end, "\n\n* * *")
        self.text.mark_set("insert", f"{end}+7c")

    def join_next(self):
        """Une el párrafo del cursor con el siguiente: arregla un párrafo partido por un salto de página."""
        text = self.text
        n = int(text.index("insert").split(".")[0])
        last = int(text.index("end-1c").split(".")[0])
        following = next((m for m in range(n + 1, last + 1) if text.get(f"{m}.0", f"{m}.end").strip()), None)
        line = text.get(f"{n}.0", f"{n}.end")
        if following is None or not _is_paragraph(line) or not _is_paragraph(
                text.get(f"{following}.0", f"{following}.end")):
            self.win.bell()
            return
        nxt = text.get(f"{following}.0", f"{following}.end")
        end = f"{n}.{len(line.rstrip())}"
        text.delete(end, f"{following}.{len(nxt) - len(nxt.lstrip())}")
        text.insert(end, " ")
        text.mark_set("insert", f"{end}+1c")

    def split_here(self):
        """Parte el párrafo en el cursor. Una cursiva o negrita abierta se cierra y se vuelve a abrir: si no,
        cada mitad quedaría con una marca suelta y edit_section la quitaría."""
        text = self.text
        if not _is_paragraph(text.get("insert linestart", "insert lineend")):
            self.win.bell()
            return
        while text.get("insert-1c") == " ":
            text.delete("insert-1c")
        while text.get("insert") == " ":
            text.delete("insert")
        before = text.get("insert linestart", "insert")
        opened = [m for m in "ib" if before.count(f"<{m}>") > before.count(f"</{m}>")]
        text.insert("insert", "".join(f"</{m}>" for m in reversed(opened)) + "\n\n"
                    + "".join(f"<{m}>" for m in opened))
```

- [ ] **Step 4: Run test to verify it passes**

Run: «Una sola prueba» con `test_text_editor_actions_and_undo`, y luego con `test_text_editor_highlights_marks`.
Expected: `ok` las dos veces.

- [ ] **Step 5: Run the full suite**

Run: `.venv/Scripts/python.exe test_convert.py`
Expected: exit 0, ninguna línea `FAIL`.

- [ ] **Step 6: Commit**

```bash
git add app.py test_convert.py
git commit -m "feat: text editor buttons and shortcuts for italics, bold, titles, scene breaks, join and split

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Panel del PDF sincronizado con el cursor y barra de estado

**Files:**
- Modify: `app.py` (constante `EDITOR_PANEL` junto a `PREVIEW_BOX`, `TextEditor.__init__`, `TextEditor.refresh`, métodos nuevos de `TextEditor`)
- Test: `test_convert.py` (test nuevo después del de la tarea 3)

**Interfaces:**
- Consumes: `TextEditor.body`, `TextEditor.scroll`, `TextEditor.later`, `TextEditor.refresh`, `TextEditor.item`, los tags `src{k}` (tareas 1 y 2), `App.pdf_doc`, `App.book`, `MARK_COLORS["current"]`, `convert._plain`, `convert.TOKEN`.
- Produces:
  - `TextEditor.source_block() -> int`: índice del bloque de origen de la línea del cursor.
  - `TextEditor.draw_source()`: dibuja el panel ahora mismo, sin esperar.
  - `TextEditor.shown`: `("page", número desde 1)`, `("image", k)` o `None` si no hay PDF.
  - `TextEditor.box`: `pymupdf.Rect` del párrafo en su página, o `None`.
  - `TextEditor.status`: `ttk.Label` con «Página N del PDF · …».
  - `TextEditor.turn(delta: int)`, `TextEditor.show_page(index: int)`, `TextEditor.find_box(page, block)` (estático).

- [ ] **Step 1: Write the failing test**

```python
def test_text_editor_shows_source_page_and_illustration():
    """El panel lateral sigue al cursor: la página del PDF del párrafo (recuadrado), también en una línea
    nueva escrita debajo; sobre [Ilustración N], la ilustración. Sección vacía y libro sin PDF: sin errores."""
    import app

    root, window = visible_window()
    window.pdf = make_novel()
    window.show_book(convert.analyze(window.pdf))
    editor = app.TextEditor(window, "1", 0, len(window.book.blocks))
    text = editor.text

    def at(needle):
        text.mark_set("insert", text.search(needle, "1.0"))
        editor.draw_source()
        return editor.shown

    seen = [at("Fin."), editor.box is not None, at("[Ilustración 0]"), at("Ella penso")]
    text.insert(text.index(f"{text.search('Fin.', '1.0')} lineend"), "\n\nParrafo nuevo")
    seen.append(at("Parrafo nuevo"))
    status = editor.status.cget("text")
    empty = app.TextEditor(window, "6", 7, 7)  # título sin texto debajo: muestra la página del título
    empty.draw_source()
    seen.append(empty.shown)
    doc, window.pdf_doc = window.pdf_doc, None  # revisión de un PDF que ya no está
    at("Fin.")
    seen.append(editor.shown)
    window.pdf_doc = doc
    window.close()
    assert seen == [("page", 4), True, ("image", 0), ("page", 3), ("page", 4), ("page", 4), None], seen
    assert status.startswith("Página 4 del PDF · párrafo "), status
```

- [ ] **Step 2: Run test to verify it fails**

Run: «Una sola prueba» con `test_text_editor_shows_source_page_and_illustration`.
Expected: FAIL con `AttributeError: 'TextEditor' object has no attribute 'draw_source'`.

- [ ] **Step 3: Write minimal implementation**

Añadir junto a `PREVIEW_BOX` en `app.py`:

```python
EDITOR_PANEL = (360, 480)  # ancho y alto del panel del PDF en el editor de texto, en píxeles
```

En `TextEditor.__init__`:

1. Justo después de `buttons.pack(side="bottom", pady=8)` y de sus dos botones:

```python
        self.status = ttk.Label(win, text="", padding=(8, 0))
        self.status.pack(side="bottom", anchor="w")
```

2. Justo antes de `self.scroll.pack(side="right", fill="y")`. Con `pack`, el panel tiene que entrar antes que la barra de desplazamiento para quedar a la derecha del todo:

```python
        panel = ttk.Frame(self.body, padding=(8, 0, 0, 0))
        panel.pack(side="right", fill="y")
        self.canvas = tk.Canvas(panel, width=EDITOR_PANEL[0], height=EDITOR_PANEL[1], highlightthickness=0,
                                background="#f4f4f4")
        self.canvas.pack()
        nav = ttk.Frame(panel)
        nav.pack(pady=(6, 0))
        ttk.Button(nav, text="◀", width=3, command=lambda: self.turn(-1)).pack(side="left")
        self.page_label = ttk.Label(nav, text="", width=16, anchor="center")
        self.page_label.pack(side="left", padx=4)
        ttk.Button(nav, text="▶", width=3, command=lambda: self.turn(1)).pack(side="left")
        self.shown, self.box, self.page, self.photo, self.offset = None, None, 0, None, (0, 0)
```

3. Justo después de los `bind` de `<<Modified>>` y `<Destroy>`:

```python
        for event in ("<KeyRelease>", "<ButtonRelease-1>"):  # el cursor se movió: el panel lo sigue
            text.bind(event, lambda _: self.later("source", 150, self.draw_source), add="+")
```

4. Al final de `__init__`, después de `self.highlight()`:

```python
        self.draw_source()
```

Sustituir `TextEditor.refresh` por:

```python
    def refresh(self):
        self.highlight()
        self.update_status()
```

Métodos nuevos en `TextEditor`:

```python
    def source_block(self):
        """Bloque del libro del que viene la línea del cursor: el de su marca [Ilustración N], el de su tag
        src<k> o, en una línea nueva, el de la línea anterior más cercana que lo tenga. Sin ninguno (sección
        vacía), el título de la sección."""
        text = self.text
        n = int(text.index("insert").split(".")[0])
        token = convert.TOKEN.fullmatch(text.get(f"{n}.0", f"{n}.end").strip())
        if token and int(token.group(2)) < len(self.app.book.blocks):
            return int(token.group(2))
        for m in range(n, 0, -1):
            for index in (f"{m}.0", f"{m}.end-1c"):
                for tag in text.tag_names(index):
                    if tag.startswith("src"):
                        return int(tag[3:])
        return int(self.item)

    def draw_source(self):
        """Panel lateral: la ilustración del bloque del cursor, o su página del PDF con el párrafo recuadrado."""
        block = self.app.book.blocks[self.source_block()]
        if block.kind == "image" and block.image:
            pix = pymupdf.Pixmap(block.image)
            if pix.alpha:
                pix = pymupdf.Pixmap(pix, 0)
            if pix.colorspace and pix.colorspace.n not in (1, 3):
                pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
            scale = min(EDITOR_PANEL[0] / pix.width, EDITOR_PANEL[1] / pix.height, 1)
            if scale < 1:
                pix = pymupdf.Pixmap(pix, max(1, round(pix.width * scale)), max(1, round(pix.height * scale)), None)
            self.shown, self.box = ("image", self.source_block()), None
            self.put(pix)
            self.page_label.configure(text=f"Ilustración · pág. {block.page}")
        elif self.app.pdf_doc is None:
            self.shown, self.box = None, None
            self.canvas.delete("all")
            self.canvas.create_text(EDITOR_PANEL[0] // 2, EDITOR_PANEL[1] // 2, fill="#9a9a9a",
                                    width=EDITOR_PANEL[0] - 40, justify="center",
                                    text="Sin el PDF original no se puede mostrar la página")
            self.page_label.configure(text="")
        else:
            page = self.app.pdf_doc[block.page - 1]
            self.box = pymupdf.Rect(block.rect) if block.rect else self.find_box(page, block)
            self.show_page(block.page - 1)
        self.update_status()

    def show_page(self, index):
        """Dibuja la página index (desde 0) del PDF, con self.box recuadrado si lo hay."""
        page = self.app.pdf_doc[index]
        self.page, self.shown = index, ("page", index + 1)
        zoom = min(EDITOR_PANEL[0] / page.rect.width, EDITOR_PANEL[1] / page.rect.height)
        self.put(page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)))
        if self.box:
            x, y = self.offset
            box = self.box * zoom
            self.canvas.create_rectangle(x + box.x0 - 3, y + box.y0 - 3, x + box.x1 + 3, y + box.y1 + 3,
                                         outline=MARK_COLORS["current"], width=2)
        self.page_label.configure(text=f"Página {index + 1} de {self.app.pdf_doc.page_count}")

    def put(self, pix):
        """Dibuja el pixmap centrado en el panel."""
        self.photo = tk.PhotoImage(master=self.win, data=pix.tobytes("ppm"))  # referencia: sin ella, Tk la borra
        self.offset = ((EDITOR_PANEL[0] - pix.width) // 2, (EDITOR_PANEL[1] - pix.height) // 2)
        self.canvas.delete("all")
        self.canvas.create_image(*self.offset, anchor="nw", image=self.photo)

    def turn(self, delta):
        """◀ ▶: hojea el PDF sin mover el cursor; el siguiente movimiento vuelve a la página de su párrafo."""
        if self.app.pdf_doc is None:
            return
        self.box = None
        self.show_page(min(max(self.page + delta, 0), self.app.pdf_doc.page_count - 1))

    @staticmethod
    def find_box(page, block):
        """Recuadro del párrafo en su página, de sus 6 primeras palabras a sus 6 últimas. None si el texto no
        aparece tal cual (palabra partida con guion en el PDF, párrafo ya editado)."""
        words = convert._plain(block).split()
        hits = page.search_for(" ".join(words[:6])) if words else []
        if not hits:
            return None
        box = hits[0]
        tail = [r for r in page.search_for(" ".join(words[-6:])) if r.y0 >= box.y0]
        return box | tail[0] if tail else box

    def update_status(self):
        """«Página 34 del PDF · párrafo 12 de 80 · 3.412 palabras»."""
        lines = self.text.get("1.0", "end-1c").split("\n")
        paragraphs = [n for n, line in enumerate(lines, start=1) if _is_paragraph(line)]
        current = int(self.text.index("insert").split(".")[0])
        words = sum(len(MARK.sub("", lines[n - 1]).split()) for n in paragraphs)
        count = len(paragraphs)
        where = (f"párrafo {paragraphs.index(current) + 1} de {count}" if current in paragraphs
                 else f"{count} párrafo{'' if count == 1 else 's'}")
        page = self.app.book.blocks[self.source_block()].page
        self.status.configure(text=f"Página {page} del PDF · {where} · "
                                   f"{words:,} palabra{'' if words == 1 else 's'}".replace(",", "."))
```

- [ ] **Step 4: Run test to verify it passes**

Run: «Una sola prueba» con `test_text_editor_shows_source_page_and_illustration`, y luego con `test_text_editor_actions_and_undo` y `test_text_editor_opens_section_and_saves_only_changes`.
Expected: `ok` las tres veces.

- [ ] **Step 5: Run the full suite**

Run: `.venv/Scripts/python.exe test_convert.py`
Expected: exit 0, ninguna línea `FAIL`.

- [ ] **Step 6: Commit**

```bash
git add app.py test_convert.py
git commit -m "feat: text editor shows the PDF page of the paragraph under the cursor

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: .exe y prueba a mano con una novela real

**Files:**
- Ninguno nuevo. Solo `dist/PDF a EPUB.exe`, que está ignorado por git.

- [ ] **Step 1: Build**

Ejecutar la skill `/build-exe`: tests, PyInstaller y arranque de prueba, uno detrás de otro y nunca a la vez.
Expected: `Build complete!` y `vivo=True`.

- [ ] **Step 2: Prueba a mano**

Abrir `dist\PDF a EPUB.exe` y un PDF de `Novelas PDF/` (por ejemplo `Re Zero - Volumen 01 [EHJR].pdf`). Elegir un capítulo y pulsar E. Comprobar:
- Títulos en azul y grandes, cursivas en cursiva con las marcas en gris, `[Ilustración N]` con fondo verde.
- Ctrl+I con una palabra seleccionada la pone en cursiva; Ctrl+Z lo deshace de una vez.
- Al mover el cursor por los párrafos, el panel cambia de página y recuadra el párrafo; sobre `[Ilustración N]` muestra la ilustración.
- Escape con cambios pregunta; «Aceptar» actualiza la vista previa del EPUB.

Anotar cualquier cosa que no se vea bien para contársela al usuario. No arreglar sin preguntar.

- [ ] **Step 3: Report**

Dar al usuario la ruta completa del .exe:
`C:\Users\HP\OneDrive\Documentos\Proyectos\Light Novel Creator EPUB\dist\PDF a EPUB.exe`
