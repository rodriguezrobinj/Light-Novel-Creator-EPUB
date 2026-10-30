# Salida para lectores Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Perfiles Kindle y Kobo, salida KEPUB, tamaño real con aviso de Send to Kindle y copia al e-reader USB.

**Architecture:** `convert.write_epub` gana `kepub=False`: las secciones pasan por `_kepub` (regex sobre el XHTML propio) al escribirse en el zip. `app.py` añade la opción `kepub` a perfiles, revisión, preferencias y herencia, nombra el archivo con `_epub_name`, y la ventana «EPUB listo» muestra tamaño, botones de copia (`_drives`, `_reader_kind`) y «Enviar a Kindle…».

**Tech Stack:** Python 3.14, tkinter, PyMuPDF, `ctypes` (kernel32), `webbrowser`, `shutil`.

**Spec:** `docs/superpowers/specs/2026-10-29-salida-lectores-design.md`

## Global Constraints

- Texto visible en español con tildes; comentarios en español, cortos, el porqué; identificadores en inglés.
- Trabajo pesado (copia incluida) en el proceso de trabajo (`self.run`), nunca en un hilo.
- Cada cambio de `convert.py` lleva prueba en `test_convert.py`.
- Pruebas: `.venv/Scripts/python.exe test_convert.py` (exit 1 si hay `FAIL`). No correr suite y build a la vez.
- Commits en inglés, Conventional Commits, en la rama `feat/salida-lectores`.

## Review Focus

- Párrafos con entidades (`&amp;`, `&lt;`) y cursivas: `_kepub` no parte entidades ni etiquetas; el texto plano no cambia (Task 1).
- Títulos de capítulo con `<span class="num">`: el KEPUB sigue siendo XML válido (`check_epub` vacío, Task 1).
- Usuario escribe `libro.epub` con perfil Kobo: se guarda `libro.kepub.epub`; `libro.kepub.epub` sin Kobo: `libro.epub` (Task 2).
- Convertir carpeta con perfil Kobo: cada EPUB sale `.kepub.epub` (Task 2).
- Lector de tarjetas vacío o unidad sin etiqueta: `_drives` no falla ni muestra diálogos (Task 3, manual: necesita hardware).

---

### Task 1: KEPUB en `convert.py`

**Files:**
- Modify: `convert.py` (`_xhtml` zona ~1248, `write_epub` firma ~1286 y escritura del zip ~1512)
- Test: `test_convert.py`

**Interfaces:**
- Produces: `convert.write_epub(..., kepub=False)`; `convert._kepub(xhtml: str) -> str`; `convert._sentences(text: str) -> list[str]`.

- [ ] **Step 1: Write the failing test**

```python
def test_kepub_wraps_sentences_and_keeps_text():
    book = convert.Book("Libro", "", jpeg(60, 80), [
        convert.Block("heading", 1, "Capítulo 1: Inicio"),
        convert.Block("text", 1, "Hola. ¿Vienes? Tom &amp; Jerry <i>dijo algo. Y más</i> fin"),
        convert.Block("text", 1, "Sin punto final")], [0])
    plain, kobo = TMP / "normal.epub", TMP / "kobo.kepub.epub"
    convert.write_epub(book, [(0, "Capítulo 1: Inicio")], plain)
    convert.write_epub(book, [(0, "Capítulo 1: Inicio")], kobo, kepub=True)
    assert convert.check_epub(kobo) == [], convert.check_epub(kobo)
    with zipfile.ZipFile(plain) as a, zipfile.ZipFile(kobo) as b:
        before, after = a.read("OEBPS/sec001.xhtml").decode(), b.read("OEBPS/sec001.xhtml").decode()
    text = lambda s: " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s)).split())
    assert text(before) == text(after), (before, after)
    ids = re.findall(r'class="koboSpan" id="(kobo\.\d+\.\d+)"', after)
    assert len(ids) == len(set(ids)) >= 6, ids
    assert '<span class="koboSpan" id="kobo.2.1">Hola. </span>' in after, after
    assert re.search(r'<i><span class="koboSpan" id="kobo\.2\.\d+">dijo algo\. </span>', after), after
    assert 'id="book-columns"' in after and 'id="book-inner"' in after, after
    assert all("koboSpan" in p for p in re.findall(r"<p[^>]*>.*?</p>", after)), after
    assert convert._sentences("Uno. Dos... ¿Tres? «Cuatro.» cinco") == ["Uno. ", "Dos... ", "¿Tres? ",
                                                                        "«Cuatro.» ", "cinco"]
```

- [ ] **Step 2: Run tests, expect FAIL** (`write_epub() got an unexpected keyword argument 'kepub'`).

- [ ] **Step 3: Implement** — after `_xhtml`:

```python
KOBO_BLOCK = re.compile(r"(<(p|h1|h2)\b[^>]*>)(.*?)(</\2>)", re.S)


def _sentences(text):
    """Trozos de text, cada uno una frase con su espacio final: juntos dan text sin cambios."""
    return [s for s in re.findall(r"[^.!?…]*(?:[.!?…]+[»\"”’)\]]*\s*|$)", text) if s]


def _kepub(xhtml):
    """Sección en formato Kobo (KEPUB): cada frase en un koboSpan, con el que el Kobo cuenta páginas,
    tiempo de lectura y subraya, y el cuerpo en los div que espera su motor (como kepubify).
    Solo se envuelven nodos de texto: las etiquetas quedan igual y el anidado sigue siendo válido."""
    paragraph = 0

    def wrap(m):
        nonlocal paragraph
        paragraph += 1
        n, out = 0, []
        for piece in re.split(r"(<[^>]+>)", m[3]):
            if piece.startswith("<") or not piece.strip():
                out.append(piece)
                continue
            for s in _sentences(piece):
                n += 1
                out.append(f'<span class="koboSpan" id="kobo.{paragraph}.{n}">{s}</span>')
        return m[1] + "".join(out) + m[4]

    body = KOBO_BLOCK.sub(wrap, xhtml)
    return (body.replace("<body>\n", '<body>\n<div id="book-columns"><div id="book-inner">\n', 1)
            .replace("\n</body>", "\n</div></div>\n</body>", 1))
```

`write_epub`: add `kepub=False` to the signature, a docstring line
`kepub: secciones en formato Kobo (_kepub); el archivo debe acabar en .kepub.epub.`, and in the zip loop:

```python
            for name, text in files.items():
                z.writestr(f"OEBPS/{name}", _kepub(text) if kepub else text)
```

- [ ] **Step 4: Run tests, expect all ok.**
- [ ] **Step 5: Commit** `feat: Kobo KEPUB output with sentence spans`.

### Task 2: Opción `kepub`, perfiles Kindle y Kobo, nombre del archivo

**Files:**
- Modify: `app.py` (`INHERITED` ~37, `PREFS` ~42, `PROFILES` ~48, `_write_options` ~121, `_batch` ~300, `generate` ~1160, `convert_folder` ~1397, `omnibus` ~1614, opciones ~557-572, `fields` ~1721)
- Test: `test_convert.py`

**Interfaces:**
- Consumes: `convert.write_epub(..., kepub=bool)` (Task 1).
- Produces: `app._epub_name(path, kepub: bool) -> str`; `app.PROFILES["Kindle"]`, `app.PROFILES["Kobo"]`; `App.kepub_var`; clave `"kepub"` en `_write_options`.

- [ ] **Step 1: Write the failing tests**

```python
def test_kepub_option_profiles_and_names():
    import app

    assert app._epub_name("C:/x/Libro", True) == "C:/x/Libro.kepub.epub"
    assert app._epub_name("C:/x/Libro.epub", True) == "C:/x/Libro.kepub.epub"
    assert app._epub_name("C:/x/Libro.KEPUB.epub", False) == "C:/x/Libro.epub"
    assert app._epub_name("Vol. 1", False) == "Vol. 1.epub"
    assert app.PROFILES["Kobo"]["kepub"] and not app.PROFILES["Kindle"]["kepub"]
    assert app.PROFILES["Kindle"]["shrink_size"] == 1600 and not app.PROFILES["Kindle"]["gray"]
    assert all("kepub" in p for name, p in app.PROFILES.items() if name != "Personalizado")
    assert app._write_options({"kepub": True})["kepub"] and not app._write_options({})["kepub"]
    assert "kepub" in app.INHERITED and "kepub" in app.PREFS


def test_batch_with_kobo_writes_kepub():
    import app

    pdf = TMP / "lote-kobo" / "Mi Novela - Volumen 1.pdf"
    pdf.parent.mkdir(exist_ok=True)
    make_novel().save(pdf)
    results = app._batch([str(pdf)], {"kepub": True})
    assert results[0][1] == "", results
    assert pdf.with_name("Mi Novela - Volumen 1.kepub.epub").is_file(), list(pdf.parent.iterdir())
```

- [ ] **Step 2: Run tests, expect FAIL** (`module 'app' has no attribute '_epub_name'`).

- [ ] **Step 3: Implement**
  - `INHERITED` and `PREFS`: add `"kepub"`.
  - `PROFILES`: add `"kepub": False` to every existing profile except «Personalizado», then
    `"Kindle": {"gray": False, "shrink": True, "shrink_size": 1600, "epub2": False, "kepub": False}` and
    `"Kobo": {"gray": True, "shrink": True, "shrink_size": 1400, "epub2": False, "kepub": True}`.
  - `_write_options`: `"kepub": bool(state.get("kepub")),`.
  - After `_write_checked`:

```python
def _epub_name(path, kepub):
    """Nombre del EPUB: el Kobo solo usa su motor KEPUB si el archivo acaba en .kepub.epub."""
    return re.sub(r"(\.kepub)?\.epub$", "", str(path), flags=re.I) + (".kepub.epub" if kepub else ".epub")
```

  - `_batch`: `options = _write_options(state, state["_breaks"])`, `out = _epub_name(Path(pdf).with_suffix(""), options["kepub"])`, then `_write_checked(book, chapters, out, removed, options)`.
  - `generate`: `initialfile=_epub_name(self.pdf.stem, self.kepub_var.get())`; after `if not out: return`, `out = _epub_name(out, self.kepub_var.get())`.
  - `omnibus`: `initialfile=_epub_name("Recopilatorio", self.kepub_var.get())`; after `if not out: return`, `out = _epub_name(out, self.kepub_var.get())`.
  - `convert_folder`: `existing = [p for p in pdfs if p.with_suffix(".epub").exists() or p.with_suffix(".kepub.epub").exists()]`.
  - Options: `self.kepub_var = tk.BooleanVar(value=False)` and `("Formato Kobo (KEPUB)", self.kepub_var)` in the «Opciones» menu, after EPUB 2.
  - `fields`: `"kepub": self.kepub_var`.

- [ ] **Step 4: Run tests, expect all ok.**
- [ ] **Step 5: Commit** `feat: Kindle and Kobo profiles, Kobo profile saves .kepub.epub`.

### Task 3: Ventana «EPUB listo»: tamaño, copiar al e-reader, Send to Kindle

**Files:**
- Modify: `app.py` (imports, constantes, `result_buttons` ~1195, métodos nuevos `copy_to`, `send_to_kindle`)
- Test: `test_convert.py`

**Interfaces:**
- Consumes: `App.profile_var`, `App.run`, `App.open_external`.
- Produces: `app._size_text(int) -> str`, `app._reader_kind(root) -> "kindle"|"kobo"|"usb"`, `app._drives() -> [(raíz "E:\\", etiqueta)]`, `app.KINDLE_MAIL`.

- [ ] **Step 1: Write the failing test**

```python
def test_reader_kind_and_size_text():
    import app

    kindle, kobo, usb = (TMP / f"unidad-{name}" for name in ("kindle", "kobo", "usb"))
    for folder in (kindle / "documents", kindle / "system", kobo / ".kobo", usb):
        folder.mkdir(parents=True, exist_ok=True)
    assert [app._reader_kind(r) for r in (kindle, kobo, usb)] == ["kindle", "kobo", "usb"]
    assert app._size_text(18_400_000) == "18,4 MB" and app._size_text(51_000_000) == "51,0 MB"
    assert app.KINDLE_MAIL == 50_000_000
    assert all(len(root) == 3 and root.endswith(":\\") for root, _ in app._drives())
```

- [ ] **Step 2: Run tests, expect FAIL.**

- [ ] **Step 3: Implement** — imports `ctypes`, `webbrowser`; after `_epub_name`:

```python
KINDLE_MAIL = 50_000_000  # límite de Send to Kindle por correo; la web admite hasta 200 MB


def _size_text(size):
    """Tamaño en MB decimales, como da Amazon sus límites, con coma decimal."""
    return f"{size / 1e6:.1f} MB".replace(".", ",")


def _reader_kind(root):
    """"kindle", "kobo" o "usb" según las carpetas de la unidad."""
    root = Path(root)
    if (root / "documents").is_dir() and (root / "system").is_dir():
        return "kindle"
    return "kobo" if (root / ".kobo").is_dir() else "usb"


def _drives():
    """Unidades extraíbles con disco: [(raíz, etiqueta)]. Los lectores MTP no tienen letra y no salen;
    un lector de tarjetas vacío falla en GetVolumeInformationW y se salta."""
    k32 = ctypes.windll.kernel32
    mask, found = k32.GetLogicalDrives(), []
    for n in range(26):
        root = f"{chr(65 + n)}:\\"
        if mask >> n & 1 and k32.GetDriveTypeW(root) == 2:  # DRIVE_REMOVABLE
            label = ctypes.create_unicode_buffer(261)
            if k32.GetVolumeInformationW(root, label, 261, None, None, None, None, 0):
                found.append((root, label.value or "USB"))
    return found
```

`result_buttons`: the label shows `str(out)` plus, for a file, `\n<tamaño>` and with profile Kindle over
`KINDLE_MAIL` the line `Pasa de 50 MB: por correo no llega al Kindle; usa la web de Send to Kindle (hasta 200 MB).`
For a file, after «Abrir carpeta»: one button `Copiar a {label} ({root[:2]})` per non-Kindle drive,
`command=lambda r=root: self.copy_to(out, r, win)`; for a Kindle drive, a label
`{label} ({root[:2]}) es un Kindle: no lee EPUB por USB. Usa «Enviar a Kindle…».`; button
`Enviar a Kindle…` if profile is Kindle or a Kindle drive is connected.

```python
    def copy_to(self, out, root, win):
        """Copia el EPUB al lector, en Books/ si la tiene, en el proceso de trabajo (USB lento)."""
        folder = Path(root) / "Books" if (Path(root) / "Books").is_dir() else Path(root)
        if self.run(shutil.copy2, (str(out), str(folder)),
                    lambda dest: messagebox.showinfo("Copiado", f"EPUB copiado en:\n{dest}"), busy="Copiando…"):
            win.destroy()

    def send_to_kindle(self, out, win):
        """Send to Kindle no tiene API pública: se abre su web y el archivo, seleccionado, para arrastrarlo."""
        webbrowser.open("https://www.amazon.com/sendtokindle")
        self.open_external(out, win, folder=True)
```

- [ ] **Step 4: Run tests, expect all ok.**
- [ ] **Step 5: Commit** `feat: EPUB size, Send to Kindle and copy to a USB e-reader after generating`.

### Task 4: Build y prueba real

- [ ] `/probar-novela` con un tomo real y perfil Kobo: `check_epub` vacío.
- [ ] `/build-exe` (pruebas, PyInstaller, arranque).
- [ ] Commit final si el build cambió algo.
