"""Ventana para convertir PDFs de novelas ligeras a EPUB."""
import base64
import collections
import concurrent.futures
import contextlib
import copy
import ctypes
import dataclasses
import functools
import difflib
import hashlib
import html
import http.server
import io
import json
import logging
import multiprocessing
import os
import pickle
import queue
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.parse
import urllib.request
import webbrowser
import zipfile
import zlib
from datetime import datetime
from pathlib import Path

import pymupdf
import segno
import shiboken6
from PySide6.QtCore import QEvent, QEventLoop, QLockFile, QObject, QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QAction, QColor, QFont, QFontDatabase, QFontMetrics, QIcon, QImage, QKeyEvent, QKeySequence, QPainter,
                           QPainterPath,
                           QPalette, QPixmap, QShortcut, QTextCharFormat, QTextCursor, QTextLayout)
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout,
                               QInputDialog, QTreeWidget, QTreeWidgetItem,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QPlainTextEdit,
                               QProgressBar,
                               QAbstractItemView, QButtonGroup, QHeaderView, QStyle, QStyledItemDelegate,
                               QStyleOptionViewItem, QTableWidget, QTableWidgetItem, QTabWidget,
                               QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox, QSplitter, QStackedWidget, QTabBar,
                               QLayout, QTextBrowser, QTextEdit, QToolButton, QVBoxLayout, QWidget, QWidgetAction)

import convert
import editor
import faithful
import grammar
import options
import qtk
import registro
import report
import theme
import translate
import upscale
from qtk import filedialog, messagebox

# Programas de Windows por su ruta, y sin buscar en la carpeta actual: un explorer.exe o un java.exe puesto en la
# carpeta del PDF (una descarga) se ejecutaría en vez del de Windows
os.environ["NoDefaultCurrentDirectoryInExePath"] = "1"
WINDOWS = Path(os.environ.get("SystemRoot") or "C:/Windows")
EXPLORER = str(WINDOWS / "explorer.exe")
POWERSHELL = str(WINDOWS / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")

ON, OFF, CUT = "Capítulo", "Subtítulo", "Quitar"  # tipo de cada título de la lista
WEB_POLL_MS = 100  # cada cuánto se mira la hoja web mientras se escribe en ella: lo escrito va a las otras vistas
SEP = "Separador"                                   # título que se escribe como separador de escena
# Estilo de párrafo de la cinta -> etiqueta en la hoja web
WEB_TAGS = {"Normal": "p", "Capítulo": "h1", "Subtítulo": "h2", "Título 3": "h3", "Título 4": "h4", "Cita": "blockquote"}
# Estilos de párrafo en la cinta del editor de texto (editor.Sheet.set_paragraph_style)
STYLES = ["Normal", "Capítulo", "Subtítulo", "Título 3", "Título 4", "Cita"]

FRONT = "Inicio del libro"                           # lo que va antes del primer capítulo, en el editor
TOC_PAGE = "Contenido"  # en el editor, la página de títulos: cambiar uno renombra su capítulo (sección -1..-1)
CHAPTER_WIDTHS = (300, 180, 170, 140)  # botón de capítulo del editor en cada nivel de la cinta (fit_ribbon)
GLYPH = 24                                           # px del glifo de un botón grande de la cinta, como en Word
SMALL_GLYPH = 16                                     # px del glifo de un botón pequeño de la cinta
NEXT = {ON: OFF, OFF: SEP, SEP: CUT, CUT: ON}       # cada clic pasa al siguiente
IMG, DROPPED = "Ilustración", "Descartada"           # tipo de las filas de imagen
IMAGE_NEXT = {IMG: CUT, CUT: IMG, DROPPED: IMG}      # clic en una imagen: se quita o se recupera
SPLIT, ROTATED = "Dividida", "Girada"                # ilustración doble: en dos páginas o girada 90°
WIDE_NEXT = {IMG: SPLIT, SPLIT: ROTATED, ROTATED: CUT, CUT: IMG, DROPPED: IMG}
LAYOUT = {SPLIT: "split", ROTATED: "rotate"}         # tipo de la fila -> Block.layout
COVER = "cover"  # fila fija de la portada, arriba de la lista: no es un bloque del libro
CONTENTS = "contents"  # fila fija de la página «Contenido» del EPUB, bajo la portada
INDEX = "Índice"  # tipo de la fila «Contenido» cuando el EPUB la lleva (si no, CUT)
TOC_TITLES = ("contents", "table of contents", "contenido", "contenidos", "índice", "indice")  # índice impreso
KIND_TAG = {ON: "chapter", OFF: "subtitle", CUT: "cut", IMG: "image", DROPPED: "dropped", INDEX: "chapter",
            SPLIT: "image", ROTATED: "image", SEP: "separator"}  # estilo por tipo
KEYS = {"c": ON, "s": OFF, "q": CUT, "i": IMG, "d": SPLIT, "g": ROTATED, "x": SEP}  # atajos de teclado en la lista
LANGUAGES = ["es", "en", "pt", "fr", "it", "ja"]     # idioma del EPUB: lo usan la partición de palabras y la voz
LANG = re.compile(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{1,8})*")  # el idioma va tal cual en el XML del EPUB
# Lo que comparten los tomos de una serie: al abrir un tomo sin revisión se toma de otro ya revisado
INHERITED = ("author", "publisher", "translator", "illustrator", "subjects", "lang", "style", "separator",
             "separator_icon",
             "typography", "shrink", "shrink_size", "gray", "trim", "font", "font_file", "epub2", "reading",
             "contents", "credits", "profile", "replacements", "horizontal", "kepub", "words", "hyphenate", "glossary", "tr_glossary", "tr_notes",
             "book_type", "franchise", "author_native", "illustrator_native", "relay", "epub_by", "distributor", "links",
             "synopsis", "endnotes", "note_style", "heading_font", "heading_font_file", "box_style", "logo")
# De INHERITED, lo que es de toda la serie aunque el tomo ya tenga revisión: glosarios, diccionario y personajes
SERIES_VOCAB = ("words", "glossary", "tr_glossary", "tr_notes")
def _data_dir():
    """Carpeta de preferencias y cachés. La versión portable trae «datos» junto al .exe: todo queda ahí,
    se lleva en un USB y no deja nada en el equipo."""
    portable = Path(sys.executable).parent / "datos"
    if getattr(sys, "frozen", False) and portable.is_dir():
        return portable
    return Path(os.environ.get("APPDATA") or Path.home()) / "PDF a EPUB"


# Preferencias de la ventana entre sesiones (opciones, última carpeta y PDF recientes)
SETTINGS = _data_dir() / "ajustes.json"
LOG = logging.getLogger("app")  # registro.log: lo que hace la ventana (registro.py)
STANDBY = True  # un proceso de trabajo preparado de antemano (warm_worker); las pruebas lo apagan
# Opciones → Editor: cómo es el editor de cada uno. Los que ya existían (editor_nav, editor_dark) se recuerdan también
# al cambiarlos desde el propio editor
UNDO_SCOPES = ("Por capítulo", "Todo el libro")
EDITOR_SIDES = ("La última usada", "Original", "EPUB", "Hoja web")
EDITOR_DEFAULTS = {"editor_undo": UNDO_SCOPES[0], "editor_side": EDITOR_SIDES[0], "editor_zoom": 100,
                   "editor_changes": False, "editor_nav": False, "editor_dark": False, "editor_spell": True,
                   "editor_resume": True}
RECENT = 8  # PDF recientes que recuerda el menú
OUT_NAMES = ["Como el PDF", "Título del libro", "Serie - Volumen N"]  # Opciones → Salida → Nombre del EPUB
# Lo de «La aplicación» en Opciones que no es del editor: sus valores de fábrica
APP_DEFAULTS = {"accent": "Azul", "ui_font": "9 pt", "reopen_last": False, "recent_max": RECENT, "out_name": OUT_NAMES[0],
                "out_folder": "", "spell_caps": False, "quality_off": [], "quality_levels": {},
                "grammar": {"engine": "local", "url": "", "user": "", "key": ""}}  # key: cifrada (translate.protect)
BOOK_FIELDS = ("title", "author", "series", "volume", "publisher", "translator", "description", "illustrator",
               "proofreader", "editor", "original_title", "subjects", "date")  # datos del libro en la revisión
# Los de «Más datos…» (página de título y catálogo): también son datos del libro, como convert.Book
MORE_FIELDS = (("subtitle", "Subtítulo"), ("book_type", "Tipo"), ("franchise", "Franquicia"),
               ("rating", "Valoración (0-5)"), ("romaji_title", "Título romanizado"),
               ("author_native", "Autor en japonés"), ("illustrator_native", "Ilustrador en japonés"),
               ("relay", "Traducción intermedia"), ("epub_by", "Maquetación EPUB"), ("distributor", "Distribuye"),
               ("isbn", "ISBN"), ("asin", "ASIN"), ("source_url", "Enlace de la fuente"),
               ("revised", "Última corrección"), ("links", "Enlaces"))
BOOK_FIELDS += tuple(name for name, _ in MORE_FIELDS)
# Perfil de salida -> opciones que fija (los demás perfiles dejan las opciones como estén)
PROFILES = {"Personalizado": {},
            "Celular": {"gray": False, "shrink": True, "shrink_size": 1200, "epub2": False, "kepub": False, "hyphenate": False},
            "E-reader de tinta": {"gray": True, "shrink": True, "shrink_size": 1400, "epub2": False, "kepub": False, "hyphenate": False},
            "Tablet": {"gray": False, "shrink": True, "shrink_size": 2000, "epub2": False, "kepub": False, "hyphenate": False},
            # Kindle en color: Amazon convierte el EPUB y la app del celular y el Colorsoft lo muestran
            "Kindle": {"gray": False, "shrink": True, "shrink_size": 1600, "epub2": False, "kepub": False, "hyphenate": True},
            "Kobo": {"gray": True, "shrink": True, "shrink_size": 1400, "epub2": False, "kepub": True, "hyphenate": False},
            "Lector antiguo (EPUB 2)": {"gray": False, "shrink": True, "shrink_size": 1400, "epub2": True,
                                        "kepub": False, "hyphenate": False}}
FONT_CHOICES = ["Del lector"] + list(convert.FONTS) + ["Otra…"]  # «Otra…» pide un archivo
HEADING_OWN = "Archivo propio"  # fuente de títulos de un archivo (heading_font_file)
# Un EPUB abierto sale como es: su CSS, sus separadores y sus páginas; la limpieza tipográfica ya la tiene
EPUB_DEFAULTS = {"style": convert.ORIGINAL, "separator": "Icono del original", "typography": False, "contents": False,
                 "credits": False, "synopsis": False}
APP_NAME = "Light Novel Creator"  # nombre visible; el .exe y la carpeta de ajustes siguen siendo «PDF a EPUB»
KIND_COLOR = theme.kind_colors(False)  # colores de cada tipo de fila; en oscuro, theme.kind_colors(True)
PREVIEW_BOX = (480, 640)  # ancho y alto máximos de cada vista previa, en píxeles
PREVIEW_CHROME = 40       # alto de la fila ◀ leyenda ▶ de cada vista previa
PAGE_GAP = 8              # píxeles entre la página y la siguiente en el panel del editor
DEVICES = {"Celular": (300, 560), 'E-reader 6"': (420, 560), "Tablet": (560, 750)}  # página en puntos
THUMB_HEIGHT = 64         # alto de las miniaturas del PDF, en píxeles
THUMBS_PER_TICK = 6       # miniaturas por tanda como mucho: dibujarlas todas de golpe congelaría la ventana
THUMBS_TICK_S = 0.05      # y no más de 50 ms por tanda: 6 láminas a color de un EPUB, juntas, la paraban 0,9 s
SUGGEST_AHEAD = 40        # sugerencias de ortografía pedidas por adelantado en cada capítulo
MARK_COLORS = {"title": "#1f6fd1", "image": "#2e9d4a", "current": "#f08c00"}  # bordes de las miniaturas
ICON = Path(__file__).parent / "icon.ico"  # en el .exe, junto a los módulos (--add-data icon.ico;.)
LEVEL_NAMES = {"Grave": ("grave", "graves"), "Revisar": ("por revisar", "por revisar"),
               "Menor": ("menor", "menores")}  # singular y plural: «1 grave», «2 graves»
LEVEL_PLURAL = {level: names[1] for level, names in LEVEL_NAMES.items()}
# Herramientas con índices de bloques del libro: se cierran al restaurar una versión y al abrir otro libro
BLOCK_TOOLS = ("spelling_window", "page_window", "quality_window", "images_window", "cover_window")
# Las que guardan en el libro abierto lo que leyeron del anterior: se cierran al abrir otro libro
BOOK_TOOLS = ("translate_window", "versions_window", "replacements_window", "glossary_window", "online_window")
# Atajos de teclado por grupos: la ventana «Atajos de teclado…» los enseña en tabla. «+» separa teclas que van a la
# vez; «/» alternativas
SHORTCUT_GROUPS = [
    ("Lista de capítulos", [
        ("C", "Marcar como capítulo"), ("S", "Marcar como subtítulo"), ("X", "Marcar como separador de escena"),
        ("Q", "Quitar del libro"), ("I", "Marcar como ilustración"), ("D / G", "Ilustración doble: dividir o girar"),
        ("F2", "Cambiar el título (también doble clic)"), ("E", "Editar el texto de la fila"),
        ("R", "Marcar como revisado"), ("Alt+↑ / Alt+↓", "Mover la fila (también arrastrándola)")]),
    ("Editor de texto", [
        ("Ctrl+K / Ctrl+I", "Cursiva"), ("Ctrl+N / Ctrl+B", "Negrita"), ("Ctrl+S", "Subrayado"),
        ("Ctrl+T", "Título o párrafo"), ("Ctrl+E", "Centrar"), ("Ctrl+D", "Alinear a la derecha"),
        ("Ctrl+J", "Unir con el párrafo siguiente"), ("Mayús+Intro", "Salto de línea en el mismo párrafo"),
        ("Mayús+F3", "Mayúsculas y minúsculas"), ("Ctrl+Alt+-", "Raya (—)"), ("Ctrl+Alt+.", "Puntos suspensivos (…)"),
        ("Ctrl+Alt+Q", "Comillas «»"), ("Ctrl+Alt+K", "Enlace"), ("Ctrl+F", "Buscar"), ("Ctrl+H", "Reemplazar"),
        ("F7", "Cambio siguiente"), ("F8", "Aviso siguiente"), ("F9", "Panel de navegación"),
        ("F11", "Modo concentración"), ("Ctrl+Intro", "Revisado y al capítulo siguiente sin revisar"),
        ("Ctrl+RePág / Ctrl+AvPág", "Capítulo anterior o siguiente")]),
    ("General", [
        ("Ctrl+Z", "Deshacer"), ("Ctrl+Y", "Rehacer (en el editor)"), ("Ctrl+F", "Buscar en el libro"),
        ("Ctrl+,", "Opciones")]),
]
SHORTCUTS = ("Clic en Tipo, o teclas C (capítulo), S (subtítulo), X (separador), Q (quitar), I (ilustración),"
             " D/G (doble: dividir o girar)\nDoble clic o F2: editar título · E: editar texto · R: revisado\n"
             "Arrastrar o Alt+↑/↓: mover · Ctrl+F: buscar · Ctrl+Z: deshacer · Ctrl+,: opciones")


def _quality_summary(report):
    """"2 graves · 5 por revisar" de un informe de calidad, o "" si no hay avisos."""
    counts = collections.Counter(i.level for i in report)
    return " · ".join(f"{counts[level]} {LEVEL_NAMES[level][counts[level] != 1]}" for level in convert.LEVELS
                      if counts[level])


def _issue_key(issue):
    """Cómo se recuerda un aviso ignorado: por su tipo y su texto (o su página); el bloque cambia al editar."""
    return [issue.kind, issue.text or issue.page]


def _swap_image(book, k, data):
    """Cambia la imagen del bloque k en una lista nueva: la de antes queda intacta para deshacer."""
    book.blocks = book.blocks[:k] + [dataclasses.replace(book.blocks[k], image=data)] + book.blocks[k + 1:]


def _scaled_jpeg(data, width, height):
    """Una lámina JPEG ya reducida a caber en width×height, o None si no es JPEG o Qt no la lee. Qt la descodifica
    a 1/2, 1/4 u 1/8 de su tamaño: 2,4 veces más rápido que entera con MuPDF (las 15 de Strange Grief 1, 0,3 s)."""
    if data[:2] != b"\xff\xd8":
        return None
    from PySide6.QtCore import QBuffer, QByteArray
    from PySide6.QtGui import QImageReader
    buffer = QBuffer()
    buffer.setData(QByteArray(data))
    reader = QImageReader(buffer, b"jpeg")
    size = reader.size()
    if not size.isValid():
        return None
    if size.width() > width or size.height() > height:
        size.scale(QSize(width, height), Qt.AspectRatioMode.KeepAspectRatio)
        reader.setScaledSize(size)
    image = reader.read()
    return None if image.isNull() else image


def _diff_spans(before, after):
    """Tramos que cambian entre la vista previa de antes y la de después: ([(i, j) en before], [(i, j) en after])."""
    ops = difflib.SequenceMatcher(None, before, after, autojunk=False).get_opcodes()
    return ([(i1, i2) for op, i1, i2, _, _ in ops if op in ("replace", "delete")],
            [(j1, j2) for op, _, _, j1, j2 in ops if op in ("replace", "insert")])


def _log(text, tb=None):
    """Apunta un fallo en errores.log, junto a las preferencias: el .exe no tiene consola donde verlo. Con la pila
    (tb, o la del except en curso), también las variables de donde saltó y tras qué acción."""
    tb = tb or sys.exc_info()[2]
    details = registro.crash_details(tb) if tb else ""
    if details:
        text = f"{text.rstrip()}\n{details}"
    if registro.LAST_ACTION:
        text = f"{text.rstrip()}\nTras: {registro.LAST_ACTION}"
    path = SETTINGS.parent / "errores.log"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"--- {datetime.now():%Y-%m-%d %H:%M:%S}\n{text.rstrip()}\n")
    except OSError:
        pass  # sin registro; el aviso en pantalla sale igual
    LOG.error("%s", text.rstrip())  # y en registro.log, con lo que pasó antes
    return path


def _worker(events, work, args, report):
    """Corre en otro proceso: PyMuPDF retiene el GIL y, en un hilo, trabaría la ventana. args es la tupla o,
    desde App.run, la ruta del archivo donde la guardó."""
    if multiprocessing.parent_process() is not None:  # su registro, a la ventana (en las pruebas corre aquí mismo)
        logging.getLogger().handlers[:] = [registro.ToQueue(events)]
        logging.getLogger().setLevel(logging.DEBUG)
    log = logging.getLogger("trabajo")
    started = time.perf_counter()
    try:
        if isinstance(args, str):
            path = args
            try:
                with open(path, "rb") as f:
                    args = pickle.load(f)
            finally:
                with contextlib.suppress(OSError):
                    os.remove(path)
        if report:
            result = work(*args, progress=lambda done, total: events.put(("progress", done, total)))
        else:
            result = work(*args)
        log.info("%s terminó en %.1f s · memoria máx. %s MB", getattr(work, "__name__", work),
                 time.perf_counter() - started, registro.peak_memory_mb())
        events.put(("done", result))
    except convert.BookError as e:  # el libro no se puede convertir: se avisa, no es un fallo de la app
        log.warning("%s no pudo: %s", getattr(work, "__name__", work), e)
        events.put(("error", str(e)))
    except Exception as e:
        details = registro.crash_details(e.__traceback__)  # qué valía qué donde saltó
        log.exception("%s falló tras %.1f s · memoria máx. %s MB\n%s", getattr(work, "__name__", work),
                      time.perf_counter() - started, registro.peak_memory_mb(), details)
        events.put(("error", str(e) or type(e).__name__))  # MemoryError() no trae mensaje
        events.put(("log", f"{traceback.format_exc()}{details}"))  # la ventana lo apunta en errores.log


def _spell_server(requests, replies):
    """Proceso de ortografía del editor: carga Hunspell una vez (~4 s) y responde mientras el editor está
    abierto. ("check", [palabras]) -> ("unknown", [las que no están]); ("suggest", palabra) -> ("suggestions",
    palabra, [sugerencias]); ("first", palabra): la misma, antes que las demás (clic derecho); ("reset",): el
    editor se cerró, se olvida lo pedido (el proceso sigue para el siguiente, App.spell_process); None termina.
    Las revisiones pasan antes que las sugerencias pendientes (~1 s cada una): lo que se escribe se subraya
    enseguida."""
    spell, pending, done, good = convert._hunspell(), [], set(), set()  # good: las correctas, para sugerir
    parent = multiprocessing.parent_process()  # None si corre en un hilo (pruebas)
    while True:
        if not pending:
            try:
                pending.append(requests.get(timeout=2))
            except queue.Empty:
                if parent is not None and not parent.is_alive():
                    return  # la ventana murió sin avisar: la cola no se cierra y esperaría para siempre
                continue
        while True:
            try:
                pending.append(requests.get_nowait())
            except queue.Empty:
                break
        if None in pending:
            return
        if ("reset",) in pending:  # lo de antes era del editor cerrado: sus sugerencias ya no hacen falta
            pending = pending[len(pending) - pending[::-1].index(("reset",)):]
            done.clear()
            good.clear()
        for _, words in [r for r in pending if r[0] == "check"]:
            unknown = [w for w in words if not spell.lookup(w)]
            good.update(set(words) - set(unknown))
            replies.put(("unknown", unknown))
        firsts = [r[1] for r in pending if r[0] == "first"]
        pending = [("suggest", w) for w in reversed(firsts)] + [r for r in pending if r[0] == "suggest"]
        pending = [r for n, r in enumerate(pending) if r[1] not in done and r not in pending[:n]]
        if pending:
            word = pending.pop(0)[1]
            done.add(word)
            replies.put(("suggestions", word, convert.spell_suggestions(word, good)))


# EPUBCheck, el validador oficial de EPUB (W3C, licencia BSD): se descarga una vez, a petición, junto a las preferencias
EPUBCHECK_VERSION = "5.4.0"
# Herramientas → «Apoyar el proyecto»: las mismas cuentas que .github/FUNDING.yml
DONATE = (("GitHub Sponsors", "https://github.com/sponsors/rodriguezrobinj"), ("Ko-fi", "https://ko-fi.com/rodriguezrobinj"))
EPUBCHECK_URL = (f"https://github.com/w3c/epubcheck/releases/download/v{EPUBCHECK_VERSION}/"
                 f"epubcheck-{EPUBCHECK_VERSION}.zip")
# Resumen de lo que trae el zip oficial (_epubcheck_digest), sacado de la copia de 5.4.0 descargada de GitHub: el zip
# no se guarda y así vale sea cual sea su compresión. Con otra versión hay que sacarlo de nuevo; vacío, no se comprueba
EPUBCHECK_SHA256 = "2f6c560858b770a2278c4c319f3a4f657f1e580e91ca0a0f948975a259281886"
EPUBCHECK_LEVELS = {"FATAL": "Error grave", "ERROR": "Error", "WARNING": "Advertencia", "USAGE": "Sugerencia",
                    "INFO": "Información"}


def _epubcheck_jar():
    return SETTINGS.parent / f"epubcheck-{EPUBCHECK_VERSION}" / "epubcheck.jar"


def _epubcheck_digest(z):
    """sha256 de los nombres y el sha256 de cada archivo del zip, en orden."""
    digest = hashlib.sha256()
    for name in sorted(n for n in z.namelist() if not n.endswith("/")):
        digest.update(name.encode() + b"\0" + hashlib.sha256(z.read(name)).digest())
    return digest.hexdigest()


def _download_epubcheck(folder):
    """Descarga EPUBCheck y lo descomprime en folder (proceso de trabajo). Devuelve la ruta de su .jar."""
    folder, inside = Path(folder), f"epubcheck-{EPUBCHECK_VERSION}/"
    folder.mkdir(parents=True, exist_ok=True)
    part = folder / "epubcheck.zip.part"
    try:
        with urllib.request.urlopen(EPUBCHECK_URL, timeout=60) as response, open(part, "wb") as f:
            shutil.copyfileobj(response, f)
    except OSError as e:
        part.unlink(missing_ok=True)
        raise RuntimeError("No se pudo descargar EPUBCheck: comprueba la conexión a internet.") from e
    try:
        with zipfile.ZipFile(part) as z:
            names = z.namelist()
            # Solo su carpeta: un nombre con «..» o fuera de ella escribiría donde no debe
            if not names or any(not n.startswith(inside) or ".." in Path(n).parts for n in names):
                raise RuntimeError("El archivo descargado de EPUBCheck no es el esperado.")
            if EPUBCHECK_SHA256 and _epubcheck_digest(z) != EPUBCHECK_SHA256:  # Java ejecutaría lo que traiga
                raise RuntimeError("El EPUBCheck descargado no coincide con el oficial: no se usa.")
            # Se descomprime aparte y se pone en su sitio de una vez: un corte a mitad dejaba el .jar sin sus
            # bibliotecas y la ventana ya no lo volvía a descargar
            tmp = Path(tempfile.mkdtemp(prefix="lnepub-epubcheck-", dir=folder))
            try:
                z.extractall(tmp)
                shutil.rmtree(folder / inside, ignore_errors=True)
                os.replace(tmp / inside, folder / inside)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
    except zipfile.BadZipFile as e:
        raise RuntimeError("El archivo descargado de EPUBCheck no es el esperado.") from e
    finally:
        part.unlink(missing_ok=True)
    return str(folder / inside / "epubcheck.jar")


def _epubcheck_messages(data):
    """[(gravedad, dónde, mensaje)] del informe JSON de EPUBCheck, en español."""
    found = []
    for m in data.get("messages", []):
        where = (m.get("locations") or [{}])[0]
        place = where.get("path", "") + (f", línea {where['line']}" if (where.get("line") or -1) > 0 else "")
        found.append((EPUBCHECK_LEVELS.get(m.get("severity"), str(m.get("severity"))), place,
                      str(m.get("message", "")).strip()))
    return found


def _epubcheck(jar, epub):
    """EPUBCheck sobre epub con sus mensajes en español (proceso de trabajo: tarda de 5 a 30 s)."""
    with tempfile.TemporaryDirectory(prefix="lnepub-epubcheck-") as tmp:
        report = Path(tmp) / "informe.json"
        java = shutil.which("java")
        if not java:
            raise RuntimeError("EPUBCheck necesita Java, que no está instalado en este equipo.")
        run = subprocess.run([java, "-jar", jar, str(epub), "--json", str(report), "--locale", "es"],
                             capture_output=True, timeout=600,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))  # sin consola negra
        if not report.is_file():
            detail = (run.stderr or run.stdout).decode("utf-8", "replace").strip().splitlines()[-1:]
            raise RuntimeError("EPUBCheck no pudo revisar el libro." + (f"\n{detail[0]}" if detail else ""))
        return _epubcheck_messages(json.loads(report.read_text(encoding="utf-8")))


# Los errores de re vienen en inglés: los más comunes, dichos para el usuario
REGEX_ERRORS = (("missing )", "falta cerrar un paréntesis"), ("unbalanced parenthesis", "sobra un «)»"),
                ("unterminated character set", "falta cerrar un «[»"), ("nothing to repeat", "«*», «+» o «?» sin nada "
                 "delante"), ("multiple repeat", "dos repeticiones seguidas"), ("bad escape", "barra invertida no "
                 "válida"), ("bad character range", "rango de letras al revés"), ("unknown extension", "«(?» no válido"))


IMAGE_TYPES = [("Imagen", "*.jpg *.jpeg *.png *.webp")]


def _image_ext(data):
    return "png" if data[:4] == b"\x89PNG" else "jpg"


def _image_size(data):
    """(ancho, alto) de una imagen, de su cabecera: descodificarla entera para eso era la mitad de lo que tardaba en
    abrirse «Ilustraciones…» (Eminence 7, 21 láminas: 0,29 s)."""
    from PySide6.QtCore import QBuffer, QByteArray
    from PySide6.QtGui import QImageReader
    buffer = QBuffer()
    buffer.setData(QByteArray(data))
    image = QImageReader(buffer).size()
    if not image.isValid():
        image = QImage.fromData(data).size()
    return image.width(), image.height()


def _image_info(data):
    """«1200×1700 px · 340 KB · JPEG» de una imagen."""
    width, height = _image_size(data)
    return f"{width}×{height} px · {max(1, round(len(data) / 1024))} KB · {_image_ext(data).upper()}"


THUMB_BOX = (72, 72)  # miniatura de la lista de ilustraciones: marco cuadrado, igual para verticales y apaisadas


def _framed_thumb(data):
    """La imagen reducida y centrada en un marco transparente de THUMB_BOX: todas las filas, iguales."""
    # Ya reducida al descodificar (_scaled_jpeg): entera, la otra mitad de lo que tardaba «Ilustraciones…»
    image = (_scaled_jpeg(data, *THUMB_BOX) or QImage.fromData(data)).scaled(*THUMB_BOX, Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation)
    framed = QPixmap(*THUMB_BOX)
    framed.fill(Qt.GlobalColor.transparent)
    painter = QPainter(framed)
    painter.drawImage((THUMB_BOX[0] - image.width()) // 2, (THUMB_BOX[1] - image.height()) // 2, image)
    painter.end()
    return framed


def _images():
    """Carpeta de las imágenes elegidas o pegadas, junto a las preferencias: las revisiones apuntan ahí."""
    return SETTINGS.parent / "ilustraciones"


def _store_image(path):
    """Copia la imagen elegida junto a las preferencias y devuelve esa ruta: la revisión la guarda, y la del usuario
    (una carpeta de exportación) puede borrarse; entonces, al reabrir, el cambio se perdía."""
    return _store_bytes(Path(path).read_bytes(), Path(path).suffix.lower())


def _store_bytes(data, suffix):
    """Guarda una imagen hecha por la app (una mejorada) donde _store_image, por su contenido. Devuelve la ruta."""
    dest = _images() / (hashlib.sha1(data).hexdigest()[:16] + suffix)
    if not dest.is_file():
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    return str(dest)


def _network_path(path):
    """Ruta de red (\\\\servidor\\…): con solo mirarla, Windows se conecta y entrega el hash de la contraseña. Una
    revisión de otro (el .json junto al PDF) puede traerla, así que se mira el texto antes de tocar el disco."""
    return re.match(r"[\\/]{2}", str(path)) is not None


def _font_file(path):
    """Fuente propia de una revisión o de los ajustes si es una ruta local absoluta a un .ttf/.otf; si no, ""."""
    path = str(path or "")
    return path if Path(path).is_absolute() and not _network_path(path) and path.lower().endswith((".ttf", ".otf")) \
        else ""


def _stored_image(path):
    """La ruta es de la carpeta de ilustraciones del programa (_store_image), la única que escribe la ventana."""
    folder = os.path.normcase(str(_images())) + os.sep
    return not _network_path(path) and os.path.normcase(os.path.normpath(str(path))).startswith(folder)


def _thumb_path(path):
    """Miniatura de la portada de un tomo para la pantalla de inicio, junto a las preferencias."""
    return SETTINGS.parent / "miniaturas" / (hashlib.sha1(str(Path(path).resolve()).lower().encode()).hexdigest()[:16]
                                             + ".jpg")


def _comment_block(blocks, texts, comment):
    """Párrafo del libro de un comentario: el que empieza por su texto (mejor en su página) o, si se editó, el más
    parecido de su página. texts: [(índice, texto)] de los párrafos. None si ya no está."""
    start, page = comment["text"], comment.get("page")
    hits = [k for k, t in texts if t.startswith(start)]
    if hits:
        return min(hits, key=lambda k: blocks[k].page != page)
    near = [(difflib.SequenceMatcher(None, t[:len(start)], start).ratio(), k) for k, t in texts if blocks[k].page == page]
    ratio, k = max(near, default=(0, None))
    return k if ratio >= 0.6 else None


def _ask_line(parent, title, label, text):
    """Una línea de texto pedida al usuario, o None si cancela."""
    value, ok = QInputDialog.getText(parent, title, label, text=text)
    return value if ok else None


def _ask_note(parent, note):
    """Texto del comentario del revisor (note: el de ahora), o None si se cancela."""
    text, ok = QInputDialog.getMultiLineText(parent, "Comentario del revisor",
                                             "Nota sobre este párrafo, para ti o para el traductor (no sale en el "
                                             "EPUB). Vacía, quita el comentario:", note)
    return text if ok else None


MAX_PATH = 260  # caracteres de ruta desde los que Windows y casi todos los programas ya no abren un archivo
NO_VOICE = ("Windows no tiene ninguna voz en español instalada.\n\nInstálala en Configuración → Hora e idioma → "
            "Voz → Agregar voces, elige Español y vuelve a pulsar «Leer».")
# Voz de Windows (System.Speech): lee una línea de la entrada, la dice y responde «ok». Sin voz en español, «novoice»
SPEECH_SCRIPT = r"""
[Console]::InputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$v = $s.GetInstalledVoices() | Where-Object { $_.Enabled -and $_.VoiceInfo.Culture.Name -like 'es*' } | Select-Object -First 1
if (-not $v) { [Console]::Out.WriteLine('novoice'); exit }
$s.SelectVoice($v.VoiceInfo.Name)
while ($null -ne ($line = [Console]::In.ReadLine())) { $s.Speak($line); [Console]::Out.WriteLine('ok') }
"""


class Speaker(QObject):
    """Lee en voz alta con la voz de Windows, sin dependencias: un PowerShell que vive mientras se lee y recibe un
    párrafo por línea. Parar lo cierra (la voz se calla en el acto); el siguiente «Leer» abre otro. Con la tubería
    de QProcess, PowerShell leía el primer párrafo y no veía los siguientes: una tubería normal y un hilo que
    escucha sus «ok»."""
    done = Signal()       # acabó el párrafo
    failed = Signal(str)  # el motivo, para el usuario
    heard = Signal(object, str)  # (proceso, línea), del hilo que escucha a la ventana

    def __init__(self):
        super().__init__()
        self.proc = None
        self.heard.connect(self._heard)

    def say(self, text):
        try:
            if self.proc is None:
                script = SPEECH_SCRIPT.encode("utf-16-le")  # -EncodedCommand: sin líos de comillas
                self.proc = subprocess.Popen(
                    [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand",
                     base64.b64encode(script).decode()], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                threading.Thread(target=self._listen, args=(self.proc,), daemon=True).start()
            self.proc.stdin.write((" ".join(text.split()) + "\n").encode("utf-8"))
            self.proc.stdin.flush()
        except OSError:
            self._fail("No se pudo abrir la voz de Windows (PowerShell).")

    def _listen(self, proc):
        for raw in proc.stdout:  # termina al cerrarse el proceso
            try:
                self.heard.emit(proc, raw.decode("utf-8", "replace").strip())
            except RuntimeError:  # el editor ya se cerró
                return

    def _heard(self, proc, line):
        if proc is not self.proc:  # de una lectura ya parada
            return
        if line == "ok":
            self.done.emit()
        elif line == "novoice":
            self._fail(NO_VOICE)

    def _fail(self, message):
        self.stop()
        self.failed.emit(message)

    def stop(self):
        proc, self.proc = self.proc, None
        if proc is not None:
            with contextlib.suppress(OSError):
                proc.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(1)


def _render_thumbs(jobs):
    """Proceso aparte: miniatura de la primera página de cada tomo [(archivo, destino)], que en las novelas es su
    portada. En la ventana, PyMuPDF congelaba el arranque."""
    for src, dest in jobs:
        try:
            with convert.open_pdf(src) as doc:
                page = doc[0]
                zoom = 124 / max(page.rect.height, 1)
                data = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).tobytes("jpg", jpg_quality=85)
            Path(dest).parent.mkdir(parents=True, exist_ok=True)
            Path(dest).write_bytes(data)
        except Exception:  # PDF roto, protegido o vacío: la tarjeta se queda con el icono
            continue


def _start_thumbs(jobs):
    """Arranca _render_thumbs; None si no se pudo (la tarjeta se queda con el icono)."""
    try:
        proc = multiprocessing.Process(target=_render_thumbs, args=(jobs,), daemon=True)
        proc.start()
        return proc
    except Exception:
        return None


def _standby(events, jobs):
    """Proceso de trabajo preparado de antemano: con todo ya importado, espera su encargo (work, args, report) y lo
    hace como _worker. Arrancar uno nuevo para cada trabajo costaba 1,7 s en el .exe antes de empezar."""
    parent = multiprocessing.parent_process()
    while True:
        try:
            job = jobs.get(timeout=2)
            break
        except queue.Empty:
            if parent is not None and not parent.is_alive():
                return  # la ventana murió sin avisar: nadie le daría encargo
    _worker(events, *job)


def _start_standby():
    """(proceso, eventos, encargos) de _standby, o None si no arranca: los trabajos arrancan su proceso al momento."""
    try:
        events, jobs = multiprocessing.Queue(), multiprocessing.Queue()
        proc = multiprocessing.Process(target=_standby, args=(events, jobs), daemon=True)
        proc.start()
        return proc, events, jobs
    except Exception:
        LOG.warning("No se pudo preparar el proceso de trabajo", exc_info=True)
        return None


def _start_spell_process():
    """(peticiones, respuestas, proceso) de _spell_server, o None si no arranca: el editor sigue sin él."""
    try:
        requests, replies = multiprocessing.Queue(), multiprocessing.Queue()
        # al cerrar, el servidor se detiene y nadie lee lo que quedó por enviar: sin esto, la app no termina
        requests.cancel_join_thread()
        proc = multiprocessing.Process(target=_spell_server, args=(requests, replies), daemon=True)
        proc.start()
        return requests, replies, proc
    except Exception:
        return None


def _toc_page(doc):
    """Página del índice impreso (desde 0) entre las 30 primeras de doc; None si no lo tiene."""
    for n in range(min(30, doc.page_count if doc else 0)):
        if any(line.strip().lower() in TOC_TITLES for line in doc[n].get_text().splitlines()):
            return n
    return None


def _analyze(pdf, progress=None):
    """convert.analyze, las ilustraciones repetidas, el informe de calidad y la página del índice impreso, en el
    proceso de trabajo: en la ventana congelaban al abrir (Kage 5: 2,4 s las repetidas; Nageki 1: 0,9 s el informe;
    Strange Grief 1: 0,8 s leer sus 30 primeras páginas buscando el índice)."""
    book = convert.analyze(pdf, progress)
    _fit_review(pdf, book)
    with contextlib.closing(convert.open_pdf(pdf)) as doc:  # la ventana lo abre sin volver a recorrerlo
        shown = {"toc": _toc_page(doc), "jpx": doc.jpx}
    return book, convert.duplicate_images(book), convert.quality_report(book), convert.report_memos(), shown


def _sweep_temp(age=86400):
    """Borra de %TEMP% los datos de trabajos y las vistas previas de más de un día: los deja una ventana que se
    cerró de golpe (corte de luz, Administrador de tareas)."""
    for path in Path(tempfile.gettempdir()).glob("lnepub-*"):
        if path.name.startswith(("lnepub-trabajo-", "lnepub-vista-")) and path.suffix in (".pickle", ".epub"):
            with contextlib.suppress(OSError):  # otra ventana lo tiene abierto
                if time.time() - path.stat().st_mtime > age:
                    path.unlink()
        elif path.name.startswith("lnepub-pdf-") and path.is_dir():  # la de write_pdf que el antivirus no dejó borrar
            with contextlib.suppress(OSError):
                if time.time() - path.stat().st_mtime > age:
                    shutil.rmtree(path, ignore_errors=True)


def _write_json(path, data):
    """Escribe el .json entero o nada: un corte a mitad dejaba el archivo roto y la revisión se ignoraba."""
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        for attempt in range(5):  # OneDrive o el antivirus tienen el archivo abierto un momento al sincronizarlo
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.2)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def _same_type(value, like):
    """Valor de ajustes.json válido para una opción: el mismo tipo que su valor de fábrica (escrito a mano puede
    traer «sí» donde va true)."""
    return isinstance(value, bool) if isinstance(like, bool) else type(value) is type(like)


def _write_options(state, breaks=()):
    """Opciones de write_epub a partir de una revisión, de los campos de la ventana o de lo heredado."""
    lang, style = str(state.get("lang", "es")).strip(), state.get("style")
    font, path = state.get("font"), _font_file(state.get("font_file"))
    return {"lang": lang if LANG.fullmatch(lang) else "es",
            "typography": bool(state.get("typography", True)),
            "style": style if style in convert.STYLES or style == convert.ORIGINAL else "Clásico",
            "separator": convert.separator_choice(str(state.get("separator", "Según el estilo"))),
            "separator_icon": icon if (icon := str(state.get("separator_icon") or "")).startswith("<svg") else "",
            "shrink": int(state.get("shrink_size") or convert.SHRINK) if state.get("shrink", True) else False,
            "replacements": [tuple(rule) for rule in state.get("replacements", [])],
            "credits": bool(state.get("credits")), "gray": bool(state.get("gray")), "trim": bool(state.get("trim")),
            "font": font if font in convert.FONTS
            else path if path and Path(path).name == font and Path(path).is_file() else "",
            "epub2": bool(state.get("epub2")), "reading": bool(state.get("reading")), "kepub": bool(state.get("kepub")),
            "horizontal": bool(state.get("horizontal", True)),
            "contents": bool(state.get("contents", True)), "hyphenate": bool(state.get("hyphenate")), "breaks": list(breaks),
            "synopsis": bool(state.get("synopsis", True)), "endnotes": bool(state.get("endnotes")),
            "note_style": note if (note := str(state.get("note_style", "1"))) in convert.NOTE_STYLES else "1",
            "box_style": box if (box := str(state.get("box_style", "Sencillo"))) in convert.BOX_STYLES else "Sencillo",
            "heading_font": _heading_font(state), "logo": _image_file(state.get("logo")),
            "title_image": str(state.get("title_image") or ""),
            "alts": {str(k): str(v) for k, v in state["alts"].items()} if isinstance(state.get("alts"), dict) else {}}


def _heading_font(state):
    """Fuente de títulos de write_epub: una de las incluidas o la ruta de la propia si sigue en su sitio."""
    choice = str(state.get("heading_font", ""))
    if choice in convert.HEADING_FONTS:
        return choice
    return _font_file(state.get("heading_font_file")) if choice == HEADING_OWN else ""


def _image_file(path):
    """Imagen PNG o JPG local de los ajustes (el logo); "" si no lo es o ya no existe."""
    path = str(path or "")
    return path if (Path(path).is_absolute() and not _network_path(path) and path.lower().endswith((".png", ".jpg", ".jpeg"))
                    and Path(path).is_file()) else ""


def _write_pdf(book, chapters, out, removed, options, size, progress=None):
    """convert.write_pdf en el proceso de trabajo (maquetar un libro largo tarda unos segundos)."""
    convert.write_pdf(book, chapters, out, size, progress, removed=removed, **options)
    return []


def _write_checked(book, chapters, out, removed, options, progress=None):
    """write_epub y, en el mismo proceso de trabajo, check_epub: devuelve los problemas del EPUB."""
    convert.write_epub(book, chapters, out, removed=removed, progress=progress, **options)
    rebuilt = ["EPUB 2 no admite el HTML del EPUB original: el libro se rehízo con el aspecto de la app."
               ] if book.origin and options.get("epub2") else []
    return rebuilt + convert.check_epub(out)


def _epub_name(path, kepub):
    """Nombre del EPUB: el Kobo solo usa su motor KEPUB si el archivo acaba en .kepub.epub."""
    return re.sub(r"(\.kepub)?\.epub$", "", str(path), flags=re.I) + (".kepub.epub" if kepub else ".epub")


def _confirm_name(chosen, final, parent=None):
    """El diálogo de guardar solo pregunta antes de pisar el nombre elegido; si luego cambia (la extensión o el
    .kepub.epub del Kobo se añaden después), un archivo con el nombre final se reemplaza solo si el usuario quiere."""
    return (os.path.normcase(chosen) == os.path.normcase(final) or not Path(final).exists()
            or messagebox.askyesno("Reemplazar", f"Ya existe «{Path(final).name}».\n\n¿Reemplazarlo?", parent=parent))


def _out(source):
    """Ruta del EPUB de salida sin extensión: junto al original y con su nombre; un EPUB de entrada lleva
    además «(LNC)» para no pisarse."""
    source = Path(source)
    return source.with_name(source.stem + " (LNC)") if source.suffix.lower() == ".epub" else source.with_suffix("")


def _has_epub(source):
    """El original ya tiene su EPUB de salida (normal o KEPUB)."""
    return any(Path(_epub_name(_out(source), kepub)).exists() for kepub in (False, True))


def _sources(folder):
    """PDF y EPUB de la carpeta que se pueden convertir. Un EPUB que es la salida de otro archivo no lo es:
    acaba en «(LNC)» o .kepub.epub, o hay un PDF con su nombre."""
    found = []
    for path in sorted(Path(folder).iterdir()):
        suffix = path.suffix.lower()
        if suffix == ".pdf" or (suffix == ".epub" and not path.stem.endswith(" (LNC)")
                                and not path.name.lower().endswith(".kepub.epub")
                                and not any(path.with_suffix(ext).exists() for ext in (".pdf", ".PDF"))):
            found.append(path)
    return [path for path in found if path.is_file()]


KINDLE_MAIL = 50_000_000  # límite de Send to Kindle por correo; la web admite hasta 200 MB


# Etiquetas de formato en un título: en la lista (y en el índice del EPUB) el título va sin ellas. Solo estas: un
# título como «<Interludio>» se queda como está
TITLE_TAGS = re.compile(r"</?(?:b|i|em|strong|span|sup|sub|small|sc|u|s|br|a)\b[^>]*>", re.I)
# la llamada a una nota (EPUB) no es parte del nombre: «saisen-box❮01❯» con la etiqueta a la vista en la lista
NOTE_CALL = re.compile(r'<a\b[^>]*noteref[^>]*>.*?</a>', re.I | re.S)


def _title_text(text):
    """Título de una fila sin etiquetas de formato («<b>La Tienda</b>» -> «La Tienda»)."""
    cut = TITLE_TAGS.sub(lambda m: " " if m[0][1:3].lower() == "br" else "", NOTE_CALL.sub("", text))  # «Capí<i>tulo</i>» sin espacio
    return " ".join(html.unescape(cut).split()) if "<" in text or "&" in text else text


def _block_title(block):
    """El nombre de la fila de un título: el del índice del EPUB de entrada o su texto, sin etiquetas."""
    return block.title or _title_text(block.html)


def _size_text(size):
    """Tamaño en MB decimales, como da Amazon sus límites, con coma decimal."""
    return f"{size / 1e6:.1f} MB".replace(".", ",")


def _file_size(size):
    """Peso de un archivo para leerlo de un vistazo: B, KB o MB (decimales, como _size_text), con coma."""
    if size < 1000:
        return f"{size} B"
    return f"{size / 1000:.1f} KB".replace(".", ",") if size < 1e6 else _size_text(size)


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


# La revisión (tipos, títulos editados, datos del libro y opciones) se guarda junto al PDF en un
# .json: al volver a abrir el mismo PDF se recupera, aunque se haya cerrado la ventana sin generar.
def _review_path(pdf):
    return Path(pdf).with_name(Path(pdf).stem + ".lnepub.json")


WORK_KEYS = ("minutes", "paragraphs", "reviewed")


def _log_work(work, day, **amounts):
    """Suma minutos, párrafos cambiados o capítulos revisados al día day ("AAAA-MM-DD") de work."""
    entry = work.setdefault(day, {})
    for key, n in amounts.items():
        entry[key] = round(entry.get(key, 0) + n, 1)


def _work_summary(work, today):
    """[(rótulo, minutos, párrafos, revisados)] de hoy, de los últimos 7 días y del total del libro."""
    from datetime import date, timedelta
    week = {(date.fromisoformat(today) - timedelta(days=n)).isoformat() for n in range(7)}
    out = []
    for label, days in (("Hoy", {today}), ("Últimos 7 días", week), ("Total del libro", None)):
        picked = [w for day, w in work.items() if days is None or day in days]
        out.append((label, *(sum(w.get(key, 0) for w in picked) for key in WORK_KEYS)))
    return out


def _duration(minutes):
    """«45 min», «1 h 05 min»."""
    minutes = round(minutes)
    return f"{minutes // 60} h {minutes % 60:02d} min" if minutes >= 60 else f"{minutes} min"


def _draft_path(pdf):
    """Borrador del editor: lo cambiado sin aceptar, por si la aplicación se cierra a mitad."""
    return Path(pdf).with_name(Path(pdf).stem + ".lnepub.borrador.json")


def _translation_path(pdf):
    return Path(pdf).with_name(Path(pdf).stem + ".lnepub.traduccion.json")


def _grammar_path(pdf):
    return Path(pdf).with_name(Path(pdf).stem + ".lnepub.gramatica.json")


def _read_translation(pdf):
    """Traducciones guardadas de este libro: {"src", "dst", "done": {clave del original: {"text", "hand", "src",
    "dst"}}}. Cada párrafo lleva sus idiomas: se puede haber traducido parte a otro por error. Los guardados antes
    sin ellos toman los del archivo. Además, si los tiene: "source" (el original, para la memoria de la serie),
    "memory" (la traducción viene de otro tomo) y "review" (lo que dijo la IA revisora: {"for": clave de la
    traducción que miró, y "ok" o "problem" y "fix"})."""
    return _read_translation_file(_translation_path(pdf))


def _read_translation_file(path):
    empty = {"src": "auto", "dst": "es", "done": {}}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        src, dst = str(data.get("src") or "auto"), str(data.get("dst") or "es")
        done = {}
        for k, v in data["done"].items():
            if isinstance(v, dict) and str(v.get("text") or "").strip():  # el .json se puede editar a mano
                entry = done[str(k)] = {"text": str(v["text"]), "hand": bool(v.get("hand")),
                                        "src": str(v.get("src") or src), "dst": str(v.get("dst") or dst)}
                if v.get("source"):
                    entry["source"] = str(v["source"])
                if v.get("memory"):
                    entry["memory"] = True
                check = v.get("review")
                if isinstance(check, dict) and check.get("for"):
                    entry["review"] = {"for": str(check["for"])} | (
                        {"problem": str(check.get("problem") or ""), "fix": str(check["fix"])}
                        if str(check.get("fix") or "").strip() else {"ok": True})
        return {"src": src, "dst": dst, "done": done}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return empty


def _memory_path():
    return SETTINGS.with_suffix(".memoria.json")  # junto a las preferencias: vale para cualquier carpeta


def _memory_index():
    """{serie en minúsculas: [archivos de traducción de sus tomos]}."""
    try:
        store = json.loads(_memory_path().read_text(encoding="utf-8"))
        return store if isinstance(store, dict) else {}
    except (OSError, ValueError):
        return {}


def _remember_translation(pdf, series):
    """Apunta dónde está la traducción de este tomo con el nombre de su serie: los demás tomos reutilizan lo ya
    traducido aunque estén en otra carpeta. No copia nada: cada tomo sigue con su archivo."""
    series, path = series.strip().lower(), str(_translation_path(pdf).resolve())
    store = _memory_index()
    paths = store.get(series) if isinstance(store.get(series), list) else []
    if not series or path in paths:
        return
    store[series] = paths + [path]
    try:
        _memory_path().parent.mkdir(parents=True, exist_ok=True)
        _write_json(_memory_path(), store)
    except OSError:
        pass  # sin esto solo se reutiliza lo de la misma carpeta


def _series_memory(pdf, series):
    """Lo ya traducido en los otros tomos de la serie: {clave del original: {"text", "hand", "src", "dst", "volume"
    y, si se guardó, "source"}}. Tomos apuntados por _remember_translation y los de la carpeta de este (uno movido
    pierde su ruta); cada uno, solo si su revisión dice que es de la serie. Entre dos tomos gana lo corregido a
    mano y luego el más reciente."""
    series = series.strip().lower()
    if not series:
        return {}
    store, own = _memory_index(), _translation_path(pdf).resolve()
    listed = [Path(p) for p in store.get(series, []) if isinstance(p, str)] if isinstance(store.get(series), list) else []
    alive = [p for p in listed if p.is_file()]
    if len(alive) != len(listed):  # tomos borrados o movidos: fuera del índice
        store[series] = [str(p) for p in alive]
        try:
            _write_json(_memory_path(), store)
        except OSError:
            pass
    found, seen = [], {own}
    for path in alive + sorted(own.parent.glob("*.lnepub.traduccion.json")):
        path = path.resolve()
        if path in seen:
            continue
        seen.add(path)
        try:
            review = path.with_name(path.name.removesuffix(".traduccion.json") + ".json")
            state = json.loads(review.read_text(encoding="utf-8"))
            if str(state.get("series", "")).strip().lower() == series:
                found.append((path.stat().st_mtime, str(state.get("volume", "")).strip(),
                              _read_translation_file(path)["done"]))
        except (OSError, ValueError, AttributeError):
            continue
    memory = {}
    for _, volume, done in sorted(found, key=lambda f: f[0]):
        for k, entry in done.items():
            if k not in memory or entry["hand"] or not memory[k]["hand"]:
                memory[k] = entry | {"volume": volume}
    return memory


def _translate_thread(job, out, stop):
    """Traduce job["groups"] (uno por capítulo: {"items": [(clave, texto)], "context", "chapter"}) por lotes y pone
    en out ("done", {clave: traducción}) tras cada uno, ("error", mensaje) si falla y ("end",) al acabar. Van
    job["workers"] capítulos a la vez, cada uno con su contexto: el libro sale antes y el tono no se mezcla. Es un
    hilo, no un proceso: la red no retiene el GIL (PyMuPDF sí) y así pausar es solo marcar stop.
    Con job["mode"] == "review" no traduce: la IA revisa lo traducido. Los items son (clave, original, traducción) y
    pone ("reviewed", {clave: (traducción que miró, resultado de translate.review)})."""
    kind = translate.ENGINES[job["engine"]]["kind"]

    def check(group):
        items, style = group["items"], job["style"] | {"chapter": group["chapter"]}
        for batch in translate.batches([a + b for _, a, b in items], kind):  # cada párrafo va dos veces
            if stop.is_set():
                return
            part = [items[n] for n in batch]
            result = translate.review(job["engine"], [(a, b) for _, a, b in part], job["src"], job["dst"], job["key"],
                                      job["model"], job["base"], job["glossary"], style)
            out.put(("reviewed", {k: (b, found) for (k, _, b), found in zip(part, result)}))

    def chapter(group):
        if job.get("mode") == "review":
            return check(group)
        texts, context = [t for _, t in group["items"]], list(group["context"])
        style = job["style"] | {"chapter": group["chapter"]}
        for batch in translate.batches(texts, kind):
            if stop.is_set():
                return
            part = [texts[n] for n in batch]
            result = translate.translate(job["engine"], part, job["src"], job["dst"], job["key"], job["model"],
                                         job["base"], job["glossary"], context, style)
            context = (context + list(zip(part, result)))[-translate.CONTEXT:]
            out.put(("done", {group["items"][n][0]: t for n, t in zip(batch, result)}))

    try:
        with concurrent.futures.ThreadPoolExecutor(job["workers"]) as pool:
            try:
                for future in [pool.submit(chapter, group) for group in job["groups"]]:
                    future.result()
            except BaseException:
                stop.set()  # los demás capítulos acaban su lote, que se guarda, y paran
                raise
    except translate.TranslateError as e:
        out.put(("error", str(e)))
    except Exception as e:
        out.put(("error", f"Fallo inesperado al traducir: {e or type(e).__name__}"))
        out.put(("log", traceback.format_exc()))
    out.put(("end",))


def _fingerprint(pdf):
    """Tamaño, fecha y resumen del final del PDF (su índice y su /ID): si cambian el tamaño o el resumen, la
    revisión guardada es de otro PDF y no se usa. La fecha no cuenta: OneDrive la cambia al sincronizar."""
    with open(pdf, "rb") as f:
        size = os.fstat(f.fileno()).st_size
        f.seek(max(0, size - 65536))
        return [size, os.fstat(f.fileno()).st_mtime_ns, hashlib.sha1(f.read()).hexdigest()]


def _same_pdf(saved, pdf):
    """saved (el "pdf" de una revisión) es de este PDF. Las revisiones de antes no traen resumen: basta el tamaño."""
    now = _fingerprint(pdf)
    return saved[0] == now[0] and list(saved[2:]) in ([], now[2:])


def _read_review(pdf, blocks=None):
    """Revisión guardada de este PDF, o None si no hay o si el PDF cambió desde entonces. Sin blocks
    (el panel de serie, que no analiza) solo se compara el PDF (_same_pdf: sin la fecha, que OneDrive cambia y
    apartaba la revisión aunque el PDF fuera el mismo)."""
    try:
        state = json.loads(_review_path(pdf).read_text(encoding="utf-8"))
        if (_same_pdf(state["pdf"], pdf) and blocks in (None, state["blocks"])
                and isinstance(state["rows"], dict)):
            return state
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        pass
    return None


def _migrate_review(state, book):
    """Revisión hecha con el análisis de antes de convert._credit_pages_as_images (la página del grupo como texto),
    trasladada a los bloques de ahora: sus cambios se repiten sobre el libro de antes y cada índice se cuenta con
    la página ya en una ilustración. None si no es de ese análisis o si algún cambio tocaba la página del grupo."""
    raw, candidates, spans_before = convert.uncredited(book)
    raw = [dataclasses.replace(b) for b in raw]  # copias: repetir los cambios no toca el libro
    regions = [raw[s:s + n] for s, n in spans_before]
    if not regions or state.get("blocks") != len(raw):
        return None
    work = dataclasses.replace(book, blocks=raw, candidates=candidates, credit_pages=[])

    def spans():  # dónde está ahora cada página del grupo: (inicio, bloques)
        found = []
        for region in regions:
            at = next((i for i, b in enumerate(work.blocks) if b is region[0]), None)
            if at is None or any(work.blocks[at + j] is not b for j, b in enumerate(region)):
                raise LookupError
            found.append((at, len(region)))
        return found

    def new(i, where):  # índice de antes -> de ahora; dentro de una página del grupo no hay
        if any(s < i < s + n for s, n in where):
            raise LookupError
        return i - sum(n - 1 for s, n in where if s + n <= i)

    edits = []
    try:
        for edit in state.get("edits", []):
            where = spans()
            if edit[0] == "image":
                edits.append(["image", new(int(edit[1]), where), edit[2]])
            elif edit[0] == "head":
                edits.append(["head", new(int(edit[1]), where), *edit[2:5]])
            elif edit[0] == "move":
                start, end, dest = int(edit[1]), int(edit[2]), int(edit[3])
                if any(start < s + n and end > s for s, n in where):
                    raise LookupError
                edits.append(["move", new(start, where), new(end, where), new(dest, where)])
                convert.move_section(work, start, end, dest)
            else:
                start, end = int(edit[0]), int(edit[1])
                if any(start < s + n and end > s for s, n in where):
                    raise LookupError
                edits.append([new(start, where), new(end, where), edit[2]])
                convert.edit_section(work, start, end, str(edit[2]), _images())
        where = spans()
        rows = {}
        for item, row in state["rows"].items():
            k = int(item)
            if not any(s <= k < s + n for s, n in where):  # las filas de la página del grupo ya no existen
                rows[str(new(k, where))] = row
        at = state.get("editor_at")
        at = new(at, where) if isinstance(at, int) and not any(s <= at < s + n for s, n in where) else None
    except (LookupError, ValueError, TypeError, IndexError):
        return None
    return {**state, "blocks": len(book.blocks), "edits": edits, "rows": rows, "editor_at": at}


def _migrate_dropped(state, book):
    """Revisión hecha cuando los títulos de solo ilustraciones (Witch 1: «Insert», «Title Page») aún eran
    capítulos, trasladada: cada índice se cuenta sin ellos y sus filas se van. None si no es de ese análisis o si
    algún cambio de texto los tocaba."""
    marks = [i for i, _ in book.dropped_titles]  # dónde estaban, en el análisis de antes
    if not marks or state.get("blocks") != len(book.blocks) + len(marks):
        return None

    def new(i):
        if i in marks:
            raise LookupError
        return i - sum(1 for m in marks if m < i)

    try:
        edits = []
        for edit in state.get("edits", []):
            if edit[0] == "image":
                edits.append(["image", new(int(edit[1])), edit[2]])
            elif edit[0] == "head":
                edits.append(["head", new(int(edit[1])), *edit[2:5]])
            elif edit[0] == "move" or int(edit[0]) <= max(marks):
                raise LookupError  # mover, o reescribir antes de ellos (cambia dónde quedan): ya no se puede contar
            else:
                edits.append([new(int(edit[0])), new(int(edit[1])), edit[2]])
        rows = {str(new(int(item))): row for item, row in state["rows"].items() if int(item) not in marks}
        at = state.get("editor_at")
        at = new(at) if isinstance(at, int) and at not in marks else None
    except (LookupError, ValueError, TypeError, IndexError, KeyError):
        return None
    return {**state, "blocks": len(book.blocks), "edits": edits, "rows": rows, "editor_at": at}


def _review_for(pdf, book):
    """La revisión guardada de pdf para este análisis: la suya o, si se hizo con la página del grupo como texto o
    con los títulos de solo ilustraciones, trasladada (_migrate_review, _migrate_dropped). None si no hay o es de
    otro análisis."""
    state = _read_review(pdf, len(book.blocks))
    if book.origin and state and not state.get("faithful"):  # de cuando un EPUB se leía como PDF: no casa
        return None
    if state or not (book.credit_pages or book.dropped_titles):
        return state
    old = _read_review(pdf)
    if not old:
        return None
    return (_migrate_review(old, book) if book.credit_pages else None) or _migrate_dropped(old, book)


def _epub_block_page(book, doc, k):
    """Página (desde 0) del original de MuPDF donde está el bloque k de un EPUB: el bloque solo sabe la primera
    página de su archivo, y un capítulo ocupa varias. Se busca su texto (o el del párrafo siguiente, si es una
    ilustración o un separador) en las páginas de su archivo."""
    block = book.blocks[k]
    start = block.page - 1
    if doc is None or not block.src:
        return start
    end = next((b.page - 1 for b in book.blocks[k:] if b.src and b.src[0] != block.src[0]), doc.page_count)
    if block.kind == "image":  # la n.ª imagen de su archivo: la página donde se llega a ella
        nth = sum(1 for b in book.blocks[:k] if b.image and b.src and b.src[0] == block.src[0])
        seen = 0
        for i in range(start, min(end + 1, doc.page_count)):
            seen += len(doc[i].get_image_info())
            if seen > nth:
                return i
        return start
    probe = next((b for b in book.blocks[k:k + 8] if b.kind == "p" and b.src and b.src[0] == block.src[0]
                  and len(_source_plain(b)) >= 4), None)
    if probe is None:
        return start
    if convert.CJK.search(_source_plain(probe)):  # japonés: 24 letras seguidas cruzan de línea y search_for no las halla
        # «「ん？」» es una letra y sale en cualquier página: se busca el primer párrafo con algo más
        longer = next((b for b in book.blocks[k:k + 8] if b.kind == "p" and b.src and b.src[0] == block.src[0]
                       and len(_match_key(_source_plain(b))) >= 6), probe)
        key = _match_key(_source_plain(longer))[:16]
        return next((i for i in range(start, min(end + 1, doc.page_count)) if key in _match_key(doc[i].get_text())),
                    start)
    words = " ".join(_source_plain(probe).translate(convert.PREVIEW_TABLE).split())[:24]
    return next((i for i in range(start, min(end + 1, doc.page_count)) if doc[i].search_for(words)), start)


@functools.lru_cache(maxsize=8)  # se busca párrafo a párrafo en las mismas páginas
def _page_chars(doc, number):
    """Letras de la página (sin espacios) en orden de lectura y el recuadro de cada una."""
    chars = [(c["c"], pymupdf.Rect(c["bbox"])) for b in doc[number].get_text("rawdict")["blocks"] if b["type"] == 0
             for line in b["lines"] for span in line["spans"] for c in span["chars"] if not c["c"].isspace()]
    return "".join(c for c, _ in chars), [r for _, r in chars]


def _cjk_box(doc, number, text):
    """Recuadro de un párrafo japonés en la página, letra a letra desde su principio (el furigana va en línea,
    como en _plain); hasta el final de la página si sigue en la siguiente. None si no empieza en ella o si es
    corto y se repite («「──っ！」»)."""
    flat, boxes = _page_chars(doc, number)
    at = flat.find(text[:12])
    if at < 0 or (len(text) <= 6 and flat.count(text) > 1):
        return None
    box = pymupdf.Rect(boxes[at])
    for r in boxes[at + 1:at + len(text)]:
        box |= r
    return box


def _source_plain(block):
    """Texto del bloque como está en el original: el que se leyó de un EPUB (traducido o corregido, el original
    sigue con el de antes); en un PDF, el de ahora."""
    return convert._unhtml(block.orig) if block.orig else convert._plain(block)


def _match_key(text):
    """Texto para buscar un párrafo en una página dibujada: solo letras y cifras, en minúsculas (sin espacios,
    rayas ni comillas, que la tipografía cambia)."""
    return re.sub(r"\W+", "", text.translate(convert.PREVIEW_TABLE)).lower()


def websheet_text_file(blocks):
    import websheet  # aquí: websheet trae editor y Qt
    return websheet.text_file(blocks)


def _page_pixmap(page, matrix, dark=False):
    """Pixmap de una página; dark: como el modo oscuro de los lectores, invierte el papel y el texto, no las
    ilustraciones."""
    pix = page.get_pixmap(matrix=matrix)
    if dark:
        art = pymupdf.Pixmap(pix, 0)  # copia, sin alfa como pix (copy exige el mismo)
        pix.invert_irect()
        if pix.colorspace and pix.colorspace.n in (1, 3):  # el papel del gris de la hoja, la letra la del tema
            pix.tint_with(int(theme.DARK["sheet"][1:], 16), int(theme.DARK["ink"][1:], 16))
        for info in page.get_image_info():
            box = (pymupdf.Rect(info["bbox"]) * matrix).irect & pix.irect
            if not box.is_empty:
                pix.copy(art, box)
    return pix


def _fit_review(pdf, book):
    """Si la revisión de antes tocaba la página del grupo (Eminence 7 editó la suya) y no se puede trasladar, el
    libro vuelve a llevar esas páginas como texto: así la revisión coincide entera y no se pierde nada."""
    if not book.credit_pages or _review_for(pdf, book):
        return
    old = _read_review(pdf)
    blocks, candidates, _ = convert.uncredited(book)
    if old and old.get("blocks") == len(blocks):
        book.blocks, book.candidates, book.credit_pages = blocks, candidates, []


def _backup_path(path):
    """.bak libre para apartar path: uno anterior no se pisa (.bak, .2.bak, .3.bak…)."""
    backup, n = path.with_name(path.name + ".bak"), 1
    while backup.exists():
        n += 1
        backup = path.with_name(f"{path.name}.{n}.bak")
    return backup


def _set_aside(pdf):
    """Revisión guardada que ya no vale (otro análisis u otro PDF), apartada en .bak antes de que la ventana
    escriba la nueva encima. Devuelve la copia, o None si no había revisión."""
    review = _review_path(pdf)
    backup = _backup_path(review)
    try:
        os.replace(review, backup)
    except OSError:
        return None  # no había revisión
    return backup


# Versiones del trabajo de un libro, como los commits de git: copias completas de la revisión en un zip junto al
# PDF. Restaurar una no borra las posteriores: así conviven alternativas del mismo tomo.
# No cuentan para saber si una versión automática repite la última
VERSION_NOISE = ("problems", "pdf", "editor_at", "work", "faithful", "editor_history")
COMPARE_LIMIT = 300  # párrafos cambiados que se muestran al comparar: más no se leen y la ventana tardaría
FIELD_LABELS = {"title": "Título", "author": "Autor", "series": "Serie", "volume": "Vol.", "publisher": "Grupo",
                "translator": "Traductor", "illustrator": "Ilustraciones", "proofreader": "Corrección",
                "editor": "Edición", "original_title": "Título original", "subjects": "Géneros", "date": "Fecha",
                "lang": "Idioma", "description": "Sinopsis", "cover": "Portada", "style": "Estilo",
                "separator": "Separadores", "separator_icon": "Icono del separador", "font": "Fuente", "font_file": "Archivo de fuente", "profile": "Perfil",
                "typography": "Limpieza tipográfica", "gray": "Imágenes en gris", "shrink": "Reducir imágenes",
                "shrink_size": "Tamaño de las imágenes", "trim": "Recortar márgenes blancos",
                "credits": "Página de título y créditos", "reading": "Minutos de lectura", "contents": "Índice", "epub2": "EPUB 2",
                "horizontal": "Texto en horizontal",
                "kepub": "Formato Kobo (KEPUB)", "hyphenate": "Partir palabras con guiones",
                "autocase": "Mayúsculas del español al abrir", "replacements": "Reemplazos",
                "words": "Diccionario de la serie", "glossary": "Glosario", "tr_glossary": "Glosario de traducción",
                "tr_notes": "Personajes y estilo de traducción", "ignored": "Avisos ignorados", "comments": "Comentarios",
                "alts": "Descripciones de ilustraciones",
                "echoes": "Buscar ecos", "whole_words": "No partir palabras en el PDF",
                "pdf_size": "Tamaño de PDF preferido", **dict(MORE_FIELDS), "synopsis": "Página de sinopsis",
                "endnotes": "Notas al final del libro", "note_style": "Llamada de nota", "heading_font": "Fuente de títulos",
                "heading_font_file": "Archivo de fuente de títulos", "box_style": "Estilo de recuadros", "logo": "Logo",
                "title_image": "Ilustración de la página de título"}


def _versions_path(pdf):
    return Path(pdf).with_name(Path(pdf).stem + ".lnepub.versiones.zip")


def _load_versions(pdf):
    """Versiones guardadas de este PDF, de la más antigua a la más nueva, y el .bak donde se apartó el archivo si
    estaba dañado (si no, None): así la versión siguiente no lo pisa. OSError si no se puede leer."""
    path = _versions_path(pdf)
    try:
        with zipfile.ZipFile(path) as z:
            versions = [json.loads(z.read(name)) for name in sorted(z.namelist())]
        if all(isinstance(v, dict) and isinstance(v.get("state"), dict) and "name" in v for v in versions):
            return versions, None
    except FileNotFoundError:
        return [], None
    except (zipfile.BadZipFile, zlib.error, ValueError, KeyError):
        pass
    backup = _backup_path(path)
    os.replace(path, backup)
    return [], backup


def _write_versions(pdf, versions):
    """Escribe todas las versiones o ninguna, como _write_json; sin versiones, borra el archivo."""
    path = _versions_path(pdf)
    if not versions:
        path.unlink(missing_ok=True)
        return
    tmp = path.with_name(path.name + ".tmp")
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            for n, version in enumerate(versions):
                z.writestr(f"{n:04}.json", json.dumps(version, ensure_ascii=False))
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def _append_version(pdf, version):
    """Añade una versión al final del zip sin reescribir las de antes: con muchas, rehacerlo entero en cada EPUB
    generado tardaba más y más. Borrar y renombrar sí lo reescriben entero (_write_versions). Se añade sobre una
    copia y luego se cambia por el original: un corte a medias no deja el zip roto con todas las versiones dentro."""
    path = _versions_path(pdf)
    tmp = path.with_name(path.name + ".tmp")
    try:
        if path.exists():
            shutil.copyfile(path, tmp)  # copia de bytes, sin recomprimir: sigue siendo rápido
        with zipfile.ZipFile(tmp, "a", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            names = set(z.namelist())
            n = len(names)
            while f"{n:04}.json" in names:  # nombres por orden: _load_versions las lee así
                n += 1
            z.writestr(f"{n:04}.json", json.dumps(version, ensure_ascii=False))
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def _shown(key, value):
    """Valor de un dato al comparar versiones, como lo entiende quien lo eligió en la ventana."""
    if key == "cover":
        kind, choice = value if isinstance(value, list) and len(value) == 2 else (None, None)
        names = {"page": lambda: f"página {choice + 1}", "image": lambda: "una ilustración",
                 "file": lambda: Path(str(choice)).name, "text": lambda: "con título"}
        return names[kind]() if kind in names else "la del PDF"
    if isinstance(value, bool):
        return "sí" if value else "no"
    if isinstance(value, list):
        return str(len(value))
    return f"«{value}»" if str(value if value is not None else "").strip() else "(vacío)"


def _data_changes(old, new):
    """Líneas [(texto, tag)] de los datos del libro y las opciones que cambian de old a new."""
    lines = []
    for key, label in FIELD_LABELS.items():
        a, b = old.get(key), new.get(key)
        if a == b or (not a and not b):  # sin valor y vacío son lo mismo para quien lo lee
            continue
        if key in ("description", "separator_icon"):  # un texto largo o un SVG: no se lee entero
            lines.append([(f"{label}: {'quitado' if not b else 'puesto' if not a else 'cambiado'}"
                           if key == "separator_icon" else f"{label}: cambiada", None)])
        else:
            lines.append([(f"{label}: ", None), (_shown(key, a), "old"), (" → ", None), (_shown(key, b), "new")])
    images = [sum(1 for e in s.get("edits", []) if e[:1] == ["image"]) for s in (old, new)]
    if images[0] != images[1]:
        lines.append([("Ilustraciones cambiadas por otra imagen: ", None), (str(images[0]), "old"), (" → ", None),
                      (str(images[1]), "new")])
    return lines


def _rows_of(state):
    """(tipo, título) de las filas de la lista, en su orden."""
    rows = state.get("rows", {})
    return [tuple(rows[k][:2]) for k in sorted(rows, key=int)]


def _row_changes(old, new):
    """Líneas [(texto, tag)] de las filas de la lista añadidas, quitadas, renombradas o con otro tipo."""
    a, b, lines = _rows_of(old), _rows_of(new), []
    ops = difflib.SequenceMatcher(None, [t for _, t in a], [t for _, t in b], autojunk=False).get_opcodes()
    for op, i1, i2, j1, j2 in ops:
        if op == "equal":
            lines += [[(f"«{t}»: ", None), (ka, "old"), (" → ", None), (kb, "new")]
                      for (ka, t), (kb, _) in zip(a[i1:i2], b[j1:j2]) if ka != kb]
            continue
        pairs = list(zip(a[i1:i2], b[j1:j2])) if op == "replace" else []
        for (ka, ta), (kb, tb) in pairs:
            kind = [(f" ({ka} → {kb})", None)] if ka != kb else []
            lines.append([("«", None), (ta, "old"), ("» → «", None), (tb, "new"), ("»", None)] + kind)
        lines += [[("Ya no está: ", None), (f"«{t}» ({k})", "old")] for k, t in a[i1 + len(pairs):i2]]
        lines += [[("Nueva: ", None), (f"«{t}» ({k})", "new")] for k, t in b[j1 + len(pairs):j2]]
    return lines


def _plain_lines(blocks):
    """(página, texto) de los párrafos, títulos y separadores: lo que se compara del texto entre versiones."""
    return [(b.page, ("# " if b.kind == "heading" else "") + html.unescape(convert.TAGS.sub("", b.html)))
            for b in blocks if b.kind in ("p", "heading", "break")]


def _word_diff(before, after):
    """Un párrafo con lo quitado ("old") y lo puesto ("new") palabra a palabra (en japonés, letra a letra: no lleva
    espacios), como el control de cambios de Word."""
    x, y = editor.WORDS.findall(before), editor.WORDS.findall(after)
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, x, y, autojunk=False).get_opcodes():
        if op == "equal":
            out.append(("".join(x[i1:i2]), None))
            continue
        if i2 > i1:
            out.append(("".join(x[i1:i2]), "old"))
        if j2 > j1:
            out.append(("".join(y[j1:j2]), "new"))
    return out


def _text_changes(old_blocks, new_blocks):
    """Líneas [(texto, tag)] de los párrafos que cambian, con su página."""
    a, b, lines = _plain_lines(old_blocks), _plain_lines(new_blocks), []
    ops = difflib.SequenceMatcher(None, [t for _, t in a], [t for _, t in b], autojunk=False).get_opcodes()
    for op, i1, i2, j1, j2 in ops:
        if op == "equal":
            continue
        olds, news = a[i1:i2], b[j1:j2]
        for n in range(max(len(olds), len(news))):
            o, w = olds[n] if n < len(olds) else None, news[n] if n < len(news) else None
            lines.append([(f"pág. {(w or o)[0]} · ", "note")] + _word_diff(o[1] if o else "", w[1] if w else ""))
    return lines


def _compare(old, new, old_blocks=None, new_blocks=None):
    """Qué cambia de la revisión old a new, como [(texto, tag)] para un TextBox: "head" en los apartados, "old" y
    "new" en lo quitado y lo puesto. Sin los bloques de las dos (otro análisis), el texto no se compara."""
    parts = [("Datos del libro y opciones", _data_changes(old, new)), ("Lista", _row_changes(old, new))]
    if old_blocks is None or new_blocks is None:
        parts.append(("Texto", [[("No se compara el texto: la versión es de otro análisis del PDF.", "note")]]))
    else:
        lines = _text_changes(old_blocks, new_blocks)
        if len(lines) > COMPARE_LIMIT:
            more = convert.counted(len(lines) - COMPARE_LIMIT, "párrafo cambiado más", "párrafos cambiados más")
            lines = lines[:COMPARE_LIMIT] + [[(f"… y {more}", "note")]]
        parts.append(("Texto", lines))
    out = []
    for title, lines in parts:
        if lines:
            out.append((f"{title}\n", "head"))
            for line in lines:
                out += line + [("\n", None)]
            out.append(("\n", None))
    return out or [("Sin cambios.", "note")]


def _version_changes(prev, cur):
    """Resumen de una versión respecto a la anterior: «2 datos · 3 filas · 1 edición de texto»."""
    if prev is None:
        return "Primera versión"
    old, new = prev.get("edits", []), cur.get("edits", [])
    common = next((n for n, (x, y) in enumerate(zip(old, new)) if x != y), min(len(old), len(new)))
    counts = ((len(_data_changes(prev, cur)), "dato", "datos"), (len(_row_changes(prev, cur)), "fila", "filas"),
              (len(new) - common, "edición de texto", "ediciones de texto"))
    return " · ".join(convert.counted(n, one, many) for n, one, many in counts if n) or "Sin cambios"


def _date_text(iso):
    try:
        return datetime.fromisoformat(iso).strftime("%d/%m/%Y %H:%M")
    except (TypeError, ValueError):
        return str(iso)


def _inherited(pdf, series):
    """Datos de la serie (INHERITED) de otros tomos de la misma serie en la misma carpeta: cada uno, del más reciente
    que lo tiene (Making Magic: la revisión del EPUB original, sin glosario y más reciente que la del traducido, dejaba
    el tomo 2 sin glosario). Sin ninguno ahí, lo guardado al revisar otro tomo (_remember_series). {} si no hay."""
    found = []
    for path in Path(pdf).parent.glob("*.lnepub.json") if series else ():
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            if path != _review_path(pdf) and str(state.get("series", "")).strip().lower() == series.strip().lower():
                found.append((path.stat().st_mtime, path.name, state))
        except (OSError, ValueError, AttributeError):
            continue
    if not found:  # tomo en otra carpeta
        state = _series_store().get(series.strip().lower(), {}) if series else {}
        return {name: state[name] for name in INHERITED if name in state}
    found.sort(key=lambda f: f[:2], reverse=True)
    out = {}
    for name in INHERITED:
        have = [state[name] for _, _, state in found if name in state]
        if have:
            out[name] = next((v for v in have if v not in ("", [], {})), have[0])
    return out


def _series_path():
    return SETTINGS.with_suffix(".series.json")  # junto a las preferencias: vale para cualquier carpeta


def _series_store():
    try:
        store = json.loads(_series_path().read_text(encoding="utf-8"))
        return store if isinstance(store, dict) else {}
    except (OSError, ValueError):
        return {}


def _remember_series(state):
    """Guarda lo que comparten los tomos (INHERITED) con el nombre de la serie, para heredarlo aunque
    el siguiente tomo esté en otra carpeta."""
    series = str(state.get("series", "")).strip().lower()
    if not series:
        return
    store = _series_store()
    store[series] = {name: state[name] for name in INHERITED if name in state}
    try:
        _series_path().parent.mkdir(parents=True, exist_ok=True)
        _write_json(_series_path(), store)
    except OSError:
        pass  # sin esto solo se hereda en la misma carpeta, como antes


def _replay(book, state):
    """Vuelve a aplicar al libro recién analizado los cambios de bloques de una revisión, en su orden
    (cada uno cuenta los bloques tal como los dejó el anterior): textos editados [inicio, fin, texto],
    secciones movidas ["move", inicio, fin, destino], ilustraciones cambiadas ["image", bloque, archivo] y
    cabeceras de capítulo editadas ["head", bloque, archivo, antes, después]. Devuelve los aplicados."""
    done = []
    for edit in state.get("edits", []):
        try:
            if edit[0] == "image":
                k, path = int(edit[1]), str(edit[2])
                if not (0 <= k < len(book.blocks) and book.blocks[k].kind in ("image", "heading")):  # o un título dibujado
                    break
                try:
                    if _stored_image(path):  # otra ruta (de red, de una revisión ajena) no se abre
                        _swap_image(book, k, convert.load_cover(path))
                except ValueError:
                    pass  # la imagen ya no está: se queda la del PDF, y el cambio se recuerda por si vuelve
                done.append(["image", k, path])
                continue
            if edit[0] == "head":
                k, shell = int(edit[1]), [str(part) for part in edit[2:5]]
                if not 0 <= k < len(book.blocks) or len(shell) != 3:
                    break
                convert.set_shell(book, k, shell)
                done.append(["head", k, *shell])
                continue
            if edit[0] == "move":
                start, end, dest = int(edit[1]), int(edit[2]), int(edit[3])
                if not (0 <= start < end <= len(book.blocks) and 0 <= dest <= len(book.blocks)) or start <= dest < end:
                    break
                convert.move_section(book, start, end, dest)
                done.append(["move", start, end, dest])
                continue
            start, end, text = int(edit[0]), int(edit[1]), str(edit[2])
            if not 0 <= start <= end <= len(book.blocks):  # 0: página propia o arreglo al principio del libro
                break
        except (TypeError, ValueError, IndexError):
            break  # el .json se puede editar a mano
        convert.edit_section(book, start, end, text, _images())
        done.append([start, end, text])
    return done


def _plan(book, rows):
    """Capítulos, secciones quitadas y títulos que van como separador según los tipos de la lista
    ({fila: (tipo, título)}); marca también qué ilustraciones entran y cómo."""
    chapters, removed, breaks = [], [], []
    for item, (kind, title, *_) in rows.items():  # el tercer valor, si está, es la marca de revisado
        k = int(item)
        if not 0 <= k < len(book.blocks):  # fila de una edición que _replay ya no pudo aplicar
            continue
        if book.blocks[k].kind == "break":  # ya es un separador: su fila solo lo muestra
            continue
        if book.blocks[k].kind == "image":
            book.blocks[k].keep = kind in (IMG, SPLIT, ROTATED)
            book.blocks[k].layout = LAYOUT.get(kind, "")
        elif kind == ON:
            chapters.append((k, _title_text(title)))  # sin «<b>» de revisiones de antes
        elif kind == CUT:
            removed.append(k)
        elif kind == SEP:
            breaks.append(k)
    return chapters, removed, breaks


def _cover_image(doc, choice, book=None):
    """Portada elegida: ("page", n) es una página del PDF (desde 0); ("file", ruta), una imagen;
    ("image", k), la ilustración del bloque k; ("text", ""), la dibujada con los datos del libro."""
    kind, value = choice
    if kind == "text":
        return convert.text_cover(book)
    if kind == "page":
        return convert._render(doc[value], crop=True)
    if kind == "image":
        return book.blocks[int(value)].image
    if _network_path(value):
        raise convert.BookError("La imagen está en una carpeta de red: cópiala al equipo y elígela desde ahí.")
    return convert.load_cover(value)


def _parse_replacements(text):
    """Una regla por línea, "buscar -> reemplazo"; las líneas sin flecha no cuentan."""
    rules = []
    for line in text.splitlines():
        if "->" in line:
            find, new = (s.strip() for s in line.split("->", 1))
            if find:
                rules.append([find, new])
    return rules


def _all_caps(titles):
    """Los títulos vienen en MAYÚSCULAS (Re Zero 1): al menos 4 de cada 5."""
    titles = [t for t in titles if any(c.isalpha() for c in t)]
    return bool(titles) and sum(t.isupper() for t in titles) >= 0.8 * len(titles)


def _prepare(pdf, options):
    """Un PDF listo para escribir sin pasar por la ventana: con su revisión guardada si la hay y, si no,
    con lo detectado y los datos heredados de otro tomo de la serie (options: las opciones de la ventana
    para los PDF sin revisión). Devuelve (book, chapters, removed, state)."""
    book = convert.analyze(pdf)
    _fit_review(pdf, book)
    state = _review_for(pdf, book)
    if state:
        _replay(book, state)
        chapters, removed, breaks = _plan(book, state["rows"])
    else:
        state = {**options, **(EPUB_DEFAULTS if book.origin else {}), **_inherited(pdf, book.series)}
        state["author"] = book.author or state.get("author", "")
        chapters = [(k, _block_title(book.blocks[k])) for k in book.candidates]
        removed, breaks = convert.link_sections(book), []
        for k in convert.duplicate_images(book):
            book.blocks[k].keep = False
        heads = [k for k, b in enumerate(book.blocks) if b.kind == "heading"]
        if state.get("autocase") and _all_caps([convert._plain(book.blocks[k]) for k in heads]):
            words = convert.body_words(book)
            chapters = [(k, convert.spanish_case(t, words)) for k, t in chapters]
            for k in heads:
                book.blocks[k].html = html.escape(convert.spanish_case(convert._plain(book.blocks[k]), words))
    for name in BOOK_FIELDS:
        if name in state:
            setattr(book, name, str(state[name]).strip())
    if state.get("cover"):
        try:
            with convert.open_pdf(pdf) as doc:
                book.cover = _cover_image(doc, state["cover"], book)
        except Exception:
            pass  # la imagen de portada ya no existe: se queda la del PDF
    state["_breaks"] = breaks
    return book, chapters, removed, state


def _batch_partials(pdfs):
    """Los EPUB a medio escribir que _batch puede dejar junto a cada PDF (normal o KEPUB, según su revisión)."""
    return [convert.partial_path(_epub_name(_out(pdf), kepub)) for pdf in pdfs for kepub in (False, True)]


def _batch(pdfs, options, progress=None):
    """Convierte varios PDF, cada uno junto a su EPUB (ver _prepare). Devuelve [(nombre del PDF,
    error o "", problemas del EPUB según check_epub)]."""
    results = []
    for n, pdf in enumerate(pdfs):
        try:
            book, chapters, removed, state = _prepare(pdf, options)
            written = _write_options(state, state["_breaks"])
            problems = _write_checked(book, chapters, _epub_name(_out(pdf), written["kepub"]),
                                      removed, written)
            results.append((Path(pdf).name, "", problems))
        except Exception as e:
            results.append((Path(pdf).name, str(e) or type(e).__name__, []))
        if progress:
            progress(n + 1, len(pdfs))
    return results


ANILIST = "https://graphql.anilist.co"  # API pública, sin clave
ROLES = {"MAIN": "Principal", "SUPPORTING": "Secundario", "BACKGROUND": "De fondo"}  # papeles de AniList
ANILIST_QUERY = """query ($q: String) { Page(perPage: 6) { media(search: $q, type: MANGA, format: NOVEL) {
  title { romaji english native } startDate { year } genres description(asHtml: false)
  staff(perPage: 10) { edges { role node { name { full native } } } }
  characters(sort: [ROLE, RELEVANCE], perPage: 25) { edges { role node { name { full } } } } } } }"""
GENRES = {"Action": "Acción", "Adventure": "Aventura", "Comedy": "Comedia", "Drama": "Drama", "Ecchi": "Ecchi",
          "Fantasy": "Fantasía", "Horror": "Terror", "Mahou Shoujo": "Mahō shōjo", "Mecha": "Mecha",
          "Music": "Música", "Mystery": "Misterio", "Psychological": "Psicológico", "Romance": "Romance",
          "Sci-Fi": "Ciencia ficción", "Slice of Life": "Recuentos de la vida", "Sports": "Deportes",
          "Supernatural": "Sobrenatural", "Thriller": "Suspense"}  # los géneros de AniList, en español


def _lookup(series):
    """Busca la serie entre las novelas ligeras de AniList (_anilist_results). En el proceso de trabajo:
    la red puede tardar y la ventana no debe quedarse colgada."""
    body = json.dumps({"query": ANILIST_QUERY, "variables": {"q": series}}).encode()
    request = urllib.request.Request(ANILIST, body, {"Content-Type": "application/json", "Accept": "application/json",
                                                     "User-Agent": "PDF a EPUB"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            data = json.load(response)
    except (OSError, ValueError) as e:
        raise convert.BookError(f"No se pudo consultar AniList: {e}") from e
    return _anilist_results(data)


def _anilist_results(data):
    """Respuesta de AniList -> [{name, author, illustrator, original_title, subjects, description, characters}],
    characters: [(nombre, papel)] con papel una clave de ROLES, los principales primero.
    La sinopsis de AniList está en inglés; se le quitan las etiquetas y la nota "(Source: …)"."""
    found = []
    for m in (((data.get("data") or {}).get("Page") or {}).get("media") or []):
        title = m.get("title") or {}
        staff = [(edge.get("role") or "", ((edge.get("node") or {}).get("name") or {}).get("full") or "",
                  ((edge.get("node") or {}).get("name") or {}).get("native") or "")
                 for edge in (m.get("staff") or {}).get("edges") or []]
        author, author_native = next(((n, j) for role, n, j in staff if re.search(r"story|original", role, re.I)),
                                     ("", ""))
        illustrator, illustrator_native = next(((n, j) for role, n, j in staff
                                                if re.search(r"\b(art|illustration)\b", role, re.I)
                                                and not re.search(r"story", role, re.I)), ("", ""))
        name = title.get("romaji") or title.get("english") or ""
        year = (m.get("startDate") or {}).get("year")
        english = title.get("english") or ""
        text = re.sub(r"<br\s*/?>", "\n", m.get("description") or "", flags=re.I)
        text = html.unescape(re.sub(r"<[^>]+>", "", text))
        text = re.sub(r"\(Source:[^)]*\)\s*$", "", text.strip()).strip()
        found.append({"name": name + (f" ({year})" if year else "") + (f" · {english}" if english and english != name else ""),
                      "author": author, "illustrator": illustrator, "original_title": title.get("native") or "",
                      "author_native": author_native, "illustrator_native": illustrator_native,
                      "romaji_title": title.get("romaji") or "",
                      "subjects": ", ".join(GENRES.get(g, g) for g in m.get("genres") or []),
                      "description": re.sub(r"\n{3,}", "\n\n", text),
                      "characters": [(edge["node"]["name"]["full"], edge.get("role") or "")
                                     for edge in (m.get("characters") or {}).get("edges") or []
                                     if ((edge.get("node") or {}).get("name") or {}).get("full")]})
    return found


WATCH_MS = 10_000  # cada cuánto se mira la carpeta vigilada


def _ready(folder, sizes, skip):
    """PDF de la carpeta vigilada listos para convertir: sin EPUB, fuera de skip y con el mismo tamaño
    que en la vuelta anterior (si cambia, aún se está copiando o descargando).
    Devuelve (listos, {pdf: tamaño} para la vuelta siguiente)."""
    ready, now = [], {}
    for pdf in _sources(folder):
        if str(pdf) in skip or _has_epub(pdf):
            continue
        try:
            now[str(pdf)] = size = pdf.stat().st_size
        except OSError:
            continue
        if size and sizes.get(str(pdf)) == size:
            ready.append(str(pdf))
    return ready, now


def _omnibus(pdfs, out, options, progress=None):
    """Recopilatorio: los tomos, en el orden dado, en un solo EPUB (convert.combine). Las opciones
    son las de la ventana; los datos del libro, los del primer tomo. Devuelve los problemas del EPUB."""
    parts = []
    for n, pdf in enumerate(pdfs):
        book, chapters, removed, state = _prepare(pdf, options)
        parts.append((book, chapters, removed, state["_breaks"]))
        if progress:
            progress(n + 1, len(pdfs) + 1)
    first, last = parts[0][0], parts[-1][0]
    title = (f"{first.series} - Volúmenes {first.volume}-{last.volume}" if first.series and first.volume and last.volume
             else f"{first.title} (recopilatorio)")
    book, chapters, removed, breaks = convert.combine(parts, title)
    problems = _write_checked(book, chapters, out, removed, _write_options(options, breaks))
    if progress:
        progress(len(pdfs) + 1, len(pdfs) + 1)
    return problems


def _lan_ip():
    """IP de este equipo en la red local: la de la ruta por defecto. Conectar un socket UDP no envía nada."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


AUTOSAVE_MS = 2000  # la revisión se guarda sola este tiempo después del último cambio
DRAFT_MS = 5000  # el editor escribe su borrador este tiempo después del primer cambio sin guardar
SEND_MINUTES = 10  # «Enviar al móvil» deja de enviar solo, por si la ventana se queda abierta


def _serve(path, host):
    """Sirve el EPUB por HTTP en un puerto libre, desde un hilo: solo lee un archivo, no usa PyMuPDF. Solo en
    server.path, con una clave al azar: cualquiera en la misma Wi-Fi (un café, una residencia) podía bajarlo
    probando puertos. server.shutdown() lo para."""
    data, name = Path(path).read_bytes(), Path(path).name

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != self.server.path:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/epub+zip")
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{urllib.parse.quote(name)}")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)  # si el móvil corta a mitad, falla aquí y no cuenta como enviado
            self.server.downloaded = True  # el panel de serie lo cuenta como enviado al cerrar la ventana

        def log_message(self, *args):
            pass  # el .exe no tiene consola: escribir en stderr (None) fallaría

    server = http.server.ThreadingHTTPServer((host, 0), Handler)
    server.path, server.downloaded = f"/{secrets.token_urlsafe(8)}/{urllib.parse.quote(name)}", False
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _calibre():
    """calibre.exe si está instalado: en el PATH o en su carpeta de siempre."""
    found = shutil.which("calibre")
    for var in ("ProgramFiles", "ProgramFiles(x86)"):
        if not found and os.environ.get(var) and (Path(os.environ[var]) / "Calibre2" / "calibre.exe").is_file():
            found = str(Path(os.environ[var]) / "Calibre2" / "calibre.exe")
    return found


def _dropped_pdf(mime):
    """El primer .pdf o .epub de lo que se suelta sobre la ventana; None si no hay ninguno."""
    for url in mime.urls() if mime.hasUrls() else []:
        path = url.toLocalFile()
        if path.lower().endswith((".pdf", ".epub")) and Path(path).is_file():
            return path
    return None


def _sent_path():
    return SETTINGS.with_suffix(".envios.json")  # junto a las preferencias: vale para cualquier carpeta


def _sent_key(epub):
    return os.path.normcase(os.path.abspath(epub))


def _record_send(epub, how):
    """Apunta el último envío del EPUB al lector, para el panel de serie. Si falla no se avisa: el envío ya
    está hecho y el registro solo informa."""
    try:
        path = _sent_path()
        sent = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        sent[_sent_key(epub)] = {"how": how, "when": f"{datetime.now():%Y-%m-%d}"}
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(path, sent)
    except (OSError, ValueError, AttributeError, TypeError):
        pass


def _sent_text(epub):
    """«USB · 07/11»: cómo y cuándo se envió el EPUB por última vez, o «—»."""
    try:
        entry = json.loads(_sent_path().read_text(encoding="utf-8")).get(_sent_key(epub))
        _, month, day = entry["when"].split("-")
        return f"{entry['how']} · {day}/{month}"
    except (OSError, ValueError, AttributeError, KeyError, TypeError):
        return "—"


def _missing_volumes(paths):
    """«Faltan los tomos 2 y 5»: los tomos enteros que faltan entre el primero y el último de cada serie de la
    carpeta. Con varias series, una línea por serie con su nombre."""
    series = {}
    for path in paths:
        name, volume = convert._series_volume(Path(path).stem, "")
        if volume and float(volume).is_integer():
            series.setdefault(name.casefold(), (name, set()))[1].add(int(float(volume)))
    lines = []
    for name, numbers in series.values():
        gaps = [str(n) for n in range(min(numbers), max(numbers)) if n not in numbers]
        if gaps:
            listed = gaps[0] if len(gaps) == 1 else ", ".join(gaps[:-1]) + " y " + gaps[-1]
            lines.append((name, ("falta el tomo " if len(gaps) == 1 else "faltan los tomos ") + listed))
    if len(lines) == 1:
        return lines[0][1][0].upper() + lines[0][1][1:]
    return "\n".join(f"{name}: {text}" for name, text in lines)


def _volume_epub(pdf):
    """El EPUB de un tomo: el normal o el de Kobo, el que exista."""
    epub = Path(_epub_name(_out(pdf), False))
    return epub if epub.exists() else Path(_epub_name(_out(pdf), True))


def _volume_status(pdf):
    """Estado de un tomo: «EPUB generado», «EPUB anterior a la revisión», «Revisado, sin EPUB» o «Sin revisar»."""
    pdf = Path(pdf)
    state, epub = _read_review(pdf), _volume_epub(pdf)
    if epub.exists() and (not state or epub.stat().st_mtime >= _review_path(pdf).stat().st_mtime - 5):
        return "EPUB generado"
    if epub.exists():
        return "EPUB anterior a la revisión"
    return "Revisado, sin EPUB" if state else "Sin revisar"


class Window(QMainWindow):
    """Ventana principal: pasa a App el cierre (puede cancelarlo) y el PDF que se suelta encima."""
    on_close = on_drop = None

    def __init__(self):
        qtk.app()  # sin QApplication, Qt aborta el proceso al crear la ventana, sin decir nada
        super().__init__()
        self.setAcceptDrops(True)
        self.caption = None

    def showEvent(self, event):
        qtk.use_caption(self)  # barra de título del tema (la de Windows 10 era negra)
        super().showEvent(event)

    def nativeEvent(self, kind, message):
        handled, result = qtk.caption_event(self, kind, message)
        return (handled, result) if handled else super().nativeEvent(kind, message)

    def closeEvent(self, event):
        if self.on_close:  # App.close decide. Su root.close() de dentro no cierra (Qt no cierra dos veces a la
            event.setAccepted(bool(self.on_close()))  # vez): la X de la barra pedía un segundo clic

    def dragEnterEvent(self, event):
        if _dropped_pdf(event.mimeData()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        path = _dropped_pdf(event.mimeData())
        if path and self.on_drop:
            event.acceptProposedAction()
            self.on_drop(path)


def _skeleton_rows(tree):
    """Barras de la lista mientras se analiza: tipo, título de largo variado, lectura y página."""
    def bars(width, height):
        step, header = tree.fontMetrics().height() + 8, tree.header()
        rects = []
        for row in range(height // step):
            y = row * step + step / 2 - 5
            for n in range(4):
                x, w = header.sectionPosition(n), header.sectionSize(n)
                if n == 1:  # el título, alineado a la izquierda y de largo distinto en cada fila
                    rects.append(QRectF(x + 8, y, (w - 16) * (0.4 + 0.12 * (row * 3 % 5)), 10))
                else:
                    rects.append(QRectF(x + w * 0.25, y, w * 0.5, 10))
        return rects
    return bars


def _skeleton_page(view):
    """Barras de una hoja de vista previa mientras se analiza: un título y párrafos de líneas."""
    def bars(width, height):
        w, h = view.size_
        left = (width - w) / 2  # la hoja va centrada arriba (Preview)
        rects, y, n = [QRectF(left + w * 0.25, h * 0.08, w * 0.5, 12)], h * 0.08 + 34, 0
        while w and y + 8 < h * 0.94:
            n += 1
            rects.append(QRectF(left + w * 0.1, y, w * 0.8 * (0.55 if n % 6 == 0 else 1), 7))
            y += 22 if n % 6 == 0 else 14
        return rects
    return bars


def _skeleton_strip(width, height):
    """Miniaturas de la tira mientras se analiza, donde irán las de verdad (Strip: márgenes 4, separación 6)."""
    thumb = int(THUMB_HEIGHT * 0.71)
    return [QRectF(4 + n * (thumb + 6), 4, thumb, THUMB_HEIGHT) for n in range(width // (thumb + 6) + 1)]


def _row(*widgets, margins=(0, 0, 0, 0), spacing=6):
    """Fila horizontal; un número en widgets es un hueco elástico con ese peso."""
    box = QHBoxLayout()
    box.setContentsMargins(*margins)
    box.setSpacing(spacing)
    for widget in widgets:
        if isinstance(widget, int):
            box.addStretch(widget)
        elif isinstance(widget, QWidget):
            box.addWidget(widget)
        else:
            box.addLayout(widget)
    return box


def _menu_button(text, menu):
    button = QToolButton(objectName="menu")  # con la flecha de las listas (QSS), no «▾» de texto
    button.setText(text)
    button.setMenu(menu)
    button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    _attach(button, menu)
    return button


def _attach(button, menu):
    """El menú sale pegado al botón y al menos de su ancho, y el botón se queda marcado (:open en el QSS): se ve
    como el botón que se despliega, no como otra ventana."""
    menu.setObjectName("drop")  # sin borde arriba: sigue al botón (QSS)
    menu.aboutToShow.connect(lambda: menu.setMinimumWidth(button.width()))
    # Un menú más ancho que su botón lleva su borde de arriba solo donde el botón ya no lo tapa: a la derecha y, si
    # no cabe en la pantalla y se corre a la izquierda, también a la izquierda del botón
    seam, left = QFrame(menu, objectName="seam"), QFrame(menu, objectName="seam")

    def place(event):
        if event.type() in (QEvent.Type.Show, QEvent.Type.Resize):
            # Sin sitio debajo (barra de abajo en pantalla completa) se abre encima: la unión va arriba (up)
            up = menu.y() < button.mapToGlobal(QPoint(0, 0)).y()
            if menu.property("up") != up:
                for widget in (menu, button):
                    widget.setProperty("up", up)
                    widget.style().unpolish(widget)
                    widget.style().polish(widget)
            dx, y = button.mapToGlobal(QPoint(0, 0)).x() - menu.x(), menu.height() - 1 if up else 0
            seam.setGeometry(dx + button.width() - 1, y, max(0, menu.width() - dx - button.width() + 1), 1)
            left.setGeometry(0, y, max(0, dx), 1)
        return False

    editor.watch(menu, place)
    # El clic en el botón que cierra el menú no lo vuelve a abrir: tras cerrar otra lista con Esc, Qt repetía ese
    # clic sobre el botón, ya en otra vuelta de eventos (de ahí el plazo y no un aviso inmediato)
    closed = [0.0]
    menu.aboutToHide.connect(lambda: closed.__setitem__(0, time.monotonic()))
    editor.watch(button, lambda event: event.type() == QEvent.Type.MouseButtonPress
                 and time.monotonic() - closed[0] < 0.25)


def _combo(var, values, width, other=None, changed=None):
    """Desplegable de var. other: el último elemento («Otro…»), que pide uno propio con other(el de antes)."""
    box = QComboBox()
    box.addItems(values + (["Otro…"] if other else []))
    box.setMinimumContentsLength(width)
    box.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    var.combo(box, own=bool(other))
    if other:
        qtk.ask_other(box, other)
    if changed:
        box.activated.connect(lambda _: changed())
    return box


class StatusLabel(QLabel):
    """Línea de estado: si no cabe acaba en «…», y el texto entero sale al pasar el ratón. text() sigue
    siendo el texto entero."""

    def setText(self, text):
        super().setText(text)
        self.setToolTip(text)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, self.width()))


MARKUP = re.compile(r"</?(?:i|b|sup|sc)>")  # cursiva y negrita de section_text: en la lista no se leen las etiquetas
LANG_ROLE = Qt.ItemDataRole.UserRole + 7  # («EN → ES», otro destino que el elegido) de una fila de «Traducir»
WARN_ROLE = Qt.ItemDataRole.UserRole + 8  # avisos de español de la traducción de una fila de «Traducir»
DONE_STATES = ("Traducido", "Revisado", "Aplicado")  # estados de «Traducir» que junta el filtro «Traducidos»


def lang_code(code):
    """«EN», «ES»…; «auto» queda como «Auto»: no se sabe qué idioma detectó el motor."""
    return "Auto" if code == "auto" else code.upper()


def _two_lines(text, font, width):
    """Las dos primeras líneas de text a ese ancho; la segunda acaba en «…» si queda más."""
    layout = QTextLayout(text, font)
    layout.beginLayout()
    line = layout.createLine()
    if not line.isValid():
        layout.endLayout()
        return ["", ""]
    line.setLineWidth(width)
    end = line.textStart() + line.textLength()
    layout.endLayout()
    return [text[:end].rstrip(), QFontMetrics(font).elidedText(text[end:].strip(), Qt.TextElideMode.ElideRight, width)]


class _Cards(QStyledItemDelegate):
    """Fila de «Traducir» como tarjeta: el original arriba y su traducción debajo, dos líneas cada uno, sin las
    etiquetas <i> y <b>; en la columna del estado, un punto de su color. Con columnas de una línea, un párrafo
    largo y su traducción se leían como un solo bloque."""
    PAD = 7

    def __init__(self, tree, colors):
        super().__init__(tree)
        self.colors = colors  # () -> (colores del tema, de los estados): cambian al cambiar de tema

    def sizeHint(self, option, index):
        return QSize(super().sizeHint(option, index).width(),
                     4 * QFontMetrics(option.font).lineSpacing() + 3 * self.PAD)

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        opt.widget.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        tones, states = self.colors()
        item = self.parent().itemFromIndex(index)
        step = QFontMetrics(opt.font).lineSpacing()
        box = option.rect.adjusted(self.PAD + 4, self.PAD, -self.PAD, -self.PAD)
        painter.save()
        painter.setPen(QColor(tones["line"]))
        painter.drawLine(option.rect.bottomLeft(), option.rect.bottomRight())
        if index.column() == 0:
            original, translation = item.text(0), item.text(1)
            head = QFont(opt.font)
            head.setBold(original.startswith("# "))
            original = MARKUP.sub("", convert.split_prefix(original)[1])
            hint = QFont(opt.font)
            hint.setItalic(True)
            blocks = [(original, head, tones["muted"]),
                      (MARKUP.sub("", translation), head, tones["ink"]) if translation
                      else ("Sin traducir", hint, tones["hint"])]
            parts = [(font, color, [p for p in _two_lines(text, font, box.width()) if p]) for text, font, color in blocks]
            # Los dos juntos y en el centro de la fila: un original de una línea no deja hueco antes de su traducción
            y = option.rect.top() + (option.rect.height() - step * sum(len(p) for *_, p in parts) - self.PAD // 2) // 2
            for font, color, lines in parts:
                painter.setFont(font)
                painter.setPen(QColor(color))
                for part in lines:
                    painter.drawText(QRect(box.left(), y, box.width(), step),
                                     Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, part)
                    y += step
                y += self.PAD // 2
        else:
            state = item.text(index.column())
            color = QColor(states.get(state, tones["muted"]))
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setBrush(color)
            painter.setPen(Qt.PenStyle.NoPen)
            pair, warned = index.data(LANG_ROLE), index.data(WARN_ROLE)
            middle = option.rect.center().y() - (bool(pair) + bool(warned)) * step // 2  # con sus líneas debajo
            painter.drawEllipse(QRectF(box.left(), middle - 4, 8, 8))
            painter.setPen(color)
            painter.drawText(QRect(box.left() + 14, middle - step // 2, box.width() - 14, step),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, state)
            below = middle + step // 2
            if pair:  # «EN → JA» en color de aviso si no es el destino elegido: traducido a otro idioma por error
                text, wrong = pair
                font = QFont(opt.font)
                font.setBold(wrong)
                painter.setFont(font)
                painter.setPen(QColor(tones["warn"] if wrong else tones["muted"]))
                painter.drawText(QRect(box.left() + 14, below, box.width() - 14, step),
                                 Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
                below += step
            if warned:  # erratas y avisos de calidad de la traducción
                painter.setFont(opt.font)
                painter.setPen(QColor(tones["warn"]))
                painter.drawText(QRect(box.left() + 14, below, box.width() - 14, step),
                                 Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                                 "▲ " + convert.counted(warned, "aviso"))
        painter.restore()


def _label(text="", name=None):
    label = QLabel(text)
    if name:
        label.setObjectName(name)
    return label


def _fold_icon(dark, up):
    """La flecha ▾ (▴ con up) de las listas como icono de 20x12 con 8 px vacíos a la izquierda: el hueco con el
    texto del botón, que Qt deja en 4 px y la pegaba."""
    pixmap = QPixmap(40, 24)  # al doble, como el PNG: nítida con zoom
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.drawPixmap(16, 0, QPixmap(theme.chevron(theme.colors(dark)["muted"], up=up)))
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


def _glyph_icon(glyph, color, size=GLYPH):
    """Glifo de theme.ICONS dibujado como icono monocromo de size px, con el color del texto del tema. Se escala
    a la caja: a tamaño de letra, «±» salía diminuto y «▲» enorme al lado."""
    ratio = QApplication.primaryScreen().devicePixelRatio()
    pixmap = QPixmap(round(size * ratio), round(size * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    path = QPainterPath()
    path.addText(0, 0, QFont("Segoe UI", 40), glyph)
    box = path.boundingRect()
    if box.isEmpty():  # sin fuentes (pruebas sin pantalla): icono vacío
        return QIcon(pixmap)
    scale = min(size * 0.85 / box.width(), size * 0.8 / box.height())
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.translate(size / 2, size / 2)
    painter.scale(scale, scale)
    painter.translate(-box.center())
    painter.fillPath(path, QColor(color))
    painter.end()
    return QIcon(pixmap)


def _button(text, command, name=None):
    button = QPushButton(text)
    button.clicked.connect(lambda: command())
    if name:
        button.setObjectName(name)
    return button


class App:
    def __init__(self, root):
        self.root, self.book, self.pdf = root, None, None
        self.events = multiprocessing.Queue()  # el proceso de trabajo envía aquí progreso y resultado
        self.done = None                       # qué hacer con el resultado del trabajo en curso
        self.latest = None                     # último progreso (hecho, total) pendiente de pintar
        self.partial = None                    # archivos a medio escribir del trabajo en curso (ver run)
        self.args_file = ""                    # datos del trabajo en curso, para el proceso (ver run)
        self.reports = {}                      # informes de calidad ya calculados (ver quality_report)
        self.editor_save = None                # guarda y cierra el cuadro de edición abierto
        self.view = None                       # última posición del scroll de la lista
        self.pdf_doc = None                    # PDF abierto para la vista previa
        self.preview_path = os.path.join(tempfile.gettempdir(), f"lnepub-vista-{os.getpid()}.epub")
        _sweep_temp()
        # Lo que se escribió en preview_path y ese EPUB ya maquetado ((ancho, alto), documento, páginas): un
        # cambio pedía la vista previa hasta 4 veces y maquetarla cada vez paraba la ventana medio segundo
        self.preview_key, self.preview_doc = None, None
        self.preview_faithful = False  # la vista previa del EPUB es una copia fiel (book.origin): sin portada de la app
        self.pdf_page = 0                      # página del PDF que muestra la vista previa (desde 0)
        self.highlight = None                  # (página, rectángulo) del título seleccionado
        self.epub_page, self.epub_pages = 0, 0  # página mostrada y total de la sección en el EPUB
        self.epub_cut = False                  # la sección seleccionada está en "Quitar"
        self.epub_message = ""                 # aviso de la vista previa del EPUB si la sección no va
        self.thumbs = {}                       # miniaturas dibujadas: página -> tamaño
        self.thumb_job = 0                     # tanda de miniaturas en curso (para cancelarla)
        self.wide = set()                      # filas de ilustraciones dobles (apaisadas)
        self.history = []                      # para deshacer: [(fila, tipo, título) de antes] o copia antes de editar texto
        self.busy = ""                         # qué dice la barra de estado mientras avanza el trabajo
        self.edits, self.analyzed = [], 0      # textos editados [(inicio, fin, texto)] y bloques del análisis
        self.find_target = None                # (página, texto) que la vista previa debe mostrar tras buscar
        self.next_page = 0                     # página de la sección del EPUB al seleccionarla (◀ desde la siguiente: la última)
        self.settings = {}                     # preferencias guardadas (SETTINGS)
        self.last_problems = None              # avisos del último EPUB generado (para el panel de serie)
        self.drag_source = None                # fila que se arrastra para moverla
        self.text_editor = None                # editor de texto abierto (TextEditor), si lo hay
        self.result_window = None              # diálogo del último EPUB generado
        self.matches = []                      # resultados de la búsqueda: (bloque, página, texto)
        self.dark = False                      # tema oscuro activo
        self.proc = None
        self.standby = None                    # proceso de trabajo ya arrancado para el siguiente (warm_worker)
        self.book_lock = self.lock_key = None  # candado del libro abierto (ver lock_book)
        self.closed = False                    # ventana cerrada: lo diferido (miniaturas, repintar) ya no se hace
        self._description = ""                 # sinopsis (propiedad description)
        self.replacements = []                 # [[buscar, reemplazo]]; se editan en su propio cuadro
        self.words = []                        # diccionario de la serie: palabras que la ortografía da por buenas
        self.glossary = []                     # glosario de la serie: los nombres como deben ir
        self.tr_glossary = []                  # glosario de traducción de la serie: [[original, traducción]]
        self.tr_notes = ""                     # personajes y estilo para la IA que traduce, de la serie
        self.comments = []                     # comentarios del revisor: {"text", "page", "note"}; no van al EPUB
        self.editor_at = None                  # bloque del cursor al cerrar el editor: «Lo dejaste en…»
        # _history_key de un capítulo aceptado en el editor -> [el de antes, para su deshacer; el del principio, para
        # «Ver todos los cambios»], como _history_delta. Va en la revisión: sigue al cerrar la aplicación
        self.editor_history = {}
        self.work = {}                         # trabajo en el editor por día: {"AAAA-MM-DD": {"minutes", …}}
        self.alts = {}                         # descripciones de ilustraciones: {convert.image_key: texto}
        # Proceso de las miniaturas de recientes y archivos ya probados (self.thumbs es la tira de páginas)
        self.recent_thumbs, self.thumbs_tried = None, set()
        self.ignored = []                      # avisos del informe que no se muestran (_issue_key)
        self.unknown = set()                   # palabras desconocidas de la última revisión ortográfica
        self.spell_server = None               # proceso de ortografía del editor (spell_process)
        self.lt_server = None                  # LanguageTool local: (proceso, dirección, registro), uno por sesión
        self.grammar_memo = (None, [])         # (lo que mira grammar_issues, sus avisos)
        # ("page", n), ("file", ruta), ("image", k), ("text", "") o None: la que eligió el análisis
        self.cover_choice = None
        self.device_var, self.dark_var = qtk.Var('E-reader 6"'), qtk.Var(False)
        self.theme_var = qtk.Var("Sistema")
        self.title_var, self.author_var, self.series_var, self.volume_var = (qtk.Var() for _ in range(4))
        self.publisher_var, self.translator_var = qtk.Var(), qtk.Var()
        # Datos de la página de créditos y del catálogo: en el panel «Editar datos»
        self.illustrator_var, self.proofreader_var, self.editor_var = qtk.Var(), qtk.Var(), qtk.Var()
        self.original_title_var, self.subjects_var, self.date_var = qtk.Var(), qtk.Var(), qtk.Var()
        for name, _ in MORE_FIELDS:  # «Más datos…»
            setattr(self, f"{name}_var", qtk.Var())
        self.title_image_var = qtk.Var("")  # image_key de la ilustración de la página de título ("" sin ilustración)
        self.synopsis_var, self.endnotes_var = qtk.Var(True), qtk.Var(False)  # página «Sinopsis»; notas al final
        self.note_style_var = qtk.Var("1")  # cómo se ve la llamada de nota (convert.NOTE_STYLES)
        self.heading_font_var = qtk.Var("La del texto")  # convert.HEADING_FONT_CHOICES o «Archivo propio»
        self.heading_font_file_var = qtk.Var("")  # ruta de la fuente propia de títulos
        self.box_style_var = qtk.Var("Sencillo")  # aspecto de los recuadros (convert.BOX_STYLES)
        self.logo_var = qtk.Var("")  # imagen del logo del grupo, en su propia página
        self.lang_var = qtk.Var("es")
        self.autocase_var = qtk.Var(False)
        self.profile_var = qtk.Var("Personalizado")  # fija gris, tamaño de imagen, EPUB 2 y KEPUB
        self.separator_var = qtk.Var("Según el estilo")  # cómo se ven los separadores de escena
        self.separator_icon_var = qtk.Var("")  # SVG del «Icono propio» (convert.trace_svg de la imagen elegida)
        self.separator_before = "Según el estilo"  # al cancelar «Icono propio» sin icono, se vuelve a este
        self.style_var = qtk.Var("Clásico")  # aspecto del EPUB: letra, párrafos e inicio de capítulo
        self.font_var = qtk.Var("Del lector")  # tipografía incrustada (convert.FONTS) o nombre de la propia
        self.font_file_var = qtk.Var("")  # ruta de la fuente propia
        self.typography_var = qtk.Var(True)  # comillas «», puntos suspensivos, raya, espacios
        self.gray_var = qtk.Var(False)  # para e-readers de tinta: pesa menos y se ve igual
        self.shrink_var = qtk.Var(True)  # las imágenes son casi todo el peso del EPUB
        self.shrink_size_var = qtk.Var(convert.SHRINK)  # lado mayor de las imágenes reducidas
        self.trim_var, self.credits_var, self.reading_var = qtk.Var(False), qtk.Var(False), qtk.Var(False)
        self.horizontal_var = qtk.Var(True)  # un EPUB japonés en vertical sale de izquierda a derecha
        self.epub2_var, self.kepub_var = qtk.Var(False), qtk.Var(False)
        self.contents_var = qtk.Var(True)  # página «Contenido» tras la portada, con capítulos y subtítulos
        self.contents_var.listeners.append(lambda: self.book is not None and self.contents_row())  # su fila
        self.echoes_var = qtk.Var(False)  # ecos en el informe: muchos avisos de estilo, solo si se piden
        self.hyphenate_var = qtk.Var(False)  # guiones blandos: el Kindle apenas parte palabras en español
        self.whole_words_var = qtk.Var(False)  # PDF maquetado sin palabras partidas al final de línea
        self.pdf_size_var = qtk.Var("A5")  # tamaño de PDF que sale primero en «PDF ▾»

        root.setWindowTitle(APP_NAME)
        root.on_close, root.on_drop = self.close, self.open_path
        if ICON.exists():
            root.setWindowIcon(QIcon(str(ICON)))
        # A la medida de la pantalla libre: en 1366×768 con la barra de tareas, «Generar EPUB» debe verse
        area = root.screen().availableGeometry()
        # (setGeometry no cuenta la barra de título: unos 32 px encima y 8 de borde debajo)
        width, height = min(1300, area.width() - 20), min(760, area.height() - 48)
        root.setGeometry(area.x() + 10, area.y() + 34, width, height)  # el tamaño al restaurarla
        root.setWindowState(Qt.WindowState.WindowMaximized)  # se abre ocupando la pantalla
        sys.excepthook = self.on_error  # un fallo en un botón o atajo se apunta y se avisa

        central = QWidget()
        root.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Barra superior
        top = QFrame(objectName="bar")
        self.open_btn = _button("Abrir PDF o EPUB…", self.open_pdf)
        self.batch_btn = _button("Convertir carpeta…", self.convert_folder)
        self.recent_menu = QMenu(root)
        tools = QMenu(root)
        # Las que trabajan sobre el libro abierto: en gris sin libro (update_chrome), no un aviso al pulsarlas
        self.book_actions = [tools.addAction(text, action) for text, action in (
            ("Buscar en el libro…  (Ctrl+F)", self.find), ("Informe de calidad…", self.quality),
            ("Ortografía…", self.spelling),
            ("Página de personajes…", self.character_page), ("Ilustraciones…", self.images_dialog),
            ("Traducir…", self.translate_book),
            ("Versiones…", self.versions))]
        self.book_actions.append(tools.addSeparator())
        tools.addAction("Panel de serie…", self.series_panel)
        tools.addAction("Volumen recopilatorio…", self.omnibus)
        tools.addAction("Vigilar una carpeta…", lambda: self.open_options("Carpeta vigilada"))
        tools.addSeparator()
        tools.addAction("Opciones…  (Ctrl+,)", lambda: self.open_options())
        tools.addAction("Atajos de teclado…", self.show_shortcuts)
        tools.addAction("Registro de la aplicación", self.open_log)
        tools.addSeparator()
        support = tools.addMenu("Apoyar el proyecto")
        for text, url in DONATE:
            support.addAction(text, lambda url=url: webbrowser.open(url))
        self.file_label = _label("Ningún archivo", "muted")
        self.recent_btn, self.find_btn = _menu_button("Recientes", self.recent_menu), _button("Buscar", self.find)
        top.setLayout(_row(self.open_btn, self.batch_btn, self.recent_btn, _menu_button("Herramientas", tools),
                           self.file_label, 1, self.find_btn, margins=(10, 8, 10, 8)))
        outer.addWidget(top)
        QShortcut(QKeySequence("Ctrl+F"), root, self.find)
        QShortcut(QKeySequence("Ctrl+,"), root, lambda: self.open_options())
        # En toda la ventana: tras «Editar texto…» o «Todos» el foco se queda en el botón, no en la lista.
        # Un campo de texto con el foco se queda Ctrl+Z para deshacer lo escrito en él.
        QShortcut(QKeySequence("Ctrl+Z"), root, self.undo)

        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)
        self.stack.addWidget(self.empty_page())
        page = QWidget()
        self.stack.addWidget(page)
        body = QVBoxLayout(page)
        body.setContentsMargins(10, 8, 10, 8)  # abajo también: la lista y la tira de páginas tocaban el borde
        body.setSpacing(8)

        # Tarjeta del libro: lo esencial a la vista; «Editar datos» despliega el resto
        card = QFrame(objectName="card")
        self.cover_thumb = QLabel()
        self.cover_thumb.setFixedSize(40, 56)
        # Recortados con «…»: un título de 2000 letras ensanchaba la ventana a 16 000 px
        self.book_title, self.book_line = StatusLabel(objectName="title"), StatusLabel(objectName="muted")
        for label in (self.book_title, self.book_line):
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.data_btn = _button("Editar datos", self.toggle_data)
        # La flecha a la derecha, la misma de «Recientes» y «Herramientas» (el «▾» de texto no se parecía)
        self.data_btn.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.data_btn.setIconSize(QSize(20, 12))  # la flecha mide 12: lo de más es el hueco con el texto (_fold_icon)
        self.data_btn.setStyleSheet("padding-right: 6px;")  # a 9 px del borde, como la de «menu» en el QSS
        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(self.book_title)
        text.addWidget(self.book_line)
        row = _row(self.cover_thumb, text, self.data_btn, margins=(10, 8, 10, 8), spacing=10)
        row.setStretchFactor(text, 1)  # el sitio para el título: con un hueco elástico, el recortado quedaba en 0 px
        card.setLayout(row)
        body.addWidget(card)
        # Con barra de desplazamiento si no cabe: la tarjeta entera subía el alto mínimo de la ventana por encima de la
        # pantalla, y la ventana crecía por debajo de la barra de tareas
        self.data_panel = QScrollArea(objectName="dataScroll")
        self.data_panel.setWidget(self.book_data())
        self.data_panel.setWidgetResizable(True)
        self.data_panel.setFrameShape(QFrame.Shape.NoFrame)
        self.data_panel.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.data_panel.hide()
        body.addWidget(self.data_panel)
        for var in (self.title_var, self.author_var, self.publisher_var, self.lang_var, self.series_var,
                    self.volume_var):
            var.listeners.append(self.update_header)
        self.lang_timer = None
        self.lang_var.listeners.append(self.lang_changed)
        self.autosave_timer = None
        for var in self.fields().values():  # los datos del libro y sus opciones van en la revisión
            var.listeners.append(self.autosave)

        # Lista a la izquierda, vistas previas a la derecha, con divisor
        split = QSplitter(Qt.Orientation.Horizontal)
        body.addWidget(split, 1)
        left = QWidget()
        column = QVBoxLayout(left)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(6)
        self.find_entry = QLineEdit(placeholderText="Buscar en el libro (Intro)")
        self.find_count = _label("", "muted")
        self.find_bar = QFrame(objectName="card")
        self.replace_entry = QLineEdit(placeholderText="Reemplazar por")
        self.replace_entry.setMinimumWidth(150)  # en ventana pequeña los botones lo dejaban sin sitio para escribir
        bar = QVBoxLayout(self.find_bar)
        bar.setContentsMargins(6, 4, 6, 4)
        bar.setSpacing(4)
        bar.addLayout(_row(self.find_entry, self.find_count, _button("✕", self.close_find)))
        bar.addLayout(_row(self.replace_entry, _button("Reemplazar", self.replace_found),
                           _button("Todos", lambda: self.replace_found(every=True)),
                           _button("Guardar como regla", self.save_rule)))
        self.find_results = QListWidget()
        self.find_results.setMaximumHeight(160)
        self.find_bar.hide()
        self.find_results.hide()
        column.addWidget(self.find_bar)
        column.addWidget(self.find_results)
        self.tree = qtk.RowList(("inc", "title", "min", "page", "ok"),
                                {"inc": "Tipo", "title": "Título", "min": "Lectura", "page": "Página", "ok": "✓"},
                                {"inc": 112, "min": 64, "page": 60, "ok": 64})  # con el margen de las flechas
        self.tree.setToolTip(SHORTCUTS)
        column.addWidget(self.tree, 1)
        split.addWidget(left)
        self.views = qtk.Box()
        split.addWidget(self.views)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        split.setSizes([470, 700])
        views = QVBoxLayout(self.views)
        views.setContentsMargins(6, 0, 0, 0)
        views.setSpacing(4)
        self.tabs = QTabBar()
        for name in ("PDF", "EPUB", "Ambos"):
            self.tabs.addTab(name)
        self.tabs.setCurrentIndex(2)
        self.tabs.currentChanged.connect(lambda _: self.show_panes())
        # otro tamaño de página: el EPUB de la vista previa se rehace con sus ilustraciones a la medida
        device = _combo(self.device_var, list(DEVICES), 10, changed=self.update_preview)
        dark = qtk.Var.check(self.dark_var, QCheckBox("Modo oscuro"))
        dark.toggled.connect(lambda _: self.book and (self.draw_pdf(), self.draw_epub()))  # las dos vistas
        views.addLayout(_row(self.tabs, 1, device, dark, _button("Editar texto…", self.edit_text)))
        self.pages = qtk.Box()
        panes = QHBoxLayout(self.pages)
        panes.setContentsMargins(0, 0, 0, 0)
        self.pdf_pane, self.pdf_caption, self.pdf_view = self.preview_pane("PDF", self.pdf_step)
        self.epub_pane, self.epub_caption, self.epub_view = self.preview_pane("EPUB", self.epub_step)
        import webview  # aquí y no arriba: los procesos de trabajo no cargan Chromium
        if webview.available():
            # Oculta y vacía: con ella, la ventana nace preparada para Chromium y Chromium ya está en marcha. Sin
            # ella, abrir «Editar texto…» la volvía a crear y parecía que se cerraba y se abría otra aplicación
            self.chromium = webview.EpubView()
            self.chromium.hide()
            self.epub_pane.layout().addWidget(self.chromium)
        panes.addWidget(self.pdf_pane)
        panes.addWidget(self.epub_pane)
        views.addWidget(self.pages, 1)
        self.strip = qtk.Strip(THUMB_HEIGHT)
        self.strip.clicked.connect(self.jump)
        views.addWidget(self.strip)
        # Mientras se analiza, lista, hojas y tira muestran su esqueleto: se ve que algo está cargando
        self.loading = False
        self.shimmers = (qtk.Shimmer(self.tree.viewport(), _skeleton_rows(self.tree)),
                         qtk.Shimmer(self.pdf_view, _skeleton_page(self.pdf_view)),
                         qtk.Shimmer(self.epub_view, _skeleton_page(self.epub_view)),
                         qtk.Shimmer(self.strip.viewport(), _skeleton_strip))

        # Barra inferior: opciones del EPUB, estado, avisos y la acción principal
        bottom = QFrame(objectName="bar")
        rows = QVBoxLayout(bottom)
        rows.setContentsMargins(10, 6, 10, 6)
        rows.setSpacing(4)
        self.quality_btn = _button("Calidad", self.quality)
        self.quality_btn.setEnabled(False)
        self.gen_btn = _button("Generar EPUB…", self.generate, "primary")
        self.pdf_menu = QMenu(root)
        self.pdf_menu.aboutToShow.connect(self.fill_pdf_menu)  # el tamaño preferido puede cambiar en Opciones
        self.fill_pdf_menu()
        self.pdf_btn = _menu_button("PDF", self.pdf_menu)
        self.pdf_btn.setToolTip("El libro maquetado en PDF, con las mismas opciones que el EPUB")
        self.gen_btn.setEnabled(False)
        self.pdf_btn.setEnabled(False)
        self.options_row = QWidget()  # perfil, aspecto y «Generar»: solo con un libro abierto (update_chrome)
        rows.addWidget(self.options_row)
        # Aquí lo que se cambia mirando la vista previa: el lector de destino y el aspecto. El resto, en
        # Herramientas → Opciones
        self.font_last = "Del lector"  # a la que se vuelve si «Otra…» no elige archivo
        self.font_box = _combo(self.font_var, FONT_CHOICES, 8, changed=self.font_changed)
        # «Original» solo con un EPUB de entrada (show_book la pone o la quita): su CSS, sin tocar
        self.style_box = _combo(self.style_var, list(convert.STYLES), 10, changed=self.update_preview)
        self.style_box.setToolTip("«Original»: el aspecto del EPUB abierto, con su CSS y sus fuentes")
        # «Otro…»: el texto que se escriba es el separador, tal cual
        self.separator_box = _combo(self.separator_var, convert.SEPARATOR_CHOICES, 12, changed=self.separator_changed,
                                    other=lambda now: _ask_line(root, "Separador", "Separador de escena (se pone tal "
                                                                "cual entre las escenas):", now))
        self.separator_box.setToolTip("Elige uno, u «Otro…» para escribir el tuyo. «Icono propio»: una imagen tuya "
                                      "(PNG o JPG) hecha icono del color del texto")
        index = qtk.Var.check(self.contents_var, QCheckBox("Índice"))
        index.setToolTip("Página «Contenido» tras la portada, con los capítulos y sus subtítulos")
        self.options_row.setLayout(_row(_label("Perfil:"), _combo(self.profile_var, list(PROFILES), 15,
                                                                 changed=self.apply_profile),
                            _label("Estilo:"), self.style_box,
                            _label("Separador:"), self.separator_box,
                            _label("Letra:"), self.font_box, index, 1, self.pdf_btn, self.gen_btn))
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedWidth(160)
        self.status = StatusLabel(objectName="muted")
        # Un estado largo se recorta en vez de ensanchar la ventana más allá de la pantalla
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.cancel_btn = _button("Cancelar", self.cancel_work)  # solo con un trabajo en curso (update_chrome)
        self.cancel_btn.setToolTip("Detiene el trabajo en curso; lo que estaba a medio escribir se borra")
        status_row = _row(self.progress, self.cancel_btn, self.status, self.quality_btn)
        status_row.setStretchFactor(self.status, 1)  # con un hueco elástico al lado, Ignored se quedaba en 0 px
        rows.addLayout(status_row)
        outer.addWidget(bottom)
        self.bottom = bottom

        self.tree.pressed.connect(self.on_click)
        self.tree.double_clicked.connect(self.on_double_click)
        self.tree.released.connect(self.on_drop)
        self.tree.selected.connect(self.update_preview)
        self.tree.verticalScrollBar().valueChanged.connect(lambda _: self.on_scroll())
        for key, kind in KEYS.items():
            self.tree.bind_key(getattr(Qt.Key, f"Key_{key.upper()}"), lambda kind=kind: self.shortcut(kind))
        self.tree.bind_key(Qt.Key.Key_F2, lambda: self.tree.selection() and self.edit_title(self.tree.selection()[0]))
        self.tree.bind_key(Qt.Key.Key_Up, lambda: self.move_selected(-1), Qt.KeyboardModifier.AltModifier)
        self.tree.bind_key(Qt.Key.Key_Down, lambda: self.move_selected(1), Qt.KeyboardModifier.AltModifier)
        self.tree.bind_key(Qt.Key.Key_E, self.edit_text)
        self.tree.bind_key(Qt.Key.Key_R, self.toggle_reviewed)
        self.find_entry.returnPressed.connect(self.search_book)
        self.find_results.currentRowChanged.connect(self.pick_found)
        QShortcut(QKeySequence("Escape"), self.find_bar, self.close_find,
                  context=Qt.ShortcutContext.WidgetWithChildrenShortcut)
        base = self.tree.font()
        self.fonts = {name: QFont(base) for name in KIND_COLOR}
        self.fonts["dropped"].setItalic(True)
        self.fonts["chapter"].setBold(True)
        self.fonts["subtitle"].setItalic(True)
        self.fonts["cut"].setStrikeOut(True)
        self.fonts["separator"].setItalic(True)
        # Marcos de tamaño fijo: solo cambian si cambia el sitio de las vistas previas, no con cada página
        self.idle_text, self.views_size = "Abre un PDF para ver la vista previa", None
        self.pages.resized.connect(self.on_resize)
        # Valores de fábrica: lo que no esté en «Libros nuevos» (o esté mal escrito) sale de aquí
        self.factory = {key: var.get() for key, var in self.fields().items() if key in options.BOOK_KEYS}
        self.load_settings()
        self.update_chrome()
        self.apply_theme()
        theme.follow(qtk.app(), self.apply_theme)
        self.poller = QTimer()
        self.poller.timeout.connect(self.poll)
        self.poller.start(100)
        qtk.after(2000, self.warm_worker)  # tras abrirse la ventana: arrancarlo no le quita el sitio
        # Carpeta vigilada de la sesión anterior: lo llegado con la ventana cerrada también se convierte
        self.watch, self.watch_sizes, self.watch_skip = "", {}, set()
        watch = self.settings.get("watch")
        if isinstance(watch, str) and watch and Path(watch).is_dir():  # Path("") sería la carpeta actual
            self.watch = watch
        self.watcher = QTimer()
        self.watcher.timeout.connect(self.poll_watch)
        self.watcher.start(WATCH_MS)

    @property
    def description(self):
        return self._description

    @description.setter
    def description(self, text):
        self._description = text
        if self.synopsis.toPlainText() != text:
            self.synopsis.blockSignals(True)
            self.synopsis.setPlainText(text)
            self.synopsis.blockSignals(False)

    def empty_page(self):
        """Sin PDF abierto: dónde soltarlo, el botón para abrirlo y los recientes con su estado."""
        page = QWidget()
        drop = QFrame(objectName="drop")
        inner = QVBoxLayout(drop)
        inner.addStretch(1)
        icon = _label("🗎")
        icon.setStyleSheet("font-size: 40pt;")
        for widget in (icon, _label("Suelta aquí un PDF o EPUB de novela ligera", "big"), _label("o", "muted"),
                       _button("Abrir PDF o EPUB…", self.open_pdf, "primary"),
                       _button("¿Muchos tomos? Convertir una carpeta entera", self.convert_folder, "link")):
            inner.addWidget(widget, 0, Qt.AlignmentFlag.AlignHCenter)
        inner.addStretch(1)
        recent = QFrame(objectName="card")
        column = QVBoxLayout(recent)
        column.addWidget(_label("Recientes", "title"))
        self.recent_list = QListWidget()
        self.recent_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)  # tarjetas altas
        self.recent_list.itemClicked.connect(lambda item: item.data(Qt.ItemDataRole.UserRole)
                                             and self.open_path(item.data(Qt.ItemDataRole.UserRole)))
        self.recent_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.recent_list.customContextMenuRequested.connect(self.recent_context)
        column.addWidget(self.recent_list, 1)
        # Sin recientes, un aviso en el medio del panel: una línea gris arriba de la lista vacía apenas se veía
        self.recent_empty = QWidget()
        hint = _label("Los PDF y EPUB que abras saldrán aquí con su portada, para volver a ellos con un clic.", "muted")
        hint.setWordWrap(True)
        hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        empty_column = QVBoxLayout(self.recent_empty)
        empty_column.addStretch(1)
        empty_column.addWidget(_label("Aún no hay libros recientes", "title"), 0, Qt.AlignmentFlag.AlignHCenter)
        empty_column.addWidget(hint)
        empty_column.addStretch(1)
        column.addWidget(self.recent_empty, 1)
        page.setLayout(_row(drop, recent, margins=(16, 16, 16, 16), spacing=16))
        page.layout().setStretch(0, 3)
        page.layout().setStretch(1, 2)
        return page

    def book_data(self):
        """Panel «Editar datos»: todos los datos del libro, la sinopsis, la portada, los reemplazos y AniList."""
        panel = QFrame(objectName="card")
        grid = QGridLayout(panel)
        grid.setContentsMargins(10, 8, 10, 8)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)
        fields = ((("Título:", self.title_var), ("Autor:", self.author_var), ("Serie:", self.series_var),
                   ("Vol.:", self.volume_var)),
                  (("Grupo:", self.publisher_var), ("Traductor:", self.translator_var),
                   ("Ilustraciones:", self.illustrator_var)),  # el idioma está en Opciones → Perfil y formato
                  (("Corrección:", self.proofreader_var), ("Edición:", self.editor_var),
                   ("Título original:", self.original_title_var), ("Fecha:", self.date_var)))
        for r, row in enumerate(fields):
            for c, (label, var) in enumerate(row):
                grid.addWidget(QLabel(label), r, c * 2)
                widget = var.entry(QLineEdit())
                if var is self.date_var:
                    widget.setPlaceholderText("AAAA o AAAA-MM-DD")
                grid.addWidget(widget, r, c * 2 + 1)
        grid.addWidget(QLabel("Géneros:"), 3, 0)
        subjects = self.subjects_var.entry(QLineEdit(placeholderText="separados por comas"))
        grid.addWidget(subjects, 3, 1, 1, 7)
        grid.addWidget(QLabel("Sinopsis:"), 4, 0, Qt.AlignmentFlag.AlignTop)
        self.synopsis = QPlainTextEdit()
        self.synopsis.setFixedHeight(62)
        self.synopsis.textChanged.connect(lambda: setattr(self, "_description", self.synopsis.toPlainText().strip()))
        grid.addWidget(self.synopsis, 4, 1, 1, 7)
        titles = QMenu(self.root)
        titles.addAction("Mayúsculas al estilo español", self.fix_case)
        titles.addAction("Numeración uniforme (Capítulo 1, 2…)", self.fix_numbers)
        titles.addAction("Apartados sin nombre (Parte 1…) como separador", self.parts_as_breaks)
        self.cover_label = _label("", "muted")
        grid.addLayout(_row(_button("Portada…", self.choose_cover), self.cover_label,
                            _button("Reemplazos…", self.edit_replacements),
                            _button("Glosario…", self.edit_glossary), _menu_button("Títulos", titles),
                            _button("Más datos…", self.edit_more_data), 1,
                            _button("Buscar en internet (AniList)…", self.search_online)), 5, 0, 1, 8)
        for c in (1, 3, 5):
            grid.setColumnStretch(c, 2)
        grid.setColumnStretch(7, 1)
        return panel

    def toggle_data(self):
        shown = not self.data_panel.isVisible()
        if shown:  # entera si cabe (la lista y las vistas previas se encogen); con barra solo en pantallas muy bajas.
            # Medida al abrirla: al crear la ventana el tema aún no estaba puesto y salía más baja
            full = self.data_panel.widget().sizeHint().height() + 12  # holgura: con la barra, el texto se parte más
            room = self.root.screen().availableGeometry().height() if self.root.screen() else full
            self.data_panel.setMaximumHeight(full)
            self.data_panel.setMinimumHeight(min(full, int(room * 0.45)))
        self.data_panel.setVisible(shown)
        self.data_btn.setText("Ocultar datos" if shown else "Editar datos")
        self.data_btn.setIcon(_fold_icon(self.dark, shown))

    def update_header(self):
        """Tarjeta del libro: título, y autor · grupo · serie y tomo · idioma."""
        title = self.title_var.get().strip() or (self.pdf.stem if self.pdf else "")
        self.book_title.setText(title)
        series = self.series_var.get().strip()
        if series and self.volume_var.get().strip():
            series += f" · vol. {self.volume_var.get().strip()}"
        lang = self.lang_var.get().strip()
        parts = [self.author_var.get().strip(), self.publisher_var.get().strip(), series,
                 translate.LANGS.get(lang, lang)]  # «Español», no «es»
        self.book_line.setText(" · ".join(p for p in parts if p))

    def update_cover(self):
        image = QImage.fromData(self.book.cover) if self.book and self.book.cover else QImage()
        if image.isNull():
            self.cover_thumb.clear()
            return
        self.cover_thumb.setPixmap(QPixmap.fromImage(image).scaled(40, 56, Qt.AspectRatioMode.KeepAspectRatio,
                                                                   Qt.TransformationMode.SmoothTransformation))

    def apply_theme(self):
        """Pone el tema elegido y vuelve a pintar lo que tiene colores propios (lista y vistas previas)."""
        self.dark = theme.apply(qtk.app(), self.theme_var.get(), self.settings.get("accent", "Azul"),
                                theme.UI_SIZES.get(self.settings.get("ui_font"), 9))
        colors = theme.kind_colors(self.dark)
        for tag in KIND_TAG.values():
            self.tree.tag_configure(tag, font=self.fonts[tag], foreground=colors[tag])
        self.update_quality()
        self.redraw()
        if self.text_editor:
            self.text_editor.recolor(self.dark)
        if self.book is None:  # las tarjetas de recientes llevan colores del tema
            self.fill_recent()
        self.data_btn.setIcon(_fold_icon(self.dark, not self.data_panel.isHidden()))

    def show_panes(self):
        mode = self.tabs.currentIndex()  # 0 PDF, 1 EPUB, 2 los dos
        self.pdf_pane.setVisible(mode != 1)
        self.epub_pane.setVisible(mode != 0)
        self.views_size = None
        self.redraw()

    def run(self, work, args, done, report=False, busy="Analizando…", partial=()):
        """Ejecuta work(*args) en otro proceso y llama a done(resultado) en la ventana.
        Con report=True, work recibe progress=... y la barra avanza junto al texto busy. partial: archivos a medio
        escribir que se borran si el trabajo se cancela o se cierra la ventana. False si ya había un trabajo en
        curso (su resultado llegaría a este done) o si no se pudo empezar."""
        if self.done:
            self.status.setText("Espera a que termine el trabajo en curso")  # el menú no se desactiva como los botones
            return False
        self.open_btn.setEnabled(False)
        self.batch_btn.setEnabled(False)
        self.gen_btn.setEnabled(False)
        self.pdf_btn.setEnabled(False)
        self.done, self.busy, self.partial = done, busy, list(partial)
        self.update_chrome()  # sin libro, la barra de abajo aparece para el progreso
        # Los datos van en un archivo: por la tubería del proceso nuevo, la ventana esperaba a que arrancara
        # para leerlos (unos 3 s con un libro con ilustraciones). Así start() vuelve enseguida.
        try:
            fd, self.args_file = tempfile.mkstemp(prefix="lnepub-trabajo-", suffix=".pickle")
            with os.fdopen(fd, "wb") as f:
                pickle.dump(args, f, protocol=pickle.HIGHEST_PROTOCOL)
            standby, self.standby = self.standby, None
            if standby and standby[0].is_alive():  # el preparado: ya importado, empieza enseguida
                self.proc, self.events, jobs = standby
                jobs.put((work, self.args_file, report))
            else:
                self.proc = multiprocessing.Process(target=_worker, args=(self.events, work, self.args_file, report),
                                                    daemon=True)
                self.proc.start()
            self.work_log = (getattr(work, "__name__", str(work)), time.perf_counter())
            LOG.info("Trabajo %s: empieza (proceso %s) · %s", self.work_log[0], self.proc.pid, busy)
        except Exception as e:  # disco lleno, sin permisos en %TEMP%: sin esto la ventana se quedaba ocupada
            _log(traceback.format_exc())
            self.proc = None
            self.finish(("error", f"No se pudo empezar el trabajo: {e or type(e).__name__}"))
            return False
        return True

    def warm_worker(self):
        """Deja un proceso de trabajo arrancado y esperando (_standby), para que el siguiente trabajo no espere a que
        arranque. Uno solo, y no mientras trabaja otro (le quitaría CPU)."""
        if not STANDBY or self.closed or self.done or (self.standby and self.standby[0].is_alive()):
            return
        self.standby = _start_standby()

    def stop_work(self):
        """Detiene el proceso de trabajo y borra lo que dejó a medias: el EPUB o PDF y el archivo de sus datos."""
        if self.proc is not None and self.proc.is_alive():
            self.proc.terminate()
            self.proc.join()
        for path in self.partial or ():
            Path(path).unlink(missing_ok=True)
        if self.args_file:
            with contextlib.suppress(OSError):  # el proceso lo borra al leerlo
                os.remove(self.args_file)

    def cancel_work(self):
        """«Cancelar», junto a la barra de progreso."""
        if not self.done:
            return
        self.stop_work()
        self.events = multiprocessing.Queue()  # la del proceso detenido puede quedar a medias o con su último aviso
        self.finish(("cancel",))

    def update_chrome(self):
        """Sin libro abierto, solo lo que sirve para empezar: abrir, convertir una carpeta, las herramientas que no
        piden libro y los recientes de la pantalla de inicio. Las opciones del EPUB, Generar, Calidad, Buscar y el nombre del
        archivo llegan con el libro; la barra de abajo también sale mientras se trabaja, por el progreso."""
        has = self.book is not None
        for widget in (self.options_row, self.quality_btn, self.recent_btn, self.file_label, self.find_btn):
            widget.setVisible(has)
        self.bottom.setVisible(has or self.done is not None)
        self.cancel_btn.setVisible(self.done is not None)
        loading = not has and self.done is not None and self.stack.currentIndex() == 1  # analizando un libro
        if loading != self.loading:
            self.loading = loading
            self.idle_text = "" if loading else "Abre un PDF para ver la vista previa"  # sin aviso bajo las barras
            self.redraw()
            colors = theme.colors(self.dark)
            for shimmer in self.shimmers:
                shimmer.start(colors["line"], colors["shine"]) if loading else shimmer.stop()
        for action in self.book_actions:  # ocultas: en gris, en el tema oscuro parecían activas
            action.setVisible(has)

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
            elif event[0] == "log":  # detalle del fallo del proceso de trabajo, tras su "error"
                _log(event[1])
            elif event[0] == "rec":  # una línea de su registro
                registro.replay(event)
            else:
                self.finish(event)
        # Un proceso que muere sin avisar (fallo de MuPDF, falta de memoria) no envía "error".
        # Su último evento ya está en la cola antes de que termine, así que se mira después de vaciarla.
        if self.done and not self.proc.is_alive() and self.events.empty():
            self.finish(("error", f"El proceso de trabajo terminó inesperadamente (código {self.proc.exitcode})."))
        latest, self.latest = self.latest, None
        if latest:
            done, total = latest
            self.progress.setMaximum(total)
            self.progress.setValue(done)
            self.status.setText(f"{self.busy} {done * 100 // total} %")

    def finish(self, event):
        """Cierra el trabajo en curso: desbloquea la ventana y entrega el resultado o el error."""
        done, self.done, self.latest, self.partial = self.done, None, None, None
        qtk.after(1000, self.warm_worker)  # el de este trabajo ya terminó: otro preparado para el siguiente
        name, started = getattr(self, "work_log", ("?", time.perf_counter()))
        ok = event[0] in ("done", "cancel")
        LOG.log(logging.INFO if ok else logging.ERROR, "Trabajo %s: %s en %.1f s%s", name,
                {"done": "hecho", "cancel": "cancelado"}.get(event[0], "error"), time.perf_counter() - started,
                "" if ok else f" · {event[1]}")
        self.update_chrome()
        with contextlib.suppress(OSError):  # el proceso lo borra al leerlo; si murió antes, queda aquí
            os.remove(self.args_file)
        self.open_btn.setEnabled(True)
        self.batch_btn.setEnabled(True)
        if self.book:
            self.gen_btn.setEnabled(True)
            self.pdf_btn.setEnabled(True)
        if event[0] == "done":
            done(event[1])
        elif event[0] == "cancel":
            self.progress.setValue(0)
            if not self.book:  # análisis cancelado: el libro de antes ya se cerró, se vuelve al inicio
                self.pdf = None
                self.lock_book(None)  # otra ventana ya lo puede abrir
                self.stack.setCurrentIndex(0)
                self.update_header()
            self.status.setText("Cancelado")
        else:
            self.progress.setValue(0)
            self.status.setText("Error")
            messagebox.showerror("Error", f"{event[1]}\n\nEl detalle queda en {SETTINGS.parent / 'errores.log'}")

    def on_error(self, kind, value, tb):
        """Fallo dentro de la ventana (un botón, un atajo): se apunta en errores.log y se avisa, en vez
        de perderse en una consola que el .exe no tiene."""
        path = _log("".join(traceback.format_exception(kind, value, tb)), tb)
        messagebox.showerror("Algo falló", f"{value or kind.__name__}\n\nEl detalle queda en:\n{path}")

    def close(self):
        """Al cerrar a mitad de un trabajo, detiene el proceso y borra el EPUB a medio escribir.
        False si se canceló (editor de texto con cambios)."""
        editor = self.text_editor
        if editor:
            editor.sync_web()
        if editor and editor.dirty() and not messagebox.askyesno(
                "Cerrar", "El texto que estás editando tiene cambios sin guardar. ¿Cerrar y descartarlos?",
                parent=editor.win):
            editor.win.raise_()
            return False
        if self.translations_running() and not messagebox.askyesno(
                "Cerrar", "Hay una traducción en curso: si sales ahora se pierde el lote que se está traduciendo (hasta "
                          "30 párrafos; lo anterior ya está guardado). ¿Salir igual?"):
            return False
        if not self.save_review() and not messagebox.askyesno(
                "Cerrar", f"{self.status.text()}.\n\n¿Cerrar igual? Se pierde lo revisado desde la última vez que se "
                          "guardó."):
            return False
        if editor:
            editor.win.done(0)
            self.save_review()  # con dónde quedó el editor (editor_at)
        self.poller.stop()
        self.watcher.stop()
        if self.lang_timer:  # el informe se recalcularía sobre la ventana ya cerrada
            qtk.cancel(self.lang_timer)
        if self.autosave_timer:  # ya se guardó arriba
            qtk.cancel(self.autosave_timer)
        self.save_settings()
        if self.done:
            self.stop_work()
        if self.standby and self.standby[0].is_alive():
            self.standby[0].terminate()
        if self.lt_server and self.lt_server[0].poll() is None:  # Java con ~1 GB: no se queda tras la ventana
            self.lt_server[0].terminate()
        self.thumb_job += 1  # cancela las miniaturas pendientes
        if self.pdf_doc:
            self.pdf_doc.close()
            self.pdf_doc = None
        if self.preview_doc:
            self.preview_doc[1].close()
        Path(self.preview_path).unlink(missing_ok=True)
        self.lock_book(None)
        self.root.on_close, self.closed = None, True
        freezes = getattr(_log_start, "freezes", None)
        LOG.info("Se cierra la ventana · memoria máx. %s MB · %s congelaciones (la peor, %.1f s)",
                 registro.peak_memory_mb(), freezes.count if freezes else 0, freezes.worst if freezes else 0)
        self.root.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)  # libera la ventana y sus diálogos
        self.root.close()
        return True

    def preview_pane(self, name, step):
        """Columna de una vista previa: leyenda, la hoja y los botones ◀ ▶. Devuelve (columna, leyenda, hoja)."""
        pane = QWidget()
        column = QVBoxLayout(pane)
        column.setContentsMargins(4, 0, 4, 0)
        column.setSpacing(4)
        caption = _label(name, "muted")
        caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        view = qtk.Preview()
        # ◀ leyenda ▶ en una sola línea: en pantallas bajas, cada píxel de alto es de la hoja
        back, forward = qtk.ArrowButton(-1, lambda: step(-1)), qtk.ArrowButton(1, lambda: step(1))
        column.addLayout(_row(back, caption, forward))
        column.addWidget(view, 1)
        return pane, caption, view

    def box_size(self):
        """Tamaño fijo de cada vista previa (proporción 3:4) según el sitio que tienen. No depende de la
        página: así cambiar de dispositivo o de página no mueve el resto de la ventana."""
        if self.views_size is None:
            shown = 1 if self.tabs.currentIndex() != 2 else 2
            room_w, room_h = self.pages.width() // shown - 12, self.pages.height() - PREVIEW_CHROME
            if self.pages.height() < 60:  # aún sin dibujar: tamaño normal
                return 315, 420
            height = max(90, min(PREVIEW_BOX[1], room_h, int(room_w / 0.75)))
            self.views_size = (int(height * 0.75), height)
        return self.views_size

    def blank(self, view, text):
        """Hoja en blanco del tamaño de la vista previa, con un aviso tenue en el centro."""
        view.show_blank(self.box_size(), text, theme.colors(self.dark))

    def redraw(self):
        """Vuelve a dibujar las vistas previas (al iniciar, al cargar un libro o al cambiar de tamaño)."""
        if self.closed:
            return
        if self.book and self.tree.current() == COVER:
            self.show_cover()
            return
        if self.book and self.tree.current() == CONTENTS:
            self.show_contents()
            return
        if self.book and self.tree.selection():
            self.draw_pdf()
            self.draw_epub()
            return
        self.blank(self.pdf_view, self.idle_text)
        self.blank(self.epub_view, self.idle_text)
        # Tira vacía: rectángulos tenues en lugar de un hueco; analizando no, que va el esqueleto y salían dobles
        if not self.thumbs and not self.loading:
            self.strip.placeholders(20, int(THUMB_HEIGHT * 0.71), theme.colors(self.dark)["line"])

    def on_resize(self):
        size = (self.pages.width(), self.pages.height())
        if size != getattr(self, "pages_size", None):
            self.pages_size, self.views_size = size, None
            qtk.after(30, self.redraw)  # una vez al terminar de arrastrar, no en cada píxel

    def render(self, page, dark=False):
        """Imagen de una página (del PDF o del EPUB) ajustada a su marco, y su escala."""
        width, height = self.box_size()
        zoom = min(width / page.rect.width, height / page.rect.height)
        matrix = pymupdf.Matrix(zoom, zoom)
        return qtk.qimage(_page_pixmap(page, matrix, dark)), zoom

    def cover_page(self):
        """Página del PDF de donde sale la portada (desde 0): la elegida o la primera. None si no sale del PDF
        (un archivo o la portada con título)."""
        kind, value = self.cover_choice or ("", None)
        if kind == "page":
            return int(value)
        if kind == "image" and self.book:
            return self.book.blocks[int(value)].page - 1
        return None if kind in ("file", "text") else 0

    def cover_cell(self):
        page = self.cover_page()
        return "" if page is None else page + 1

    def show_cover(self):
        """Fila «Portada»: su página del PDF y la portada tal como abre el EPUB."""
        self.pdf_page, self.highlight = self.cover_page() or 0, None
        self.draw_pdf()
        self.epub_cut, self.epub_pages = False, 0
        image = QImage.fromData(self.book.cover) if self.book.cover else QImage()
        if image.isNull():
            self.blank(self.epub_view, "El EPUB llevará una portada con el título")
        else:
            width, height = self.box_size()
            self.epub_view.show_image((width, height), image.scaled(width, height, Qt.AspectRatioMode.KeepAspectRatio,
                                                                     Qt.TransformationMode.SmoothTransformation))
        self.epub_caption.setText("EPUB · portada")

    def spell_candidates(self):
        """Las palabras del libro que revisa la ortografía del editor. Una vez por libro: sacarlas en cada apertura
        del editor tardaba 0,16 s (Strange Grief 1, 8033 palabras). Se rehacen si cambia el texto o el diccionario
        propio."""
        blocks = [b for b in self.book.blocks if b.kind in ("p", "heading")]
        key = (id(self.book), len(blocks), sum(len(b.html) for b in blocks), tuple(sorted(self.words)))
        if getattr(self, "spell_memo", (None,))[0] != key:
            skip = convert.spell_skip(self.words)
            text = " ".join(convert._plain(b) for b in blocks)
            self.spell_memo = (key, {w for w in convert.spell_words(text) if convert.spell_candidate(w, skip)})
        return self.spell_memo[1]

    def toc_page(self):
        """Página del índice impreso del PDF (desde 0), entre las primeras; None si no lo tiene. Una vez por
        documento: cada cambio de «Contenido» la buscaba de nuevo y, en un EPUB, contar sus páginas lo compone entero
        (registro.log: la ventana congelada 2,3 s al abrir un EPUB japonés)."""
        doc = self.pdf_doc
        if getattr(self, "toc_memo", (None,))[0] is not doc:
            self.toc_memo = (doc, _toc_page(doc))
        return self.toc_memo[1]

    def contents_row(self):
        """La fila «Contenido» como va: «Índice» si el EPUB la lleva, «Quitar» si no; con la página del índice
        impreso del PDF."""
        on = self.contents_var.get()
        page = self.toc_page()
        values = (INDEX if on else CUT, "Contenido", "", "" if page is None else page + 1, "")
        if self.tree.exists(CONTENTS):
            for column, value in zip(self.tree.columns, values):
                self.tree.set(CONTENTS, column, value)
            self.tree.item(CONTENTS, tags=(KIND_TAG[values[0]],))
        else:
            self.tree.pin(CONTENTS, values, (KIND_TAG[values[0]],), index=1)
        if self.tree.current() == CONTENTS:
            self.show_contents()

    def show_contents(self):
        """Fila «Contenido»: el índice impreso del PDF y la página «Contenido» del EPUB, con los capítulos y
        subtítulos de la lista como irán (renombrados, sin los quitados)."""
        page = self.toc_page()
        self.pdf_page, self.highlight = page or 0, None
        if page is None:
            self.blank(self.pdf_view, "El PDF no tiene índice impreso")
        else:
            self.draw_pdf()
        self.epub_page, self.epub_pages = 0, 0
        self.epub_cut, self.epub_message = not self.contents_var.get(), "El EPUB irá sin «Contenido»"
        if not self.epub_cut:
            rows = [(self.tree.set(i, "inc"), self.tree.set(i, "title"), self.book.blocks[int(i)].page)
                    for i in self.tree.get_children() if self.is_title(i) and self.tree.set(i, "inc") in (ON, OFF)]
            # Solo los títulos: escribir el libro entero para una página de nombres tardaba
            part = convert.Book(self.book.title, "", self.book.cover,
                                [convert.Block("heading", page, html.escape(title)) for _, title, page in rows], [])
            chapters = [(n, title) for n, (kind, title, _) in enumerate(rows) if kind == ON]
            options = self.write_options([])
            options.update(shrink=False, trim=False, epub2=False, credits=False, contents=True, reading=False,
                           synopsis=False, logo="")
            key = ("contents", chapters, rows, sorted(options.items()))
            try:
                if key != self.preview_key:  # «Contenido» va justo tras la portada: el capítulo 1 del EPUB
                    self.preview_key, self.preview_doc = None, self.preview_doc and self.preview_doc[1].close()
                    convert.write_epub(part, chapters, self.preview_path, **options)
                    self.preview_key, self.preview_faithful = key, False
            except Exception:
                self.epub_pages = -1
        self.draw_epub()

    def update_preview(self, _=None):
        """Al seleccionar (o cambiar) un título: su página del PDF y su sección en el EPUB."""
        if self.book and self.tree.current() == COVER:
            self.show_cover()
            return
        if self.book and self.tree.current() == CONTENTS:
            self.show_contents()
            return
        selected = self.tree.selection()
        if not selected or not self.book:
            return
        item = selected[0]
        index = int(item)
        block = self.book.blocks[index]
        self.pdf_page = _epub_block_page(self.book, self.pdf_doc, index) if self.book.origin else block.page - 1
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
                inner = {}  # tipos de los títulos dentro del tramo (subtítulos de un capítulo), desde 0
                if is_image:
                    part_blocks = [convert.Block("image", block.page, image=block.image, layout=LAYOUT.get(kind, ""))]
                else:
                    # Un capítulo sigue con sus subtítulos, como en el EPUB: no se corta en el primero
                    end = self.section_of(item)[1]
                    part_blocks = self.book.blocks[index:end]
                    inner = {k - index: self.tree.set(str(k), "inc") for k in range(index + 1, end)
                             if self.tree.exists(str(k)) and self.is_title(str(k))}
                # Una ilustración sola, sin el EPUB de origen: la copia fiel solo sabe escribir bloques de sus archivos
                part = convert.Book(self.book.title, "", self.book.cover, part_blocks, [],
                                    origin=None if is_image else self.book.origin, vertical=self.book.vertical)
                # una fila de separador que ya es un separador (el dibujo de un EPUB) va como es, no como título
                options = self.write_options([0] * (kind == SEP and block.kind != "break")
                                             + [k for k, v in inner.items() if v == SEP])
                options["removed"] = [k for k, v in inner.items() if v == CUT]
                # rápido: solo lo que se ve; sin página de índice: el capítulo 1 es la sección
                options.update(shrink=False, trim=False, epub2=False, credits=False, contents=False, synopsis=False,
                               logo="")
                chapters = [(0, self.tree.set(item, "title"))] if kind == ON else []
                options["fit"] = DEVICES[self.device_var.get()]  # ilustraciones que caben en la página de MuPDF
                options["preview"] = True  # un EPUB de entrada: sin las imágenes del resto del libro
                key = (self.book.title, self.book.cover, chapters, sorted(options.items()),
                       [(b.kind, b.page, b.html, b.image, b.layout, b.align, b.keep) for b in part_blocks])
                if key != self.preview_key:  # lo mismo que ya está escrito y maquetado: no se rehace
                    self.preview_key, self.preview_doc = None, self.preview_doc and self.preview_doc[1].close()
                    convert.write_epub(part, chapters, self.preview_path, **options)
                    self.preview_key, self.preview_faithful = key, bool(part.origin) and Path(part.origin).is_file()
            except Exception:
                self.epub_pages = -1  # no se pudo generar
        self.draw_epub()
        if self.epub_page:  # se entró por el final (◀ desde la sección de después): el original, a esa página
            self.follow_epub()
        if self.find_target:
            self.show_found(*self.find_target)

    def show_found(self, page, term):
        """Tras buscar: la página del PDF con el resultado y, en la sección, la página del EPUB que lo tiene."""
        self.find_target = None
        self.pdf_page, self.highlight = page - 1, None
        self.draw_pdf()
        try:
            epub, pages = self.preview_epub()
            self.epub_page = next((n for n in range(pages) if self.epub_load(n).search_for(term)), 0)
        except Exception:
            pass  # sin vista previa del EPUB: se queda en su primera página
        self.draw_epub()

    def draw_pdf(self):
        canvas = self.pdf_view
        try:
            image, zoom = self.render(self.pdf_doc[self.pdf_page], dark=self.dark_var.get())
        except Exception:
            self.blank(canvas, "Sin vista previa")
            return
        marks = []
        if self.highlight and self.highlight[0] == self.pdf_page:  # recuadro sobre la línea tomada como título
            x0, y0, x1, y1 = (v * zoom for v in self.highlight[1])
            marks.append((QRectF(x0 - 3, y0 - 2, x1 - x0 + 6, y1 - y0 + 4), "red"))
        canvas.show_image(self.box_size(), image, marks)
        self.pdf_caption.setText(f"{self.source_name()} · página {self.pdf_page + 1}")
        self.mark_current()

    def preview_epub(self):
        """El EPUB de la vista previa maquetado para el dispositivo elegido, y sus páginas: recordado mientras
        no se reescriba ni cambie el dispositivo."""
        size = DEVICES[self.device_var.get()]
        if not self.preview_doc or self.preview_doc[0] != size:
            if self.preview_doc:
                self.preview_doc[1].close()
            self.preview_doc = None
            # Desde los bytes: abierto por ruta, Windows no dejaría reescribir el archivo. Arreglado como el original
            # (_preview_epub): sin eso la ilustración de una página Kadokawa salía pequeña en una esquina
            data = convert._preview_epub(self.preview_path) or Path(self.preview_path).read_bytes()
            epub = pymupdf.open(stream=data, filetype="epub")
            epub.layout(width=size[0], height=size[1], fontsize=11)
            # La portada (capítulo 0) no es de la sección, salvo en un EPUB cuya sección empieza en la portada del
            # original: entonces no se añade la de la app (faithful) y saltarla desfasaba el original una página.
            # Solo en la copia fiel: una ilustración o «Contenido» se escriben sin el origen, siempre con portada
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                skip = 0 if self.preview_faithful and not any(
                    n.endswith("lnc/portada.xhtml") for n in z.namelist()) else 1
            # Todas las páginas de la sección: un capítulo de un EPUB puede ocupar varios archivos (portadilla y texto)
            pages = [(c, p) for c in range(skip, epub.chapter_count) for p in range(epub.chapter_page_count(c))]
            if self.book and self.book.origin:  # sin las páginas en blanco del salto antes de cada ilustración
                pages = [n for n in pages if epub.load_page(n).get_bboxlog()] or pages
            self.preview_doc = size, epub, pages
        return self.preview_doc[1], len(self.preview_doc[2])

    def epub_load(self, n):
        """Página n (desde 0) de la sección en la vista previa del EPUB."""
        return self.preview_doc[1].load_page(self.preview_doc[2][n])

    def draw_epub(self):
        view = self.epub_view
        if self.epub_cut or self.epub_pages < 0:
            self.blank(view, self.epub_message if self.epub_cut else "Sin vista previa")
            self.epub_caption.setText("EPUB")
            return
        try:
            epub, self.epub_pages = self.preview_epub()
            self.epub_page = max(0, min(self.epub_page, self.epub_pages - 1))
            image = self.device_image(self.epub_load(self.epub_page), self.dark_var.get())
        except Exception:
            self.blank(view, "Sin vista previa")
            return
        view.show_image(self.box_size(), image)
        self.epub_caption.setText(f"EPUB · {self.device_var.get()} · {self.epub_page + 1}/{self.epub_pages}")

    def device_image(self, page, dark):
        """Página del EPUB en la pantalla del dispositivo. Una de maquetación fija (Kadokawa: 716×1024, o el doble
        si es una ilustración a dos páginas) no se adapta al tamaño elegido: el lector la encaja entera en la
        pantalla, con bandas de papel, y así se ve."""
        width, height = DEVICES[self.device_var.get()]
        if abs(page.rect.width - width) < 1 and abs(page.rect.height - height) < 1:
            return self.render(page, dark)[0]
        box_w, box_h = self.box_size()
        zoom = min(box_w / width, box_h / height)
        screen = QImage(round(width * zoom), round(height * zoom), QImage.Format.Format_RGB32)
        screen.fill(QColor(theme.DARK["sheet"] if dark else "white"))
        scale = min(screen.width() / page.rect.width, screen.height() / page.rect.height)
        image = qtk.qimage(_page_pixmap(page, pymupdf.Matrix(scale, scale), dark))
        painter = QPainter(screen)
        painter.drawImage((screen.width() - image.width()) // 2, (screen.height() - image.height()) // 2, image)
        painter.end()
        return screen

    def pdf_step(self, delta):
        if self.pdf_doc:
            self.pdf_page = max(0, min(self.pdf_page + delta, self.pdf_doc.page_count - 1))
            self.draw_pdf()
            self.follow_original()

    # Original y EPUB sincronizados: al pasar página en uno, el otro va a la misma parte del libro
    def epub_firsts(self):
        """Bloque del libro con que empieza cada página de la vista previa del EPUB (el que sigue de la anterior si
        no empieza ninguno), buscando el texto de cada párrafo en las páginas, en orden. Una vez por vista previa."""
        doc, selected = self.preview_doc, self.tree.selection()
        if not doc or not selected or not self.book or self.epub_cut or self.tree.current() in (COVER, CONTENTS):
            return []
        if len(doc) > 3:
            return doc[3]
        start, end = self.section_of(selected[0])
        blocks, firsts, k = self.book.blocks, [], start

        def where(j, text, alone, at):
            """(posición, avance) del bloque j en la página, detrás de at: un párrafo repetido («Ｒｅ：ゼロ…１» en
            奥付) no se toma por el de antes. Sin letras (un separador), corto («Sí.») o una ilustración sola: con lo
            de antes, sin avanzar. None si no está."""
            if blocks[j].kind == "image":
                return (at, 0) if alone else None
            probe = _match_key(convert._plain(blocks[j]))[:16]
            if len(probe) < 6:
                return at, 0
            spot = text.find(probe, at)
            return (spot, 1) if spot >= 0 else None
        for n in doc[2]:
            page = doc[1].load_page(n)
            text = _match_key(page.get_text())
            alone = not text and bool(page.get_image_info())  # una ilustración, sola en su página
            first, at = None, 0
            while k < end:
                spot = where(k, text, alone, at)
                if spot is None:  # uno que no se encuentra (quitado, renombrado): los de detrás
                    k = next((j for j in range(k + 1, min(k + 9, end)) if (blocks[j].kind == "image" or len(
                        _match_key(convert._plain(blocks[j]))) >= 6) and where(j, text, alone, at)), k)
                    spot = where(k, text, alone, at)
                    if spot is None:
                        break
                at = spot[0] + spot[1]
                first = k if first is None else first
                k += 1
                if alone and blocks[k - 1].kind == "image":  # una ilustración por página
                    break
            firsts.append(first if first is not None else max(start, k - 1))
        self.preview_doc = (*doc, firsts)
        return firsts

    def original_page(self, k):
        """Página del original (desde 0) donde está el bloque k."""
        return _epub_block_page(self.book, self.pdf_doc, k) if self.book.origin else self.book.blocks[k].page - 1

    def block_at_original(self, page):
        """Primer bloque de la sección de la fila que está en la página page del original (o el que viene de la
        anterior); None si la página no es de la sección."""
        selected = self.tree.selection()
        if not selected or not self.book:
            return None
        start, end = self.section_of(selected[0])
        blocks = self.book.blocks
        if self.book.origin and self.pdf_doc:  # un EPUB: el bloque solo sabe la primera página de su archivo
            text = _match_key(self.pdf_doc[page].get_text())
            # Solo los del archivo de esa página (su page es la primera del archivo): una frase del capítulo 1 que
            # también está en el prólogo dejaba la fila en el capítulo y el EPUB en su final
            here = max((b.page - 1 for b in blocks if b.page - 1 <= page), default=-1)
            # el que empieza más arriba; uno corto («Sí.») está en cualquier página: no cuenta
            hits = [(text.find(probe), k) for k in range(start, end) if blocks[k].kind != "image"
                    and blocks[k].page - 1 == here
                    and len(probe := _match_key(_source_plain(blocks[k]))[:16]) >= 8 and probe in text]
            if hits:
                k = min(hits)[1]
                # Un párrafo repetido en el archivo («Ｒｅ：ゼロ…１» al principio y al final de 奥付): el de su orden,
                # contando las veces que ya salió en las páginas de antes; si no, la 2.ª página volvía a la 1.ª
                probe = _match_key(_source_plain(blocks[k]))[:16]
                twins = [j for j in range(start, end) if blocks[j].kind != "image" and blocks[j].page - 1 == here
                         and _match_key(_source_plain(blocks[j]))[:16] == probe]
                if len(twins) > 1:
                    seen = sum(_match_key(self.pdf_doc[n].get_text()).count(probe) for n in range(here, page))
                    k = twins[min(seen, len(twins) - 1)]
                return k
            if not text:  # una ilustración
                found = next((k for k in range(start, end) if blocks[k].kind == "image"
                              and self.original_page(k) == page), None)
                if found is not None:
                    return found
        on = [k for k in range(start, end) if blocks[k].page - 1 == page]
        if on:
            return on[0]
        pages = [blocks[k].page - 1 for k in range(start, end)]
        if pages and min(pages) < page <= max(pages):
            return max(k for k in range(start, end) if blocks[k].page - 1 < page)
        return None

    def reading_rows(self):
        """Filas que se leen una tras otra con ◀ ▶: sin separadores ni lo de dentro de un capítulo (subtítulos e
        ilustraciones: ya se leen con él, y salían dos veces), salvo el elegido."""
        selected, items, until = self.tree.selection(), [], -1
        for i in self.tree.get_children():
            if self.is_break(i) or (int(i) < until and i not in selected):
                continue
            items.append(i)
            if self.is_title(i) and self.tree.set(i, "inc") == ON:
                until = self.section_of(i)[1]
        return items

    def follow_original(self):
        """El original cambió de página: el EPUB, a la página con ese texto; si es de otra sección, a la fila de la
        lista que la tiene."""
        if not self.book or not self.pdf_doc or self.tree.current() in (COVER, CONTENTS) or not self.tree.selection():
            return
        page = self.pdf_page
        k = self.block_at_original(page)
        if k is None:
            item = next((i for i in reversed(self.reading_rows()) if self.book.blocks[int(i)].page - 1 <= page), None)
            if item is None or item == self.tree.selection()[0]:
                return
            self.tree.selection_set(item)  # update_preview: la sección, desde su principio
            self.tree.see(item)
            self.pdf_page = page
            self.draw_pdf()
            k = self.block_at_original(page)
            if k is None:
                return
        firsts = self.epub_firsts()
        n = max((n for n, first in enumerate(firsts) if first <= k), default=0)
        if firsts and n != self.epub_page:
            self.epub_page = n
            self.draw_epub()

    def follow_epub(self):
        """El EPUB cambió de página dentro de la sección: el original, a la página de su primer párrafo."""
        firsts = self.epub_firsts()
        if 0 <= self.epub_page < len(firsts) and self.pdf_doc:
            page = self.original_page(firsts[self.epub_page])
            if page != self.pdf_page and 0 <= page < self.pdf_doc.page_count:
                self.pdf_page = page
                self.draw_pdf()

    def epub_step(self, delta):
        """◀ ▶ del EPUB: páginas de la sección y, al llegar a un extremo, la sección vecina de la lista,
        para leer el libro seguido (hacia atrás se entra por su última página). Un salto largo se queda
        primero en el borde de la sección."""
        edge = 0 if delta < 0 else self.epub_pages - 1
        if self.epub_pages > 0 and self.epub_page != edge:
            self.epub_page = max(0, min(self.epub_page + delta, self.epub_pages - 1))
            self.draw_epub()
            self.follow_epub()
            return
        selected = self.tree.selection()
        # Los separadores no son secciones: desde uno se sigue con la sección de antes o la de después.
        items = self.reading_rows()
        here = int(selected[0]) if selected else None
        if here is None:
            n = 0 if delta > 0 and self.tree.current() in (COVER, CONTENTS) else -1  # de la portada al libro
        elif selected[0] in items:
            n = items.index(selected[0]) + delta
        else:
            n = sum(int(i) < here for i in items) - (1 if delta < 0 else 0)
        if 0 <= n < len(items):
            self.next_page = 10 ** 6 if delta < 0 else 0  # draw_epub la ajusta a la última
            self.tree.selection_set(items[n])
            self.tree.see(items[n])

    def start_thumbs(self):
        """Empieza a dibujar las miniaturas del PDF, por tandas para no congelar la ventana."""
        self.thumb_job += 1  # cancela la tanda de un libro anterior
        self.strip.clear()
        self.thumbs = {}
        titles = {b.page for b in self.book.blocks if b.kind == "heading"}
        images = {b.page for b in self.book.blocks if b.kind == "image"}
        qtk.after(10, self.add_thumbs, self.thumb_job, 0, titles, images)

    def add_thumbs(self, job, start, titles, images):
        if job != self.thumb_job or not self.pdf_doc or self.closed:
            return
        if self.text_editor:  # con el editor abierto, la tira espera: dibujar páginas trababa cada tecla
            qtk.after(1000, self.add_thumbs, job, start, titles, images)
            return
        end, until = min(start + THUMBS_PER_TICK, self.pdf_doc.page_count), time.perf_counter() + THUMBS_TICK_S
        for n in range(start, end):
            if n > start and time.perf_counter() > until:  # el resto, en la tanda siguiente
                end = n
                break
            page = self.pdf_doc[n]
            zoom = THUMB_HEIGHT / page.rect.height
            image = qtk.qimage(page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)))
            color = MARK_COLORS["title"] if n + 1 in titles else MARK_COLORS["image"] if n + 1 in images else ""
            self.strip.add(n, image, color)
            self.thumbs[n] = (image.width(), image.height())
        self.mark_current()
        if end < self.pdf_doc.page_count:
            qtk.after(10, self.add_thumbs, job, end, titles, images)

    def mark_current(self):
        """Destaca en la tira la miniatura de la página que muestra la vista previa del PDF."""
        self.strip.mark(self.pdf_page)

    def jump(self, n):
        """Clic en una miniatura: esa página en la vista previa y, si tiene un título, seleccionarlo."""
        self.pdf_page = n
        self.draw_pdf()
        item = next((i for i in self.tree.get_children() if self.book.blocks[int(i)].page == n + 1), None)
        if item:
            self.tree.selection_set(item)
            self.tree.see(item)
        else:  # una página de en medio de una sección: el EPUB, a ella
            self.follow_original()

    def remember(self, change):
        """Apunta un cambio para Ctrl+Z y guarda la revisión poco después (autosave)."""
        self.history.append(change)
        self.autosave()

    def autosave(self):
        """Guarda la revisión 2 s después del último cambio: una ventana cerrada de golpe (corte de luz, el
        Administrador de tareas) ya no pierde lo revisado, y varios cambios seguidos son una sola escritura."""
        if self.autosave_timer:
            qtk.cancel(self.autosave_timer)
        self.autosave_timer = qtk.after(AUTOSAVE_MS, self.autosaved)

    def autosaved(self):
        self.autosave_timer = None
        if not self.closed:
            self.save_review()

    def row_state(self, item):
        return item, self.tree.set(item, "inc"), self.tree.set(item, "title")

    def set_kind(self, item, kind):
        self.remember([self.row_state(item)])
        self.tree.set(item, "inc", kind)
        self.tree.item(item, tags=(KIND_TAG[kind],))

    def update_reviewed(self):
        """Cabecera de la columna ✓: capítulos revisados de los que hay («✓ 12/30»). De paso, las flechas de
        pertenencia de la columna Tipo y los avisos del índice impreso: tras cualquier cambio de tipo se pasa por aquí."""
        self.link_rows()
        if self.book and self.book.toc:
            self.update_quality()
        chapters = [i for i in self.tree.get_children() if self.tree.set(i, "inc") == ON]
        done = sum(bool(self.tree.set(i, "ok")) for i in chapters)
        self.tree.headerItem().setText(self.tree.columns.index("ok"), f"✓ {done}/{len(chapters)}" if chapters else "✓")
        return done, len(chapters)

    def link_rows(self):
        """Columna Tipo: una flecha baja de cada capítulo a las filas que van dentro de él en el EPUB
        (subtítulos, ilustraciones, separadores), hasta el siguiente capítulo."""
        shapes, group = {}, []
        for i in self.tree.get_children() + ("",):  # "": cierra el último grupo
            if not i or self.tree.set(i, "inc") == ON:
                if len(group) > 1:
                    shapes.update(dict.fromkeys(group[1:], "mid"))
                    shapes[group[0]], shapes[group[-1]] = "root", "last"
                group = [i]
            elif group:
                group.append(i)
        self.tree.link(shapes, "inc")

    def toggle_reviewed(self):
        """R: marca el título elegido como revisado y pasa al siguiente capítulo sin revisar; si ya lo estaba,
        lo desmarca."""
        selected = self.tree.selection()
        if not self.book or not selected or not self.is_title(selected[0]):
            return
        item = selected[0]
        mark = "" if self.tree.set(item, "ok") else "✓"
        self.tree.set(item, "ok", mark)
        self.autosave()
        done, total = self.update_reviewed()
        self.status.setText(f"Capítulos revisados: {done} de {total}")
        items = self.tree.get_children()
        following = next((i for i in items[items.index(item) + 1:]
                          if self.tree.set(i, "inc") == ON and not self.tree.set(i, "ok")), None)
        if mark and following:
            self.tree.selection_set(following)
            self.tree.see(following)

    def undo(self, _=None):
        """Ctrl+Z: deshace el último cambio: tipo o título de una fila, títulos corregidos de golpe o
        un texto editado (vuelve la copia de los bloques y de la lista de antes)."""
        if not self.history:
            return
        change = self.history.pop()
        self.autosave()
        if isinstance(change, dict):
            self.book.blocks, self.book.candidates = change["blocks"], change["candidates"]
            del self.edits[change["edits"]:]
            self.fill_tree(change["rows"])
            # Lista vacía, o la fila elegida era la de una página añadida que ya no está: nada que volver a elegir
            if change["item"] is None or not self.tree.exists(change["item"]):
                return
            change = [self.row_state(change["item"])]
        for item, kind, title in change:
            self.tree.set(item, "inc", kind)
            self.tree.item(item, tags=(KIND_TAG[kind],))
            self.tree.set(item, "title", title)
        self.update_reviewed()
        self.tree.selection_set(change[-1][0])
        self.tree.see(change[-1][0])
        self.update_preview()

    def cycle(self, item):
        """Orden de tipos de una fila al hacer clic: título, ilustración o ilustración doble."""
        if self.is_break(item):
            return {}
        if not self.is_image(item):
            return NEXT
        return WIDE_NEXT if item in self.wide else IMAGE_NEXT

    def close_tools(self, names):
        """Cierra las ventanas de herramientas abiertas (atributos names; algunas guardan (ventana, …))."""
        for win in (getattr(self, name, None) for name in names):
            win = win[0] if isinstance(win, tuple) else win
            if win and shiboken6.isValid(win):
                win.close()

    def raise_tool(self, name):
        """True si la herramienta name ya está abierta: se trae al frente. Un doble clic abría dos y al abrir otro
        libro solo se cerraba la última; la otra cambiaba el libro nuevo con datos del anterior."""
        win = getattr(self, name, None)
        win = win[0] if isinstance(win, tuple) else win
        if win and shiboken6.isValid(win) and win.isVisible():
            win.raise_()
            win.activateWindow()
            return True
        return False

    def lock_book(self, path):
        """Candado del libro path para esta ventana, en la carpeta de ajustes (None: suelta el que tenga). False si
        otra ventana lo tiene abierto: las dos guardarían su revisión una encima de la otra."""
        key = hashlib.sha1(str(path).lower().encode()).hexdigest()[:16] if path else None
        if key == self.lock_key:
            return True
        lock = None
        if key:
            folder = SETTINGS.parent / "abiertos"
            with contextlib.suppress(OSError):
                folder.mkdir(parents=True, exist_ok=True)
            lock = QLockFile(str(folder / f"{key}.lock"))
            lock.setStaleLockTime(0)  # vale mientras viva la otra ventana; si murió de golpe, Qt lo quita
            if not lock.tryLock(0):
                if lock.error() == QLockFile.LockError.LockFailedError:
                    return False
                lock = key = None  # sin poder escribir el candado (carpeta de solo lectura), se abre igual
        if self.book_lock:
            self.book_lock.unlock()
        self.book_lock, self.lock_key = lock, key
        return True

    def open_pdf(self):
        path = filedialog.askopenfilename(filetypes=[("PDF o EPUB", "*.pdf *.epub")],
                                          initialdir=self.settings.get("folder"))
        if path:
            self.open_path(path)

    def source_name(self):
        """Nombre del original en pestañas y leyendas: «PDF», u «Original» si es un EPUB (la vista previa de la
        derecha ya se llama EPUB)."""
        return "Original" if self.pdf and self.pdf.suffix.lower() == ".epub" else "PDF"

    def open_path(self, path):
        """Abre un PDF o un EPUB: desde el botón, el menú de recientes o arrastrado sobre el .exe."""
        if self.done:  # hay un trabajo en curso
            self.status.setText("Espera a que termine el trabajo en curso")  # doble clic en el panel de serie
            return
        if not Path(path).is_file():
            messagebox.showerror("Error", f"No se encuentra el archivo:\n{path}")
            return
        if not self.save_review() and not messagebox.askyesno(
                "Abrir", f"{self.status.text()}.\n\n¿Abrir el otro libro igual? Se pierde lo revisado en este desde la "
                         "última vez que se guardó."):
            return
        if not self.lock_book(Path(path).resolve()):
            messagebox.showerror("Abrir", f"«{Path(path).name}» ya está abierto en otra ventana de {APP_NAME}.\n\n"
                                          "Ciérralo allí para abrirlo aquí: si no, cada ventana guardaría su revisión "
                                          "encima de la de la otra.")
            return
        self.close_tools(BLOCK_TOOLS + BOOK_TOOLS)  # sin cerrar, cambiarían el libro nuevo con datos del anterior
        path = str(Path(path).resolve())
        LOG.info("Abrir %s (%s KB)", path, Path(path).stat().st_size // 1024)
        self.settings["folder"] = str(Path(path).parent)
        keep = self.settings.get("recent_max", RECENT)
        self.settings["recent"] = [path] + [p for p in self.settings.get("recent", []) if p != path][:max(1, keep) - 1]
        self.fill_recent()
        self.pdf, self.book = Path(path), None
        self.file_label.setText(self.pdf.name)
        self.tabs.setTabText(0, self.source_name())
        self.pdf_caption.setText(self.source_name())
        self.tree.delete(*self.tree.get_children())
        # Nada del libro anterior bajo el esqueleto: sus miniaturas, sus leyendas y su recuento de revisados
        self.thumb_job += 1
        self.strip.clear()
        self.thumbs = {}
        self.pdf_caption.setText(self.source_name())
        self.epub_caption.setText("EPUB")
        self.tree.headerItem().setText(self.tree.columns.index("ok"), "✓")
        self.cover_thumb.clear()
        self.book_title.setText(self.pdf.stem)  # los datos del libro anterior, no: los del nuevo llegan al analizar
        self.book_line.setText("")
        self.status.setText("Analizando…")  # el resumen del libro anterior no
        self.stack.setCurrentIndex(1)
        self.run(_analyze, (path,), lambda result: self.show_book(*result), report=True)

    def show_book(self, book, twins=None, report=None, memos=None, shown=None):
        """Pone en la ventana un libro analizado. twins, report, memos y shown: sus ilustraciones repetidas, su
        informe de calidad, lo que el informe recuerda de cada párrafo y, para abrir el PDF (_analyze), la página de
        su índice impreso y los JPEG 2000 que arreglar, si ya se calcularon en el proceso de trabajo."""
        self.book = book
        LOG.info("Libro: «%s» · %d bloques · %d títulos · %d ilustraciones · %s", book.title, len(book.blocks),
                 len(book.candidates), sum(b.kind == "image" for b in book.blocks),
                 "EPUB de entrada" if book.origin else "PDF")
        # El análisis tal cual, para restaurar versiones sin volver a analizar: las ediciones hacen listas nuevas
        self.pristine = (list(book.blocks), list(book.candidates), book.cover)
        # Son del libro: llegan con la revisión (el glosario, también heredado de otro tomo)
        self.glossary, self.tr_glossary, self.comments, self.alts = [], [], [], {}
        self.tr_notes, self.editor_at, self.work = "", None, {}
        self.editor_history = {}
        if report is not None and not self.echoes_var.get() and self.spanish():  # el del proceso: sin opciones, en español
            self.reports[self.report_key()] = report
        if memos:
            convert.seed_report_memos(memos)
        if self.pdf_doc:
            self.pdf_doc.close()
        # vista previa, miniaturas y panel del editor
        self.pdf_doc = convert.open_pdf(self.pdf, shown["jpx"] if shown else None)
        if shown:
            self.toc_memo = (self.pdf_doc, shown["toc"])
        self.stack.setCurrentIndex(1)
        self.update_chrome()
        self.save_thumb()
        self.apply_state(self.new_book_options())  # la revisión o la serie, si las hay, mandan después
        if book.origin:
            self.apply_state(EPUB_DEFAULTS)
        self.offer_original(bool(book.origin))
        self.title_var.set(book.title)
        self.author_var.set(book.author)
        self.series_var.set(book.series)
        self.volume_var.set(book.volume)
        for name in BOOK_FIELDS:
            if hasattr(self, f"{name}_var"):
                getattr(self, f"{name}_var").set(getattr(book, name))
        self.description = book.description
        self.last_problems = None  # avisos del último EPUB generado (para el panel de serie)
        self.replacements = []  # son de la serie: llegan con la revisión o heredados de otro tomo
        self.words, self.ignored, self.unknown = [], [], set()
        self.cover_choice = None
        self.cover_label.setText(f"Portada: la del {'EPUB' if self.source_name() == 'Original' else 'PDF'}")
        self.edits, self.analyzed = [], len(book.blocks)
        self.fill_tree({})
        cut = convert.link_sections(book)  # publicidad del grupo: se propone quitarla
        for k in cut:
            self.set_kind(str(k), CUT)
        if twins is None:  # la misma ilustración otra vez: se propone descartarla
            twins = convert.duplicate_images(book)
        for k in twins:
            self.set_kind(str(k), DROPPED)
        self.update_reviewed()  # una vez, no por fila: recorre la lista entera
        heads = [i for i in self.tree.get_children() if self.is_title(i)]
        if self.autocase_var.get() and _all_caps([self.tree.set(i, "title") for i in heads]):
            self.fix_case()
        self.idle_text = "Selecciona un título en la lista"
        self.blank(self.pdf_view, self.idle_text)
        self.blank(self.epub_view, self.idle_text)
        self.start_thumbs()
        self.gen_btn.setEnabled(True)
        self.pdf_btn.setEnabled(True)
        self.progress.setValue(0)
        items = self.tree.get_children()
        found = len(book.candidates)
        images = sum(self.is_image(i) for i in items)
        scenes = sum(self.is_break(i) for i in items)
        dropped = sum(self.tree.set(i, "inc") == DROPPED for i in items)
        others = len(items) - found - images - scenes
        text = (convert.counted(found, "capítulo detectado", "capítulos detectados") if found
                else "No se detectaron capítulos: el EPUB tendrá una sola sección")
        text += f", {convert.counted(others, 'subtítulo')}" if others else ""
        text += (f" y {convert.counted(images, 'ilustración', 'ilustraciones')}"
                 + (f" ({convert.counted(dropped, 'descartada')})" if dropped else "") if images else "")
        text += f" · {convert.counted(len(cut), 'sección', 'secciones')} del grupo en Quitar" if cut else ""
        twins_text = convert.counted(len(twins), "ilustración repetida descartada", "ilustraciones repetidas descartadas")
        text += f" · {twins_text}" if twins else ""
        pages = [str(page) for page, _, why in book.skipped if why == convert.TOC_SKIPPED]
        if book.toc:  # el índice impreso no se copia: se usa para encontrar y comprobar los capítulos
            text += f" · índice del PDF en la página {', '.join(pages)} ({convert.counted(len(book.toc), 'título')})"
        text += f" · {convert.counted(scenes, 'separador', 'separadores')}" if scenes else ""  # al final: si no cabe, se corta esto
        if self.load_review():
            text = "Revisión anterior recuperada"
            # Serie convertida antes de traducir: este tomo ya tenía revisión, pero sin glosario ni personajes; los de
            # otro tomo valen. Lo que el tomo tenga suyo no se toca
            shared = {name: value for name, value in _inherited(self.pdf, book.series).items()
                      if name in SERIES_VOCAB and value and not getattr(self, name)}
            if shared:
                self.apply_state(shared)
                text += " · glosario y personajes de la serie tomados de otro tomo"
        elif self.pdf:
            # Del mismo PDF pero de otro análisis (otra versión del programa): los cambios de texto van por número
            # de bloque y ya no encajan, pero los datos del libro (título, serie, glosarios, portada…) sí valen
            stale = _read_review(self.pdf)
            backup = _set_aside(self.pdf)
            if backup:  # delante: si la línea no cabe, se corta el recuento y no esto
                text = f"La revisión guardada no coincide con este análisis: copia en {backup.name} · {text}"
            if stale and book.origin and not stale.get("faithful"):
                # De cuando el EPUB se leía como un PDF: sus datos y opciones eran de aquella lectura (el autor con su
                # furigana pegado, el estilo de la app). Solo vale lo de la serie
                self.apply_state({k: v for k, v in stale.items() if k in SERIES_VOCAB + ("replacements",)})
                text = (f"La revisión guardada era de la lectura anterior de este EPUB: queda en {backup.name}; se "
                        "conservan el glosario y los reemplazos")
            elif stale:
                self.apply_state(stale)
                self.description = stale.get("description", "")
                if stale.get("cover"):
                    try:
                        self.set_cover(stale["cover"])
                    except Exception:
                        pass  # la imagen de portada ya no existe: se queda la del PDF
                text = (f"La revisión guardada no coincide con este análisis: se conservan los datos del libro; los "
                        f"cambios de texto y de la lista quedan en {backup.name}")
            else:
                inherited = _inherited(self.pdf, book.series)
                if book.origin:  # el EPUB trae sus datos y su aspecto: de la serie, solo su vocabulario
                    inherited = {k: v for k, v in inherited.items() if k in SERIES_VOCAB + ("replacements",)}
                if book.author:
                    inherited.pop("author", None)  # el del PDF manda
                if inherited:
                    self.apply_state(inherited)
                    text += " · datos de la serie tomados de otro tomo"
        self.history = []  # lo recuperado no se deshace
        self.status.setText(text)
        self.update_header()
        self.update_cover()
        start = self.first_row()
        if start and not self.tree.selection():  # la vista previa sale sola, sin esperar a que se elija un título
            self.tree.selection_set(start)
            self.tree.see(start)

    def fill_tree(self, rows):
        """Llena la lista con los títulos e ilustraciones del libro, en su orden. rows: {fila: (tipo,
        título)} de las que ya tienen tipo; las demás salen como las dejó el análisis: los capítulos
        detectados como capítulo, los demás títulos ("El lado de…") como subtítulo, y las ilustraciones
        descartadas por el análisis (fondos de título, imágenes dentro del texto) como "Descartada"."""
        self.tree.delete(*self.tree.get_children())
        self.tree.pin(COVER, ("Portada", "Portada", "", self.cover_cell(), ""), ("image",))
        self.contents_row()
        blocks = self.book.blocks
        chapters = set(self.book.candidates)
        self.wide = {str(i) for i, b in enumerate(blocks) if b.kind == "image" and b.image and convert.is_wide(b.image)}
        heads = [i for i, b in enumerate(blocks) if b.kind == "heading"]
        on = {k for k in heads if rows.get(str(k), [ON if k in chapters else OFF])[0] == ON}
        # Un capítulo se lee con sus subtítulos: sus minutos llegan hasta el siguiente capítulo
        minutes = {k: convert.reading_minutes(blocks[k + 1:next((j for j in heads if j > k and (j in on or k not in on)),
                                                                len(blocks))]) for k in heads}
        for i, block in enumerate(blocks):
            if block.kind == "heading":
                # el nombre del índice del EPUB de entrada; si no, el texto del título
                mark, title = (ON if i in chapters else OFF), _block_title(block)
            elif block.kind == "image":
                double = " doble" if str(i) in self.wide else ""
                mark, title = (IMG if block.keep else DROPPED), f"Ilustración{double} · página {block.page}"
            elif block.kind == "break" and not convert.is_rule(block):  # separador de escena: su símbolo
                symbol = convert._plain(block).strip() or ("Adorno" if block.image else "Separador")
                mark, title = SEP, f"{symbol} · página {block.page}"
            else:
                continue
            mark, title, *done = rows.get(str(i), (mark, title))
            if block.kind == "heading":  # revisiones de antes guardaron «<b>…» como título
                title = _title_text(title)
            reading = f"{minutes[i]} min" if minutes.get(i) else ""
            self.tree.insert("", "end", iid=str(i), values=(mark, title, reading, block.page, done[0] if done else ""),
                             tags=(KIND_TAG[mark],))
        self.update_reviewed()
        self.update_quality()

    def is_image(self, item):
        return self.book is not None and self.book.blocks[int(item)].kind == "image"

    def is_title(self, item):
        return self.book is not None and self.book.blocks[int(item)].kind == "heading"

    def is_break(self, item):
        """Fila de un separador de escena del texto: solo se ve; se cambia con «Editar texto…»."""
        return self.book is not None and self.book.blocks[int(item)].kind == "break"

    def on_click(self, event):
        item = self.tree.identify_row(event.y)
        column = self.tree.identify_column(event.x)
        if column == "#1" and self.tree.identify_row(event.y, pinned=True) == CONTENTS:  # como la casilla «Índice»
            self.contents_var.set(not self.contents_var.get())
        if item and column == "#1" and self.tree.set(item, "inc") in self.cycle(item):
            self.set_kind(item, self.cycle(item)[self.tree.set(item, "inc")])
            self.update_reviewed()
            self.update_preview()
        self.drag_source = item if item and column == "#2" else None  # on_drop decide si se movió
        self.tree.drag_iid = self.drag_source if self.drag_source and not self.is_break(item) else None  # su guía

    def on_double_click(self, event):
        item = self.tree.identify_row(event.y)
        column = self.tree.identify_column(event.x)
        if item and column == "#1":  # Tk manda el segundo clic rápido aquí, no a on_click
            self.on_click(event)
        if item and column == "#2":
            self.edit_title(item)
        if self.tree.identify_row(event.y, pinned=True) == COVER:  # cambiarla, como «Portada…» de «Editar datos»
            self.choose_cover()

    def shortcut(self, kind):
        """Atajo (KEYS) sobre la fila seleccionada; se ignora si ese tipo no vale para la fila (C en una
        ilustración, D en una que no es doble)."""
        selected = self.tree.selection()
        if not selected:
            return
        item = selected[0]
        if kind in self.cycle(item) and kind != self.tree.set(item, "inc"):
            self.set_kind(item, kind)
            self.update_reviewed()
            self.update_preview()

    def edit_title(self, item):
        if self.is_image(item) or self.is_break(item):  # el nombre de una ilustración o un separador no se edita
            return
        self.tree.see(item)
        x, y, w, h = self.tree.bbox(item, "title")
        entry = QLineEdit(self.tree.set(item, "title"), self.tree.viewport())
        entry.setGeometry(x, y, w, max(h, entry.sizeHint().height()))
        entry.selectAll()
        entry.show()
        entry.setFocus()
        state = {"open": True}

        def save():
            if not state["open"]:  # ya cerrado por otro camino (scroll, Intro, Esc)
                return
            text = entry.text().strip()
            if text and text != self.tree.set(item, "title"):
                self.remember([self.row_state(item)])
                self.tree.set(item, "title", text)
            close()
            self.update_preview()

        def close():
            state["open"] = False
            entry.deleteLater()
            self.editor_save = None
            self.tree.setFocus()

        self.editor_save = save
        entry.editingFinished.connect(save)  # Intro o perder el foco
        QShortcut(QKeySequence("Escape"), entry, close, context=Qt.ShortcutContext.WidgetShortcut)
        self.title_entry = entry

    def on_scroll(self):
        # El cuadro de edición está colocado sobre una fila: si la lista se desplaza, se guarda y se cierra.
        if self.editor_save:
            self.editor_save()

    def generate(self):
        folder = self.settings.get("out_folder") or ""
        chosen = filedialog.asksaveasfilename(filetypes=[("EPUB", "*.epub")],
                                              initialdir=folder if folder and Path(folder).is_dir() else self.pdf.parent,
                                              initialfile=_epub_name(self.out_name(), self.kepub_var.get()))
        out = _epub_name(chosen, self.kepub_var.get())  # sin extensión, o .kepub.epub para el Kobo
        if not chosen or not _confirm_name(chosen, out):
            return
        chapters, removed, breaks = self.prepare_output()
        self.status.setText("Generando EPUB…")
        self.run(_write_checked, (self.book, chapters, out, removed, self.write_options(breaks)),
                 lambda problems: self.saved(out, problems), report=True, busy="Generando EPUB…",
                 partial=[convert.partial_path(out)])

    def fill_pdf_menu(self):
        """«PDF ▾»: el tamaño preferido (Opciones → PDF) primero."""
        labels = {"A5": "A5, para leer o imprimir", "e-reader 6″": "e-reader 6″", "A4": "A4"}
        preferred = self.pdf_size_var.get()
        self.pdf_menu.clear()
        for size in sorted(labels, key=lambda s: s != preferred):
            mark = " (preferido)" if size == preferred else ""
            self.pdf_menu.addAction(f"Generar PDF ({labels[size]}){mark}…", lambda size=size: self.generate_pdf(size))

    def generate_pdf(self, size):
        """«PDF ▾»: el libro maquetado en PDF al tamaño elegido (convert.PDF_SIZES), con las opciones del EPUB."""
        if not self.book:
            return
        chosen = filedialog.asksaveasfilename(filetypes=[("PDF", "*.pdf")], initialdir=self.pdf.parent,
                                              initialfile=f"{_out(self.pdf).name} ({size}).pdf")
        out = chosen if chosen.lower().endswith(".pdf") else chosen + ".pdf"
        if not chosen or not _confirm_name(chosen, out):
            return
        if Path(out).resolve() == Path(self.pdf).resolve():  # mismo nombre que el original: lo pisaría
            messagebox.showerror("Generar PDF", "Ese es el PDF original: elige otro nombre.")
            return
        chapters, removed, breaks = self.prepare_output()
        self.status.setText(f"Generando PDF {size}…")
        options = {**self.write_options(breaks), "whole_words": self.whole_words_var.get()}  # solo el PDF
        self.run(_write_pdf, (self.book, chapters, out, removed, options, size),
                 lambda problems: self.saved(out, problems), report=True, busy="Maquetando el PDF…",
                 partial=[convert.partial_path(out)])

    def prepare_output(self):
        """Los datos de la ventana al libro y la revisión guardada antes de generar: (capítulos, quitados, separadores)."""
        for name in BOOK_FIELDS:
            if hasattr(self, f"{name}_var"):
                setattr(self.book, name, getattr(self, f"{name}_var").get().strip())
        self.book.title = self.book.title or self.pdf.stem
        self.book.description = self.description
        if self.cover_choice and self.cover_choice[0] == "text":  # con el título y la serie de ahora
            self.book.cover = convert.text_cover(self.book)
        self.save_review()
        return _plan(self.book, self.rows())

    def separator_changed(self, parent=None):
        """«Icono propio»: la imagen que se elija (PNG o JPG), sin su fondo y hecha icono (convert.trace_svg). Sin
        imagen ni icono de antes, vuelve el separador de antes."""
        if self.separator_var.get() == "Icono propio":
            path = filedialog.askopenfilename(filetypes=[("Imagen", "*.png *.jpg *.jpeg")], title="Imagen del separador",
                                              parent=parent or self.root)
            svg = convert.trace_svg(Path(path).read_bytes()) if path and Path(path).is_file() else ""
            if path and not svg:
                messagebox.showwarning("Icono propio", "No se encontró ninguna figura en la imagen. Usa una con el "
                                       "fondo liso o transparente.", parent=parent or self.root)
            if svg:
                self.separator_icon_var.set(svg)
            elif not self.separator_icon_var.get():
                self.separator_var.set(self.separator_before)
        self.separator_before = self.separator_var.get()
        self.update_preview()

    def out_name(self):
        """Nombre que propone «Generar EPUB» (Opciones → Salida): el del PDF, el título o «Serie - Volumen N»."""
        how = self.settings.get("out_name", OUT_NAMES[0])
        title, series, volume = (self.title_var.get().strip(), self.series_var.get().strip(),
                                 self.volume_var.get().strip())
        name = (title if how == OUT_NAMES[1] else f"{series} - Volumen {volume}" if how == OUT_NAMES[2] and series
                and volume else "")
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name).strip(" .")  # lo que Windows no deja en un nombre
        return name or _out(self.pdf).name

    def write_options(self, breaks=()):
        """Opciones de write_epub según la ventana (ver _write_options)."""
        state = {name: var.get() for name, var in self.fields().items()}
        state["replacements"], state["alts"] = self.replacements, self.alts
        return _write_options(state, breaks)

    def saved(self, out, problems=(), of_book=True):
        """El EPUB o PDF out está escrito. of_book=False (el recopilatorio): no es el libro abierto, y sus avisos y
        su versión automática no van en él."""
        self.progress.setValue(0)  # la barra llena se quedaba tras el aviso de guardado
        self.status.setText(f"Guardado: {Path(out).name}" + (" (con avisos)" if problems else ""))
        if not of_book:
            self.result_dialog(out, problems)
            return
        self.last_problems = list(problems)
        self.save_review()  # el panel de serie muestra los avisos del último EPUB
        kind = "PDF" if str(out).lower().endswith(".pdf") else "EPUB"
        self.add_version(f"{kind} generado · {Path(out).name}", auto=True)  # lo publicado, para compararlo luego
        self.result_dialog(out, problems)

    def dialog(self, title, modal=False, parent=None):
        """Ventana secundaria con su columna; sin modal, la principal sigue usable (como Toplevel). parent: otra
        ventana encima de la que debe salir (el editor, que es modal)."""
        win = Dialog(parent or self.root)
        win.setWindowTitle(title)
        win.setModal(modal)
        win.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        column = QVBoxLayout(win)
        column.setContentsMargins(14, 12, 14, 12)
        column.setSpacing(8)
        return win, column

    def result_dialog(self, out, problems=()):
        """Tras generar: dónde quedó, cuánto pesa, qué avisos dio la revisión y, sin buscarlo, abrirlo, abrir
        su carpeta, copiarlo al lector o enviarlo al Kindle. Un solo diálogo (antes eran dos seguidos)."""
        problems = list(problems or ())
        is_pdf = str(out).lower().endswith(".pdf")
        is_file, kindle = Path(out).is_file(), self.profile_var.get() == "Kindle" and not is_pdf
        win, column = self.dialog("PDF listo" if is_pdf else "EPUB guardado con avisos" if problems else "EPUB listo")
        heading = _label("PDF maquetado." if is_pdf else "La revisión encontró avisos" if problems
                         else "Revisado: sin problemas.", "warn" if problems else "title")
        column.addWidget(heading)
        text = str(Path(out))  # el diálogo de guardar la da con «/»: en Windows, como en el Explorador
        if is_file:
            size = Path(out).stat().st_size
            text += f"\n{_size_text(size)}"
            if kindle and size > KINDLE_MAIL:
                text += "\nPasa de 50 MB: por correo no llega al Kindle; usa la web de Send to Kindle (hasta 200 MB)."
        info = _label(text, "muted")
        info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        column.addWidget(info)
        if problems:
            found = QPlainTextEdit("\n".join(problems[:15]) + ("\n…" if len(problems) > 15 else ""))
            found.setReadOnly(True)
            found.setMinimumSize(560, 120)
            column.addWidget(found)
        buttons = []
        if is_file:
            buttons.append(_button("Abrir PDF" if is_pdf else "Abrir EPUB", lambda: self.open_external(out, win),
                                   "primary"))
        buttons.append(_button("Abrir carpeta", lambda: self.open_external(out, win, folder=True)))
        if is_file:
            buttons.append(_button("Enviar al móvil", lambda: self.send_to_phone(out)))
            if _calibre():
                buttons.append(_button("Añadir a Calibre", lambda: self.add_to_calibre(out)))
            for root, label in _drives():
                if _reader_kind(root) == "kindle" and not is_pdf:  # el Kindle no lee EPUB por USB: Send to Kindle
                    kindle = True
                    column.addWidget(_label(f"{label} ({root[:2]}) es un Kindle: no lee EPUB por USB. "
                                            "Usa «Enviar a Kindle…».", "muted"))
                    continue
                buttons.append(_button(f"Copiar a {label} ({root[:2]})", lambda r=root: self.copy_to(out, r, win)))
            if kindle:
                buttons.append(_button("Enviar a Kindle…", lambda: self.send_to_kindle(out, win)))
        buttons.append(_button("Cerrar", win.close))
        column.addLayout(_row(*buttons, 1))
        if is_file and not is_pdf:  # en su propia fila: la de los botones ya llena 1093 px con un lector conectado
            check = _button("Validar con EPUBCheck", lambda: self.validate_epub(out))
            check.setToolTip("El validador oficial de EPUB del W3C: lo que aceptan las tiendas y los lectores exigentes")
            column.addLayout(_row(check, 1))
        win.show()
        self.result_window = win

    def validate_epub(self, out):
        """«Validar con EPUBCheck»: necesita Java; el programa se descarga una vez si el usuario quiere."""
        if not shutil.which("java"):
            messagebox.showinfo("EPUBCheck", "EPUBCheck necesita Java, que no está instalado en este equipo.\n\n"
                                "Instálalo gratis desde java.com y vuelve a pulsar «Validar con EPUBCheck».")
            return
        jar = _epubcheck_jar()
        if not jar.is_file():
            if messagebox.askyesno("EPUBCheck", "EPUBCheck es el validador oficial de EPUB del W3C. Hay que "
                                   "descargarlo una vez (33 MB, desde GitHub).\n\n¿Descargarlo ahora?"):
                self.run(_download_epubcheck, (str(SETTINGS.parent),), lambda _: self.validate_epub(out),
                         busy="Descargando EPUBCheck…")
            return
        self.status.setText("Validando con EPUBCheck…")
        self.run(_epubcheck, (str(jar), str(out)), lambda found: self.show_epubcheck(out, found),
                 busy="Validando con EPUBCheck…")

    def show_epubcheck(self, out, found):
        """Lo que dijo EPUBCheck: errores y advertencias con su archivo, o que el EPUB cumple el estándar."""
        errors = sum(level.startswith("Error") for level, _, _ in found)
        self.status.setText(f"EPUBCheck: {convert.counted(errors, 'error', 'errores')}, "
                            f"{convert.counted(len(found) - errors, 'advertencia')}")
        win, column = self.dialog("EPUBCheck")
        column.addWidget(_label(Path(out).name, "muted"))
        if not found:
            column.addWidget(_label("Sin errores ni advertencias: el EPUB cumple el estándar.", "title"))
        else:
            column.addWidget(_label(f"{convert.counted(errors, 'error', 'errores')} · "
                                    f"{convert.counted(len(found) - errors, 'advertencia')}", "warn" if errors else "title"))
            table = qtk.RowList(("level", "where", "message"), {"level": "Gravedad", "where": "Dónde",
                                                                "message": "Mensaje"}, {"level": 110, "where": 200})
            for n, row in enumerate(found):
                table.insert("", "end", iid=str(n), values=row)
            table.setMinimumSize(760, 260)
            column.addWidget(table, 1)
        column.addLayout(_row(1, _button("Cerrar", win.close, "primary")))
        win.show()
        self.epubcheck_window = win

    def copy_to(self, out, root, win):
        """Copia el EPUB al lector, en Books/ si la tiene, en el proceso de trabajo (USB lento)."""
        folder = Path(root) / "Books" if (Path(root) / "Books").is_dir() else Path(root)
        if self.run(shutil.copy2, (str(out), str(folder)),
                    lambda dest: (_record_send(out, "USB"), messagebox.showinfo("Copiado", f"EPUB copiado en:\n{dest}")),
                    busy="Copiando…"):
            win.close()

    def send_to_phone(self, out, ip=None):
        """Sirve el EPUB a la red local y muestra su QR: el móvil lo descarga sin cable. Al cerrar la
        ventana se apaga el servidor. ip: solo para las pruebas (127.0.0.1 no es una red local)."""
        if not ip and _lan_ip().startswith("127."):
            messagebox.showerror("Enviar al móvil", "No hay red local: conecta el equipo a la Wi-Fi.")
            return
        ip = ip or _lan_ip()
        try:
            server = _serve(out, ip)
        except OSError as e:
            messagebox.showerror("Enviar al móvil", f"No se pudo enviar: {e.strerror or e}")
            return
        url = f"http://{ip}:{server.server_port}{server.path}"
        png = io.BytesIO()
        segno.make(url).save(png, kind="png", scale=8, border=4)
        pixmap = QPixmap()
        pixmap.loadFromData(png.getvalue(), "PNG")
        win, column = self.dialog("Enviar al móvil")
        qr = QLabel()
        qr.setPixmap(pixmap)
        qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(qr)
        link = _label(url, "title")
        link.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        column.addWidget(link)
        column.addWidget(_label("Escanea el código con el móvil conectado a la misma Wi-Fi, o escribe la dirección\n"
                                "en su navegador. Si Windows pregunta en el Firewall, permite el acceso.\n"
                                f"Al cerrar esta ventana, o a los {SEND_MINUTES} minutos, deja de enviarse.", "muted"))
        column.addLayout(_row(1, _button("Cerrar", win.close)))
        win.finished.connect(lambda _: (server.shutdown(), server.server_close(),
                                        server.downloaded and _record_send(out, "Wi-Fi")))
        win.show()
        qtk.after(SEND_MINUTES * 60_000, lambda: shiboken6.isValid(win) and win.isVisible() and win.close())
        self.phone_window = win, server

    def add_to_calibre(self, out):
        """calibre.exe con un libro lo añade a su biblioteca, también si Calibre ya está abierto."""
        try:
            subprocess.Popen([_calibre(), str(out)])
        except OSError as e:
            messagebox.showerror("Añadir a Calibre", f"No se pudo abrir Calibre: {e.strerror or e}")
            return
        _record_send(out, "Calibre")
        self.status.setText("Enviado a Calibre: lo añade a su biblioteca.")

    def send_to_kindle(self, out, win):
        """Send to Kindle no tiene API pública: se abre su web y el archivo, seleccionado, para arrastrarlo."""
        webbrowser.open("https://www.amazon.com/sendtokindle")
        _record_send(out, "Kindle (web)")  # la web de Amazon no confirma el envío: se apunta al abrirla
        self.open_external(out, win, folder=True)

    def open_external(self, path, win=None, folder=False):
        # Making Magic: Windows y los visores (Foxit) no abren una ruta de MAX_PATH o más; daban «no se encuentra»
        target, long_path = Path(path), len(str(path)) >= MAX_PATH
        try:
            if folder:
                # con ruta larga, «/select» tampoco encuentra el archivo: la carpeta a secas
                args = ([EXPLORER, str(target.parent)] if long_path and target.is_file()
                        else [EXPLORER, f"/select,{target}"] if target.is_file() else [EXPLORER, str(target)])
                subprocess.Popen(args)
            else:
                os.startfile(path)
        except OSError as e:
            if long_path and target.exists():
                if messagebox.askyesno(
                        "No se pudo abrir",
                        f"El archivo se guardó bien, pero su ruta tiene {len(str(path))} caracteres y Windows no "
                        f"abre archivos con rutas de {MAX_PATH} o más: los lectores de PDF y EPUB tampoco lo abren ahí.\n\n"
                        f"{path}\n\nMuévelo a una carpeta más corta o acorta su nombre. ¿Abrir su carpeta?"):
                    subprocess.Popen([EXPLORER, str(target.parent)])
            else:
                messagebox.showerror("Error", f"No se pudo abrir:\n{path}\n\n{e}")
        if win is not None:
            win.close()

    def load_settings(self):
        """Preferencias de la sesión anterior: opciones, tema, última carpeta y PDF recientes."""
        try:
            self.settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
            if not isinstance(self.settings, dict):
                raise ValueError
        except (OSError, ValueError):
            self.settings = {}
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
        chosen = self.settings.get("theme")
        self.theme_var.set(chosen if chosen in theme.CHOICES else "Sistema")
        self.fill_recent()

    def option_choices(self):
        """Valores de las listas de «Opciones»."""
        return {"editor_undo": list(UNDO_SCOPES), "editor_side": list(EDITOR_SIDES),
                "profile": list(PROFILES), "lang": LANGUAGES, "pdf_size": list(convert.PDF_SIZES),
                "theme": theme.CHOICES, "device": list(DEVICES), "note_style": list(convert.NOTE_STYLES),
                "heading_font": convert.HEADING_FONT_CHOICES + [HEADING_OWN], "box_style": list(convert.BOX_STYLES),
                # lo de la barra de abajo que pide algo más (un archivo, un texto) se queda en ella
                "style": ([convert.ORIGINAL] if self.book and self.book.origin else []) + list(convert.STYLES),
                "separator": [c for c in convert.SEPARATOR_CHOICES if c != "Icono propio"],
                "font": [c for c in FONT_CHOICES if c != "Otra…"], "accent": list(theme.ACCENTS),
                "ui_font": list(theme.UI_SIZES), "out_name": OUT_NAMES}

    def app_factory(self):
        """Lo de «La aplicación» como viene de fábrica: para «Restablecer» en Opciones."""
        return {**EDITOR_DEFAULTS, **copy.deepcopy(APP_DEFAULTS), "theme": "Sistema", "device": list(DEVICES)[0],
                "dark": False, "echoes": False, "watch": ""}

    @staticmethod
    def quality_kinds():
        """[(tipo, título, gravedad)] de cada aviso del informe, para Opciones → Informe de calidad. Los ecos tienen
        su casilla aparte."""
        return [k for k in convert.QUALITY_KINDS if k[0] != "echo"] + [
            ("comment", "Comentario del revisor", "Revisar"), ("toc", "Falta un capítulo del índice", "Revisar"),
            ("toc-extra", "Capítulo que no está en el índice", "Menor")] + grammar.KINDS

    @staticmethod
    def quality_levels():
        return {level: LEVEL_PLURAL[level] for level in convert.LEVELS}

    def open_options(self, category=None, scope=None, on_close=None):
        """«Opciones»: en category, o en la última vista."""
        self.options_window = options.OptionsDialog(self, self.option_choices(), PROFILES,
                                                    category or self.settings.get("options_page"), scope, on_close)
        return self.options_window

    def app_values(self):
        """Lo de «La aplicación» en Opciones, copiado: se edita sin tocar nada hasta Aceptar."""
        return {"theme": self.theme_var.get(), "device": self.settings.get("device", self.device_var.get()),
                "dark": self.settings.get("dark") is True, "echoes": self.echoes_var.get(), "watch": self.watch,
                "translate": copy.deepcopy(self.settings.get("translate") or {}),
                **{key: self.settings.get(key, value) for key, value in EDITOR_DEFAULTS.items()},
                **{key: copy.deepcopy(self.settings.get(key, value)) for key, value in APP_DEFAULTS.items()},
                "words": list(self.words) if self.book else None}  # el diccionario del libro (y su serie)

    def accept_options(self, defaults, values):
        """Aceptar en Opciones: «Libros nuevos» y la aplicación a ajustes.json, el libro a su revisión."""
        self.settings["defaults"] = dict(defaults)
        self.settings.update(device=values["device"], dark=values["dark"], echoes=values["echoes"])
        self.settings.update({key: values[key] for key in EDITOR_DEFAULTS})  # el editor abierto los lee al usarlos
        look = (self.settings.get("accent"), self.settings.get("ui_font"))
        report = (self.settings.get("quality_off"), self.settings.get("quality_levels"))
        self.settings.update({key: values[key] for key in APP_DEFAULTS})
        if values.get("words") is not None and self.book:
            self.words = list(values["words"])
        if look != (values["accent"], values["ui_font"]):
            self.apply_theme()
        if report != (values["quality_off"], values["quality_levels"]):
            self.update_quality()
        translation = self.settings.setdefault("translate", {})
        translation.clear()  # el mismo dict: Traducir, si está abierto, lo tiene y ve lo nuevo
        translation.update(values["translate"])
        open_translation = getattr(self, "translate_window", None)
        if open_translation and shiboken6.isValid(open_translation) and hasattr(open_translation, "parts"):
            open_translation.parts["reload"]()  # Traducir abierto: el motor y la clave nuevos, no los de antes
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
        look = {key: values[key] for key in ("accent", "ui_font")}  # se ven al elegirlos: Cancelar los devuelve
        if self.theme_var.get() != values["theme"] or any(self.settings.get(k) != v for k, v in look.items()):
            self.theme_var.set(values["theme"])
            self.settings.update(look)
            self.apply_theme()
        if changed and self.book:
            self.update_preview()

    def ask_folder(self):
        return filedialog.askdirectory(initialdir=self.settings.get("folder"), title="Carpeta que vigilar")

    def ask_font_file(self):
        return filedialog.askopenfilename(filetypes=[("Fuente", "*.ttf *.otf")])

    def font_changed(self):
        """«Otra…» pide un archivo de fuente; sin elegir ninguno, se queda la letra de antes."""
        if self.font_var.get() == "Otra…":
            path = self.ask_font_file()
            if path:
                self.font_file_var.set(path)
                self.show_font(Path(path).name)
            self.font_var.set(Path(path).name if path else self.font_last)
        self.font_last = self.font_var.get()
        self.update_preview()

    def show_font(self, name):
        """Pone la fuente propia en el combo, antes de «Otra…», y la deja elegida."""
        if self.font_box.findText(name) < 0:
            self.font_box.insertItem(self.font_box.count() - 1, name)
        self.font_box.setCurrentText(name)

    def offer_original(self, epub):
        """«Original» en el desplegable de estilo solo con un EPUB de entrada (con un PDF no hay aspecto original)."""
        at = self.style_box.findText(convert.ORIGINAL)
        if epub and at < 0:
            self.style_box.insertItem(0, convert.ORIGINAL)
        elif not epub and at >= 0:
            if self.style_var.get() == convert.ORIGINAL:
                self.style_var.set("Clásico")
            self.style_box.removeItem(at)
        self.style_box.setCurrentText(self.style_var.get())

    def new_book_options(self):
        """Opciones con las que abre un libro sin revisión ni serie: «Libros nuevos» sobre los valores de fábrica."""
        options = {**self.factory, **self.settings.get("defaults", {})}
        options["separator"] = convert.separator_choice(options.get("separator", ""))  # «Original del PDF» de antes
        return options

    def save_settings(self):
        self.settings["theme"] = self.theme_var.get()
        try:
            SETTINGS.parent.mkdir(parents=True, exist_ok=True)
            _write_json(SETTINGS, self.settings)
        except OSError:
            pass  # sin preferencias guardadas la próxima vez; nada más

    def fill_recent(self):
        """Menú «Recientes» y, en la pantalla vacía, una tarjeta por tomo: portada, nombre, PDF o EPUB, estado y
        fecha de la última revisión."""
        self.recent_menu.clear()
        self.recent_list.clear()
        recent = [p for p in self.settings.get("recent", []) if isinstance(p, str)]
        for path in recent:
            self.recent_menu.addAction(Path(path).name, lambda p=path: self.open_path(p))
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(path)
            card = self.recent_card(path)
            item.setSizeHint(card.sizeHint())
            self.recent_list.addItem(item)
            self.recent_list.setItemWidget(item, card)
        missing = [(p, str(_thumb_path(p))) for p in recent if p not in self.thumbs_tried and Path(p).is_file()
                   and not _thumb_path(p).is_file()]
        if missing and self.recent_thumbs is None:
            self.thumbs_tried.update(p for p, _ in missing)  # un archivo que no da miniatura no se reintenta
            self.recent_thumbs = _start_thumbs(missing)
            if self.recent_thumbs:
                qtk.after(300, self.wait_thumbs)
        self.recent_list.setVisible(bool(recent))
        self.recent_empty.setVisible(not recent)
        if not recent:
            self.recent_menu.addAction("(ninguno)").setEnabled(False)

    def recent_card(self, path):
        """Tarjeta de un tomo reciente. La portada es la miniatura guardada al abrirlo (save_thumb): abrir cada PDF
        al arrancar para sacarla sería lento."""
        path, colors = Path(path), theme.colors(self.dark)
        found = path.is_file()
        status = _volume_status(path) if found else "No se encuentra"
        color = {"EPUB generado": colors["ok"], "Sin revisar": colors["muted"], "No se encuentra": colors["warn"]}.get(
            status, theme.level_colors(self.dark)["Revisar"])
        card = QWidget()
        thumb = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        thumb.setFixedSize(44, 62)
        pixmap = QPixmap(str(_thumb_path(path))) if _thumb_path(path).is_file() else QPixmap()
        if pixmap.isNull():
            thumb.setText("🗎")
            thumb.setStyleSheet(f"font-size: 20pt; background: {colors['base']}; border: 1px solid {colors['line']};")
        else:
            thumb.setPixmap(pixmap.scaled(44, 62, Qt.AspectRatioMode.KeepAspectRatio,
                                          Qt.TransformationMode.SmoothTransformation))
        name = QLabel(path.stem)
        name.setStyleSheet("font-weight: 600;")
        kind = QLabel("EPUB" if path.suffix.lower() == ".epub" else "PDF")  # Maryoku 01: el PDF y su EPUB, no repetidos
        kind.setStyleSheet(f"color: {colors['muted']}; border: 1px solid {colors['line']}; border-radius: 3px; "
                           "padding: 0 4px; font-size: 8pt;")
        when = ""
        if found and _review_path(path).is_file():
            when = f" · revisado el {datetime.fromtimestamp(_review_path(path).stat().st_mtime):%d/%m/%Y}"
        state = QLabel(f'<span style="color:{color}">●</span> {html.escape(status)}{when}')
        state.setObjectName("muted")
        texts = QVBoxLayout()
        texts.setSpacing(2)
        texts.addLayout(_row(name, kind, 1))
        texts.addWidget(state)
        texts.addStretch(1)
        layout = _row(thumb, texts, margins=(6, 6, 6, 6), spacing=10)
        layout.setStretch(1, 1)
        card.setLayout(layout)
        return card

    def wait_thumbs(self):
        """Al acabar las miniaturas, las tarjetas las muestran (si sigue la pantalla de inicio)."""
        if self.closed or self.recent_thumbs is None:
            return
        if self.recent_thumbs.is_alive():
            qtk.after(300, self.wait_thumbs)
            return
        self.recent_thumbs = None
        if self.book is None:
            self.fill_recent()

    def recent_context(self, pos):
        """Clic derecho en un reciente: abrir su carpeta o quitarlo de la lista (el archivo no se toca)."""
        item = self.recent_list.itemAt(pos)
        path = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not path:
            return
        menu = QMenu(self.recent_list)
        menu.addAction("Abrir carpeta", lambda: self.open_external(path, folder=True)).setEnabled(Path(path).exists())
        menu.addAction("Quitar de recientes", lambda: self.forget_recent(path))
        menu.popup(self.recent_list.viewport().mapToGlobal(pos))
        self.recent_popup = menu

    def forget_recent(self, path):
        self.settings["recent"] = [p for p in self.settings.get("recent", []) if p != path]
        self.save_settings()
        self.fill_recent()

    def save_thumb(self):
        """Miniatura de la portada del libro abierto para su tarjeta de recientes."""
        if not (self.book and self.pdf and self.book.cover):
            return
        image = QImage.fromData(self.book.cover)
        if image.isNull():
            return
        target = _thumb_path(self.pdf)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            image.scaledToHeight(124, Qt.TransformationMode.SmoothTransformation).save(str(target), "JPG", 85)
        except OSError:
            pass  # sin miniatura, la tarjeta muestra el icono

    def edit_replacements(self):
        if self.raise_tool("replacements_window"):
            return
        win, column = self.dialog("Reemplazos")
        column.addWidget(QLabel("Una regla por línea:  buscar -> reemplazo\nCambia palabras completas en textos y "
                                "títulos. Con «re:» delante, expresión regular (re:Onii-?chan -> Onii-chan; \\1 en el "
                                "reemplazo).\nPasan a los demás tomos de la serie."))
        text = QPlainTextEdit("\n".join(f"{find} -> {new}" for find, new in self.replacements))
        text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        text.setMinimumSize(560, 260)
        column.addWidget(text, 1)
        text.setFocus()

        def bad_rules(rules):
            return [f"{find}: {n}" for find, n in convert.count_matches(self.book or convert.Book("", "", b"", [], []), rules)
                    if isinstance(n, str)]

        def save():
            rules = _parse_replacements(text.toPlainText())
            bad = bad_rules(rules)
            if bad:
                messagebox.showerror("Reemplazos", "Corrige estas reglas:\n" + "\n".join(bad), parent=win)
                return
            self.replacements = rules
            win.close()
            self.update_preview()

        def count():
            """Cuántas veces aplica cada regla en el libro, antes de aceptar."""
            rules = _parse_replacements(text.toPlainText())
            if not self.book or not rules:
                return
            lines = [f"{find}: {n if isinstance(n, str) else convert.counted(n, 'vez', 'veces')}"
                     for find, n in convert.count_matches(self.book, rules)]
            messagebox.showinfo("Coincidencias", "\n".join(lines), parent=win)

        def variants():
            """Propone una regla por cada nombre escrito de varias formas: las otras -> la más usada."""
            groups = convert.name_variants(self.book) if self.book else []
            have = {find for find, _ in _parse_replacements(text.toPlainText())}
            rules = [f"{other} -> {group[0]}" for group in groups for other in group[1:] if other not in have]
            if not rules:
                messagebox.showinfo("Variantes de nombres", "No se encontraron nombres escritos de varias formas.",
                                    parent=win)
                return
            lead = "\n" if text.toPlainText().strip() else ""
            text.setPlainText(text.toPlainText() + f"{lead}# Variantes encontradas: borra las que no sean errores\n"
                              + "\n".join(rules))

        column.addLayout(_row(_button("Buscar variantes de nombres", variants), _button("Contar coincidencias", count),
                              1, _button("Aceptar", save, "primary")))
        win.show()
        self.replacements_window = win, text, save

    def edit_glossary(self):
        """Glosario de la serie: los nombres y términos como deben ir. El informe avisa de las formas parecidas."""
        if self.raise_tool("glossary_window"):
            return
        win, column = self.dialog("Glosario")
        column.addWidget(QLabel("Un nombre o término por línea, como debe ir escrito («Emilia», «Reinhard»). El informe "
                                "de calidad avisa\nde las formas parecidas («Emilía», «Reinhart»). Lo que va tras # "
                                "no cuenta. Pasa a los demás tomos de la serie."))
        text = QPlainTextEdit("\n".join(self.glossary))
        text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        text.setMinimumSize(560, 260)
        column.addWidget(text, 1)
        text.setFocus()

        def terms():
            return list(dict.fromkeys(t for t in (line.split("#")[0].strip() for line in text.toPlainText().splitlines())
                                      if t))

        def add_names():
            """Añade los nombres propios de este tomo que salen 5 veces o más y aún no están."""
            have = set(terms())
            names = sorted(((n, f) for f, n in (convert.proper_names(self.book) if self.book else {}).items()
                            if n >= 5 and f not in have), reverse=True)
            if not names:
                messagebox.showinfo("Glosario", "No hay nombres nuevos que salgan 5 veces o más en este tomo.", parent=win)
                return
            lead = "\n" if text.toPlainText().strip() else ""
            text.setPlainText(text.toPlainText() + lead + "\n".join(f"{f}  # {convert.counted(n, 'vez', 'veces')}"
                                                                    for n, f in names))

        def save():
            self.glossary = terms()
            win.close()
            self.update_quality()

        column.addLayout(_row(_button("Añadir nombres de este tomo", add_names), 1, _button("Aceptar", save, "primary")))
        win.show()
        self.glossary_window = win, text, save

    def edit_text(self):
        """Abre el editor en la sección de la fila seleccionada (la de su título, si es una ilustración o un
        separador), con el cursor en esa fila."""
        if not self.book:
            return None
        item = (self.tree.selection() or (self.first_row(),))[0]
        if not item:
            return None
        return TextEditor(self, item, *self.section_bounds(int(item)))

    def first_row(self):
        """Fila con la que empieza la vista previa: el primer capítulo, o la primera fila si no hay ninguno."""
        items = self.tree.get_children()
        return next((i for i in items if self.is_title(i) and self.tree.set(i, "inc") == ON), items[0] if items else None)

    def section_heads(self):
        """Títulos que abren sección en el editor: los capítulos. Subtítulos, separadores («ªª») y lo quitado
        quedan dentro del capítulo donde están; lo de antes del primero es el inicio del libro."""
        return [k for k, b in enumerate(self.book.blocks) if b.kind == "heading"
                and self.tree.exists(str(k)) and self.tree.set(str(k), "inc") == ON]

    def section_bounds(self, k):
        """Bloques de la sección del bloque k: de su título (o del principio del libro) al siguiente."""
        heads = self.section_heads()
        start = max((j for j in heads if j <= k), default=0)
        end = min((j for j in heads if j > start), default=len(self.book.blocks))
        return start, end

    def issue_places(self, issues):
        """Dónde está cada aviso, en una pasada por el libro: [(corto, completo)], como «Capítulo 0 · párr. 4» y
        «Capítulo 0: El discípulo… › Lado de Teto · párrafo 4 de 38 · pág. 4». El número de párrafo es el de la
        barra de estado del editor: solo párrafos de texto."""
        blocks, heads = self.book.blocks, set(self.section_heads())
        where, totals, start, sub, n = [], {}, 0, None, 0
        for k, b in enumerate(blocks):
            if k in heads:
                start, sub, n = k, None, 0
            elif b.kind == "heading":
                sub = k
            elif b.kind == "p":
                plain = convert._plain(b).strip()
                n += bool(plain) and not convert.SCENE_BREAK.fullmatch(plain)
            where.append((start, sub, n))
            totals[start] = n
        title = (lambda j: self.tree.set(str(j), "title") if self.tree.exists(str(j)) else convert._plain(blocks[j]))
        places = []
        for issue in issues:
            if issue.block is None or issue.block >= len(blocks):
                places.append(("", f"Pág. {issue.page} del PDF: texto que no entró en el libro"))
                continue
            start, sub, n = where[issue.block]
            chapter = title(start) if start in heads else "Inicio del libro"
            short = chapter.split(":")[0].strip() if ":" in chapter else chapter
            if issue.block == start or blocks[issue.block].kind == "heading":
                spot = "el título"
            else:
                spot = f"párrafo {n} de {totals[start]}"
            places.append((f"{short} · " + (f"párr. {n}" if spot.startswith("párrafo") else "título"),
                           chapter + (f" › {title(sub)}" if sub is not None and sub != issue.block else "")
                           + f" · {spot} · pág. {issue.page}"))
        return places

    def edit_issue(self, issue):
        """Abre el editor en el aviso: su capítulo, su párrafo y el trozo exacto elegido, con su barra."""
        if not self.book:
            return None
        if issue.block is None or issue.block >= len(self.book.blocks):
            return self.edit_text()  # texto que no entró en el libro: la sección elegida en la lista
        editor = self.text_editor
        if editor is None or not shiboken6.isValid(editor.win):
            rows = self.tree.get_children()
            if not rows:  # libro sin títulos ni ilustraciones: el editor se abre siempre desde una fila
                messagebox.showinfo("Abrir en el editor", "El libro no tiene ninguna fila en la lista, y el editor se "
                                    "abre por capítulos: este libro aún no tiene ninguno.")
                return None
            start, end = self.section_bounds(issue.block)
            item = next((str(j) for j in range(issue.block, -1, -1) if self.tree.exists(str(j))), rows[0])
            editor = TextEditor(self, item, start, end)
        editor.win.raise_()
        editor.goto_issue(issue)
        return editor

    def apply_edit(self, edit, item):
        self.apply_edits([edit], item)

    def apply_edits(self, edits, item, titles=None, follow=False, kinds=None, retype=None, heads=()):
        """Aplica textos editados: cambia los bloques, renumera la lista conservando los tipos elegidos
        y los apunta para la revisión. Guarda una copia de antes para deshacerlos de una vez con Ctrl+Z.
        Varios cambios van de atrás adelante: así los índices de los siguientes siguen valiendo.
        titles: {fila: título nuevo} de títulos renombrados en el editor (conservan su tipo). follow: item es
        la fila de antes de los cambios y se vuelve a elegir donde quede. kinds: por cada edición, el tipo
        (Capítulo/Subtítulo) de sus títulos, en orden. retype: {fila: tipo} de títulos que solo cambian de tipo.
        heads: [(bloque, (archivo, antes, después))] cabeceras editadas en «Código HTML»; van antes que los textos,
        con los índices de antes, y edit_section las deja con su capítulo."""
        rows = self.rows()
        self.remember({"blocks": self.book.blocks, "candidates": self.book.candidates, "rows": rows,
                             "edits": len(self.edits), "item": item})  # edit_section crea listas nuevas
        rows = {k: [v[0], titles[k], *v[2:]] if titles and k in titles else v for k, v in rows.items()}
        rows = {k: [retype[k], *v[1:]] if retype and k in retype else v for k, v in rows.items()}
        for k, shell in heads:
            convert.set_shell(self.book, k, shell)
            self.edits.append(["head", k, *shell])
        for n, edit in enumerate(edits):
            mapping = convert.edit_section(self.book, *edit, _images())
            self.edits.append(list(edit))
            rows = {str(mapping[int(k)]): v for k, v in rows.items() if int(k) in mapping}
            if kinds and kinds[n]:  # títulos escritos en el editor: con el tipo elegido en la cinta
                count = sum(1 for line in edit[2].splitlines() if line.strip())
                heads = [j for j in range(edit[0], edit[0] + count) if self.book.blocks[j].kind == "heading"]
                for j, kind in zip(heads, kinds[n]):
                    if kind in NEXT:  # el editor da Capítulo o Subtítulo; la traducción, el que tenía la fila
                        rows[str(j)] = [kind, _block_title(self.book.blocks[j]), ""]
            if follow and item is not None:
                item = str(mapping[int(item)]) if int(item) in mapping else None
        self.fill_tree(rows)
        if item is not None:  # None: libro sin títulos ni ilustraciones, la lista está vacía
            self.tree.selection_set(item)
            self.tree.see(item)
        self.update_preview()

    def apply_fixes(self, edits, message):
        """Arreglos de un bloque cada uno, de una vez (Ctrl+Z los deshace juntos), con la sección del primero
        seleccionada."""
        edits = sorted(edits, key=lambda e: -e[0])
        item = next((str(j) for j in range(edits[-1][0], -1, -1)
                     if self.tree.exists(str(j)) and self.is_title(str(j))), None)
        self.apply_edits(edits, item or next(iter(self.tree.get_children()), None))
        self.status.setText(message)

    def replace_found(self, every=False):
        """Cambia lo buscado por «Reemplazar por» en el párrafo del resultado elegido o en todos los párrafos.
        Solo palabras completas, como las reglas: «Tino» no cambia dentro de «destino»."""
        term, new, row = self.find_entry.text().strip(), self.replace_entry.text().strip(), self.find_results.currentRow()
        if not self.book or len(term) < 2 or not (every or 0 <= row < len(self.matches)):
            return
        edits = convert.replace_text(self.book, term, new, word=True,
                                     blocks=None if every else [self.matches[row][0]])
        if not edits:
            self.status.setText("Nada que reemplazar: solo se cambian palabras completas de los párrafos; "
                                "los títulos, en la lista (F2)")
            return
        self.apply_fixes(edits, f"{convert.counted(len(edits), 'párrafo cambiado', 'párrafos cambiados')} · Ctrl+Z para deshacer")
        self.search_book()

    def save_rule(self):
        """Lo buscado y su reemplazo como regla de «Reemplazos»: vale para todos los tomos de la serie."""
        rule = [self.find_entry.text().strip(), self.replace_entry.text().strip()]
        if rule[0] and rule not in self.replacements:
            self.replacements.append(rule)
            self.status.setText(f"Regla «{rule[0]} -> {rule[1]}» guardada en Reemplazos: vale para toda la serie")
            self.update_preview()

    def spelling(self):
        """Revisa la ortografía en el proceso de trabajo: cargar el diccionario tarda unos segundos."""
        if not self.book:
            return
        if not self.spanish():
            messagebox.showinfo("Ortografía", f"La ortografía solo revisa libros en español, y este está en "
                                              f"{translate.LANGS.get(self.lang_var.get().strip(), self.lang_var.get().strip()).lower()} "
                                              f"(Herramientas › Opciones › Idioma del libro).")
            return
        texts = [convert._plain(b) for b in self.book.blocks if b.kind == "p"]
        if self.run(convert.unknown_words, (texts, self.words, bool(self.settings.get("spell_caps"))),
                    self.spelling_dialog):
            self.status.setText("Revisando la ortografía…")
        else:  # el menú no se desactiva como los botones
            self.status.setText("Espera a que termine el trabajo en curso")

    def spelling_dialog(self, found):
        """Palabras que no están en el diccionario (convert.unknown_words): cada aparición con su página y su frase
        entera, la palabra resaltada; las sugerencias como botones, el cambio visto antes de hacerlo, y cambiarla
        en esa aparición o en todo el libro, ignorarla en esta sesión o añadirla al diccionario de la serie. El
        editor de texto las subraya."""
        self.unknown = {w for w, _, _ in found}
        self.status.setText(f"Ortografía: {convert.counted(len(found), 'palabra que no está', 'palabras que no están')} "
                            "en el diccionario")
        self.close_tools(["spelling_window"])  # la búsqueda nueva sustituye a la anterior
        win, column = self.dialog("Ortografía")
        win.resize(980, 600)
        column.addWidget(_label("Palabras que no están en el diccionario, las más repetidas primero. "
                                + ("También las que empiezan en mayúscula (Opciones → Ortografía)."
                                   if self.settings.get("spell_caps") else
                                   "Las que empiezan en mayúscula no se revisan: suelen ser nombres."), "muted"))
        panes = QSplitter(Qt.Orientation.Horizontal)
        column.addWidget(panes, 1)
        left = QWidget()
        left_col = QVBoxLayout(left)
        left_col.setContentsMargins(0, 0, 0, 0)
        search = QLineEdit(placeholderText="Buscar palabra")
        search.textChanged.connect(lambda: fill())
        table = qtk.RowList(("word", "count"), {"word": "Palabra", "count": "Veces"}, {"count": 60}, stretch="word")
        progress = _label("", "muted")
        left_col.addWidget(search)
        left_col.addWidget(table, 1)
        left_col.addWidget(progress)
        panes.addWidget(left)
        right = QWidget()
        side = QVBoxLayout(right)
        side.setContentsMargins(theme.SPACE["section"], 0, 0, 0)
        side.addWidget(_label("Dónde sale · clic: verla en las vistas previas", "muted"))
        places = QListWidget()
        places.setWordWrap(True)
        places.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        side.addWidget(places, 1)
        side.addWidget(_label("Cambiar por", "muted"))
        chips_row = QHBoxLayout()
        chips_row.setSpacing(theme.SPACE["gap"])
        side.addLayout(chips_row)
        fix = QLineEdit(placeholderText="Otra palabra: escríbela aquí")
        side.addWidget(fix)
        preview = QLabel()
        preview.setWordWrap(True)
        preview.setTextFormat(Qt.TextFormat.RichText)
        side.addWidget(preview)
        one_btn = _button("Cambiar esta", lambda: change(only=True))
        one_btn.setToolTip("Solo en el párrafo elegido en «Dónde sale»")
        change_btn = _button("Cambiar todas", lambda: change(), "primary")
        ignore_btn = _button("Ignorar", lambda: ignore())
        ignore_btn.setToolTip("No la vuelve a enseñar mientras la aplicación siga abierta; no se añade al diccionario")
        add_btn = _button("Añadir al diccionario", lambda: add())
        side.addLayout(_row(one_btn, change_btn, 1, ignore_btn, add_btn))
        panes.addWidget(right)
        panes.setSizes([300, 680])
        state = {"found": list(found), "places": [], "total": len(found), "shown": []}
        colors, tags = theme.colors(self.dark), theme.diff_tags(self.dark)
        mark = tags["old"]

        def fill(select=0):
            table.delete(*table.get_children())
            wanted = search.text().strip().lower()
            state["shown"] = [n for n, (word, _, _) in enumerate(state["found"]) if wanted in word.lower()]
            for n in state["shown"]:
                word, count, _ = state["found"][n]
                table.insert("", "end", iid=str(n), values=(word, count))
            win.setWindowTitle(f"Ortografía · {convert.counted(len(state['found']), 'palabra')}")
            done_count = state["total"] - len(state["found"])
            progress.setText(f"Revisadas {done_count} de {state['total']}")
            if state["shown"]:
                item = str(state["shown"][min(select, len(state["shown"]) - 1)])
                table.selection_set(item)
                table.see(item)
            else:
                pick()

        def chosen():
            selected = table.selection()
            return state["found"][int(selected[0])] if selected else None

        def sentence(plain, start, end):
            """La frase de la palabra: del punto de antes al de después (como mucho 120 letras a cada lado)."""
            a = max((m.end() for m in re.finditer(r"[.!?…]\s+", plain[:start])), default=0)
            stop = re.search(r"[.!?…](?=\s|$)", plain[end:])
            b = end + stop.end() if stop else len(plain)
            a, b = max(a, start - 120), min(b, end + 120)
            return ("…" if a > 0 else "") + plain[a:b] + ("…" if b < len(plain) else ""), start - a + (1 if a else 0)

        def place_html(page, text, at, word):
            chip = (f'<span style="background-color: {colors["sel"]}; color: {colors["ink"]};">&nbsp;pág. {page}'
                    '&nbsp;</span>&nbsp; ')
            return (chip + html.escape(text[:at]) + f'<span style="background-color: {mark["background"]}; color: '
                    f'{mark["foreground"]}; font-weight: 600;">{html.escape(text[at:at + len(word)])}</span>'
                    + html.escape(text[at + len(word):]))

        def pick():
            entry = chosen()
            places.clear()
            while chips_row.count():
                old = chips_row.takeAt(0).widget()
                if old:
                    old.hide()  # hasta borrarse seguía a la vista, debajo de las nuevas
                    old.deleteLater()
            fix.clear()
            state["places"] = []
            for btn in (one_btn, change_btn, ignore_btn, add_btn):
                btn.setEnabled(bool(entry))
            preview.clear()
            if not entry:
                return
            word, _, suggestions = entry
            for suggestion in (suggestions or [])[:6]:
                chip = QPushButton(suggestion, objectName="chip", checkable=True)
                chip.clicked.connect(lambda _=False, s=suggestion: fix.setText(s))
                chips_row.addWidget(chip)
            chips_row.addStretch(1)
            pattern = re.compile(rf"(?<!\w){re.escape(word)}(?!\w)", re.I)
            for k, block in enumerate(self.book.blocks):
                plain = convert._plain(block) if block.kind == "p" else ""
                m = pattern.search(plain)
                if m and len(state["places"]) < 200:
                    text, at = sentence(plain, m.start(), m.end())
                    state["places"].append((k, block.page, word, text, at))
                    item = QListWidgetItem()
                    label = QLabel(place_html(block.page, text, at, m[0]))
                    label.setWordWrap(True)
                    label.setTextFormat(Qt.TextFormat.RichText)
                    label.setContentsMargins(4, 3, 4, 3)
                    label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)  # el clic, a la fila
                    places.addItem(item)
                    places.setItemWidget(item, label)
                    item.setSizeHint(QSize(0, label.heightForWidth(max(200, places.viewport().width() - 12)) + 6))
            fix.setText(suggestions[0] if suggestions else "")
            if places.count():
                places.setCurrentRow(0)
            show_preview()

        def show_preview():
            row = places.currentRow()
            new = fix.text().strip()
            for n in range(chips_row.count()):
                chip = chips_row.itemAt(n).widget()
                if chip:
                    chip.setChecked(chip.text() == new)
            if not 0 <= row < len(state["places"]) or not new:
                preview.setText("")
                return
            _, page, word, text, at = state["places"][row]
            found = text[at:at + len(word)]
            new = new[:1].upper() + new[1:] if found[:1].isupper() else new  # como replace_text
            old_look, new_look = tags["old"], tags["new"]
            # la palabra entera tachada y la nueva al lado: letra a letra, «elf» -> «elfo» solo marcaba la «o»
            preview.setText(f"Así quedaría en la pág. {page}: " + html.escape(text[:at])
                            + f'<span style="color: {old_look["foreground"]}; background-color: '
                              f'{old_look["background"]}; text-decoration: line-through;">{html.escape(found)}</span>'
                            + " → " + f'<span style="color: {new_look["foreground"]}; background-color: '
                              f'{new_look["background"]};">{html.escape(new)}</span>' + html.escape(text[at + len(word):]))

        def done(word, message):
            self.unknown.discard(word)
            n = int(table.selection()[0])
            del state["found"][n]
            position = state["shown"].index(n) if n in state["shown"] else 0
            fill(position)
            self.status.setText(message)

        def change(only=False):
            word, new = chosen()[0], fix.text().strip()
            if not new or new == word:
                return
            edits = convert.replace_text(self.book, word, new, word=True)
            row = places.currentRow()
            if only:  # solo el párrafo elegido
                k = state["places"][row][0] if 0 <= row < len(state["places"]) else None
                edits = [e for e in edits if e[0] == k]
            if edits:
                self.apply_fixes(edits, "")
            if only and len(state["places"]) > 1:
                self.status.setText(f"«{word}» cambiada por «{new}» en ese párrafo · Ctrl+Z para deshacer")
                pick()  # quedan otras apariciones: la palabra sigue en la lista
                places.setCurrentRow(min(row, places.count() - 1))
                return
            done(word, f"«{word}» cambiada por «{new}» en {convert.counted(len(edits), 'párrafo')} · Ctrl+Z para deshacer")

        def ignore():
            word = chosen()[0]
            done(word, f"«{word}» ignorada mientras la aplicación siga abierta")

        def add():
            word = chosen()[0]
            self.words.append(word)
            done(word, f"«{word}» añadida al diccionario de la serie")

        table.selected.connect(pick)
        places.currentRowChanged.connect(lambda row: (0 <= row < len(state["places"])
                                                      and self.show_match(*state["places"][row][:3]), show_preview()))
        fix.textChanged.connect(lambda _: show_preview())
        fill()
        win.show()
        win.parts = {"table": table, "places": places, "fix": fix, "change": change_btn, "add": add_btn,
                     "one": one_btn, "ignore": ignore_btn, "search": search, "progress": progress, "preview": preview,
                     "chips": chips_row}
        self.spelling_window = win

    def insert_page(self, name, lines, first=True, at=None):
        """Capítulo nuevo con título name y un párrafo por línea, antes del primer capítulo, al final o en el bloque
        at. Devuelve dónde quedó su título."""
        start = next((int(i) for i in self.tree.get_children() if self.tree.set(i, "inc") == ON), None)
        if at is None:
            at = start if start is not None and first else len(self.book.blocks)
        lines = [f"# {name}"] + [line.strip() for line in lines if line.strip()]
        self.apply_edits([(at, at, "\n\n".join(lines))], str(at))
        self.tree.set(str(at), "inc", ON)  # sin entrada propia en deshacer: Ctrl+Z quita la página entera
        self.tree.item(str(at), tags=(KIND_TAG[ON],))
        self.update_reviewed()
        self.update_preview()
        self.status.setText(f"Página «{name}» añadida · E para editarla · Ctrl+Z para quitarla")
        return at

    def character_page(self):
        """Página «Personajes» con los de la serie en AniList (character_dialog)."""
        if self.book:
            self.search_online(lambda item: self.character_dialog(item["characters"]))

    def character_dialog(self, characters):
        """Personajes de AniList con casilla, nombre y nota editables. Solo los principales vienen marcados: los
        demás pueden destripar la historia. Cada fila cuenta cuántas veces sale el nombre en el libro: con 0 el
        nombre no está escrito como en la traducción."""
        if not characters:
            messagebox.showinfo("Página de personajes", "AniList no tiene personajes de esta serie.")
            return
        win, column = self.dialog("Personajes")
        column.addWidget(_label("Solo vienen marcados los principales: los demás pueden destripar la historia. "
                                "Escribe los nombres como en la traducción: «0 veces» es que no salen así en el libro.",
                                "muted"))
        grid_box = QWidget()
        grid = QGridLayout(grid_box)
        for n, text in enumerate(("", "Nombre", "Nota (opcional)", "Papel", "En el libro")):
            grid.addWidget(_label(text, "muted"), 0, n)
        rows = []

        def count(row):
            name = row["name"].text().strip()
            times = convert.count_matches(self.book, [(name, "")])[0][1] if name else 0
            row["count"].setText(convert.counted(times, "vez", "veces") if name else "")

        for n, (name, role) in enumerate(characters, 1):
            row = {"check": QCheckBox(), "name": QLineEdit(name), "note": QLineEdit(), "count": QLabel()}
            row["check"].setChecked(role == "MAIN")
            row["name"].setMinimumWidth(220)
            row["note"].setMinimumWidth(260)
            row["name"].textChanged.connect(lambda _, row=row: count(row))
            count(row)
            for k, widget in enumerate((row["check"], row["name"], row["note"], QLabel(ROLES.get(role, role)),
                                        row["count"])):
                grid.addWidget(widget, n, k)
            rows.append(row)
        grid.setRowStretch(len(rows) + 1, 1)
        scroll = QScrollArea()
        scroll.setWidget(grid_box)
        scroll.setWidgetResizable(True)
        scroll.setMinimumSize(760, 360)
        column.addWidget(scroll, 1)

        def save():
            chosen = [(r["name"].text().strip(), r["note"].text().strip()) for r in rows
                      if r["check"].isChecked() and r["name"].text().strip()]
            if not chosen:
                messagebox.showerror("Página de personajes", "Marca al menos un personaje.", parent=win)
                return
            # Dos puntos y no raya: la limpieza tipográfica pegaría la raya a la nota, como en un diálogo
            self.insert_page("Personajes", [f"<b>{name}</b>" + (f": {note}" if note else "") for name, note in chosen])
            win.close()

        column.addLayout(_row(1, _button("Añadir antes del primer capítulo", save, "primary")))
        win.parts = {"rows": rows, "save": save}
        win.show()

    def add_page(self, parent=None, choices=None, done=None):
        """Página propia (créditos del grupo, agradecimientos, nota del traductor) como capítulo nuevo: desde el
        editor (Insertar → «Página nueva…»), tras su capítulo, antes del primero o al final. Se deshace con Ctrl+Z.
        done(título, líneas, dónde): lo que hace el editor; sin él, se inserta aquí."""
        if not self.book:
            return
        choices = choices or ["Antes del primer capítulo", "Al final del libro"]
        self.close_tools(["page_window"])
        win, column = self.dialog("Página nueva", parent=parent)
        title = QLineEdit("Nota del traductor")
        body = QPlainTextEdit()
        body.setPlaceholderText("Un párrafo por línea. <i>cursiva</i> y <b>negrita</b>, como en «Editar texto…».")
        body.setMinimumSize(520, 220)
        where = qtk.Var(choices[0])
        column.addWidget(_label("Título", "muted"))
        column.addWidget(title)
        column.addWidget(_label("Texto", "muted"))
        column.addWidget(body, 1)

        def save():
            name = title.text().strip()
            if not name:
                messagebox.showerror("Página nueva", "Escribe un título.", parent=win)
                return
            win.close()
            if done:
                done(name, body.toPlainText().splitlines(), where.get())
            else:
                self.insert_page(name, body.toPlainText().splitlines(), where.get().startswith("Antes"))

        where.set(choices[0])
        column.addLayout(_row(_label("Dónde:"), _combo(where, choices, 22), 1, _button("Añadir", save, "primary")))
        win.show()
        win.parts = {"title": title, "body": body, "where": where, "save": save, "choices": choices}
        self.page_window = win

    def replace_image(self, k, parent=None):
        """Elige un archivo para la ilustración del bloque k y, tras ver el antes y el después, la cambia."""
        path = filedialog.askopenfilename(filetypes=IMAGE_TYPES, parent=parent)
        if not path:
            return False
        try:
            data = convert.load_cover(path)
        except ValueError as e:
            messagebox.showerror("Error", str(e), parent=parent)
            return False
        if self.confirm_image(k, data, parent=parent) != "replace":
            return False
        self.replace_images([(k, data, _store_image(path))])
        self.status.setText(f"Ilustración cambiada por {Path(path).name} · Ctrl+Z para deshacer")
        return True

    def replace_images(self, changes):
        """Cambia ilustraciones [(bloque, imagen, archivo guardado)] en un solo paso de deshacer. Se apuntan en la
        revisión como ["image", bloque, archivo]."""
        rows = self.rows()
        item = self.tree.selection()[0] if self.tree.selection() else None
        self.remember({"blocks": self.book.blocks, "candidates": self.book.candidates, "rows": rows,
                             "edits": len(self.edits), "item": item})
        for k, data, path in changes:
            _swap_image(self.book, k, data)
            self.edits.append(["image", k, path])
            if rows[str(k)][0] in (SPLIT, ROTATED) and not convert.is_wide(data):  # si no, el EPUB la partiría
                rows[str(k)] = [IMG] + rows[str(k)][1:]
        self.fill_tree(rows)
        if item and self.tree.exists(item):
            self.tree.selection_set(item)
            self.tree.see(item)
        self.update_preview()

    def image_blocks(self):
        """Ilustraciones y títulos dibujados (Witch 1): en «Ilustraciones…», aunque los títulos no son filas de imagen."""
        return [k for k, b in enumerate(self.book.blocks) if b.kind in ("image", "heading") and b.image]

    def image_name(self, n, k):
        """Nombre del archivo exportado de la ilustración número n (desde 1): «Reemplazar desde carpeta» lo busca."""
        return f"ilustracion-{n:02d}-pag-{self.book.blocks[k].page:03d}.{_image_ext(self.book.blocks[k].image)}"

    def image_title(self, n, k):
        """«Ilustración 3 · pág. 12 · Capítulo 2: …», y «(descartada)» si no va al EPUB."""
        start = self.section_bounds(k)[0]
        chapter = (self.tree.set(str(start), "title") if start in self.section_heads() and self.tree.exists(str(start))
                   else "Inicio del libro")
        dropped = self.tree.exists(str(k)) and self.tree.set(str(k), "inc") == DROPPED
        if self.book.blocks[k].kind == "heading":
            return f"Ilustración {n} · pág. {self.book.blocks[k].page} · título de «{chapter}»"
        return f"Ilustración {n} · pág. {self.book.blocks[k].page} · {chapter}" + (" (descartada)" if dropped else "")

    def confirm_image(self, k, new, batch=False, parent=None):
        """Antes y después de cambiar la ilustración del bloque k: "replace", "skip" (solo en lote) o None si se
        cancela. Nada cambia hasta pulsar Reemplazar. parent: la ventana desde la que se pide (el editor)."""
        n = self.image_blocks().index(k) + 1
        win, column = self.dialog("Reemplazar ilustración", modal=True, parent=parent)
        column.addWidget(_label(self.image_title(n, k), "title"))
        sides = []
        for caption, data in (("Antes", self.book.blocks[k].image), ("Después", new)):
            picture = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
            pixmap = QPixmap()
            pixmap.loadFromData(data)
            picture.setPixmap(pixmap.scaled(380, 470, Qt.AspectRatioMode.KeepAspectRatio,
                                            Qt.TransformationMode.SmoothTransformation))
            picture.setMinimumSize(380, 470)
            side = QVBoxLayout()
            side.addWidget(_label(caption, "title"), 0, Qt.AlignmentFlag.AlignHCenter)
            side.addWidget(picture, 1)
            side.addWidget(_label(_image_info(data), "muted"), 0, Qt.AlignmentFlag.AlignHCenter)
            sides.append(side)
        column.addLayout(_row(*sides, spacing=16))
        answer = {"value": None}

        def choose(value):
            answer["value"] = value
            win.accept()

        buttons = [1, _button("Cancelar", win.reject)]
        if batch:
            buttons.append(_button("Saltar esta", lambda: choose("skip")))
        buttons.append(_button("Reemplazar", lambda: choose("replace"), "primary"))
        column.addLayout(_row(*buttons))
        self.image_compare = win
        win.exec()
        return answer["value"]

    def export_images(self, ks, folder):
        """Guarda las ilustraciones ks en folder tal como salieron del PDF (sin reducir). Devuelve cuántas."""
        order = self.image_blocks()
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        for k in ks:
            (folder / self.image_name(order.index(k) + 1, k)).write_bytes(self.book.blocks[k].image)
        return len(ks)

    def replace_from_folder(self, folder, parent=None):
        """Empareja los archivos de folder con las ilustraciones por el nombre de la exportación (con cualquier
        extensión de imagen) y, para cada uno que cambió, pregunta con el antes y el después. Lo aceptado se
        aplica de una vez. Devuelve (reemplazadas, saltadas) o None si se canceló."""
        files = {f.stem.lower(): f for f in Path(folder).iterdir()
                 if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")}
        changes, skipped, found = [], 0, 0
        for n, k in enumerate(self.image_blocks(), 1):
            match = files.get(Path(self.image_name(n, k)).stem)
            if match is None or match.read_bytes() == self.book.blocks[k].image:  # igual que la exportada: sin cambio
                continue
            found += 1
            try:
                data = convert.load_cover(match)
            except ValueError:
                continue
            answer = self.confirm_image(k, data, batch=True)
            if answer is None:
                return None
            if answer == "replace":
                changes.append((k, data, _store_image(match)))
            else:
                skipped += 1
        if changes:
            self.replace_images(changes)
        return len(changes), skipped, found

    def images_dialog(self):
        """Herramientas → «Ilustraciones…»: todas las del libro con su miniatura, para exportarlas (una o todas),
        cambiarlas (una, o las de una carpeta con los nombres de la exportación) viendo antes y después y describirlas
        (el texto que lee un lector de pantalla)."""
        if not self.book or self.raise_tool("images_window"):
            return
        order = self.image_blocks()
        if not order:
            messagebox.showinfo("Ilustraciones", "Este libro no tiene ilustraciones.")
            return
        win, column = self.dialog("Ilustraciones")
        win.resize(980, 600)
        listing = QListWidget()
        # Por píxeles: por filas, al final quedaba el trozo de una fila vacío abajo y arriba no
        listing.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        listing.setIconSize(QSize(*THUMB_BOX))
        listing.setMinimumWidth(420)
        # Filas iguales: el icono de una ilustración apaisada era más bajo y la fila se encogía; un título largo
        # abría una barra horizontal. Se recorta con «…» y se lee entero al pasar el ratón.
        listing.setUniformItemSizes(True)
        listing.setTextElideMode(Qt.TextElideMode.ElideRight)
        listing.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        listing.setSpacing(2)
        preview = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        preview.setMinimumSize(360, 440)
        info = _label("", "muted")
        info.setWordWrap(True)
        described = _label("")  # su descripción, a la vista: antes había que ir a otra herramienta para verla
        described.setWordWrap(True)
        described.setTextFormat(Qt.TextFormat.PlainText)
        improve_btn = _button("Mejorar esta", lambda: improve([order[listing.currentRow()]]))
        improve_btn.setToolTip("Agrandarla y quitarle los artefactos del JPEG con IA (Real-ESRGAN), hasta el tamaño de "
                               "las imágenes del perfil")
        improve_all = _button("Mejorar las pequeñas…", lambda: improve(order))
        right = QVBoxLayout()
        right.addWidget(preview, 1)
        right.addWidget(info)
        right.addWidget(described)
        right.addLayout(_row(improve_btn, improve_all, 1))
        body = _row(listing, right, spacing=16)
        body.setStretch(1, 1)
        column.addLayout(body, 1)
        note = _label("", "muted")

        def fill(row=0):
            listing.clear()
            for n, k in enumerate(order, 1):
                text = f"{self.image_title(n, k)}\n{_image_info(self.book.blocks[k].image)}"
                item = QListWidgetItem(QIcon(_framed_thumb(self.book.blocks[k].image)), text)
                item.setToolTip(text)
                item.setSizeHint(QSize(0, THUMB_BOX[1] + 8))
                item.setData(Qt.ItemDataRole.UserRole, k)
                listing.addItem(item)
            listing.setCurrentRow(min(row, listing.count() - 1))

        def show(row):
            if row < 0:
                return
            k = order[row]
            pixmap = QPixmap()
            pixmap.loadFromData(self.book.blocks[k].image)
            preview.setPixmap(pixmap.scaled(360, 440, Qt.AspectRatioMode.KeepAspectRatio,
                                            Qt.TransformationMode.SmoothTransformation))
            info.setText(f"{self.image_title(row + 1, k)}\n{_image_info(self.book.blocks[k].image)} · "
                         f"se exporta como {self.image_name(row + 1, k)}")
            alt = self.alts.get(convert.image_key(self.book.blocks[k].image), "")
            described.setText(f"Descripción: {alt}" if alt else "Sin descripción: el lector de pantalla dirá "
                                                               "«Ilustración de «capítulo»»")

        def folder_of_book():
            return str(self.pdf.parent) if self.pdf else self.settings.get("folder")

        def export_all():
            folder = filedialog.askdirectory(initialdir=folder_of_book(), title="Carpeta para las ilustraciones",
                                             parent=win)
            if folder:
                n = self.export_images(order, folder)
                note.setText(f"{convert.counted(n, 'ilustración', 'ilustraciones')} exportadas en {folder}")

        def export_one():
            row = listing.currentRow()
            name = self.image_name(row + 1, order[row])
            chosen = filedialog.asksaveasfilename(initialfile=name, initialdir=folder_of_book(), filetypes=IMAGE_TYPES,
                                                  parent=win)
            path = chosen if Path(chosen).suffix else chosen + Path(name).suffix
            if chosen and _confirm_name(chosen, path, win):
                Path(path).write_bytes(self.book.blocks[order[row]].image)
                note.setText(f"Exportada en {path}")

        def replace_one():
            row = listing.currentRow()
            if self.replace_image(order[row], win):
                fill(row)
                note.setText("Ilustración cambiada · Ctrl+Z en la ventana principal para deshacer")

        def improve(ks):
            small = self.small_images(ks)
            if not small:
                note.setText(f"{'Ya tiene' if len(ks) == 1 else 'Todas tienen'} buen tamaño para el perfil "
                             f"({self.upscale_target()} px de lado mayor): no hace falta mejorar")
                return
            row = listing.currentRow()

            def finished(count):
                if shiboken6.isValid(win):
                    fill(row)
                    note.setText(f"{convert.counted(count, 'ilustración mejorada', 'ilustraciones mejoradas')}"
                                 + (" · Ctrl+Z en la ventana principal para deshacer" if count else ""))

            self.improve_images(small, win, finished)

        def describe_one():
            row = listing.currentRow()
            if self.describe(order[row], win) is not None:
                show(row)

        def replace_folder():
            folder = filedialog.askdirectory(initialdir=folder_of_book(), title="Carpeta con las ilustraciones nuevas",
                                             parent=win)
            if not folder:
                return
            result = self.replace_from_folder(folder, win)
            if result is None:
                note.setText("Cancelado: no se cambió ninguna ilustración")
            elif not result[2]:
                note.setText("En esa carpeta no hay ilustraciones cambiadas con los nombres de la exportación "
                             "(ilustracion-01-pag-005.jpg…)")
            else:
                fill(listing.currentRow())
                note.setText(f"{convert.counted(result[0], 'ilustración reemplazada', 'ilustraciones reemplazadas')}"
                             + (f" · {result[1]} saltadas" if result[1] else "")
                             + (" · Ctrl+Z en la ventana principal para deshacer" if result[0] else ""))

        listing.currentRowChanged.connect(show)
        column.addWidget(note)
        column.addLayout(_row(_button("Exportar todas…", export_all), _button("Exportar esta…", export_one), 1,
                              _button("Describir…", describe_one), _button("Cambiar…", replace_one),
                              _button("Cambiar desde carpeta…", replace_folder), _button("Cerrar", win.close)))
        fill()
        win.show()
        win.parts = {"list": listing, "note": note, "preview": preview, "described": described,
                     "describe": describe_one}
        self.images_window = win

    def upscale_target(self):
        """Lado mayor al que se mejoran las ilustraciones: el de «Tamaño de las imágenes» si se reducen; si no, el de
        una tablet. Más grande no se vería mejor en el lector y el EPUB pesaría más."""
        return int(self.shrink_size_var.get() or convert.SHRINK) if self.shrink_var.get() else upscale.DEFAULT_TARGET

    def small_images(self, ks):
        """Las ilustraciones de ks que no llegan al tamaño del perfil: las que Real-ESRGAN puede mejorar."""
        target = self.upscale_target()
        return [k for k in ks if upscale.scale_for(*_image_size(self.book.blocks[k].image), target)]

    def improve_images(self, ks, parent=None, then=None):
        """«Mejorar»: Real-ESRGAN agranda y limpia las ilustraciones ks en el proceso de trabajo; luego, cada una con
        su antes y después, y lo aceptado de una vez (un Ctrl+Z). Se descarga una vez si el usuario quiere.
        then(cambiadas): al acabar."""
        home = SETTINGS.parent
        if not upscale.installed(home):
            if messagebox.askyesno("Mejorar ilustraciones", "Para mejorar ilustraciones hay que descargar Real-ESRGAN "
                                   f"una vez (unos {upscale.ESR_SIZE}, de GitHub). Funciona con la tarjeta gráfica del "
                                   "equipo. ¿Descargarlo ahora?", parent=parent):
                self.run(upscale.download, (str(home),), lambda _: self.improve_images(ks, parent, then), report=True,
                         busy="Descargando Real-ESRGAN…")
            return
        images = {k: self.book.blocks[k].image for k in ks}

        def done(results):
            changes = []
            for k, data in results:
                # Mientras trabajaba, el libro pudo cambiar: solo se ofrece si sigue la misma ilustración
                if k >= len(self.book.blocks) or self.book.blocks[k].image != images[k]:
                    continue
                answer = self.confirm_image(k, data, batch=len(results) > 1, parent=parent)
                if answer is None:
                    break
                if answer == "replace":
                    changes.append((k, data, _store_bytes(data, "." + _image_ext(data))))
            if changes:
                self.replace_images(changes)
                self.status.setText(f"{convert.counted(len(changes), 'ilustración mejorada', 'ilustraciones mejoradas')}"
                                    " · Ctrl+Z para deshacer")
            if then:
                then(len(changes))

        self.run(upscale.upscale_many, (str(home), list(images.items()), self.upscale_target()), done, report=True,
                 busy="Mejorando ilustraciones…")

    def describe(self, k, parent=None):
        """Pide la descripción de la ilustración del bloque k (por su imagen: sigue con ella aunque cambie de sitio).
        Devuelve el texto nuevo ("" sin descripción) o None si se canceló."""
        key = convert.image_key(self.book.blocks[k].image)
        text = _ask_line(parent or self.root, "Describir ilustración", "Qué se ve, en una frase: lo lee en voz alta el "
                         "lector de pantalla de quien no la ve.\nVacío: «Ilustración de «capítulo»».",
                         self.alts.get(key, ""))
        if text is None:
            return None
        if text.strip():
            self.alts[key] = text.strip()
        else:
            self.alts.pop(key, None)
        self.status.setText("Descripción guardada" if text.strip() else "Sin descripción propia")
        return text.strip()

    def find(self, _=None):
        """Ctrl+F: barra de búsqueda sobre la lista; al elegir un resultado, las vistas previas van a él."""
        if not self.book:
            return
        self.find_bar.show()
        self.find_entry.setFocus()
        self.find_entry.selectAll()

    def show_match(self, k, page, term):
        """Selecciona el título de la sección donde está el bloque k y lleva las vistas previas al texto."""
        item = next((str(j) for j in range(k, -1, -1) if self.tree.exists(str(j)) and self.is_title(str(j))), None)
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
        pdfs = _sources(folder)
        existing = [p for p in pdfs if _has_epub(p)]
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
        options = self.new_book_options()
        self.run(_batch, ([str(p) for p in pdfs], options), lambda results: self.batch_done(results, folder),
                 report=True, busy="Convirtiendo…", partial=_batch_partials(pdfs))

    def batch_done(self, results, folder=None):
        self.progress.setValue(0)
        failed = [f"{name}: {error}" for name, error, _ in results if error]
        warned = [f"{name}: {problems[0]}" + (f" (y {len(problems) - 1} más)" if len(problems) > 1 else "")
                  for name, _, problems in results if problems]
        self.status.setText(f"{len(results) - len(failed)} de {len(results)} EPUB generados")
        if failed or warned:
            parts = (["No se pudieron convertir:\n" + "\n".join(failed)] if failed else []) + \
                    (["Generados con avisos:\n" + "\n".join(warned)] if warned else [])
            messagebox.showwarning("Convertir carpeta", "\n\n".join(parts))
        else:
            messagebox.showinfo("Convertir carpeta", f"{len(results)} EPUB generados junto a sus PDF.")
        if folder:
            self.result_dialog(folder)

    def set_watch(self, folder):
        # Lo que ya hay al activarla no se toca: solo lo que llegue después
        self.watch, self.watch_sizes = folder, {}
        self.watch_skip = {str(p) for p in _sources(folder)} if folder else set()
        self.settings["watch"] = folder
        self.status.setText(f"Vigilando {folder}" if folder else "Carpeta vigilada desactivada")

    def poll_watch(self):
        """Convierte los PDF nuevos de la carpeta vigilada, salvo si la ventana está ocupada con otro trabajo."""
        if not self.watch or self.done or not Path(self.watch).is_dir():
            return
        ready, self.watch_sizes = _ready(self.watch, self.watch_sizes, self.watch_skip)
        if ready:
            self.watch_skip.update(ready)  # un PDF que falla no se reintenta en cada vuelta
            options = self.new_book_options()
            self.run(_batch, (ready, options), self.watch_done, report=True, busy="Carpeta vigilada:",
                     partial=_batch_partials(ready))

    def watch_done(self, results):
        """Sin ventanas emergentes: la conversión pasa sola, quizá con el usuario en otra cosa."""
        self.progress.setValue(0)
        failed = [name for name, error, _ in results if error]
        warned = [name for name, error, problems in results if problems and not error]
        text = f"Carpeta vigilada: {len(results) - len(failed)} EPUB nuevo(s) ({datetime.now():%H:%M})"
        if warned:
            text += " · con avisos: " + ", ".join(warned)
        if failed:
            text += " · no se pudo: " + ", ".join(failed)
        self.status.setText(text)

    def set_cover(self, choice):
        """Cambia la portada (ver _cover_image)."""
        kind, value = choice
        self.book.cover = _cover_image(self.pdf_doc, choice, self.book)
        self.cover_choice = [kind, value]
        self.autosave()
        labels = {"page": lambda: f"Portada: página {value + 1}", "image": lambda: f"Portada: ilustración de la "
                  f"página {self.book.blocks[int(value)].page}", "file": lambda: f"Portada: {Path(value).name}",
                  "text": lambda: "Portada: con título"}
        self.cover_label.setText(labels[kind]())
        if self.tree.exists(COVER):
            self.tree.set(COVER, "page", self.cover_cell())
        self.update_cover()
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

    def illustration_grid(self, chosen):
        """Miniaturas de las ilustraciones del libro; un clic llama a chosen(índice del bloque)."""
        grid = QListWidget()
        grid.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)  # sin hueco al final
        grid.setViewMode(QListWidget.ViewMode.IconMode)
        grid.setIconSize(QPixmap(130, 130).size())
        grid.setGridSize(QPixmap(150, 160).size())
        grid.setResizeMode(QListWidget.ResizeMode.Adjust)
        grid.setMovement(QListWidget.Movement.Static)
        grid.setMinimumSize(780, 440)
        images = [k for k, b in enumerate(self.book.blocks) if b.kind == "image" and b.image]
        for k in images:
            pix = convert._rgb(pymupdf.Pixmap(self.book.blocks[k].image), alpha=False)
            scale = 130 / max(pix.width, pix.height)
            pix = pymupdf.Pixmap(pix, max(1, int(pix.width * scale)), max(1, int(pix.height * scale)), None)
            item = QListWidgetItem(QIcon(QPixmap.fromImage(qtk.qimage(pix))), f"página {self.book.blocks[k].page}")
            item.setData(Qt.ItemDataRole.UserRole, k)
            grid.addItem(item)
        if not images:
            grid.addItem("Este libro no tiene ilustraciones")
        grid.itemClicked.connect(lambda item: item.data(Qt.ItemDataRole.UserRole) is not None
                                 and chosen(item.data(Qt.ItemDataRole.UserRole)))
        return grid

    def edit_more_data(self):
        """«Más datos…»: lo de la página de título y del catálogo que no cabe en el panel. Aceptar lo guarda."""
        if not self.book:
            return
        win, column = self.dialog("Más datos del libro")
        column.addWidget(_label("Salen en la página de título (Opciones → Texto) y en los datos del EPUB que leen "
                                "Calibre y los lectores.", "muted"))
        grid = QGridLayout()
        entries = {}
        for n, (name, label) in enumerate(MORE_FIELDS[:-1]):  # los enlaces, abajo en su caja
            entries[name] = QLineEdit(getattr(self, f"{name}_var").get())
            entries[name].setMinimumWidth(240)  # el nombre japonés o un enlace se leen enteros
            grid.addWidget(QLabel(f"{label}:"), n // 2, (n % 2) * 2)
            grid.addWidget(entries[name], n // 2, (n % 2) * 2 + 1)
        hints = {"book_type": "Novela ligera", "rating": "de 0 a 5, como 4,5", "isbn": "978-…",
                 "relay": "chn-ing: Nombre", "revised": "08.04.2026", "source_url": "https://…",
                 "author_native": "橘 公司", "franchise": "reúne varias series"}
        for name, hint in hints.items():
            entries[name].setPlaceholderText(hint)
        column.addLayout(grid)
        column.addWidget(QLabel("Enlaces (uno por línea, «Texto = https://…»):"))
        links = QPlainTextEdit(self.links_var.get())
        links.setFixedHeight(70)
        column.addWidget(links)
        chosen = [self.title_image_var.get()]
        picture = _label("", "muted")

        def show_picture():
            found = next((b for b in self.book.blocks if b.kind == "image" and b.image and chosen[0]
                          and convert.image_key(b.image) == chosen[0]), None)
            picture.setText(f"Ilustración de la página de título: página {found.page}" if found
                            else "La página de título va sin ilustración")

        def pick_picture():
            box, inner = self.dialog("Ilustración de la página de título", parent=win)
            inner.addWidget(self.illustration_grid(lambda k: (chosen.__setitem__(0, convert.image_key(
                self.book.blocks[k].image)), show_picture(), box.close())), 1)
            box.show()
            self.picture_window = box

        def save():
            for name, entry in entries.items():
                getattr(self, f"{name}_var").set(entry.text().strip())
            self.links_var.set(links.toPlainText().strip())
            self.title_image_var.set(chosen[0])
            win.close()

        show_picture()
        column.addLayout(_row(picture, 1, _button("Elegir ilustración…", pick_picture),
                              _button("Sin ilustración", lambda: (chosen.__setitem__(0, ""), show_picture()))))
        column.addLayout(_row(1, _button("Aceptar", save, "primary"), _button("Cancelar", win.close)))
        win.show()
        self.more_window = win, entries, links, save

    def choose_cover(self):
        """Portada: miniaturas de las ilustraciones para elegir con un clic, o la página mostrada del PDF,
        o una imagen de un archivo."""
        if not self.book or self.raise_tool("cover_window"):
            return
        win, column = self.dialog("Elegir portada")
        grid = self.illustration_grid(lambda k: (self.set_cover(("image", k)), win.close()))
        column.addWidget(grid, 1)
        column.addLayout(_row(_button(f"Página mostrada del original ({self.pdf_page + 1})",
                                      lambda: (self.cover_from_page(), win.close())),
                              _button("Imagen de un archivo…", lambda: (win.close(), self.cover_from_file())),
                              _button("Portada con título", lambda: (self.set_cover(("text", "")), win.close())), 1))
        win.show()
        self.cover_window = win, grid

    def search_online(self, then=None):
        """Busca la serie en AniList; la elegida va a then (por defecto, apply_online)."""
        series = (self.series_var.get() or self.title_var.get()).strip()
        if not series:
            messagebox.showinfo("Buscar en internet", "Escribe primero la serie o el título del libro.")
            return
        if self.run(_lookup, (series,), lambda found: self.pick_online(found, series, then), busy="Buscando…"):
            self.status.setText(f"Buscando «{series}» en AniList…")

    def pick_online(self, found, series, then=None):
        """Lista de lo que encontró AniList: doble clic o Aceptar lleva la serie a then (apply_online: rellena
        los campos vacíos)."""
        then = then or self.apply_online
        self.status.setText("")
        if not found:
            messagebox.showinfo("Buscar en internet", f"AniList no encontró «{series}» entre las novelas ligeras.\n\n"
                                "Prueba a escribir en Serie el nombre en japonés romanizado o en inglés.")
            return
        self.close_tools(["online_window"])  # la búsqueda nueva sustituye a la anterior
        win, column = self.dialog("Resultados de AniList")
        column.addWidget(QLabel(f"«{series}»: elige la serie"))
        box = QListWidget()
        for item in found:
            box.addItem(item["name"])
        box.setCurrentRow(0)
        box.setMinimumWidth(620)
        column.addWidget(box, 1)

        def pick():
            if box.currentRow() >= 0:
                win.close()
                then(found[box.currentRow()])

        box.itemDoubleClicked.connect(lambda _: pick())
        column.addLayout(_row(1, _button("Aceptar", pick, "primary")))
        win.show()
        self.online_window = win, box, pick

    def apply_online(self, item):
        """Solo rellena lo vacío: lo escrito a mano o leído del PDF no se pisa."""
        fill = [(label, var, item[key]) for key, label, var in (
            ("author", "Autor", self.author_var), ("illustrator", "Ilustraciones", self.illustrator_var),
            ("original_title", "Título original", self.original_title_var), ("subjects", "Géneros", self.subjects_var),
            ("romaji_title", "Título romanizado", self.romaji_title_var),
            ("author_native", "Autor en japonés", self.author_native_var),
            ("illustrator_native", "Ilustrador en japonés", self.illustrator_native_var))
            if item.get(key) and not var.get().strip()]
        description = item["description"] if item["description"] and not self.description.strip() else ""
        if not fill and not description:
            messagebox.showinfo("Datos de AniList", "No hay nada que rellenar: esos datos ya están en la ventana.")
            return
        lines = [f"{label}: {value}" for label, _, value in fill]
        if description:
            lines.append(f"Sinopsis (en inglés, puedes cambiarla en «Sinopsis…»): {description[:100]}…")
        if not messagebox.askyesno("Datos de AniList", "Se rellenarán los campos vacíos:\n\n" + "\n".join(lines)):
            return
        for _, var, value in fill:
            var.set(value)
        if description:
            self.description = description
        self.status.setText("Datos tomados de AniList")

    def retitle(self, change, done):
        """Cambia el título de las filas de título según change(título) -> título nuevo; un solo Ctrl+Z
        lo deshace todo."""
        if not self.book:
            return
        changed = []
        for item in self.tree.get_children():
            if self.is_title(item):
                title = change(self.tree.set(item, "title"))
                if title != self.tree.set(item, "title"):
                    changed.append(self.row_state(item))
                    self.tree.set(item, "title", title)
        if changed:
            self.remember(changed)
            self.update_preview()
        self.status.setText(f"{done} en {convert.counted(len(changed), 'título')}")

    def fix_case(self):
        """Pone los títulos con las mayúsculas del español (convert.spanish_case)."""
        if self.book:
            words = convert.body_words(self.book)
            self.retitle(lambda t: convert.spanish_case(t, words), "Mayúsculas corregidas")

    def fix_numbers(self):
        """"Capítulo Uno" / "Capítulo IV" / "Capitulo 3" -> "Capítulo 1" / "Capítulo 4" / "Capítulo 3"."""
        self.retitle(convert.numbered, "Numeración unificada")

    def parts_as_breaks(self):
        """Los apartados sin nombre ("Parte 1", "Parte 2"… de Re Zero) pasan a separador de escena."""
        if not self.book:
            return
        changed = []
        for item in self.tree.get_children():
            if (self.is_title(item) and self.tree.set(item, "inc") != SEP
                    and convert.is_subpart(self.tree.set(item, "title"))):
                changed.append(self.row_state(item))
                self.tree.set(item, "inc", SEP)
                self.tree.item(item, tags=(KIND_TAG[SEP],))
        if changed:
            self.remember(changed)
            self.update_reviewed()
            self.update_preview()
        self.status.setText(f"{convert.counted(len(changed), 'apartado')} como separador")

    def apply_profile(self):
        """El perfil fija las opciones que importan para ese lector (PROFILES)."""
        for name, value in PROFILES.get(self.profile_var.get(), {}).items():
            self.fields()[name].set(value)
        self.update_preview()

    def spell_process(self):
        """El proceso de ortografía del editor, el mismo de una apertura a otra: arrancarlo y cargar Hunspell
        tardaba 4,4 s cada vez que se abría el editor. Lo que quede por leer era del editor anterior."""
        server = self.spell_server
        if server and server[2].is_alive():
            with contextlib.suppress(queue.Empty, OSError, ValueError, EOFError):
                while True:
                    server[1].get_nowait()
            return server
        self.spell_server = _start_spell_process()
        return self.spell_server

    def spanish(self):
        """El libro está en español: las erratas, los calcos, los ecos y la ortografía solo saben de español."""
        return (self.lang_var.get().strip() or "es").lower().startswith("es")

    def lang_changed(self):
        """Otro idioma cambia los avisos del informe; se espera a que se acabe de escribir en el campo."""
        if self.book:
            if self.lang_timer:
                qtk.cancel(self.lang_timer)
            self.lang_timer = qtk.after(600, lambda: self.book and self.update_quality())

    def quality_report(self):
        """convert.quality_report del libro, recordado por su contenido: tarda ~1 s en un libro largo, y
        deshacer, cambiar de tema o abrir el informe lo pedían otra vez con el mismo texto."""
        key = self.report_key()
        if key not in self.reports:
            if len(self.reports) >= 8:  # los últimos estados: basta para deshacer varias veces
                del self.reports[next(iter(self.reports))]
            self.reports[key] = convert.quality_report(self.book, self.echoes_var.get(), self.glossary,
                                                       self.lang_var.get().strip() or "es")
        found = list(self.reports[key]) + self.toc_issues() + self.comment_issues() + self.grammar_issues()
        off, levels = set(self.settings.get("quality_off") or ()), self.settings.get("quality_levels") or {}
        # Opciones → Informe de calidad: los apagados no salen; los de otra gravedad cuentan como ella en la nota
        return [dataclasses.replace(i, level=levels[i.kind]) if levels.get(i.kind) in convert.LEVELS else i
                for i in found if i.kind not in off]

    def grammar_issues(self):
        """Avisos de LanguageTool de los párrafos ya revisados con «Revisar gramática» (su caché junto al libro).
        Recordados por la caché y los bloques: el informe se pide a menudo."""
        if not self.book or not self.pdf:
            return []
        path, lang = _grammar_path(self.pdf), self.lang_var.get().strip() or "es"
        try:
            stamp = path.stat().st_mtime_ns
        except OSError:
            return []
        memo, issues = self.grammar_memo
        if memo and memo[:3] == (str(path), stamp, lang) and memo[3] is self.book.blocks:
            return issues
        issues = grammar.issues(self.book.blocks, grammar.load(path, lang))
        self.grammar_memo = ((str(path), stamp, lang, self.book.blocks), issues)
        return issues

    def grammar_engine(self):
        """(motor, dirección, usuario, clave) de Opciones → Ortografía → Revisión de gramática."""
        data = self.settings.get("grammar") if isinstance(self.settings.get("grammar"), dict) else {}
        engine = data.get("engine") if data.get("engine") in grammar.ENGINES else "local"
        base = {"public": grammar.PUBLIC_URL, "premium": grammar.PREMIUM_URL}.get(engine, str(data.get("url") or ""))
        premium = engine == "premium"
        return (engine, base.strip(), str(data.get("user") or "").strip() if premium else "",
                translate.unprotect(str(data.get("key") or "")) if premium else "")

    def check_grammar(self, parent=None, then=None):
        """«Revisar gramática»: LanguageTool revisa los párrafos sin revisión (nuevos o editados) en el proceso de
        trabajo y sus avisos entran en el informe. El motor local se descarga una vez si el usuario quiere y su
        servidor arranca una vez por sesión. then: al acabar bien (el informe abierto se rellena)."""
        if not self.book:
            return
        engine, base, user, key = self.grammar_engine()
        path, lang = _grammar_path(self.pdf), self.lang_var.get().strip() or "es"
        # Lo que sigue en inglés (ya es «Texto sin traducir») revisado como español solo da avisos sin sentido
        english = not lang.lower().startswith("en")
        texts = [t for t in (convert._plain(b).strip() for b in self.book.blocks if b.kind == "p")
                 if not (english and convert._facts(t)[3])]
        found = grammar.load(path, lang)
        if not any(t and grammar.key(t) not in found for t in texts):
            messagebox.showinfo("Gramática", "Ya está revisado: no hay párrafos nuevos ni cambiados desde la última "
                                "revisión.", parent=parent)
            return
        if engine == "custom" and not base:
            messagebox.showinfo("Gramática", "Falta la dirección del servidor propio: escríbela en Opciones → "
                                "Ortografía.", parent=parent)
            return
        if engine == "premium" and not (user and key):
            messagebox.showinfo("Gramática", "Faltan el usuario y la clave de LanguageTool Premium: escríbelos en "
                                "Opciones → Ortografía.", parent=parent)
            return
        wait, log = 0, ""
        if engine == "local":
            java = shutil.which("java")
            if not java:
                messagebox.showinfo("Gramática", "Revisar la gramática en este equipo necesita Java, que no está "
                                    "instalado.\n\nInstálalo gratis desde java.com y vuelve a pulsar «Revisar "
                                    "gramática», o elige languagetool.org en Opciones → Ortografía.", parent=parent)
                return
            home = SETTINGS.parent
            if not grammar.installed(home):
                if messagebox.askyesno("Gramática", "Para revisar la gramática en este equipo hay que descargar "
                                       f"LanguageTool una vez (unos {grammar.LT_SIZE}, de languagetool.org). "
                                       "¿Descargarlo ahora?", parent=parent):
                    self.run(grammar.download, (str(home),), lambda _: self.check_grammar(parent, then), report=True,
                             busy="Descargando LanguageTool…")
                return
            if not self.lt_server or self.lt_server[0].poll() is not None:
                try:
                    self.lt_server = grammar.start(home, java)
                except OSError as e:
                    messagebox.showerror("Gramática", f"No se pudo arrancar LanguageTool.\n{e}", parent=parent)
                    return
            _, base, log = self.lt_server
            wait = 90  # al arrancar tarda unos segundos; si ya responde, no espera

        def done(result):
            count, matches = result
            self.update_quality()
            self.status.setText(f"Gramática: {count} párrafos revisados, {matches} avisos")
            if then:
                then()

        self.run(grammar.review, (str(path), base, texts, lang, user, key, grammar.LIMITS[engine], wait, log), done,
                 report=True, busy="Revisando gramática…")

    def comment_issues(self):
        """Los comentarios del revisor como avisos «Revisar»: salen en F8, en el informe y al exportarlo. El de un
        párrafo que ya no está sale con su página."""
        if not self.comments:
            return []
        texts = [(k, convert._plain(b).strip()) for k, b in enumerate(self.book.blocks) if b.kind == "p"]
        by_block, issues = dict(texts), []
        for c in self.comments:
            k = _comment_block(self.book.blocks, texts, c)
            text = by_block[k] if k is not None else c["text"]
            issues.append(convert.Issue("comment", k, self.book.blocks[k].page if k is not None else c.get("page", 0),
                                        "Revisar", "Comentario del revisor", c["note"],
                                        "Cuando esté resuelto, quítalo con «Ignorar» (o en el editor, clic derecho → "
                                        "«Quitar comentario»).", convert._cut(text), text=text))
        return issues

    def set_comment(self, k, note):
        """Pone (o cambia, o con note vacía quita) el comentario del párrafo k del libro."""
        texts = [(j, convert._plain(b).strip()) for j, b in enumerate(self.book.blocks) if b.kind == "p"]
        self.comments = [c for c in self.comments if _comment_block(self.book.blocks, texts, c) != k]
        if note.strip():
            self.comments.append({"text": dict(texts)[k][:80], "page": self.book.blocks[k].page, "note": note.strip()})
        self.update_quality()

    def remove_comment(self, issue):
        texts = [(j, convert._plain(b).strip()) for j, b in enumerate(self.book.blocks) if b.kind == "p"]
        self.comments = [c for c in self.comments if not (c["note"] == issue.problem and
                                                          _comment_block(self.book.blocks, texts, c) == issue.block)]
        self.update_quality()

    def toc_issues(self):
        """Avisos del índice impreso del PDF frente a los capítulos de la lista: un título del índice sin
        capítulo (se quedó como subtítulo o no se detectó) y, si el índice es completo, un capítulo que no nombra."""
        toc = self.book.toc
        if not toc:
            return []
        where = next((page for page, _, why in self.book.skipped if why == convert.TOC_SKIPPED), 0)
        rows = [(i, self.tree.set(i, "inc"), convert._key(self.tree.set(i, "title"))) for i in self.tree.get_children()]
        chapters = [(i, key) for i, kind, key in rows if kind == ON]
        issues = []
        for title, page in toc:
            key = convert._key(title)
            if any(convert._toc_match(key, other) for _, other in chapters):
                continue
            row = next((i for i, kind, other in rows if convert._toc_match(key, other)), None)
            if row is not None:  # está en la lista, como subtítulo o quitado: casi seguro un error
                solution = "Si es un capítulo, márcalo como capítulo en la lista (tecla C)."
                block, page, level = int(row), self.book.blocks[int(row)].page, "Revisar"
            else:  # sin título en el texto: a menudo una página de ilustraciones o una nota
                solution = ("Busca el título en la vista previa del PDF; si es un capítulo, escríbelo con «Editar "
                            "texto…» y el estilo Capítulo (Ctrl+T). Si no lo es (ilustraciones, notas), ignora el aviso.")
                block, level = None, "Menor"
            issues.append(convert.Issue("toc", block, page or where, level, f"Falta un capítulo del índice: «{title}»",
                                        f"El índice del PDF (página {where}) lo nombra, pero en la lista no es un capítulo.",
                                        solution, text=title))
        if len(toc) >= 3:  # con dos o menos títulos, el índice no es la lista entera
            keys = [convert._key(title) for title, _ in toc]
            for i, key in chapters:
                if not any(convert._toc_match(key, other) for other in keys):
                    title = self.tree.set(i, "title")
                    issues.append(convert.Issue("toc-extra", int(i), self.book.blocks[int(i)].page, "Menor",
                                                f"Capítulo que no está en el índice: «{title}»",
                                                f"El índice del PDF (página {where}) no lo nombra.",
                                                "Si no es un capítulo (un apartado, una nota), cámbialo a Subtítulo "
                                                "(tecla S) o quítalo (tecla Q).", text=title))
        return issues

    def word_count(self):
        """Palabras de los párrafos del libro: la nota del informe se mide por cada 10 000."""
        return convert.text_stats(convert._plain(b) for b in self.book.blocks if b.kind == "p")["words"]

    def export_report(self, report, score, parent):
        """«Exportar…» del informe de calidad: .html para leerlo sin la app o .csv para Excel."""
        name = f"{self.pdf.stem if self.pdf else self.book.title} - informe de calidad.html"
        chosen = filedialog.asksaveasfilename(initialfile=name, parent=parent,
                                              initialdir=str(self.pdf.parent) if self.pdf else None,
                                              filetypes=[("Página web", "*.html"), ("Tabla para Excel", "*.csv")],
                                              title="Exportar el informe de calidad")
        path = chosen if Path(chosen).suffix else chosen + ".html"
        if not chosen or not _confirm_name(chosen, path, parent):
            return
        try:
            convert.export_report(report, path, self.title_var.get() or self.book.title, score)
        except OSError as e:
            messagebox.showerror("Exportar", f"No se pudo guardar el informe:\n{e.strerror or e}", parent=parent)
            return
        self.status.setText(f"Informe guardado: {Path(path).name}")

    def report_key(self):
        """Lo que mira convert.quality_report del libro: si no cambia, el informe tampoco."""
        b = self.book
        return (b.title, b.cover, tuple(b.candidates), tuple(tuple(s) for s in b.skipped),
                tuple((x.kind, x.page, x.html, x.image, x.keep, x.layout, x.align) for x in b.blocks),
                self.echoes_var.get(), tuple(self.glossary), self.lang_var.get().strip())

    def update_quality(self):
        """Botón de la barra inferior con los avisos del informe de calidad: dice qué falta antes de generar,
        en rojo si hay algo grave."""
        report = [i for i in self.quality_report() if _issue_key(i) not in self.ignored] if self.book else []
        graves = sum(i.level == "Grave" for i in report)
        rest = _quality_summary([i for i in report if i.level != "Grave"])
        if graves:
            text = ("▲ 1 aviso grave: revísalo antes de generar" if graves == 1
                    else f"▲ {graves} avisos graves: revísalos antes de generar") + (f" · {rest}" if rest else "")
        else:
            text = f"▲ {rest}" if rest else "✓ Sin avisos" if self.book else "Calidad"
        self.quality_btn.setText(text)
        self.quality_btn.setObjectName("warn" if graves else "" if report or not self.book else "ok")
        self.quality_btn.style().unpolish(self.quality_btn)
        self.quality_btn.style().polish(self.quality_btn)
        self.quality_btn.setEnabled(bool(self.book))

    def quality(self):
        """Informe de calidad (convert.quality_report). Arriba, de un vistazo: la nota en un anillo, cuántos hay de
        cada gravedad (un clic filtra), los tipos más frecuentes y un cuadro por capítulo (un clic filtra). Debajo,
        los avisos agrupados por tipo y, del elegido, el problema, la solución, el párrafo con lo señalado
        resaltado y el arreglo como un diff. «Arreglar todos» enseña antes la lista para marcar o desmarcar. Al
        elegir un aviso, las vistas previas de la ventana principal van a él."""
        if not self.book or self.raise_tool("quality_window"):
            return
        win, column = self.dialog("Informe de calidad")
        win.resize(1120, 660)  # cabe en 1366x728 con la barra de tareas
        levels = theme.level_colors(self.dark)
        state = {"report": [], "places": [], "all": [], "score": 100, "kind": "", "chapter": -1, "groups": {}}
        show = qtk.Var("Todos")
        # --- resumen
        ring = report.ScoreRing(self.dark)
        summary = _label("", "title")  # «Nota 64/100 · 8 por revisar · 290 menores», en una línea
        chips = {}
        chip_row = QHBoxLayout()
        chip_row.setSpacing(theme.SPACE["gap"])
        for name in ["Todos"] + [LEVEL_PLURAL[level].capitalize() for level in convert.LEVELS] + ["Ignorados"]:
            chip = QPushButton(name, objectName="chip", checkable=True)
            chip.clicked.connect(lambda _=False, n=name: (show.set(n), fill()))
            chips[name] = chip
            chip_row.addWidget(chip)
        chip_row.addStretch(1)
        filters = _label("", "muted")
        clear_filter = _button("Quitar filtro", lambda: (state.update(kind="", chapter=-1), fill()))
        options_btn = _button("Opciones del informe…", lambda: self.open_options(
            "Informe de calidad", on_close=lambda: shiboken6.isValid(win) and fill()))
        export_btn = _button("Exportar…", lambda: self.export_report(state["all"], state["score"], win))
        export_btn.setToolTip("Guardar el informe como página web (.html) o tabla para Excel (.csv), para otra persona")
        grammar_btn = _button("Revisar gramática", lambda: self.check_grammar(
            win, then=lambda: shiboken6.isValid(win) and fill()))
        grammar_btn.setToolTip("LanguageTool busca tildes, concordancia y queísmo; solo revisa lo nuevo o editado. "
                               "Dónde corre, en Opciones → Ortografía")
        bars = report.KindBars(self.dark)
        bars.picked.connect(lambda kind: (state.update(kind=kind), fill()))
        strip = report.ChapterStrip(self.dark)
        strip.picked.connect(lambda n: (state.update(chapter=n), fill()))
        head = QVBoxLayout()
        head.setSpacing(theme.SPACE["gap"])
        head.addWidget(summary)
        head.addLayout(chip_row)
        head.addLayout(_row(filters, clear_filter, 1))
        kinds_box = QVBoxLayout()
        kinds_box.setSpacing(2)
        kinds_label = _label("Tipos más frecuentes · clic para ver solo ese", "muted")
        kinds_box.addWidget(kinds_label)
        kinds_box.addWidget(bars)
        kinds_box.addStretch(1)
        actions = QVBoxLayout()
        actions.addWidget(grammar_btn)
        actions.addWidget(options_btn)
        actions.addWidget(export_btn)
        actions.addStretch(1)
        # En tres filas: en una sola, las fichas, las barras y los botones no cabían en 1093 px (125 %)
        column.addLayout(_row(ring, head, 1, spacing=theme.SPACE["section"]))
        middle = _row(kinds_box, actions, spacing=theme.SPACE["section"])
        middle.setStretch(0, 1)
        column.addLayout(middle)
        strip_label = _label("Capítulos", "muted")
        column.addLayout(_row(strip_label, strip, spacing=theme.SPACE["pad"]))
        # --- lista y detalle; sin nada que enseñar, en su lugar un aviso en el medio (vacíos parecían rotos)
        body = QStackedWidget()
        column.addWidget(body, 1)
        panes = QSplitter(Qt.Orientation.Horizontal)
        body.addWidget(panes)
        empty = QWidget()
        empty_column = QVBoxLayout(empty)
        empty_column.addStretch(1)
        empty_icon = _label(theme.ICONS["ok"])
        empty_icon.setStyleSheet(f"font-size: 40pt; color: {theme.colors(self.dark)['ok']};")
        empty_title = _label("", "big")
        empty_hint = _label("", "muted")
        empty_hint.setWordWrap(True)
        empty_hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        for widget in (empty_icon, empty_title):
            empty_column.addWidget(widget, 0, Qt.AlignmentFlag.AlignHCenter)
        empty_column.addWidget(empty_hint)  # a lo ancho: centrada en el layout, la línea ajustada salía cortada
        empty_column.addStretch(1)
        body.addWidget(empty)
        # El aviso primero y sin columna de gravedad: la dice el color, y el grupo la nombra (antes cada fila
        # repetía «● Menor» y el grupo ponía «N avisos» bajo «Dónde»: parecía otra fila más)
        table = qtk.RowList(("title", "where", "page"), {"title": "Aviso", "where": "Dónde", "page": "Pág."},
                            {"where": 150, "page": 48})
        for level, color in levels.items():
            table.tag_configure(level, foreground=color)
        bold = QFont(table.font())
        bold.setWeight(QFont.Weight.DemiBold)
        table.tag_configure("group", font=bold)
        table.double_clicked.connect(lambda _: open_issue())  # doble clic: al editor, en el trozo exacto
        panes.addWidget(table)
        right = QWidget()
        side = QVBoxLayout(right)
        side.setContentsMargins(theme.SPACE["section"], 0, 0, 0)
        # El detalle con su barra: entero pedía 752 px de alto y la ventana no cabía en 1366x728
        detail = QScrollArea()
        detail.setWidgetResizable(True)
        detail.setFrameShape(QFrame.Shape.NoFrame)
        detail.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        detail.setWidget(right)
        heading = _label("", "title")
        heading.setWordWrap(True)
        side.addWidget(heading)
        place = _label("", "muted")  # capítulo › subtítulo · párrafo N de M · página
        place.setWordWrap(True)
        side.addWidget(place)
        texts = {}
        for name, caption in (("problem", "Problema"), ("solution", "Solución")):
            side.addWidget(_label(caption, "muted"))
            texts[name] = QLabel()
            texts[name].setWordWrap(True)
            texts[name].setTextFormat(Qt.TextFormat.PlainText)  # un «<i>» de un comentario no es HTML aquí
            side.addWidget(texts[name])
        previews = {}
        for name, caption in (("before", "En el texto"), ("after", "Arreglo")):
            side.addWidget(_label(caption, "muted"))
            box = QTextBrowser()
            box.setOpenLinks(False)
            box.setFixedHeight(80)
            side.addWidget(box)
            previews[name] = box
        legend = _label("¶ marca un salto de párrafo", "muted")
        side.addWidget(legend)
        fix_btn = _button("Arreglar", lambda: apply([chosen_issue()]), "primary")
        all_btn = _button("Arreglar todos", lambda: confirm_all(same_kind(chosen_kind())))
        ignore_btn = _button("Ignorar", lambda: ignore(chosen_issue()))
        edit_btn = _button("Abrir en el editor", lambda: open_issue())
        edit_btn.setToolTip("Abre el capítulo con el trozo del aviso elegido (también con doble clic en la lista)")
        side.addStretch(1)
        aside = QWidget()  # el detalle se desplaza; los botones, siempre a la vista debajo
        aside_column = QVBoxLayout(aside)
        aside_column.setContentsMargins(0, 0, 0, 0)
        aside_column.addWidget(detail, 1)
        actions_row = _row(fix_btn, all_btn, edit_btn, 1, ignore_btn)
        actions_row.setContentsMargins(theme.SPACE["section"], 0, 0, 0)
        aside_column.addLayout(actions_row)
        panes.addWidget(aside)
        panes.setSizes([500, 600])

        def chosen_issue():
            chosen = table.selection()
            return state["report"][int(chosen[0])] if chosen and not chosen[0].startswith("k:") else None

        def chosen_kind():
            chosen = table.selection()
            if not chosen:
                return ""
            return chosen[0][2:] if chosen[0].startswith("k:") else state["report"][int(chosen[0])].kind

        def open_issue():
            issue = chosen_issue()
            if issue:
                self.edit_issue(issue)

        def kind_title(issue):
            """El nombre del tipo, sin lo de cada aviso: «Párrafo muy largo (900 caracteres)» -> «Párrafo muy largo»."""
            return re.sub(r"\s*(\([^)]*\)|:\s*«.*)$", "", issue.title)

        def sections():
            """[(título, inicio, fin)] de los capítulos del libro, como el editor: lo de antes del primero, aparte."""
            heads = self.section_heads() + [len(self.book.blocks)]
            found = [("Inicio del libro", 0, heads[0])] if heads[0] > 0 else []
            return found + [(self.tree.set(str(k), "title"), k, end) for k, end in zip(heads, heads[1:])]

        def fill(select=None):
            everything = self.quality_report()
            ignored = [i for i in everything if _issue_key(i) in self.ignored]
            reported = [i for i in everything if _issue_key(i) not in self.ignored]
            state["all"], state["score"] = reported, convert.quality_score(reported, self.word_count())
            ring.set_score(state["score"])
            summary.setText(f"Nota {state['score']}/100 · " + (_quality_summary(reported)
                                                               or "No se encontró nada sospechoso.")
                            + (f" · {convert.counted(len(ignored), 'ignorado')}" if ignored else ""))
            for name, chip in chips.items():
                count = (len(reported) if name == "Todos" else len(ignored) if name == "Ignorados" else
                         sum(1 for i in reported if LEVEL_PLURAL[i.level].capitalize() == name))
                chip.setText(f"{name} ({count})")
                chip.setChecked(name == show.get())
            base = ignored if show.get() == "Ignorados" else [
                i for i in reported if show.get() == "Todos" or LEVEL_PLURAL[i.level].capitalize() == show.get()]
            # tipos y capítulos, de lo que deja la gravedad elegida: un clic en ellos filtra un poco más
            counts = collections.Counter(i.kind for i in base)
            first = {i.kind: i for i in reversed(base)}
            bars.set_rows([(kind, kind_title(first[kind]), n, first[kind].level) for kind, n in counts.most_common()],
                          state["kind"])
            parts = sections()
            words = [sum(len(convert._plain(b).split()) for b in self.book.blocks[s:e] if b.kind == "p")
                     for _, s, e in parts]
            per_chapter = [[i for i in base if i.block is not None and s <= i.block < e] for _, s, e in parts]
            strip.set_cells([(title, len(found), n, next((lv for lv in convert.LEVELS if any(i.level == lv for i in found)),
                                                         "")) for (title, _, _), found, n in zip(parts, per_chapter, words)],
                            state["chapter"])
            shown = [i for i in base if (not state["kind"] or i.kind == state["kind"])
                     and (state["chapter"] < 0 or state["chapter"] >= len(parts) or i in per_chapter[state["chapter"]])]
            notes = ([f"tipo «{kind_title(first[state['kind']])}»"] if state["kind"] in first else []) + (
                [f"«{parts[state['chapter']][0]}»"] if 0 <= state["chapter"] < len(parts) else [])
            filters.setText(("Solo " + " · ".join(notes)) if notes else "")
            clear_filter.setVisible(bool(notes))
            state["report"] = shown
            clean = not reported and show.get() != "Ignorados"
            for widget in (strip_label, strip):  # sin avisos, cuadros grises sobran
                widget.setVisible(not clean)
            kinds_label.setVisible(bool(base))
            bars.setVisible(bool(base))
            body.setCurrentWidget(panes if shown else empty)
            empty_icon.setVisible(clean)
            empty_title.setText("Todo en orden" if clean else "Ningún aviso con este filtro")
            empty_hint.setText(
                ("El informe no encontró nada sospechoso en el libro: ni restos del PDF, ni puntuación rara, ni "
                 "párrafos cortados." + (f" Hay {convert.counted(len(ignored), 'aviso ignorado', 'avisos ignorados')}"
                                         ": míralos en «Ignorados»." if ignored else ""))
                if clean else "Elige «Todos» o quita el filtro para ver los demás avisos.")
            ignore_btn.setText("No ignorar" if show.get() == "Ignorados" else "Ignorar")
            win.setWindowTitle(f"Informe de calidad · {convert.counted(len(reported), 'aviso')}")
            table.delete(*table.get_children())
            state["places"] = self.issue_places(shown)
            order = sorted(collections.Counter(i.kind for i in shown).items(),
                           key=lambda kv: (convert.LEVELS.index(next(i.level for i in shown if i.kind == kv[0])), -kv[1]))
            groups = state["groups"] = {}
            for kind, n in order:
                issue = next(i for i in shown if i.kind == kind)
                gid = f"k:{kind}"
                table.insert("", "end", iid=gid, values=(f"{kind_title(issue)} ({n})", "", ""),
                             tags=(issue.level, "group"))
                table.rows[gid].setFirstColumnSpanned(True)  # el grupo, de lado a lado: un encabezado, no una fila
                table.rows[gid].setToolTip(0, f"{convert.counted(n, 'aviso')} · {LEVEL_NAMES[issue.level][n != 1]}")
                groups[kind] = []
                for k, found in enumerate(shown):
                    if found.kind == kind:
                        table.insert(gid, "end", iid=str(k), values=(found.title, state["places"][k][0],
                                                                     found.page), tags=(found.level,))
                        groups[kind].append(str(k))
                table.rows[gid].setExpanded(n <= 25 or len(order) == 1)  # los muy numerosos, plegados
            rows = [iid for kind, _ in order for iid in groups[kind]]
            if rows:
                item = rows[min(select, len(rows) - 1)] if isinstance(select, int) else rows[0]
                table.selection_set(item)
                table.see(item)
            else:
                pick()

        def same_kind(kind):
            return [i for i in state["report"] if i.kind == kind and i.edit]

        def pick():
            issue, kind = chosen_issue(), chosen_kind()
            group = [state["report"][int(k)] for k in state["groups"].get(kind, [])] if kind and not issue else []
            shown = issue or (group[0] if group else None)
            heading.setText(issue.title if issue else (f"{group[0].title.split(' (')[0]} · "
                                                        f"{convert.counted(len(group), 'aviso')}" if group else ""))
            heading.setStyleSheet(f"color: {levels[shown.level]};" if shown else "")
            place.setText(state["places"][int(table.selection()[0])][1] if issue else
                          ("Elige uno en la lista para verlo en el texto" if group else ""))
            texts["problem"].setText(shown.problem if shown else "")
            texts["solution"].setText(shown.solution if shown else "")
            if issue:
                block = self.book.blocks[issue.block] if issue.block is not None and issue.block < len(
                    self.book.blocks) else None
                paragraph = convert.shown_text(block, issue.block) if block is not None else issue.before
                previews["before"].setHtml(report.marked_html(paragraph.strip(), issue.span and (
                    issue.span[0] - (len(paragraph) - len(paragraph.lstrip())),
                    issue.span[1] - (len(paragraph) - len(paragraph.lstrip()))), self.dark)
                    if paragraph.strip() else html.escape(f"(texto de la página {issue.page} del original: míralo "
                                                          "en la vista previa)"))
                if issue.edit and issue.after:
                    previews["after"].setHtml(report.diff_html(issue.before, issue.after, self.dark))
                elif issue.edit:  # el arreglo quita el párrafo entero
                    previews["after"].setHtml(report.diff_html(issue.before, "", self.dark) + " <i>(se quita)</i>")
                else:
                    previews["after"].setHtml(html.escape("Sin arreglo automático: sigue la solución."))
            else:
                for box in previews.values():
                    box.clear()
            legend.setVisible(bool(issue and "¶" in (issue.before + issue.after)))
            fix_btn.setEnabled(bool(issue and issue.edit))
            ignore_btn.setEnabled(bool(issue))
            edit_btn.setEnabled(bool(issue))
            count = len(same_kind(kind)) if kind else 0
            all_btn.setText(f"Arreglar todos los de este tipo ({count})")
            all_btn.setEnabled(count > 1 or (count == 1 and not issue))
            if issue:
                term = " ".join(issue.text.split()[:6])
                if issue.block is None or not term:
                    self.show_found(issue.page, term)
                else:
                    self.show_match(issue.block, issue.page, term)

        def apply(issues):
            # De atrás adelante, y sin pisarse: un arreglo que toca lo que otro ya cambió se deja para luego
            edits, limit = [], len(self.book.blocks) + 1
            for issue in sorted(issues, key=lambda i: -i.edit[0]):
                if issue.edit[1] <= limit:
                    edits.append(issue.edit)
                    limit = issue.edit[0]
            self.apply_fixes(edits, f"{convert.counted(len(edits), 'aviso arreglado', 'avisos arreglados')} · Ctrl+Z para deshacer")
            fill(position())

        def position():
            chosen = table.selection()
            rows = [iid for kind in state["groups"] for iid in state["groups"][kind]]
            return rows.index(chosen[0]) if chosen and chosen[0] in rows else 0

        def confirm_all(issues):
            """Antes de «Arreglar todos»: cada cambio con su casilla; solo los marcados, en un único Ctrl+Z."""
            if not issues:
                return
            box, layout = self.dialog("Arreglar todos", parent=win)
            box.resize(760, 460)
            layout.addWidget(_label(f"{issues[0].title.split(' (')[0]}: desmarca los que no quieras cambiar", "title"))
            listing = QListWidget()
            for issue in issues:
                change = report.change_snippet(issue.before, issue.after) if issue.after else f"se quita: {issue.before}"
                item = QListWidgetItem(f"pág. {issue.page} · {change}")
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked)
                item.setToolTip(f"Ahora: {issue.before}\nCon el arreglo: {issue.after or '(se quita)'}")
                listing.addItem(item)
            layout.addWidget(listing, 1)

            def marked():
                return [issue for n, issue in enumerate(issues) if listing.item(n).checkState() == Qt.CheckState.Checked]

            def mark(on):
                for n in range(listing.count()):
                    listing.item(n).setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)

            def go():
                chosen = marked()
                box.close()
                if chosen:
                    apply(chosen)

            go_btn = _button(f"Arreglar {len(issues)}", go, "primary")
            listing.itemChanged.connect(lambda _: (go_btn.setText(f"Arreglar {len(marked())}"),
                                                   go_btn.setEnabled(bool(marked()))))
            layout.addLayout(_row(_button("Marcar todos", lambda: mark(True)),
                                  _button("Desmarcar todos", lambda: mark(False)), 1, go_btn,
                                  _button("Cancelar", box.close)))
            box.parts = {"list": listing, "go": go, "mark": mark}
            box.show()
            self.fix_all_window = box

        def ignore(issue):
            """Ignorar (o volver a mostrar) un aviso: se recuerda en la revisión. Un comentario del revisor se quita."""
            if issue is None:
                return
            key = _issue_key(issue)
            if issue.kind == "comment":
                self.remove_comment(issue)
            else:
                self.ignored = [k for k in self.ignored if k != key] if key in self.ignored else self.ignored + [key]
            self.update_quality()
            fill(position())

        table.selected.connect(pick)
        fill()
        win.show()
        win.parts = {"table": table, "summary": summary, "heading": heading, "texts": texts, "previews": previews,
                     "fix": fix_btn, "all": all_btn, "ignore": ignore_btn, "show": show, "fill": fill,
                     "echoes": options_btn, "place": place, "edit": edit_btn, "ring": ring, "chips": chips,
                     "bars": bars, "strip": strip, "filters": filters, "clear": clear_filter,
                     "empty": empty, "empty_title": empty_title,
                     "rows": lambda: [iid for kind in state["groups"] for iid in state["groups"][kind]],
                     "state": state}
        self.quality_window = win

    def series_panel(self):
        """Los tomos de la carpeta con su estado; doble clic abre uno."""
        folder = self.pdf.parent if self.pdf else filedialog.askdirectory(initialdir=self.settings.get("folder"))
        if not folder:
            return
        win, column = self.dialog(f"Serie · {Path(folder).name}")
        table = qtk.RowList(("pdf", "state", "warn", "sent"),
                            {"pdf": "Tomo", "state": "Estado", "warn": "Avisos", "sent": "Enviado"},
                            {"state": 200, "warn": 80, "sent": 130}, stretch="pdf")
        table.setMinimumSize(800, 420)
        sources = _sources(folder)
        missing = _missing_volumes(sources)
        if missing:
            column.addWidget(_label(missing, "warn"))
        for pdf in sources:
            state = _read_review(pdf)
            problems = state.get("problems") if state else None
            warn = "—" if problems is None else len(problems) or "Ninguno"
            table.insert("", "end", iid=str(pdf), values=(pdf.name, _volume_status(pdf), warn,
                                                          _sent_text(_volume_epub(pdf))))
        table.double_clicked.connect(lambda _: table.selection() and (self.open_path(table.selection()[0]), win.close()))
        column.addWidget(table, 1)
        column.addWidget(_label("Doble clic: abrir el tomo", "muted"))
        win.show()
        self.series_window = win, table

    def omnibus(self):
        """Volumen recopilatorio: varios PDF (en orden de nombre) en un solo EPUB (ver _omnibus)."""
        if self.done:  # trabajo en curso: el menú no se desactiva como los botones
            return
        paths = filedialog.askopenfilenames(filetypes=[("PDF o EPUB", "*.pdf *.epub")], initialdir=self.settings.get("folder"),
                                            title="Tomos del recopilatorio")
        if len(paths) < 2:
            if paths:
                messagebox.showinfo("Volumen recopilatorio", "Elige al menos dos tomos.")
            return
        pdfs = sorted(paths)
        chosen = filedialog.asksaveasfilename(filetypes=[("EPUB", "*.epub")], initialdir=Path(pdfs[0]).parent,
                                              initialfile=_epub_name("Recopilatorio", self.kepub_var.get()))
        out = _epub_name(chosen, self.kepub_var.get())
        if not chosen or not _confirm_name(chosen, out):
            return
        self.save_review()  # el tomo abierto entra con lo revisado hasta ahora
        options = self.new_book_options()
        options["replacements"] = self.replacements
        self.run(_omnibus, (pdfs, out, options), lambda problems: self.saved(out, problems, of_book=False), report=True,
                 busy="Preparando el recopilatorio…", partial=[convert.partial_path(out)])

    def show_shortcuts(self):
        """Herramientas → «Atajos de teclado…»: una tabla por grupo, cada tecla dibujada como una tecla."""
        win, column = self.dialog("Atajos de teclado")
        colors = theme.colors(self.dark)
        key_style = (f"QLabel#key {{ border: 1px solid {colors['edge']}; border-bottom-width: 2px; border-radius: 4px; "
                     f"background: {colors['card']}; padding: 1px 6px; font-weight: 600; min-width: 12px; "
                     f"qproperty-alignment: AlignCenter; }}"
                     # «,», «.» y «-» a tamaño de letra apenas se veían: más grandes, en la misma tecla
                     f"QLabel#key[sign=\"true\"] {{ font-size: {theme.UI_SIZES.get(self.settings.get('ui_font'), 9) + 4}pt; "
                     f"padding: 0px 6px; }}")
        win.setStyleSheet(key_style)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        grid = QGridLayout(inner)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(6)
        row = 0
        for group, keys in SHORTCUT_GROUPS:
            title = _label(group, "title")
            grid.addWidget(title, row, 0, 1, 2)
            row += 1
            for combo, action in keys:
                # En su propio widget: en un layout anidado, la rejilla no se enteraba del alto con el QSS de las
                # teclas y las dejaba aplastadas, con un hueco al final
                keys_box = QWidget()
                keys_row = QHBoxLayout(keys_box)
                keys_row.setContentsMargins(0, 0, 0, 0)
                keys_row.setSpacing(4)
                for n, alternative in enumerate(combo.split(" / ")):
                    if n:
                        keys_row.addWidget(_label("o", "muted"))
                    for m, key in enumerate(alternative.split("+")):
                        if m:
                            keys_row.addWidget(_label("+", "muted"))
                        cap = QLabel(key, objectName="key")
                        cap.setProperty("sign", len(key) == 1 and not key.isalnum())
                        keys_row.addWidget(cap)
                keys_row.addStretch(1)
                grid.addWidget(keys_box, row, 0)
                grid.addWidget(_label(action), row, 1)
                row += 1
            grid.setRowMinimumHeight(row, 10)
            row += 1
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(row, 1)
        scroll.setWidget(inner)
        column.addWidget(scroll, 1)
        column.addLayout(_row(1, _button("Cerrar", win.close, "primary")))
        win.resize(560, 620)
        win.show()
        self.shortcuts_window = win

    def open_log(self):
        """Abre registro.log (lo que hizo cada parte de la app) con el editor de texto de Windows."""
        log = registro.path(SETTINGS.parent)
        if not log.is_file():
            messagebox.showinfo("Registro", f"Aún no hay nada apuntado en:\n{log}")
            return
        os.startfile(log)

    def section_of(self, item):
        """Tramo de bloques de una fila: un capítulo hasta el siguiente capítulo (con sus subtítulos);
        otro título, hasta el siguiente título; una ilustración, solo ella."""
        start = int(item)
        blocks = self.book.blocks
        if blocks[start].kind == "image":
            return start, start + 1
        chapter = self.tree.set(item, "inc") == ON
        end = next((j for j in range(start + 1, len(blocks)) if blocks[j].kind == "heading"
                    and (not chapter or self.tree.exists(str(j)) and self.tree.set(str(j), "inc") == ON)), len(blocks))
        return start, end

    def move_row(self, item, dest):
        """Mueve la sección de una fila para que empiece en el bloque dest (len(blocks): al final).
        Se apunta en la revisión como ["move", …] y se deshace con Ctrl+Z."""
        start, end = self.section_of(item)
        if start <= dest <= end:  # ya está ahí
            return
        rows = self.rows()
        self.remember({"blocks": self.book.blocks, "candidates": self.book.candidates, "rows": rows,
                             "edits": len(self.edits), "item": item})
        mapping = convert.move_section(self.book, start, end, dest)
        self.edits.append(["move", start, end, dest])
        self.fill_tree({str(mapping[int(k)]): v for k, v in rows.items()})
        moved = str(mapping[start])
        self.tree.selection_set(moved)
        self.tree.see(moved)
        self.update_preview()

    def move_selected(self, delta):
        """Alt+↑ / Alt+↓: la sección seleccionada pasa antes de la fila anterior o después de la siguiente."""
        selected = self.tree.selection()
        if not self.book or not selected:
            return
        item = selected[0]
        if self.is_break(item):
            return
        items = [i for i in self.tree.get_children() if not self.is_break(i)]
        start, end = self.section_of(item)
        if delta < 0:
            before = [i for i in items if int(i) < start]
            if before:
                self.move_row(item, int(before[-1]))
        else:
            after = [i for i in items if int(i) >= end]
            if after:
                self.move_row(item, self.section_of(after[0])[1])

    def on_drop(self, event):
        """Arrastrar una fila por el título y soltarla sobre otra: su sección pasa delante de esa fila."""
        target = self.tree.identify_row(event.y)
        source = getattr(self, "drag_source", None)
        self.drag_source = None
        if source and target and target != source and self.tree.exists(source) and not self.is_break(source):
            self.move_row(source, int(target))

    def fields(self):
        """Campos de la ventana que se guardan en la revisión."""
        return {"title": self.title_var, "author": self.author_var, "series": self.series_var,
                "volume": self.volume_var, "publisher": self.publisher_var, "translator": self.translator_var,
                "illustrator": self.illustrator_var, "proofreader": self.proofreader_var, "editor": self.editor_var,
                "original_title": self.original_title_var, "subjects": self.subjects_var, "date": self.date_var,
                "lang": self.lang_var, "style": self.style_var, "separator": self.separator_var,
                "separator_icon": self.separator_icon_var,
                "typography": self.typography_var, "shrink": self.shrink_var, "shrink_size": self.shrink_size_var,
                "credits": self.credits_var, "gray": self.gray_var, "trim": self.trim_var, "font": self.font_var,
                "font_file": self.font_file_var, "epub2": self.epub2_var, "reading": self.reading_var, "profile": self.profile_var,
                "horizontal": self.horizontal_var,
                "contents": self.contents_var,
                "autocase": self.autocase_var, "kepub": self.kepub_var,
                "hyphenate": self.hyphenate_var, "whole_words": self.whole_words_var, "pdf_size": self.pdf_size_var,
                **{name: getattr(self, f"{name}_var") for name, _ in MORE_FIELDS},
                "title_image": self.title_image_var, "synopsis": self.synopsis_var, "endnotes": self.endnotes_var,
                "note_style": self.note_style_var, "heading_font": self.heading_font_var,
                "heading_font_file": self.heading_font_file_var, "box_style": self.box_style_var, "logo": self.logo_var}

    def rows(self):
        return {item: [self.tree.set(item, "inc"), self.tree.set(item, "title"), self.tree.set(item, "ok")]
                for item in self.tree.get_children()}

    def review_state(self):
        """Lo que se guarda del trabajo en este libro: en la revisión y en cada versión."""
        state = {name: var.get() for name, var in self.fields().items()}
        state.update(blocks=self.analyzed, edits=self.edits, description=self.description, cover=self.cover_choice,
                     replacements=self.replacements, rows=self.rows(), problems=self.last_problems, words=self.words,
                     glossary=self.glossary, tr_glossary=self.tr_glossary, ignored=self.ignored, comments=self.comments,
                     alts=self.alts, tr_notes=self.tr_notes, editor_at=self.editor_at, work=self.work,
                     editor_history=self.editor_history,
                     faithful=bool(getattr(self.book, "origin", "")))  # revisión del EPUB leído por su HTML
        return state

    def save_review(self):
        """Guarda la revisión del libro abierto. False si no se pudo (el motivo queda en la línea de estado)."""
        if not (self.book and self.pdf):
            return True
        state = self.review_state()
        try:
            state["pdf"] = _fingerprint(self.pdf)
            _write_json(_review_path(self.pdf), state)
            _remember_series(state)
            self.save_thumb()  # la portada elegida, para la tarjeta de recientes
        except OSError as e:  # PDF borrado, carpeta de solo lectura, OneDrive bloqueándolo: el trabajo en curso sigue
            self.status.setText(f"No se pudo guardar la revisión: {e.strerror or e}")
            return False
        return True

    def apply_state(self, state):
        """Pone en la ventana los campos y reemplazos de una revisión guardada (o heredada)."""
        for name, var in self.fields().items():
            if name in state:
                try:
                    var.set(state[name])
                except (ValueError, TypeError):
                    pass  # valor mal escrito a mano en el .json
        if self.style_var.get() not in convert.STYLES and self.style_var.get() != convert.ORIGINAL:  # .json a mano
            self.style_var.set("Clásico")
        self.separator_var.set(convert.separator_choice(self.separator_var.get()))  # «Original del PDF» de antes
        if not _font_file(self.font_file_var.get()):  # ruta de red o que no es una fuente: ni se mira
            self.font_file_var.set("")
        font, path = self.font_var.get(), Path(self.font_file_var.get())
        if font not in FONT_CHOICES or font == "Otra…":
            if self.font_file_var.get() and path.name == font and path.is_file():
                self.show_font(font)
            else:
                self.font_var.set("Del lector")
                self.status.setText(f"No se encontró la fuente {font}: se usa la del lector.")
        self.font_last = self.font_var.get()
        try:
            self.replacements = [[str(a), str(b)] for a, b in state["replacements"]]
        except (KeyError, TypeError, ValueError):
            pass  # sin reemplazos guardados, o mal escritos a mano
        if isinstance(state.get("words"), list):
            self.words = [str(w) for w in state["words"]]
        if isinstance(state.get("glossary"), list):
            self.glossary = [str(w) for w in state["glossary"]]
        if isinstance(state.get("tr_glossary"), list):
            self.tr_glossary = [[str(a), str(b)] for a, b in (p for p in state["tr_glossary"]
                                                               if isinstance(p, list) and len(p) == 2)]
        if isinstance(state.get("tr_notes"), str):
            self.tr_notes = state["tr_notes"]

    def load_review(self, state=None):
        """Recupera la revisión guardada de este PDF, o la de una versión (state). False si no hay o si el PDF
        cambió desde entonces."""
        moved = False
        if state is None:
            state = _review_for(self.pdf, self.book) if self.pdf else None
            moved = bool(state) and state["blocks"] != (_read_review(self.pdf) or {}).get("blocks")
        if not state:
            return False
        if moved:  # revisión del análisis de antes, trasladada: la original queda en .bak y se guarda la de ahora
            try:
                shutil.copy2(_review_path(self.pdf), _backup_path(_review_path(self.pdf)))
                _write_json(_review_path(self.pdf), state)
            except OSError:
                pass  # se vuelve a trasladar la próxima vez
        self.edits = _replay(self.book, state)
        self.fill_tree({})
        for item, (kind, title, *done) in state["rows"].items():
            if self.tree.exists(item) and kind in self.cycle(item):
                self.set_kind(item, kind)
                self.tree.set(item, "title", title)
                self.tree.set(item, "ok", done[0] if done else "")
        self.update_reviewed()
        self.apply_state(state)
        self.description = state.get("description", "")
        self.last_problems = state.get("problems")
        self.ignored = state["ignored"] if isinstance(state.get("ignored"), list) else []
        self.alts = {str(k): str(v) for k, v in state["alts"].items()} if isinstance(state.get("alts"), dict) else {}
        self.comments = [c for c in state.get("comments", []) if isinstance(c, dict)  # el .json se edita a mano
                         and isinstance(c.get("note"), str) and isinstance(c.get("text"), str)]
        history = state.get("editor_history")
        self.editor_history = dict(history) if isinstance(history, dict) else {}  # history_entry valida cada una
        at = state.get("editor_at")
        self.editor_at = at if isinstance(at, int) and not isinstance(at, bool) else None
        work = state.get("work") if isinstance(state.get("work"), dict) else {}
        self.work = {str(day): {key: w[key] for key in WORK_KEYS if isinstance(w.get(key), (int, float))}
                     for day, w in work.items() if isinstance(w, dict)}
        if state.get("cover"):
            try:
                self.set_cover(state["cover"])
            except Exception:
                pass  # la imagen de portada ya no existe: se queda la del PDF
        self.update_quality()  # sin los avisos ignorados
        return True

    def add_version(self, name, auto=False):
        """Guarda lo de ahora como versión. Una automática igual que la última (sin contar los avisos) no se repite.
        True si lo de ahora queda guardado en las versiones; False si no se pudo escribir, con aviso."""
        if not (self.book and self.pdf):
            return False
        try:
            state = json.loads(json.dumps(self.review_state() | {"pdf": _fingerprint(self.pdf)}))
            versions, backup = _load_versions(self.pdf)
            if backup:
                self.status.setText(f"El archivo de versiones estaba dañado: copia en {backup.name}")
            same = [{k: v for k, v in s.items() if k not in VERSION_NOISE} for s in (versions[-1]["state"], state)] \
                if versions else None
            if auto and same and same[0] == same[1]:
                return True
            _append_version(self.pdf, {"name": name, "date": datetime.now().isoformat(timespec="seconds"),
                                        "auto": auto, "state": state})
        except OSError as e:
            self.status.setText(f"No se pudo guardar la versión: {e.strerror or e}")
            return False
        return True

    def restorable(self, state):
        """Una versión vale para este libro si es del mismo análisis: mismos bloques y el mismo PDF (_same_pdf: la
        fecha no: OneDrive la cambia al sincronizar)."""
        try:
            return (state.get("blocks") == self.analyzed and isinstance(state.get("rows"), dict)
                    and _same_pdf(state["pdf"], self.pdf))
        except (OSError, KeyError, TypeError, IndexError):
            return False

    def version_blocks(self, state):
        """Bloques del libro con las ediciones de una versión, sobre el análisis; None si es de otro análisis."""
        if not self.restorable(state):
            return None
        book = dataclasses.replace(self.book, blocks=list(self.pristine[0]), candidates=list(self.pristine[1]))
        _replay(book, state)
        return book.blocks

    def compare(self, old, new=None):
        """Qué cambia de la versión old a la new (None: lo de ahora), como [(texto, tag)] (ver _compare)."""
        if new is None:
            new_state, new_blocks = self.review_state(), self.book.blocks
        else:
            new_state, new_blocks = new, self.version_blocks(new)
        return _compare(old, new_state, self.version_blocks(old), new_blocks)

    def restore_version(self, n):
        """Vuelve a la versión n (en el orden de _load_versions) sin borrar ninguna: antes guarda lo de ahora."""
        try:
            version = _load_versions(self.pdf)[0][n]
        except (OSError, IndexError) as e:
            messagebox.showerror("Versiones", f"No se pudo leer la versión: {getattr(e, 'strerror', None) or e}")
            return False
        if not self.restorable(version["state"]):
            messagebox.showerror("Versiones", "Esta versión es de otro análisis del PDF (cambió el PDF o la forma de "
                                              "leerlo): no se puede restaurar, solo comparar.")
            return False
        # Sin el nombre de la restaurada: restaurar una de estas daría «Antes de restaurar «Antes de restaurar…»»
        if not self.add_version("Antes de restaurar otra versión", auto=True):
            return False  # sin esa copia, lo de ahora se perdería
        self.close_tools(BLOCK_TOOLS)
        self.book.blocks, self.book.candidates, self.book.cover = list(self.pristine[0]), list(self.pristine[1]), \
            self.pristine[2]
        self.cover_choice = None  # la versión pone la suya, si eligió otra
        self.cover_label.setText(f"Portada: la del {'EPUB' if self.source_name() == 'Original' else 'PDF'}")
        self.load_review(version["state"])
        self.history = []  # Ctrl+Z volvería a bloques que ya no están; lo de antes quedó como versión
        self.update_header()
        self.update_cover()
        self.update_preview()
        self.save_review()
        self.status.setText(f"Versión «{version['name']}» restaurada · lo de antes quedó en Versiones")
        return True

    def translation_sections(self):
        """[(inicio, fin, nombre)] de lo que se puede traducir por partes: lo de antes del primer capítulo y cada
        capítulo con sus subtítulos, como las secciones del editor."""
        heads = self.section_heads()
        starts = sorted({0, *heads})
        return [(a, b, self.tree.set(str(a), "title") if a in heads else "Inicio del libro")
                for a, b in zip(starts, starts[1:] + [len(self.book.blocks)])]

    def apply_translation(self, done, dst):
        """Cambia cada párrafo del libro que tiene traducción en done ({clave: {"text"}}) de una vez (un Ctrl+Z lo
        deshace), tras guardar lo de antes como versión «Antes de traducir». Los títulos conservan su tipo y el
        idioma del libro pasa a dst. Devuelve los párrafos cambiados, o None si no se pudo guardar la versión."""
        texts = {k: v["text"] for k, v in done.items()}
        edits, kinds, changed = [], [], 0
        for start, end, _ in reversed(self.translation_sections()):  # de atrás adelante, como apply_edits pide
            old, new = convert.section_text(self.book, start, end), translate.translated_section(self.book, start, end, texts)
            if new != old:
                changed += sum(a != b for a, b in zip(old.split("\n\n"), new.split("\n\n")))
                edits.append([start, end, new])
                # edit_section rehace los bloques de la sección: sus títulos recuperan aquí el tipo de su fila
                kinds.append([self.tree.set(str(j), "inc") for j in range(start, end)
                              if self.book.blocks[j].kind == "heading" and self.tree.exists(str(j))])
        if not edits:
            return 0
        if not self.add_version("Antes de traducir", auto=True):
            return None
        selected = self.tree.selection()
        self.apply_edits(edits, selected[0] if selected else next(iter(self.tree.get_children()), None), kinds=kinds)
        self.lang_var.set(dst)
        self.save_review()
        return changed

    def translate_book(self):
        """Traducción automática con revisión lado a lado, como Ebook Translator de Calibre: cada párrafo con su
        traducción, que se corrige a mano o se pide otra vez; «Aplicar al libro» los cambia de una vez. Lo traducido
        se guarda junto al PDF tras cada lote: se puede cerrar y seguir otro día sin pagar dos veces lo mismo."""
        if not (self.book and self.pdf):
            return None
        old = getattr(self, "translate_window", None)
        # Oculta pero terminando su lote (se cerró a mitad): vuelve la misma, si es de este libro
        if (old and shiboken6.isValid(old) and old.parts["book"] is self.book
                and (old.isVisible() or old.parts["work"]["thread"])):
            old.parts["work"]["closing"] = False
            old.show()
            old.raise_()
            old.activateWindow()
            return old
        pdf = self.pdf  # la ventana guarda siempre en su libro, aunque mientras tanto se abra otro
        saved = _read_translation(pdf)
        done = saved["done"]
        series = self.series_var.get()
        memory = _series_memory(pdf, series)  # lo ya traducido en los otros tomos de la serie
        sources = {v["source"]: k for k, v in memory.items() if v.get("source")}  # original -> su clave
        if not isinstance(self.settings.get("translate"), dict):
            self.settings["translate"] = {}
        prefs = self.settings["translate"]  # el mismo dict que edita Opciones → Traducción
        for name in ("models", "keys"):
            if not isinstance(prefs.get(name), dict):
                prefs[name] = {}
        win, column = self.dialog("Traducir")
        self.translate_window = win
        win.resize(1100, 760)
        src, dst, engine, scope = QComboBox(), QComboBox(), QComboBox(), QComboBox()
        for code, name in translate.LANGS.items():
            src.addItem(name, code)
            if code != "auto":
                dst.addItem(name, code)
        src.setCurrentIndex(max(0, src.findData(saved["src"] if done else prefs.get("src", "auto"))))
        dst.setCurrentIndex(max(0, dst.findData(saved["dst"] if done else prefs.get("dst", "es"))))
        engine.addItems(list(translate.ENGINES))
        engine.setCurrentIndex(max(0, engine.findText(str(prefs.get("engine", "")))))
        sections = self.translation_sections()
        scope.addItem("Todo el libro", None)
        for n, (_, _, title) in enumerate(sections):
            scope.addItem(title, n)
        scope.setMinimumContentsLength(34)
        scope.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        model, base, api_key = QComboBox(), QLineEdit(), QLineEdit()
        # Se elige de los que da «Buscar modelos» u «Otro…» para escribirlo
        model.setMinimumContentsLength(22)
        model.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        model.setPlaceholderText("Elige uno")
        qtk.ask_other(model, lambda now: _ask_line(win, "Modelo", "Nombre del modelo:", now))
        models_btn = _button("Buscar modelos", lambda: find_models())
        api_key.setEchoMode(QLineEdit.EchoMode.Password)
        base.setPlaceholderText("http://localhost:11434/v1")
        base.setMinimumWidth(260)
        model_label, base_label, key_label = QLabel("Modelo:"), QLabel("URL:"), QLabel("Clave:")
        glossary_btn = _button("Glosario y estilo…", lambda: edit_glossary())
        settings = QFrame()
        settings.setObjectName("card")
        inside = QVBoxLayout(settings)
        inside.setContentsMargins(12, 10, 12, 10)
        inside.setSpacing(8)
        # Motor, modelo y clave se eligen en Opciones → Traducción; aquí solo se ven en el resumen. Los controles
        # siguen, ocultos: de ellos leen traducir, revisar con la IA y el glosario
        summary = _label("", "muted")
        engine_btn = _button("Motor y claves…", lambda: self.open_options("Traducción"))
        engine_box = QWidget()
        engine_box.setLayout(_row(engine, model_label, model, models_btn, base_label, base, key_label, api_key))
        engine_box.hide()
        inside.addLayout(_row(QLabel("De:"), src, QLabel("A:"), dst, summary, engine_btn, 1, QLabel("Traducir:"),
                              scope))
        inside.addLayout(_row(engine_box, 1, glossary_btn))
        hint = _label("", "muted")
        hint.setWordWrap(True)
        inside.addWidget(hint)
        column.addWidget(settings)
        # Filtros por estado con su cuenta y buscador: entre miles de párrafos se va directo a lo que falta
        chips, chip_group = {}, QButtonGroup(win)
        for key, label in (("all", "Todos"), ("Pendiente", "Pendientes"), ("Dudoso", "Dudosos"),
                           ("Con fallos", "Con fallos"), ("warn", "Con avisos"), ("De la serie", "De la serie"),
                           ("A mano", "A mano"), ("done", "Traducidos")):
            chip = QPushButton(label)
            chip.setObjectName("chip")
            chip.setCheckable(True)
            chip.clicked.connect(lambda _=False: (work.update(only=None), show_rows()))
            chip_group.addButton(chip)
            chips[key] = chip
        chips["all"].setChecked(True)
        search = QLineEdit()
        search.setPlaceholderText("Buscar en el original o en la traducción")
        search.setClearButtonEnabled(True)
        search.setMinimumWidth(224)
        search.textChanged.connect(lambda _: show_rows())
        lang_filter = QComboBox()
        lang_filter.setMinimumContentsLength(20)
        lang_filter.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        lang_filter.setToolTip("Solo los párrafos traducidos de un idioma a otro: para encontrar los que se tradujeron "
                               "a un idioma por error y volver a traducirlos")
        lang_filter.activated.connect(lambda _: show_rows())
        column.addLayout(_row(*chips.values(), 1, search))  # el filtro de idiomas va abajo: con ocho filtros no cabe
        panes = QSplitter(Qt.Orientation.Vertical)
        column.addWidget(panes, 1)
        table = qtk.RowList(("orig", "trans", "state"), {"orig": "Original", "trans": "Traducción", "state": "Estado"},
                            {"state": 120}, stretch="orig")
        table.setHeaderHidden(True)
        table.setColumnHidden(1, True)  # la traducción va dentro de la tarjeta, bajo el original
        table.setItemDelegate(_Cards(table, lambda: (theme.colors(self.dark), theme.state_colors(self.dark))))
        table.setSelectionMode(table.SelectionMode.ExtendedSelection)  # varios con Ctrl o Mayús: «Retraducir selección»
        panes.addWidget(table)
        bottom = QWidget()
        lower = QGridLayout(bottom)
        lower.setContentsMargins(0, 8, 0, 0)
        lower.setHorizontalSpacing(10)
        for side in (0, 1):  # mitad y mitad: una corrección larga con sus botones no estrecha el original
            lower.setColumnStretch(side, 1)
            lower.setColumnMinimumWidth(side, 300)
        original_box, translation_box = QPlainTextEdit(), QPlainTextEdit()
        for pane in (original_box, translation_box):
            font = pane.font()
            font.setPointSizeF(10.5)
            pane.setFont(font)
        original_box.setReadOnly(True)
        original_box.setPlaceholderText("Elige un párrafo de la lista")
        translation_box.setPlaceholderText("Su traducción: se corrige aquí, a mano")
        # Faltas subrayadas como en «Editar texto…», con destino español
        look = editor.Look(translation_box.document())
        look.set_tags(theme.editor_tags(self.dark))
        translation_box.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        translation_box.customContextMenuRequested.connect(lambda pos: spell_menu(pos))
        typing = QTimer(win)  # lo que se escribe se revisa al parar un momento
        typing.setSingleShot(True)
        typing.setInterval(800)
        typing.timeout.connect(lambda: send_words([translation_box.toPlainText()]))
        translation_box.textChanged.connect(typing.start)
        doubt = _label("", "warn")
        doubt.setWordWrap(True)
        prev_btn, next_btn = _button("◀ Anterior", lambda: step(-1)), _button("Siguiente ▶", lambda: step(1))
        lower.addWidget(_label("ORIGINAL", "muted"), 0, 0)
        lower.addWidget(_label("TRADUCCIÓN · SE CORRIGE AQUÍ", "muted"), 0, 1)
        lower.addWidget(original_box, 1, 0)
        # La corrección que propone la IA revisora, bajo la traducción: en su caja, una larga se lee entera
        fix_box = QPlainTextEdit()
        fix_box.setReadOnly(True)
        fix_box.setFont(translation_box.font())
        fix_box.setMaximumHeight(84)
        fix_box.setToolTip("Corrección que propone la IA: «Aceptar corrección» la pone como traducción")
        fix_box.hide()
        right = QVBoxLayout()
        right.setSpacing(6)
        right.addWidget(translation_box, 1)
        right.addWidget(fix_box)
        lower.addLayout(right, 1, 1)
        # Lo que propone la IA revisora para el párrafo elegido: se acepta o se da por bueno lo que hay
        accept_btn = _button("Aceptar corrección", lambda: settle(True), "primary")
        discard_btn = _button("Descartar", lambda: settle(False))
        discard_btn.setToolTip("La traducción se queda como está y cuenta como revisada")
        accept_all_btn = _button("Aceptar todas", lambda: settle_all())
        accept_all_btn.setToolTip("Pone la corrección de la IA en todos los párrafos «Con fallos» de una vez")
        accept_btn.hide()
        discard_btn.hide()
        accept_all_btn.hide()
        lower.addLayout(_row(prev_btn, next_btn, 1, lang_filter), 2, 0)
        fixes = _row(doubt, accept_btn, accept_all_btn, discard_btn)
        fixes.setStretch(0, 1)
        lower.addLayout(fixes, 2, 1)
        # Un párrafo pendiente que se parece a uno ya traducido en otro tomo de la serie
        similar_label, similar_btn = _label("", "muted"), _button("Usar esta", lambda: use_similar())
        similar_label.setWordWrap(True)
        similar_row = QWidget()
        similar_row.setLayout(_row(similar_label, similar_btn))
        similar_row.layout().setStretch(0, 1)
        similar_row.hide()
        lower.addWidget(similar_row, 3, 0, 1, 2)
        panes.addWidget(bottom)
        panes.setSizes([470, 200])
        panes.setStretchFactor(0, 3)  # al encoger la ventana (zoom de Windows), la lista conserva más alto
        progress = QProgressBar()
        progress.setTextVisible(False)
        progress.hide()
        column.addWidget(progress)
        status = StatusLabel()
        column.addWidget(status)
        run_btn = _button("Traducir", lambda: run(), "primary")
        again_btn = _button("Retraducir selección", lambda: run(again=True))
        apply_btn = _button("Aplicar al libro", lambda: apply())
        coherence_btn = _button("Coherencia…", lambda: coherence())
        coherence_btn.setToolTip("Nombres y términos del original que la traducción escribe de varias formas")
        review_btn = _button("Revisar con IA", lambda: review())
        review_btn.setToolTip("La IA compara cada traducción con su original y marca las que cambian el sentido, se "
                              "dejan algo o no suenan naturales, con una corrección. Revisa los párrafos elegidos "
                              "(dos o más) o, si no, todo lo traducido que aún no miró.")
        column.addLayout(_row(run_btn, again_btn, review_btn, coherence_btn, 1, apply_btn,
                              _button("Cerrar", lambda: close())))
        # only: claves que se dejan a la vista (las de «Coherencia…»), hasta que se pulsa un filtro
        work = {"thread": None, "stop": None, "queue": None, "shown": None, "again": set(), "engine": "", "count": 0,
                "total": 0, "closing": False, "langs": None, "only": None, "check": 0, "quality": {}}
        warnings = {}  # clave -> avisos de español de su traducción (informe de calidad y ortografía)
        # Proceso de ortografía (arranca con la primera traducción al español), lo que ya dijo y lo que se le pidió
        spell = {"proc": None, "unknown": set(), "checked": set(), "suggestions": {}}
        rows = {}  # fila (índice del bloque) -> (clave, prefijo, texto original)
        index = {}  # clave -> sus filas: un lote no recorre la lista entera (miles de párrafos)
        hints = {"google": "Gratis y sin cuenta, frase a frase: los diálogos salen más literales. Si Google corta por "
                           "exceso de peticiones, la traducción espera y lo vuelve a intentar.",
                 "gcloud": "La API oficial de Google: no corta por exceso de peticiones y trae 500.000 caracteres "
                           "gratis al mes; pide una clave de Google Cloud con la facturación activada.",
                 "deepl": "Más natural que Google, frase a frase. La clave gratuita (acaba en «:fx») trae 500.000 "
                          "caracteres al mes. No usa el glosario.",
                 "openai": "La IA traduce varios capítulos a la vez viendo el capítulo entero, el glosario y la ficha de "
                           "personajes de «Glosario y estilo…», y repasa sola lo dudoso. Se paga por uso con tu clave.",
                 "custom": "Modelo en tu equipo (Ollama, LM Studio): gratis y privado, lento sin una buena tarjeta "
                           "gráfica. Escribe la URL de su API y el nombre del modelo."}

        def remember():
            """Guarda en las preferencias lo escrito para el motor elegido; la clave, cifrada."""
            name = work["engine"]
            if name:
                prefs["models"][name] = model.currentText().strip()
                try:
                    prefs["keys"][name] = translate.protect(api_key.text().strip())
                except OSError:
                    prefs["keys"].pop(name, None)  # sin cifrar no se guarda: se vuelve a pedir
                if translate.ENGINES[name].get("custom"):
                    prefs["base"] = base.text().strip()
            prefs.update(engine=engine.currentText(), src=src.currentData(), dst=dst.currentData())
            self.settings["translate"] = prefs
            self.save_settings()

        def show_engine():
            name = work["engine"] = engine.currentText()
            info = translate.ENGINES[name]
            # la lista buscada era de otro motor
            qtk.fill_choices(model, [], prefs["models"].get(name) or info.get("model", ""))
            api_key.setText(translate.unprotect(prefs["keys"].get(name, "")))
            base.setText(prefs.get("base") or info.get("base", ""))
            api_key.setPlaceholderText("Opcional" if info.get("custom") else "Clave API")
            hint.setText(hints["custom" if info.get("custom") else "openai" if "model" in info else info["kind"]])
            missing = info.get("key") and not api_key.text().strip()
            summary.setText(f"Motor: {name}" + (f" · {model.currentText().strip()}" if "model" in info else "")
                            + (" · falta la clave" if missing else ""))

        def reload():
            """Tras «Opciones»: el motor y la clave de las preferencias, que acaban de cambiar."""
            if shiboken6.isValid(win):
                engine.setCurrentIndex(max(0, engine.findText(str(prefs.get("engine", "")))))
                show_engine()

        def applied():
            """Textos ya puestos en el libro: un párrafo así está «Aplicado», no pendiente."""
            return {" ".join(v["text"].split()) for v in done.values()}

        def reviewed(k):
            """Lo que dijo la IA revisora de la traducción de clave k tal como está ahora, o None: si la traducción
            cambió después, aquello ya no vale."""
            entry = done.get(k) or {}
            found = entry.get("review")
            return found if found and found.get("for") == translate.key(entry["text"]) else None

        def state_of(k, text, texts):
            if k in done:
                if done[k]["hand"]:
                    return "A mano"
                found = reviewed(k)
                if found and found.get("problem"):
                    return "Con fallos"
                if translate.doubts(text, done[k]["text"], done[k].get("dst", dst.currentData()), self.tr_glossary):
                    return "Dudoso"
                return "Revisado" if found else "De la serie" if done[k].get("memory") else "Traducido"
            return "Aplicado" if " ".join(text.split()) in texts else "Pendiente"

        def fill():
            keep()
            work["shown"] = None
            table.delete(*table.get_children())
            rows.clear()
            index.clear()
            texts, taken = applied(), 0
            for start, end, _ in sections if scope.currentData() is None else [sections[scope.currentData()]]:
                for k, prefix, text in translate.units(self.book, start, end):
                    code = translate.key(text)
                    known = memory.get(code)
                    # Ya traducido en otro tomo de la serie, a este idioma: no se paga otra vez
                    if (known and code not in done and known["dst"] == dst.currentData()
                            and " ".join(text.split()) not in texts):
                        done[code] = {"text": known["text"], "hand": False, "src": known["src"], "dst": known["dst"],
                                      "memory": True, "source": text}
                        taken += 1
                    rows[str(k)] = code, prefix, text
                    index.setdefault(code, []).append(str(k))
                    table.insert("", "end", iid=str(k), values=(("# " if prefix == "# " else "") + text,
                                                                 done.get(code, {}).get("text", ""),
                                                                 state_of(code, text, texts)))
                    mark_langs(str(k))
            if taken:
                save()
            pick()
            count()
            check()
            if taken:
                status.setText(f"{convert.counted(taken, 'párrafo')} ya traducidos en otros tomos de la serie: están "
                               f"en el filtro «De la serie».")

        def langs(k):
            """(origen, destino) con que se tradujo el párrafo de clave k, o None si no tiene traducción."""
            entry = done.get(k)
            return (entry.get("src", "auto"), entry.get("dst", dst.currentData())) if entry else None

        def mark_langs(iid):
            """«EN → ES» bajo el estado; en color de aviso si el destino no es el elegido arriba."""
            pair = langs(rows[iid][0])
            item = table.rows[iid]
            item.setData(2, LANG_ROLE, (f"{lang_code(pair[0])} → {lang_code(pair[1])}", pair[1] != dst.currentData())
                         if pair else None)

        def count():
            todo = [rows[i][2] for i in table.get_children() if table.set(i, "state") == "Pendiente"]
            letters = f"{sum(map(len, todo)):,}".replace(",", ".")
            status.setText(f"{convert.counted(len(todo), 'párrafo pendiente', 'párrafos pendientes')} de {len(rows)} · "
                           f"{letters} caracteres por traducir")
            apply_btn.setEnabled(bool(done) and work["thread"] is None)
            states = collections.Counter(item.text(2) for item in table.rows.values())
            for key, chip in chips.items():
                label = chip.text().rsplit(" ", 1)[0] if chip.text()[-1:].isdigit() else chip.text()
                many = (len(rows) if key == "all" else sum(states[s] for s in DONE_STATES) if key == "done"
                        else sum(1 for i in rows if rows[i][0] in warnings) if key == "warn" else states[key])
                chip.setText(f"{label} {many}")
            pairs = collections.Counter(langs(rows[i][0]) for i in rows if rows[i][0] in done)
            chosen = lang_filter.currentData()
            lang_filter.clear()
            lang_filter.addItem("Todos los idiomas", None)
            for (a, b), many in sorted(pairs.items(), key=lambda p: -p[1]):
                # «es>ja» y no la tupla: Qt la devuelve como lista y no se encontraría
                lang_filter.addItem(f"{translate.LANGS.get(a, a)} → {translate.LANGS.get(b, b)} ({many})", f"{a}>{b}")
            lang_filter.setCurrentIndex(max(0, lang_filter.findData(chosen)))
            show_rows()

        def show_rows():
            """Solo las filas del filtro elegido que tienen lo buscado, en el original o en la traducción."""
            want = next(key for key, chip in chips.items() if chip.isChecked())
            query, pair, only = search.text().strip().lower(), lang_filter.currentData(), work["only"]
            for iid, item in table.rows.items():
                state = item.text(2)
                shown = (want == "all" or state == want or (want == "done" and state in DONE_STATES)
                         or (want == "warn" and rows[iid][0] in warnings))
                shown = shown and (only is None or rows[iid][0] in only)
                shown = shown and (pair is None or ">".join(langs(rows[iid][0]) or ()) == pair)
                item.setHidden(not shown or bool(query and query not in item.text(0).lower()
                                                  and query not in item.text(1).lower()))

        def step(delta):
            """«Anterior» y «Siguiente»: la fila de al lado que se ve, sin perder lo corregido."""
            current = table.currentItem()
            item = (table.itemBelow if delta > 0 else table.itemAbove)(current) if current else table.topLevelItem(0)
            while item is not None and item.isHidden():
                item = (table.itemBelow if delta > 0 else table.itemAbove)(item)
            if item is not None:
                table.selection_set(item.data(0, qtk.IID))
                table.see(item.data(0, qtk.IID))
                translation_box.setFocus()

        def refresh(keys):
            texts = applied()
            for k in keys:
                for iid in index.get(k, ()):
                    table.set(iid, "trans", done.get(k, {}).get("text", ""))
                    table.set(iid, "state", state_of(k, rows[iid][2], texts))
                    mark_langs(iid)
                    if iid == work["shown"]:
                        translation_box.setPlainText(done.get(k, {}).get("text", ""))
            count()

        def save():
            try:
                _write_json(_translation_path(pdf), {"src": src.currentData(), "dst": dst.currentData(), "done": done})
                _remember_translation(pdf, series)
            except OSError as e:
                status.setText(f"No se pudo guardar la traducción: {e.strerror or e}")

        def keep():
            """Lo corregido a mano en el párrafo que se estaba viendo."""
            iid = work["shown"]
            if iid not in rows:
                return
            k = rows[iid][0]
            new = " ".join(translation_box.toPlainText().split())
            if new == " ".join(done.get(k, {}).get("text", "").split()):
                return
            if new:
                old = done.get(k, {})
                done[k] = {"text": new, "hand": True, "src": old.get("src", src.currentData()),
                           "dst": old.get("dst", dst.currentData()), "source": rows[iid][2]}
            else:
                done.pop(k, None)
            refresh({k})
            save()
            count()
            check()

        def spanish():
            return dst.currentData() == "es"  # la ortografía y los avisos de calidad solo saben de español

        def send_words(texts):
            """Manda al proceso de ortografía las palabras de texts que aún no revisó; lo arranca si hace falta."""
            if not spanish():
                return
            skip = convert.spell_skip(self.words)
            new = {w for text in texts for w in convert.spell_words(MARKUP.sub(" ", text))
                   if convert.spell_candidate(w, skip)} - spell["checked"]
            if not new:
                return
            if not spell["proc"]:
                spell["proc"] = _start_spell_process()
                if not spell["proc"]:
                    return  # sin ortografía, lo demás sigue
                qtk.after(300, poll_spell)
            try:
                spell["proc"][0].put(("check", sorted(new)))
                spell["checked"] |= new
            except (OSError, ValueError):
                spell["proc"] = None

        def poll_spell():
            if not spell["proc"] or not shiboken6.isValid(win):
                return
            new = set()
            while True:
                try:
                    reply = spell["proc"][1].get_nowait()
                except queue.Empty:
                    break
                except (OSError, ValueError, EOFError):
                    spell["proc"] = None
                    return
                if reply[0] == "unknown":
                    new |= set(reply[1]) - spell["unknown"]
                elif reply[0] == "suggestions":
                    spell["suggestions"][reply[1]] = reply[2]
            if new:
                spell["unknown"] |= new
                warn()
            if spell["proc"][2].is_alive():
                qtk.after(300, poll_spell)
            else:
                spell["proc"] = None

        def stop_spell():
            if spell["proc"]:
                spell["proc"][2].terminate()  # nada que guardar: lo aprendido ya está en self.words
                spell["proc"] = None

        def misspelled():
            """Palabras que el diccionario no conoce y la serie no da por buenas."""
            return spell["unknown"] - convert.spell_skip(self.words) if spanish() else set()

        def add_word(word):
            """Al diccionario de la serie (el de «Ortografía…»): no se vuelve a marcar en ningún tomo."""
            if word not in self.words:
                self.words.append(word)
                self.save_review()
            warn()

        def spell_menu(pos):
            """Clic derecho en la traducción, en español: sugerencias de la falta de ahí y lo de siempre."""
            cursor = translation_box.cursorForPosition(pos)
            cursor.select(QTextCursor.SelectionType.WordUnderCursor)
            word, menu = cursor.selectedText(), QMenu(translation_box)
            if word and word.lower() in look.unknown:
                suggestions = spell["suggestions"].get(word)
                if suggestions is None and spell["proc"]:
                    try:
                        spell["proc"][0].put(("first", word))
                    except (OSError, ValueError):
                        spell["proc"] = None
                for suggestion in suggestions or ():
                    action = menu.addAction(suggestion, lambda s=suggestion: cursor.insertText(s))
                    font = action.font()
                    font.setBold(True)
                    action.setFont(font)
                if not suggestions:  # llegan en un momento: al volver a pulsar ya están
                    menu.addAction("Buscando sugerencias: vuelve a pulsar" if suggestions is None
                                   else "Sin sugerencias").setEnabled(False)
                menu.addAction("Añadir al diccionario de la serie", lambda: add_word(word))
                menu.addSeparator()
            menu.addAction("Deshacer", translation_box.undo)
            menu.addSeparator()
            menu.addAction("Cortar", translation_box.cut)
            menu.addAction("Copiar", translation_box.copy)
            menu.addAction("Pegar", translation_box.paste)
            menu.addAction("Seleccionar todo", translation_box.selectAll)
            menu.exec(translation_box.mapToGlobal(pos))

        def warn(found=None):
            """Pone en cada fila sus avisos de español: los del informe de calidad (found, si llega uno nuevo) y las
            palabras que no están en el diccionario. La tarjeta dice cuántos; el detalle, al elegir la fila."""
            if found is not None:
                work["quality"] = found
            unknown = misspelled()
            look.set_unknown(unknown)
            warnings.clear()
            for k in {code for code, _, _ in rows.values() if code in done} if spanish() else ():
                lines = [f"{title}: {problem}" for title, problem in work["quality"].get(k, ())]
                lines += [f"No está en el diccionario: «{w}»" for w in dict.fromkeys(
                    convert.spell_words(MARKUP.sub(" ", done[k]["text"]))) if w in unknown]
                if lines:
                    warnings[k] = lines
            for iid, item in table.rows.items():
                item.setData(2, WARN_ROLE, len(warnings.get(rows[iid][0], ())) or None)
            count()
            notes()

        def check(later=True):
            """Revisa el español de las traducciones de la lista: ortografía (en su proceso) e informe de calidad (en
            un hilo: con un libro entero tarda un segundo y la ventana se quedaría parada). later=False, ya."""
            work["check"] += 1  # un informe pedido antes ya no vale
            if not spanish():
                warn({})
                return
            pairs = list({code: (code, done[code]["text"]) for code, _, _ in rows.values() if code in done}.values())
            send_words(text for _, text in pairs)
            if not later:
                warn(translate.quality(pairs, self.glossary))
                return
            job, found, glossary = work["check"], queue.Queue(), list(self.glossary)

            def report():
                try:
                    found.put(translate.quality(pairs, glossary))
                except Exception:
                    found.put({})
                    _log(traceback.format_exc())

            def wait():
                if not shiboken6.isValid(win) or job != work["check"]:
                    return
                try:
                    warn(found.get_nowait())
                except queue.Empty:
                    qtk.after(200, wait)

            threading.Thread(target=report, daemon=True).start()
            qtk.after(200, wait)

        def notes():
            """Bajo la traducción elegida, por qué merece otro vistazo: lo dudoso y sus avisos de español."""
            iid = work["shown"]
            lines, state = [], table.set(iid, "state") if iid in rows else ""
            for widget in (accept_btn, discard_btn, fix_box):
                widget.setVisible(state == "Con fallos")
            accept_all_btn.setVisible(state == "Con fallos" and len(faulty()) > 1)
            if state == "Con fallos":
                found = reviewed(rows[iid][0])
                lines.append("Revisión de la IA: " + found["problem"] + " Su corrección está bajo la traducción.")
                fix_box.setPlainText(found["fix"])
            if state == "Dudoso":  # la IA ya lo repasó una vez y sigue así
                lines.append("Dudoso: " + "; ".join(translate.doubts(
                    rows[iid][2], done[rows[iid][0]]["text"], dst.currentData(), self.tr_glossary)) + ".")
            if iid in rows:
                lines += warnings.get(rows[iid][0], ())
            doubt.setText("\n".join("▲ " + line for line in lines))
            return lines

        def faulty():
            """Claves de los párrafos con una corrección de la IA pendiente («Con fallos»)."""
            return [k for k in done if (reviewed(k) or {}).get("problem")]

        def settle_one(k, accept):
            entry, found = done[k], reviewed(k)
            if accept:
                entry["text"] = found["fix"]
                entry.pop("memory", None)  # ya no es la del otro tomo
            entry["review"] = {"for": translate.key(entry["text"]), "ok": True}

        def settle(accept):
            """Cierra la revisión de la IA del párrafo elegido: con accept, su corrección pasa a ser la traducción; si
            no, vale la que hay. De las dos formas queda «Revisado»."""
            iid = work["shown"]
            found = reviewed(rows[iid][0]) if iid in rows else None
            if not (found and found.get("problem")):
                return
            settle_one(rows[iid][0], accept)
            refresh({rows[iid][0]})
            save()
            notes()
            check()

        def settle_all():
            """Todas las correcciones de la IA de una vez: en un tomo entero son cientos (Making Magic: 9 de 117
            párrafos, 8 de ellas buenas), una a una no se acaba. Pregunta antes, con cuántas son."""
            keys = faulty()
            if not keys or not messagebox.askyesno(
                    "Aceptar correcciones",
                    f"¿Poner la corrección de la IA en {convert.counted(len(keys), 'párrafo')} «Con fallos»?\n\n"
                    "La IA revisora también se equivoca a veces: si puedes, míralas antes con el filtro «Con fallos». "
                    "Quedan como revisados; las que no te convenzan, corrígelas después a mano.",
                    parent=win):
                return
            for k in keys:
                settle_one(k, True)
            refresh(set(keys))
            save()
            notes()
            check()

        def pick():
            keep()
            selected = table.selection()
            iid = work["shown"] = selected[0] if selected else None
            original_box.setPlainText(rows[iid][2] if iid else "")
            translation_box.setPlainText(done.get(rows[iid][0], {}).get("text", "") if iid else "")
            translation_box.setEnabled(iid is not None)
            typing.stop()  # el texto puesto ya está revisado: no es algo que se haya escrito
            found = notes()
            if found and found[0].startswith("Dudoso"):
                status.setText(found[0])
            near = (translate.similar(rows[iid][2], sources)
                    if iid and sources and table.set(iid, "state") == "Pendiente" else None)
            work["similar"] = memory[sources[near[0]]]["text"] if near else None
            similar_row.setVisible(bool(near))
            if near:
                entry = memory[sources[near[0]]]
                where = f"el tomo {entry['volume']}" if entry["volume"] else "otro tomo"
                similar_label.setText(f"Parecido ({round(100 * near[1])} %) en {where} de la serie: "
                                      f"«{convert._cut(MARKUP.sub('', near[0]))}» → "
                                      f"«{convert._cut(MARKUP.sub('', entry['text']))}»")

        def use_similar():
            """La traducción del párrafo parecido de otro tomo, para corregir lo que cambia: queda «A mano»."""
            if work.get("similar") and work["shown"] in rows:
                translation_box.setPlainText(work["similar"])
                keep()
                similar_row.hide()
                translation_box.setFocus()

        def busy(on):
            run_btn.setText("Pausar" if on else "Traducir")
            run_btn.setEnabled(True)
            progress.setVisible(on)
            progress.setRange(0, max(1, work["total"]))
            progress.setValue(work["count"])
            for widget in (again_btn, src, dst, engine, scope, model, models_btn, base, api_key, glossary_btn,
                           coherence_btn, review_btn):
                widget.setEnabled(not on)
            count()
            if not on:
                check()  # la tanda acabó: sus avisos

        def run(again=False):
            if work["thread"]:  # «Pausar»: el lote en curso termina y se guarda
                work["stop"].set()
                run_btn.setEnabled(False)
                run_btn.setText("Pausando…")
                return
            keep()
            if src.currentData() == dst.currentData():
                messagebox.showinfo("Traducir", "El idioma de origen y el de destino son el mismo.", parent=win)
                return
            picked = {i for i in table.selection() if not table.rows[i].isHidden()}
            ids = [i for i in table.get_children() if (i in picked if again else table.set(i, "state") == "Pendiente")]
            if not ids:
                messagebox.showinfo("Traducir", "Elige en la lista los párrafos que quieres volver a traducir." if again
                                    else "No queda nada pendiente de traducir aquí.", parent=win)
                return
            name = engine.currentText()
            info = translate.ENGINES[name]
            if info.get("key") and not api_key.text().strip():
                messagebox.showerror("Traducir", f"Falta la clave de {name}. Ponla en «Motor y claves…».", parent=win)
                self.open_options("Traducción")
                return
            remember()
            order, wanted = table.get_children(), set(ids)
            place = {iid: n for n, iid in enumerate(order)}
            originals = {k: t for k, _, t in rows.values()}
            ai, cloud = info["kind"] in translate.AI, info["kind"] != "google" and not info.get("custom")
            style = {"variant": prefs.get("variant", ""), "honorifics": prefs.get("honorifics", True),
                     "notes": self.tr_notes,
                     # Lo corregido a mano enseña a la IA el estilo que se quiere
                     "examples": [(originals[k], v["text"]) for k, v in done.items()
                                  if v["hand"] and k in originals and 20 <= len(originals[k]) <= 400][-5:]}
            groups, seen = [], set()
            for start, end, _ in sections if scope.currentData() is None else [sections[scope.currentData()]]:
                members = [i for i in order if start <= int(i) < end]
                mine = [i for i in members if i in wanted]
                # «—¿Eh?» repetido, una vez en todo el libro
                items = list(dict.fromkeys((rows[i][0], rows[i][2]) for i in mine if rows[i][0] not in seen))
                if not items:
                    continue
                seen.update(k for k, _ in items)
                before = [rows[i] for i in order[max(0, place[mine[0]] - 60):place[mine[0]]]]
                groups.append({"items": items,
                               "context": [(t, done[k]["text"]) for k, _, t in before if k in done][-translate.CONTEXT:],
                               # Un modelo local suele tener poca memoria de contexto: el capítulo no le cabe
                               "chapter": "\n".join(rows[i][2] for i in members)
                               if ai and cloud and prefs.get("chapter", True) else ""})
            job = {"engine": name, "src": src.currentData(), "dst": dst.currentData(), "key": api_key.text().strip(),
                   "model": model.currentText().strip(), "base": base.text().strip() if info.get("custom") else "",
                   "glossary": list(self.tr_glossary), "groups": groups, "style": style,
                   # Google gratis corta con peticiones seguidas y un modelo local va de una en una
                   "workers": 3 if cloud else 1}
            keys = {k for group in groups for k, _ in group["items"]}
            work.update(stop=threading.Event(), queue=queue.Queue(), count=0, total=len(keys), mode="",
                        again=keys if again else set(), langs=(job["src"], job["dst"]))
            work["thread"] = threading.Thread(target=_translate_thread, args=(job, work["queue"], work["stop"]),
                                              daemon=True)
            work["thread"].start()
            busy(True)
            status.setText(f"Traduciendo con {name}… 0 de {len(keys)} párrafos")
            qtk.after(200, poll)

        def review():
            """La IA compara lo traducido con su original y marca lo que está mal, con una corrección que se acepta o
            se descarta. Revisa las filas elegidas (dos o más) o, si no, lo traducido de la lista que aún no miró;
            nunca lo corregido a mano, que ya lo vio una persona."""
            if work["thread"]:
                return
            keep()
            name = engine.currentText()
            info = translate.ENGINES[name]
            if info["kind"] not in translate.AI:
                messagebox.showinfo("Revisar con IA", "Solo una IA puede revisar la traducción: elige una en «Motor» "
                                                      "(OpenAI, DeepSeek, Gemini, Claude…).", parent=win)
                return
            if info.get("key") and not api_key.text().strip():
                messagebox.showerror("Revisar con IA", f"Falta la clave de {name}. Ponla en «Motor y claves…».", parent=win)
                self.open_options("Traducción")
                return
            order = table.get_children()
            picked = {i for i in table.selection() if not table.rows[i].isHidden()}
            mine = lambda i: (rows[i][0] in done and not done[rows[i][0]]["hand"]
                              and done[rows[i][0]].get("dst", dst.currentData()) == dst.currentData())
            wanted = {i for i in order if mine(i) and (i in picked if len(picked) > 1 else not reviewed(rows[i][0]))}
            if not wanted:
                messagebox.showinfo("Revisar con IA", "Aquí no queda nada traducido sin revisar. Para revisar otra vez, "
                                                      "elige dos párrafos o más (lo corregido a mano no se revisa).",
                                    parent=win)
                return
            remember()
            cloud, groups, seen = not info.get("custom"), [], set()
            for start, end, _ in sections if scope.currentData() is None else [sections[scope.currentData()]]:
                members = [i for i in order if start <= int(i) < end]
                items = list(dict.fromkeys((rows[i][0], rows[i][2], done[rows[i][0]]["text"]) for i in members
                                           if i in wanted and rows[i][0] not in seen))
                if items:
                    seen.update(k for k, _, _ in items)
                    groups.append({"items": items, "chapter": "\n".join(rows[i][2] for i in members)
                                   if cloud and prefs.get("chapter", True) else ""})
            job = {"mode": "review", "engine": name, "src": src.currentData(), "dst": dst.currentData(),
                   "key": api_key.text().strip(), "model": model.currentText().strip(),
                   "base": base.text().strip() if info.get("custom") else "", "glossary": list(self.tr_glossary),
                   "groups": groups, "style": {"notes": self.tr_notes}, "workers": 3 if cloud else 1}
            work.update(stop=threading.Event(), queue=queue.Queue(), count=0, total=len(seen), again=set(),
                        mode="review", langs=None)
            work["thread"] = threading.Thread(target=_translate_thread, args=(job, work["queue"], work["stop"]),
                                              daemon=True)
            work["thread"].start()
            busy(True)
            status.setText(f"Revisando con {name}… 0 de {len(seen)} párrafos")
            qtk.after(200, poll)

        def merge_review(found):
            """Pone cada revisión llegada si su traducción sigue siendo la que la IA miró: una corregida a mano o
            traducida otra vez mientras tanto se queda sin revisión."""
            for k, (text, result) in found.items():
                entry = done.get(k)
                if result is not False and entry and not entry["hand"] and entry["text"] == text:
                    entry["review"] = {"for": translate.key(text)} | (result or {"ok": True})
            refresh(set(found))
            save()

        def poll():
            if not shiboken6.isValid(win):
                return
            events, arrived, checked = [], {}, {}
            while True:
                try:
                    events.append(work["queue"].get_nowait())
                except queue.Empty:
                    break
            for event in events:
                if event[0] == "done":
                    arrived.update(event[1])
                elif event[0] == "reviewed":
                    checked.update(event[1])
            if checked:
                merge_review(checked)
                work["count"] += len(checked)
                progress.setValue(work["count"])
                status.setText(f"Revisando… {work['count']} de {work['total']} párrafos "
                               f"({100 * work['count'] // max(1, work['total'])} %)")
            if arrived:  # todos los lotes llegados, de una vez: con Google llegan muchos y cada guardado escribe todo
                # Cerrada a mitad de lote y abierta otra vez sobre el mismo libro: lo que llega va a la nueva, que es
                # la que guarda (si guardaran las dos, una pisaría lo de la otra)
                twins = [w for w in self.root.findChildren(Dialog) if w is not win and shiboken6.isValid(w)
                         and "merge" in getattr(w, "parts", {}) and w.parts["pdf"] == pdf and w.isVisible()]
                merge(arrived, work["again"], store=not twins, langs=work["langs"])
                for twin in twins:
                    twin.parts["merge"](arrived, work["again"], langs=work["langs"])
                work["count"] += len(arrived)
                progress.setValue(work["count"])
                status.setText(f"Traduciendo… {work['count']} de {work['total']} párrafos "
                               f"({100 * work['count'] // max(1, work['total'])} %)")
            for event in events:
                if event[0] == "error" and not work["closing"]:  # cerrada: nadie lo leería
                    messagebox.showerror("Traducir", event[1], parent=win)
                elif event[0] == "log":
                    _log(event[1])
                elif event[0] == "end":
                    work["thread"] = None
                    if work["closing"]:  # su lote ya está guardado: ahora sí se cierra
                        win.deleteLater()
                        return
                    busy(False)
                    if work["mode"] == "review":
                        bad = sum(1 for item in table.rows.values() if item.text(2) == "Con fallos")
                        status.setText(f"Revisión terminada: {convert.counted(bad, 'párrafo')} con fallos."
                                       + (" Están en el filtro «Con fallos», cada uno con su corrección." if bad else ""))
                    return
            qtk.after(200, poll)

        def merge(arrived, again=(), store=True, langs=None):
            """Pone las traducciones llegadas, salvo las corregidas a mano mientras tanto (si no se pidieron otra vez)."""
            for k, text in arrived.items():
                if not (done.get(k, {}).get("hand") and k not in again):
                    src_code, dst_code = langs or (src.currentData(), dst.currentData())
                    done[k] = {"text": text, "hand": False, "src": src_code, "dst": dst_code}
                    if k in index:  # el original, para la memoria de la serie
                        done[k]["source"] = rows[index[k][0]][2]
            refresh(set(arrived))
            if store:
                save()
            if work["thread"] is None:
                count()

        def find_models():
            """Pide a la API los modelos de hoy con esta clave, en un hilo: sin conexión tardaría en fallar."""
            name, info = engine.currentText(), translate.ENGINES[engine.currentText()]
            if info.get("key") and not api_key.text().strip():
                messagebox.showerror("Traducir", f"Falta la clave de {name}: sin ella la API no da la lista.", parent=win)
                return
            found, url = queue.Queue(), base.text().strip() if info.get("custom") else ""

            def ask():
                try:
                    found.put(translate.models(name, api_key.text().strip(), url))
                except Exception as e:  # TranslateError ya viene en español
                    found.put(str(e) or type(e).__name__)

            models_btn.setEnabled(False)
            models_btn.setText("Buscando…")
            threading.Thread(target=ask, daemon=True).start()

            def wait():
                if not shiboken6.isValid(win):
                    return
                try:
                    result = found.get_nowait()
                except queue.Empty:
                    qtk.after(200, wait)
                    return
                models_btn.setText("Buscar modelos")
                models_btn.setEnabled(work["thread"] is None)
                if isinstance(result, str) or not result or engine.currentText() != name:
                    if engine.currentText() == name:
                        messagebox.showerror("Traducir", result or f"{name} no devolvió ningún modelo.", parent=win)
                    return
                qtk.fill_choices(model, result, model.currentText().strip())
                status.setText(f"{name} ofrece {convert.counted(len(result), 'modelo')}: elige uno en «Modelo».")

            qtk.after(200, wait)

        def apply():
            keep()
            book_keys = {translate.key(t) for _, _, t in translate.units(self.book, 0, len(self.book.blocks))}
            other = collections.Counter(done[k]["dst"] for k in book_keys
                                        if k in done and done[k].get("dst", dst.currentData()) != dst.currentData())
            if other:
                where = ", ".join(f"{translate.LANGS.get(c, c)}: {n}" for c, n in other.most_common())
                if not messagebox.askyesno(
                        "Traducir", f"{convert.counted(sum(other.values()), 'párrafo está traducido', 'párrafos están traducidos')} "
                                    f"a otro idioma que el elegido ({dst.currentText()}): {where}.\n\nPara "
                                    f"encontrarlos, elige su idioma en el filtro de la lista, selecciónalos con Ctrl+A "
                                    f"y pulsa «Retraducir selección».\n\n¿Aplicar igual?", parent=win):
                    return
            changed = self.apply_translation(done, dst.currentData())
            if changed is None:
                messagebox.showerror("Traducir", "No se pudo guardar la versión «Antes de traducir»: el libro queda "
                                                 "como estaba.", parent=win)
                return
            fill()
            status.setText(f"{convert.counted(changed, 'párrafo cambiado', 'párrafos cambiados')} en el libro; el idioma "
                           f"pasa a {dst.currentText()}. Ctrl+Z en la lista lo deshace."
                           if changed else "Nada que cambiar: lo traducido ya está en el libro.")

        def coherence():
            """Términos del original que la traducción escribe de varias formas: se elige una, entra en el glosario de
            la serie y la lista deja a la vista los párrafos con las otras, para retraducirlos o corregirlos. No se
            cambia el texto solo: el género y el número romperían la frase."""
            keep()
            pairs = list({code: (code, text, done[code]["text"])
                          for _, _, text in translate.units(self.book, 0, len(self.book.blocks))
                          if (code := translate.key(text)) in done}.values())
            found = translate.inconsistent(pairs, self.tr_glossary)
            if not found:
                messagebox.showinfo("Coherencia", "No se ve ningún término traducido de varias formas."
                                    if pairs else "Aún no hay nada traducido que comparar.", parent=win)
                return
            box, inner = self.dialog("Coherencia de términos", modal=True)
            box.resize(780, 520)
            note = _label("Estos nombres y términos del original están traducidos de más de una forma. Elige uno, "
                          "la forma que debe quedar y «Usar esta forma»: entra en el glosario y la lista te deja "
                          "los párrafos con las otras formas para retraducirlos o corregirlos.", "muted")
            note.setWordWrap(True)
            inner.addWidget(note)
            terms = qtk.RowList(("term", "forms"), {"term": "Término del original", "forms": "Cómo está traducido"},
                                {"term": 220}, stretch="forms")
            for n, (term, forms) in enumerate(found):
                terms.insert("", "end", iid=str(n), values=(term, " · ".join(f"«{f}» {len(keys)}" for f, keys in forms)))
            inner.addWidget(terms, 1)
            choice = QComboBox()
            choice.setMinimumContentsLength(30)

            def show_forms():
                choice.clear()
                for iid in terms.selection()[:1]:
                    choice.addItems([form for form, _ in found[int(iid)][1]])

            def use():
                if not terms.selection() or choice.currentIndex() < 0:
                    return
                term, forms = found[int(terms.selection()[0])]
                self.tr_glossary = [pair for pair in self.tr_glossary if pair[0] != term] + [[term, choice.currentText()]]
                self.save_review()  # el glosario va con la serie: que no dependa de otro guardado
                others = {k for n, (_, keys) in enumerate(forms) if n != choice.currentIndex() for k in keys}
                box.close()
                if scope.currentData() is not None:  # los párrafos pueden estar en cualquier capítulo
                    scope.setCurrentIndex(0)
                    fill()
                else:
                    refresh(set(done))  # el término nuevo deja en «Dudoso» los que no lo siguen
                work["only"] = others
                chips["all"].setChecked(True)
                search.clear()
                lang_filter.setCurrentIndex(0)
                show_rows()
                table.clearSelection()
                shown = [iid for iid, item in table.rows.items() if not item.isHidden()]
                for iid in shown:
                    table.rows[iid].setSelected(True)
                if shown:
                    table.see(shown[0])
                status.setText(f"{convert.counted(len(shown), 'párrafo', 'párrafos')} con otra forma de «{term}»: pulsa "
                               f"«Retraducir selección» o corrígelos a mano. Un filtro vuelve a mostrar todos.")

            terms.selected.connect(show_forms)
            terms.selection_set("0")
            show_forms()
            inner.addLayout(_row(QLabel("Forma que debe quedar:"), choice, 1,
                                 _button("Usar esta forma", use, "primary"), _button("Cerrar", box.close)))
            box.show()
            win.coherence_box = {"box": box, "table": terms, "forms": choice, "use": use}

        def edit_glossary():
            """Lo que guía a la IA, en pestañas: glosario (tabla con las veces que sale cada término), personajes (una
            fila por personaje) y estilo. Glosario y personajes son de la serie; el estilo, de las preferencias. En
            dos cajas de texto, con muchos términos y notas largas, todo se leía como un solo párrafo."""
            box, inner = self.dialog("Glosario y estilo de traducción", modal=True)
            box.resize(820, 600)
            whole = "\n".join(t for _, _, t in translate.units(self.book, 0, len(self.book.blocks)))
            tabs = QTabWidget()
            inner.addWidget(tabs, 1)

            def table(headings):
                grid = QTableWidget(0, len(headings))
                grid.setHorizontalHeaderLabels(headings)
                grid.verticalHeader().hide()
                grid.setAlternatingRowColors(True)
                grid.setShowGrid(False)
                grid.setWordWrap(True)
                grid.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
                grid.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft)
                return grid

            def page(*parts):
                widget = QWidget()
                lay = QVBoxLayout(widget)
                lay.setContentsMargins(0, 10, 0, 0)
                lay.setSpacing(8)
                for part in parts:
                    if isinstance(part, QHBoxLayout):
                        lay.addLayout(part)
                    else:
                        lay.addWidget(part, 1 if isinstance(part, (QTableWidget, QPlainTextEdit)) else 0)
                return widget

            def explain(text):
                label = _label(text, "muted")
                label.setWordWrap(True)
                return label

            # Glosario
            terms = table(["Original", "Traducción", "En el tomo"])
            head = terms.horizontalHeader()
            head.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            head.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            head.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)

            def add_term(a="", b=""):
                n = terms.rowCount()
                terms.insertRow(n)
                times = whole.count(a) if a else 0
                cells = [QTableWidgetItem(a), QTableWidgetItem(b),
                         QTableWidgetItem(convert.counted(times, "vez", "veces") if a else "")]
                cells[2].setFlags(cells[2].flags() & ~Qt.ItemFlag.ItemIsEditable)  # se cuenta, no se escribe
                cells[2].setForeground(QColor(theme.colors(self.dark)["muted"]))
                for c, cell in enumerate(cells):
                    terms.setItem(n, c, cell)
                return n

            term_search = QLineEdit()
            term_search.setPlaceholderText("Buscar término")
            term_search.setClearButtonEnabled(True)

            def find_terms():
                query = term_search.text().strip().lower()
                for n in range(terms.rowCount()):
                    terms.setRowHidden(n, bool(query) and not any(
                        query in (terms.item(n, c).text() if terms.item(n, c) else "").lower() for c in (0, 1)))

            term_search.textChanged.connect(lambda _: find_terms())

            def new_row(grid, add):
                term_search.clear()
                n = add()
                grid.setCurrentCell(n, 0)
                grid.scrollToItem(grid.item(n, 0))
                grid.editItem(grid.item(n, 0))

            def remove(grid):
                for n in sorted({i.row() for i in grid.selectedIndexes()}, reverse=True):
                    grid.removeRow(n)
                titles()

            tabs.addTab(page(explain("Cada término se traduce siempre así («Witch Cult» → «Culto de la Bruja»). Lo usa "
                                     "la IA; Google y DeepL no. Pasa a los demás tomos de la serie. Doble clic en una "
                                     "celda para cambiarla."),
                             _row(term_search, 1, _button("Añadir término", lambda: new_row(terms, add_term)),
                                  _button("Quitar", lambda: remove(terms))), terms), "")

            # Personajes
            people = table(["Personaje", "Cómo es, cómo habla y a quién trata de tú o de usted"])
            people.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
            people.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            people.setColumnWidth(0, 190)

            def add_person(name="", about=""):
                n = people.rowCount()
                people.insertRow(n)
                first = QTableWidgetItem(name)
                bold = first.font()
                bold.setBold(True)
                first.setFont(bold)
                people.setItem(n, 0, first)
                people.setItem(n, 1, QTableWidgetItem(about))
                people.resizeRowToContents(n)
                return n

            people.itemChanged.connect(lambda item: people.resizeRowToContents(item.row()))
            tabs.addTab(page(explain("Una fila por personaje: así la IA no equivoca el género ni el trato. Pasa a los "
                                     "demás tomos de la serie."),
                             _row(1, _button("Añadir personaje", lambda: new_row(people, add_person)),
                                  _button("Quitar", lambda: remove(people))), people), "")

            # Estilo
            variant, honorifics = QComboBox(), QCheckBox("Mantener honoríficos (-san, -kun…)")
            chapter = QCheckBox("La IA ve el capítulo entero")
            for code, label in translate.VARIANTS.items():
                variant.addItem(label, code)
            variant.setCurrentIndex(max(0, variant.findData(prefs.get("variant", ""))))
            honorifics.setChecked(bool(prefs.get("honorifics", True)))
            chapter.setChecked(bool(prefs.get("chapter", True)))
            general = QPlainTextEdit()
            general.setPlaceholderText("Indicaciones para todo el libro, una por línea (p. ej. «Los monstruos hablan "
                                       "en tercera persona»)")
            tabs.addTab(page(_row(QLabel("Español de:"), variant, 1),
                             explain("Solo cuando el destino es español: España usa «vosotros» y su vocabulario; "
                                     "Latinoamérica, «ustedes» y palabras que se entienden en todos los países."),
                             honorifics,
                             explain("Marcado, «Subaru-kun» se queda igual. Sin marcar, la IA lo cambia por la forma "
                                     "de tratarse del español (el tono, «señorita», «maestro»…)."),
                             chapter,
                             explain("Con el capítulo original delante, la IA sabe quién habla y qué viene después. "
                                     "Gasta más texto de entrada, que las API cobran rebajado al repetirse; los "
                                     "modelos en tu equipo no lo reciben."),
                             _label("INDICACIONES GENERALES", "muted"), general), "Estilo")

            # tr_notes guarda personajes («Nombre: nota») e indicaciones generales, una por línea
            for line in self.tr_notes.splitlines():
                found = re.match(r"\s*([^:]{1,40}?)\s*:\s*(\S.*)", line)
                if found and len(found[1].split()) <= 4:
                    add_person(found[1], found[2].strip())
                elif line.strip():
                    general.appendPlainText(line.strip())
            for a, b in self.tr_glossary:
                add_term(a, b)

            def cells(grid):
                return [[(grid.item(n, c).text() if grid.item(n, c) else "").strip() for c in (0, 1)]
                        for n in range(grid.rowCount())]

            def pairs():
                out = {}
                for a, b in cells(terms):
                    if a and b:
                        out.setdefault(a, b)
                return [[a, b] for a, b in out.items()]

            def notes():
                lines = [line.strip() for line in general.toPlainText().splitlines() if line.strip()]
                return "\n".join(lines + [f"{a}: {b}" for a, b in cells(people) if a and b])

            def titles():
                tabs.setTabText(0, f"Glosario ({terms.rowCount()})")
                tabs.setTabText(1, f"Personajes ({people.rowCount()})")

            terms.model().rowsInserted.connect(lambda *_: titles())
            people.model().rowsInserted.connect(lambda *_: titles())
            titles()
            note = _label("", "muted")
            note.setWordWrap(True)
            inner.addWidget(note)

            def add_names():
                """Los nombres propios de este tomo que salen 5 veces o más, iguales en los dos lados para corregir."""
                have = {a for a, _ in cells(terms)}
                names = sorted(((n, f) for f, n in convert.proper_names(self.book).items() if n >= 5 and f not in have),
                               reverse=True)
                if not names:
                    messagebox.showinfo("Glosario de traducción", "No hay nombres nuevos que salgan 5 veces o más en "
                                                                  "este tomo.", parent=box)
                    return
                for _, f in names:
                    add_term(f, f)
                tabs.setCurrentIndex(0)
                note.setText(f"{convert.counted(len(names), 'nombre añadido', 'nombres añadidos')} al glosario, igual "
                             f"en los dos lados: cambia la traducción de los que no se queden así.")

            def suggest():
                """La IA lee el libro y propone términos y personajes; nada se guarda sin «Aceptar»."""
                name, info = engine.currentText(), translate.ENGINES[engine.currentText()]
                if info["kind"] not in translate.AI:
                    messagebox.showinfo("Glosario y estilo", "Esto lo propone una IA: elige una en «Motor» (OpenAI, "
                                                             "DeepSeek, Claude, Gemini…).", parent=box)
                    return
                if info.get("key") and not api_key.text().strip():
                    messagebox.showerror("Glosario y estilo", f"Falta la clave de {name}.", parent=box)
                    return
                # Cuatro trozos repartidos por el libro (el principio solo trae créditos y pocos personajes), pedidos a
                # la vez: tarda lo que uno y la ventana puede decir cuántos van
                step = max(1, (len(whole) - 7500) // 3)
                samples = [whole[n * step:n * step + 7500] for n in range(4)] if len(whole) > 30000 else [whole]
                names = [f for f, _ in sorted(convert.proper_names(self.book).items(), key=lambda p: -p[1])[:60]]
                args = (src.currentData(), dst.currentData(), api_key.text().strip(), model.currentText().strip(),
                        base.text().strip() if info.get("custom") else "", names, whole)
                found, started = queue.Queue(), time.time()
                # Las notas de los tomos anteriores: la IA amplía la de un personaje si este tomo cuenta algo nuevo
                known = [f"{a}: {b}" for a, b in cells(people) if a and b]
                known_names = {line.split(":")[0] for line in known}

                def ask():
                    got_terms, got_people = {}, {}
                    try:
                        with concurrent.futures.ThreadPoolExecutor(len(samples)) as pool:
                            jobs = [pool.submit(translate.propose, name, sample, *args, known=known) for sample in samples]
                            for n, job in enumerate(concurrent.futures.as_completed(jobs), 1):
                                got = job.result()
                                for a, b in got[0]:
                                    got_terms.setdefault(a, b)
                                for line in got[1]:  # el mismo personaje en dos trozos: la primera nota, o la más larga
                                    who = line.split(":")[0]  # de un conocido (cada trozo amplía lo suyo)
                                    if who not in got_people or (who in known_names and len(line) > len(got_people[who])):
                                        got_people[who] = line
                                found.put(("progress", n))
                        found.put(([[a, b] for a, b in got_terms.items()], list(got_people.values())))
                    except Exception as e:  # TranslateError ya viene en español
                        found.put(str(e) or type(e).__name__)

                suggest_btn.setEnabled(False)
                suggest_btn.setText(f"Leyendo el libro… 0 de {len(samples)}")
                threading.Thread(target=ask, daemon=True).start()

                def wait():
                    if not shiboken6.isValid(box):
                        return
                    try:
                        result = found.get_nowait()
                        while result[0] == "progress":
                            suggest_btn.setText(f"Leyendo el libro… {result[1]} de {len(samples)}")
                            result = found.get_nowait()
                    except queue.Empty:
                        note.setText(f"La IA lee {convert.counted(len(samples), 'trozo', 'trozos')} del libro a la "
                                     f"vez; suele tardar un minuto. Van {int(time.time() - started)} s.")
                        qtk.after(200, wait)
                        return
                    suggest_btn.setText("Proponer con la IA")
                    suggest_btn.setEnabled(True)
                    if isinstance(result, str):
                        note.setText("")
                        messagebox.showerror("Glosario y estilo", result, parent=box)
                        return
                    have, known = {a for a, _ in cells(terms)}, {a for a, _ in cells(people)}
                    new_terms = [(a, b) for a, b in result[0] if a not in have]
                    new_people = [line.partition(":")[::2] for line in result[1] if line.split(":")[0] not in known]
                    for a, b in new_terms:
                        add_term(a, b)
                    # La nota ampliada de un conocido, en su fila: en cursiva y con la anterior en el tooltip
                    rows_now, grown = {a: (n, b) for n, (a, b) in enumerate(cells(people))}, 0
                    for line in result[1]:
                        who, _, about = (part.strip() for part in line.partition(":"))
                        if who in rows_now and about and about != rows_now[who][1] and people.item(rows_now[who][0], 1):
                            item = people.item(rows_now[who][0], 1)
                            item.setText(about)
                            font = item.font()
                            font.setItalic(True)
                            item.setFont(font)
                            item.setToolTip(f"Antes: {rows_now[who][1]}")
                            grown += 1
                    for a, b in new_people:
                        add_person(a.strip(), b.strip())
                    found_text = [convert.counted(len(new_terms), 'término nuevo', 'términos nuevos'),
                                  convert.counted(len(new_people), 'personaje nuevo', 'personajes nuevos')]
                    if grown:
                        found_text.append(convert.counted(grown, "nota ampliada", "notas ampliadas")
                                          + " (en cursiva; la anterior, al pasar el ratón)")
                    note.setText(f"La IA propone {', '.join(found_text[:-1])} y {found_text[-1]}: revísalos antes de "
                                 f"aceptar.")

                qtk.after(200, wait)

            def accept():
                self.tr_glossary, self.tr_notes = pairs(), notes()
                prefs.update(variant=variant.currentData(), honorifics=honorifics.isChecked(),
                             chapter=chapter.isChecked())
                self.settings["translate"] = prefs
                self.save_settings()
                self.save_review()  # glosario y personajes van con la serie: que no dependan de otro guardado
                refresh(set(done))  # un término nuevo del glosario puede dejar traducciones en «Dudoso»
                box.close()

            suggest_btn = _button("Proponer con la IA", suggest)
            inner.addLayout(_row(_button("Añadir nombres de este tomo", add_names), suggest_btn, 1,
                                 _button("Aceptar", accept, "primary")))
            box.show()
            win.glossary_box = {"box": box, "terms": terms, "people": people, "general": general, "tabs": tabs,
                                "variant": variant, "honorifics": honorifics, "chapter": chapter, "suggest": suggest,
                                "accept": accept, "note": note, "add_term": add_term, "add_person": add_person,
                                "add_names": add_names, "pairs": pairs, "notes": notes}

        def close():
            keep()
            remember()
            stop_spell()
            if work["thread"]:  # a mitad de lote: se oculta, lo termina, lo guarda y luego se cierra (poll)
                work["stop"].set()
                work["closing"] = True
                run_btn.setEnabled(False)
                run_btn.setText("Pausando…")
                status.setText("Terminando el lote en curso para guardarlo; luego se para.")
                win.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)  # cerrar desde fuera solo la oculta
                win.hide()
                return
            win.on_reject = None
            win.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
            win.close()

        win.on_reject = close
        table.selected.connect(pick)
        engine.activated.connect(lambda _: (remember(), show_engine()))
        scope.activated.connect(lambda _: fill())
        dst.activated.connect(lambda _: fill())  # con lo de la serie que esté traducido a ese idioma
        show_engine()
        fill()
        win.parts = {"summary": summary, "engine_btn": engine_btn, "reload": reload,
                     "table": table, "run": run, "apply": apply, "translation": translation_box, "engine": engine,
                     "scope": scope, "dst": dst, "src": src, "key": api_key, "status": status, "work": work, "done": done,
                     "glossary": edit_glossary, "close": close, "merge": merge, "pdf": pdf, "book": self.book,
                     "model": model, "models": find_models, "chips": chips, "search": search, "step": step,
                     "doubt": doubt, "progress": progress, "langs": lang_filter, "coherence": coherence,
                     "warnings": warnings, "check": check, "look": look, "spell": spell, "add_word": add_word,
                     "similar": similar_label, "similar_row": similar_row, "use_similar": use_similar,
                     "review": review, "accept": lambda: settle(True), "discard": lambda: settle(False),
                     "accept_btn": accept_btn, "fix": fix_box, "accept_all": settle_all, "accept_all_btn": accept_all_btn}
        win.show()
        return win

    def translations_running(self):
        """Ventanas «Traducir» con un lote en camino, visibles u ocultas terminándolo."""
        return [w for w in self.root.findChildren(Dialog) if shiboken6.isValid(w) and "merge" in getattr(w, "parts", {})
                and w.parts["work"]["thread"]]

    def versions(self):
        """Versiones del libro, como los commits de git: guardar, comparar (con lo de ahora, o dos entre sí),
        restaurar, renombrar y borrar."""
        if not (self.book and self.pdf) or self.raise_tool("versions_window"):
            return
        win, column = self.dialog("Versiones")
        win.resize(940, 620)
        name = QLineEdit()
        name.setPlaceholderText("Nombre de la versión, p. ej. «Antes de corregir»")
        save_btn = _button("Guardar versión", lambda: save(), "primary")
        column.addLayout(_row(QLabel("Nombre:"), name, save_btn))
        panes = QSplitter(Qt.Orientation.Vertical)
        column.addWidget(panes, 1)
        table = qtk.RowList(("name", "date", "changes"), {"name": "Versión", "date": "Fecha", "changes": "Cambios"},
                            {"date": 130, "changes": 300}, stretch="name")
        table.setSelectionMode(table.SelectionMode.ExtendedSelection)  # dos con Ctrl+clic: se comparan entre sí
        panes.addWidget(table)
        bottom = QWidget()
        lower = QVBoxLayout(bottom)
        lower.setContentsMargins(0, 6, 0, 0)
        heading = _label("", "muted")
        heading.setWordWrap(True)
        lower.addWidget(heading)
        box = qtk.TextBox()
        box.setReadOnly(True)
        for tag, look in theme.diff_tags(self.dark).items():
            box.tag_configure(tag, **look)
        lower.addWidget(box, 1)
        panes.addWidget(bottom)
        panes.setSizes([220, 400])
        restore_btn = _button("Restaurar", lambda: restore())
        rename_btn = _button("Renombrar", lambda: rename())
        delete_btn = _button("Borrar", lambda: delete())
        column.addLayout(_row(restore_btn, rename_btn, delete_btn, 1, _button("Cerrar", win.close)))
        state = {"versions": []}

        def fill(select=None):
            try:
                versions, backup = _load_versions(self.pdf)
            except OSError as e:
                versions, backup = [], None
                messagebox.showerror("Versiones", f"No se pudo leer el archivo de versiones: {e.strerror or e}", parent=win)
            if backup:
                messagebox.showwarning("Versiones", f"El archivo de versiones estaba dañado. Se apartó como {backup.name} "
                                                    "y se empieza uno nuevo.", parent=win)
            state["versions"] = versions
            table.delete(*table.get_children())
            for n in reversed(range(len(versions))):  # la más nueva arriba, como git log
                v = versions[n]
                table.insert("", "end", iid=str(n), values=(
                    v["name"] + (" (automática)" if v.get("auto") else ""), _date_text(v.get("date")),
                    _version_changes(versions[n - 1]["state"] if n else None, v["state"])))
            win.setWindowTitle(f"Versiones · {convert.counted(len(versions), 'versión', 'versiones')}")
            if versions:
                table.selection_set(str(len(versions) - 1 if select is None else min(select, len(versions) - 1)))
            pick()

        def chosen():
            return sorted(int(i) for i in table.selection())

        def pick():
            picked, versions = chosen(), state["versions"]
            restore_btn.setEnabled(len(picked) == 1)
            rename_btn.setEnabled(len(picked) == 1)
            delete_btn.setEnabled(bool(picked))
            for tag in ("old", "new", "note", "head"):
                box.tag_remove(tag, "1.0", "end")
            box.delete("1.0", "end")
            if not picked:
                heading.setText("Elige una versión para ver qué cambió desde entonces, o dos (Ctrl+clic) para "
                                "compararlas." if versions else
                                "Sin versiones todavía. «Guardar versión» guarda todo lo revisado hasta ahora; cada "
                                "EPUB que generas también se guarda como versión, automáticamente.")
                return
            old = versions[picked[0]]
            if len(picked) == 1:
                heading.setText(f"De «{old['name']}» a lo de ahora")
                name.setText(old["name"])
                segments = self.compare(old["state"])
            else:
                new = versions[picked[-1]]
                heading.setText(f"De «{old['name']}» a «{new['name']}»")
                segments = self.compare(old["state"], new["state"])
            box.insert("1.0", "".join(text for text, _ in segments))
            at = 0
            for text, tag in segments:
                if tag:
                    box.tag_add(tag, f"1.0+{at}c", f"1.0+{at + len(text)}c")
                at += len(text)

        def save():
            if self.add_version(name.text().strip() or f"Versión del {datetime.now():%d/%m/%Y %H:%M}"):
                self.status.setText("Versión guardada")
            name.clear()
            fill()

        def change(edit):
            """Cambia la lista de versiones con edit(versions) y la vuelve a escribir."""
            versions = state["versions"]
            edit(versions)
            try:
                _write_versions(self.pdf, versions)
            except OSError as e:
                messagebox.showerror("Versiones", f"No se pudieron guardar las versiones: {e.strerror or e}", parent=win)

        def rename():
            picked = chosen()
            if name.text().strip():
                change(lambda versions: versions[picked[0]].update(name=name.text().strip()))
                fill(picked[0])

        def delete():
            picked = chosen()
            names = ", ".join(f"«{state['versions'][n]['name']}»" for n in picked)
            if messagebox.askyesno("Borrar versiones", f"¿Borrar {names}? No se puede deshacer.", parent=win):
                change(lambda versions: [versions.pop(n) for n in reversed(picked)])
                fill(picked[0] - 1 if picked[0] else 0)

        def restore():
            picked = chosen()
            if self.restore_version(picked[0]):
                fill()

        table.selected.connect(pick)
        name.returnPressed.connect(save)
        fill()
        win.show()
        name.setFocus()
        win.parts = {"name": name, "save": save_btn, "table": table, "box": box, "heading": heading,
                     "restore": restore_btn, "rename": rename_btn, "delete": delete_btn, "fill": fill}
        self.versions_window = win

    def search_book(self):
        term = self.find_entry.text().strip()
        self.find_results.clear()
        self.matches = []
        if len(term) < 2:
            self.find_count.setText("Escribe al menos dos letras")
            self.find_results.hide()
            return
        for k, block in enumerate(self.book.blocks):
            if block.kind not in ("p", "heading"):
                continue
            plain = html.unescape(convert.TAGS.sub("", block.html))
            pos = plain.lower().find(term.lower())
            if pos >= 0 and len(self.matches) < 500:
                self.matches.append((k, block.page, term))
                self.find_results.addItem(f"pág. {block.page} · …{plain[max(0, pos - 40):pos + 60]}…")
        self.find_count.setText(convert.counted(len(self.matches), "resultado")
                                + (" (se muestran los 500 primeros)" if len(self.matches) == 500 else ""))
        self.find_results.setVisible(bool(self.matches))

    def pick_found(self, row):
        if 0 <= row < len(self.matches):
            self.show_match(*self.matches[row])

    def close_find(self):
        self.find_bar.hide()
        self.find_results.hide()
        self.tree.setFocus()


class Dialog(QDialog):
    """QDialog que pregunta a on_reject antes de cerrarse con Esc o con la X de la ventana, y que al
    abrirse cabe en la pantalla (con el zoom de Windows a 125 % en 1366x768 solo quedan 1093x582)."""
    on_reject = None
    caption = None
    closing = asked = False  # dentro de closeEvent; on_reject pidió cerrar ahí dentro

    def close(self):
        if self.closing:  # Qt no cierra dos veces a la vez: lo acepta closeEvent (la ✕ pedía un segundo clic)
            self.asked = True
            return True
        return super().close()

    def closeEvent(self, event):
        self.closing, self.asked = True, False
        try:
            super().closeEvent(event)  # reject() -> on_reject
        finally:
            self.closing = False
        if self.asked:
            event.accept()

    def nativeEvent(self, kind, message):
        handled, result = qtk.caption_event(self, kind, message)
        return (handled, result) if handled else super().nativeEvent(kind, message)

    def showEvent(self, event):
        qtk.use_caption(self)  # barra de título del tema, con los botones que tenga la ventana
        if self.layout():  # con el alto de la barra ya sumado: si no, se medía sin ella y luego crecía 32 px
            self.layout().activate()
        super().showEvent(event)
        if self.windowState() & Qt.WindowState.WindowMaximized:  # moverla la sacaría de maximizada
            return
        area = self.screen().availableGeometry()
        frame = self.frameGeometry().size() - self.size()  # barra de título y bordes
        width = max(self.minimumWidth(), min(self.width(), area.width() - frame.width()))
        height = max(self.minimumHeight(), min(self.height(), area.height() - frame.height()))
        if (width, height) != (self.width(), self.height()):
            self.resize(width, height)
        box = self.frameGeometry()
        # Si aun así no cabe, que se vea el principio: la barra de título y la esquina izquierda
        x = max(area.x(), min(box.x(), area.right() - box.width() + 1))
        y = max(area.y(), min(box.y(), area.bottom() - box.height() + 1))
        if (x, y) != (box.x(), box.y()):
            self.move(x, y)

    def reject(self):
        if self.on_reject:
            self.on_reject()
        else:
            super().reject()


WEB_TAB = 2  # «Hoja web» en el panel derecho del editor, tras «Original» y «EPUB»


HISTORY_STEPS = 100  # pasos de deshacer (y de rehacer) del editor que se guardan por capítulo
# shortcut: se guardan los últimos 400 capítulos editados del libro; si alguien necesita más, subirlo
HISTORY_KEPT = 400


def _history_lines(text):
    return [line.strip() for line in text.split("\n\n") if line.strip()]


def _history_key(text):
    """Clave del texto de un capítulo en editor_history: sin el número de sus marcas («[Separador 7]»), que cambia
    al añadir o quitar párrafos en otro capítulo, y corta, para guardarla en la revisión."""
    plain = "\n\n".join(convert.TOKEN.sub(r"[\1]", line) for line in _history_lines(text))
    return hashlib.sha1(plain.encode("utf-8")).hexdigest()[:20]


def _history_delta(after, before):
    """before escrito frente a after: [i, j] es after[i:j] tal cual y un texto es una línea suya. Así la revisión
    guarda solo lo cambiado, y lo igual toma las marcas de ahora."""
    a, b = _history_lines(after), _history_lines(before)
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        out += [[i1, i2]] if op == "equal" else b[j1:j2]
    return out


def _history_apply(delta, text):
    lines = _history_lines(text)
    return "\n\n".join(x for part in delta for x in (lines[part[0]:part[1]] if isinstance(part, list) else [part]))


def history_entry(history, text):
    """(versiones del deshacer del capítulo que ahora es text, de la más vieja a la más nueva; posición de text en
    ellas; el texto del principio), o None si no hay. Lo de después de text es lo que se puede rehacer."""
    entry = history.get(_history_key(text))
    try:
        if isinstance(entry, dict):
            versions, at = [_history_apply(d, text) for d in entry["v"]], int(entry["at"])
            return (versions, at, _history_apply(entry["first"], text)) if 0 <= at < len(versions) else None
        if isinstance(entry, list):  # revisión de antes: [el de antes de aceptar, el del principio], uno por aceptar
            first, versions = _history_apply(entry[1], text), [text]
            while isinstance(entry, list) and len(versions) < HISTORY_STEPS:
                older = _history_apply(entry[0], versions[0])
                if older in versions:
                    break
                versions.insert(0, older)
                entry = history.get(_history_key(older))
            return versions, len(versions) - 1, first
    except (TypeError, IndexError, KeyError, ValueError):  # revisión editada a mano
        pass
    return None


def history_add(history, versions, at, first, loaded=None):
    """Al aceptar en el editor: el deshacer del capítulo (versions, de la más vieja a la más nueva), el capítulo como
    queda (versions[at]) y como estaba al principio. loaded: como estaba al abrir, cuya entrada ya no vale."""
    text = versions[at]
    key = _history_key(text)
    if loaded is not None:
        history.pop(_history_key(loaded), None)
    history.pop(key, None)  # al final: es lo más nuevo
    if len(versions) > 1 or _history_lines(first) != _history_lines(text):
        history[key] = {"v": [_history_delta(text, v) for v in versions], "at": at,
                        "first": _history_delta(text, first)}
    for old in list(history)[:-HISTORY_KEPT]:
        del history[old]


def history_firsts(first, versions):
    """{línea de cada versión: su línea en first, None si es un párrafo nuevo desde entonces}, para «Mostrar
    cambios» frente al principio (editor.compared). Los párrafos que cambian a la vez se emparejan en orden."""
    old, out = _history_lines(first), {}
    for version in versions:
        new = _history_lines(version)
        for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
            for n in range(j2 - j1):
                out.setdefault(new[j1 + n], old[i1 + n] if op != "insert" and n < i2 - i1 else None)
    return out


def undo_versions(doc, dumped, limit=HISTORY_STEPS):
    """([texto de cada paso del deshacer de doc, del más viejo al más nuevo], posición del de ahora): se deshace y
    rehace entero para leerlos y doc queda como estaba. Los pasos que no cambian el texto no cuentan."""
    versions, back = [dumped(doc)], 0
    while back < limit and doc.isUndoAvailable():
        doc.undo()
        back += 1
        versions.insert(0, dumped(doc))
    for _ in range(back):
        doc.redo()
    at, ahead = len(versions) - 1, 0
    while ahead < limit and doc.isRedoAvailable():
        doc.redo()
        ahead += 1
        versions.append(dumped(doc))
    for _ in range(ahead):
        doc.undo()
    out = []
    for n, version in enumerate(versions):
        if out and _history_lines(out[-1]) == _history_lines(version):
            at -= n <= at  # el de ahora es el igual de antes
            continue
        out.append(version)
    return out, at


class TextEditor:
    """Editor del texto como Word, capítulo a capítulo: la cinta dice en qué capítulo se está y lleva a los
    demás (menú y ◀ ▶); la hoja (editor.Sheet) muestra cursiva, negrita, títulos, enlaces, ilustraciones y
    separadores como quedan; a la derecha la página del PDF del párrafo del cursor, grande, con zoom y
    sincronizada con la hoja. Cada capítulo visitado guarda su texto y su deshacer; al aceptar,
    App.apply_edits guarda lo cambiado de todos con convert.edit_section."""

    def __init__(self, app, item, start, end):
        self.app, self.item = app, item
        LOG.info("Editor: abre la fila %s (bloques %s-%s)", item, start, end)
        self.win = win = Dialog(app.root)
        # Word se abre a pantalla completa. Al crearla: cambiarlo con las vistas de Chromium ya puestas rehacía la
        # ventana de Windows entera (0,19 s de los ~1,5 que tardaba en abrirse)
        win.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint)
        win.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        # Esc y la X de la ventana preguntan antes de descartar cambios; leyendo en voz alta, Esc solo calla la voz
        # En modo concentración, Esc vuelve a la vista normal antes que nada
        win.on_reject = lambda: (self.stop_reading() if self.reading is not None else self.toggle_focus()
                                 if self.focused else self.cancel())
        self.focused, self.before_focus = False, []
        QShortcut(QKeySequence("F11"), win, self.toggle_focus)  # también con el foco fuera de la hoja
        self.reading, self.speaker = None, None  # párrafo que se lee en voz alta (QTextBlock) y la voz (Speaker)
        self.colors = theme.colors(app.dark)
        editor.IMAGES = _images()
        editor.LINK_COLOR = self.colors["acc"]  # el azul fijo apenas se leía sobre la hoja oscura (3:1)
        # Un EPUB se ve con su CSS y sus fuentes (las clases de sus párrafos); un PDF, con la hoja de siempre
        origin = app.book.origin
        stamp = (os.path.getmtime(origin), os.path.getsize(origin)) if origin and os.path.exists(origin) else None
        editor.use_css(faithful.book_style(origin, stamp) if stamp else None)
        self.sheet = sheet = editor.Sheet()
        sheet.route_undo = True  # Ctrl+Z y Ctrl+Y por do(): con «Todo el libro», también lo de otros capítulos
        sheet.tags, sheet.unknown = theme.editor_tags(app.dark), set(app.unknown)  # y la última ortografía
        sheet.mark_current = True
        # Una ilustración pegada desde otro capítulo llega con su imagen (Sheet.adopt)
        sheet.foreign = lambda k: (app.book.blocks[k].image if self.current and not self.current[0] <= k < self.current[1]
                                   and 0 <= k < len(app.book.blocks) and app.book.blocks[k].kind == "image" else None)
        self.pages = {}  # (inicio, fin) -> {"doc", "look", "lines"} de cada sección visitada
        # Avisos del informe por bloque, tomados al abrir: la hoja los marca en los párrafos sin cambiar
        self.issues = {}
        for issue in app.quality_report():
            if issue.block is not None and _issue_key(issue) not in app.ignored:
                self.issues.setdefault(issue.block, []).append(issue)
        self.shown_issue = None
        # Ortografía en vivo: palabras ya enviadas al proceso, las que no están, ignoradas y sugerencias
        self.unknown, self.checked, self.ignored_words, self.suggestions = set(app.unknown), set(), set(), {}
        self.sections = self.outline()
        self.current = None
        column = QVBoxLayout(win)
        column.setContentsMargins(0, 0, 0, 8)
        column.setSpacing(0)
        column.addWidget(self.ribbon())
        column.addWidget(self.find_bar())
        column.addWidget(self.link_bar())
        column.addWidget(self.split_bar())
        column.addWidget(self.issue_bar())
        column.addWidget(self.resume_bar())
        # Hoja centrada sobre el escritorio gris, como la vista de impresión de Word
        desk = QFrame(objectName="desk")
        desk_row = QHBoxLayout(desk)
        desk_row.setContentsMargins(16, 16, 16, 16)  # también abajo: sin él la hoja tocaba el borde
        # En modo concentración, de capítulo en capítulo con dos flechas grandes a los lados, como un lector
        # en los bordes de la pantalla y discretas (gris del texto desactivado, sin marco): no distraen de la hoja
        self.focus_prev = qtk.ArrowButton(-1, lambda: self.step_section(-1), "Capítulo anterior (Ctrl+RePág)",
                                          size=12, subtle=True)
        self.focus_next = qtk.ArrowButton(1, lambda: self.step_section(1), "Capítulo siguiente (Ctrl+AvPág)",
                                          size=12, subtle=True)
        for button in (self.focus_prev, self.focus_next):
            button.setObjectName("edge")
            button.setFixedSize(40, 120)
            button.hide()
        desk_row.addWidget(self.focus_prev, 0, Qt.AlignmentFlag.AlignVCenter)
        desk_row.addStretch(1)
        desk_row.addWidget(sheet, 20)
        # Vista «Código HTML» (Vista → Código): el mismo capítulo como su HTML, en lugar de la hoja
        self.code = editor.CodeEdit(theme.code_colors(app.dark))  # con números de línea y colores de código
        # su línea es un párrafo: la hoja (oculta) va a él, y con ella el original, el EPUB y la hoja web
        self.code.cursorPositionChanged.connect(lambda: self.code.isVisible()
                                                and self.later("code", 150, self.code_moved))
        self.code.textChanged.connect(lambda: self.code.isVisible() and self.code.hasFocus()
                                      and self.later("codelive", 30, self.apply_code_live))
        self.applying_code = False  # la hoja cambia por el código: el código no se vuelve a escribir
        self.code.setFont(QFont("Segoe UI", 9))  # la de la ventana, como el visor del explorador de archivos
        self.shells = {}  # (inicio, fin) -> cabecera editada en «Código HTML» sin guardar aún
        self.code_shell, self.code_frame = None, ([], [])  # la cabecera que enseña el código y sus líneas
        self.code.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.code.hide()
        desk_row.addWidget(self.code, 20)
        # Pestaña «Hoja web» del panel derecho: el capítulo en Chromium, editable, con el aspecto del libro. web_on: el
        # cursor está en ella (la cinta y lo escrito van a ella); si no, enseña lo de la hoja de la izquierda
        self.web, self.web_on, self.web_state = None, False, {}
        # ¿cambios sin volcar en la hoja web?, qué hacer al cargarla, y el párrafo de su cursor (su sitio en la de Qt)
        self.web_dirty, self.web_then, self.web_home = False, None, None
        self.web_busy, self.web_queue = False, []  # on_sheet en curso y lo pulsado mientras tanto
        # Lo que la web volcó al cargar (sin tocar), su capítulo y la versión de la hoja que enseña
        self.web_base, self.web_section, self.web_rev = None, None, None
        self.web_lines, self.web_objects, self.web_page = None, {}, ""  # lo que enseña la hoja web (sync_web_live)
        self.epub_lines, self.epub_drawn = None, None  # la pestaña EPUB: lo que tiene puesto; lo que se dibujó
        self.epub_stale = False  # algo que patch_epub no pudo poner: draw_web lo rehará
        import webview
        if webview.available():
            self.web = webview.WebSheet()
            self.web.dark = bool(app.settings.get("editor_dark", False))  # Vista → Modo oscuro, al cargar
            self.web.hide()
            self.web_poll = QTimer(win)  # la cinta sigue al cursor de la hoja web (negrita, estilo, alineación)
            self.web_poll.timeout.connect(self.poll_web)
            self.web_issues = {}  # id -> Issue de los avisos pintados en la hoja web
            self.web.ready.connect(self.web_ready)
            self.web.menu_at.connect(self.web_menu)
            for key, action in (("F7", lambda: self.next_change()), ("F8", lambda: self.next_issue()),
                                ("Ctrl+F", lambda: self.show_find_bar(False)),
                                ("Ctrl+H", lambda: self.show_find_bar(True)), ("F9", lambda: self.toggle_nav()),
                                # los de la hoja (Sheet.keyPressEvent) que Chromium no tiene
                                ("Ctrl+T", lambda: self.do("toggle_heading")), ("Ctrl+J", lambda: self.do("join_next")),
                                ("Ctrl+D", lambda: self.do("toggle_right")),
                                ("Ctrl+E", lambda: self.do("toggle_center")),
                                ("Shift+F3", lambda: self.do("change_case")),
                                ("Ctrl+Alt+K", lambda: self.show_link_bar()),
                                ("Ctrl+Return", lambda: self.toggle_reviewed()),
                                ("Ctrl+Alt+-", lambda: self.do("insert_symbol", "—")),
                                ("Ctrl+Alt+.", lambda: self.do("insert_symbol", "…")),
                                ("Ctrl+Alt+Q", lambda: self.do("wrap", "«", "»")),
                                ("Ctrl+Shift+=", lambda: self.do("toggle_sup")),
                                ("Ctrl+=", lambda: self.do("toggle_sub")),
                                ("Ctrl+Shift+>", lambda: self.do("grow_font", 1)),
                                ("Ctrl+Shift+<", lambda: self.do("grow_font", -1))):
                QShortcut(QKeySequence(key), self.web, action, context=Qt.ShortcutContext.WidgetWithChildrenShortcut)
        desk_row.addStretch(1)
        desk_row.addWidget(self.focus_next, 0, Qt.AlignmentFlag.AlignVCenter)
        # El borde de la hoja se arrastra para estrecharla o ensancharla, hasta el ancho de la página (set_text_zoom)
        self.page_share, self.dragging = 1.0, None  # parte del ancho máximo que ocupa la hoja
        self.desk = desk
        desk.setMouseTracking(True)
        # La barra de scroll de la hoja heredaba del escritorio la flecha de ensanchar al pasar del borde a ella
        sheet.verticalScrollBar().setCursor(Qt.CursorShape.ArrowCursor)
        editor.watch(desk, self.on_desk_event)
        # Página del PDF a la derecha: se ajusta al ancho del panel y se amplía con el zoom
        self.canvas = qtk.Preview()
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.canvas)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)  # más pequeña que el panel: en el medio
        # Solo lectura: un clic lleva la hoja al párrafo; Ctrl+C y el clic derecho copian su texto o una imagen
        self.canvas.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        editor.watch(self.canvas, self.on_canvas_event)
        # Título que en el PDF es un dibujo: un botón junto a ◀ ▶ del panel, que no tapa la página (ni sale en
        # pantalla completa, sin panel); al pulsarlo, lo explica
        self.source_note = _button("Título en imagen", lambda: self.note.setText(
            "El EPUB usa este dibujo con el texto del título"),
            "warn")
        self.source_note.setToolTip("En el PDF este título es una imagen: compruébalo con el original")
        self.source_note.hide()
        self.page_label = _label("", "muted")
        self.page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Del ancho del texto más largo: al pasar de una ilustración a una página, los botones no se movían de sitio
        self.page_label.setMinimumWidth(max(self.page_label.fontMetrics().horizontalAdvance(text) for text in
                                            ("Página 8888 de 8888", "Ilustración · pág. 8888")) + 8)
        self.zoom_label = _label("100 %", "muted")
        side = self.side = QWidget()
        side_column = QVBoxLayout(side)
        side_column.setContentsMargins(8, 8, 8, 0)
        # «Original»: la página de la que sale el párrafo; «EPUB»: el capítulo como saldrá, con lo que se va escribiendo
        self.side_tabs = QTabBar()
        self.side_tabs.addTab("Original")
        self.side_web = None
        import webview
        if webview.available():
            self.side_tabs.addTab("EPUB")
            self.side_web = webview.EpubView()
        self.side_tabs.setDrawBase(False)
        self.side_tabs.currentChanged.connect(self.show_side)
        self.web_path = os.path.join(tempfile.gettempdir(), f"lnepub-vista-editor-{os.getpid()}.epub")
        side_column.addWidget(self.side_tabs, 0, Qt.AlignmentFlag.AlignLeft)
        side_column.addWidget(self.scroll, 1)
        self.epub_box = self.web_box = None
        if self.side_web is not None:
            self.epub_box = QWidget()
            row = QHBoxLayout(self.epub_box)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(0)
            row.addWidget(self.side_web, 1)  # del ancho del panel, como la hoja web
            row.addWidget(self.side_web.bar)
            self.epub_box.hide()
            side_column.addWidget(self.epub_box, 1)
            self.side_web.moved.connect(lambda: self.web_shown() and self.show_web_page(self.side_web))
            self.side_web.page().clicked.connect(self.epub_clicked)
            self.side_web.page().picked.connect(self.epub_picked)
            self.side_web.page().lined.connect(self.epub_line)
            # al borrarse la página Chromium aún contesta (como en WebSheet._ready): cerrado, nada que marcar
            self.side_web.paged.connect(lambda _: self.closed or (self.mark_epub(), self.tag_epub()))
        if self.web is not None:
            self.side_tabs.addTab("Hoja web")
            self.web_box = QWidget()
            row = QHBoxLayout(self.web_box)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(0)
            row.addWidget(self.web, 1)
            row.addWidget(self.web.bar)
            self.web.show()
            self.web_box.hide()
            side_column.addWidget(self.web_box, 1)
            self.web.moved.connect(lambda: self.web_tab() and self.show_web_page(self.web))
        self.epub_zoom, self.web_zoom = 100, 100  # − y + del panel, cada pestaña el suyo
        # Fuera, de capítulo en capítulo (hojear página a página era lento para cruzar el libro); dentro, de página
        # ◀◀ y no los de saltar pista: Windows los dibuja como emoji azules
        prev_btn = qtk.ArrowButton(-1, lambda: self.step_section(-1), "Capítulo anterior", double=True)
        next_btn = qtk.ArrowButton(1, lambda: self.step_section(1), "Capítulo siguiente", double=True)
        self.prev_page = qtk.ArrowButton(-1, lambda: self.step_page(-1), "Página anterior")
        self.next_page = qtk.ArrowButton(1, lambda: self.step_page(1), "Página siguiente")
        side_column.addLayout(_row(prev_btn, self.prev_page, self.page_label, self.next_page, next_btn,
                                   self.source_note, 1, _button("−", lambda: self.zoom(-25)), self.zoom_label,
                                   _button("+", lambda: self.zoom(25))))
        # Sobre la hoja, como «Original | EPUB» del panel: una sola vista a la vez, la hoja o su código HTML
        self.view_tabs = QTabBar()
        self.view_tabs.addTab("Hoja")
        self.view_tabs.addTab("Código HTML")
        self.view_tabs.setDrawBase(False)
        self.view_tabs.currentChanged.connect(self.show_view)
        left = QWidget()
        left_column = QVBoxLayout(left)
        left_column.setContentsMargins(0, 8, 0, 0)
        left_column.setSpacing(0)
        left_column.addWidget(self.view_tabs, 0, Qt.AlignmentFlag.AlignLeft)
        left_column.addWidget(desk, 1)
        self.split = QSplitter()
        self.split.addWidget(self.nav_panel())
        self.split.addWidget(self.files_panel())
        self.split.addWidget(left)
        self.split.addWidget(side)
        self.split.setStretchFactor(2, 1)
        self.split.setStretchFactor(3, 1)
        # Con navegación, archivos y panel derecho la hoja quedaba en 120 px: un párrafo largo partido letra a letra
        # tardaba 0,16 s por tecla al maquetarse (caos del editor, semilla 23)
        left.setMinimumWidth(360)
        self.split.setCollapsible(2, False)
        self.split.splitterMoved.connect(lambda *_: self.later("panel", 80, self.redraw))
        column.addWidget(self.split, 1)
        # Barra de estado como Word: dónde estás y el zoom del texto
        # Recortadas y con el sitio que sobra: un aviso que aparece o se va no mueve lo de la derecha
        self.status = StatusLabel(objectName="muted")
        self.note = StatusLabel(objectName="muted")  # resultado de la última herramienta («Tipografía corregida…»)
        for label in (self.status, self.note):
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.text_zoom = QSlider(Qt.Orientation.Horizontal)
        self.text_zoom.setRange(50, 200)
        self.text_zoom.setValue(self.option("editor_zoom"))
        self.text_zoom.setFixedWidth(140)
        self.text_zoom_label = _label("100 %", "muted")
        # Del ancho de «200 %»: al pasar de 100 % a 90 % la barra cambiaba de ancho y los botones se movían
        for label in (self.text_zoom_label, self.zoom_label):
            label.setMinimumWidth(label.fontMetrics().horizontalAdvance("300 %") + 4)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.text_zoom.valueChanged.connect(self.set_text_zoom)
        status_row = self.status_row = _row(self.status, self.note,
                          _button("−", lambda: self.text_zoom.setValue(self.text_zoom.value() - 10)),
                          self.text_zoom, _button("+", lambda: self.text_zoom.setValue(self.text_zoom.value() + 10)),
                          # En pantalla completa, Aceptar y Cancelar vuelven al editor: no lo cierran
                          self.text_zoom_label,
                          _button("Aceptar", lambda: self.toggle_focus() if self.focused else self.save(), "primary"),
                          _button("Cancelar", lambda: self.toggle_focus() if self.focused else self.cancel()),
                          margins=(12, 8, 12, 0))
        # lo más largo que suele decir: con un EPUB, «del original» no cabía en 260 px
        self.status.setMinimumWidth(self.status.fontMetrics().horizontalAdvance(
            "Página 13 del original · 573 párrafos · 9.551 palabras") + 8)
        status_row.setStretchFactor(self.status, 1)
        status_row.setStretchFactor(self.note, 1)
        column.addLayout(status_row)
        self.shown, self.box, self.page, self.pdf_zoom = None, None, 0, 1.0
        self.more, self.below = None, None  # trozo del párrafo en la página siguiente y dónde va esa página
        self.drawn = None  # source_key() del dibujo actual del panel
        self.page_cache, self.file_starts = {}, None  # source_page: página de cada bloque de un EPUB
        self.pending, self.closed = {}, False  # trabajo diferido: nombre -> temporizador
        self.set_text_zoom(self.text_zoom.value())
        # «Todo el libro» (Opciones → Editor): [(inicio, fin)] del capítulo de cada paso de deshacer y de rehacer, en
        # orden; cada capítulo sigue con su deshacer de Qt (book_step)
        self.book_undo, self.book_redo = [], []
        if self.option("editor_changes"):
            self.show_changes_action.setChecked(True)
        self.open_section(start, end, int(item))
        self.recover_draft()
        if self.option("editor_resume"):
            self.offer_resume()
        # Pestaña del panel: la elegida en Opciones o la última usada (la hoja web se recuerda); se escribe en la hoja
        # hasta que se pulsa en la web
        side = self.option("editor_side")
        if side == "EPUB" and self.side_web is not None:
            self.side_tabs.setCurrentIndex(1)
        elif self.web is not None and (side == "Hoja web" or side == EDITOR_SIDES[0]
                                       and app.settings.get("editor_web", False)):
            self.side_tabs.setCurrentIndex(WEB_TAB)
        self.forwarding, self.side_keys = False, editor._Filter(self.win, self.side_key)
        QApplication.instance().installEventFilter(self.side_keys)
        if self.web is not None:
            QApplication.instance().focusChanged.connect(self.on_focus)
            sheet.textChanged.connect(lambda: not self.web_on and self.web_tab()
                                      and self.later("webview", 40, self.sync_web_live))
        self.opened, self.saved_paragraphs = time.monotonic(), 0  # para «Tu trabajo» de «Cifras»
        sheet.textChanged.connect(lambda: self.later("status", 300, self.update_status))
        # Sin reiniciar la cuenta en cada aviso (la ortografía repinta y avisa sin parar): como mucho 5 s de trabajo perdido
        sheet.textChanged.connect(lambda: "draft" in self.pending or self.later("draft", DRAFT_MS, self.write_draft))
        sheet.cursorPositionChanged.connect(lambda: self.later("source", 150, self.draw_source))
        sheet.cursorPositionChanged.connect(lambda: self.later("follow", 200, self.follow_cursor))
        sheet.textChanged.connect(lambda: self.code.isVisible() and not self.applying_code
                                  and self.later("coderefresh", 100, self.refresh_code))
        sheet.cursorPositionChanged.connect(self.update_tools)
        # Un clic o una tecla en la hoja mientras se lee: la voz se calla, como al pulsar Parar
        sheet.cursorPositionChanged.connect(lambda: self.reading is not None and not self.web_on
                                            and self.sheet.textCursor().position() != self.reading.position()
                                            and self.stop_reading())
        sheet.currentCharFormatChanged.connect(lambda _: self.update_tools())
        sheet.undoAvailable.connect(lambda _: self.sync_undo())
        sheet.redoAvailable.connect(lambda _: self.sync_undo())
        self.code.undoAvailable.connect(lambda _: self.sync_undo())
        self.code.redoAvailable.connect(lambda _: self.sync_undo())
        sheet.textChanged.connect(lambda: self.later("extra", 250, self.refresh_marks))
        sheet.textChanged.connect(lambda: self.later("spell", 1000, self.check_new_words))
        # La pestaña EPUB: lo escrito, al momento en su párrafo (patch_epub); rehecha entera, al parar de escribir
        sheet.textChanged.connect(lambda: self.web_shown() and not self.web_on and self.later("epublive", 30, self.patch_epub))
        sheet.textChanged.connect(lambda: self.web_shown() and self.later("web", 1200, self.draw_web))
        sheet.command.connect(self.on_command)
        sheet.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        sheet.customContextMenuRequested.connect(self.context_menu)
        # el diccionario es de español; en Opciones → Editor se puede no subrayar
        self.spell = app.spell_process() if app.spanish() and self.option("editor_spell") else None
        if self.spell:  # todo el libro de una vez; luego, lo que se escriba
            self.send_words(words=app.spell_candidates())
            qtk.after(300, self.poll_spelling)
        editor.watch(self.scroll.viewport(), self.on_panel_event)
        editor.watch(win, lambda event: event.type() == QEvent.Type.Resize and self.later("ribbon", 60, self.fit_ribbon)
                     and False)
        win.finished.connect(lambda _: self.on_destroy())
        app.text_editor = self
        win.setModal(True)  # con la lista activa se podía abrir otro PDF u otro editor y aceptar sobre otros bloques
        self.fit_ribbon()  # antes de mostrarla: con la cinta ancha, maximizada quedaba más ancha que la pantalla
        # Maximizada al crearla, Windows la dejaba del tamaño de su contenido: se maximiza ya mostrada, y hasta
        # entonces ocupa la pantalla (a su tamaño se veía una ventana pequeña un instante, como si se abriera otra).
        # Transparente no: Chromium no volvía a dibujarla
        win.setGeometry(win.screen().availableGeometry())
        win.show()
        qtk.after(0, self.maximize)
        self.update_tools()
        self.draw_source()
        sheet.setFocus()

    def ribbon(self):
        """Cinta como la de Word: pestañas Inicio, Insertar, Revisar y Vista con sus grupos separados por una
        línea, botones grandes para lo principal y, a la derecha de las pestañas, siempre a la vista, en qué
        capítulo se está y a cuál ir. Repartida en pestañas cabe en 1366 px con el nombre de cada botón."""
        bar = self.ribbon_bar = QFrame(objectName="ribbon")
        column = QVBoxLayout(bar)
        column.setContentsMargins(10, 2, 10, 4)
        column.setSpacing(2)
        tabs, pages = self.ribbon_tabs, self.ribbon_pages = QTabBar(), QStackedWidget()
        tabs.setDrawBase(False)
        tabs.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # la hoja se queda con el foco al cambiar de pestaña
        tabs.currentChanged.connect(pages.setCurrentIndex)
        # Como Word: doble clic en una pestaña oculta la cinta (o la fija otra vez); oculta, un clic la muestra
        # encima de la hoja hasta un clic fuera o hasta usar uno de sus botones
        self.ribbon_pinned, self.pinned_at = True, 0.0
        self.ribbon_float = QFrame(self.win, objectName="ribbon")
        float_column = QVBoxLayout(self.ribbon_float)
        float_column.setContentsMargins(10, 2, 10, 4)
        self.ribbon_float.hide()
        tabs.tabBarDoubleClicked.connect(lambda _: self.pin_ribbon(not self.ribbon_pinned))
        tabs.tabBarClicked.connect(lambda _: self.ribbon_pinned or qtk.after(0, self.float_ribbon))
        # Solo mientras flota (float_ribbon): puesto siempre, miraba cada evento de la app, también los miles de
        # crear el editor
        self.ribbon_watch = editor._Filter(self.win, self.outside_ribbon)
        # Botones que se quedan en su icono si la cinta no cabe (compact_to): (botón, texto, texto corto o None
        # si se queda en su glifo, nivel)
        self.compact, self.ribbon_level, self.ribbon_rows = [], 0, []
        self.glyphs = []  # (botón, glifo, tamaño): se vuelven a pintar al cambiar de tema (recolor)
        ink = self.colors["ink"]

        def big(button, icon):
            """Botón grande de Word: glifo arriba y nombre debajo."""
            self.glyphs.append((button, theme.ICONS[icon], GLYPH))
            button.setIcon(_glyph_icon(theme.ICONS[icon], ink))
            button.setIconSize(QSize(GLYPH, GLYPH))
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)

        def tool(text, tip, action, checkable=False, font=None, short=None, level=1, icon=None, glyph=None):
            """Botón de la cinta: icon (clave de theme.ICONS) lo hace grande; glyph pone el glifo pequeño al lado
            del texto, dibujado como icono: como letra, «≡» o «⇥» apenas se veían."""
            button = QToolButton(objectName="tool")
            button.setText(text)
            button.setToolTip(tip)
            if short or (glyph and text):
                self.compact.append((button, text, short, level))
            if icon:
                big(button, icon)
            if glyph:
                self.glyphs.append((button, theme.ICONS[glyph], SMALL_GLYPH))
                button.setIcon(_glyph_icon(theme.ICONS[glyph], ink, SMALL_GLYPH))
                button.setIconSize(QSize(SMALL_GLYPH, SMALL_GLYPH))
                button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon if text
                                          else Qt.ToolButtonStyle.ToolButtonIconOnly)
            button.setCheckable(checkable)
            if font:
                button.setFont(font)
            button.clicked.connect(lambda: (self.drop_ribbon(), action(), self.focus_page(), self.update_tools()))
            return button

        def menu_tool(text, tip, items, icon=None):
            """Botón con menú («Símbolos ▾»): items [(texto, acción)] y None para una línea separadora."""
            button = QToolButton(objectName="tool")
            button.setText(text)
            button.setToolTip(tip)
            button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            if icon:
                big(button, icon)
            menu = QMenu(button)
            for item in items:
                if item is None:
                    menu.addSeparator()
                else:
                    menu.addAction(item[0], lambda fn=item[1]: (self.drop_ribbon(), fn(), self.focus_page(),
                                                                self.update_tools()))
            button.setMenu(menu)
            _attach(button, menu)
            return button, menu

        def page(name):
            widget = QWidget()
            row = QHBoxLayout(widget)
            row.setContentsMargins(0, 2, 0, 0)
            row.setSpacing(10)
            self.ribbon_rows.append(row)
            pages.addWidget(widget)
            tabs.addTab(name)

        def group(caption, *rows):
            """Grupo de la pestaña: botones pequeños en dos filas, como Office, o una fila de botones grandes;
            el nombre abajo y una línea que lo separa del anterior."""
            row = self.ribbon_rows[-1]
            if row.count():
                line = QFrame(objectName="sep")
                line.setFixedWidth(1)
                row.addWidget(line)
            box = QVBoxLayout()
            box.setSpacing(2)
            box.addStretch(1)
            for widgets in rows:  # centradas en el grupo, como en Word: alineadas a la izquierda parecía desordenado
                box.addLayout(_row(1, *widgets, 1, spacing=2))
            box.addStretch(1)
            label = _label(caption, "muted")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            box.addWidget(label)
            row.addLayout(box)
            return label

        sheet = self.sheet
        bold_font, italic_font = QFont("Segoe UI", 11, QFont.Weight.Bold), QFont("Segoe UI", 11)
        italic_font.setItalic(True)
        underline_font, strike_font = QFont("Segoe UI", 11), QFont("Segoe UI", 10)
        underline_font.setUnderline(True)
        strike_font.setStrikeOut(True)

        page("Inicio")
        self.undo_btn = tool("Deshacer", "Deshacer (Ctrl+Z)", lambda: self.do("undo"), glyph="undo")
        self.redo_btn = tool("Rehacer", "Rehacer (Ctrl+Y)", lambda: self.do("redo"), glyph="redo")
        self.undo_btn.setEnabled(False)
        self.redo_btn.setEnabled(False)
        self.undo_group = group("Deshacer", [self.undo_btn], [self.redo_btn])
        self.bold_btn = tool("N", "Negrita (Ctrl+N)", lambda: self.do("toggle_bold"), True, bold_font)
        self.italic_btn = tool("K", "Cursiva (Ctrl+K)", lambda: self.do("toggle_italic"), True, italic_font)
        self.case_btn = tool("Aa Mayúsculas", "Mayúsculas y minúsculas (Mayús+F3)", lambda: self.do("change_case"), short="Aa",
                             level=2)
        self.sup_btn = tool("x²", "Superíndice: 1.ᵉʳ, m² (Ctrl+Mayús+=)", lambda: self.do("toggle_sup"), True)
        self.caps_btn = tool("Vᴇʀs", "Versalitas: rangos, habilidades, sonidos en letra pequeña", lambda: self.do("toggle_small_caps"),
                             True)
        self.underline_btn = tool("S", "Subrayado (Ctrl+S)", lambda: self.do("toggle_underline"), True, underline_font)
        self.strike_btn = tool("abc", "Tachado", lambda: self.do("toggle_strike"), True, strike_font)
        self.sub_btn = tool("x₂", "Subíndice: H₂O (Ctrl+=)", lambda: self.do("toggle_sub"), True)
        group("Fuente", [self.bold_btn, self.italic_btn, self.underline_btn, self.strike_btn, self.sup_btn, self.sub_btn],
              [self.case_btn, self.caps_btn])
        # Letra, tamaño y color: como en Word, sobre lo elegido; salen en el libro como <span style>. En su pestaña:
        # en Inicio no cabían en 1366 px con el nombre de cada botón
        self.family_box = QComboBox()
        self.family_box.setToolTip("Tipo de letra de lo elegido (las del libro primero)")
        self.family_box.setMinimumContentsLength(12)
        self.family_box.setPlaceholderText("Letra")
        self.family_box.addItems(["(La del libro)"] + sorted(editor.FAMILIES.values()) + list(convert.FONTS))
        self.family_box.setCurrentIndex(-1)
        self.family_box.activated.connect(lambda n: (self.do("set_family", None if n == 0 else self.family_box.itemText(n)),
                                                     sheet.setFocus(), self.update_tools()))
        grow = tool("A⁺", "Agrandar la letra (Ctrl+Mayús+>)", lambda: self.do("grow_font", 1))
        shrink = tool("A⁻", "Reducir la letra (Ctrl+Mayús+<)", lambda: self.do("grow_font", -1))
        self.color_btn, _ = menu_tool("Color ▾", "Color del texto elegido",
                                      [(name, lambda c=color: self.do("set_color", c)) for name, color in theme.TEXT_COLORS])
        letter = [self.family_box], [grow, shrink, self.color_btn]
        self.style = QComboBox()
        self.style.addItems(STYLES)
        self.style.setToolTip("Estilo del párrafo: capítulo, subtítulo, título 3 y 4 o cita (Ctrl+T: capítulo o normal)")
        self.style.setMinimumWidth(110)
        self.style.activated.connect(lambda n: (self.do("set_paragraph_style", STYLES[n]), sheet.setFocus(),
                                                self.update_tools()))
        # Las tres alineaciones, también dentro de un recuadro; sin texto: son tres y la cinta no da para más
        self.left_btn = tool("", "Alinear el párrafo a la izquierda", lambda: self.do("set_align", None), True,
                             glyph="left")
        self.center_btn = tool("", "Centrar el párrafo: cartas, carteles (Ctrl+E)", lambda: self.do("toggle_center"), True,
                               glyph="center")
        self.frame_btn, _ = menu_tool("Recuadro ▾", "Meter los párrafos elegidos en un recuadro (ventana de estado, "
                                      "carta, nota del traductor, cita) o sacarlos de él",
                                      [("Recuadro", lambda: self.on_sheet(lambda: self.sheet.set_frame("")))]
                                      + [(name, lambda k=kind: self.on_sheet(lambda: self.sheet.set_frame(k)))
                                         for kind, name in convert.BOX_KINDS.items()]
                                      + [None, ("Quitar recuadro", lambda: self.on_sheet(lambda: self.sheet.set_frame(None)))])
        self.frame_btn.setCheckable(True)
        self.right_btn = tool("", "Alinear el párrafo a la derecha: firmas y fechas de cartas (Ctrl+D)",
                              lambda: self.do("toggle_right"), True, glyph="right")
        self.list_btn, _ = menu_tool("Lista ▾", "Lista de viñetas o numerada con los párrafos elegidos, como en Word. "
                                     "Intro en un elemento vacío sale de la lista", [
            ("●  Viñetas", lambda: self.do("set_list", "disc")), ("○  Viñetas huecas", lambda: self.do("set_list", "circle")),
            ("■  Viñetas cuadradas", lambda: self.do("set_list", "square")), None,
            ("1.  Numerada", lambda: self.do("set_list", "decimal")),
            ("a.  Letras minúsculas", lambda: self.do("set_list", "lower-alpha")),
            ("A.  Letras mayúsculas", lambda: self.do("set_list", "upper-alpha")),
            ("i.  Romanos en minúsculas", lambda: self.do("set_list", "lower-roman")),
            ("I.  Romanos", lambda: self.do("set_list", "upper-roman")), None,
            ("Quitar la lista", lambda: self.do("set_list", None))])
        self.justify_btn = tool("", "Justificar el párrafo: el texto llega a los dos márgenes", lambda: self.do("toggle_justify"),
                                True, glyph="justify")
        indent = tool("Sangría", "Con o sin sangría en la primera línea del párrafo", lambda: self.do("toggle_indent"),
                      icon="indent")
        group("Párrafo", [self.style, self.left_btn, self.center_btn, self.right_btn, self.justify_btn],
              [self.frame_btn, self.list_btn,
               tool("Unir", "Une el párrafo con el siguiente: arregla uno partido por un salto de página (Ctrl+J)",
                    lambda: self.do("join_next"), level=2, glyph="join")])
        group("Edición", [tool("Buscar", "Buscar y reemplazar en todos los capítulos (Ctrl+F, Ctrl+H)",
                               lambda: self.show_find_bar(False), icon="search")])

        page("Formato")
        group("Letra", *letter)
        group("Limpiar", [tool("Borrar\nformato", "Quita cursiva, negrita, subrayado, tamaño y color de lo elegido; los "
                               "enlaces y el furigana se quedan", lambda: self.do("clear_format"), icon="clear")])
        group("Párrafo", [indent, tool("Salto de\nlínea", "Nueva línea dentro del mismo párrafo, como en una carta o "
                                       "un poema (Mayús+Intro)", lambda: self.do("insert_line_break"), icon="newline")])
        group("Japonés", [tool("Furigana", "Lectura pequeña encima de un nombre o una palabra japonesa (<ruby>): "
                                           "elige el texto y escribe su lectura", self.add_ruby, icon="ruby")])
        openings = self.book_openings()
        if openings:  # EPUB de entrada: sus clases de párrafo, para no escribir <p class="…"> en el código
            classes, _ = menu_tool("Estilo del\noriginal ▾", "Clases de párrafo que usa el libro (sin sangría, "
                                   "centrado, espacio arriba…): al párrafo del cursor o a los elegidos",
                                   [("Párrafo normal (sin clase)", lambda: self.on_sheet(lambda: self.sheet.set_opening("")))]
                                   + [None] + [(label, lambda o=opening: self.on_sheet(lambda: self.sheet.set_opening(o)))
                                               for label, opening in openings], icon="style")
            group("Original", [classes])

        page("Insertar")
        self.symbols_btn, _ = menu_tool("Símbolos ▾", "Raya, comillas, signos de apertura y tipografía española", [
            ("— Raya   (Ctrl+Alt+-)", lambda: self.do("insert_symbol", "—")),
            ("… Puntos suspensivos   (Ctrl+Alt+.)", lambda: self.do("insert_symbol", "…")),
            ("« » Comillas   (Ctrl+Alt+Q)", lambda: self.do("wrap", "«", "»")),
            ("¿ ? Pregunta", lambda: self.do("wrap", "¿", "?")), ("¡ ! Exclamación", lambda: self.do("wrap", "¡", "!")), None,
            ("Corregir tipografía del capítulo", lambda: self.on_sheet(self.fix_typography))], icon="symbols")
        group("Símbolos", [self.symbols_btn])
        self.break_btn, _ = menu_tool("Separador ▾", "Separador de escena tras el párrafo, o los del libro hechos "
                                      "icono (una silueta del color del texto)", [
            ("Separador de escena", lambda: self.do("insert_break")), None,
            ("Separadores del libro como icono", self.icon_separators),
            ("Icono propio desde una imagen…", self.own_icon_separator)], icon="scene")
        group("Texto", [self.break_btn,
                        tool("Nota…", "Nota al pie: se escribe en un cuadro; en el EPUB sale al tocar su llamada. Con "
                             "el cursor en una nota, la edita", self.note_dialog,
                             icon="note")])
        # Un botón con menú: dos botones grandes ensanchaban «Insertar» más que la pantalla de 1366 px
        self.picture_btn, _ = menu_tool("Imagen ▾", "Imagen tras el párrafo del cursor; también se pega una copiada "
                                        "(Ctrl+V)", [
            ("Desde un archivo…", lambda: self.on_sheet(self.insert_image)),
            ("Página del original que se ve a la derecha", lambda: self.on_sheet(self.page_as_image))], icon="image")
        group("Ilustraciones", [self.picture_btn])
        group("Vínculos", [tool("Enlace", "Enlace en el texto elegido: a una página web o a otro capítulo del libro "
                                "(Ctrl+Alt+K)", self.show_link_bar, icon="link")])
        group("Tabla", [tool("Tabla…", "Tabla de filas y columnas (pantallas de estado, listas de objetos): una fila por "
                             "línea, con « | » entre las celdas", self.table_dialog, icon="table")])
        # un botón con menú: dos grandes ensanchaban «Insertar» más que la pantalla
        chapter_tools, _ = menu_tool("Capítulo ▾", "Una página nueva, dividir el capítulo en el cursor, o un "
                                     "capítulo nuevo con el título oculto (en el índice, no en la página)", [
            ("Página nueva…   (créditos, nota del traductor: un capítulo propio)", self.new_page),
            ("Dividir el capítulo aquí", self.show_split_bar),
            ("Título oculto…   (capítulo de solo una ilustración: «Contenido», un mapa)", self.hidden_title_dialog)],
            icon="split")
        group("Estructura", [chapter_tools])

        page("Revisar")
        self.changes_btn, self.changes_menu = menu_tool("Cambios ▾", "Párrafos que editaste en este capítulo", [
            ("Cambio siguiente   (F7)", self.next_change),
            ("Ver todos los cambios…", self.show_all_changes),
            ("Volver al original", lambda: self.on_sheet(self.restore_current))],
            icon="changes")
        self.issues_btn, self.issues_menu = menu_tool("Avisos ▾", "Avisos del informe de calidad en este capítulo", [
            ("Aviso siguiente   (F8)", self.next_issue)], icon="warn")
        for menu in (self.changes_menu, self.issues_menu):  # primera línea: el recuento, que no se pulsa
            info = QAction("", menu)
            info.setEnabled(False)
            menu.insertAction(menu.actions()[0], info)
            menu.insertSeparator(menu.actions()[1])
        self.changes_menu.addSeparator()
        self.show_changes_action = self.changes_menu.addAction("Mostrar cambios")
        self.show_changes_action.setCheckable(True)
        self.show_changes_action.toggled.connect(self.set_show_changes)
        group("Revisión", [self.changes_btn, self.issues_btn])
        group("Corrección", [tool("Cifras", "Cifras: palabras, párrafos, minutos de lectura y diálogo del capítulo y "
                                  "del libro", lambda: self.flush_web(self.show_stats) if self.web_on else self.show_stats(),
                                  icon="stats")])
        self.read_btn = tool("Leer", "Leer en voz alta desde el párrafo del cursor, para corregir de oído "
                             "(Esc, un clic o el mismo botón para parar)", self.toggle_reading, True, icon="read")
        group("Voz", [self.read_btn])

        page("Vista")
        self.nav_btn = tool("Navegación", "Panel con los títulos, avisos, comentarios y cambios del capítulo (F9)",
                            self.toggle_nav, True, icon="nav")
        self.files_btn = tool("Archivos", "Explorador de archivos del EPUB, como el de Calibre: texto, estilos, imágenes, "
                              "tipos de letra y lo demás", self.toggle_files, True, icon="files")
        # Casilla como las de «Mostrar» de Word; va a la par que «Mostrar cambios» del menú Cambios
        self.show_changes_box = QCheckBox("Mostrar cambios")
        self.show_changes_box.setToolTip("Marca en la hoja los párrafos que editaste")
        self.show_changes_box.toggled.connect(self.show_changes_action.setChecked)
        self.show_changes_action.toggled.connect(self.show_changes_box.setChecked)
        # Original, EPUB y hoja web con fondo oscuro y letra clara (las ilustraciones, tal cual); se recuerda
        self.side_dark = bool(self.app.settings.get("editor_dark", False))
        self.dark_box = QCheckBox("Modo oscuro")
        self.dark_box.setToolTip("Original, EPUB y hoja web con fondo oscuro y letra clara, como el modo noche de los "
                                 "lectores; las ilustraciones no cambian")
        self.dark_box.setChecked(self.side_dark)
        self.dark_box.toggled.connect(self.set_side_dark)
        group("Mostrar", [self.nav_btn, self.files_btn, self.show_changes_box, self.dark_box])
        for row in self.ribbon_rows:
            row.addStretch(1)

        # A la derecha de las pestañas, como «Compartir» en Word: dónde estás y a dónde ir, en cualquier pestaña.
        # Sin ◀ ▶: ya están bajo la página del PDF (◀◀ ▶▶) y con Ctrl+RePág y Ctrl+AvPág
        QShortcut(QKeySequence("Ctrl+PgUp"), self.win, lambda: self.step_section(-1))
        QShortcut(QKeySequence("Ctrl+PgDown"), self.win, lambda: self.step_section(1))
        self.chapter_btn = QToolButton(objectName="menu")  # ▾ como los demás botones con menú
        self.chapter_btn.setToolTip("Ir a otro capítulo")
        self.review_btn = tool("✓", "Marca el capítulo como revisado y pasa al siguiente sin revisar (Ctrl+Intro)",
                               self.toggle_reviewed, True)
        self.tabs_row = _row(tabs, 1, self.chapter_btn, self.review_btn, spacing=6)
        column.addLayout(self.tabs_row)
        column.addWidget(pages)
        self.chapters_popup()
        return bar

    def recolor(self, dark):
        """Tema cambiado con el editor abierto: los iconos de la cinta, dibujados con el color del texto, se
        quedaban con el del tema anterior (apenas se veían)."""
        self.colors = theme.colors(dark)
        self.code.set_colors(theme.code_colors(dark))
        if self.show_changes_action.isChecked():  # lo puesto y lo quitado, con los colores del tema
            self.set_show_changes(True)
        for button, glyph, size in self.glyphs:
            if shiboken6.isValid(button):
                button.setIcon(_glyph_icon(glyph, self.colors["ink"], size))

    def pin_ribbon(self, on):
        """Fija la cinta (on) o la deja en solo la fila de pestañas."""
        self.ribbon_pinned, self.pinned_at = on, time.monotonic()
        self.drop_ribbon()

    def float_ribbon(self):
        """Con la cinta oculta, un clic en una pestaña la muestra encima de la hoja, sin moverla."""
        if self.ribbon_pinned or not shiboken6.isValid(self.ribbon_bar):
            return
        if time.monotonic() - self.pinned_at < QApplication.doubleClickInterval() / 1000:
            return  # el clic del propio doble clic que acaba de ocultarla
        frame, pages = self.ribbon_float, self.ribbon_pages
        frame.layout().addWidget(pages)  # pasa de la cinta al recuadro flotante
        pages.show()
        top = self.ribbon_bar.mapTo(self.win, QPoint(0, self.ribbon_bar.height()))
        frame.setGeometry(top.x(), top.y(), self.ribbon_bar.width(), frame.sizeHint().height())
        frame.show()
        frame.raise_()
        QApplication.instance().installEventFilter(self.ribbon_watch)

    def drop_ribbon(self):
        """La cinta vuelve a su sitio: visible si está fijada, oculta si no."""
        if not shiboken6.isValid(self.ribbon_float):
            return
        self.ribbon_float.hide()
        QApplication.instance().removeEventFilter(self.ribbon_watch)
        self.ribbon_bar.layout().addWidget(self.ribbon_pages)
        self.ribbon_pages.setVisible(self.ribbon_pinned and not self.focused)  # pantalla completa: sin cinta

    def outside_ribbon(self, event):
        """Un clic fuera de la cinta flotante (y de sus pestañas) la oculta; los menús de sus botones son otra
        ventana y no cuentan."""
        if (event.type() != QEvent.Type.MouseButtonPress or not shiboken6.isValid(self.ribbon_float)
                or not self.ribbon_float.isVisible()):
            return False
        widget = QApplication.widgetAt(event.globalPosition().toPoint())
        if widget is None or widget.window() is not self.win:
            return False
        if not (self.ribbon_float.isAncestorOf(widget) or widget is self.ribbon_float
                or self.ribbon_tabs.isAncestorOf(widget) or widget is self.ribbon_tabs):
            self.drop_ribbon()
        return False

    def fit_ribbon(self):
        """Si la cinta no cabe (1366 px, o 1093 con el zoom de Windows al 125 %), quedan solo con su icono
        primero deshacer, insertar y buscar, luego párrafo y revisar, y al final el botón de capítulo se
        estrecha y los grupos se juntan; el texto sigue en la ayuda."""
        if not shiboken6.isValid(self.ribbon_bar):
            return
        screen = self.win.screen().availableGeometry().width()
        room = min(self.win.width(), screen) if self.win.isVisible() else screen  # se abre maximizado
        if getattr(self, "fitted", None) == room:  # al maximizarla ya mostrada, el mismo ancho: medir no cambia nada
            return
        column = self.win.layout()
        # Al medir un nivel ancho, Qt agrandaba la ventana a su mínimo y luego no la estrechaba: sin restricción
        column.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        try:
            for level in range(len(CHAPTER_WIDTHS)):
                self.ribbon_level = level
                for row in self.ribbon_rows:  # grupos más juntos si no cabe
                    row.setSpacing({0: 10, 1: 6, 2: 6}.get(level, 4))
                self.compact_to(level)
                self.show_chapter(next((n for n, s in enumerate(self.sections) if s[1:3] == self.current), None))
                self.show_counts()
                # Los botones piden su tamaño nuevo más tarde (LayoutRequest): sin esto se medía el de antes
                QApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest.value)
                # la cinta oculta no está dentro de ribbon_bar: se mide aparte
                if max(self.ribbon_bar.sizeHint().width(), self.ribbon_pages.sizeHint().width() + 20) <= room:
                    break
            self.fitted = room
        finally:
            column.setSizeConstraint(QLayout.SizeConstraint.SetDefaultConstraint)

    def compact_to(self, level):
        """Botones de la cinta en su forma corta desde su nivel: el texto corto, o solo el glifo."""
        for button, text, short, needs in self.compact:
            if short:
                button.setText(short if level >= needs else text)
            else:
                button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly if level >= needs
                                          else Qt.ToolButtonStyle.ToolButtonTextBesideIcon)

    def chapters_popup(self):
        """Lista de capítulos bajo el botón: pequeña, con scroll y un filtro. Un menú de una acción por capítulo
        (Mitsuha 1: 34) se repartía en columnas por toda la pantalla: es un menú con la lista dentro, del botón
        como los demás ▾. Como ventana suelta, el clic en el botón para cerrarla la volvía a abrir."""
        pop = self.chapter_popup = QMenu(self.win)
        box = QWidget()
        column = QVBoxLayout(box)
        column.setContentsMargins(6, 6, 6, 6)
        self.chapter_filter = QLineEdit()
        self.chapter_filter.setPlaceholderText("Buscar capítulo")
        self.chapter_filter.textChanged.connect(self.filter_chapters)
        self.chapter_filter.returnPressed.connect(self.pick_first_chapter)
        self.chapter_list = QListWidget()
        self.chapter_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.chapter_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.chapter_list.itemClicked.connect(self.pick_chapter)
        self.chapter_list.itemActivated.connect(self.pick_chapter)
        column.addWidget(self.chapter_filter)
        column.addWidget(self.chapter_list, 1)
        box.setFixedSize(460, 380)
        action = QWidgetAction(pop)
        action.setDefaultWidget(box)
        pop.addAction(action)
        pop.aboutToShow.connect(self.show_chapters)
        self.chapter_btn.setMenu(pop)
        self.chapter_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        _attach(self.chapter_btn, pop)  # bajo el botón, o encima en la barra de abajo (pantalla completa)

    def show_chapters(self):
        """Al abrirse la lista: sin filtro, con «•» en los cambiados y el capítulo de ahora a la vista."""
        self.mark_changed()
        self.chapter_filter.clear()

        def ready():  # ya visible: antes no se desplaza ni toma el foco
            current = self.chapter_list.currentItem()
            if current:
                self.chapter_list.scrollToItem(current, QListWidget.ScrollHint.PositionAtCenter)
            self.chapter_filter.setFocus()

        qtk.after(0, lambda: shiboken6.isValid(self.chapter_list) and ready())

    def filter_chapters(self, text):
        for n in range(self.chapter_list.count()):
            item = self.chapter_list.item(n)
            item.setHidden(bool(text) and text.lower() not in item.text().lower())

    def pick_first_chapter(self):
        item = next((self.chapter_list.item(n) for n in range(self.chapter_list.count())
                     if not self.chapter_list.item(n).isHidden()), None)
        if item:
            self.pick_chapter(item)

    def pick_chapter(self, item):
        self.chapter_popup.hide()
        self.open_section(*self.sections[item.data(Qt.ItemDataRole.UserRole)][1:3])

    def find_bar(self):
        """Barra de buscar y reemplazar bajo la cinta (Ctrl+F, Ctrl+H): todos los capítulos, uno a uno."""
        bar = self.find_frame = QFrame(objectName="ribbon")
        self.find_entry, self.replace_entry = QLineEdit(), QLineEdit()
        self.find_entry.setPlaceholderText("Buscar en el libro")
        self.replace_entry.setPlaceholderText("Reemplazar por")
        self.find_entry.returnPressed.connect(lambda: self.find(1))
        self.find_info = _label("", "muted")
        self.whole_box, self.case_box = QCheckBox("Palabra completa"), QCheckBox("Mayúsculas")
        self.case_box.setToolTip("Distinguir mayúsculas y minúsculas")
        self.regex_box = QCheckBox(".*")
        font = self.regex_box.font()  # dos signos finos: en negrita y algo mayores se leen (contraste 1,4:1 si no)
        font.setBold(True)
        font.setPointSizeF(font.pointSizeF() + 2)
        self.regex_box.setFont(font)
        self.regex_box.setToolTip(r"Expresión regular: \d+ cifras, (\w+) una palabra… En el reemplazo, \1 es lo "
                                  "del primer paréntesis")
        for widget in (self.find_entry, self.regex_box):  # la expresión se revisa al escribirla
            (widget.textChanged if widget is self.find_entry else widget.toggled).connect(lambda _: self.regex_error())
        close = _button("✕", lambda: (bar.hide(), self.focus_page()))
        close.setToolTip("Cerrar (Esc)")
        all_btn = _button("Todos", self.show_all_matches)
        all_btn.setToolTip("Lista de todos los resultados del libro")
        bar.setLayout(_row(self.find_entry, qtk.ArrowButton(-1, lambda: self.find(-1), "Anterior"),
                           qtk.ArrowButton(1, lambda: self.find(1), "Siguiente"),
                           all_btn, self.whole_box, self.case_box, self.regex_box,
                           self.replace_entry, _button("Reemplazar", self.replace_one),
                           _button("Reemplazar todo", self.replace_everywhere), self.find_info, 1, close,
                           margins=(10, 6, 10, 6)))
        self.escape_closes(bar, self.find_entry, self.replace_entry)
        bar.hide()
        return bar

    def link_bar(self):
        """Barra para poner o quitar un enlace web en el texto elegido (Ctrl+Alt+K)."""
        bar = self.link_frame = QFrame(objectName="ribbon")
        self.link_entry = QLineEdit()
        self.link_entry.setPlaceholderText("Dirección del enlace: https://…")
        self.link_entry.returnPressed.connect(self.apply_link)
        self.link_info = _label("", "muted")
        # o a otro capítulo del libro: se guarda por su título y el EPUB lleva a su archivo
        self.link_chapter = QComboBox()
        self.link_chapter.setPlaceholderText("o a un capítulo…")
        self.link_chapter.setMinimumContentsLength(18)
        self.link_chapter.activated.connect(lambda n: self.apply_link(
            "lnc:cap:" + urllib.parse.quote(self.link_chapter.itemText(n))))
        bar.setLayout(_row(_label("Enlace:"), self.link_entry, self.link_chapter,
                           _button("Aplicar", self.apply_link, "primary"),
                           _button("Quitar enlace", lambda: self.apply_link("")), self.link_info, 1,
                           _button("✕", lambda: (bar.hide(), self.focus_page())), margins=(10, 6, 10, 6)))
        self.escape_closes(bar, self.link_entry)
        bar.hide()
        return bar

    def focus_page(self):
        """El foco a la hoja a la vista: con la hoja web, a ella (en la de Qt, oculta, se escribía a ciegas)."""
        (self.web if self.web_on else self.sheet).setFocus()

    def icon_separators(self):
        """Los separadores dibujados del libro, como icono (convert.trace_svg): la opción «Icono del original» de la
        ventana. La pestaña EPUB lo enseña ya."""
        self.app.separator_var.set("Icono del original")
        self.app.separator_changed()
        self.draw_web()
        self.tell("Separadores dibujados como icono del color del texto (Separador: «Icono del original»)")

    def own_icon_separator(self):
        """Una imagen propia (PNG o JPG) hecha icono, para todos los separadores: la opción «Icono propio»."""
        self.app.separator_var.set("Icono propio")
        self.app.separator_changed(self.win)
        self.draw_web()
        if self.app.separator_var.get() == "Icono propio":
            self.tell("Separadores con tu icono (Separador: «Icono propio»)")

    def tell(self, text):
        """Nota de la última herramienta en la barra de estado; se borra sola: tras deshacer ya no sería cierta."""
        self.note.setText(text)
        self.later("note", 6000, lambda: shiboken6.isValid(self.note) and self.note.setText(""))

    def escape_closes(self, bar, *entries):
        """Esc en el campo de una barra la cierra. Un atajo no bastaba: con la ventana sin activar (tras la
        lista de «Todos») Esc llegaba al diálogo, que lo tomaba como cancelar el editor."""
        def handler(event):
            if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
                bar.hide()
                self.focus_page()
                return True
            return False

        for entry in entries:
            editor.watch(entry, handler)

    def on_command(self, name):
        """Atajos de la hoja que abren una barra: Ctrl+Alt+K enlace, Ctrl+F buscar, Ctrl+H reemplazar."""
        actions = {"undo": lambda: self.do("undo"), "redo": lambda: self.do("redo"),
                   "link": self.show_link_bar, "reviewed": self.toggle_reviewed, "next_change": self.next_change,
                   "next_issue": self.next_issue, "nav": self.toggle_nav, "focus": self.toggle_focus,
                   # Ctrl+Mayús+F12 con un hueco a la vista: guarda pantalla, memoria y maquetación para hallar la causa
                   "diagnose": lambda: messagebox.showinfo("Diagnóstico de la hoja", "Guardado en:\n" + str(
                       self.sheet.diagnose(_data_dir() / "diagnostico" / time.strftime("%Y%m%d-%H%M%S"))))}
        if name in actions:
            actions[name]()
        else:
            self.show_find_bar(name == "replace")

    def hide_bars(self):
        for bar in (self.find_frame, self.link_frame, self.split_frame, self.issue_frame, self.resume_frame):
            bar.hide()

    def toggle_focus(self):
        """F11: modo concentración, solo la hoja a pantalla completa, sin cinta, barras ni paneles. Se cambia de
        capítulo con dos flechas a los lados de la hoja, y el capítulo y Revisado pasan a la barra de abajo (sin
        ellos no había cómo cambiar de capítulo). F11 o Esc vuelven como estaba."""
        if not self.focused and self.code.isVisible():  # pantalla completa: solo la hoja, sin el código
            self.toggle_code()
            if self.code.isVisible():  # el código tiene un error: se arregla antes
                return
        if not self.focused and self.web_on:  # la hoja web se oculta: lo escrito en ella, a la hoja
            self.flush_web(lambda: (self.web_done(), self.sheet.setFocus()))
        self.focused = not self.focused
        self.drop_ribbon()
        for button in (self.focus_prev, self.focus_next):
            button.setVisible(self.focused)
        self.set_text_zoom(self.text_zoom.value())  # el ancho elegido en pantalla completa, solo en ella
        if not self.focused:
            self.desk.unsetCursor()
        if self.focused:  # tras los avisos, que tienen su sitio fijo: no se mueven al cambiar el texto
            self.status_row.insertWidget(2, self.chapter_btn)
            self.status_row.insertWidget(3, self.review_btn)
        else:  # a su sitio en la fila de las pestañas: tras las pestañas y el espacio
            self.tabs_row.insertWidget(2, self.chapter_btn)
            self.tabs_row.insertWidget(3, self.review_btn)
        if self.focused:
            self.before_focus = [w for w in (self.ribbon_bar, self.side, self.nav, self.files, self.view_tabs,
                                             self.find_frame,
                                             self.link_frame, self.split_frame, self.issue_frame, self.resume_frame)
                                 if not w.isHidden()]
            for widget in self.before_focus:
                widget.hide()
            self.win.showFullScreen()
            self.tell("Modo concentración: F11 o Esc para salir")
        else:
            for widget in self.before_focus:
                widget.show()
            # De pantalla completa a maximizada de golpe, Windows la dejaba más ancha que la pantalla
            self.win.showNormal()
            qtk.after(0, lambda: shiboken6.isValid(self.win) and self.win.showMaximized())
            self.note.setText("")
        self.focus_page()

    # Mostrar cambios, como el control de cambios de Word con todas las marcas: lo puesto subrayado y lo quitado
    # tachado en su sitio, en la hoja, el código y la hoja web (la pestaña EPUB es como saldrá el libro: sin marcas)
    def change_colors(self):
        """(fondo de lo puesto, fondo de lo quitado) del tema, como GitHub."""
        colors = theme.mark_colors(self.app.dark)
        return colors["added"], colors["removed"]

    def set_show_changes(self, on):
        """Solo se repintan los párrafos cambiados de la hoja a la vista (los de la pantalla primero, por tandas); los
        otros capítulos, al volver a ellos, y el código, al abrirlo. Repintarlo todo congelaba la ventana segundos.
        Se recuerda (Opciones → Editor → «Mostrar cambios al abrir»): al reabrir el editor seguía apagado."""
        if self.app.settings.get("editor_changes", False) != bool(on):
            self.app.settings["editor_changes"] = bool(on)
            self.app.save_settings()
        color, removed = self.change_colors()
        for key, page in self.pages.items():
            if key == self.current:
                page["look"].show_changes(color if on else None, removed, self.changes_in_view())
            else:
                page["look"].changes, page["look"].removed, page["stale"] = color if on else None, removed, True
        if self.code.isVisible():
            self.code.show_changes(color if on else None, removed)
        else:
            self.code.look.changes, self.code.look.removed = color if on else None, removed
        if self.web is not None and self.web.loaded:
            self.web_marks()

    def changes_in_view(self):
        """Párrafos cambiados de la hoja, primero los de la pantalla y los que siguen."""
        top = self.sheet.cursorForPosition(QPoint(0, 0)).blockNumber()
        return sorted(self.sheet.changed_blocks(), key=lambda b: (b.blockNumber() < top, b.blockNumber()))

    def book_classes(self):
        """Clases del CSS del libro (sus hojas de estilo, si es un EPUB, y las de la app), ordenadas."""
        css = convert.BASE_CSS + "".join(convert.STYLES.values())
        if self.app.book.origin:
            try:
                with zipfile.ZipFile(self.app.book.origin) as z:
                    css += "".join(z.read(n).decode("utf-8", "replace") for n in z.namelist()
                                   if n.lower().endswith(".css"))
            except (OSError, zipfile.BadZipFile):
                pass  # sin el EPUB de entrada: solo las de la app
        selectors = re.sub(r"\{[^}]*\}", " ", re.sub(r"/\*.*?\*/", "", css, flags=re.S))  # sin valores: «.ttf»
        return sorted(set(re.findall(r"\.([A-Za-z_][\w-]*)", selectors)))

    def code_originals(self):
        """Línea de código de antes de cada línea de la vista «Código HTML» (None: párrafo nuevo), en su orden."""
        out = []
        for block, lines in zip(self.blocks(), self.sheet.all_lines()):
            original = editor.original_of(block)
            if editor.original_group(block)[1]:  # partido con Intro: el código lo compara con el primero (code_group)
                original = None
            for line in lines:
                out.append(None if original is None else editor.to_code(original if len(lines) == 1 else line))
        return out

    def resume_bar(self):
        """«Lo dejaste en «Capítulo 5» · Ir allí»: el editor no salta solo, quien lo abre desde un aviso o una fila
        quiere ir ahí."""
        bar = self.resume_frame = QFrame(objectName="ribbon")
        self.resume_label = _label("")
        self.resume_go = _button("Ir allí", self.resume, "primary")
        bar.setLayout(_row(self.resume_label, self.resume_go, 1,
                           _button("✕", lambda: (bar.hide(), self.focus_page())), margins=(10, 6, 10, 6)))
        self.resume_at = None
        bar.hide()
        return bar

    def offer_resume(self):
        at, blocks = self.app.editor_at, len(self.app.book.blocks)
        if at is None or not 0 <= at < blocks:
            return
        section = next((s for s in self.sections if s[1] <= at < s[2]), None)
        if section is None or section[1:3] == self.current:
            return
        self.resume_at = at
        self.resume_label.setText(f"Lo dejaste en «{section[0]}»")
        self.resume_frame.show()

    def resume(self):
        section = next((s for s in self.sections if s[1] <= self.resume_at < s[2]), None)
        self.resume_frame.hide()
        if section:
            self.open_section(section[1], section[2], self.resume_at)

    def show_link_bar(self, synced=False):
        if self.web_on and not synced:  # la selección de la web, a la hoja de Qt: el enlace se pone allí
            self.web_to_sheet(lambda: self.show_link_bar(True))
            return
        self.link_selection = QTextCursor(self.sheet.textCursor())  # el foco se va a la barra
        if not self.link_selection.hasSelection() and not self.sheet.link_at():
            self.link_info.setText("Elige primero el texto del enlace")
        else:
            self.link_info.setText("")
        self.link_entry.setText(self.sheet.link_at() or "https://")
        self.link_chapter.clear()
        self.link_chapter.addItems([title for title, start, _, _ in self.sections if start >= 0])
        self.link_chapter.setCurrentIndex(-1)
        self.hide_bars()
        self.link_frame.show()
        self.link_entry.setFocus()
        self.link_entry.selectAll()

    def apply_link(self, href=None):
        """Pone (o quita, con "") el enlace en lo elegido al abrir la barra. Solo direcciones web."""
        href = self.link_entry.text().strip() if href is None else href
        if href and not href.startswith("lnc:cap:") and not re.fullmatch(r"https?://[^\s\"<>]+\.[^\s\"<>]+", href):
            self.link_info.setText("La dirección debe empezar por http:// o https://")
            return
        self.sheet.setTextCursor(self.link_selection)
        self.sheet.set_link(href)
        self.link_frame.hide()
        self.back_to_page()

    def back_to_page(self):
        """Tras una barra que cambió la hoja de Qt: con la hoja web, se vuelve a cargar con el resultado."""
        if self.web_on:
            self.sheet_to_web()
        else:
            self.sheet.setFocus()

    def show_find_bar(self, replace):
        self.hide_bars()
        self.find_frame.show()
        cursor = self.sheet.textCursor()
        chosen = self.web.page().selectedText() if self.web_on else cursor.selectedText()
        if chosen and "\u2029" not in chosen and "\n" not in chosen:
            self.find_entry.setText(chosen)
        (self.replace_entry if replace and self.find_entry.text() else self.find_entry).setFocus()
        self.find_entry.selectAll()

    def find(self, delta):
        """Siguiente (o anterior) aparición en la hoja; al acabarse, sigue en los capítulos siguientes."""
        term = self.find_entry.text()
        if not term or self.regex_error():
            return False
        if self.web_on:
            self.web_find(delta)
            return True
        if self.sheet.find_next(term, delta < 0, *self.find_options()):
            self.find_info.setText("")
            return True
        order = [s[1:3] for s in self.sections]
        here = order.index(self.current) if self.current in order else 0
        for step in range(1, len(order) + 1):
            start, end = order[(here + step * delta) % len(order)]
            text = self.pages[(start, end)]["doc"].toPlainText() if (start, end) in self.pages else \
                convert.section_text(self.app.book, start, end)
            if self.find_pattern().search(text):
                self.open_section(start, end)
                self.sheet.moveCursor(QTextCursor.MoveOperation.Start if delta > 0 else QTextCursor.MoveOperation.End)
                if self.sheet.find_next(term, delta < 0, *self.find_options()):
                    self.find_info.setText("")
                    return True
        self.find_info.setText("Sin resultados")
        QApplication.beep()
        return False

    def web_find(self, delta, fresh=False, count=None):
        """Buscar en la hoja web: la expresión de Python sobre el texto de cada párrafo (lo mismo que en la hoja) y
        el resultado elegido en Chromium. Sin más en el capítulo, sigue en los siguientes. fresh: desde el principio
        (o el final) del capítulo recién abierto; count: el resultado número count del capítulo («Todos»)."""
        pattern = self.find_pattern()

        def got(data):
            info = json.loads(data or "{}")
            texts, (i, s, e) = info.get("texts", []), info.get("at") or (-1, 0, 0)
            if fresh or count is not None:
                i, s, e = (-1, 0, 0) if delta > 0 else (len(texts), 0, 0)
            hits = [(n, m.start(), m.end()) for n, t in enumerate(texts) for m in pattern.finditer(t) if m.end() > m.start()]
            if count is not None:
                hit = hits[count] if count < len(hits) else None
            elif delta > 0:
                hit = next((h for h in hits if (h[0], h[1]) >= (i, e)), None)
            else:
                hit = next((h for h in reversed(hits) if (h[0], h[2]) <= (i, s)), None)
            if hit:
                self.web.call("select", *hit)
                self.find_info.setText("")
                return
            if not fresh and count is None and self.web_other_chapter(delta, pattern):
                return
            self.find_info.setText("Sin resultados")
            QApplication.beep()
        self.web.call("texts", done=got)

    def web_other_chapter(self, delta, pattern):
        """Abre en la hoja web el siguiente capítulo (o anterior) con resultados y elige el primero (o el último)."""
        order = [s[1:3] for s in self.sections]
        here = order.index(self.current) if self.current in order else 0
        for step in range(1, len(order) + 1):
            start, end = order[(here + step * delta) % len(order)]
            if (start, end) == self.current:  # vuelta entera: desde el principio del de ahora
                self.web_find(delta, fresh=True)
                return True
            text = self.pages[(start, end)]["doc"].toPlainText() if (start, end) in self.pages else \
                convert.section_text(self.app.book, start, end)
            if pattern.search(text):
                self.web_then = lambda: self.web_find(delta, fresh=True)
                self.open_section(start, end)
                return True
        return False

    def replace_one(self):
        """Cambia la aparición elegida y busca la siguiente, como Word."""
        if self.regex_error():
            return
        if self.web_on:
            self.web_replace()
            return
        if not self.sheet.replace_current(self.find_entry.text(), self.replace_entry.text(), *self.find_options()):
            self.find(1)
            return
        self.find(1)

    def web_replace(self):
        """Reemplazar en la hoja web: si lo elegido es un resultado, lo cambia (con \\1 de la expresión), y busca el
        siguiente."""
        pattern, new = self.find_pattern(), self.replace_entry.text()

        def got(data):
            info = json.loads(data or "{}")
            texts, (i, s, e) = info.get("texts", []), info.get("at") or (-1, 0, 0)
            m = pattern.match(texts[i], s) if 0 <= i < len(texts) and e > s else None
            if m and m.end() == e:
                self.web.call("replaceSel", m.expand(new) if self.regex_box.isChecked() else new)
            self.web_find(1)
        self.web.call("texts", done=got)

    def replace_everywhere(self):
        """Cambia todas las apariciones en todos los capítulos; cada capítulo, un paso de su deshacer."""
        term, new = self.find_entry.text(), self.replace_entry.text()
        if not term or self.regex_error():
            return
        if self.web_on:  # en las hojas de Qt de cada capítulo, con lo escrito en la web, y luego otra vez la web
            self.flush_web(lambda: (self.web_done(), self.replace_everywhere(), self.load_web()))
            return
        here, total = self.current, 0
        for _, start, end, _ in self.sections:
            loaded = (start, end) in self.pages
            text = self.pages[(start, end)]["doc"].toPlainText() if loaded else \
                convert.section_text(self.app.book, start, end)
            if self.find_pattern().search(text):
                self.open_section(start, end)
                total += self.sheet.replace_all(term, new, *self.find_options())
        self.open_section(*here)
        self.find_info.setText(f"{convert.counted(total, 'reemplazo')}")
        self.mark_changed()

    # Barras de dividir capítulo y de aviso
    def split_bar(self):
        """Barra para partir el capítulo en el cursor: pide el título del capítulo nuevo."""
        bar = self.split_frame = QFrame(objectName="ribbon")
        self.split_entry = QLineEdit()
        self.split_entry.setPlaceholderText("Título del capítulo nuevo")
        self.split_entry.returnPressed.connect(self.apply_split)
        self.split_info = _label("", "muted")
        bar.setLayout(_row(_label("Capítulo nuevo desde el cursor:"), self.split_entry,
                           _button("Dividir", self.apply_split, "primary"), self.split_info, 1,
                           _button("✕", lambda: (bar.hide(), self.focus_page())), margins=(10, 6, 10, 6)))
        self.escape_closes(bar, self.split_entry)
        bar.hide()
        return bar

    def show_split_bar(self, synced=False):
        if self.web_on and not synced:
            self.web_to_sheet(lambda: self.show_split_bar(True))
            return
        self.split_cursor = QTextCursor(self.sheet.textCursor())  # el foco se va a la barra
        self.split_info.setText("")
        self.split_entry.clear()
        self.hide_bars()
        self.split_frame.show()
        self.split_entry.setFocus()

    def apply_split(self):
        self.sheet.setTextCursor(self.split_cursor)
        if not self.split_entry.text().strip():
            self.split_info.setText("Escribe el título del capítulo nuevo")
            return
        if not self.sheet.split_chapter(self.split_entry.text()):
            self.split_info.setText("Pon el cursor en un párrafo de texto, no en un título ni una ilustración")
            return
        self.split_frame.hide()
        self.tell("Capítulo nuevo: al aceptar sale en la lista · Ctrl+Z para deshacer")
        self.back_to_page()

    def issue_bar(self):
        """Barra del aviso elegido (F8): qué pasa, qué hacer y, si se puede, arreglarlo en la hoja."""
        bar = self.issue_frame = QFrame(objectName="ribbon")
        self.issue_title = _label("", "title")
        self.issue_text = QLabel()
        self.issue_text.setWordWrap(True)
        self.issue_fix = _button("Arreglar", self.fix_issue, "primary")
        column = QVBoxLayout(bar)
        column.setContentsMargins(10, 6, 10, 6)
        column.addLayout(_row(self.issue_title, 1, self.issue_fix, _button("Siguiente (F8)", self.next_issue),
                              _button("✕", lambda: (bar.hide(), self.focus_page()))))
        column.addWidget(self.issue_text)
        bar.hide()
        return bar

    # Marcas de la hoja: cambios y avisos
    def loaded(self):
        """Bloques del libro cargados en la hoja de ahora, en orden."""
        return [k for k, _ in self.pages[self.current]["lines"]] if self.current in self.pages else []

    def block_issues(self, block):
        """Avisos del párrafo si sigue como al abrir el editor (si se cambió, ya no valen)."""
        k = block.blockFormat().property(editor.SOURCE)
        if k is None or k not in self.issues or editor.objects_of(block):
            return []
        original = editor.original_of(block)
        page = self.pages.get(self.current, {})
        # la línea de un paso de deshacer de otra vez (replay_history) no es la del libro: sus avisos no valen
        loaded = page.setdefault("loaded", {line for _, line in page.get("lines", ())}) if page else ()
        return list(self.issues[k]) if original in loaded and editor.block_lines(block) == [original] else []

    def block_issue(self, block):
        issues = self.block_issues(block)
        return issues[0] if issues else None

    @staticmethod
    def issue_range(block, issue):
        """(inicio, fin) del problema dentro del párrafo de la hoja, en posiciones de Qt (un emoji ocupa dos); sin
        posición, todo el párrafo."""
        text = block.text()
        size = len(text)
        start, end = issue.span or (0, size)
        start, end = min(start, size), min(end, size)
        start, end = (start, end) if end > start else (0, size)
        return qtk.u16(text, start), qtk.u16(text, end)

    def refresh_marks(self):
        """Marcas de la hoja, como Word: ondulado bajo el trozo exacto de cada aviso, del color de su gravedad, y
        barrita en el margen de los párrafos cambiados. Solo se pintan: no se guardan ni entran en deshacer."""
        if not shiboken6.isValid(self.sheet) or self.current not in self.pages:
            return
        colors = theme.mark_colors(self.app.dark)
        selections, changed, issues = [], self.sheet.changed_blocks(), 0
        nav = {"Títulos": [], "Avisos": [], "Comentarios": [], "Cambios": [(b, b.text()) for b in changed]}
        block = self.sheet.document().firstBlock()
        while block.isValid():
            if block.blockFormat().property(editor.HEADING):
                nav["Títulos"].append((block, block.text()))
            for issue in self.block_issues(block):
                if issue.kind == "comment":
                    nav["Comentarios"].append((block, issue.problem))
                else:
                    nav["Avisos"].append((block, f"{issue.title}: {block.text()}"))
                start, end = self.issue_range(block, issue)
                selection = QTextEdit.ExtraSelection()
                if issue.kind == "comment":  # como una nota adhesiva: fondo amarillo, no ondulado de error
                    selection.format.setBackground(QColor(colors["comment"]))  # la nota, en el clic derecho
                else:
                    selection.format.setUnderlineStyle(QTextCharFormat.UnderlineStyle.WaveUnderline)
                    selection.format.setUnderlineColor(QColor(colors[issue.level]))
                cursor = QTextCursor(block)
                cursor.setPosition(block.position() + start)
                cursor.setPosition(block.position() + end, QTextCursor.MoveMode.KeepAnchor)
                selection.cursor = cursor
                selections.append(selection)
                issues += 1
            if not isinstance(self.reading, int) and block == self.reading:  # el párrafo que se lee en voz alta, de fondo: sin elegirlo, que teclear no lo borre
                selection = QTextEdit.ExtraSelection()
                selection.format.setBackground(QColor(colors["reading"]))
                selection.cursor = QTextCursor(block)
                selection.cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
                selections.insert(0, selection)
            block = block.next()
        self.sheet.setExtraSelections(selections)
        self.sheet.set_bars(changed, colors["changed"])
        self.counts = len(changed), issues
        self.show_counts()
        self.nav_entries = nav
        self.refill_nav()

    def refill_nav(self):
        """El panel de navegación, si está a la vista: con la hoja web, de lo que hay en ella (web_nav)."""
        if self.nav.isHidden():
            return
        if self.web_on:
            self.web_nav()
        else:
            self.fill_nav()

    def web_nav(self):
        """El panel de navegación de la hoja web, con lo escrito en ella: títulos, avisos, comentarios y cambios;
        cada entrada lleva ("web", número del párrafo)."""
        def got(data):
            if not self.web_on or not data:
                return
            d = json.loads(data)
            entries = {"Títulos": [], "Avisos": [], "Comentarios": [], "Cambios": []}
            entries["Títulos"] = [(("web", i), text) for i, text in d.get("titles", [])]
            entries["Cambios"] = [(("web", i), text) for i, text in d.get("changed", [])]
            for i, text, ident in d.get("issues", []):
                issue = self.web_issues.get(ident)
                if issue is not None:
                    entries["Comentarios" if issue.kind == "comment" else "Avisos"].append((("web", i), text))
            self.nav_entries = entries
            self.fill_nav()
        self.web.call("outline", done=got)

    # Panel de navegación, como el de Word: lo que hay en el capítulo, a un clic
    def nav_panel(self):
        self.nav = QTreeWidget()
        self.nav.setHeaderHidden(True)
        self.nav.setMinimumWidth(200)
        self.nav.itemClicked.connect(self.nav_go)
        self.nav_entries = {}
        self.nav.setVisible(bool(self.app.settings.get("editor_nav")))
        self.nav_btn.setChecked(not self.nav.isHidden())
        return self.nav

    def toggle_nav(self):
        """F9: muestra u oculta el panel; se recuerda para la próxima vez."""
        self.nav.setVisible(self.nav.isHidden())
        self.nav_btn.setChecked(not self.nav.isHidden())
        self.app.settings["editor_nav"] = not self.nav.isHidden()
        self.app.save_settings()
        self.refill_nav()

    def fill_nav(self):
        """Cuatro grupos con su cuenta; cada entrada, el párrafo recortado. Los vacíos salen sin desplegar."""
        self.nav.clear()
        for name, entries in self.nav_entries.items():
            group = QTreeWidgetItem(self.nav, [f"{name} ({len(entries)})"])
            for block, text in entries:
                text = " ".join(text.replace("￼", "").split())
                item = QTreeWidgetItem(group, [text[:60] + ("…" if len(text) > 60 else "")])
                item.setToolTip(0, text)
                item.setData(0, Qt.ItemDataRole.UserRole, block if isinstance(block, tuple) else block.blockNumber())
            group.setExpanded(bool(entries))

    def nav_go(self, item):
        number = item.data(0, Qt.ItemDataRole.UserRole)
        if number is None:
            item.setExpanded(not item.isExpanded())
            return
        if isinstance(number, tuple):  # ("web", párrafo) de web_nav
            if self.web_on:
                self.web.call("select", number[1], 0)
                self.web.setFocus()
            return
        block = self.sheet.document().findBlockByNumber(number)
        if block.isValid():
            self.goto_block(block)
            (self.web if self.web_on else self.sheet).setFocus()

    # Explorador de archivos, como el de Calibre «Modificar libro»: los archivos del EPUB por grupos
    def files_panel(self):
        self.files = QTreeWidget()
        self.files.setHeaderHidden(True)
        self.files.setColumnCount(2)  # nombre y peso
        self.files.header().setStretchLastSection(False)
        self.files.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.files.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.files.setMinimumWidth(240)
        self.files.itemClicked.connect(lambda item: self.files_go(item, False))
        self.files.itemActivated.connect(lambda item: self.files_go(item))  # doble clic o Intro (sin la columna)
        self.files_path = os.path.join(tempfile.gettempdir(), f"lnepub-vista-archivos-{os.getpid()}.epub")
        self.file_sections, self.file_view, self.files_written = {}, None, False
        self.separator_images = {}  # separator_group
        self.files.setVisible(bool(self.app.settings.get("editor_files")))
        self.files_btn.setChecked(not self.files.isHidden())
        if not self.files.isHidden():
            self.fill_files()
        return self.files

    def toggle_files(self):
        """Muestra u oculta el explorador; se recuerda para la próxima vez."""
        self.files.setVisible(self.files.isHidden())
        self.files_btn.setChecked(not self.files.isHidden())
        self.app.settings["editor_files"] = not self.files.isHidden()
        self.app.save_settings()
        if not self.files.isHidden():
            self.fill_files()

    def fill_files(self):
        """Los archivos del EPUB original o, con un PDF, los del que se generará con las opciones de ahora (escrito
        aparte, sin reducir imágenes: los nombres son los mismos). Ese se escribe una vez por editor: tarda un segundo
        en un libro grande, con el cursor de espera."""
        book = self.app.book
        self.files.clear()
        try:
            if book.origin:
                path = book.origin
                self.file_sections = {}
                for k, b in enumerate(book.blocks):
                    if b.src:
                        self.file_sections.setdefault(b.src[0], k)
            else:
                path = self.files_path
                if not self.files_written:
                    chapters, removed, breaks = _plan(book, self.app.rows())
                    options = self.app.write_options(breaks)
                    options.update(shrink=False, trim=False, gray=False, kepub=False, preview=True)
                    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
                    try:
                        firsts = convert.write_epub(book, chapters, path, removed=removed, **options)
                    finally:
                        QApplication.restoreOverrideCursor()
                    self.file_sections = {f"OEBPS/{name}": k for name, k in firsts.items()}
                    self.files_written = True
            with zipfile.ZipFile(path) as z:
                groups = faithful.file_groups(z)
                sizes = {info.filename: info.file_size for info in z.infolist()}
                groups = self.separator_group(z, groups, sizes)
        except Exception:
            _log(traceback.format_exc())
            QTreeWidgetItem(self.files, ["No se pudo leer el EPUB"])
            return
        bold = QFont(self.files.font())
        bold.setBold(True)
        for name, paths in groups:
            group = QTreeWidgetItem(self.files, [name])
            group.setFont(0, bold)
            for path in paths:
                item = QTreeWidgetItem(group, [path.rsplit("/", 1)[-1], _file_size(sizes.get(path, 0))])
                item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                item.setForeground(1, QColor(self.colors["muted"]))  # gris: el nombre es lo primero
                item.setToolTip(0, "Adorno del original, dibujado dentro del texto (Separador: «Icono del original»)"
                                if path in self.separator_images else path)
                item.setData(0, Qt.ItemDataRole.UserRole, path)
            group.setExpanded(True)

    def separator_group(self, z, groups, sizes):
        """groups con «Separadores» tras «Imágenes»: las imágenes del EPUB que son separadores de escena y, si el
        separador va dibujado dentro del texto (Separador: «Icono del original»), su adorno del original, con una
        nombre «separador-N.png» que no es del zip (separator_images: show_file lo muestra desde el libro)."""
        adornos = {}  # clave de la imagen -> datos, en orden de aparición
        self.separator_images = {}  # nombre -> datos de los adornos que no son archivo del EPUB
        for b in self.app.book.blocks:
            if b.kind == "break" and b.image:
                adornos.setdefault(convert.image_key(b.image), b.image)
        if not adornos:
            return groups
        found, out = [], []
        for name, paths in groups:
            if name == "Imágenes":
                found = [p for p in paths if convert.image_key(z.read(p)) in adornos]
                paths = [p for p in paths if p not in found]
            out.append((name, paths))
        shown = {convert.image_key(z.read(p)) for p in found}
        for data in (data for key, data in adornos.items() if key not in shown):
            path = f"separador-{len(self.separator_images) + 1}.{'png' if data[:4] == b'\x89PNG' else 'jpg'}"
            self.separator_images[path] = data
            found.append(path)
            sizes[path] = len(data)
        at = next((n + 1 for n, (name, _) in enumerate(out) if name == "Imágenes"), len(out))
        out.insert(at, ("Separadores", found))
        return [(name, paths) for name, paths in out if paths]

    def image_actions(self, win, column, path, k, data):
        """Bajo una ilustración del explorador: su descripción y «Describir…», «Cambiar…» y «Exportar…», como en
        Herramientas → «Ilustraciones…»."""
        described = _label("")
        described.setWordWrap(True)
        described.setTextFormat(Qt.TextFormat.PlainText)

        def show_alt():
            alt = self.app.alts.get(convert.image_key(self.app.book.blocks[k].image), "")
            described.setText(f"Descripción: {alt}" if alt else "Sin descripción: el lector de pantalla dirá "
                                                               "«Ilustración de «capítulo»»")

        def describe():
            if self.app.describe(k, win) is not None:
                show_alt()

        def change():
            if self.change_picture(k, win):
                self.files_written = False  # el EPUB del explorador lleva aún la de antes
                self.fill_files()
                win.close()
                self.show_file(path)

        def export():
            name = path.rsplit("/", 1)[-1]
            chosen = filedialog.asksaveasfilename(initialfile=name, filetypes=IMAGE_TYPES, parent=win)
            target = chosen if Path(chosen).suffix else chosen + Path(name).suffix
            if chosen and _confirm_name(chosen, target, win):
                Path(target).write_bytes(data)
                described.setText(f"Exportada en {target}")

        show_alt()
        column.addWidget(described)
        column.addLayout(_row(_button("Describir…", describe), _button("Cambiar…", change),
                              _button("Exportar…", export), 1))
        win.parts = {"described": described, "describe": describe, "change": change, "export": export}

    def files_go(self, item, opening=True):
        """Un clic en el archivo de un capítulo lo abre en la hoja; doble clic (opening) en otro, lo muestra."""
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if path is None:
            if not opening:
                item.setExpanded(not item.isExpanded())
            return
        k = self.file_sections.get(path)
        section = next((s for s in self.sections if s[1] <= k < s[2]), None) if k is not None else None
        if section is not None:
            if section[1:3] != self.current:
                self.open_section(*section[1:3])
        elif opening:
            self.show_file(path)

    def image_block(self, path):
        """Bloque del libro de una imagen del EPUB: con un PDF por su nombre (img/b00012.jpg es el bloque 12), con
        un EPUB por el archivo del que se leyó (Block.ref). None si no es una ilustración del libro (portada,
        iconos)."""
        blocks = self.app.book.blocks
        if self.app.book.origin:
            return next((k for k, b in enumerate(blocks) if b.kind == "image" and b.ref == path), None)
        m = re.search(r"/img/b(\d{5})(?:_\d+)?\.\w+$", path)
        k = int(m[1]) if m else None
        return k if k is not None and k < len(blocks) and blocks[k].kind == "image" else None

    def show_file(self, path):
        """Un archivo del EPUB de solo lectura: el código con colores, la imagen con sus medidas o una muestra de la
        letra. Una ilustración del libro, además, se cambia, se describe o se exporta desde aquí. Una sola ventana:
        el mismo archivo trae al frente la abierta, otro la sustituye (varios dobles clics abrían varias)."""
        old = self.file_view
        if old is not None and shiboken6.isValid(old) and old.isVisible():
            if old.property("path") == path:
                old.raise_()
                old.activateWindow()
                return
            old.close()
        k = self.image_block(path)
        try:
            if path in self.separator_images:
                data = self.separator_images[path]
            else:
                with zipfile.ZipFile(self.app.book.origin or self.files_path) as z:
                    data = z.read(path)
            if k is not None and self.app.book.blocks[k].image:
                data = self.app.book.blocks[k].image  # la de ahora: cambiada, el EPUB de entrada aún tiene la vieja
        except Exception:
            _log(traceback.format_exc())
            messagebox.showwarning("Explorador de archivos", f"No se pudo leer {path}.", parent=self.win)
            return
        win = Dialog(self.win)
        win.setWindowTitle(path.rsplit("/", 1)[-1])
        win.setProperty("path", path)
        win.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        column = QVBoxLayout(win)
        size = _file_size(len(data))
        low = path.lower()
        picture = QPixmap()
        if low.endswith(faithful.FILE_GROUPS[2][1]) and not low.endswith(".svg") and picture.loadFromData(data):
            label = QLabel()
            label.setPixmap(picture.scaled(800, 480, Qt.AspectRatioMode.KeepAspectRatio,  # con sus botones cabe en 768
                                           Qt.TransformationMode.SmoothTransformation)
                            if picture.width() > 800 or picture.height() > 480 else picture)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            column.addWidget(label, 1)
            column.addWidget(_label(f"{picture.width()} × {picture.height()} px · {size}", "muted"))
            if k is not None:
                self.image_actions(win, column, path, k, data)
        elif low.endswith(faithful.FILE_GROUPS[3][1]):
            font_id = QFontDatabase.addApplicationFontFromData(data)
            families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
            if font_id >= 0:  # solo mientras se ve: no se queda en las letras de la app
                win.destroyed.connect(lambda: QFontDatabase.removeApplicationFont(font_id))
            sample = QLabel("El veloz murciélago hindú comía feliz cardillo y kiwi.\n"
                            "¿ABCDEFGHIJKLMNÑOPQRSTUVWXYZ? ¡0123456789! «áéíóú»")
            if families:
                sample.setFont(QFont(families[0], 20))
            column.addWidget(sample, 1)
            column.addWidget(_label(f"{families[0] if families else 'Letra que no se puede mostrar'} · {size}",
                                    "muted"))
        else:
            code = editor.CodeEdit(theme.code_colors(self.app.dark))
            code.setPlainText(data.decode("utf-8", "replace"))
            code.setReadOnly(True)
            column.addWidget(code, 1)
            column.addWidget(_label(f"{path} · {size} · solo lectura", "muted"))
            win.resize(900, 650)
        column.addLayout(_row(1, _button("Cerrar", win.close, "primary")))
        win.show()
        self.file_view = win

    def show_counts(self):
        """Recuentos de Cambios y Avisos en la cinta y en la primera línea de su menú."""
        changed, issues = getattr(self, "counts", (0, 0))
        self.changes_menu.actions()[0].setText(convert.counted(changed, "párrafo cambiado", "párrafos cambiados"))
        self.issues_menu.actions()[0].setText(convert.counted(issues, "aviso", "avisos") + " en este capítulo")
        self.changes_btn.setText(f"Cambios ({changed}) ▾" if changed else "Cambios ▾")
        self.issues_btn.setText(f"Avisos ({issues}) ▾" if issues else "Avisos ▾")

    def goto_block(self, block):
        cursor = self.sheet.textCursor()
        cursor.setPosition(block.position())
        self.sheet.setTextCursor(cursor)
        self.sheet.ensureCursorVisible()
        if self.web_on:
            self.web.call("goHome", self.web_index(block))

    def next_change(self):
        """F7: el siguiente párrafo cambiado del capítulo (vuelve al principio al acabar)."""
        if self.web_on:
            self.web.call("next", "change", done=lambda found: found or self.tell("Sin más cambios en este capítulo"))
            return
        changed = self.sheet.changed_blocks()
        if not changed:
            self.tell("Sin cambios en este capítulo")
            return
        here = self.sheet.textCursor().block().blockNumber()
        self.goto_block(next((b for b in changed if b.blockNumber() > here), changed[0]))

    def restore_current(self):
        block = self.sheet.textCursor().block()
        if not self.sheet.can_restore(block, self.loaded()):
            self.tell("Este párrafo no tiene cambios, o se partió o unió: usa Deshacer (Ctrl+Z)")
            QApplication.beep()
            return
        self.sheet.restore(block)
        self.tell("Párrafo devuelto a su texto original · Ctrl+Z para deshacer")

    def next_issue(self):
        """F8: el siguiente aviso desde el cursor, también en los capítulos siguientes, con su barra. En la hoja web,
        el siguiente pintado; al acabar el capítulo, sigue en la hoja con el de los siguientes."""
        if self.web_on:
            self.web.call("next", "issue", done=lambda found: found or self.flush_web(
                lambda: (self.web_done(), self.next_issue())))
            return
        here = self.sheet.textCursor().block()
        block = here.next()
        while block.isValid():
            if self.block_issue(block):
                return self.show_issue(block)
            block = block.next()
        order = [s[1:3] for s in self.sections]
        row = order.index(self.current) if self.current in order else 0
        for step in range(1, len(order) + 1):
            start, end = order[(row + step) % len(order)]
            if not any(start <= k < end for k in self.issues):
                continue
            self.open_section(start, end)
            block = self.sheet.document().firstBlock()
            while block.isValid():
                if self.block_issue(block):
                    return self.show_issue(block)
                block = block.next()
        self.issue_frame.hide()
        self.tell("No quedan avisos sin arreglar")

    def goto_issue(self, issue):
        """Va al aviso del informe de calidad: su capítulo, su párrafo y su trozo. Si el párrafo ya se cambió en el
        editor, el aviso ya no vale: solo se va al párrafo."""
        k = issue.block
        bounds = next((s[1:3] for s in self.sections if s[1] <= k < s[2]), None)
        if bounds and bounds != self.current:
            self.open_section(*bounds)
        block = self.sheet.document().firstBlock()
        while block.isValid() and editor.source_of(block) != k:
            block = block.next()
        if not block.isValid():
            return False
        same = next((i for i in self.block_issues(block) if (i.kind, i.span, i.text) == (issue.kind, issue.span, issue.text)),
                    None)
        if same:
            self.show_issue(block, same)
        else:
            self.goto_block(block)
            self.tell("Este párrafo cambió desde el informe: revísalo a mano")
        return True

    def show_issue(self, block, issue=None):
        """El aviso del párrafo (o issue) con su barra y el trozo exacto elegido, como un resultado de Buscar."""
        issue = self.shown_issue = issue or self.block_issue(block)
        start, end = self.issue_range(block, issue)
        cursor = QTextCursor(block)
        cursor.setPosition(block.position() + end)
        cursor.setPosition(block.position() + start, QTextCursor.MoveMode.KeepAnchor)  # el cursor, al principio
        self.sheet.setTextCursor(cursor)
        self.sheet.ensureCursorVisible()
        self.hide_bars()
        self.issue_title.setText(f"● {issue.level} · {issue.title}")
        # los de texto, no los del subrayado: el naranja de «Revisar» se leía a 3:1
        self.issue_title.setStyleSheet(f"color: {theme.level_colors(self.app.dark)[issue.level]};")
        self.issue_text.setText(f"{issue.problem}\nSolución: {issue.solution}")
        self.issue_fix.setEnabled(issue.edit is not None)
        self.issue_frame.show()
        self.sheet.setFocus()

    def fix_issue(self, issue=None, go_next=True):
        """«Arreglar»: el cambio del aviso, en la hoja (un paso de deshacer); desde la barra, luego el siguiente."""
        issue = issue or self.shown_issue
        if not issue or not issue.edit or not self.sheet.replace_sources(*issue.edit):
            QApplication.beep()
            return
        self.refresh_marks()
        self.tell("Aviso arreglado · Ctrl+Z para deshacer")
        if go_next:
            self.next_issue()

    @staticmethod
    def fix_preview(issue):
        """El texto con el arreglo, alrededor de lo que cambia: «…estoy seguro de que se…»."""
        after = re.sub(r"\s+", " ", convert.TAGS.sub("", issue.edit[2]))
        start = issue.span[0] if issue.span else 0
        left = max(0, after.rfind(" ", 0, max(start - 12, 0)) + 1)
        right = after.find(" ", min(start + 36, len(after)))
        right = len(after) if right < 0 else right
        return ("…" if left else "") + after[left:right].strip() + ("…" if right < len(after) else "")

    def ignore_issue(self, issue):
        """«Ignorar este aviso»: como en el informe de calidad, se recuerda en la revisión. Un comentario se quita."""
        key = _issue_key(issue)
        if issue.kind == "comment":
            self.app.remove_comment(issue)
        elif key not in self.app.ignored:
            self.app.ignored.append(key)
            self.app.update_quality()
        self.drop_issues(issue.block, lambda i: i is issue)
        if self.shown_issue is issue:
            self.issue_frame.hide()
        self.refresh_marks()
        self.tell("Comentario quitado" if issue.kind == "comment" else "Aviso ignorado: no vuelve a salir en este libro")

    def drop_issues(self, k, which):
        """Quita de los avisos del párrafo k los que cumplen which."""
        left = [i for i in self.issues.get(k, []) if not which(i)]
        if left:
            self.issues[k] = left
        else:
            self.issues.pop(k, None)

    def edit_comment(self, block):
        """Clic derecho → «Comentario…»: pone, cambia o (vacío) quita el comentario del párrafo."""
        k = editor.source_of(block)
        old = next((i for i in self.issues.get(k, []) if i.kind == "comment"), None)
        note = _ask_note(self.win, old.problem if old else "")
        if note is None or k is None:
            return
        self.app.set_comment(k, note)
        self.drop_issues(k, lambda i: i.kind == "comment")
        for issue in self.app.comment_issues():
            if issue.block == k:
                self.issues.setdefault(k, []).append(issue)
        self.refresh_marks()
        self.tell("Comentario guardado" if note.strip() else "Comentario quitado")

    # Símbolos, revisado, cifras
    def fix_typography(self):
        n = self.sheet.fix_typography()
        self.tell(f"Tipografía corregida en {convert.counted(n, 'párrafo')} · Ctrl+Z para deshacer" if n
                          else "La tipografía ya estaba bien")

    def update_review(self):
        """Botón ✓ de la cinta: marcado si el capítulo de ahora está revisado, con la cuenta («✓ 3/25»)."""
        if not shiboken6.isValid(self.review_btn):
            return
        done, total = self.app.update_reviewed()
        item = str(self.current[0]) if self.current else ""
        self.review_btn.setText("✓" if self.ribbon_level >= 3 else f"✓ Revisado · {done}/{total}")
        self.review_btn.setToolTip(f"Capítulos revisados: {done} de {total}. Marca este como revisado y pasa al "
                                   "siguiente sin revisar (Ctrl+Intro)")
        self.review_btn.setChecked(bool(item and self.app.tree.exists(item) and self.app.tree.set(item, "ok")))

    def toggle_reviewed(self):
        """Ctrl+Intro: marca el capítulo como revisado (la ✓ de la lista, al momento) y abre el siguiente sin
        revisar; si ya lo estaba, lo desmarca."""
        tree, item = self.app.tree, str(self.current[0])
        if not tree.exists(item) or tree.set(item, "inc") != ON:
            self.tell("Solo los capítulos se marcan como revisados")
            QApplication.beep()
            self.update_review()
            return
        mark = "" if tree.set(item, "ok") else "✓"
        # marcar es dar el capítulo por terminado: un clic o un Ctrl+Intro sin querer no lo hace
        if mark and not messagebox.askyesno(
                "Marcar como revisado", f"¿Diste por terminado «{tree.set(item, 'title')}»?\n\nSe marcará como "
                "revisado (✓ en la lista) y se abrirá el siguiente capítulo sin revisar.", parent=self.win):
            self.update_review()
            return
        tree.set(item, "ok", mark)
        done, total = self.app.update_reviewed()
        row = next((n for n, s in enumerate(self.sections) if s[1:3] == self.current), 0)
        following = next((s for s in self.sections[row + 1:] if tree.exists(str(s[1]))
                          and tree.set(str(s[1]), "inc") == ON and not tree.set(str(s[1]), "ok")), None)
        if mark:  # desmarcar no resta: el trabajo de revisarlo ya se hizo
            _log_work(self.app.work, datetime.now().date().isoformat(), reviewed=1)
        if mark and following:
            self.open_section(*following[1:3])
        self.update_review()
        self.tell(f"Capítulos revisados: {done} de {total}" if mark else "Capítulo sin marcar como revisado")

    def show_stats(self):
        """Cifras del capítulo, de la selección y del libro: palabras, párrafos, minutos y diálogo."""
        def texts(doc):
            found, block = [], doc.firstBlock()
            while block.isValid():
                if editor._is_text(block):
                    found.append(block.text())
                block = block.next()
            return found

        book = []
        for _, start, end, _ in self.sections:
            if (start, end) in self.pages:
                book += texts(self.pages[(start, end)]["doc"])
            else:
                book += [convert._plain(b) for b in self.app.book.blocks[start:end] if b.kind == "p"]
        here, whole = convert.text_stats(texts(self.sheet.document())), convert.text_stats(book)
        selected = self.sheet.textCursor().selectedText().replace(" ", " ")
        number = lambda n: f"{n:,}".replace(",", ".")
        win = Dialog(self.win)
        win.setWindowTitle("Cifras")
        win.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        column = QVBoxLayout(win)
        grid = QGridLayout()
        rows = [("", "Este capítulo", "Libro"),
                ("Palabras", number(here["words"]), number(whole["words"])),
                ("Párrafos", number(here["paragraphs"]), number(whole["paragraphs"])),
                ("Minutos de lectura", number(here["minutes"]), number(whole["minutes"])),
                ("Diálogo", f"{here['dialog']} %", f"{whole['dialog']} %"),
                ("Narración", f"{100 - here['dialog']} %" if here["words"] else "0 %",
                 f"{100 - whole['dialog']} %" if whole["words"] else "0 %")]
        for r, row in enumerate(rows):
            for c, text in enumerate(row):
                label = _label(text, "muted" if r == 0 or c == 0 else None)
                if c:
                    label.setAlignment(Qt.AlignmentFlag.AlignRight)
                grid.addWidget(label, r, c)
        grid.setHorizontalSpacing(24)
        column.addLayout(grid)
        # Tu trabajo: lo guardado de días anteriores y esta sesión, aunque aún no se haya aceptado
        work = copy.deepcopy(self.app.work)
        _log_work(work, datetime.now().date().isoformat(), paragraphs=self.changed_count(),
                  minutes=(time.monotonic() - self.opened) / 60)
        mine = QGridLayout()
        for c, text in enumerate(("Tu trabajo", "Tiempo", "Párrafos cambiados", "Capítulos revisados")):
            mine.addWidget(_label(text, "muted"), 0, c)
        for r, (label, minutes, paragraphs, reviewed) in enumerate(_work_summary(
                work, datetime.now().date().isoformat()), 1):
            for c, text in enumerate((label, _duration(minutes), number(round(paragraphs)), number(round(reviewed)))):
                cell = _label(text, "muted" if c == 0 else None)
                if c:
                    cell.setAlignment(Qt.AlignmentFlag.AlignRight)
                mine.addWidget(cell, r, c)
        mine.setHorizontalSpacing(24)
        column.addSpacing(12)
        column.addLayout(mine)
        chapters = sum(1 for s in self.sections if s[0] != FRONT)
        extra = f"{convert.counted(chapters, 'capítulo')} en el libro"
        if selected.strip():
            extra += f" · selección: {convert.counted(convert.word_count(selected), 'palabra')}"
        column.addWidget(_label(extra, "muted"))
        column.addLayout(_row(1, _button("Cerrar", win.close, "primary")))
        win.show()
        self.stats_window = win

    def all_changes(self):
        """[(título, inicio, fin, [(antes, después, línea de la hoja)])] de cada capítulo cambiado: lo escrito sin
        aceptar y lo aceptado antes en esta sesión (editor_history), frente a como estaba al principio."""
        history, found = self.app.editor_history, []
        for title, start, end, _ in self.sections:
            page = self.pages.get((start, end))
            if start < 0 or (page is None and not history):
                continue
            loaded = [line for _, line in (page["lines"] if page else self.section_lines(start, end)[0])]
            now = [line for line in self.sheet.dumped(page["doc"]).split("\n\n") if line.strip()] if page else loaded
            here = "\n\n".join(loaded)
            entry = history_entry(history, here)
            old = [line for line in (entry[2] if entry else here).split("\n\n") if line.strip()]
            diffs = []
            for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, now, autojunk=False).get_opcodes():
                if op == "replace" and i2 - i1 == j2 - j1:  # párrafos seguidos cambiados: uno a uno
                    diffs += [(old[i1 + n], now[j1 + n], j1 + n) for n in range(i2 - i1)]
                elif op != "equal":
                    diffs.append(("\n".join(old[i1:i2]), "\n".join(now[j1:j2]), j1))
            if diffs:
                found.append((title, start, end, diffs))
        return found

    def show_all_changes(self, flushed=False):
        """Revisar → Cambios → «Ver todos los cambios»: cada párrafo cambiado del libro, antes y ahora lado a lado
        como GitHub, por capítulos; su número lleva la hoja a él. La ventana se queda abierta mientras se revisa."""
        if self.web_on and not flushed:
            self.flush_web(lambda: self.show_all_changes(True))
            return
        found = self.all_changes()
        if not found:
            self.tell("Sin cambios en el libro desde que se abrió")
            return
        colors = theme.mark_colors(self.app.dark)
        base = QApplication.palette().color(QPalette.ColorRole.Base)

        def solid(key):  # el rich text de Qt no mezcla la transparencia: el color ya mezclado con el fondo
            c = QColor(colors[key])
            k = c.alphaF()
            return QColor(*(round(getattr(c, n)() * k + getattr(base, n)() * (1 - k))
                            for n in ("red", "green", "blue"))).name()
        tint = {"old": solid("removed"), "new": solid("added")}

        def side(text, tag, line):
            return (f'<td width="4%" align="right" bgcolor="{line}">{tag}</td>'
                    f'<td width="46%" bgcolor="{line}">{text}</td>')
        parts, count = [], 0
        for title, start, end, diffs in found:
            parts.append(f"<h3>{html.escape(title)} <span style='font-weight:normal'>· "
                         f"{convert.counted(len(diffs), 'cambio')}</span></h3>"
                         '<table width="100%" cellspacing="0" cellpadding="4">')
            for before, after, line in diffs:
                count += 1
                pieces = _word_diff(editor.plain_line(before), editor.plain_line(after))
                old = "".join(f'<span style="background-color:{tint[tag]}">{html.escape(t)}</span>' if tag
                              else html.escape(t) for t, tag in pieces if tag != "new")
                new = "".join(f'<span style="background-color:{tint[tag]}">{html.escape(t)}</span>' if tag
                              else html.escape(t) for t, tag in pieces if tag != "old")
                go = f'<a href="{start}-{end}-{line}">{line + 1}</a>'  # el n.º del párrafo, como GitHub: lleva a él
                parts.append("<tr>" + (side(old, "−", solid("removed_line")) if before else side("", "", base.name()))
                             + (side(new, go, solid("added_line")) if after else side("", go, base.name())) + "</tr>"
                             '<tr><td colspan="4" style="font-size:2px">&nbsp;</td></tr>')
            parts.append("</table>")
        if getattr(self, "changes_window", None) is not None and shiboken6.isValid(self.changes_window):
            self.changes_window.close()
        win = Dialog(self.win)
        win.setWindowTitle("Todos los cambios")
        win.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        column = QVBoxLayout(win)
        column.addWidget(_label(f"{convert.counted(count, 'cambio')} en {convert.counted(len(found), 'capítulo')}: "
                                "antes a la izquierda y ahora a la derecha, como GitHub (lo quitado en rojo y lo "
                                "puesto en verde), también lo aceptado antes; el número lleva al párrafo", "muted"))
        view = QTextBrowser()
        view.setOpenLinks(False)
        view.setHtml("".join(parts))
        view.anchorClicked.connect(lambda url: self.goto_change(*map(int, url.toString().split("-"))))
        column.addWidget(view, 1)
        column.addLayout(_row(1, _button("Cerrar", win.close, "primary")))
        win.resize(900, 560)
        win.show()
        self.changes_window = win

    def goto_change(self, start, end, line):
        """«Ir» de «Todos los cambios»: el capítulo y el párrafo n.º line de su hoja."""
        if self.web_on:
            self.flush_web(lambda: (self.web_done(), self.goto_change(start, end, line)))
            return
        if (start, end) != self.current:
            self.open_section(start, end)
        self.sheet.setTextCursor(QTextCursor(self.web_block(line)))
        self.sheet.ensureCursorVisible()
        self.win.activateWindow()
        self.sheet.setFocus()

    # Buscar: opciones y todos los resultados
    def find_options(self):
        return self.whole_box.isChecked(), self.case_box.isChecked(), self.regex_box.isChecked()

    def find_pattern(self):
        """El término de la barra como expresión, con las opciones: para mirar capítulos sin abrirlos. Lanza
        re.error si es una expresión regular mal escrita."""
        whole, case, regex = self.find_options()
        term = self.find_entry.text() if regex else re.escape(self.find_entry.text())
        return re.compile(rf"(?<!\w)(?:{term})(?!\w)" if whole else term, 0 if case else re.I)

    def regex_error(self):
        """Con «.*», el error de la expresión escrita (y el campo en rojo), o "" si vale."""
        error = ""
        if self.regex_box.isChecked() and self.find_entry.text():
            try:
                self.find_pattern()
            except re.error as e:
                why = next((text for start, text in REGEX_ERRORS if e.msg.startswith(start)), "revísala")
                error = f"Expresión no válida: {why} (carácter {(e.pos or 0) + 1})"
        self.find_entry.setStyleSheet(f"color: {theme.level_colors(self.app.dark)['Grave']}" if error else "")
        self.find_info.setText(error)
        return error

    def all_matches(self):
        """Cada resultado del libro: [(fila de sections, número de aparición en esa sección, texto)]."""
        if not self.find_entry.text() or self.regex_error():
            return []
        pattern, found = self.find_pattern(), []
        for n, (title, start, end, _) in enumerate(self.sections):
            if (start, end) in self.pages:
                text = self.pages[(start, end)]["doc"].toPlainText()
            else:
                text = "\n".join(convert._plain(b) for b in self.app.book.blocks[start:end] if b.kind in ("p", "heading"))
            for count, m in enumerate(pattern.finditer(text)):
                around = text[max(0, m.start() - 30):m.end() + 30].replace("\n", " ").replace(" ", " ")
                found.append((n, count, f"{title} · …{around.strip()}…"))
                if len(found) >= 500:  # de sobra: más no se leen
                    return found
        return found

    def show_all_matches(self, flushed=False):
        """«Todos»: lista bajo la barra con cada resultado; clic va a él. Con la hoja web, lo escrito en ella cuenta."""
        if self.web_on and not flushed:
            self.flush_web(lambda: self.show_all_matches(True))
            return
        found = self.all_matches()
        if not found:
            self.find_info.setText("Sin resultados")
            QApplication.beep()
            return
        if getattr(self, "matches_popup", None) is not None and shiboken6.isValid(self.matches_popup):
            self.matches_popup.deleteLater()  # la lista anterior (hasta 500 filas) no se queda viva
        pop = QFrame(self.win, Qt.WindowType.Popup)
        pop.setObjectName("card")
        column = QVBoxLayout(pop)
        column.setContentsMargins(8, 8, 8, 8)
        column.addWidget(_label(convert.counted(len(found), "resultado"), "muted"))
        results = QListWidget()
        results.setTextElideMode(Qt.TextElideMode.ElideRight)
        results.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for n, count, text in found:
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, (n, count))
            item.setToolTip(text)
            results.addItem(item)
        results.itemClicked.connect(lambda item: (pop.hide(), self.goto_match(*item.data(Qt.ItemDataRole.UserRole))))
        results.itemActivated.connect(lambda item: (pop.hide(), self.goto_match(*item.data(Qt.ItemDataRole.UserRole))))
        column.addWidget(results, 1)
        pop.resize(620, 360)
        corner = self.find_frame.mapToGlobal(self.find_frame.rect().bottomLeft())
        area = self.find_frame.screen().availableGeometry()
        pop.move(max(area.left(), corner.x() + 8), min(corner.y() + 2, area.bottom() - pop.height() - 8))
        pop.show()
        results.setFocus()
        self.matches_popup = pop

    def goto_match(self, row, count):
        """El resultado número count de la sección row: se abre y se elige como al buscar."""
        if self.web_on:
            bounds = self.sections[row][1:3]
            if bounds == self.current:
                self.web_find(1, count=count)
            else:
                self.web_then = lambda: self.web_find(1, count=count)
                self.open_section(*bounds)
            return
        self.open_section(*self.sections[row][1:3])
        self.sheet.moveCursor(QTextCursor.MoveOperation.Start)
        for _ in range(count + 1):
            if not self.sheet.find_next(self.find_entry.text(), False, *self.find_options()):
                break
        self.sheet.ensureCursorVisible()

    # Ortografía en vivo
    def send_words(self, text="", words=None):
        """Manda al proceso de ortografía las palabras de text (o words, ya elegidas) que aún no revisó."""
        if words is None:
            skip = convert.spell_skip(self.app.words)
            words = {w for w in convert.spell_words(text) if convert.spell_candidate(w, skip)}
        new = words - self.checked
        if new and self.spell:
            self.checked |= new
            try:
                self.spell[0].put(("check", sorted(new)))
            except (OSError, ValueError):
                self.spell = None

    def check_new_words(self):
        if self.spell and shiboken6.isValid(self.sheet):
            self.send_words(self.sheet.document().toPlainText())

    def poll_spelling(self):
        """Respuestas del proceso de ortografía: palabras que no están (se subrayan y se piden sus
        sugerencias, primero las del capítulo de ahora) y sugerencias. Si el proceso muere, se deja estar."""
        if not self.spell or not shiboken6.isValid(self.sheet):
            return
        requests, replies, proc = self.spell
        new = set()
        while True:
            try:
                reply = replies.get_nowait()
            except queue.Empty:
                break
            except (OSError, ValueError, EOFError):
                self.spell = None
                return
            if reply[0] == "unknown":
                new |= set(reply[1]) - self.unknown
            elif reply[0] == "suggestions":
                self.suggestions[reply[1]] = reply[2]
                self.show_late_suggestions(reply[1])
        if new:
            self.unknown |= new
            self.request_suggestions()
            self.update_unknown(new)
        if proc.is_alive():
            qtk.after(300, self.poll_spelling)
        else:
            self.spell = None

    def request_suggestions(self, first=None):
        """Pide las sugerencias de las palabras subrayadas del capítulo de ahora (las de todo el libro serían
        cientos, ~1 s cada una); first, la del clic derecho, antes que ninguna."""
        if not self.spell or not shiboken6.isValid(self.sheet):
            return
        pending = self.unknown - self.ignored_words - set(self.suggestions)
        # En orden de lectura y pocas: con un libro sin traducir son miles, y una expresión regular por palabra
        # congelaba la ventana 10 s (Eminence 7). Las demás se piden con el clic derecho («first»)
        here = convert.spell_words(self.sheet.document().toPlainText())
        ahead = list(dict.fromkeys(w for w in here if w in pending))[:SUGGEST_AHEAD]
        try:
            if first:
                self.suggestions.setdefault(first, None)
                self.spell[0].put(("first", first))
            for word in ahead:
                self.suggestions[word] = None  # None: pedida, aún sin respuesta
                self.spell[0].put(("suggest", word))
        except (OSError, ValueError):
            self.spell = None

    def show_late_suggestions(self, word):
        """Si el menú del clic derecho espera las sugerencias de word, las pone en él sin cerrarlo."""
        menu, wait = getattr(self, "context", None), getattr(self, "context_wait", None)
        if (not wait or getattr(self, "context_word", None) != word or not shiboken6.isValid(menu)
                or not menu.isVisible()):
            return
        for suggestion in self.suggestions[word] or ():
            action = QAction(suggestion, menu)
            action.triggered.connect(lambda _=False, s=suggestion: self.web.call("replaceWord", s)
                                     if self.context_cursor is None else self.replace_word(self.context_cursor, s))
            font = action.font()
            font.setBold(True)
            action.setFont(font)
            menu.insertAction(wait, action)
        if self.suggestions[word]:
            menu.removeAction(wait)
        else:
            wait.setText("Sin sugerencias")
        self.context_wait = None

    def update_unknown(self, changed=None):
        """Subraya en todas las hojas visitadas las palabras que no están, menos las ignoradas; changed: las
        que cambiaron (solo se repintan sus párrafos)."""
        shown = self.unknown - self.ignored_words
        self.sheet.unknown = shown  # las hojas que se carguen después
        for page in self.pages.values():
            page["look"].set_unknown(shown, changed)
        if getattr(self, "web_on", False):
            self.web.call("setSpell", json.dumps(sorted(w.lower() for w in shown)))

    def ignore_word(self, word):
        self.ignored_words.add(word)
        self.update_unknown({word})

    def add_word(self, word):
        """Al diccionario de la serie (el de «Ortografía…»): no se vuelve a marcar en ningún tomo."""
        if word not in self.app.words:
            self.app.words.append(word)
        self.ignore_word(word)
        self.tell(f"«{word}» añadida al diccionario de la serie")

    def context_menu(self, pos):
        """Clic derecho en la hoja, en español: sugerencias de la falta o el aviso subrayado ahí, volver al
        original y editar. Solo con el derecho: con el izquierdo el menú no dejaba poner el cursor ni elegir
        texto."""
        sheet = self.sheet
        here = sheet.cursorForPosition(pos)
        current = sheet.textCursor()
        if not (current.hasSelection() and current.selectionStart() <= here.position() <= current.selectionEnd()):
            sheet.setTextCursor(here)  # como Word: el clic fuera de la selección pone ahí el cursor
        if getattr(self, "context", None) is not None and shiboken6.isValid(self.context):
            self.context.deleteLater()  # el del clic anterior, ya cerrado: si no, quedaba uno vivo por clic
        self.context = self.mark_menu(pos)
        self.context.popup(sheet.viewport().mapToGlobal(pos))

    def mark_menu(self, pos):
        """Menú de lo que hay en pos: sugerencias de la palabra subrayada (o ignorarla, añadirla al diccionario),
        cada aviso subrayado ahí con su explicación, arreglo e ignorar, volver al original y deshacer, cortar,
        copiar, pegar."""
        sheet, doc = self.sheet, self.sheet.document()
        here = sheet.cursorForPosition(pos)
        word_cursor = QTextCursor(here)
        word_cursor.select(QTextCursor.SelectionType.WordUnderCursor)
        word = word_cursor.selectedText()
        menu = QMenu(sheet)
        self.context_word, self.context_cursor, self.context_wait = word, word_cursor, None
        if word and word in self.unknown - self.ignored_words:
            suggestions = self.suggestions.get(word)
            if suggestions is None:
                self.request_suggestions(first=word)
            for suggestion in suggestions or ():
                action = menu.addAction(suggestion, lambda s=suggestion: self.replace_word(word_cursor, s))
                font = action.font()
                font.setBold(True)
                action.setFont(font)
            if not suggestions:
                wait = menu.addAction("Buscando sugerencias…" if suggestions is None else "Sin sugerencias")
                wait.setEnabled(False)
                self.context_wait = wait if suggestions is None else None
            menu.addAction("Ignorar", lambda: self.ignore_word(word))
            menu.addAction("Añadir al diccionario de la serie", lambda: self.add_word(word))
            menu.addSeparator()
        note = sheet.note_at(here.position())
        if note:  # una nota: editarla o quitarla sin tocar «[Nota: …]» a mano
            menu.addAction("Editar nota…", lambda: self.note_dialog(note))
            menu.addAction("Quitar nota", lambda: self.on_sheet(lambda: self.sheet.set_note("", note[:2])))
            menu.addSeparator()
        block = here.block()
        for name in editor.objects_of(block):
            if name.startswith("Ilustración "):
                k = int(name.split()[1])
                menu.addAction("Cambiar ilustración…", lambda k=k: self.change_picture(k)).setToolTip(
                    "Para moverla, córtala (Ctrl+X) y pégala donde vaya")
                menu.addSeparator()
        offset = here.position() - block.position()
        colors = theme.level_colors(self.app.dark)  # de texto: el naranja del subrayado se leía a 3:1
        for issue in self.block_issues(block):
            start, end = self.issue_range(block, issue)
            if not start <= offset <= end:  # como Word: lo subrayado donde se pulsa
                continue
            title = QLabel(f"● {issue.level} · {issue.title}")
            title.setStyleSheet(f"color: {colors[issue.level]}; font-weight: bold; padding: 6px 12px 2px 12px;")
            explain = QLabel(issue.problem)
            explain.setWordWrap(True)
            explain.setMaximumWidth(380)
            explain.setStyleSheet("padding: 0 12px 6px 12px;")
            for label in (title, explain):
                action = QWidgetAction(menu)
                action.setDefaultWidget(label)
                menu.addAction(action)
            if issue.edit:
                change = self.fix_preview(issue)
                menu.addAction(f"Arreglar: «{change}»" if issue.edit[2] else "Arreglar: quitar este párrafo",
                               lambda i=issue: self.fix_issue(i, go_next=False))
            menu.addAction("Quitar comentario" if issue.kind == "comment" else "Ignorar este aviso",
                           lambda i=issue: self.ignore_issue(i))
            menu.addAction("Aviso siguiente   (F8)", self.next_issue)
            menu.addSeparator()
        k = editor.source_of(block)  # solo párrafos de texto del libro: un subtítulo («Parte 1») no lleva comentario
        if k is not None and editor._is_text(block) and self.app.book.blocks[k].kind == "p":
            has = any(i.kind == "comment" for i in self.block_issues(block))
            menu.addAction("Cambiar comentario…" if has else "Comentario…", lambda: self.edit_comment(block))
        if not note and not editor.objects_of(block):  # nota nueva donde se hizo clic, escrita en un cuadro
            menu.addAction("Insertar nota aquí…", lambda: (sheet.textCursor().hasSelection() or sheet.setTextCursor(here),
                                                         self.note_dialog()))
        if sheet.can_restore(block, self.loaded()):
            menu.addAction("Volver al original", lambda: (sheet.setTextCursor(QTextCursor(block)), self.restore_current()))
        # Sinónimos de una palabra bien escrita, tras los avisos: como en Word, lo que hay que arreglar va primero
        if word and word.isalpha() and word not in self.unknown - self.ignored_words and self.app.spanish():
            found = editor.synonyms(word)
            if found:
                sub = menu.addMenu("Sinónimos")
                for synonym in found:
                    shown = editor.match_case(word, synonym)
                    sub.addAction(shown, lambda s=shown: self.replace_word(word_cursor, s))
        if menu.actions() and not menu.actions()[-1].isSeparator():
            menu.addSeparator()
        selection = sheet.textCursor().hasSelection()
        for item in (("Deshacer", sheet.undo, doc.isUndoAvailable()), ("Rehacer", sheet.redo, doc.isRedoAvailable()),
                     None, ("Cortar", sheet.cut, selection), ("Copiar", sheet.copy, selection),
                     ("Pegar", sheet.paste, sheet.canPaste()), None, ("Seleccionar todo", sheet.selectAll, True)):
            if item is None:
                menu.addSeparator()
            else:
                menu.addAction(item[0], item[1]).setEnabled(item[2])
        return menu

    def insert_image(self):
        """Imagen de un archivo tras el párrafo del cursor: al aceptar, una ilustración nueva del libro."""
        path = filedialog.askopenfilename(filetypes=IMAGE_TYPES, parent=self.win)
        if not path:
            return
        try:
            self.sheet.insert_picture(convert.load_cover(path))
        except ValueError as e:
            messagebox.showerror("Error", str(e), parent=self.win)

    def page_as_image(self):
        """La página del original que muestra el panel, como imagen tras el párrafo del cursor: créditos, saludos
        del grupo o carteles que como texto se pierden. El texto de esa página se queda: se borra a mano si sobra."""
        doc = self.app.pdf_doc
        if not doc or not 0 <= self.page < doc.page_count:
            messagebox.showinfo("Página como imagen", "No hay ninguna página del original a la vista.", parent=self.win)
            return
        page = doc[self.page]
        zoom = min(4.0, 1600 / max(page.rect.width, page.rect.height))  # lado mayor ~1600 px, como «Reducir imágenes»
        data = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).tobytes("jpg", jpg_quality=88)
        self.sheet.insert_picture(data)
        self.tell(f"Página {self.page + 1} del original insertada como imagen")

    def change_picture(self, k, parent=None):
        """Cambia la ilustración del bloque k por un archivo (App.replace_image) y la hoja la muestra ya. True si
        cambió."""
        if not self.app.replace_image(k, parent=parent or self.win):
            return False
        pictures = self.pictures(k, k + 1)
        if k in pictures:
            self.sheet.refresh_picture(k, pictures[k])
        self.drawn = None
        self.draw_source()
        return True

    def replace_word(self, cursor, new):
        cursor.beginEditBlock()
        cursor.insertText(new, cursor.charFormat())
        cursor.endEditBlock()

    def maximize(self):
        """A pantalla completa y la barra divisoria en el medio (con el tamaño ya maximizado)."""
        if shiboken6.isValid(self.win) and self.win.isVisible():
            self.win.showMaximized()
            qtk.after(0, self.center)

    def center(self):
        """Barra divisoria al medio y el párrafo del cursor a la vista."""
        if shiboken6.isValid(self.split):
            self.split.setSizes([240, 240, 10 ** 4, 10 ** 4])  # navegación y archivos (si se ven), hoja y PDF a medias
            self.sheet.ensureCursorVisible()

    def do(self, name, *args):
        """Una acción de la cinta en la hoja a la vista: la de Qt o la web (WEB_ACTIONS); lo que la web aún no hace
        se dice en la barra de abajo."""
        if self.code.isVisible() and name in ("undo", "redo") and not self.web_on:  # el deshacer del código
            return getattr(self.code, name)()
        if name in ("undo", "redo") and not self.web_on and self.book_wide():
            return self.book_step(name == "redo")
        if not self.web_on:
            return getattr(self.sheet, name)(*args)
        call = self.web.call
        actions = {
            "toggle_bold": lambda: call("cmd", "bold"), "toggle_italic": lambda: call("cmd", "italic"),
            "toggle_underline": lambda: call("cmd", "underline"), "toggle_strike": lambda: call("cmd", "strikeThrough"),
            "toggle_sup": lambda: call("cmd", "superscript"), "toggle_sub": lambda: call("cmd", "subscript"),
            "clear_format": lambda: call("cmd", "removeFormat"), "insert_line_break": lambda: call("cmd", "insertLineBreak"),
            "undo": lambda: self.web_undo(False), "redo": lambda: self.web_undo(True),
            "insert_symbol": lambda text: call("cmd", "insertText", text),
            "wrap": lambda a, b: self.web.page().runJavaScript(
                f"document.execCommand('insertText', false, {json.dumps(a)} + window.getSelection().toString() + "
                f"{json.dumps(b)})"),
            "insert_note": lambda: call("cmd", "insertText", "[Nota: ]"),
            "set_color": lambda color: call("css", "foreColor", QColor(color).name() if color is not None else "inherit"),
            "set_family": lambda family: call("css", "fontName", family or "inherit"),
            "grow_font": lambda step: call("size", "1.2" if step > 0 else "0.83"),
        }
        # Lo demás en la hoja de Qt: versalitas, recuadro, lista, unir, separador, mayúsculas, y estilo, alineación y
        # sangría del párrafo (en Chromium cambian su atributo a mano, fuera de su deshacer: Ctrl+Z no los quitaba)
        if name not in actions:
            self.on_sheet(lambda: getattr(self.sheet, name)(*args))
            return None
        actions[name](*args)
        self.web.setFocus()
        return None

    def web_undo(self, redo):
        """Deshacer (o rehacer) en la hoja web; sin nada que deshacer en Chromium, lo hecho por la hoja de Qt (lo que
        pasó por on_sheet, o lo escrito antes de cambiar de capítulo) se deshace allí."""
        def done(ok):
            doc = self.sheet.document()
            if not ok and self.book_wide():  # lo de otro capítulo: se pasa a él y se sigue en la hoja
                self.flush_web(lambda: (self.web_done(), self.book_step(redo)))
            elif not ok and (doc.isRedoAvailable() if redo else doc.isUndoAvailable()):
                self.on_sheet(self.sheet.redo if redo else self.sheet.undo)
        self.web.call("undo", redo, done=done)

    def toggle_web(self):
        """La pestaña «Hoja web» del panel, o vuelta a «Original»."""
        if self.web is not None:
            self.side_tabs.setCurrentIndex(0 if self.web_tab() else WEB_TAB)

    def web_tab(self):
        # al cerrarse el editor aún llegan cambios de foco, con las pestañas ya borradas
        return self.web is not None and shiboken6.isValid(self.side_tabs) and self.side_tabs.currentIndex() == WEB_TAB

    def doc_rev(self):
        doc = self.sheet.document()
        return id(doc), doc.revision()

    def refresh_web(self):
        """La hoja web, sin el cursor en ella, enseña lo de la hoja: al cambiar de capítulo o tras escribir."""
        if self.closed or not self.web_tab() or self.web_on:
            return
        if self.current[0] < 0:
            self.web.show_note("«Contenido» es la lista de capítulos: se edita en la hoja de la izquierda.")
            return
        if self.web.loaded and self.web_section == self.current and self.web_rev == self.doc_rev():
            return
        self.load_web(active=False)

    def sync_web_live(self):
        """La hoja web, sin el cursor en ella, sigue a la hoja al momento: solo cambian los párrafos que cambiaron,
        sin recargar la página (recargar tardaba segundos y volvía arriba). Si algo no se puede (otro capítulo, una
        imagen nueva), se recarga."""
        if self.closed or self.web is None or self.web_on or not self.web_tab() or self.current[0] < 0:
            return
        if self.web_lines is None or self.web_section != self.current:
            self.refresh_web()
            return
        if not self.web.loaded:  # cargando: web_ready la pone al día (recargar en cada tecla no acababa nunca)
            return
        import websheet
        new = [line for lines in self.sheet.all_lines() for line in lines]
        old = self.web_lines
        if new == old:
            return
        ops = []
        for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
            if op == "equal":
                continue
            fresh = new[j1:j2]
            if any((convert.TOKEN.fullmatch(line.strip()) or convert.PICTURE.fullmatch(line.strip()))
                   and line.strip() not in self.web_objects for line in fresh):
                self.load_web(active=False)  # una ilustración que la página no tiene
                return
            ops.append([i1, i2, [websheet._element(line, self.web_objects, self.web_page) for line in fresh]])
        self.web_lines, self.web_rev = new, self.doc_rev()

        def done(ok):
            if not ok:
                self.load_web(active=False)
                return
            if any(i2 - i1 != len(htmls) for i1, i2, htmls in ops):  # párrafos de más o de menos: avisos en su sitio
                self.web_marks()
            elif self.show_changes_action.isChecked():
                self.web.call("setChanges", *self.web_changes())
            self.web.call("here", self.web_index(self.sheet.textCursor().block()), False, self.here_color())
        self.web.call("splice", ops[::-1], done=done)

    def activate_web(self):
        """El cursor entró en la hoja web: desde ahora la cinta y lo escrito van a ella. Si la hoja cambió desde que se
        cargó, antes se vuelve a cargar."""
        if self.web_on or not self.web_tab() or self.current[0] < 0:
            return
        if not self.web.loaded or self.web_section != self.current or self.web_rev != self.doc_rev():
            self.load_web(active=True)
            return
        self.web_on, self.web_home, self.web_dirty = True, None, False
        if self.reading is not None:  # la voz iba por párrafos de la otra hoja
            self.stop_reading()
        self.web_poll.start(WEB_POLL_MS)
        self.web_marks()
        self.fit_ribbon_to_view()
        self.update_tools()

    def side_key(self, event):
        """Con el foco en el panel de la derecha («Original», «EPUB»: un clic en la página), los atajos de la hoja
        (Ctrl+Z, Ctrl+Y, Ctrl+N, F7…) iban a él y no hacían nada: van a la hoja o al código a la vista. Copiar y
        seleccionar todo se quedan en el panel; la hoja web escribe por sí misma."""
        if event.type() != QEvent.Type.KeyPress or self.closed or self.forwarding or not shiboken6.isValid(self.side):
            return False
        focus = QApplication.focusWidget()
        if focus is None or not (focus is self.side or self.side.isAncestorOf(focus)):
            return False
        if self.web is not None and (focus is self.web or self.web.isAncestorOf(focus)):
            return False
        mods, key = event.modifiers(), event.key()
        command = mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)             or Qt.Key.Key_F1 <= key <= Qt.Key.Key_F35
        if not command or event.matches(QKeySequence.StandardKey.Copy)                 or event.matches(QKeySequence.StandardKey.SelectAll):
            return False
        target = self.code if self.code.isVisible() else self.sheet
        self.forwarding = True  # la tecla reenviada pasa otra vez por este filtro
        try:
            QApplication.sendEvent(target, QKeyEvent(QEvent.Type.KeyPress, key, mods, event.text()))
        finally:
            self.forwarding = False
        return True

    def on_focus(self, old, new):
        """El cursor pasa de una hoja a otra: a la web, se escribe en ella; a la hoja, lo escrito en la web pasa a la
        hoja antes (y el cursor se queda donde se pulsó)."""
        if self.closed or new is None or not shiboken6.isValid(self.web) or not shiboken6.isValid(self.sheet):
            return
        if new is self.web or self.web.isAncestorOf(new):
            self.activate_web()
        elif self.web_on and any(new is w or w.isAncestorOf(new) for w in (self.sheet, self.code)):
            pos = self.sheet.textCursor().position()

            def back():
                self.web_done()
                cursor = self.sheet.textCursor()
                cursor.setPosition(min(pos, self.sheet.document().characterCount() - 1))
                self.sheet.setTextCursor(cursor)
            self.flush_web(back)

    def load_web(self, active=True):
        """Pone en la hoja web el capítulo de la hoja (con lo que lleve escrito). active: se escribe en ella."""
        import websheet
        start, end = self.current
        lines = [line for line in editor.dump(self.sheet.document()).split("\n\n") if line.strip()]
        pictures = {}
        for line in lines:  # imágenes puestas en esta sesión: su archivo en la carpeta del programa
            m = convert.PICTURE.fullmatch(line.strip())
            if m and editor.IMAGES and (Path(editor.IMAGES) / m[2]).is_file():
                pictures[line.strip()] = (Path(editor.IMAGES) / m[2]).read_bytes()
        files, objects, path, head, body = websheet.sources(self.app.book, start, end, lines, pictures,
                                                            self.app.horizontal_var.get())
        if "webview" in self.pending:  # una carga para ver, pendiente: esta la sustituye
            qtk.cancel(self.pending.pop("webview"))
        text = websheet.page(lines, objects, head, body, path)
        if self.app.book.vertical and self.app.horizontal_var.get():  # como saldrá: de izquierda a derecha
            text = text.replace("</head>", f"<style>{faithful.HORIZONTAL_CSS}</style></head>", 1)
        self.web.load_sheet(files, path, text)
        self.web_lines, self.web_objects, self.web_page = lines, objects, path
        self.web_on, self.web_dirty, self.web_home = active, False, None
        self.web_base, self.web_section, self.web_rev = None, self.current, self.doc_rev()
        if active:
            self.web_poll.start(WEB_POLL_MS)
        else:
            self.web_poll.stop()

    def web_ready(self):
        """La hoja web cargó: sus marcas, el cursor en el párrafo del de la hoja y lo que esperaba (buscar)."""
        self.web.call("dump", done=lambda d: setattr(self, "web_base", self.web_text(d)))  # sin tocar: la base
        self.web.call("here", self.web_index(self.sheet.textCursor().block()), not self.web_on, self.here_color())
        if self.web_on:
            self.web.setFocus()
        self.web_marks()
        self.web.call("goHome", self.web_index(self.sheet.textCursor().block()))
        if not self.web_on:  # lo escrito en la hoja mientras cargaba
            self.later("webview", 0, self.sync_web_live)
        then, self.web_then = self.web_then, None
        if then:  # el de sheet_to_web acaba con web_settled, ya con la selección puesta
            then()
        else:
            self.web_settled()

    def web_settled(self):
        """La hoja web volvió de on_sheet con su selección: lo pulsado mientras tanto, ahora."""
        self.web_busy = False
        if self.web_queue:
            self.on_sheet(self.web_queue.pop(0))

    def web_index(self, block):
        """Número del párrafo de la hoja web (hijo de la hoja, como los carga load_web) del bloque de la hoja."""
        if block.document() is not self.sheet.document():
            return 0
        return sum(len(lines) for lines in self.sheet.all_lines()[:block.blockNumber()])

    def web_block(self, n):
        """El bloque de la hoja del párrafo n de la hoja web (al revés que web_index)."""
        doc = self.sheet.document()
        for k, lines in enumerate(self.sheet.all_lines()):
            n -= len(lines)
            if n < 0:
                return doc.findBlockByNumber(k)
        return doc.lastBlock()

    def poll_web(self):
        """Cada poco, con la hoja web a la vista: el estado de la cinta, las palabras nuevas que revisar, el borrador
        y el párrafo del cursor (el panel del original lo sigue)."""
        if not self.web_on:
            return
        self.web.call("state", done=self.web_polled)
        self.web.call("takeText", done=self.web_typed)

    def web_polled(self, st):
        if not self.web_on or not shiboken6.isValid(self.sheet):
            return
        self.web_state = json.loads(st) if st else {}
        self.web_dirty = bool(self.web_state.get("dirty"))
        self.update_tools()
        self.sync_undo()  # también Rehacer: antes solo Deshacer seguía a la web
        if self.web_state.get("wants") in ("undo", "redo"):  # Ctrl+Z o Ctrl+Y sin nada que hacer en Chromium
            self.web_undo(self.web_state["wants"] == "redo")
        if isinstance(self.reading, int) and self.web_state.get("i", -1) != self.reading:
            self.stop_reading()  # un clic o una tecla en otro párrafo: la voz se calla, como en la hoja
        home = self.web_state.get("home", -1)
        if home >= 0 and home != self.web_home:  # la hoja de Qt (oculta) al mismo párrafo: el panel dibuja su página
            self.web_home = home
            block = self.web_block(home)
            if block.isValid() and block != self.sheet.textCursor().block():
                self.sheet.setTextCursor(QTextCursor(block))

    def web_typed(self, text):
        """Se escribió en la hoja web: a la hoja de Qt al momento (el código y el original lo siguen sin salir de la
        web), palabras que revisar, el borrador y la pestaña «EPUB»."""
        if not text or not self.web_on:
            return
        if not self.web_busy and self.web.loaded and self.web_base is not None:  # recargando, el volcado sería viejo
            # si al volver ya se salió de la web, su volcado ya pasó y este marcaría la web como al día sin estarlo
            self.web.call("dump", done=lambda d: self.web_on and not self.web_busy and self.take_web(d))
        self.send_words(text)
        if "draft" not in self.pending:
            self.later("draft", DRAFT_MS, self.write_draft)
        if self.web_shown():
            self.later("web", 1200, self.draw_web)
        self.later("nav", 600, self.refill_nav)

    def web_text(self, dumped):
        """lnc.dump() como texto de la hoja (websheet.lines_of), o None si no se pudo."""
        import websheet
        if not dumped:
            return None
        try:
            return websheet.lines_of(dumped)
        except ValueError as e:  # no debería pasar: Chromium siempre cierra sus etiquetas
            _log(f"Hoja web: {e}")
            return None

    def web_changes(self):
        """(n.º de los párrafos cambiados, HTML de antes de cada párrafo) de la hoja web, en su orden. Lo de antes, solo
        en el primero de los que vienen del mismo (Intro): la hoja web compara con él los que lo siguen; Chromium saca
        su texto como el de la hoja (notas, saltos)."""
        changed = {b.blockNumber() for b in self.sheet.changed_blocks()}
        marked, before, n, block = [], [], 0, self.sheet.document().firstBlock()
        for lines in self.sheet.all_lines():
            original = editor.compared(block, self.sheet.look.firsts)
            head = original is not None and len(lines) == 1 and editor.original_group(block)[1] == 0
            for line in lines:
                before.append(editor.to_code(original) if head else None)
                if block.blockNumber() in changed and not (convert.TOKEN.fullmatch(line.strip())
                                                           or convert.PICTURE.fullmatch(line.strip())):
                    marked.append(n)
                n += 1
            block = block.next()
        return marked, before

    def web_marks(self):
        """Marcas de la hoja web, las mismas que las de la hoja: palabras que no están, avisos del informe (del color
        de su gravedad, en su trozo) y párrafos cambiados. Se pintan con resaltados de CSS: el HTML no cambia."""
        if self.web is None or not self.web.loaded:  # también cuando solo enseña la hoja: sus cambios a la vista
            return
        import websheet
        colors = theme.mark_colors(self.app.dark)
        levels = list(convert.LEVELS)
        marked, before = self.web_changes()
        issues, self.web_issues, n = [], {}, 0
        block = self.sheet.document().firstBlock()
        for lines in self.sheet.all_lines():
            for line in lines:
                if not (convert.TOKEN.fullmatch(line.strip()) or convert.PICTURE.fullmatch(line.strip())):
                    text = block.text()
                    for issue in self.block_issues(block):
                        start, end = issue.span or (0, len(text))
                        start, end = min(start, len(text)), min(end, len(text))
                        start, end = (start, end) if end > start else (0, len(text))
                        piece = text[start:end].replace(editor.VOID_CHAR, "").replace(editor.LINE_BREAK, "")
                        self.web_issues[len(self.web_issues)] = issue
                        issues.append({"id": len(self.web_issues) - 1, "i": n, "text": piece, "at": start,
                                       "level": levels.index(issue.level) if issue.level in levels else 0})
                n += 1
            block = block.next()
        data = {"spell": sorted(w.lower() for w in self.unknown - self.ignored_words), "issues": issues,
                "changed": marked, "show": self.show_changes_action.isChecked(), "before": before,
                "del_css": websheet.DEL_CSS % theme.css_color(colors["removed"]),
                "colors": {"spell": self.sheet.tags.get("broken", {}).get("foreground", colors["Grave"]),
                           "change": theme.css_color(colors["added"])},
                "levels": [colors.get(level, colors["Grave"]) for level in levels]}
        self.web.call("setMarks", json.dumps(data))
        self.refill_nav()  # tras las marcas: el panel lista los avisos pintados

    def web_menu(self, pos):
        """Clic derecho en la hoja web, como en la hoja: sugerencias de la palabra que no está (ignorarla, añadirla al
        diccionario), cada aviso de ese punto con su explicación, arreglo e ignorar, y deshacer, cortar, copiar,
        pegar."""
        from PySide6.QtWebEngineCore import QWebEnginePage

        def show(data):
            info = json.loads(data or "{}")
            word = info.get("word", "")
            menu = QMenu(self.web)
            self.context, self.context_word, self.context_cursor, self.context_wait = menu, word, None, None
            if word and word in self.unknown - self.ignored_words:
                suggestions = self.suggestions.get(word)
                if suggestions is None:
                    self.request_suggestions(first=word)
                for suggestion in suggestions or ():
                    action = menu.addAction(suggestion, lambda s=suggestion: self.web.call("replaceWord", s))
                    font = action.font()
                    font.setBold(True)
                    action.setFont(font)
                if not suggestions:
                    wait = menu.addAction("Buscando sugerencias…" if suggestions is None else "Sin sugerencias")
                    wait.setEnabled(False)
                    self.context_wait = wait if suggestions is None else None
                menu.addAction("Ignorar", lambda: self.ignore_word(word))
                menu.addAction("Añadir al diccionario de la serie", lambda: self.add_word(word))
                menu.addSeparator()
            colors = theme.level_colors(self.app.dark)  # de texto: el naranja del subrayado se leía a 3:1
            for ident in info.get("issues", []):
                issue = self.web_issues.get(ident)
                if issue is None:
                    continue
                title = QLabel(f"● {issue.level} · {issue.title}")
                title.setStyleSheet(f"color: {colors[issue.level]}; font-weight: bold; padding: 6px 12px 2px 12px;")
                explain = QLabel(issue.problem)
                explain.setWordWrap(True)
                explain.setMaximumWidth(380)
                explain.setStyleSheet("padding: 0 12px 6px 12px;")
                for label in (title, explain):
                    action = QWidgetAction(menu)
                    action.setDefaultWidget(label)
                    menu.addAction(action)
                if issue.edit:
                    change = self.fix_preview(issue)
                    menu.addAction(f"Arreglar: «{change}»" if issue.edit[2] else "Arreglar: quitar este párrafo",
                                   lambda i=issue: self.on_sheet(lambda: self.fix_issue(i, go_next=False)))
                menu.addAction("Quitar comentario" if issue.kind == "comment" else "Ignorar este aviso",
                               lambda i=issue: (self.ignore_issue(i), self.web_marks()))
                menu.addAction("Aviso siguiente   (F8)", self.next_issue)
                menu.addSeparator()
            self.web_menu_more(menu, info, word)
            page = self.web.page()
            for item in (("Deshacer", QWebEnginePage.WebAction.Undo), ("Rehacer", QWebEnginePage.WebAction.Redo),
                         None, ("Cortar", QWebEnginePage.WebAction.Cut), ("Copiar", QWebEnginePage.WebAction.Copy),
                         ("Pegar", QWebEnginePage.WebAction.Paste), None,
                         ("Seleccionar todo", QWebEnginePage.WebAction.SelectAll)):
                if item is None:
                    menu.addSeparator()
                else:
                    menu.addAction(item[0], lambda a=item[1]: page.triggerAction(a))
            menu.popup(self.web.mapToGlobal(pos))
        self.web.call("at", pos.x(), pos.y(), done=show)

    def web_menu_more(self, menu, info, word):
        """Lo demás del clic derecho de la hoja (mark_menu) en la hoja web: cambiar la ilustración, comentario del
        párrafo, volver al original y sinónimos. Lo que cambia el texto pasa por la hoja de Qt (on_sheet), con el
        cursor donde se pulsó (lnc.at lo puso)."""
        token = convert.TOKEN.fullmatch(info.get("token", ""))
        if token and token[1] == "Ilustración":
            k = int(token[2])
            menu.addAction("Cambiar ilustración…", lambda: self.on_sheet(lambda: self.change_picture(k))).setToolTip(
                "Para moverla, córtala (Ctrl+X) y pégala donde vaya")
            menu.addSeparator()
        if info.get("home") is None:
            return
        block = self.web_block(info["home"])
        k = editor.source_of(block)
        if k is not None and editor._is_text(block) and self.app.book.blocks[k].kind == "p":
            has = any(i.kind == "comment" for i in self.block_issues(block))
            menu.addAction("Cambiar comentario…" if has else "Comentario…",
                           lambda: (self.edit_comment(block), self.web_marks()))
        if info.get("edited") or self.sheet.can_restore(block, self.loaded()):
            menu.addAction("Volver al original", lambda: self.on_sheet(self.restore_current))
        if word and word.isalpha() and word not in self.unknown - self.ignored_words and self.app.spanish():
            found = editor.synonyms(word)
            if found:
                sub = menu.addMenu("Sinónimos")
                for synonym in found:
                    shown = editor.match_case(word, synonym)
                    sub.addAction(shown, lambda s=shown: self.web.call("replaceWord", s))
        if menu.actions() and not menu.actions()[-1].isSeparator():
            menu.addSeparator()

    def on_sheet(self, fn):
        """fn() en la hoja de Qt: con la hoja web, sobre lo escrito en ella y con su selección, y vuelta a la web
        con el resultado y la selección. Así lo que Chromium no sabe hacer (versalitas, recuadro, unir…) también
        se hace desde la hoja web."""
        if not self.web_on:
            fn()
        elif self.web_busy:  # la anterior aún recarga la web: si no, esta volcaría la página vieja y la pisaría
            self.web_queue.append(fn)
        else:
            self.web_busy = True
            self.web_to_sheet(lambda: self.sheet_step(fn))

    def sheet_step(self, fn):
        try:
            fn()
        finally:  # aunque falle, la web vuelve y la cola sigue
            self.sheet_to_web()

    def web_to_sheet(self, then):
        """Lo escrito en la hoja web pasa a la de Qt, su selección también (websheet.positions), y luego then()."""
        import websheet

        def got(data):
            info = json.loads(data or "{}")
            texts, (i, s, e), to = info.get("texts", []), info.get("at") or (-1, 0, 0), info.get("to")

            def at(n, pos):  # posición en la hoja de Qt de pos del párrafo n de la web
                block = self.web_block(n)
                table = websheet.positions(texts[n] if n < len(texts) else "", block.text())
                return block.position() + table[min(pos, len(table) - 1)]

            def flushed():
                if 0 <= i < len(texts):
                    cursor = self.sheet.textCursor()
                    cursor.setPosition(at(i, s))
                    cursor.setPosition(at(*to) if to else at(i, e), QTextCursor.MoveMode.KeepAnchor)
                    self.sheet.setTextCursor(cursor)
                then()
            self.flush_web(flushed)
        self.web.call("texts", done=got)

    def sheet_to_web(self):
        """Vuelve a cargar la hoja web con la hoja de Qt, con su selección."""
        import websheet
        cursor, doc = self.sheet.textCursor(), self.sheet.document()
        ends = []
        for pos in (cursor.selectionStart(), cursor.selectionEnd()):
            block = doc.findBlock(pos)
            ends.append((self.web_index(block), block.text(), pos - block.position()))

        def place():
            def got(data):
                texts = json.loads(data or "{}").get("texts", [])
                spots = []
                for n, qt, pos in ends:
                    if n >= len(texts):
                        self.web_settled()
                        return
                    spots.append((n, websheet.web_position(websheet.positions(texts[n], qt), pos)))
                (i, a), (j, z) = spots
                self.web.call("select", i, a, z, j, done=lambda _: self.web_settled())
            self.web.call("texts", done=got)
        self.web_then = place
        self.load_web()

    def flush_web(self, then):
        """Lo escrito en la hoja web pasa a la hoja de Qt (si cambió) y luego then()."""
        self.web.call("dump", done=lambda dumped: (self.take_web(dumped), then()))

    def take_web(self, dumped):
        """lnc.dump() de la hoja web, a la hoja de Qt si se escribió en la web desde que cargó."""
        text = self.web_text(dumped)
        changed = self.web_base is None or text != self.web_base
        if text is not None and changed and self.sheet.set_text(text):
            self.web.call("rehome")  # la hoja de Qt ya tiene los párrafos de ahora
            self.web_home = None
        if text is not None:
            self.web_base, self.web_rev = text, self.doc_rev()  # las dos hojas, iguales
            self.web_lines = [line for lines in self.sheet.all_lines() for line in lines]

    def web_done(self):
        """La hoja web deja de ser donde se escribe; sigue a la vista si su pestaña lo está."""
        self.web_on, self.web_dirty, self.web_busy, self.web_queue = False, False, False, []
        self.web_poll.stop()
        self.fit_ribbon_to_view()
        self.update_tools()

    def toggle_code(self):
        """Vista «Código HTML»: el capítulo de la hoja como su HTML, editable; al volver, lo cambiado pasa a la hoja
        (un deshacer). Una línea que no se entiende o deja una etiqueta sin cerrar se avisa y no se sale."""
        if not self.code.isVisible():
            line = self.web_index(self.sheet.textCursor().block())
            # all_lines: las líneas de cada párrafo ya calculadas (dump las volvía a sacar todas)
            body = editor.to_code("\n\n".join(x for lines in self.sheet.all_lines() for x in lines))
            self.code_shell = self.shell_of(*self.current)  # la página entera: su cabecera, el texto y su pie
            self.code.setPlainText(editor.page_code(self.code_shell, body))
            self.code_text = body
            self.code_frame = self.frame_of(self.code_shell)
            h = len(self.code_frame[0])
            if not self.code.classes:  # para el autocompletado de class="…"
                self.code.classes = self.book_classes()
            color, removed = self.change_colors()  # «Mostrar cambios» frente a cada línea de antes
            saved = self.frame_of(self.shell_of(*self.current, saved=True))
            self.code.show_changes(color if self.show_changes_action.isChecked() else None, removed,
                                   [editor.pretty_line(o) for o in saved[0]]
                                   + [o and editor.pretty_line(o) for o in self.code_originals()]
                                   + [editor.pretty_line(o) for o in saved[1]])
            self.sheet.hide()
            self.code.show()
            block = self.code.document().findBlockByNumber(h + line)
            if block.isValid():
                self.code.setTextCursor(QTextCursor(block))
                self.code.centerCursor()
            self.code.setFocus()
            self.sync_view()  # la cinta, solo Vista y Deshacer (fit_ribbon_to_view)
            return
        if not self.take_shell():
            return
        lines, h, f = self.code_parts()
        if "\n".join(lines[h:f]) != self.code_text:
            try:
                text = editor.from_code("\n".join(lines[h:f]))
            except ValueError as e:
                self.sync_view()
                messagebox.showwarning("Código HTML", f"{e}\n\nArréglalo para volver a la hoja.", parent=self.win)
                return
            if editor.to_code(text) != self.code_text:  # solo líneas vacías de más: la hoja ya es esa
                self.sheet.set_text(text)
        code = lines[h:self.code.textCursor().blockNumber()]
        self.sheet.setTextCursor(QTextCursor(self.web_block(sum(1 for raw in code if raw.strip()))))
        self.code.hide()
        self.sheet.show()
        self.sheet.ensureCursorVisible()
        self.sheet.setFocus()
        self.sync_view()
        self.update_tools()

    # La cabecera del capítulo en «Código HTML»: lo de antes y después del texto en su página del EPUB
    def shell_of(self, start, end, saved=False):
        """(archivo, antes, después) de la página del capítulo start..end: la editada en esta sesión (salvo con saved),
        la guardada en el libro o la de siempre (la del original, o la que escribe write_epub). None en «Contenido»
        o si el original no se puede leer: el código enseña solo el texto, como antes."""
        book = self.app.book
        if start < 0 or start >= len(book.blocks):
            return None
        if not saved and (start, end) in self.shells:
            return self.shells[(start, end)]
        if book.blocks[start].shell:
            return book.blocks[start].shell
        if book.origin:
            name = websheet_text_file(book.blocks[start:end])
            try:
                with zipfile.ZipFile(book.origin) as z:
                    return (name, *faithful.file_shell(z.read(name).decode("utf-8", "replace")))
            except (KeyError, OSError, zipfile.BadZipFile, TypeError):
                return None
        heads = self.app.section_heads()
        title = self.app.tree.set(str(start), "title") if start in heads and self.app.tree.exists(str(start)) \
            else "Inicio"
        return ("", *convert.page_shell(title, self.app.write_options().get("lang", "es"), start in heads))

    @staticmethod
    def frame_of(shell):
        """([líneas de la cabecera], [líneas del pie]) de shell, como las enseña el código."""
        if not shell:
            return [], []
        lines = editor.page_code(shell, "\ue000").split("\n")
        cut = lines.index("\ue000")
        return lines[:cut], lines[cut + 1:]

    def code_parts(self):
        """(líneas del código, h, f): cabecera lines[:h], texto lines[h:f], pie lines[f:] (editor.split_page)."""
        lines = self.code.code().split("\n")
        if not self.code_shell:
            return lines, 0, len(lines)
        head, foot = self.code_frame  # sin tocar (lo normal): donde estaban, aunque el texto esté a medio escribir
        if len(lines) >= len(head) + len(foot) and lines[:len(head)] == head and lines[len(lines) - len(foot):] == foot:
            return lines, len(head), len(lines) - len(foot)
        return (lines, *editor.split_page(lines))

    def shell_changed(self):
        lines, h, f = self.code_parts()
        return bool(self.code_shell) and (lines[:h], lines[f:]) != self.code_frame

    def take_shell(self):
        """La cabecera escrita en el código, para guardarla con el capítulo: si no vale (XML roto, sin <head> o
        <body>, texto en ella), se avisa y False: el código sigue a la vista para arreglarla."""
        if not self.shell_changed():
            return True
        lines, h, f = self.code_parts()
        error = editor.check_shell(lines[:h], lines[f:], f + 1) if h else \
            "Falta <body>: la cabecera tiene que acabar en él y en lo que envuelve el texto"
        if error:
            self.sync_view()
            messagebox.showwarning("Código HTML", f"{error}\n\nArréglalo para volver a la hoja.", parent=self.win)
            return False
        head, foot = lines[:h], lines[f:]
        if (head, foot) == self.frame_of(self.shell_of(*self.current, saved=True)):
            self.shells.pop(self.current, None)  # como estaba: nada que guardar
        else:
            self.shells[self.current] = (self.code_shell[0], "\n".join(head) + "\n", "\n" + "\n".join(foot) + "\n")
        self.code_shell, self.code_frame = self.shell_of(*self.current), (head, foot)
        return True

    def apply_code_live(self):
        """Lo escrito en el código, a la hoja si ya vale (a medio escribir una etiqueta, aún no): así la hoja web, el
        EPUB y el original lo enseñan sin salir del código. Cada vez es un deshacer de la hoja."""
        lines, h, f = self.code_parts()
        code = "\n".join(lines[h:f])
        if not self.code.isVisible() or code == self.code_text:
            return
        try:
            text = editor.from_code(code)
        except ValueError:
            return
        # code_text es el código de la hoja, sin líneas vacías: con una (Intro sin escribir aún) las líneas ya no
        # casan con los párrafos y el cursor del código no sigue al de la hoja (saltaba a la línea de arriba)
        canonical = editor.to_code(text)
        line = sum(1 for raw in lines[h:self.code.textCursor().blockNumber()] if raw.strip())
        if canonical != self.code_text:
            self.code_text = canonical
            self.applying_code = True
            try:
                self.sheet.set_text(text)
            finally:
                self.applying_code = False
        self.sheet.setTextCursor(QTextCursor(self.web_block(line)))

    def refresh_code(self):
        """La hoja cambió por otro lado (la hoja web) con el código a la vista: el código, al día, en su línea."""
        if not self.code.isVisible() or self.applying_code:
            return
        code = editor.to_code(editor.dump(self.sheet.document()))
        lines, h, f = self.code_parts()
        typed = "\n".join(lines[h:f])
        if code == typed:
            return
        if typed != self.code_text:  # lo escrito aún no pasó a la hoja (una etiqueta a medias): pisarlo lo perdía,
            try:                     # con el deshacer del código, y no se podía volver a la hoja
                pending = editor.to_code(editor.from_code(typed)) != self.code_text
            except ValueError:
                pending = True
            if pending:
                return
        line = self.code.textCursor().blockNumber()
        self.code.blockSignals(True)
        self.code.setPlainText("\n".join(lines[:h] + ([code] if code else []) + lines[f:]))  # la cabecera, como está
        self.code.blockSignals(False)
        self.code_text = code
        block = self.code.document().findBlockByNumber(min(line, self.code.blockCount() - 1))
        self.code.setTextCursor(QTextCursor(block))
        self.code.mark_line()

    def code_moved(self):
        """El cursor del código cambió de línea: la hoja (oculta) va a ese párrafo."""
        lines, h, f = self.code_parts()
        if self.code.isVisible() and "\n".join(lines[h:f]) == self.code_text:  # sin editar: las líneas casan
            n = self.code.textCursor().blockNumber() - h  # en la cabecera, el primer párrafo; en el pie, el último
            block = self.web_block(max(0, min(n, f - h - 1)))
            if block != self.sheet.textCursor().block():
                self.sheet.setTextCursor(QTextCursor(block))

    def show_view(self, index):
        """Pestañas sobre la hoja: la hoja o «Código HTML», una sola a la vista. El código sale de la hoja: lo escrito
        en la web pasa antes a ella; al volver, la web enseña lo cambiado."""
        if (index == 1) == self.code.isVisible():
            return
        if index == 1 and self.web_on:
            self.flush_web(lambda: (self.web_done(), self.toggle_code()))
            return
        self.toggle_code()

    def sync_view(self):
        """La pestaña elegida sobre la hoja, la de lo que se ve (sin volver a pedir el cambio)."""
        self.view_tabs.blockSignals(True)
        self.view_tabs.setCurrentIndex(1 if self.code.isVisible() else 0)
        self.view_tabs.blockSignals(False)
        if self.web is not None:
            self.later("webview", 0, self.refresh_web)
        self.fit_ribbon_to_view()

    def fit_ribbon_to_view(self):
        """La cinta, solo con lo que vale: con el código a la vista, Vista y Deshacer (el deshacer del código: con la
        pestaña entera apagada se quedaba gris aunque hubiera qué deshacer); si se escribe en la hoja web, toda."""
        on = not self.code.isVisible() or self.web_on
        keep = (self.undo_btn, self.redo_btn, self.undo_group)
        for n in range(self.ribbon_pages.count() - 1):
            page = self.ribbon_pages.widget(n)
            page.setEnabled(True)
            for child in page.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly):
                if child not in keep:
                    child.setEnabled(on)
        self.sync_undo()

    def sync_undo(self):
        """Deshacer y Rehacer, según lo que se edita: el código, la hoja web (lo suyo o, sin nada, lo de la hoja de Qt,
        como web_undo) o la hoja."""
        if not hasattr(self, "undo_btn") or not shiboken6.isValid(self.undo_btn):  # aún sin cinta, o ya cerrado
            return
        doc = (self.code if self.code.isVisible() else self.sheet).document()
        web = getattr(self, "web_state", {}) if getattr(self, "web_on", False) else {}
        book = self.book_wide() and not self.code.isVisible() and hasattr(self, "book_undo")
        self.undo_btn.setEnabled(bool(web.get("canUndo")) or doc.isUndoAvailable()
                                 or book and self.book_pending(self.book_undo, False) is not None)
        self.redo_btn.setEnabled(bool(web.get("canRedo")) or doc.isRedoAvailable()
                                 or book and self.book_pending(self.book_redo, True) is not None)

    def option(self, key):
        """Opciones → Editor: lo elegido, o lo de siempre."""
        return self.app.settings.get(key, EDITOR_DEFAULTS[key])

    def book_wide(self):
        """¿Deshacer y rehacer de todo el libro (Opciones → Editor), y no de cada capítulo?"""
        return self.option("editor_undo") == UNDO_SCOPES[1]

    def note_step(self, key):
        """Un paso nuevo en el deshacer del capítulo key: el último del libro. Lo rehecho de antes ya no se rehace,
        como en Word (Qt ya lo quitó de ese capítulo)."""
        self.book_undo.append(key)
        self.book_redo.clear()
        self.sync_undo()

    def book_pending(self, steps, redo):
        """Posición en steps del último paso que aún se puede deshacer (o rehacer); None si no queda ninguno. Los
        que ya no están en su capítulo (deshechos con su propia orden) se saltan."""
        for n in range(len(steps) - 1, -1, -1):
            page = self.pages.get(steps[n])
            if page and (page["doc"].isRedoAvailable() if redo else page["doc"].isUndoAvailable()):
                return n
        return None

    def book_step(self, redo):
        """Deshacer (o rehacer) de todo el libro: el último cambio, en el capítulo que sea; si es otro, se abre. Sin
        nada apuntado, lo de este capítulo."""
        source, target = (self.book_redo, self.book_undo) if redo else (self.book_undo, self.book_redo)
        n = self.book_pending(source, redo)
        if n is None:
            (self.sheet.redo if redo else self.sheet.undo)()
            return
        key = source[n]
        del source[n:]  # los de detrás ya no se podían deshacer
        if key != self.current:
            self.open_section(*key)
        (self.sheet.redo if redo else self.sheet.undo)()
        target.append(key)
        self.sync_undo()

    def book_openings(self):
        """[(clase, etiqueta)] de los párrafos <p class="…"> del EPUB de entrada, de más a menos usadas (15 como mucho)."""
        if not self.app.book or not self.app.book.origin:
            return []
        count = collections.Counter(b.opening for b in self.app.book.blocks if b.kind == "p" and b.opening
                                    and (convert.OPENING.match(b.opening) or [None, ""])[1].lower() == "p"
                                    and 'class="' in b.opening)
        return [(re.search(r'class="([^"]*)"', o)[1], o) for o, _ in count.most_common(15)]

    def table_dialog(self):
        """«Tabla…»: columnas, filas y cabecera; la tabla va tras el párrafo del cursor, lista para escribir encima."""
        win = Dialog(self.win)
        win.setWindowTitle("Tabla")
        columns, rows, head = QSpinBox(), QSpinBox(), QCheckBox("Primera fila de cabecera")
        columns.setRange(1, 8)
        columns.setValue(2)
        rows.setRange(1, 40)
        rows.setValue(3)
        head.setChecked(True)
        ok, cancel = _button("Insertar", win.accept, "primary"), _button("Cancelar", win.reject)
        column = QVBoxLayout(win)
        column.addLayout(_row(_label("Columnas:"), columns, _label("Filas:"), rows, 1))
        column.addWidget(head)
        column.addWidget(_label("En la hoja cada fila es una línea, con « | » entre las celdas: se escribe encima.",
                                "muted"))
        column.addLayout(_row(1, ok, cancel))
        if win.exec():
            self.on_sheet(lambda: self.sheet.insert_table(columns.value(), rows.value(), head.isChecked()))

    def note_dialog(self, note=None):
        """Nota al pie en un cuadro: nueva en el cursor o, con el cursor en una (o note), la de ahí. En la hoja va como
        «[Nota: …]»; en el EPUB, nota emergente."""
        def run():
            here = note or self.sheet.note_at(self.sheet.textCursor().position())
            text, ok = QInputDialog.getMultiLineText(
                self.win, "Nota al pie", "Texto de la nota (en el EPUB sale al tocar su llamada; vacío la quita):",
                here[2] if here else "")
            if ok and (text.strip() or here):
                self.sheet.set_note(text, here[:2] if here else None)
        self.on_sheet(run)

    def hidden_title_dialog(self):
        """«Título oculto…»: el nombre del capítulo nuevo (el del de ahora, para empezar) y va antes del cursor."""
        def run():
            if not self.current or self.current[0] < 0:  # «Contenido», la lista de capítulos: solo se renombra
                self.tell("El título oculto va en un capítulo del libro, no en «Contenido»")
                return
            row = next((s for s in self.sections if s[1:3] == self.current), None)
            name, ok = QInputDialog.getText(self.win, "Título oculto", "Nombre del capítulo en el índice (no se ve "
                                            "en la página):", text=row[0] if row and row[1] >= 0 else "")
            if ok and name.strip():
                self.sheet.insert_hidden_title(" ".join(name.split()))
        self.on_sheet(run)

    def add_ruby(self):
        """Furigana: la lectura encima del texto elegido (o de lo que se escriba), en la hoja a la vista."""
        if self.web_on:
            base = self.web.page().selectedText().strip()  # noqa: lo elegido en la hoja web
        else:
            cursor = self.sheet.textCursor()
            base = cursor.selectedText().strip() if cursor.hasSelection() else ""
        if not base:
            base, ok = QInputDialog.getText(self.win, "Furigana", "Texto (el nombre o la palabra):")
            if not ok or not base.strip():
                return
        reading, ok = QInputDialog.getText(self.win, "Furigana", f"Lectura de «{base}» (sale pequeña encima):")
        if ok and reading.strip() and self.web_on:
            self.web.call("ruby", html.escape(base.strip(), quote=False), html.escape(reading.strip(), quote=False))
        elif ok and reading.strip():
            self.sheet.insert_ruby(base.strip(), reading.strip())

    def update_tools(self):
        """La cinta sigue al cursor, como en Word: N y K marcadas si lo están, y el estilo del párrafo."""
        if not shiboken6.isValid(self.sheet) or not shiboken6.isValid(self.style):
            return
        if getattr(self, "web_on", False):
            st = self.web_state
            for button, key in ((self.bold_btn, "bold"), (self.italic_btn, "italic"), (self.underline_btn, "underline"),
                                (self.strike_btn, "strike"), (self.sup_btn, "sup"), (self.sub_btn, "sub"),
                                (self.caps_btn, "sc"), (self.frame_btn, "frame")):
                button.setChecked(bool(st.get(key)))
            tag = st.get("tag", "p")
            self.style.setCurrentText(next((name for name, t in WEB_TAGS.items() if t == tag), "Normal"))
            align = st.get("align", "")
            self.left_btn.setChecked(align in ("left", "", "start"))
            self.center_btn.setChecked(align == "center")
            self.right_btn.setChecked(align == "right")
            self.justify_btn.setChecked(align == "justify")
            return
        f = self.sheet.currentCharFormat()
        self.bold_btn.setChecked(f.fontWeight() >= QFont.Weight.DemiBold)
        self.italic_btn.setChecked(f.fontItalic())
        self.style.setCurrentIndex(STYLES.index(self.sheet.paragraph_style()))
        self.underline_btn.setChecked(f.fontUnderline() and not f.isAnchor())
        self.strike_btn.setChecked(f.fontStrikeOut())
        self.sub_btn.setChecked(self.sheet.is_sub())
        self.justify_btn.setChecked(self.sheet.paragraph_css("text-align") == "justify")
        self.sup_btn.setChecked(self.sheet.is_sup())
        self.caps_btn.setChecked(self.sheet.is_small_caps())
        self.left_btn.setChecked(not self.sheet.is_centered() and not self.sheet.is_right())
        self.center_btn.setChecked(self.sheet.is_centered())
        self.right_btn.setChecked(self.sheet.is_right())
        self.frame_btn.setChecked(self.sheet.is_framed())

    def outline(self):
        """Secciones del libro para el panel de navegación: [(título, inicio, fin, sangría)], una por capítulo.
        Lo de antes del primero es «Inicio del libro»."""
        blocks, found = self.app.book.blocks, [(TOC_PAGE, -1, -1, False)]
        heads = self.app.section_heads() + [len(blocks)]
        if heads[0] > 0:
            found.append((FRONT, 0, heads[0], False))
        for k, end in zip(heads, heads[1:]):
            title = self.app.tree.set(str(k), "title") if self.app.tree.exists(str(k)) else convert._plain(blocks[k])
            kind = self.app.tree.set(str(k), "inc") if self.app.tree.exists(str(k)) else ON
            found.append((title, k, end, kind != ON))
        return found

    def open_section(self, start, end, focus=None):
        """Muestra el capítulo start..end en la hoja (el ya editado si se visitó) y lo dice en la cinta."""
        if getattr(self, "web_on", False) and self.current and (start, end) != self.current:
            # con la hoja web: lo escrito pasa a su capítulo, se abre el otro y se sigue escribiendo en la web;
            # «Contenido» (la lista de capítulos) solo en la hoja
            self.flush_web(lambda: (self.web_done(), self.open_section(start, end, focus),
                                    start >= 0 and self.web_tab() and self.load_web(active=True)))
            return
        if self.reading is not None:
            self.stop_reading()
        page = self.pages.get((start, end))
        if page is None:
            lines, levels = self.section_lines(start, end)
            doc, look = self.sheet.set_content(lines, self.pictures(start, end), self.colors["muted"], levels)
            page = self.pages[(start, end)] = {"doc": doc, "look": look, "lines": lines, "levels": levels}
            if start >= 0:
                self.replay_history(doc, lines, look)
            page["mark"] = (doc.availableUndoSteps(), doc.availableRedoSteps())  # sin tocar: keep_history lo salta
            # Tras el historial de otras veces: ese queda en su capítulo, por detrás de lo de hoy en los demás
            doc.undoCommandAdded.connect(lambda key=(start, end): self.note_step(key))
            if self.show_changes_action.isChecked():
                look.show_changes(*self.change_colors(), self.changes_in_view())
        else:
            self.sheet.use(page["doc"], page["look"])
            if page.pop("stale", False):  # «Mostrar cambios» cambió con otro capítulo a la vista
                page["look"].show_changes(page["look"].changes, None, self.changes_in_view())
        self.current = (start, end)
        self.epub_lines = None  # la pestaña EPUB es de otro capítulo hasta que se dibuje este
        if focus is not None:
            self.sheet.goto_source(focus)
        row = next((n for n, s in enumerate(self.sections) if s[1:3] == (start, end)), None)
        title = self.sections[row][0] if row is not None else self.app.tree.set(self.item, "title")
        self.win.setWindowTitle(f"Texto · {title}")
        self.show_chapter(row)
        self.sync_undo()
        self.drawn = None
        self.update_tools()
        self.draw_source()
        self.refresh_marks()
        if getattr(self, "spell", None):
            self.request_suggestions()
        self.sheet.ensureCursorVisible()
        self.sheet.setFocus()
        if self.web is not None and not self.web_on:  # la hoja web enseña el capítulo nuevo
            self.later("webview", 0, self.refresh_web)

    def replay_history(self, doc, lines, look):
        """El deshacer y el rehacer de otras veces en este capítulo, paso a paso, en el de su hoja: al reabrir el
        editor (o la aplicación) Ctrl+Z y Ctrl+Y siguen donde se dejaron, y «Mostrar cambios» compara con el
        principio. La hoja acaba como está el libro."""
        entry = history_entry(self.app.editor_history, "\n\n".join(line for _, line in lines))
        if entry is None:
            return
        versions, at, first = entry
        look.firsts, look.baseline = history_firsts(first, versions), _history_lines(first)
        doc.setUndoRedoEnabled(False)  # la más vieja es el principio: no se deshace más allá
        self.sheet.set_text(versions[0])
        doc.setUndoRedoEnabled(True)
        ahead = 0  # pasos tras el de ahora: se deshacen y quedan para rehacer
        for n, version in enumerate(versions[1:], 1):
            ahead += self.sheet.set_text(version) and n > at
        for _ in range(ahead):
            doc.undo()
        self.sheet.moveCursor(QTextCursor.MoveOperation.Start)

    def section_lines(self, start, end):
        """Líneas de la sección como se cargan en la hoja, [(bloque, línea)], y {bloque: tipo} de sus títulos. Un
        título se ve con el nombre de su fila: el que irá al EPUB (renombrado, mayúsculas corregidas). La sección
        -1..-1 es «Contenido»: los capítulos y subtítulos de la lista, un título por línea."""
        book, tree = self.app.book, self.app.tree
        if start < 0:
            heads = [i for i in tree.get_children() if book.blocks[int(i)].kind == "heading"
                     and tree.set(i, "inc") in (ON, OFF)]
            return [(int(i), "# " + tree.set(i, "title")) for i in heads], {int(i): tree.set(i, "inc") for i in heads}
        # Un título con su etiqueta (EPUB: <h1 class="lucida">) se ve como en el libro; los demás, con el nombre de la fila
        lines = [(k, "# " + tree.set(str(k), "title") if book.blocks[k].kind == "heading" and tree.exists(str(k))
                  and not book.blocks[k].opening else convert.section_text(book, k, k + 1)) for k in range(start, end)]
        levels = {k: tree.set(str(k), "inc") for k in range(start, end)
                  if book.blocks[k].kind == "heading" and tree.exists(str(k))}
        return lines, levels

    # Borrador: lo cambiado sin aceptar sobrevive a un cierre de la aplicación
    def write_draft(self, current=None):
        """Escribe el borrador con las secciones cambiadas; sin cambios lo borra. Un fallo no para el trabajo. Con la
        hoja web, el capítulo de ahora es lo escrito en ella (current)."""
        if not self.app.pdf or self.closed or not shiboken6.isValid(self.win):
            return
        if self.web_on and current is None:
            self.web.call("dump", done=lambda d: self.write_draft(self.web_text(d) or editor.dump(self.sheet.document())))
            return
        path = _draft_path(self.app.pdf)
        pages = []
        for (start, end), page in self.pages.items():
            if start < 0:  # «Contenido» no va al borrador: sus cambios son nombres de la lista
                continue
            # una vez: page_dirty la volcaba otra
            here = current is not None and (start, end) == self.current
            text = current if here else self.sheet.dumped(page["doc"])
            if self.page_dirty(page, text):
                pages.append({"start": start, "end": end, "lines": [line for _, line in page["lines"]], "text": text})
        try:
            if pages:
                _write_json(path, {"saved": datetime.now().isoformat(timespec="minutes"), "pages": pages})
            else:
                path.unlink(missing_ok=True)
        except OSError as e:  # carpeta de solo lectura, OneDrive bloqueándolo
            self.tell(f"No se pudo guardar el borrador: {e.strerror or e}")

    def drop_draft(self):
        if self.app.pdf:
            with contextlib.suppress(OSError):
                _draft_path(self.app.pdf).unlink(missing_ok=True)

    def recover_draft(self):
        """Si la aplicación se cerró con cambios sin aceptar, ofrece recuperarlos. Una sección vuelve solo si el
        libro la tiene igual que cuando se escribió el borrador: si no, el texto ya no encajaría en sus bloques."""
        if not self.app.pdf:
            return
        path = _draft_path(self.app.pdf)
        try:
            draft = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return  # sin borrador, o roto: nada que recuperar
        pages = [p for p in draft.get("pages", []) if isinstance(p, dict)] if isinstance(draft, dict) else []
        try:
            when = datetime.fromisoformat(str(draft.get("saved"))).strftime("del %d/%m a las %H:%M")
        except (AttributeError, ValueError):
            when = "de una sesión anterior"
        if not pages or not messagebox.askyesno(
                "Recuperar cambios", f"Hay cambios sin guardar {when}: la aplicación se cerró antes de aceptarlos."
                "\n\n¿Recuperarlos?", parent=self.win):
            self.drop_draft()
            return
        lost, blocks = 0, len(self.app.book.blocks)
        for p in pages:
            try:
                start, end, text, old = int(p["start"]), int(p["end"]), str(p["text"]), [str(x) for x in p["lines"]]
            except (KeyError, TypeError, ValueError):
                lost += 1
                continue
            lines, levels = self.section_lines(start, end) if 0 <= start < end <= blocks else ([], {})
            if not lines or [line for _, line in lines] != old:
                lost += 1
                continue
            doc, look = self.sheet.set_draft(lines, text, self.pictures(start, end), self.colors["muted"], levels)
            self.pages[(start, end)] = {"doc": doc, "look": look, "lines": lines, "levels": levels}
        self.open_section(*self.current)
        if lost:
            what = "sección no se recuperó" if lost == 1 else "secciones no se recuperaron"
            messagebox.showinfo("Recuperar cambios", f"{lost} {what}: el libro cambió desde entonces.", parent=self.win)

    def show_chapter(self, row):
        """Indicador de la cinta: «☰ Capítulo 3 de 31 · Título ▾»."""
        chapters = sum(1 for s in self.sections if s[1] >= 0 and s[0] != FRONT)
        number = sum(1 for s in self.sections[:row + 1] if s[1] >= 0 and s[0] != FRONT) if row is not None else 0
        title = self.sections[row][0] if row is not None else self.app.tree.set(self.item, "title")
        special = row is not None and (title == FRONT or self.sections[row][1] < 0)  # «Inicio», «Contenido»
        where = title if special else f"Capítulo {number} de {chapters}"
        text = f"☰  {where}" if special else f"☰  {where} · {title}"
        # Ancho fijo con «…»: un título largo ensanchaba la cinta más que la pantalla (1366 px)
        width = CHAPTER_WIDTHS[self.ribbon_level]
        # ▾ del QSS (botón «menu»): el texto deja sitio a sus márgenes (12 + 28 px)
        self.chapter_btn.setText(self.chapter_btn.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight,
                                                                           width - 44))
        self.chapter_btn.setToolTip(f"{where}: {title}. Clic para ir a otro capítulo")
        self.chapter_btn.setFixedWidth(width)
        self.update_review()  # la lista de capítulos («•» en los cambiados) se rehace al abrirla

    def step_section(self, delta, at_end=False):
        """Capítulo anterior o siguiente; at_end: con el cursor en su último párrafo (se llegó pasando páginas hacia
        atrás, y las vistas se quedan al final)."""
        row = next((n for n, s in enumerate(self.sections) if s[1:3] == self.current), None)
        if row is not None and 0 <= row + delta < len(self.sections):
            start, end = self.sections[row + delta][1:3]
            self.open_section(start, end, end - 1 if at_end and start >= 0 else None)

    def page_starts(self):
        """{página del PDF: primer bloque que empieza en ella}, solo de lo que sale en los capítulos."""
        starts = {}
        for _, start, end, _ in self.sections:
            for k in range(start, end):
                starts.setdefault(self.app.book.blocks[k].page, k)
        return starts

    def web_shown(self):
        return self.side_web is not None and self.side_tabs.currentIndex() == 1

    def show_side(self, index):
        """Pestaña del panel: «Original» (la página de origen), «EPUB» (el capítulo como saldrá) u «Hoja web» (el
        capítulo con el aspecto del libro, editable)."""
        web, sheet_web = index == 1 and self.side_web is not None, index == WEB_TAB and self.web is not None
        if self.web is not None:
            if self.web_on and not sheet_web:  # lo escrito en la web pasa a la hoja antes de dejar de verla
                self.flush_web(self.web_done)
            self.web_box.setVisible(sheet_web)
            self.app.settings["editor_web"] = sheet_web  # la próxima vez, el editor abre con la misma pestaña
            self.app.save_settings()
        self.scroll.setVisible(not web and not sheet_web)
        if self.side_web is not None:
            self.epub_box.setVisible(web)
        zoom = self.web_zoom if sheet_web else self.epub_zoom if web else round(self.pdf_zoom * 100)
        self.zoom_label.setText(f"{zoom} %")
        if sheet_web:
            self.page_label.setText("")
            self.refresh_web()
        elif web:
            self.draw_web()
        else:
            self.redraw()
            self.update_status()

    def draw_web(self, text=None):
        """El capítulo de la hoja escrito como irá al EPUB (con lo cambiado y sin guardar) y dibujado por Chromium en
        el tamaño del dispositivo de la ventana, en la página del párrafo del cursor. Con la hoja web, lo escrito en
        ella (text)."""
        if self.closed or not self.web_shown() or not self.current or self.current[0] < 0:
            return
        if self.web_on and text is None:
            self.web.call("dump", done=lambda d: self.draw_web(self.web_text(d) or editor.dump(self.sheet.document())))
            return
        import webview
        book, (start, end) = self.app.book, self.current
        self.epub_drawn, self.epub_stale = self.sheet.all_lines(), False  # lo escrito después lo pone patch_epub
        text = "\n\n".join(line for lines in self.epub_drawn for line in lines) if text is None else text
        draft = copy.copy(book)  # el libro con este capítulo como está en la hoja; el de la ventana no se toca
        draft.blocks, draft.candidates = list(book.blocks), list(book.candidates)
        try:
            convert.edit_section(draft, start, end, text, editor.IMAGES)
            stop = end + len(draft.blocks) - len(book.blocks)
            # Con un EPUB, también el archivo de sus notas (notas.xhtml): sin él, la vista previa quitaba el enlace de
            # cada llamada y la nota emergente no salía
            here = book.blocks[start].src[0] if book.origin and book.blocks[start].src else ""
            wanted = {faithful._resolve(here, m[1]) for m in re.finditer(r'href="([^"#:]+)#', text)} - {here}
            notes = [b for b in book.blocks if here and b.src and b.src[0] in wanted]
            part = convert.Book(book.title, "", book.cover, draft.blocks[start:stop] + notes, [], origin=book.origin,
                                vertical=book.vertical)
            first = part.blocks[0] if part.blocks else None
            chapters = [(0, self.app.tree.set(str(start), "title") if self.app.tree.exists(str(start))
                         else convert._plain(first))] if first is not None and first.kind == "heading" else []
            options = self.app.write_options([])
            options.update(shrink=False, trim=False, epub2=False, credits=False, contents=False, synopsis=False,
                           logo="", kepub=False, preview=True, stamp=True)
            convert.write_epub(part, chapters, self.web_path, **options)
            data = Path(self.web_path).read_bytes()
            path, override = webview.section_path(data), None
            # Un capítulo de varios archivos (portadilla de página fija y texto, como en los EPUB de Kadokawa): todos
            # en una página, sobre el de texto, que trae su hoja de estilo
            mine = {b.src[0] for b in part.blocks[:stop - start] if b.src}
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                _, _, items, order, _ = faithful.package(z)
                spine = [items[i][0] for i in order if "html" in items[i][1] and items[i][0] in mine]
                base = websheet_text_file(part.blocks[:stop - start])
                if len(spine) > 1 and base in spine:
                    files = {name: z.read(name) for name in spine}
                    path, override = base, {base: webview.merge_files(files, spine, base)}
        except Exception:
            _log(traceback.format_exc())
            self.page_label.setText("Sin vista del EPUB")
            return
        width, height = DEVICES[self.app.device_var.get()]
        self.side_web.show_file(data, path, (round(width * 16 / 11), round(height * 16 / 11)), self.side_dark,
                                override=override)

    def mark_epub(self):
        """Pestaña EPUB: el párrafo del cursor recuadrado como en el panel del original, y a la vista. Se busca por sus
        letras (un párrafo repetido, por su orden); una ilustración, por su número en el capítulo."""
        if not self.web_shown():
            return
        block = self.sheet.textCursor().block()
        starts = self.line_starts()
        start, count = starts[block.blockNumber()] if block.blockNumber() < len(starts) else (0, 0)
        line = start if count else -1  # con un PDF, la vista va sellada por líneas (convert._stamp): sin adivinar
        before = [b for b in self.blocks() if b.blockNumber() < block.blockNumber()]
        if self.drawn_object(block):  # ilustración o separador dibujado: el n.º tal de las imágenes
            self.side_web.mark("", MARK_COLORS["current"], image=sum(1 for b in before if self.drawn_object(b)),
                               line=line)
            return
        if not block.text().strip():  # una línea vacía: nada que recuadrar
            self.side_web.mark(None, MARK_COLORS["current"], line=line)
            return
        words, nth = self.epub_key(block, before)
        self.side_web.mark(words, MARK_COLORS["current"], nth, line=line)

    def line_starts(self):
        """[(primera línea, cuántas)] de cada párrafo de la hoja en el capítulo que escribe draw_web: edit_section
        hace un bloque de cada línea con texto, y convert._stamp sella su elemento con ese n.º. Con los mismos
        párrafos que cuando se dibujó, los de entonces: la vista es esa hasta que se rehaga."""
        lines = self.sheet.all_lines()
        drawn = self.epub_drawn if self.epub_drawn is not None and len(self.epub_drawn) == len(lines) else lines
        starts, j = [], 0
        for chunk in drawn:
            count = sum(1 for line in chunk for part in line.splitlines() if part.strip())
            starts.append((j, count))
            j += count
        return starts

    def epub_line(self, j):
        """Clic en la línea j de la pestaña EPUB sellada (convert._stamp): la hoja va a su párrafo."""
        if self.web_on:  # lo escrito en la web, antes a la hoja
            self.flush_web(lambda: (self.web_done(), self.epub_line(j)))
            return
        n = next((n for n, (start, count) in enumerate(self.line_starts()) if start <= j < start + count), None)
        block = self.sheet.document().findBlockByNumber(n) if n is not None else None
        if self.current and self.current[0] >= 0 and block is not None and block.isValid():
            self.sheet.setTextCursor(QTextCursor(block))
            self.sheet.ensureCursorVisible()

    @staticmethod
    def epub_letters(block):
        """Solo las letras del párrafo: comillas, rayas, notas «01» y <br/> cambian en el EPUB."""
        return "".join(c for c in editor.NOTE.sub("", block.text()).lower() if c.isalpha())

    def epub_key(self, block, before):
        """(letras, n.º) con que la pestaña EPUB encuentra el párrafo block (webview.FIND_JS): un trozo de sus letras
        y cuántos de los de antes (before) también lo tienen."""
        letters = self.epub_letters
        # un separador escrito («※ ・ ※») no tiene letras: se cuenta entre los párrafos sin ellas
        text = "" if editor.objects_of(block) else letters(block)
        words = text if len(text) <= 40 else text[len(text) // 4:][:30]  # un trozo del medio
        ghost = self.ghost_title()
        nth = sum(1 for b in before if b.text().strip() and not self.drawn_object(b) and b != ghost
                  and (words in letters(b) if words else not letters(b)))
        return words, nth

    def ghost_title(self):
        """Primer párrafo de la hoja si es el título sintético de un EPUB de entrada (el nombre de la sección, que no
        se escribe): la pestaña EPUB no lo tiene y contarlo llevaba «「ま──」» a la siguiente «ま» del capítulo."""
        start = self.current[0] if self.current else -1
        title = self.app.book.blocks[start] if start >= 0 else None
        if title is None or title.kind != "heading" or not title.src or title.src[1] >= 0:
            return None
        first = self.sheet.document().firstBlock()
        return first if self.epub_letters(first) == "".join(c for c in convert._plain(title).lower() if c.isalpha()) \
            else None

    def tag_epub(self):
        """Tras dibujar la pestaña EPUB: cada párrafo suyo con su n.º en la hoja (webview.TAG_JS), y cómo era cada
        párrafo entonces: lo que se escriba luego va al suyo al momento (patch_epub)."""
        blocks = list(self.blocks())
        # como epub_key, con las letras de cada párrafo una sola vez: párrafo a párrafo tardaba segundos
        ghost = self.ghost_title()
        shown = [bool(b.text().strip()) and not self.drawn_object(b) and b != ghost for b in blocks]
        letters = [self.epub_letters(b) if on else "" for b, on in zip(blocks, shown)]
        starts = self.line_starts()
        items = []
        for n, block in enumerate(blocks):
            if not shown[n]:
                continue
            text = "" if editor.objects_of(block) else letters[n]
            words = text if len(text) <= 40 else text[len(text) // 4:][:30]
            nth = sum(1 for k in range(n) if shown[k] and (words in letters[k] if words else not letters[k]))
            items.append([n, words, nth, starts[n][0] if n < len(starts) and starts[n][1] else -1])
        self.side_web.tag(items)
        self.epub_lines = self.epub_drawn or self.sheet.all_lines()
        self.patch_epub()  # lo escrito mientras se dibujaba

    def patch_epub(self):
        """Lo escrito, en su párrafo de la pestaña EPUB al momento (sin rehacer el capítulo, que tarda): cada párrafo
        de la hoja que cambió desde que se dibujó, como irá (reemplazos y tipografía). Con párrafos de más o de menos
        espera a draw_web."""
        if not self.web_shown() or self.epub_lines is None:
            return
        lines = self.sheet.all_lines()
        if len(lines) != len(self.epub_lines):
            self.epub_stale = True
            return
        typography, rules = self.app.typography_var.get(), convert._rules(self.app.replacements)
        whole, patched = True, False  # todo puesto tal cual irá: no hace falta rehacer el capítulo al parar
        for k, (old, new) in enumerate(zip(self.epub_lines, lines)):
            if old is new or old == new:
                continue
            patched = True
            if len(new) != 1 or convert.TOKEN.fullmatch(new[0].strip()) or convert.PICTURE.fullmatch(new[0].strip()):
                whole = False
                continue
            whole = (whole and "[Nota:" not in new[0]
                     and convert.split_prefix(new[0])[0] == convert.split_prefix((old or [""])[0])[0])
            html = convert._replace(convert._inline(convert.split_prefix(new[0])[1]), rules) if rules else                 convert._inline(convert.split_prefix(new[0])[1])
            self.side_web.patch(k, convert._typography(html) if typography else html,
                                lambda: self.later("web", 300, self.draw_web))  # sin su párrafo: se rehace
        self.epub_lines = lines
        self.epub_stale = self.epub_stale or not whole  # hasta que draw_web lo rehaga
        if patched and not self.epub_stale and "web" in self.pending:  # el capítulo rehecho saldría igual: se ahorra (bloqueaba la ventana)
            qtk.cancel(self.pending.pop("web"))

    def drawn_object(self, block):
        """¿El párrafo de la hoja es una ilustración o un separador dibujado (una imagen en el EPUB)?"""
        for name in editor.objects_of(block):
            kind, _, n = name.partition(" ")
            if kind in ("Ilustración", "Imagen") or (kind == "Separador" and n.isdigit()
                                                     and int(n) < len(self.app.book.blocks)
                                                     and self.app.book.blocks[int(n)].image):
                return True
        return False

    def epub_picked(self, n):
        """Clic en la imagen n.º n de la pestaña EPUB: la hoja va a la ilustración (o separador dibujado) n.º n."""
        if self.web_on:
            self.flush_web(lambda: (self.web_done(), self.epub_picked(n)))
            return
        found = [b for b in self.blocks() if self.drawn_object(b)]
        if self.current and self.current[0] >= 0 and found:
            self.sheet.setTextCursor(QTextCursor(found[min(n, len(found) - 1)]))
            self.sheet.ensureCursorVisible()

    def epub_clicked(self, text):
        """Clic en un párrafo de la pestaña EPUB: la hoja va al párrafo con ese texto (el más cercano al cursor, si
        se repite), y con ella el original, la hoja web y el código. La limpieza tipográfica cambia comillas y rayas:
        se compara sin ellas."""
        def bare(s):
            return re.sub(r"[\W_]+", " ", s).strip().lower()
        def symbols(b):  # un separador escrito, por sus símbolos: «※ ・ ※» no tiene letras
            names = editor.objects_of(b)
            k = int(names[0].split()[-1]) if names and names[0].startswith("Separador ") else -1
            return "".join(convert._plain(self.app.book.blocks[k]).split()) if 0 <= k < len(self.app.book.blocks) \
                else ""
        want, signs = bare(text)[:60], "".join(text.split())
        if not (want or signs) or not self.current or self.current[0] < 0:
            return
        if self.web_on:  # lo escrito en la web, antes a la hoja
            self.flush_web(lambda: (self.web_done(), self.epub_clicked(text)))
            return
        here = self.sheet.textCursor().blockNumber()
        found = [b for b in self.blocks() if want and bare(b.text())[:60] == want]
        if not found and not want:
            found = [b for b in self.blocks() if symbols(b) == signs or "".join(b.text().split()) == signs]
        if not found:
            found = [b for b in self.blocks() if len(want) > 12 and want[:30] in bare(b.text())]
        if found:
            block = min(found, key=lambda b: abs(b.blockNumber() - here))
            self.sheet.setTextCursor(QTextCursor(block))
            self.sheet.ensureCursorVisible()

    def blocks(self):
        b = self.sheet.document().firstBlock()
        while b.isValid():
            yield b
            b = b.next()

    def follow_cursor(self):
        """El párrafo del cursor de la hoja (o del código), en las otras vistas: recuadrado en el EPUB y con la banda
        de la hoja en la hoja web (cuando no se escribe en ella: entonces la pone su propio cursor)."""
        if self.closed:
            return
        self.mark_epub()
        lines, h, f = self.code_parts() if self.code.isVisible() else ([], 0, 0)
        if self.code.isVisible() and "\n".join(lines[h:f]) == self.code_text:  # sin editar: las líneas casan
            block = self.code.document().findBlockByNumber(h + self.web_index(self.sheet.textCursor().block()))
            if block.isValid() and block != self.code.textCursor().block():
                self.code.setTextCursor(QTextCursor(block))
                self.code.centerCursor()
        if self.web_tab() and not self.web_on and self.web.loaded:
            self.web.call("here", self.web_index(self.sheet.textCursor().block()), True, self.here_color())

    def here_color(self):
        """La banda del párrafo del cursor de la hoja (Sheet.paintEvent), en CSS."""
        c = QApplication.palette().color(QPalette.ColorRole.Highlight)
        return f"rgba({c.red()},{c.green()},{c.blue()},0.1)"

    def step_page(self, delta):
        """◀ ▶ bajo el PDF: la hoja va al primer párrafo de la página anterior o siguiente (abriendo su capítulo si
        es otro) y el panel la dibuja. Las páginas sin nada del libro se saltan: no hay qué mostrar en la hoja.
        En la pestaña «EPUB», las páginas del capítulo como saldrá."""
        if self.web_shown() or self.web_tab():  # una pantalla más abajo o más arriba, como la barra
            view = self.side_web if self.web_shown() else self.web
            # Recién pasado de capítulo, la barra sigue siendo la del anterior (al final) hasta que Chromium carga el
            # nuevo: con clics rápidos cada uno saltaba otro capítulo. Hasta que llegue (3 s como mucho), se ignoran
            waiting = getattr(self, "turning", None)
            if waiting and waiting[0] is view and waiting[1] == view.loads and time.monotonic() - waiting[2] < 3:
                return
            bar = view.bar
            if bar.value() >= bar.maximum() if delta > 0 else bar.value() <= 0:  # al final: el capítulo de al lado
                before = self.current
                self.step_section(delta, at_end=delta < 0)
                if self.current != before:
                    self.turning = (view, view.loads, time.monotonic())
            else:
                view.step(delta)
            return
        if self.app.book.origin and self.app.pdf_doc is not None:
            # Un EPUB: sus bloques solo saben la primera página de su archivo, y con ella ◀ ▶ saltaban de capítulo
            order = [k for _, start, end, _ in self.sections for k in range(start, end)]
            k = self.source_block()
            here, i = self.source_page(k), order.index(k) if k in order else 0
            ahead = order[i + 1:] if delta > 0 else order[i - 1::-1] if i else []
            target = next((j for j in ahead if (self.source_page(j) > here if delta > 0
                                                else self.source_page(j) < here)), None)
            if target is not None and delta < 0:  # al primer párrafo de esa página
                n = order.index(target)
                while n and self.source_page(order[n - 1]) == self.source_page(target):
                    n -= 1
                target = order[n]
            if target is not None:
                self.go_block(target)
            return
        here, starts = self.app.book.blocks[self.source_block()].page, self.page_starts()
        pages = [p for p in starts if (p > here if delta > 0 else p < here)]
        if pages:
            self.go_block(starts[min(pages) if delta > 0 else max(pages)])

    def go_block(self, k):
        """La hoja al párrafo del bloque k, abriendo su capítulo si es otro, y el panel lo dibuja."""
        bounds = next(s[1:3] for s in self.sections if s[1] <= k < s[2])
        if bounds != self.current and self.web_on:  # la hoja web se vuelve a cargar en ese párrafo (web_ready)
            self.open_section(*bounds, k)
            return
        if bounds != self.current:
            self.open_section(*bounds)
        self.sheet.goto_source(k)
        self.sheet.ensureCursorVisible()
        self.draw_source()  # sin esperar a la pausa del cursor: el clic se ve al momento
        if self.web_on:
            self.web.call("goHome", self.web_index(self.sheet.textCursor().block()))

    def mark_changed(self):
        """Lista de capítulos: el de ahora elegido y «•» junto a los que tienen cambios sin guardar."""
        if not shiboken6.isValid(self.chapter_list):
            return
        self.chapter_list.clear()
        for n, (label, start, end, _) in enumerate(self.sections):
            page = self.pages.get((start, end))
            reviewed = self.app.tree.exists(str(start)) and self.app.tree.set(str(start), "ok")
            item = QListWidgetItem(("✓ " if reviewed else "") + label + ("  •" if page and self.page_dirty(page) else ""))
            item.setData(Qt.ItemDataRole.UserRole, n)
            item.setToolTip(label)
            self.chapter_list.addItem(item)
            if (start, end) == self.current:
                self.chapter_list.setCurrentItem(item)

    def pictures(self, start, end):
        """Miniaturas de las ilustraciones y adornos de la sección, y el texto de los separadores escritos."""
        found = {}
        # Ya leídas y reducidas, de una vez a otra del editor (registro.log: abrir un capítulo con láminas a color
        # congelaba la ventana 1,6 s leyéndolas enteras cada vez, para verlas de 160 px de alto)
        memo = self.app.__dict__.setdefault("picture_memo", {})
        for k in range(start, end):
            block = self.app.book.blocks[k]
            if block.kind in ("image", "break") and block.image:
                ink = self.colors["ink"] if block.kind == "break" else None
                key = (hash(block.image), len(block.image), ink)
                if key not in memo:
                    image = _scaled_jpeg(block.image, 1260, 3 * editor.THUMB) if ink is None else None
                    try:
                        # el adorno transparente, del color del texto: un símbolo más, no una foto con fondo blanco
                        image = image or qtk.qimage(pymupdf.Pixmap(block.image), ink)
                    except Exception:
                        continue  # imagen que MuPDF no lee: queda su marca
                    if image.height() > 3 * editor.THUMB or image.width() > 1260:  # 3 veces lo que se ve: nítida
                        image = image.scaled(1260, 3 * editor.THUMB, Qt.AspectRatioMode.KeepAspectRatio,
                                             Qt.TransformationMode.SmoothTransformation)
                    if len(memo) > 300:
                        memo.clear()
                    memo[key] = image
                found[k] = memo[key]
            elif block.kind == "break":
                found[k] = convert._plain(block).strip() or "❖"  # «<b>※ ・ ※</b>» de un EPUB: sin etiquetas
            elif block.kind == "heading" and block.image:  # título dibujado: con el texto de ahora (traducido)
                found[f"título {k}"] = QImage.fromData(convert.title_picture(block.image, convert._plain(block)))
        return found

    def page_width(self, percent=None):
        """Ancho máximo de la hoja: crece con la letra, como la página en Word."""
        return int(820 * (percent or self.text_zoom.value()) / 100)

    def set_text_zoom(self, percent):
        self.sheet.set_zoom(percent)
        share = self.page_share if self.focused else 1.0  # más ancha solo en pantalla completa
        self.sheet.setMaximumWidth(int(self.page_width(percent) * share))
        font = QFont("Segoe UI")
        font.setPointSizeF(max(6.0, 9 * percent / 100))  # el código, a la par
        self.code.setFont(font)
        self.text_zoom_label.setText(f"{percent} %")

    def on_desk_event(self, event):
        """En pantalla completa, arrastrar el borde de la hoja la ensancha (o la vuelve a estrechar), igual a los
        dos lados (va centrada): desde el ancho de la página hasta poco antes de las flechas de los lados."""
        kind = event.type()
        if not self.focused or kind not in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonPress,
                                            QEvent.Type.MouseButtonRelease):
            return False
        x = event.position().x()
        sheet = self.sheet.geometry()
        near = abs(x - sheet.left()) <= 10 or abs(x - sheet.right()) <= 10
        if kind == QEvent.Type.MouseButtonPress and near:
            self.dragging = True
            return True
        if kind == QEvent.Type.MouseButtonRelease:
            self.dragging = None
            return False
        if self.dragging:
            width = 2 * abs(x - self.desk.width() / 2)
            room = self.desk.width() - 2 * (self.focus_prev.width() + 40)  # la hoja no llega a las flechas
            self.page_share = max(1.0, min(width, room) / self.page_width())
            self.sheet.setMaximumWidth(int(self.page_width() * self.page_share))
            return True
        self.desk.setCursor(Qt.CursorShape.SizeHorCursor if near else Qt.CursorShape.ArrowCursor)
        return False

    def later(self, name, ms, fn):
        """Ejecuta fn tras ms milisegundos sin otro aviso del mismo nombre: no repinta en cada tecla."""
        if self.closed:  # al cerrarse, el panel aún avisa de su tamaño: repintaba sobre la ventana ya cerrada
            return
        if name in self.pending:
            qtk.cancel(self.pending[name])
        self.pending[name] = qtk.after(ms, lambda: (self.pending.pop(name, None), fn()))

    def on_destroy(self):
        LOG.info("Editor: se cierra")
        if self.app.text_editor is self:
            self.app.text_editor = None
        if shiboken6.isValid(self.sheet):  # al destruirse, la hoja aún avisaría de cambios del cursor
            self.sheet.blockSignals(True)
        for job in self.pending.values():
            qtk.cancel(job)
        self.pending.clear()
        self.closed = True
        QApplication.instance().removeEventFilter(self.side_keys)
        if self.web is not None and shiboken6.isValid(self.web):  # lo que Chromium conteste ya no tiene a quién
            self.web.closing = True
            self.web_poll.stop()
        if self.web is not None:
            try:
                QApplication.instance().focusChanged.disconnect(self.on_focus)
            except (RuntimeError, TypeError):
                pass
        self.drop_draft()  # se aceptó o se descartó: el borrador ya no hace falta
        # shortcut: tras aceptar, un párrafo añadido antes desplaza el bloque; se queda cerca, que basta para volver
        _log_work(self.app.work, datetime.now().date().isoformat(), paragraphs=self.saved_paragraphs,
                  minutes=(time.monotonic() - self.opened) / 60)
        if shiboken6.isValid(self.sheet) and self.sheet.current_source() is not None:
            self.app.editor_at = self.sheet.current_source()
        self.app.save_review()
        if self.spell:  # sigue vivo para la próxima vez (App.spell_process); lo aprendido ya está en App.words
            with contextlib.suppress(OSError, ValueError):
                self.spell[0].put(("reset",))
            self.spell = None
        if self.speaker:
            self.speaker.stop()

    # Leer en voz alta
    def toggle_reading(self):
        if self.reading is not None:
            self.stop_reading()
            return
        if self.speaker is None:
            self.speaker = Speaker()
            self.speaker.done.connect(self.read_next)
            self.speaker.failed.connect(self.reading_failed)
        if self.web_on:
            self.web_read(-1)
        else:
            self.read_block(self.sheet.textCursor().block())

    def read_next(self):
        """Acabó un párrafo: el siguiente, en la hoja o en la hoja web (donde self.reading es su número)."""
        if isinstance(self.reading, int):
            self.web_read(self.reading + 1)
        elif self.reading is not None:
            self.read_block(self.reading.next())

    def web_read(self, i):
        """Lee el párrafo i de la hoja web (-1: el del cursor), o el siguiente con texto, de fondo y con el cursor
        al principio; al acabar el capítulo, para."""
        def got(data):
            info = json.loads(data) if data else None
            if not info or not self.web_on:
                self.stop_reading()
                return
            self.reading = info["i"]
            self.read_btn.setChecked(True)
            self.speaker.say(info["text"])
        self.web.call("readAt", i, theme.mark_colors(self.app.dark)["reading"], done=got)

    def read_block(self, block):
        """Lee el párrafo block (o el siguiente con texto) con el cursor al principio y su fondo marcado; al acabar
        el capítulo, para."""
        while block.isValid() and not block.text().replace("\ufffc", "").strip():  # ilustraciones, separadores
            block = block.next()
        if not block.isValid():
            self.stop_reading()
            return
        self.reading = block
        self.sheet.setTextCursor(QTextCursor(block))
        self.sheet.ensureCursorVisible()
        self.read_btn.setChecked(True)
        self.refresh_marks()
        self.speaker.say(block.text().replace("\ufffc", " "))

    def stop_reading(self):
        self.reading = None
        if self.speaker:
            self.speaker.stop()
        if self.web_on:
            self.web.call("stopReading")
        if shiboken6.isValid(self.read_btn):
            self.read_btn.setChecked(False)
            self.refresh_marks()

    def reading_failed(self, message):
        self.stop_reading()
        messagebox.showinfo("Leer en voz alta", message, parent=self.win)

    def on_panel_event(self, event):
        """Ctrl+rueda amplía la página; al cambiar el tamaño del panel se vuelve a ajustar."""
        if event.type() == QEvent.Type.Wheel and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom(25 if event.angleDelta().y() > 0 else -25)
            return True
        if event.type() == QEvent.Type.Resize:
            self.later("panel", 80, self.redraw)
        return False

    def zoom(self, delta):
        """− y + del panel: el zoom de la pestaña a la vista (original, EPUB u hoja web)."""
        if self.web_tab():
            self.web_zoom = min(300, max(50, self.web_zoom + delta))
            self.web.scale = self.web_zoom / 100
            self.web.fit()
            self.zoom_label.setText(f"{self.web_zoom} %")
            return
        if self.web_shown():
            self.epub_zoom = min(300, max(50, self.epub_zoom + delta))
            self.side_web.scale = self.epub_zoom / 100
            self.side_web.fit()
            self.zoom_label.setText(f"{self.epub_zoom} %")
            return
        self.pdf_zoom = min(3.0, max(0.5, self.pdf_zoom + delta / 100))
        self.zoom_label.setText(f"{round(self.pdf_zoom * 100)} %")
        self.redraw()

    def set_side_dark(self, on):
        """Modo oscuro de las vistas del panel: se vuelven a dibujar el original y el EPUB, y la hoja web lo pone."""
        self.side_dark = on
        self.app.settings["editor_dark"] = on
        self.app.save_settings()
        self.drawn = None
        self.redraw()
        if self.web_shown():
            self.draw_web()
        if self.web is not None:
            self.web.set_dark(on)

    def show_web_page(self, view):
        """«Página n de m» de la pestaña EPUB u hoja web: lo que cabe en la vista es una página."""
        self.page_label.setText(f"Página {view.page_ + 1} de {view.pages}")

    def redraw(self):
        """Vuelve a dibujar lo que muestra el panel con su tamaño y zoom de ahora."""
        if self.shown and self.shown[0] == "image":
            self.drawn = None
            self.draw_source()
        elif self.shown:
            self.show_page(self.page)

    def panel_width(self):
        return max(200, self.scroll.viewport().width() - 4)

    def source_block(self):
        """Bloque del libro del párrafo del cursor (o de su ilustración); sin ninguno, el título de la sección."""
        k = self.sheet.current_source()
        return k if k is not None and k < len(self.app.book.blocks) else int(self.item)

    def source_key(self):
        """(bloque del cursor, bloques unidos a su párrafo en la misma página, PDF): lo que dibuja el panel. Los
        párrafos unidos en la hoja se recuadran todos, no solo el primero. Al final, el texto del párrafo si no es
        el de su bloque (uno que ya no está en el libro y volvió con Ctrl+Z: draw_source lo busca en la página)."""
        k, blocks = self.source_block(), self.app.book.blocks
        joined = [j for j in self.sheet.current_sources()[1:] if j < len(blocks) and blocks[j].page == blocks[k].page]
        lines = editor.block_lines(self.sheet.textCursor().block())
        text = editor.plain_line(lines[0]) if lines else ""
        stray = text if text.split() and text.split() != convert._plain(blocks[k]).split() else None
        return k, joined, id(self.app.pdf_doc), stray

    def draw_source(self):
        """Panel: la ilustración del bloque del cursor, o su página del PDF con el párrafo recuadrado."""
        key = self.source_key()
        k, joined = key[:2]
        blocks = self.app.book.blocks
        block = blocks[k]
        if key == self.drawn:  # mismo párrafo: la página ya está dibujada
            self.update_status()
            return
        self.drawn = key
        self.source_note.setVisible(self.pictured(block))
        if block.kind == "image" and block.image:
            pix = pymupdf.Pixmap(block.image)
            scale = min(self.panel_width() * self.pdf_zoom / pix.width, 1.5)
            if scale != 1:
                pix = pymupdf.Pixmap(pix, max(1, round(pix.width * scale)), max(1, round(pix.height * scale)), None)
            self.shown, self.box, self.more, self.page = ("image", k), None, None, block.page - 1
            self.put(pix)
            self.page_label.setText(f"Ilustración · pág. {block.page}")
        elif self.app.pdf_doc is None:
            self.shown, self.box, self.more = None, None, None
            size = (self.panel_width(), int(self.panel_width() * 1.4))
            self.canvas.setFixedSize(*size)
            self.canvas.show_blank(size, "Sin el PDF original no se puede mostrar la página",
                                   theme.colors(self.app.dark))
            self.page_label.setText("")
        else:
            index = self.source_page(k)
            page = self.app.pdf_doc[index]
            boxes = [pymupdf.Rect(b.rect) if b.rect else self.find_box(page, b)
                     for b in [block] + [blocks[j] for j in joined]]
            boxes = [box for box in boxes if box]
            self.box = boxes[0] if boxes else None
            for box in boxes[1:]:
                self.box |= box
            # El párrafo que sigue en la página siguiente (Backstabbed 1, pág. 96): esa página va debajo y su
            # trozo también se recuadra. En un EPUB las páginas son de MuPDF, no del libro: no se busca
            more = None if self.app.book.origin else self.continuation(([block] + [blocks[j] for j in joined])[-1])
            self.more = (block.page, more) if more else None
            stray = key[3] and self.find_box(page, convert.Block("p", block.page, html.escape(key[3], quote=False)))
            if stray:  # el párrafo tal como está en la hoja: el de su bloque recuadraba otro
                self.box, self.more = stray, None
            self.show_page(index)
        self.update_status()

    def source_page(self, k):
        """Página (desde 0) del original en la que está el bloque k. En un PDF, la suya. Un bloque de un EPUB solo
        sabe la primera página de su archivo (faithful._first_pages): su texto se busca en las de ese archivo."""
        block, doc = self.app.book.blocks[k], self.app.pdf_doc
        start = block.page - 1
        if not self.app.book.origin or doc is None:
            return start
        if block.kind not in ("p", "heading"):  # ilustración o separador: la página del párrafo que le sigue
            if k not in self.page_cache:
                self.page_cache[k] = _epub_block_page(self.app.book, doc, k)
            return self.page_cache[k]
        if k not in self.page_cache:
            if self.file_starts is None:  # primera página de cada archivo, en orden
                self.file_starts = sorted({b.page for b in self.app.book.blocks})
            after = next((p - 1 for p in self.file_starts if p > block.page), doc.page_count)
            # desde la del párrafo anterior del mismo archivo: van en orden
            near = self.page_cache.get(k - 1, start) if k and self.app.book.blocks[k - 1].page == block.page else start
            self.page_cache[k] = next((i for i in range(near, min(after, doc.page_count))
                                       if self.find_box(doc[i], block)), start)
        return self.page_cache[k]

    def continuation(self, block):
        """Recuadro de lo que el párrafo block sigue en la página siguiente, o None si acaba en la suya."""
        doc, words = self.app.pdf_doc, convert._plain(block).split()
        if block.kind != "p" or len(words) < 6 or block.page >= doc.page_count:
            return None
        # Seis palabras del final; si lo que sigue es más corto, tres (las seis cruzan de página y no se hallan)
        hits = next((hits for tail in (" ".join(words[-6:]), " ".join(words[-3:]))
                     if (hits := doc[block.page].search_for(tail)) and not doc[block.page - 1].search_for(tail)), None)
        if not hits:
            return None
        end = max(r.y1 for r in hits if r.y0 < hits[0].y1 + 2 * hits[0].height)  # el final puede ir en dos líneas
        known, box = {w.lower() for w in words}, None
        lines = [line for b in doc[block.page].get_text("dict")["blocks"] if b["type"] == 0 for line in b["lines"]]
        for line in sorted(lines, key=lambda line: line["bbox"][1]):
            rect = pymupdf.Rect(line["bbox"])
            if rect.y0 > end:
                break
            # Desde la primera línea con las palabras del párrafo: la cabecera de la página no
            text = "".join(span["text"] for span in line["spans"]).lower().split()
            if box is None and not (text and sum(w in known for w in text) >= 0.8 * len(text)):
                continue
            box = rect if box is None else box | rect
        return box

    def pictured(self, block):
        """¿En el PDF el título es una imagen? Su recuadro no tiene texto (lo leyó el OCR) o, sin recuadro (lo
        puso el marcador del PDF, Witch 1), su texto no está en la página y la página lleva imágenes."""
        if self.app.pdf_doc is None or block.kind != "heading":
            return False
        page = self.app.pdf_doc[block.page - 1]
        if block.rect:
            return not page.get_text("text", clip=pymupdf.Rect(block.rect)).strip()
        words = convert._plain(block).split()[:4]
        return bool(words and page.get_images() and not page.search_for(" ".join(words)))

    def show_page(self, index):
        """Dibuja la página index (desde 0) del PDF al ancho del panel por el zoom, con self.box recuadrado; si el
        párrafo sigue en la siguiente (self.more), esa va debajo con su trozo recuadrado."""
        page = self.app.pdf_doc[index]
        self.page, self.shown = index, ("page", index + 1)
        zoom = self.page_zoom = self.panel_width() / page.rect.width * self.pdf_zoom
        matrix = pymupdf.Matrix(zoom, zoom)

        def mark(box, top=0):
            box = box * zoom
            return QRectF(box.x0 - 3, top + box.y0 - 3, box.width + 6, box.height + 6), MARK_COLORS["current"]

        marks = [mark(self.box)] if self.box else []
        image = qtk.qimage(_page_pixmap(page, matrix, self.side_dark))
        self.below = None  # (página de debajo, dónde empieza en píxeles)
        if self.more and self.more[0] == index + 1:
            second = qtk.qimage(_page_pixmap(self.app.pdf_doc[index + 1], matrix, self.side_dark))
            top = image.height() + PAGE_GAP
            both = QImage(max(image.width(), second.width()), top + second.height(), QImage.Format.Format_ARGB32)
            both.fill(Qt.GlobalColor.transparent)
            painter = QPainter(both)
            painter.drawImage(0, 0, image)
            painter.drawImage(0, top, second)
            painter.end()
            image, self.below = both, (index + 1, top)
            marks.append(mark(self.more[1], top))
        self.canvas.setFixedSize(image.width(), image.height())
        self.canvas.show_image((image.width(), image.height()), image, marks)
        if self.box:  # el párrafo a la vista aunque la página no quepa
            self.scroll.ensureVisible(int(self.box.x0 * zoom), int((self.box.y0 + self.box.y1) / 2 * zoom), 40,
                                      self.scroll.viewport().height() // 3)
        self.page_label.setText(f"Página {index + 1} de {self.app.pdf_doc.page_count}")

    def put(self, pix, marks=()):
        """Dibuja el pixmap en el panel, a su tamaño (con scroll si no cabe)."""
        image, self.below = qtk.qimage(pix), None
        self.canvas.setFixedSize(image.width(), image.height())
        self.canvas.show_image((image.width(), image.height()), image, marks)

    def on_canvas_event(self, event):
        """Clic en la página del panel: la hoja va a ese párrafo; Ctrl+C copia el recuadrado; clic derecho, el menú."""
        kind = event.type()
        if kind == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            self.pick_source(event.position().toPoint())
        elif kind == QEvent.Type.ContextMenu:
            self.pick_source(event.pos())
            self.source_menu(event.pos()).popup(event.globalPos())
            return True
        elif kind == QEvent.Type.KeyPress and event.matches(QKeySequence.StandardKey.Copy):
            if self.shown and self.shown[0] == "image":
                self.copy_image(pymupdf.Pixmap(self.app.book.blocks[self.shown[1]].image))
            elif self.box:
                self.copy_text(self.source_text())
            return True
        return False

    def source_point(self, pos):
        """(página desde 0, punto en ella) bajo pos del panel; None si el panel no muestra una página."""
        if not self.shown or self.shown[0] != "page":
            return None
        index, y = self.page, pos.y()
        if self.below and y >= self.below[1]:
            index, y = self.below[0], y - self.below[1]
        return index, pymupdf.Point(pos.x() / self.page_zoom, y / self.page_zoom)

    def pick_source(self, pos):
        """La hoja al párrafo o título del libro bajo pos, si lo hay en la página mostrada; también el que viene de
        la página anterior y sigue arriba de esta."""
        spot = self.source_point(pos)
        if spot is None:
            return
        index, point = spot
        page, blocks = self.app.pdf_doc[index], self.app.book.blocks
        if self.app.book.origin:  # un EPUB: el párrafo cuyo texto lleva la línea pulsada
            every = page.get_text("words")  # (x0, y0, x1, y1, palabra, bloque, línea, n)
            hit = min(every, key=lambda w: abs((w[1] + w[3]) / 2 - point.y), default=None)
            line = [w for w in every if hit and w[5:7] == hit[5:7]]  # la línea más cercana al clic, entera
            words = " ".join(w[4] for w in sorted(line, key=lambda w: w[0])[:5])
            if words:
                found = [k for _, start, end, _ in self.sections for k in range(start, end)
                         if blocks[k].kind in ("p", "heading") and blocks[k].page <= index + 1
                         and words in " ".join(convert._plain(blocks[k]).split())]
                # del archivo de esa página (el de primera página más alta); si la línea se repite («—Sí.»), el que
                # en esta página se recuadra donde se pulsó
                found = [k for k in found if blocks[k].page == max(blocks[j].page for j in found)]
                k = next((k for k in found if (box := self.find_box(page, blocks[k]))
                          and (box + (-4, -4, 4, 4)).contains(point)), found[0] if found else None)
                if k is not None:
                    self.page_cache[k] = index
                    self.go_block(k)
            return
        last = None  # último párrafo de la página anterior: puede seguir en esta
        for _, start, end, _ in self.sections:
            for k in range(start, end):
                block = blocks[k]
                if block.page == index and block.kind == "p":
                    last = k
                if block.page == index + 1 and block.kind in ("p", "heading"):
                    box = pymupdf.Rect(block.rect) if block.rect else self.find_box(page, block)
                    if box and (box + (-4, -4, 4, 4)).contains(point):
                        self.go_block(k)
                        return
        if last is not None:
            box = self.continuation(blocks[last])
            if box and (box + (-4, -4, 4, 4)).contains(point):
                self.go_block(last)

    def box_text(self, index, box):
        """Texto del PDF en la franja de box de la página index (a lo ancho de la página: la primera línea del
        párrafo puede ser más corta que las demás), en una línea y sin los guiones de partición."""
        page = self.app.pdf_doc[index]
        clip = pymupdf.Rect(0, box.y0 - 1, page.rect.width, box.y1 + 1)
        return " ".join(page.get_text("text", clip=clip, flags=pymupdf.TEXTFLAGS_TEXT | pymupdf.TEXT_DEHYPHENATE).split())

    def source_text(self):
        """Texto del párrafo recuadrado, con lo que sigue en la página siguiente."""
        parts = [(self.page, self.box)] if self.box else []
        if self.below and self.more:
            parts.append(self.more)
        return " ".join(self.box_text(index, box) for index, box in parts)

    def source_menu(self, pos):
        """Menú del clic derecho en el panel: copiar, nunca cambiar, el texto o la imagen del original."""
        menu = QMenu(self.canvas)
        if self.shown and self.shown[0] == "image":
            menu.addAction("Copiar imagen", lambda: self.copy_image(
                pymupdf.Pixmap(self.app.book.blocks[self.shown[1]].image)))
            return menu
        spot = self.source_point(pos)
        if spot is None:
            return menu
        index, point = spot
        page = self.app.pdf_doc[index]
        text = self.source_text()
        copy = menu.addAction("Copiar texto del párrafo\tCtrl+C", lambda: self.copy_text(text))
        copy.setEnabled(bool(text))
        menu.addAction("Copiar texto de la página", lambda: self.copy_text(page.get_text("text").strip()))
        # La imagen bajo el puntero; en un título hecho imagen, su recuadro
        spot = next((pymupdf.Rect(info["bbox"]) for info in page.get_image_info()
                     if pymupdf.Rect(info["bbox"]).contains(point)), None)
        if spot is None and self.box and not text and index == self.page:
            spot = self.box
        menu.addSeparator()
        if spot is not None:
            spot = spot & page.rect  # nuevo Rect: &= cambiaría self.box
            menu.addAction("Copiar imagen", lambda: self.copy_image(page.get_pixmap(matrix=pymupdf.Matrix(3, 3),
                                                                                     clip=spot)))
        menu.addAction("Copiar la página como imagen",
                       lambda: self.copy_image(page.get_pixmap(matrix=pymupdf.Matrix(2, 2))))
        return menu

    def copy_text(self, text):
        QApplication.clipboard().setText(text)
        self.note.setText("Texto del original copiado")

    def copy_image(self, pix):
        QApplication.clipboard().setImage(qtk.qimage(pix))
        self.note.setText("Imagen del original copiada")

    @staticmethod
    def find_box(page, block):
        """Recuadro del párrafo en su página, de sus 6 primeras palabras a sus 6 últimas. None si el texto no
        aparece tal cual (palabra partida con guion en el PDF, párrafo ya editado)."""
        text = _source_plain(block)
        words = (text if page.parent.is_pdf else text.translate(convert.PREVIEW_TABLE)).split()  # EPUB: «・» es «·»
        if convert.CJK.search(text):  # japonés: sin espacios, el párrafo entero era una «palabra» que cruza líneas
            return _cjk_box(page.parent, page.number, "".join(words))
        hits = []
        for r in page.search_for(" ".join(words[:6])) if words else []:
            # letra a letra cuando cambia la fuente («※ · ※»: de reserva cada uno): los trozos seguidos, uno
            if hits and abs(r.x0 - hits[-1].x1) < 1 and r.y0 < hits[-1].y1 and r.y1 > hits[-1].y0:
                hits[-1] |= r
            else:
                hits.append(r)
        if not hits or (len(hits) > 1 and len(words) <= 6):  # línea corta repetida ("—Sí."): no se sabe cuál
            return None
        box = hits[0]
        tail = [r for r in page.search_for(" ".join(words[-6:])) if r.y0 >= box.y0]
        for n, r in enumerate(tail):  # unas palabras que cruzan de línea dan una pieza por línea
            if n and r.y0 > box.y1 + r.height:  # otra aparición más abajo
                break
            box |= r
        # Todo el ancho de sus líneas: la primera (sangrada) y la última pueden ser más cortas que las de en medio
        top, bottom = box.y0, box.y1
        for x0, y0, x1, y1, *_ in page.get_text("words"):
            middle = (y0 + y1) / 2
            if top <= middle <= bottom:
                box |= pymupdf.Rect(x0, y0, x1, y1)
        return box

    def update_status(self):
        """«Página 34 del PDF · párrafo 12 de 80 · 3.412 palabras»."""
        count, current, words = self.sheet.paragraphs()
        where = f"párrafo {current} de {count}" if current else f"{count} párrafo{'' if count == 1 else 's'}"
        page = self.app.book.blocks[self.source_block()].page
        starts = self.page_starts()
        self.prev_page.setEnabled(any(p < page for p in starts))
        self.next_page.setEnabled(any(p > page for p in starts))
        source = "del original" if self.app.book.origin else "del PDF"
        if self.app.book.origin:  # la página en la que está, no la primera de su archivo
            page = self.source_page(self.source_block()) + 1
        self.status.setText(f"Página {page} {source} · {where} · "
                            f"{words:,} palabra{'' if words == 1 else 's'}".replace(",", "."))

    def page_dirty(self, page, text=None):
        """¿Cambió algo que edit_section lea en esa sección? Líneas en blanco y espacios sobrantes no cuentan.
        text: su dump, si ya se tiene."""
        def lines(text):
            return [line.strip() for line in text.splitlines() if line.strip()]
        text = self.sheet.dumped(page["doc"]) if text is None else text
        return lines(text) != lines("\n\n".join(line for _, line in page["lines"]))

    def sync_web(self):
        """Pregunta a la hoja web si tiene cambios ahora mismo (como mucho 1 s): cerrar la ventana no espera al
        sondeo, y lo escrito en el último medio segundo no contaba."""
        if not self.web_on:
            return
        loop = QEventLoop()
        self.web.call("state", done=lambda st: (self.web_polled(st), loop.quit()))
        QTimer.singleShot(1000, loop.quit)
        loop.exec()

    def dirty(self):
        """¿Hay algo sin guardar? También lo escrito en la hoja web y aún no volcado (según su último sondeo)."""
        return ((self.web_on and self.web_dirty) or bool(self.shells) or (self.code.isVisible() and self.shell_changed())
                or any(self.page_dirty(page) for page in self.pages.values()))

    def changes(self):
        """Lo que cambió en las secciones visitadas, línea a línea frente a lo cargado: [[inicio, fin, texto]]
        de atrás adelante para edit_section, y {fila: título} de los títulos solo renombrados (conservan su
        tipo y su ✓)."""
        edits, titles, kinds, retype = [], {}, {}, {}
        for (start, _), page in self.pages.items():
            old = [line for _, line in page["lines"]]
            source = [k for k, _ in page["lines"]]  # bloque de cada línea cargada (en «Contenido», salteados)
            pairs = editor.dump_levels(page["doc"], self.sheet.all_lines(page["doc"]))  # [(línea, tipo o None)]
            new = [line for line, _ in pairs]
            for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
                k = str(source[i1]) if i1 < len(source) else str(start + i1)
                if tag == "equal":  # un título igual puede haber cambiado de tipo (capítulo ↔ subtítulo)
                    for n in range(i2 - i1):
                        level, was = pairs[j1 + n][1], page["levels"].get(source[i1 + n])
                        if level in (ON, OFF) and was is not None and level != was:
                            retype[str(source[i1 + n])] = level
                    continue
                if (tag == "replace" and i2 - i1 == j2 - j1 == 1 and old[i1].startswith("# ")
                        and new[j1].startswith("# ") and self.app.tree.exists(k)):
                    titles[k] = new[j1][2:].strip()
                    if pairs[j1][1] in (ON, OFF):
                        retype[k] = pairs[j1][1]
                elif start < 0:  # en «Contenido» solo se renombra: añadir o quitar líneas no toca el libro
                    continue
                else:
                    edits.append([start + i1, start + i2, "\n\n".join(new[j1:j2])])
                    kinds[start + i1] = [level for line, level in pairs[j1:j2] if editor.is_heading_line(line)]
        edits.sort(reverse=True)
        return edits, titles, [kinds[e[0]] for e in edits], retype

    def changed_count(self):
        """Párrafos cambiados, nuevos o quitados en las secciones visitadas, frente a lo cargado."""
        count = 0
        for page in self.pages.values():
            old = [line for _, line in page["lines"]]
            new = [line for line in self.sheet.dumped(page["doc"]).split("\n\n") if line.strip()]
            count += sum(max(i2 - i1, j2 - j1) for op, i1, i2, j1, j2 in
                         difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes() if op != "equal")
        return count

    def save(self, then=None):
        """Aplica solo lo que cambió: un párrafo rehecho pierde su página del PDF, y un título rehecho su tipo.
        then: lo que se hace con el editor ya cerrado (la página nueva)."""
        if getattr(self, "web_on", False):  # lo escrito en la hoja web, primero a la hoja
            self.flush_web(lambda: (self.web_done(), self.save(then)))
            return
        if self.code.isVisible() and not self.take_shell():
            return
        self.keep_history()
        if self.dirty():
            self.saved_paragraphs = self.changed_count()
            edits, titles, kinds, retype = self.changes()
            LOG.info("Editor: guarda %d cambios", len(edits))
            self.app.apply_edits(edits, self.item, titles, follow=True, kinds=kinds, retype=retype,
                                 heads=[(start, shell) for (start, _), shell in self.shells.items()])
        self.win.done(0)
        if then:
            then()

    def keep_history(self):
        """Al aceptar: el deshacer y el rehacer de cada capítulo tocado van a la revisión (replay_history)."""
        for (start, _), page in self.pages.items():
            doc = page["doc"]
            if start < 0 or page.get("mark") == (doc.availableUndoSteps(), doc.availableRedoSteps()):
                continue
            loaded = "\n\n".join(line for _, line in page["lines"])
            versions, at = undo_versions(doc, self.sheet.dumped)
            old = history_entry(self.app.editor_history, loaded)
            history_add(self.app.editor_history, versions, at, old[2] if old else versions[0], loaded)

    def new_page(self):
        """Insertar → «Página nueva…» (créditos, nota del traductor): se guarda lo editado, se crea la página tras
        este capítulo, antes del primero o al final, y el editor se abre en ella."""
        blocks, (start, end) = self.app.book.blocks, self.current
        follows = blocks[end] if start >= 0 and end < len(blocks) else None  # el bloque que va tras este capítulo

        def create(name, lines, where):
            def after_save():
                app, book = self.app, self.app.book
                if where == "Tras este capítulo":
                    # el mismo bloque (guardar no toca lo que sigue al capítulo), o el final del libro
                    at = next((i for i, b in enumerate(book.blocks) if b is follows), len(book.blocks)) \
                        if follows is not None else len(book.blocks)
                else:
                    at = None if where.startswith("Antes") else len(book.blocks)
                k = app.insert_page(name, lines, at=at)
                app.tree.selection_set(str(k))
                TextEditor(app, str(k), *app.section_bounds(k))
            self.save(after_save)
        self.app.add_page(parent=self.win, choices=["Tras este capítulo", "Antes del primer capítulo",
                                                     "Al final del libro"] if start >= 0 else None, done=create)

    def cancel(self, _=None, asked=False):
        if self.web_on and not asked:  # lo último escrito en la web cuenta: se pregunta a la hoja antes
            self.web.call("state", done=lambda st: (self.web_polled(st), self.cancel(asked=True)))
            return
        if self.dirty() and not messagebox.askyesno(
                "Descartar cambios", "El texto tiene cambios sin guardar. ¿Descartarlos?", parent=self.win):
            return
        self.win.done(0)


def _log_start():
    """Primera línea de cada sesión en el registro: versiones y si hay Chromium, lo que explica muchos fallos. Los
    avisos de Qt y de Chromium, que en el .exe no tenían dónde salir, también van al registro."""
    import platform

    import PySide6
    from PySide6.QtCore import QtMsgType, qInstallMessageHandler

    import webview
    levels = {QtMsgType.QtDebugMsg: logging.DEBUG, QtMsgType.QtInfoMsg: logging.INFO,
              QtMsgType.QtWarningMsg: logging.WARNING, QtMsgType.QtCriticalMsg: logging.ERROR,
              QtMsgType.QtFatalMsg: logging.CRITICAL}
    qt_log = logging.getLogger("qt")
    qInstallMessageHandler(lambda kind, context, text: qt_log.log(levels.get(kind, logging.INFO), "%s", text))
    freezes, beat = registro.Freezes(), QTimer()  # la ventana que no responde: cuánto y dónde (registro.Freezes)
    beat.timeout.connect(freezes.beat)
    beat.start(100)
    _log_start.keep, _log_start.freezes = beat, freezes  # vivos mientras dure la app
    qtk.log_actions()  # clics, atajos y ventanas: lo que pasó antes de un fallo
    chromium = "sí" if webview.available() else f"no ({webview.ERROR or 'apagado'})"
    screen = qtk.app().primaryScreen()
    LOG.info("=== %s · %s · Python %s · Qt %s · PyMuPDF %s · %s · Chromium: %s · pantalla %s a %d %% · %s",
             APP_NAME, "exe" if getattr(sys, "frozen", False) else "fuente", platform.python_version(),
             PySide6.__version__, pymupdf.VersionBind, platform.platform(), chromium,
             f"{screen.size().width()}×{screen.size().height()}" if screen else "?",
             round(100 * screen.devicePixelRatio()) if screen else 100,
             f"argumentos {sys.argv[1:]}" if sys.argv[1:] else "sin argumentos")
    LOG.debug("Preferencias en %s%s", SETTINGS.parent, " (portable)" if SETTINGS.parent.name == "datos" else "")


def _check_chromium(report):
    """Carga una página de prueba en la vista web y escribe en report «ok N» (sus páginas) o el error. 0 si funcionó.
    Sin esto, un .exe sin alguna DLL de Chromium caía en silencio a la vista de MuPDF."""
    import io

    import webview
    if not webview.available():
        Path(report).write_text(f"sin Chromium: {webview.ERROR}", encoding="utf-8")
        return 1
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as z:
        z.writestr("p.xhtml", '<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml"><head>'
                   '<title>p</title></head><body><p>Prueba de Chromium.</p></body></html>')
    view = webview.EpubView()
    view.resize(300, 400)
    result = []
    view.paged.connect(result.append)
    view.show_file(data.getvalue(), "p.xhtml", (300, 400))
    qtk.after(30000, lambda: result.append(-1))
    while not result:
        qtk.app().processEvents()
        time.sleep(0.02)
    Path(report).write_text(f"ok {result[0]}" if result[0] > 0 else "Chromium no cargó la página", encoding="utf-8")
    return 0 if result[0] > 0 else 1


if __name__ == "__main__":
    multiprocessing.freeze_support()  # en el .exe, el proceso de trabajo vuelve a lanzar el ejecutable
    sys.excepthook = lambda kind, value, tb: _log("".join(traceback.format_exception(kind, value, tb)), tb)
    registro.setup(SETTINGS.parent)
    qtk.app()
    _log_start()
    if sys.argv[1:2] == ["--comprobar-chromium"]:  # /build-exe: que el .exe trae Chromium entero y dibuja una página
        sys.exit(_check_chromium(sys.argv[2] if len(sys.argv) > 2 else "chromium.txt"))
    root = Window()
    window = App(root)
    root.show()
    if len(sys.argv) > 1:  # PDF arrastrado sobre el .exe o "Abrir con…"
        qtk.after(200, window.open_path, sys.argv[1])
    elif window.settings.get("reopen_last"):  # Opciones → Inicio
        last = next((p for p in window.settings.get("recent", []) if isinstance(p, str) and Path(p).is_file()), None)
        if last:
            qtk.after(200, window.open_path, last)
    sys.exit(qtk.app().exec())
