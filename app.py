"""Ventana para convertir PDFs de novelas ligeras a EPUB."""
import base64
import collections
import concurrent.futures
import contextlib
import copy
import ctypes
import dataclasses
import difflib
import hashlib
import html
import http.server
import io
import json
import multiprocessing
import os
import pickle
import queue
import re
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
from PySide6.QtCore import QEvent, QObject, QProcess, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QAction, QColor, QFont, QFontMetrics, QIcon, QImage, QKeySequence, QPainter, QPainterPath,
                           QPixmap, QShortcut, QTextCharFormat, QTextCursor, QTextLayout)
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout,
                               QInputDialog, QTreeWidget, QTreeWidgetItem,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QPlainTextEdit,
                               QProgressBar,
                               QAbstractItemView, QButtonGroup, QHeaderView, QStyle, QStyledItemDelegate,
                               QStyleOptionViewItem, QTableWidget, QTableWidgetItem, QTabWidget,
                               QPushButton, QScrollArea, QSizePolicy, QSlider, QSplitter, QStackedWidget, QTabBar,
                               QLayout, QTextEdit, QToolButton, QVBoxLayout, QWidget, QWidgetAction)

import convert
import editor
import options
import qtk
import theme
import translate
from qtk import filedialog, messagebox

ON, OFF, CUT = "Capítulo", "Subtítulo", "Quitar"  # tipo de cada título de la lista
SEP = "Separador"                                   # título que se escribe como separador de escena
STYLES = ["Normal", "Capítulo", "Subtítulo"]          # estilos de párrafo en la cinta del editor de texto
STYLE_LEVELS = [None, "Capítulo", "Subtítulo"]
FRONT = "Inicio del libro"                           # lo que va antes del primer capítulo, en el editor
CHAPTER_WIDTHS = (300, 180, 170, 140)  # botón de capítulo del editor en cada nivel de la cinta (fit_ribbon)
GLYPH = 24                                           # px del glifo de un botón grande de la cinta, como en Word
NEXT = {ON: OFF, OFF: SEP, SEP: CUT, CUT: ON}       # cada clic pasa al siguiente
IMG, DROPPED = "Ilustración", "Descartada"           # tipo de las filas de imagen
IMAGE_NEXT = {IMG: CUT, CUT: IMG, DROPPED: IMG}      # clic en una imagen: se quita o se recupera
SPLIT, ROTATED = "Dividida", "Girada"                # ilustración doble: en dos páginas o girada 90°
WIDE_NEXT = {IMG: SPLIT, SPLIT: ROTATED, ROTATED: CUT, CUT: IMG, DROPPED: IMG}
LAYOUT = {SPLIT: "split", ROTATED: "rotate"}         # tipo de la fila -> Block.layout
KIND_TAG = {ON: "chapter", OFF: "subtitle", CUT: "cut", IMG: "image", DROPPED: "dropped",
            SPLIT: "image", ROTATED: "image", SEP: "separator"}  # estilo por tipo
KEYS = {"c": ON, "s": OFF, "q": CUT, "i": IMG, "d": SPLIT, "g": ROTATED, "x": SEP}  # atajos de teclado en la lista
LANGUAGES = ["es", "en", "pt", "fr", "it", "ja"]     # idioma del EPUB: lo usan la partición de palabras y la voz
LANG = re.compile(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{1,8})*")  # el idioma va tal cual en el XML del EPUB
# Lo que comparten los tomos de una serie: al abrir un tomo sin revisión se toma de otro ya revisado
INHERITED = ("author", "publisher", "translator", "illustrator", "subjects", "lang", "style", "separator",
             "typography", "shrink", "shrink_size", "gray", "trim", "font", "font_file", "epub2", "reading",
             "credits", "profile", "replacements", "kepub", "words", "hyphenate", "glossary", "tr_glossary", "tr_notes")
# De INHERITED, lo que es de toda la serie aunque el tomo ya tenga revisión: glosarios, diccionario y personajes
SERIES_VOCAB = ("words", "glossary", "tr_glossary", "tr_notes")
# Preferencias de la ventana entre sesiones (opciones, última carpeta y PDF recientes)
SETTINGS = Path(os.environ.get("APPDATA") or Path.home()) / "PDF a EPUB" / "ajustes.json"
RECENT = 8  # PDF recientes que recuerda el menú
BOOK_FIELDS = ("title", "author", "series", "volume", "publisher", "translator", "description", "illustrator",
               "proofreader", "editor", "original_title", "subjects", "date")  # datos del libro en la revisión
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
APP_NAME = "Light Novel Creator"  # nombre visible; el .exe y la carpeta de ajustes siguen siendo «PDF a EPUB»
KIND_COLOR = theme.kind_colors(False)  # colores de cada tipo de fila; en oscuro, theme.kind_colors(True)
PREVIEW_BOX = (480, 640)  # ancho y alto máximos de cada vista previa, en píxeles
PREVIEW_CHROME = 40       # alto de la fila ◀ leyenda ▶ de cada vista previa
DEVICES = {"Celular": (300, 560), 'E-reader 6"': (420, 560), "Tablet": (560, 750)}  # página en puntos
THUMB_HEIGHT = 64         # alto de las miniaturas del PDF, en píxeles
THUMBS_PER_TICK = 6       # miniaturas por tanda: dibujarlas todas de golpe congelaría la ventana
SUGGEST_AHEAD = 40        # sugerencias de ortografía pedidas por adelantado en cada capítulo
MARK_COLORS = {"title": "#1f6fd1", "image": "#2e9d4a", "current": "#f08c00"}  # bordes de las miniaturas
ICON = Path(__file__).parent / "icon.ico"  # en el .exe, junto a los módulos (--add-data icon.ico;.)
# Aspecto de las marcas en el editor de texto (theme.editor_tags); "broken" es lo que edit_section quitaría
LEVEL_COLOR = theme.level_colors(False)  # avisos del informe de calidad; en oscuro, theme.level_colors(True)
LEVEL_NAMES = {"Grave": ("grave", "graves"), "Revisar": ("por revisar", "por revisar"),
               "Menor": ("menor", "menores")}  # singular y plural: «1 grave», «2 graves»
LEVEL_PLURAL = {level: names[1] for level, names in LEVEL_NAMES.items()}
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


def _diff_spans(before, after):
    """Tramos que cambian entre la vista previa de antes y la de después: ([(i, j) en before], [(i, j) en after])."""
    ops = difflib.SequenceMatcher(None, before, after, autojunk=False).get_opcodes()
    return ([(i1, i2) for op, i1, i2, _, _ in ops if op in ("replace", "delete")],
            [(j1, j2) for op, _, _, j1, j2 in ops if op in ("replace", "insert")])


def _log(text):
    """Apunta un fallo en errores.log, junto a las preferencias: el .exe no tiene consola donde verlo."""
    path = SETTINGS.parent / "errores.log"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"--- {datetime.now():%Y-%m-%d %H:%M:%S}\n{text.rstrip()}\n")
    except OSError:
        pass  # sin registro; el aviso en pantalla sale igual
    return path


def _worker(events, work, args, report):
    """Corre en otro proceso: PyMuPDF retiene el GIL y, en un hilo, trabaría la ventana. args es la tupla o,
    desde App.run, la ruta del archivo donde la guardó."""
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
        events.put(("done", result))
    except Exception as e:
        events.put(("error", str(e) or type(e).__name__))  # MemoryError() no trae mensaje
        events.put(("log", traceback.format_exc()))  # la ventana lo apunta en errores.log


def _spell_server(requests, replies):
    """Proceso de ortografía del editor: carga Hunspell una vez (~4 s) y responde mientras el editor está
    abierto. ("check", [palabras]) -> ("unknown", [las que no están]); ("suggest", palabra) -> ("suggestions",
    palabra, [sugerencias]); ("first", palabra): la misma, antes que las demás (clic derecho); None termina.
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
EPUBCHECK_URL = (f"https://github.com/w3c/epubcheck/releases/download/v{EPUBCHECK_VERSION}/"
                 f"epubcheck-{EPUBCHECK_VERSION}.zip")
EPUBCHECK_LEVELS = {"FATAL": "Error grave", "ERROR": "Error", "WARNING": "Advertencia", "USAGE": "Sugerencia",
                    "INFO": "Información"}


def _epubcheck_jar():
    return SETTINGS.parent / f"epubcheck-{EPUBCHECK_VERSION}" / "epubcheck.jar"


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
    with zipfile.ZipFile(part) as z:
        names = z.namelist()
        # Solo su carpeta: un nombre con «..» o fuera de ella escribiría donde no debe
        if not names or any(not n.startswith(inside) or ".." in Path(n).parts for n in names):
            raise RuntimeError("El archivo descargado de EPUBCheck no es el esperado.")
        z.extractall(folder)
    part.unlink()
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
        run = subprocess.run(["java", "-jar", jar, str(epub), "--json", str(report), "--locale", "es"],
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


def _image_info(data):
    """«1200×1700 px · 340 KB · JPEG» de una imagen."""
    image = QImage.fromData(data)
    return f"{image.width()}×{image.height()} px · {max(1, round(len(data) / 1024))} KB · {_image_ext(data).upper()}"


THUMB_BOX = (72, 72)  # miniatura de la lista de ilustraciones: marco cuadrado, igual para verticales y apaisadas


def _framed_thumb(data):
    """La imagen reducida y centrada en un marco transparente de THUMB_BOX: todas las filas, iguales."""
    image = QImage.fromData(data).scaled(*THUMB_BOX, Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation)
    framed = QPixmap(*THUMB_BOX)
    framed.fill(Qt.GlobalColor.transparent)
    painter = QPainter(framed)
    painter.drawImage((THUMB_BOX[0] - image.width()) // 2, (THUMB_BOX[1] - image.height()) // 2, image)
    painter.end()
    return framed


def _store_image(path):
    """Copia la imagen elegida junto a las preferencias y devuelve esa ruta: la revisión la guarda, y la del usuario
    (una carpeta de exportación) puede borrarse; entonces, al reabrir, el cambio se perdía."""
    data = Path(path).read_bytes()
    dest = SETTINGS.parent / "ilustraciones" / (hashlib.sha1(data).hexdigest()[:16] + Path(path).suffix.lower())
    if not dest.is_file():
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    return str(dest)


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
    párrafo por línea. Parar lo cierra (la voz se calla en el acto); el siguiente «Leer» abre otro."""
    done = Signal()       # acabó el párrafo
    failed = Signal(str)  # el motivo, para el usuario

    def __init__(self):
        super().__init__()
        self.proc = None

    def say(self, text):
        if self.proc is None:
            self.proc = QProcess(self)
            self.proc.readyReadStandardOutput.connect(self._read)
            self.proc.errorOccurred.connect(lambda _: self._fail("No se pudo abrir la voz de Windows (PowerShell)."))
            script = SPEECH_SCRIPT.encode("utf-16-le")  # -EncodedCommand: sin líos de comillas
            self.proc.start("powershell", ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                           "-EncodedCommand", base64.b64encode(script).decode()])
        self.proc.write((" ".join(text.split()) + "\n").encode("utf-8"))

    def _read(self):
        for line in bytes(self.proc.readAllStandardOutput()).decode("utf-8", "replace").split():
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
            proc.readyReadStandardOutput.disconnect()
            proc.errorOccurred.disconnect()  # matarlo no es un fallo
            proc.kill()
            proc.waitForFinished(1000)
            proc.deleteLater()


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


def _start_spell_process():
    """(peticiones, respuestas, proceso) de _spell_server, o None si no arranca: el editor sigue sin él."""
    try:
        requests, replies = multiprocessing.Queue(), multiprocessing.Queue()
        proc = multiprocessing.Process(target=_spell_server, args=(requests, replies), daemon=True)
        proc.start()
        return requests, replies, proc
    except Exception:
        return None


def _analyze(pdf, progress=None):
    """convert.analyze, las ilustraciones repetidas y el informe de calidad, en el proceso de trabajo: en la
    ventana congelaban al abrir (Kage 5: 2,4 s las repetidas; Nageki 1: 0,9 s el informe)."""
    book = convert.analyze(pdf, progress)
    return book, convert.duplicate_images(book), convert.quality_report(book), convert.report_memos()


def _write_json(path, data):
    """Escribe el .json entero o nada: un corte a mitad dejaba el archivo roto y la revisión se ignoraba."""
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def _ps_quote(text):
    """Texto entre comillas simples de PowerShell: un apóstrofo (O'Neil) se escribe doble."""
    return "'" + str(text).replace("'", "''") + "'"


def _same_type(value, like):
    """Valor de ajustes.json válido para una opción: el mismo tipo que su valor de fábrica (escrito a mano puede
    traer «sí» donde va true)."""
    return isinstance(value, bool) if isinstance(like, bool) else type(value) is type(like)


def _write_options(state, breaks=()):
    """Opciones de write_epub a partir de una revisión, de los campos de la ventana o de lo heredado."""
    lang, style = str(state.get("lang", "es")).strip(), state.get("style")
    font, path = state.get("font"), str(state.get("font_file") or "")
    return {"lang": lang if LANG.fullmatch(lang) else "es",
            "typography": bool(state.get("typography", True)),
            "style": style if style in convert.STYLES else "Clásico",
            "separator": str(state.get("separator", "Según el estilo")),
            "shrink": int(state.get("shrink_size") or convert.SHRINK) if state.get("shrink", True) else False,
            "replacements": [tuple(rule) for rule in state.get("replacements", [])],
            "credits": bool(state.get("credits")), "gray": bool(state.get("gray")), "trim": bool(state.get("trim")),
            "font": font if font in convert.FONTS
            else path if path and Path(path).name == font and Path(path).is_file() else "",
            "epub2": bool(state.get("epub2")), "reading": bool(state.get("reading")), "kepub": bool(state.get("kepub")),
            "hyphenate": bool(state.get("hyphenate")), "breaks": list(breaks),
            "alts": {str(k): str(v) for k, v in state["alts"].items()} if isinstance(state.get("alts"), dict) else {}}


def _write_pdf(book, chapters, out, removed, options, size, progress=None):
    """convert.write_pdf en el proceso de trabajo (maquetar un libro largo tarda unos segundos)."""
    convert.write_pdf(book, chapters, out, size, progress, removed=removed, **options)
    return []


def _write_checked(book, chapters, out, removed, options, progress=None):
    """write_epub y, en el mismo proceso de trabajo, check_epub: devuelve los problemas del EPUB."""
    convert.write_epub(book, chapters, out, removed=removed, progress=progress, **options)
    return convert.check_epub(out)


def _epub_name(path, kepub):
    """Nombre del EPUB: el Kobo solo usa su motor KEPUB si el archivo acaba en .kepub.epub."""
    return re.sub(r"(\.kepub)?\.epub$", "", str(path), flags=re.I) + (".kepub.epub" if kepub else ".epub")


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
    """Tamaño y fecha del PDF: si cambian, la revisión guardada es de otro PDF y no se usa."""
    stat = Path(pdf).stat()
    return [stat.st_size, stat.st_mtime_ns]


def _read_review(pdf, blocks=None):
    """Revisión guardada de este PDF, o None si no hay o si el PDF cambió desde entonces. Sin blocks
    (el panel de serie, que no analiza) solo se compara el PDF."""
    try:
        state = json.loads(_review_path(pdf).read_text(encoding="utf-8"))
        if (state["pdf"] == _fingerprint(pdf) and blocks in (None, state["blocks"])
                and isinstance(state["rows"], dict)):
            return state
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def _set_aside(pdf):
    """Revisión guardada que ya no vale (otro análisis u otro PDF), apartada en .bak antes de que la ventana
    escriba la nueva encima. Devuelve la copia, o None si no había revisión."""
    review = _review_path(pdf)
    backup = review.with_name(review.name + ".bak")
    try:
        os.replace(review, backup)
    except OSError:
        return None  # no había revisión
    return backup


# Versiones del trabajo de un libro, como los commits de git: copias completas de la revisión en un zip junto al
# PDF. Restaurar una no borra las posteriores: así conviven alternativas del mismo tomo.
# No cuentan para saber si una versión automática repite la última
VERSION_NOISE = ("problems", "pdf", "editor_at", "work")
COMPARE_LIMIT = 300  # párrafos cambiados que se muestran al comparar: más no se leen y la ventana tardaría
FIELD_LABELS = {"title": "Título", "author": "Autor", "series": "Serie", "volume": "Vol.", "publisher": "Grupo",
                "translator": "Traductor", "illustrator": "Ilustraciones", "proofreader": "Corrección",
                "editor": "Edición", "original_title": "Título original", "subjects": "Géneros", "date": "Fecha",
                "lang": "Idioma", "description": "Sinopsis", "cover": "Portada", "style": "Estilo",
                "separator": "Separadores", "font": "Fuente", "font_file": "Archivo de fuente", "profile": "Perfil",
                "typography": "Limpieza tipográfica", "gray": "Imágenes en gris", "shrink": "Reducir imágenes",
                "shrink_size": "Tamaño de las imágenes", "trim": "Recortar márgenes blancos",
                "credits": "Página de créditos", "reading": "Minutos de lectura", "epub2": "EPUB 2",
                "kepub": "Formato Kobo (KEPUB)", "hyphenate": "Partir palabras con guiones",
                "autocase": "Mayúsculas del español al abrir", "replacements": "Reemplazos",
                "words": "Diccionario de la serie", "glossary": "Glosario", "tr_glossary": "Glosario de traducción",
                "tr_notes": "Personajes y estilo de traducción", "ignored": "Avisos ignorados", "comments": "Comentarios",
                "alts": "Descripciones de ilustraciones",
                "echoes": "Buscar ecos", "whole_words": "No partir palabras en el PDF",
                "pdf_size": "Tamaño de PDF preferido"}


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
    backup = path.with_name(path.name + ".bak")
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
        if key == "description":
            lines.append([(f"{label}: cambiada", None)])
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
    """Un párrafo con lo quitado ("old") y lo puesto ("new") palabra a palabra, como el control de cambios de Word."""
    x, y = re.findall(r"\S+|\s+", before), re.findall(r"\S+|\s+", after)
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
    secciones movidas ["move", inicio, fin, destino] e ilustraciones cambiadas ["image", bloque, archivo].
    Devuelve los aplicados."""
    done = []
    for edit in state.get("edits", []):
        try:
            if edit[0] == "image":
                k, path = int(edit[1]), str(edit[2])
                if not (0 <= k < len(book.blocks) and book.blocks[k].kind == "image"):
                    break
                try:
                    _swap_image(book, k, convert.load_cover(path))
                except ValueError:
                    pass  # la imagen ya no está: se queda la del PDF, y el cambio se recuerda por si vuelve
                done.append(["image", k, path])
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
        convert.edit_section(book, start, end, text)
        done.append([start, end, text])
    return done


def _plan(book, rows):
    """Capítulos, secciones quitadas y títulos que van como separador según los tipos de la lista
    ({fila: (tipo, título)}); marca también qué ilustraciones entran y cómo."""
    chapters, removed, breaks = [], [], []
    for item, (kind, title, *_) in rows.items():  # el tercer valor, si está, es la marca de revisado
        k = int(item)
        if book.blocks[k].kind == "break":  # ya es un separador: su fila solo lo muestra
            continue
        if book.blocks[k].kind == "image":
            book.blocks[k].keep = kind in (IMG, SPLIT, ROTATED)
            book.blocks[k].layout = LAYOUT.get(kind, "")
        elif kind == ON:
            chapters.append((k, title))
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
    state = _read_review(pdf, len(book.blocks))
    if state:
        _replay(book, state)
        chapters, removed, breaks = _plan(book, state["rows"])
    else:
        state = {**options, **_inherited(pdf, book.series)}
        state["author"] = book.author or state.get("author", "")
        chapters = [(k, html.unescape(book.blocks[k].html)) for k in book.candidates]
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
  staff(perPage: 10) { edges { role node { name { full } } } }
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
        raise ValueError(f"No se pudo consultar AniList: {e}") from e
    return _anilist_results(data)


def _anilist_results(data):
    """Respuesta de AniList -> [{name, author, illustrator, original_title, subjects, description, characters}],
    characters: [(nombre, papel)] con papel una clave de ROLES, los principales primero.
    La sinopsis de AniList está en inglés; se le quitan las etiquetas y la nota "(Source: …)"."""
    found = []
    for m in (((data.get("data") or {}).get("Page") or {}).get("media") or []):
        title = m.get("title") or {}
        staff = [(edge.get("role") or "", ((edge.get("node") or {}).get("name") or {}).get("full") or "")
                 for edge in (m.get("staff") or {}).get("edges") or []]
        author = next((n for role, n in staff if re.search(r"story|original", role, re.I)), "")
        illustrator = next((n for role, n in staff if re.search(r"\b(art|illustration)\b", role, re.I)
                            and not re.search(r"story", role, re.I)), "")
        name = title.get("romaji") or title.get("english") or ""
        year = (m.get("startDate") or {}).get("year")
        english = title.get("english") or ""
        text = re.sub(r"<br\s*/?>", "\n", m.get("description") or "", flags=re.I)
        text = html.unescape(re.sub(r"<[^>]+>", "", text))
        text = re.sub(r"\(Source:[^)]*\)\s*$", "", text.strip()).strip()
        found.append({"name": name + (f" ({year})" if year else "") + (f" · {english}" if english and english != name else ""),
                      "author": author, "illustrator": illustrator, "original_title": title.get("native") or "",
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


def _serve(path, host):
    """Sirve el EPUB por HTTP (cualquier ruta lo devuelve) en un puerto libre, desde un hilo: solo lee un
    archivo, no usa PyMuPDF. server.shutdown() lo para."""
    data, name = Path(path).read_bytes(), Path(path).name

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/epub+zip")
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{urllib.parse.quote(name)}")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            self.server.downloaded = True  # el panel de serie lo cuenta como enviado al cerrar la ventana

        def log_message(self, *args):
            pass  # el .exe no tiene consola: escribir en stderr (None) fallaría

    server = http.server.ThreadingHTTPServer((host, 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _calibre():
    """calibre.exe si está instalado: en el PATH o en su carpeta de siempre."""
    found = shutil.which("calibre")
    for var in ("ProgramFiles", "ProgramFiles(x86)"):
        if not found and os.environ.get(var) and (Path(os.environ[var]) / "Calibre2" / "calibre.exe").is_file():
            found = str(Path(os.environ[var]) / "Calibre2" / "calibre.exe")
    return found


def _install_paths():
    """Dónde queda instalado el .exe y su acceso directo del menú Inicio (solo para este usuario)."""
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Programs" / "PDF a EPUB"
    menu = Path(os.environ.get("APPDATA") or Path.home()) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    return base / "PDF a EPUB.exe", menu / "PDF a EPUB.lnk"


OPEN_WITHS = [rf"Software\Classes\SystemFileAssociations\{ext}\shell\PDFaEPUB"  # "Convertir a EPUB" en su menú
              for ext in (".pdf", ".epub")]


def _install(source):
    """Copia el .exe a la carpeta de programas del usuario, crea el acceso directo del menú Inicio y
    añade "Convertir a EPUB" al menú contextual de los PDF. Sin permisos de administrador."""
    import winreg
    exe, link = _install_paths()
    exe.parent.mkdir(parents=True, exist_ok=True)
    if Path(source).resolve() != exe.resolve():
        shutil.copy2(source, exe)
    script = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut({_ps_quote(link)});"
              f"$s.TargetPath={_ps_quote(exe)};$s.IconLocation={_ps_quote(f'{exe},0')};$s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], check=True,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    for open_with in OPEN_WITHS:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, open_with) as key:
            winreg.SetValue(key, "", winreg.REG_SZ, "Convertir a EPUB")
            winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, str(exe))
            winreg.SetValue(key, "command", winreg.REG_SZ, f'"{exe}" "%1"')
    return exe


def _uninstall():
    """Deshace _install: borra el acceso directo, la entrada del menú de los PDF y el .exe copiado."""
    import winreg
    exe, link = _install_paths()
    link.unlink(missing_ok=True)
    for sub in [key for open_with in OPEN_WITHS for key in (open_with + r"\command", open_with)]:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)
        except OSError:
            pass  # ya no estaba
    if Path(sys.executable).resolve() != exe.resolve():  # el .exe en uso no se puede borrar
        exe.unlink(missing_ok=True)


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

    def closeEvent(self, event):
        if self.on_close:  # App.close decide; si sigue adelante, vuelve a cerrar sin on_close
            event.ignore()
            self.on_close()

    def dragEnterEvent(self, event):
        if _dropped_pdf(event.mimeData()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        path = _dropped_pdf(event.mimeData())
        if path and self.on_drop:
            event.acceptProposedAction()
            self.on_drop(path)


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
    button = QToolButton()
    button.setText(f"{text} ▾")
    button.setMenu(menu)
    button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    return button


def _combo(var, values, width, editable=False, changed=None):
    box = QComboBox()
    box.addItems(values)
    box.setEditable(editable)
    box.setMinimumContentsLength(width)
    box.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    var.combo(box)
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
            original = MARKUP.sub("", original.removeprefix("# ").removeprefix(convert.CENTER)
                                    .removeprefix(convert.FRAME).removeprefix(convert.RIGHT))
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


def _glyph_icon(glyph, color):
    """Glifo de theme.ICONS dibujado como icono grande y monocromo, con el color del texto del tema. Se escala
    a la caja: a tamaño de letra, «±» salía diminuto y «▲» enorme al lado."""
    ratio = QApplication.primaryScreen().devicePixelRatio()
    pixmap = QPixmap(round(GLYPH * ratio), round(GLYPH * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    path = QPainterPath()
    path.addText(0, 0, QFont("Segoe UI", 40), glyph)
    box = path.boundingRect()
    if box.isEmpty():  # sin fuentes (pruebas sin pantalla): icono vacío
        return QIcon(pixmap)
    scale = min(GLYPH * 0.85 / box.width(), GLYPH * 0.8 / box.height())
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.translate(GLYPH / 2, GLYPH / 2)
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
        self.partial = None                    # EPUB a medio escribir del trabajo en curso
        self.args_file = ""                    # datos del trabajo en curso, para el proceso (ver run)
        self.reports = {}                      # informes de calidad ya calculados (ver quality_report)
        self.editor_save = None                # guarda y cierra el cuadro de edición abierto
        self.view = None                       # última posición del scroll de la lista
        self.pdf_doc = None                    # PDF abierto para la vista previa
        self.preview_path = os.path.join(tempfile.gettempdir(), f"lnepub-vista-{os.getpid()}.epub")
        # Lo que se escribió en preview_path y ese EPUB ya maquetado ((ancho, alto), documento, páginas): un
        # cambio pedía la vista previa hasta 4 veces y maquetarla cada vez paraba la ventana medio segundo
        self.preview_key, self.preview_doc = None, None
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
        self.closed = False                    # ventana cerrada: lo diferido (miniaturas, repintar) ya no se hace
        self._description = ""                 # sinopsis (propiedad description)
        self.replacements = []                 # [[buscar, reemplazo]]; se editan en su propio cuadro
        self.words = []                        # diccionario de la serie: palabras que la ortografía da por buenas
        self.glossary = []                     # glosario de la serie: los nombres como deben ir
        self.tr_glossary = []                  # glosario de traducción de la serie: [[original, traducción]]
        self.tr_notes = ""                     # personajes y estilo para la IA que traduce, de la serie
        self.comments = []                     # comentarios del revisor: {"text", "page", "note"}; no van al EPUB
        self.editor_at = None                  # bloque del cursor al cerrar el editor: «Lo dejaste en…»
        self.work = {}                         # trabajo en el editor por día: {"AAAA-MM-DD": {"minutes", …}}
        self.alts = {}                         # descripciones de ilustraciones: {convert.image_key: texto}
        self.thumbs, self.thumbs_tried = None, set()  # proceso de miniaturas de recientes y archivos ya probados
        self.ignored = []                      # avisos del informe que no se muestran (_issue_key)
        self.unknown = set()                   # palabras desconocidas de la última revisión ortográfica
        # ("page", n), ("file", ruta), ("image", k), ("text", "") o None: la que eligió el análisis
        self.cover_choice = None
        self.device_var, self.dark_var = qtk.Var('E-reader 6"'), qtk.Var(False)
        self.theme_var = qtk.Var("Sistema")
        self.title_var, self.author_var, self.series_var, self.volume_var = (qtk.Var() for _ in range(4))
        self.publisher_var, self.translator_var = qtk.Var(), qtk.Var()
        # Datos de la página de créditos y del catálogo: en el panel «Editar datos»
        self.illustrator_var, self.proofreader_var, self.editor_var = qtk.Var(), qtk.Var(), qtk.Var()
        self.original_title_var, self.subjects_var, self.date_var = qtk.Var(), qtk.Var(), qtk.Var()
        self.lang_var = qtk.Var("es")
        self.autocase_var = qtk.Var(False)
        self.profile_var = qtk.Var("Personalizado")  # fija gris, tamaño de imagen, EPUB 2 y KEPUB
        self.separator_var = qtk.Var("Según el estilo")  # cómo se ven los separadores de escena
        self.style_var = qtk.Var("Clásico")  # aspecto del EPUB: letra, párrafos e inicio de capítulo
        self.font_var = qtk.Var("Del lector")  # tipografía incrustada (convert.FONTS) o nombre de la propia
        self.font_file_var = qtk.Var("")  # ruta de la fuente propia
        self.typography_var = qtk.Var(True)  # comillas «», puntos suspensivos, raya, espacios
        self.gray_var = qtk.Var(False)  # para e-readers de tinta: pesa menos y se ve igual
        self.shrink_var = qtk.Var(True)  # las imágenes son casi todo el peso del EPUB
        self.shrink_size_var = qtk.Var(convert.SHRINK)  # lado mayor de las imágenes reducidas
        self.trim_var, self.credits_var, self.reading_var = qtk.Var(False), qtk.Var(False), qtk.Var(False)
        self.epub2_var, self.kepub_var = qtk.Var(False), qtk.Var(False)
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
            ("Ortografía…", self.spelling), ("Añadir página…", self.add_page),
            ("Página de personajes…", self.character_page), ("Ilustraciones…", self.images_dialog),
            ("Cambiar ilustración…", self.change_image),
            ("Describir ilustración…", self.describe_image), ("Traducir…", self.translate_book),
            ("Versiones…", self.versions))]
        self.book_actions.append(tools.addSeparator())
        tools.addAction("Panel de serie…", self.series_panel)
        tools.addAction("Volumen recopilatorio…", self.omnibus)
        tools.addAction("Vigilar una carpeta…", lambda: self.open_options("Carpeta vigilada"))
        tools.addSeparator()
        tools.addAction("Opciones…  (Ctrl+,)", lambda: self.open_options())
        tools.addAction("Atajos de teclado…", lambda: messagebox.showinfo("Atajos de teclado", SHORTCUTS))
        tools.addSeparator()
        tools.addAction("Instalar en este equipo", self.install)
        tools.addAction("Desinstalar", self.uninstall)
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
        self.book_title, self.book_line = _label("", "title"), _label("", "muted")
        self.data_btn = _button("Editar datos ▾", self.toggle_data)
        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(self.book_title)
        text.addWidget(self.book_line)
        card.setLayout(_row(self.cover_thumb, text, 1, self.data_btn, margins=(10, 8, 10, 8), spacing=10))
        body.addWidget(card)
        self.data_panel = self.book_data()
        self.data_panel.hide()
        body.addWidget(self.data_panel)
        for var in (self.title_var, self.author_var, self.publisher_var, self.lang_var, self.series_var,
                    self.volume_var):
            var.listeners.append(self.update_header)
        self.lang_timer = None
        self.lang_var.listeners.append(self.lang_changed)

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
        panes.addWidget(self.pdf_pane)
        panes.addWidget(self.epub_pane)
        views.addWidget(self.pages, 1)
        self.strip = qtk.Strip(THUMB_HEIGHT)
        self.strip.clicked.connect(self.jump)
        views.addWidget(self.strip)

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
        self.options_row = QWidget()  # perfil, opciones del libro y «Generar»: solo con un libro abierto (update_chrome)
        rows.addWidget(self.options_row)
        # El resto de opciones vive en «Opciones»: aquí solo lo que más se cambia, el lector de destino
        self.options_row.setLayout(_row(_label("Perfil:"), _combo(self.profile_var, list(PROFILES), 15,
                                                                 changed=self.apply_profile),
                            _button("Opciones del libro…", lambda: self.open_options("Perfil y formato", "book")),
                            1, self.pdf_btn, self.gen_btn))
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedWidth(160)
        self.status = StatusLabel(objectName="muted")
        # Un estado largo se recorta en vez de ensanchar la ventana más allá de la pantalla
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        status_row = _row(self.progress, self.status, self.quality_btn)
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
        for widget in (icon, _label("Suelta aquí un PDF de novela ligera", "big"), _label("o", "muted"),
                       _button("Abrir PDF o EPUB…", self.open_pdf, "primary"),
                       _button("¿Muchos tomos? Convertir una carpeta entera", self.convert_folder, "link")):
            inner.addWidget(widget, 0, Qt.AlignmentFlag.AlignHCenter)
        inner.addStretch(1)
        recent = QFrame(objectName="card")
        column = QVBoxLayout(recent)
        column.addWidget(_label("Recientes", "title"))
        self.recent_list = QListWidget()
        self.recent_list.itemClicked.connect(lambda item: item.data(Qt.ItemDataRole.UserRole)
                                             and self.open_path(item.data(Qt.ItemDataRole.UserRole)))
        self.recent_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.recent_list.customContextMenuRequested.connect(self.recent_context)
        column.addWidget(self.recent_list, 1)
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
                            _button("Glosario…", self.edit_glossary), _menu_button("Títulos", titles), 1,
                            _button("Buscar en internet (AniList)…", self.search_online)), 5, 0, 1, 8)
        for c in (1, 3, 5):
            grid.setColumnStretch(c, 2)
        grid.setColumnStretch(7, 1)
        return panel

    def toggle_data(self):
        shown = not self.data_panel.isVisible()
        self.data_panel.setVisible(shown)
        self.data_btn.setText("Ocultar datos ▴" if shown else "Editar datos ▾")

    def update_header(self):
        """Tarjeta del libro: título, y autor · grupo · serie y tomo · idioma."""
        title = self.title_var.get().strip() or (self.pdf.stem if self.pdf else "")
        self.book_title.setText(title)
        series = self.series_var.get().strip()
        if series and self.volume_var.get().strip():
            series += f" · vol. {self.volume_var.get().strip()}"
        parts = [self.author_var.get().strip(), self.publisher_var.get().strip(), series, self.lang_var.get().strip()]
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
        self.dark = theme.apply(qtk.app(), self.theme_var.get())
        colors = theme.kind_colors(self.dark)
        for tag in KIND_TAG.values():
            self.tree.tag_configure(tag, font=self.fonts[tag], foreground=colors[tag])
        self.update_quality()
        self.redraw()
        if self.book is None:  # las tarjetas de recientes llevan colores del tema
            self.fill_recent()

    def show_panes(self):
        mode = self.tabs.currentIndex()  # 0 PDF, 1 EPUB, 2 los dos
        self.pdf_pane.setVisible(mode != 1)
        self.epub_pane.setVisible(mode != 0)
        self.views_size = None
        self.redraw()

    def run(self, work, args, done, report=False, busy="Analizando…"):
        """Ejecuta work(*args) en otro proceso y llama a done(resultado) en la ventana.
        Con report=True, work recibe progress=... y la barra avanza junto al texto busy. False si ya había
        un trabajo en curso: su resultado llegaría a este done."""
        if self.done:
            return False
        self.open_btn.setEnabled(False)
        self.batch_btn.setEnabled(False)
        self.gen_btn.setEnabled(False)
        self.pdf_btn.setEnabled(False)
        self.done, self.busy = done, busy
        self.update_chrome()  # sin libro, la barra de abajo aparece para el progreso
        # Los datos van en un archivo: por la tubería del proceso nuevo, la ventana esperaba a que arrancara
        # para leerlos (unos 3 s con un libro con ilustraciones). Así start() vuelve enseguida.
        fd, path = tempfile.mkstemp(prefix="lnepub-trabajo-", suffix=".pickle")
        with os.fdopen(fd, "wb") as f:
            pickle.dump(args, f, protocol=pickle.HIGHEST_PROTOCOL)
        self.args_file = path
        self.proc = multiprocessing.Process(target=_worker, args=(self.events, work, path, report), daemon=True)
        self.proc.start()
        return True

    def update_chrome(self):
        """Sin libro abierto, solo lo que sirve para empezar: abrir, convertir una carpeta, las herramientas que no
        piden libro y los recientes de la pantalla de inicio. Las opciones del EPUB, Generar, Calidad, Buscar y el nombre del
        archivo llegan con el libro; la barra de abajo también sale mientras se trabaja, por el progreso."""
        has = self.book is not None
        for widget in (self.options_row, self.quality_btn, self.recent_btn, self.file_label, self.find_btn):
            widget.setVisible(has)
        self.bottom.setVisible(has or self.done is not None)
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
        else:
            self.progress.setValue(0)
            self.status.setText("Error")
            messagebox.showerror("Error", f"{event[1]}\n\nEl detalle queda en {SETTINGS.parent / 'errores.log'}")

    def on_error(self, kind, value, tb):
        """Fallo dentro de la ventana (un botón, un atajo): se apunta en errores.log y se avisa, en vez
        de perderse en una consola que el .exe no tiene."""
        path = _log("".join(traceback.format_exception(kind, value, tb)))
        messagebox.showerror("Algo falló", f"{value or kind.__name__}\n\nEl detalle queda en:\n{path}")

    def close(self):
        """Al cerrar a mitad de un trabajo, detiene el proceso y borra el EPUB a medio escribir.
        False si se canceló (editor de texto con cambios)."""
        editor = self.text_editor
        if editor and editor.dirty() and not messagebox.askyesno(
                "Cerrar", "El texto que estás editando tiene cambios sin guardar. ¿Cerrar y descartarlos?",
                parent=editor.win):
            editor.win.raise_()
            return False
        if self.translations_running() and not messagebox.askyesno(
                "Cerrar", "Hay una traducción en curso: si sales ahora se pierde el lote que se está traduciendo (hasta "
                          "30 párrafos; lo anterior ya está guardado). ¿Salir igual?"):
            return False
        if editor:
            editor.win.done(0)
        self.poller.stop()
        self.watcher.stop()
        if self.lang_timer:  # el informe se recalcularía sobre la ventana ya cerrada
            qtk.cancel(self.lang_timer)
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
        if self.preview_doc:
            self.preview_doc[1].close()
        Path(self.preview_path).unlink(missing_ok=True)
        self.root.on_close, self.closed = None, True
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
        back, forward = _button("◀", lambda: step(-1)), _button("▶", lambda: step(1))
        for button in (back, forward):
            button.setFixedWidth(36)
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
        if self.book and self.tree.selection():
            self.draw_pdf()
            self.draw_epub()
            return
        self.blank(self.pdf_view, self.idle_text)
        self.blank(self.epub_view, self.idle_text)
        if not self.thumbs:  # tira vacía: rectángulos tenues en lugar de un hueco
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
        pix = page.get_pixmap(matrix=matrix)
        if dark:  # como el modo oscuro de los lectores: invierte el papel y el texto, no las ilustraciones
            art = pymupdf.Pixmap(pix, 0)  # copia, sin alfa como pix (copy exige el mismo)
            pix.invert_irect()
            for info in page.get_image_info():
                box = (pymupdf.Rect(info["bbox"]) * matrix).irect & pix.irect
                if not box.is_empty:
                    pix.copy(art, box)
        return qtk.qimage(pix), zoom

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
                inner = {}  # tipos de los títulos dentro del tramo (subtítulos de un capítulo), desde 0
                if is_image:
                    part_blocks = [convert.Block("image", block.page, image=block.image, layout=LAYOUT.get(kind, ""))]
                else:
                    # Un capítulo sigue con sus subtítulos, como en el EPUB: no se corta en el primero
                    end = self.section_of(item)[1]
                    part_blocks = self.book.blocks[index:end]
                    inner = {k - index: self.tree.set(str(k), "inc") for k in range(index + 1, end)
                             if self.tree.exists(str(k)) and self.is_title(str(k))}
                part = convert.Book(self.book.title, "", self.book.cover, part_blocks, [])
                options = self.write_options([0] * (kind == SEP) + [k for k, v in inner.items() if v == SEP])
                options["removed"] = [k for k, v in inner.items() if v == CUT]
                options.update(shrink=False, trim=False, epub2=False, credits=False)  # rápido: solo lo que se ve
                chapters = [(0, self.tree.set(item, "title"))] if kind == ON else []
                options["fit"] = DEVICES[self.device_var.get()]  # ilustraciones que caben en la página de MuPDF
                key = (self.book.title, self.book.cover, chapters, sorted(options.items()),
                       [(b.kind, b.page, b.html, b.image, b.layout, b.align, b.keep) for b in part_blocks])
                if key != self.preview_key:  # lo mismo que ya está escrito y maquetado: no se rehace
                    self.preview_key, self.preview_doc = None, self.preview_doc and self.preview_doc[1].close()
                    convert.write_epub(part, chapters, self.preview_path,
                                       contents=False,  # sin página de índice: el capítulo 1 es la sección
                                       **options)
                    self.preview_key = key
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
            epub, pages = self.preview_epub()
            self.epub_page = next((n for n in range(pages) if epub.load_page((1, n)).search_for(term)), 0)
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
            # Desde los bytes: abierto por ruta, Windows no dejaría reescribir el archivo
            epub = pymupdf.open(stream=Path(self.preview_path).read_bytes(), filetype="epub")
            epub.layout(width=size[0], height=size[1], fontsize=11)
            self.preview_doc = size, epub, epub.chapter_page_count(1)  # el capítulo 0 es la portada
        return self.preview_doc[1:]

    def draw_epub(self):
        view = self.epub_view
        if self.epub_cut or self.epub_pages < 0:
            self.blank(view, self.epub_message if self.epub_cut else "Sin vista previa")
            self.epub_caption.setText("EPUB")
            return
        try:
            epub, self.epub_pages = self.preview_epub()
            self.epub_page = max(0, min(self.epub_page, self.epub_pages - 1))
            image, _ = self.render(epub.load_page((1, self.epub_page)), dark=self.dark_var.get())
        except Exception:
            self.blank(view, "Sin vista previa")
            return
        view.show_image(self.box_size(), image)
        self.epub_caption.setText(f"EPUB · {self.device_var.get()} · {self.epub_page + 1}/{self.epub_pages}")

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
        selected = self.tree.selection()
        # Los separadores no son secciones: desde uno se sigue con la sección de antes o la de después.
        # Los títulos de dentro de un capítulo (subtítulos) ya se leen con él: se saltan, salvo el elegido.
        items, inside = [], False
        for i in self.tree.get_children():
            chapter = self.is_title(i) and self.tree.set(i, "inc") == ON
            inside = inside or chapter
            if not self.is_break(i) and (chapter or not inside or not self.is_title(i) or i in selected):
                items.append(i)
        here = int(selected[0]) if selected else None
        if here is None:
            n = -1
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
        end = min(start + THUMBS_PER_TICK, self.pdf_doc.page_count)
        for n in range(start, end):
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

    def row_state(self, item):
        return item, self.tree.set(item, "inc"), self.tree.set(item, "title")

    def set_kind(self, item, kind):
        self.history.append([self.row_state(item)])
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
            return
        if not Path(path).is_file():
            messagebox.showerror("Error", f"No se encuentra el archivo:\n{path}")
            return
        self.save_review()
        for win in (getattr(self, "spelling_window", None), getattr(self, "page_window", None),
                    getattr(self, "translate_window", None)):
            if win and shiboken6.isValid(win):  # sin cerrar, cambiarían el libro nuevo con datos del anterior
                win.close()
        path = str(Path(path).resolve())
        self.settings["folder"] = str(Path(path).parent)
        self.settings["recent"] = [path] + [p for p in self.settings.get("recent", []) if p != path][:RECENT - 1]
        self.fill_recent()
        self.pdf, self.book = Path(path), None
        self.file_label.setText(self.pdf.name)
        self.tabs.setTabText(0, self.source_name())
        self.pdf_caption.setText(self.source_name())
        self.tree.delete(*self.tree.get_children())
        self.stack.setCurrentIndex(1)
        self.update_header()
        self.run(_analyze, (path,), lambda result: self.show_book(*result), report=True)

    def show_book(self, book, twins=None, report=None, memos=None):
        """Pone en la ventana un libro analizado. twins, report y memos: sus ilustraciones repetidas, su informe
        de calidad y lo que el informe recuerda de cada párrafo, si ya se calcularon en el proceso de trabajo."""
        self.book = book
        # El análisis tal cual, para restaurar versiones sin volver a analizar: las ediciones hacen listas nuevas
        self.pristine = (list(book.blocks), list(book.candidates), book.cover)
        # Son del libro: llegan con la revisión (el glosario, también heredado de otro tomo)
        self.glossary, self.tr_glossary, self.comments, self.alts = [], [], [], {}
        self.tr_notes, self.editor_at, self.work = "", None, {}
        if report is not None and not self.echoes_var.get() and self.spanish():  # el del proceso: sin opciones, en español
            self.reports[self.report_key()] = report
        if memos:
            convert.seed_report_memos(memos)
        if self.pdf_doc:
            self.pdf_doc.close()
        self.pdf_doc = convert.open_pdf(self.pdf)  # vista previa, miniaturas y panel del editor
        self.stack.setCurrentIndex(1)
        self.update_chrome()
        self.save_thumb()
        self.apply_state(self.new_book_options())  # la revisión o la serie, si las hay, mandan después
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
            if stale:
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
                if book.author:
                    inherited.pop("author", None)  # el del PDF manda
                if inherited:
                    self.apply_state(inherited)
                    text += " · datos de la serie tomados de otro tomo"
        self.history = []  # lo recuperado no se deshace
        self.status.setText(text)
        self.update_header()
        self.update_cover()

    def fill_tree(self, rows):
        """Llena la lista con los títulos e ilustraciones del libro, en su orden. rows: {fila: (tipo,
        título)} de las que ya tienen tipo; las demás salen como las dejó el análisis: los capítulos
        detectados como capítulo, los demás títulos ("El lado de…") como subtítulo, y las ilustraciones
        descartadas por el análisis (fondos de título, imágenes dentro del texto) como "Descartada"."""
        self.tree.delete(*self.tree.get_children())
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
                mark, title = (ON if i in chapters else OFF), html.unescape(block.html)
            elif block.kind == "image":
                double = " doble" if str(i) in self.wide else ""
                mark, title = (IMG if block.keep else DROPPED), f"Ilustración{double} · página {block.page}"
            elif block.kind == "break":  # separador de escena del texto: su símbolo, para encontrarlo
                symbol = html.unescape(block.html).strip() or ("Adorno" if block.image else "Separador")
                mark, title = SEP, f"{symbol} · página {block.page}"
            else:
                continue
            mark, title, *done = rows.get(str(i), (mark, title))
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
                self.history.append([self.row_state(item)])
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
        out = filedialog.asksaveasfilename(defaultextension=".epub", filetypes=[("EPUB", "*.epub")],
                                           initialdir=self.pdf.parent,
                                           initialfile=_epub_name(_out(self.pdf).name, self.kepub_var.get()))
        if not out:
            return
        out = _epub_name(out, self.kepub_var.get())
        chapters, removed, breaks = self.prepare_output()
        self.status.setText("Generando EPUB…")
        if self.run(_write_checked, (self.book, chapters, out, removed, self.write_options(breaks)),
                    lambda problems: self.saved(out, problems), report=True, busy="Generando EPUB…"):
            self.partial = f"{out}.tmp"  # el nombre temporal que usa write_epub

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
        out = filedialog.asksaveasfilename(defaultextension=".pdf", filetypes=[("PDF", "*.pdf")],
                                           initialdir=self.pdf.parent,
                                           initialfile=f"{_out(self.pdf).name} ({size}).pdf")
        if not out:
            return
        if Path(out).resolve() == Path(self.pdf).resolve():  # mismo nombre que el original: lo pisaría
            messagebox.showerror("Generar PDF", "Ese es el PDF original: elige otro nombre.")
            return
        chapters, removed, breaks = self.prepare_output()
        self.status.setText(f"Generando PDF {size}…")
        options = {**self.write_options(breaks), "whole_words": self.whole_words_var.get()}  # solo el PDF
        if self.run(_write_pdf, (self.book, chapters, out, removed, options, size),
                    lambda problems: self.saved(out, problems), report=True, busy="Maquetando el PDF…"):
            self.partial = f"{out}.tmp"

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

    def write_options(self, breaks=()):
        """Opciones de write_epub según la ventana (ver _write_options)."""
        state = {name: var.get() for name, var in self.fields().items()}
        state["replacements"], state["alts"] = self.replacements, self.alts
        return _write_options(state, breaks)

    def saved(self, out, problems=()):
        self.progress.setValue(0)  # la barra llena se quedaba tras el aviso de guardado
        self.status.setText(f"Guardado: {Path(out).name}" + (" (con avisos)" if problems else ""))
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
        text = str(out)
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
        url = f"http://{ip}:{server.server_port}/{urllib.parse.quote(Path(out).name)}"
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
                                "Al cerrar esta ventana deja de enviarse.", "muted"))
        column.addLayout(_row(1, _button("Cerrar", win.close)))
        win.finished.connect(lambda _: (server.shutdown(), server.server_close(),
                                        getattr(server, "downloaded", False) and _record_send(out, "Wi-Fi")))
        win.show()
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
                args = (["explorer", str(target.parent)] if long_path and target.is_file()
                        else ["explorer", f"/select,{target}"] if target.is_file() else ["explorer", str(target)])
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
                    subprocess.Popen(["explorer", str(target.parent)])
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
        if self.theme_var.get() != values["theme"]:
            self.theme_var.set(values["theme"])
            self.apply_theme()
        if changed and self.book:
            self.update_preview()

    def ask_folder(self):
        return filedialog.askdirectory(initialdir=self.settings.get("folder"), title="Carpeta que vigilar")

    def ask_font_file(self):
        return filedialog.askopenfilename(filetypes=[("Fuente", "*.ttf *.otf")])

    def new_book_options(self):
        """Opciones con las que abre un libro sin revisión ni serie: «Libros nuevos» sobre los valores de fábrica."""
        return {**self.factory, **self.settings.get("defaults", {})}

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
        if missing and self.thumbs is None:
            self.thumbs_tried.update(p for p, _ in missing)  # un archivo que no da miniatura no se reintenta
            self.thumbs = _start_thumbs(missing)
            if self.thumbs:
                qtk.after(300, self.wait_thumbs)
        if not recent:
            self.recent_menu.addAction("(ninguno)").setEnabled(False)
            item = QListWidgetItem("Aún no has abierto ningún PDF")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.recent_list.addItem(item)

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
        if self.closed or self.thumbs is None:
            return
        if self.thumbs.is_alive():
            qtk.after(300, self.wait_thumbs)
            return
        self.thumbs = None
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
        selected = self.tree.selection()
        if not self.book or not selected:
            return None
        return TextEditor(self, selected[0], *self.section_bounds(int(selected[0])))

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
        """Dónde está cada aviso, en una pasada por el libro: [(corto, completo)], como «Capítulo 0 · ¶4» y
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
            places.append((f"{short} · " + (f"¶{n}" if spot.startswith("párrafo") else "título"),
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
            start, end = self.section_bounds(issue.block)
            item = next((str(j) for j in range(issue.block, -1, -1) if self.tree.exists(str(j))),
                        self.tree.get_children()[0])
            editor = TextEditor(self, item, start, end)
        editor.win.raise_()
        editor.goto_issue(issue)
        return editor

    def apply_edit(self, edit, item):
        self.apply_edits([edit], item)

    def apply_edits(self, edits, item, titles=None, follow=False, kinds=None, retype=None):
        """Aplica textos editados: cambia los bloques, renumera la lista conservando los tipos elegidos
        y los apunta para la revisión. Guarda una copia de antes para deshacerlos de una vez con Ctrl+Z.
        Varios cambios van de atrás adelante: así los índices de los siguientes siguen valiendo.
        titles: {fila: título nuevo} de títulos renombrados en el editor (conservan su tipo). follow: item es
        la fila de antes de los cambios y se vuelve a elegir donde quede. kinds: por cada edición, el tipo
        (Capítulo/Subtítulo) de sus títulos, en orden. retype: {fila: tipo} de títulos que solo cambian de tipo."""
        rows = self.rows()
        self.history.append({"blocks": self.book.blocks, "candidates": self.book.candidates, "rows": rows,
                             "edits": len(self.edits), "item": item})  # edit_section crea listas nuevas
        rows = {k: [v[0], titles[k], *v[2:]] if titles and k in titles else v for k, v in rows.items()}
        rows = {k: [retype[k], *v[1:]] if retype and k in retype else v for k, v in rows.items()}
        for n, edit in enumerate(edits):
            mapping = convert.edit_section(self.book, *edit)
            self.edits.append(list(edit))
            rows = {str(mapping[int(k)]): v for k, v in rows.items() if int(k) in mapping}
            if kinds and kinds[n]:  # títulos escritos en el editor: con el tipo elegido en la cinta
                count = sum(1 for line in edit[2].splitlines() if line.strip())
                heads = [j for j in range(edit[0], edit[0] + count) if self.book.blocks[j].kind == "heading"]
                for j, kind in zip(heads, kinds[n]):
                    if kind in NEXT:  # el editor da Capítulo o Subtítulo; la traducción, el que tenía la fila
                        rows[str(j)] = [kind, html.unescape(self.book.blocks[j].html), ""]
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
                                              f"«{self.lang_var.get().strip()}» (Editar datos › Idioma).")
            return
        texts = [convert._plain(b) for b in self.book.blocks if b.kind == "p"]
        if self.run(convert.unknown_words, (texts, self.words), self.spelling_dialog):
            self.status.setText("Revisando la ortografía…")
        else:  # el menú no se desactiva como los botones
            self.status.setText("Espera a que termine el trabajo en curso")

    def spelling_dialog(self, found):
        """Palabras que no están en el diccionario (convert.unknown_words): dónde salen, cambiarlas en todo el
        libro o añadirlas al diccionario de la serie. El editor de texto las subraya."""
        self.unknown = {w for w, _, _ in found}
        self.status.setText(f"Ortografía: {convert.counted(len(found), 'palabra que no está', 'palabras que no están')} "
                            "en el diccionario")
        win, column = self.dialog("Ortografía")
        win.resize(900, 520)
        column.addWidget(_label("Palabras que no están en el diccionario, las más repetidas primero. Las que empiezan "
                                "en mayúscula no se revisan: suelen ser nombres.", "muted"))
        panes = QSplitter(Qt.Orientation.Horizontal)
        column.addWidget(panes, 1)
        table = qtk.RowList(("word", "count"), {"word": "Palabra", "count": "Veces"}, {"count": 60}, stretch="word")
        panes.addWidget(table)
        right = QWidget()
        side = QVBoxLayout(right)
        side.setContentsMargins(12, 0, 0, 0)
        side.addWidget(_label("Dónde sale (clic: verlo en las vistas previas)", "muted"))
        places = QListWidget()
        side.addWidget(places, 1)
        side.addWidget(_label("Cambiar por", "muted"))
        fix = QComboBox()
        fix.setEditable(True)
        side.addWidget(fix)
        change_btn = _button("Cambiar todas", lambda: change(), "primary")
        add_btn = _button("Añadir al diccionario", lambda: add())
        side.addLayout(_row(change_btn, add_btn, 1))
        panes.addWidget(right)
        panes.setSizes([300, 600])
        state = {"found": list(found), "places": []}

        def fill(select=0):
            table.delete(*table.get_children())
            for n, (word, count, _) in enumerate(state["found"]):
                table.insert("", "end", iid=str(n), values=(word, count))
            win.setWindowTitle(f"Ortografía · {convert.counted(len(state['found']), 'palabra')}")
            if state["found"]:
                item = str(min(select, len(state["found"]) - 1))
                table.selection_set(item)
                table.see(item)
            else:
                pick()

        def chosen():
            selected = table.selection()
            return state["found"][int(selected[0])] if selected else None

        def pick():
            entry = chosen()
            places.clear()
            fix.clear()
            state["places"] = []
            change_btn.setEnabled(bool(entry))
            add_btn.setEnabled(bool(entry))
            if not entry:
                return
            word, _, suggestions = entry
            fix.addItems(suggestions or [word])
            pattern = re.compile(rf"(?<!\w){re.escape(word)}(?!\w)", re.I)
            for k, block in enumerate(self.book.blocks):
                plain = convert._plain(block) if block.kind == "p" else ""
                m = pattern.search(plain)
                if m and len(state["places"]) < 200:
                    state["places"].append((k, block.page, word))
                    places.addItem(f"pág. {block.page} · …{plain[max(0, m.start() - 40):m.end() + 50]}…")

        def done(word, message):
            self.unknown.discard(word)
            n = int(table.selection()[0])
            del state["found"][n]
            fill(n)
            self.status.setText(message)

        def change():
            word, new = chosen()[0], fix.currentText().strip()
            edits = convert.replace_text(self.book, word, new, word=True) if new and new != word else []
            if edits:
                self.apply_fixes(edits, "")
            done(word, f"«{word}» cambiada por «{new}» en {convert.counted(len(edits), 'párrafo')} · Ctrl+Z para deshacer")

        def add():
            word = chosen()[0]
            self.words.append(word)
            done(word, f"«{word}» añadida al diccionario de la serie")

        table.selected.connect(pick)
        places.currentRowChanged.connect(lambda row: 0 <= row < len(state["places"])
                                         and self.show_match(*state["places"][row]))
        fill()
        win.show()
        win.parts = {"table": table, "places": places, "fix": fix, "change": change_btn, "add": add_btn}
        self.spelling_window = win

    def insert_page(self, name, lines, first=True):
        """Capítulo nuevo con título name y un párrafo por línea, antes del primer capítulo o al final."""
        start = next((int(i) for i in self.tree.get_children() if self.tree.set(i, "inc") == ON), None)
        at = start if start is not None and first else len(self.book.blocks)
        lines = [f"# {name}"] + [line.strip() for line in lines if line.strip()]
        self.apply_edits([(at, at, "\n\n".join(lines))], str(at))
        self.tree.set(str(at), "inc", ON)  # sin entrada propia en deshacer: Ctrl+Z quita la página entera
        self.tree.item(str(at), tags=(KIND_TAG[ON],))
        self.update_reviewed()
        self.update_preview()
        self.status.setText(f"Página «{name}» añadida · E para editarla · Ctrl+Z para quitarla")

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

    def add_page(self):
        """Página propia (créditos del grupo, agradecimientos, nota del traductor) como capítulo nuevo, antes
        del primer capítulo o al final. Luego se edita con «Editar texto…» y se deshace con Ctrl+Z."""
        if not self.book:
            return
        win, column = self.dialog("Añadir página")
        title = QLineEdit("Nota del traductor")
        body = QPlainTextEdit()
        body.setPlaceholderText("Un párrafo por línea. <i>cursiva</i> y <b>negrita</b>, como en «Editar texto…».")
        body.setMinimumSize(520, 220)
        where = qtk.Var("Antes del primer capítulo")
        column.addWidget(_label("Título", "muted"))
        column.addWidget(title)
        column.addWidget(_label("Texto", "muted"))
        column.addWidget(body, 1)

        def save():
            name = title.text().strip()
            if not name:
                messagebox.showerror("Añadir página", "Escribe un título.", parent=win)
                return
            self.insert_page(name, body.toPlainText().splitlines(), where.get().startswith("Antes"))
            win.close()

        column.addLayout(_row(_label("Dónde:"), _combo(where, ["Antes del primer capítulo", "Al final del libro"], 22),
                              1, _button("Añadir", save, "primary")))
        win.show()
        win.parts = {"title": title, "body": body, "where": where, "save": save}
        self.page_window = win

    def change_image(self):
        """Cambia la ilustración elegida en la lista por una imagen de archivo (mejor calidad, otra versión).
        Se apunta en la revisión como ["image", bloque, archivo]."""
        selected = self.tree.selection()
        if not self.book or not selected or not self.is_image(selected[0]):
            messagebox.showinfo("Cambiar ilustración", "Elige primero una ilustración en la lista.")
            return
        self.replace_image(int(selected[0]))

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
        self.history.append({"blocks": self.book.blocks, "candidates": self.book.candidates, "rows": rows,
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
        return [k for k, b in enumerate(self.book.blocks) if b.kind == "image" and b.image]

    def image_name(self, n, k):
        """Nombre del archivo exportado de la ilustración número n (desde 1): «Reemplazar desde carpeta» lo busca."""
        return f"ilustracion-{n:02d}-pag-{self.book.blocks[k].page:03d}.{_image_ext(self.book.blocks[k].image)}"

    def image_title(self, n, k):
        """«Ilustración 3 · pág. 12 · Capítulo 2: …», y «(descartada)» si no va al EPUB."""
        start = self.section_bounds(k)[0]
        chapter = (self.tree.set(str(start), "title") if start in self.section_heads() and self.tree.exists(str(start))
                   else "Inicio del libro")
        dropped = self.tree.exists(str(k)) and self.tree.set(str(k), "inc") == DROPPED
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
        """Herramientas → «Ilustraciones…»: todas las del libro con su miniatura, para exportarlas (una o todas) y
        reemplazarlas (una, o las de una carpeta con los nombres de la exportación) viendo antes y después."""
        if not self.book:
            return
        order = self.image_blocks()
        if not order:
            messagebox.showinfo("Ilustraciones", "Este libro no tiene ilustraciones.")
            return
        win, column = self.dialog("Ilustraciones")
        win.resize(980, 600)
        listing = QListWidget()
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
        right = QVBoxLayout()
        right.addWidget(preview, 1)
        right.addWidget(info)
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
            path = filedialog.asksaveasfilename(defaultextension=Path(name).suffix, initialfile=name,
                                                initialdir=folder_of_book(), filetypes=IMAGE_TYPES, parent=win)
            if path:
                Path(path).write_bytes(self.book.blocks[order[row]].image)
                note.setText(f"Exportada en {path}")

        def replace_one():
            row = listing.currentRow()
            if self.replace_image(order[row], win):
                fill(row)
                note.setText("Ilustración reemplazada · Ctrl+Z en la ventana principal para deshacer")

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
                              _button("Reemplazar…", replace_one),
                              _button("Reemplazar desde carpeta…", replace_folder), _button("Cerrar", win.close)))
        fill()
        win.show()
        win.parts = {"list": listing, "note": note, "preview": preview}
        self.images_window = win

    def describe_image(self):
        """Texto alternativo de la ilustración elegida: lo que dice un lector de pantalla en su lugar."""
        selected = self.tree.selection()
        if not self.book or not selected or not self.is_image(selected[0]):
            messagebox.showinfo("Describir ilustración", "Elige primero una ilustración en la lista.")
            return
        key = convert.image_key(self.book.blocks[int(selected[0])].image)
        text = _ask_line(self.root, "Describir ilustración", "Qué se ve, en una frase: lo lee en voz alta el lector de "
                         "pantalla de quien no la ve.\nVacío: «Ilustración de «capítulo»».", self.alts.get(key, ""))
        if text is None:
            return
        if text.strip():
            self.alts[key] = text.strip()
        else:
            self.alts.pop(key, None)
        self.status.setText("Descripción guardada" if text.strip() else "Sin descripción propia")

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
                 report=True, busy="Convirtiendo…")

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
            self.run(_batch, (ready, options), self.watch_done, report=True, busy="Carpeta vigilada:")

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
        labels = {"page": lambda: f"Portada: página {value + 1}", "image": lambda: f"Portada: ilustración de la "
                  f"página {self.book.blocks[int(value)].page}", "file": lambda: f"Portada: {Path(value).name}",
                  "text": lambda: "Portada: con título"}
        self.cover_label.setText(labels[kind]())
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

    def choose_cover(self):
        """Portada: miniaturas de las ilustraciones para elegir con un clic, o la página mostrada del PDF,
        o una imagen de un archivo."""
        if not self.book:
            return
        win, column = self.dialog("Elegir portada")
        grid = QListWidget()
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
                                 and (self.set_cover(("image", item.data(Qt.ItemDataRole.UserRole))), win.close()))
        column.addWidget(grid, 1)
        column.addLayout(_row(_button(f"Página mostrada del original ({self.pdf_page + 1})",
                                      lambda: (self.cover_from_page(), win.close())),
                              _button("Imagen de un archivo…", lambda: (win.close(), self.cover_from_file())),
                              _button("Portada con título", lambda: (self.set_cover(("text", "")), win.close())), 1))
        win.show()
        self.cover_window = win, grid

    def edit_more(self):
        """Los datos que no van en la tarjeta del libro: están en el panel «Editar datos»."""
        if not self.data_panel.isVisible():
            self.toggle_data()

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
            ("original_title", "Título original", self.original_title_var), ("subjects", "Géneros", self.subjects_var))
            if item[key] and not var.get().strip()]
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
            self.history.append(changed)
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
            self.history.append(changed)
            self.update_reviewed()
            self.update_preview()
        self.status.setText(f"{convert.counted(len(changed), 'apartado')} como separador")

    def apply_profile(self):
        """El perfil fija las opciones que importan para ese lector (PROFILES)."""
        for name, value in PROFILES.get(self.profile_var.get(), {}).items():
            self.fields()[name].set(value)
        self.update_preview()

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
        return list(self.reports[key]) + self.toc_issues() + self.comment_issues()

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
        path = filedialog.asksaveasfilename(defaultextension=".html", initialfile=name, parent=parent,
                                            initialdir=str(self.pdf.parent) if self.pdf else None,
                                            filetypes=[("Página web", "*.html"), ("Tabla para Excel", "*.csv")],
                                            title="Exportar el informe de calidad")
        if not path:
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
        """Informe de calidad (convert.quality_report): los avisos por gravedad y, del elegido, el problema,
        la solución, la vista previa de antes y después y el botón que lo arregla. Al elegir un aviso, las
        vistas previas de la ventana principal van a él."""
        if not self.book:
            return
        win, column = self.dialog("Informe de calidad")
        win.resize(1060, 600)
        levels = theme.level_colors(self.dark)
        summary = _label("", "title")
        summary.setWordWrap(True)  # nota, recuentos e ignorados: en 1093 px (125 %) baja a dos líneas
        show = qtk.Var("Todos")
        shown = _combo(show, ["Todos"] + [LEVEL_PLURAL[level].capitalize() for level in convert.LEVELS] + ["Ignorados"],
                       12, changed=lambda: fill())
        # Los ecos se activan en Opciones → Informe de calidad; al volver, la lista se rehace con ellos
        echoes = _button("Opciones del informe…", lambda: self.open_options(
            "Informe de calidad", on_close=lambda: shiboken6.isValid(win) and fill()))
        export_btn = _button("Exportar…", lambda: self.export_report(state["all"], state["score"], win))
        export_btn.setToolTip("Guardar el informe como página web (.html) o tabla para Excel (.csv), para otra persona")
        column.addLayout(_row(summary, 1, echoes, QLabel("Mostrar:"), shown, export_btn))
        panes = QSplitter(Qt.Orientation.Horizontal)
        column.addWidget(panes, 1)
        table = qtk.RowList(("level", "page", "where", "title"), {"level": "Gravedad", "page": "Pág.", "where": "Dónde",
                                                                  "title": "Aviso"}, {"level": 90, "page": 44, "where": 130})
        for level, color in levels.items():
            table.tag_configure(level, foreground=color)
        table.double_clicked.connect(lambda _: open_issue())  # doble clic: al editor, en el trozo exacto
        panes.addWidget(table)
        right = QWidget()
        side = QVBoxLayout(right)
        side.setContentsMargins(12, 0, 0, 0)
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
            side.addWidget(texts[name])
        previews = {}
        for name, caption in (("before", "Ahora"), ("after", "Con el arreglo")):
            side.addWidget(_label(caption, "muted"))
            box = qtk.TextBox()
            box.setReadOnly(True)
            box.setFixedHeight(96)
            for tag, look in theme.diff_tags(self.dark).items():
                box.tag_configure(tag, **look)
            side.addWidget(box)
            previews[name] = box
        fix_btn = _button("Arreglar", lambda: apply([state["report"][int(table.selection()[0])]]), "primary")
        all_btn = _button("Arreglar todos", lambda: apply(same_kind(state["report"][int(table.selection()[0])])))
        ignore_btn = _button("Ignorar", lambda: ignore(state["report"][int(table.selection()[0])]))
        edit_btn = _button("Abrir en el editor", lambda: open_issue())
        edit_btn.setToolTip("Abre el capítulo con el trozo del aviso elegido (también con doble clic en la lista)")
        side.addLayout(_row(fix_btn, all_btn, edit_btn, 1, ignore_btn))
        side.addStretch(1)
        panes.addWidget(right)
        panes.setSizes([470, 590])
        state = {"report": [], "places": []}

        def open_issue():
            chosen = table.selection()
            if chosen:
                self.edit_issue(state["report"][int(chosen[0])])

        def fill(select=0):
            everything = self.quality_report()
            ignored = [i for i in everything if _issue_key(i) in self.ignored]
            report = [i for i in everything if _issue_key(i) not in self.ignored]
            state["all"], state["score"] = report, convert.quality_score(report, self.word_count())
            summary.setText(f"Nota {state['score']}/100 · " + (_quality_summary(report) or "No se encontró nada sospechoso.")
                            + (f" · {convert.counted(len(ignored), 'ignorado')}" if ignored else ""))
            graves = any(i.level == "Grave" for i in report)
            summary.setStyleSheet(f"color: {levels['Grave']};" if graves else "")
            wanted = next((level for level in convert.LEVELS if LEVEL_PLURAL[level].capitalize() == show.get()), None)
            state["report"] = ignored if show.get() == "Ignorados" else [i for i in report if wanted in (None, i.level)]
            ignore_btn.setText("No ignorar" if show.get() == "Ignorados" else "Ignorar")
            win.setWindowTitle(f"Informe de calidad · {convert.counted(len(report), 'aviso')}")
            table.delete(*table.get_children())
            state["places"] = self.issue_places(state["report"])
            for n, issue in enumerate(state["report"]):
                table.insert("", "end", iid=str(n), values=(f"● {issue.level}", issue.page, state["places"][n][0],
                                                            issue.title), tags=(issue.level,))
            if state["report"]:
                item = str(min(select, len(state["report"]) - 1))
                table.selection_set(item)
                table.see(item)
            else:
                pick()

        def preview(box, text, spans, tag, empty):
            box.delete("1.0", "end")
            if text:
                box.insert("1.0", text)
                for i, j in spans:
                    box.tag_add(tag, f"1.0+{i}c", f"1.0+{j}c")
            else:
                box.insert("1.0", empty)
                box.tag_add("note", "1.0", "end")

        def same_kind(issue):
            return [i for i in state["report"] if i.kind == issue.kind and i.edit]

        def pick():
            chosen = table.selection()
            issue = state["report"][int(chosen[0])] if chosen else None
            heading.setText(issue.title if issue else "")
            heading.setStyleSheet(f"color: {levels[issue.level]};" if issue else "")
            place.setText(state["places"][int(chosen[0])][1] if issue else "")
            texts["problem"].setText(issue.problem if issue else "")
            texts["solution"].setText(issue.solution if issue else "")
            for box in previews.values():
                for tag in ("old", "new", "note"):
                    box.tag_remove(tag, "1.0", "end")
            old, new = _diff_spans(issue.before, issue.after) if issue and issue.after else ([], [])
            if issue and issue.edit and not issue.after:  # el arreglo quita el párrafo entero
                old = [(0, len(issue.before))]
            preview(previews["before"], issue.before if issue else "", old, "old",
                    f"(texto de la página {issue.page} del original: míralo en la vista previa)" if issue else "")
            preview(previews["after"], issue.after if issue else "", new, "new",
                    "(se quita)" if issue and issue.edit else "Sin arreglo automático: sigue la solución.")
            fix_btn.setEnabled(bool(issue and issue.edit))
            ignore_btn.setEnabled(bool(issue))
            count = len(same_kind(issue)) if issue and issue.edit else 0
            all_btn.setText(f"Arreglar todos los de este tipo ({count})")
            all_btn.setEnabled(count > 1)
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
            fill(int(table.selection()[0]) if table.selection() else 0)

        def ignore(issue):
            """Ignorar (o volver a mostrar) un aviso: se recuerda en la revisión. Un comentario del revisor se quita."""
            key = _issue_key(issue)
            if issue.kind == "comment":
                self.remove_comment(issue)
            else:
                self.ignored = [k for k in self.ignored if k != key] if key in self.ignored else self.ignored + [key]
            self.update_quality()
            fill(int(table.selection()[0]) if table.selection() else 0)

        table.selected.connect(pick)
        fill()
        win.show()
        win.parts = {"table": table, "summary": summary, "heading": heading, "texts": texts, "previews": previews,
                     "fix": fix_btn, "all": all_btn, "ignore": ignore_btn, "show": show, "fill": fill, "echoes": echoes,
                     "place": place, "edit": edit_btn}
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
        out = filedialog.asksaveasfilename(defaultextension=".epub", filetypes=[("EPUB", "*.epub")],
                                           initialdir=Path(pdfs[0]).parent,
                                           initialfile=_epub_name("Recopilatorio", self.kepub_var.get()))
        if not out:
            return
        out = _epub_name(out, self.kepub_var.get())
        self.save_review()  # el tomo abierto entra con lo revisado hasta ahora
        options = self.new_book_options()
        options["replacements"] = self.replacements
        if self.run(_omnibus, (pdfs, out, options), lambda problems: self.saved(out, problems), report=True,
                    busy="Preparando el recopilatorio…"):
            self.partial = f"{out}.tmp"

    def install(self):
        """Instala el .exe para este usuario (ver _install). Desde Python no hay .exe que instalar."""
        if not getattr(sys, "frozen", False):
            messagebox.showinfo("Instalar", "Esto se hace desde el .exe (PDF a EPUB.exe).")
            return
        exe, link = _install_paths()
        if not messagebox.askyesno("Instalar en este equipo", f"Se copiará el programa a:\n{exe}\n\ny se añadirá:\n"
                                   "· un acceso directo en el menú Inicio\n"
                                   "· «Convertir a EPUB» en el menú del clic derecho de los PDF\n\n¿Continuar?"):
            return
        try:
            _install(sys.executable)
        except (OSError, subprocess.CalledProcessError) as e:
            messagebox.showerror("Instalar", f"No se pudo instalar:\n{e}")
            return
        messagebox.showinfo("Instalar", "Instalado. Ya puedes abrirlo desde el menú Inicio o con clic derecho en "
                                        "un PDF → «Convertir a EPUB».")

    def uninstall(self):
        exe, link = _install_paths()
        if not messagebox.askyesno("Desinstalar", f"Se quitarán el acceso directo, «Convertir a EPUB» del menú de "
                                   f"los PDF y el programa copiado en:\n{exe}\n\nTus preferencias y revisiones se "
                                   "conservan. ¿Continuar?"):
            return
        try:
            _uninstall()
        except OSError as e:
            messagebox.showerror("Desinstalar", f"No se pudo desinstalar del todo:\n{e}")
            return
        messagebox.showinfo("Desinstalar", "Desinstalado.")

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
        self.history.append({"blocks": self.book.blocks, "candidates": self.book.candidates, "rows": rows,
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
                "typography": self.typography_var, "shrink": self.shrink_var, "shrink_size": self.shrink_size_var,
                "credits": self.credits_var, "gray": self.gray_var, "trim": self.trim_var, "font": self.font_var,
                "font_file": self.font_file_var, "epub2": self.epub2_var, "reading": self.reading_var, "profile": self.profile_var,
                "autocase": self.autocase_var, "kepub": self.kepub_var,
                "hyphenate": self.hyphenate_var, "whole_words": self.whole_words_var, "pdf_size": self.pdf_size_var}

    def rows(self):
        return {item: [self.tree.set(item, "inc"), self.tree.set(item, "title"), self.tree.set(item, "ok")]
                for item in self.tree.get_children()}

    def review_state(self):
        """Lo que se guarda del trabajo en este libro: en la revisión y en cada versión."""
        state = {name: var.get() for name, var in self.fields().items()}
        state.update(blocks=self.analyzed, edits=self.edits, description=self.description, cover=self.cover_choice,
                     replacements=self.replacements, rows=self.rows(), problems=self.last_problems, words=self.words,
                     glossary=self.glossary, tr_glossary=self.tr_glossary, ignored=self.ignored, comments=self.comments,
                     alts=self.alts, tr_notes=self.tr_notes, editor_at=self.editor_at, work=self.work)
        return state

    def save_review(self):
        if not (self.book and self.pdf):
            return
        state = self.review_state()
        try:
            state["pdf"] = _fingerprint(self.pdf)
            _write_json(_review_path(self.pdf), state)
            _remember_series(state)
            self.save_thumb()  # la portada elegida, para la tarjeta de recientes
        except OSError as e:  # PDF borrado, carpeta de solo lectura, OneDrive bloqueándolo: el trabajo en curso sigue
            self.status.setText(f"No se pudo guardar la revisión: {e.strerror or e}")

    def apply_state(self, state):
        """Pone en la ventana los campos y reemplazos de una revisión guardada (o heredada)."""
        for name, var in self.fields().items():
            if name in state:
                try:
                    var.set(state[name])
                except (ValueError, TypeError):
                    pass  # valor mal escrito a mano en el .json
        if self.style_var.get() not in convert.STYLES:  # el .json se puede editar a mano
            self.style_var.set("Clásico")
        font, path = self.font_var.get(), Path(self.font_file_var.get())
        if font not in FONT_CHOICES or font == "Otra…":
            if not (self.font_file_var.get() and path.name == font and path.is_file()):
                self.font_var.set("Del lector")
                self.status.setText(f"No se encontró la fuente {font}: se usa la del lector.")
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
        if state is None:
            state = _read_review(self.pdf, len(self.book.blocks)) if self.pdf else None
        if not state:
            return False
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
            versions.append({"name": name, "date": datetime.now().isoformat(timespec="seconds"), "auto": auto,
                             "state": state})
            _write_versions(self.pdf, versions)
        except OSError as e:
            self.status.setText(f"No se pudo guardar la versión: {e.strerror or e}")
            return False
        return True

    def restorable(self, state):
        """Una versión vale para este libro si es del mismo análisis: mismos bloques y mismo tamaño de PDF (la
        fecha no: OneDrive la cambia al sincronizar)."""
        try:
            return (state.get("blocks") == self.analyzed and isinstance(state.get("rows"), dict)
                    and state["pdf"][0] == _fingerprint(self.pdf)[0])
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
        for win in (getattr(self, name, None) for name in ("spelling_window", "page_window", "quality_window")):
            if win and shiboken6.isValid(win):  # tienen índices de bloques del libro de antes
                win.close()
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
        model.setEditable(True)  # se escribe a mano o se elige de los que da «Buscar modelos»
        model.setMinimumContentsLength(22)
        model.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
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
            model.clear()  # la lista buscada era de otro motor
            model.setEditText(prefs["models"].get(name) or info.get("model", ""))
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
                    table.insert("", "end", iid=str(k), values=(prefix.removeprefix(convert.CENTER)
                                                                 .removeprefix(convert.FRAME).removeprefix(convert.RIGHT) + text,
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
                current = model.currentText().strip()
                model.clear()
                model.addItems(result)
                model.setEditText(current)
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
        if not (self.book and self.pdf):
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

    def showEvent(self, event):
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


class TextEditor:
    """Editor del texto como Word, capítulo a capítulo: la cinta dice en qué capítulo se está y lleva a los
    demás (menú y ◀ ▶); la hoja (editor.Sheet) muestra cursiva, negrita, títulos, enlaces, ilustraciones y
    separadores como quedan; a la derecha la página del PDF del párrafo del cursor, grande, con zoom y
    sincronizada con la hoja. Cada capítulo visitado guarda su texto y su deshacer; al aceptar,
    App.apply_edits guarda lo cambiado de todos con convert.edit_section."""

    def __init__(self, app, item, start, end):
        self.app, self.item = app, item
        self.win = win = Dialog(app.root)
        win.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        # Esc y la X de la ventana preguntan antes de descartar cambios; leyendo en voz alta, Esc solo calla la voz
        # En modo concentración, Esc vuelve a la vista normal antes que nada
        win.on_reject = lambda: (self.stop_reading() if self.reading is not None else self.toggle_focus()
                                 if self.focused else self.cancel())
        self.focused, self.before_focus = False, []
        QShortcut(QKeySequence("F11"), win, self.toggle_focus)  # también con el foco fuera de la hoja
        self.reading, self.speaker = None, None  # párrafo que se lee en voz alta (QTextBlock) y la voz (Speaker)
        self.colors = theme.colors(app.dark)
        self.sheet = sheet = editor.Sheet()
        sheet.tags, sheet.unknown = theme.editor_tags(app.dark), set(app.unknown)  # y la última ortografía
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
        column.addWidget(self.diff_bar())
        # Hoja centrada sobre el escritorio gris, como la vista de impresión de Word
        desk = QFrame(objectName="desk")
        desk_row = QHBoxLayout(desk)
        desk_row.setContentsMargins(16, 16, 16, 16)  # también abajo: sin él la hoja tocaba el borde
        desk_row.addStretch(1)
        desk_row.addWidget(sheet, 20)
        desk_row.addStretch(1)
        # Página del PDF a la derecha: se ajusta al ancho del panel y se amplía con el zoom
        self.canvas = qtk.Preview()
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.canvas)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)  # más pequeña que el panel: en el medio
        self.page_label = _label("", "muted")
        self.page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zoom_label = _label("100 %", "muted")
        side = self.side = QWidget()
        side_column = QVBoxLayout(side)
        side_column.setContentsMargins(8, 8, 8, 0)
        side_column.addWidget(self.scroll, 1)
        # Fuera, de capítulo en capítulo (hojear página a página era lento para cruzar el libro); dentro, de página
        # ◀◀ y no los de saltar pista: Windows los dibuja como emoji azules
        prev_btn, next_btn = _button("◀◀", lambda: self.step_section(-1)), _button("▶▶", lambda: self.step_section(1))
        prev_btn.setToolTip("Capítulo anterior")
        next_btn.setToolTip("Capítulo siguiente")
        self.prev_page, self.next_page = _button("◀", lambda: self.step_page(-1)), _button("▶", lambda: self.step_page(1))
        self.prev_page.setToolTip("Página anterior")
        self.next_page.setToolTip("Página siguiente")
        side_column.addLayout(_row(prev_btn, self.prev_page, self.page_label, self.next_page, next_btn,
                                   1, _button("−", lambda: self.zoom(-25)), self.zoom_label,
                                   _button("+", lambda: self.zoom(25))))
        self.split = QSplitter()
        self.split.addWidget(self.nav_panel())
        self.split.addWidget(desk)
        self.split.addWidget(side)
        self.split.setStretchFactor(1, 1)
        self.split.setStretchFactor(2, 1)
        self.split.splitterMoved.connect(lambda *_: self.later("panel", 80, self.redraw))
        column.addWidget(self.split, 1)
        # Barra de estado como Word: dónde estás y el zoom del texto
        self.status = _label("", "muted")
        self.note = _label("", "muted")  # resultado de la última herramienta («Tipografía corregida en 3 párrafos»)
        self.text_zoom = QSlider(Qt.Orientation.Horizontal)
        self.text_zoom.setRange(50, 200)
        self.text_zoom.setValue(100)
        self.text_zoom.setFixedWidth(140)
        self.text_zoom_label = _label("100 %", "muted")
        self.text_zoom.valueChanged.connect(self.set_text_zoom)
        status_row = _row(self.status, self.note, 1,
                          _button("−", lambda: self.text_zoom.setValue(self.text_zoom.value() - 10)),
                          self.text_zoom, _button("+", lambda: self.text_zoom.setValue(self.text_zoom.value() + 10)),
                          self.text_zoom_label, _button("Aceptar", self.save, "primary"),
                          _button("Cancelar", self.cancel), margins=(12, 8, 12, 0))
        column.addLayout(status_row)
        self.shown, self.box, self.page, self.pdf_zoom = None, None, 0, 1.0
        self.drawn = None  # source_key() del dibujo actual del panel
        self.pending, self.closed = {}, False  # trabajo diferido: nombre -> temporizador
        self.set_text_zoom(100)
        self.open_section(start, end, int(item))
        self.recover_draft()
        self.offer_resume()
        self.opened, self.saved_paragraphs = time.monotonic(), 0  # para «Tu trabajo» de «Cifras»
        sheet.textChanged.connect(lambda: self.later("status", 300, self.update_status))
        # Sin reiniciar la cuenta en cada aviso (la ortografía repinta y avisa sin parar): como mucho 5 s de trabajo perdido
        sheet.textChanged.connect(lambda: "draft" in self.pending or self.later("draft", 5000, self.write_draft))
        sheet.textChanged.connect(lambda: self.later("diff", 150, self.update_diff))
        sheet.cursorPositionChanged.connect(lambda: self.later("diff", 150, self.update_diff))
        sheet.cursorPositionChanged.connect(lambda: self.later("source", 150, self.draw_source))
        sheet.cursorPositionChanged.connect(self.update_tools)
        # Un clic o una tecla en la hoja mientras se lee: la voz se calla, como al pulsar Parar
        sheet.cursorPositionChanged.connect(lambda: self.reading is not None and self.sheet.textCursor().position()
                                            != self.reading.position() and self.stop_reading())
        sheet.currentCharFormatChanged.connect(lambda _: self.update_tools())
        sheet.undoAvailable.connect(self.undo_btn.setEnabled)
        sheet.redoAvailable.connect(self.redo_btn.setEnabled)
        sheet.textChanged.connect(lambda: self.later("extra", 250, self.refresh_marks))
        sheet.textChanged.connect(lambda: self.later("spell", 1000, self.check_new_words))
        sheet.command.connect(self.on_command)
        sheet.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        sheet.customContextMenuRequested.connect(self.context_menu)
        self.spell = _start_spell_process() if app.spanish() else None  # el diccionario es de español
        if self.spell:  # todo el libro de una vez; luego, lo que se escriba
            blocks = [b for b in app.book.blocks if b.kind in ("p", "heading")]
            self.send_words(" ".join(convert._plain(b) for b in blocks))
            qtk.after(300, self.poll_spelling)
        editor.watch(self.scroll.viewport(), self.on_panel_event)
        editor.watch(win, lambda event: event.type() == QEvent.Type.Resize and self.later("ribbon", 60, self.fit_ribbon)
                     and False)
        win.finished.connect(lambda _: self.on_destroy())
        app.text_editor = self
        win.setModal(True)  # con la lista activa se podía abrir otro PDF u otro editor y aceptar sobre otros bloques
        win.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint)  # Word se abre a pantalla completa
        self.fit_ribbon()  # antes de mostrarla: con la cinta ancha, maximizada quedaba más ancha que la pantalla
        win.show()
        # Maximizada al crearla, Windows la dejaba del tamaño de su contenido: se maximiza ya mostrada
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
        # Botones que se quedan en su icono si la cinta no cabe (fit_ribbon): (botón, texto, icono, nivel)
        self.compact, self.ribbon_level, self.ribbon_rows = [], 0, []
        ink = self.colors["ink"]

        def big(button, icon):
            """Botón grande de Word: glifo arriba y nombre debajo."""
            button.setIcon(_glyph_icon(theme.ICONS[icon], ink))
            button.setIconSize(QSize(GLYPH, GLYPH))
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)

        def tool(text, tip, action, checkable=False, font=None, short=None, level=1, icon=None):
            button = QToolButton(objectName="tool")
            button.setText(text)
            button.setToolTip(tip)
            if short:
                self.compact.append((button, text, short, level))
            if icon:
                big(button, icon)
            button.setCheckable(checkable)
            if font:
                button.setFont(font)
            button.clicked.connect(lambda: (action(), self.sheet.setFocus(), self.update_tools()))
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
                    menu.addAction(item[0], lambda fn=item[1]: (fn(), self.sheet.setFocus(), self.update_tools()))
            button.setMenu(menu)
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
            for widgets in rows:
                box.addLayout(_row(*widgets, 1, spacing=2))
            box.addStretch(1)
            label = _label(caption, "muted")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            box.addWidget(label)
            row.addLayout(box)

        sheet = self.sheet
        bold_font, italic_font = QFont("Segoe UI", 11, QFont.Weight.Bold), QFont("Segoe UI", 11)
        italic_font.setItalic(True)

        page("Inicio")
        self.undo_btn = tool("↶ Deshacer", "Deshacer (Ctrl+Z)", sheet.undo, short="↶")
        self.redo_btn = tool("↷ Rehacer", "Rehacer (Ctrl+Y)", sheet.redo, short="↷")
        self.undo_btn.setEnabled(False)
        self.redo_btn.setEnabled(False)
        group("Deshacer", [self.undo_btn], [self.redo_btn])
        self.bold_btn = tool("N", "Negrita (Ctrl+N)", sheet.toggle_bold, True, bold_font)
        self.italic_btn = tool("K", "Cursiva (Ctrl+K)", sheet.toggle_italic, True, italic_font)
        self.case_btn = tool("Aa Mayúsculas", "Mayúsculas y minúsculas (Mayús+F3)", sheet.change_case, short="Aa",
                             level=2)
        self.sup_btn = tool("x²", "Superíndice: 1.ᵉʳ, m² (Ctrl+Mayús+=)", sheet.toggle_sup, True)
        self.caps_btn = tool("Vᴇʀs", "Versalitas: rangos, habilidades, sonidos en letra pequeña", sheet.toggle_small_caps,
                             True)
        group("Fuente", [self.bold_btn, self.italic_btn, self.sup_btn], [self.case_btn, self.caps_btn])
        self.style = QComboBox()
        self.style.addItems(STYLES)
        self.style.setToolTip("Estilo del párrafo: título de capítulo o subtítulo (Ctrl+T: capítulo o normal)")
        self.style.setMinimumWidth(110)
        self.style.activated.connect(lambda n: (sheet.set_heading(STYLE_LEVELS[n]), sheet.setFocus(),
                                                self.update_tools()))
        self.center_btn = tool("≡ Centrar", "Centrar el párrafo: cartas, carteles (Ctrl+E)", sheet.toggle_center, True,
                               short="≡", level=2)
        self.frame_btn = tool("▭ Recuadro", "Meter los párrafos elegidos en un recuadro, como las ventanas de estado, "
                              "o sacarlos de él", sheet.toggle_frame, True, short="▭", level=2)
        self.right_btn = tool("⇥ Derecha", "Alinear el párrafo a la derecha: firmas y fechas de cartas (Ctrl+D)",
                              sheet.toggle_right, True, short="⇥", level=2)
        group("Párrafo", [self.style, self.center_btn, self.right_btn], [self.frame_btn,
              tool("⤒ Unir", "Une el párrafo con el siguiente: arregla uno partido por un salto de página (Ctrl+J)",
                   sheet.join_next, short="Unir", level=2)])  # «⤒» solo se veía poco
        group("Edición", [tool("Buscar", "Buscar y reemplazar en todos los capítulos (Ctrl+F, Ctrl+H)",
                               lambda: self.show_find_bar(False), icon="search")])

        page("Insertar")
        self.symbols_btn, _ = menu_tool("Símbolos ▾", "Raya, comillas, signos de apertura y tipografía española", [
            ("— Raya   (Ctrl+Alt+-)", lambda: sheet.insert_symbol("—")),
            ("… Puntos suspensivos   (Ctrl+Alt+.)", lambda: sheet.insert_symbol("…")),
            ("« » Comillas   (Ctrl+Alt+Q)", lambda: sheet.wrap("«", "»")),
            ("¿ ? Pregunta", lambda: sheet.wrap("¿", "?")), ("¡ ! Exclamación", lambda: sheet.wrap("¡", "!")), None,
            ("Corregir tipografía del capítulo", self.fix_typography)], icon="symbols")
        group("Símbolos", [self.symbols_btn])
        group("Texto", [tool("Separador", "Separador de escena tras el párrafo", sheet.insert_break, icon="scene"),
                        tool("Nota", "Nota del traductor: en el EPUB sale al tocar la llamada", sheet.insert_note,
                             icon="note")])
        group("Vínculos", [tool("Enlace", "Enlace a una página web en el texto elegido (Ctrl+Alt+K)",
                                self.show_link_bar, icon="link")])
        group("Capítulo", [tool("Dividir\ncapítulo", "Dividir el capítulo: uno nuevo desde el cursor, para un capítulo "
                                "que el PDF unió con el anterior", self.show_split_bar, icon="split")])

        page("Revisar")
        self.changes_btn, self.changes_menu = menu_tool("Cambios ▾", "Párrafos que editaste en este capítulo", [
            ("Cambio siguiente   (F7)", self.next_change), ("Volver al original", self.restore_current)],
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
                                  "del libro", self.show_stats, icon="stats")])
        self.read_btn = tool("Leer", "Leer en voz alta desde el párrafo del cursor, para corregir de oído "
                             "(Esc, un clic o el mismo botón para parar)", self.toggle_reading, True, icon="read")
        group("Voz", [self.read_btn])

        page("Vista")
        self.nav_btn = tool("Navegación", "Panel con los títulos, avisos, comentarios y cambios del capítulo (F9)",
                            self.toggle_nav, True, icon="nav")
        # Casilla como las de «Mostrar» de Word; va a la par que «Mostrar cambios» del menú Cambios
        self.show_changes_box = QCheckBox("Mostrar cambios")
        self.show_changes_box.setToolTip("Marca en la hoja los párrafos que editaste")
        self.show_changes_box.toggled.connect(self.show_changes_action.setChecked)
        self.show_changes_action.toggled.connect(self.show_changes_box.setChecked)
        group("Mostrar", [self.nav_btn, self.show_changes_box])
        for row in self.ribbon_rows:
            row.addStretch(1)

        # A la derecha de las pestañas, como «Compartir» en Word: dónde estás y a dónde ir, en cualquier pestaña
        self.prev_chapter = tool("◀", "Capítulo anterior", lambda: self.step_section(-1))
        self.next_chapter = tool("▶", "Capítulo siguiente", lambda: self.step_section(1))
        self.chapter_btn = QToolButton(objectName="tool")
        self.chapter_btn.setToolTip("Ir a otro capítulo")
        self.chapter_btn.clicked.connect(self.show_chapters)
        self.review_btn = tool("✓", "Marca el capítulo como revisado y pasa al siguiente sin revisar (Ctrl+Intro)",
                               self.toggle_reviewed, True)
        column.addLayout(_row(tabs, 1, self.prev_chapter, self.chapter_btn, self.next_chapter, self.review_btn,
                              spacing=2))
        column.addWidget(pages)
        self.chapters_popup()
        return bar

    def fit_ribbon(self):
        """Si la cinta no cabe (1366 px, o 1093 con el zoom de Windows al 125 %), quedan solo con su icono
        primero deshacer, insertar y buscar, luego párrafo y revisar, y al final el botón de capítulo se
        estrecha y los grupos se juntan; el texto sigue en la ayuda."""
        if not shiboken6.isValid(self.ribbon_bar):
            return
        screen = self.win.screen().availableGeometry().width()
        room = min(self.win.width(), screen) if self.win.isVisible() else screen  # se abre maximizado
        column = self.win.layout()
        # Al medir un nivel ancho, Qt agrandaba la ventana a su mínimo y luego no la estrechaba: sin restricción
        column.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        try:
            for level in range(len(CHAPTER_WIDTHS)):
                self.ribbon_level = level
                for row in self.ribbon_rows:  # grupos más juntos si no cabe
                    row.setSpacing({0: 10, 1: 6, 2: 6}.get(level, 4))
                for button, text, short, needs in self.compact:
                    button.setText(short if level >= needs else text)
                self.show_chapter(next((n for n, s in enumerate(self.sections) if s[1:3] == self.current), None))
                self.show_counts()
                # Los botones piden su tamaño nuevo más tarde (LayoutRequest): sin esto se medía el de antes
                QApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest.value)
                if self.ribbon_bar.sizeHint().width() <= room:
                    break
        finally:
            column.setSizeConstraint(QLayout.SizeConstraint.SetDefaultConstraint)

    def chapters_popup(self):
        """Lista de capítulos bajo el botón: pequeña, con scroll y un filtro. Un QMenu con 34 capítulos
        (Mitsuha 1) se repartía en columnas por toda la pantalla y tapaba el editor."""
        pop = self.chapter_popup = QFrame(self.win, Qt.WindowType.Popup)
        pop.setObjectName("card")
        column = QVBoxLayout(pop)
        column.setContentsMargins(8, 8, 8, 8)
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
        pop.resize(460, 380)

    def show_chapters(self):
        """Abre la lista bajo el botón del capítulo, dentro de la pantalla y con el de ahora a la vista."""
        self.mark_changed()
        self.chapter_filter.clear()
        pop, button = self.chapter_popup, self.chapter_btn
        area = button.screen().availableGeometry()
        corner = button.mapToGlobal(button.rect().bottomRight())
        x = min(max(area.left(), corner.x() - pop.width()), area.right() - pop.width() - 8)  # sin tocar el borde
        y = min(corner.y() + 2, area.bottom() - pop.height() - 8)
        pop.move(x, y)
        pop.show()
        current = self.chapter_list.currentItem()
        if current:
            self.chapter_list.scrollToItem(current, QListWidget.ScrollHint.PositionAtCenter)
        self.chapter_filter.setFocus()

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
        self.regex_box.setToolTip(r"Expresión regular: \d+ cifras, (\w+) una palabra… En el reemplazo, \1 es lo "
                                  "del primer paréntesis")
        for widget in (self.find_entry, self.regex_box):  # la expresión se revisa al escribirla
            (widget.textChanged if widget is self.find_entry else widget.toggled).connect(lambda _: self.regex_error())
        close = _button("✕", lambda: (bar.hide(), self.sheet.setFocus()))
        close.setToolTip("Cerrar (Esc)")
        all_btn = _button("Todos", self.show_all_matches)
        all_btn.setToolTip("Lista de todos los resultados del libro")
        bar.setLayout(_row(self.find_entry, _button("◀", lambda: self.find(-1)), _button("▶", lambda: self.find(1)),
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
        bar.setLayout(_row(_label("Enlace:"), self.link_entry, _button("Aplicar", self.apply_link, "primary"),
                           _button("Quitar enlace", lambda: self.apply_link("")), self.link_info, 1,
                           _button("✕", lambda: (bar.hide(), self.sheet.setFocus())), margins=(10, 6, 10, 6)))
        self.escape_closes(bar, self.link_entry)
        bar.hide()
        return bar

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
                self.sheet.setFocus()
                return True
            return False

        for entry in entries:
            editor.watch(entry, handler)

    def on_command(self, name):
        """Atajos de la hoja que abren una barra: Ctrl+Alt+K enlace, Ctrl+F buscar, Ctrl+H reemplazar."""
        actions = {"link": self.show_link_bar, "reviewed": self.toggle_reviewed, "next_change": self.next_change,
                   "next_issue": self.next_issue, "nav": self.toggle_nav, "focus": self.toggle_focus}
        if name in actions:
            actions[name]()
        else:
            self.show_find_bar(name == "replace")

    def hide_bars(self):
        for bar in (self.find_frame, self.link_frame, self.split_frame, self.issue_frame, self.resume_frame):
            bar.hide()

    def toggle_focus(self):
        """F11: modo concentración, solo la hoja a pantalla completa, sin cinta, barras ni paneles. F11 o Esc
        vuelven como estaba."""
        self.focused = not self.focused
        if self.focused:
            self.before_focus = [w for w in (self.ribbon_bar, self.side, self.nav, self.find_frame, self.link_frame,
                                             self.split_frame, self.issue_frame, self.resume_frame, self.diff_frame)
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
        self.sheet.setFocus()

    # Mostrar cambios, como el control de cambios de Word: lo puesto subrayado en la hoja y el original en una barra
    def diff_bar(self):
        bar = self.diff_frame = QFrame(objectName="ribbon")
        self.diff_label = QLabel()
        self.diff_label.setTextFormat(Qt.TextFormat.RichText)
        self.diff_label.setWordWrap(True)
        self.diff_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        bar.setLayout(_row(_label("Cambios:", "muted"), self.diff_label, margins=(10, 6, 10, 6)))
        bar.hide()
        return bar

    def set_show_changes(self, on):
        color = theme.mark_colors(self.app.dark)["changed"] if on else None
        for page in self.pages.values():
            page["look"].show_changes(color)
        self.update_diff()

    def update_diff(self):
        """El original del párrafo del cursor con lo quitado tachado y lo puesto subrayado; sin cambios, se oculta."""
        if not shiboken6.isValid(self.diff_frame):
            return
        block = self.sheet.textCursor().block()
        original = block.blockFormat().property(editor.ORIGINAL)
        if (not self.show_changes_action.isChecked() or editor.objects_of(block) or not block.text().strip()
                or (original is not None and editor.block_lines(block) == [original])):
            self.diff_frame.hide()
            return
        if original is None:
            self.diff_label.setText("<i>Párrafo nuevo</i>")
        else:
            colors = theme.mark_colors(self.app.dark)
            parts, diff = [], _word_diff(editor.plain_line(original), block.text())
            for n, (text, tag) in enumerate(diff):
                text = html.escape(text)
                gap = " " if tag == "old" and n + 1 < len(diff) and diff[n + 1][1] == "new" else ""  # «cargada grávida»
                parts.append(f'<s style="color:{colors["Grave"]}">{text}</s>{gap}' if tag == "old" else
                             f'<u style="color:{colors["changed"]}">{text}</u>' if tag == "new" else text)
            self.diff_label.setText("".join(parts))
        self.diff_frame.show()

    def resume_bar(self):
        """«Lo dejaste en «Capítulo 5» · Ir allí»: el editor no salta solo, quien lo abre desde un aviso o una fila
        quiere ir ahí."""
        bar = self.resume_frame = QFrame(objectName="ribbon")
        self.resume_label = _label("")
        self.resume_go = _button("Ir allí", self.resume, "primary")
        bar.setLayout(_row(self.resume_label, self.resume_go, 1,
                           _button("✕", lambda: (bar.hide(), self.sheet.setFocus())), margins=(10, 6, 10, 6)))
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

    def show_link_bar(self):
        self.link_selection = QTextCursor(self.sheet.textCursor())  # el foco se va a la barra
        if not self.link_selection.hasSelection() and not self.sheet.link_at():
            self.link_info.setText("Elige primero el texto del enlace")
        else:
            self.link_info.setText("")
        self.link_entry.setText(self.sheet.link_at() or "https://")
        self.hide_bars()
        self.link_frame.show()
        self.link_entry.setFocus()
        self.link_entry.selectAll()

    def apply_link(self, href=None):
        """Pone (o quita, con "") el enlace en lo elegido al abrir la barra. Solo direcciones web."""
        href = self.link_entry.text().strip() if href is None else href
        if href and not re.fullmatch(r"https?://[^\s\"<>]+\.[^\s\"<>]+", href):
            self.link_info.setText("La dirección debe empezar por http:// o https://")
            return
        self.sheet.setTextCursor(self.link_selection)
        self.sheet.set_link(href)
        self.link_frame.hide()
        self.sheet.setFocus()

    def show_find_bar(self, replace):
        self.hide_bars()
        self.find_frame.show()
        cursor = self.sheet.textCursor()
        if cursor.hasSelection() and "\u2029" not in cursor.selectedText():
            self.find_entry.setText(cursor.selectedText())
        (self.replace_entry if replace and self.find_entry.text() else self.find_entry).setFocus()
        self.find_entry.selectAll()

    def find(self, delta):
        """Siguiente (o anterior) aparición en la hoja; al acabarse, sigue en los capítulos siguientes."""
        term = self.find_entry.text()
        if not term or self.regex_error():
            return False
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

    def replace_one(self):
        """Cambia la aparición elegida y busca la siguiente, como Word."""
        if self.regex_error():
            return
        if not self.sheet.replace_current(self.find_entry.text(), self.replace_entry.text(), *self.find_options()):
            self.find(1)
            return
        self.find(1)

    def replace_everywhere(self):
        """Cambia todas las apariciones en todos los capítulos; cada capítulo, un paso de su deshacer."""
        term, new = self.find_entry.text(), self.replace_entry.text()
        if not term or self.regex_error():
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
                           _button("✕", lambda: (bar.hide(), self.sheet.setFocus())), margins=(10, 6, 10, 6)))
        self.escape_closes(bar, self.split_entry)
        bar.hide()
        return bar

    def show_split_bar(self):
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
        self.sheet.setFocus()

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
                              _button("✕", lambda: (bar.hide(), self.sheet.setFocus()))))
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
        original = block.blockFormat().property(editor.ORIGINAL)
        return list(self.issues[k]) if original is not None and editor.block_lines(block) == [original] else []

    def block_issue(self, block):
        issues = self.block_issues(block)
        return issues[0] if issues else None

    @staticmethod
    def issue_range(block, issue):
        """(inicio, fin) del problema dentro del párrafo de la hoja; sin posición, todo el párrafo."""
        size = max(block.length() - 1, 0)
        start, end = issue.span or (0, size)
        start, end = min(start, size), min(end, size)
        return (start, end) if end > start else (0, size)

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
            if block == self.reading:  # el párrafo que se lee en voz alta, de fondo: sin elegirlo, que teclear no lo borre
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
        if not self.nav.isHidden():
            self.fill_nav()

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
        if not self.nav.isHidden():
            self.fill_nav()

    def fill_nav(self):
        """Cuatro grupos con su cuenta; cada entrada, el párrafo recortado. Los vacíos salen sin desplegar."""
        self.nav.clear()
        for name, entries in self.nav_entries.items():
            group = QTreeWidgetItem(self.nav, [f"{name} ({len(entries)})"])
            for block, text in entries:
                text = " ".join(text.replace("￼", "").split())
                item = QTreeWidgetItem(group, [text[:60] + ("…" if len(text) > 60 else "")])
                item.setToolTip(0, text)
                item.setData(0, Qt.ItemDataRole.UserRole, block.blockNumber())
            group.setExpanded(bool(entries))

    def nav_go(self, item):
        number = item.data(0, Qt.ItemDataRole.UserRole)
        if number is None:
            item.setExpanded(not item.isExpanded())
            return
        block = self.sheet.document().findBlockByNumber(number)
        if block.isValid():
            self.goto_block(block)
            self.sheet.setFocus()

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

    def next_change(self):
        """F7: el siguiente párrafo cambiado del capítulo (vuelve al principio al acabar)."""
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
        """F8: el siguiente aviso desde el cursor, también en los capítulos siguientes, con su barra."""
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
        self.issue_title.setStyleSheet(f"color: {theme.mark_colors(self.app.dark)[issue.level]};")
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
            extra += f" · selección: {convert.counted(len(selected.split()), 'palabra')}"
        column.addWidget(_label(extra, "muted"))
        column.addLayout(_row(1, _button("Cerrar", win.close, "primary")))
        win.show()
        self.stats_window = win

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

    def show_all_matches(self):
        """«Todos»: lista bajo la barra con cada resultado; clic va a él."""
        found = self.all_matches()
        if not found:
            self.find_info.setText("Sin resultados")
            QApplication.beep()
            return
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
        self.open_section(*self.sections[row][1:3])
        self.sheet.moveCursor(QTextCursor.MoveOperation.Start)
        for _ in range(count + 1):
            if not self.sheet.find_next(self.find_entry.text(), False, *self.find_options()):
                break
        self.sheet.ensureCursorVisible()

    # Ortografía en vivo
    def send_words(self, text):
        """Manda al proceso de ortografía las palabras de text que aún no revisó."""
        skip = convert.spell_skip(self.app.words)
        new = {w for w in convert.spell_words(text) if convert.spell_candidate(w, skip)} - self.checked
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
            action.triggered.connect(lambda _=False, s=suggestion: self.replace_word(self.context_cursor, s))
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
        block = here.block()
        for name in editor.objects_of(block):
            if name.startswith("Ilustración "):
                k = int(name.split()[1])
                menu.addAction("Cambiar ilustración…", lambda k=k: self.change_picture(k)).setToolTip(
                    "Para moverla, córtala (Ctrl+X) y pégala donde vaya")
                menu.addSeparator()
        offset = here.position() - block.position()
        colors = theme.mark_colors(self.app.dark)
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

    def change_picture(self, k):
        """Cambia la ilustración del bloque k por un archivo (App.replace_image) y la hoja la muestra ya."""
        if self.app.replace_image(k, parent=self.win):
            pictures = self.pictures(k, k + 1)
            if k in pictures:
                self.sheet.refresh_picture(k, pictures[k])
            self.drawn = None
            self.draw_source()

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
            self.split.setSizes([240, 10 ** 4, 10 ** 4])  # panel de navegación (si se ve), hoja y PDF a medias
            self.sheet.ensureCursorVisible()

    def update_tools(self):
        """La cinta sigue al cursor, como en Word: N y K marcadas si lo están, y el estilo del párrafo."""
        if not shiboken6.isValid(self.sheet) or not shiboken6.isValid(self.style):
            return
        f = self.sheet.currentCharFormat()
        self.bold_btn.setChecked(f.fontWeight() >= QFont.Weight.DemiBold)
        self.italic_btn.setChecked(f.fontItalic())
        level = self.sheet.heading_level()
        self.style.setCurrentIndex(0 if level is None else 1 if level == ON else 2)
        self.sup_btn.setChecked(self.sheet.is_sup())
        self.caps_btn.setChecked(self.sheet.is_small_caps())
        self.center_btn.setChecked(self.sheet.is_centered())
        self.right_btn.setChecked(self.sheet.is_right())
        self.frame_btn.setChecked(self.sheet.is_framed())

    def outline(self):
        """Secciones del libro para el panel de navegación: [(título, inicio, fin, sangría)], una por capítulo.
        Lo de antes del primero es «Inicio del libro»."""
        blocks, found = self.app.book.blocks, []
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
        if self.reading is not None:
            self.stop_reading()
        page = self.pages.get((start, end))
        if page is None:
            lines, levels = self.section_lines(start, end)
            doc, look = self.sheet.set_content(lines, self.pictures(start, end), self.colors["muted"], levels)
            page = self.pages[(start, end)] = {"doc": doc, "look": look, "lines": lines, "levels": levels}
            if self.show_changes_action.isChecked():
                look.show_changes(theme.mark_colors(self.app.dark)["changed"])
        else:
            self.sheet.use(page["doc"], page["look"])
        self.current = (start, end)
        if focus is not None:
            self.sheet.goto_source(focus)
        row = next((n for n, s in enumerate(self.sections) if s[1:3] == (start, end)), None)
        title = self.sections[row][0] if row is not None else self.app.tree.set(self.item, "title")
        self.win.setWindowTitle(f"Texto · {title}")
        self.show_chapter(row)
        self.undo_btn.setEnabled(page["doc"].isUndoAvailable())
        self.redo_btn.setEnabled(page["doc"].isRedoAvailable())
        self.drawn = None
        self.update_tools()
        self.draw_source()
        self.refresh_marks()
        if getattr(self, "spell", None):
            self.request_suggestions()
        self.sheet.ensureCursorVisible()
        self.sheet.setFocus()

    def section_lines(self, start, end):
        """Líneas de la sección como se cargan en la hoja, [(bloque, línea)], y {bloque: tipo} de sus títulos. Un
        título se ve con el nombre de su fila: el que irá al EPUB (renombrado, mayúsculas corregidas)."""
        book, tree = self.app.book, self.app.tree
        lines = [(k, "# " + tree.set(str(k), "title") if book.blocks[k].kind == "heading" and tree.exists(str(k))
                  else convert.section_text(book, k, k + 1)) for k in range(start, end)]
        levels = {k: tree.set(str(k), "inc") for k in range(start, end)
                  if book.blocks[k].kind == "heading" and tree.exists(str(k))}
        return lines, levels

    # Borrador: lo cambiado sin aceptar sobrevive a un cierre de la aplicación
    def write_draft(self):
        """Escribe el borrador con las secciones cambiadas; sin cambios lo borra. Un fallo no para el trabajo."""
        if not self.app.pdf or not shiboken6.isValid(self.win):
            return
        path = _draft_path(self.app.pdf)
        pages = [{"start": start, "end": end, "lines": [line for _, line in page["lines"]],
                  "text": editor.dump(page["doc"])} for (start, end), page in self.pages.items() if self.page_dirty(page)]
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
        """Indicador de la cinta: «☰ Capítulo 3 de 31 · Título ▾», y ◀ ▶ activos si hay a dónde ir."""
        chapters = sum(1 for s in self.sections if s[0] != FRONT)
        number = sum(1 for s in self.sections[:row + 1] if s[0] != FRONT) if row is not None else 0
        title = self.sections[row][0] if row is not None else self.app.tree.set(self.item, "title")
        where = FRONT if title == FRONT else f"Capítulo {number} de {chapters}"
        text = f"☰  {where} · {title}" if title != FRONT else f"☰  {FRONT}"
        # Ancho fijo con «…»: un título largo ensanchaba la cinta más que la pantalla (1366 px)
        width = CHAPTER_WIDTHS[self.ribbon_level]
        self.chapter_btn.setText(self.chapter_btn.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight,
                                                                           width - 30) + "  ▾")
        self.chapter_btn.setToolTip(f"{where}: {title}. Clic para ir a otro capítulo")
        self.chapter_btn.setFixedWidth(width)
        self.prev_chapter.setEnabled(row is not None and row > 0)
        self.next_chapter.setEnabled(row is not None and row < len(self.sections) - 1)
        self.update_review()  # la lista de capítulos («•» en los cambiados) se rehace al abrirla

    def step_section(self, delta):
        row = next((n for n, s in enumerate(self.sections) if s[1:3] == self.current), None)
        if row is not None and 0 <= row + delta < len(self.sections):
            self.open_section(*self.sections[row + delta][1:3])

    def page_starts(self):
        """{página del PDF: primer bloque que empieza en ella}, solo de lo que sale en los capítulos."""
        starts = {}
        for _, start, end, _ in self.sections:
            for k in range(start, end):
                starts.setdefault(self.app.book.blocks[k].page, k)
        return starts

    def step_page(self, delta):
        """◀ ▶ bajo el PDF: la hoja va al primer párrafo de la página anterior o siguiente (abriendo su capítulo si
        es otro) y el panel la dibuja. Las páginas sin nada del libro se saltan: no hay qué mostrar en la hoja."""
        here, starts = self.app.book.blocks[self.source_block()].page, self.page_starts()
        pages = [p for p in starts if (p > here if delta > 0 else p < here)]
        if not pages:
            return
        k = starts[min(pages) if delta > 0 else max(pages)]
        bounds = next(s[1:3] for s in self.sections if s[1] <= k < s[2])
        if bounds != self.current:
            self.open_section(*bounds)
        self.sheet.goto_source(k)
        self.sheet.ensureCursorVisible()
        self.draw_source()  # sin esperar a la pausa del cursor: el clic se ve al momento

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
        for k in range(start, end):
            block = self.app.book.blocks[k]
            if block.kind in ("image", "break") and block.image:
                try:
                    # el adorno transparente, del color del texto: un símbolo más, no una foto con fondo blanco
                    found[k] = qtk.qimage(pymupdf.Pixmap(block.image),
                                          self.colors["ink"] if block.kind == "break" else None)
                except Exception:
                    pass  # imagen que MuPDF no lee: queda su marca
            elif block.kind == "break":
                found[k] = html.unescape(block.html).strip() or "❖"
        return found

    def set_text_zoom(self, percent):
        self.sheet.set_zoom(percent)
        self.sheet.setMaximumWidth(int(820 * percent / 100))  # la hoja crece con la letra, como la página en Word
        self.text_zoom_label.setText(f"{percent} %")

    def later(self, name, ms, fn):
        """Ejecuta fn tras ms milisegundos sin otro aviso del mismo nombre: no repinta en cada tecla."""
        if self.closed:  # al cerrarse, el panel aún avisa de su tamaño: repintaba sobre la ventana ya cerrada
            return
        if name in self.pending:
            qtk.cancel(self.pending[name])
        self.pending[name] = qtk.after(ms, lambda: (self.pending.pop(name, None), fn()))

    def on_destroy(self):
        if self.app.text_editor is self:
            self.app.text_editor = None
        if shiboken6.isValid(self.sheet):  # al destruirse, la hoja aún avisaría de cambios del cursor
            self.sheet.blockSignals(True)
        for job in self.pending.values():
            qtk.cancel(job)
        self.pending.clear()
        self.closed = True
        self.drop_draft()  # se aceptó o se descartó: el borrador ya no hace falta
        # shortcut: tras aceptar, un párrafo añadido antes desplaza el bloque; se queda cerca, que basta para volver
        _log_work(self.app.work, datetime.now().date().isoformat(), paragraphs=self.saved_paragraphs,
                  minutes=(time.monotonic() - self.opened) / 60)
        if shiboken6.isValid(self.sheet) and self.sheet.current_source() is not None:
            self.app.editor_at = self.sheet.current_source()
        self.app.save_review()
        if self.spell:
            self.spell[2].terminate()  # nada que guardar: lo aprendido ya está en App.words
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
            self.speaker.done.connect(lambda: self.reading is not None and self.read_block(self.reading.next()))
            self.speaker.failed.connect(self.reading_failed)
        self.read_block(self.sheet.textCursor().block())

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
        self.pdf_zoom = min(3.0, max(0.5, self.pdf_zoom + delta / 100))
        self.zoom_label.setText(f"{round(self.pdf_zoom * 100)} %")
        self.redraw()

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
        párrafos unidos en la hoja se recuadran todos, no solo el primero."""
        k, blocks = self.source_block(), self.app.book.blocks
        joined = [j for j in self.sheet.current_sources()[1:] if j < len(blocks) and blocks[j].page == blocks[k].page]
        return k, joined, id(self.app.pdf_doc)

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
        if block.kind == "image" and block.image:
            pix = pymupdf.Pixmap(block.image)
            scale = min(self.panel_width() * self.pdf_zoom / pix.width, 1.5)
            if scale != 1:
                pix = pymupdf.Pixmap(pix, max(1, round(pix.width * scale)), max(1, round(pix.height * scale)), None)
            self.shown, self.box, self.page = ("image", k), None, block.page - 1
            self.put(pix)
            self.page_label.setText(f"Ilustración · pág. {block.page}")
        elif self.app.pdf_doc is None:
            self.shown, self.box = None, None
            size = (self.panel_width(), int(self.panel_width() * 1.4))
            self.canvas.setFixedSize(*size)
            self.canvas.show_blank(size, "Sin el PDF original no se puede mostrar la página",
                                   theme.colors(self.app.dark))
            self.page_label.setText("")
        else:
            page = self.app.pdf_doc[block.page - 1]
            boxes = [pymupdf.Rect(b.rect) if b.rect else self.find_box(page, b)
                     for b in [block] + [blocks[j] for j in joined]]
            boxes = [box for box in boxes if box]
            self.box = boxes[0] if boxes else None
            for box in boxes[1:]:
                self.box |= box
            self.show_page(block.page - 1)
        self.update_status()

    def show_page(self, index):
        """Dibuja la página index (desde 0) del PDF al ancho del panel por el zoom, con self.box recuadrado."""
        page = self.app.pdf_doc[index]
        self.page, self.shown = index, ("page", index + 1)
        zoom = self.panel_width() / page.rect.width * self.pdf_zoom
        marks = []
        if self.box:
            box = self.box * zoom
            marks.append((QRectF(box.x0 - 3, box.y0 - 3, box.width + 6, box.height + 6), MARK_COLORS["current"]))
        self.put(page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)), marks)
        if self.box:  # el párrafo a la vista aunque la página no quepa
            self.scroll.ensureVisible(int(self.box.x0 * zoom), int((self.box.y0 + self.box.y1) / 2 * zoom), 40,
                                      self.scroll.viewport().height() // 3)
        self.page_label.setText(f"Página {index + 1} de {self.app.pdf_doc.page_count}")

    def put(self, pix, marks=()):
        """Dibuja el pixmap en el panel, a su tamaño (con scroll si no cabe)."""
        image = qtk.qimage(pix)
        self.canvas.setFixedSize(image.width(), image.height())
        self.canvas.show_image((image.width(), image.height()), image, marks)

    @staticmethod
    def find_box(page, block):
        """Recuadro del párrafo en su página, de sus 6 primeras palabras a sus 6 últimas. None si el texto no
        aparece tal cual (palabra partida con guion en el PDF, párrafo ya editado)."""
        words = convert._plain(block).split()
        hits = page.search_for(" ".join(words[:6])) if words else []
        if not hits or (len(hits) > 1 and len(words) <= 6):  # línea corta repetida ("—Sí."): no se sabe cuál
            return None
        box = hits[0]
        tail = [r for r in page.search_for(" ".join(words[-6:])) if r.y0 >= box.y0]
        for n, r in enumerate(tail):  # unas palabras que cruzan de línea dan una pieza por línea
            if n and r.y0 > box.y1 + r.height:  # otra aparición más abajo
                break
            box |= r
        return box

    def update_status(self):
        """«Página 34 del PDF · párrafo 12 de 80 · 3.412 palabras»."""
        count, current, words = self.sheet.paragraphs()
        where = f"párrafo {current} de {count}" if current else f"{count} párrafo{'' if count == 1 else 's'}"
        page = self.app.book.blocks[self.source_block()].page
        starts = self.page_starts()
        self.prev_page.setEnabled(any(p < page for p in starts))
        self.next_page.setEnabled(any(p > page for p in starts))
        self.status.setText(f"Página {page} del PDF · {where} · "
                            f"{words:,} palabra{'' if words == 1 else 's'}".replace(",", "."))

    @staticmethod
    def page_dirty(page):
        """¿Cambió algo que edit_section lea en esa sección? Líneas en blanco y espacios sobrantes no cuentan."""
        def lines(text):
            return [line.strip() for line in text.splitlines() if line.strip()]
        return lines(editor.dump(page["doc"])) != lines("\n\n".join(line for _, line in page["lines"]))

    def dirty(self):
        return any(self.page_dirty(page) for page in self.pages.values())

    def changes(self):
        """Lo que cambió en las secciones visitadas, línea a línea frente a lo cargado: [[inicio, fin, texto]]
        de atrás adelante para edit_section, y {fila: título} de los títulos solo renombrados (conservan su
        tipo y su ✓)."""
        edits, titles, kinds, retype = [], {}, {}, {}
        for (start, _), page in self.pages.items():
            old = [line for _, line in page["lines"]]
            pairs = editor.dump_levels(page["doc"])  # [(línea, tipo de título o None)]
            new = [line for line, _ in pairs]
            for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
                k = str(start + i1)
                if tag == "equal":  # un título igual puede haber cambiado de tipo (capítulo ↔ subtítulo)
                    for n in range(i2 - i1):
                        level, was = pairs[j1 + n][1], page["levels"].get(start + i1 + n)
                        if level in (ON, OFF) and was is not None and level != was:
                            retype[str(start + i1 + n)] = level
                    continue
                if (tag == "replace" and i2 - i1 == j2 - j1 == 1 and old[i1].startswith("# ")
                        and new[j1].startswith("# ") and self.app.tree.exists(k)):
                    titles[k] = new[j1][2:].strip()
                    if pairs[j1][1] in (ON, OFF):
                        retype[k] = pairs[j1][1]
                else:
                    edits.append([start + i1, start + i2, "\n\n".join(new[j1:j2])])
                    kinds[start + i1] = [level for line, level in pairs[j1:j2] if line.startswith("# ")]
        edits.sort(reverse=True)
        return edits, titles, [kinds[e[0]] for e in edits], retype

    def changed_count(self):
        """Párrafos cambiados, nuevos o quitados en las secciones visitadas, frente a lo cargado."""
        count = 0
        for page in self.pages.values():
            old = [line for _, line in page["lines"]]
            new = [line for line in editor.dump(page["doc"]).split("\n\n") if line.strip()]
            count += sum(max(i2 - i1, j2 - j1) for op, i1, i2, j1, j2 in
                         difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes() if op != "equal")
        return count

    def save(self):
        """Aplica solo lo que cambió: un párrafo rehecho pierde su página del PDF, y un título rehecho su tipo."""
        if self.dirty():
            self.saved_paragraphs = self.changed_count()
            edits, titles, kinds, retype = self.changes()
            self.app.apply_edits(edits, self.item, titles, follow=True, kinds=kinds, retype=retype)
        self.win.done(0)

    def cancel(self, _=None):
        if self.dirty() and not messagebox.askyesno(
                "Descartar cambios", "El texto tiene cambios sin guardar. ¿Descartarlos?", parent=self.win):
            return
        self.win.done(0)


if __name__ == "__main__":
    multiprocessing.freeze_support()  # en el .exe, el proceso de trabajo vuelve a lanzar el ejecutable
    sys.excepthook = lambda kind, value, tb: _log("".join(traceback.format_exception(kind, value, tb)))
    qtk.app()
    root = Window()
    window = App(root)
    root.show()
    if len(sys.argv) > 1:  # PDF arrastrado sobre el .exe o "Abrir con…"
        qtk.after(200, window.open_path, sys.argv[1])
    sys.exit(qtk.app().exec())
