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
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QFrame, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QPushButton, QRadioButton, QScrollArea, QSpinBox,
                               QSplitter, QVBoxLayout, QWidget)
import shiboken6

import grammar
import qtk
import theme
import translate

BOOK, APP = "El libro", "La aplicación"
CATEGORIES = {BOOK: ("Perfil y formato", "Aspecto", "Texto", "Páginas", "Ilustraciones", "PDF"),
              APP: ("Apariencia", "Inicio", "Salida", "Vista previa", "Editor", "Ortografía", "Traducción",
                    "Carpeta vigilada", "Informe de calidad")}
Option = namedtuple("Option", "category key kind label help")
SECTION = "section"  # un título dentro de la página, no una opción


def _section(category, label):
    return Option(category, "", SECTION, label, "")


OPTIONS = (
    Option("Perfil y formato", "profile", "choice", "Perfil",
           "Fija las opciones que importan para ese lector: gris, tamaño de imagen, formato Kobo…"),
    Option("Perfil y formato", "epub2", "check", "EPUB 2 (lectores antiguos)", "Para lectores de hace años que no abren EPUB 3."),
    Option("Perfil y formato", "kepub", "check", "Formato Kobo (KEPUB)",
           "El Kobo muestra páginas y estadísticas de lectura; el archivo acaba en .kepub.epub."),
    Option("Perfil y formato", "lang", "language", "Idioma del libro",
           "Decide cómo se parten las palabras, la voz de lectura y los avisos del informe."),
    _section("Aspecto", "Texto"),
    Option("Aspecto", "style", "choice", "Estilo",
           "La maqueta del libro: márgenes, sangría, títulos. «Original», con un EPUB: su CSS, sin tocar. También en "
           "la barra de abajo, junto a la vista previa."),
    Option("Aspecto", "font", "choice", "Letra",
           "«Del lector» deja elegir la letra en el lector. Una propia (.ttf, .otf), con «Otra…» en la barra de abajo."),
    Option("Aspecto", "separator", "choice", "Separador de escena",
           "Lo que va entre dos escenas. Uno escrito a mano o «Icono propio», en la barra de abajo."),
    _section("Aspecto", "Títulos, notas y recuadros"),
    Option("Aspecto", "heading_font", "choice", "Fuente de títulos",
           "Los títulos de capítulo con otra letra: Eagle Lake es caligráfica. «Archivo propio» usa el de abajo."),
    Option("Aspecto", "heading_font_file", "file", "Archivo de fuente de títulos", "Un .ttf o .otf, para «Archivo propio»."),
    Option("Aspecto", "note_style", "choice", "Llamada de nota", "Cómo se ve el número de la nota en el texto."),
    Option("Aspecto", "box_style", "choice", "Estilo de recuadros",
           "«Pantalla de juego»: fondo negro, brillo azul y letra blanca, como las ventanas de opciones de un juego."),
    Option("Texto", "typography", "check", "Limpieza tipográfica",
           "Comillas «», puntos suspensivos, raya de diálogo y espacios, al estilo RAE."),
    Option("Texto", "hyphenate", "check", "Partir palabras con guiones (Kindle)",
           "El Kindle apenas parte palabras en español: sin esto el texto justificado queda con huecos."),
    Option("Texto", "horizontal", "check", "Texto en horizontal (EPUB japoneses)",
           "Un EPUB japonés escrito en vertical sale de izquierda a derecha, como un libro en español."),
    Option("Texto", "autocase", "check", "Corregir las mayúsculas al abrir si vienen en MAYÚSCULAS",
           "Los títulos escritos todo en mayúsculas pasan a mayúsculas al estilo español."),
    _section("Páginas", "Al principio"),
    Option("Páginas", "credits", "check", "Página de título y créditos",
           "Tras la portada: título, volumen y los créditos por rol, con enlaces. Sus datos, en «Editar datos» → "
           "«Más datos…»."),
    Option("Páginas", "synopsis", "check", "Página de sinopsis",
           "Tras la portada, la sinopsis del libro. **negrita** y *cursiva* en la sinopsis se ven así."),
    Option("Páginas", "logo", "file", "Logo del grupo",
           "Una imagen PNG o JPG en su propia página, tras la página de título. En «Libros nuevos», para todos."),
    Option("Páginas", "contents", "check", "Índice («Contenido»)",
           "Una página con los capítulos y sus subtítulos, tras la portada. También en la barra de abajo."),
    Option("Páginas", "reading", "check", "Minutos de lectura en el índice",
           "Cuánto se tarda en leer cada capítulo, en la página «Contenido»."),
    _section("Páginas", "Al final"),
    Option("Páginas", "endnotes", "check", "Notas al final del libro",
           "Todas las notas en una página «Notas», con enlace de vuelta. Si no, cada capítulo lleva las suyas al final."),
    Option("Ilustraciones", "shrink", "check", "Reducir imágenes", "Las imágenes son casi todo el peso del EPUB."),
    Option("Ilustraciones", "shrink_size", "number", "Lado mayor, en píxeles", "Tamaño de las imágenes reducidas."),
    Option("Ilustraciones", "gray", "check", "Imágenes en gris", "Para e-readers de tinta: pesa menos y se ve igual."),
    Option("Ilustraciones", "trim", "check", "Recortar márgenes blancos de las ilustraciones",
           "Quita el borde blanco que traen algunas ilustraciones escaneadas."),
    Option("PDF", "whole_words", "check", "No partir palabras al final de línea",
           "La palabra que no cabe pasa entera a la línea siguiente, sin guion."),
    Option("PDF", "pdf_size", "choice", "Tamaño preferido", "Sale primero en el menú «PDF ▾»."),
    _section("Apariencia", "Colores"),
    Option("Apariencia", "theme", "choice", "Tema", "«Sistema» sigue el modo claro u oscuro de Windows."),
    Option("Apariencia", "accent", "choice", "Color de acento",
           "El de los botones principales, la pestaña elegida y lo seleccionado."),
    _section("Apariencia", "Letra"),
    Option("Apariencia", "ui_font", "choice", "Tamaño de la letra de la ventana",
           "Más grande se lee mejor en pantallas pequeñas o con el zoom de Windows bajo."),
    Option("Inicio", "reopen_last", "check", "Abrir el último libro al empezar",
           "Al abrir la aplicación, el libro con el que se trabajó la última vez, sin pasar por «Recientes»."),
    Option("Inicio", "recent_max", "number", "Libros en «Recientes»", "Cuántos recuerda el menú y la pantalla de inicio."),
    Option("Salida", "out_name", "choice", "Nombre del EPUB",
           "El nombre que propone «Generar EPUB»: el del PDF, el título del libro o «Serie - Volumen N»."),
    Option("Salida", "out_folder", "folder", "Carpeta donde guardar",
           "Dónde propone guardar «Generar EPUB». Sin ninguna, junto al PDF."),
    Option("Vista previa", "device", "choice", "Dispositivo al abrir", "Tamaño de página de la vista previa del EPUB."),
    Option("Vista previa", "dark", "check", "Modo oscuro al abrir", "Las vistas previas en claro sobre negro."),
    Option("Editor", "editor_undo", "choice", "Deshacer y rehacer",
           "«Por capítulo»: cada capítulo con su propio deshacer, como hasta ahora. «Todo el libro»: Ctrl+Z deshace "
           "el último cambio, en el capítulo que sea, y lo abre si es otro, como en Word."),
    Option("Editor", "editor_side", "choice", "Pestaña del panel al abrir",
           "Lo que se ve a la derecha de la hoja: la página original, el capítulo como saldrá en el EPUB o la hoja web."),
    Option("Editor", "editor_zoom", "number", "Tamaño de la hoja al abrir (%)",
           "El de la barra de abajo del editor; se puede cambiar mientras se escribe."),
    Option("Editor", "editor_changes", "check", "Mostrar cambios al abrir",
           "Lo editado, marcado en la hoja desde el principio (Vista → Mostrar cambios)."),
    Option("Editor", "editor_nav", "check", "Panel de navegación al abrir",
           "Títulos, avisos, comentarios y cambios del capítulo a la izquierda (F9)."),
    Option("Editor", "editor_dark", "check", "Panel en modo oscuro",
           "Original, EPUB y hoja web con fondo oscuro y letra clara; las ilustraciones no cambian."),
    Option("Editor", "editor_spell", "check", "Subrayar las faltas de ortografía",
           "Mientras se escribe, en rojo; el diccionario es de español. Sin esto, «Ortografía…» sigue funcionando."),
    Option("Editor", "editor_resume", "check", "Ofrecer volver a donde lo dejaste",
           "Al abrir el editor en otro capítulo, la barra «Lo dejaste en… · Ir allí»."),
    Option("Ortografía", "spell_caps", "check", "Revisar también las palabras en mayúscula",
           "Sin esto, «Ortografía…» salta las que empiezan en mayúscula: suelen ser nombres propios."),
    Option("Ortografía", "words", "words", "Palabras añadidas al diccionario",
           "Las de «Añadir al diccionario»: valen para este libro y los demás tomos de su serie. Quita la que se "
           "añadió por error."),
    Option("Ortografía", "grammar", "grammar", "Revisión de gramática",
           "Dónde corre LanguageTool al pulsar «Revisar gramática» en el Informe de calidad. En este equipo es "
           "privado y sin límites; languagetool.org no descarga nada, pero el texto sale del equipo y va más lento."),
    Option("Traducción", "translate", "translate", "Motor, modelo y clave de API",
           "Servicio que traduce, su modelo y su clave, e idiomas con los que empieza «Traducir»."),
    Option("Carpeta vigilada", "watch", "folder", "Carpeta vigilada",
           "Mientras la ventana esté abierta, cada PDF nuevo que llegue a ella se convierte solo."),
    Option("Informe de calidad", "echoes", "check", "Buscar ecos (palabras repetidas cerca)",
           "Muchos avisos de estilo: solo si se piden."),
    Option("Informe de calidad", "quality", "quality", "Avisos",
           "Quita la marca de los que no quieras ver y cambia su gravedad: la nota del informe cuenta más los graves."),
)
# Lo que se guarda por libro y en «Libros nuevos»; font_file es la ruta de la letra propia («Otra…» de la barra)
BOOK_KEYS = tuple(o.key for o in OPTIONS if o.category in CATEGORIES[BOOK] and o.kind != SECTION) + ("font_file",)


FILE_TYPES = {"logo": ("Imagen", "*.png *.jpg *.jpeg"), "heading_font_file": ("Fuente", "*.ttf *.otf")}
NUMBERS = {"shrink_size": (400, 4000, 100), "editor_zoom": (50, 200, 10), "recent_max": (3, 30, 1)}  # mín, máx, paso


def fold(text):
    """Sin tildes ni mayúsculas: «guion» encuentra «guiones» y «Guión»."""
    return "".join(c for c in unicodedata.normalize("NFD", str(text).lower()) if unicodedata.category(c) != "Mn")


def find(text):
    """Opciones cuyo nombre o ayuda contiene text."""
    needle = fold(text).strip()
    return [o for o in OPTIONS if needle and o.kind != SECTION and (needle in fold(o.label) or needle in fold(o.help))]


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
        if key in ("theme", "accent", "ui_font"):  # la apariencia se ve al momento; Cancelar la devuelve
            if key == "theme":
                self.owner.theme_var.set(value)
            else:  # apply_theme lee el acento y la letra de los ajustes (restore_options los devuelve)
                self.owner.settings[key] = value
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
        reset = QPushButton("Restablecer")
        reset.setToolTip("Vuelve las opciones de esta página a como vienen de fábrica")
        reset.clicked.connect(self.reset_page)
        head = QHBoxLayout()
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(reset)
        layout.addLayout(head)
        self.parts["widgets"], self.parts["notes"], self.parts["reset"] = {}, {}, reset
        for option in OPTIONS:
            if option.category == category and option.kind == SECTION:
                caption = QLabel(option.label.upper())  # como los encabezados de la lista de la izquierda
                caption.setObjectName("muted")
                bold = QFont(caption.font())
                bold.setBold(True)
                caption.setFont(bold)
                caption.setContentsMargins(0, theme.SPACE["section"], 0, theme.SPACE["tight"])
                layout.addWidget(caption)
            elif option.category == category:
                self.add_option(layout, option)
        self.parts["copy"] = None
        if self.is_book() and self.scope == "book":
            copy_btn = QPushButton("Usar también para libros nuevos")
            copy_btn.setToolTip("Copia los valores de esta página a «Libros nuevos»")
            keys = [o.key for o in OPTIONS if o.category == category and o.kind != SECTION]
            copy_btn.clicked.connect(lambda: self.defaults.update({k: self.owner.fields()[k].get() for k in keys}))
            layout.addWidget(copy_btn, 0, Qt.AlignmentFlag.AlignLeft)
            self.parts["copy"] = copy_btn
        layout.addStretch(1)
        self.scroll.setWidget(page)  # la página anterior se borra con su scroll

    def reset_page(self):
        """«Restablecer»: las opciones de la página a sus valores de fábrica (del libro, en el alcance elegido)."""
        factory = {**self.owner.factory, **self.owner.app_factory()}
        for option in OPTIONS:
            if option.category == self.category and option.key in factory and option.kind != "translate":
                self.store(option.key, copy.deepcopy(factory[option.key]))
        if self.category == "Informe de calidad":  # todos los avisos, cada uno con su gravedad de siempre
            self.values.update(quality_off=[], quality_levels={})
        if self.is_book() and self.scope == "book":
            self.owner.update_preview()
        if self.category == "Apariencia":
            self.owner.theme_var.set(self.values["theme"])
            self.owner.settings.update(accent=self.values["accent"], ui_font=self.values["ui_font"])
            self.owner.apply_theme()
        self.show(self.category)

    def add_option(self, layout, option):
        """Control de una opción y, debajo, su ayuda en gris."""
        special = {"translate": self.translation_page, "words": self.words_option, "quality": self.quality_option,
                   "grammar": self.grammar_option}
        if option.kind in special:  # con su propia página: no son un valor suelto
            special[option.kind](layout, option)
            return
        key, value = option.key, self.get(option.key)
        if option.kind in ("folder", "file"):
            widget = QLabel(value or "Ninguna")
            widget.setWordWrap(True)
            pick, stop = QPushButton("Elegir…"), QPushButton("Dejar de vigilar" if key == "watch" else "Quitar")
            stop.setEnabled(bool(value))
            pick.clicked.connect(lambda: self.pick_folder(key) if option.kind == "folder" else self.pick_file(key))
            stop.clicked.connect(lambda: (self.put(key, ""), self.show(self.category)))
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            if key != "watch":  # la carpeta vigilada tiene su página; lo demás comparte la suya
                line.addWidget(QLabel(f"{option.label}:"))
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
                low, high, step = NUMBERS.get(key, (0, 10000, 1))
                widget.setRange(low, high)
                widget.setSingleStep(step)
                widget.setValue(int(value))
                widget.valueChanged.connect(lambda n, key=key: self.put(key, n))
            else:
                widget = QComboBox()
                values = list(self.choices.get(key, []))
                widget.setMinimumContentsLength(18)
                if option.kind == "language":  # el nombre a la vista, el código («es») en el libro
                    if str(value) not in values:  # uno puesto a mano en un libro viejo («es-MX»): se conserva
                        values.append(str(value))
                    for code in values:
                        widget.addItem(translate.LANGS.get(code, code), code)
                    widget.setCurrentIndex(max(0, widget.findData(str(value))))
                    widget.activated.connect(lambda _, w=widget, key=key: self.put(key, w.currentData()))
                else:
                    if str(value) not in values and value not in (None, ""):  # uno propio («◇», una letra suelta)
                        values.append(str(value))
                    widget.addItems(values)
                    widget.setCurrentText(str(value))
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
                error = QLabel("La carpeta ya no existe: elige otra o deja de vigilarla." if key == "watch" else
                               "La carpeta ya no existe: elige otra o quítala.")
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
        # Se elige de los que da «Buscar modelos» u «Otro…» para escribirlo
        model.setMinimumContentsLength(22)
        model.setPlaceholderText("Elige uno")
        qtk.ask_other(model, lambda now: (lambda r: r[0] if r[1] else None)(
            QInputDialog.getText(self.win, "Modelo", "Nombre del modelo:", text=now)))
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
            qtk.fill_choices(model, [], data["models"].get(name) or info.get("model", ""))  # la buscada era de otro motor
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
                qtk.fill_choices(model, result, model.currentText().strip() or result[0])
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

    def words_option(self, layout, option):
        """Las palabras del diccionario del libro (y de su serie), cada una con «Quitar». Sin libro no hay."""
        layout.addWidget(QLabel(f"{option.label}:"))
        words = self.values.get("words")
        if words is None:
            note = QLabel("Abre un libro para ver las palabras de su diccionario.")
            note.setObjectName("muted")
            layout.addWidget(note)
            return
        listing = QListWidget()
        listing.addItems(sorted(words, key=fold))
        listing.setMinimumHeight(160)
        remove = QPushButton("Quitar")
        remove.setEnabled(False)
        listing.currentRowChanged.connect(lambda row: remove.setEnabled(row >= 0))

        def drop():
            item = listing.currentItem()
            if item:
                self.values["words"] = [w for w in self.values["words"] if w != item.text()]
                listing.takeItem(listing.row(item))

        remove.clicked.connect(drop)
        line = QHBoxLayout()
        line.addWidget(listing, 1)
        line.addWidget(remove, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(line)
        note = QLabel(option.help if words else "Aún no hay ninguna: se añaden en «Ortografía…».")
        note.setObjectName("muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.parts["widgets"]["words"] = listing

    def grammar_option(self, layout, option):
        """Motor de LanguageTool; la dirección del servidor propio y el usuario y la clave de Premium solo con el
        suyo. La clave se guarda cifrada (translate.protect); todo espera a Aceptar."""
        data = self.values["grammar"] = dict(self.values.get("grammar") or {})
        engine, url, user, key = QComboBox(), QLineEdit(str(data.get("url") or "")), QLineEdit(), QLineEdit()
        for code, name in grammar.ENGINES.items():
            engine.addItem(name, code)
        engine.setCurrentIndex(max(0, engine.findData(data.get("engine"))))
        url.setPlaceholderText("http://localhost:8081")
        user.setText(str(data.get("user") or ""))
        user.setPlaceholderText("Correo de la cuenta")
        key.setText(translate.unprotect(str(data.get("key") or "")))
        key.setEchoMode(QLineEdit.EchoMode.Password)
        key.setPlaceholderText("Clave API")
        rows = {}
        for name, text, widget in (("engine", "Revisión de gramática:", engine), ("url", "Servidor:", url),
                                   ("user", "Usuario:", user), ("key", "Clave:", key)):
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            line.addWidget(QLabel(text))
            line.addWidget(widget, 0 if name == "engine" else 1)
            if name != "engine":
                widget.setMinimumWidth(260)
            line.addStretch(1)
            layout.addWidget(row)
            rows[name] = row

        def keep():
            try:
                secret = translate.protect(key.text().strip())
            except OSError:  # sin cifrar no se guarda
                secret = ""
            data.update(engine=engine.currentData(), url=url.text().strip(), user=user.text().strip(), key=secret)
            rows["url"].setVisible(data["engine"] == "custom")
            for name in ("user", "key"):
                rows[name].setVisible(data["engine"] == "premium")

        engine.currentIndexChanged.connect(lambda _: keep())  # con ratón, teclado o rueda: los campos de ese motor
        for widget in (url, user, key):
            widget.editingFinished.connect(keep)
        keep()
        note = QLabel(option.help)
        note.setObjectName("muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.parts["widgets"]["grammar"] = engine

    def quality_option(self, layout, option):
        """Cada tipo de aviso del informe con su casilla (verlo o no) y su gravedad."""
        note = QLabel(option.help)
        note.setObjectName("muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        off, levels = set(self.values["quality_off"]), dict(self.values["quality_levels"])
        rows = {}
        for kind, title, level in self.owner.quality_kinds():
            check = QCheckBox(title)
            check.setChecked(kind not in off)
            pick = QComboBox()
            pick.addItems(list(self.owner.quality_levels()))
            pick.setCurrentText(levels.get(kind, level))
            check.toggled.connect(lambda on, kind=kind: self.values.update(quality_off=sorted(
                (set(self.values["quality_off"]) - {kind}) if on else (set(self.values["quality_off"]) | {kind}))))
            pick.activated.connect(lambda _, w=pick, kind=kind, level=level: self.values.update(quality_levels={
                **{k: v for k, v in self.values["quality_levels"].items() if k != kind},
                **({kind: w.currentText()} if w.currentText() != level else {})}))
            line = QHBoxLayout()
            line.addWidget(check, 1)
            line.addWidget(pick)
            layout.addLayout(line)
            rows[kind] = (check, pick)
        self.parts["widgets"]["quality"] = rows

    def pick_file(self, key):
        path = qtk.filedialog.askopenfilename(filetypes=[FILE_TYPES[key]])
        if path:
            self.put(key, path)
            self.show(self.category)

    def pick_folder(self, key):
        folder = self.owner.ask_folder() if key == "watch" else qtk.filedialog.askdirectory(
            title="Carpeta donde guardar los EPUB")
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
