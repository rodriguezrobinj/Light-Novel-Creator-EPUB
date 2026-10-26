"""Ventana para convertir PDFs de novelas ligeras a EPUB."""
import html
import multiprocessing
import os
import queue
import tempfile
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import pymupdf

import convert

ON, OFF, CUT = "Capítulo", "Subtítulo", "Quitar"  # tipo de cada título de la lista
NEXT = {ON: OFF, OFF: CUT, CUT: ON}                 # cada clic pasa al siguiente
KIND_TAG = {ON: "chapter", OFF: "subtitle", CUT: "cut"}  # estilo de cada fila según su tipo
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
        self.device_var = tk.StringVar(value='E-reader 6"')
        self.dark_var = tk.BooleanVar(value=False)
        base = tkfont.nametofont("TkDefaultFont")
        self.fonts = {name: base.copy() for name in ("chapter", "subtitle", "cut")}
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
        self.file_label = ttk.Label(top, text="Ningún archivo")
        self.file_label.pack(side="left", padx=8)

        meta = ttk.Frame(root, padding=(8, 0))
        meta.pack(fill="x")
        self.title_var, self.author_var = tk.StringVar(), tk.StringVar()
        self.series_var, self.volume_var = tk.StringVar(), tk.StringVar()
        for label, var, width in (("Título:", self.title_var, 45), ("Autor:", self.author_var, 22),
                                  ("Serie:", self.series_var, 35), ("Vol.:", self.volume_var, 5)):
            ttk.Label(meta, text=label).pack(side="left")
            ttk.Entry(meta, textvariable=var, width=width).pack(side="left", padx=(4, 12))

        # La barra inferior y la ayuda se empaquetan antes que el centro: pack reparte el espacio en
        # orden, y así el centro (con las vistas previas) es el que encoge en pantallas bajas.
        bottom = ttk.Frame(root, padding=8)
        bottom.pack(side="bottom", fill="x")
        self.progress = ttk.Progressbar(bottom, length=280)
        self.progress.pack(side="left")
        self.status = ttk.Label(bottom, text="")
        self.status.pack(side="left", padx=8)
        self.gen_btn = ttk.Button(bottom, text="Generar EPUB…", command=self.generate, state="disabled")
        self.gen_btn.pack(side="right")
        self.typography_var = tk.BooleanVar(value=True)  # comillas «», puntos suspensivos, raya, espacios
        ttk.Checkbutton(bottom, text="Limpieza tipográfica", variable=self.typography_var,
                        command=self.update_preview).pack(side="right", padx=12)
        ttk.Label(root, text="Clic en Tipo: capítulo, subtítulo o quitar · Doble clic en el título: editar",
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
        self.epub_view = ttk.Label(epub_pane[2], text="Selecciona un título", anchor="center")
        self.epub_view.image = None  # referencia a la imagen: sin ella, Tk la borra
        self.epub_view.pack()
        device = ttk.Combobox(epub_pane[1], textvariable=self.device_var, values=list(DEVICES), width=11,
                              state="readonly")
        device.pack(side="left", padx=(8, 4))
        device.bind("<<ComboboxSelected>>", lambda _: self.draw_epub())
        ttk.Checkbutton(epub_pane[1], text="Modo oscuro", variable=self.dark_var,
                        command=self.draw_epub).pack(side="left")
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
        for tag in KIND_TAG.values():
            self.tree.tag_configure(tag, font=self.fonts[tag],
                                    foreground={"chapter": "", "subtitle": "#555555", "cut": "#9a9a9a"}[tag])
        root.after(100, self.poll)

    def run(self, work, args, done, report=False):
        """Ejecuta work(*args) en otro proceso y llama a done(resultado) en la ventana.
        Con report=True, work recibe progress=... y la barra avanza."""
        self.open_btn.state(["disabled"])
        self.gen_btn.state(["disabled"])
        self.done = done
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
            self.status.configure(text=f"Analizando… {done * 100 // total} %")
        self.root.after(100, self.poll)

    def finish(self, event):
        """Cierra el trabajo en curso: desbloquea la ventana y entrega el resultado o el error."""
        done, self.done, self.latest, self.partial = self.done, None, None, None
        self.open_btn.state(["!disabled"])
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

    def render(self, page, dark=False):
        """Imagen de una página (del PDF o del EPUB) ajustada al alto disponible, y su escala."""
        self.views.update_idletasks()
        room = self.views.winfo_height() - PREVIEW_CHROME
        height = min(PREVIEW_BOX[1], room) if room > 150 else PREVIEW_BOX[1]  # sin dibujar aún: tamaño normal
        zoom = min(PREVIEW_BOX[0] / page.rect.width, height / page.rect.height)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
        if dark:  # aproximación del modo oscuro de los lectores: invierte también las ilustraciones
            pix.invert_irect()
        return tk.PhotoImage(data=pix.tobytes("ppm")), zoom

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
        self.epub_page, self.epub_pages = 0, 0
        self.epub_cut = self.tree.set(item, "inc") == CUT
        if not self.epub_cut:
            # EPUB mínimo con solo esta sección (del título al siguiente): rápido de escribir y de maquetar
            try:
                blocks = self.book.blocks
                end = next((k for k in range(index + 1, len(blocks)) if blocks[k].kind == "heading"), len(blocks))
                part = convert.Book(self.book.title, "", self.book.cover, blocks[index:end], [])
                chapter = self.tree.set(item, "inc") == ON
                convert.write_epub(part, [(0, self.tree.set(item, "title"))] if chapter else [], self.preview_path,
                                   typography=self.typography_var.get())
            except Exception:
                self.epub_pages = -1  # no se pudo generar
        self.draw_epub()

    def draw_pdf(self):
        canvas = self.pdf_view
        canvas.delete("all")
        try:
            canvas.image, zoom = self.render(self.pdf_doc[self.pdf_page])
        except Exception:
            canvas.image = None
            canvas.configure(width=PREVIEW_BOX[0], height=60)
            canvas.create_text(PREVIEW_BOX[0] // 2, 30, text="Sin vista previa")
            return
        canvas.configure(width=canvas.image.width(), height=canvas.image.height())
        canvas.create_image(0, 0, anchor="nw", image=canvas.image)
        if self.highlight and self.highlight[0] == self.pdf_page:  # recuadro sobre la línea tomada como título
            x0, y0, x1, y1 = (v * zoom for v in self.highlight[1])
            canvas.create_rectangle(x0 - 3, y0 - 2, x1 + 3, y1 + 2, outline="red", width=2, tags="title")
        self.pdf_caption.configure(text=f"PDF · página {self.pdf_page + 1}")
        self.mark_current()

    def draw_epub(self):
        view = self.epub_view
        if self.epub_cut or self.epub_pages < 0:
            view.image = None
            view.configure(image="", text="Esta sección no irá en el EPUB" if self.epub_cut else "Sin vista previa")
            self.epub_caption.configure(text="EPUB")
            return
        try:
            with pymupdf.open(self.preview_path) as epub:
                width, height = DEVICES[self.device_var.get()]
                epub.layout(width=width, height=height, fontsize=11)
                self.epub_pages = epub.chapter_page_count(1)  # el capítulo 0 es la portada
                self.epub_page = max(0, min(self.epub_page, self.epub_pages - 1))
                view.image, _ = self.render(epub.load_page((1, self.epub_page)), dark=self.dark_var.get())
        except Exception:
            view.image = None
            view.configure(image="", text="Sin vista previa")
            return
        view.configure(image=view.image, text="")
        self.epub_caption.configure(text=f"EPUB · {self.device_var.get()} · {self.epub_page + 1}/{self.epub_pages}")

    def pdf_step(self, delta):
        if self.pdf_doc:
            self.pdf_page = max(0, min(self.pdf_page + delta, self.pdf_doc.page_count - 1))
            self.draw_pdf()

    def epub_step(self, delta):
        if self.epub_pages > 0:
            self.epub_page = max(0, min(self.epub_page + delta, self.epub_pages - 1))
            self.draw_epub()

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
        for n in range(start, end):
            page = self.pdf_doc[n]
            zoom = THUMB_HEIGHT / page.rect.height
            image = tk.PhotoImage(data=page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).tobytes("ppm"))
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

    def set_kind(self, item, kind):
        self.tree.set(item, "inc", kind)
        self.tree.item(item, tags=(KIND_TAG[kind],))

    def open_pdf(self):
        path = filedialog.askopenfilename(filetypes=[("PDF", "*.pdf")])
        if not path:
            return
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
        # Todos los títulos: los capítulos marcados; los subtítulos ("El lado de…") desmarcados, por si
        # alguno debe ser capítulo. Un título desmarcado sale en el EPUB como subtítulo.
        chapters = set(book.candidates)
        for i, block in enumerate(book.blocks):
            if block.kind == "heading":
                mark = ON if i in chapters else OFF
                self.tree.insert("", "end", iid=str(i), values=(mark, html.unescape(block.html), block.page),
                                 tags=(KIND_TAG[mark],))
        self.start_thumbs()
        self.gen_btn.state(["!disabled"])
        self.progress.configure(value=0)
        found, others = len(chapters), len(self.tree.get_children()) - len(chapters)
        text = f"{found} capítulos detectados" if found else "No se detectaron capítulos: el EPUB tendrá una sola sección"
        self.status.configure(text=text + (f" y {others} subtítulos" if others else ""))

    def on_click(self, event):
        item = self.tree.identify_row(event.y)
        if item and self.tree.identify_column(event.x) == "#1":
            self.set_kind(item, NEXT[self.tree.set(item, "inc")])
            self.update_preview()

    def on_double_click(self, event):
        item = self.tree.identify_row(event.y)
        column = self.tree.identify_column(event.x)
        if item and column == "#1":  # Tk manda el segundo clic rápido aquí, no a on_click
            self.on_click(event)
        if not item or column != "#2":
            return
        x, y, w, h = self.tree.bbox(item, "title")
        entry = ttk.Entry(self.tree)
        entry.insert(0, self.tree.set(item, "title"))
        entry.select_range(0, "end")
        entry.place(x=x, y=y, width=w, height=h)
        entry.focus()

        def save(_=None):
            if not entry.winfo_exists():  # ya cerrado por otro camino (scroll, Return, Escape)
                return
            if entry.get().strip():
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
        chapters = [(int(item), self.tree.set(item, "title")) for item in self.tree.get_children()
                    if self.tree.set(item, "inc") == ON]
        self.status.configure(text="Generando EPUB…")
        removed = [int(item) for item in self.tree.get_children() if self.tree.set(item, "inc") == CUT]
        self.run(convert.write_epub, (self.book, chapters, out, "es", removed, self.typography_var.get()),
                 lambda _: self.saved(out))
        self.partial = f"{out}.tmp"  # el nombre temporal que usa write_epub

    def saved(self, out):
        self.status.configure(text=f"Guardado: {Path(out).name}")
        messagebox.showinfo("Listo", f"EPUB guardado en:\n{out}")


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
