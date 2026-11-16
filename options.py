"""Ventana «Opciones»: todos los ajustes en un sitio, como las Preferencias de Acrobat.
La tabla OPTIONS declara cada opción una vez; páginas, buscador y ayudas salen de ella."""
import copy
import queue
import threading
import unicodedata
from collections import namedtuple
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QPushButton, QRadioButton, QScrollArea, QSpinBox,
                               QSplitter, QVBoxLayout, QWidget)
import shiboken6

import qtk
import theme
import translate

BOOK, APP = "El libro", "La aplicación"
CATEGORIES = {BOOK: ("Perfil y formato", "Texto", "Ilustraciones", "PDF"),
              APP: ("Apariencia", "Vista previa", "Traducción", "Carpeta vigilada", "Informe de calidad")}
Option = namedtuple("Option", "category key kind label help")
OPTIONS = (
    Option("Perfil y formato", "profile", "choice", "Perfil",
           "Fija las opciones que importan para ese lector: gris, tamaño de imagen, formato Kobo…"),
    Option("Perfil y formato", "epub2", "check", "EPUB 2 (lectores antiguos)", "Para lectores de hace años que no abren EPUB 3."),
    Option("Perfil y formato", "kepub", "check", "Formato Kobo (KEPUB)",
           "El Kobo muestra páginas y estadísticas de lectura; el archivo acaba en .kepub.epub."),
    Option("Perfil y formato", "lang", "language", "Idioma del libro",
           "Decide cómo se parten las palabras, la voz de lectura y los avisos del informe."),
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
# Lo que se guarda por libro y en «Libros nuevos». El aspecto (estilo, separador y letra, con la ruta de la propia)
# está en la barra de abajo de la ventana: se cambia mirando la vista previa
BOOK_KEYS = tuple(o.key for o in OPTIONS if o.category in CATEGORIES[BOOK]) + ("style", "separator", "font", "font_file")


def fold(text):
    """Sin tildes ni mayúsculas: «guion» encuentra «guiones» y «Guión»."""
    return "".join(c for c in unicodedata.normalize("NFD", str(text).lower()) if unicodedata.category(c) != "Mn")


def find(text):
    """Opciones cuyo nombre o ayuda contiene text."""
    needle = fold(text).strip()
    return [o for o in OPTIONS if needle and (needle in fold(o.label) or needle in fold(o.help))]


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
        empty = QLabel("")
        empty.setObjectName("muted")
        empty.setWordWrap(True)
        empty.hide()
        side.addWidget(search)
        side.addWidget(listing, 1)
        side.addWidget(empty)
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
        self.items = {}  # categoría o encabezado -> QListWidgetItem
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
                      "put": self.put, "empty": empty, "copy": None}
        search.textChanged.connect(lambda _: self.filter())
        listing.currentItemChanged.connect(lambda item, _: item and self.show(item.text().strip()))
        this_book.toggled.connect(lambda on: self.set_scope("book" if on else "new"))
        self.category = None
        pages = [name for group in CATEGORIES.values() for name in group]  # ajustes.json se puede editar a mano
        self.show(category if category in pages else CATEGORIES[BOOK][0])
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
        if key == "theme":  # el tema se ve al momento; Cancelar lo devuelve (restore_options)
            self.owner.theme_var.set(value)
            self.owner.apply_theme()
        if key in BOOK_KEYS and self.scope == "book":
            self.owner.update_preview()
        if len(changes) > 1:
            self.show(self.category)  # las casillas del perfil cambiaron

    def filter(self):
        """Solo las categorías con alguna opción que coincide; la página elegida resalta las que coinciden."""
        text = self.parts["search"].text()
        names = [name for group in CATEGORIES.values() for name in group]
        hits = {o.category for o in find(text)} if text.strip() else set(names)
        for header, group in CATEGORIES.items():
            self.items[header].setHidden(not any(name in hits for name in group))
            for name in group:
                self.items[name].setHidden(name not in hits)
        self.parts["empty"].setText("" if hits else f"Ninguna opción contiene «{text.strip()}»")
        self.parts["empty"].setVisible(not hits)
        self.show(self.category if self.category in hits or not hits else next(n for n in names if n in hits))

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
        self.parts["copy"] = None
        if self.is_book() and self.scope == "book":
            copy_btn = QPushButton("Usar también para libros nuevos")
            copy_btn.setToolTip("Copia los valores de esta página a «Libros nuevos»")
            keys = [o.key for o in OPTIONS if o.category == category]
            copy_btn.clicked.connect(lambda: self.defaults.update({k: self.owner.fields()[k].get() for k in keys}))
            layout.addWidget(copy_btn, 0, Qt.AlignmentFlag.AlignLeft)
            self.parts["copy"] = copy_btn
        layout.addStretch(1)
        self.scroll.setWidget(page)  # la página anterior se borra con su scroll

    def add_option(self, layout, option):
        """Control de una opción y, debajo, su ayuda en gris."""
        key, value = option.key, self.get(option.key)
        if option.kind == "translate":
            self.translation_page(layout, option)
            return
        if option.kind == "folder":
            widget = QLabel(value or "Ninguna")
            widget.setWordWrap(True)
            pick, stop = QPushButton("Elegir…"), QPushButton("Dejar de vigilar")
            stop.setEnabled(bool(value))
            pick.clicked.connect(lambda: self.pick_folder(key))
            stop.clicked.connect(lambda: (self.put(key, ""), self.show(self.category)))
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            line.addWidget(widget, 1)
            line.addWidget(pick)
            line.addWidget(stop)
        elif option.kind == "check":
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
                widget.addItems(values)
                widget.setEditable(option.kind == "language")
                widget.setCurrentText(str(value))
                widget.setMinimumContentsLength(18)
                if option.kind == "language":
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
        fixed = self.profiles.get(self.get("profile"), {})
        if key in fixed and key != "profile":
            note.setText(f"{option.help} Fijada por el perfil {self.get('profile')}.")
        text = self.parts["search"].text().strip()
        if text and option in find(text):
            row.setStyleSheet(f"background: {theme.colors(self.owner.dark)['sel']};")
        layout.addWidget(row)
        layout.addWidget(note)
        if option.kind == "folder":
            self.parts["watch_error"] = None
            if value and not Path(value).is_dir():
                error = QLabel("La carpeta ya no existe: elige otra o deja de vigilarla.")
                error.setObjectName("warn")
                layout.addWidget(error)
                self.parts["watch_error"] = error
        self.parts["widgets"][key], self.parts["notes"][key] = widget, note

    def translation_page(self, layout, option):
        """Motor, modelo, URL y clave del motor elegido, e idiomas con los que empieza Traducir. La clave se guarda
        cifrada (translate.protect); todo espera a Aceptar."""
        data = self.values["translate"]
        for name in ("models", "keys"):
            if not isinstance(data.get(name), dict):
                data[name] = {}
        engine, model, base, key = QComboBox(), QComboBox(), QLineEdit(), QLineEdit()
        src, dst, status = QComboBox(), QComboBox(), QLabel("")
        engine.addItems(list(translate.ENGINES))
        engine.setCurrentIndex(max(0, engine.findText(str(data.get("engine", "")))))
        model.setEditable(True)  # se escribe a mano o se elige de los que da «Buscar modelos»
        model.setMinimumContentsLength(22)
        base.setPlaceholderText("http://localhost:11434/v1")
        base.setMinimumWidth(260)
        key.setEchoMode(QLineEdit.EchoMode.Password)
        key.setMinimumWidth(260)
        eye = QPushButton(theme.ICONS["show"])
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
        rows, shown = {}, {"name": engine.currentText()}  # el motor cuyos campos se ven ahora

        def labelled(text, *widgets):
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            line.addWidget(QLabel(text))
            for widget in widgets:
                line.addWidget(widget)
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
            """Lo escrito, al motor cuyos campos se ven; la clave, cifrada (sin cifrar no se guarda)."""
            name = shown["name"]
            data.update(engine=engine.currentText(), src=src.currentData(), dst=dst.currentData())
            data["models"][name] = model.currentText().strip()
            try:
                data["keys"][name] = translate.protect(key.text().strip())
            except OSError:
                data["keys"].pop(name, None)
            if translate.ENGINES[name].get("custom"):
                data["base"] = base.text().strip()

        def show_engine():
            name = shown["name"] = engine.currentText()
            info = translate.ENGINES[name]
            for widget in (model, base, key):
                widget.blockSignals(True)  # poner los valores del motor no es escribir
            model.clear()  # la lista buscada era de otro motor
            model.setEditText(data["models"].get(name) or info.get("model", ""))
            key.setText(translate.unprotect(data["keys"].get(name, "")))
            base.setText(data.get("base") or info.get("base", ""))
            for widget in (model, base, key):
                widget.blockSignals(False)
            rows["model"].setVisible("model" in info)
            rows["base"].setVisible(bool(info.get("custom")))
            rows["key"].setVisible(info["kind"] != "google")
            key.setPlaceholderText("Opcional" if info.get("custom") else "Clave API")
            status.setText("")

        def find_models():
            """La lista de modelos de la API, en un hilo: sin conexión tardaría en fallar."""
            name, info = engine.currentText(), translate.ENGINES[engine.currentText()]
            if info.get("key") and not key.text().strip():
                status.setText(f"Falta la clave de {name}: sin ella la API no da la lista.")
                return
            found, url = queue.Queue(), base.text().strip() if info.get("custom") else ""
            typed = key.text().strip()

            def ask():
                try:
                    found.put(translate.models(name, typed, url))
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
                if isinstance(result, str) or not result or engine.currentText() != name:
                    if engine.currentText() == name:
                        status.setText(result or f"{name} no devolvió ningún modelo.")
                    return
                current = model.currentText().strip()
                model.clear()
                model.addItems(result)
                model.setEditText(current or result[0])
                keep()
                count = f"{len(result)} modelo" + ("s" if len(result) != 1 else "")
                status.setText(f"{name} ofrece {count}: elige uno en «Modelo».")

            qtk.after(200, wait)

        engine.activated.connect(lambda _: (keep(), show_engine(), keep()))
        model.currentTextChanged.connect(lambda _: keep())
        base.textChanged.connect(lambda _: keep())
        key.textChanged.connect(lambda _: keep())
        src.activated.connect(lambda _: keep())
        dst.activated.connect(lambda _: keep())
        models_btn.clicked.connect(find_models)
        show_engine()
        self.parts.update(engine=engine, model=model, base=base, key=key, eye=eye, src=src, dst=dst,
                          models=find_models, status=status)

    def pick_folder(self, key):
        folder = self.owner.ask_folder()
        if folder:
            self.put(key, folder)
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
