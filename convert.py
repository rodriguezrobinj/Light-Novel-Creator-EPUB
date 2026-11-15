"""Convierte PDFs de novelas ligeras en EPUB con texto fluido."""
import base64
import collections
import colorsys
import csv
import difflib
import functools
import hashlib
import html
import io
import os
import re
import tempfile
import unicodedata
import uuid
import xml.etree.ElementTree as ET
import zipfile
import zlib
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

import pymupdf
from PIL import Image, ImageOps

MEMO_SIZE = 50000  # resultados por función de _memo: de sobra para varios libros


def _memo(fn):
    """Como functools.lru_cache de un argumento, con su dict a la vista (.cache): el proceso de trabajo se lo
    pasa a la ventana. Lleno, se vacía entero: más simple que llevar el orden de uso."""
    cache = {}

    @functools.wraps(fn)
    def memo(arg):
        try:
            return cache[arg]
        except KeyError:
            pass
        if len(cache) >= MEMO_SIZE:
            cache.clear()
        cache[arg] = value = fn(arg)
        return value

    memo.cache = cache
    return memo


BAND = 0.12     # franja superior/inferior (fracción de la altura) donde viven encabezados y pies
SCENE_GAP = 2.5  # hueco mínimo entre dos líneas del texto (en interlíneas) que separa escenas (Mitsuha GambaTrans: 3,3)
REPEAT = 0.30   # fracción de páginas en que una línea debe repetirse para ser encabezado o pie
KEYWORDS = re.compile(
    r"(cap[ií]tulo|pr[oó]logo|ep[ií]logo|interludio|historia paralela|palabras de cierre"
    r"|extra|parte|chapter|prologue|epilogue|afterword|interlude)\b", re.I)
TOC_TITLE = re.compile(r"(contenidos?|[ií]ndice|tabla de contenidos?|contents|table of contents):?", re.I)
DOT_LEADER = re.compile(r"[.\-_·…]{4,}\s*(?:p[áa]g\.?\s*)?(\d+)\s*\|?$", re.I)  # "..... 12", "---- Pág. 54"
TOC_SKIPPED = "se omitió como índice del PDF"  # motivo en Book.skipped de una página de índice
SCENE_BREAK = re.compile(r"[\s☆★*＊◆◇●○■□♦♢※✦✧❖◈•·#~◎◯♠♣♥♤♧♡◊]+")
# Fuente Symbol sin tabla Unicode: PyMuPDF da el código de la fuente (Mitsuha 6 [Bell T]: 0xAA es ♠, sale «ª»)
SYMBOL_FONT = str.maketrans({"\xa7": "♣", "\xa8": "♦", "\xa9": "♥", "\xaa": "♠", "\xb7": "•", "\xe0": "◊",
                             "\xb4": "×", "\xa5": "∞", "\xae": "→", "\xac": "←"})
END_PUNCT = (".", "!", "?", "…", '"', "'", "»", "”", "’", ":", ")", "]")  # "]": notas del traductor
SOFT_HYPHEN = "\u00ad"  # guion de partición: solo se ve si cae al final de la línea
TAGS = re.compile(r"<[^>]+>")
# U+FFFC: el hueco que PyMuPDF deja en el texto por una imagen en l\u00ednea de un EPUB (la imagen sale aparte)
BAD_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffc\ufffe\uffff]")


@dataclass
class Row:
    text: str     # texto plano
    html: str     # texto con <i>/<b>, escapado para XHTML
    x0: float
    x1: float
    y: float      # línea base
    size: float
    bold: bool
    heading: bool = False  # se decide en analyze, cuando ya se conoce el tamaño normal del texto
    starts: bool = False   # con esta línea empieza un marcador del PDF: abre un título nuevo
    box: tuple = ()        # rectángulo propio si la línea junta varias (etiqueta + nombre debajo)


@dataclass
class Block:
    kind: str           # "p" | "heading" | "image" | "break"
    page: int           # página de origen (empieza en 1)
    html: str = ""
    image: bytes = b""  # JPEG si kind == "image"
    keep: bool = True   # imagen: va en el EPUB (False = descartada, se puede recuperar en la ventana)
    layout: str = ""    # imagen doble: "" tal cual, "split" en dos páginas, "rotate" girada 90°
    align: str = ""     # párrafo: "center" si en el PDF va centrado (cartas, carteles, avisos)
    rect: tuple = ()    # (x0, y0, x1, y1) del título en su página, para resaltarlo en la vista previa


@dataclass
class Book:
    title: str
    author: str
    cover: bytes        # JPEG
    blocks: list
    candidates: list    # índices en blocks de los títulos de capítulo detectados
    series: str = ""    # serie a la que pertenece ("" si no se sabe)
    volume: str = ""    # número de volumen dentro de la serie ("1", "2.5")
    publisher: str = ""    # grupo de traducción
    translator: str = ""
    description: str = ""  # sinopsis
    illustrator: str = ""
    proofreader: str = ""  # corrección
    editor: str = ""       # edición (de las ilustraciones, maquetación…)
    original_title: str = ""
    subjects: str = ""     # géneros, separados por comas
    date: str = ""         # fecha de publicación (AAAA o AAAA-MM-DD)
    skipped: list = field(default_factory=list)  # [(página, letras, motivo)] de texto que no entró
    toc: list = field(default_factory=list)  # [(título, página o None)] del índice impreso del PDF


VOLUME = re.compile(r"\b(?:vol(?:umen|ume)?\.?|tomo)\s*0*(\d+(?:[.,]\d+)?)", re.I)


def _series_volume(stem, meta_title):
    """Serie y volumen desde el nombre del archivo ("Serie - Volumen 01 [WP]" -> "Serie", "1").
    Si el nombre no dice la serie ("Volumen 1 World Project"), se usa el título del PDF."""
    m = VOLUME.search(stem)
    if not m:
        return "", ""
    series = re.sub(r"\[[^\]]*\]", "", stem[:m.start()]).strip(" -–—_.")
    # el título del PDF «Maryoku - Volumen 3» también lleva el volumen: la serie es lo de antes
    series = series or VOLUME.split(meta_title)[0].strip(" -–—_.") or meta_title.strip()
    return series, m.group(1).replace(",", ".")


QUOTE_OPENERS = set("  ([¿¡—–«“‘")  # tras estos caracteres, una comilla abre
QUOTE_CLOSE_NEXT = set(" ,.;:!?)»”")      # antes de estos, una comilla cierra aunque siga a un espacio
DASH_CLOSE_NEXT = set(",.;:!?)»”…")       # tras una raya de cierre puede venir puntuación


def _dashes(chunks):
    """Guiones que hacen de raya, al estilo RAE: el que abre el párrafo es la raya de diálogo
    ("- Hola" -> "—Hola") y los demás abren o cierran un inciso ("-dijo-." -> "—dijo—."): la raya de
    apertura va pegada a lo que sigue y la de cierre a lo que precede. Un guion entre letras o cifras
    (Greg-sama, 1-2) no se toca. Las letras quitadas quedan como "" en chunks."""
    chars = [(c, j, i) for j, chunk in enumerate(chunks) for i, c in enumerate(chunk)]
    first = next((n for n, (c, _, _) in enumerate(chars) if not c.isspace()), None)
    opened = False

    def put(n, s):
        if n < len(chars):  # tras un guion al final del párrafo, el espacio siguiente es imaginario
            chunks[chars[n][1]][chars[n][2]] = s

    for n, (c, _, _) in enumerate(chars):
        if c not in "-–—":
            continue
        prev = chars[n - 1][0] if n else " "
        nxt = chars[n + 1][0] if n + 1 < len(chars) else " "
        if n == first:
            put(n, "—")
            if nxt == " ":
                put(n + 1, "")
            continue
        before, after = prev.isspace(), nxt.isspace() or nxt in DASH_CLOSE_NEXT
        if before == after:  # entre espacios decide el inciso abierto; entre letras, palabra compuesta
            if not before:
                continue
            opening = not opened
        elif before:
            if nxt.isdigit():  # número negativo
                continue
            opening = True
        else:
            opening = False
        put(n, "—")
        if opening and nxt == " ":
            put(n + 1, "")
        elif not opening and prev == " ":
            put(n - 1, "")
        opened = opening


@functools.lru_cache(maxsize=50000)  # la vista previa reescribe el capítulo en cada cambio: casi todo igual
def _typography(html_text, dashes=True):
    """Tipografía de un párrafo al estilo RAE: «…» (y “…” dentro), ‘…’, puntos suspensivos, raya
    de diálogo e incisos (_dashes) y espacios. No toca las etiquetas <i>/<b>: la posición de cada
    comilla se mira a través de ellas. La cuenta de comillas abiertas empieza en cada párrafo, para
    no arrastrar errores del PDF. dashes=False en los títulos: en "Serie - Volumen 1" el guion separa."""
    parts = re.split(r"(<[^>]+>)", html_text)  # el texto queda en las posiciones pares
    for k in range(0, len(parts), 2):
        text = re.sub(r"\.{3,}", "…", parts[k]).replace("─", "—")
        text = re.sub(r" {2,}", " ", text)
        parts[k] = re.sub(r" +([,.;:!?])", r"\1", text)
    chunks = [list(parts[k]) for k in range(0, len(parts), 2)]
    if dashes:
        _dashes(chunks)  # antes que las comillas: tras una raya, la comilla abre
    chars = [(c, j, i) for j, chunk in enumerate(chunks) for i, c in enumerate(chunk) if c]
    depth = 0
    for n, (c, j, i) in enumerate(chars):
        prev = chunks[chars[n - 1][1]][chars[n - 1][2]] if n else " "  # ya convertido
        nxt = chars[n + 1][0] if n + 1 < len(chars) else " "
        # Abre tras espacio o signo de apertura, o justo tras un cierre (“Hola.”“Adiós.”)
        opening = (prev in QUOTE_OPENERS or prev in "»”") and nxt not in QUOTE_CLOSE_NEXT
        if c in '"“”':
            if opening:
                chunks[j][i] = "“" if depth else "«"
                depth += 1
            else:
                chunks[j][i] = "”" if depth > 1 else "»"
                depth = max(0, depth - 1)
        elif c == "«":
            depth += 1
        elif c == "»":
            depth = max(0, depth - 1)
        elif c == "'":
            chunks[j][i] = "‘" if opening else "’"  # entre letras es apóstrofo: ’
    for j, chunk in enumerate(chunks):
        parts[2 * j] = "".join(chunk)
    return "".join(parts)


def _bold(s):
    return bool(s["flags"] & pymupdf.TEXT_FONT_BOLD) or "bold" in s["font"].lower()


def _italic(s):
    font = s["font"].lower()
    return bool(s["flags"] & pymupdf.TEXT_FONT_ITALIC) or "italic" in font or "oblique" in font


REF = re.compile(r"\d{1,3}|\*{1,3}|[†‡]")  # llamada de nota: número o asterisco en letra pequeña
REF_MARK = re.compile(r'<sup class="ref">([^<]+)</sup>')  # llamada en una fila, hasta saber si hay nota
NOTE = re.compile(r'<span class="nota">(.*?)</span>')  # nota al pie dentro de su párrafo


def _ref(s, spans):
    """Llamada de nota: span de cifras pegado a una palabra en letra mayor y elevado sobre su línea.
    Toma la línea base de esa palabra, porque elevado caería en otra fila. Si no lo es, s tal cual."""
    near = [t for t in spans if t is not s and t["size"] >= s["size"] * 1.25
            and -1 <= t["origin"][1] - s["origin"][1] <= t["size"] * 0.6
            and min(abs(s["bbox"][0] - t["bbox"][2]), abs(t["bbox"][0] - s["bbox"][2])) < t["size"]]
    if not near:
        return s
    t = min(near, key=lambda t: abs(t["origin"][1] - s["origin"][1]))
    return {**s, "origin": (s["origin"][0], t["origin"][1]), "ref": True}


def _row(spans):
    spans.sort(key=lambda s: s["origin"][0])
    texts, parts = [], []
    for k, (prev, s) in enumerate(zip([None] + spans, spans)):
        t = BAD_XML.sub("", s["text"])
        if s.get("ref"):  # fuera del texto plano: no cuenta para títulos ni párrafos
            parts.append(f'<sup class="ref">{html.escape(t.strip())}</sup>')
            continue
        if k == len(spans) - 1 and t.rstrip().endswith(SOFT_HYPHEN):
            t = t.rstrip()[:-1] + "-"
        t = t.replace(SOFT_HYPHEN, "")
        # Espacio solo si hay hueco visible: un cambio de fuente dentro de "<i>hola</i>," no lo lleva.
        if (texts and not texts[-1].endswith(" ") and not t.startswith(" ")
                and s["bbox"][0] - prev["bbox"][2] > s["size"] * 0.15):
            texts.append(" ")
            parts.append(" ")
        texts.append(t)
        part = s.get("icon") or html.escape(t, quote=False)
        if _italic(s):
            part = f"<i>{part}</i>"
        if _bold(s):
            part = f"<b>{part}</b>"
        parts.append(part)
    main = max(spans, key=lambda s: len(s["text"].strip()))
    return Row(" ".join("".join(texts).split()), "".join(parts).strip(),
               spans[0]["bbox"][0], spans[-1]["bbox"][2], spans[0]["origin"][1],
               round(main["size"], 1), all(_bold(s) for s in spans))


def _rows(page):
    """Agrupa los spans por línea base: el texto justificado parte una línea en varios spans.
    Las líneas que no van en horizontal se saltan: son el encabezado y el pie de una página girada
    para una ilustración apaisada (Re Zero 6), que quedarían en mitad de la página como títulos.
    Sin TEXT_PRESERVE_IMAGES: el dict copiaba los bytes de cada imagen y aquí solo cuenta el texto (Maryoku 1: 1,5 s)."""
    flags = pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES
    spans = [s for b in page.get_text("dict", flags=flags)["blocks"] for line in b.get("lines", [])
             if abs(line["dir"][0]) > 0.9 for s in line["spans"] if BAD_XML.sub("", s["text"]).strip()]
    spans = [_ref(s, spans) if REF.fullmatch(s["text"].strip()) else s for s in spans]
    for s in spans:
        if "symbol" in s["font"].lower():
            s["text"] = s["text"].translate(SYMBOL_FONT)
    _inline_glyphs(page, spans)
    rows, group = [], []
    for s in sorted(spans, key=lambda s: s["origin"][1]):
        if group and abs(s["origin"][1] - group[0]["origin"][1]) > 2:
            rows.append(_row(group))
            group = []
        group.append(s)
    if group:
        rows.append(_row(group))
    return rows


def _footnotes(rows, body, height):
    """Separa las notas al pie de una página: filas de la mitad inferior, en letra menor que el cuerpo,
    que empiezan por una llamada; las filas siguientes en esa letra las continúan. El número puede venir
    en el mismo span que la nota («5 Los tics…», Re Zero 3): vale si la página tiene esa llamada.
    Devuelve (filas del cuerpo, {número: html de la nota})."""
    refs = {n for r in rows for n in REF_MARK.findall(r.html)}
    kept, notes, current = [], {}, None
    for r in rows:
        m = REF_MARK.match(r.html) or re.match(r"(\d{1,3}|\*{1,3}|[†‡])\s+", r.html)
        small = not r.heading and r.size < body - 0.5 and r.y > height / 2
        if small and m and (m[0].startswith("<") or m[1] in refs):
            current = m[1]
            notes[current] = r.html[m.end():].strip()
        elif small and current is not None:
            notes[current] = _join(notes[current], r.html)
        else:
            current = None
            kept.append(r)
    return kept, notes


def _attach_notes(blocks, notes):
    """Cambia cada llamada de los bloques por su nota (dentro del párrafo, así sobrevive a editar y
    mover secciones). Una llamada sin nota vuelve a ser texto («m²»); una nota sin llamada va al final
    del último párrafo, para no perderla."""
    used = set()

    def note(m):
        if m[1] not in notes or m[1] in used:  # cada nota, una vez: el segundo «2» es un exponente
            return m[1]
        used.add(m[1])
        return f'<span class="nota">{notes[m[1]]}</span>'

    for b in blocks:
        b.html = REF_MARK.sub(note, b.html)
    last = next((b for b in reversed(blocks) if b.kind == "p"), None)
    if last is not None:
        last.html += "".join(f'<span class="nota">{text}</span>' for num, text in notes.items() if num not in used)


def _norm(text):
    return re.sub(r"\d+", "#", " ".join(text.lower().split()))


def _strip_margins(pages, heights):
    """Quita encabezados y pies: líneas de las franjas que se repiten en muchas páginas."""
    def in_band(r, h):
        return r.y < h * BAND or r.y > h * (1 - BAND)

    def loose(text):  # "Einherjar Project Prólogo | 11" y "… Capítulo 3 | 25" -> "einherjar project"
        return " ".join(re.sub(r"[\W\d_]+", " ", KEYWORDS.sub(" ", _norm(text).replace("#", " "))).split())

    def key(r):  # con el tamaño: el título grande del capítulo no es su encabezado pequeño de igual texto
        return _norm(r.text), round(r.size)

    counts, loose_counts = collections.Counter(), collections.Counter()
    for rows, h in zip(pages, heights):
        counts.update({key(r) for r in rows if in_band(r, h)})
        loose_counts.update({loose(r.text) for r in rows if in_band(r, h)})
    least = max(2, REPEAT * len(pages))
    repeated = {k for k, n in counts.items() if n >= least}
    # Encabezados que cambian con el capítulo: la variante del prólogo o de las palabras del autor
    # ("Einherjar Project Palabras del autor | 230") sale en pocas páginas, pero empieza igual.
    loose_repeated = tuple(k for k, n in loose_counts.items() if n >= least and len(k) >= 6)

    def margin(r, h):
        if not in_band(r, h):
            return False
        # La clave suelta solo para líneas con número de página que no abren con "Capítulo…": un título
        # de capítulo en la franja superior también se queda en pocas palabras repetidas
        footer = re.search(r"\d", r.text) and not KEYWORDS.match(r.text.strip(LABEL_DECOR))
        return (key(r) in repeated or _norm(r.text) == "#"  # "#": números de página sueltos
                or bool(footer and loose_repeated and loose(r.text).startswith(loose_repeated)))

    return [[r for r in rows if not margin(r, h)] for rows, h in zip(pages, heights)]


def _is_heading(r, body):
    if len(r.text) > 120:
        return False
    return r.size >= body * 1.3 or (r.bold and (r.size >= body * 1.1 or bool(KEYWORDS.match(r.text))))


LABEL_DECOR = "»«-–—|·*~ "  # adornos alrededor de una etiqueta de capítulo: "» Capítulo 1 «", "«Prólogo»"
HEADING_DECOR = "◆◇♦■□●○◈❖✦✧★☆ "  # símbolos que adornan un título por delante o detrás: «◆Prólogo»
CHAPTER_LABEL = re.compile(
    r"(?:cap[ií]tulo|chapter|pr[oó]logo|prologue|ep[ií]logo|epilogue|interludio|interlude|anexo|parte|part"
    r"|volumen|historia\b[^:]*)(?: [\w.]+)?", re.I)


def _label(row):
    """Texto de una etiqueta de capítulo sola ("» Capítulo 1 «", "«Prólogo»", "CAPITULO UNO:"), sin
    adornos; None si la línea es otra cosa."""
    text = row.text.strip(LABEL_DECOR).rstrip(":.").strip()
    if text and (CHAPTER_LABEL.fullmatch(text) or (text != row.text.strip() and len(text) <= 40)):
        return text
    return None


def _join_labels(rows):
    """Algunos PDF ponen la etiqueta del capítulo en una línea y el nombre debajo, en negrita y a
    veces más pequeño que un título: se unen en un solo título "Capítulo 1: Nombre"."""
    joined, i = [], 0
    while i < len(rows):
        row = rows[i]
        label = _label(row)
        # Sin ser título, solo si encima de un título: el número de capítulo pequeño y sin negrita de un EPUB
        if not row.heading and not (label and CHAPTER_LABEL.fullmatch(label) and i + 1 < len(rows)
                                    and rows[i + 1].heading):
            label = None
        if label is None:
            joined.append(row)
            i += 1
            continue
        names, j = [], i + 1
        while j < len(rows) and len(names) < 3:
            nxt, last = rows[j], (names[-1] if names else row)
            # Un marcador abre un título nuevo, salvo en la primera línea bajo la etiqueta: hay PDF que
            # marcan "Capitulo 1" y, en el nivel 2, su nombre (Re Zero 3). "Parte 1" no es un nombre.
            # El hueco se mide con la letra mayor: entre líneas base crece con la de abajo (Making Magic: «Chapter 3»
            # en 11 pt y el nombre en 16,5, 27 pt más abajo)
            if (len(nxt.text) > 120 or not (nxt.bold or nxt.heading) or (nxt.starts and names)
                    or SUBPART.fullmatch(nxt.text.strip())
                    or nxt.y - last.y >= 2.2 * max(last.size, nxt.size)
                    or (names and abs(nxt.size - names[0].size) >= 0.5)):
                break
            names.append(nxt)
            j += 1
        text = f"{label}: {' '.join(n.text for n in names)}" if names else label
        group = [row] + names
        joined.append(replace(row, text=text, html=html.escape(text), heading=True, box=_rect(group),
                              x0=min(r.x0 for r in group), x1=max(r.x1 for r in group)))
        i = j
    return joined


def _is_toc(rows):
    """Página de índice del PDF: título "Contenido" en sus primeras líneas (puede haber créditos
    encima) o al menos tres líneas con puntos o guiones hasta el número de página."""
    return (any(TOC_TITLE.fullmatch(r.text) for r in rows[:3])
            or sum(bool(DOT_LEADER.search(r.text)) for r in rows) >= 3)


def _toc_entries(rows):
    """Títulos de una página de índice: [(título, página o None)]. Un título largo sigue en la línea de abajo
    hasta la que acaba en puntos y número; una línea que empieza por palabra clave («Capítulo 11») abre otro
    aunque el de antes acabe en un número sin puntos («… mundo . 36»). Sin puntos en la página, sin páginas."""
    start = next((n + 1 for n, r in enumerate(rows[:3]) if TOC_TITLE.fullmatch(r.text.strip())), 0)  # tras «Contenido»
    leaders = any(DOT_LEADER.search(r.text) for r in rows)
    entries, text = [], ""

    def flush():
        m = re.search(r"[\s.…·]+(\d+)$", text) if leaders else None
        entries.append(((text[:m.start()] if m else text).strip(LABEL_DECOR), int(m[1]) if m else None))

    for r in rows[start:]:
        line = r.text.strip(LABEL_DECOR)  # "| Prólogo | ...... 15"
        if text and KEYWORDS.match(line):
            flush()
            text = ""
        text = f"{text} {line}".strip()
        m = DOT_LEADER.search(text)
        if m:
            entries.append((text[:m.start()].strip(LABEL_DECOR), int(m[1])))
            text = ""
    if text:
        flush()
    return [(title, page) for title, page in entries if title]


TOC_LABEL = re.compile(r"(cap[ií]tulo|chapter|pr[oó]logo|prologue|ep[ií]logo|epilogue|interludio|interlude)\d*")


def _toc_match(a, b):
    """Dos títulos son el mismo (claves de _key): iguales; uno empieza como el otro (el índice dice solo
    «Capítulo 1», o la página corta el título), sin confundir «Capítulo 1» con «Capítulo 10»; o la misma
    etiqueta («Capítulo 4: Thousand Tricks» y «Capítulo 4: Mil trucos», traducidos distinto)."""
    short, long_ = sorted((a, b), key=len)
    if len(short) < 4:
        return short == long_ and bool(short)
    if long_.startswith(short) and (len(short) >= 10 or short == long_ or not long_[len(short)].isdigit()):
        return True
    la, lb = TOC_LABEL.match(a), TOC_LABEL.match(b)
    return bool(la and lb and la[0] == lb[0] and (la[0][-1].isdigit() or not la[0].startswith(("cap", "chap"))))


def _links_only(page, rows):
    """Página cuyas líneas son casi todas enlaces a otras páginas del libro: la continuación de un índice
    (un EPUB compuesto por PyMuPDF reparte el suyo en varias páginas, y solo la primera dice «Contenido»)."""
    links = [link["from"] for link in page.get_links() if link["kind"] == pymupdf.LINK_GOTO]
    inside = sum(any(pymupdf.Point(r.x0 + 1, r.y - r.size / 3) in rect for rect in links) for r in rows)
    return bool(rows) and inside >= 0.8 * len(rows)


def _first_group(rows):
    """Primer título de una página que solo tiene títulos (el resto son duplicados decorativos)."""
    group = [rows[0]]
    for r in rows[1:]:
        if abs(r.size - rows[0].size) >= 0.5:
            break
        group.append(r)
    return group


def _key(text):
    """Clave para comparar títulos: solo letras y números (una repetición puede perder el "!")."""
    return re.sub(r"[\W_]+", "", text.lower())


def _rect(rows):
    """Rectángulo que abarca unas líneas: la caja del span no sirve (Cambria la estira), así que
    se calcula desde la línea base y el tamaño de letra. Un Block aporta su rect: así crece línea a línea."""
    boxes = [r.rect if isinstance(r, Block) else r.box or (r.x0, r.y - r.size, r.x1, r.y + r.size * 0.3) for r in rows]
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def _title(rows):
    """Clave del primer título de la página, para comparar páginas."""
    heads = [r for r in rows if r.heading]
    return _key(" ".join(r.text for r in _first_group(heads))) if heads else ""


def _frames(page):
    """Recuadros dibujados alrededor de un texto: The World's Strongest Witch 1 pone las ventanas de estado en una
    doble línea hecha de tiras finas. Un marco son tiras que se tocan con lados a izquierda y derecha y algún borde
    arriba o abajo (partido entre páginas le falta uno); también vale un rectángulo trazado de una vez."""
    pieces, boxes = [], []
    for d in page.get_drawings():
        r = pymupdf.Rect(d["rect"])
        if min(r.width, r.height) <= 4 and max(r.width, r.height) >= 20:
            pieces.append(r)
        elif ("s" in (d.get("type") or "") and r.width >= 40 and r.height >= 20
              and any(item[0] == "re" for item in d["items"])):
            boxes.append(r)
    groups = []
    for p in pieces:
        near = [g for g in groups if any((p + (-3, -3, 3, 3)).intersects(q) for q in g)]
        groups = [g for g in groups if all(g is not h for h in near)] + [[p] + [q for g in near for q in g]]
    for g in groups:
        box = pymupdf.Rect(g[0])
        for q in g[1:]:
            box |= q
        flat, tall = [q for q in g if q.width > q.height], [q for q in g if q.width <= q.height]
        if (any(abs(q.x0 - box.x0) < 6 for q in tall) and any(abs(q.x1 - box.x1) < 6 for q in tall)
                and any(abs(q.y0 - box.y0) < 6 or abs(q.y1 - box.y1) < 6 for q in flat)):
            boxes.append(box)
    area = page.rect.width * page.rect.height
    return [b for b in boxes if b.height >= 20 and b.width * b.height < 0.85 * area]


# Símbolos que algunas novelas meten como imagen diminuta en el texto, y su dibujo en la fuente de símbolos de Windows
GLYPHS = "■□◆◇●○♥♡★☆♪♫➔→▶▲△▼▽✓✗※"
SYMBOLS_TTF = Path(os.environ.get("WINDIR") or r"C:\Windows") / "Fonts" / "seguisym.ttf"
# Icono en imagen que no es ningún símbolo de GLYPHS: va en el párrafo con su PNG dentro, así lo llevan igual la vista
# previa, el recopilatorio y las revisiones guardadas; write_epub lo saca a un archivo
ICON = re.compile(r'<img class="icono" src="data:image/png;base64,([A-Za-z0-9+/]+=*)" alt=""/>')
ICON_TAG = re.compile(r'(<img class="icono" [^>]*>)')  # para partir una línea por sus iconos
ICON_MARK = re.compile(r'&lt;(img class="icono" src="data:image/png;base64,[A-Za-z0-9+/]+=*" alt=""/)&gt;')


def _shape(pix):
    """(silueta rellena en cuadrícula de 24x24, tinta/silueta, ancho/alto) del dibujo de pix recortado a su tinta;
    None sin tinta. La silueta rellena compara la forma aunque el trazo sea fino; la proporción de tinta distingue
    ♡ de ♥ y ◇ de ◆."""
    w, h, data, stride = pix.width, pix.height, pix.samples, pix.stride
    ink = [[data[y * stride + x] < 128 for x in range(w)] for y in range(h)]
    ys = [y for y in range(h) if any(ink[y])]
    if not ys:
        return None
    xs = [x for x in range(w) if any(ink[y][x] for y in ys)]
    y0, y1, x0, x1 = ys[0], ys[-1] + 1, xs[0], xs[-1] + 1
    w, h = x1 - x0, y1 - y0
    # Una celda tiene tinta si la tiene cualquier punto suyo: tomar uno solo abría huecos en un trazo fino (♡)
    grid = [[any(ink[y][x] for y in range(y0 + h * j // 24, max(y0 + h * (j + 1) // 24, y0 + h * j // 24 + 1))
                 for x in range(x0 + w * i // 24, max(x0 + w * (i + 1) // 24, x0 + w * i // 24 + 1)))
             for i in range(24)] for j in range(24)]
    outside, todo = set(), [(i, j) for i in range(24) for j in (0, 23)] + [(i, j) for j in range(24) for i in (0, 23)]
    while todo:  # lo que se alcanza desde el borde sin cruzar tinta es fuera; el resto, la silueta
        i, j = todo.pop()
        if 0 <= i < 24 and 0 <= j < 24 and (i, j) not in outside and not grid[j][i]:
            outside.add((i, j))
            todo += [(i + 1, j), (i - 1, j), (i, j + 1), (i, j - 1)]
    filled = tuple((i, j) not in outside for j in range(24) for i in range(24))
    return filled, sum(map(sum, grid)) / max(1, sum(filled)), w / h


@functools.cache
def _glyph_shapes():
    """{símbolo: _shape} de GLYPHS; vacío sin la fuente de símbolos (entonces la imagen se pierde, como antes)."""
    if not SYMBOLS_TTF.is_file():
        return {}
    doc, out = pymupdf.open(), {}
    for ch in GLYPHS:
        page = doc.new_page(width=100, height=100)
        page.insert_font(fontname="sym", fontfile=str(SYMBOLS_TTF))
        page.insert_text((14, 76), ch, fontsize=64, fontname="sym")
        shape = _shape(page.get_pixmap(colorspace=pymupdf.csGRAY))
        if shape:
            out[ch] = shape
    return out


def _glyph(page, box):
    """El símbolo que dibuja una imagen diminuta del texto (Witch 1: ♡, ♥, ♪, ◇, ➔, viñetas ■ ◆), o "" si no se
    parece a ninguno."""
    zoom = 48 / max(1, box.height)
    pix = page.get_pixmap(clip=box, matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csGRAY)
    return _classify(pix.width, pix.height, pix.stride, pix.samples)


@functools.lru_cache(maxsize=256)  # el mismo ♡ sale decenas de veces en un tomo: se mira una vez
def _classify(width, height, stride, samples):
    """_glyph de un dibujo ya pasado a gris."""
    shapes = _glyph_shapes()
    found = _shape(type("Pix", (), {"width": width, "height": height, "stride": stride, "samples": samples}))
    if not shapes or not found:
        return ""
    filled, density, ratio = found

    def score(shape):
        other, other_density, other_ratio = shape
        overlap = sum(a and b for a, b in zip(filled, other)) / max(1, sum(a or b for a, b in zip(filled, other)))
        return (overlap - abs(density - other_density)
                - (0.3 if abs(ratio - other_ratio) > 0.4 * max(ratio, other_ratio) else 0))

    best = max(shapes, key=lambda ch: score(shapes[ch]))
    return best if score(shapes[best]) >= 0.55 else ""


def _icon(page, box):
    """<img> de ICON con el dibujo de box sin el papel: lo blanco queda transparente y el color, el mismo sobre
    blanco. Un icono que no se reconoce se ve como en el PDF en vez de perderse."""
    zoom = 48 / max(1, box.height)
    pix = page.get_pixmap(clip=box, matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csRGB, alpha=False)
    rgb, out = pix.samples, bytearray()
    for i in range(0, len(rgb), 3):
        px = rgb[i:i + 3]
        a = 255 - min(px)
        out += bytes(255 - (255 - c) * 255 // a if a else 255 for c in px) + bytes([a])
    png = pymupdf.Pixmap(pymupdf.csRGB, pix.width, pix.height, bytes(out), True).tobytes("png")
    return f'<img class="icono" src="data:image/png;base64,{base64.b64encode(png).decode()}" alt=""/>'


def _split_span(page, span, x):
    """span partido en x, donde un símbolo en imagen va dentro de su texto («find! ♪”»); None si no se puede. Las
    letras salen de rawdict con las mismas opciones que _rows (con recorte, MuPDF añadía una letra de más)."""
    flags = pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_IMAGES
    chars = next((t["chars"] for block in page.get_text("rawdict", flags=flags)["blocks"]
                  for line in block.get("lines", []) for t in line["spans"]
                  if abs(t["origin"][1] - span["origin"][1]) <= 1 and abs(t["bbox"][0] - span["bbox"][0]) <= 1), [])
    chars = [c for c in chars if (c["bbox"][0] + c["bbox"][2]) / 2 <= span["bbox"][2] + 1]  # ya partido: solo lo suyo
    k = sum((c["bbox"][0] + c["bbox"][2]) / 2 < x for c in chars)
    end = k
    while end and not chars[end - 1]["c"].strip():  # la mitad izquierda acaba en su última letra
        end -= 1
    while k < len(chars) and not chars[k]["c"].strip():  # el hueco de las imágenes: la otra mitad empieza tras él
        k += 1
    if not (end and k < len(chars)):
        return None
    text = "".join(c["c"] for c in chars)
    return ({**span, "text": text[:end], "bbox": (*span["bbox"][:2], chars[end - 1]["bbox"][2], span["bbox"][3])},
            {**span, "text": text[k:], "origin": (chars[k]["origin"][0], span["origin"][1]),
             "bbox": (chars[k]["bbox"][0], *span["bbox"][1:])})


def _inline_glyphs(page, spans):
    """Añade a spans los símbolos en imagen de una línea de texto: sin ellos «Margrave! ♡» perdía el ♡. Una imagen
    el doble de ancha que alta lleva dos (Witch 1: «♥♥»); dentro del texto de un span, lo parte."""
    for info in page.get_image_info():
        box = pymupdf.Rect(info["bbox"])
        if not (3 <= box.width <= 40 and 3 <= box.height <= 20):
            continue
        middle = (box.y0 + box.y1) / 2

        def away(t):
            return 0 if t["bbox"][0] <= box.x0 <= t["bbox"][2] else min(abs(box.x0 - t["bbox"][2]),
                                                                         abs(t["bbox"][0] - box.x1))

        near = [t for t in spans if t["bbox"][1] - 2 <= middle <= t["bbox"][3] + 2 and away(t) < 3 * t["size"]]
        if not near:
            continue
        line = min(near, key=away)
        words = " ".join(t["text"] for t in spans if abs(t["origin"][1] - line["origin"][1]) <= 2)
        if re.fullmatch(r"[\d\s.|·–—-]*", words):  # Kage 5: el adorno del número de página no es texto
            continue
        text = _glyph(page, box)
        if not text and box.width >= 1.6 * box.height:  # no es un símbolo ancho (➔): varios seguidos («♥♥»)
            n = round(box.width / box.height)
            text = "".join(_glyph(page, pymupdf.Rect(box.x0 + box.width * j / n, box.y0,
                                                     box.x0 + box.width * (j + 1) / n, box.y1)) for j in range(n))
        # Nageki 1, pág. 9: con Cambria el bbox de la línea sube dos letras sobre su base y abarcaba el adorno-separador
        # de encima; un icono va a la altura de las letras de su línea
        if not text and not line["origin"][1] - 1.2 * line["size"] <= middle <= line["origin"][1] + 0.3 * line["size"]:
            continue
        icon = "" if text else _icon(page, box)
        if line["bbox"][0] + 1 < box.x0 and box.x1 < line["bbox"][2] - 1:
            halves = _split_span(page, line, box.x0)
            if halves:
                spans.remove(line)
                spans += halves
        # Sin negrita ni cursiva: el símbolo no parte la etiqueta de su línea («■ <b>Lorna</b>»)
        spans.append({**line, "text": text, "bbox": tuple(box), "origin": (box.x0, line["origin"][1]), "flags": 0,
                      "font": "glyph", "icon": icon})


def _image_info(page, digests):
    """Imágenes dibujadas en la página, con su xref y el bbox en coordenadas de la página girada.
    La xref sale del tamaño en píxeles y los bits por componente, que no obligan a decodificar. Si dos imágenes
    de la página coinciden en eso (tiras de Re Zero 8), de la huella MD5, como get_image_info(xrefs=True):
    decodificarlas todas era un tercio del análisis de Kage 6. digests (xref -> huella) vale para todo el PDF."""
    sizes = collections.defaultdict(list)
    for xref, _, width, height, bpc, *_ in page.get_images():
        sizes[(width, height, bpc)].append(xref)
    if all(len(xrefs) == 1 for xrefs in sizes.values()):
        # shortcut: una imagen incrustada en el contenido (sin xref) del mismo tamaño que otra de la página
        # tomaría su xref; con huella saldría 0. No se ha visto en ningún PDF.
        return [dict(info, bbox=tuple(_shown(page, info)),
                     xref=sizes.get((info["width"], info["height"], info["bpc"]), [0])[0])
                for info in page.get_image_info()]
    by_digest = {}
    for xref, *_ in page.get_images():
        if xref not in digests:
            digests[xref] = pymupdf.Pixmap(page.parent, xref).digest
        by_digest[digests[xref]] = xref
    return [dict(info, bbox=tuple(_shown(page, info)), xref=by_digest.get(info["digest"], 0))
            for info in page.get_image_info(hashes=True)]


def _shown(page, info):
    """bbox de una imagen tal como se ve: get_image_info lo da sin girar y page.rect va girado (Re Zero 8)."""
    return pymupdf.Rect(info["bbox"]) * page.rotation_matrix


def _image_coverage(page, infos):
    area = page.rect
    return sum(abs(pymupdf.Rect(i["bbox"]) & area) for i in infos) / abs(area)


def _images(page, infos):
    """Imágenes incrustadas de la página: [(xref, fracción de la página que cubre)], de mayor a menor."""
    area, found = abs(page.rect), {}
    for info in infos:
        if info["xref"]:
            share = abs(pymupdf.Rect(info["bbox"]) & page.rect) / area
            found[info["xref"]] = max(found.get(info["xref"], 0), share)
    return sorted(found.items(), key=lambda item: -item[1])


def _ornaments(page, rows, infos, before=()):
    """Posición vertical de los adornos que hacen de separador de escena (Kage, Nageki): imágenes
    pequeñas, centradas, con texto debajo y solas en su altura; con texto encima, fuera de los márgenes. before:
    los bbox de las imágenes de la página anterior."""
    width, height = page.rect.width, page.rect.height
    found = []
    for info in infos:
        x0, y0, x1, y1 = info["bbox"]
        below = [r for r in rows if r.y > y1]
        # The World's Strongest Witch 86 y 144, Nageki, Megami, Kage: la escena cambia con la página y el adorno la
        # abre, sin texto encima. No lo es: lo que sigue a un título (decoración del capítulo), la misma imagen en
        # el mismo sitio de la página anterior (cabecera), una imagen ancha (título hecho imagen de Mitsuha, logo
        # de J-Novel: los adornos son de ancho/alto ≤ 1,22) ni otra con una imagen entre ella y el texto
        first = min(below, key=lambda r: r.y) if below else None
        top = (first and not first.heading and not any(r.y < y0 for r in rows) and x1 - x0 <= 1.5 * (y1 - y0)
               and not any(other is not info and y1 - 1 <= other["bbox"][1] < first.y for other in infos)
               and not any(all(abs(a - b) < 1 for a, b in zip(info["bbox"], box)) for box in before))
        above = max((r for r in rows if r.y < y0), key=lambda r: r.y, default=None)
        # Eminence 7: el adorno puede cerrar la página (la escena sigue en la siguiente, pág. 58); la raya justo
        # bajo un título («Afterword», pág. 246) o pegada bajo su línea (el enlace de los créditos de Re Zero 6)
        # es su subrayado
        underline = above and (above.heading or y0 - above.y < 0.5 * above.size)
        # Mitsuha 4, pág. 190: «Ilustrador», título dorado hecho imagen, ancho. Los adornos son grises o negros
        title = x1 - x0 >= 2.5 * (y1 - y0) and info["xref"] and _colorful(page.parent, info["xref"])
        if (x1 - x0 < 0.3 * width and y1 - y0 < 0.1 * height and abs((x0 + x1) / 2 - width / 2) < 0.15 * width
                and not title
                and (top or BAND * height < (y0 + y1) / 2 < (1 - BAND) * height and above and not underline)
                # The World's Strongest Witch: la flecha de «Lv. 2 → Lv. 3» y el ♡ o ♪ al final de una línea corta son
                # imágenes en la línea (una línea base cruza su altura); la línea que empieza justo debajo de un adorno
                # (Kage 5, pág. 99) no cuenta
                and not any(y0 < r.y < y1 + 0.3 * r.size for r in rows)):
            found.append(((y0 + y1) / 2, info))
    return sorted(found, key=lambda f: f[0])  # [(posición vertical, info)], de arriba abajo


def _drawn(page):
    """Dibujos de trazos de la página agrupados en cajas, como las imágenes de _image_info ({"bbox", "xref": 0}):
    Eminence 7 dibuja su separador de escena (dos barras y una raya) sin imagen ni texto. El fondo de página
    no cuenta."""
    boxes = []
    for d in page.get_drawings():
        r = pymupdf.Rect(d["rect"]) + (-1, -1, 1, 1)  # una línea recta no tiene alto: sin margen no se toca con nada
        if abs(r) > 0.05 * abs(page.rect):
            continue
        while hit := next((b for b in boxes if b.intersects(r)), None):
            boxes.remove(hit)
            r |= hit
        boxes.append(r)
    return [{"bbox": tuple(b), "xref": 0, "drawn": True} for b in boxes]


def _colorful(doc, xref):
    """¿Imagen de color? Media de saturación de la tinta (lo que no es fondo claro): los adornos-separador
    medidos van de 0 a 9 (negros o grises); el título dorado «Ilustrador» de Mitsuha 4, 183."""
    try:
        pix = _rgb(pymupdf.Pixmap(doc, xref), alpha=False)
    except Exception:
        return False
    if pix.n != 3:  # en gris: sin color
        return False
    pix = pymupdf.Pixmap(pix, 40, 40, None)  # muestra pequeña: basta para el color
    data = pix.samples
    ink = [data[i:i + 3] for i in range(0, len(data), 3) if min(data[i:i + 3]) < 230]
    return bool(ink) and sum(max(p) - min(p) for p in ink) / len(ink) > 60


def _ornament_image(doc, page, info):
    """La imagen de un adorno-separador, por si se quiere el original: la incrustada o, si va dibujado, su dibujo."""
    if info["xref"]:
        return _extract(doc, info["xref"])
    return page.get_pixmap(dpi=200, clip=info["bbox"], alpha=True).tobytes("png") if info.get("drawn") else b""


def _jpeg(pix, quality):
    """JPEG de un pixmap sin transparencia. Con Pillow (libjpeg-turbo): el codificador de MuPDF tardaba 270–600 ms
    por página-ilustración, casi la mitad del análisis (Kage 6). CMYK y otros raros, con MuPDF."""
    mode = {1: "L", 3: "RGB"}.get(pix.n)
    if mode is None or pix.alpha:
        return pix.tobytes("jpg", jpg_quality=quality)
    out = io.BytesIO()
    Image.frombuffer(mode, (pix.width, pix.height), pix.samples_mv, "raw", mode, pix.stride, 1).save(
        out, "JPEG", quality=quality)
    return out.getvalue()


def _rgb(pix, alpha=True):
    """Pixmap en gris o RGB: CMYK u otros pasan a RGB. Con alpha=False, sin transparencia."""
    if pix.alpha and not alpha:
        pix = pymupdf.Pixmap(pix, 0)
    if pix.colorspace and pix.colorspace.n not in (1, 3):
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    return pix


def _with_mask(pix, mask):
    """Aplica una máscara de transparencia. PyMuPDF a veces entrega la imagen con un canal alfa
    opaco y la forma en la máscara aparte: ese alfa se descarta y manda la máscara."""
    if pix.alpha:
        pix = pymupdf.Pixmap(pix, 0)
    return pymupdf.Pixmap(pix, mask)


def _extract(doc, xref):
    """Imagen incrustada tal como está en el PDF, sin el texto que tenga encima. Con transparencia
    (logos), en PNG para conservarla: sin el canal alfa, un logo negro con la forma en la
    transparencia quedaría como un rectángulo negro. Sin transparencia, en JPEG."""
    pix = pymupdf.Pixmap(doc, xref)
    smask = doc.extract_image(xref).get("smask")
    if smask:  # la forma está en la máscara: se aplica siempre, aunque la imagen ya traiga un alfa
        mask = pymupdf.Pixmap(doc, smask)
        if (mask.width, mask.height) == (pix.width, pix.height):
            pix = _with_mask(pix, mask)
    pix = _rgb(pix)
    if pix.alpha:
        return pix.tobytes("png")
    return _jpeg(pix, 85)


def _illustration(images, uses):
    """xref de la ilustración a página completa que hay bajo el texto, o None. images: de _images.
    Una imagen usada en más de 2 páginas es un fondo decorativo."""
    if images and images[0][1] >= 0.5 and uses[images[0][0]] <= 2:
        return images[0][0]
    return None


def _render(page, crop=False):
    """La página como JPEG. Con crop, solo la zona de la ilustración: rara vez llega al borde exacto
    de la página (medido: 0,2 a 2,8 pt) y el blanco del papel se veía como un marco. El recorte entra
    1 pt para no dejar píxeles a medias con el blanco."""
    clip = None
    if crop:
        art = pymupdf.Rect()
        for info in page.get_image_info():
            art |= _shown(page, info) & page.rect
        if abs(art) >= 0.5 * abs(page.rect):
            clip = art + (1, 1, -1, -1)
    return _jpeg(page.get_pixmap(dpi=150, clip=clip), 85)


def load_cover(path):
    """Imagen de portada desde un archivo, en JPEG: el EPUB declara la portada como cover.jpg."""
    try:
        data = Path(path).read_bytes()
        if data[:3] == b"\xff\xd8\xff":
            return data
        pix = pymupdf.Pixmap(data)
    except Exception as e:
        raise ValueError(f"No se pudo abrir la imagen: {e}") from e
    return _jpeg(_rgb(pix, alpha=False), 90)


def text_cover(book):
    """Portada dibujada con serie, volumen, título y autor, para cuando el PDF no trae una buena. El color
    del fondo sale de la serie: todos los tomos de una serie comparten color."""
    hue = zlib.crc32((book.series or book.title).encode()) % 360 / 360
    with pymupdf.open() as doc:
        page = doc.new_page(width=600, height=900)
        page.draw_rect(page.rect, color=None, fill=colorsys.hls_to_rgb(hue, 0.22, 0.45))
        page.insert_font("bold", fontfile=str(FONT_DIR / "literata-700-normal.ttf"))
        page.insert_font("text", fontfile=str(FONT_DIR / "literata-400-normal.ttf"))
        top = " · ".join(filter(None, [book.series, book.volume and f"Volumen {book.volume}"]))
        for rect, text, font, size in (((50, 110, 550, 260), top, "text", 30),
                                       ((50, 300, 550, 680), book.title, "bold", 64),
                                       ((50, 740, 550, 850), book.author, "text", 28)):
            while text and size > 10 and page.insert_textbox(rect, text, fontname=font, fontsize=size,
                                                             color=(1, 1, 1), align=1) < 0:
                size -= 4  # no cabe: nada se escribió, se prueba más pequeño
        page.draw_line((200, 285), (400, 285), color=(1, 1, 1), width=1.5)
        return _jpeg(page.get_pixmap(matrix=pymupdf.Matrix(2, 2)), 90)


SHRINK = 1400  # lado mayor de las imágenes reducidas, en píxeles: de sobra para un celular o un e-reader


def _shrink(data, limit=SHRINK):
    """Imagen más ligera: lado mayor de limit píxeles como mucho y JPEG de calidad 75. Con
    transparencia sigue en PNG. Si no gana nada, devuelve la original."""
    pix = pymupdf.Pixmap(data)
    scale = limit / max(pix.width, pix.height)
    if scale < 1:
        pix = pymupdf.Pixmap(pix, round(pix.width * scale), round(pix.height * scale), None)
    small = pix.tobytes("png") if pix.alpha else _jpeg(pix, 75)
    return small if len(small) < len(data) else data


GRAY_GAMMA = [round(255 * (v / 255) ** 1.15) for v in range(256)]  # oscurece un poco los grises medios


def _gray(data):
    """Imagen en escala de grises con más contraste: en un e-reader de tinta pesa menos, y las ilustraciones
    escaneadas, que salen lavadas (grises en vez de negro y blanco), se ven nítidas."""
    pix = pymupdf.Pixmap(data)
    if pix.n - pix.alpha != 1:
        pix = pymupdf.Pixmap(pymupdf.csGRAY, pix)
    mode = "LA" if pix.alpha else "L"
    img = Image.frombuffer(mode, (pix.width, pix.height), pix.samples_mv, "raw", mode, pix.stride, 1)
    gray = ImageOps.autocontrast(img.getchannel("L"), cutoff=0.5).point(GRAY_GAMMA)
    out = io.BytesIO()
    if pix.alpha:
        Image.merge("LA", (gray, img.getchannel("A"))).save(out, "PNG")
    else:
        gray.save(out, "JPEG", quality=85)
    return out.getvalue()


@functools.lru_cache(maxsize=256)  # la lista lo pregunta en cada edición: decodificar costaba 2 s (Kage 5)
def is_wide(data):
    """Ilustración doble (dos páginas del libro en una imagen apaisada): en un celular en vertical
    se ve diminuta, así que la ventana ofrece dividirla o girarla."""
    try:
        width, height = Image.open(io.BytesIO(data)).size  # solo la cabecera: decodificarla costaba ~30 ms
    except Exception:  # un formato que Pillow no lee
        pix = pymupdf.Pixmap(data)
        width, height = pix.width, pix.height
    return width > height * 1.15


def _spread(data, layout):
    """Imagen doble preparada para una pantalla vertical: "split", las dos mitades como páginas
    (la derecha primero, como en el libro japonés); "rotate", girada 90° a la derecha."""
    pix = pymupdf.Pixmap(data)
    with pymupdf.open() as doc:
        page = doc.new_page(width=pix.width, height=pix.height)  # 1 punto = 1 píxel a 72 ppp
        page.insert_image(page.rect, stream=data)
        if layout == "rotate":
            page.set_rotation(90)
            return [_jpeg(page.get_pixmap(), 90)]
        w, h = page.rect.width, page.rect.height
        return [_jpeg(page.get_pixmap(clip=pymupdf.Rect(x0, 0, x0 + w / 2, h)), 90)
                for x0 in (w / 2, 0)]


LONG_WORD = re.compile(r"(?<![\w&-])[^\W\d_]{7,}(?![\w-])")  # «&»: no partir entidades como &hellip;


@functools.lru_cache(maxsize=1)
def _hyphenator():
    import pyphen  # patrones españoles de hyph-utf8, los mismos de LibreOffice
    return pyphen.Pyphen(lang="es", left=2, right=2)


def _hyphenate(html_text):
    """Guiones blandos en las palabras largas, fuera de etiquetas y enlaces: Kindle apenas parte en español
    y el texto justificado queda con huecos. Una palabra con guion propio («Subaru-kun») no se toca."""
    parts, link = re.split(r"(<[^>]+>)", html_text), False
    for i, part in enumerate(parts):
        if part.startswith("<"):
            link = part.startswith("<a ") or (link and part != "</a>")
        elif not link:
            parts[i] = LONG_WORD.sub(lambda m: _hyphenator().inserted(m[0], hyphen=SOFT_HYPHEN), part)
    return "".join(parts)


def _replace(html_text, rules):
    """Aplica los reemplazos del usuario (_rules) solo al texto, sin tocar las etiquetas <i>/<b>."""
    parts = re.split(r"(<[^>]+>)", html_text)
    for k in range(0, len(parts), 2):
        for pattern, new, regex in rules:
            if not regex:
                parts[k] = pattern.sub(lambda _: new, parts[k])
                continue
            # La regex ve el texto sin entidades («;» no rompe «&amp;») y lo que sale se escapa:
            # una plantilla «\074script\076» no mete etiquetas en el EPUB
            plain = html.unescape(parts[k])
            if pattern.search(plain):
                parts[k] = html.escape(pattern.sub(new, plain), quote=False)
    return "".join(parts)


def rule_pattern(find):
    """Expresión de una regla: "re:…" es una expresión regular tal cual; si no, la palabra o frase
    completa ("Ana" no cambia "Anabel"), escapada como el texto del EPUB ("Tom & Jerry" está como
    "Tom &amp; Jerry"). Lanza re.error si la expresión regular no es válida."""
    if find.startswith("re:"):
        return re.compile(find[3:])
    return re.compile(rf"(?<!\w){re.escape(html.escape(find, quote=False))}(?!\w)")


def _rules(replacements):
    """[(buscar, reemplazo)] a [(expresión, reemplazo, es_regex)]. Una expresión regular o una plantilla
    no válida («\\2» con un solo grupo) se salta: la ventana ya avisa al escribirla, y así una conversión
    por lotes no se para."""
    rules = []
    for find, new in replacements:
        if not find:
            continue
        try:
            rules.append(_rule(find, new))
        except (re.error, IndexError):
            continue
    return rules


def _rule(find, new):
    """Una regla de _rules. La plantilla de una regex va sin escapar: «\\g<1>» escapado ya no sería un
    grupo. Lanza re.error o IndexError si la expresión o su plantilla no valen."""
    pattern, regex = rule_pattern(find), find.startswith("re:")
    if regex:
        pattern.sub(new, "")  # compila la plantilla aunque no haya coincidencias: avisa de grupos que no hay
    return pattern, new if regex else html.escape(new, quote=False), regex


def count_matches(book, replacements):
    """Cuántas veces aplica cada regla en los párrafos y títulos del libro: [(buscar, veces o error)]."""
    texts = [b.html for b in book.blocks if b.kind in ("p", "heading")]
    counts = []
    for find, new in replacements:
        try:
            pattern, _, regex = _rule(find, new)
        except (re.error, IndexError) as e:
            counts.append((find, f"expresión no válida: {e}"))
            continue
        see = html.unescape if regex else str  # como _replace: la regex ve el texto sin entidades
        counts.append((find, sum(len(pattern.findall(see(part))) for t in texts for part in re.split(r"<[^>]+>", t))))
    return counts


SAFE = re.compile(r"(</?(?:i|b|sup|sc)>|\[Nota: |<img [^>]*>)")  # marcas del editor que un reemplazo no debe romper


def replace_text(book, find, new, word=False, blocks=None):
    """Arreglos para edit_section que cambian find por new en los párrafos (todos o los índices blocks).
    Sin distinguir mayúsculas, pero si lo encontrado empieza en mayúscula el reemplazo también ("Dijó" ->
    "Dijo"). word: solo palabras completas. Los títulos no: su texto es el de la lista."""
    body = re.escape(find)
    pattern = re.compile(rf"(?<!\w){body}(?!\w)" if word else body, re.I)
    swap = (lambda m: new[:1].upper() + new[1:] if m[0][:1].isupper() else new)
    edits = []
    for k in range(len(book.blocks)) if blocks is None else blocks:
        if book.blocks[k].kind != "p":
            continue
        line = _line(book.blocks[k], k)
        fixed = "".join(p if n % 2 else pattern.sub(swap, p) for n, p in enumerate(SAFE.split(line)))
        if fixed != line:
            edits.append((k, k + 1, fixed))
    return edits


TOKEN = re.compile(r"\[(Ilustración|Separador) (\d+)\]")  # marcas del editor de texto
INLINE = re.compile(r"&lt;(/?)(i|b|sup|sc)&gt;")
NOTE_MARK = re.compile(r"\[Nota: (.+?)\]")  # nota al pie en el editor de texto
# Enlace escrito en el editor, ya escapado: solo direcciones web (un javascript: no sirve en un libro)
LINK_MARK = re.compile(r'&lt;a href="(https?://[^"\s<>]+)"&gt;(.*?)&lt;/a&gt;')
CENTER = "<center>"  # al principio de una línea del editor: párrafo centrado (carta, cartel)
FRAME = "<marco>"  # al principio de una línea del editor: párrafo dentro de un recuadro (ventana de estado)
RIGHT = "<derecha>"  # al principio de una línea del editor: párrafo a la derecha (firma de una carta)
# Versalitas en el libro: <small> y no <span>, que cortaría la nota (<span class="nota">…</span>) que la lleve
SC_OPEN, SC_CLOSE = '<small class="sc">', "</small>"
BULLETS = "■◆●▪•◇□▶►◼"  # viñetas: dentro de un recuadro, las líneas sin ella van sangradas bajo la anterior


def section_text(book, start, end):
    """Texto editable de los bloques start..end-1: un párrafo por línea (con <i>/<b>), "# " delante
    de un título, y las ilustraciones y separadores como marcas "[Ilustración N]" / "[Separador N]"
    (N, el índice del bloque) para poder moverlos o borrarlos."""
    return "\n\n".join(_line(book.blocks[k], k) for k in range(start, end))


def _line(b, k):
    """Línea de section_text del bloque b, que es el número k del libro."""
    if b.kind == "p":
        prefix = {"center": CENTER, "frame": FRAME, "right": RIGHT}.get(b.align, "")
        text = NOTE.sub(r"[Nota: \1]", b.html).replace(SC_OPEN, "<sc>").replace(SC_CLOSE, "</sc>")
        return prefix + html.unescape(text)
    if b.kind == "heading":
        return "# " + html.unescape(b.html)
    return f"[{'Ilustración' if b.kind == 'image' else 'Separador'} {k}]"


def _balanced(out):
    stack = []
    for close, tag in re.findall(r"<(/?)(i|b|sup|sc)>", out):
        if not close:
            stack.append(tag)
        elif not stack or stack.pop() != tag:
            return False
    return not stack


def _inline(text):
    """Línea escrita por el usuario a XHTML: escapada, salvo <i> y <b>, que se conservan si están bien
    cerradas; si no, se quitan para no romper el EPUB. "[Nota: …]" vuelve a ser la nota al pie, si
    dentro las etiquetas también cierran (si no, cruzarían el límite de la nota)."""
    out = INLINE.sub(r"<\1\2>", html.escape(BAD_XML.sub("", text), quote=False))  # un control rompe el XML
    out = out if _balanced(out) else TAGS.sub("", out)
    out = LINK_MARK.sub(lambda m: f'<a href="{m[1]}">{m[2]}</a>' if _balanced(m[2]) else m[0], out)
    out = NOTE_MARK.sub(lambda m: f'<span class="nota">{m[1]}</span>' if _balanced(m[1]) else m[0], out)
    return ICON_MARK.sub(r"<\1>", out).replace("<sc>", SC_OPEN).replace("</sc>", SC_CLOSE)


def edit_section(book, start, end, text):
    """Cambia los bloques start..end-1 por el texto editado (formato de section_text). Las marcas
    recuperan su ilustración o separador; "# Título" crea un título; una línea de solo símbolos
    ("* * *") crea un separador. Ajusta book.candidates y devuelve {índice viejo: índice nuevo} de los
    bloques que siguen en el libro (los párrafos se sustituyen: no están)."""
    old = book.blocks
    page = old[start - 1].page if start else 1
    # Un párrafo o título sin cambios sigue igual (página, recuadro, centrado); lo cambiado o nuevo toma
    # la página del anterior
    same = {}
    for k in range(start, end):
        if old[k].kind in ("p", "heading"):
            same.setdefault(_line(old[k], k).strip(), old[k])
            same.setdefault(_line(old[k], k).strip().removeprefix(CENTER), old[k])  # edición de antes de <center>
    lines = [line for line in (line.strip() for line in text.splitlines()) if line]
    # Párrafo cambiado en su sitio, uno por uno (errata, traducción): sigue siendo el mismo del PDF y conserva
    # su página y su recuadro. Unir o partir párrafos no cuadra uno a uno y toma la página del anterior.
    origin = {}
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, [_line(old[k], k).strip() for k in range(start, end)],
                                                      lines, autojunk=False).get_opcodes():
        if op == "replace" and i2 - i1 == j2 - j1:
            origin |= {j1 + n: old[start + i1 + n] for n in range(i2 - i1)
                       if old[start + i1 + n].kind in ("p", "heading")}
    new, kept = [], {}
    for j, line in enumerate(lines):
        m = TOKEN.fullmatch(line)
        k = int(m.group(2)) if m else -1
        if m and start <= k < end and k not in kept and old[k].kind == ("image" if m.group(1) == "Ilustración" else "break"):
            kept[k] = start + len(new)
            new.append(old[k])
            page = old[k].page
        elif line in same:
            new.append(replace(same[line]))  # copia: la línea puede estar dos veces
            page = same[line].page
        elif line.startswith("# ") and line[2:].strip():
            new.append(Block("heading", page, _inline(line[2:].strip())))
        elif SCENE_BREAK.fullmatch(line):
            new.append(Block("break", page, html.escape(line, quote=False)))
        elif line.startswith(CENTER):
            new.append(Block("p", page, _inline(line[len(CENTER):].strip()), align="center"))
        elif line.startswith(FRAME):
            new.append(Block("p", page, _inline(line[len(FRAME):].strip()), align="frame"))
        elif line.startswith(RIGHT):
            new.append(Block("p", page, _inline(line[len(RIGHT):].strip()), align="right"))
        else:
            new.append(Block("p", page, _inline(line)))
        if j in origin and new[-1].kind in ("p", "heading") and not (m or line in same):
            page = new[-1].page = origin[j].page
            new[-1].rect = origin[j].rect
            was, now = origin[j], new[-1]
            # Traducido sin la marca (revisiones de antes de los recuadros, o la IA la quitó): sigue en su recuadro
            # y con su viñeta. Quitar solo el recuadro deja el mismo texto, y eso no se toca.
            if (was.kind == now.kind == "p" and was.align == "frame" and not now.align
                    and _plain(was) != _plain(now)):
                now.align = "frame"
                bullet = _plain(was)[:1]
                if bullet in BULLETS and _plain(now)[:1] not in BULLETS:
                    now.html = f"{bullet} {now.html}"
    delta = len(new) - (end - start)
    book.blocks = old[:start] + new + old[end:]
    mapping = {k: k for k in range(start)} | kept | {k: k + delta for k in range(end, len(old))}
    book.candidates = [mapping[k] for k in book.candidates if k in mapping]
    return mapping


HONORIFICS = {"san", "sama", "kun", "chan", "dono", "sensei", "senpai", "sempai", "nee", "nii", "onee", "onii",
              "neesan", "niisan", "oneesan", "oniisan", "ue"}


def _fold(text):
    """Clave de un nombre sin tildes, mayúsculas, guiones ni espacios: "Tiño-Sama" -> "tinosama"."""
    plain = "".join(c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c))
    return re.sub(r"[\s\-]", "", plain.lower())


@_memo  # por párrafo: el informe con glosario lo pide tras cada edición, y el libro entero tardaba 0,3 s
def _block_names(block_html):
    """_name_forms de un párrafo."""
    text = html.unescape(TAGS.sub("", block_html))
    tokens = re.findall(r"[^\W\d_]+(?:-[^\W\d_]+)*", text)
    common = frozenset(_fold(t) for t in tokens if t[:1].islower())
    proper = frozenset(_fold(re.split(r"[- ]", m.group(1))[0])
                       for m in re.finditer(r"(?<=[\w,;]) ([^\W\d_][\w-]*)", text) if m.group(1)[:1].isupper())
    forms = collections.Counter(t for t in tokens if t[:1].isupper() and not (len(t) > 1 and t.isupper()))
    forms.update(f"{a} {b}" for a, b in zip(tokens, tokens[1:]) if a[:1].isupper() and b.lower() in HONORIFICS)
    return forms, common, proper


def _name_forms(book):
    """(formas con mayúscula y sus veces, claves _fold de las palabras que salen en minúscula, claves de las que
    van con mayúscula a mitad de frase, como los nombres propios)."""
    forms, common, proper = collections.Counter(), set(), set()
    for b in book.blocks:
        if b.kind in ("p", "heading"):
            f, c, p = _block_names(b.html)
            forms.update(f)
            common |= c
            proper |= p
    return forms, common, proper


def proper_names(book):
    """Los nombres propios del libro y sus veces: {forma: veces}. Palabras con mayúscula que nunca salen en
    minúscula y que van con mayúscula a mitad de frase; sin las formas con el honorífico aparte."""
    forms, common, proper = _name_forms(book)
    return {f: n for f, n in forms.items() if " " not in f and len(_fold(f)) >= 3 and _fold(f) not in common
            and _fold(f.split("-")[0]) in proper}


def _one_edit(a, b):
    """a y b se diferencian en una letra: cambiada, de más o de menos."""
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    a, b = sorted((a, b), key=len)
    return len(b) - len(a) == 1 and any(b[:i] + b[i + 1:] == a for i in range(len(b)))


def glossary_fixes(book, glossary):
    """{forma del libro: término del glosario} de los nombres que no están en el glosario pero son uno de sus
    términos con otras tildes o guiones, o con una letra distinta si el término tiene 5 o más ("Emilía",
    "Reinhart"). Con menos, una letra separa nombres distintos ("Rem" / "Ram")."""
    terms = {_fold(t): t for t in glossary}
    known = set(glossary)
    fixes = {}
    for form in proper_names(book):
        key = _fold(form)
        term = terms.get(key) or next((t for k, t in terms.items() if len(k) >= 5 and _one_edit(key, k)), None)
        if term and form not in known:
            fixes[form] = term
    return fixes


def name_variants(book):
    """Nombres escritos de varias formas ("Greg-sama" / "Greg sama", "Tino" / "Tiño"):
    [[forma más usada, otras…]], los más frecuentes primero. Solo palabras con mayúscula que nunca
    salen en minúscula ("Él" / "El", "Tú" / "Tu") y que alguna vez van con mayúscula en mitad de una
    frase, como los nombres propios ("Utilizó" / "Utilizo" al empezar frase, "Ja-ja" / "Jaja", no)."""
    forms, common, proper = _name_forms(book)
    groups = collections.defaultdict(dict)
    for form, n in forms.items():
        key = _fold(form)
        if len(key) >= 3 and key not in common:
            groups[key][form] = n
    found = [sorted(g, key=lambda f: -g[f]) for g in groups.values()
             if len(g) > 1 and any(_fold(re.split(r"[- ]", f)[0]) in proper for f in g)]
    return sorted(found, key=lambda g: -sum(forms[f] for f in g))


# Honorífico tras un nombre: «Subaru-kun», «Subaru kun»
HONORIFIC = re.compile(r"(?<![\w-])([^\W\d_]+)(-| )((?i:" + "|".join(sorted(HONORIFICS, key=len, reverse=True))
                       + r"))(?![\w-])")
SOLE_WORD = re.compile(r"(?<![\w-])[^\W\d_]+(?![\w-])")
HONORIFIC_ENDS = tuple(HONORIFICS)  # solo una palabra que acaba en honorífico puede ser una forma junta


@_memo
def _honorific_key(form):
    """Clave de un honorífico: «Subaru-kun», «Subaru kun» y «Subarukun» son el mismo; «sempai» es «senpai»."""
    return _fold(form).replace("sempai", "senpai")


@_memo  # el informe lo pide para todo el libro tras cada edición
def _honorific_forms(text):
    """(formas con guion o espacio, palabras que acaban en honorífico) de un párrafo."""
    compounds = tuple(m[0] for m in HONORIFIC.finditer(text) if m[2] == "-"
                      or (m[3].islower() and (m[1][:1].isupper() or m[1].lower() in HONORIFICS)))
    return compounds, tuple(w for w in SOLE_WORD.findall(text) if w.lower().endswith(HONORIFIC_ENDS))


def honorific_fixes(texts):
    """Honoríficos escritos de varias formas en el libro: {forma en minúsculas: (forma elegida, sus veces, veces
    de esta)}, solo de las que no son la elegida: la más usada, o la de guion si la otra no le dobla. Con espacio solo cuentan en minúscula («San Pedro» no es uno). Juntas
    («Subarukun») solo si la separada también sale: «Hassan» no es «Has-san»."""
    forms = collections.Counter()
    for text in texts:
        forms.update(_honorific_forms(text)[0])
    keys = {_honorific_key(f) for f in forms} | {"senpai"}
    for text in texts:
        forms.update(w for w in _honorific_forms(text)[1] if _honorific_key(w) in keys)
    groups = collections.defaultdict(collections.Counter)
    for form, n in forms.items():
        groups[_honorific_key(form)][form] += n
    fixes = {}
    for group in groups.values():
        same = collections.Counter()  # «Senpai» al empezar frase y «senpai» son la misma forma
        for form, n in group.items():
            same[form.lower()] += n
        if len(same) > 1:
            top = same.most_common(1)[0][0]
            # Con guion es lo habitual en las traducciones: gana si la más usada no le dobla («sama» 2, «-sama» 1)
            hyphen = max((low for low in same if "-" in low), key=same.__getitem__, default=None)
            if hyphen and same[top] <= 2 * same[hyphen]:
                top = hyphen
            best = max((f for f in group if f.lower() == top), key=group.__getitem__)
            fixes.update({low: (best, same[top], n) for low, n in same.items() if low != top})
    return fixes


SPELL_DIR = Path(__file__).parent / "dict"  # Hunspell es_ES de LibreOffice (RLA-ES); en el .exe, junto a los módulos


@functools.lru_cache(maxsize=1)
def _hunspell():
    from spylls.hunspell import Dictionary  # tarda unos 4 s: solo cuando se pide la ortografía
    return Dictionary.from_files(str(SPELL_DIR / "es_ES"))


def spell_words(text):
    """Palabras de un texto, como las cuenta la ortografía."""
    return re.findall(r"[^\W\d_]+", unicodedata.normalize("NFC", text))


def spell_skip(known=()):
    """Lo que la ortografía no revisa: el diccionario de la serie (known) y los honoríficos."""
    return {w.lower() for w in known} | HONORIFICS


def spell_candidate(word, skip):
    """¿Se revisa la palabra? Solo en minúscula y de 3 letras o más (con mayúscula suelen ser nombres), fuera
    de skip (spell_skip) y sin onomatopeyas ("aaah")."""
    return word[:1].islower() and len(word) >= 3 and word not in skip and not re.search(r"(.)\1\1", word)


def spell_suggestions(word, good=(), limit=4):
    """Sugerencias para una palabra que no está, como en «Ortografía…»: primero la que solo cambia en tildes,
    luego las palabras correctas parecidas de good (las del libro). Las de Hunspell tardaban de 1 a más de 15 s
    por palabra: el clic derecho se quedaba en «Buscando sugerencias…»."""
    spell = _hunspell()
    accents = [v for v in _accents(word) if v != word and spell.lookup(v)]
    near = difflib.get_close_matches(word, [g for g in good if abs(len(g) - len(word)) <= 2], limit, 0.75)
    return list(dict.fromkeys(accents + near))[:limit]


DIALOG_START = ("—", "–", "-", "«", "“", '"', "'", "‘")  # un párrafo de diálogo empieza por raya o comillas


def text_stats(texts):
    """Cifras de unos párrafos: palabras, párrafos, minutos de lectura (230 por minuto, como
    reading_minutes) y el porcentaje de palabras en párrafos de diálogo."""
    texts = [t.strip() for t in texts if t.strip()]
    counts = [len(t.split()) for t in texts]
    words = sum(counts)
    dialog = sum(n for t, n in zip(texts, counts) if t.startswith(DIALOG_START))
    return {"words": words, "paragraphs": len(texts), "minutes": max(1, round(words / 230)) if words else 0,
            "dialog": round(100 * dialog / words) if words else 0}


def unknown_words(texts, known=()):
    """Palabras de texts que no están en el diccionario: [(palabra, veces, [sugerencias])], las más repetidas
    primero. Solo en minúscula y de 3 letras o más: con mayúscula suelen ser nombres. No cuentan known (el
    diccionario de la serie), los honoríficos ni las onomatopeyas ("aaah"). Se sugieren palabras correctas
    del propio libro, primero la que solo cambia en tildes: las de Hunspell tardan ~1 s cada una."""
    words = collections.Counter(w for t in texts for w in spell_words(t))
    skip = spell_skip(known)
    spell, good, bad = _hunspell(), [], []
    for w, n in words.items():
        if spell_candidate(w, skip):
            (good if spell.lookup(w) else bad).append(w)
    folded = {_fold(w): w for w in sorted(good, key=lambda w: words[w])}  # la más usada gana
    found = []
    for w in bad:
        accents = [v for v in _accents(w) if v != w and spell.lookup(v)]
        near = difflib.get_close_matches(w, [g for g in good if abs(len(g) - len(w)) <= 2], 4, 0.75)
        same = [folded[_fold(w)]] if _fold(w) in folded else []
        found.append((w, words[w], list(dict.fromkeys(accents + same + near))[:4]))
    return sorted(found, key=lambda f: (-f[1], f[0]))


def _accents(word):
    """La palabra sin tildes y con una tilde en cada vocal ("dijó" -> "dijo", "tambien" -> "también"): la
    errata más común, y pocas consultas al diccionario. La ñ y la ü se conservan."""
    bare = unicodedata.normalize("NFC", unicodedata.normalize("NFD", word).replace("́", ""))
    marks = {"a": "á", "e": "é", "i": "í", "o": "ó", "u": "ú"}
    return [bare] + [bare[:n] + marks[c] + bare[n + 1:] for n, c in enumerate(bare) if c in marks]


def body_words(book):
    """Cómo escribe el libro cada palabra: (veces en minúscula, veces con mayúscula en mitad de una
    frase), para saber cuáles son nombres propios."""
    text = " ".join(html.unescape(TAGS.sub("", b.html)) for b in book.blocks if b.kind == "p")
    lower = collections.Counter(w for w in re.findall(r"[^\W\d_]+", text) if w.islower())
    proper = collections.Counter(m.group(1).lower() for m in re.finditer(r"(?<=[\w,;] )([^\W\d_]+)", text)
                                 if m.group(1)[:1].isupper())
    return lower, proper


def spanish_case(title, words):
    """Título con las mayúsculas del español: solo la primera palabra (y la que sigue a ":" o a un
    punto, cierre de exclamación o interrogación) y los nombres propios. words: body_words; es nombre
    la palabra que el libro escribe con mayúscula en mitad de frase más veces que en minúscula."""
    lower, proper = words
    all_caps = title.isupper()
    parts = re.split(r"([^\W\d_]+)", title)  # las palabras quedan en las posiciones impares
    capital = True
    for k, part in enumerate(parts):
        if k % 2 == 0:
            if re.search(r"[.:!?]", part):
                capital = True
            continue
        low = part.lower()
        if capital:
            parts[k] = low[:1].upper() + low[1:] if all_caps else part[:1].upper() + part[1:]
        elif proper[low] > lower[low]:
            parts[k] = low[:1].upper() + low[1:] if all_caps else part
        elif all_caps or part[1:] == low[1:]:  # "Al" -> "al"; una sigla ("NPC") en un título normal, no
            parts[k] = low
        capital = False
    return "".join(parts)


# palabras completas: «discordia» o «twitteros» no son publicidad del grupo
LINKS = re.compile(r"https?://|www\.|\b(?:facebook|twitter|discord|patreon|ko-fi|paypal|instagram|tiktok|wixsite"
                   r"|blogspot)\b", re.I)


def link_sections(book):
    """Títulos cuya sección es publicidad del grupo (redes, Patreon, enlaces): corta, de menos de 2000
    letras, y con algún enlace. La sección llega hasta donde la quitaría write_epub. En los
    preliminares cuenta también la página: en una página de créditos con enlaces (Re Zero) se quitan
    todos sus títulos, no solo el último."""
    heads = [k for k, b in enumerate(book.blocks) if b.kind == "heading"]
    first = book.blocks[book.candidates[0]].page if book.candidates else 0
    linked = {b.page for b in book.blocks if b.page < first and b.kind in ("p", "heading") and LINKS.search(b.html)}
    found = [k for k in heads if book.blocks[k].page in linked]
    for k in heads:
        chapter = k in book.candidates
        end = next((j for j in heads if j > k and (j in book.candidates or not chapter)), len(book.blocks))
        text = " ".join(TAGS.sub("", b.html) for b in book.blocks[k:end] if b.kind in ("p", "heading"))
        if len(text) < 2000 and LINKS.search(text) and k not in found:
            found.append(k)
    return sorted(found)


def _join(a, b):
    """Une dos líneas de un párrafo; una palabra partida con guion se junta aunque haya etiquetas.
    Un honorífico tras el guion ("Gilbert-" / "kun") no es una palabra partida: conserva el guion."""
    if re.search(r"\w-$", TAGS.sub("", a)) and TAGS.sub("", b)[:1].islower():
        if re.match(r"[^\W\d_]*", TAGS.sub("", b)).group().lower() in HONORIFICS:
            return a + b
        i = a.rindex("-")  # el guion final; detrás solo puede haber etiquetas de cierre
        return a[:i] + a[i + 1:] + b
    return f"{a} {b}"


SUBPART = re.compile(r"(parte|part|secci[oó]n|section)\s+[\w.]+", re.I)
BARE_PART = re.compile(r"\d{1,2}")  # Re Zero 4 y 5: «2» solo, en negrita, marca el apartado como «Parte 2»


def is_subpart(title):
    """¿Apartado sin nombre de un capítulo? «Parte 2», «Sección 3» o el número solo de Re Zero 4."""
    title = title.strip()
    return bool(SUBPART.fullmatch(title) or BARE_PART.fullmatch(title))
CREDIT = re.compile(r"(autor|author|escrito por|historia|traducci[oó]n|traductor(?:es)?|translation|ilustraci[oó]n(?:es)?"
                    r"|ilustrador(?:a|es)?|illustrations?|correcci[oó]n|corrector(?:a|es)?|edici[oó]n|editor(?:a|es)?)"
                    r"([^:]{0,40}):\s*(.+)", re.I)


def _credits(blocks):
    """Autor y traductor desde la página de créditos de los preliminares ("Autor: Tappei Nagatsuki",
    "Traducción al español: …"). Una línea que acaba en guion sigue en la siguiente. La traducción a
    otro idioma ("Traducción al inglés") no cuenta: es la de la edición de la que se tradujo."""
    lines, found = [], {}
    for b in blocks:
        if b.kind == "p":
            text = html.unescape(TAGS.sub("", b.html)).strip()
            if lines and re.search(r"[-–]$", lines[-1]):
                lines[-1] += " " + text
            else:
                lines.append(text)
    for line in lines:
        m = CREDIT.match(line)
        if not m:
            continue
        what, detail, value = m.group(1).lower(), m.group(2).lower(), m.group(3).strip(" -–")
        if what.startswith(("autor", "author", "escrito", "historia")):
            found.setdefault("author", value)
        elif what.startswith(("ilustr", "illustr")):
            found.setdefault("illustrator", value)
        elif what.startswith("correc"):
            found.setdefault("proofreader", value)
        elif what.startswith(("edici", "editor")):  # "Edición de las ilustraciones en español: …"
            found.setdefault("editor", value)
        elif not re.search(r"ingl[eé]s|japon[eé]s|english|japanese", detail) or "español" in detail:
            found.setdefault("translator", value)
    return found


def counted(n, one, many=None):
    """«1 capítulo», «3 capítulos»: el número con su palabra (many si el plural no es one + "s")."""
    return f"{n} {one if n == 1 else (many or one + 's')}"


@_memo  # el informe de calidad lo pide por párrafo tras cada edición
def _letters(text):
    return sum(map(str.isalpha, text))


def _plain(block):
    return _unhtml(block.html)


@_memo  # tras una edición casi todos los párrafos siguen igual
def _unhtml(text):
    if "icono" in text:  # «así ♡ y» sin el icono es «así y», no «así  y»
        text = ICON_TAG.sub("", text.replace(' <img class="icono"', '<img class="icono"'))
    return html.unescape(TAGS.sub("", text))


NUMBER_WORDS = ("cero uno dos tres cuatro cinco seis siete ocho nueve diez once doce trece catorce quince dieciséis"
                " diecisiete dieciocho diecinueve veinte veintiuno veintidós veintitrés veinticuatro veinticinco"
                " veintiséis veintisiete veintiocho veintinueve treinta").split()
LABEL_FIX = {"capitulo": "Capítulo", "prologo": "Prólogo", "epilogo": "Epílogo"}  # sin tilde -> con tilde
ROMAN = re.compile(r"(?=[IVXLC])(C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})")


def _roman(text):
    """Valor de un número romano ("XIV" -> 14) o None."""
    m = ROMAN.fullmatch(text.upper())
    if not m or text != text.upper():
        return None
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
    total = 0
    for a, b in zip(text, text[1:] + " "):
        total += -values[a] if values.get(b, 0) > values[a] else values[a]
    return total


def numbered(title):
    """Numeración uniforme: "Capítulo Uno" / "Capítulo IV" / "Capitulo 3" -> "Capítulo 1" / "Capítulo 4" /
    "Capítulo 3". También pone la tilde que falte en Capítulo, Prólogo y Epílogo."""
    m = re.match(r"(cap[ií]tulo|chapter|parte|part|volumen|libro|pr[oó]logo|ep[ií]logo)\b(\s+)?([^\W_]+)?", title, re.I)
    if not m:
        return title
    word = m.group(1)
    fixed = LABEL_FIX.get(_fold(word), word)
    fixed = fixed if word[:1].isupper() or word.isupper() else fixed.lower()
    if word.isupper() and len(word) > 1:
        fixed = fixed.upper()
    number = m.group(3)
    if number and m.group(2):
        value = NUMBER_WORDS.index(number.lower()) if number.lower() in NUMBER_WORDS else _roman(number)
        if value is not None:
            return f"{fixed} {value}{title[m.end():]}"
    return fixed + title[len(word):]


def reading_minutes(blocks):
    """Minutos de lectura de unos bloques, a 230 palabras por minuto."""
    words = sum(len(_plain(b).split()) for b in blocks if b.kind in ("p", "heading"))
    return max(1, round(words / 230)) if words else 0


def move_section(book, start, end, dest):
    """Mueve los bloques start..end-1 para que empiecen donde estaba el bloque dest (dest fuera del
    tramo; len(blocks) los lleva al final). Ajusta book.candidates y devuelve {índice viejo: nuevo}."""
    order = list(range(len(book.blocks)))
    moved = order[start:end]
    rest = order[:start] + order[end:]
    at = rest.index(dest) if dest < len(order) else len(rest)
    order = rest[:at] + moved + rest[at:]
    mapping = {old: new for new, old in enumerate(order)}
    book.blocks = [book.blocks[k] for k in order]
    book.candidates = sorted(mapping[k] for k in book.candidates)
    return mapping


def _hash(data):
    """Huella de 64×64 en gris de una imagen, para encontrar repetidas aunque cambie la compresión."""
    pix = _rgb(pymupdf.Pixmap(data), alpha=False)
    pix = pymupdf.Pixmap(pymupdf.csGRAY, pymupdf.Pixmap(pix, 64, 64, None))
    return pix.samples


def _same(a, b):
    """Misma imagen salvo la compresión: como mucho un 1 % de píxeles muy distintos. Las portadas de
    capítulo con el mismo marco y otro título (Kage) cambian bastante más en la zona del título."""
    return sum(abs(x - y) > 40 for x, y in zip(a, b)) <= len(a) // 100


def duplicate_images(book):
    """Ilustraciones repetidas (la portada otra vez dentro del libro, un inserto duplicado): índices
    de las que sobran. Se conserva la primera; la portada cuenta como ya vista."""
    seen = [_hash(book.cover)] if book.cover else []
    found = []
    for k, b in enumerate(book.blocks):
        if b.kind != "image" or not b.image:
            continue
        mark = _hash(b.image)
        if any(_same(mark, other) for other in seen):
            found.append(k)
        else:
            seen.append(mark)
    return found


def _trim(data):
    """Recorta el margen blanco uniforme de una ilustración escaneada; si el borde no es blanco o el
    recorte se comería más de la mitad, la deja como está."""
    pix = pymupdf.Pixmap(data)
    if pix.alpha:
        return data
    gray = pymupdf.Pixmap(pymupdf.csGRAY, pix) if pix.n > 1 else pix
    w, h, s = gray.width, gray.height, gray.samples

    def ink(line):
        return min(line) < 235

    top = next((y for y in range(h) if ink(s[y * w:(y + 1) * w])), h)
    bottom = next((y for y in range(h - 1, -1, -1) if ink(s[y * w:(y + 1) * w])), -1) + 1
    left = next((x for x in range(w) if ink(s[x::w])), w)
    right = next((x for x in range(w - 1, -1, -1) if ink(s[x::w])), -1) + 1
    if bottom <= top or right <= left:
        return data
    if (right - left) * (bottom - top) < 0.5 * w * h or (left, top, right, bottom) == (0, 0, w, h):
        return data
    with pymupdf.open() as doc:
        page = doc.new_page(width=w, height=h)
        page.insert_image(page.rect, stream=data)
        return _jpeg(page.get_pixmap(clip=pymupdf.Rect(left, top, right, bottom)), 90)


LEVELS = ("Grave", "Revisar", "Menor")  # gravedad de un aviso del informe de calidad, de más a menos
MOJIBAKE = re.compile(r"Ã[\x80-\xbf]|Â[\xa0-\xbf]|â€")  # UTF-8 leído como Windows-1252: "canciÃ³n"
ENGLISH = set("the and you is are was were of to that it its this what with for on in at be have has had "
              "not but my your his her he she they we i i'm don't it's can will would there their about "
              "from as an".split())  # palabras vacías: en un texto inglés son una de cada tres o más
SPANISH = set("el la los las de que y en un una es por con se lo su para al del me te le mi tu".split())


@dataclass
class Issue:
    """Un aviso del informe de calidad, con lo que hace falta para entenderlo y arreglarlo."""
    kind: str                # tipo de aviso ("lower", "number"…): "Arreglar todos" junta los del mismo tipo
    block: int | None        # índice del bloque, o None si es texto que no llegó al libro
    page: int
    level: str               # uno de LEVELS
    title: str               # qué pasa, en una línea
    problem: str             # por qué es sospechoso
    solution: str            # qué hacer
    before: str = ""         # vista previa del texto ahora
    after: str = ""          # y con el arreglo ("" si el arreglo quita el párrafo)
    edit: tuple | None = None  # arreglo automático: (inicio, fin, texto) para edit_section
    text: str = ""           # texto que buscar en las vistas previas
    span: tuple | None = None  # (inicio, fin) del problema en el párrafo como lo muestra el editor (_issue_span)


def _cut(text, n=160, tail=False):
    """El principio (o el final) de un texto largo, para la vista previa."""
    if len(text) <= n:
        return text
    return "…" + text[-n:].lstrip() if tail else text[:n].rstrip() + "…"


def _around(text, start, end, n=70):
    """El texto alrededor de start..end, para la vista previa."""
    a, b = max(0, start - n), min(len(text), end + n)
    return ("…" if a else "") + text[a:b] + ("…" if b < len(text) else "")


def _remojibake(text):
    """Deshace "canciÃ³n" -> "canción", o None si no se puede sin perder nada."""
    try:
        return text.encode("cp1252").decode("utf-8")
    except UnicodeError:
        return None


# Una oración que acaba en ? o !, desde el corte anterior. Los puntos suspensivos, la raya y el cierre de
# comillas no cortan: «¿El grupo que... los fundó?», «¿Conoces este país —el reino— ahora?» (Re Zero 2,
# Nageki 1), «¿Desde cuándo el concepto de «hacer trampa» se aplica?» (Maryoku)
SENTENCE = re.compile(r"((?:\.{2,}+|[^.!?])*?)([?!]+)")  # posesivo: «Pensé....» largo no se cuelga
LEAD = re.compile(r"(?:\s|[«»“”\"'(—–-]|</?(?:i|b|sup)>|<small class=\"sc\">|</small>)*")   # lo que va antes de la primera palabra
DOUBLED = re.compile(r"\b(de|la|el|que|en|y|los|las|un|una|por|con|se|lo) \1\b", re.I)


def _opening_marks(line):
    """Pone ¿ o ¡ al principio de las oraciones que solo llevan el signo de cierre ("Qué haces?")."""
    def fix(m):
        body, close = m[1], m[2]
        lead = LEAD.match(body).end()
        # Tras «?, me pregunto» la oración sigue: no empieza otra
        if "¿" in body or "¡" in body or not any(c.isalpha() for c in body) or body[lead:lead + 1] in ",;:":
            return m[0]
        return body[:lead] + ("¿" if close[0] == "?" else "¡") + body[lead:] + close
    return SENTENCE.sub(fix, line) if "?" in line or "!" in line else line  # sin signos, nada que recorrer


# "(N.T.: …)", "[NdT: …]", "(Nota del traductor: …)" dentro del párrafo; sin paréntesis dentro de la nota
INLINE_NOTE = re.compile(r"\s*[(\[]\s*(?:N\.\s?T\.?|N/T|NdT|T/N|Nota del? traductor)\s*[:.\-–—]?\s*([^()\[\]]+?)\s*[)\]]",
                         re.I)

def _subs(*rules):
    """Arreglo de una línea que aplica varias sustituciones seguidas: una función o (patrón, cambio[, pistas]).
    Con pistas (textos), el patrón solo se prueba si la línea tiene alguna: mucho más rápido que el patrón."""
    rules = [r if callable(r) else (functools.partial(re.compile(r[0]).sub, r[1]), r[2] if len(r) > 2 else ())
             for r in rules]

    def fix(s):
        for rule in rules:
            if callable(rule):
                s = rule(s)
            elif not rule[1] or any(h in s for h in rule[1]):
                s = rule[0](s)
        return s
    return fix


def _table(table, names=()):
    """Arreglo de las palabras de table {mal: bien}, con su mayúscula inicial. Las de names, que también son
    nombres propios ("Ivan", "Dragon"), solo en minúscula."""
    keys = list(table) + [k.capitalize() for k in table if k not in names]
    pattern = re.compile(r"\b(?:" + "|".join(sorted(keys, key=len, reverse=True)) + r")\b")

    def repl(m):
        # En mayúscula a media frase es un nombre o un título («The Legend of the Heroes»); con guion, una risa
        # o un sonido («Ahi-ha»)
        if (m[0][0].isupper() and m.string[:m.start()].rstrip()[-1:].isalpha()
                or m.string[m.end():m.end() + 1] == "-"):  # «A-Asi», tartamudeo, sí
            return m[0]
        return _case(table[m[0].lower()], m[0])

    def fix(s):
        # Mirar antes si sale alguna: recorrer cientos de alternativas en cada párrafo es lo lento
        if table.keys().isdisjoint(_words(s)):
            return s
        return pattern.sub(repl, s)
    return fix


@functools.lru_cache(maxsize=4)
def _words(s):
    """Las palabras de una línea, en minúscula; las miran varias reglas seguidas."""
    return frozenset(re.findall(r"[^\W\d_]+", s.lower()))


def _after(words, new, n=1):
    """Cambio por new de lo encontrado si lo preceden una de words (sus n palabras de antes, en minúscula).
    El patrón empieza por su palabra rara («echo», «ves»): una alternativa larga al principio, en cada
    posición de cada párrafo, hacía el informe tres veces más lento."""
    tail = re.compile(r"[^\W\d_]+" + r" [^\W\d_]+" * (n - 1) + "$")

    def repl(m):
        w = tail.search(m.string, max(0, m.start() - 40), m.start())
        return new if w and w[0].lower() in words else m[0]
    return repl


def _ion(m):
    """«informacion» -> «información»: solo palabras en minúscula ("Fusion", nombre, no) y sin "ss"
    ("mission", inglés, no)."""
    w = re.search(r"[^\W\d_]+$", m.string[max(0, m.start() - 30):m.start()])
    return "ión" if w and w[0].islower() and w[0][-1] in "cs" and w[0][-2:] != "ss" else m[0]


def _case(new, old):
    """new con la mayúscula inicial de old: ("tú", "Tu") -> "Tú"."""
    return new.capitalize() if old[:1].isupper() else new


BARE = str.maketrans("áéíóú", "aeiou")
# Palabras que sin tilde no existen («mas» y «aun», sí: «mas no», «aun así» están bien); de erratas reales
# de las traducciones de la carpeta de muestras
ACCENTS = {w.translate(BARE): w for w in (
    "también después aquí ahí allí allá acá así además quizás jamás detrás atrás todavía adiós demás ningún "
    "algún según común razón corazón ojalá país día días mío mía míos mías tío tía oír reír sonreír héroe "
    "héroes café fácil difícil útil inútil débil rápido rápida rápidos rápidas rápidamente último última "
    "últimos últimas últimamente único única únicos únicas únicamente prácticamente básicamente mágico mágica "
    "mágicos mágicas técnica técnicas música próximo próxima máximo máxima mínimo mínima lágrima lágrimas "
    "jóvenes imágenes dragón jardín están éramos íbamos estábamos").split()}
ACCENTS |= {"fué": "fue", "dió": "dio", "vió": "vio", "fuí": "fui", "tí": "ti", "ví": "vi"}
# Imperfecto y condicional: "tenia" -> "tenía". Solo raíces cuyo "-ia" sin tilde no es otra palabra ("sabia",
# "hacia", "seria" y "venia", sí)
ACCENTS |= {stem + end: stem + "í" + end[1:] for end in ("ia", "ias", "ian", "iamos") for stem in (
    "hab ten pod quer dec viv parec conoc cre le ve com deb segu sent ped sal exist sonre ment tem corr entend "
    "perd dorm ofrec volv pon tra ca o podr tendr har querr deber estar habr dir sabr vendr saldr pondr "
    "gustar encantar importar preferir").split()}
NAMES = {"dragon", "mia", "mio", "dia", "tia", "tio", "iva", "ivan", "ivas"}  # también nombres: solo en minúscula
INTERROGATIVE = {w.translate(BARE): w for w in "qué cómo dónde quién quiénes cuál cuáles cuánto cuánta cuántos "
                                                "cuántas".split()}
PREPOSITIONS = "a|de|con|para|por|sin|ante|contra|entre|hacia|hasta|según|sobre|tras"
# Tras «a el» / «de el» va un nombre («al asiento»), salvo que «el» sea el pronombre: «a él le», «de él con»
PRONOUN_NEXT = "le|les|lo|me|te|se|nos|no|ni|también|tampoco|con|y|pero"
SPELLING = {"aveces": "a veces", "talvez": "tal vez", "enserio": "en serio", "derepente": "de repente",
            "atravez": "a través", "atraves": "a través", "almenos": "al menos", "porfavor": "por favor",
            "osea": "o sea", "apesar": "a pesar", "sinembargo": "sin embargo", "deveras": "de veras",
            "haci": "así", "haiga": "haya", "haigan": "hayan", "iva": "iba", "ivan": "iban", "ivas": "ibas",
            "hiba": "iba", "hiban": "iban", "aber": "haber", "ubo": "hubo", "ubiera": "hubiera",
            "ubieran": "hubieran", "ubieron": "hubieron", "abia": "había", "abía": "había", "abian": "habían",
            "abían": "habían", "tube": "tuve", "tubiste": "tuviste", "tubimos": "tuvimos", "tubieron": "tuvieron",
            "tubiera": "tuviera", "tubieras": "tuvieras", "tubieran": "tuvieran", "tubiese": "tuviese",
            "hechar": "echar", "hechaba": "echaba", "hechaban": "echaban", "hechando": "echando", "hechó": "echó",
            "hecharon": "echaron", "hechara": "echara", "dijistes": "dijiste", "hicistes": "hiciste",
            "fuistes": "fuiste", "vinistes": "viniste", "tuvistes": "tuviste", "estuvistes": "estuviste",
            "pudistes": "pudiste", "quisistes": "quisiste", "supistes": "supiste"}
# Lo que pide «de que»: «darse cuenta de que», «a pesar de que», «acordarse de que», «estar seguro de que»
# («Seguro que vendrá», sin «estar», está bien)
QUEISMO = ({f"{d} cuenta" for d in "di dio diste dimos dieron doy das da damos dan daba daban dé des darme darte "
            "darse darnos dándome dándote dándose dando".split()} | {"a pesar", "en caso"}
           | {f"{p} {a}" for p in "me te se nos".split()
              for a in "acuerdo acuerdas acuerda acordamos acuerdan acordé acordó acordaba".split()}
           | {f"{e} {s}" for e in "estoy estás está estaba estabas estaban estamos están estaría esté estés".split()
              for s in "seguro segura seguros seguras".split()})
DASHES = ("—", "―", "─", "–")  # rayas del PDF: «—», «―» (Re Zero), «─» (Kage), «–»
PLACES = set("delante detrás encima debajo cerca alrededor enfrente dentro lejos".split())  # «detrás mío»
# Nombres en -ar/-er/-ir: tras «ha» no son un infinitivo («no ha lugar»)
NOT_INFINITIVE = "lugar|mar|hogar|altar|azar|bar|collar|par|mujer|ayer|placer|poder|deber|taller|alquiler"
ABBREVIATIONS = set("sr sra srta dr dra ud uds vd etc aprox pág págs núm vol cap ej av lic ing prof vs st mr mrs "
                    "ms art fig tel min máx mín seg atte".split())  # tras ellas el punto no acaba la frase
# «tomar lugar», «hacer sentido» -> «tener lugar», «tener sentido», en el mismo tiempo
TENER = {"toma": "tiene", "tomó": "tuvo", "tomaba": "tenía", "tomará": "tendrá", "tomaría": "tendría",
         "tomado": "tenido", "tomar": "tener", "toman": "tienen", "tomaron": "tuvieron", "tomaban": "tenían",
         "hace": "tiene", "hizo": "tuvo", "hacía": "tenía", "hará": "tendrá", "haría": "tendría", "hacen": "tienen",
         "hacer": "tener"}
DAYS_MONTHS = ("Lunes|Martes|Miércoles|Jueves|Viernes|Sábado|Enero|Febrero|Marzo|Mayo|Junio|Julio|Agosto|"
               "Septiembre|Setiembre|Octubre|Noviembre|Diciembre")  # Abril y Domingo también son nombres
# «el Señor Demonio», «el Señor Oscuro»: títulos de fantasía que hacen de nombre, con mayúscula
TITLE_NOUNS = "Demonio|Demonios|Oscuro|Supremo|Dragón|Dragones|Feudal|Celestial|Espada|Tiempo|Destino|Lich"
CAPITAL = re.compile(r"([^\W\d_]{2,})\.\s+[a-záéíóúñ]")  # "la mesa. luego"; los puntos suspensivos no cuentan


# Tras «¿que» repiten lo dicho: «¿Que el Maestro…?». Sin «esta», «estas»: suelen ser «está», «estás» sin tilde
DETERMINERS = set("el un una unos unas ese esa esos esas aquel aquella mi mis tu tus su sus nuestro nuestra "
                  "nuestros nuestras".split())
# Tras «¿como» un verbo pide «cómo» («¿Cómo supiste?»); lo demás es comparación: «¿Como tú?», «¿Como qué?»
VERB = re.compile(r" (?:es|eres|era|eras|fue|fuiste|son|soy|somos|está|estás|están|estaba|estabas|estuvo|ha|has|"
                  r"han|he|hemos|hay|había|se|te|me|nos|os|le|les|lo|sé|sabes|sabe|sabías|supiste|supo|puede|puedes|"
                  r"pueden|puedo|pudiste|pudo|podría|podrías|va|vas|van|vamos|voy|iba|hiciste|hizo|haces|hace|"
                  r"hacen|hago|quieres|quiere|quieren|dices|dice|dijiste|dijo|no|sigues|sigue|demonios|diablos|rayos|"
                  r"[^\W\d_]+(?:aste|iste|aron|ieron)|[^\W\d_]{2,}(?:ar|er|ir)(?:me|te|se|lo|la|le|los|las|les|nos)?)"
                  r"\b|\s*[?!….]")
LABEL = re.compile(r"[^.!?:]{2,60}:\s")  # «TÍTULO: …», «Corrección: Rulos»: fichas y créditos, no frases


def _pronoun(m):
    """«tu», «mi», «el», «si» antes de un signo -> «tú», «mí», «él», «sí». No si es la palabra citada a media
    frase («y “si”», «empiezan con “Mi”»; «“Si”, decretó», al principio, sí), ni «Oh mi» (de «Oh my»), ni el artículo de una frase dicha palabra a palabra:
    «¡Ese! ¡Es! ¡El! ¡Problema!» (Re Zero)."""
    before = m.string[:m.start()]
    if (before[-1:] in "“«\"'‘" and before[:-1].strip() or re.search(r"\b[Oo]h $", before)
            or m[0].lower() == "el" and re.search(r"(?:^|[.!?…]\s*)[¡¿“]*[^\W\d_]+[.!?]\s*[¡¿]*$", before)):
        return m[0]
    return _case({"tu": "tú", "mi": "mí", "el": "él", "si": "sí"}[m[0].lower()], m[0])


def _question(m):
    """«¿que…?», «¿como…?» -> «¿qué…?», «¿cómo…?». Salvo «¿Que el Maestro me está usando?», «¿Que habrá
    personas que…?», que repiten lo dicho (tras «que» un artículo o un nombre, o una pregunta larga), y «¿Como
    tú?», comparación (tras «como» no va un verbo)."""
    word, rest = m[2].lower(), m.string[m.end():]
    if word == "que":
        question = re.match(r"[^?]*", rest)[0]
        nxt = re.match(r" ([^\W\d_]+)", rest)
        if nxt and (nxt[1] in DETERMINERS or nxt[1][0].isupper()) or len(question.split()) >= 7:
            return m[0]
    if word == "como" and not VERB.match(rest):
        return m[0]
    return "¿" + m[1] + _case(INTERROGATIVE[word], m[2])


def _open_space(m):
    """Quita el espacio tras «¿ ¡ « “ (». No si “ va suelta entre espacios y en la línea sobran “: hace de raya
    («La palabra “Torre “ probablemente…», Mitsuha), ni ¡ ¿ pegados a otro signo («…?¡ campo», Re Zero)."""
    s = m.string
    before = re.sub(r"(?:<[^>]+>)+$", "", s[:m.start()])
    if (m[1] == "“" and before[-1:].isspace() and s.count("“") > s.count("”")
            or m[1] in "¿¡" and before[-1:] in ("?", "!")):
        return m[0]
    return m[1]


def _quote_space(m):
    """Espacio tras » ” ) pegados a una palabra. Una ” sin “ antes no cierra: hace de raya («piezas” quiero
    decir», Mitsuha)."""
    if m[1] in "»”" and not re.search("[«“\"]", m.string[:m.start()]):
        return m[0]
    return m[1] + " "


def _inciso(m):
    """«—Hola, — dijo» -> «—Hola —dijo». No si la raya, pegada al signo, cierra un inciso («pieles, etc.— que»,
    Maryoku), ni si es la primera y única de la línea: abre el diálogo tras la narración, «El guardia contestó,
    ―tengo entendido…» (Re Zero)."""
    s = m.string
    if re.match(r"[^\W\d_][.,][—―─–]\s", m[0]) or (
            not any(d in s[:m.start()] or d in s[m.end():] for d in DASHES)):
        return m[0]
    return m[1] + " —"


def _close(m):
    """Cambia por ? o ! el punto que acaba una pregunta o exclamación abierta. No tras una abreviatura («¡Hola,
    Sr. García!»), ni si la frase que sigue la cierra: «¡Oh, wow. Esos son barcos!» (Mitsuha)."""
    rest = re.search(r"[¿¡?!]", m.string[m.end():])
    if m[3].lower() in ABBREVIATIONS or rest and rest[0] in "?!":
        return m[0]
    return m[1] + m[2] + m[3] + ("?" if m[1] == "¿" else "!")


TYPOS = (  # (tipo, título, arreglo de la línea, por qué, solución): erratas típicas de las traducciones fan
    ("opening", "Falta el signo de apertura", _opening_marks,
     "En español las preguntas y exclamaciones llevan signo de apertura (¿ ¡) además del de cierre.", "Corregirlo."),
    ("doubled", "Palabra repetida", lambda s: DOUBLED.sub(r"\1", s),
     "La misma palabra dos veces seguidas suele ser un error al escribir.", "Corregirlo."),
    ("aver", "«Haber si» en vez de «a ver si»",
     lambda s: re.sub(r"(^|[¿¡—«“\"(.…]\s*)([Hh])aber si\b",
                      lambda m: m[1] + ("A" if m[2] == "H" else "a") + " ver si", s),
     "«A ver si» (del verbo ver) se confunde a menudo con «haber».", "Corregirlo."),
    ("porque", "«¿Porque» en vez de «¿por qué»", lambda s: re.sub(r"¿([Pp])orque\b", r"¿\1or qué", s),
     "En una pregunta va «por qué», separado y con tilde.", "Corregirlo."),
    ("note", "Nota del traductor en el texto", lambda s: INLINE_NOTE.sub(lambda m: f"[Nota: {m[1]}]", s),
     "Una nota entre paréntesis corta la lectura; como nota emergente se lee al tocar la llamada.",
     "Convertirla en nota emergente."),
    ("accent", "Falta o sobra una tilde", _subs(
        _table(ACCENTS, NAMES),
        # Antes de un signo, «tu», «mi», «el» y «si» son pronombres o afirmación: «para mí», «¡Tú!», «¿Sí?».
        # Tras puntos suspensivos o una coma puede ser un titubeo: «mi… mi espada», «el, eh, enviado»
        (r"\b(?:[Tt]u|[Mm]i|[Ee]l|[Ss]i)(?=[.;:!?»”)](?!\.))", _pronoun),
        (rf" el(?=,| (?:{PRONOUN_NEXT})\b)", _after(set(PREPOSITIONS.split("|")), " él")),
        # «¿Que no vienes?», «¿Que si vendrá?» repiten lo dicho: ahí «que» no lleva tilde
        (r"¿(\s*(?:[Yy] )?)(" + "|".join(list(INTERROGATIVE) + [w.capitalize() for w in INTERROGATIVE]) +
         r")\b(?! (?:no|si)\b)", _question),
        (r"\bmas\b(?! no\b)", "más"),
        (r"ion\b", _ion)),
     "Palabras que sin tilde no existen («tambien», «tenia», «informacion»), pronombres que la llevan («para "
     "mí», «con él», «¡Tú!»), preguntas («¿cómo?») y monosílabos que no la llevan («fue», «dio», «ti»).",
     "Corregirlo."),
    ("spelling", "Palabra mal escrita", _subs(
        _table(SPELLING, NAMES),
        (r" ha\b", _after(set("voy vas va vamos van iba ibas iban íbamos".split()), " a")),
        (rf"\b([Hh])a (?=(?!(?:{NOT_INFINITIVE})\b)[a-záéíóúñ]+(?:ar|er|ir)\b)", lambda m: ("A" if m[1] == "H" else "a") + " ",
         ("ha ", "Ha ")),
        (r"\b([Aa]) (?=(?:sido|habido|tenido|podido)\b)", lambda m: ("Ha" if m[1] == "A" else "ha") + " ",
         ("sido", "habido", "tenido", "podido")),
        (r"\b([Ee]) (?=(?:sido|estado|hecho|visto|dicho|tenido|podido|habido|oído|querido|pensado)\b)",
         lambda m: ("He" if m[1] == "E" else "he") + " ", ("ido", "hecho", "visto", "dicho", "oído")),
        (r" echo\b", _after(set("he has ha hemos han había habías habían haber habría hubiera hubiese de".split()),
                            " hecho")),
        (r" ves\b", _after(set("una otra tal cada alguna esta primera última de".split()), " vez")),  # «¿La ves?», no
        (r" ves\b", _after({"a la"}, " vez", 2))),
     "Errata frecuente: palabras juntas que van separadas («a veces», «en serio»), «ha» por «a» («va a llover», "
     "«vino a ver»), «a» o «e» por «ha» o «he» («ha sido», «he visto»), «echo» por «hecho», «ves» por «vez».",
     "Corregirlo."),
    ("queismo", "Falta «de» antes de «que»", _subs(
        (r" que\b", _after(QUEISMO, " de que", 2))),
     "Se dice «darse cuenta de que», «a pesar de que», «estar seguro de que», «acordarse de que»: sin el "
     "«de» es queísmo.", "Corregirlo."),
    ("dequeismo", "Sobra «de» antes de «que»", _subs(
        (r" de que\b", _after(set("pienso piensas piensa pensé pensó pensaba creo crees cree creí creyó creía opino "
                                  "supongo supuso suponía parece parecía imagino imaginé considero dije dijo dijiste "
                                  "dijeron".split()), " que"))),
     "Se dice «pienso que», «creo que», «dijo que», «parece que»: con «de» es dequeísmo.", "Corregirlo."),
    ("haber", "«Había muchos», no «habían muchos»", _subs(
        (r"\b(?i:habían|hubieron|habrán|habrían|hayan|hubiesen|hubieran)(?= (?i:muchos|muchas|varios|varias|"
         r"algunos|algunas|unos|unas|pocos|pocas|tantos|tantas|demasiados|demasiadas|dos|tres|cuatro|cinco|seis|"
         r"siete|ocho|nueve|diez|cientos|miles|decenas|docenas|más)\b)",
         lambda m: _case({"habían": "había", "hubieron": "hubo", "habrán": "habrá", "habrían": "habría",
                           "hayan": "haya", "hubiesen": "hubiese", "hubieran": "hubiera"}[m[0].lower()], m[0]),
         ("abían", "ubieron", "abrán", "abrían", "ayan", "ubiesen", "ubieran"))),
     "«Haber» con el sentido de «existir» no tiene plural: «había muchos soldados», «hubo dos ataques».",
     "Corregirlo."),
    ("contraction", "«a el» o «de el» sin contraer", _subs(
        (rf"\b(a|[Dd]e) el (?!(?:{PRONOUN_NEXT}|la|los|las|os|e|o|u|mismo|misma|solo|sólo|ya|como|cómo|cuando|"
         r"mientras|en|por|para|sin|a|de)\b)(?=[a-záéíóúñ])", lambda m: {"a": "al ", "de": "del ", "De": "Del "}[m[1]],
         ("a el ", "e el "))),
     "Delante de un nombre, «a el» y «de el» se contraen: «volvió al asiento del carro». Si «el» es el "
     "pronombre, no se contrae y lleva tilde: «a él».", "Corregirlo."),
    ("grammar", "Construcción incorrecta", _subs(
        (r"\b([Mm])ás (mejor|peor|mejores|peores)\b", lambda m: m[2].capitalize() if m[1] == "M" else m[2],
         ("ás mejor", "ás peor")),
        (r" mí[oa]\b", _after(PLACES, " de mí")), (r" tuy[oa]\b", _after(PLACES, " de ti"))),
     "«Más mejor» y «más peor» repiten la comparación (basta «mejor», «peor»); «detrás mío» es «detrás de mí».",
     "Corregirlo."),
    ("space", "Espacio de más junto a un signo", _subs((r"([¿¡«“(])\s+", _open_space), (r"\s+([»”)])", r"\1")),
     "Tras los signos de apertura (¿ ¡ « “ () y antes de los de cierre (» ” )) no va espacio.", "Quitarlo."),
    ("nospace", "Falta un espacio tras un signo", _subs(
        (r"(?<=[^\W\d_])([,;])(?=[^\W\d_]|[¿¡«])", r"\1 "),
        # "se burló.Shinichi"; "S.A." o "p.ej.", no; "…¡?" (signos revueltos), tampoco
        (r"(?<=[a-záéíóúñü]{2})([.?!…])(?=[A-ZÁÉÍÓÚÑ]|[¿¡]+[^\W\d_])", r"\1 "),
        (r"([»”)])(?<=[^\s¿¡(«“—―─–][»”)])(?=[^\W\d_]|[¿¡«])", _quote_space)),  # tras «¿» la ” abre: «¿”amiga”?»
     "Tras la coma, el punto, el cierre de pregunta o exclamación y el cierre de comillas va un espacio: sin él "
     "las palabras se leen pegadas.", "Ponerlo."),
    ("punct", "Signos de puntuación repetidos", _subs(
        (r",{2,}", ","), (r"[,;]\s*\.(?!\.)", "."), (r"([?!])\.(?!\.)", r"\1"), (r"…\.(?!\.)", "…"),
        (r"(?<![.…])(?<![.…] )\.\s+\.(?!\s*[.…])", ".", (". .", ".  .")),  # ". ." suelto; ". . ." son puntos suspensivos
        (r"([!?¡¿])\1{3,}", r"\1\1\1", ("!!!!", "????", "¡¡¡¡", "¿¿¿¿"))),
     "Dos signos donde va uno: «,,», «?.», «….», «. .». Tras los puntos suspensivos o el cierre de pregunta "
     "o exclamación no va punto, y más de tres «!» o «?» seguidos sobran.", "Corregirlo."),
    ("dialog", "Puntuación del inciso del narrador", _subs(
        # Mitsuha: «—Sí, — respondió el soldado, —un buque…» -> «—Sí —respondió el soldado—, un buque…»
        (r",\s*[—―─–]\s*([a-záéíóúñ][^—―─–.!?]*?),\s*[—―─–]\s*", r" —\1—, ", DASHES),
        (r"([^\W\d_])[.,]\s*[—―─–]\s*(?=[a-záéíóúñ])", _inciso, DASHES),
        # Making Magic (DeepSeek): «Creación!— Y aquello» -> «Creación! —Y aquello»: la narración abre con su raya
        (r"([!?…])[—―─–]\s*(?=[A-ZÁÉÍÓÚÑ¿¡])", r"\1 —", DASHES)),
     "En un diálogo, el punto o la coma no van antes del inciso del narrador sino tras su raya de cierre: "
     "«—Sí —respondió—, vamos.»", "Corregirlo."),
    ("calque", "Calco del inglés", _subs(
        (r"\b([Ee])n base a\b", lambda m: ("Con" if m[1] == "E" else "con") + " base en", ("base a",)),
        (r"\b((?i:tom(?:a|ó|aba|ará|aría|ado|ar|an|aron|aban)))(?= lugar\b)",
         lambda m: _case(TENER[m[1].lower()], m[1]), ("lugar",)),
        (r"\b((?i:hace|hizo|hacía|hará|haría|hacen|hacer))(?= sentido\b)",
         lambda m: _case(TENER[m[1].lower()], m[1]), ("sentido",)),
        (rf"\b([Ee])n orden (?:de|a) (?=(?!(?:{NOT_INFINITIVE})\b)[a-záéíóúñ]+(?:ar|er|ir)\b)",
         lambda m: ("Para" if m[1] == "E" else "para") + " ", ("orden",))),
     "Traducción literal del inglés: «based on» es «con base en», «take place» es «tener lugar», «make sense» es "
     "«tener sentido», «in order to» es «para».", "Corregirlo."),
    ("uppercase", "Mayúscula innecesaria", _subs(
        (rf"(?<=[a-záéíóúñ,] )({DAYS_MONTHS})\b", lambda m: m[1].lower()),
        (rf"(?<=[a-záéíóúñ,] )(Señor|Señora|Señorita|Don|Doña)(?= (?!(?:{TITLE_NOUNS})\b)[A-ZÁÉÍÓÚÑ][a-záéíóúñ])",
         lambda m: m[1].lower())),
     "En español los días, los meses y los tratamientos («señor», «don») van en minúscula a mitad de frase.",
     "Corregirlo."),
    ("closing", "Falta el signo de cierre", _subs(
        # «80.000», «G.I.», no; ni con una comilla o un paréntesis antes del punto: el signo falta dentro
        # («¡Esa sí que es una gran noticia,” celebró…», Mitsuha) y no sabemos dónde
        (r"([¿¡])([^¿¡?!…—―─.”»\"')]*?)([^\W\d_]*)\.(?![.…\w])", _close)),
     "La pregunta o la exclamación se abre con ¿ o ¡ pero acaba en punto: le falta su signo de cierre.",
     "Corregirlo."),
)


def _beside_icons(fix, line):
    """fix(texto) aplicado a line salvo a sus iconos: las letras del base64 de su PNG no son palabras."""
    if "icono" not in line:
        return fix(line)
    parts = ICON_TAG.split(line)
    return "".join(p if n % 2 else fix(p) for n, p in enumerate(parts))


@_memo
def _typos(line):
    """Las erratas de TYPOS de una línea: ((índice en TYPOS, línea arreglada)…). Recordadas por línea: tras un
    arreglo, el informe solo vuelve a mirar los párrafos que cambiaron."""
    found = []
    for n, (_, _, rule, _, _) in enumerate(TYPOS):
        fixed = _beside_icons(rule, line)
        # Lo que la limpieza tipográfica del EPUB ya arregla («— dijo» -> «—dijo»), no se avisa
        if fixed != line and _typography(_inline(fixed)) != _typography(_inline(line)):
            found.append((n, fixed))
    return tuple(found)


@_memo  # tras una edición casi todos los párrafos siguen igual
def _facts(text):
    """Lo que el informe mira de un párrafo sin sus vecinos: (número suelto, enlace, caracteres rotos, inglés,
    posición de la minúscula tras un punto o None, acaba en letra)."""
    words = re.findall(r"[a-záéíóúñü']+", text.lower())
    english = sum(w in ENGLISH for w in words)
    capital = next((m.span() for m in CAPITAL.finditer(text) if m[1].lower() not in ABBREVIATIONS), None)
    # «—¡3!» es un diálogo (una cuenta atrás), no un número de página
    return (bool(re.fullmatch(r"\W*\d+\W*", text)) and not re.search(r"[—―¡¿!?«»]", text),
            bool(LINKS.search(text)),
            "\ufffd" in text or bool(MOJIBAKE.search(text)),
            len(words) >= 6 and english >= 0.35 * len(words) and sum(w in SPANISH for w in words) < english,
            capital, bool(re.search(r"[^\W\d_]$", text)))


PAIRS = (("«", "»"), ("“", "”"), ("(", ")"))
# Emoticonos «:)» y enumeraciones «a)»: un «)» que no cierra nada
LONE_CLOSE = re.compile(r"[:;]-?\)|(?:^|(?<=\s))[a-z0-9]\)")
UNPAIRED_STYLE = 20  # más avisos de un mismo signo en un libro: es su estilo, no se avisan
QUOTE_LEAD = re.compile(r"\s*(?:[—―─–]\s*)?[«»“”\"]")  # párrafo que empieza con comillas: sigue una cita


@_memo
def _unpaired(text):
    """El primer signo sin pareja del párrafo: (posición, signo de apertura, True si falta el cierre), o None."""
    clean = LONE_CLOSE.sub(lambda m: " " * len(m[0]), text)
    for a, b in PAIRS:
        depth, first = 0, None
        for i, c in enumerate(clean):
            if a == "“" and c == '"':  # «“Hola"» (Maryoku, Nageki): la recta cierra la abierta o abre otra
                c = b if depth else a
            if c == a:
                first = first if depth else i
                depth += 1
            elif c == b:
                if not depth:
                    return i, a, False
                depth -= 1
        if depth:
            return first, a, True
    return None


# Palabras de 5 letras o más tan comunes que repetirlas no se nota
ECHO_SKIP = set("cuando porque entonces también estaba estaban había habían tenía tenían puede pueden quiero quería "
                "sobre desde hasta donde mientras siempre todavía mucho mucha muchos muchas nuestro nuestra algún "
                "alguna alguien nadie nada cosas parece podía después antes ahora aquí estoy estás están somos "
                "fueron siendo hacia contra otros otras tanto todos todas porqué quizás realmente simplemente".split())


@_memo
def _echo(text):
    """(inicio, fin) de la segunda de dos palabras iguales a menos de 12 palabras, o None. Solo en minúscula (los
    nombres se repiten a propósito), de 5 letras o más y no comunes; a menos de 3 es repetición buscada
    («espera, espera»)."""
    seen = {}
    for n, m in enumerate(re.finditer(r"[^\W\d_]+", text)):
        w = m[0]
        if len(w) < 5 or not w.islower() or w in ECHO_SKIP:
            continue
        if w in seen and 3 <= n - seen[w] <= 12:
            return m.span()
        seen[w] = n
    return None


REPORT_MEMOS = (_unhtml, _letters, _honorific_key, _honorific_forms, _facts, _typos, _unpaired, _echo)


def report_memos():
    """Lo que quality_report recuerda de cada párrafo. El primer informe de un libro se hace en el proceso de
    trabajo: la ventana recibe esto con él, y su primera edición no lo recalcula entero (2–3 s parada)."""
    return {f.__name__: f.cache for f in REPORT_MEMOS}


def seed_report_memos(memos):
    """Añade a la ventana lo que el proceso de trabajo recordó (report_memos)."""
    for f in REPORT_MEMOS:
        f.cache.update(memos.get(f.__name__, {}))


def _words_around(shown, start, end):
    """Amplía start..end a palabras enteras; si no abarca nada (falta algo, como un «de»), a la palabra de antes
    y la de después: un subrayado de un espacio no se ve."""
    if start == end:
        while start > 0 and shown[start - 1].isspace():
            start -= 1
    while start > 0 and not shown[start - 1].isspace():
        start -= 1
    while end < len(shown) and not shown[end].isspace():
        end += 1
    return start, end


def _issue_span(issue, blocks):
    """(inicio, fin) del problema del aviso en el texto del párrafo como lo muestra el editor (sin etiquetas, con
    «[Nota: …]»): el editor lo subraya ahí, como Word, y no el párrafo entero. None si no se sabe."""
    b = blocks[issue.block]
    shown = TAGS.sub("", ICON.sub("￼", _line(b, issue.block)))  # en la hoja, el icono es un objeto: un carácter
    shown = shown[2:] if b.kind == "heading" else shown
    if issue.kind == "icon":
        return shown.find("￼"), shown.find("￼") + 1
    text, lead = shown.strip(), len(shown) - len(shown.lstrip())
    edit = issue.edit
    if edit and edit[2] and edit[:2] == (issue.block, issue.block + 1):  # arreglo de este párrafo: lo que cambia
        after = TAGS.sub("", edit[2])
        start = len(os.path.commonprefix([shown, after]))
        tail = len(os.path.commonprefix([shown[start:][::-1], after[start:][::-1]]))
        return _words_around(shown, start, max(start, len(shown) - tail))
    if b.kind != "p" or text != _plain(b).strip():  # con notas, las posiciones de _plain no valen aquí
        return (lead, lead + len(text)) if text else None
    if issue.kind == "capital":
        start, end = _facts(text)[4]
        return _words_around(shown, lead + start, lead + end)
    if issue.kind == "broken":
        m = MOJIBAKE.search(text) or re.search("�", text)
        return _words_around(shown, lead + m.start(), lead + m.end()) if m else None
    if issue.kind == "unpaired" and _unpaired(text):
        pos = _unpaired(text)[0]
        return lead + pos, lead + pos + 1
    if issue.kind == "echo" and _echo(text):
        return tuple(lead + p for p in _echo(text))
    if issue.kind == "lower":
        return lead, lead + len(text.split()[0])
    if issue.kind == "end":
        return lead + len(text) - len(text.split()[-1]), lead + len(text)
    return lead, lead + len(text)


def quality_report(book, echoes=False, glossary=(), lang="es"):
    """Lo sospechoso del libro, para revisarlo: [Issue], por página. Cada aviso dice el problema, la
    solución y, si se puede arreglar solo, el cambio para edit_section y su vista previa. Con echoes, también
    las palabras repetidas cerca (muchos avisos de estilo); glossary, los nombres de la serie como deben ir.
    lang: idioma del libro; las erratas, calcos y ecos son del español, y «sin traducir» busca inglés."""
    spanish, in_english = lang.lower().startswith("es"), lang.lower().startswith("en")
    blocks = book.blocks
    unpaired_signs = {}  # bloque -> signo de su aviso «Signo sin pareja»
    report = [Issue("skipped", None, page, "Grave", f"Texto no incluido ({letters} letras)",
                    f"La página {page} tiene texto que no entró en el libro: {why}.",
                    "Mira la página en la vista previa del PDF. Si era texto de la novela, cópialo en su "
                    "capítulo con «Editar texto…» (tecla E). Si era un índice o créditos, ignora el aviso.")
              for page, letters, why in book.skipped
              if letters >= 20 and not (why == TOC_SKIPPED and book.toc)]  # índice leído: lo compara la ventana
    # Los recuadros («HP: 10/10») se repiten por la historia: no son encabezados del PDF
    lines = collections.Counter(_plain(b).strip() for b in blocks if b.kind == "p" and b.align != "frame")
    heads = [k for k, b in enumerate(blocks) if b.kind == "heading"] + [len(blocks)]
    drop = (lambda k: (k, k + 1, ""))
    honorifics = honorific_fixes([_plain(b) for b in blocks if b.kind == "p"])
    honorific = re.compile(r"(?<![\w-])(" + "|".join(sorted(map(re.escape, honorifics), key=len, reverse=True))
                           + r")(?![\w-])", re.I) if honorifics else None
    named = glossary_fixes(book, glossary) if glossary else {}
    names = re.compile(r"(?<![\w-])(" + "|".join(sorted(map(re.escape, named), key=len, reverse=True))
                       + r")(?![\w-])") if named else None
    prev_text = None
    for k, b in enumerate(blocks):
        if b.kind != "p":
            prev_text = None if b.kind == "heading" else prev_text
            continue
        text = _plain(b).strip()
        number, link, broken, english, capital, letter_end = _facts(text)
        english = english and not in_english
        if text[:1].islower():
            # El párrafo anterior, saltando ilustraciones y separadores (la página se cortó con una imagen)
            j = next((j for j in range(k - 1, -1, -1) if blocks[j].kind in ("p", "heading")), None)
            head = _plain(blocks[j]).strip() if j is not None and blocks[j].kind == "p" else ""
            before = _cut(head, tail=True) + " ¶\n" + _cut(text) if head else _cut(text)
            problem = ("Empieza en minúscula: suele ser el final de la frase del párrafo anterior, cortada por "
                       "un cambio de página o una ilustración.")
            if head and not head.endswith(END_PUNCT):
                between = [_line(blocks[i], i) for i in range(j + 1, k)]  # se quedan tras el párrafo unido
                # el segundo pierde su <center>/<marco>: si no, quedaría escrito en medio del párrafo
                tail = _line(replace(b, align=""), k).lstrip()
                edit = (j, k + 1, "\n\n".join([_line(blocks[j], j).rstrip() + " " + tail] + between))
                report.append(Issue("lower", k, b.page, "Revisar", "Párrafo partido", problem,
                                    "Unirlo al párrafo anterior.", before,
                                    _cut(head, tail=True) + " " + _cut(text), edit, text))
            else:  # Maryoku 9: el anterior ya acaba la frase; unirlos a ciegas mezclaría texto desordenado
                report.append(Issue("lower", k, b.page, "Revisar", "Párrafo partido", problem + (
                                    " Pero el párrafo anterior ya termina la frase: puede faltar texto o estar "
                                    "desordenado." if head else " Pero va justo tras un título."),
                                    "Compáralo con la página del PDF y corrígelo con «Editar texto…» (tecla E): "
                                    "busca de dónde viene el principio de la frase.", before, text=text))
        if len(text) > 3000:
            report.append(Issue("long", k, b.page, "Revisar", f"Párrafo muy largo ({len(text)} caracteres)",
                                "Un párrafo tan largo suele ser varios unidos: el PDF no marcaba bien dónde "
                                "empieza cada uno.",
                                "Ábrelo con «Editar texto…» (tecla E) y deja una línea en blanco donde empiece "
                                "cada párrafo.", _cut(text, 400), text=text))
        if number:
            report.append(Issue("number", k, b.page, "Revisar", "Número suelto",
                                "Un párrafo que solo es un número suele ser el número de página del PDF.",
                                "Quitarlo.", text, "", drop(k), text))
        elif len(text) < 60 and lines[text] >= 3 and text[:1].isalnum() and not text.endswith(END_PUNCT):
            # "Einherjar Project" sí; un diálogo repetido ("¿Eh?", "Ya veo.") empieza con signo o acaba en punto
            report.append(Issue("repeated", k, b.page, "Revisar", f"Línea repetida {lines[text]} veces",
                                "Una línea corta que se repite por todo el libro suele ser el encabezado o el "
                                "pie de página del PDF.", "Quitarla (con «Arreglar todos» se quitan todas).",
                                text, "", drop(k), text))
        elif text == prev_text and len(text) >= 20:
            report.append(Issue("duplicate", k, b.page, "Revisar", "Párrafo duplicado",
                                "Es igual que el párrafo anterior: el PDF lo repetía o se pegó dos veces.",
                                "Quitar la copia.", _cut(text), "", drop(k), text))
        if link:
            short = len(text) < 200  # un aviso del grupo; uno largo puede ser texto de la novela con un nombre
            report.append(Issue("link", k, b.page, "Menor", "Enlace o red social",
                                "Enlaces y redes del grupo de traducción: en un lector no se pueden abrir.",
                                "Quitarlo." if short else "Quita el enlace con «Editar texto…» (tecla E).",
                                _cut(text), "", drop(k) if short else None, text))
        if broken:
            m = MOJIBAKE.search(text) or re.search("�", text)
            fixed = _remojibake(_line(b, k)) if MOJIBAKE.search(text) else None
            report.append(Issue("broken", k, b.page, "Grave", "Caracteres rotos",
                                "Hay letras que el PDF guardó mal (tildes o eñes cambiadas por símbolos).",
                                "Corregirlas." if fixed else "Corrígelas con «Editar texto…» (tecla E) o con "
                                "«Reemplazos…» si se repiten en todo el libro.",
                                _around(text, m.start(), m.end()),
                                _around(_remojibake(text) or "", m.start(), m.end()) if fixed else "",
                                (k, k + 1, fixed) if fixed else None, text))
        if english:
            report.append(Issue("english", k, b.page, "Revisar", "Texto sin traducir",
                                "El párrafo parece estar en inglés: el traductor se lo saltó o es una nota del "
                                "grupo.", "Tradúcelo o quítalo con «Editar texto…» (tecla E).", _cut(text),
                                text=text))
        nxt = _plain(blocks[k + 1]).strip() if k + 1 < len(blocks) and blocks[k + 1].kind == "p" else ""
        unpaired = _unpaired(text) if not link else None
        if unpaired:
            sign, open_ = unpaired[1:]
            twin = _unpaired(nxt if open_ else prev_text or "")
            # Cita o diálogo de varios párrafos: los siguientes empiezan con comillas y solo el último cierra
            # (Maryoku, Kage); o el PDF partió el párrafo y el signo que falta está en el de al lado
            if sign != "(" and QUOTE_LEAD.match(nxt if open_ else text) or twin and twin[1:] == (sign, not open_):
                unpaired = None
        if unpaired:
            pos = unpaired[0]
            unpaired_signs[k] = unpaired[1]
            report.append(Issue("unpaired", k, b.page, "Revisar", "Signo sin pareja",
                                "Se abren comillas o un paréntesis que no se cierran, o se cierran sin abrirse: "
                                "falta un signo o sobra.",
                                "Compáralo con el PDF y corrígelo con «Editar texto…» (tecla E).",
                                _around(text, pos, pos + 1), text=text))
        echo = _echo(text) if echoes and spanish else None
        if echo:
            report.append(Issue("echo", k, b.page, "Menor", "Palabra repetida cerca",
                                f"«{text[echo[0]:echo[1]]}» sale dos veces en pocas palabras: suena repetido.",
                                "Cambia una por un sinónimo o reescribe la frase con «Editar texto…» (tecla E).",
                                _around(text, *echo), text=text))
        line = _line(b, k)
        if ICON.search(line):
            report.append(Issue("icon", k, b.page, "Menor", "Icono sin reconocer",
                                "El PDF dibuja aquí un símbolo como imagen y no se parece a ningún carácter conocido: "
                                "va en el libro como una imagen pequeña dentro del texto.",
                                "Si es un símbolo de texto (♡, ★, ♪…), bórralo y escribe el carácter con «Editar "
                                "texto…» (tecla E), en «Insertar › Símbolos». Si se ve bien, ignora el aviso.",
                                _cut(_unhtml(ICON.sub("[icono]", b.html)).strip()), text=text))
        for t, fixed in _typos(line) if spanish and not link else ():
            kind, title, _, why, solution = TYPOS[t]
            before, after = TAGS.sub("", line), TAGS.sub("", fixed)
            n = len(os.path.commonprefix([before, after]))
            report.append(Issue(kind, k, b.page, "Menor", title, why, solution,
                                _around(before, n, n), _around(after, n, n + len(after) - len(before)),
                                (k, k + 1, fixed), text))
        bare = ICON.sub("￼", line)  # honoríficos y nombres, no en las letras del PNG de un icono
        m = honorific.search(bare) if honorific else None
        if m:
            fixed = _beside_icons(lambda s: honorific.sub(lambda h: (str.upper if h[0][:1].isupper() else str.lower)(
                honorifics[h[0].lower()][0][:1]) + honorifics[h[0].lower()][0][1:], s), line)
            best, n_best, n_here = honorifics[m[0].lower()]
            before, after = TAGS.sub("", line), TAGS.sub("", fixed)
            n = len(os.path.commonprefix([before, after]))
            report.append(Issue("honorific", k, b.page, "Menor", "Honorífico escrito de varias formas",
                                f"El libro escribe «{best}» {counted(n_best, 'vez', 'veces')} y «{m[0]}» "
                                f"{counted(n_here, 'vez', 'veces')}: mejor una sola forma.",
                                f"Unificarlo en «{best}».", _around(before, n, n),
                                _around(after, n, n + len(after) - len(before)), (k, k + 1, fixed), text))
        m = names.search(bare) if names else None
        if m:
            fixed = _beside_icons(lambda s: names.sub(lambda h: named[h[0]], s), line)
            before, after = TAGS.sub("", line), TAGS.sub("", fixed)
            n = len(os.path.commonprefix([before, after]))
            report.append(Issue("glossary", k, b.page, "Menor", "Nombre distinto al glosario",
                                f"El glosario de la serie escribe «{named[m[0]]}»; aquí sale «{m[0]}».",
                                f"Cambiarlo por «{named[m[0]]}». Si las dos formas valen, añade esta al glosario.",
                                _around(before, n, n), _around(after, n, n + len(after) - len(before)),
                                (k, k + 1, fixed), text))
        # La nota al pie va dentro del párrafo: su punto final no corta la frase (Psychology of Harem 2)
        flow = _unhtml(NOTE.sub(" ", b.html)).strip() if capital and NOTE.search(b.html) else text
        capital = _facts(flow)[4] if flow is not text else capital
        if capital and not link:
            report.append(Issue("capital", k, b.page, "Menor", "Minúscula tras un punto",
                                "Tras un punto la frase nueva empieza en mayúscula. Si la frase sigue, el punto "
                                "sobra: suele quedar de un salto de línea del PDF.",
                                "Compáralo con el PDF y corrígelo con «Editar texto…» (tecla E): quita el punto o "
                                "pon la mayúscula.", _around(flow, *capital), text=text))
        if (len(text) >= 40 and letter_end and nxt[:1].isupper() and not link
                and not LABEL.match(text.strip()) and not LABEL.match(nxt)):
            report.append(Issue("end", k, b.page, "Menor", "Falta el punto final",
                                "El párrafo acaba sin punto y el siguiente empieza en mayúscula: falta el punto, "
                                "o el párrafo está partido y el siguiente sigue la frase.",
                                "Compáralo con el PDF y corrígelo con «Editar texto…» (tecla E): pon el punto o "
                                "une los dos párrafos.", _cut(text, tail=True) + " ¶\n" + _cut(nxt), text=text))
        prev_text = text
    # Un signo sin pareja en todo el libro es el estilo del grupo (Maryoku no cierra las “ del diálogo), no una
    # errata: decenas de avisos que nadie va a revisar uno a uno
    many = {s for s, n in collections.Counter(unpaired_signs.values()).items() if n > UNPAIRED_STYLE}
    report = [i for i in report if not (i.kind == "unpaired" and unpaired_signs[i.block] in many)]
    last = None
    for k, end in zip(heads, heads[1:]):
        if k not in book.candidates:
            continue
        title = _plain(blocks[k]).strip()
        nxt = next((j for j in heads if j > k and j in book.candidates), len(blocks))
        letters = sum(_letters(_plain(b)) for b in blocks[k + 1:nxt] if b.kind == "p")
        if letters < 300 and not PART_TITLE.match(title):
            report.append(Issue("short", k, blocks[k].page, "Revisar", f"Capítulo muy corto ({counted(letters, 'letra')})",
                                f"«{title}» casi no tiene texto: puede ser un título mal detectado (una cita, "
                                "un cartel) o un capítulo cuyo texto quedó en otro.",
                                "Si no es un capítulo, cámbialo a Subtítulo (tecla S) o quítalo (tecla Q) en la "
                                "lista.", _cut(title + "\n" + " ".join(_plain(b) for b in blocks[k + 1:nxt]
                                                                      if b.kind == "p")), text=title))
        m = re.match(r"Cap[ií]tulo (\d+)\b", numbered(title), re.I)
        if m:
            n = int(m[1])
            if last is not None and n > last + 1:  # menor o igual: otra parte que vuelve a empezar
                missing = str(last + 1) if n == last + 2 else f"{last + 1} a {n - 1}"
                report.append(Issue("gap", k, blocks[k].page, "Grave", f"Falta el capítulo {missing}",
                                    f"Tras el capítulo {last} viene «{title}».",
                                    "Busca su título en el PDF (Ctrl+F) y márcalo como Capítulo (tecla C). Si "
                                    "el PDF tampoco lo tiene, ignora el aviso.", title, text=title))
            last = n
    for issue in report:
        if issue.block is not None:
            issue.span = _issue_span(issue, blocks)
    return sorted(report, key=lambda r: (r.page, r.block if r.block is not None else -1))


SCORE_WEIGHTS = {"Grave": 10, "Revisar": 3, "Menor": 1}


def quality_score(report, words):
    """Nota del libro de 0 a 100: cada aviso resta según su gravedad, por cada 10 000 palabras, para que un libro
    largo y uno corto se comparen igual."""
    if not words:
        return 100
    penalty = sum(SCORE_WEIGHTS.get(i.level, 0) for i in report) * 10000 / words
    return max(0, min(100, round(100 - penalty)))


def export_report(report, path, title, score):
    """Guarda el informe para otra persona: .csv (para Excel en español: «;» y UTF-8 con BOM) o una página .html
    que se lee sin la app."""
    rows = [(i.level, str(i.page), i.title, i.problem, i.solution, i.before) for i in report]
    head = ("Gravedad", "Página", "Aviso", "Problema", "Solución", "Texto")
    if str(path).lower().endswith(".csv"):
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            csv.writer(f, delimiter=";").writerows([head, *rows])
        return
    counts = collections.Counter(i.level for i in report)
    summary = " · ".join(f"{counts[level]} {level.lower()}" for level in LEVELS if counts[level]) or "Sin avisos"
    color = {"Grave": "#c62828", "Revisar": "#b26a00", "Menor": "#1f5fb4"}
    cells = "\n".join(f'<tr><td class="lvl" style="color:{color.get(level, "#333")}">{html.escape(level)}</td>'
                      + "".join(f"<td>{html.escape(c)}</td>" for c in rest) + "</tr>" for level, *rest in rows)
    page = f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><title>Informe de calidad · {html.escape(title)}</title>
<style>
body {{ font: 15px/1.45 "Segoe UI", sans-serif; margin: 24px; color: #222; background: #fff; }}
h1 {{ font-size: 22px; margin: 0 0 4px; }} .muted {{ color: #666; margin: 0 0 16px; }}
.score {{ font-size: 18px; font-weight: 600; }}
table {{ border-collapse: collapse; width: 100%; }} th, td {{ border-bottom: 1px solid #ddd; padding: 6px 8px;
text-align: left; vertical-align: top; }} th {{ background: #f3f3f3; }} .lvl {{ font-weight: 600; white-space: nowrap; }}
</style></head><body>
<h1>Informe de calidad · {html.escape(title)}</h1>
<p class="muted"><span class="score">Nota {score}/100</span> · {html.escape(summary)}</p>
<table><tr>{"".join(f"<th>{h}</th>" for h in head)}</tr>
{cells}
</table></body></html>
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)


def combine(parts, title):
    """Une varios tomos en un recopilatorio. parts: [(book, chapters, removed, breaks)] ya revisados.
    Cada tomo empieza con un capítulo con su título y su portada. Devuelve (book, chapters, removed, breaks)."""
    blocks, chapters, removed, breaks, candidates = [], [], [], [], []
    for book, part_chapters, part_removed, part_breaks in parts:
        offset = len(blocks) + 2  # título del tomo y su portada
        chapters.append((len(blocks), book.title))
        candidates.append(len(blocks))
        # sin portada, un bloque vacío que no se escribe: así todos los tomos se desplazan igual
        blocks += [Block("heading", 1, html.escape(book.title)), Block("image", 1, image=book.cover, keep=bool(book.cover))]
        blocks += book.blocks
        chapters += [(k + offset, t) for k, t in part_chapters]
        candidates += [k + offset for k in book.candidates]
        removed += [k + offset for k in part_removed]
        breaks += [k + offset for k in part_breaks]
    first = parts[0][0]
    merged = replace(first, title=title, blocks=blocks, candidates=candidates, volume="", skipped=[], toc=[])
    return merged, chapters, removed, breaks


FONTS = {"Literata": "literata", "Bitter": "bitter"}  # tipografías libres (OFL) en la carpeta fonts
FONT_DIR = Path(__file__).parent / "fonts"  # en el .exe, junto a los módulos (--add-data fonts;fonts)


def open_pdf(path):
    """Abre el PDF (o EPUB) para leerlo o dibujarlo. Una imagen JPEG 2000 que trae su propia paleta y el PDF
    marca /Indexed salía como ruido al dibujarla más pequeña que su tamaño (Eminence 7, las ilustraciones):
    MuPDF la reduce promediando los índices de la paleta como si fueran colores. Sin el /ColorSpace del PDF
    usa los colores del propio JPEG 2000. Solo en memoria: el archivo no cambia."""
    doc = pymupdf.open(path)
    if doc.is_pdf and not doc.needs_pass:
        for xref in range(1, doc.xref_length()):
            try:
                if doc.xref_get_key(xref, "Filter")[1] != "/JPXDecode":
                    continue
                kind, value = doc.xref_get_key(xref, "ColorSpace")
                if kind == "xref":
                    value = doc.xref_object(int(value.split()[0]), compressed=True)
                if "/Indexed" in value and b"pclr" in doc.xref_stream_raw(xref)[:4096]:  # la paleta va en la cabecera
                    doc.xref_set_key(xref, "ColorSpace", "null")
            except Exception:  # Re Zero 3: una entrada de la tabla sin objeto detrás; el libro abre igual
                continue
    return doc


def analyze(pdf_path, progress=None):
    """Lee el PDF (o un EPUB, que PyMuPDF compone en páginas) y lo devuelve como bloques, con los títulos de
    capítulo candidatos."""
    kind = "EPUB" if str(pdf_path).lower().endswith(".epub") else "PDF"
    try:
        doc = open_pdf(pdf_path)
    except Exception as e:
        raise ValueError(f"No se pudo abrir el {kind}: {e}") from e
    with doc:
        if doc.needs_pass:
            raise ValueError(f"El {kind} está protegido con contraseña.")
        n = doc.page_count
        pages = []
        for i, page in enumerate(doc):
            pages.append(_rows(page))
            if progress:
                progress(i + 1, 2 * n)
        pages = _strip_margins(pages, [page.rect.height for page in doc])
        if not any(pages):
            raise ValueError(f"El {kind} no tiene texto (parece escaneado). Hace falta OCR, que no está soportado.")

        sizes = collections.Counter()  # tamaño de letra -> caracteres
        for rows in pages:
            for r in rows:
                sizes[r.size] += len(r.text)
        body = sizes.most_common(1)[0][0]
        gaps = collections.Counter()
        indents = total = 0
        for rows in pages:
            normal = [r for r in rows if r.size == body]
            gaps.update(round(b.y - a.y) for a, b in zip(normal, normal[1:]) if 0 < b.y - a.y < body * 3)
            if normal:
                left = min(r.x0 for r in normal)
                total += len(normal)
                indents += sum(r.x0 > left + body * 0.8 for r in normal)
        line_gap = gaps.most_common(1)[0][0] if gaps else body * 1.2
        indent_style = indents > total * 0.05  # los párrafos se marcan con sangría
        # Hueco que separa escenas (Mitsuha GambaTrans: 3,3 líneas): bastante más que el espacio entre párrafos, y
        # raro. Si sale en casi cada página es el estilo del libro (Megami 4: una línea en blanco entre párrafos)
        spaced = collections.Counter()
        for rows in pages:
            normal = [r for r in rows if r.size == body]
            spaced.update(round(b.y - a.y) for a, b in zip(normal, normal[1:]) if b.y - a.y > line_gap * 1.15)
        paragraph_gap = max((g for g in spaced if g < 3 * line_gap), key=spaced.get, default=line_gap)
        scene_gap = max(SCENE_GAP * line_gap, 1.8 * paragraph_gap)
        if sum(count for gap, count in spaced.items() if gap > scene_gap) > 2 * n:  # Megami 4: 4 por página
            scene_gap = None

        # Título por tamaño o negrita, o porque el PDF lo marca: hay subtítulos del tamaño del texto
        # ("El lado de la bruja") que solo se distinguen por su marcador de nivel 2.
        bookmarks = collections.defaultdict(set)
        for _, title, p in doc.get_toc():
            if 1 <= p <= n and _key(title):
                bookmarks[p - 1].add(_key(title))
        for i, rows in enumerate(pages):
            normal = [r for r in rows if abs(r.size - body) < 0.5]
            left, right = min((r.x0 for r in normal), default=0), max((r.x1 for r in normal), default=0)
            for r in rows:
                key = _key(r.text)
                # The World's Strongest Witch: «Obtained Title: …» va en negrita algo más grande, pero con la sangría
                # del párrafo y sin centrar: es texto destacado. Los subtítulos van al margen o centrados
                emphasis = (normal and r.size < body * 1.3 and not KEYWORDS.match(r.text) and r.x0 > left + body * 0.8
                            and abs((r.x0 + r.x1) / 2 - (left + right) / 2) > (right - left) * 0.05)
                r.heading = (_is_heading(r, body) and not emphasis or any(
                    key == mark or (r.bold and len(key) >= 4 and key in mark) for mark in bookmarks[i])
                    or (r.bold and bool(BARE_PART.fullmatch(r.text.strip()))))  # apartado «2» (is_subpart)
                r.starts = len(key) >= 4 and any(mark.startswith(key) for mark in bookmarks[i])

        uses = collections.Counter(img[0] for page in doc for img in page.get_images())  # xref -> páginas
        # Primera página con un capítulo: lo anterior son preliminares (portadilla, créditos, logos)
        first_chapter = next((i for i, rows in enumerate(pages) if rows and not _is_toc(rows) and any(
            r.heading and KEYWORDS.match(r.text.strip(LABEL_DECOR)) for r in rows)), 0)  # sin capítulos: nada
        blocks, skipped = [], []
        para, short, last_text = None, False, ""
        toc = False  # la página anterior era el índice
        contents = []  # títulos del índice impreso
        repeated = ""  # título que la página anterior ya emitió junto a su ilustración
        follow = ""  # título de la portadilla anterior: la página siguiente lo repite antes del texto
        frames = [_frames(page) for page in doc]
        repeats = collections.Counter(tuple(round(v) for v in b) for page_frames in frames for b in page_frames)
        frames = [[b for b in page_frames if repeats[tuple(round(v) for v in b)] <= max(3, n // 5)]
                  for page_frames in frames]
        digests, last_boxes = {}, []
        for i, (page, rows) in enumerate(zip(doc, pages)):
            num = i + 1
            if progress:
                progress(n + num, 2 * n)
            pending, repeated = repeated, ""
            # Eminence 7: tras la portadilla, la página repite etiqueta (pequeña) y nombre encima del texto
            if follow and rows and _key(rows[0].text) and _key(rows[0].text) in follow:
                lead = next((j for j, r in enumerate(rows) if abs(r.size - body) < 0.5), len(rows))
                lost = sum(_letters(r.text) for r in rows[:lead] if _key(r.text) not in follow)  # lo ya dicho no avisa
                if lost:
                    skipped.append((num, lost, "título repetido tras su portadilla, omitido"))
                rows = rows[lead:]
            follow = ""
            # Eminence 7: portadilla con el nombre arriba, «Chapter 1» enorme abajo y la marca del libro en letra
            # diminuta. La marca se quita y la etiqueta se une al nombre: eran tres capítulos con la repetición
            if any(r.heading for r in rows) and all(r.heading or r.size < body * 0.6 for r in rows):
                rows = [r for r in rows if r.heading]
                label = _label(rows[-1])
                if len(rows) >= 2 and label and CHAPTER_LABEL.fullmatch(label) and not _label(rows[0]):
                    group = _first_group(rows[:-1]) + rows[-1:]
                    text = f"{label}: {' '.join(r.text for r in group[:-1])}"
                    rows = [replace(rows[-1], text=text, html=html.escape(text), box=_rect(group),
                                    x0=min(r.x0 for r in group), x1=max(r.x1 for r in group))]
                    follow = _key(text)
            headings = [r for r in rows if r.heading]
            infos = _image_info(page, digests)
            drawn = _drawn(page)
            before, last_boxes = last_boxes, [info["bbox"] for info in infos + drawn]  # las de la anterior: cabeceras
            images = _images(page, infos)
            # ponytail: solo ilustraciones a página completa; las pequeñas dentro del texto se descartan
            covered = sum(len(r.text) for r in rows) < 200 and _image_coverage(page, infos) > 0.5
            if covered and not headings:
                blocks.append(Block("image", num, image=_render(page, crop=True)))
                para = None
                continue
            # Portada de capítulo: la ilustración tapa el título (texto oculto) y la página siguiente lo repite.
            # Se emite el título y la ilustración; sin repetición, la imagen es un fondo decorativo y se descarta.
            if covered and len(headings) == len(rows) and i + 1 < n and _title(rows) == _title(pages[i + 1]):
                group = _first_group(rows)
                blocks.append(Block("heading", num, html.escape(" ".join(r.text for r in group)), rect=_rect(group)))
                blocks.append(Block("image", num, image=_render(page, crop=True)))
                para, repeated = None, _title(rows)
                continue
            if not rows:
                art = pymupdf.Rect()
                for info in infos:
                    if info["xref"] == 0 or uses[info["xref"]] <= 2:  # las que se repiten son fondo o adorno
                        art |= pymupdf.Rect(info["bbox"]) & page.rect
                if abs(art) >= 0.15 * abs(page.rect):  # Witch 1: la portadilla con el logo ocupa el 45 %
                    blocks.append(Block("image", num, image=_jpeg(page.get_pixmap(dpi=150, clip=art), 85)))
                    para = None
                continue
            toc = _is_toc(rows) or (toc and _links_only(page, rows))
            if toc:  # se apunta: si no era el índice, se habría perdido una página de texto
                skipped.append((num, sum(map(_letters, (r.text for r in rows))), TOC_SKIPPED))
                contents += _toc_entries(rows)
                continue
            # Página de diseño de los preliminares (portadilla con logo y créditos): poco texto y alguna
            # imagen propia. Se conserva entera, como imagen, para no perder su composición.
            if i < first_chapter and sum(len(r.text) for r in rows) < 200 and any(
                    share >= 0.02 and uses[xref] <= 2 for xref, share in images):
                blocks.append(Block("image", num, image=_render(page)))
                para = None
                continue
            heading_only = len(headings) == len(rows)
            if heading_only:
                kept = _first_group(rows)
                dropped = sum(_letters(r.text) for r in rows[len(kept):])
                if dropped:
                    skipped.append((num, dropped, "títulos repetidos de una página de título, omitidos"))
                rows = kept
            rows = _join_labels(rows)
            rows, notes = _footnotes(rows, body, page.rect.height)
            start = len(blocks)
            inside = {id(r): f for r in rows for f in frames[i]
                      if pymupdf.Point((r.x0 + r.x1) / 2, r.y - r.size * 0.35) in f}
            # Márgenes de cada recuadro: dentro, la sangría y la línea corta se miden respecto a él
            edges = {}
            for r in rows:
                if id(r) in inside and not r.heading:
                    key = id(inside[id(r)])
                    lo, hi = edges.get(key, (r.x0, r.x1))
                    edges[key] = (min(lo, r.x0), max(hi, r.x1))
            normal = [r for r in rows if not r.heading]
            # Ilustración con el texto de la novela encima: se guardan las dos cosas, la imagen tras el texto
            # de la página (es la escena que se lee ahí, Maryoku 5). El párrafo abierto sigue abierto, así que
            # su continuación se une a él aunque la imagen quede en medio.
            art = _illustration(images, uses) if normal else None
            left = min((r.x0 for r in normal), default=0)
            right = max((r.x1 for r in normal), default=0)
            prev = None
            ornaments = _ornaments(page, rows, infos + drawn, before)  # adornos-separador, de arriba abajo
            boxes = [info["bbox"] for info in infos + drawn]  # imágenes, recuadros y adornos: no son un hueco
            for r in rows:
                while ornaments and ornaments[0][0] < r.y:  # el adorno queda por encima de esta línea
                    info = ornaments.pop(0)[1]
                    if blocks and blocks[-1].kind != "break":  # guarda el adorno, por si se quiere el original
                        blocks.append(Block("break", num, image=_ornament_image(doc, page, info)))
                    para = None
                # Mitsuha (GambaTrans): la escena cambia con un hueco de unas tres líneas en blanco, sin símbolo ni
                # adorno. Solo entre dos líneas del texto, sin nada en medio (ilustración, recuadro, título); no
                # antes de una firma centrada o a la derecha (el autor al final del epílogo, Eminence 7) ni alrededor
                # de una ficha de estado («NOMBRE: Chise», Maryoku)
                if (prev is not None and scene_gap and r.y - prev.y > scene_gap and not prev.heading and not r.heading
                        and abs(prev.size - body) < 0.5 and abs(r.size - body) < 0.5
                        and not prev.bold  # Maryoku 1: subtítulo en negrita del tamaño del texto; es su aire
                        and id(prev) not in inside and id(r) not in inside
                        and not any(prev.y < (box[1] + box[3]) / 2 < r.y for box in boxes)
                        and blocks and blocks[-1].kind == "p"
                        and max(prev.x0, r.x0) < left + (right - left) / 4  # la sangría de Re Zero 7 es de 35 pt
                        and not LABEL.match(r.text.strip()) and not LABEL.match(_plain(blocks[-1]))
                        and not r.text.rstrip().endswith(":")):  # «Protector de la Tierra:» presenta una ficha
                    blocks.append(Block("break", num))  # sin imagen ni texto: en el EPUB, el del estilo
                    para = None
                if SCENE_BREAK.fullmatch(r.text):  # solo símbolos: separador aunque vaya en letra de título (Mitsuha 6)
                    blocks.append(Block("break", num, r.html))  # guarda el texto original ("☆", "* * *")
                    para = None
                elif r.heading:
                    # Se une a la línea anterior si es el mismo título partido: mismo tamaño y pegada
                    # (un subtítulo justo debajo del capítulo queda más lejos, o lo abre un marcador).
                    if (prev is not None and prev.heading and abs(prev.size - r.size) < 0.5
                            and r.y - prev.y < 2 * r.size and not r.starts):
                        blocks[-1].html += " " + html.escape(r.text)
                        blocks[-1].rect = _rect([blocks[-1], r])  # el rectángulo abarca todas sus líneas
                    else:
                        # Psychology of Harem 2: «◆Capítulo 1: El Discurso»; el rombo es adorno, no título
                        blocks.append(Block("heading", num, html.escape(r.text.strip(HEADING_DECOR) or r.text),
                                            rect=_rect([r])))
                    para = None
                else:
                    framed = id(r) in inside
                    lo, hi = edges[id(inside[id(r)])] if framed else (left, right)
                    indented = r.x0 > lo + body * 0.8
                    # Línea centrada (carta, cartel, aviso): lejos de los dos márgenes y con el centro en el
                    # del texto. La última línea corta de un párrafo empieza en el margen: no cuenta.
                    width = hi - lo
                    centered = not framed and (width > 0 and r.x0 > lo + width * 0.1 and r.x1 < hi - width * 0.1
                                               and abs((r.x0 + r.x1) / 2 - (lo + hi) / 2) < width * 0.05)
                    # Entrar o salir de un recuadro siempre abre párrafo: el de dentro no se une al de fuera
                    switch = para is not None and (para.align == "frame") != framed
                    # Una viñeta abre línea solo en un recuadro: en la prosa, un ♡ al principio de línea sigue la frase
                    if para is None or indented or short or centered or switch or (framed and r.text[:1] in BULLETS):
                        new = True
                    elif prev is None:  # primera línea de la página: ¿sigue el párrafo anterior?
                        # Witch 1: el Afterword (título solo en imagen) empieza sin sangría en una página que el
                        # índice marca; unido, quedaba al final del capítulo anterior
                        new = last_text.endswith(END_PUNCT) and (not indent_style or bool(bookmarks.get(num - 1)))
                    else:
                        new = r.y - prev.y > line_gap * 1.3
                    # Una minúscula sigue la frase; también tras «¡Gran escritor!"» (Psychology of Harem 2, pág. 300)
                    if (new and para is not None and not switch and r.text[:1].islower()
                            and (not last_text.endswith(END_PUNCT) or re.search(r"[!?][\"”»’)]*\s*$", last_text))):
                        new = False  # algunos PDF ponen con sangría el final de una frase sin acabar
                    if new:
                        para = Block("p", num, r.html, align="frame" if framed else "center" if centered else "",
                                     rect=_rect([r]))
                        blocks.append(para)
                    else:
                        para.html = _join(para.html, r.html)
                        if para.page == num:  # el recuadro, solo en la página donde empieza el párrafo
                            para.rect = _rect([para, r])
                    short = r.x1 < hi - (hi - lo) * 0.2
                    last_text = r.text
                prev = r
            if ornaments and blocks and blocks[-1].kind != "break":  # adorno al pie: la escena sigue en la siguiente
                blocks.append(Block("break", num, image=_ornament_image(doc, page, ornaments[0][1])))
                para = None
            # Desde el párrafo que venía abierto de la página anterior: también puede llevar llamadas de esta
            _attach_notes(blocks[max(start - 1, 0):], notes)
            if art:
                blocks.append(Block("image", num, image=_extract(doc, art)))
            # Imágenes que no entran solas, apuntadas como descartadas para poder recuperarlas en la
            # ventana: las de dentro del texto y el fondo de una página de título. Los fondos y barras
            # que se repiten en muchas páginas y las diminutas no: serían ruido.
            for xref, share in images:
                background = heading_only and covered and xref == images[0][0]
                if xref != art and share >= 0.02 and (uses[xref] <= 2 or background):
                    blocks.append(Block("image", num, image=_extract(doc, xref), keep=False))
            if pending:  # quitar el título repetido: ya salió con la ilustración de la página anterior
                k = next((k for k in range(start, len(blocks)) if blocks[k].kind == "heading"), None)
                if k is not None and _key(html.unescape(blocks[k].html)) == pending:
                    del blocks[k]

        if not any(b.kind in ("p", "heading") for b in blocks):
            raise ValueError("No se encontró texto de la novela en el PDF (¿solo tiene el índice?).")

        if blocks and blocks[0].kind == "image" and blocks[0].page == 1:
            cover = blocks.pop(0).image
        else:
            cover = _render(doc[0], crop=True)

        # un marcador sin destino (página -1) haría un capítulo falso al principio
        outline = [(title, p) for level, title, p in doc.get_toc() if level == 1 and 1 <= p <= n]
        if outline:
            chosen, inserted = set(), []
            for title, p in outline:
                k = next((k for k, b in enumerate(blocks) if b.kind == "heading" and b.page == p), None)
                if k is None:
                    k = next((k for k, b in enumerate(blocks) if b.page >= p), len(blocks))
                    blocks.insert(k, Block("heading", p, html.escape(title)))
                    inserted.append(blocks[k])
                chosen.add(id(blocks[k]))
            # Witch 1: «Cover» (la portada va aparte) y «Table of Contents» (el índice no se copia) no tienen nada:
            # como capítulos, salían vacíos en el EPUB
            for title in inserted:
                k = next(k for k, b in enumerate(blocks) if b is title)
                after = blocks[k + 1] if k + 1 < len(blocks) else None
                if after is None or (after.kind == "heading" and id(after) in chosen):
                    del blocks[k]
                    chosen.discard(id(title))
            # Marcadores incompletos: un título de capítulo en una página sin ningún marcador también cuenta.
            marked = {p for _, _, p in doc.get_toc()}
            chosen |= {id(b) for b in blocks if b.kind == "heading" and b.page not in marked
                       and KEYWORDS.match(html.unescape(b.html))}
            candidates = [k for k, b in enumerate(blocks) if id(b) in chosen]
        else:
            # La portadilla ("The Eminence in Shadow", "- Volumen 5 -") va antes del primer capítulo con
            # palabra clave: no cuenta como capítulo (en la ventana queda como subtítulo).
            heads = [k for k, b in enumerate(blocks) if b.kind == "heading"]
            first = next((k for k in heads if KEYWORDS.match(html.unescape(blocks[k].html))), None)
            candidates = [k for k in heads if first is None or k >= first]
            # "El lado de Beretta" justo bajo "Capítulo 21: …" (Maryoku 6) es un subtítulo: como capítulo dejaba
            # vacío el 21. Con él, los títulos del libro que empiezan igual ("El lado de Selene").
            text = lambda k: html.unescape(blocks[k].html)
            lead = lambda k: " ".join(text(k).lower().split()[:2])
            under = {lead(k) for k in candidates if k - 1 in candidates and KEYWORDS.match(text(k - 1))
                     and not KEYWORDS.match(text(k))}
            candidates = [k for k in candidates if KEYWORDS.match(text(k)) or lead(k) not in under]
        # Un título que el índice impreso nombra es un capítulo, aunque no lleve palabra clave ("Un caballero
        # al lado de Ischea"); los subtítulos ("El lado de…") no salen en el índice.
        keys = [_key(title) for title, _ in contents]
        candidates = sorted(set(candidates) | {k for k, b in enumerate(blocks) if b.kind == "heading" and any(
            _toc_match(_key(html.unescape(b.html)), key) for key in keys)})
        # "Parte 1", "Parte 2"… sin nombre y con la numeración que vuelve a empezar en cada capítulo
        # (Re Zero) son apartados del capítulo: subtítulos. "Parte uno: Stifled Shadow" sí es una parte.
        bare = [k for k in candidates if is_subpart(html.unescape(blocks[k].html))]
        if len({_key(blocks[k].html) for k in bare}) < len(bare):
            candidates = [k for k in candidates if k not in bare]

        meta = doc.metadata or {}
        meta_title = meta.get("title") or ""
        title = meta_title or Path(pdf_path).stem
        series, volume = _series_volume(Path(pdf_path).stem, meta_title)
        # Sin el volumen en el título, todos los tomos se llamarían igual en lectores que no muestran la serie
        if series and volume and not re.search(rf"(?<!\d)0*{re.escape(volume)}(?!\d)", meta_title):
            title = f"{series} - Volumen {volume}"
        credits = _credits(blocks[:candidates[0]] if candidates else [])
        # El autor de los metadatos suele ser quien maquetó el PDF: manda la página de créditos
        return Book(title, credits.get("author") or meta.get("author") or "", cover, blocks, candidates, series,
                    volume, translator=credits.get("translator", ""), illustrator=credits.get("illustrator", ""),
                    proofreader=credits.get("proofreader", ""), editor=credits.get("editor", ""), skipped=skipped,
                    toc=contents)


# Tipografía de libro impreso, común a todos los estilos: partición de palabras (el idioma ya va en
# cada página), sin sangría tras un título, separador o ilustración, y sin líneas sueltas.
BASE_CSS = """body { margin: 0 4%; line-height: 1.5; }
p { margin: 0 0 0.4em; orphans: 2; widows: 2; -webkit-hyphens: auto; hyphens: auto; }
h1, h2 { -webkit-hyphens: none; hyphens: none; page-break-after: avoid; }
h1 .num, h1 .name { display: block; }
h2 { text-align: center; font-size: 1.15em; margin: 1.5em 0 1em; }
p.brk { text-indent: 0; text-align: center; margin: 1em 0; }
hr.brk { border: 0; border-top: 1px solid #999999; width: 33%; margin: 1.2em auto; }
div.brk-gap { height: 1.5em; }
div.brk-img { text-align: center; margin: 1em 0; }
div.brk-img img { height: 1.4em; }
h1 + p, h2 + p, p.brk + p, hr.brk + p, div.brk-gap + p, div.brk-img + p, div.img + p { text-indent: 0; }
div.img { text-align: center; margin: 0; page-break-before: always; page-break-after: always; }
div.img img { max-width: 100%; max-height: 95vh; }
h1.toc { text-align: center; }
ol.toc, ol.toc ol { list-style: none; margin: 0; }
ol.toc { padding: 0; }
ol.toc ol { padding-left: 1.5em; font-size: 0.9em; }
ol.toc li { margin: 0.35em 0; }
ol.toc a { text-decoration: none; color: inherit; }
p.credits { text-indent: 0; text-align: center; margin: 0.6em 0; }
p.center { text-indent: 0; text-align: center; }
p.right { text-indent: 0; text-align: right; }
small.sc { font-size: 1em; font-variant: small-caps; }
img.icono { height: 1em; vertical-align: -0.15em; }
div.marco { border: 3px double #777777; padding: 0.5em 0.9em; margin: 1em 0; font-family: sans-serif;
  font-size: 0.95em; }
div.marco p { text-indent: 0; margin: 0; text-align: left; }
div.marco p.sub { padding-left: 1.3em; }
div.marco p.head { margin-top: 0.6em; }
p.status { text-indent: 0; text-align: center; font-family: sans-serif; font-size: 0.9em; margin: 0.8em 8%;
  padding: 0.4em; border: 1px solid #888888; }
p.status + p.status { margin-top: -0.85em; border-top: 0; }
span.min { font-size: 0.8em; color: #777777; }
a.nref { font-size: 0.75em; vertical-align: super; line-height: 0; text-decoration: none; }
.nota { font-size: 0.85em; margin-top: 1.5em; }
.nota p { text-indent: 0; }
"""
STYLES = {  # nombre en la ventana -> CSS propio del estilo
    "Clásico": """body { font-family: serif; }
p { text-indent: 1.5em; text-align: justify; }
h1 { text-align: center; font-size: 1.4em; margin: 2em 0 1em; }
h1 .num { font-size: 0.7em; font-weight: normal; }
""",
    "Moderno": """body { font-family: sans-serif; }
p { text-indent: 0; text-align: left; margin-bottom: 0.8em; }
h1 { text-align: left; font-size: 1.3em; margin: 1.5em 0 1em; }
h1 .num { font-size: 0.65em; font-weight: normal; text-transform: uppercase; letter-spacing: 0.1em; color: #666666; }
""",
    "Novela ligera": """body { font-family: serif; }
p { text-indent: 1.5em; text-align: justify; }
h1 { text-align: center; font-size: 1.25em; margin: 12% 0 1.2em; padding-bottom: 0.5em; border-bottom: 1px solid #999999; }
h1 .num { font-size: 0.8em; font-weight: normal; font-variant: small-caps; letter-spacing: 0.15em; }
h1 .name { font-size: 1.2em; margin-top: 0.3em; }
h1 .name.long { font-size: 1em; }
p.brk { font-size: 1.2em; }
""",
}
SEPARATORS = {"Novela ligera": "❖"}  # separador de escena; el resto usa "* * *"
SEPARATOR_CHOICES = ["Según el estilo", "Original del PDF", "* * *", "❖", "✦", "⁂", "◆ ◆ ◆", "~", "Línea",
                     "Espacio en blanco"]  # en la ventana, desplegable "Separador"
CHAPTER_NUMBER = re.compile(  # "Capítulo 3: Nombre" -> número y nombre, para el inicio de capítulo
    r"((?:cap[ií]tulo|chapter|pr[oó]logo|prologue|ep[ií]logo|epilogue|interludio|interlude|parte|part"
    r"|historia (?:paralela|adicional|corta))(?: [\w.]+)?)\s*[:.\-–—]\s*(.+)", re.I)


def _chapter_heading(title):
    """<h1> de un capítulo: si empieza por "Capítulo 3:" o similar, número y nombre en dos líneas."""
    m = CHAPTER_NUMBER.fullmatch(title)
    if not m:
        return f"<h1>{title}</h1>"
    name = "name long" if len(html.unescape(m.group(2))) > 45 else "name"  # los nombres largos, algo menores
    return f'<h1><span class="num">{m.group(1)}</span> <span class="{name}">{m.group(2)}</span></h1>'

CONTAINER = """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>
"""


def _xhtml(title, body, lang):
    return ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
            f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"'
            f' lang="{lang}" xml:lang="{lang}">\n'
            f'<head><meta charset="utf-8"/><title>{html.escape(BAD_XML.sub("", title))}</title>'
            '<link rel="stylesheet" type="text/css" href="style.css"/></head>\n'
            f'<body>\n{BAD_XML.sub("", body)}\n</body>\n</html>\n')  # un carácter de control rompe el XML


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


def _separator(block, index, style, choice, images):
    """XHTML de un separador de escena según la opción elegida (SEPARATOR_CHOICES). Con "Original del
    PDF" usa el adorno dibujado o el texto que traía; si no trae nada, el del estilo."""
    if choice == "Original del PDF":
        if block.image:
            ext = "png" if block.image[:4] == b"\x89PNG" else "jpg"
            src = f"img/b{index:05d}.{ext}"
            images[src] = block.image
            return f'<div class="brk-img"><img src="{src}" alt="* * *"/></div>'
        if block.html:
            return f'<p class="brk">{block.html}</p>'
        choice = "Según el estilo"
    if choice == "Línea":
        return '<hr class="brk"/>'
    if choice == "Espacio en blanco":
        return '<div class="brk-gap"></div>'
    symbol = SEPARATORS.get(style, "* * *") if choice == "Según el estilo" else html.escape(choice)
    return f'<p class="brk">{symbol}</p>'


def image_key(data):
    """Clave de una ilustración para recordar su descripción: el índice del bloque cambia al editar, ella no."""
    return hashlib.sha1(data).hexdigest()[:16]


def _accessibility(illustrated):
    """Metadatos de accesibilidad de EPUB 3 (schema.org): las tiendas los piden desde la Ley Europea de
    Accesibilidad. Sin dcterms:conformsTo: nadie ha verificado el libro contra WCAG."""
    features = ["tableOfContents", "readingOrder", "structuralNavigation"] + (["alternativeText"] if illustrated else [])
    summary = ("Texto que se adapta a la pantalla, con índice, capítulos navegables y un orden de lectura lógico. "
               + ("Las ilustraciones llevan una descripción breve. " if illustrated else "")
               + "Sin destellos, movimiento ni sonido.")
    return ([f'<meta property="schema:accessMode">{mode}</meta>' for mode in ["textual"] + ["visual"] * illustrated]
            + ['<meta property="schema:accessModeSufficient">textual</meta>']
            + [f'<meta property="schema:accessibilityFeature">{f}</meta>' for f in features]
            + ['<meta property="schema:accessibilityHazard">none</meta>',
               f'<meta property="schema:accessibilitySummary">{summary}</meta>'])


def _file_as(author):
    """Autor para ordenar: "Tappei Nagatsuki" -> "Nagatsuki, Tappei" (si ya lleva coma, tal cual)."""
    words = author.split()
    return author if "," in author or len(words) < 2 else f"{words[-1]}, {' '.join(words[:-1])}"


STATUS = re.compile(r"\s*[【\[].*[】\]]\s*")  # "【Habilidad adquirida】", "[Nivel 5]": ventana de estado


def _fit_style(data, page):
    """Tamaño de una ilustración para la vista previa, que cabe en la página (ancho, alto en pt). MuPDF no entiende
    max-height: una más alta que la página pasaba entera a la siguiente y dejaba la primera en blanco con un borde
    de la imagen abajo (Eminence 7, pág. 23). Solo encoge: una pequeña se ve a su tamaño, como en un lector."""
    width, height = Image.open(io.BytesIO(data)).size
    scale = min(1, page[0] * 0.78 / width, page[1] * 0.85 / height)  # dentro de los márgenes de MuPDF (~8 %)
    return f' style="width: {width * scale:.0f}px; height: {height * scale:.0f}px"'


def write_epub(book, chapters, out_path, lang="es", removed=(), typography=False, style="Clásico", contents=True,
               separator="Según el estilo", shrink=False, replacements=(), credits=False, gray=False,
               breaks=(), trim=False, font="", epub2=False, reading=False, kepub=False, hyphenate=False, alts=None,
               progress=None, fit=None, whole_words=False, css_extra=""):
    """Escribe el EPUB. chapters: [(índice de bloque, título)] elegidos por el usuario.
    removed: índices de títulos cuya sección se quita (agradecimientos, enlaces del grupo…).
    Un capítulo se lleva todo hasta el siguiente capítulo, subtítulos incluidos; un subtítulo,
    hasta el siguiente título.
    typography: aplica la limpieza tipográfica al estilo RAE (_typography) a textos y títulos.
    style: uno de STYLES. contents: añade la página "Contenido" tras la portada (la vista previa
    la omite para dibujar directamente la sección). separator: una de SEPARATOR_CHOICES.
    shrink: reduce las imágenes y la portada (_shrink); True a SHRINK píxeles o un número de píxeles.
    replacements: [(buscar, reemplazo)] del usuario, en textos y títulos (nombres, honoríficos…).
    credits: añade tras la portada una página "Créditos" con los datos del libro.
    gray: ilustraciones en escala de grises (la portada no: en la biblioteca del lector se ve en color).
    breaks: títulos que se escriben como separador de escena ("Parte 1" sin nombre).
    trim: recorta el margen blanco de las ilustraciones (_trim). font: una de FONTS o la ruta de un .ttf/.otf, incrustada.
    epub2: paquete EPUB 2 para lectores antiguos. reading: minutos de lectura en la página "Contenido".
    kepub: secciones en formato Kobo (_kepub); el archivo debe acabar en .kepub.epub.
    hyphenate: guiones blandos en los párrafos (_hyphenate), solo en libros en español.
    whole_words: ninguna palabra se parte con guion al final de línea; la que no cabe pasa entera a la siguiente.
    alts: {image_key: descripción} de las ilustraciones que describió el usuario; las demás, «Ilustración de
    «capítulo»». Con los metadatos de accesibilidad (EPUB 3), el lector de pantalla las anuncia.
    Cada página del PDF deja una marca invisible (page-list): el lector puede mostrar su número.
    progress(hechos, total): avanza por sección e ilustración escritas (reducir las imágenes es lo lento).
    fit: (ancho, alto) de la página de la vista previa: cada ilustración lleva un tamaño que cabe en ella
    (_fit_style). Solo para MuPDF; el EPUB de verdad no lo lleva. css_extra: reglas al final de la hoja (write_pdf)."""
    rules = _rules(replacements)
    hyphenate = hyphenate and not whole_words and lang.split("-")[0].lower() == "es"
    typo = (lambda s, dashes=False: _typography(s, dashes)) if typography else (lambda s, dashes=False: s)
    fix = lambda s, dashes=False: typo(_replace(s, rules) if rules else s, dashes)  # títulos: sin rayas
    limit = SHRINK if shrink is True else int(shrink or 0)
    starts, breaks = dict(chapters), set(breaks)
    skip = set()
    for k in removed:
        chapter = k in book.candidates or k in starts
        end = next((j for j in range(k + 1, len(book.blocks))
                    if book.blocks[j].kind == "heading" and (j in starts or not chapter)), len(book.blocks))
        skip.update(range(k, end))
    sections = []  # [título, es_capítulo, [(índice, bloque)]]
    for k, b in enumerate(book.blocks):
        if k in skip or (b.kind == "image" and not b.keep):  # sección quitada o imagen descartada
            continue
        if k in starts:
            sections.append([starts[k], True, []])
        else:
            if not sections:
                sections.append(["Inicio", False, []])
            sections[-1][2].append((k, b))

    files, images = {}, {}

    def icon_file(m):
        """El icono de ICON como archivo: el mismo ♡ en todo el libro, uno solo."""
        data = base64.b64decode(m[1])
        src = f"img/icono-{image_key(data)}.png"
        images[src] = data
        return f'<img class="icono" src="{src}" alt=""/>'

    subtitles = {}  # archivo -> [(ancla, título escapado)]: subentradas del índice
    pages, marked = [], set()  # [(archivo, página)] de la lista de páginas; páginas ya marcadas
    noted = 0  # notas al pie escritas: numeración seguida en todo el libro, ids únicos
    for n, (title, is_chapter, blocks) in enumerate(sections, 1):
        name = f"sec{n:03d}.xhtml"
        # quote=False: la limpieza tipográfica ve las comillas («"Hola"» -> «“Hola”»), no &quot;
        heading = fix(html.escape(title, quote=False))
        parts = [_chapter_heading(heading)] if is_chapter else []
        pending = None  # página que empieza en este tramo: su marca va en el siguiente párrafo o título
        notes = []  # (número, html) de las notas de esta sección: van al final, tras el texto

        def noteref(m):
            nonlocal noted
            noted += 1
            notes.append((noted, m[1]))
            kind = "" if epub2 else ' epub:type="noteref"'
            return f'<a class="nref"{kind} href="#nota{noted}" id="ref{noted}">{noted}</a>'

        def mark():
            nonlocal pending
            if pending is None or epub2:
                return ""
            page, pending = pending, None
            pages.append((name, page))
            return f'<span epub:type="pagebreak" role="doc-pagebreak" id="pg{page}" title="{page}"></span>'

        framed = [b.kind == "p" and b.align == "frame" for _, b in blocks]
        # Con viñetas en el recuadro, las líneas sin viñeta son sus datos y van sangradas (Witch 1: «HP: 10/10»)
        bulleted, run = set(), []
        for at, inside in enumerate(framed):
            if inside:
                run.append(at)
            if not inside or at == len(blocks) - 1:
                if any(_plain(blocks[j][1])[:1] in BULLETS for j in run):
                    bulleted |= set(run)
                run = []
        for at, (k, b) in enumerate(blocks):
            if b.page not in marked:
                marked.add(b.page)
                pending = b.page
            if framed[at] and (at == 0 or not framed[at - 1]):
                parts.append('<div class="marco">')
            if b.kind == "image":
                alt = (alts or {}).get(image_key(b.image)) or (f"Ilustración de «{title}»" if is_chapter else "Ilustración")
                for i, data in enumerate(_spread(b.image, b.layout) if b.layout else [b.image]):
                    ext = "png" if data[:4] == b"\x89PNG" else "jpg"  # PNG si trae transparencia
                    src = f"img/b{k:05d}{'_' + str(i) if b.layout else ''}.{ext}"  # por bloque: una página puede tener varias
                    images[src] = data
                    sized = _fit_style(data, fit) if fit else ""
                    parts.append(f'<div class="img"><img src="{src}" alt="{html.escape(alt)}"{sized}/></div>')
            elif b.kind == "break" or k in breaks:
                parts.append(_separator(b if b.kind == "break" else Block("break", b.page), k, style, separator, images))
            elif b.kind == "heading":  # título que no es capítulo: subtítulo ("El lado de la bruja")
                parts.append(f'{mark()}<h2 id="t{k}">{NOTE.sub(noteref, fix(b.html))}</h2>')  # fuera: el título queda limpio
                # en el índice, texto solo: <i> no vale en el <text> del NCX y la nota no es del título
                subtitles.setdefault(name, []).append((f"t{k}", TAGS.sub("", NOTE.sub("", fix(b.html)))))
            else:
                if framed[at]:
                    bullet = _plain(b)[:1] in BULLETS  # un apartado nuevo del recuadro («◆ Equipment») se separa
                    css = ("sub" if at in bulleted and not bullet else "head" if bullet and at and framed[at - 1]
                           else "")
                else:
                    css = ("status" if STATUS.fullmatch(_plain(b)) else "right" if b.align == "right"
                           else "center" if b.align else "")
                attr = f' class="{css}"' if css else ""
                body = NOTE.sub(noteref, ICON.sub(icon_file, fix(b.html, dashes=not framed[at])))  # la nota sale del párrafo: no se parte
                parts.append(f"<p{attr}>{mark()}{_hyphenate(body) if hyphenate else body}</p>")
            if framed[at] and (at == len(blocks) - 1 or not framed[at + 1]):
                parts.append("</div>")
        # Los lectores con notas emergentes (Kobo, Kindle, Apple Books) ocultan el aside y lo muestran
        # al tocar la llamada; los demás lo dejan al final de la sección, con enlace de vuelta.
        tag, kind = ("div", "") if epub2 else ("aside", ' epub:type="footnote"')
        parts += [f'<{tag}{kind} class="nota" id="nota{k}"><p><a href="#ref{k}">{k}</a>. {text}</p></{tag}>'
                  for k, text in notes]
        files[name] = _xhtml(html.unescape(heading), "\n".join(parts), lang)

    # el mismo libro exportado otra vez conserva su identificador: el lector no lo duplica ni pierde la posición
    uid = f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, f'{book.series}|{book.volume}|{book.title}|{book.author}')}"
    title = html.escape(book.title)
    meta = []  # metadatos: en EPUB 3 los roles y el orden van en <meta refines>; en EPUB 2, como atributos opf:

    def person(tag, ident, value, role, file_as=""):
        value = html.escape(value)
        if epub2:
            extra = f' opf:file-as="{html.escape(file_as)}"' if file_as else ""
            meta.append(f'<dc:{tag} opf:role="{role}"{extra}>{value}</dc:{tag}>')
            return
        meta.append(f'<dc:{tag} id="{ident}">{value}</dc:{tag}>')
        meta.append(f'<meta refines="#{ident}" property="role" scheme="marc:relators">{role}</meta>')
        if file_as:
            meta.append(f'<meta refines="#{ident}" property="file-as">{html.escape(file_as)}</meta>')

    if book.author:
        person("creator", "aut", book.author, "aut", _file_as(book.author))
    for ident, value, role in (("trl", book.translator, "trl"), ("ill", book.illustrator, "ill"),
                               ("pfr", book.proofreader, "pfr"), ("edt", book.editor, "edt")):
        if value:
            person("contributor", ident, value, role)
    if book.publisher:
        meta.append(f"<dc:publisher>{html.escape(book.publisher)}</dc:publisher>")
    if book.description:
        meta.append(f"<dc:description>{html.escape(book.description)}</dc:description>")
    meta += [f"<dc:subject>{html.escape(s.strip())}</dc:subject>" for s in book.subjects.split(",") if s.strip()]
    if book.date:
        meta.append(f"<dc:date>{html.escape(book.date)}</dc:date>")
    if book.original_title and not epub2:
        meta.append(f'<meta property="dcterms:alternative">{html.escape(book.original_title)}</meta>')
    if book.series:  # serie en EPUB 3 (belongs-to-collection) y en el formato de Calibre
        name, number = html.escape(book.series), html.escape(book.volume)
        if not epub2:
            meta.append(f'<meta property="belongs-to-collection" id="serie">{name}</meta>\n'
                        '<meta refines="#serie" property="collection-type">series</meta>')
            if number:
                meta.append(f'<meta refines="#serie" property="group-position">{number}</meta>')
        meta.append(f'<meta name="calibre:series" content="{name}"/>')
        if number:
            meta.append(f'<meta name="calibre:series_index" content="{number}"/>')
    if not epub2:
        meta.append(f'<meta property="dcterms:modified">{datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ}</meta>')
        meta += _accessibility(bool(images))
    nav_props, cover_props = ("", "") if epub2 else (' properties="nav"', ' properties="cover-image"')
    manifest = [
        f'<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml"{nav_props}/>',
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
        '<item id="css" href="style.css" media-type="text/css"/>',
        f'<item id="cover-image" href="cover.jpg" media-type="image/jpeg"{cover_props}/>',
        '<item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>',
    ]
    manifest += [f'<item id="s{k}" href="{name}" media-type="application/xhtml+xml"/>' for k, name in enumerate(files)]
    manifest += [f'<item id="i{k}" href="{src}" media-type="image/{"png" if src.endswith(".png") else "jpeg"}"/>'
                 for k, src in enumerate(images)]
    fonts = {}  # ruta en el EPUB -> archivo de la carpeta fonts
    css = BASE_CSS + STYLES[style]
    if whole_words:  # el PDF maquetado por MuPDF partía «mu-jer», «cue-ro» con hyphens: auto
        css += "p { -webkit-hyphens: none; hyphens: none; }\n"
    if font in FONTS:
        for weight, slant in (("400", "normal"), ("400", "italic"), ("700", "normal"), ("700", "italic")):
            fonts[f"fonts/{FONTS[font]}-{weight}-{slant}.ttf"] = FONT_DIR / f"{FONTS[font]}-{weight}-{slant}.ttf"
            css += (f'@font-face {{ font-family: "{font}"; font-weight: {weight}; font-style: {slant}; '
                    f'src: url("fonts/{FONTS[font]}-{weight}-{slant}.ttf"); }}\n')
        css += f'body {{ font-family: "{font}", serif; }}\n'
    elif font and Path(font).is_file():  # fuente propia: un solo archivo, el lector simula negrita y cursiva
        src, family = f"fonts/propia{Path(font).suffix.lower()}", re.sub(r'["\\]', "", Path(font).stem)
        fonts[src] = Path(font)  # nombre fijo: el del usuario puede llevar espacios o tildes, que rompen el href
        css += f'@font-face {{ font-family: "{family}"; src: url("{src}"); }}\nbody {{ font-family: "{family}", serif; }}\n'
    css += css_extra
    manifest += [f'<item id="f{k}" href="{src}" media-type="application/vnd.ms-opentype"/>' for k, src in enumerate(fonts)]
    spine = ['<itemref idref="cover"/>'] + [f'<itemref idref="s{k}"/>' for k in range(len(files))]
    toc_page = "contents.xhtml" if contents else "nav.xhtml"
    if contents:  # página "Contenido" visible, justo después de la portada
        manifest.append('<item id="contents" href="contents.xhtml" media-type="application/xhtml+xml"/>')
        spine.insert(1, '<itemref idref="contents"/>')
    if credits:  # antes del índice
        manifest.append('<item id="credits" href="credits.xhtml" media-type="application/xhtml+xml"/>')
        spine.insert(1, '<itemref idref="credits"/>')
    # Inicio de la lectura: el primer capítulo (no "Inicio", que son las láminas del principio)
    reading_start = next((name for name, sec in zip(files, sections) if sec[1]), next(iter(files), "cover.xhtml"))
    manifest_xml, spine_xml, meta_xml = "\n".join(manifest), "\n".join(spine), "\n".join(meta)
    opf_ns = ' xmlns:opf="http://www.idpf.org/2007/opf"' if epub2 else ""
    opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="{'2.0' if epub2 else '3.0'}" unique-identifier="uid">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"{opf_ns}>
<dc:identifier id="uid">{uid}</dc:identifier>
<dc:title>{title}</dc:title>
{meta_xml}
<dc:language>{lang}</dc:language>
<meta name="cover" content="cover-image"/>
</metadata>
<manifest>
{manifest_xml}
</manifest>
<spine toc="ncx">
{spine_xml}
</spine>
<guide>
<reference type="cover" title="Portada" href="cover.xhtml"/>
<reference type="toc" title="Índice" href="{toc_page}"/>
<reference type="text" title="Inicio de la lectura" href="{reading_start}"/>
</guide>
</package>
"""
    # Índice en dos niveles: cada sección y, dentro, sus subtítulos con enlace a su ancla.
    items, shown, points, order = [], [], [], 0
    for (name, sec) in zip(files, sections):
        t = sec[0]
        subs = subtitles.get(name, [])
        children = "".join(f'<li><a href="{name}#{anchor}">{text}</a></li>' for anchor, text in subs)
        children = f"<ol>{children}</ol>" if subs else ""
        items.append(f'<li><a href="{name}">{fix(html.escape(t, quote=False))}</a>{children}</li>')
        minutes = reading_minutes([b for _, b in sec[2]]) if reading else 0
        extra = f' <span class="min">· {minutes} min</span>' if minutes else ""
        shown.append(f'<li><a href="{name}">{fix(html.escape(t, quote=False))}</a>{extra}{children}</li>')
        order += 1  # playOrder: el capítulo antes que sus subtítulos, todos correlativos
        point = (f'<navPoint id="n{order}" playOrder="{order}"><navLabel><text>{fix(html.escape(t, quote=False))}</text></navLabel>'
                 f'<content src="{name}"/>')
        for anchor, text in subs:
            order += 1
            point += (f'<navPoint id="n{order}" playOrder="{order}"><navLabel><text>{text}</text></navLabel>'
                      f'<content src="{name}#{anchor}"/></navPoint>')
        points.append(point + "</navPoint>")
    items, shown, points = "\n".join(items), "\n".join(shown), "\n".join(points)
    # Puntos de referencia: los botones "Ir a la portada / al índice / al inicio" de los lectores
    landmarks = ('<nav epub:type="landmarks" id="landmarks" hidden="hidden"><ol>'
                 '<li><a epub:type="cover" href="cover.xhtml">Portada</a></li>'
                 f'<li><a epub:type="toc" href="{toc_page}">Índice</a></li>'
                 f'<li><a epub:type="bodymatter" href="{reading_start}">Inicio de la lectura</a></li></ol></nav>')
    page_list = ('\n<nav epub:type="page-list" id="page-list" hidden="hidden"><ol>'
                 + "".join(f'<li><a href="{name}#pg{page}">{page}</a></li>' for name, page in pages)
                 + "</ol></nav>") if pages else ""
    nav = _xhtml("Índice", f'<nav epub:type="toc" id="toc"><h1>Índice</h1><ol>\n{items}\n</ol></nav>\n{landmarks}'
                 f"{page_list}", lang)
    contents_page = _xhtml("Contenido", f'<h1 class="toc">Contenido</h1>\n<ol class="toc">\n{shown}\n</ol>', lang)
    ncx = f"""<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
<head><meta name="dtb:uid" content="{uid}"/></head>
<docTitle><text>{title}</text></docTitle>
<navMap>
{points}
</navMap>
</ncx>
"""
    cover = _xhtml(book.title, '<div class="img"><img src="cover.jpg" alt="Portada"/></div>', lang)
    serie = f"{book.series}, volumen {book.volume}" if book.series and book.volume else book.series
    lines = [f"<b>{title}</b>"] + [f"{label}: {html.escape(value)}" for label, value in (
        ("Título original", book.original_title), ("Serie", serie), ("Autor", book.author),
        ("Ilustraciones", book.illustrator), ("Traducción", book.translator), ("Corrección", book.proofreader),
        ("Edición", book.editor), ("Grupo", book.publisher)) if value] + [f"EPUB creado el {datetime.now():%d/%m/%Y}"]
    credits_page = _xhtml("Créditos", '<h1 class="toc">Créditos</h1>\n'
                          + "\n".join(f'<p class="credits">{line}</p>' for line in lines), lang)

    tmp = f"{out_path}.tmp"
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
            z.writestr("META-INF/container.xml", CONTAINER)
            z.writestr("OEBPS/content.opf", BAD_XML.sub("", opf))  # metadatos pegados con controles
            z.writestr("OEBPS/nav.xhtml", nav)
            if contents:
                z.writestr("OEBPS/contents.xhtml", contents_page)
            if credits:
                z.writestr("OEBPS/credits.xhtml", credits_page)
            z.writestr("OEBPS/toc.ncx", BAD_XML.sub("", ncx))
            z.writestr("OEBPS/style.css", css)
            z.writestr("OEBPS/cover.xhtml", cover)
            cover_data = _shrink(book.cover, limit) if limit and book.cover else book.cover
            if cover_data[:4] == b"\x89PNG":  # ilustración con transparencia como portada: cover.jpg es JPEG
                cover_data = _jpeg(_rgb(pymupdf.Pixmap(cover_data), alpha=False), 90)
            z.writestr("OEBPS/cover.jpg", cover_data)
            total, done = len(files) + len(images) + 1, 1
            if progress:
                progress(done, total)
            for name, text in files.items():
                z.writestr(f"OEBPS/{name}", _kepub(text) if kepub else text)
                done += 1
                if progress:
                    progress(done, total)
            for src, data in images.items():
                data = _trim(data) if trim else data
                data = _gray(data) if gray else data
                z.writestr(f"OEBPS/{src}", _shrink(data, limit) if limit else data)
                done += 1
                if progress:
                    progress(done, total)
            for src, path in fonts.items():
                z.write(path, f"OEBPS/{src}")
        os.replace(tmp, out_path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


XHTML = "{http://www.w3.org/1999/xhtml}"
PART_TITLE = re.compile(r"(parte|volumen|libro|part|book)\b", re.I)  # portadillas: sin texto a propósito

# Tamaños del PDF: (ancho, alto en puntos, letra). A5 para leer o imprimir; 6″ para lectores que leen mal el EPUB
PDF_SIZES = {"A5": (420, 595, 10.5), "e-reader 6″": (300, 400, 9), "A4": (595, 842, 12)}


ILLUSTRATION_SHARE = 0.25  # una imagen que ocupa más de esto de la página es una ilustración, no un adorno


def _image_page(pdf, xref, pno, rect):
    """Página nueva en la posición pno de pdf con su imagen xref llenándola entera: el ancho es el del libro
    (todas las páginas iguales de ancho) y el alto sigue a la imagen; con el alto del libro, una ilustración
    doble quedaba pequeña entre márgenes blancos (Witch 1)."""
    info = pdf.extract_image(xref)
    page = pdf.new_page(pno, width=rect.width, height=rect.width * info["height"] / info["width"])
    if info.get("smask"):  # con transparencia: la máscara va aparte en el PDF
        page.insert_image(page.rect, pixmap=pymupdf.Pixmap(pymupdf.Pixmap(pdf, xref), pymupdf.Pixmap(pdf, info["smask"])))
    else:
        page.insert_image(page.rect, stream=info["image"])  # el JPEG tal cual: sin volver a comprimir


def _full_page_images(pdf):
    """Pone cada ilustración (y la portada) de pdf en una página entera. Una que compartía página con texto sale
    de ella: antes del texto si estaba encima, después si estaba debajo; el texto se queda. Los adornos de
    separador (pequeños) no se tocan. Cambia pdf en su sitio: copiar página a página duplicaba las letras
    incrustadas (Re Zero 1: de 4 a 20 MB). Devuelve [página nueva de cada página vieja], para el índice."""
    plans = []  # por página: (imágenes antes, se queda la página con su texto, imágenes después, cajas a quitar)
    for page in pdf:
        area = page.rect.width * page.rect.height
        big = []
        for image in page.get_images(full=True):
            try:
                box = page.get_image_bbox(image)
            except ValueError:
                continue
            if box.width * box.height > ILLUSTRATION_SHARE * area:
                big.append((image[0], box))
        text = [pymupdf.Rect(b[:4]) for b in page.get_text("blocks") if b[6] == 0 and b[4].strip()]
        if not big:
            plans.append(([], True, [], []))
        elif not text:
            plans.append(([xref for xref, _ in big], False, [], []))
        else:
            top = min(r.y0 for r in text)
            before = [xref for xref, box in big if box.y1 <= top + 1]
            plans.append((before, True, [xref for xref, _ in big if xref not in before], [box for _, box in big]))
    moved, count = [], 0
    for before, keep, after, _ in plans:
        moved.append(count)
        count += len(before) + keep + len(after)
    for n in range(len(plans) - 1, -1, -1):  # desde el final: las páginas de antes no se mueven
        before, keep, after, boxes = plans[n]
        if not before and keep and not after:
            continue
        rect = pdf[n].rect
        for xref in reversed(after):
            _image_page(pdf, xref, n + 1, rect)
        if keep:
            page = pdf[n]
            for box in boxes:  # quitar la imagen solo de esta página: delete_image cambia el objeto compartido
                page.add_redact_annot(box)
            page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_REMOVE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                                  text=pymupdf.PDF_REDACT_TEXT_NONE)
        for xref in reversed(before):
            _image_page(pdf, xref, n, rect)
        if not keep:
            pdf.delete_page(n + len(before))
    return moved


# Making Magic: con la letra por defecto de MuPDF (Charis SIL) las versalitas quedan en el PDF como «CƒĵǙǵŧǡ 3»;
# releído, «Chapter 3» no era etiqueta, y copiar o buscar fallaba. Mayúsculas algo menores: se ven casi igual
PDF_CSS = "h1 .num, small.sc { font-variant: normal; text-transform: uppercase; }\nsmall.sc { font-size: 0.8em; }\n"


def write_pdf(book, chapters, out_path, size="A5", progress=None, **options):
    """PDF maquetado: el mismo libro que write_epub (con sus opciones), compuesto por MuPDF al tamaño de
    PDF_SIZES, con el índice como marcadores, título y autor en las propiedades y número de página al pie
    (no en la portada ni en las ilustraciones). Cada ilustración, y la portada, ocupa una página entera
    (_full_page_images). Se escribe en out_path + ".tmp" y se renombra.
    progress(hechos, 100): por etapas; maquetar con MuPDF es una sola llamada y salta de 40 a 70."""
    step = progress or (lambda done, total: None)
    width, height, fontsize = PDF_SIZES[size]
    with tempfile.TemporaryDirectory(prefix="lnepub-pdf-") as tmp:
        epub = Path(tmp) / "libro.epub"
        write_epub(book, chapters, epub, **{**options, "kepub": False, "epub2": False, "css_extra": PDF_CSS},
                   progress=lambda done, total: step(done * 40 // total, 100))
        with pymupdf.open(epub) as source:
            source.layout(width=width, height=height, fontsize=fontsize)
            toc = source.get_toc()
            laid = pymupdf.open("pdf", source.convert_to_pdf())
    step(70, 100)
    pdf = laid
    moved = _full_page_images(pdf)
    step(80, 100)
    pdf.set_toc([[level, title, moved[page - 1] + 1, *rest] for level, title, page, *rest in toc])
    pdf.set_metadata({"title": book.title, "author": book.author, "subject": book.series,
                      "creator": "Light Novel Creator", "producer": "Light Novel Creator"})
    for n, page in enumerate(pdf):
        step(80 + n * 15 // len(pdf), 100)
        if n == 0 or (not page.get_text().strip() and page.get_images()):  # portada, ilustración a página completa
            continue
        page.insert_textbox(pymupdf.Rect(0, height - 26, width, height - 10), str(n + 1), fontsize=fontsize * 0.75,
                            fontname="tiro", color=(0.45, 0.45, 0.45), align=pymupdf.TEXT_ALIGN_CENTER)
    partial = f"{out_path}.tmp"
    pdf.save(partial, garbage=3, deflate=True)
    pdf.close()
    os.replace(partial, out_path)
    step(100, 100)


def check_epub(path):
    """Revisa un EPUB ya escrito: XML válido, ningún archivo, imagen ni enlace del índice que falte, y
    ningún capítulo vacío (salvo portadillas como "Parte uno"). Devuelve los problemas, en español."""
    problems = []
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        trees = {}
        for name in sorted(n for n in names if n.endswith((".xhtml", ".opf", ".ncx"))):
            try:
                trees[name] = ET.fromstring(z.read(name))
            except ET.ParseError as e:
                problems.append(f"{name}: XML no válido ({e})")
    base = "OEBPS/"
    opf = trees.get(base + "content.opf")
    if opf is not None:
        for item in opf.iter("{http://www.idpf.org/2007/opf}item"):
            if base + item.get("href") not in names:
                problems.append(f"Falta {item.get('href')}, declarado en content.opf")
    ids = {name: {e.get("id") for e in tree.iter() if e.get("id")} for name, tree in trees.items()}
    for name, tree in trees.items():
        if not name.endswith(".xhtml"):
            continue
        for img in tree.iter(XHTML + "img"):
            if base + img.get("src", "") not in names:
                problems.append(f"{name[len(base):]}: falta la imagen {img.get('src')}")
        for a in tree.iter(XHTML + "a"):
            target, _, anchor = a.get("href", "").partition("#")
            if target and not re.match(r"https?://", target) and (base + target not in names or (anchor and anchor not in ids.get(base + target, ()))):
                problems.append(f"{name[len(base):]}: enlace roto a {a.get('href')}")
        h1 = next(tree.iter(XHTML + "h1"), None)
        if (re.fullmatch(r"OEBPS/sec\d+\.xhtml", name) and h1 is not None
                and not any(True for _ in tree.iter(XHTML + "p")) and not any(True for _ in tree.iter(XHTML + "img"))):
            title = " ".join("".join(h1.itertext()).split())
            if not PART_TITLE.match(title):
                problems.append(f"Capítulo vacío: {title}")
    return problems
