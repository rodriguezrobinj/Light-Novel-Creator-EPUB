"""Ventana para convertir PDFs de novelas ligeras a EPUB."""
import collections
import ctypes
import difflib
import html
import http.server
import io
import json
import multiprocessing
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import traceback
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path

import pymupdf
import segno
from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QActionGroup, QFont, QIcon, QImage, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMainWindow, QMenu, QPlainTextEdit, QProgressBar,
                               QPushButton, QSizePolicy, QSplitter, QStackedWidget, QTabBar, QToolButton,
                               QVBoxLayout, QWidget)

import convert
import qtk
import theme
from qtk import filedialog, messagebox

ON, OFF, CUT = "Capítulo", "Subtítulo", "Quitar"  # tipo de cada título de la lista
SEP = "Separador"                                   # título que se escribe como separador de escena
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
             "credits", "profile", "replacements", "kepub")
# Preferencias de la ventana entre sesiones (opciones, última carpeta y PDF recientes)
SETTINGS = Path(os.environ.get("APPDATA") or Path.home()) / "PDF a EPUB" / "ajustes.json"
PREFS = ("lang", "style", "separator", "typography", "shrink", "shrink_size", "gray", "credits", "trim", "font",
         "font_file", "epub2", "reading", "profile", "autocase", "kepub")
RECENT = 8  # PDF recientes que recuerda el menú
BOOK_FIELDS = ("title", "author", "series", "volume", "publisher", "translator", "description", "illustrator",
               "proofreader", "editor", "original_title", "subjects", "date")  # datos del libro en la revisión
# Perfil de salida -> opciones que fija (los demás perfiles dejan las opciones como estén)
PROFILES = {"Personalizado": {},
            "Celular": {"gray": False, "shrink": True, "shrink_size": 1200, "epub2": False, "kepub": False},
            "E-reader de tinta": {"gray": True, "shrink": True, "shrink_size": 1400, "epub2": False, "kepub": False},
            "Tablet": {"gray": False, "shrink": True, "shrink_size": 2000, "epub2": False, "kepub": False},
            # Kindle en color: Amazon convierte el EPUB y la app del celular y el Colorsoft lo muestran
            "Kindle": {"gray": False, "shrink": True, "shrink_size": 1600, "epub2": False, "kepub": False},
            "Kobo": {"gray": True, "shrink": True, "shrink_size": 1400, "epub2": False, "kepub": True},
            "Lector antiguo (EPUB 2)": {"gray": False, "shrink": True, "shrink_size": 1400, "epub2": True,
                                        "kepub": False}}
FONT_CHOICES = ["Del lector"] + list(convert.FONTS) + ["Otra…"]  # «Otra…» pide un archivo
APP_NAME = "Light Novel Creator"  # nombre visible; el .exe y la carpeta de ajustes siguen siendo «PDF a EPUB»
KIND_COLOR = theme.kind_colors(False)  # colores de cada tipo de fila; en oscuro, theme.kind_colors(True)
PREVIEW_BOX = (480, 640)  # ancho y alto máximos de cada vista previa, en píxeles
EDITOR_PANEL = (360, 480)  # ancho y alto máximos del panel del PDF en el editor de texto, en píxeles
PREVIEW_CHROME = 40       # alto de la fila ◀ leyenda ▶ de cada vista previa
DEVICES = {"Celular": (300, 560), 'E-reader 6"': (420, 560), "Tablet": (560, 750)}  # página en puntos
THUMB_HEIGHT = 64         # alto de las miniaturas del PDF, en píxeles
THUMBS_PER_TICK = 6       # miniaturas por tanda: dibujarlas todas de golpe congelaría la ventana
MARK_COLORS = {"title": "#1f6fd1", "image": "#2e9d4a", "current": "#f08c00"}  # bordes de las miniaturas
ICON = Path(__file__).parent / "icon.ico"  # en el .exe, junto a los módulos (--add-data icon.ico;.)
MARK = re.compile(r"</?[ib]>")  # marcas de cursiva y negrita del editor de texto
# Aspecto de las marcas en el editor de texto (theme.editor_tags); "broken" es lo que edit_section quitaría
EDITOR_TAGS = theme.editor_tags(False)
STYLE_TAGS = {1: "italic", 2: "bold", 3: "bolditalic"}  # cursiva suma 1 y negrita 2
LEVEL_COLOR = theme.level_colors(False)  # avisos del informe de calidad; en oscuro, theme.level_colors(True)
LEVEL_NAMES = {"Grave": ("grave", "graves"), "Revisar": ("por revisar", "por revisar"),
               "Menor": ("menor", "menores")}  # singular y plural: «1 grave», «2 graves»
LEVEL_PLURAL = {level: names[1] for level, names in LEVEL_NAMES.items()}
SHORTCUTS = ("Clic en Tipo, o teclas C (capítulo), S (subtítulo), X (separador), Q (quitar), I (ilustración),"
             " D/G (doble: dividir o girar)\nDoble clic o F2: editar título · E: editar texto\n"
             "Arrastrar o Alt+↑/↓: mover · Ctrl+F: buscar · Ctrl+Z: deshacer")


def _quality_summary(report):
    """"2 graves · 5 por revisar" de un informe de calidad, o "" si no hay avisos."""
    counts = collections.Counter(i.level for i in report)
    return " · ".join(f"{counts[level]} {LEVEL_NAMES[level][counts[level] != 1]}" for level in convert.LEVELS
                      if counts[level])


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
    """Corre en otro proceso: PyMuPDF retiene el GIL y, en un hilo, trabaría la ventana."""
    try:
        if report:
            result = work(*args, progress=lambda done, total: events.put(("progress", done, total)))
        else:
            result = work(*args)
        events.put(("done", result))
    except Exception as e:
        events.put(("error", str(e) or type(e).__name__))  # MemoryError() no trae mensaje
        events.put(("log", traceback.format_exc()))  # la ventana lo apunta en errores.log


def _analyze(pdf, progress=None):
    """convert.analyze y las ilustraciones repetidas, en el proceso de trabajo: en la ventana, buscarlas
    la congelaba (Kage 5: 2,4 s)."""
    book = convert.analyze(pdf, progress)
    return book, convert.duplicate_images(book)


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
            "breaks": list(breaks)}


def _write_checked(book, chapters, out, removed, options):
    """write_epub y, en el mismo proceso de trabajo, check_epub: devuelve los problemas del EPUB."""
    convert.write_epub(book, chapters, out, removed=removed, **options)
    return convert.check_epub(out)


def _epub_name(path, kepub):
    """Nombre del EPUB: el Kobo solo usa su motor KEPUB si el archivo acaba en .kepub.epub."""
    return re.sub(r"(\.kepub)?\.epub$", "", str(path), flags=re.I) + (".kepub.epub" if kepub else ".epub")


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
    if found:
        state = max(found, key=lambda f: f[:2])[2]
    else:  # tomo en otra carpeta: lo guardado al revisar otro tomo de la serie (_remember_series)
        state = _series_store().get(series.strip().lower(), {}) if series else {}
    return {name: state[name] for name in INHERITED if name in state}


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
    (cada uno cuenta los bloques tal como los dejó el anterior): textos editados [inicio, fin, texto]
    y secciones movidas ["move", inicio, fin, destino]. Devuelve los aplicados."""
    done = []
    for edit in state.get("edits", []):
        try:
            if edit[0] == "move":
                start, end, dest = int(edit[1]), int(edit[2]), int(edit[3])
                if not (0 <= start < end <= len(book.blocks) and 0 <= dest <= len(book.blocks)) or start <= dest < end:
                    break
                convert.move_section(book, start, end, dest)
                done.append(["move", start, end, dest])
                continue
            start, end, text = int(edit[0]), int(edit[1]), str(edit[2])
            if not 0 < start <= end <= len(book.blocks):
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
    for item, (kind, title) in rows.items():
        k = int(item)
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
            with pymupdf.open(pdf) as doc:
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
            problems = _write_checked(book, chapters, _epub_name(Path(pdf).with_suffix(""), written["kepub"]),
                                      removed, written)
            results.append((Path(pdf).name, "", problems))
        except Exception as e:
            results.append((Path(pdf).name, str(e) or type(e).__name__, []))
        if progress:
            progress(n + 1, len(pdfs))
    return results


ANILIST = "https://graphql.anilist.co"  # API pública, sin clave
ANILIST_QUERY = """query ($q: String) { Page(perPage: 6) { media(search: $q, type: MANGA, format: NOVEL) {
  title { romaji english native } startDate { year } genres description(asHtml: false)
  staff(perPage: 10) { edges { role node { name { full } } } } } } }"""
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
    """Respuesta de AniList -> [{name, author, illustrator, original_title, subjects, description}].
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
                      "description": re.sub(r"\n{3,}", "\n\n", text)})
    return found


WATCH_MS = 10_000  # cada cuánto se mira la carpeta vigilada


def _ready(folder, sizes, skip):
    """PDF de la carpeta vigilada listos para convertir: sin EPUB, fuera de skip y con el mismo tamaño
    que en la vuelta anterior (si cambia, aún se está copiando o descargando).
    Devuelve (listos, {pdf: tamaño} para la vuelta siguiente)."""
    ready, now = [], {}
    for pdf in sorted(Path(folder).glob("*.pdf")):
        if str(pdf) in skip or pdf.with_suffix(".epub").exists() or pdf.with_suffix(".kepub.epub").exists():
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


OPEN_WITH = r"Software\Classes\SystemFileAssociations\.pdf\shell\PDFaEPUB"  # "Abrir con" en el menú de los PDF


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
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, OPEN_WITH) as key:
        winreg.SetValue(key, "", winreg.REG_SZ, "Convertir a EPUB")
        winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, str(exe))
        winreg.SetValue(key, "command", winreg.REG_SZ, f'"{exe}" "%1"')
    return exe


def _uninstall():
    """Deshace _install: borra el acceso directo, la entrada del menú de los PDF y el .exe copiado."""
    import winreg
    exe, link = _install_paths()
    link.unlink(missing_ok=True)
    for sub in (OPEN_WITH + r"\command", OPEN_WITH):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)
        except OSError:
            pass  # ya no estaba
    if Path(sys.executable).resolve() != exe.resolve():  # el .exe en uso no se puede borrar
        exe.unlink(missing_ok=True)


def _is_paragraph(line):
    """Línea del editor de texto que es un párrafo: ni título, ni marca de ilustración o separador, ni
    separador escrito. Solo los párrafos se unen, se parten o pasan a título."""
    line = line.strip()
    return (bool(line) and not line.startswith("# ") and not convert.TOKEN.fullmatch(line)
            and not convert.SCENE_BREAK.fullmatch(line))


def _open_marks(text):
    """Marcas <i>/<b> que quedan abiertas al final de text, de fuera adentro."""
    stack = []
    for m in MARK.finditer(text):
        if not m.group().startswith("</"):
            stack.append(m.group()[1])
        elif stack and stack[-1] == m.group()[2]:
            stack.pop()
    return stack


def _balanced(text):
    """¿Cierran todas las marcas <i>/<b> y en orden? Si no, convert.edit_section quita todas las de la línea."""
    stack = []
    for m in MARK.finditer(text):
        if not m.group().startswith("</"):
            stack.append(m.group()[1])
        elif not stack or stack.pop() != m.group()[2]:
            return False
    return not stack


def _dropped_pdf(mime):
    """El primer .pdf de lo que se suelta sobre la ventana; None si no hay ninguno."""
    for url in mime.urls() if mime.hasUrls() else []:
        path = url.toLocalFile()
        if path.lower().endswith(".pdf") and Path(path).is_file():
            return path
    return None


def _volume_status(pdf):
    """Estado de un tomo: «EPUB generado», «EPUB anterior a la revisión», «Revisado, sin EPUB» o «Sin revisar»."""
    pdf = Path(pdf)
    state, epub = _read_review(pdf), pdf.with_suffix(".epub")
    if not epub.exists():
        epub = pdf.with_suffix(".kepub.epub")
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


def _label(text="", name=None):
    label = QLabel(text)
    if name:
        label.setObjectName(name)
    return label


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
        self.editor_save = None                # guarda y cierra el cuadro de edición abierto
        self.view = None                       # última posición del scroll de la lista
        self.pdf_doc = None                    # PDF abierto para la vista previa
        self.preview_path = os.path.join(tempfile.gettempdir(), f"lnepub-vista-{os.getpid()}.epub")
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
        self.font_last = "Del lector"  # a la que se vuelve si «Otra…» no elige archivo
        self.typography_var = qtk.Var(True)  # comillas «», puntos suspensivos, raya, espacios
        self.gray_var = qtk.Var(False)  # para e-readers de tinta: pesa menos y se ve igual
        self.shrink_var = qtk.Var(True)  # las imágenes son casi todo el peso del EPUB
        self.shrink_size_var = qtk.Var(convert.SHRINK)  # lado mayor de las imágenes reducidas
        self.trim_var, self.credits_var, self.reading_var = qtk.Var(False), qtk.Var(False), qtk.Var(False)
        self.epub2_var, self.kepub_var = qtk.Var(False), qtk.Var(False)

        root.setWindowTitle(APP_NAME)
        root.on_close, root.on_drop = self.close, self.open_path
        if ICON.exists():
            root.setWindowIcon(QIcon(str(ICON)))
        # A la medida de la pantalla libre: en 1366×768 con la barra de tareas, «Generar EPUB» debe verse
        area = root.screen().availableGeometry()
        # (setGeometry no cuenta la barra de título: unos 32 px encima y 8 de borde debajo)
        width, height = min(1300, area.width() - 20), min(760, area.height() - 48)
        root.setGeometry(area.x() + 10, area.y() + 34, width, height)
        sys.excepthook = self.on_error  # un fallo en un botón o atajo se apunta y se avisa

        central = QWidget()
        root.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Barra superior
        top = QFrame(objectName="bar")
        self.open_btn = _button("Abrir PDF…", self.open_pdf)
        self.batch_btn = _button("Convertir carpeta…", self.convert_folder)
        self.recent_menu = QMenu(root)
        tools = QMenu(root)
        tools.addAction("Buscar en el libro…  (Ctrl+F)", self.find)
        tools.addAction("Informe de calidad…", self.quality)
        tools.addAction("Panel de serie…", self.series_panel)
        tools.addAction("Volumen recopilatorio…", self.omnibus)
        tools.addAction("Vigilar una carpeta…", self.watch_folder)
        tools.addSeparator()
        themes = tools.addMenu("Tema")
        group = QActionGroup(root)
        self.theme_actions = {}
        for name in theme.CHOICES:
            action = themes.addAction(name)
            action.setCheckable(True)
            group.addAction(action)
            action.triggered.connect(lambda _, name=name: (self.theme_var.set(name), self.apply_theme()))
            self.theme_actions[name] = action
        tools.addAction("Atajos de teclado…", lambda: messagebox.showinfo("Atajos de teclado", SHORTCUTS))
        tools.addSeparator()
        tools.addAction("Instalar en este equipo", self.install)
        tools.addAction("Desinstalar", self.uninstall)
        self.file_label = _label("Ningún archivo", "muted")
        top.setLayout(_row(self.open_btn, self.batch_btn, _menu_button("Recientes", self.recent_menu),
                           _menu_button("Herramientas", tools), self.file_label, 1,
                           _button("Buscar", self.find), margins=(10, 8, 10, 8)))
        outer.addWidget(top)
        QShortcut(QKeySequence("Ctrl+F"), root, self.find)

        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)
        self.stack.addWidget(self.empty_page())
        page = QWidget()
        self.stack.addWidget(page)
        body = QVBoxLayout(page)
        body.setContentsMargins(10, 8, 10, 0)
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
        self.find_bar.setLayout(_row(self.find_entry, self.find_count, _button("✕", self.close_find),
                                     margins=(6, 4, 6, 4)))
        self.find_results = QListWidget()
        self.find_results.setMaximumHeight(160)
        self.find_bar.hide()
        self.find_results.hide()
        column.addWidget(self.find_bar)
        column.addWidget(self.find_results)
        self.tree = qtk.RowList(("inc", "title", "min", "page"),
                                {"inc": "Tipo", "title": "Título", "min": "Lectura", "page": "Página"},
                                {"inc": 92, "min": 64, "page": 60})
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
        device = _combo(self.device_var, list(DEVICES), 10, changed=self.draw_epub)
        dark = qtk.Var.check(self.dark_var, QCheckBox("Modo oscuro"))
        dark.toggled.connect(lambda _: self.draw_epub())
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
        extras = QMenu(root)
        for label, var in (("Limpieza tipográfica", self.typography_var), ("Imágenes en gris", self.gray_var),
                           ("Reducir imágenes", self.shrink_var),
                           ("Recortar márgenes blancos de las ilustraciones", self.trim_var),
                           ("Página de créditos", self.credits_var),
                           ("Minutos de lectura en el índice", self.reading_var),
                           ("EPUB 2 (lectores antiguos)", self.epub2_var),
                           ("Formato Kobo (KEPUB)", self.kepub_var)):
            action = extras.addAction(label)
            action.setCheckable(True)
            var.check(action)
            action.triggered.connect(lambda _: self.update_preview())
        self.font_box = _combo(self.font_var, FONT_CHOICES, 8, changed=self.font_changed)
        self.quality_btn = _button("Calidad", self.quality)
        self.quality_btn.setEnabled(False)
        self.gen_btn = _button("Generar EPUB…", self.generate, "primary")
        self.gen_btn.setEnabled(False)
        rows.addLayout(_row(_label("Perfil:"), _combo(self.profile_var, list(PROFILES), 15, changed=self.apply_profile),
                            _label("Separador:"), _combo(self.separator_var, convert.SEPARATOR_CHOICES, 12,
                                                         changed=self.update_preview),
                            _label("Estilo:"), _combo(self.style_var, list(convert.STYLES), 10,
                                                      changed=self.update_preview),
                            _label("Letra:"), self.font_box,
                            _menu_button("Opciones", extras), 1, self.gen_btn))
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedWidth(160)
        self.status = _label("", "muted")
        # Un estado largo se recorta en vez de ensanchar la ventana más allá de la pantalla
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        rows.addLayout(_row(self.progress, self.status, 1, self.quality_btn))
        outer.addWidget(bottom)

        self.tree.pressed.connect(self.on_click)
        self.tree.double_clicked.connect(self.on_double_click)
        self.tree.released.connect(self.on_drop)
        self.tree.selected.connect(self.update_preview)
        self.tree.verticalScrollBar().valueChanged.connect(lambda _: self.on_scroll())
        for key, kind in KEYS.items():
            self.tree.bind_key(getattr(Qt.Key, f"Key_{key.upper()}"), lambda kind=kind: self.shortcut(kind))
        self.tree.bind_key(Qt.Key.Key_F2, lambda: self.tree.selection() and self.edit_title(self.tree.selection()[0]))
        self.tree.bind_key(Qt.Key.Key_Z, self.undo, Qt.KeyboardModifier.ControlModifier)
        self.tree.bind_key(Qt.Key.Key_Up, lambda: self.move_selected(-1), Qt.KeyboardModifier.AltModifier)
        self.tree.bind_key(Qt.Key.Key_Down, lambda: self.move_selected(1), Qt.KeyboardModifier.AltModifier)
        self.tree.bind_key(Qt.Key.Key_E, self.edit_text)
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
        self.load_settings()
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
        icon = _label("📄")
        icon.setStyleSheet("font-size: 40pt;")
        for widget in (icon, _label("Suelta aquí un PDF de novela ligera", "big"), _label("o", "muted"),
                       _button("Abrir PDF…", self.open_pdf, "primary"),
                       _button("¿Muchos tomos? Convertir una carpeta entera", self.convert_folder, "link")):
            inner.addWidget(widget, 0, Qt.AlignmentFlag.AlignHCenter)
        inner.addStretch(1)
        recent = QFrame(objectName="card")
        column = QVBoxLayout(recent)
        column.addWidget(_label("Recientes", "title"))
        self.recent_list = QListWidget()
        self.recent_list.itemClicked.connect(lambda item: self.open_path(item.data(Qt.ItemDataRole.UserRole)))
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
                   ("Ilustraciones:", self.illustrator_var), ("Idioma:", self.lang_var)),
                  (("Corrección:", self.proofreader_var), ("Edición:", self.editor_var),
                   ("Título original:", self.original_title_var), ("Fecha:", self.date_var)))
        for r, row in enumerate(fields):
            for c, (label, var) in enumerate(row):
                grid.addWidget(QLabel(label), r, c * 2)
                if var is self.lang_var:
                    widget = _combo(var, LANGUAGES, 4, editable=True)
                else:
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
        titles.addSeparator()
        autocase = titles.addAction("Corregir las mayúsculas al abrir si vienen en MAYÚSCULAS")
        autocase.setCheckable(True)
        self.autocase_var.check(autocase)
        self.cover_label = _label("", "muted")
        grid.addLayout(_row(_button("Portada…", self.choose_cover), self.cover_label,
                            _button("Reemplazos…", self.edit_replacements), _menu_button("Títulos", titles), 1,
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
        if self.theme_var.get() in self.theme_actions:
            self.theme_actions[self.theme_var.get()].setChecked(True)
        colors = theme.kind_colors(self.dark)
        for tag in KIND_TAG.values():
            self.tree.tag_configure(tag, font=self.fonts[tag], foreground=colors[tag])
        self.update_quality()
        self.redraw()

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
        self.done, self.busy = done, busy
        self.proc = multiprocessing.Process(target=_worker, args=(self.events, work, args, report), daemon=True)
        self.proc.start()
        return True

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
        self.open_btn.setEnabled(True)
        self.batch_btn.setEnabled(True)
        if self.book:
            self.gen_btn.setEnabled(True)
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
        if editor:
            editor.win.done(0)
        self.poller.stop()
        self.watcher.stop()
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
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
        if dark:  # aproximación del modo oscuro de los lectores: invierte también las ilustraciones
            pix.invert_irect()
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
                blocks = self.book.blocks
                if is_image:
                    part_blocks = [convert.Block("image", block.page, image=block.image, layout=LAYOUT.get(kind, ""))]
                else:
                    end = next((k for k in range(index + 1, len(blocks)) if blocks[k].kind == "heading"), len(blocks))
                    part_blocks = blocks[index:end]
                part = convert.Book(self.book.title, "", self.book.cover, part_blocks, [])
                options = self.write_options([0] if kind == SEP else [])
                options.update(shrink=False, trim=False, epub2=False, credits=False)  # rápido: solo lo que se ve
                convert.write_epub(part, [(0, self.tree.set(item, "title"))] if kind == ON else [], self.preview_path,
                                   contents=False,  # sin página de índice: el capítulo 1 es la sección
                                   **options)
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
        marks = []
        if self.highlight and self.highlight[0] == self.pdf_page:  # recuadro sobre la línea tomada como título
            x0, y0, x1, y1 = (v * zoom for v in self.highlight[1])
            marks.append((QRectF(x0 - 3, y0 - 2, x1 - x0 + 6, y1 - y0 + 4), "red"))
        canvas.show_image(self.box_size(), image, marks)
        self.pdf_caption.setText(f"PDF · página {self.pdf_page + 1}")
        self.mark_current()

    def draw_epub(self):
        view = self.epub_view
        if self.epub_cut or self.epub_pages < 0:
            self.blank(view, self.epub_message if self.epub_cut else "Sin vista previa")
            self.epub_caption.setText("EPUB")
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
        items, selected = self.tree.get_children(), self.tree.selection()
        n = items.index(selected[0]) + delta if selected else -1
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
        self.file_label.setText(self.pdf.name)
        self.tree.delete(*self.tree.get_children())
        self.stack.setCurrentIndex(1)
        self.update_header()
        self.run(_analyze, (path,), lambda result: self.show_book(*result), report=True)

    def show_book(self, book, twins=None):
        """Pone en la ventana un libro analizado. twins: sus ilustraciones repetidas, si ya se buscaron."""
        self.book = book
        if self.pdf_doc:
            self.pdf_doc.close()
        self.pdf_doc = pymupdf.open(self.pdf)
        self.stack.setCurrentIndex(1)
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
        self.cover_choice = None
        self.cover_label.setText("Portada: la del PDF")
        self.edits, self.analyzed = [], len(book.blocks)
        self.fill_tree({})
        cut = convert.link_sections(book)  # publicidad del grupo: se propone quitarla
        for k in cut:
            self.set_kind(str(k), CUT)
        if twins is None:  # la misma ilustración otra vez: se propone descartarla
            twins = convert.duplicate_images(book)
        for k in twins:
            self.set_kind(str(k), DROPPED)
        heads = [i for i in self.tree.get_children() if not self.is_image(i)]
        if self.autocase_var.get() and _all_caps([self.tree.set(i, "title") for i in heads]):
            self.fix_case()
        self.idle_text = "Selecciona un título en la lista"
        self.blank(self.pdf_view, self.idle_text)
        self.blank(self.epub_view, self.idle_text)
        self.start_thumbs()
        self.gen_btn.setEnabled(True)
        self.progress.setValue(0)
        items = self.tree.get_children()
        found = len(book.candidates)
        images = sum(self.is_image(i) for i in items)
        dropped = sum(self.tree.set(i, "inc") == DROPPED for i in items)
        others = len(items) - found - images
        text = f"{found} capítulos detectados" if found else "No se detectaron capítulos: el EPUB tendrá una sola sección"
        text += f", {others} subtítulos" if others else ""
        text += f" y {images} ilustraciones" + (f" ({dropped} descartadas)" if dropped else "") if images else ""
        text += f" · {len(cut)} secciones del grupo en Quitar" if cut else ""
        text += f" · {len(twins)} ilustraciones repetidas descartadas" if twins else ""
        if self.load_review():
            text = "Revisión anterior recuperada"
        elif self.pdf:
            backup = _set_aside(self.pdf)
            if backup:
                text += f" · la revisión guardada no coincide con este análisis: copia en {backup.name}"
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
        heads = [i for i, b in enumerate(blocks) if b.kind == "heading"] + [len(blocks)]
        minutes = {k: convert.reading_minutes(blocks[k + 1:end]) for k, end in zip(heads, heads[1:])}
        for i, block in enumerate(blocks):
            if block.kind == "heading":
                mark, title = (ON if i in chapters else OFF), html.unescape(block.html)
            elif block.kind == "image":
                double = " doble" if str(i) in self.wide else ""
                mark, title = (IMG if block.keep else DROPPED), f"Ilustración{double} · página {block.page}"
            else:
                continue
            mark, title = rows.get(str(i), (mark, title))
            reading = f"{minutes[i]} min" if minutes.get(i) else ""
            self.tree.insert("", "end", iid=str(i), values=(mark, title, reading, block.page), tags=(KIND_TAG[mark],))
        self.update_quality()

    def is_image(self, item):
        return self.book is not None and self.book.blocks[int(item)].kind == "image"

    def on_click(self, event):
        item = self.tree.identify_row(event.y)
        column = self.tree.identify_column(event.x)
        if item and column == "#1":
            self.set_kind(item, self.cycle(item)[self.tree.set(item, "inc")])
            self.update_preview()
        self.drag_source = item if item and column == "#2" else None  # on_drop decide si se movió

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
                                           initialfile=_epub_name(self.pdf.stem, self.kepub_var.get()))
        if not out:
            return
        out = _epub_name(out, self.kepub_var.get())
        for name in BOOK_FIELDS:
            if hasattr(self, f"{name}_var"):
                setattr(self.book, name, getattr(self, f"{name}_var").get().strip())
        self.book.title = self.book.title or self.pdf.stem
        self.book.description = self.description
        if self.cover_choice and self.cover_choice[0] == "text":  # con el título y la serie de ahora
            self.book.cover = convert.text_cover(self.book)
        self.save_review()
        chapters, removed, breaks = _plan(self.book, self.rows())
        self.status.setText("Generando EPUB…")
        if self.run(_write_checked, (self.book, chapters, out, removed, self.write_options(breaks)),
                    lambda problems: self.saved(out, problems)):
            self.partial = f"{out}.tmp"  # el nombre temporal que usa write_epub

    def write_options(self, breaks=()):
        """Opciones de write_epub según la ventana (ver _write_options)."""
        state = {name: var.get() for name, var in self.fields().items()}
        state["replacements"] = self.replacements
        return _write_options(state, breaks)

    def saved(self, out, problems=()):
        self.status.setText(f"Guardado: {Path(out).name}" + (" (con avisos)" if problems else ""))
        self.last_problems = list(problems)
        self.save_review()  # el panel de serie muestra los avisos del último EPUB
        self.result_dialog(out, problems)

    def dialog(self, title, modal=False):
        """Ventana secundaria con su columna; sin modal, la principal sigue usable (como Toplevel)."""
        win = QDialog(self.root)
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
        is_file, kindle = Path(out).is_file(), self.profile_var.get() == "Kindle"
        win, column = self.dialog("EPUB guardado con avisos" if problems else "EPUB listo")
        heading = _label("La revisión encontró avisos" if problems else "Revisado: sin problemas.",
                         "warn" if problems else "title")
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
            buttons.append(_button("Abrir EPUB", lambda: self.open_external(out, win), "primary"))
        buttons.append(_button("Abrir carpeta", lambda: self.open_external(out, win, folder=True)))
        if is_file:
            buttons.append(_button("Enviar al móvil", lambda: self.send_to_phone(out)))
            if _calibre():
                buttons.append(_button("Añadir a Calibre", lambda: self.add_to_calibre(out)))
            for root, label in _drives():
                if _reader_kind(root) == "kindle":  # el Kindle no lee EPUB por USB: solo por Send to Kindle
                    kindle = True
                    column.addWidget(_label(f"{label} ({root[:2]}) es un Kindle: no lee EPUB por USB. "
                                            "Usa «Enviar a Kindle…».", "muted"))
                    continue
                buttons.append(_button(f"Copiar a {label} ({root[:2]})", lambda r=root: self.copy_to(out, r, win)))
            if kindle:
                buttons.append(_button("Enviar a Kindle…", lambda: self.send_to_kindle(out, win)))
        buttons.append(_button("Cerrar", win.close))
        column.addLayout(_row(*buttons, 1))
        win.show()
        self.result_window = win

    def copy_to(self, out, root, win):
        """Copia el EPUB al lector, en Books/ si la tiene, en el proceso de trabajo (USB lento)."""
        folder = Path(root) / "Books" if (Path(root) / "Books").is_dir() else Path(root)
        if self.run(shutil.copy2, (str(out), str(folder)),
                    lambda dest: messagebox.showinfo("Copiado", f"EPUB copiado en:\n{dest}"), busy="Copiando…"):
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
        win.finished.connect(lambda _: (server.shutdown(), server.server_close()))
        win.show()
        self.phone_window = win, server

    def add_to_calibre(self, out):
        """calibre.exe con un libro lo añade a su biblioteca, también si Calibre ya está abierto."""
        try:
            subprocess.Popen([_calibre(), str(out)])
        except OSError as e:
            messagebox.showerror("Añadir a Calibre", f"No se pudo abrir Calibre: {e.strerror or e}")
            return
        self.status.setText("Enviado a Calibre: lo añade a su biblioteca.")

    def send_to_kindle(self, out, win):
        """Send to Kindle no tiene API pública: se abre su web y el archivo, seleccionado, para arrastrarlo."""
        webbrowser.open("https://www.amazon.com/sendtokindle")
        self.open_external(out, win, folder=True)

    def open_external(self, path, win=None, folder=False):
        try:
            if folder:
                target = Path(path)
                args = ["explorer", f"/select,{target}"] if target.is_file() else ["explorer", str(target)]
                subprocess.Popen(args)
            else:
                os.startfile(path)
        except OSError as e:
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
        try:
            self.apply_state({name: self.settings[name] for name in PREFS if name in self.settings})
        except (ValueError, TypeError):
            pass  # valor mal escrito a mano en el .json
        chosen = self.settings.get("theme")
        self.theme_var.set(chosen if chosen in theme.CHOICES else "Sistema")
        self.fill_recent()

    def save_settings(self):
        fields = self.fields()
        self.settings.update({name: fields[name].get() for name in PREFS})
        self.settings["theme"] = self.theme_var.get()
        try:
            SETTINGS.parent.mkdir(parents=True, exist_ok=True)
            _write_json(SETTINGS, self.settings)
        except OSError:
            pass  # sin preferencias guardadas la próxima vez; nada más

    def fill_recent(self):
        """Menú «Recientes» y, en la pantalla vacía, la lista con el estado de cada tomo."""
        self.recent_menu.clear()
        self.recent_list.clear()
        recent = [p for p in self.settings.get("recent", []) if isinstance(p, str)]
        for path in recent:
            self.recent_menu.addAction(Path(path).name, lambda p=path: self.open_path(p))
            status = _volume_status(path) if Path(path).is_file() else "no se encuentra"
            item = QListWidgetItem(f"{Path(path).stem}\n{status}")
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(path)
            self.recent_list.addItem(item)
        if not recent:
            self.recent_menu.addAction("(ninguno)").setEnabled(False)
            item = QListWidgetItem("Aún no has abierto ningún PDF")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.recent_list.addItem(item)

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
            lines = [f"{find}: {n if isinstance(n, str) else f'{n} veces'}"
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

    def edit_text(self):
        """Abre el editor del texto de la sección seleccionada (de su título al siguiente)."""
        selected = self.tree.selection()
        if not self.book or not selected or self.is_image(selected[0]):
            return None
        item = selected[0]
        start = int(item) + 1
        end = next((k for k in range(start, len(self.book.blocks)) if self.book.blocks[k].kind == "heading"),
                   len(self.book.blocks))
        return TextEditor(self, item, start, end)

    def apply_edit(self, edit, item):
        self.apply_edits([edit], item)

    def apply_edits(self, edits, item):
        """Aplica textos editados: cambia los bloques, renumera la lista conservando los tipos elegidos
        y los apunta para la revisión. Guarda una copia de antes para deshacerlos de una vez con Ctrl+Z.
        Varios cambios van de atrás adelante: así los índices de los siguientes siguen valiendo."""
        rows = self.rows()
        self.history.append({"blocks": self.book.blocks, "candidates": self.book.candidates, "rows": rows,
                             "edits": len(self.edits), "item": item})  # edit_section crea listas nuevas
        for edit in edits:
            mapping = convert.edit_section(self.book, *edit)
            self.edits.append(list(edit))
            rows = {str(mapping[int(k)]): v for k, v in rows.items() if int(k) in mapping}
        self.fill_tree(rows)
        self.tree.selection_set(item)
        self.tree.see(item)
        self.update_preview()

    def find(self, _=None):
        """Ctrl+F: barra de búsqueda sobre la lista; al elegir un resultado, las vistas previas van a él."""
        if not self.book:
            return
        self.find_bar.show()
        self.find_entry.setFocus()
        self.find_entry.selectAll()

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
        existing = [p for p in pdfs if p.with_suffix(".epub").exists() or p.with_suffix(".kepub.epub").exists()]
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

    def watch_folder(self):
        """Activa o desactiva la carpeta vigilada; se recuerda entre sesiones."""
        if self.watch:
            if messagebox.askyesno("Carpeta vigilada", f"Se está vigilando:\n{self.watch}\n\n¿Dejar de vigilarla?"):
                self.set_watch("")
            return
        folder = filedialog.askdirectory(initialdir=self.settings.get("folder"), title="Carpeta que vigilar")
        if folder:
            self.set_watch(folder)
            messagebox.showinfo("Carpeta vigilada", f"Mientras esta ventana esté abierta, cada PDF nuevo de\n{folder}\n"
                                "se convierte solo, junto al PDF, con su revisión, los datos de su serie o las "
                                "opciones actuales.\n\nLos PDF que ya están en la carpeta no: para esos, "
                                "«Convertir carpeta…».")

    def set_watch(self, folder):
        # Lo que ya hay al activarla no se toca: solo lo que llegue después
        self.watch, self.watch_sizes = folder, {}
        self.watch_skip = {str(p) for p in Path(folder).glob("*.pdf")} if folder else set()
        self.settings["watch"] = folder
        self.status.setText(f"Vigilando {folder}" if folder else "Carpeta vigilada desactivada")

    def poll_watch(self):
        """Convierte los PDF nuevos de la carpeta vigilada, salvo si la ventana está ocupada con otro trabajo."""
        if not self.watch or self.done or not Path(self.watch).is_dir():
            return
        ready, self.watch_sizes = _ready(self.watch, self.watch_sizes, self.watch_skip)
        if ready:
            self.watch_skip.update(ready)  # un PDF que falla no se reintenta en cada vuelta
            fields = self.fields()
            options = {name: fields[name].get() for name in PREFS}
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
        column.addLayout(_row(_button(f"Página mostrada del PDF ({self.pdf_page + 1})",
                                      lambda: (self.cover_from_page(), win.close())),
                              _button("Imagen de un archivo…", lambda: (win.close(), self.cover_from_file())),
                              _button("Portada con título", lambda: (self.set_cover(("text", "")), win.close())), 1))
        win.show()
        self.cover_window = win, grid

    def edit_more(self):
        """Los datos que no van en la tarjeta del libro: están en el panel «Editar datos»."""
        if not self.data_panel.isVisible():
            self.toggle_data()

    def search_online(self):
        series = (self.series_var.get() or self.title_var.get()).strip()
        if not series:
            messagebox.showinfo("Buscar en internet", "Escribe primero la serie o el título del libro.")
            return
        if self.run(_lookup, (series,), lambda found: self.pick_online(found, series), busy="Buscando…"):
            self.status.setText(f"Buscando «{series}» en AniList…")

    def pick_online(self, found, series):
        """Lista de lo que encontró AniList: doble clic o Aceptar rellena los campos vacíos (apply_online)."""
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
                self.apply_online(found[box.currentRow()])

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
            if not self.is_image(item):
                title = change(self.tree.set(item, "title"))
                if title != self.tree.set(item, "title"):
                    changed.append(self.row_state(item))
                    self.tree.set(item, "title", title)
        if changed:
            self.history.append(changed)
            self.update_preview()
        self.status.setText(f"{done} en {len(changed)} títulos")

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
            if (not self.is_image(item) and self.tree.set(item, "inc") != SEP
                    and convert.SUBPART.fullmatch(self.tree.set(item, "title").strip())):
                changed.append(self.row_state(item))
                self.tree.set(item, "inc", SEP)
                self.tree.item(item, tags=(KIND_TAG[SEP],))
        if changed:
            self.history.append(changed)
            self.update_preview()
        self.status.setText(f"{len(changed)} apartados como separador")

    def font_changed(self):
        """«Otra…» pide un archivo de fuente; sin elegir ninguno, se queda la letra de antes."""
        if self.font_var.get() == "Otra…":
            path = filedialog.askopenfilename(filetypes=[("Fuente", "*.ttf *.otf")])
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

    def apply_profile(self):
        """El perfil fija las opciones que importan para ese lector (PROFILES)."""
        for name, value in PROFILES.get(self.profile_var.get(), {}).items():
            self.fields()[name].set(value)
        self.update_preview()

    def update_quality(self):
        """Botón de la barra inferior con los avisos del informe de calidad: dice qué falta antes de generar,
        en rojo si hay algo grave."""
        report = convert.quality_report(self.book) if self.book else []
        graves = sum(i.level == "Grave" for i in report)
        rest = _quality_summary([i for i in report if i.level != "Grave"])
        if graves:
            text = ("⚠ 1 aviso grave: revísalo antes de generar" if graves == 1
                    else f"⚠ {graves} avisos graves: revísalos antes de generar") + (f" · {rest}" if rest else "")
        else:
            text = f"⚠ {rest}" if rest else "✓ Sin avisos" if self.book else "Calidad"
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
        show = qtk.Var("Todos")
        shown = _combo(show, ["Todos"] + [LEVEL_PLURAL[level].capitalize() for level in convert.LEVELS], 12,
                       changed=lambda: fill())
        column.addLayout(_row(summary, 1, QLabel("Mostrar:"), shown))
        panes = QSplitter(Qt.Orientation.Horizontal)
        column.addWidget(panes, 1)
        table = qtk.RowList(("level", "page", "title"), {"level": "Gravedad", "page": "Pág.", "title": "Aviso"},
                            {"level": 100, "page": 50})
        for level, color in levels.items():
            table.tag_configure(level, foreground=color)
        panes.addWidget(table)
        right = QWidget()
        side = QVBoxLayout(right)
        side.setContentsMargins(12, 0, 0, 0)
        heading = _label("", "title")
        heading.setWordWrap(True)
        side.addWidget(heading)
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
        side.addLayout(_row(fix_btn, all_btn, _button("Editar texto…", lambda: self.edit_text()), 1))
        side.addStretch(1)
        panes.addWidget(right)
        panes.setSizes([420, 640])
        state = {"report": []}

        def fill(select=0):
            report = convert.quality_report(self.book)
            summary.setText(_quality_summary(report) or "No se encontró nada sospechoso.")
            graves = any(i.level == "Grave" for i in report)
            summary.setStyleSheet(f"color: {levels['Grave']};" if graves else "")
            wanted = next((level for level in convert.LEVELS if LEVEL_PLURAL[level].capitalize() == show.get()), None)
            state["report"] = [i for i in report if wanted in (None, i.level)]
            win.setWindowTitle(f"Informe de calidad · {len(report)} avisos")
            table.delete(*table.get_children())
            for n, issue in enumerate(state["report"]):
                table.insert("", "end", iid=str(n), values=(f"● {issue.level}", issue.page, issue.title),
                             tags=(issue.level,))
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
            texts["problem"].setText(issue.problem if issue else "")
            texts["solution"].setText(issue.solution if issue else "")
            for box in previews.values():
                for tag in ("old", "new", "note"):
                    box.tag_remove(tag, "1.0", "end")
            old, new = _diff_spans(issue.before, issue.after) if issue and issue.after else ([], [])
            if issue and issue.edit and not issue.after:  # el arreglo quita el párrafo entero
                old = [(0, len(issue.before))]
            preview(previews["before"], issue.before if issue else "", old, "old",
                    f"(texto de la página {issue.page} del PDF: míralo en la vista previa)" if issue else "")
            preview(previews["after"], issue.after if issue else "", new, "new",
                    "(se quita)" if issue and issue.edit else "Sin arreglo automático: sigue la solución.")
            fix_btn.setEnabled(bool(issue and issue.edit))
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
            item = next((str(j) for j in range(edits[-1][0], -1, -1)
                         if self.tree.exists(str(j)) and not self.is_image(str(j))), None)
            self.apply_edits(edits, item or self.tree.get_children()[0])
            self.status.setText(f"{len(edits)} avisos arreglados · Ctrl+Z para deshacer")
            fill(int(table.selection()[0]) if table.selection() else 0)

        table.selected.connect(pick)
        fill()
        win.show()
        win.parts = {"table": table, "summary": summary, "heading": heading, "texts": texts, "previews": previews,
                     "fix": fix_btn, "all": all_btn, "show": show, "fill": fill}
        self.quality_window = win

    def series_panel(self):
        """Los tomos de la carpeta con su estado; doble clic abre uno."""
        folder = self.pdf.parent if self.pdf else filedialog.askdirectory(initialdir=self.settings.get("folder"))
        if not folder:
            return
        win, column = self.dialog(f"Serie · {Path(folder).name}")
        table = qtk.RowList(("pdf", "state", "warn"), {"pdf": "Tomo", "state": "Estado", "warn": "Avisos"},
                            {"state": 200, "warn": 80}, stretch="pdf")
        table.setMinimumSize(680, 420)
        for pdf in sorted(Path(folder).glob("*.pdf")):
            state = _read_review(pdf)
            problems = state.get("problems") if state else None
            warn = "—" if problems is None else len(problems) or "Ninguno"
            table.insert("", "end", iid=str(pdf), values=(pdf.name, _volume_status(pdf), warn))
        table.double_clicked.connect(lambda _: table.selection() and (self.open_path(table.selection()[0]), win.close()))
        column.addWidget(table, 1)
        column.addWidget(_label("Doble clic: abrir el tomo", "muted"))
        win.show()
        self.series_window = win, table

    def omnibus(self):
        """Volumen recopilatorio: varios PDF (en orden de nombre) en un solo EPUB (ver _omnibus)."""
        if self.done:  # trabajo en curso: el menú no se desactiva como los botones
            return
        paths = filedialog.askopenfilenames(filetypes=[("PDF", "*.pdf")], initialdir=self.settings.get("folder"),
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
        options = {name: self.fields()[name].get() for name in PREFS}
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
        items = self.tree.get_children()
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
        if source and target and target != source and self.tree.exists(source):
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
                "autocase": self.autocase_var, "kepub": self.kepub_var}

    def rows(self):
        return {item: [self.tree.set(item, "inc"), self.tree.set(item, "title")] for item in self.tree.get_children()}

    def save_review(self):
        if not (self.book and self.pdf):
            return
        state = {name: var.get() for name, var in self.fields().items()}
        state.update(blocks=self.analyzed, edits=self.edits, description=self.description, cover=self.cover_choice,
                     replacements=self.replacements, rows=self.rows(), problems=self.last_problems)
        try:
            state["pdf"] = _fingerprint(self.pdf)
            _write_json(_review_path(self.pdf), state)
            _remember_series(state)
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
        self.last_problems = state.get("problems")
        if state.get("cover"):
            try:
                self.set_cover(state["cover"])
            except Exception:
                pass  # la imagen de portada ya no existe: se queda la del PDF
        return True

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
        self.find_count.setText(f"{len(self.matches)} resultados"
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
    """QDialog que pregunta a on_reject antes de cerrarse con Esc o con la X de la ventana."""
    on_reject = None

    def reject(self):
        if self.on_reject:
            self.on_reject()
        else:
            super().reject()


class TextEditor:
    """Editor del texto de una sección, de su título al siguiente: erratas, párrafos unidos o partidos,
    títulos que el análisis no vio ("# Título"), ilustraciones movidas. Mismo formato que
    convert.section_text; al aceptar, App.apply_edit lo aplica con convert.edit_section."""

    def __init__(self, app, item, start, end):
        self.app, self.item, self.start, self.end = app, item, start, end
        self.win = win = Dialog(app.root)
        win.setWindowTitle(f"Texto · {app.tree.set(item, 'title')}")
        win.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        win.on_reject = self.cancel  # Esc y la X de la ventana preguntan antes de descartar cambios
        area = app.root.screen().availableGeometry()
        width, height = min(1250, area.width() - 60), min(760, area.height() - 60)
        win.resize(width, height)
        panel_height = max(240, min(EDITOR_PANEL[1], height - 250))  # barra, ayuda, ◀ ▶, estado y botones
        self.panel = (min(EDITOR_PANEL[0], int(panel_height * 0.75)), panel_height)
        column = QVBoxLayout(win)
        column.setContentsMargins(12, 10, 12, 10)
        self.tools = QHBoxLayout()  # botones de formato
        column.addLayout(self.tools)
        column.addWidget(_label("Un párrafo por línea · «# Título» crea un título · [Ilustración N] y [Separador N]"
                                " se pueden mover o borrar", "muted"))
        self.text = text = qtk.TextBox()
        text.setFont(QFont("Segoe UI", 11))
        self.canvas = qtk.Preview()
        self.canvas.setFixedSize(*self.panel)
        self.page_label = _label("", "muted")
        self.page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        side = QVBoxLayout()
        side.addWidget(self.canvas, 0, Qt.AlignmentFlag.AlignHCenter)
        side.addLayout(_row(1, _button("◀", lambda: self.turn(-1)), self.page_label, _button("▶", lambda: self.turn(1)), 1))
        side.addStretch(1)
        column.addLayout(_row(text, side, spacing=10), 1)
        self.status = _label("", "muted")
        column.addWidget(self.status)
        column.addLayout(_row(1, _button("Aceptar", self.save, "primary"), _button("Cancelar", self.cancel)))
        self.shown, self.box, self.page, self.offset = None, None, 0, (0, 0)
        self.drawn = None  # (bloque, PDF) del dibujo actual del panel
        for tag, options in theme.editor_tags(app.dark).items():
            text.tag_configure(tag, **options)
        self.pending = {}  # trabajo diferido: nombre -> temporizador
        self.shortcuts = {}
        actions = (("Cursiva", "Ctrl+I", lambda: self.toggle_mark("i")),
                   ("Negrita", "Ctrl+B", lambda: self.toggle_mark("b")),
                   ("Título ↔ párrafo", "Ctrl+T", self.toggle_heading),
                   ("Separador", "", self.insert_break),
                   ("Unir con el siguiente", "Ctrl+J", self.join_next),
                   ("Partir aquí", "Ctrl+Return", self.split_here))
        for label, key, change in actions:
            shown_key = key.replace("Return", "Enter")
            self.tools.addWidget(_button(f"{label}  {shown_key}".strip(), lambda change=change: self.step(change)))
            if key:
                self.shortcuts[key] = QShortcut(QKeySequence(key), text, lambda change=change: self.step(change),
                                                context=Qt.ShortcutContext.WidgetShortcut)
        self.tools.addStretch(1)
        # Cada bloque con su tag src<k>: va en el formato del texto, se mueve con él y dice de qué bloque viene
        for k in range(start, end):
            if k > start:
                text.insert("end", "\n\n")
            text.insert("end", convert.section_text(app.book, k, k + 1), (f"src{k}",))
        self.original = text.get("1.0", "end-1c")
        text.edit_reset()
        text.mark_set("insert", "1.0")
        text.textChanged.connect(self.on_modified)
        text.cursorPositionChanged.connect(lambda: self.later("source", 150, self.draw_source))
        win.finished.connect(lambda _: self.on_destroy())
        self.highlight()
        self.draw_source()
        app.text_editor = self
        win.setModal(True)  # con la lista activa se podía abrir otro PDF u otro editor y aceptar sobre otros bloques
        win.show()
        text.setFocus()

    def later(self, name, ms, fn):
        """Ejecuta fn tras ms milisegundos sin otro aviso del mismo nombre: no repinta en cada tecla."""
        if name in self.pending:
            qtk.cancel(self.pending[name])
        self.pending[name] = qtk.after(ms, lambda: (self.pending.pop(name, None), fn()))

    def on_modified(self):
        self.later("refresh", 300, self.refresh)

    def on_destroy(self):
        if self.app.text_editor is self:
            self.app.text_editor = None
        for job in self.pending.values():
            qtk.cancel(job)
        self.pending.clear()

    def refresh(self):
        self.highlight()
        self.update_status()

    def source_block(self):
        """Bloque del libro del que viene la línea del cursor: el de su marca [Ilustración N], el de su tag
        src<k> o, en una línea nueva, el de la línea anterior más cercana que lo tenga. Sin ninguno (sección
        vacía), el título de la sección."""
        text = self.text
        n = int(text.index("insert").split(".")[0])
        token = convert.TOKEN.fullmatch(text.get(f"{n}.0", f"{n}.end").strip())
        if token and int(token.group(2)) < len(self.app.book.blocks):
            return int(token.group(2))
        for m in range(n, 0, -1):
            for index in (f"{m}.0", f"{m}.end-1c"):
                for tag in text.tag_names(index):
                    if tag.startswith("src"):
                        return int(tag[3:])
        return int(self.item)

    def draw_source(self):
        """Panel lateral: la ilustración del bloque del cursor, o su página del PDF con el párrafo recuadrado."""
        k = self.source_block()
        block = self.app.book.blocks[k]
        if (k, id(self.app.pdf_doc)) == self.drawn:  # mismo bloque: la página ya está dibujada
            self.update_status()
            return
        self.drawn = (k, id(self.app.pdf_doc))
        if block.kind == "image" and block.image:
            pix = pymupdf.Pixmap(block.image)
            scale = min(self.panel[0] / pix.width, self.panel[1] / pix.height, 1)
            if scale < 1:
                pix = pymupdf.Pixmap(pix, max(1, round(pix.width * scale)), max(1, round(pix.height * scale)), None)
            self.shown, self.box, self.page = ("image", k), None, block.page - 1  # ◀ ▶ siguen desde aquí
            self.put(pix)
            self.page_label.setText(f"Ilustración · pág. {block.page}")
        elif self.app.pdf_doc is None:
            self.shown, self.box = None, None
            self.canvas.show_blank(self.panel, "Sin el PDF original no se puede mostrar la página",
                                   theme.colors(self.app.dark))
            self.page_label.setText("")
        else:
            page = self.app.pdf_doc[block.page - 1]
            self.box = pymupdf.Rect(block.rect) if block.rect else self.find_box(page, block)
            self.show_page(block.page - 1)
        self.update_status()

    def show_page(self, index):
        """Dibuja la página index (desde 0) del PDF, con self.box recuadrado si lo hay."""
        page = self.app.pdf_doc[index]
        self.page, self.shown = index, ("page", index + 1)
        zoom = min(self.panel[0] / page.rect.width, self.panel[1] / page.rect.height)
        marks = []
        if self.box:
            box = self.box * zoom
            marks.append((QRectF(box.x0 - 3, box.y0 - 3, box.width + 6, box.height + 6), MARK_COLORS["current"]))
        self.put(page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)), marks)
        self.page_label.setText(f"Página {index + 1} de {self.app.pdf_doc.page_count}")

    def put(self, pix, marks=()):
        """Dibuja el pixmap centrado en el panel."""
        self.offset = self.canvas.show_image(self.panel, qtk.qimage(pix), marks)

    def turn(self, delta):
        """◀ ▶: hojea el PDF sin mover el cursor; el siguiente movimiento vuelve a la página de su párrafo."""
        if self.app.pdf_doc is None:
            return
        self.box, self.drawn = None, None  # el próximo movimiento del cursor vuelve a su página
        self.show_page(min(max(self.page + delta, 0), self.app.pdf_doc.page_count - 1))

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
        lines = self.text.get("1.0", "end-1c").split("\n")
        paragraphs = [n for n, line in enumerate(lines, start=1) if _is_paragraph(line)]
        current = int(self.text.index("insert").split(".")[0])
        words = sum(len(MARK.sub("", lines[n - 1]).split()) for n in paragraphs)
        count = len(paragraphs)
        where = (f"párrafo {paragraphs.index(current) + 1} de {count}" if current in paragraphs
                 else f"{count} párrafo{'' if count == 1 else 's'}")
        page = self.app.book.blocks[self.source_block()].page
        self.status.setText(f"Página {page} del PDF · {where} · "
                            f"{words:,} palabra{'' if words == 1 else 's'}".replace(",", "."))

    def step(self, change):
        """Una acción del editor como un solo paso de deshacer."""
        with self.text.group():
            change()
        self.highlight()
        self.later("source", 150, self.draw_source)  # el cursor pudo moverse (unir, partir…)
        self.text.setFocus()
        return "break"

    def toggle_mark(self, mark):
        """Envuelve la selección en <i>/<b>, o quita las marcas si ya la envuelven. Sin selección, deja las
        marcas vacías con el cursor dentro. No cruza párrafos ni corta otra marca: quedaría mal anidada y
        edit_section quitaría todas las de la línea. Tampoco toca [Ilustración N]: dejaría de reconocerse."""
        text, opening, closing = self.text, f"<{mark}>", f"</{mark}>"
        selected = bool(text.tag_ranges("sel"))
        first = text.index("sel.first") if selected else text.index("insert")
        last = text.index("sel.last") if selected else first
        if last != first and last.endswith(".0"):  # triple clic: la línea más su salto
            last = text.index(f"{last}-1c")
        line = text.get(f"{first} linestart", f"{first} lineend").strip()
        inside = text.get(first, last)
        if (first.split(".")[0] != last.split(".")[0] or convert.TOKEN.fullmatch(line)
                or (line and convert.SCENE_BREAK.fullmatch(line))):
            self.text.bell()
        elif first == last:
            text.insert("insert", opening + closing)
            text.mark_set("insert", f"insert-{len(closing)}c")
        elif (text.get(f"{first}-{len(opening)}c", first) == opening
                and text.get(last, f"{last}+{len(closing)}c") == closing):
            text.delete(last, f"{last}+{len(closing)}c")
            text.delete(f"{first}-{len(opening)}c", first)
        elif inside.startswith(opening) and inside.endswith(closing) and _balanced(inside):  # marcas incluidas
            text.delete(f"{last}-{len(closing)}c", last)
            text.delete(first, f"{first}+{len(opening)}c")
        elif not _balanced(inside):
            self.text.bell()
        else:
            text.insert(last, closing)
            text.insert(first, opening)

    def toggle_heading(self):
        """Pone o quita "# " delante de la línea del cursor. No en una línea vacía: "# " solo se leería
        como el separador "#"."""
        text = self.text
        if text.get("insert linestart", "insert linestart+2c") == "# ":
            text.delete("insert linestart", "insert linestart+2c")
        elif _is_paragraph(text.get("insert linestart", "insert lineend")):
            text.insert("insert linestart", "# ")
        else:
            self.text.bell()

    def insert_break(self):
        """Separador de escena escrito, como párrafo propio tras la línea del cursor."""
        end = self.text.index("insert lineend")
        self.text.insert(end, "\n\n* * *")
        self.text.mark_set("insert", f"{end}+7c")

    def join_next(self):
        """Une el párrafo del cursor con el siguiente: arregla un párrafo partido por un salto de página."""
        text = self.text
        n = int(text.index("insert").split(".")[0])
        last = int(text.index("end-1c").split(".")[0])
        following = next((m for m in range(n + 1, last + 1) if text.get(f"{m}.0", f"{m}.end").strip()), None)
        line = text.get(f"{n}.0", f"{n}.end")
        if following is None or not _is_paragraph(line) or not _is_paragraph(
                text.get(f"{following}.0", f"{following}.end")):
            self.text.bell()
            return
        nxt = text.get(f"{following}.0", f"{following}.end")
        end = f"{n}.{len(line.rstrip())}"
        text.delete(end, f"{following}.{len(nxt) - len(nxt.lstrip())}")
        text.insert(end, " ")
        text.mark_set("insert", f"{end}+1c")

    def split_here(self):
        """Parte el párrafo en el cursor. Una cursiva o negrita abierta se cierra y se vuelve a abrir: si no,
        cada mitad quedaría con una marca suelta y edit_section la quitaría."""
        text = self.text
        if not _is_paragraph(text.get("insert linestart", "insert lineend")):
            self.text.bell()
            return
        while text.get("insert-1c") == " ":
            text.delete("insert-1c")
        while text.get("insert") == " ":
            text.delete("insert")
        opened = _open_marks(text.get("insert linestart", "insert"))  # de fuera adentro: se cierran al revés
        text.insert("insert", "".join(f"</{m}>" for m in reversed(opened)) + "\n\n"
                    + "".join(f"<{m}>" for m in opened))

    def highlight(self):
        """Pone los tags de aspecto según las marcas. Solo cambia tags, no texto: no entra en deshacer."""
        text, blocks, used = self.text, self.app.book.blocks, set()
        for tag in EDITOR_TAGS:
            text.tag_remove(tag, "1.0", "end")
        for n, line in enumerate(text.get("1.0", "end-1c").split("\n"), start=1):
            bare = line.strip()
            token = convert.TOKEN.fullmatch(bare)
            if bare.startswith("# "):
                text.tag_add("heading", f"{n}.0", f"{n}.end")
            elif token:
                # Las mismas condiciones que edit_section: si no, la marca saldría como texto en el EPUB
                k = int(token.group(2))
                valid = (self.start <= k < self.end and k not in used
                         and blocks[k].kind == ("image" if token.group(1) == "Ilustración" else "break"))
                used.add(k)
                text.tag_add("token" if valid else "broken", f"{n}.0", f"{n}.end")
            elif bare and convert.SCENE_BREAK.fullmatch(bare):
                text.tag_add("scene", f"{n}.0", f"{n}.end")
            flags = [0] * len(line)
            for flag, mark in ((1, "i"), (2, "b")):
                for m in re.finditer(rf"<{mark}>(.*?)</{mark}>", line):
                    for c in range(m.start(1), m.end(1)):
                        flags[c] |= flag
            run = 0
            for c in range(1, len(line) + 1):
                if c == len(line) or flags[c] != flags[run]:
                    if flags[run]:
                        text.tag_add(STYLE_TAGS[flags[run]], f"{n}.{run}", f"{n}.{c}")
                    run = c
            broken = not _balanced(line)
            for m in MARK.finditer(line):
                text.tag_add("broken" if broken else "mark", f"{n}.{m.start()}", f"{n}.{m.end()}")

    def dirty(self):
        """¿Cambió algo que edit_section lea? Líneas en blanco y espacios sobrantes no cuentan."""
        def lines(text):
            return [line.strip() for line in text.splitlines() if line.strip()]
        return lines(self.text.get("1.0", "end-1c")) != lines(self.original)

    def save(self):
        """Aplica el texto solo si cambió: edit_section rehace los párrafos y perderían su página del PDF."""
        if self.dirty():
            self.app.apply_edit([self.start, self.end, self.text.get("1.0", "end")], self.item)
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
