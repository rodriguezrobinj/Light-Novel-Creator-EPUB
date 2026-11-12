# Ventana «Opciones» — plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Todos los ajustes en una ventana «Opciones» con categorías, buscador y alcance «Este libro | Libros nuevos», y los libros nuevos abren con valores elegidos en vez de con los del último libro.

**Architecture:** Un módulo nuevo `options.py` con la tabla `OPTIONS` (una línea por opción) y `OptionsDialog`, que construye páginas, buscador y ayudas desde la tabla. `app.py` le da los valores: los `*_var` del libro abierto (en vivo), `settings["defaults"]` (Libros nuevos) y los de la aplicación; al Aceptar, `App.accept_options` los guarda. Los controles viejos se quitan al final, cuando la ventana ya cubre todo.

**Tech Stack:** Python 3.14, PySide6 (vía `qtk` y `theme`), tests en `test_convert.py` sin pantalla.

**Spec:** `docs/superpowers/specs/2026-11-11-ventana-opciones-design.md`

## Global Constraints

- Todo texto visible en español con tildes; comentarios en español, cortos, que dicen el porqué; identificadores en inglés.
- En `app.py`, API de `qtk` (`Var`, `RowList`, `after`, `messagebox`, `filedialog`). Colores de `theme.py`, nunca hex sueltos.
- Nada de PyMuPDF en hilos: lo pesado va por `window.run`. Buscar modelos (red) sí va en hilo, como hoy.
- Un `Var` no se enlaza con `.check()/.combo()/.entry()` a controles del diálogo: el diálogo se borra al cerrar y `Var.set` llamaría a un control muerto (ver el comentario en el Informe de calidad).
- Tests: `.venv/Scripts/python.exe test_convert.py` (exit 1 con cualquier `FAIL`); nunca a la vez que el build.
- Tras cada tarea con cambios en la app, la suite entera pasa. `/build-exe` al final.
- Commits: `feat:`/`fix:`/`test:` + asunto y cuerpo en español, sin firma de Claude, en la rama `feat/ventana-opciones`.

## Review Focus

1. **`ajustes.json` escrito a mano o de una versión vieja** (sin `defaults`, con `gray: "sí"`, con `translate` que no es dict): la app abre igual y usa los valores válidos. Test en Tarea 2.
2. **Cancelar tras cambiar perfil, letra propia y tema**: todo vuelve, también el tema y la vista previa. Test en Tarea 3 y Tarea 5.
3. **Traducir abierto mientras se cambian motor o clave en Opciones**: al volver, Traducir usa lo nuevo y al cerrarse no lo pisa con lo viejo. Test en Tarea 6.
4. **Abrir Opciones sin libro**: «Este libro» desactivado, cambios a «Libros nuevos», sin errores. Test en Tarea 3.
5. **Letra propia cuyo archivo ya no existe**: aviso rojo en la página, sin ventana emergente, y el EPUB usa la del lector. Test en Tarea 4.

---

### Task 1: Tabla de opciones y búsqueda (`options.py`)

**Files:**
- Create: `options.py`
- Test: `test_convert.py` (junto a los demás `test_window_*`, al final antes de `FakeSpellProcess` no importa: cualquier sitio antes de `no_modal_dialogs`)
- Modify: `PDF a EPUB.spec` solo si `options` no entra solo (PyInstaller sigue los `import`; no debería hacer falta)

**Interfaces:**
- Produces: `options.BOOK`, `options.APP` (nombres de grupo), `options.CATEGORIES: dict[str, tuple[str, ...]]`, `options.Option(category, key, kind, label, help)`, `options.OPTIONS: tuple[Option, ...]`, `options.BOOK_KEYS: tuple[str, ...]` (claves de libro, `font_file` incluida), `options.fold(text) -> str`, `options.find(text) -> list[Option]`.

- [ ] **Step 1: Test que falla**

```python
def test_options_table_and_search():
    """Cada opción está una vez, en una categoría que existe; buscar «guion» lleva a Texto y a PDF, sin tildes ni
    mayúsculas; y las claves de libro son las que se guardan por libro."""
    import options
    keys = [o.key for o in options.OPTIONS]
    categories = {c for group in options.CATEGORIES.values() for c in group}
    found = sorted({o.category for o in options.find("GUIÓN")})
    assert len(keys) == len(set(keys)), keys
    assert all(o.category in categories for o in options.OPTIONS)
    assert found == ["PDF", "Texto"], found
    assert options.find("zzz") == []
    assert "font_file" in options.BOOK_KEYS and "theme" not in options.BOOK_KEYS
    assert options.fold("Tipografía ÚNICA") == "tipografia unica"
```

- [ ] **Step 2: Verlo fallar**

Run: `.venv/Scripts/python.exe test_convert.py` → `FAIL test_options_table_and_search` con `ModuleNotFoundError: No module named 'options'`.

- [ ] **Step 3: Crear `options.py` con la tabla**

```python
"""Ventana «Opciones»: todos los ajustes en un sitio, como las Preferencias de Acrobat.
La tabla OPTIONS declara cada opción una vez; páginas, buscador y ayudas salen de ella."""
import unicodedata
from collections import namedtuple

BOOK, APP = "El libro", "La aplicación"
CATEGORIES = {BOOK: ("Perfil y formato", "Aspecto", "Texto", "Ilustraciones", "PDF"),
              APP: ("Apariencia", "Vista previa", "Traducción", "Carpeta vigilada", "Informe de calidad")}
Option = namedtuple("Option", "category key kind label help")
OPTIONS = (
    Option("Perfil y formato", "profile", "choice", "Perfil",
           "Fija las opciones que importan para ese lector: gris, tamaño de imagen, Kobo, guiones."),
    Option("Perfil y formato", "epub2", "check", "EPUB 2 (lectores antiguos)", "Para lectores de hace años que no abren EPUB 3."),
    Option("Perfil y formato", "kepub", "check", "Formato Kobo (KEPUB)",
           "El Kobo muestra páginas y estadísticas de lectura; el archivo acaba en .kepub.epub."),
    Option("Perfil y formato", "lang", "language", "Idioma del libro",
           "Decide cómo se parten las palabras, la voz de lectura y los avisos del informe."),
    Option("Aspecto", "style", "choice", "Estilo", "Letra, párrafos e inicio de capítulo del EPUB."),
    Option("Aspecto", "separator", "choice", "Separador de escena", "Cómo se ven los cambios de escena."),
    Option("Aspecto", "font", "font", "Letra", "Tipografía incrustada en el EPUB; «Del lector» usa la del aparato."),
    Option("Texto", "typography", "check", "Limpieza tipográfica",
           "Comillas «», puntos suspensivos, raya de diálogo y espacios, al estilo RAE."),
    Option("Texto", "hyphenate", "check", "Partir palabras con guiones (Kindle)",
           "El Kindle apenas parte palabras en español: sin esto el texto justificado queda con huecos."),
    Option("Texto", "reading", "check", "Minutos de lectura en el índice",
           "Cuánto se tarda en leer cada capítulo, en la página «Contenido»."),
    Option("Texto", "credits", "check", "Página de créditos",
           "Tras la portada, una página con los datos del libro: traductor, grupo, ilustrador…"),
    Option("Texto", "autocase", "check", "Corregir las mayúsculas al abrir si vienen en MAYÚSCULAS",
           "Los títulos escritos todo en mayúsculas pasan a mayúsculas al estilo español."),
    Option("Ilustraciones", "shrink", "check", "Reducir imágenes", "Las imágenes son casi todo el peso del EPUB."),
    Option("Ilustraciones", "shrink_size", "number", "Lado mayor, en píxeles", "Tamaño de las imágenes reducidas."),
    Option("Ilustraciones", "gray", "check", "Imágenes en gris", "Para e-readers de tinta: pesa menos y se ve igual."),
    Option("Ilustraciones", "trim", "check", "Recortar márgenes blancos de las ilustraciones",
           "Quita el borde blanco que traen algunas ilustraciones escaneadas."),
    Option("PDF", "whole_words", "check", "No partir palabras al final de línea",
           "La palabra que no cabe pasa entera a la línea siguiente, sin guion."),
    Option("PDF", "pdf_size", "choice", "Tamaño preferido", "Sale primero en el menú «PDF ▾»."),
    Option("Apariencia", "theme", "choice", "Tema", "«Sistema» sigue el modo claro u oscuro de Windows."),
    Option("Vista previa", "device", "choice", "Dispositivo al abrir", "Tamaño de página de la vista previa del EPUB."),
    Option("Vista previa", "dark", "check", "Modo oscuro al abrir", "Las vistas previas en claro sobre negro."),
    Option("Traducción", "translate", "translate", "Motor, modelo y clave de API",
           "Servicio que traduce, su modelo y su clave, e idiomas con los que empieza «Traducir»."),
    Option("Carpeta vigilada", "watch", "folder", "Carpeta vigilada",
           "Mientras la ventana esté abierta, cada PDF nuevo que llegue a ella se convierte solo."),
    Option("Informe de calidad", "echoes", "check", "Buscar ecos (palabras repetidas cerca)",
           "Muchos avisos de estilo: solo si se piden."),
)
# Lo que se guarda por libro y en «Libros nuevos»; la ruta de la letra propia va con su nombre
BOOK_KEYS = tuple(o.key for o in OPTIONS if o.category in CATEGORIES[BOOK]) + ("font_file",)


def fold(text):
    """Sin tildes ni mayúsculas: «guion» encuentra «guiones» y «Guión»."""
    return "".join(c for c in unicodedata.normalize("NFD", str(text).lower()) if unicodedata.category(c) != "Mn")


def find(text):
    """Opciones cuyo nombre o ayuda contiene text."""
    needle = fold(text).strip()
    return [o for o in OPTIONS if needle and (needle in fold(o.label) or needle in fold(o.help))]
```

- [ ] **Step 4: Verlo pasar**

Run: `.venv/Scripts/python.exe test_convert.py` → `ok test_options_table_and_search`, sin `FAIL`.

- [ ] **Step 5: Commit**

```bash
git add options.py test_convert.py
git commit -q -F - <<'EOF'
feat: tabla de opciones con búsqueda para la ventana «Opciones»
EOF
```

---

### Task 2: «Libros nuevos»: `settings["defaults"]`, migración y uso al abrir

**Files:**
- Modify: `app.py` — `App.__init__` (vars, `pdf_size_var`), `fields()`, `load_settings`, `save_settings`, `show_book`, `poll_watch`, `convert_folder` (`_batch`), `omnibus` (líneas con `{name: fields[name].get() for name in PREFS}`), constante `PREFS` (se borra).
- Modify: `test_convert.py` — `visible_window(settings=None)`.
- Modify: `docs/superpowers/specs/2026-11-11-ventana-opciones-design.md` — párrafo «Migración».

**Interfaces:**
- Consumes: `options.BOOK_KEYS`.
- Produces: `App.factory: dict` (valores de fábrica de `BOOK_KEYS`), `App.new_book_options() -> dict` (fábrica + `settings["defaults"]`, solo claves de `BOOK_KEYS`), `App.pdf_size_var: qtk.Var("A5")`, `settings["defaults"]: dict`, `settings["device"]`, `settings["dark"]`, `settings["echoes"]`.

- [ ] **Step 1: Tests que fallan**

En `visible_window`, aceptar ajustes previos:

```python
def visible_window(settings=None):
    """Ventana Qt sin pantalla (QT_QPA_PLATFORM=offscreen), con su tamaño: la lista calcula posiciones.
    settings: ajustes.json ya escrito, para probar lo que la ventana hace con él."""
    import app

    app.SETTINGS = settings or TMP / f"ajustes-{time_ns()}.json"  # cada ventana sin preferencias de otra prueba
```

Test nuevo:

```python
def test_window_new_books_use_saved_defaults():
    """Libros nuevos: un ajustes.json antiguo (opciones en la raíz, sin «defaults») pasa a «Libros nuevos» sin cambiar
    nada. Un libro sin revisión ni serie abre con esos valores, no con los del libro anterior; uno con revisión, con
    la suya. Un valor mal escrito a mano se ignora."""
    import json
    settings = TMP / f"ajustes-viejos-{time_ns()}.json"
    settings.write_text(json.dumps({"gray": True, "style": "Novela ligera", "credits": "sí", "translate": []}),
                        encoding="utf-8")
    root, window = visible_window(settings)
    migrated = dict(window.settings["defaults"])
    folder = TMP / f"libros-{time_ns()}"
    folder.mkdir()
    books = {}
    for name in ("con-revision", "nuevo"):
        doc = pymupdf.open()
        add_page(doc, 1, [(72, 100, "Capitulo 1: Uno", 20, "helv"), (72, 150, "Texto.", 11, "cour")])
        doc.save(folder / f"{name}.pdf")
        books[name] = folder / f"{name}.pdf"
    book = lambda: convert.Book("Libro", "", b"", [convert.Block("heading", 1, "Capítulo 1: Uno"),
                                                  convert.Block("p", 1, "Texto.")], [0])
    window.pdf = books["con-revision"]
    window.show_book(book())
    window.gray_var.set(False)  # este libro, sin gris
    window.save_review()
    window.pdf = books["nuevo"]
    window.show_book(book())  # sin revisión ni serie: «Libros nuevos», no lo del libro anterior
    fresh = (window.gray_var.get(), window.style_var.get(), window.credits_var.get())
    window.pdf = books["con-revision"]
    window.show_book(book())
    reviewed = window.gray_var.get()
    options = window.new_book_options()
    window.close()
    assert migrated["gray"] is True and migrated["style"] == "Novela ligera" and "credits" not in migrated, migrated
    assert fresh == (True, "Novela ligera", False), fresh
    assert reviewed is False
    assert options["gray"] is True and set(options) == set(__import__("options").BOOK_KEYS), options
```

- [ ] **Step 2: Verlo fallar**

Run: `.venv/Scripts/python.exe test_convert.py` → `FAIL test_window_new_books_use_saved_defaults` con `KeyError: 'defaults'`.

- [ ] **Step 3: Implementar**

En `App.__init__`, tras `self.whole_words_var`:

```python
        self.pdf_size_var = qtk.Var("A5")  # tamaño de PDF que sale primero en «PDF ▾»
```

En `fields()`: quitar `"echoes": self.echoes_var` (pasa a ser de la aplicación) y añadir `"pdf_size": self.pdf_size_var`.

Antes de `self.load_settings()` en `__init__`:

```python
        # Valores de fábrica: lo que no esté en «Libros nuevos» (o esté mal escrito) sale de aquí
        self.factory = {key: var.get() for key, var in self.fields().items() if key in options.BOOK_KEYS}
```

`import options` junto a los demás imports de módulos propios (`import convert`, `import theme`…).

`load_settings` (sustituye el bloque que aplicaba `PREFS`):

```python
        defaults = self.settings.get("defaults")
        if not isinstance(defaults, dict):  # ajustes de antes: las opciones de libro iban en la raíz
            defaults = {key: self.settings[key] for key in options.BOOK_KEYS if key in self.settings}
        self.settings["defaults"] = {key: value for key, value in defaults.items()
                                     if key in self.factory and _same_type(value, self.factory[key])}
        if not isinstance(self.settings.get("translate"), dict):
            self.settings["translate"] = {}
        self.apply_state(self.new_book_options())
        self.echoes_var.set(self.settings.get("echoes") is True)
        if self.settings.get("device") in DEVICES:
            self.device_var.set(self.settings["device"])
        self.dark_var.set(self.settings.get("dark") is True)
```

Función de módulo, junto a `_write_options`:

```python
def _same_type(value, like):
    """Valor de ajustes.json válido para una opción: el mismo tipo que su valor de fábrica (escrito a mano puede
    traer «sí» donde va true)."""
    return isinstance(value, bool) if isinstance(like, bool) else type(value) is type(like)
```

Método nuevo:

```python
    def new_book_options(self):
        """Opciones con las que abre un libro sin revisión ni serie: «Libros nuevos» sobre los valores de fábrica."""
        return {**self.factory, **self.settings.get("defaults", {})}
```

`save_settings` deja de copiar los campos del libro a la raíz:

```python
    def save_settings(self):
        self.settings["theme"] = self.theme_var.get()
        try:
            SETTINGS.parent.mkdir(parents=True, exist_ok=True)
            _write_json(SETTINGS, self.settings)
        except OSError:
            pass  # sin preferencias guardadas la próxima vez; nada más
```

En `show_book`, justo antes de `self.title_var.set(book.title)`:

```python
        self.apply_state(self.new_book_options())  # la revisión o la serie, si las hay, mandan después
```

En `poll_watch`, `convert_folder` y `omnibus`, cambiar `{name: fields[name].get() for name in PREFS}` (y su `fields = self.fields()` si queda sin uso) por `self.new_book_options()`; el de `omnibus` conserva su `options["replacements"] = self.replacements`. Borrar la constante `PREFS`.

En la spec, sustituir el párrafo «Migración» por:

```markdown
Si `ajustes.json` no tiene `defaults`, se crea con los valores de libro que ya guarda en su raíz (lo
último usado). El usuario no ve ningún cambio hasta que toque «Libros nuevos». Convertir carpeta, la
carpeta vigilada y el recopilatorio leen de `defaults`; la raíz ya no se escribe.
```

- [ ] **Step 4: Suite entera**

Run: `.venv/Scripts/python.exe test_convert.py` → sin `FAIL`. Si falla un test que contaba con que un libro nuevo heredara las opciones del anterior, ajustarlo a «Libros nuevos» (es el cambio pedido) y anotarlo en el commit.

- [ ] **Step 5: Commit**

```bash
git add app.py test_convert.py docs/superpowers/specs/2026-11-11-ventana-opciones-design.md
git commit -q -F - <<'EOF'
feat: los libros nuevos abren con los valores de «Libros nuevos»

Antes un libro sin revisión ni serie se quedaba con las opciones del libro
anterior. Los ajustes guardados de antes pasan a «Libros nuevos» sin cambios.
EOF
```

---

### Task 3: `OptionsDialog`: categorías, alcance, páginas del libro, Aceptar y Cancelar

**Files:**
- Modify: `options.py` — clase `OptionsDialog`.
- Modify: `app.py` — `App.option_choices()`, `App.open_options()`, `App.accept_options()`, `App.restore_options()`; entradas «Herramientas → Opciones…», `Ctrl+,` y botón «Opciones del libro…» en `options_row` (junto a Perfil, antes del hueco elástico).
- Test: `test_convert.py`.

**Interfaces:**
- Consumes: `options.OPTIONS`, `options.CATEGORIES`, `options.BOOK_KEYS`, `App.new_book_options()`, `App.fields()`, `App.dialog(title, modal)`, `App.update_preview()`.
- Produces:
  - `options.OptionsDialog(owner, choices: dict[str, list], profiles: dict, category: str | None = None, scope: str | None = None, on_close=None)`; atributos `win` (el `Dialog`), `scope` (`"book"` o `"new"`), `category`, `parts: dict` con `"list"`, `"search"`, `"this_book"`, `"new_books"`, `"widgets"` (clave → control de la página visible), `"notes"` (clave → `QLabel` de ayuda), `"accept"`, `"cancel"`, `"show"` (función `show(category)`), `"put"` (función `put(key, value)`).
  - `App.option_choices() -> dict` con `profile, style, separator, lang, font, pdf_size, theme, device`.
  - `App.open_options(category=None, scope=None, on_close=None) -> OptionsDialog`.
  - `App.app_values() -> dict` con `theme, device, dark, echoes, watch, translate` (copia profunda).
  - `App.accept_options(defaults: dict, values: dict)` y `App.restore_options(book: dict, values: dict)`.

- [ ] **Step 1: Tests que fallan**

```python
def test_window_options_this_book_live_and_cancel():
    """Opciones en «Este libro»: el estilo cambia el libro y la vista previa al momento; Cancelar lo deja todo
    como estaba. Aceptar lo guarda en la revisión. «Opciones del libro…» abre en «Perfil y formato»."""
    root, window = visible_window()
    window.pdf = TMP / f"opciones-{time_ns()}.pdf"
    doc = pymupdf.open()
    add_page(doc, 1, [(72, 100, "Capitulo 1: Uno", 20, "helv")])
    doc.save(window.pdf)
    window.show_book(convert.Book("Libro", "", b"", [convert.Block("heading", 1, "Capítulo 1: Uno"),
                                                    convert.Block("p", 1, "Texto.")], [0]))
    redrawn = []
    window.update_preview = lambda _=None: redrawn.append(window.style_var.get())
    before = window.style_var.get()
    other = next(s for s in convert.STYLES if s != before)
    dialog = window.open_options()
    first = (dialog.category, dialog.scope)
    dialog.parts["show"]("Aspecto")
    dialog.parts["put"]("style", other)
    live = window.style_var.get()
    dialog.parts["cancel"]()
    cancelled = window.style_var.get()
    book_button = next(b for b in window.options_row.findChildren(QPushButton) if b.text() == "Opciones del libro…")
    book_button.click()
    root.update()
    dialog = window.options_window
    opened = dialog.category
    dialog.parts["show"]("Aspecto")
    dialog.parts["put"]("style", other)
    dialog.parts["accept"]()
    saved = json.loads(app._review_path(window.pdf).read_text(encoding="utf-8"))["style"]
    window.close()
    assert first[1] == "book" and live == other and redrawn and redrawn[-1] == before, (first, live, redrawn)
    assert cancelled == before and opened == "Perfil y formato" and saved == other, (cancelled, opened, saved)


def test_window_options_new_books_without_book():
    """Sin libro abierto, «Este libro» está desactivado y lo cambiado va a «Libros nuevos» al Aceptar; Cancelar no
    guarda nada."""
    root, window = visible_window()
    dialog = window.open_options("Ilustraciones")
    disabled, scope = not dialog.parts["this_book"].isEnabled(), dialog.scope
    dialog.parts["put"]("gray", True)
    dialog.parts["cancel"]()
    after_cancel = window.settings["defaults"].get("gray")
    dialog = window.open_options("Ilustraciones")
    dialog.parts["put"]("gray", True)
    dialog.parts["accept"]()
    stored = json.loads(app.SETTINGS.read_text(encoding="utf-8"))["defaults"]["gray"]
    window.close()
    assert disabled and scope == "new" and after_cancel in (None, False) and stored is True, (disabled, scope, stored)
```

(Al principio de estos tests, `import json, app` y `from PySide6.QtWidgets import QPushButton` si el archivo no los tiene ya a nivel de módulo.)

- [ ] **Step 2: Verlo fallar**

Run: `.venv/Scripts/python.exe test_convert.py` → los dos `FAIL` con `AttributeError: 'App' object has no attribute 'open_options'`.

- [ ] **Step 3: `OptionsDialog` en `options.py`**

```python
import copy

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QPushButton, QRadioButton, QScrollArea, QSpinBox,
                               QSplitter, QVBoxLayout, QWidget)

import theme


class OptionsDialog:
    """«Opciones»: categorías a la izquierda y su página a la derecha. Lo de «Este libro» va a los Var del libro
    al momento (la vista previa se ve); lo de «Libros nuevos» y de la aplicación espera a Aceptar. Cancelar
    vuelve todo a como estaba al abrir."""

    def __init__(self, owner, choices, profiles, category=None, scope=None, on_close=None):
        self.owner, self.choices, self.profiles, self.on_close = owner, choices, profiles, on_close
        self.scope = "book" if owner.book and scope != "new" else "new"  # sin libro, solo «Libros nuevos»
        self.defaults = dict(owner.new_book_options())  # «Libros nuevos», pendiente hasta Aceptar
        self.values = owner.app_values()  # de la aplicación, pendiente hasta Aceptar (el tema se ve ya)
        self.book_before = {k: v.get() for k, v in owner.fields().items() if k in BOOK_KEYS}
        self.values_before = copy.deepcopy(self.values)
        win, column = owner.dialog("Opciones", modal=True)
        self.win = win
        win.resize(860, 560)
        win.on_reject = self.escape
        split = QSplitter(Qt.Orientation.Horizontal)
        column.addWidget(split, 1)
        left = QWidget()
        side = QVBoxLayout(left)
        side.setContentsMargins(0, 0, 0, 0)
        search = QLineEdit(placeholderText="Buscar (Ctrl+F)")
        search.setClearButtonEnabled(True)
        QShortcut(QKeySequence("Ctrl+F"), win, search.setFocus)
        listing = QListWidget()
        side.addWidget(search)
        side.addWidget(listing, 1)
        split.addWidget(left)
        right = QWidget()
        page_column = QVBoxLayout(right)
        page_column.setContentsMargins(8, 0, 0, 0)
        this_book, new_books = QRadioButton("Este libro"), QRadioButton("Libros nuevos")
        group = QButtonGroup(win)
        group.addButton(this_book)
        group.addButton(new_books)
        this_book.setEnabled(bool(owner.book))
        (this_book if self.scope == "book" else new_books).setChecked(True)
        self.scope_row = QWidget()
        scope_layout = QHBoxLayout(self.scope_row)
        scope_layout.setContentsMargins(0, 0, 0, 0)
        scope_layout.addWidget(QLabel("Aplicar a:"))
        scope_layout.addWidget(this_book)
        scope_layout.addWidget(new_books)
        scope_layout.addStretch(1)
        page_column.addWidget(self.scope_row)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        page_column.addWidget(self.scroll, 1)
        split.addWidget(right)
        split.setSizes([230, 630])
        accept, cancel = QPushButton("Aceptar"), QPushButton("Cancelar")
        accept.setObjectName("primary")
        accept.clicked.connect(self.accept)
        cancel.clicked.connect(self.cancel)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(accept)
        buttons.addWidget(cancel)
        column.addLayout(buttons)
        self.items = {}  # categoría -> QListWidgetItem
        bold = QFont(listing.font())
        bold.setBold(True)
        for header, names in CATEGORIES.items():
            item = QListWidgetItem(header.upper())
            item.setFlags(Qt.ItemFlag.NoItemFlags)  # encabezado: no se elige
            item.setFont(bold)
            listing.addItem(item)
            self.items[header] = item
            for name in names:
                self.items[name] = QListWidgetItem(f"   {name}")
                listing.addItem(self.items[name])
        self.parts = {"list": listing, "search": search, "this_book": this_book, "new_books": new_books,
                      "widgets": {}, "notes": {}, "accept": self.accept, "cancel": self.cancel, "show": self.show,
                      "put": self.put}
        listing.currentItemChanged.connect(lambda item, _: item and self.show(item.text().strip()))
        this_book.toggled.connect(lambda on: self.set_scope("book" if on else "new"))
        self.category = None
        self.show(category if category in self.items and category not in CATEGORIES else CATEGORIES[BOOK][0])
        win.show()

    def is_book(self, category=None):
        return (category or self.category) in CATEGORIES[BOOK]

    def get(self, key):
        if key in BOOK_KEYS:
            return self.owner.fields()[key].get() if self.scope == "book" else self.defaults.get(key)
        return self.values[key]

    def store(self, key, value):
        if key in BOOK_KEYS and self.scope == "book":
            self.owner.fields()[key].set(value)
        elif key in BOOK_KEYS:
            self.defaults[key] = value
        else:
            self.values[key] = value

    def put(self, key, value):
        """Un cambio del usuario. El perfil arrastra sus opciones; tocar una opción que fija el perfil lo deja en
        «Personalizado»."""
        changes = {key: value}
        profile = self.get("profile")
        if key == "profile":
            changes.update(self.profiles.get(value, {}))
        elif key in self.profiles.get(profile, {}) and self.profiles[profile][key] != value:
            changes["profile"] = "Personalizado"
        for name, new in changes.items():
            self.store(name, new)
        if key in BOOK_KEYS and self.scope == "book":
            self.owner.update_preview()
        if len(changes) > 1:
            self.show(self.category)  # las casillas del perfil cambiaron

    def set_scope(self, scope):
        self.scope = scope
        self.show(self.category)

    def show(self, category):
        """Página de una categoría, hecha de nuevo con los valores del alcance elegido."""
        self.category = category
        self.owner.settings["options_page"] = category
        listing = self.parts["list"]
        listing.blockSignals(True)
        listing.setCurrentItem(self.items[category])
        listing.blockSignals(False)
        self.scope_row.setVisible(self.is_book())
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 4, 12, 4)
        layout.setSpacing(4)
        title = QLabel(category)
        title.setObjectName("title")
        layout.addWidget(title)
        self.parts["widgets"], self.parts["notes"] = {}, {}
        for option in OPTIONS:
            if option.category == category:
                self.add_option(layout, option)
        layout.addStretch(1)
        self.scroll.setWidget(page)  # la página anterior se borra con su scroll

    def add_option(self, layout, option):
        """Control de una opción y, debajo, su ayuda en gris."""
        key, value = option.key, self.get(option.key)
        if option.kind == "check":
            widget = QCheckBox(option.label)
            widget.setChecked(bool(value))
            widget.toggled.connect(lambda on, key=key: self.put(key, on))
            row = widget
        else:
            if option.kind == "number":
                widget = QSpinBox()
                widget.setRange(400, 4000)
                widget.setSingleStep(100)
                widget.setValue(int(value))
                widget.valueChanged.connect(lambda n, key=key: self.put(key, n))
            else:
                widget = QComboBox()
                values = list(self.choices.get(key, []))
                if option.kind == "font":
                    values = self.font_choices(value)
                widget.addItems(values)
                widget.setEditable(option.kind == "language")
                widget.setCurrentText(str(value))
                widget.setMinimumContentsLength(18)
                if option.kind == "font":
                    widget.activated.connect(lambda _, w=widget: self.pick_font(w))
                elif option.kind == "language":
                    widget.currentTextChanged.connect(lambda text, key=key: self.put(key, text.strip()))
                else:
                    widget.activated.connect(lambda _, w=widget, key=key: self.put(key, w.currentText()))
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            line.addWidget(QLabel(f"{option.label}:"))
            line.addWidget(widget)
            line.addStretch(1)
        note = QLabel(option.help)
        note.setObjectName("muted")
        note.setWordWrap(True)
        note.setContentsMargins(22 if option.kind == "check" else 0, 0, 0, 8)
        layout.addWidget(row)
        layout.addWidget(note)
        self.parts["widgets"][key], self.parts["notes"][key] = widget, note

    def font_choices(self, current):
        """Letras incrustadas, la propia si la hay, y «Otra…» al final para elegir un archivo."""
        values = [name for name in self.choices["font"] if name != "Otra…"]
        if current and current not in values:
            values.append(current)
        return values + ["Otra…"]

    def pick_font(self, widget):
        """«Otra…» pide un archivo .ttf u .otf; sin elegir ninguno se queda la letra de antes."""
        if widget.currentText() != "Otra…":
            self.put("font", widget.currentText())
            return
        path = self.owner.ask_font_file()
        if path:
            self.store("font_file", path)
            self.put("font", Path(path).name)
        self.show(self.category)

    def accept(self):
        self.owner.accept_options(self.defaults, self.values)
        self.close()

    def cancel(self):
        self.owner.restore_options(self.book_before, self.values_before)
        self.close()

    def escape(self):
        """Esc o la ✕: con texto en el buscador lo vacía; si no, como Cancelar."""
        if self.parts["search"].text():
            self.parts["search"].clear()
        else:
            self.cancel()

    def close(self):
        self.win.on_reject = None
        self.win.close()
        if self.on_close:
            self.on_close()
```

(`from pathlib import Path` arriba del módulo.)

- [ ] **Step 4: Métodos de `App`**

```python
    def option_choices(self):
        """Valores de las listas de «Opciones»."""
        return {"profile": list(PROFILES), "style": list(convert.STYLES), "separator": convert.SEPARATOR_CHOICES,
                "lang": LANGUAGES, "font": FONT_CHOICES, "pdf_size": list(convert.PDF_SIZES),
                "theme": theme.CHOICES, "device": list(DEVICES)}

    def open_options(self, category=None, scope=None, on_close=None):
        """«Opciones»: en category, o en la última vista."""
        self.options_window = options.OptionsDialog(self, self.option_choices(), PROFILES,
                                                    category or self.settings.get("options_page"), scope, on_close)
        return self.options_window

    def app_values(self):
        """Lo de «La aplicación» en Opciones, copiado: se edita sin tocar nada hasta Aceptar."""
        return {"theme": self.theme_var.get(), "device": self.settings.get("device", self.device_var.get()),
                "dark": self.settings.get("dark") is True, "echoes": self.echoes_var.get(), "watch": self.watch,
                "translate": copy.deepcopy(self.settings.get("translate") or {})}

    def accept_options(self, defaults, values):
        """Aceptar en Opciones: «Libros nuevos» y la aplicación a ajustes.json, el libro a su revisión."""
        self.settings["defaults"] = dict(defaults)
        self.settings.update(device=values["device"], dark=values["dark"], echoes=values["echoes"])
        translation = self.settings.setdefault("translate", {})
        translation.clear()  # el mismo dict: Traducir, si está abierto, lo tiene y ve lo nuevo
        translation.update(values["translate"])
        if values["echoes"] != self.echoes_var.get():
            self.echoes_var.set(values["echoes"])
            self.update_quality()
        if values["theme"] != self.theme_var.get():
            self.theme_var.set(values["theme"])
            self.apply_theme()
        if values["watch"] != self.watch:
            self.set_watch(values["watch"])
        self.save_settings()
        self.save_review()

    def restore_options(self, book, values):
        """Cancelar en Opciones: el libro y el tema vuelven a como estaban al abrirla."""
        fields = self.fields()
        changed = [key for key, value in book.items() if fields[key].get() != value]
        for key in changed:
            fields[key].set(book[key])
        if self.theme_var.get() != values["theme"]:
            self.theme_var.set(values["theme"])
            self.apply_theme()
        if changed and self.book:
            self.update_preview()

    def ask_font_file(self):
        return filedialog.askopenfilename(filetypes=[("Fuente", "*.ttf *.otf")])
```

`import copy` arriba si no está. Entradas:
- En el menú `tools`, antes de `tools.addSeparator()` que precede a «Tema»: `tools.addAction("Opciones…  (Ctrl+,)", lambda: self.open_options())`.
- Tras `QShortcut(QKeySequence("Ctrl+F"), root, self.find)`: `QShortcut(QKeySequence("Ctrl+,"), root, lambda: self.open_options())`.
- En `options_row`, tras el combo de Perfil: `_button("Opciones del libro…", lambda: self.open_options("Perfil y formato", "book"))`.

- [ ] **Step 5: Suite entera**

Run: `.venv/Scripts/python.exe test_convert.py` → sin `FAIL`.

- [ ] **Step 6: Commit**

```bash
git add options.py app.py test_convert.py
git commit -q -F - <<'EOF'
feat: ventana «Opciones» con las opciones del libro y de libros nuevos

Herramientas → Opciones…, Ctrl+, y «Opciones del libro…» la abren. Lo de
«Este libro» se ve ya en la vista previa; Cancelar lo deshace.
EOF
```

---

### Task 4: Buscador, notas del perfil, «Usar también para libros nuevos» y aviso de letra perdida

**Files:**
- Modify: `options.py` — `OptionsDialog.filter`, `show`, `add_option`.
- Test: `test_convert.py`.

**Interfaces:**
- Consumes: `options.find`, `OptionsDialog.parts`, `theme.colors(dark)["sel"]`, `owner.dark`.
- Produces: `parts["copy"]` (botón «Usar también para libros nuevos»), `parts["empty"]` (el aviso sin coincidencias), `parts["font_error"]` (`QLabel` rojo o `None`).

- [ ] **Step 1: Tests que fallan**

```python
def test_window_options_search_profile_and_missing_font():
    """Buscar «guion» deja Texto y PDF y resalta la opción; sin coincidencias, un aviso. El perfil Kindle marca los
    guiones con su nota; desmarcarlos pasa a «Personalizado». «Usar también para libros nuevos» copia la página.
    Una letra propia sin archivo avisa en rojo en la página, sin ventana."""
    root, window = visible_window()
    window.show_book(convert.Book("Libro", "", b"", [convert.Block("p", 1, "Texto.")], []))
    dialog = window.open_options("Perfil y formato", "book")
    dialog.parts["search"].setText("guion")
    listing = dialog.parts["list"]
    shown = [listing.item(n).text().strip() for n in range(listing.count()) if not listing.item(n).isHidden()]
    dialog.parts["search"].setText("zzz")
    empty = not dialog.parts["empty"].isHidden()
    dialog.parts["search"].clear()
    dialog.parts["put"]("profile", "Kindle")
    dialog.parts["show"]("Texto")
    note = dialog.parts["notes"]["hyphenate"].text()
    dialog.parts["put"]("hyphenate", False)
    profile = window.profile_var.get()
    dialog.parts["copy"].click()
    copied = dialog.defaults["hyphenate"]
    window.font_file_var.set(str(TMP / "no-existe.ttf"))
    window.font_var.set("no-existe.ttf")
    dialog.parts["show"]("Aspecto")
    error = dialog.parts["font_error"]
    dialog.parts["cancel"]()
    window.close()
    assert shown == ["EL LIBRO", "Texto", "PDF"], shown
    assert empty and "Fijada por el perfil Kindle" in note and profile == "Personalizado", (empty, note, profile)
    assert copied is False and error is not None and "No se encuentra" in error.text(), (copied, error)
```

- [ ] **Step 2: Verlo fallar**

Run: `.venv/Scripts/python.exe test_convert.py` → `FAIL` con `KeyError: 'empty'`.

- [ ] **Step 3: Implementar**

En `__init__`, tras crear `listing`: aviso vacío y conexión del buscador.

```python
        empty = QLabel("")
        empty.setObjectName("muted")
        empty.setWordWrap(True)
        empty.hide()
        side.addWidget(empty)
        search.textChanged.connect(lambda _: self.filter())
```

y en `self.parts`: `"empty": empty, "copy": None, "font_error": None`.

```python
    def filter(self):
        """Solo las categorías con alguna opción que coincide; la página elegida resalta las que coinciden."""
        text = self.parts["search"].text()
        hits = {o.category for o in find(text)} if text.strip() else set(self.items) - set(CATEGORIES)
        for header, names in CATEGORIES.items():
            self.items[header].setHidden(not any(name in hits for name in names))
            for name in names:
                self.items[name].setHidden(name not in hits)
        self.parts["empty"].setText(f"Ninguna opción contiene «{text.strip()}»" if not hits else "")
        self.parts["empty"].setVisible(not hits)
        if hits and self.category not in hits:
            self.show(next(name for names in CATEGORIES.values() for name in names if name in hits))
        else:
            self.show(self.category)
```

Al final de `add_option`, nota del perfil y resaltado:

```python
        fixed = self.profiles.get(self.get("profile"), {})
        if key in fixed and key != "profile":
            note.setText(f"{option.help} Fijada por el perfil {self.get('profile')}.")
        text = self.parts["search"].text().strip()
        if text and option in find(text):
            row.setAutoFillBackground(True)
            row.setStyleSheet(f"background: {theme.colors(self.owner.dark)['sel']};")
```

Y aviso de letra (dentro de `add_option`, rama `font`, tras crear el combo):

```python
                if option.kind == "font":
                    lost = value not in self.choices["font"] and not Path(str(self.get("font_file"))).is_file()
                    if lost:
                        error = QLabel("No se encuentra el archivo de la letra: el EPUB usará la del lector.")
                        error.setObjectName("warn")
                        self.parts["font_error"] = error
```

y tras `layout.addWidget(note)`: `if option.kind == "font" and self.parts["font_error"]: layout.addWidget(self.parts["font_error"])`. En `show`, antes del bucle: `self.parts["font_error"] = None`.

Botón «Usar también para libros nuevos» al final de `show`, antes del `addStretch`, solo con `self.is_book() and self.scope == "book"`:

```python
        if self.is_book() and self.scope == "book":
            copy_btn = QPushButton("Usar también para libros nuevos")
            copy_btn.setToolTip("Copia los valores de esta página a «Libros nuevos»")
            keys = [o.key for o in OPTIONS if o.category == category] + (["font_file"] if category == "Aspecto" else [])
            copy_btn.clicked.connect(lambda: self.defaults.update({k: self.owner.fields()[k].get() for k in keys}))
            layout.addWidget(copy_btn, 0, Qt.AlignmentFlag.AlignLeft)
            self.parts["copy"] = copy_btn
        else:
            self.parts["copy"] = None
```

Comprobar que `theme.colors(dark)` tiene la clave `"sel"` (la usa la QSS de `QToolButton#tool:checked`); si se llama distinto, usar esa.

- [ ] **Step 4: Suite entera** → sin `FAIL`.

- [ ] **Step 5: Commit**

```bash
git add options.py test_convert.py
git commit -q -F - <<'EOF'
feat: buscador y ayudas en «Opciones», notas del perfil y aviso de letra perdida
EOF
```

---

### Task 5: Páginas de la aplicación: Apariencia, Vista previa, Informe de calidad y Carpeta vigilada

**Files:**
- Modify: `options.py` — `add_option` para `theme` (en vivo), `folder`.
- Modify: `app.py` — nada nuevo salvo lo de la Tarea 3 (`accept_options` ya guarda `device`, `dark`, `echoes`, `watch`).
- Test: `test_convert.py`.

**Interfaces:**
- Consumes: `App.apply_theme()`, `App.set_watch(folder)`, `filedialog.askdirectory` (vía `owner.ask_folder()`).
- Produces: `App.ask_folder() -> str`; `parts["watch_error"]`.

- [ ] **Step 1: Tests que fallan**

```python
def test_window_options_app_pages():
    """Tema: se ve al momento y Cancelar lo devuelve. Aceptar guarda dispositivo, modo oscuro y ecos, que la ventana
    usa al abrir la próxima vez. Carpeta vigilada: elegir y aceptar la vigila; una que ya no existe avisa en rojo."""
    import app
    root, window = visible_window()
    dialog = window.open_options("Apariencia")
    dialog.parts["put"]("theme", "Oscuro")
    live = window.dark
    dialog.parts["cancel"]()
    back = window.dark
    folder = TMP / f"vigilada-{time_ns()}"
    folder.mkdir()
    window.ask_folder = lambda: str(folder)
    dialog = window.open_options("Carpeta vigilada")
    next(b for b in dialog.win.findChildren(QPushButton) if b.text() == "Elegir…").click()
    dialog.parts["put"]("device", "Tablet")
    dialog.parts["put"]("dark", True)
    dialog.parts["put"]("echoes", True)
    dialog.parts["accept"]()
    watching, echoes = window.watch, window.echoes_var.get()
    folder.rmdir()
    dialog = window.open_options("Carpeta vigilada")
    lost = dialog.parts["watch_error"]
    dialog.parts["cancel"]()
    settings = app.SETTINGS
    window.close()
    root, again = visible_window(settings)
    reopened = (again.device_var.get(), again.dark_var.get(), again.echoes_var.get())
    again.close()
    assert live and not back and watching == str(folder) and echoes, (live, back, watching, echoes)
    assert lost is not None and "ya no existe" in lost.text(), lost
    assert reopened == ("Tablet", True, True), reopened
```

- [ ] **Step 2: Verlo fallar** → `FAIL` (`Elegir…` no existe).

- [ ] **Step 3: Implementar**

En `OptionsDialog.put`, tras guardar los cambios:

```python
        if key == "theme":  # el tema se ve al momento; Cancelar lo devuelve (restore_options)
            self.owner.theme_var.set(value)
            self.owner.apply_theme()
```

En `add_option`, rama nueva antes de la de combos, para `folder`:

```python
        if option.kind == "folder":
            path = QLabel(value or "Ninguna")
            path.setWordWrap(True)
            pick = QPushButton("Elegir…")
            stop = QPushButton("Dejar de vigilar")
            stop.setEnabled(bool(value))
            pick.clicked.connect(lambda: (folder := self.owner.ask_folder()) and (self.put(key, folder),
                                                                                     self.show(self.category)))
            stop.clicked.connect(lambda: (self.put(key, ""), self.show(self.category)))
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            line.addWidget(path, 1)
            line.addWidget(pick)
            line.addWidget(stop)
            widget = path
            self.parts["watch_error"] = None
            if value and not Path(value).is_dir():
                self.parts["watch_error"] = QLabel("La carpeta ya no existe: elige otra o deja de vigilarla.")
                self.parts["watch_error"].setObjectName("warn")
```

y tras añadir la nota: `if option.kind == "folder" and self.parts["watch_error"]: layout.addWidget(self.parts["watch_error"])`. La rama `check`/`number`/combo pasa a `elif`.

En `App`:

```python
    def ask_folder(self):
        return filedialog.askdirectory(initialdir=self.settings.get("folder"), title="Carpeta que vigilar")
```

- [ ] **Step 4: Suite entera** → sin `FAIL`.

- [ ] **Step 5: Commit**

```bash
git add options.py app.py test_convert.py
git commit -q -F - <<'EOF'
feat: tema, vista previa, ecos y carpeta vigilada en «Opciones»
EOF
```

---

### Task 6: Traducción en «Opciones»; Traducir muestra el motor y enlaza

**Files:**
- Modify: `options.py` — página `translate`.
- Modify: `app.py` — `translate_book`: `prefs` es `self.settings["translate"]` (mismo objeto); fila de motor, modelo, URL y clave ocultas; resumen «Motor: … · …» y botón «Motor y claves…»; mensajes de clave que falta.
- Test: `test_convert.py` — mover la parte de «Buscar modelos» y la visibilidad de la clave del test de Traducir (~líneas 8040-8061) a uno de Opciones.

**Interfaces:**
- Consumes: `translate.ENGINES`, `translate.LANGS`, `translate.models(name, key, url)`, `translate.protect`, `translate.unprotect`.
- Produces: en `OptionsDialog.parts`: `"engine"`, `"model"`, `"base"`, `"key"`, `"eye"`, `"src"`, `"dst"`, `"models"` (función buscar modelos), `"status"` (`QLabel`). En Traducir `win.parts`: `"summary"` (`QLabel`), `"engine_btn"` (botón «Motor y claves…»).

- [ ] **Step 1: Tests que fallan**

```python
def test_window_options_translation_page():
    """Traducción en Opciones: motor, modelo buscado con la clave, clave oculta con 👁; al Aceptar, Traducir abierto
    usa lo nuevo y al cerrarse no lo pisa."""
    import translate
    root, window = visible_window()
    window.show_book(convert.Book("Libro", "", b"", [convert.Block("p", 1, "Hello there.")], []))
    original = translate.models
    translate.models = lambda name, key, url: ["deepseek-chat", "deepseek-reasoner"]
    try:
        window.translate_book()
        tr = window.translate_window.parts
        dialog = window.open_options("Traducción")
        parts = dialog.parts
        parts["engine"].setCurrentIndex(parts["engine"].findText("DeepSeek"))
        parts["engine"].activated.emit(parts["engine"].currentIndex())
        hidden = parts["key"].echoMode() == QLineEdit.EchoMode.Password
        parts["key"].setText("sk-nueva")
        parts["models"]()
        end = time.time() + 10
        while parts["model"].count() < 2 and time.time() < end:
            root.update()
            time.sleep(0.05)
        found = [parts["model"].itemText(n) for n in range(parts["model"].count())]
        parts["eye"].click()
        shown = parts["key"].echoMode() == QLineEdit.EchoMode.Normal
        parts["accept"]()
        root.update()
        summary, used = tr["summary"].text(), (tr["engine"].currentText(), tr["key"].text())
        tr["close"]()
        stored = translate.unprotect(window.settings["translate"]["keys"]["DeepSeek"])
    finally:
        translate.models = original
    window.close()
    assert hidden and shown and found == ["deepseek-chat", "deepseek-reasoner"], (hidden, shown, found)
    assert "DeepSeek" in summary and used == ("DeepSeek", "sk-nueva") and stored == "sk-nueva", (summary, used, stored)
```

(`from PySide6.QtWidgets import QLineEdit` si no está.) Borrar del test de Traducir que hoy cubre «Buscar modelos» las líneas que llaman a `parts["models"]()` y comprueban `parts["key"].isVisible()` / `parts["model"].isVisible()`: eso ya no se ve en Traducir. El resto de tests que hacen `parts["engine"].setCurrentIndex(...)` y `parts["key"].setText(...)` sigue valiendo (controles ocultos).

- [ ] **Step 2: Verlo fallar** → `FAIL` con `KeyError: 'engine'`.

- [ ] **Step 3: Página `translate` en `options.py`**

```python
import queue
import threading

import shiboken6

import qtk
import translate
```

Rama en `add_option` para `option.kind == "translate"`: delega en `self.translation_page(layout, option)` y `return`.

```python
    def translation_page(self, layout, option):
        """Motor, modelo, URL y clave del motor elegido, e idiomas con los que empieza Traducir. La clave se guarda
        cifrada (translate.protect) en el dict de la aplicación; todo espera a Aceptar."""
        data = self.values["translate"]
        for name in ("models", "keys"):
            if not isinstance(data.get(name), dict):
                data[name] = {}
        engine, model, base, key = QComboBox(), QComboBox(), QLineEdit(), QLineEdit()
        src, dst, status = QComboBox(), QComboBox(), QLabel("")
        engine.addItems(list(translate.ENGINES))
        engine.setCurrentIndex(max(0, engine.findText(str(data.get("engine", "")))))
        model.setEditable(True)
        model.setMinimumContentsLength(22)
        key.setEchoMode(QLineEdit.EchoMode.Password)
        eye = QPushButton("👁")
        eye.setToolTip("Mostrar u ocultar la clave")
        eye.clicked.connect(lambda: key.setEchoMode(QLineEdit.EchoMode.Normal if key.echoMode()
                                                    == QLineEdit.EchoMode.Password else QLineEdit.EchoMode.Password))
        models_btn = QPushButton("Buscar modelos")
        for code, name in translate.LANGS.items():
            src.addItem(name, code)
            if code != "auto":
                dst.addItem(name, code)
        src.setCurrentIndex(max(0, src.findData(data.get("src", "auto"))))
        dst.setCurrentIndex(max(0, dst.findData(data.get("dst", "es"))))
        status.setObjectName("muted")
        status.setWordWrap(True)
        rows = {}

        def labelled(text, *widgets):
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            line.addWidget(QLabel(text))
            for w in widgets:
                line.addWidget(w)
            line.addStretch(1)
            layout.addWidget(row)
            return row

        labelled("Motor:", engine)
        rows["model"] = labelled("Modelo:", model, models_btn)
        rows["base"] = labelled("URL:", base)
        rows["key"] = labelled("Clave:", key, eye)
        labelled("Traducir de:", src, QLabel("a:"), dst)
        layout.addWidget(status)
        note = QLabel(option.help)
        note.setObjectName("muted")
        note.setWordWrap(True)
        layout.addWidget(note)

        def keep():
            """Lo escrito, al dict del motor elegido; la clave, cifrada (sin cifrar no se guarda)."""
            name = engine.currentText()
            data.update(engine=name, src=src.currentData(), dst=dst.currentData())
            data["models"][name] = model.currentText().strip()
            try:
                data["keys"][name] = translate.protect(key.text().strip())
            except OSError:
                data["keys"].pop(name, None)
            if translate.ENGINES[name].get("custom"):
                data["base"] = base.text().strip()

        def show_engine():
            name = engine.currentText()
            info = translate.ENGINES[name]
            model.clear()
            model.setEditText(data["models"].get(name) or info.get("model", ""))
            key.setText(translate.unprotect(data["keys"].get(name, "")))
            base.setText(data.get("base") or info.get("base", ""))
            rows["model"].setVisible("model" in info)
            rows["base"].setVisible(bool(info.get("custom")))
            rows["key"].setVisible(info["kind"] != "google")
            key.setPlaceholderText("Opcional" if info.get("custom") else "Clave API")

        def find_models():
            """La lista de modelos de la API, en un hilo: sin conexión tardaría en fallar."""
            name, info = engine.currentText(), translate.ENGINES[engine.currentText()]
            if info.get("key") and not key.text().strip():
                status.setText(f"Falta la clave de {name}: sin ella la API no da la lista.")
                return
            found, url = queue.Queue(), base.text().strip() if info.get("custom") else ""

            def ask():
                try:
                    found.put(translate.models(name, key.text().strip(), url))
                except Exception as e:  # TranslateError ya viene en español
                    found.put(str(e) or type(e).__name__)

            models_btn.setEnabled(False)
            models_btn.setText("Buscando…")
            threading.Thread(target=ask, daemon=True).start()

            def wait():
                if not shiboken6.isValid(models_btn):  # Opciones se cerró o cambió de página
                    return
                try:
                    result = found.get_nowait()
                except queue.Empty:
                    qtk.after(200, wait)
                    return
                models_btn.setText("Buscar modelos")
                models_btn.setEnabled(True)
                if isinstance(result, str) or not result:
                    status.setText(result or f"{name} no devolvió ningún modelo.")
                    return
                current = model.currentText().strip()
                model.clear()
                model.addItems(result)
                model.setEditText(current or result[0])
                keep()
                status.setText(f"{name} ofrece {len(result)} modelos: elige uno en «Modelo».")

            qtk.after(200, wait)

        engine.activated.connect(lambda _: (keep(), show_engine()))
        for signal in (model.currentTextChanged, base.textChanged, key.textChanged):
            signal.connect(lambda _: keep())
        src.activated.connect(lambda _: keep())
        dst.activated.connect(lambda _: keep())
        models_btn.clicked.connect(find_models)
        show_engine()
        self.parts.update(engine=engine, model=model, base=base, key=key, eye=eye, src=src, dst=dst,
                          models=find_models, status=status)
```

Ojo: `engine.activated` llama a `keep()` antes de `show_engine()`: guardaría lo escrito en el motor de antes con el nombre del nuevo. Guardar el nombre anterior: `previous = {"name": engine.currentText()}`; `keep()` usa `previous["name"]` para modelos, claves y base; `show_engine()` pone `previous["name"] = engine.currentText()` al final; `data["engine"]` siempre el visible.

- [ ] **Step 4: Traducir**

En `translate_book`:
- `prefs = self.settings.setdefault("translate", {})`; si no es dict, `self.settings["translate"] = prefs = {}`. Así Opciones y Traducir comparten el mismo dict (`accept_options` lo vacía y rellena en su sitio).
- Fila 1: quitar `QLabel("Motor:"), engine`; fila 2 (modelo, URL, clave): no se añade. Los controles siguen existiendo (los usan `run`, `remember`, `show_engine` y los tests) pero ocultos: `for w in (engine, model, models_btn, base, api_key, model_label, base_label, key_label): w.hide()` justo después de crearlos, y en `show_engine` dejar de hacer `setVisible` (borrar ese bucle `for widget, shown in …`).
- Añadir `summary = _label("", "muted")` y `engine_btn = _button("Motor y claves…", lambda: self.open_options("Traducción", on_close=reload))` en la fila 1, delante del hueco elástico.

```python
        def describe():
            name = engine.currentText()
            info = translate.ENGINES[name]
            missing = info.get("key") and not api_key.text().strip()
            summary.setText(f"Motor: {name}" + (f" · {model.currentText().strip()}" if "model" in info else "")
                            + (" · falta la clave" if missing else ""))

        def reload():
            """Tras «Opciones»: el motor y la clave de las preferencias, que acaban de cambiar."""
            if not shiboken6.isValid(win):
                return
            engine.setCurrentIndex(max(0, engine.findText(str(prefs.get("engine", "")))))
            show_engine()
            describe()
```

- Llamar a `describe()` al final de `show_engine()`.
- En los dos sitios que hacen `messagebox.showerror(... "Falta la clave" ...)` seguido de `api_key.setFocus()` (líneas ~5318 y ~5375 y ~5809), cambiar el `setFocus()` por `self.open_options("Traducción", on_close=reload)` tras el aviso, y que el texto acabe en «Ponla en «Motor y claves…».».
- En `win.parts`, añadir `"summary": summary, "engine_btn": engine_btn`.

- [ ] **Step 5: Suite entera** → sin `FAIL`. Los tests de Traducir con DeepSeek y clave deben seguir pasando sin cambios.

- [ ] **Step 6: Commit**

```bash
git add options.py app.py test_convert.py
git commit -q -F - <<'EOF'
feat: motor, modelo y claves de traducción en «Opciones»

Traducir muestra el motor elegido y «Motor y claves…» lleva a Opciones; al
volver usa lo nuevo.
EOF
```

---

### Task 7: Tamaño de PDF preferido primero en «PDF ▾»

**Files:**
- Modify: `app.py` — el menú `pdf_menu` se rehace en `aboutToShow`.
- Test: `test_convert.py`.

**Interfaces:**
- Consumes: `App.pdf_size_var`, `convert.PDF_SIZES`.
- Produces: `App.fill_pdf_menu()`.

- [ ] **Step 1: Test que falla**

```python
def test_window_pdf_menu_puts_preferred_size_first():
    """El tamaño preferido en Opciones sale primero en «PDF ▾», con «(preferido)»."""
    root, window = visible_window()
    window.pdf_size_var.set("A4")
    window.fill_pdf_menu()
    first = window.pdf_btn.menu().actions()[0].text()
    window.close()
    assert first.startswith("Generar PDF (A4") and "(preferido)" in first, first
```

- [ ] **Step 2: Verlo fallar** → `AttributeError: … fill_pdf_menu`.

- [ ] **Step 3: Implementar**

Sustituir el bucle que llena `pdf_menu` por `pdf_menu.aboutToShow.connect(self.fill_pdf_menu)` y guardar `self.pdf_menu = pdf_menu`; quitar la casilla «No partir palabras al final de línea» (vive en Opciones → PDF).

```python
    def fill_pdf_menu(self):
        """«PDF ▾»: el tamaño preferido (Opciones → PDF) primero."""
        labels = {"A5": "A5, para leer o imprimir", "e-reader 6″": "e-reader 6″", "A4": "A4"}
        preferred = self.pdf_size_var.get()
        self.pdf_menu.clear()
        for size in sorted(labels, key=lambda s: s != preferred):
            mark = " (preferido)" if size == preferred else ""
            self.pdf_menu.addAction(f"Generar PDF ({labels[size]}){mark}…", lambda size=size: self.generate_pdf(size))
```

Llamar a `self.fill_pdf_menu()` una vez tras crear el menú (para que tenga acciones antes del primer clic y en tests). En `test_write_pdf_whole_words_never_splits_a_word`, la parte de ventana marca la opción con `window.whole_words_var.set(True)` en vez de buscar la casilla del menú, y la línea `assert "palabras" in label` se borra.

- [ ] **Step 4: Suite entera** → sin `FAIL`.

- [ ] **Step 5: Commit**

```bash
git add app.py test_convert.py
git commit -q -F - <<'EOF'
feat: el tamaño de PDF preferido sale primero en «PDF ▾»
EOF
```

---

### Task 8: Quitar los controles viejos y enlazar desde donde estaban

**Files:**
- Modify: `app.py` — barra de abajo, «Títulos ▾», panel «Editar datos», Herramientas, Informe de calidad, `apply_theme`, `apply_state`, `font_changed`, `show_font`.
- Modify: `test_convert.py` — tests que usan `font_box`, la casilla de ecos o el combo de idioma.

**Interfaces:**
- Consumes: `App.open_options`.
- Produces: en el Informe de calidad, botón «Opciones del informe…».

- [ ] **Step 1: Test que falla**

```python
def test_window_options_are_only_in_options():
    """Los ajustes ya no están repartidos: la barra deja Perfil, «Opciones del libro…», PDF y Generar; no hay menú
    «Opciones ▾», ni Tema en Herramientas, ni idioma en «Editar datos». El informe lleva a sus opciones."""
    from PySide6.QtWidgets import QComboBox, QToolButton
    root, window = visible_window()
    window.show_book(convert.Book("Libro", "", b"", [convert.Block("p", 1, "Texto.")], []))
    bar = [b.text() for b in window.options_row.findChildren(QToolButton)]
    combos = window.options_row.findChildren(QComboBox)
    menus = [a.text() for b in window.root.findChildren(QToolButton) if b.menu() for a in b.menu().actions()]
    data_combos = [c for c in window.data_panel.findChildren(QComboBox)]
    window.quality()
    report = root.dialogs()[-1]
    link = [b.text() for b in report.findChildren(QPushButton) if b.text() == "Opciones del informe…"]
    window.close()
    assert "Opciones ▾" not in bar and len(combos) == 1, (bar, len(combos))
    assert "Tema" not in menus and "Opciones…  (Ctrl+,)" in menus, menus
    assert not data_combos and link, (data_combos, link)
```

- [ ] **Step 2: Verlo fallar** → `FAIL` (hay cuatro combos en la barra).

- [ ] **Step 3: Quitar y enlazar**

- `options_row`: queda `_row(_label("Perfil:"), _combo(self.profile_var, list(PROFILES), 15, changed=self.apply_profile), _button("Opciones del libro…", …), 1, self.pdf_btn, self.gen_btn)`. Borrar el menú `extras`, `self.font_box` y su creación.
- `book_data()`: quitar `self.lang_var` de la fila de la rejilla (y su rama `if var is self.lang_var`); quitar la acción marcable «Corregir las mayúsculas al abrir…» y su separador del menú «Títulos ▾».
- Herramientas: borrar el submenú «Tema», `QActionGroup`, `self.theme_actions`; en `apply_theme` borrar las dos líneas de `theme_actions`. «Vigilar una carpeta…» pasa a `lambda: self.open_options("Carpeta vigilada")`; `watch_folder` se borra si nadie más la llama (grep).
- `apply_state`: la rama de letra propia ya no llama a `show_font` (no hay combo): `if self.font_file_var.get() and path.name == font and path.is_file(): pass`; el resto igual. Borrar `font_changed`, `show_font` y `self.font_last` si grep no encuentra otros usos.
- Informe de calidad: quitar `echoes` (la casilla) de la fila; en su lugar `_button("Opciones del informe…", lambda: self.open_options("Informe de calidad", on_close=lambda: shiboken6.isValid(win) and fill()))`.
- Tests: el de la letra propia (~6058, usaba `window.font_box`) pasa a usar Opciones (`dialog = window.open_options("Aspecto", "book")`, `window.ask_font_file = lambda: str(fuente)`, elegir «Otra…» con `widgets["font"].setCurrentText("Otra…")` y `widgets["font"].activated.emit(0)`), con las mismas comprobaciones de `write_options()["font"]`. El de ecos (~5617) cambia la casilla por `window.echoes_var.set(True)` seguido de `window.update_quality()`.

- [ ] **Step 4: Suite entera** → sin `FAIL` ni `aviso no previsto`.

- [ ] **Step 5: Commit**

```bash
git add app.py test_convert.py
git commit -q -F - <<'EOF'
feat: los ajustes solo viven en «Opciones»

La barra de abajo deja Perfil, «Opciones del libro…», PDF y Generar. Tema,
idioma, mayúsculas al abrir y ecos se van a Opciones; el Informe de calidad y
Vigilar una carpeta llevan a su página.
EOF
```

---

### Task 9: Verificar en la ventana real, build y merge

**Files:**
- Create (scratchpad, no se versiona): guion de `/verificar-ventana`.

- [ ] **Step 1: `/verificar-ventana`** con un guion que: abre un libro real, abre «Opciones del libro…», recorre cada categoría con `foto()`, busca «guion», cambia Estilo y Cancela, cambia Perfil a Kindle y Acepta; repite en tema Oscuro; y `--escala 1.25`. Mirar las fotos: nada cortado, contraste, la ventana cabe en 1366 px y en 1093 px.
- [ ] **Step 2:** `/regresion-series` no hace falta (`convert.py` no cambia). Suite entera sin `FAIL`.
- [ ] **Step 3:** `/build-exe` (comprobar que `options.py` entra en el .exe: el arranque de prueba abre la ventana; si no, añadir `options` a `hiddenimports` del spec).
- [ ] **Step 4:** `/commit`: merge a `master` con `merge: ventana «Opciones» que reúne todos los ajustes`.
