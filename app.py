"""Ventana para convertir PDFs de novelas ligeras a EPUB."""
import html
import multiprocessing
import queue
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import convert

ON, OFF = "✔", "✘"


def _worker(events, work, args, report):
    """Corre en otro proceso: PyMuPDF retiene el GIL y, en un hilo, trabaría la ventana."""
    try:
        if report:
            result = work(*args, progress=lambda done, total: events.put(("progress", done, total)))
        else:
            result = work(*args)
        events.put(("done", result))
    except Exception as e:
        events.put(("error", str(e)))


class App:
    def __init__(self, root):
        self.root, self.book, self.pdf = root, None, None
        self.events = multiprocessing.Queue()  # el proceso de trabajo envía aquí progreso y resultado
        self.done = None                       # qué hacer con el resultado del trabajo en curso
        self.latest = None                     # último progreso (hecho, total) pendiente de pintar
        root.title("PDF a EPUB")
        root.geometry("780x560")

        top = ttk.Frame(root, padding=8)
        top.pack(fill="x")
        self.open_btn = ttk.Button(top, text="Abrir PDF…", command=self.open_pdf)
        self.open_btn.pack(side="left")
        self.file_label = ttk.Label(top, text="Ningún archivo")
        self.file_label.pack(side="left", padx=8)

        meta = ttk.Frame(root, padding=(8, 0))
        meta.pack(fill="x")
        self.title_var, self.author_var = tk.StringVar(), tk.StringVar()
        ttk.Label(meta, text="Título:").pack(side="left")
        ttk.Entry(meta, textvariable=self.title_var, width=45).pack(side="left", padx=(4, 12))
        ttk.Label(meta, text="Autor:").pack(side="left")
        ttk.Entry(meta, textvariable=self.author_var, width=25).pack(side="left", padx=4)

        frame = ttk.Frame(root, padding=8)
        frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(frame, columns=("inc", "title", "page"), show="headings", selectmode="browse")
        for col, text, width in (("inc", "Incluir", 60), ("title", "Capítulo", 560), ("page", "Página", 70)):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, anchor="w" if col == "title" else "center", stretch=col == "title")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<Button-1>", self.on_click)
        self.tree.bind("<Double-1>", self.on_double_click)
        ttk.Label(root, text="Clic en Incluir: marcar o desmarcar · Doble clic en el título: editar",
                  padding=(8, 0)).pack(anchor="w")

        bottom = ttk.Frame(root, padding=8)
        bottom.pack(fill="x")
        self.progress = ttk.Progressbar(bottom, length=280)
        self.progress.pack(side="left")
        self.status = ttk.Label(bottom, text="")
        self.status.pack(side="left", padx=8)
        self.gen_btn = ttk.Button(bottom, text="Generar EPUB…", command=self.generate, state="disabled")
        self.gen_btn.pack(side="right")
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
        for _ in range(1000):  # tope: con el proceso enviando sin parar, vaciar la cola no terminaría
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
        done, self.done, self.latest = self.done, None, None
        self.open_btn.state(["!disabled"])
        if self.book:
            self.gen_btn.state(["!disabled"])
        if event[0] == "done":
            done(event[1])
        else:
            self.status.configure(text="Error")
            messagebox.showerror("Error", event[1])

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
        self.title_var.set(book.title)
        self.author_var.set(book.author)
        for i in book.candidates:
            block = book.blocks[i]
            self.tree.insert("", "end", iid=str(i), values=(ON, html.unescape(block.html), block.page))
        self.gen_btn.state(["!disabled"])
        self.progress.configure(value=0)
        found = len(book.candidates)
        self.status.configure(text=f"{found} capítulos detectados" if found else
                              "No se detectaron capítulos: el EPUB tendrá una sola sección")

    def on_click(self, event):
        item = self.tree.identify_row(event.y)
        if item and self.tree.identify_column(event.x) == "#1":
            self.tree.set(item, "inc", OFF if self.tree.set(item, "inc") == ON else ON)

    def on_double_click(self, event):
        item = self.tree.identify_row(event.y)
        if not item or self.tree.identify_column(event.x) != "#2":
            return
        x, y, w, h = self.tree.bbox(item, "title")
        entry = ttk.Entry(self.tree)
        entry.insert(0, self.tree.set(item, "title"))
        entry.select_range(0, "end")
        entry.place(x=x, y=y, width=w, height=h)
        entry.focus()

        def save(_=None):
            if entry.get().strip():
                self.tree.set(item, "title", entry.get().strip())
            entry.destroy()

        entry.bind("<Return>", save)
        entry.bind("<FocusOut>", save)
        entry.bind("<Escape>", lambda _: entry.destroy())

    def generate(self):
        out = filedialog.asksaveasfilename(defaultextension=".epub", filetypes=[("EPUB", "*.epub")],
                                           initialdir=self.pdf.parent, initialfile=self.pdf.stem + ".epub")
        if not out:
            return
        self.book.title = self.title_var.get().strip() or self.pdf.stem
        self.book.author = self.author_var.get().strip()
        chapters = [(int(item), self.tree.set(item, "title")) for item in self.tree.get_children()
                    if self.tree.set(item, "inc") == ON]
        self.status.configure(text="Generando EPUB…")
        self.run(convert.write_epub, (self.book, chapters, out), lambda _: self.saved(out))

    def saved(self, out):
        self.status.configure(text=f"Guardado: {Path(out).name}")
        messagebox.showinfo("Listo", f"EPUB guardado en:\n{out}")


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
