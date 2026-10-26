"""Convierte PDFs de novelas ligeras en EPUB con texto fluido."""
import collections
import html
import os
import re
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pymupdf

BAND = 0.12     # franja superior/inferior (fracción de la altura) donde viven encabezados y pies
REPEAT = 0.30   # fracción de páginas en que una línea debe repetirse para ser encabezado o pie
KEYWORDS = re.compile(
    r"(cap[ií]tulo|pr[oó]logo|ep[ií]logo|interludio|historia paralela|palabras de cierre"
    r"|extra|parte|chapter|prologue|epilogue|afterword|interlude)\b", re.I)
TOC_TITLE = re.compile(r"(contenidos?|[ií]ndice|tabla de contenidos?|contents|table of contents):?", re.I)
DOT_LEADER = re.compile(r"\.{4,}\s*\d+$")
SCENE_BREAK = re.compile(r"[\s☆★*＊◆◇●○■□♦♢※✦✧❖◈•·#~◎◯]+")
END_PUNCT = (".", "!", "?", "…", '"', "'", "»", "”", "’", ":", ")")
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


@dataclass
class Block:
    kind: str           # "p" | "heading" | "image" | "break"
    page: int           # página de origen (empieza en 1)
    html: str = ""
    image: bytes = b""  # JPEG si kind == "image"


@dataclass
class Book:
    title: str
    author: str
    cover: bytes        # JPEG
    blocks: list
    candidates: list    # índices en blocks de los títulos de capítulo detectados
    series: str = ""    # serie a la que pertenece ("" si no se sabe)
    volume: str = ""    # número de volumen dentro de la serie ("1", "2.5")


VOLUME = re.compile(r"\b(?:vol(?:umen|ume)?\.?|tomo)\s*0*(\d+(?:[.,]\d+)?)", re.I)


def _series_volume(stem, meta_title):
    """Serie y volumen desde el nombre del archivo ("Serie - Volumen 01 [WP]" -> "Serie", "1").
    Si el nombre no dice la serie ("Volumen 1 World Project"), se usa el título del PDF."""
    m = VOLUME.search(stem)
    if not m:
        return "", ""
    series = re.sub(r"\[[^\]]*\]", "", stem[:m.start()]).strip(" -–—_.")
    return series or meta_title.strip(), m.group(1).replace(",", ".")


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
    """Agrupa los spans por línea base: el texto justificado parte una línea en varios spans."""
    spans = [s for b in page.get_text("dict")["blocks"] for line in b.get("lines", [])
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

    counts = collections.Counter()
    for rows, h in zip(pages, heights):
        counts.update({_norm(r.text) for r in rows if in_band(r, h)})
    repeated = {k for k, n in counts.items() if n >= max(2, REPEAT * len(pages))}
    repeated.add("#")  # números de página sueltos
    return [[r for r in rows if not (in_band(r, h) and _norm(r.text) in repeated)]
            for rows, h in zip(pages, heights)]


def _is_heading(r, body):
    if len(r.text) > 120:
        return False
    return r.size >= body * 1.3 or (r.bold and (r.size >= body * 1.1 or bool(KEYWORDS.match(r.text))))


def _is_toc(rows):
    return bool(TOC_TITLE.fullmatch(rows[0].text)) or sum(bool(DOT_LEADER.search(r.text)) for r in rows) >= 3


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


def _title(rows):
    """Clave del primer título de la página, para comparar páginas."""
    heads = [r for r in rows if r.heading]
    return _key(" ".join(r.text for r in _first_group(heads))) if heads else ""


def _image_coverage(page):
    area = page.rect
    return sum(abs(pymupdf.Rect(i["bbox"]) & area) for i in page.get_image_info()) / abs(area)


def _illustration(doc, page, uses):
    """JPEG de la ilustración a página completa que hay bajo el texto, sin el texto encima.
    Una imagen usada en más de 2 páginas es un fondo decorativo: None."""
    infos = [i for i in page.get_image_info(xrefs=True) if i["xref"]]
    if not infos:
        return None
    big = max(infos, key=lambda i: abs(pymupdf.Rect(i["bbox"]) & page.rect))
    if abs(pymupdf.Rect(big["bbox"]) & page.rect) < 0.5 * abs(page.rect) or uses[big["xref"]] > 2:
        return None
    pix = pymupdf.Pixmap(doc, big["xref"])
    if pix.alpha:
        pix = pymupdf.Pixmap(pix, 0)
    if pix.n not in (1, 3):  # CMYK u otros: JPEG necesita gris o RGB
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    return pix.tobytes("jpg", jpg_quality=85)


def _render(page):
    return page.get_pixmap(dpi=150).tobytes("jpg", jpg_quality=85)


def _join(a, b):
    """Une dos líneas de un párrafo; una palabra partida con guion se junta aunque haya etiquetas."""
    if re.search(r"\w-$", TAGS.sub("", a)) and TAGS.sub("", b)[:1].islower():
        i = a.rindex("-")  # el guion final; detrás solo puede haber etiquetas de cierre
        return a[:i] + a[i + 1:] + b
    return f"{a} {b}"


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
        blocks = []
        para, short, last_text = None, False, ""
        repeated = ""  # título que la página anterior ya emitió junto a su ilustración
        for i, (page, rows) in enumerate(zip(doc, pages)):
            num = i + 1
            if progress:
                progress(n + num, 2 * n)
            pending, repeated = repeated, ""
            headings = [r for r in rows if r.heading]
            # ponytail: solo ilustraciones a página completa; las pequeñas dentro del texto se descartan
            covered = sum(len(r.text) for r in rows) < 200 and _image_coverage(page) > 0.5
            if covered and not headings:
                blocks.append(Block("image", num, image=_render(page)))
                para = None
                continue
            # Portada de capítulo: la ilustración tapa el título (texto oculto) y la página siguiente lo repite.
            # Se emite el título y la ilustración; sin repetición, la imagen es un fondo decorativo y se descarta.
            if covered and len(headings) == len(rows) and i + 1 < n and _title(rows) == _title(pages[i + 1]):
                blocks.append(Block("heading", num, html.escape(" ".join(r.text for r in _first_group(rows)))))
                blocks.append(Block("image", num, image=_render(page)))
                para, repeated = None, _title(rows)
                continue
            if not rows or _is_toc(rows):
                continue
            if len(headings) == len(rows):
                rows = _first_group(rows)
            start = len(blocks)
            normal = [r for r in rows if not r.heading]
            # Ilustración con el texto de la novela encima: se guardan las dos cosas. El párrafo abierto
            # sigue abierto, así que su continuación se une a él aunque la imagen quede en medio.
            art = _illustration(doc, page, uses) if normal else None
            if art:
                blocks.append(Block("image", num, image=art))
            left = min((r.x0 for r in normal), default=0)
            right = max((r.x1 for r in normal), default=0)
            prev = None
            for r in rows:
                if r.heading:
                    # Se une a la línea anterior si es el mismo título partido: mismo tamaño y pegada
                    # (un subtítulo justo debajo del capítulo queda más lejos, o lo abre un marcador).
                    if (prev is not None and prev.heading and abs(prev.size - r.size) < 0.5
                            and r.y - prev.y < 2 * r.size and not r.starts):
                        blocks[-1].html += " " + html.escape(r.text)
                    else:
                        blocks.append(Block("heading", num, html.escape(r.text)))
                    para = None
                elif SCENE_BREAK.fullmatch(r.text):
                    blocks.append(Block("break", num))
                    para = None
                else:
                    indented = r.x0 > left + body * 0.8
                    if para is None or indented or short:
                        new = True
                    elif prev is None:  # primera línea de la página: ¿sigue el párrafo anterior?
                        new = not indent_style and last_text.endswith(END_PUNCT)
                    else:
                        new = r.y - prev.y > line_gap * 1.3
                    if new and para is not None and r.text[:1].islower() and not last_text.endswith(END_PUNCT):
                        new = False  # algunos PDF ponen con sangría el final de una frase sin acabar
                    if new:
                        para = Block("p", num, r.html)
                        blocks.append(para)
                    else:
                        para.html = _join(para.html, r.html)
                    short = r.x1 < right - (right - left) * 0.2
                    last_text = r.text
                prev = r
            if pending:  # quitar el título repetido: ya salió con la ilustración de la página anterior
                k = next((k for k in range(start, len(blocks)) if blocks[k].kind == "heading"), None)
                if k is not None and _key(html.unescape(blocks[k].html)) == pending:
                    del blocks[k]

        if not any(b.kind in ("p", "heading") for b in blocks):
            raise ValueError("No se encontró texto de la novela en el PDF (¿solo tiene el índice?).")

        if blocks and blocks[0].kind == "image" and blocks[0].page == 1:
            cover = blocks.pop(0).image
        else:
            cover = _render(doc[0])

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
            candidates = [k for k, b in enumerate(blocks) if b.kind == "heading"]

        meta = doc.metadata or {}
        meta_title = meta.get("title") or ""
        title = meta_title or Path(pdf_path).stem
        series, volume = _series_volume(Path(pdf_path).stem, meta_title)
        # Sin el volumen en el título, todos los tomos se llamarían igual en lectores que no muestran la serie
        if series and volume and not re.search(rf"(?<!\d)0*{re.escape(volume)}(?!\d)", meta_title):
            title = f"{series} - Volumen {volume}"
        return Book(title, meta.get("author") or "", cover, blocks, candidates, series, volume)


CSS = """body { margin: 0 4%; line-height: 1.5; }
h1 { text-align: center; font-size: 1.4em; margin: 2em 0 1em; }
h2 { text-align: center; font-size: 1.15em; margin: 1.5em 0 1em; }
p { text-indent: 1.5em; margin: 0 0 0.4em; text-align: justify; }
p.brk { text-indent: 0; text-align: center; margin: 1em 0; }
div.img { text-align: center; margin: 0; page-break-before: always; page-break-after: always; }
div.img img { max-width: 100%; max-height: 95vh; }
"""

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


def write_epub(book, chapters, out_path, lang="es", removed=()):
    """Escribe el EPUB. chapters: [(índice de bloque, título)] elegidos por el usuario.
    removed: índices de títulos cuya sección se quita (agradecimientos, enlaces del grupo…).
    Un capítulo se lleva todo hasta el siguiente capítulo, subtítulos incluidos; un subtítulo,
    hasta el siguiente título."""
    starts = dict(chapters)
    skip = set()
    for k in removed:
        chapter = k in book.candidates or k in starts
        end = next((j for j in range(k + 1, len(book.blocks))
                    if book.blocks[j].kind == "heading" and (j in starts or not chapter)), len(book.blocks))
        skip.update(range(k, end))
    sections = []  # [título, es_capítulo, [(índice, bloque)]]
    for k, b in enumerate(book.blocks):
        if k in skip:
            continue
        if k in starts:
            sections.append([starts[k], True, []])
        else:
            if not sections:
                sections.append(["Inicio", False, []])
            sections[-1][2].append((k, b))

    files, images = {}, {}
    subtitles = {}  # archivo -> [(ancla, título escapado)]: subentradas del índice
    for n, (title, is_chapter, blocks) in enumerate(sections, 1):
        name = f"sec{n:03d}.xhtml"
        parts = [f"<h1>{html.escape(title)}</h1>"] if is_chapter else []
        for k, b in blocks:
            if b.kind == "image":
                src = f"img/p{b.page:04d}.jpg"
                images[src] = b.image
                parts.append(f'<div class="img"><img src="{src}" alt=""/></div>')
            elif b.kind == "break":
                parts.append('<p class="brk">* * *</p>')
            elif b.kind == "heading":  # título que no es capítulo: subtítulo ("El lado de la bruja")
                parts.append(f'<h2 id="t{k}">{b.html}</h2>')
                subtitles.setdefault(name, []).append((f"t{k}", b.html))
            else:
                parts.append(f"<p>{b.html}</p>")
        files[name] = _xhtml(title, "\n".join(parts), lang)

    uid = f"urn:uuid:{uuid.uuid4()}"
    title = html.escape(book.title)
    creator = f"<dc:creator>{html.escape(book.author)}</dc:creator>\n" if book.author else ""
    if book.series:  # serie en EPUB 3 (belongs-to-collection) y en el formato de Calibre
        name, number = html.escape(book.series), html.escape(book.volume)
        creator += (f'<meta property="belongs-to-collection" id="serie">{name}</meta>\n'
                    '<meta refines="#serie" property="collection-type">series</meta>\n'
                    f'<meta name="calibre:series" content="{name}"/>\n')
        if number:
            creator += (f'<meta refines="#serie" property="group-position">{number}</meta>\n'
                        f'<meta name="calibre:series_index" content="{number}"/>\n')
    modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest = [
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
        '<item id="css" href="style.css" media-type="text/css"/>',
        '<item id="cover-image" href="cover.jpg" media-type="image/jpeg" properties="cover-image"/>',
        '<item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>',
    ]
    manifest += [f'<item id="s{k}" href="{name}" media-type="application/xhtml+xml"/>' for k, name in enumerate(files)]
    manifest += [f'<item id="i{k}" href="{src}" media-type="image/jpeg"/>' for k, src in enumerate(images)]
    spine = ['<itemref idref="cover"/>'] + [f'<itemref idref="s{k}"/>' for k in range(len(files))]
    manifest_xml, spine_xml = "\n".join(manifest), "\n".join(spine)
    opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:identifier id="uid">{uid}</dc:identifier>
<dc:title>{title}</dc:title>
{creator}<dc:language>{lang}</dc:language>
<meta property="dcterms:modified">{modified}</meta>
<meta name="cover" content="cover-image"/>
</metadata>
<manifest>
{manifest_xml}
</manifest>
<spine toc="ncx">
{spine_xml}
</spine>
</package>
"""
    # Índice en dos niveles: cada sección y, dentro, sus subtítulos con enlace a su ancla.
    entries = [(name, sec[0]) for name, sec in zip(files, sections)]
    items, points, order = [], [], 0
    for name, t in entries:
        subs = subtitles.get(name, [])
        children = "".join(f'<li><a href="{name}#{anchor}">{text}</a></li>' for anchor, text in subs)
        items.append(f'<li><a href="{name}">{html.escape(t)}</a>' + (f"<ol>{children}</ol>" if subs else "") + "</li>")
        order += 1  # playOrder: el capítulo antes que sus subtítulos, todos correlativos
        point = (f'<navPoint id="n{order}" playOrder="{order}"><navLabel><text>{html.escape(t)}</text></navLabel>'
                 f'<content src="{name}"/>')
        for anchor, text in subs:
            order += 1
            point += (f'<navPoint id="n{order}" playOrder="{order}"><navLabel><text>{text}</text></navLabel>'
                      f'<content src="{name}#{anchor}"/></navPoint>')
        points.append(point + "</navPoint>")
    items, points = "\n".join(items), "\n".join(points)
    nav = _xhtml("Índice", f'<nav epub:type="toc" id="toc"><h1>Índice</h1><ol>\n{items}\n</ol></nav>', lang)
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

    tmp = f"{out_path}.tmp"
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
            z.writestr("META-INF/container.xml", CONTAINER)
            z.writestr("OEBPS/content.opf", opf)
            z.writestr("OEBPS/nav.xhtml", nav)
            z.writestr("OEBPS/toc.ncx", ncx)
            z.writestr("OEBPS/style.css", CSS)
            z.writestr("OEBPS/cover.xhtml", cover)
            z.writestr("OEBPS/cover.jpg", book.cover)
            for name, text in files.items():
                z.writestr(f"OEBPS/{name}", text)
            for src, data in images.items():
                z.writestr(f"OEBPS/{src}", data)
        os.replace(tmp, out_path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
