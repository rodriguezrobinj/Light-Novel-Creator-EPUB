"""Ventana para convertir PDFs de novelas ligeras a EPUB."""
import html
import json
import multiprocessing
import os
import queue
import re
import sys
import tempfile
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import pymupdf

import convert

ON, OFF, CUT = "Capítulo", "Subtítulo", "Quitar"  # tipo de cada título de la lista
NEXT = {ON: OFF, OFF: CUT, CUT: ON}                 # cada clic pasa al siguiente
IMG, DROPPED = "Ilustración", "Descartada"           # tipo de las filas de imagen
IMAGE_NEXT = {IMG: CUT, CUT: IMG, DROPPED: IMG}      # clic en una imagen: se quita o se recupera
SPLIT, ROTATED = "Dividida", "Girada"                # ilustración doble: en dos páginas o girada 90°
WIDE_NEXT = {IMG: SPLIT, SPLIT: ROTATED, ROTATED: CUT, CUT: IMG, DROPPED: IMG}
LAYOUT = {SPLIT: "split", ROTATED: "rotate"}         # tipo de la fila -> Block.layout
KIND_TAG = {ON: "chapter", OFF: "subtitle", CUT: "cut", IMG: "image", DROPPED: "dropped",
            SPLIT: "image", ROTATED: "image"}        # estilo por tipo
KEYS = {"c": ON, "s": OFF, "q": CUT, "i": IMG, "d": SPLIT, "g": ROTATED}  # atajos de teclado en la lista
LANGUAGES = ["es", "en", "pt", "fr", "it", "ja"]     # idioma del EPUB: lo usan la partición de palabras y la voz
LANG = re.compile(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{1,8})*")  # el idioma va tal cual en el XML del EPUB
# Lo que comparten los tomos de una serie: al abrir un tomo sin revisión se toma de otro ya revisado
INHERITED = ("author", "publisher", "translator", "lang", "style", "separator", "typography", "shrink",
             "gray", "replacements")
# Preferencias de la ventana entre sesiones (opciones, última carpeta y PDF recientes)
SETTINGS = Path(os.environ.get("APPDATA") or Path.home()) / "PDF a EPUB" / "ajustes.json"
PREFS = ("lang", "style", "separator", "typography", "shrink", "gray", "credits")
RECENT = 8  # PDF recientes que recuerda el menú
KIND_COLOR = {"chapter": "", "subtitle": "#555555", "cut": "#9a9a9a", "image": "#2e7d32", "dropped": "#8a8a8a"}
PREVIEW_BOX = (400, 560)  # ancho y alto máximos de cada vista previa, en píxeles
PREVIEW_CHROME = 190      # alto de leyendas, botones y miniaturas que rodean a las vistas previas
DEVICES = {"Celular": (300, 560), 'E-reader 6"': (420, 560), "Tablet": (560, 750)}  # página en puntos
THUMB_HEIGHT = 80         # alto de las miniaturas del PDF, en píxeles
THUMBS_PER_TICK = 6       # miniaturas por tanda: dibujarlas todas de golpe congelaría la ventana
MARK_COLORS = {"title": "#1f6fd1", "image": "#2e9d4a", "current": "#f08c00"}  # bordes de las miniaturas


def _worker(events, work, args, report):
    """Corre en otro proceso: PyMuPDF retiene el GIL y, en un hilo, trabaría la ventana."""
    try:
        if report:
            result = work(*args, progress=lambda done, total: events.put(("progress", done, total)))
        else:
            result = work(*args)
        events.put(("done", result))
    except Exception as e:
        events.put(("error", str(e) or type(e).__name__))  # MemoryError() no trae mensaje


def _write_checked(book, chapters, out, *options):
    """write_epub y, en el mismo proceso de trabajo, check_epub: devuelve los problemas del EPUB."""
    convert.write_epub(book, chapters, out, *options)
    return convert.check_epub(out)


# La revisión (tipos, títulos editados, datos del libro y opciones) se guarda junto al PDF en un
# .json: al volver a abrir el mismo PDF se recupera, aunque se haya cerrado la ventana sin generar.
def _review_path(pdf):
    return Path(pdf).with_name(Path(pdf).stem + ".lnepub.json")


def _fingerprint(pdf):
    """Tamaño y fecha del PDF: si cambian, la revisión guardada es de otro PDF y no se usa."""
    stat = Path(pdf).stat()
    return [stat.st_size, stat.st_mtime_ns]


def _read_review(pdf, blocks):
    """Revisión guardada de este PDF, o None si no hay o si el PDF cambió desde entonces."""
    try:
        state = json.loads(_review_path(pdf).read_text(encoding="utf-8"))
        if state["pdf"] == _fingerprint(pdf) and state["blocks"] == blocks and isinstance(state["rows"], dict):
            return state
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def _inherited(pdf, series):
    """Datos de la serie (INHERITED) de la revisión más reciente de otro tomo de la misma serie,
    en la misma carpeta. {} si no hay."""
    found = []
    for path in Path(pdf).parent.glob("*.lnepub.json") if series else ():
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            if path != _review_path(pdf) and str(state.get("series", "")).strip().lower() == series.strip().lower():
                found.append((path.stat().st_mtime, path.name, state))
        except (OSError, ValueError, AttributeError):
            continue
    if not found:
        return {}
    state = max(found, key=lambda f: f[:2])[2]
    return {name: state[name] for name in INHERITED if name in state}


def _replay(book, state):
    """Vuelve a aplicar al libro recién analizado los textos editados de una revisión, en su orden
    (cada edición cuenta los bloques tal como los dejó la anterior). Devuelve las aplicadas."""
    done = []
    for edit in state.get("edits", []):
        try:
            start, end, text = int(edit[0]), int(edit[1]), str(edit[2])
            if not 0 < start <= end <= len(book.blocks):
                break
        except (TypeError, ValueError, IndexError):
            break  # el .json se puede editar a mano
        convert.edit_section(book, start, end, text)
        done.append([start, end, text])
    return done


def _plan(book, rows):
    """Capítulos y secciones quitadas según los tipos de la lista ({fila: (tipo, título)}); marca
    también qué ilustraciones entran y cómo."""
    chapters, removed = [], []
    for item, (kind, title) in rows.items():
        k = int(item)
        if book.blocks[k].kind == "image":
            book.blocks[k].keep = kind in (IMG, SPLIT, ROTATED)
            book.blocks[k].layout = LAYOUT.get(kind, "")
        elif kind == ON:
            chapters.append((k, title))
        elif kind == CUT:
            removed.append(k)
    return chapters, removed


def _cover_image(doc, choice):
    """Portada elegida: ("page", n) es una página del PDF (desde 0); ("file", ruta), una imagen."""
    kind, value = choice
    return convert._render(doc[value], crop=True) if kind == "page" else convert.load_cover(value)


def _parse_replacements(text):
    """Una regla por línea, "buscar -> reemplazo"; las líneas sin flecha no cuentan."""
    rules = []
    for line in text.splitlines():
        if "->" in line:
            find, new = (s.strip() for s in line.split("->", 1))
            if find:
                rules.append([find, new])
    return rules


def _batch(pdfs, options, progress=None):
    """Convierte varios PDF sin pasar por la ventana: con la revisión guardada de cada uno si la hay y,
    si no, con lo detectado y los datos heredados de otro tomo de la serie. options: estilo y demás
    opciones de la ventana, para los PDF sin revisión. Devuelve [(nombre del PDF, error o "", problemas
    del EPUB según check_epub)]."""
    results = []
    for n, pdf in enumerate(pdfs):
        try:
            book = convert.analyze(pdf)
            state = _read_review(pdf, len(book.blocks))
            if state:
                _replay(book, state)
                chapters, removed = _plan(book, state["rows"])
            else:
                state = {**options, **_inherited(pdf, book.series)}
                state["author"] = book.author or state.get("author", "")
                chapters = [(k, html.unescape(book.blocks[k].html)) for k in book.candidates]
                removed = convert.link_sections(book)
            for name in ("title", "author", "series", "volume", "publisher", "translator", "description"):
                if name in state:
                    setattr(book, name, str(state[name]).strip())
            if state.get("cover"):
                try:
                    with pymupdf.open(pdf) as doc:
                        book.cover = _cover_image(doc, state["cover"])
                except Exception:
                    pass  # la imagen de portada ya no existe: se queda la del PDF
            lang = str(state.get("lang", "es"))
            style = state.get("style")
            problems = _write_checked(book, chapters, Path(pdf).with_suffix(".epub"),
                                      lang if LANG.fullmatch(lang) else "es", removed,
                                      bool(state.get("typography", True)), style if style in convert.STYLES else "Clásico",
                                      True, str(state.get("separator", "Según el estilo")),
                                      bool(state.get("shrink", True)), [tuple(r) for r in state.get("replacements", [])],
                                      bool(state.get("credits")), bool(state.get("gray")))
            results.append((Path(pdf).name, "", problems))
        except Exception as e:
            results.append((Path(pdf).name, str(e) or type(e).__name__, []))
        if progress:
            progress(n + 1, len(pdfs))
    return results


class App:
    def __init__(self, root):
        self.root, self.book, self.pdf = root, None, None
        self.events = multiprocessing.Queue()  # el proceso de trabajo envía aquí progreso y resultado
        self.done = None                       # qué hacer con el resultado del trabajo en curso
        self.latest = None                     # último progreso (hecho, total) pendiente de pintar
        self.partial = None                    # EPUB a medio escribir del trabajo en curso
        self.editor_save = None                # guarda y cierra el cuadro de edición abierto
        self.view = None                       # última posición del scroll de la lista
        self.pdf_doc = None                    # PDF abierto para la vista previa
        self.preview_path = os.path.join(tempfile.gettempdir(), f"lnepub-vista-{os.getpid()}.epub")
        self.pdf_page = 0                      # página del PDF que muestra la vista previa (desde 0)
        self.highlight = None                  # (página, rectángulo) del título seleccionado
        self.epub_page, self.epub_pages = 0, 0  # página mostrada y total de la sección en el EPUB
        self.epub_cut = False                  # la sección seleccionada está en "Quitar"
        self.thumbs, self.thumb_images = {}, []  # miniaturas: página -> (x, ancho, alto)
        self.thumb_job, self.thumb_x = 0, 4    # tanda en curso (para cancelarla) y posición siguiente
        self.wide = set()                      # filas de ilustraciones dobles (apaisadas)
        self.history = []                      # para deshacer: [(fila, tipo, título) de antes] o copia antes de editar texto
        self.busy = ""                         # qué dice la barra de estado mientras avanza el trabajo
        self.edits, self.analyzed = [], 0      # textos editados [(inicio, fin, texto)] y bloques del análisis
        self.find_target = None                # (página, texto) que la vista previa debe mostrar tras buscar
        self.next_page = 0                     # página de la sección del EPUB al seleccionarla (◀ desde la siguiente: la última)
        self.settings = {}                     # preferencias guardadas (SETTINGS)
        self.device_var = tk.StringVar(value='E-reader 6"')
        self.dark_var = tk.BooleanVar(value=False)
        base = tkfont.nametofont("TkDefaultFont")
        self.fonts = {name: base.copy() for name in KIND_COLOR}
        self.fonts["dropped"].configure(slant="italic")
        self.fonts["chapter"].configure(weight="bold")
        self.fonts["subtitle"].configure(slant="italic")
        self.fonts["cut"].configure(overstrike=1)
        root.title("PDF a EPUB")
        root.geometry("1300x720")
        root.protocol("WM_DELETE_WINDOW", self.close)

        top = ttk.Frame(root, padding=8)
        top.pack(fill="x")
        self.open_btn = ttk.Button(top, text="Abrir PDF…", command=self.open_pdf)
        self.open_btn.pack(side="left")
        self.batch_btn = ttk.Button(top, text="Convertir carpeta…", command=self.convert_folder)
        self.batch_btn.pack(side="left", padx=(4, 0))
        recent = ttk.Menubutton(top, text="Recientes")
        self.recent_menu = tk.Menu(recent, tearoff=False)
        recent.configure(menu=self.recent_menu)
        recent.pack(side="left", padx=(4, 0))
        self.file_label = ttk.Label(top, text="Ningún archivo")
        self.file_label.pack(side="left", padx=8)
        ttk.Button(top, text="Portada: archivo…", command=self.cover_from_file).pack(side="right")
        ttk.Button(top, text="Portada: página mostrada", command=self.cover_from_page).pack(side="right", padx=4)
        self.cover_label = ttk.Label(top, text="")
        self.cover_label.pack(side="right", padx=8)
        self.cover_choice = None  # ("page", n), ("file", ruta) o None: la que eligió el análisis

        meta = ttk.Frame(root, padding=(8, 0))
        meta.pack(fill="x")
        self.title_var, self.author_var = tk.StringVar(), tk.StringVar()
        self.series_var, self.volume_var = tk.StringVar(), tk.StringVar()
        for label, var, width in (("Título:", self.title_var, 45), ("Autor:", self.author_var, 22),
                                  ("Serie:", self.series_var, 35), ("Vol.:", self.volume_var, 5)):
            ttk.Label(meta, text=label).pack(side="left")
            ttk.Entry(meta, textvariable=var, width=width).pack(side="left", padx=(4, 12))
        meta2 = ttk.Frame(root, padding=(8, 4, 8, 0))
        meta2.pack(fill="x")
        self.publisher_var, self.translator_var = tk.StringVar(), tk.StringVar()
        self.lang_var = tk.StringVar(value="es")
        self.description = ""  # sinopsis; se edita en su propio cuadro
        self.replacements = []  # [[buscar, reemplazo]]; se editan en su propio cuadro
        for label, var, width in (("Grupo:", self.publisher_var, 30), ("Traductor:", self.translator_var, 30)):
            ttk.Label(meta2, text=label).pack(side="left")
            ttk.Entry(meta2, textvariable=var, width=width).pack(side="left", padx=(4, 12))
        ttk.Label(meta2, text="Idioma:").pack(side="left")
        ttk.Combobox(meta2, textvariable=self.lang_var, values=LANGUAGES, width=4).pack(side="left", padx=(4, 12))
        ttk.Button(meta2, text="Sinopsis…", command=self.edit_description).pack(side="left")
        ttk.Button(meta2, text="Reemplazos…", command=self.edit_replacements).pack(side="left", padx=4)
        ttk.Button(meta2, text="Mayúsculas de títulos", command=self.fix_case).pack(side="left")

        # La barra inferior y la ayuda se empaquetan antes que el centro: pack reparte el espacio en
        # orden, y así el centro (con las vistas previas) es el que encoge en pantallas bajas.
        bottom = ttk.Frame(root, padding=8)
        bottom.pack(side="bottom", fill="x")
        self.progress = ttk.Progressbar(bottom, length=200)
        self.progress.pack(side="left")
        self.status = ttk.Label(bottom, text="")
        self.status.pack(side="left", padx=8)
        self.gen_btn = ttk.Button(bottom, text="Generar EPUB…", command=self.generate, state="disabled")
        self.gen_btn.pack(side="right")
        self.credits_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(bottom, text="Página de créditos", variable=self.credits_var).pack(side="right", padx=(12, 0))
        self.shrink_var = tk.BooleanVar(value=True)  # las imágenes son casi todo el peso del EPUB
        ttk.Checkbutton(bottom, text="Reducir imágenes", variable=self.shrink_var).pack(side="right", padx=(12, 0))
        self.gray_var = tk.BooleanVar(value=False)  # para e-readers de tinta: pesa menos y se ve igual
        ttk.Checkbutton(bottom, text="Imágenes en gris", variable=self.gray_var,
                        command=self.update_preview).pack(side="right", padx=(12, 0))
        self.typography_var = tk.BooleanVar(value=True)  # comillas «», puntos suspensivos, raya, espacios
        ttk.Checkbutton(bottom, text="Limpieza tipográfica", variable=self.typography_var,
                        command=self.update_preview).pack(side="right", padx=12)
        self.style_var = tk.StringVar(value="Clásico")  # aspecto del EPUB: letra, párrafos e inicio de capítulo
        style = ttk.Combobox(bottom, textvariable=self.style_var, values=list(convert.STYLES), width=13,
                             state="readonly")
        style.pack(side="right")
        style.bind("<<ComboboxSelected>>", lambda _: self.update_preview())
        ttk.Label(bottom, text="Estilo:").pack(side="right", padx=(12, 4))
        self.separator_var = tk.StringVar(value="Según el estilo")  # cómo se ven los separadores de escena
        separator = ttk.Combobox(bottom, textvariable=self.separator_var, values=convert.SEPARATOR_CHOICES,
                                 width=16, state="readonly")
        separator.pack(side="right")
        separator.bind("<<ComboboxSelected>>", lambda _: self.update_preview())
        ttk.Label(bottom, text="Separador:").pack(side="right", padx=(12, 4))
        ttk.Label(root, text="Clic en Tipo, o teclas C (capítulo), S (subtítulo), Q (quitar), I (ilustración),"
                             " D/G (ilustración doble: dividir o girar) · Doble clic o F2: editar título"
                             " · E: editar texto · Ctrl+F: buscar · Ctrl+Z: deshacer",
                  padding=(8, 0)).pack(side="bottom", anchor="w")

        middle = ttk.Frame(root, padding=8)
        middle.pack(fill="both", expand=True)
        self.views = ttk.Frame(middle, padding=(8, 0, 0, 0))
        self.views.pack(side="right", fill="y")
        strip_frame = ttk.Frame(self.views)  # miniaturas debajo de las dos vistas previas
        strip_frame.pack(side="bottom", fill="x", pady=(6, 0))
        self.strip = tk.Canvas(strip_frame, height=THUMB_HEIGHT + 12, highlightthickness=0)
        strip_scroll = ttk.Scrollbar(strip_frame, orient="horizontal", command=self.strip.xview)
        self.strip.configure(xscrollcommand=strip_scroll.set)
        self.strip.pack(fill="x")
        strip_scroll.pack(fill="x")
        pdf_pane = self.preview_pane(self.views, "PDF", self.pdf_step)
        self.pdf_caption, self.pdf_view = pdf_pane[0], tk.Canvas(pdf_pane[2], highlightthickness=0)
        self.pdf_view.image = None
        self.pdf_view.pack()
        epub_pane = self.preview_pane(self.views, "EPUB", self.epub_step)
        self.epub_caption = epub_pane[0]
        self.epub_view = tk.Canvas(epub_pane[2], highlightthickness=0)
        self.epub_view.image = None  # referencia a la imagen: sin ella, Tk la borra
        self.epub_view.pack()
        device = ttk.Combobox(epub_pane[1], textvariable=self.device_var, values=list(DEVICES), width=11,
                              state="readonly")
        device.pack(side="left", padx=(8, 4))
        device.bind("<<ComboboxSelected>>", lambda _: self.draw_epub())
        ttk.Checkbutton(epub_pane[1], text="Modo oscuro", variable=self.dark_var,
                        command=self.draw_epub).pack(side="left")
        ttk.Button(epub_pane[1], text="Editar texto…", command=self.edit_text).pack(side="left", padx=(8, 0))
        frame = ttk.Frame(middle)
        frame.pack(side="left", fill="both", expand=True)
        self.tree = ttk.Treeview(frame, columns=("inc", "title", "page"), show="headings", selectmode="browse")
        for col, text, width in (("inc", "Tipo", 80), ("title", "Título", 290), ("page", "Página", 60)):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, anchor="w" if col == "title" else "center", stretch=col == "title")
        self.scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=self.on_scroll)
        self.tree.pack(side="left", fill="both", expand=True)
        self.scroll.pack(side="right", fill="y")
        self.tree.bind("<Button-1>", self.on_click)
        self.tree.bind("<Double-1>", self.on_double_click)
        self.tree.bind("<<TreeviewSelect>>", self.update_preview)
        for key, kind in KEYS.items():
            for k in (key, key.upper()):
                self.tree.bind(f"<KeyPress-{k}>", lambda _, kind=kind: self.shortcut(kind))
        self.tree.bind("<F2>", lambda _: self.tree.selection() and self.edit_title(self.tree.selection()[0]))
        self.tree.bind("<Control-z>", self.undo)
        self.tree.bind("<Control-Z>", self.undo)  # con Bloq Mayús
        for key in ("e", "E"):
            self.tree.bind(f"<KeyPress-{key}>", lambda _: self.edit_text())
        root.bind("<Control-f>", self.find)
        root.bind("<Control-F>", self.find)
        for tag in KIND_TAG.values():
            self.tree.tag_configure(tag, font=self.fonts[tag],
                                    foreground=KIND_COLOR[tag])
        # Marcos de tamaño fijo: solo cambian si cambia el alto de la ventana, no con cada página
        self.idle_text, self.views_height = "Abre un PDF para ver la vista previa", 0
        self.views.bind("<Configure>", self.on_resize)
        self.redraw()
        self.load_settings()
        root.after(100, self.poll)

    def run(self, work, args, done, report=False, busy="Analizando…"):
        """Ejecuta work(*args) en otro proceso y llama a done(resultado) en la ventana.
        Con report=True, work recibe progress=... y la barra avanza junto al texto busy."""
        self.open_btn.state(["disabled"])
        self.batch_btn.state(["disabled"])
        self.gen_btn.state(["disabled"])
        self.done, self.busy = done, busy
        self.proc = multiprocessing.Process(target=_worker, args=(self.events, work, args, report), daemon=True)
        self.proc.start()

    def report(self, done, total):
        # Solo se guarda el último valor: pintar cada evento de progreso cuesta llamadas a Tk.
        self.latest = (done, total)

    def poll(self):
        while True:  # el proceso envía 2 eventos por página: la ventana lee mucho más rápido
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            if event[0] == "progress":
                self.report(event[1], event[2])
            else:
                self.finish(event)
        # Un proceso que muere sin avisar (fallo de MuPDF, falta de memoria) no envía "error".
        # Su último evento ya está en la cola antes de que termine, así que se mira después de vaciarla.
        if self.done and not self.proc.is_alive() and self.events.empty():
            self.finish(("error", f"El proceso de trabajo terminó inesperadamente (código {self.proc.exitcode})."))
        latest, self.latest = self.latest, None
        if latest:
            done, total = latest
            self.progress.configure(maximum=total, value=done)
            self.status.configure(text=f"{self.busy} {done * 100 // total} %")
        self.root.after(100, self.poll)

    def finish(self, event):
        """Cierra el trabajo en curso: desbloquea la ventana y entrega el resultado o el error."""
        done, self.done, self.latest, self.partial = self.done, None, None, None
        self.open_btn.state(["!disabled"])
        self.batch_btn.state(["!disabled"])
        if self.book:
            self.gen_btn.state(["!disabled"])
        if event[0] == "done":
            done(event[1])
        else:
            self.progress.configure(value=0)
            self.status.configure(text="Error")
            messagebox.showerror("Error", event[1])

    def close(self):
        """Al cerrar a mitad de un trabajo, detiene el proceso y borra el EPUB a medio escribir."""
        self.save_review()
        self.save_settings()
        if self.done and self.proc.is_alive():
            self.proc.terminate()
            self.proc.join()
            if self.partial:
                Path(self.partial).unlink(missing_ok=True)
        self.thumb_job += 1  # cancela las miniaturas pendientes
        if self.pdf_doc:
            self.pdf_doc.close()
            self.pdf_doc = None
        Path(self.preview_path).unlink(missing_ok=True)
        self.root.destroy()

    def preview_pane(self, parent, name, step):
        """Columna de una vista previa: leyenda, botones ◀ ▶ y sitio para la imagen.
        Devuelve (leyenda, fila de botones, marco de la imagen)."""
        pane = ttk.Frame(parent)
        pane.pack(side="left", padx=4, anchor="n")
        caption = ttk.Label(pane, text=name)
        caption.pack()
        controls = ttk.Frame(pane)
        controls.pack(pady=2)
        ttk.Button(controls, text="◀", width=3, command=lambda: step(-1)).pack(side="left")
        ttk.Button(controls, text="▶", width=3, command=lambda: step(1)).pack(side="left")
        body = ttk.Frame(pane)
        body.pack()
        return caption, controls, body

    def box_size(self):
        """Tamaño fijo de cada vista previa (proporción 3:4) según el alto de la ventana. No depende
        de la página: así cambiar de dispositivo o de página no mueve el resto de la ventana."""
        room = self.views.winfo_height() - PREVIEW_CHROME
        height = min(PREVIEW_BOX[1], room) if room > 150 else 420  # aún sin dibujar: tamaño normal
        return int(height * 0.75), height

    def blank(self, canvas, text):
        """Hoja en blanco del tamaño de la vista previa, con un aviso tenue en el centro."""
        width, height = self.box_size()
        canvas.delete("all")
        canvas.image = None
        canvas.configure(width=width, height=height)
        canvas.create_rectangle(1, 1, width - 2, height - 2, fill="white", outline="#d4d4d4", tags="sheet")
        canvas.create_text(width // 2, height // 2, text=text, fill="#9a9a9a", width=width - 40,
                           justify="center", tags="message")

    def place(self, canvas, image):
        """Pone la imagen centrada en su marco fijo; devuelve el desplazamiento (x, y)."""
        width, height = self.box_size()
        canvas.delete("all")
        canvas.image = image
        canvas.configure(width=width, height=height)
        x, y = (width - image.width()) // 2, (height - image.height()) // 2
        canvas.create_image(x, y, anchor="nw", image=image)
        return x, y

    def redraw(self):
        """Vuelve a dibujar las vistas previas (al iniciar, al cargar un libro o al cambiar el alto)."""
        if self.book and self.tree.selection():
            self.draw_pdf()
            self.draw_epub()
            return
        self.blank(self.pdf_view, self.idle_text)
        self.blank(self.epub_view, self.idle_text)
        if not self.thumbs:  # tira vacía: rectángulos tenues en lugar de un hueco
            self.strip.delete("placeholder")
            width = int(THUMB_HEIGHT * 0.71)
            for n in range(20):
                x = 4 + n * (width + 8)
                self.strip.create_rectangle(x, 6, x + width, 6 + THUMB_HEIGHT, fill="#e6e6e6", outline="",
                                            tags="placeholder")

    def on_resize(self, event):
        if event.height != self.views_height:  # solo el alto cambia el tamaño de los marcos
            self.views_height = event.height
            self.redraw()

    def render(self, page, dark=False):
        """Imagen de una página (del PDF o del EPUB) ajustada a su marco, y su escala."""
        width, height = self.box_size()
        zoom = min(width / page.rect.width, height / page.rect.height)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
        if dark:  # aproximación del modo oscuro de los lectores: invierte también las ilustraciones
            pix.invert_irect()
        return tk.PhotoImage(master=self.root, data=pix.tobytes("ppm")), zoom

    def update_preview(self, _=None):
        """Al seleccionar (o cambiar) un título: su página del PDF y su sección en el EPUB."""
        selected = self.tree.selection()
        if not selected or not self.book:
            return
        item = selected[0]
        index = int(item)
        block = self.book.blocks[index]
        self.pdf_page = block.page - 1
        self.highlight = (self.pdf_page, block.rect) if block.rect else None
        self.draw_pdf()
        self.epub_page, self.epub_pages, self.next_page = self.next_page, 0, 0
        is_image = block.kind == "image"
        kind = self.tree.set(item, "inc")
        self.epub_cut = kind in (CUT, DROPPED)
        self.epub_message = "Esta ilustración no irá en el EPUB" if is_image else "Esta sección no irá en el EPUB"
        if not self.epub_cut:
            # EPUB mínimo con solo esta sección (del título al siguiente) o esta ilustración: rápido
            try:
                blocks = self.book.blocks
                if is_image:
                    part_blocks = [convert.Block("image", block.page, image=block.image, layout=LAYOUT.get(kind, ""))]
                else:
                    end = next((k for k in range(index + 1, len(blocks)) if blocks[k].kind == "heading"), len(blocks))
                    part_blocks = blocks[index:end]
                part = convert.Book(self.book.title, "", self.book.cover, part_blocks, [])
                convert.write_epub(part, [(0, self.tree.set(item, "title"))] if kind == ON else [], self.preview_path,
                                   typography=self.typography_var.get(), style=self.style_var.get(),
                                   contents=False,  # sin página de índice: el capítulo 1 es la sección
                                   separator=self.separator_var.get(), replacements=self.replacements,
                                   gray=self.gray_var.get())
            except Exception:
                self.epub_pages = -1  # no se pudo generar
        self.draw_epub()
        if self.find_target:
            self.show_found(*self.find_target)

    def show_found(self, page, term):
        """Tras buscar: la página del PDF con el resultado y, en la sección, la página del EPUB que lo tiene."""
        self.find_target = None
        self.pdf_page, self.highlight = page - 1, None
        self.draw_pdf()
        try:
            with pymupdf.open(self.preview_path) as epub:
                width, height = DEVICES[self.device_var.get()]
                epub.layout(width=width, height=height, fontsize=11)
                self.epub_page = next((n for n in range(epub.chapter_page_count(1))
                                       if epub.load_page((1, n)).search_for(term)), 0)
        except Exception:
            pass  # sin vista previa del EPUB: se queda en su primera página
        self.draw_epub()

    def draw_pdf(self):
        canvas = self.pdf_view
        try:
            image, zoom = self.render(self.pdf_doc[self.pdf_page])
        except Exception:
            self.blank(canvas, "Sin vista previa")
            return
        dx, dy = self.place(canvas, image)
        if self.highlight and self.highlight[0] == self.pdf_page:  # recuadro sobre la línea tomada como título
            x0, y0, x1, y1 = (v * zoom for v in self.highlight[1])
            canvas.create_rectangle(dx + x0 - 3, dy + y0 - 2, dx + x1 + 3, dy + y1 + 2, outline="red", width=2,
                                    tags="title")
        self.pdf_caption.configure(text=f"PDF · página {self.pdf_page + 1}")
        self.mark_current()

    def draw_epub(self):
        view = self.epub_view
        if self.epub_cut or self.epub_pages < 0:
            self.blank(view, self.epub_message if self.epub_cut else "Sin vista previa")
            self.epub_caption.configure(text="EPUB")
            return
        try:
            with pymupdf.open(self.preview_path) as epub:
                width, height = DEVICES[self.device_var.get()]
                epub.layout(width=width, height=height, fontsize=11)
                self.epub_pages = epub.chapter_page_count(1)  # el capítulo 0 es la portada
                self.epub_page = max(0, min(self.epub_page, self.epub_pages - 1))
                image, _ = self.render(epub.load_page((1, self.epub_page)), dark=self.dark_var.get())
        except Exception:
            self.blank(view, "Sin vista previa")
            return
        self.place(view, image)
        self.epub_caption.configure(text=f"EPUB · {self.device_var.get()} · {self.epub_page + 1}/{self.epub_pages}")

    def pdf_step(self, delta):
        if self.pdf_doc:
            self.pdf_page = max(0, min(self.pdf_page + delta, self.pdf_doc.page_count - 1))
            self.draw_pdf()

    def epub_step(self, delta):
        """◀ ▶ del EPUB: páginas de la sección y, al llegar a un extremo, la sección vecina de la lista,
        para leer el libro seguido (hacia atrás se entra por su última página). Un salto largo se queda
        primero en el borde de la sección."""
        edge = 0 if delta < 0 else self.epub_pages - 1
        if self.epub_pages > 0 and self.epub_page != edge:
            self.epub_page = max(0, min(self.epub_page + delta, self.epub_pages - 1))
            self.draw_epub()
            return
        items, selected = self.tree.get_children(), self.tree.selection()
        n = items.index(selected[0]) + delta if selected else -1
        if 0 <= n < len(items):
            self.next_page = 10 ** 6 if delta < 0 else 0  # draw_epub la ajusta a la última
            self.tree.selection_set(items[n])
            self.tree.see(items[n])

    def start_thumbs(self):
        """Empieza a dibujar las miniaturas del PDF, por tandas para no congelar la ventana."""
        self.thumb_job += 1  # cancela la tanda de un libro anterior
        self.strip.delete("all")
        self.thumbs, self.thumb_images, self.thumb_x = {}, [], 4
        titles = {b.page for b in self.book.blocks if b.kind == "heading"}
        images = {b.page for b in self.book.blocks if b.kind == "image"}
        self.root.after(10, self.add_thumbs, self.thumb_job, 0, titles, images)

    def add_thumbs(self, job, start, titles, images):
        if job != self.thumb_job or not self.pdf_doc:
            return
        end = min(start + THUMBS_PER_TICK, self.pdf_doc.page_count)
        self.strip.delete("placeholder")
        for n in range(start, end):
            page = self.pdf_doc[n]
            zoom = THUMB_HEIGHT / page.rect.height
            image = tk.PhotoImage(master=self.root, data=page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).tobytes("ppm"))
            self.thumb_images.append(image)
            x, w, h = self.thumb_x, image.width(), image.height()
            self.strip.create_image(x, 6, anchor="nw", image=image, tags=(f"p{n}",))
            color = MARK_COLORS["title"] if n + 1 in titles else MARK_COLORS["image"] if n + 1 in images else ""
            self.strip.create_rectangle(x - 2, 4, x + w + 2, 8 + h, outline=color, width=3, tags=(f"b{n}",))
            self.strip.tag_bind(f"p{n}", "<Button-1>", lambda _, n=n: self.jump(n))
            self.thumbs[n] = (x, w, h)
            self.thumb_x += w + 8
        self.strip.configure(scrollregion=(0, 0, self.thumb_x, THUMB_HEIGHT + 12))
        self.mark_current()
        if end < self.pdf_doc.page_count:
            self.root.after(10, self.add_thumbs, job, end, titles, images)

    def mark_current(self):
        """Destaca en la tira la miniatura de la página que muestra la vista previa del PDF."""
        self.strip.delete("current")
        if self.pdf_page in self.thumbs:
            x, w, h = self.thumbs[self.pdf_page]
            self.strip.create_rectangle(x - 4, 2, x + w + 4, 10 + h, outline=MARK_COLORS["current"], width=3,
                                        tags="current")

    def jump(self, n):
        """Clic en una miniatura: esa página en la vista previa y, si tiene un título, seleccionarlo."""
        self.pdf_page = n
        self.draw_pdf()
        item = next((i for i in self.tree.get_children() if self.book.blocks[int(i)].page == n + 1), None)
        if item:
            self.tree.selection_set(item)
            self.tree.see(item)

    def row_state(self, item):
        return item, self.tree.set(item, "inc"), self.tree.set(item, "title")

    def set_kind(self, item, kind):
        self.history.append([self.row_state(item)])
        self.tree.set(item, "inc", kind)
        self.tree.item(item, tags=(KIND_TAG[kind],))

    def undo(self, _=None):
        """Ctrl+Z: deshace el último cambio: tipo o título de una fila, títulos corregidos de golpe o
        un texto editado (vuelve la copia de los bloques y de la lista de antes)."""
        if not self.history:
            return
        change = self.history.pop()
        if isinstance(change, dict):
            self.book.blocks, self.book.candidates = change["blocks"], change["candidates"]
            del self.edits[change["edits"]:]
            self.fill_tree(change["rows"])
            change = [self.row_state(change["item"])]
        for item, kind, title in change:
            self.tree.set(item, "inc", kind)
            self.tree.item(item, tags=(KIND_TAG[kind],))
            self.tree.set(item, "title", title)
        self.tree.selection_set(change[-1][0])
        self.tree.see(change[-1][0])
        self.update_preview()

    def fix_case(self):
        """Pone los títulos con las mayúsculas del español (convert.spanish_case). Se deshace con Ctrl+Z."""
        if not self.book:
            return
        words = convert.body_words(self.book)
        changed = []
        for item in self.tree.get_children():
            if not self.is_image(item):
                title = convert.spanish_case(self.tree.set(item, "title"), words)
                if title != self.tree.set(item, "title"):
                    changed.append(self.row_state(item))
                    self.tree.set(item, "title", title)
        if changed:
            self.history.append(changed)
            self.update_preview()
        self.status.configure(text=f"Mayúsculas corregidas en {len(changed)} títulos")

    def cycle(self, item):
        """Orden de tipos de una fila al hacer clic: título, ilustración o ilustración doble."""
        if not self.is_image(item):
            return NEXT
        return WIDE_NEXT if item in self.wide else IMAGE_NEXT

    def open_pdf(self):
        path = filedialog.askopenfilename(filetypes=[("PDF", "*.pdf")], initialdir=self.settings.get("folder"))
        if path:
            self.open_path(path)

    def open_path(self, path):
        """Abre un PDF: desde el botón, el menú de recientes o arrastrado sobre el .exe."""
        if self.done:  # hay un trabajo en curso
            return
        if not Path(path).is_file():
            messagebox.showerror("Error", f"No se encuentra el archivo:\n{path}")
            return
        self.save_review()
        path = str(Path(path).resolve())
        self.settings["folder"] = str(Path(path).parent)
        self.settings["recent"] = [path] + [p for p in self.settings.get("recent", []) if p != path][:RECENT - 1]
        self.fill_recent()
        self.pdf, self.book = Path(path), None
        self.file_label.configure(text=self.pdf.name)
        self.tree.delete(*self.tree.get_children())
        self.run(convert.analyze, (path,), self.show_book, report=True)

    def show_book(self, book):
        self.book = book
        if self.pdf_doc:
            self.pdf_doc.close()
        self.pdf_doc = pymupdf.open(self.pdf)
        self.title_var.set(book.title)
        self.author_var.set(book.author)
        self.series_var.set(book.series)
        self.volume_var.set(book.volume)
        self.publisher_var.set(book.publisher)
        self.translator_var.set(book.translator)
        self.description = book.description
        self.replacements = []  # son de la serie: llegan con la revisión o heredados de otro tomo
        self.cover_choice = None
        self.cover_label.configure(text="Portada: la del PDF")
        self.edits, self.analyzed = [], len(book.blocks)
        self.fill_tree({})
        cut = convert.link_sections(book)  # publicidad del grupo: se propone quitarla
        for k in cut:
            self.set_kind(str(k), CUT)
        self.idle_text = "Selecciona un título en la lista"
        self.blank(self.pdf_view, self.idle_text)
        self.blank(self.epub_view, self.idle_text)
        self.start_thumbs()
        self.gen_btn.state(["!disabled"])
        self.progress.configure(value=0)
        items = self.tree.get_children()
        found = len(book.candidates)
        images = sum(self.is_image(i) for i in items)
        dropped = sum(self.tree.set(i, "inc") == DROPPED for i in items)
        others = len(items) - found - images
        text = f"{found} capítulos detectados" if found else "No se detectaron capítulos: el EPUB tendrá una sola sección"
        text += f", {others} subtítulos" if others else ""
        text += f" y {images} ilustraciones" + (f" ({dropped} descartadas)" if dropped else "") if images else ""
        text += f" · {len(cut)} secciones del grupo en Quitar" if cut else ""
        if self.load_review():
            text = "Revisión anterior recuperada"
        elif self.pdf:
            inherited = _inherited(self.pdf, book.series)
            if book.author:
                inherited.pop("author", None)  # el del PDF manda
            if inherited:
                self.apply_state(inherited)
                text += " · datos de la serie tomados de otro tomo"
        self.history = []  # lo recuperado no se deshace
        self.status.configure(text=text)

    def fill_tree(self, rows):
        """Llena la lista con los títulos e ilustraciones del libro, en su orden. rows: {fila: (tipo,
        título)} de las que ya tienen tipo; las demás salen como las dejó el análisis: los capítulos
        detectados como capítulo, los demás títulos ("El lado de…") como subtítulo, y las ilustraciones
        descartadas por el análisis (fondos de título, imágenes dentro del texto) como "Descartada"."""
        self.tree.delete(*self.tree.get_children())
        chapters = set(self.book.candidates)
        self.wide = {str(i) for i, b in enumerate(self.book.blocks)
                     if b.kind == "image" and b.image and convert.is_wide(b.image)}
        for i, block in enumerate(self.book.blocks):
            if block.kind == "heading":
                mark, title = (ON if i in chapters else OFF), html.unescape(block.html)
            elif block.kind == "image":
                double = " doble" if str(i) in self.wide else ""
                mark, title = (IMG if block.keep else DROPPED), f"Ilustración{double} · página {block.page}"
            else:
                continue
            mark, title = rows.get(str(i), (mark, title))
            self.tree.insert("", "end", iid=str(i), values=(mark, title, block.page), tags=(KIND_TAG[mark],))

    def is_image(self, item):
        return self.book is not None and self.book.blocks[int(item)].kind == "image"

    def on_click(self, event):
        item = self.tree.identify_row(event.y)
        if item and self.tree.identify_column(event.x) == "#1":
            self.set_kind(item, self.cycle(item)[self.tree.set(item, "inc")])
            self.update_preview()

    def on_double_click(self, event):
        item = self.tree.identify_row(event.y)
        column = self.tree.identify_column(event.x)
        if item and column == "#1":  # Tk manda el segundo clic rápido aquí, no a on_click
            self.on_click(event)
        if item and column == "#2":
            self.edit_title(item)

    def shortcut(self, kind):
        """Atajo (KEYS) sobre la fila seleccionada; se ignora si ese tipo no vale para la fila (C en una
        ilustración, D en una que no es doble)."""
        selected = self.tree.selection()
        if not selected:
            return
        item = selected[0]
        if kind in self.cycle(item) and kind != self.tree.set(item, "inc"):
            self.set_kind(item, kind)
            self.update_preview()

    def edit_title(self, item):
        if self.is_image(item):  # el nombre de una ilustración no se edita
            return
        self.tree.see(item)
        self.root.update_idletasks()
        x, y, w, h = self.tree.bbox(item, "title")
        entry = ttk.Entry(self.tree)
        entry.insert(0, self.tree.set(item, "title"))
        entry.select_range(0, "end")
        entry.place(x=x, y=y, width=w, height=h)
        entry.focus()

        def save(_=None):
            if not entry.winfo_exists():  # ya cerrado por otro camino (scroll, Return, Escape)
                return
            if entry.get().strip() and entry.get().strip() != self.tree.set(item, "title"):
                self.history.append([self.row_state(item)])
                self.tree.set(item, "title", entry.get().strip())
            close()
            self.update_preview()

        def close(_=None):
            entry.destroy()
            self.editor_save = None

        self.editor_save = save
        entry.bind("<Return>", save)
        entry.bind("<FocusOut>", save)
        entry.bind("<Escape>", close)

    def on_scroll(self, first, last):
        self.scroll.set(first, last)
        # El cuadro de edición está colocado sobre una fila: si la lista se desplaza, se guarda y se cierra.
        if self.editor_save and (first, last) != self.view:
            self.editor_save()
        self.view = (first, last)

    def generate(self):
        out = filedialog.asksaveasfilename(defaultextension=".epub", filetypes=[("EPUB", "*.epub")],
                                           initialdir=self.pdf.parent, initialfile=self.pdf.stem + ".epub")
        if not out:
            return
        self.book.title = self.title_var.get().strip() or self.pdf.stem
        self.book.author = self.author_var.get().strip()
        self.book.series = self.series_var.get().strip()
        self.book.volume = self.volume_var.get().strip()
        self.book.publisher = self.publisher_var.get().strip()
        self.book.translator = self.translator_var.get().strip()
        self.book.description = self.description
        self.save_review()
        chapters, removed = _plan(self.book, self.rows())
        self.status.configure(text="Generando EPUB…")
        lang = self.lang_var.get().strip()
        lang = lang if LANG.fullmatch(lang) else "es"
        self.run(_write_checked, (self.book, chapters, out, lang, removed, self.typography_var.get(),
                                  self.style_var.get(), True, self.separator_var.get(), self.shrink_var.get(),
                                  self.replacements, self.credits_var.get(), self.gray_var.get()),
                 lambda problems: self.saved(out, problems))
        self.partial = f"{out}.tmp"  # el nombre temporal que usa write_epub

    def saved(self, out, problems=()):
        self.status.configure(text=f"Guardado: {Path(out).name}" + (" (con avisos)" if problems else ""))
        if problems:
            messagebox.showwarning("EPUB guardado con avisos", f"EPUB guardado en:\n{out}\n\nLa revisión encontró:\n"
                                   + "\n".join(problems[:15]) + ("\n…" if len(problems) > 15 else ""))
        else:
            messagebox.showinfo("Listo", f"EPUB guardado en:\n{out}\n\nRevisado: sin problemas.")

    def load_settings(self):
        """Preferencias de la sesión anterior: opciones, última carpeta y PDF recientes."""
        try:
            self.settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
            if not isinstance(self.settings, dict):
                raise ValueError
        except (OSError, ValueError):
            self.settings = {}
        try:
            self.apply_state({name: self.settings[name] for name in PREFS if name in self.settings})
        except tk.TclError:
            pass  # valor mal escrito a mano en el .json
        self.fill_recent()

    def save_settings(self):
        fields = self.fields()
        self.settings.update({name: fields[name].get() for name in PREFS})
        try:
            SETTINGS.parent.mkdir(parents=True, exist_ok=True)
            SETTINGS.write_text(json.dumps(self.settings, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass  # sin preferencias guardadas la próxima vez; nada más

    def fill_recent(self):
        self.recent_menu.delete(0, "end")
        recent = [p for p in self.settings.get("recent", []) if isinstance(p, str)]
        for path in recent:
            self.recent_menu.add_command(label=Path(path).name, command=lambda p=path: self.open_path(p))
        if not recent:
            self.recent_menu.add_command(label="(ninguno)", state="disabled")

    def edit_description(self):
        win = tk.Toplevel(self.root)
        win.title("Sinopsis")
        win.transient(self.root)
        text = tk.Text(win, width=70, height=12, wrap="word")
        text.pack(fill="both", expand=True, padx=8, pady=8)
        text.insert("1.0", self.description)
        text.focus()

        def save():
            self.description = text.get("1.0", "end").strip()
            win.destroy()

        ttk.Button(win, text="Aceptar", command=save).pack(pady=(0, 8))

    def edit_replacements(self):
        win = tk.Toplevel(self.root)
        win.title("Reemplazos")
        win.transient(self.root)
        ttk.Label(win, text="Una regla por línea:  buscar -> reemplazo\nCambia palabras completas en textos y títulos."
                            " Pasan a los demás tomos de la serie.", padding=(8, 8, 8, 0)).pack(anchor="w")
        text = tk.Text(win, width=60, height=14, wrap="none")
        text.pack(fill="both", expand=True, padx=8, pady=8)
        text.insert("1.0", "\n".join(f"{find} -> {new}" for find, new in self.replacements))
        text.focus()

        def save():
            self.replacements = _parse_replacements(text.get("1.0", "end"))
            win.destroy()
            self.update_preview()

        def variants():
            """Propone una regla por cada nombre escrito de varias formas: las otras -> la más usada."""
            groups = convert.name_variants(self.book) if self.book else []
            have = {find for find, _ in _parse_replacements(text.get("1.0", "end"))}
            rules = [f"{other} -> {group[0]}" for group in groups for other in group[1:] if other not in have]
            if not rules:
                messagebox.showinfo("Variantes de nombres", "No se encontraron nombres escritos de varias formas.",
                                    parent=win)
                return
            lead = "\n" if text.get("1.0", "end").strip() else ""
            text.insert("end", f"{lead}# Variantes encontradas: borra las que no sean errores\n" + "\n".join(rules))

        buttons = ttk.Frame(win)
        buttons.pack(pady=(0, 8))
        ttk.Button(buttons, text="Buscar variantes de nombres", command=variants).pack(side="left", padx=4)
        ttk.Button(buttons, text="Aceptar", command=save).pack(side="left", padx=4)

    def edit_text(self):
        """Editor del texto de la sección seleccionada (de su título al siguiente): erratas, párrafos
        unidos o partidos, títulos que el análisis no vio ("# Título"), ilustraciones movidas."""
        selected = self.tree.selection()
        if not self.book or not selected or self.is_image(selected[0]):
            return
        item = selected[0]
        start = int(item) + 1
        end = next((k for k in range(start, len(self.book.blocks)) if self.book.blocks[k].kind == "heading"),
                   len(self.book.blocks))
        win = tk.Toplevel(self.root)
        win.title(f"Texto · {self.tree.set(item, 'title')}")
        win.transient(self.root)
        ttk.Label(win, text="Un párrafo por línea · «# Título» crea un título nuevo · [Ilustración N] y [Separador N]"
                            " se pueden mover o borrar · <i>cursiva</i> y <b>negrita</b>",
                  padding=(8, 8, 8, 0)).pack(anchor="w")
        text = tk.Text(win, width=100, height=32, wrap="word", undo=True)
        scroll = ttk.Scrollbar(win, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        buttons = ttk.Frame(win)
        buttons.pack(side="bottom", pady=8)
        scroll.pack(side="right", fill="y", pady=8)
        text.pack(fill="both", expand=True, padx=(8, 0), pady=8)
        text.insert("1.0", convert.section_text(self.book, start, end))
        text.edit_reset()
        text.focus()

        def save():
            self.apply_edit([start, end, text.get("1.0", "end")], item)
            win.destroy()

        ttk.Button(buttons, text="Aceptar", command=save).pack(side="left", padx=4)
        ttk.Button(buttons, text="Cancelar", command=win.destroy).pack(side="left", padx=4)

    def apply_edit(self, edit, item):
        """Aplica un texto editado: cambia los bloques, renumera la lista conservando los tipos elegidos
        y lo apunta para la revisión. Guarda una copia de antes para deshacerlo con Ctrl+Z."""
        rows = self.rows()
        self.history.append({"blocks": self.book.blocks, "candidates": self.book.candidates, "rows": rows,
                             "edits": len(self.edits), "item": item})  # edit_section crea listas nuevas
        mapping = convert.edit_section(self.book, *edit)
        self.edits.append(edit)
        self.fill_tree({str(mapping[int(k)]): v for k, v in rows.items() if int(k) in mapping})
        self.tree.selection_set(item)
        self.tree.see(item)
        self.update_preview()

    def find(self, _=None):
        """Ctrl+F: busca un texto en el libro; al elegir un resultado, las vistas previas van a él."""
        if not self.book:
            return
        win = tk.Toplevel(self.root)
        win.title("Buscar")
        win.transient(self.root)
        entry = ttk.Entry(win, width=50)
        entry.pack(fill="x", padx=8, pady=(8, 4))
        count = ttk.Label(win, text="Escribe y pulsa Intro")
        count.pack(anchor="w", padx=8)
        results = tk.Listbox(win, width=90, height=18)
        results.pack(fill="both", expand=True, padx=8, pady=8)
        matches = []
        entry.focus()

        def search(_=None):
            term = entry.get().strip()
            results.delete(0, "end")
            matches.clear()
            if len(term) < 2:
                return
            for k, block in enumerate(self.book.blocks):
                if block.kind not in ("p", "heading"):
                    continue
                plain = html.unescape(convert.TAGS.sub("", block.html))
                pos = plain.lower().find(term.lower())
                if pos >= 0 and len(matches) < 500:
                    matches.append((k, block.page, term))
                    results.insert("end", f"pág. {block.page} · …{plain[max(0, pos - 40):pos + 60]}…")
            count.configure(text=f"{len(matches)} resultados" + (" (se muestran los 500 primeros)" if len(matches) == 500 else ""))

        def pick(_=None):
            if results.curselection():
                self.show_match(*matches[results.curselection()[0]])

        entry.bind("<Return>", search)
        results.bind("<<ListboxSelect>>", pick)

    def show_match(self, k, page, term):
        """Selecciona el título de la sección donde está el bloque k y lleva las vistas previas al texto."""
        item = next((str(j) for j in range(k, -1, -1) if self.tree.exists(str(j)) and not self.is_image(str(j))), None)
        self.find_target = (page, term)
        if item is None:
            self.show_found(page, term)
        elif self.tree.selection() == (item,):
            self.update_preview()
        else:  # al cambiar la selección, Tk llama a update_preview, que aplica find_target
            self.tree.selection_set(item)
            self.tree.see(item)

    def convert_folder(self):
        """Genera un EPUB por cada PDF de una carpeta, junto al PDF, sin revisarlos uno a uno."""
        folder = filedialog.askdirectory(initialdir=self.settings.get("folder"))
        if not folder:
            return
        self.save_review()  # el PDF abierto también se convierte con lo revisado hasta ahora
        pdfs = sorted(Path(folder).glob("*.pdf"))
        existing = [p for p in pdfs if p.with_suffix(".epub").exists()]
        if existing:
            answer = messagebox.askyesnocancel(
                "Convertir carpeta", f"{len(existing)} de {len(pdfs)} PDF ya tienen su EPUB.\n\n"
                "Sí: reemplazarlos · No: convertir solo los que faltan")
            if answer is None:
                return
            if not answer:
                pdfs = [p for p in pdfs if p not in existing]
        if not pdfs:
            messagebox.showinfo("Convertir carpeta", "No hay PDF que convertir en esa carpeta.")
            return
        fields = self.fields()
        options = {name: fields[name].get() for name in PREFS}
        self.run(_batch, ([str(p) for p in pdfs], options), self.batch_done, report=True, busy="Convirtiendo…")

    def batch_done(self, results):
        self.progress.configure(value=0)
        failed = [f"{name}: {error}" for name, error, _ in results if error]
        warned = [f"{name}: {problems[0]}" + (f" (y {len(problems) - 1} más)" if len(problems) > 1 else "")
                  for name, _, problems in results if problems]
        self.status.configure(text=f"{len(results) - len(failed)} de {len(results)} EPUB generados")
        if failed or warned:
            parts = (["No se pudieron convertir:\n" + "\n".join(failed)] if failed else []) + \
                    (["Generados con avisos:\n" + "\n".join(warned)] if warned else [])
            messagebox.showwarning("Convertir carpeta", "\n\n".join(parts))
        else:
            messagebox.showinfo("Convertir carpeta", f"{len(results)} EPUB generados junto a sus PDF.")

    def set_cover(self, choice):
        """Cambia la portada (ver _cover_image)."""
        kind, value = choice
        self.book.cover = _cover_image(self.pdf_doc, choice)
        self.cover_choice = [kind, value]
        self.cover_label.configure(text=f"Portada: página {value + 1}" if kind == "page" else f"Portada: {Path(value).name}")
        self.update_preview()

    def cover_from_page(self):
        if self.book and self.pdf_doc:
            self.set_cover(("page", self.pdf_page))

    def cover_from_file(self):
        if not self.book:
            return
        path = filedialog.askopenfilename(filetypes=[("Imagen", "*.jpg *.jpeg *.png *.webp")])
        if path:
            try:
                self.set_cover(("file", path))
            except ValueError as e:
                messagebox.showerror("Error", str(e))

    def fields(self):
        """Campos de la ventana que se guardan en la revisión."""
        return {"title": self.title_var, "author": self.author_var, "series": self.series_var,
                "volume": self.volume_var, "publisher": self.publisher_var, "translator": self.translator_var,
                "lang": self.lang_var, "style": self.style_var, "separator": self.separator_var,
                "typography": self.typography_var, "shrink": self.shrink_var, "credits": self.credits_var,
                "gray": self.gray_var}

    def rows(self):
        return {item: [self.tree.set(item, "inc"), self.tree.set(item, "title")] for item in self.tree.get_children()}

    def save_review(self):
        if not (self.book and self.pdf):
            return
        state = {name: var.get() for name, var in self.fields().items()}
        state.update(blocks=self.analyzed, edits=self.edits, description=self.description, cover=self.cover_choice,
                     replacements=self.replacements, rows=self.rows())
        try:
            state["pdf"] = _fingerprint(self.pdf)
            _review_path(self.pdf).write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass  # PDF borrado o carpeta de solo lectura: se pierde la revisión guardada, no el trabajo en curso

    def apply_state(self, state):
        """Pone en la ventana los campos y reemplazos de una revisión guardada (o heredada)."""
        for name, var in self.fields().items():
            if name in state:
                var.set(state[name])
        if self.style_var.get() not in convert.STYLES:  # el .json se puede editar a mano
            self.style_var.set("Clásico")
        try:
            self.replacements = [[str(a), str(b)] for a, b in state["replacements"]]
        except (KeyError, TypeError, ValueError):
            pass  # sin reemplazos guardados, o mal escritos a mano

    def load_review(self):
        """Recupera la revisión guardada de este PDF. False si no hay o si el PDF cambió desde entonces."""
        state = _read_review(self.pdf, len(self.book.blocks)) if self.pdf else None
        if not state:
            return False
        self.edits = _replay(self.book, state)
        self.fill_tree({})
        for item, (kind, title) in state["rows"].items():
            if self.tree.exists(item) and kind in self.cycle(item):
                self.set_kind(item, kind)
                self.tree.set(item, "title", title)
        self.apply_state(state)
        self.description = state.get("description", "")
        if state.get("cover"):
            try:
                self.set_cover(state["cover"])
            except Exception:
                pass  # la imagen de portada ya no existe: se queda la del PDF
        return True


if __name__ == "__main__":
    multiprocessing.freeze_support()  # en el .exe, el proceso de trabajo vuelve a lanzar el ejecutable
    root = tk.Tk()
    window = App(root)
    if len(sys.argv) > 1:  # PDF arrastrado sobre el .exe o "Abrir con…"
        root.after(200, window.open_path, sys.argv[1])
    root.mainloop()
