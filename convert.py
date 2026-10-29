"""Convierte PDFs de novelas ligeras en EPUB con texto fluido."""
import collections
import functools
import html
import os
import re
import unicodedata
import uuid
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

import pymupdf

BAND = 0.12     # franja superior/inferior (fracción de la altura) donde viven encabezados y pies
REPEAT = 0.30   # fracción de páginas en que una línea debe repetirse para ser encabezado o pie
KEYWORDS = re.compile(
    r"(cap[ií]tulo|pr[oó]logo|ep[ií]logo|interludio|historia paralela|palabras de cierre"
    r"|extra|parte|chapter|prologue|epilogue|afterword|interlude)\b", re.I)
TOC_TITLE = re.compile(r"(contenidos?|[ií]ndice|tabla de contenidos?|contents|table of contents):?", re.I)
DOT_LEADER = re.compile(r"[.\-_·…]{4,}\s*(?:p[áa]g\.?\s*)?\d+\s*\|?$", re.I)  # "..... 12", "---- Pág. 54"
SCENE_BREAK = re.compile(r"[\s☆★*＊◆◇●○■□♦♢※✦✧❖◈•·#~◎◯]+")
END_PUNCT = (".", "!", "?", "…", '"', "'", "»", "”", "’", ":", ")", "]")  # "]": notas del traductor
SOFT_HYPHEN = "\u00ad"  # guion de partición: solo se ve si cae al final de la línea
TAGS = re.compile(r"<[^>]+>")
BAD_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")


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


def _row(spans):
    spans.sort(key=lambda s: s["origin"][0])
    texts, parts = [], []
    for k, (prev, s) in enumerate(zip([None] + spans, spans)):
        t = BAD_XML.sub("", s["text"])
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
    para una ilustración apaisada (Re Zero 6), que quedarían en mitad de la página como títulos."""
    spans = [s for b in page.get_text("dict")["blocks"] for line in b.get("lines", []) if abs(line["dir"][0]) > 0.9
             for s in line["spans"] if s["text"].strip()]
    rows, group = [], []
    for s in sorted(spans, key=lambda s: s["origin"][1]):
        if group and abs(s["origin"][1] - group[0]["origin"][1]) > 2:
            rows.append(_row(group))
            group = []
        group.append(s)
    if group:
        rows.append(_row(group))
    return rows


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
        label = _label(row) if row.heading else None
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
    se calcula desde la línea base y el tamaño de letra."""
    boxes = [r.box or (r.x0, r.y - r.size, r.x1, r.y + r.size * 0.3) for r in rows]
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def _title(rows):
    """Clave del primer título de la página, para comparar páginas."""
    heads = [r for r in rows if r.heading]
    return _key(" ".join(r.text for r in _first_group(heads))) if heads else ""


def _image_info(page, digests):
    """Imágenes dibujadas en la página, con su xref. get_image_info(xrefs=True) decodifica cada imagen
    de la página en cada llamada para sacar su huella; digests (xref -> huella) la guarda para todo el PDF.
    Copias, con bbox en coordenadas de la página girada: PyMuPDF guarda las suyas para _render."""
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
    return pix.tobytes("jpg", jpg_quality=85)


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
    return page.get_pixmap(dpi=150, clip=clip).tobytes("jpg", jpg_quality=85)


def load_cover(path):
    """Imagen de portada desde un archivo, en JPEG: el EPUB declara la portada como cover.jpg."""
    try:
        data = Path(path).read_bytes()
        if data[:3] == b"\xff\xd8\xff":
            return data
        pix = pymupdf.Pixmap(data)
    except Exception as e:
        raise ValueError(f"No se pudo abrir la imagen: {e}") from e
    return _rgb(pix, alpha=False).tobytes("jpg", jpg_quality=90)


SHRINK = 1400  # lado mayor de las imágenes reducidas, en píxeles: de sobra para un celular o un e-reader


def _shrink(data, limit=SHRINK):
    """Imagen más ligera: lado mayor de limit píxeles como mucho y JPEG de calidad 75. Con
    transparencia sigue en PNG. Si no gana nada, devuelve la original."""
    pix = pymupdf.Pixmap(data)
    scale = limit / max(pix.width, pix.height)
    if scale < 1:
        pix = pymupdf.Pixmap(pix, round(pix.width * scale), round(pix.height * scale), None)
    small = pix.tobytes("png") if pix.alpha else pix.tobytes("jpg", jpg_quality=75)
    return small if len(small) < len(data) else data


def _gray(data):
    """Imagen en escala de grises: en un e-reader de tinta se ve igual y pesa menos."""
    pix = pymupdf.Pixmap(data)
    if pix.n - pix.alpha == 1:
        return data
    pix = pymupdf.Pixmap(pymupdf.csGRAY, pix)
    return pix.tobytes("png") if pix.alpha else pix.tobytes("jpg", jpg_quality=85)


@functools.lru_cache(maxsize=1024)  # la lista lo pregunta en cada edición: decodificar costaba 2 s (Kage 5)
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
            return [page.get_pixmap().tobytes("jpg", jpg_quality=90)]
        w, h = page.rect.width, page.rect.height
        return [page.get_pixmap(clip=pymupdf.Rect(x0, 0, x0 + w / 2, h)).tobytes("jpg", jpg_quality=90)
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


TOKEN = re.compile(r"\[(Ilustración|Separador) (\d+)\]")  # marcas del editor de texto
INLINE = re.compile(r"&lt;(/?)([ib])&gt;")


def section_text(book, start, end):
    """Texto editable de los bloques start..end-1: un párrafo por línea (con <i>/<b>), "# " delante
    de un título, y las ilustraciones y separadores como marcas "[Ilustración N]" / "[Separador N]"
    (N, el índice del bloque) para poder moverlos o borrarlos."""
    return "\n\n".join(_line(book.blocks[k], k) for k in range(start, end))


def _line(b, k):
    """Línea de section_text del bloque b, que es el número k del libro."""
    if b.kind == "p":
        return html.unescape(b.html)
    if b.kind == "heading":
        return "# " + html.unescape(b.html)
    return f"[{'Ilustración' if b.kind == 'image' else 'Separador'} {k}]"


def _inline(text):
    """Línea escrita por el usuario a XHTML: escapada, salvo <i> y <b>, que se conservan si están bien
    cerradas; si no, se quitan para no romper el EPUB."""
    out = INLINE.sub(r"<\1\2>", html.escape(text, quote=False))
    stack = []
    for close, tag in re.findall(r"<(/?)([ib])>", out):
        if not close:
            stack.append(tag)
        elif not stack or stack.pop() != tag:
            return TAGS.sub("", out)
    return TAGS.sub("", out) if stack else out


def edit_section(book, start, end, text):
    """Cambia los bloques start..end-1 por el texto editado (formato de section_text). Las marcas
    recuperan su ilustración o separador; "# Título" crea un título; una línea de solo símbolos
    ("* * *") crea un separador. Ajusta book.candidates y devuelve {índice viejo: índice nuevo} de los
    bloques que siguen en el libro (los párrafos se sustituyen: no están)."""
    old = book.blocks
    page = old[start - 1].page if start else 1
    # Una línea sin cambios conserva la página de su párrafo; la cambiada o nueva, la del anterior
    pages = {}
    for k in range(start, end):
        pages.setdefault(_line(old[k], k).strip(), old[k].page)
    new, kept = [], {}
    for line in (line.strip() for line in text.splitlines()):
        if not line:
            continue
        page = pages.get(line, page)
        m = TOKEN.fullmatch(line)
        k = int(m.group(2)) if m else -1
        if m and start <= k < end and k not in kept and old[k].kind == ("image" if m.group(1) == "Ilustración" else "break"):
            kept[k] = start + len(new)
            new.append(old[k])
            page = old[k].page
        elif line.startswith("# ") and line[2:].strip():
            new.append(Block("heading", page, _inline(line[2:].strip())))
        elif SCENE_BREAK.fullmatch(line):
            new.append(Block("break", page, html.escape(line, quote=False)))
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
        return page.get_pixmap(clip=pymupdf.Rect(left, top, right, bottom)).tobytes("jpg", jpg_quality=90)


def quality_report(book):
    """Lo sospechoso del libro, para revisarlo: [(índice de bloque o None, página, motivo, texto)]."""
    report = [(None, page, f"Texto no incluido: {why} ({letters} letras)", "")
              for page, letters, why in book.skipped if letters >= 20]
    lines = collections.Counter(_plain(b).strip() for b in book.blocks if b.kind == "p")
    heads = [k for k, b in enumerate(book.blocks) if b.kind == "heading"] + [len(book.blocks)]
    for k, b in enumerate(book.blocks):
        if b.kind != "p":
            continue
        text = _plain(b).strip()
        if text[:1].islower():
            report.append((k, b.page, "Empieza en minúscula (¿párrafo partido?)", text))
        if len(text) > 3000:
            report.append((k, b.page, "Párrafo muy largo (¿párrafos unidos?)", text))
        if re.fullmatch(r"\W*\d+\W*", text):
            report.append((k, b.page, "Número suelto (¿número de página?)", text))
        elif len(text) < 60 and lines[text] >= 3 and text[:1].isalnum() and not text.endswith(END_PUNCT):
            # "Einherjar Project" sí; un diálogo repetido ("¿Eh?", "Ya veo.") empieza con signo o acaba en punto
            report.append((k, b.page, f"Línea repetida {lines[text]} veces (¿encabezado o pie?)", text))
        if LINKS.search(text):
            report.append((k, b.page, "Enlace o red social", text))
    for k, end in zip(heads, heads[1:]):
        if k in book.candidates:
            nxt = next((j for j in heads if j > k and j in book.candidates), len(book.blocks))
            letters = sum(_letters(_plain(b)) for b in book.blocks[k + 1:nxt] if b.kind == "p")
            if letters < 300 and not PART_TITLE.match(_plain(book.blocks[k])):
                report.append((k, book.blocks[k].page, f"Capítulo muy corto ({letters} letras)", _plain(book.blocks[k])))
    return sorted(report, key=lambda r: (r[1], r[0] if r[0] is not None else -1))


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
    """Lee el PDF y lo devuelve como bloques, con los títulos de capítulo candidatos."""
    try:
        doc = pymupdf.open(pdf_path)
    except Exception as e:
        raise ValueError(f"No se pudo abrir el PDF: {e}") from e
    with doc:
        if doc.needs_pass:
            raise ValueError("El PDF está protegido con contraseña.")
        n = doc.page_count
        pages = []
        for i, page in enumerate(doc):
            pages.append(_rows(page))
            if progress:
                progress(i + 1, 2 * n)
        pages = _strip_margins(pages, [page.rect.height for page in doc])
        if not any(pages):
            raise ValueError("El PDF no tiene texto (parece escaneado). Hace falta OCR, que no está soportado.")

        sizes = collections.Counter(r.size for rows in pages for r in rows for _ in r.text)
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
            if _is_toc(rows):  # se apunta: si no era el índice, se habría perdido una página de texto
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
            start = len(blocks)
            normal = [r for r in rows if not r.heading]
            # Ilustración con el texto de la novela encima: se guardan las dos cosas. El párrafo abierto
            # sigue abierto, así que su continuación se une a él aunque la imagen quede en medio.
            art = _illustration(images, uses) if normal else None
            if art:
                blocks.append(Block("image", num, image=_extract(doc, art)))
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
                if r.heading:
                    # Se une a la línea anterior si es el mismo título partido: mismo tamaño y pegada
                    # (un subtítulo justo debajo del capítulo queda más lejos, o lo abre un marcador).
                    if (prev is not None and prev.heading and abs(prev.size - r.size) < 0.5
                            and r.y - prev.y < 2 * r.size and not r.starts):
                        blocks[-1].html += " " + html.escape(r.text)
                        a, b = blocks[-1].rect, _rect([r])  # el rectángulo abarca todas sus líneas
                        blocks[-1].rect = (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))
                    else:
                        blocks.append(Block("heading", num, html.escape(r.text), rect=_rect([r])))
                    para = None
                elif SCENE_BREAK.fullmatch(r.text):
                    blocks.append(Block("break", num, r.html))  # guarda el texto original ("☆", "* * *")
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
                        para = Block("p", num, r.html, align="center" if centered else "")
                        blocks.append(para)
                    else:
                        para.html = _join(para.html, r.html)
                    short = r.x1 < right - (right - left) * 0.2
                    last_text = r.text
                prev = r
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
               breaks=(), trim=False, font="", epub2=False, reading=False):
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
    trim: recorta el margen blanco de las ilustraciones (_trim). font: una de FONTS, incrustada.
    epub2: paquete EPUB 2 para lectores antiguos. reading: minutos de lectura en la página "Contenido".
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
    for n, (title, is_chapter, blocks) in enumerate(sections, 1):
        name = f"sec{n:03d}.xhtml"
        parts = [_chapter_heading(fix(html.escape(title)))] if is_chapter else []
        pending = None  # página que empieza en este tramo: su marca va en el siguiente párrafo o título

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
                parts.append(f"<p{attr}>{mark()}{fix(b.html, dashes=True)}</p>")
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
                z.writestr(f"OEBPS/{name}", text)
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
            if target and (base + target not in names or (anchor and anchor not in ids.get(base + target, ()))):
                problems.append(f"{name[len(base):]}: enlace roto a {a.get('href')}")
        h1 = next(tree.iter(XHTML + "h1"), None)
        if (re.fullmatch(r"OEBPS/sec\d+\.xhtml", name) and h1 is not None
                and not any(True for _ in tree.iter(XHTML + "p")) and not any(True for _ in tree.iter(XHTML + "img"))):
            title = " ".join("".join(h1.itertext()).split())
            if not PART_TITLE.match(title):
                problems.append(f"Capítulo vacío: {title}")
    return problems
