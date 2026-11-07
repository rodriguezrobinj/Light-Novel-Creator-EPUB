"""Convierte PDFs de novelas ligeras en EPUB con texto fluido."""
import collections
import colorsys
import difflib
import functools
import html
import io
import os
import re
import unicodedata
import uuid
import xml.etree.ElementTree as ET
import zipfile
import zlib
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

import pymupdf
from PIL import Image

BAND = 0.12     # franja superior/inferior (fracción de la altura) donde viven encabezados y pies
REPEAT = 0.30   # fracción de páginas en que una línea debe repetirse para ser encabezado o pie
KEYWORDS = re.compile(
    r"(cap[ií]tulo|pr[oó]logo|ep[ií]logo|interludio|historia paralela|palabras de cierre"
    r"|extra|parte|chapter|prologue|epilogue|afterword|interlude)\b", re.I)
TOC_TITLE = re.compile(r"(contenidos?|[ií]ndice|tabla de contenidos?|contents|table of contents):?", re.I)
DOT_LEADER = re.compile(r"[.\-_·…]{4,}\s*(?:p[áa]g\.?\s*)?\d+\s*\|?$", re.I)  # "..... 12", "---- Pág. 54"
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


VOLUME = re.compile(r"\b(?:vol(?:umen|ume)?\.?|tomo)\s*0*(\d+(?:[.,]\d+)?)", re.I)


def _series_volume(stem, meta_title):
    """Serie y volumen desde el nombre del archivo ("Serie - Volumen 01 [WP]" -> "Serie", "1").
    Si el nombre no dice la serie ("Volumen 1 World Project"), se usa el título del PDF."""
    m = VOLUME.search(stem)
    if not m:
        return "", ""
    series = re.sub(r"\[[^\]]*\]", "", stem[:m.start()]).strip(" -–—_.")
    return series or meta_title.strip(), m.group(1).replace(",", ".")


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
        part = html.escape(t, quote=False)
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

    counts, loose_counts = collections.Counter(), collections.Counter()
    for rows, h in zip(pages, heights):
        counts.update({_norm(r.text) for r in rows if in_band(r, h)})
        loose_counts.update({loose(r.text) for r in rows if in_band(r, h)})
    least = max(2, REPEAT * len(pages))
    repeated = {k for k, n in counts.items() if n >= least}
    repeated.add("#")  # números de página sueltos
    # Encabezados que cambian con el capítulo: la variante del prólogo o de las palabras del autor
    # ("Einherjar Project Palabras del autor | 230") sale en pocas páginas, pero empieza igual.
    loose_repeated = tuple(k for k, n in loose_counts.items() if n >= least and len(k) >= 6)

    def margin(r, h):
        if not in_band(r, h):
            return False
        # La clave suelta solo para líneas con número de página que no abren con "Capítulo…": un título
        # de capítulo en la franja superior también se queda en pocas palabras repetidas
        footer = re.search(r"\d", r.text) and not KEYWORDS.match(r.text.strip(LABEL_DECOR))
        return _norm(r.text) in repeated or bool(footer and loose_repeated and loose(r.text).startswith(loose_repeated))

    return [[r for r in rows if not margin(r, h)] for rows, h in zip(pages, heights)]


def _is_heading(r, body):
    if len(r.text) > 120:
        return False
    return r.size >= body * 1.3 or (r.bold and (r.size >= body * 1.1 or bool(KEYWORDS.match(r.text))))


LABEL_DECOR = "»«-–—|·*~ "  # adornos alrededor de una etiqueta de capítulo: "» Capítulo 1 «", "«Prólogo»"
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
            if (len(nxt.text) > 120 or not (nxt.bold or nxt.heading) or (nxt.starts and names)
                    or SUBPART.fullmatch(nxt.text.strip())
                    or nxt.y - last.y >= 2.2 * last.size or (names and abs(nxt.size - names[0].size) >= 0.5)):
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


def _ornaments(page, rows, infos):
    """Posición vertical de los adornos que hacen de separador de escena (Kage, Nageki): imágenes
    pequeñas, centradas, fuera de los márgenes y con texto encima y debajo."""
    width, height = page.rect.width, page.rect.height
    found = []
    for info in infos:
        x0, y0, x1, y1 = info["bbox"]
        if (x1 - x0 < 0.3 * width and y1 - y0 < 0.1 * height and abs((x0 + x1) / 2 - width / 2) < 0.15 * width
                and BAND * height < (y0 + y1) / 2 < (1 - BAND) * height
                and any(r.y < y0 for r in rows) and any(r.y > y1 for r in rows)):
            found.append(((y0 + y1) / 2, info["xref"]))
    return sorted(found)  # [(posición vertical, xref)], de arriba abajo


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


def _gray(data):
    """Imagen en escala de grises: en un e-reader de tinta se ve igual y pesa menos."""
    pix = pymupdf.Pixmap(data)
    if pix.n - pix.alpha == 1:
        return data
    pix = pymupdf.Pixmap(pymupdf.csGRAY, pix)
    return pix.tobytes("png") if pix.alpha else _jpeg(pix, 85)


@functools.lru_cache(maxsize=256)  # la lista lo pregunta en cada edición: decodificar costaba 2 s (Kage 5)
def is_wide(data):
    """Ilustración doble (dos páginas del libro en una imagen apaisada): en un celular en vertical
    se ve diminuta, así que la ventana ofrece dividirla o girarla."""
    pix = pymupdf.Pixmap(data)
    return pix.width > pix.height * 1.15


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


def _replace(html_text, rules):
    """Aplica los reemplazos del usuario (_rules) solo al texto, sin tocar las etiquetas <i>/<b>."""
    parts = re.split(r"(<[^>]+>)", html_text)
    for k in range(0, len(parts), 2):
        for pattern, new, regex in rules:
            parts[k] = pattern.sub((lambda m: m.expand(new)) if regex else (lambda _: new), parts[k])
    return "".join(parts)


def rule_pattern(find):
    """Expresión de una regla: "re:…" es una expresión regular tal cual; si no, la palabra o frase
    completa ("Ana" no cambia "Anabel"), escapada como el texto del EPUB ("Tom & Jerry" está como
    "Tom &amp; Jerry"). Lanza re.error si la expresión regular no es válida."""
    if find.startswith("re:"):
        return re.compile(find[3:])
    return re.compile(rf"(?<!\w){re.escape(html.escape(find, quote=False))}(?!\w)")


def _rules(replacements):
    """[(buscar, reemplazo)] a [(expresión, reemplazo, es_regex)]. Una expresión regular no válida
    se salta: la ventana ya avisa al escribirla, y así una conversión por lotes no se para."""
    rules = []
    for find, new in replacements:
        if not find:
            continue
        try:
            regex = find.startswith("re:")
            rules.append((rule_pattern(find), html.escape(new, quote=False), regex))  # "\1" no cambia al escapar
        except re.error:
            continue
    return rules


def count_matches(book, replacements):
    """Cuántas veces aplica cada regla en los párrafos y títulos del libro: [(buscar, veces o error)]."""
    texts = [b.html for b in book.blocks if b.kind in ("p", "heading")]
    counts = []
    for find, _ in replacements:
        try:
            pattern = rule_pattern(find)
        except re.error as e:
            counts.append((find, f"expresión no válida: {e}"))
            continue
        counts.append((find, sum(len(pattern.findall(part)) for t in texts for part in re.split(r"<[^>]+>", t))))
    return counts


SAFE = re.compile(r"(</?[ib]>|\[Nota: )")  # marcas del editor que un reemplazo no debe romper


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
INLINE = re.compile(r"&lt;(/?)([ib])&gt;")
NOTE_MARK = re.compile(r"\[Nota: (.+?)\]")  # nota al pie en el editor de texto
# Enlace escrito en el editor, ya escapado: solo direcciones web (un javascript: no sirve en un libro)
LINK_MARK = re.compile(r'&lt;a href="(https?://[^"\s<>]+)"&gt;(.*?)&lt;/a&gt;')
CENTER = "<center>"  # al principio de una línea del editor: párrafo centrado (carta, cartel)


def section_text(book, start, end):
    """Texto editable de los bloques start..end-1: un párrafo por línea (con <i>/<b>), "# " delante
    de un título, y las ilustraciones y separadores como marcas "[Ilustración N]" / "[Separador N]"
    (N, el índice del bloque) para poder moverlos o borrarlos."""
    return "\n\n".join(_line(book.blocks[k], k) for k in range(start, end))


def _line(b, k):
    """Línea de section_text del bloque b, que es el número k del libro."""
    if b.kind == "p":
        return (CENTER if b.align == "center" else "") + html.unescape(NOTE.sub(r"[Nota: \1]", b.html))
    if b.kind == "heading":
        return "# " + html.unescape(b.html)
    return f"[{'Ilustración' if b.kind == 'image' else 'Separador'} {k}]"


def _balanced(out):
    stack = []
    for close, tag in re.findall(r"<(/?)([ib])>", out):
        if not close:
            stack.append(tag)
        elif not stack or stack.pop() != tag:
            return False
    return not stack


def _inline(text):
    """Línea escrita por el usuario a XHTML: escapada, salvo <i> y <b>, que se conservan si están bien
    cerradas; si no, se quitan para no romper el EPUB. "[Nota: …]" vuelve a ser la nota al pie, si
    dentro las etiquetas también cierran (si no, cruzarían el límite de la nota)."""
    out = INLINE.sub(r"<\1\2>", html.escape(text, quote=False))
    out = out if _balanced(out) else TAGS.sub("", out)
    out = LINK_MARK.sub(lambda m: f'<a href="{m[1]}">{m[2]}</a>' if _balanced(m[2]) else m[0], out)
    return NOTE_MARK.sub(lambda m: f'<span class="nota">{m[1]}</span>' if _balanced(m[1]) else m[0], out)


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
    new, kept = [], {}
    for line in (line.strip() for line in text.splitlines()):
        if not line:
            continue
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
        else:
            new.append(Block("p", page, _inline(line)))
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


def name_variants(book):
    """Nombres escritos de varias formas ("Greg-sama" / "Greg sama", "Tino" / "Tiño"):
    [[forma más usada, otras…]], los más frecuentes primero. Solo palabras con mayúscula que nunca
    salen en minúscula ("Él" / "El", "Tú" / "Tu") y que alguna vez van con mayúscula en mitad de una
    frase, como los nombres propios ("Utilizó" / "Utilizo" al empezar frase, "Ja-ja" / "Jaja", no)."""
    text = " ".join(html.unescape(TAGS.sub("", b.html)) for b in book.blocks if b.kind in ("p", "heading"))
    tokens = re.findall(r"[^\W\d_]+(?:-[^\W\d_]+)*", text)
    common = {_fold(t) for t in tokens if t[:1].islower()}
    proper = {_fold(re.split(r"[- ]", m.group(1))[0]) for m in re.finditer(r"(?<=[\w,;]) ([^\W\d_][\w-]*)", text)
              if m.group(1)[:1].isupper()}
    forms = collections.Counter(t for t in tokens if t[:1].isupper() and not (len(t) > 1 and t.isupper()))
    forms.update(f"{a} {b}" for a, b in zip(tokens, tokens[1:]) if a[:1].isupper() and b.lower() in HONORIFICS)
    groups = collections.defaultdict(dict)
    for form, n in forms.items():
        key = _fold(form)
        if len(key) >= 3 and key not in common:
            groups[key][form] = n
    found = [sorted(g, key=lambda f: -g[f]) for g in groups.values()
             if len(g) > 1 and any(_fold(re.split(r"[- ]", f)[0]) in proper for f in g)]
    return sorted(found, key=lambda g: -sum(forms[f] for f in g))


SPELL_DIR = Path(__file__).parent / "dict"  # Hunspell es_ES de LibreOffice (RLA-ES); en el .exe, junto a los módulos


@functools.lru_cache(maxsize=1)
def _hunspell():
    from spylls.hunspell import Dictionary  # tarda unos 4 s: solo cuando se pide la ortografía
    return Dictionary.from_files(str(SPELL_DIR / "es_ES"))


def unknown_words(texts, known=()):
    """Palabras de texts que no están en el diccionario: [(palabra, veces, [sugerencias])], las más repetidas
    primero. Solo en minúscula y de 3 letras o más: con mayúscula suelen ser nombres. No cuentan known (el
    diccionario de la serie), los honoríficos ni las onomatopeyas ("aaah"). Se sugieren palabras correctas
    del propio libro, primero la que solo cambia en tildes: las de Hunspell tardan ~1 s cada una."""
    words = collections.Counter(w for t in texts for w in re.findall(r"[^\W\d_]+", unicodedata.normalize("NFC", t)))
    skip = {w.lower() for w in known} | HONORIFICS
    spell, good, bad = _hunspell(), [], []
    for w, n in words.items():
        if w[:1].islower() and len(w) >= 3 and w not in skip and not re.search(r"(.)\1\1", w):
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


LINKS = re.compile(r"https?://|www\.|facebook|twitter|discord|patreon|ko-fi|paypal|instagram|tiktok|wixsite"
                   r"|blogspot", re.I)


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


def _letters(text):
    return sum(c.isalpha() for c in text)


def _plain(block):
    return html.unescape(TAGS.sub("", block.html))


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
SENTENCE = re.compile(r"((?:\.{2,}|[^.!?])*?)([?!]+)")
LEAD = re.compile(r"(?:\s|[«»“”\"'(—–-]|</?[ib]>)*")   # lo que va antes de la primera palabra
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
        (r"(?<![.…])(?<![.…] )\.\s+\.(?!\s*[.…])", ".", (". .", ".  ."))),  # ". ." suelto; ". . ." son puntos suspensivos
     "Dos signos donde va uno: «,,», «?.», «….», «. .». Tras los puntos suspensivos o el cierre de pregunta "
     "o exclamación no va punto.", "Corregirlo."),
    ("dialog", "Puntuación del inciso del narrador", _subs(
        # Mitsuha: «—Sí, — respondió el soldado, —un buque…» -> «—Sí —respondió el soldado—, un buque…»
        (r",\s*[—―─–]\s*([a-záéíóúñ][^—―─–.!?]*?),\s*[—―─–]\s*", r" —\1—, ", DASHES),
        (r"([^\W\d_])[.,]\s*[—―─–]\s*(?=[a-záéíóúñ])", _inciso, DASHES)),
     "En un diálogo, el punto o la coma no van antes del inciso del narrador sino tras su raya de cierre: "
     "«—Sí —respondió—, vamos.»", "Corregirlo."),
    ("closing", "Falta el signo de cierre", _subs(
        # «80.000», «G.I.», no; ni con una comilla o un paréntesis antes del punto: el signo falta dentro
        # («¡Esa sí que es una gran noticia,” celebró…», Mitsuha) y no sabemos dónde
        (r"([¿¡])([^¿¡?!…—―─.”»\"')]*?)([^\W\d_]*)\.(?![.…\w])", _close)),
     "La pregunta o la exclamación se abre con ¿ o ¡ pero acaba en punto: le falta su signo de cierre.",
     "Corregirlo."),
)


@functools.lru_cache(maxsize=20000)
def _typos(line):
    """Las erratas de TYPOS de una línea: ((índice en TYPOS, línea arreglada)…). Recordadas por línea: tras un
    arreglo, el informe solo vuelve a mirar los párrafos que cambiaron."""
    found = []
    for n, (_, _, rule, _, _) in enumerate(TYPOS):
        fixed = rule(line)
        # Lo que la limpieza tipográfica del EPUB ya arregla («— dijo» -> «—dijo»), no se avisa
        if fixed != line and _typography(_inline(fixed)) != _typography(_inline(line)):
            found.append((n, fixed))
    return tuple(found)


def quality_report(book):
    """Lo sospechoso del libro, para revisarlo: [Issue], por página. Cada aviso dice el problema, la
    solución y, si se puede arreglar solo, el cambio para edit_section y su vista previa."""
    blocks = book.blocks
    report = [Issue("skipped", None, page, "Grave", f"Texto no incluido ({letters} letras)",
                    f"La página {page} tiene texto que no entró en el libro: {why}.",
                    "Mira la página en la vista previa del PDF. Si era texto de la novela, cópialo en su "
                    "capítulo con «Editar texto…» (tecla E). Si era un índice o créditos, ignora el aviso.")
              for page, letters, why in book.skipped if letters >= 20]
    lines = collections.Counter(_plain(b).strip() for b in blocks if b.kind == "p")
    heads = [k for k, b in enumerate(blocks) if b.kind == "heading"] + [len(blocks)]
    drop = (lambda k: (k, k + 1, ""))
    prev_text = None
    for k, b in enumerate(blocks):
        if b.kind != "p":
            prev_text = None if b.kind == "heading" else prev_text
            continue
        text = _plain(b).strip()
        if text[:1].islower():
            # El párrafo anterior, saltando ilustraciones y separadores (la página se cortó con una imagen)
            j = next((j for j in range(k - 1, -1, -1) if blocks[j].kind in ("p", "heading")), None)
            head = _plain(blocks[j]).strip() if j is not None and blocks[j].kind == "p" else ""
            before = _cut(head, tail=True) + " ¶\n" + _cut(text) if head else _cut(text)
            problem = ("Empieza en minúscula: suele ser el final de la frase del párrafo anterior, cortada por "
                       "un cambio de página o una ilustración.")
            if head and not head.endswith(END_PUNCT):
                between = [_line(blocks[i], i) for i in range(j + 1, k)]  # se quedan tras el párrafo unido
                edit = (j, k + 1, "\n\n".join([_line(blocks[j], j).rstrip() + " " + _line(b, k).lstrip()] + between))
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
        if re.fullmatch(r"\W*\d+\W*", text):
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
        if LINKS.search(text):
            short = len(text) < 200  # un aviso del grupo; uno largo puede ser texto de la novela con un nombre
            report.append(Issue("link", k, b.page, "Menor", "Enlace o red social",
                                "Enlaces y redes del grupo de traducción: en un lector no se pueden abrir.",
                                "Quitarlo." if short else "Quita el enlace con «Editar texto…» (tecla E).",
                                _cut(text), "", drop(k) if short else None, text))
        if "�" in text or MOJIBAKE.search(text):
            m = MOJIBAKE.search(text) or re.search("�", text)
            fixed = _remojibake(_line(b, k)) if MOJIBAKE.search(text) else None
            report.append(Issue("broken", k, b.page, "Grave", "Caracteres rotos",
                                "Hay letras que el PDF guardó mal (tildes o eñes cambiadas por símbolos).",
                                "Corregirlas." if fixed else "Corrígelas con «Editar texto…» (tecla E) o con "
                                "«Reemplazos…» si se repiten en todo el libro.",
                                _around(text, m.start(), m.end()),
                                _around(_remojibake(text) or "", m.start(), m.end()) if fixed else "",
                                (k, k + 1, fixed) if fixed else None, text))
        words = re.findall(r"[a-záéíóúñü']+", text.lower())
        english = sum(w in ENGLISH for w in words)
        if len(words) >= 6 and english >= 0.35 * len(words) and sum(w in SPANISH for w in words) < english:
            report.append(Issue("english", k, b.page, "Revisar", "Texto sin traducir",
                                "El párrafo parece estar en inglés: el traductor se lo saltó o es una nota del "
                                "grupo.", "Tradúcelo o quítalo con «Editar texto…» (tecla E).", _cut(text),
                                text=text))
        line = _line(b, k)
        for t, fixed in _typos(line) if not LINKS.search(text) else ():
            kind, title, _, why, solution = TYPOS[t]
            before, after = TAGS.sub("", line), TAGS.sub("", fixed)
            n = len(os.path.commonprefix([before, after]))
            report.append(Issue(kind, k, b.page, "Menor", title, why, solution,
                                _around(before, n, n), _around(after, n, n + len(after) - len(before)),
                                (k, k + 1, fixed), text))
        m = next((m for m in CAPITAL.finditer(text) if m[1].lower() not in ABBREVIATIONS), None)
        if m and not LINKS.search(text):
            report.append(Issue("capital", k, b.page, "Menor", "Minúscula tras un punto",
                                "Tras un punto la frase nueva empieza en mayúscula. Si la frase sigue, el punto "
                                "sobra: suele quedar de un salto de línea del PDF.",
                                "Compáralo con el PDF y corrígelo con «Editar texto…» (tecla E): quita el punto o "
                                "pon la mayúscula.", _around(text, m.start(), m.end()), text=text))
        nxt = _plain(blocks[k + 1]).strip() if k + 1 < len(blocks) and blocks[k + 1].kind == "p" else ""
        if (len(text) >= 40 and re.search(r"[^\W\d_]$", text) and nxt[:1].isupper() and not LINKS.search(text)
                and not LABEL.match(text.strip()) and not LABEL.match(nxt)):
            report.append(Issue("end", k, b.page, "Menor", "Falta el punto final",
                                "El párrafo acaba sin punto y el siguiente empieza en mayúscula: falta el punto, "
                                "o el párrafo está partido y el siguiente sigue la frase.",
                                "Compáralo con el PDF y corrígelo con «Editar texto…» (tecla E): pon el punto o "
                                "une los dos párrafos.", _cut(text, tail=True) + " ¶\n" + _cut(nxt), text=text))
        prev_text = text
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
    return sorted(report, key=lambda r: (r.page, r.block if r.block is not None else -1))


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
    merged = replace(first, title=title, blocks=blocks, candidates=candidates, volume="", skipped=[])
    return merged, chapters, removed, breaks


FONTS = {"Literata": "literata", "Bitter": "bitter"}  # tipografías libres (OFL) en la carpeta fonts
FONT_DIR = Path(__file__).parent / "fonts"  # en el .exe, junto a los módulos (--add-data fonts;fonts)


def analyze(pdf_path, progress=None):
    """Lee el PDF (o un EPUB, que PyMuPDF compone en páginas) y lo devuelve como bloques, con los títulos de
    capítulo candidatos."""
    kind = "EPUB" if str(pdf_path).lower().endswith(".epub") else "PDF"
    try:
        doc = pymupdf.open(pdf_path)
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

        # Título por tamaño o negrita, o porque el PDF lo marca: hay subtítulos del tamaño del texto
        # ("El lado de la bruja") que solo se distinguen por su marcador de nivel 2.
        bookmarks = collections.defaultdict(set)
        for _, title, p in doc.get_toc():
            if 1 <= p <= n and _key(title):
                bookmarks[p - 1].add(_key(title))
        for i, rows in enumerate(pages):
            for r in rows:
                key = _key(r.text)
                r.heading = _is_heading(r, body) or any(
                    key == mark or (r.bold and len(key) >= 4 and key in mark) for mark in bookmarks[i])
                r.starts = len(key) >= 4 and any(mark.startswith(key) for mark in bookmarks[i])

        uses = collections.Counter(img[0] for page in doc for img in page.get_images())  # xref -> páginas
        # Primera página con un capítulo: lo anterior son preliminares (portadilla, créditos, logos)
        first_chapter = next((i for i, rows in enumerate(pages) if rows and not _is_toc(rows) and any(
            r.heading and KEYWORDS.match(r.text.strip(LABEL_DECOR)) for r in rows)), 0)  # sin capítulos: nada
        blocks, skipped = [], []
        para, short, last_text = None, False, ""
        toc = False  # la página anterior era el índice
        repeated = ""  # título que la página anterior ya emitió junto a su ilustración
        digests = {}
        for i, (page, rows) in enumerate(zip(doc, pages)):
            num = i + 1
            if progress:
                progress(n + num, 2 * n)
            pending, repeated = repeated, ""
            headings = [r for r in rows if r.heading]
            infos = _image_info(page, digests)
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
                continue
            toc = _is_toc(rows) or (toc and _links_only(page, rows))
            if toc:  # se apunta: si no era el índice, se habría perdido una página de texto
                skipped.append((num, sum(map(_letters, (r.text for r in rows))), "se omitió como índice del PDF"))
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
            normal = [r for r in rows if not r.heading]
            # Ilustración con el texto de la novela encima: se guardan las dos cosas, la imagen tras el texto
            # de la página (es la escena que se lee ahí, Maryoku 5). El párrafo abierto sigue abierto, así que
            # su continuación se une a él aunque la imagen quede en medio.
            art = _illustration(images, uses) if normal else None
            left = min((r.x0 for r in normal), default=0)
            right = max((r.x1 for r in normal), default=0)
            prev = None
            ornaments = _ornaments(page, rows, infos)  # adornos-separador, de arriba abajo
            for r in rows:
                while ornaments and ornaments[0][0] < r.y:  # el adorno queda por encima de esta línea
                    xref = ornaments.pop(0)[1]
                    if blocks and blocks[-1].kind != "break":  # guarda el adorno, por si se quiere el original
                        blocks.append(Block("break", num, image=_extract(doc, xref) if xref else b""))
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
                        blocks.append(Block("heading", num, html.escape(r.text), rect=_rect([r])))
                    para = None
                else:
                    indented = r.x0 > left + body * 0.8
                    # Línea centrada (carta, cartel, aviso): lejos de los dos márgenes y con el centro en el
                    # del texto. La última línea corta de un párrafo empieza en el margen: no cuenta.
                    width = right - left
                    centered = (width > 0 and r.x0 > left + width * 0.1 and r.x1 < right - width * 0.1
                                and abs((r.x0 + r.x1) / 2 - (left + right) / 2) < width * 0.05)
                    if para is None or indented or short or centered:
                        new = True
                    elif prev is None:  # primera línea de la página: ¿sigue el párrafo anterior?
                        new = not indent_style and last_text.endswith(END_PUNCT)
                    else:
                        new = r.y - prev.y > line_gap * 1.3
                    if new and para is not None and r.text[:1].islower() and not last_text.endswith(END_PUNCT):
                        new = False  # algunos PDF ponen con sangría el final de una frase sin acabar
                    if new:
                        para = Block("p", num, r.html, align="center" if centered else "", rect=_rect([r]))
                        blocks.append(para)
                    else:
                        para.html = _join(para.html, r.html)
                        if para.page == num:  # el recuadro, solo en la página donde empieza el párrafo
                            para.rect = _rect([para, r])
                    short = r.x1 < right - (right - left) * 0.2
                    last_text = r.text
                prev = r
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

        outline = [(title, p) for level, title, p in doc.get_toc() if level == 1]
        if outline:
            chosen = set()
            for title, p in outline:
                k = next((k for k, b in enumerate(blocks) if b.kind == "heading" and b.page == p), None)
                if k is None:
                    k = next((k for k, b in enumerate(blocks) if b.page >= p), len(blocks))
                    blocks.insert(k, Block("heading", p, html.escape(title)))
                chosen.add(id(blocks[k]))
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
        # "Parte 1", "Parte 2"… sin nombre y con la numeración que vuelve a empezar en cada capítulo
        # (Re Zero) son apartados del capítulo: subtítulos. "Parte uno: Stifled Shadow" sí es una parte.
        bare = [k for k in candidates if SUBPART.fullmatch(html.unescape(blocks[k].html).strip())]
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
                    proofreader=credits.get("proofreader", ""), editor=credits.get("editor", ""), skipped=skipped)


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
            f'<head><meta charset="utf-8"/><title>{html.escape(title)}</title>'
            '<link rel="stylesheet" type="text/css" href="style.css"/></head>\n'
            f'<body>\n{body}\n</body>\n</html>\n')


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


def _file_as(author):
    """Autor para ordenar: "Tappei Nagatsuki" -> "Nagatsuki, Tappei" (si ya lleva coma, tal cual)."""
    words = author.split()
    return author if "," in author or len(words) < 2 else f"{words[-1]}, {' '.join(words[:-1])}"


STATUS = re.compile(r"\s*[【\[].*[】\]]\s*")  # "【Habilidad adquirida】", "[Nivel 5]": ventana de estado


def write_epub(book, chapters, out_path, lang="es", removed=(), typography=False, style="Clásico", contents=True,
               separator="Según el estilo", shrink=False, replacements=(), credits=False, gray=False,
               breaks=(), trim=False, font="", epub2=False, reading=False, kepub=False):
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
    Cada página del PDF deja una marca invisible (page-list): el lector puede mostrar su número."""
    rules = _rules(replacements)
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
    subtitles = {}  # archivo -> [(ancla, título escapado)]: subentradas del índice
    pages, marked = [], set()  # [(archivo, página)] de la lista de páginas; páginas ya marcadas
    noted = 0  # notas al pie escritas: numeración seguida en todo el libro, ids únicos
    for n, (title, is_chapter, blocks) in enumerate(sections, 1):
        name = f"sec{n:03d}.xhtml"
        parts = [_chapter_heading(fix(html.escape(title)))] if is_chapter else []
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

        for k, b in blocks:
            if b.page not in marked:
                marked.add(b.page)
                pending = b.page
            if b.kind == "image":
                for i, data in enumerate(_spread(b.image, b.layout) if b.layout else [b.image]):
                    ext = "png" if data[:4] == b"\x89PNG" else "jpg"  # PNG si trae transparencia
                    src = f"img/b{k:05d}{'_' + str(i) if b.layout else ''}.{ext}"  # por bloque: una página puede tener varias
                    images[src] = data
                    parts.append(f'<div class="img"><img src="{src}" alt=""/></div>')
            elif b.kind == "break" or k in breaks:
                parts.append(_separator(b if b.kind == "break" else Block("break", b.page), k, style, separator, images))
            elif b.kind == "heading":  # título que no es capítulo: subtítulo ("El lado de la bruja")
                parts.append(f'{mark()}<h2 id="t{k}">{fix(b.html)}</h2>')  # fuera: el título queda limpio
                subtitles.setdefault(name, []).append((f"t{k}", fix(b.html)))
            else:
                css = "status" if STATUS.fullmatch(_plain(b)) else "center" if b.align else ""
                attr = f' class="{css}"' if css else ""
                parts.append(f"<p{attr}>{mark()}{NOTE.sub(noteref, fix(b.html, dashes=True))}</p>")
        # Los lectores con notas emergentes (Kobo, Kindle, Apple Books) ocultan el aside y lo muestran
        # al tocar la llamada; los demás lo dejan al final de la sección, con enlace de vuelta.
        tag, kind = ("div", "") if epub2 else ("aside", ' epub:type="footnote"')
        parts += [f'<{tag}{kind} class="nota" id="nota{k}"><p><a href="#ref{k}">{k}</a>. {text}</p></{tag}>'
                  for k, text in notes]
        files[name] = _xhtml(title, "\n".join(parts), lang)

    uid = f"urn:uuid:{uuid.uuid4()}"
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
        meta.append(f"<dc:description>{html.escape(BAD_XML.sub('', book.description))}</dc:description>")
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
        items.append(f'<li><a href="{name}">{fix(html.escape(t))}</a>{children}</li>')
        minutes = reading_minutes([b for _, b in sec[2]]) if reading else 0
        extra = f' <span class="min">· {minutes} min</span>' if minutes else ""
        shown.append(f'<li><a href="{name}">{fix(html.escape(t))}</a>{extra}{children}</li>')
        order += 1  # playOrder: el capítulo antes que sus subtítulos, todos correlativos
        point = (f'<navPoint id="n{order}" playOrder="{order}"><navLabel><text>{fix(html.escape(t))}</text></navLabel>'
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
            z.writestr("OEBPS/content.opf", opf)
            z.writestr("OEBPS/nav.xhtml", nav)
            if contents:
                z.writestr("OEBPS/contents.xhtml", contents_page)
            if credits:
                z.writestr("OEBPS/credits.xhtml", credits_page)
            z.writestr("OEBPS/toc.ncx", ncx)
            z.writestr("OEBPS/style.css", css)
            z.writestr("OEBPS/cover.xhtml", cover)
            z.writestr("OEBPS/cover.jpg", _shrink(book.cover, limit) if limit and book.cover else book.cover)
            for name, text in files.items():
                z.writestr(f"OEBPS/{name}", _kepub(text) if kepub else text)
            for src, data in images.items():
                data = _trim(data) if trim else data
                data = _gray(data) if gray else data
                z.writestr(f"OEBPS/{src}", _shrink(data, limit) if limit else data)
            for src, path in fonts.items():
                z.write(path, f"OEBPS/{src}")
        os.replace(tmp, out_path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


XHTML = "{http://www.w3.org/1999/xhtml}"
PART_TITLE = re.compile(r"(parte|volumen|libro|part|book)\b", re.I)  # portadillas: sin texto a propósito


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
