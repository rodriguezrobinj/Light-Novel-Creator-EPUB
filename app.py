"""Ventana para convertir PDFs de novelas ligeras a EPUB."""
import html
import multiprocessing
import os
import queue
import tempfile
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import pymupdf

import convert

ON, OFF = "✔", "✘"
PREVIEW_BOX = (400, 560)  # ancho y alto máximos de cada vista previa, en píxeles
PREVIEW_BLOCKS = 60       # bloques que entran en el EPUB de la vista previa: basta para su primera página


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
        ttk.Label(root, text="Clic en Incluir: marcar o desmarcar · Doble clic en el título: editar",
                  padding=(8, 0)).pack(side="bottom", anchor="w")

        middle = ttk.Frame(root, padding=8)
        middle.pack(fill="both", expand=True)
        self.views = ttk.Frame(middle, padding=(8, 0, 0, 0))
        self.views.pack(side="right", fill="y")
        self.pdf_caption, self.pdf_view = self.preview_pane(self.views, "PDF")
        self.epub_caption, self.epub_view = self.preview_pane(self.views, "EPUB")
        frame = ttk.Frame(middle)
        frame.pack(side="left", fill="both", expand=True)
        self.tree = ttk.Treeview(frame, columns=("inc", "title", "page"), show="headings", selectmode="browse")
        for col, text, width in (("inc", "Incluir", 60), ("title", "Capítulo", 300), ("page", "Página", 60)):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, anchor="w" if col == "title" else "center", stretch=col == "title")
        self.scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=self.on_scroll)
        self.tree.pack(side="left", fill="both", expand=True)
        self.scroll.pack(side="right", fill="y")
        self.tree.bind("<Button-1>", self.on_click)
        self.tree.bind("<Double-1>", self.on_double_click)
        self.tree.bind("<<TreeviewSelect>>", self.update_preview)
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
        if self.pdf_doc:
            self.pdf_doc.close()
        Path(self.preview_path).unlink(missing_ok=True)
        self.root.destroy()

    def preview_pane(self, parent, name):
        pane = ttk.Frame(parent)
        pane.pack(side="left", padx=4, anchor="n")
        caption = ttk.Label(pane, text=name)
        caption.pack()
        view = ttk.Label(pane, text="Selecciona un título", anchor="center")
        view.image = None  # referencia a la imagen: sin ella, Tk la borra
        view.pack()
        return caption, view

    def show_page(self, view, page):
        """Dibuja una página (del PDF o del EPUB) ajustada al tamaño de la vista previa."""
        self.views.update_idletasks()
        room = self.views.winfo_height() - 30  # alto disponible menos la leyenda
        height = min(PREVIEW_BOX[1], room) if room > 150 else PREVIEW_BOX[1]  # sin dibujar aún: tamaño normal
        zoom = min(PREVIEW_BOX[0] / page.rect.width, height / page.rect.height)
        view.image = tk.PhotoImage(data=page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).tobytes("png"))
        view.configure(image=view.image, text="")

    def update_preview(self, _=None):
        """Muestra la página del PDF del título seleccionado y cómo quedará en el EPUB."""
        selected = self.tree.selection()
        if not selected or not self.book:
            return
        item = selected[0]
        index = int(item)
        page = self.book.blocks[index].page
        try:
            self.show_page(self.pdf_view, self.pdf_doc[page - 1])
            self.pdf_caption.configure(text=f"PDF · página {page}")
        except Exception:
            self.pdf_view.image = None
            self.pdf_view.configure(image="", text="Sin vista previa")
        # EPUB mínimo con solo este trozo (del título al siguiente): rápido de escribir y de maquetar
        try:
            blocks = self.book.blocks
            end = next((k for k in range(index + 1, len(blocks)) if blocks[k].kind == "heading"), len(blocks))
            part = convert.Book(self.book.title, "", self.book.cover, blocks[index:min(end, index + PREVIEW_BLOCKS)], [])
            chapter = self.tree.set(item, "inc") == ON
            convert.write_epub(part, [(0, self.tree.set(item, "title"))] if chapter else [], self.preview_path)
            with pymupdf.open(self.preview_path) as epub:
                self.show_page(self.epub_view, epub.load_page((1, 0)))  # el capítulo 0 es la portada
            self.epub_caption.configure(text="EPUB · así se verá (" + ("capítulo" if chapter else "subtítulo") + ")")
        except Exception:
            self.epub_view.image = None
            self.epub_view.configure(image="", text="Sin vista previa")

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
                self.tree.insert("", "end", iid=str(i), values=(mark, html.unescape(block.html), block.page))
        self.gen_btn.state(["!disabled"])
        self.progress.configure(value=0)
        found, others = len(chapters), len(self.tree.get_children()) - len(chapters)
        text = f"{found} capítulos detectados" if found else "No se detectaron capítulos: el EPUB tendrá una sola sección"
        self.status.configure(text=text + (f" y {others} subtítulos" if others else ""))

    def on_click(self, event):
        item = self.tree.identify_row(event.y)
        if item and self.tree.identify_column(event.x) == "#1":
            self.tree.set(item, "inc", OFF if self.tree.set(item, "inc") == ON else ON)
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
        self.run(convert.write_epub, (self.book, chapters, out), lambda _: self.saved(out))
        self.partial = f"{out}.tmp"  # el nombre temporal que usa write_epub

    def saved(self, out):
        self.status.configure(text=f"Guardado: {Path(out).name}")
        messagebox.showinfo("Listo", f"EPUB guardado en:\n{out}")


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
