"""EPUB de entrada sin perder nada: read_epub lo lee como bloques que recuerdan de qué elemento salen, y write_faithful
escribe el libro copiando el original y parcheando solo lo que cambió (texto, títulos, secciones, portada, datos).
Así un EPUB hecho a mano (Sigil, Calibre) sale con su CSS, sus fuentes, sus h1-h6, furigana y enlaces."""
import copy
import functools
import html
import posixpath
import re
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import replace

import convert

OPF_NS = "{http://www.idpf.org/2007/opf}"
DC = "{http://purl.org/dc/elements/1.1/}"
# Etiqueta, comentario, CDATA o instrucción: el resto es texto
TOKEN = re.compile(r"<!--.*?-->|<!\[CDATA\[.*?\]\]>|<[?!][^>]*>|<(/?)([A-Za-z][\w:.-]*)((?:\s+[^\s=>/]+(?:\s*=\s*(?:\"[^\"]*\"|'[^']*'))?)*)\s*(/?)>",
                   re.S)
INLINE_TAGS = {"a", "abbr", "b", "bdi", "bdo", "br", "cite", "code", "data", "dfn", "em", "i", "kbd", "mark", "q",
               "rp", "rt", "rtc", "ruby", "rb", "s", "samp", "small", "span", "strong", "sub", "sup", "time", "u",
               "var", "wbr", "img", "del", "ins", "font", "big", "tt"}
TEXT_LEAVES = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "pre"}
IMAGES = {"img", "svg"}
HEADINGS = {f"h{n}" for n in range(1, 7)}
EMPTY_MARK = re.compile(r"\s*<(span|a)\b[^>]*?(?:/>|>\s*</\1>)")  # <span epub:type="pagebreak" …></span>, <a id=…/>
TRAILING_MARKS = re.compile(r"(?:<(span|a)\b[^>]*?(?:/>|>\s*</\1>)\s*)+$")


class Node:
    """Elemento del XHTML con sus posiciones en el texto del archivo."""

    def __init__(self, tag, attrs, start, inner):
        self.tag, self.attrs, self.start, self.inner = tag, attrs, start, inner
        self.inner_end = self.end = inner
        self.children, self.text = [], False  # text: lleva texto directo (no solo espacios)

    def has_text(self):
        return self.text or any(c.has_text() for c in self.children)

    def blocky(self):
        """Lleva dentro algún elemento de bloque (no en línea)."""
        return any(c.tag not in INLINE_TAGS and c.tag != "svg" or c.blocky() for c in self.children)

    def images(self):
        return [self] if self.tag in IMAGES else [n for c in self.children for n in c.images()]


def _local(tag):
    return tag.split(":")[-1].lower()


def parse(text, container="body"):
    """Árbol de elementos dentro de <container> (el <body> de un XHTML, el <navMap> de un toc.ncx), o None si no
    lo tiene. Tolerante: un cierre sin abrir se ignora."""
    body = re.search(rf"<{container}\b[^>]*>", text)
    if not body:
        return None
    root = Node(container.lower(), "", body.start(), body.end())
    stack, pos = [root], body.end()
    end = text.rfind(f"</{container}>")
    end = len(text) if end < 0 else end
    for m in TOKEN.finditer(text, pos, end):
        if text[pos:m.start()].strip() and html.unescape(text[pos:m.start()]).strip():
            stack[-1].text = True
        pos = m.end()
        if not m.group(2):  # comentario, CDATA
            if m.group(0).startswith("<![CDATA[") and m.group(0)[9:-3].strip():
                stack[-1].text = True
            continue
        tag = _local(m.group(2))
        if m.group(1):  # cierre: hasta el elemento que cierra
            for depth in range(len(stack) - 1, 0, -1):
                if stack[depth].tag == tag:
                    for node in stack[depth:]:
                        node.inner_end, node.end = m.start(), m.end()
                    del stack[depth:]
                    break
            continue
        node = Node(tag, m.group(3), m.start(), m.end())
        stack[-1].children.append(node)
        if not m.group(4) and tag not in ("br", "img", "hr", "wbr", "image", "meta", "link", "col", "area", "source"):
            stack.append(node)
    if text[pos:end].strip() and html.unescape(text[pos:end]).strip():
        stack[-1].text = True
    root.inner_end = root.end = end
    return root


def leaves(node):
    """(kind, nodo) de los bloques del árbol en orden: "text" (párrafo o título), "image" o "break" (hr)."""
    out = []
    for child in node.children:
        if child.tag == "hr":
            out.append(("break", child))
        elif child.tag in IMAGES and not child.has_text():
            out.append(("image", child))
        elif child.tag in TEXT_LEAVES or (child.tag not in INLINE_TAGS and not child.blocky()) or (
                child.tag in INLINE_TAGS and child.has_text()):
            if child.has_text():
                out.append(("text", child))
            else:
                out += [("image", n) for n in child.images()]
        else:
            out += leaves(child)
    return out


def _descendants(node):
    for child in node.children:
        yield child
        yield from _descendants(child)


def _attr(attrs, name):
    m = re.search(r"(?:^|\s)(?:[\w-]+:)?" + name + r"\s*=\s*(\"[^\"]*\"|'[^']*')", attrs or "")
    return html.unescape(m.group(1)[1:-1]) if m else ""


def _resolve(base, href):
    """Ruta dentro del zip de href, relativa al archivo base."""
    href = urllib.parse.unquote(href.split("#")[0])
    return posixpath.normpath(posixpath.join(posixpath.dirname(base), href)) if href else base


def _normal(inner):
    """html de un bloque: <em>/<strong> como <i>/<b>, que el editor y las herramientas entienden."""
    inner = re.sub(r"<(/?)em\b[^>]*>", r"<\1i>", inner)
    inner = re.sub(r"<(/?)strong\b[^>]*>", r"<\1b>", inner)
    return " ".join(inner.split())  # una línea: el editor trabaja con un párrafo por línea


def _plain(inner):
    """Texto de un título: sin etiquetas, el salto de línea como espacio y sin lo que va en <rt>."""
    inner = re.sub(r"<rt\b.*?</rt>|<rp\b.*?</rp>", "", inner, flags=re.S)
    return " ".join(html.unescape(convert.TAGS.sub(" ", re.sub(r"<br\b[^>]*>", " ", inner))).split())


def package(z):
    """(ruta del OPF, árbol del OPF, {id: (ruta, media-type, properties)}, [ids del spine], id del toc NCX)."""
    container = ET.fromstring(z.read("META-INF/container.xml"))
    path = next(e.get("full-path") for e in container.iter() if e.tag.endswith("rootfile"))
    opf = ET.fromstring(z.read(path))
    items = {i.get("id"): (_resolve(path, i.get("href", "")), i.get("media-type", ""), i.get("properties", ""))
             for i in opf.iter(OPF_NS + "item")}
    spine = opf.find(OPF_NS + "spine")
    order = [r.get("idref") for r in spine.iter(OPF_NS + "itemref") if r.get("idref") in items]
    return path, opf, items, order, spine.get("toc", "")


def toc_labels(z, items, ncx_id):
    """{archivo: nombre en el índice}: del nav de EPUB 3 o, si no hay, del toc.ncx. El primero de cada archivo."""
    labels = {}
    nav = next((p for p, kind, props in items.values() if "nav" in props.split()), None)
    if nav:
        text = z.read(nav).decode("utf-8", "replace")
        toc = re.search(r"<nav\b[^>]*epub:type=\"[^\"]*\btoc\b[^\"]*\"[^>]*>(.*?)</nav>", text, re.S)
        for href, label in re.findall(r"<a\b[^>]*href=\"([^\"]*)\"[^>]*>(.*?)</a>", toc.group(1) if toc else "", re.S):
            labels.setdefault(_resolve(nav, href), _plain(label))
    if not labels and ncx_id in items:
        ncx = items[ncx_id][0]
        for point in ET.fromstring(z.read(ncx)).iter():
            if point.tag.endswith("navPoint"):
                text = next((t.text for t in point.iter() if t.tag.endswith("text")), "") or ""
                content = next((c.get("src") for c in point if c.tag.endswith("content")), "")
                if content:
                    labels.setdefault(_resolve(ncx, content), " ".join(text.split()))
    return labels


def _meta(opf, book):
    """Datos del OPF al libro: lo que la ventana muestra en «Editar datos» y «Más datos…»."""
    meta = opf.find(OPF_NS + "metadata")
    refines = {}  # id -> {propiedad: [valores]}
    for m in meta.iter(OPF_NS + "meta"):
        if m.get("refines"):
            refines.setdefault(m.get("refines")[1:], {}).setdefault(m.get("property"), []).append(
                ((m.text or "").strip(), m.get("{http://www.w3.org/XML/1998/namespace}lang", "")))
    names = {m.get("name"): m.get("content", "") for m in meta.iter(OPF_NS + "meta") if m.get("name")}
    role = lambda e: (refines.get(e.get("id"), {}).get("role", [("", "")])[0][0]
                      or e.get(OPF_NS + "role", "") or e.get("role", ""))
    people = {}
    for e in list(meta.iter(DC + "creator")) + list(meta.iter(DC + "contributor")):
        value = (e.text or "").strip()
        r = role(e) or ("aut" if e.tag == DC + "creator" else "")
        if value:
            people.setdefault(r, []).append((value, e))
    native = lambda r: " & ".join(next((v for v, lang in refines.get(e.get("id"), {}).get("alternate-script", [])
                                        if lang.startswith("ja") and lang != "ja-Latn"), "")
                                  for _, e in people.get(r, [])).strip(" &")
    for field, r in (("author", "aut"), ("translator", "trl"), ("illustrator", "ill"), ("proofreader", "pfr"),
                     ("editor", "edt"), ("epub_by", "mrk"), ("distributor", "dst")):
        setattr(book, field, " & ".join(v for v, _ in people.get(r, [])))
    book.author_native, book.illustrator_native = native("aut"), native("ill")
    title = meta.find(DC + "title")
    if title is not None and (title.text or "").strip():
        book.title = title.text.strip()
        for value, lang in refines.get(title.get("id"), {}).get("alternate-script", []):
            if lang == "ja-Latn":
                book.romaji_title = value
            elif lang.startswith("ja"):
                book.original_title = value
    book.publisher = " & ".join((e.text or "").strip() for e in meta.iter(DC + "publisher") if (e.text or "").strip())
    description = meta.find(DC + "description")
    if description is not None and description.text:
        text = re.sub(r"<br\s*/?>|</p>", "\n", description.text)
        text = re.sub(r"</?(?:b|strong)>", "**", text)
        text = re.sub(r"</?(?:i|em)>", "*", text)
        book.description = "\n".join(line.strip() for line in html.unescape(convert.TAGS.sub("", text)).splitlines()
                                     if line.strip())
    book.subjects = ", ".join((e.text or "").strip() for e in meta.iter(DC + "subject") if (e.text or "").strip())
    date = meta.find(DC + "date")
    book.date = (date.text or "").strip()[:10] if date is not None else ""
    kind = meta.find(DC + "type")
    book.book_type = (kind.text or "").strip() if kind is not None and (kind.text or "").strip() != "Novela ligera" else ""
    for e in meta.iter(DC + "identifier"):
        value = (e.text or "").strip()
        if value.lower().startswith("urn:isbn:") or e.get(OPF_NS + "scheme", "").upper() == "ISBN":
            if len(convert._isbn(value.split(":")[-1])) >= len(convert._isbn(book.isbn)):  # el de 13 cifras gana
                book.isbn = value.split(":")[-1]
        elif value.lower().startswith("urn:amazon:"):
            book.asin = value.split(":")[-1]
        elif re.match(r"https?://", value):
            book.source_url = value
    collections = {m.get("id"): ((m.text or "").strip(), m.get("refines")) for m in meta.iter(OPF_NS + "meta")
                   if m.get("property") == "belongs-to-collection"}
    for ident, (name, parent) in collections.items():
        props = refines.get(ident, {})
        if parent:
            book.franchise = name
        elif props.get("collection-type", [("", "")])[0][0] == "series" or not book.series:
            book.series = name
            book.volume = props.get("group-position", [("", "")])[0][0]
    book.series = names.get("calibre:series") or book.series
    book.volume = names.get("calibre:series_index", "").removesuffix(".0") or book.volume
    try:
        stars = float(names.get("calibre:rating", "0")) / 2
        book.rating = (f"{stars:g}".replace(".", ",")) if stars else ""
    except ValueError:
        pass


def read_epub(path, progress=None):
    """Libro de un EPUB: cada párrafo, título, ilustración y separador del spine como un bloque que recuerda su
    elemento (Block.src) y cada archivo del spine como un capítulo, con el nombre de su índice."""
    try:
        z = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as e:
        raise ValueError(f"No se pudo abrir el EPUB: {e}") from e
    with z:
        try:
            opf_path, opf, items, order, ncx_id = package(z)
        except (KeyError, StopIteration, ET.ParseError, AttributeError) as e:
            raise ValueError(f"El EPUB está dañado: no se puede leer su lista de archivos ({e}).") from e
        names = set(z.namelist())
        labels = toc_labels(z, items, ncx_id)
        starts = _first_pages(path, len(order))
        blocks, candidates = [], []
        for n, ident in enumerate(order):
            name, kind, props = items[ident]
            if "html" not in kind or name not in names or "nav" in props.split():
                continue
            text = z.read(name).decode("utf-8", "replace")
            root = parse(text)
            if root is None:
                continue
            page = starts[n] + 1
            found = []
            for what, node in leaves(root):
                i0, i1 = node.inner, node.inner_end
                if what == "text":  # marcas vacías al borde (página, ancla): con la etiqueta, no en el texto editable
                    while m := EMPTY_MARK.match(text, i0, i1):
                        i0 = m.end()
                    if m := TRAILING_MARKS.search(text[i0:i1]):
                        i1 = i0 + m.start()
                inner = text[i0:i1]
                spot = (name, i0, i1, node.start, node.end)
                if what == "image":
                    href = _attr(node.attrs, "src") or next(
                        (_attr(c.attrs, "href") for c in _descendants(node) if c.tag == "image"), "")
                    ref = _resolve(name, href) if href and not href.startswith("data:") else ""
                    if ref in names:
                        found.append(convert.Block("image", page, image=z.read(ref), tag=node.tag, src=spot, ref=ref))
                elif what == "break":
                    found.append(convert.Block("break", page, "", tag="hr", src=spot, orig=""))
                else:
                    html_ = _normal(inner)
                    plain = _plain(inner)
                    heading = node.tag in HEADINGS
                    # «※ ・ ※ ・ ※», «* * *»: solo símbolos de separador; «—…» o «— ¡…!» son diálogo
                    if not heading and plain and convert.SCENE_BREAK.fullmatch(plain.replace("・", "")):
                        found.append(convert.Block("break", page, html_, tag=node.tag, src=spot, orig=html_))
                    else:
                        opening = _start_tag(text[node.start:node.inner])
                        # Un <p> o <h1> sin atributos es el de siempre: sin prefijo en la línea, como uno de un PDF
                        # (la hoja web y la vista de código lo devuelven así)
                        opening = "" if opening in ("<p>", "<h1>") else opening
                        found.append(convert.Block("heading" if heading else "p", page, html_, tag=node.tag, src=spot,
                                                   orig=html_, title=plain if heading else "", opening=opening))
            if not found:
                continue
            label = labels.get(name) or (found[0].title if found[0].kind == "heading" else "")
            if found[0].kind != "heading":  # sin título al principio: uno sintético, que no se escribe
                title = label or _plain(re.search(r"<title>(.*?)</title>", text, re.S).group(1)
                                        if re.search(r"<title>(.*?)</title>", text, re.S) else "") or posixpath.basename(name)
                found.insert(0, convert.Block("heading", page, html.escape(title, quote=False), tag="h1",
                                              src=(name, -1, -1, -1, -1), orig=html.escape(title, quote=False),
                                              title=title))
            else:
                found[0].title = label or found[0].title or posixpath.basename(name)
            candidates.append(len(blocks))
            blocks += found
            if progress:
                progress(n + 1, len(order))
        if not any(b.kind in ("p", "heading") for b in blocks):
            raise ValueError("El EPUB no tiene texto.")
        book = convert.Book("", "", b"", blocks, candidates, origin=str(path))
        _meta(opf, book)
        if not book.series:
            book.series, book.volume = convert._series_volume(convert.Path(path).stem, book.title)
        book.title = book.title or convert.Path(path).stem
        cover = next((p for p, kind, props in items.values() if "cover-image" in props.split()), None)
        meta_cover = next((m.get("content") for m in opf.iter(OPF_NS + "meta") if m.get("name") == "cover"), None)
        cover = cover or (items[meta_cover][0] if meta_cover in items else None)
        if cover and cover in names:
            book.cover = convert.to_jpeg(z.read(cover))
        else:
            book.cover = next((b.image for b in blocks if b.kind == "image"), b"")
        return book


def _first_pages(path, count):
    """Primera página de cada archivo del spine en MuPDF: la que muestra la vista del original."""
    try:
        with convert.open_pdf(path) as doc:
            sizes = [doc.chapter_page_count(i) for i in range(doc.chapter_count)]
    except Exception:
        sizes = []
    if len(sizes) != count:
        return [0] * count
    starts, total = [], 0
    for size in sizes:
        starts.append(total)
        total += size
    return starts


# ---------------------------------------------------------------- aspecto (para la hoja del editor)

CSS_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
SIMPLE_SELECTOR = re.compile(r"([a-z][\w-]*)?((?:\.[\w-]+)*)", re.I)


class BookStyle:
    """Lo que la hoja del editor sabe dibujar del CSS de un EPUB: reglas de etiqueta y de clase (h1, .lucida,
    p.no-indent) y sus fuentes incrustadas. Los selectores compuestos y los @media se ignoran."""

    def __init__(self, path):
        self.rules, self.fonts = [], {}  # [(especificidad, orden, etiqueta, clases, declaraciones)]; {familia: bytes}
        try:
            with zipfile.ZipFile(path) as z:
                _, _, items, _, _ = package(z)
                names = set(z.namelist())
                for href, kind, _ in items.values():
                    if kind == "text/css" and href in names:
                        self._read(z, href, z.read(href).decode("utf-8", "replace"), names)
        except (OSError, zipfile.BadZipFile, KeyError, StopIteration, ET.ParseError, AttributeError):
            pass  # sin su aspecto: la hoja se ve como la de un PDF
        self.rules.sort(key=lambda rule: rule[:2])
        self.cache = {}  # (etiqueta, clases) -> props: se pide por cada trozo de texto de la hoja

    def _read(self, z, href, text, names):
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        text = re.sub(r"@media[^{]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}", "", text)  # Kindle, Kobo: no son la pantalla
        for selectors, body in CSS_RULE.findall(text):
            decls = {k.strip().lower(): v.strip() for k, _, v in (d.partition(":") for d in body.split(";")) if v.strip()}
            if selectors.strip().lower() == "@font-face":
                family = decls.get("font-family", "").strip("\"' ")
                url = re.search(r"url\(\s*['\"]?([^'\")]+)", decls.get("src", ""))
                target = _resolve(href, url[1]) if url else ""
                if family and target in names and family not in self.fonts:
                    self.fonts[family] = z.read(target)
                continue
            for selector in selectors.split(","):
                m = SIMPLE_SELECTOR.fullmatch(selector.strip())
                if m and (m[1] or m[2]):
                    classes = tuple(c for c in m[2].split(".") if c)
                    self.rules.append((bool(m[1]) + 10 * len(classes), len(self.rules), (m[1] or "").lower(), classes,
                                       decls))

    def props(self, tag, classes):
        """Declaraciones que tocan a <tag class="classes">, de menos a más específicas."""
        key = (tag, tuple(classes))
        if key not in self.cache:
            out = {}
            for _, _, rule_tag, rule_classes, decls in self.rules:
                if (not rule_tag or rule_tag == tag) and all(c in classes for c in rule_classes):
                    out.update(decls)
            self.cache[key] = out
        return self.cache[key]


@functools.lru_cache(maxsize=4)
def book_style(path, stamp):
    """BookStyle de un EPUB, recordado mientras no cambie (stamp: su fecha y tamaño): leerlo al abrir cada vez el
    editor tardaba un segundo."""
    return BookStyle(path)


# ---------------------------------------------------------------- escritura

ORIGINAL = convert.ORIGINAL  # estilo y separador: el aspecto del EPUB de entrada, sin tocar su CSS
LNC = "lnc"  # carpeta junto al OPF con lo que añade la app: páginas, hoja de estilo, imágenes y fuentes
META_FIELDS = ("title", "author", "translator", "illustrator", "proofreader", "editor", "epub_by", "distributor",
               "publisher", "description", "subjects", "date", "book_type", "isbn", "asin", "source_url", "series",
               "volume", "franchise", "rating", "original_title", "romaji_title", "author_native", "illustrator_native")
# Elementos del OPF que salen de los datos del libro: si alguno cambió, se escriben de nuevo; el resto se copia
MANAGED_DC = ("title", "creator", "contributor", "publisher", "description", "subject", "date", "type", "identifier")
METADATA_ITEM = re.compile(r"<(dc:\w+|meta)\b(?:[^>]*?/>|[^>]*>.*?</\1>)", re.S)


OPENING_TAG = re.compile(r"<([\w:.-]+)")


def _start_tag(tag):
    """Etiqueta de apertura en una línea y sin espacios de sobra: así se compara la del editor con la del original."""
    return " ".join(tag.split())


def flattened(book):
    """Copia del libro para rehacerlo con el escritor normal (EPUB 2): sin el original y con solo el HTML que EPUB 2
    admite; el furigana pasa a «base (lectura)»."""
    out = copy.copy(book)
    out.origin = ""
    out.blocks = [replace(b, html=_flat(b.html), opening="") if b.kind in ("p", "heading") else b
                  for b in book.blocks]
    return out


def _flat(inner):
    inner = re.sub(r"<rt\b[^>]*>(.*?)</rt>", r" (\1)", inner, flags=re.S)
    inner = re.sub(r"<rp\b.*?</rp>|<br\b[^>]*>", " ", inner, flags=re.S)
    inner = re.sub(r"<a\b(?![^>]*href=\"https?://)[^>]*>(.*?)</a>", r"\1", inner, flags=re.S)  # notas, anclas
    keep = re.compile(r"</?(?:i|b|sup|sub)>|<a href=\"https?://[^\"]*\">|</a>")
    inner = re.sub(r"<a\b[^>]*(href=\"https?://[^\"]*\")[^>]*>", r"<a \1>", inner)
    return " ".join(re.sub(r"<[^>]+>", lambda m: m[0] if keep.fullmatch(m[0]) else "", inner).split())


def _book_meta(opf, path):
    """Datos del OPF como los deja read_epub (con lo que se deduce del nombre si falta)."""
    book = convert.Book("", "", b"", [], [])
    _meta(opf, book)
    if not book.series:
        book.series, book.volume = convert._series_volume(convert.Path(path).stem, book.title)
    book.title = book.title or convert.Path(path).stem
    return book


def _rel(base, target):
    """href de target (ruta del zip) desde el archivo base."""
    return urllib.parse.quote(posixpath.relpath(target, posixpath.dirname(base) or "."))


def _retitle(inner, title):
    """Contenido de un título renombrado: «Número: Nombre» en dos líneas, cada parte en la suya, si el título original
    las tenía (<br/>); si no, el texto nuevo tal cual."""
    parts = re.split(r"(<br\b[^>]*>)", inner)
    m = re.match(r"(.+?)\s*:\s*(.+)", title)
    if m and len(parts) == 3:
        return _swap_text(parts[0], m[1]) + parts[1] + _swap_text(parts[2], m[2])
    return html.escape(title, quote=False)


def _swap_text(fragment, text):
    """fragment con su texto cambiado por text y sus etiquetas tal cual (el texto va en el primer hueco con letras)."""
    pieces = re.split(r"(<[^>]+>)", fragment)
    done = False
    for n, piece in enumerate(pieces):
        if not piece.startswith("<") and piece.strip():
            pieces[n] = "" if done else html.escape(text, quote=False)
            done = True
    return "".join(pieces) if done else html.escape(text, quote=False)


def _set_attr(tag, name, value):
    """Etiqueta de apertura con el atributo name cambiado (o añadido)."""
    value = html.escape(value)
    if re.search(rf"\s{name}\s*=\s*\"[^\"]*\"", tag):
        return re.sub(rf"(\s{name}\s*=\s*)\"[^\"]*\"", lambda m: f'{m[1]}"{value}"', tag, count=1)
    return re.sub(r"\s*(/?)>$", lambda m: f' {name}="{value}"{m[1]}>', tag, count=1)


def _same_format(new, old):
    """new en el formato de old (el manifiesto dice su tipo): una PNG reducida a JPEG vuelve a PNG."""
    if old[:4] == b"\x89PNG" and new[:4] != b"\x89PNG":
        return convert.pymupdf.Pixmap(new).tobytes("png")
    if old[:3] == b"\xff\xd8\xff" and new[:3] != b"\xff\xd8\xff":
        return convert._jpeg(convert._rgb(convert.pymupdf.Pixmap(new), alpha=False), 85)
    return new


def _cut_tree(text, container, remove, rename):
    """Quita de un índice (nav o toc.ncx) las entradas (li, navPoint) que llevan a un archivo de remove y cambia el
    nombre de las que llevan a un archivo de rename ({archivo: nombre}). remove(href) y rename(href) resuelven."""
    root = parse(text, container)
    if root is None:
        return text
    edits = []  # (inicio, fin, texto nuevo)

    def walk(node):
        for child in node.children:
            if child.tag in ("li", "navpoint"):
                link = next((n for n in _descendants(child) if n.tag in ("a", "content")), None)
                href = (_attr(link.attrs, "href") or _attr(link.attrs, "src")) if link else ""
                if href and remove(href):
                    edits.append((child.start, child.end, ""))
                    continue
                name = rename(href) if href and "#" not in href else None
                label = next((n for n in _descendants(child) if n.tag in ("a", "text")), None) if name else None
                if label is not None:
                    edits.append((label.inner, label.inner_end, html.escape(name, quote=False)))
            walk(child)

    walk(root)
    for start, end, new in sorted(edits, reverse=True):
        text = text[:start] + new + text[end:]
    return text


def write_faithful(book, chapters, out_path, lang="es", removed=(), typography=False, style=ORIGINAL, contents=False,
                   separator=ORIGINAL, shrink=False, replacements=(), credits=False, gray=False, breaks=(), trim=False,
                   font="", reading=False, kepub=False, hyphenate=False, alts=None, progress=None, fit=None,
                   whole_words=False, css_extra="", synopsis=False, title_image=None, logo="", endnotes=False,
                   note_style="1", heading_font="", box_style="Sencillo"):
    """Escribe el libro leído por read_epub copiando su EPUB original (book.origin) y parcheando solo lo que cambió:
    un XHTML sin cambios sale con los mismos bytes. Mismas opciones que convert.write_epub; style, font y separator
    con ORIGINAL (o vacíos) dejan el aspecto del original."""
    rules = convert._rules(replacements)
    hyphenate = hyphenate and not whole_words and lang.split("-")[0].lower() == "es"
    note_mark = convert.NOTE_STYLES.get(note_style, "{}").format
    limit = convert.SHRINK if shrink is True else int(shrink or 0)
    skip, titles, breaks = convert.skipped(book, chapters, removed), dict(chapters), set(breaks)

    def fix(s):
        s = convert._replace(s, rules) if rules else s
        s = convert._typography(s, True) if typography else s
        return convert._hyphenate(s) if hyphenate else s

    with zipfile.ZipFile(book.origin) as z:
        names = z.namelist()
        opf_path, opf, items, order, ncx_id = package(z)
        root_dir = posixpath.dirname(opf_path)
        lnc = posixpath.join(root_dir, LNC) if root_dir else LNC
        kinds = {path: kind for path, kind, _ in items.values()}
        nav_path = next((p for p, kind, props in items.values() if "nav" in props.split()), None)
        spine = [items[i][0] for i in order]
        originals = {}  # archivo -> texto original

        def source(name):
            if name not in originals:
                originals[name] = z.read(name).decode("utf-8", "replace")
            return originals[name]

        # Archivos con bloques en el original: los que read_epub convirtió en secciones
        spans = {}
        for name in spine:
            if "html" in kinds.get(name, "") and name in names and name != nav_path:
                root = parse(source(name))
                found = [n for _, n in leaves(root)] if root else []
                if found:
                    spans[name] = (root, found)
        first = next(iter(spans), None)
        per_file, file_order, current = {}, [], None
        for k, b in enumerate(book.blocks):
            name = b.src[0] if b.src and b.src[0] in spans else (current or first)
            current = name
            if name not in per_file:
                per_file[name] = []
                file_order.append(name)
            if k not in skip and not (b.kind == "image" and not b.keep):
                per_file[name].append((k, b))
        gone = {name for name in spans if not any(b.src[3:4] != (-1,) for _, b in per_file.get(name, []))}

        added, new_items = {}, []  # ruta del zip -> bytes; (id, ruta, media-type) que van al manifiesto
        all_notes, counter = [], [0]

        def add(path, data, media):
            if path not in added:
                added[path] = data
                new_items.append((f"lnc-{len(new_items) + 1}", path, media))
            return path

        def picture(data, base, alt):
            """Ilustración nueva (añadida, cambiada o partida en dos): archivo en lnc/ y su <img>."""
            ext, media = ("png", "image/png") if data[:4] == b"\x89PNG" else ("jpg", "image/jpeg")
            path = add(f"{lnc}/img-{convert.image_key(data)}.{ext}", data, media)
            return f'<img src="{_rel(base, path)}" alt="{html.escape(alt)}"/>'

        def notes_in(new, base, notes):
            def ref(m):
                counter[0] += 1
                n = counter[0]
                (all_notes if endnotes else notes).append((n, m[1], base))
                target = _rel(base, f"{lnc}/notas.xhtml") if endnotes else ""
                return (f'<a epub:type="noteref" role="doc-noteref" href="{target}#lnc-nota{n}" id="lnc-ref{n}">'
                        f'<sup>{note_mark(n)}</sup></a>')
            return convert.NOTE.sub(ref, new)

        def rebreak(b, k):
            if separator == "Línea":
                return "<hr/>"
            if separator == "Espacio en blanco":
                return '<p style="margin: 1.5em 0; text-indent: 0">&#160;</p>'
            symbol = (html.escape(separator, quote=False)
                      if separator not in (ORIGINAL, "Original del PDF", "Según el estilo", "") else b.html or "* * *")
            return f'<p style="text-align: center; text-indent: 0; margin: 1em 0">{symbol}</p>'

        def render(k, b, base, notes, own):
            """XHTML del bloque en el archivo base; own: ocupa su propio elemento del original."""
            alt = (alts or {}).get(convert.image_key(b.image)) if b.kind == "image" and b.image else None
            if b.kind == "image":
                if own:
                    raw = source(base)[b.src[3]:b.src[4]]
                    if b.layout:
                        return "".join(f'<div style="text-align: center; page-break-before: always">'
                                       f'{picture(part, base, alt or "")}</div>' for part in convert._spread(b.image, b.layout))
                    if b.ref and b.ref in names and b.image != z.read(b.ref):  # «Cambiar ilustración»
                        path = add(f"{lnc}/img-{convert.image_key(b.image)}.{'png' if b.image[:4] == b'\x89PNG' else 'jpg'}",
                                   b.image, "image/png" if b.image[:4] == b"\x89PNG" else "image/jpeg")
                        raw = re.sub(r"((?:src|href)\s*=\s*\")[^\"]*(\")", lambda m: m[1] + _rel(base, path) + m[2], raw)
                    if alt:
                        raw = re.sub(r"<img\b[^>]*>", lambda m: _set_attr(m[0], "alt", alt), raw, count=1)
                    return raw
                return "".join(f'<div style="text-align: center; page-break-before: always; page-break-after: always">'
                               f'{picture(part, base, alt or "")}</div>'
                               for part in (convert._spread(b.image, b.layout) if b.layout else [b.image]) if part)
            if b.kind == "break" or k in breaks:
                if own and b.kind == "break" and separator in (ORIGINAL, "Original del PDF", "Según el estilo", ""):
                    return source(base)[b.src[3]:b.src[4]]
                return rebreak(b, k)
            new = fix(b.html)
            if b.kind == "heading" and k in titles and b.title and titles[k] != b.title:
                new = _retitle(new, titles[k])
            new = notes_in(new, base, notes)
            if b.src and b.src[3] >= 0:
                text = source(b.src[0])
                name, i0, i1, o0, o1 = b.src
                start = text[o0:text.index(">", o0) + 1]  # etiqueta de apertura del original
                retag = b.opening and _start_tag(b.opening) != _start_tag(start)  # estilo cambiado en el editor
                if own and new == b.orig and not retag:
                    return text[o0:o1]
                opening, closing = (b.opening if retag else start), text[i1:o1]
                if retag:  # otra etiqueta (p -> h3): también el cierre
                    closing = re.sub(r"</[\w:.-]+>\s*$", f"</{OPENING_TAG.match(b.opening)[1]}>", closing)
                if not own:  # copia de un párrafo que ya está en su sitio: el id no puede repetirse
                    opening = re.sub(r"\sid\s*=\s*\"[^\"]*\"", "", opening)
                if b.kind == "heading" and k in titles and titles[k] != b.title and re.search(r"\stitle=", opening):
                    opening = _set_attr(opening, "title", titles[k])
                return opening + text[o0 + len(start):i0] + new + closing
            if b.opening:  # párrafo nuevo con el estilo que le dio el editor
                return f"{b.opening}{new}</{OPENING_TAG.match(b.opening)[1]}>"
            if b.kind == "heading":
                tag = b.tag if b.tag in HEADINGS else ("h1" if k in titles else "h2")
                return f"<{tag}>{new}</{tag}>"
            style_ = {"center": "text-align: center; text-indent: 0", "right": "text-align: right; text-indent: 0"}.get(
                b.align.removeprefix("frame-"), "")
            if b.align.startswith("frame"):
                style_ = (style_ + "; " if style_ else "") + "border: 1px solid #777777; padding: 0.4em 0.8em"
            return f'<p{f" style={chr(34)}{style_}{chr(34)}" if style_ else ""}>{new}</p>'

        texts = {}  # archivo -> texto nuevo
        for name in file_order:
            if name is None or name in gone:
                continue
            text = source(name)
            root, found = spans[name]
            starts = {n.start for n in found}
            placed, inserts, last, last_pos, notes = {}, {}, None, -1, []
            for k, b in per_file[name]:
                if b.src and b.src[3] == -1:
                    continue  # título sintético: solo es el nombre de la sección
                own = bool(b.src) and b.src[0] == name and b.src[3] in starts and b.src[3] > last_pos
                piece = render(k, b, name, notes, own)
                if own:
                    placed[b.src[3]] = piece
                    last = last_pos = b.src[3]
                else:
                    inserts.setdefault(last, []).append(piece)
            out, cursor = [], 0
            if None in inserts:
                at = found[0].start
                out += [text[:at], "\n".join(inserts[None])]
                cursor = at
            for n in found:
                out += [text[cursor:n.start], placed.get(n.start, ""), "".join("\n" + p for p in inserts.get(n.start, []))]
                cursor = n.end
            out.append(text[cursor:])
            new = "".join(out)
            if notes:
                back = f'<p><a href="#lnc-ref{{}}" role="doc-backlink">{{}}</a> {{}}</p>'
                asides = "".join(f'\n<aside epub:type="footnote" id="lnc-nota{n}">{back.format(n, note_mark(n), body)}</aside>'
                                 for n, body, _ in notes)
                cut = new.rfind("</body>")
                new = new[:cut] + asides + "\n" + new[cut:]
            if new != text:
                texts[name] = new

        # Hoja de la app sobre el original: solo si se eligió otro aspecto
        css = "" if style in (ORIGINAL, "", None) else convert.BASE_CSS + convert.STYLES.get(style, "")
        fonts = {}
        if font in convert.FONTS:
            for weight, slant in (("400", "normal"), ("400", "italic"), ("700", "normal"), ("700", "italic")):
                fonts[f"fonts/{convert.FONTS[font]}-{weight}-{slant}.ttf"] = convert.FONT_DIR / f"{convert.FONTS[font]}-{weight}-{slant}.ttf"
                css += (f'@font-face {{ font-family: "{font}"; font-weight: {weight}; font-style: {slant}; '
                        f'src: url("fonts/{convert.FONTS[font]}-{weight}-{slant}.ttf"); }}\n')
            css += f'body, p {{ font-family: "{font}", serif; }}\n'
        elif font and convert.Path(font).is_file():
            fonts[f"fonts/propia{convert.Path(font).suffix.lower()}"] = convert.Path(font)
            css += (f'@font-face {{ font-family: "Propia"; src: url("fonts/propia{convert.Path(font).suffix.lower()}"); }}\n'
                    'body, p { font-family: "Propia", serif; }\n')
        bundled = convert.HEADING_FONTS.get(heading_font)
        if bundled or (heading_font and heading_font not in convert.HEADING_FONT_CHOICES
                       and convert.Path(heading_font).is_file()):
            src = f"fonts/{bundled}" if bundled else f"fonts/titulos{convert.Path(heading_font).suffix.lower()}"
            fonts[src] = convert.FONT_DIR / bundled if bundled else convert.Path(heading_font)
            css += (f'@font-face {{ font-family: "Titulos"; src: url("{src}"); }}\n'
                    'h1, h1 span, h2, h3 { font-family: "Titulos", serif; }\n')
        css += convert.BOX_STYLES.get(box_style, "") + css_extra
        for src, path in fonts.items():
            add(f"{lnc}/{src}", convert.Path(path).read_bytes(), "font/ttf" if src.endswith(".ttf") else "font/otf")
        if css:
            sheet = add(f"{lnc}/estilo.css", css.encode(), "text/css")
            for name in spine:
                if "html" in kinds.get(name, "") and name in names and name not in gone:
                    text = texts.get(name) or source(name)
                    link = f'<link href="{_rel(name, sheet)}" rel="stylesheet" type="text/css"/>'
                    texts[name] = text.replace("</head>", link + "</head>", 1)

        # Páginas de la app: van en lnc/ con su propia hoja
        page_css = convert.BASE_CSS + (convert.STYLES.get(style) or convert.STYLES["Clásico"]) + convert.BOX_STYLES.get(box_style, "")
        pages = []  # (ruta, texto) en el orden en que van tras la portada
        if synopsis and convert._rich(book.description):
            pages.append((f"{lnc}/sinopsis.xhtml", convert._xhtml("Sinopsis", '<h1 id="titulo">Sinopsis</h1>\n' + "\n".join(
                f"<p>{line}</p>" for line in convert._rich(book.description)), lang, kind=("frontmatter", "abstract", "doc-abstract"),
                css="lnc.css")))
        if credits:
            image = next((b.image for b in book.blocks if title_image and b.kind == "image" and b.image
                          and convert.image_key(b.image) == title_image), b"")
            src = ""
            if image:
                ext = "png" if image[:4] == b"\x89PNG" else "jpg"
                src = posixpath.basename(add(f"{lnc}/titulo.{ext}", image, f"image/{'png' if ext == 'png' else 'jpeg'}"))
            pages.append((f"{lnc}/titulo.xhtml", convert._title_page(book, src, lang, False).replace(
                'href="style.css"', 'href="lnc.css"')))
        if logo:
            try:
                data = convert.Path(logo).read_bytes()
            except OSError:
                data = b""
            if data[:4] == b"\x89PNG" or data[:3] == b"\xff\xd8\xff":
                ext = "png" if data[:4] == b"\x89PNG" else "jpg"
                add(f"{lnc}/logo.{ext}", data, f"image/{'png' if ext == 'png' else 'jpeg'}")
                pages.append((f"{lnc}/logo.xhtml", convert._xhtml("Logo", f'<div class="logo"><img src="logo.{ext}" alt="Logo"/></div>',
                                                                lang, kind=("frontmatter", "colophon", "doc-colophon"), css="lnc.css")))
        keep_files = [n for n in file_order if n and n not in gone]
        if contents:
            rows = "\n".join(f'<li><a href="{_rel(f"{lnc}/contenido.xhtml", book.blocks[k].src[0])}">'
                             f'{html.escape(title, quote=False)}</a></li>'
                             for k, title in chapters if book.blocks[k].src and book.blocks[k].src[0] in keep_files)
            pages.append((f"{lnc}/contenido.xhtml", convert._xhtml("Contenido", f'<h1 class="toc" id="titulo">Contenido</h1>\n'
                                                                    f'<ol class="toc">\n{rows}\n</ol>', lang,
                                                                    kind=("frontmatter", "", ""), css="lnc.css")))
        notes_page = None
        if all_notes:
            notes_page = (f"{lnc}/notas.xhtml", convert._xhtml("Notas", '<h1 id="titulo">Notas</h1>\n<ol class="notes">\n' + "\n".join(
                f'<li id="lnc-nota{n}"><p><a href="{_rel(f"{lnc}/notas.xhtml", base)}#lnc-ref{n}" role="doc-backlink">'
                f'{note_mark(n)}</a> {body}</p></li>' for n, body, base in all_notes) + "\n</ol>", lang,
                kind=("backmatter", "endnotes", "doc-endnotes"), css="lnc.css"))
        cover_page = None
        if spine and spine[0] in gone:  # sin la portada del original (o en la vista previa de una sección): la de la app
            cover_page = (f"{lnc}/portada.xhtml", convert._xhtml(book.title, '<div class="img"><img src="portada.jpg" alt="Portada"/></div>',
                                                                 lang, kind=("frontmatter", "cover", ""), css="lnc.css"))
            add(f"{lnc}/portada.jpg", book.cover, "image/jpeg")
        app_pages = ([cover_page] if cover_page else []) + pages + ([notes_page] if notes_page else [])
        if app_pages:
            add(f"{lnc}/lnc.css", page_css.encode(), "text/css")
        for path, text in app_pages:
            add(path, (convert._kepub(text) if kepub else text).encode(), "application/xhtml+xml")
        if kepub:
            texts = {name: convert._kepub(text) for name, text in texts.items()}
            for name in spine:
                if "html" in kinds.get(name, "") and name not in texts and name in names and name not in gone:
                    texts[name] = convert._kepub(source(name))

        # Enlaces a un archivo quitado (las llamadas de nota si se quitó «Notas»): el texto queda, sin enlace
        if gone:
            def unlink(m, base):
                return m[2] if _resolve(base, _attr(m[1], "href")) in gone else m[0]
            for name in spine:
                if "html" in kinds.get(name, "") and name in names and name not in gone and name != nav_path:
                    text = texts.get(name) or source(name)
                    new = re.sub(r"<a\b([^>]*\bhref=\"(?![a-z][\w+.-]*:)[^\"#][^\"]*\"[^>]*)>(.*?)</a>",
                                 lambda m, base=name: unlink(m, base), text, flags=re.S)
                    if new != text:
                        texts[name] = new

        # Índices: sin los archivos quitados y con los títulos renombrados
        retitle = {book.blocks[k].src[0]: title for k, title in chapters
                   if book.blocks[k].src and title != book.blocks[k].title and book.blocks[k].title}
        for path in [p for p in (nav_path, items.get(ncx_id, ("",))[0]) if p and p in names]:
            if gone or retitle:
                text = texts.get(path) or source(path)
                text = _cut_tree(text, "body" if path == nav_path else "navMap",
                                 lambda href, p=path: _resolve(p, href) in gone,
                                 lambda href, p=path: retitle.get(_resolve(p, href)))
                if text != source(path):
                    texts[path] = text

        # OPF: spine, manifiesto, guía y, si cambiaron, los datos del libro
        opf_text = z.read(opf_path).decode("utf-8")
        new_opf = _patch_opf(opf_text, opf, opf_path, items, order, gone, file_order, new_items,
                             cover_page and cover_page[0], [p for p, _ in pages], notes_page and notes_page[0], book, lang)

        cover_item = next((p for p, kind, props in items.values() if "cover-image" in props.split()), None)
        meta_cover = next((m.get("content") for m in opf.iter(OPF_NS + "meta") if m.get("name") == "cover"), None)
        cover_item = cover_item or (items[meta_cover][0] if meta_cover in items else None)
        images = {p for p, kind, _ in items.values() if kind in ("image/jpeg", "image/png") and p in names}
        tmp = convert.partial_path(out_path)
        total, done = len(names) + len(added), 0
        try:
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
                out.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
                for name in names:
                    done += 1
                    if progress:
                        progress(done, total)
                    if name == "mimetype" or name in gone:
                        continue
                    if name == opf_path:
                        out.writestr(name, new_opf)
                    elif name in texts:
                        out.writestr(name, texts[name])
                    elif name in images and (gray or trim or limit or name == cover_item):
                        data = z.read(name)
                        new = data
                        if name == cover_item and book.cover and convert.to_jpeg(data) != book.cover:
                            new = book.cover  # portada cambiada en la ventana
                        if trim and name != cover_item:
                            new = convert._trim(new)
                        if gray and name != cover_item:
                            new = convert._gray(new)
                        if limit:
                            new = convert._shrink(new, limit)
                        out.writestr(name, _same_format(new, data) if new is not data else data)
                    else:
                        out.writestr(z.getinfo(name), z.read(name))
                for path, data in added.items():
                    done += 1
                    if progress:
                        progress(done, total)
                    out.writestr(path, data)
                if fonts and "META-INF/com.apple.ibooks.display-options.xml" not in names:
                    out.writestr("META-INF/com.apple.ibooks.display-options.xml", convert.APPLE_OPTIONS)
            convert.os.replace(tmp, out_path)
        except Exception:
            if convert.os.path.exists(tmp):
                convert.os.remove(tmp)
            raise


def _patch_opf(text, opf, opf_path, items, order, gone, file_order, new_items, cover, front, notes, book, lang):
    """content.opf con el spine en el orden de la lista y sin los archivos quitados, lo añadido en el manifiesto
    (cover: portada de la app, la primera; front: páginas de la app, tras la portada; notes: al final) y los datos
    del libro si cambiaron."""
    by_path = {path: ident for ident, (path, _, _) in items.items()}
    gone_ids = {by_path[p] for p in gone if p in by_path}
    # manifiesto
    for ident in gone_ids:
        text = re.sub(rf"\s*<item\b[^>]*\bid=\"{re.escape(ident)}\"[^>]*/>", "", text, count=1)
    root_dir = posixpath.dirname(opf_path)
    lines = "".join(f'\n    <item id="{ident}" href="{_rel(opf_path, path)}" media-type="{media}"/>'
                    for ident, path, media in new_items)
    text = text.replace("</manifest>", lines + "\n  </manifest>", 1) if lines else text
    # spine: los archivos con secciones en el orden de la lista; los demás en su sitio
    refs = {m.group(1): m.group(0) for m in re.finditer(r"<itemref\b[^>]*\bidref=\"([^\"]+)\"[^>]*/>", text)}
    moving = [by_path[p] for p in file_order if p in by_path and p not in gone]
    queue = iter(moving)
    new_order = []
    for ident in order:
        if ident in gone_ids:
            continue
        new_order.append(next(queue) if ident in moving else ident)
    by_new = {path: ident for ident, path, _ in new_items}
    ref = lambda path: f'<itemref idref="{by_new[path]}"/>'
    body = [refs.get(i, f'<itemref idref="{i}"/>') for i in new_order]
    body = ([ref(cover)] if cover else []) + body
    body = body[:1] + [ref(p) for p in front] + body[1:] + ([ref(notes)] if notes else [])
    if new_order != order or cover or front or notes:
        text = re.sub(r"(<spine\b[^>]*>).*?(</spine>)", lambda m: m[1] + "\n    " + "\n    ".join(body) + "\n  " + m[2],
                      text, count=1, flags=re.S)
    # guía: sin referencias a archivos quitados
    for path in gone:
        text = re.sub(rf"\s*<reference\b[^>]*href=\"{re.escape(_rel(opf_path, path))}(?:#[^\"]*)?\"[^>]*/>", "", text)
    # datos: si alguno cambió, los suyos se escriben de nuevo; lo demás (accesibilidad, Sigil…) se queda
    fresh = _book_meta(opf, book.origin)
    language = opf.find(f"{OPF_NS}metadata/{DC}language")
    old_lang = (language.text or "").strip() if language is not None else ""
    if all(getattr(book, f) == getattr(fresh, f) for f in META_FIELDS) and lang == old_lang:
        return text
    unique = opf.get("unique-identifier", "")
    meta_match = re.search(r"(<metadata\b[^>]*>)(.*?)(</metadata>)", text, re.S)
    inner = meta_match.group(2)
    managed_ids, kept = set(), []
    for m in METADATA_ITEM.finditer(inner):
        raw, tag = m.group(0), m.group(1)
        ident = _attr(raw.split(">", 1)[0], "id")
        if tag.startswith("dc:") and tag[3:] in MANAGED_DC and not (tag == "dc:identifier" and ident == unique):
            managed_ids.add(ident)
        elif tag == "dc:language" or (tag == "meta" and re.search(
                r"property=\"(?:belongs-to-collection|dcterms:modified|dcterms:alternative)\"|name=\"calibre:", raw)):
            managed_ids.add(ident)
    for m in METADATA_ITEM.finditer(inner):
        raw, tag = m.group(0), m.group(1)
        opening = raw.split(">", 1)[0]
        ident = _attr(opening, "id")
        refines = _attr(opening, "refines").lstrip("#")
        if ident and ident in managed_ids or (refines and refines in managed_ids):
            continue
        if tag == "dc:language" or (tag == "meta" and re.search(
                r"property=\"(?:belongs-to-collection|dcterms:modified|dcterms:alternative)\"|name=\"calibre:", raw)):
            continue
        if tag.startswith("dc:") and tag[3:] in MANAGED_DC and not (tag == "dc:identifier" and ident == unique):
            continue
        kept.append(raw)
    ours = ([f'<dc:title id="title">{html.escape(book.title)}</dc:title>', f"<dc:language>{html.escape(lang)}</dc:language>"]
            + convert._metadata(book)
            + [f'<meta property="dcterms:modified">{convert.datetime.now(convert.timezone.utc):%Y-%m-%dT%H:%M:%SZ}</meta>'])
    new_inner = "\n    " + "\n    ".join(kept + ours) + "\n  "
    return text[:meta_match.start(2)] + new_inner + text[meta_match.end(2):]
