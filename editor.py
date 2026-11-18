"""Hoja del editor de texto estilo Word: lo que se ve es lo que sale en el EPUB. Lee y escribe el formato de
convert.section_text (un párrafo por línea con <i>/<b>, "# " delante de un título, las ilustraciones y los
separadores del PDF como [Ilustración N] / [Separador N]), que es lo que guarda convert.edit_section."""
import base64
import collections
import difflib
import hashlib
import html
import re
from pathlib import Path

from PySide6.QtCore import QBuffer, QMimeData, QObject, QRectF, QRegularExpression, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QImage, QPainter, QPalette, QSyntaxHighlighter, QTextBlockFormat,
                           QTextCharFormat, QTextCursor, QTextDocument, QTextFormat, QTextImageFormat, QTextListFormat)
from PySide6.QtWidgets import QApplication, QTextEdit

import convert
from qtk import u16

_USER = QTextFormat.Property.UserProperty.value
SOURCE = _USER + 1    # párrafo: índice del bloque del libro del que viene (el PDF sigue al cursor)
ORIGINAL = _USER + 2  # párrafo: su línea al cargar; si vuelve a quedar igual se guarda tal cual (no pierde página)
CANON = _USER + 3     # párrafo: cómo lo volcaba dump al cargar, para saber si cambió
HEADING = _USER + 4   # párrafo: es un título ("# ")
OBJECT = _USER + 5    # imagen: "Ilustración 12" / "Separador 7", la marca que vuelve a section_text
LEVEL = _USER + 6     # título: tipo de su fila ("Capítulo", "Subtítulo"); al aceptar, la fila queda así
CENTERED = _USER + 7  # párrafo centrado (carta, cartel): vuelve a section_text con <center> delante
FRAMED = _USER + 8    # párrafo dentro de un recuadro (ventana de estado): vuelve con <marco> delante
RIGHTED = _USER + 9   # párrafo a la derecha (firma de una carta): vuelve con <derecha> delante
FRAME_PAD = 16        # píxeles entre el recuadro y su texto
FRAME_GAP = 5         # píxeles entre los párrafos de un recuadro: pegados del todo costaba leerlos
SUB = 18              # sangría de una línea sin viñeta en un recuadro con viñetas («HP: 10/10» bajo «■ Lorna»)
MIME = "application/x-lnc-hoja"  # copiar y pegar dentro de la hoja conserva formato e ilustraciones
WHOLE = MIME + "-parrafos"  # lo copiado acaba en un salto de párrafo: se pegan párrafos enteros
ICON = _USER + 10     # imagen: la etiqueta <img> de un icono del texto (convert.ICON), que vuelve a section_text
STACK = _USER + 11   # letra: etiquetas del libro que la rodean, de fuera adentro (STACK_SEP entre ellas)
VOID = _USER + 12    # letra invisible (VOID_CHAR): una etiqueta vacía del libro que vuelve tal cual
OPENING = _USER + 13  # párrafo: su etiqueta de apertura (convert.Block.opening), con el estilo puesto en la cinta
USER_STYLE = _USER + 14  # letra: tamaño, color o letra puestos en la cinta, en CSS: vuelven como <span style>
BOOK_LOOK = _USER + 15  # párrafo con el aspecto del libro (su etiqueta y su CSS): sin el título azul de la hoja
LINK_COLOR = "#2a62c8"  # enlaces en la hoja, como en Word; TextEditor pone el azul del tema
NOTE = re.compile(r"\[Nota: [^\]]*\]")
THUMB = 160  # alto de las miniaturas de ilustración en la hoja, en píxeles
SPACE = 10  # píxeles entre párrafos, como el espaciado de Word (no se guarda)
FONT_SIZE = 12  # puntos del texto a 100 %
IMAGES = None  # carpeta de las imágenes nuevas ([Imagen nombre]); TextEditor pone la del programa (app.IMAGES)
PICTURE_FILES = (".jpg", ".jpeg", ".png", ".webp")  # archivos copiados en el Explorador que se pegan como imagen
# Estilos de lista (convert.LIST_STYLES, nombres de CSS) y el de Qt con que se ven en la hoja
LISTS = dict(zip(convert.LIST_STYLES, (
    QTextListFormat.Style.ListDisc, QTextListFormat.Style.ListCircle, QTextListFormat.Style.ListSquare,
    QTextListFormat.Style.ListDecimal, QTextListFormat.Style.ListLowerAlpha, QTextListFormat.Style.ListUpperAlpha,
    QTextListFormat.Style.ListLowerRoman, QTextListFormat.Style.ListUpperRoman)))


def _plain_format():
    return QTextCharFormat()


# ------------------------------------------------------------------ texto con formato: lo que se ve es el HTML
# Cada letra de la hoja recuerda las etiquetas que la rodean en el libro (STACK), tal cual: un <span class>, un
# <ruby> o una nota <a id=… epub:type=…> vuelven enteros aunque la hoja no sepa dibujarlos. Lo que sí sabe dibujar
# (cursiva, negrita, subrayado, tachado, superíndice, subíndice, tamaño, color, letra, enlaces) se ve como en el
# libro; al volcar, lo que se cambió con la cinta añade o quita sus etiquetas (_wanted).
STACK_SEP = "\x1f"
VOID_CHAR = "⁠"  # unión de palabras: ni se ve ni separa; lleva una etiqueta vacía del libro (VOID)
LINE_BREAK = " "  # salto de línea dentro del párrafo: <br/> (Mayús+Intro)
TAG = re.compile(r'<(/?)([A-Za-z][\w:.-]*)((?:\s+[^\s=<>/"]+(?:\s*=\s*"[^"<>]*")?)*)\s*(/?)>')
VOID_TAGS = {"br", "img", "wbr", "hr"}
SIZES = (0.7, 0.8, 1.0, 1.2, 1.5, 2.0, 2.4)  # tamaños relativos de Qt: FontSizeAdjustment de -2 a 4
HEADING_SIZES = {"h1": "1.5em", "h2": "1.3em", "h3": "1.15em", "h4": "1.05em"}
CSS = None  # hoja del EPUB de entrada (faithful.BookStyle): sus clases y fuentes se ven en la hoja; None con un PDF
FAMILIES = {}  # familia del CSS del libro -> familia registrada en Qt (sus fuentes incrustadas)


def use_css(style):
    """La hoja se ve con el CSS de un EPUB de entrada (faithful.BookStyle), con sus fuentes incrustadas; None: la de
    siempre. Las fuentes se registran en Qt una vez por familia."""
    global CSS
    CSS = style
    for family, data in (style.fonts.items() if style else ()):
        if family not in FAMILIES:
            from PySide6.QtCore import QByteArray
            from PySide6.QtGui import QFontDatabase
            ident = QFontDatabase.addApplicationFontFromData(QByteArray(data))
            names = QFontDatabase.applicationFontFamilies(ident) if ident >= 0 else []
            FAMILIES[family] = names[0] if names else family


def _name(raw):
    """Nombre de una etiqueta: '<span class="x">' -> "span"."""
    m = TAG.match(raw)
    return m[2].lower() if m else ""


def _attr(raw, name):
    m = re.search(r"\s" + name + r'\s*=\s*"([^"]*)"', raw or "")
    return html.unescape(m[1]) if m else ""


def _style(text):
    """Declaraciones de un atributo style: {"font-size": "1.2em", …}."""
    out = {}
    for part in (text or "").split(";"):
        key, _, value = part.partition(":")
        if key.strip() and value.strip():
            out[key.strip().lower()] = value.strip()
    return out


def _css(v, decls):
    """Aplica declaraciones de CSS al aspecto v (las que la hoja sabe dibujar)."""
    for key, value in decls.items():
        value = value.lower().replace("!important", "").strip()
        if value in ("inherit", "initial", "unset", "revert"):  # lo del padre sigue (el reinicio de los EPUB de Sigil)
            continue
        if key == "font-style":
            v["italic"] = value in ("italic", "oblique")
        elif key == "font-weight":
            v["bold"] = value in ("bold", "bolder") or (value.isdigit() and int(value) >= 600)
        elif key == "font-size":
            m = re.fullmatch(r"([\d.]+)\s*(em|rem|%)", value)
            if m:
                v["size"] = v.get("size", 1.0) * float(m[1]) / (100 if m[2] == "%" else 1)
            elif value in ("smaller", "small", "x-small"):
                v["size"] = v.get("size", 1.0) * 0.83
            elif value in ("larger", "large", "x-large"):
                v["size"] = v.get("size", 1.0) * 1.2
        elif key == "font-variant" and "small-caps" in value:
            v["smallcaps"] = True
        elif key in ("text-decoration", "text-decoration-line"):
            v["underline"], v["strike"] = "underline" in value, "line-through" in value
        elif key == "vertical-align" and value in ("super", "sub"):
            v["valign"] = value
        elif key == "color" and re.fullmatch(r"#[0-9a-f]{3,8}|[a-z]+|rgb\([^)]*\)", value):
            v["color"] = value
        elif key == "font-family":
            first = decls[key].split(",")[0].strip().strip("\"'")
            if first.lower() not in ("serif", "sans-serif", "inherit", "monospace"):
                v["family"] = FAMILIES.get(first, first)
            elif first.lower() == "monospace":
                v["family"] = "Consolas"


def visual(stack, para=None):
    """Aspecto de una letra con estas etiquetas encima en un párrafo con el aspecto para."""
    v = dict(para or {})
    v.setdefault("size", 1.0)
    for raw in stack:
        name = _name(raw)
        if name in ("i", "em", "cite", "dfn", "var"):
            v["italic"] = True
        elif name in ("b", "strong"):
            v["bold"] = True
        elif name in ("u", "ins"):
            v["underline"] = True
        elif name in ("s", "del", "strike"):
            v["strike"] = True
        elif name == "sup":
            v["valign"] = "super"
        elif name == "sub":
            v["valign"] = "sub"
        elif name == "small":
            v["size"] *= 0.83
        elif name == "big":
            v["size"] *= 1.2
        elif name == "sc":
            v["smallcaps"] = True
        elif name == "rt":  # furigana: la lectura, pequeña y arriba
            v.update(size=v["size"] * 0.6, valign="super", color="#888888")
        elif name == "rp":  # los paréntesis para lectores sin furigana: en la hoja no se ven
            v["hidden"] = True
        elif name in ("code", "tt", "kbd", "samp"):
            v["family"] = "Consolas"
        elif name == "a" and _attr(raw, "href"):
            v.update(href=_attr(raw, "href"), underline=True)
        if CSS:
            _css(v, CSS.props(name, _attr(raw, "class").split()))
        _css(v, _style(_attr(raw, "style")))
    return v


def look_format(v):
    """QTextCharFormat de un aspecto de visual()."""
    f = link_format(v["href"]) if v.get("href") else QTextCharFormat()
    f.setFontItalic(bool(v.get("italic")))
    f.setFontWeight(QFont.Weight.Bold if v.get("bold") else QFont.Weight.Normal)
    f.setFontUnderline(bool(v.get("underline")))
    f.setFontStrikeOut(bool(v.get("strike")))
    f.setVerticalAlignment({"super": QTextCharFormat.VerticalAlignment.AlignSuperScript,
                            "sub": QTextCharFormat.VerticalAlignment.AlignSubScript}.get(
        v.get("valign"), QTextCharFormat.VerticalAlignment.AlignNormal))
    f.setFontCapitalization(QFont.Capitalization.SmallCaps if v.get("smallcaps") else QFont.Capitalization.MixedCase)
    size = 0.7 if v.get("hidden") else v.get("size", 1.0)
    step = min(range(len(SIZES)), key=lambda n: abs(SIZES[n] - size)) - 2
    if step:
        f.setProperty(QTextFormat.Property.FontSizeAdjustment, step)
    if v.get("family"):
        f.setFontFamilies([v["family"]])
    if v.get("hidden"):
        f.setForeground(QColor(0, 0, 0, 0))
    elif v.get("color") and not v.get("href"):
        f.setForeground(QColor(v["color"]))
    return f


def parse_inline(text):
    """Línea del editor sin su prefijo -> [(tipo, valor, pila)]: ("text", texto), ("br", None), ("void", etiqueta
    vacía tal cual: <a id="x"></a>, <span …/>) o ("icon", <img> de un icono); pila: las etiquetas abiertas, de fuera
    adentro, tal cual. Un cierre sin su apertura queda como texto."""
    items, stack, opened, pos = [], [], [], 0

    def add_text(piece):
        if piece:
            items.append(("text", piece, tuple(stack)))

    for m in TAG.finditer(text):
        add_text(text[pos:m.start()])
        pos = m.end()
        raw, name = m[0], m[2].lower()
        if convert.ICON.fullmatch(raw):
            items.append(("icon", raw, tuple(stack)))
        elif m[1]:
            at = next((n for n in range(len(stack) - 1, -1, -1) if _name(stack[n]) == name), None)
            if at is None:
                add_text(raw)
                continue
            if at == len(stack) - 1 and opened[at] == len(items):  # vacía: <a id="x"></a>
                items.append(("void", stack[at] + raw, tuple(stack[:at])))
            del stack[at:], opened[at:]
        elif m[4] or name in VOID_TAGS:
            items.append(("br", None, tuple(stack)) if name == "br" else ("void", raw, tuple(stack)))
        else:
            stack.append(raw)
            opened.append(len(items))
    add_text(text[pos:])
    return items


def _serialize(seq):
    """[(tipo, valor, etiquetas)] -> HTML con las etiquetas bien anidadas: entre trozo y trozo se cierra lo justo y
    se abre lo que falta (las comunes por fuera siguen abiertas)."""
    out, opened = [], []
    for kind, value, tags in seq:
        same = 0
        while same < min(len(opened), len(tags)) and opened[same] == tags[same]:
            same += 1
        out += [f"</{_name(t)}>" for t in reversed(opened[same:])] + list(tags[same:])
        opened = list(tags)
        out.append("<br/>" if kind == "br" else value)
    out += [f"</{_name(t)}>" for t in reversed(opened)]
    return "".join(out)


def _still(raw, f):
    """¿Sigue la etiqueta raw en una letra con el formato f? Las que la cinta pone o quita (cursiva, negrita…) solo
    si su formato sigue ahí; las demás (span, ruby…) siempre."""
    name = _name(raw)
    va = f.verticalAlignment()
    owned = {"i": f.fontItalic(), "em": f.fontItalic(), "b": f.fontWeight() >= QFont.Weight.DemiBold,
             "strong": f.fontWeight() >= QFont.Weight.DemiBold, "u": f.fontUnderline(), "ins": f.fontUnderline(),
             "s": f.fontStrikeOut(), "del": f.fontStrikeOut(), "strike": f.fontStrikeOut(),
             "sup": va == QTextCharFormat.VerticalAlignment.AlignSuperScript,
             "sub": va == QTextCharFormat.VerticalAlignment.AlignSubScript,
             "sc": f.fontCapitalization() == QFont.Capitalization.SmallCaps}
    if name in owned:
        return owned[name]
    if name == "a" and _attr(raw, "href"):
        return f.isAnchor() and f.anchorHref() == _attr(raw, "href")
    return True


def _wanted(f, stack, para):
    """Etiquetas de una letra al volcar: las suyas que siguen y las que pide lo puesto con la cinta."""
    kept = [t for t in stack if _still(t, f)]
    v = visual(kept, para)
    va = f.verticalAlignment()
    add = []
    if f.isAnchor() and f.anchorHref() and f.anchorHref() != v.get("href"):
        add.append(f'<a href="{html.escape(f.anchorHref())}">')
    if f.fontWeight() >= QFont.Weight.DemiBold and not v.get("bold"):
        add.append("<b>")
    if f.fontItalic() and not v.get("italic"):
        add.append("<i>")
    if f.fontUnderline() and not v.get("underline") and not f.isAnchor():
        add.append("<u>")
    if f.fontStrikeOut() and not v.get("strike"):
        add.append("<s>")
    if f.fontCapitalization() == QFont.Capitalization.SmallCaps and not v.get("smallcaps"):
        add.append("<sc>")
    if va == QTextCharFormat.VerticalAlignment.AlignSuperScript and v.get("valign") != "super":
        add.append("<sup>")
    if va == QTextCharFormat.VerticalAlignment.AlignSubScript and v.get("valign") != "sub":
        add.append("<sub>")
    if f.property(USER_STYLE):
        add.append(f'<span style="{f.property(USER_STYLE)}">')
    return tuple(kept + add)


def _stack_of(f):
    value = f.property(STACK)
    return tuple(value.split(STACK_SEP)) if isinstance(value, str) and value else ()


def paragraph_look(opening):
    """(aspecto de sus letras, {"align", "indent", "quote"}) de un párrafo con esa etiqueta de apertura: el de su
    etiqueta (un título, más grande), el de sus clases en el CSS del libro y el de su style."""
    m = convert.OPENING.match(opening or "")
    if not m:
        return {}, {}
    tag, attrs = m[1].lower(), m[2] or ""
    decls = {"font-weight": "bold", "font-size": HEADING_SIZES.get(tag, "1em")} if tag in convert.HEADING_TAGS else {}
    if CSS:
        decls.update({k: v for k, v in CSS.props("body", []).items() if k == "font-family"})
        decls.update(CSS.props(tag, _attr(attrs, "class").split()))
    decls.update(_style(_attr(attrs, "style")))
    v = {}
    _css(v, decls)
    return v, {"align": decls.get("text-align", "").lower(), "indent": decls.get("text-indent", "").lower(),
               "quote": tag == "blockquote"}


def _retag(opening, tag):
    """La etiqueta de apertura con otro nombre y los mismos atributos: '<p class="x">' -> '<h3 class="x">'."""
    m = convert.OPENING.match(opening)
    return f"<{tag}{m[2] if m and m[2] else ''}>"


def _with_style(opening, key, value):
    """La etiqueta de apertura con key: value en su style (None quita key; sin nada, sin style)."""
    m = convert.OPENING.match(opening)
    tag, attrs = (m[1], m[2] or "") if m else ("p", "")
    decls = _style(_attr(attrs, "style"))
    if value is None:
        decls.pop(key, None)
    else:
        decls[key] = value
    style = "; ".join(f"{k}: {v}" for k, v in decls.items())
    attrs = re.sub(r'\sstyle\s*=\s*"[^"]*"', "", attrs)
    return f'<{tag}{attrs} style="{style}">' if style else f"<{tag}{attrs}>"


# ------------------------------------------------------------------ vista de código
# Cada línea del editor como el elemento que será en el libro, uno por línea; las alineaciones de la app con sus
# clases (las de convert.BASE_CSS) y las ilustraciones como comentario, que se mueven o se borran pero no se escriben
CODE_CLASSES = {"center": '<p class="center">', "right": '<p class="right">', "frame": '<p class="marco">',
                "frame-center": '<p class="marco center">', "frame-right": '<p class="marco right">'} | {
    f"list-{style}": f'<li class="lista-{style}">' for style in convert.LIST_STYLES}
CODE_LINE = re.compile(r'<(p|h[1-6]|li|div|blockquote|dd|dt|pre)((?:\s+[\w:.-]+="[^"<>]*")*)\s*>(.*)</\1>', re.S)
CODE_TOKEN = re.compile(r"<!--\s*(\[[^\]]+\])\s*-->")


def to_code(text):
    """Texto de la hoja (dump) -> su HTML, un elemento por línea."""
    out = []
    for line in (line for line in text.split("\n\n") if line.strip()):
        bare = line.strip()
        if convert.TOKEN.fullmatch(bare) or convert.PICTURE.fullmatch(bare):
            out.append(f"<!-- {bare} -->")
            continue
        prefix, rest = convert.split_prefix(line)
        inner = convert._inline(rest.strip())
        if convert.OPENING.match(prefix):
            opening = prefix
        elif prefix == "# ":
            opening = "<h1>"
        else:
            opening = CODE_CLASSES.get(align_of(line), "<p>")
        out.append(f"{opening}{inner}</{convert.OPENING.match(opening)[1]}>")
    return "\n".join(out)


def from_code(code):
    """HTML de la vista de código -> texto para la hoja (como dump). ValueError con la línea que no se entiende o
    que deja una etiqueta sin cerrar: así no se pierde nada en silencio."""
    lines = []
    for n, raw in enumerate(code.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        token = CODE_TOKEN.fullmatch(line)
        if token:
            lines.append(token[1])
            continue
        m = CODE_LINE.fullmatch(line)
        if not m:
            if line.startswith("<") and convert.OPENING.match(line):
                raise ValueError(f"Línea {n}: el párrafo no se cierra en la misma línea: {line[:60]}")
            m = CODE_LINE.fullmatch(f"<p>{line}</p>")  # texto suelto: un párrafo
        tag, attrs, inner = m[1], m[2] or "", m[3]
        if not convert._balanced(inner):
            raise ValueError(f"Línea {n}: una etiqueta no se cierra o se cierra fuera de orden: {line[:60]}")
        text = html.unescape(convert.NOTE.sub(r"[Nota: \1]", inner).replace(convert.SC_OPEN, "<sc>")
                             .replace(convert.SC_CLOSE, "</sc>"))
        opening = f"<{tag}{attrs}>"
        prefix = ("" if opening == "<p>" else "# " if opening == "<h1>" else
                  convert.ALIGN_PREFIX.get(next((a for a, o in CODE_CLASSES.items() if o == opening), ""), "") or opening)
        lines.append(prefix + text.strip())
    return "\n\n".join(lines)


class CodeLook(QSyntaxHighlighter):
    """Colores de la vista de código: etiquetas, atributos, sus valores y comentarios."""

    def __init__(self, document, colors):
        super().__init__(document)
        self.colors = colors  # {"tag", "attr", "value", "comment"}: colores del tema

    def highlightBlock(self, text):
        def paint(start, size, key):
            f = QTextCharFormat()
            f.setForeground(QColor(self.colors[key]))
            self.setFormat(u16(text, start), u16(text, start + size) - u16(text, start), f)
        for m in re.finditer(r"<[^<>]*>", text):
            paint(m.start(), m.end() - m.start(), "tag")
            for a in re.finditer(r'([\w:.-]+)(=)("[^"]*")', m[0]):
                paint(m.start() + a.start(1), len(a[1]), "attr")
                paint(m.start() + a.start(3), len(a[3]), "value")
        for m in CODE_TOKEN.finditer(text):
            paint(m.start(), m.end() - m.start(), "comment")


def is_heading_line(line):
    """¿Es un título? «# Nombre» o una línea con su etiqueta <h1>…<h6>."""
    m = convert.OPENING.match(line)
    return line.startswith("# ") or bool(m and m[1].lower() in convert.HEADING_TAGS)


def block_look(block):
    """paragraph_look del párrafo de la hoja (su etiqueta de apertura, si la tiene)."""
    return paragraph_look(block.blockFormat().property(OPENING) or "")


def char_format(italic=False, bold=False, href="", sup=False, sc=False):
    """Formato de carácter de un trozo de _runs."""
    f = link_format(href) if href else QTextCharFormat()
    f.setFontItalic(italic)
    f.setFontWeight(QFont.Weight.Bold if bold else QFont.Weight.Normal)
    f.setVerticalAlignment(QTextCharFormat.VerticalAlignment.AlignSuperScript if sup
                           else QTextCharFormat.VerticalAlignment.AlignNormal)
    f.setFontCapitalization(QFont.Capitalization.SmallCaps if sc else QFont.Capitalization.MixedCase)
    return f


def link_format(href, f=None):
    """Formato de carácter de un enlace (o sin enlace, con href vacío): subrayado y en color, como Word."""
    f = f or QTextCharFormat()
    f.setAnchor(bool(href))
    f.setAnchorHref(href)
    f.setFontUnderline(bool(href))
    if href:
        f.setForeground(QColor(LINK_COLOR))
    else:
        f.clearForeground()
    return f


def _insert_marked(cursor, text, color="#888888", para=None):
    """Texto de una línea del editor como se ve en el libro (visual): cursiva, negrita, enlaces, furigana… y las
    etiquetas que no se ven guardadas en cada letra (STACK); los iconos (convert.ICON) como imagen de la altura de
    la letra, del color del texto como los separadores. para: aspecto del párrafo (paragraph_look)."""
    for kind, value, stack in parse_inline(text):
        f = look_format(visual(stack, para))
        if stack:
            f.setProperty(STACK, STACK_SEP.join(stack))
        if kind == "text":
            cursor.insertText(value, f)
            continue
        if kind == "br":
            cursor.insertText(LINE_BREAK, f)
            continue
        if kind == "void":
            f.setProperty(VOID, value)
            cursor.insertText(VOID_CHAR, f)
            continue
        m = convert.ICON.fullmatch(value)
        marks = f
        picture = QImage.fromData(base64.b64decode(m[1]))
        tinted = QPainter(picture)
        tinted.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        tinted.fillRect(picture.rect(), QColor(color))
        tinted.end()
        name = f"icono:{hash(m[1])}"
        cursor.document().addResource(QTextDocument.ResourceType.ImageResource, QUrl(name), picture)
        f = QTextImageFormat()
        f.merge(marks)
        f.setName(name)
        height = QFontMetrics(cursor.document().defaultFont()).height() * 0.8
        f.setHeight(height)
        f.setWidth(picture.width() * height / max(picture.height(), 1))
        f.setVerticalAlignment(QTextCharFormat.VerticalAlignment.AlignMiddle)
        f.setProperty(ICON, value)
        cursor.insertImage(f)


def _symbol(text, color, height=28, width=420):
    """Imagen con el texto de un separador ("* * *", "☆"): en la hoja va como un objeto que no se edita."""
    font = QFont("Segoe UI", 14)
    # «◇» × 42 (Oukoku 4): letra menor hasta caber; la imagen encogida perdía los trazos finos
    while QFontMetrics(font).horizontalAdvance(text) + 8 > width and font.pointSizeF() > 4:
        font.setPointSizeF(font.pointSizeF() - 0.5)
    metrics = QFontMetrics(font)
    image = QImage(max(metrics.horizontalAdvance(text) + 8, 16), height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setFont(font)
    painter.setPen(QColor(color))
    painter.drawText(image.rect(), Qt.AlignmentFlag.AlignCenter, text)
    painter.end()
    return image


def _insert_object(cursor, kind, n, pictures, color):
    """[Ilustración N] / [Separador N] / [Imagen nombre]: su imagen (o el texto del separador, o la marca si falta)
    como objeto."""
    if kind == "Imagen" and n not in pictures and IMAGES:  # borrador de otra sesión: la imagen está en su archivo
        pictures[n] = QImage(str(Path(IMAGES) / n))
    picture = pictures.get(n)
    if not isinstance(picture, QImage) or picture.isNull():
        picture = _symbol(picture if isinstance(picture, str) and picture else f"[{kind} {n}]", color)
    name = f"bloque:{n}"
    cursor.document().addResource(QTextDocument.ResourceType.ImageResource, QUrl(name), picture)
    f = QTextImageFormat()
    f.setName(name)
    scale = min(1.0, THUMB / max(picture.height(), 1), 420 / max(picture.width(), 1))
    f.setWidth(picture.width() * scale)
    f.setHeight(picture.height() * scale)
    f.setProperty(OBJECT, f"{kind} {n}")
    cursor.insertImage(f)


def _insert_title(cursor, picture, text, k):
    """El dibujo de un título como objeto del ancho de la hoja; ICON guarda su texto para dump."""
    name = f"titulo:{k}"
    cursor.document().addResource(QTextDocument.ResourceType.ImageResource, QUrl(name), picture)
    f = QTextImageFormat()
    f.setName(name)
    scale = min(1.0, 420 / max(picture.width(), 1))
    f.setWidth(picture.width() * scale)
    f.setHeight(picture.height() * scale)
    f.setProperty(ICON, text)
    f.setToolTip(html.unescape(convert.TAGS.sub("", text)))
    cursor.insertImage(f)


def _strip(line):
    """La línea sin su prefijo de section_text: "# ", "<center>", "<marco>" o "<derecha>"."""
    return convert.split_prefix(line)[1]


def align_format(f, how=None):
    """Párrafo centrado ("center"), a la derecha ("right") o a la izquierda (None): se excluyen entre sí."""
    for prop, name in ((CENTERED, "center"), (RIGHTED, "right")):
        if how == name:
            f.setProperty(prop, True)
        else:
            f.clearProperty(prop)
    f.setAlignment({"center": Qt.AlignmentFlag.AlignHCenter, "right": Qt.AlignmentFlag.AlignRight}.get(
        how, Qt.AlignmentFlag.AlignLeft))
    return f


def align_of(line):
    """Alineación de una línea de section_text, como Block.align: "", "center", "frame-right", "list-decimal"…"""
    prefix = convert.split_prefix(line)[0]
    return next((a for a, p in convert.ALIGN_PREFIX.items() if p == prefix), "")


def side_of(align):
    """"center", "right" o None (izquierda) de una alineación, también dentro de un recuadro ("frame-center")."""
    return next((side for side in ("center", "right") if align.endswith(side)), None)


def list_style(block):
    """Estilo (nombre de CSS) de la lista del párrafo, o None si no está en una."""
    group = block.textList()
    if group is None:
        return None
    style = group.format().style()
    return next((name for name, qt in LISTS.items() if qt == style), "disc")  # pegada de fuera, con otro estilo


def frame_format(f, first=False, last=False, sub=False):
    """Formato de un párrafo en recuadro: cerca unos de otros y con sitio para el borde. Conserva su alineación."""
    f.setProperty(FRAMED, True)
    f.setLeftMargin(FRAME_PAD + (SUB if sub else 0))
    f.setRightMargin(FRAME_PAD)
    f.setTopMargin(FRAME_PAD if first else FRAME_GAP)
    f.setBottomMargin(FRAME_PAD + SPACE if last else 0)
    return f


def unframe_format(f):
    """Saca el párrafo del recuadro: sus márgenes de párrafo normal."""
    f.clearProperty(FRAMED)
    f.setLeftMargin(0)
    f.setRightMargin(0)
    f.setTopMargin(0)
    f.setBottomMargin(SPACE)
    return f


def _frame_runs(lines):
    """{posición en lines: (primera de su recuadro, última, sangrada)} de las líneas con <marco>."""
    out, run = {}, []
    for i, (_, line) in enumerate(lines + [(None, "")]):
        if align_of(line).startswith("frame"):
            run.append(i)
            continue
        bulleted = any(_strip(lines[j][1]).lstrip()[:1] in convert.BULLETS for j in run)
        for j in run:
            out[j] = (j == run[0], j == run[-1],
                      bulleted and _strip(lines[j][1]).lstrip()[:1] not in convert.BULLETS)
        run = []
    return out


def insert_lines(cursor, lines, pictures, remember=False, raw=False, color="#888888", levels=None, objects=True):
    """Pone lines ([(bloque, línea de section_text)]) en el cursor. La primera línea de texto va dentro del
    párrafo del cursor (pegar una palabra no parte el párrafo); el resto, en párrafos nuevos. remember: carga
    del editor, cada párrafo recuerda su línea. raw: texto de fuera, sin leer marcas. levels: {bloque: tipo
    de su fila} de los títulos. objects=False: "[Ilustración N]" queda como texto (pegado de fuera)."""
    frames = {} if raw else _frame_runs(list(lines))
    before = None  # estilo de lista de la línea anterior: las seguidas del mismo estilo son una sola lista
    for i, (k, line) in enumerate(lines):
        bare = line.strip()
        token = None if raw or not objects else convert.TOKEN.fullmatch(bare) or convert.PICTURE.fullmatch(bare)
        opened = None if raw else convert.OPENING.match(line)  # <p class="no-indent">, <h3>: su etiqueta
        heading = not raw and (line.startswith("# ") or bool(opened and opened[1].lower() in convert.HEADING_TAGS))
        para, shape = paragraph_look(opened[0]) if opened else ({}, {})
        align = "" if raw else align_of(line)
        centered, right = side_of(align) == "center", side_of(align) == "right"
        listed = align[5:] if align.startswith("list-") else None
        framed = i in frames
        scene = not raw and not token and bool(bare) and bool(convert.SCENE_BREAK.fullmatch(bare))
        drawn = pictures.get(f"título {k}") if heading and k is not None else None
        special = bool(token or heading or scene or centered or framed or right or listed or opened)
        if i or (special and cursor.block().length() > 1):
            cursor.insertBlock(QTextBlockFormat(), _plain_format())
        if i or special or remember:
            f = QTextBlockFormat()
            f.setBottomMargin(SPACE)
            if k is not None:
                f.setProperty(SOURCE, k)
            if token or scene or centered:
                f.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            if centered or right:
                align_format(f, "center" if centered else "right")
            if framed:
                frame_format(f, *frames[i])
            if heading:
                f.setProperty(HEADING, True)
                if drawn is not None:  # el dibujo del título va centrado, como en el libro
                    f.setAlignment(Qt.AlignmentFlag.AlignHCenter)
                f.setTopMargin(SPACE * 2)
                if levels and levels.get(k):
                    f.setProperty(LEVEL, levels[k])
            if opened:
                f.setProperty(OPENING, opened[0])
                f.setProperty(BOOK_LOOK, True)
                shape_format(f, shape)
            cursor.setBlockFormat(f)
        if listed:
            group = cursor.block().previous().textList() if listed == before else None
            if group is not None:
                group.add(cursor.block())
            else:
                cursor.createList(LISTS[listed])
        before = listed
        if token:
            n = token.group(2)
            _insert_object(cursor, token.group(1), int(n) if n.isdigit() else n, pictures, color)
        elif drawn is not None:  # título dibujado (Witch 1): se ve el dibujo y vuelve a section_text como su texto
            _insert_title(cursor, drawn, _strip(line), k)
        elif raw:
            cursor.insertText(line, _plain_format())
        else:
            _insert_marked(cursor, line if raw else _strip(line), color, para)
        if remember:
            f = cursor.blockFormat()
            f.setProperty(ORIGINAL, line)
            f.setProperty(CANON, _canonical(line))
            cursor.setBlockFormat(f)


def load(document, lines, pictures, color="#888888", levels=None):
    """Llena la hoja con las líneas de section_text de cada bloque ([(k, línea)]); pictures: {N: QImage o
    texto del separador}; levels: {k: tipo de fila} de los títulos. Deshacer empieza vacío."""
    document.clear()
    insert_lines(QTextCursor(document), lines, pictures, remember=True, color=color, levels=levels)
    document.clearUndoRedoStacks()


def shape_format(f, shape):
    """Formato de párrafo de un aspecto de paragraph_look: alineación, sangría de primera línea y cita."""
    align = {"center": Qt.AlignmentFlag.AlignHCenter, "right": Qt.AlignmentFlag.AlignRight,
             "justify": Qt.AlignmentFlag.AlignJustify}.get(shape.get("align"), Qt.AlignmentFlag.AlignLeft)
    f.setAlignment(align)
    m = re.fullmatch(r"([\d.]+)\s*(em|px|%)?", shape.get("indent", "") or "")
    f.setTextIndent(0 if not m else float(m[1]) * {"em": FONT_SIZE * 1.33, "%": 4.2}.get(m[2], 1))
    f.setLeftMargin(36 if shape.get("quote") else 0)
    f.setRightMargin(36 if shape.get("quote") else 0)
    return f


def _canonical(line):
    """Lo que _lines daría de la línea recién cargada, sin recorrer el documento (cargar el libro entero)."""
    bare = line.strip()
    if convert.TOKEN.fullmatch(bare):
        return bare
    text = _serialize(parse_inline(_strip(line))).strip()
    return line[:len(line) - len(_strip(line))] + text if text else ""


def _lines(block):
    """Líneas de section_text de un párrafo de la hoja: su texto con sus etiquetas (las que tenía y las que pide
    lo puesto con la cinta), y cada objeto en su línea."""
    f = block.blockFormat()
    heading = bool(f.property(HEADING))
    side = "center" if f.property(CENTERED) else "right" if f.property(RIGHTED) else ""
    align = (f"frame-{side}".rstrip("-") if f.property(FRAMED) else f"list-{list_style(block)}" if block.textList()
             else side)
    opening = f.property(OPENING)
    prefix = opening if opening else "# " if heading else convert.ALIGN_PREFIX.get(align, "")
    para = paragraph_look(opening)[0] if opening else {}
    out, seq = [], []

    def flush():
        text = _serialize(seq).strip()
        seq.clear()
        if text:
            out.append(prefix + text)

    it = block.begin()
    while not it.atEnd():
        fragment = it.fragment()
        f = fragment.charFormat()
        it += 1
        name = f.property(OBJECT) if f.isImageFormat() else None
        if name:
            flush()
            out.append(f"[{name}]")
            continue
        tags = _wanted(f, _stack_of(f), para)
        icon = f.property(ICON) if f.isImageFormat() else None
        if icon:
            seq.append(("icon", icon, tags))
            continue
        if f.property(VOID):
            seq += [("void", f.property(VOID), tags)] * fragment.text().count(VOID_CHAR)
            continue
        text = fragment.text().replace("￼", "").replace(VOID_CHAR, "")
        for n, piece in enumerate(text.split(LINE_BREAK)):  # Mayús+Intro: <br/>
            if n:
                seq.append(("br", None, tags))
            if piece:
                seq.append(("text", piece, tags))
    flush()
    return out


def block_lines(block):
    """Como _lines, pero un párrafo que vuelve a estar como al cargar da su línea original (mismo texto
    para edit_section: conserva su página y su recuadro)."""
    lines = _lines(block)
    f = block.blockFormat()
    if f.property(ORIGINAL) is not None and "\n\n".join(lines) == f.property(CANON):
        return [f.property(ORIGINAL)]
    return lines


def dump(document):
    """El texto de la hoja en el formato de section_text."""
    out, block = [], document.firstBlock()
    while block.isValid():
        out += [line for line in block_lines(block) if line.strip()]
        block = block.next()
    return "\n\n".join(out)


def dump_levels(document):
    """Las líneas de dump con el tipo de cada título ("Capítulo", "Subtítulo"…; None en lo demás)."""
    out, block = [], document.firstBlock()
    while block.isValid():
        f = block.blockFormat()
        level = (f.property(LEVEL) or "Capítulo") if f.property(HEADING) else None
        out += [(line, level if is_heading_line(line) else None) for line in block_lines(block) if line.strip()]
        block = block.next()
    return out


def objects_of(block):
    """Marcas de los objetos del párrafo ("Ilustración 3")."""
    found, it = [], block.begin()
    while not it.atEnd():
        f = it.fragment().charFormat()
        if f.isImageFormat() and f.property(OBJECT):
            found.append(f.property(OBJECT))
        it += 1
    return found


def original_of(block):
    """Línea con que se cargó el párrafo, o None si es nuevo. PySide da [] por una propiedad "" (párrafo vacío)."""
    value = block.blockFormat().property(ORIGINAL)
    return "" if value == [] else value


def source_of(block):
    """Bloque del libro del que viene el párrafo: el de su objeto, o el que recuerda su formato."""
    objects = [name for name in objects_of(block) if name.split()[1].isdigit()]  # una imagen nueva no es del libro
    if objects:
        return int(objects[0].split()[1])
    value = block.blockFormat().property(SOURCE)
    return int(value) if value is not None else None


def _is_text(block):
    """Párrafo de texto normal (ni título, ni objeto, ni separador escrito, ni vacío)."""
    bare = block.text().strip()
    return (bool(bare) and not block.blockFormat().property(HEADING) and not objects_of(block)
            and not convert.SCENE_BREAK.fullmatch(bare))


def _matches(text, words):
    """Las palabras de text que están en words (en minúscula), sin distinguir mayúsculas: «Fin» si está «fin»."""
    return (m for m in WORD.finditer(text) if m[0].lower() in words) if words else iter(())


WORD = re.compile(r"\w+")  # como (?<!\w)palabra(?!\w): «abc1» no es «abc»
REPAINT_BATCH = 40  # párrafos subrayados por tanda (Look._repaint)


_thesaurus = None  # {palabra en minúsculas: [[sinónimos de cada acepción]]}, leído la primera vez que se pide


def synonyms(word, limit=12):
    """Sinónimos de word del tesauro de LibreOffice (OpenThesaurus-es, dict/th_es_v2.dat), sin repetir ni la
    propia palabra, alternando sus acepciones («casa»: vivienda, hogar, edificio…) para que las 12 no sean de
    una sola. El tesauro solo trae lemas: si no está, se prueba el singular y el masculino.
    shortcut: las formas verbales conjugadas («corría») no tienen sinónimos; con Hunspell se podría buscar su infinitivo."""
    global _thesaurus
    if _thesaurus is None:
        _thesaurus = {}
        try:
            lines = (convert.SPELL_DIR / "th_es_v2.dat").read_text(encoding="latin-1").splitlines()
        except OSError:
            lines = []  # sin el archivo, sin sinónimos: el menú no los ofrece
        i = 1  # la primera línea es la codificación
        while i < len(lines):
            head, _, count = lines[i].partition("|")
            n = int(count) if count.isdigit() else 0
            found = _thesaurus.setdefault(head.lower(), [])
            for line in lines[i + 1:i + 1 + n]:
                found.append([re.sub(r"\s*\(.*?\)", "", w).strip() for w in line.split("|")[1:]])  # «necio (fig.)»
            i += 1 + n
    low = word.lower()
    # (lema, ¿plural?, ¿femenino?): lo hallado vuelve a la forma de word («casas» → «hogares», «bonita» → «linda»)
    tries = [(low, False, False)] + ([(low[:-2], True, False), (low[:-1], True, False)] if low.endswith("es")
                                     else [(low[:-1], True, False)] if low.endswith("s") else [])
    tries += [(key[:-1] + "o", plural, True) for key, plural, _ in tries if key.endswith("a")]
    out = []
    for key, plural, feminine in tries:
        senses = _thesaurus.get(key, [])
        for n in range(max(map(len, senses), default=0)):
            for w in (sense[n] for sense in senses if n < len(sense)):
                if " " not in w:  # una locución («en seguida») no se declina
                    w = w[:-1] + "a" if feminine and w.endswith("o") else w
                    w = _plural(w) if plural and w else w
                if w and w.lower() not in (low, key) and w not in out:
                    out.append(w)
        if out:
            break
    return out[:limit]


def _plural(word):
    """Plural español sencillo: «casa» → «casas», «hogar» → «hogares», «edificación» → «edificaciones»; lo que
    ya acaba en s o x («crisis») queda igual."""
    if word[-1] in "aeiouáéíóú":
        return word + "s"
    if word[-1] in "sx":
        return word
    if len(word) > 1 and word[-2] in "áéíóú":  # la tilde de «-ción» se va al ganar una sílaba
        word = word[:-2] + "aeiou"["áéíóú".index(word[-2])] + word[-1]
    return word + "es"


def match_case(model, word):
    """word con las mayúsculas de model: «CASA» → «HOGAR», «Casa» → «Hogar»."""
    if model.isupper() and len(model) > 1:
        return word.upper()
    return word[:1].upper() + word[1:] if model[:1].isupper() else word


def plain_line(line):
    """Texto de una línea de section_text como se ve en la hoja: sin su prefijo ni sus marcas de formato."""
    # el icono es un objeto: un carácter; una etiqueta vacía, el invisible que la lleva
    shown = {"text": None, "br": LINE_BREAK, "void": VOID_CHAR, "icon": "￼"}
    return "".join(shown[kind] or value for kind, value, _ in parse_inline(_strip(line)))


def added_spans(before, after):
    """[(inicio, largo)] de lo puesto en after que no estaba en before, palabra a palabra."""
    x, y = re.findall(r"\S+|\s+", before), re.findall(r"\S+|\s+", after)
    starts = [0]
    for token in y:
        starts.append(starts[-1] + len(token))
    return [(starts[j1], starts[j2] - starts[j1]) for op, _, _, j1, j2 in
            difflib.SequenceMatcher(None, x, y, autojunk=False).get_opcodes() if op in ("replace", "insert")]


def settle(document):
    """Maqueta la hoja entera ya. Qt lo hace a trozos, y pageCount() (que debería acabarla) deja párrafos sin
    líneas si a mitad cambia el ancho (aparece la barra de desplazamiento) o llega otro cambio: huecos con el texto
    «debajo» hasta ensanchar la hoja (Nageki 1: 319 de 361 párrafos al pasar de capítulo). Si queda alguno, se
    maqueta todo de nuevo, que no va a trozos."""
    document.pageCount()
    block = document.firstBlock()
    while block.isValid():
        if block.length() > 1 and not block.layout().lineCount():
            document.markContentsDirty(0, document.characterCount())
            return
        block = block.next()


class Look(QSyntaxHighlighter):
    """Aspecto que no se guarda: títulos grandes, notas en gris, separadores escritos y palabras desconocidas
    subrayadas. Solo pinta: no cambia el texto ni entra en deshacer."""

    def __init__(self, document):
        super().__init__(document)
        self.unknown = set()
        self.job = 0  # tanda de repintado en curso (_repaint)
        self.tags = {}
        self.changes = None  # color con que se subraya lo puesto en cada párrafo cambiado; None: no se marca

    def show_changes(self, color):
        """«Mostrar cambios»: subraya, del color dado (None lo quita), lo que se puso frente al original."""
        self.changes = color
        self.rehighlight()

    def set_tags(self, tags):
        """Colores de theme.editor_tags del tema actual."""
        self.tags = tags
        self.rehighlight()

    def set_unknown(self, words, changed=None):
        """Palabras que se subrayan. changed: solo se repintan los párrafos con esas (las nuevas o las que dejan
        de estarlo); repintar la hoja entera tardaba 1 s en un capítulo largo. Un conjunto, no una expresión con
        todas: con un libro sin traducir son miles y la ventana se congelaba (Eminence 7)."""
        self.unknown = {w.lower() for w in words}
        if changed is None:
            self.rehighlight()
            return
        changed, blocks = {w.lower() for w in changed}, []
        block = self.document().firstBlock()
        while changed and block.isValid():
            if any(True for _ in _matches(block.text(), changed)):
                blocks.append(block)
            block = block.next()
        self.job += 1
        self._repaint(self.job, blocks)

    def _repaint(self, job, blocks):
        """Repinta de REPAINT_BATCH en REPAINT_BATCH párrafos y deja respirar a la ventana entre tandas: los ~600
        de un capítulo sin traducir, de golpe, la congelaban 1 s. Una llamada nueva a set_unknown cancela esta."""
        if job != self.job:
            return
        settle(self.document())  # maquetada entera antes de tocar unos párrafos (ver Sheet.changeEvent)
        for block in blocks[:REPAINT_BATCH]:
            if block.isValid():  # borrado mientras tanto: ya no hay nada que pintar
                self.rehighlightBlock(block)
        if blocks[REPAINT_BATCH:]:
            QTimer.singleShot(0, self, lambda: self._repaint(job, blocks[REPAINT_BATCH:]))  # sin hoja, no corre

    def matches(self, text):
        """Las palabras subrayadas de text, como re.Match."""
        return _matches(text, self.unknown)

    def color(self, tag):
        return QColor(self.tags.get(tag, {}).get("foreground", "#888888"))

    def highlightBlock(self, text):
        block = self.currentBlock()
        # setFormat cuenta en UTF-16 y text en caracteres: tras un emoji (dos posiciones) se subrayaba otra cosa
        wide = any(ord(c) > 0xFFFF for c in text)

        def at(i):
            return u16(text, i) if wide else i
        if block.blockFormat().property(HEADING) and not block.blockFormat().property(BOOK_LOOK):  # el libro manda
            f = QTextCharFormat()
            f.setFontWeight(QFont.Weight.Bold)
            # Capítulo grande; subtítulo un punto menor, como Título 1 y Título 2 en Word
            subtitle = block.blockFormat().property(LEVEL) not in (None, "Capítulo")
            f.setProperty(QTextFormat.Property.FontSizeAdjustment, 1 if subtitle else 2)
            f.setForeground(self.color("heading"))
            self.setFormat(0, at(len(text)), f)
        elif text.strip() and convert.SCENE_BREAK.fullmatch(text.strip()):
            f = QTextCharFormat()
            f.setForeground(self.color("scene"))
            self.setFormat(0, at(len(text)), f)
        for m in NOTE.finditer(text):
            f = QTextCharFormat()
            f.setForeground(self.color("mark"))
            self.setFormat(at(m.start()), at(m.end()) - at(m.start()), f)
        broken = self.color("broken")  # fuera del bucle: se pinta letra a letra
        for m in self.matches(text):
            # Encima de lo que ya tenga (título, nota), por tramos del mismo formato: letra a letra se perdía el
            # ajuste entre letras, la palabra se ensanchaba y el párrafo ganaba una línea al llegar el corrector
            c, end = at(m.start()), at(m.end())
            while c < end:
                base, stop = self.format(c), c + 1
                while stop < end and self.format(stop) == base:
                    stop += 1
                f = QTextCharFormat(base)
                f.setUnderlineStyle(QTextCharFormat.UnderlineStyle.SpellCheckUnderline)
                f.setUnderlineColor(broken)
                self.setFormat(c, stop - c, f)
                c = stop
        if self.changes and text.strip() and not objects_of(block):
            original = original_of(block)
            if original is None:
                spans = [(0, len(text))]  # párrafo nuevo: todo es lo puesto
            elif block_lines(block) == [original]:
                spans = []
            else:
                spans = added_spans(plain_line(original), text)
            changes = QColor(self.changes)
            for start, size in spans:
                for c in range(at(start), at(start + size)):
                    f = QTextCharFormat(self.format(c))
                    f.setUnderlineStyle(QTextCharFormat.UnderlineStyle.SingleUnderline)
                    f.setUnderlineColor(changes)
                    f.setForeground(changes)
                    self.setFormat(c, 1, f)


class _Filter(QObject):
    def __init__(self, parent, handler):
        super().__init__(parent)
        self.handler = handler

    def eventFilter(self, obj, event):
        return bool(self.handler(event))


def watch(widget, handler):
    """handler(evento) para cada evento de widget; True lo consume (Ctrl+rueda, cambio de tamaño)."""
    widget.installEventFilter(_Filter(widget, handler))


class Sheet(QTextEdit):
    """La hoja: un QTextEdit con atajos de Word en español, pegar solo texto de fuera y objetos únicos."""
    command = Signal(str)  # atajos que resuelve la ventana: "link" (Ctrl+Alt+K), "find" (Ctrl+F), "replace" (Ctrl+H)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sheet")
        self.document().setDocumentMargin(48)
        self.look = Look(self.document())
        self.tags, self.unknown = {}, set()  # colores y palabras desconocidas de cada hoja nueva
        self.pictures, self.color = {}, "#888888"
        self.bars, self.bar_color = [], QColor("#2e9d4a")  # párrafos cambiados: barrita en el margen
        # Fondo tenue del párrafo del cursor: el original lo recuadra, y en la hoja no se veía dónde se estaba.
        # Del acento del tema, casi transparente: sigue al tema aunque cambie con el editor abierto
        self.mark_current = False  # TextEditor lo enciende
        self.foreign = None  # k -> bytes de la ilustración k si es de otro capítulo, o None (TextEditor), para adopt
        self.cursorPositionChanged.connect(lambda: self.mark_current and self.viewport().update())
        self.origins = {}  # documento -> [(bloque, línea)] tal como se cargó
        self.known = {}  # documento -> {párrafo: (revisión, ¿cambiado?)} de changed_blocks, hasta que cambie
        self.paints = collections.deque(maxlen=60)  # últimos repintados (zona, desplazamiento), para diagnose
        self.set_zoom(100)

    def set_content(self, lines, pictures, color="#888888", levels=None):
        """Hoja nueva con lines; la de antes sigue viva (con su deshacer) para volver con use(). levels: {k:
        tipo de fila} de los títulos. Devuelve (documento, aspecto)."""
        self.pictures.update(pictures)
        self.color = color
        document = QTextDocument(self)
        document.setDocumentMargin(48)
        load(document, lines, self.pictures, color, levels)
        self.origins[document] = list(lines)
        loaded = {}  # cómo se volcaba cada párrafo al cargar -> [(bloque, línea)], para reconocerlo si vuelve
        for k, line in lines:
            if k is not None and not convert.TOKEN.fullmatch(line.strip()):
                loaded.setdefault(_canonical(line), []).append((k, line))
        document.contentsChange.connect(lambda pos, removed, added: self.relink(document, loaded, pos, added))
        self.known[document] = {}
        document.contentsChange.connect(lambda pos, removed, added: self.forget(document, pos, added))
        # Letra y hoja antes del aspecto: con él puesto, cambiarlas volvía a colorear todo (3 veces al abrir)
        self.setDocument(document)
        document.setDefaultFont(self.font())
        # Strange Grief 1 (432 párrafos): al llegar las palabras del corrector un párrafo de arriba cambiaba de alto,
        # los de abajo se movían y Qt no volvía a pintar lo visible: huecos con el texto «debajo» hasta mover el
        # divisor. Con cada cambio de alto se pinta la hoja entera
        document.documentLayout().documentSizeChanged.connect(lambda size: self.viewport().update())
        document.documentLayout().update.connect(lambda rect: self.viewport().update())
        look = self.look = Look(document)  # después de cargar: colorea una vez, no párrafo a párrafo
        look.tags = self.tags
        look.set_unknown(self.unknown)
        settle(document)  # maqueta la hoja entera ya: Qt lo hace a trozos y pintaba con ella a medias
        self.moveCursor(QTextCursor.MoveOperation.Start)
        return document, look

    def refresh_picture(self, n, picture):
        """La ilustración del bloque n cambió: la nueva imagen, con su tamaño, en todas las hojas que la tienen."""
        self.pictures[n] = picture
        for document in list(self.origins):
            block = document.firstBlock()
            while block.isValid():
                if f"Ilustración {n}" in objects_of(block):
                    cursor = QTextCursor(block)
                    cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
                    cursor.beginEditBlock()
                    cursor.removeSelectedText()
                    _insert_object(cursor, "Ilustración", n, self.pictures, self.color)
                    cursor.endEditBlock()  # el texto no cambia (misma marca): no cuenta como cambio al aceptar
                block = block.next()
        self.viewport().update()

    def set_draft(self, lines, text, pictures, color="#888888", levels=None):
        """Como set_content, con text (un borrador de dump) sobre las líneas cargadas lines: un párrafo igual
        conserva su origen y su original; uno cambiado en su sitio recuerda el que reemplaza (F7 lo encuentra y
        «Volver al original» lo deshace); uno nuevo toma el origen de la línea vieja de su sitio o la anterior."""
        old = [line for _, line in lines]
        new = [line for line in text.split("\n\n") if line.strip()]
        pairs, originals = [], []
        for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
            for n in range(j2 - j1):
                if op == "equal" or (op == "replace" and i2 - i1 == j2 - j1):
                    pairs.append((lines[i1 + n][0], new[j1 + n]))
                    originals.append(old[i1 + n])
                    continue
                at = min(i1 + n, i2 - 1) if i2 > i1 else i1 - 1
                pairs.append((lines[at][0] if 0 <= at < len(lines) else None, new[j1 + n]))
                originals.append(None)
        document, look = self.set_content(pairs, pictures, color, levels)
        self.origins[document] = list(lines)
        block, n = document.firstBlock(), 0
        self.relinking = True  # solo se corrige lo que recuerda cada párrafo: no es una edición
        try:
            while block.isValid() and n < len(originals):
                f = block.blockFormat()
                if originals[n] is None:
                    f.clearProperty(ORIGINAL)
                    f.clearProperty(CANON)
                else:
                    f.setProperty(ORIGINAL, originals[n])
                    f.setProperty(CANON, _canonical(originals[n]))
                QTextCursor(block).setBlockFormat(f)
                block, n = block.next(), n + 1
        finally:
            self.relinking = False
        document.clearUndoRedoStacks()
        return document, look

    def forget(self, document, pos, added):
        """Los párrafos tocados (texto, formato o deshacer) se vuelven a comparar en changed_blocks."""
        known, block, last = self.known[document], document.findBlock(pos), document.findBlock(pos + added)
        while block.isValid():
            known.pop(block.fragmentIndex(), None)
            if block == last:
                break
            block = block.next()

    def relink(self, document, loaded, pos, added):
        """Al partir un párrafo, el nuevo copia el formato del de arriba y con él su origen. Si el trozo nuevo
        vuelve a ser un párrafo cargado cuyo bloque ya no tiene párrafo (se unió y se vuelve a separar),
        recupera ese bloque: el PDF lo recuadra a él y se guarda tal cual. En el mismo paso de deshacer."""
        if getattr(self, "relinking", False):
            return
        block, last = document.findBlock(pos), document.findBlock(pos + added)
        before = source_of(block.previous()) if block.previous().isValid() else None
        used = None  # orígenes en la hoja: se miran una vez por cambio, no por párrafo (pegar 300 tardaba 86 s)
        while block.isValid():
            k = source_of(block)
            # el del de arriba de antes de recuperar el suyo: si no, solo se recuperaba un párrafo de cada dos
            if before is not None and k == before and not objects_of(block):
                if used is None:
                    used, other = set(), document.firstBlock()
                    while other.isValid():
                        used.add(source_of(other))
                        other = other.next()
                text = "\n\n".join(_lines(block))
                match = next(((j, line) for j, line in loaded.get(text, ()) if j not in used), None)
                if match:
                    used.add(match[0])
                    f = block.blockFormat()
                    f.setProperty(SOURCE, match[0])
                    f.setProperty(ORIGINAL, match[1])
                    f.setProperty(CANON, text)
                    cursor = QTextCursor(block)
                    self.relinking = True
                    try:
                        cursor.joinPreviousEditBlock()
                        cursor.setBlockFormat(f)
                        cursor.endEditBlock()
                    finally:
                        self.relinking = False
            before = k
            if block == last:
                break
            block = block.next()

    def use(self, document, look):
        """Muestra otra hoja ya cargada, con el tamaño de letra de ahora."""
        self.setDocument(document)
        document.setDefaultFont(self.font())
        self.look = look
        settle(document)  # maquetada entera, como al cargarla

    def scrollContentsBy(self, dx, dy):
        """Al desplazarse Qt mueve lo ya pintado y solo dibuja la franja nueva: si la maquetación cambió entre
        tanto (salto a un párrafo lejano en un capítulo largo), quedaban restos y huecos. Se pinta todo."""
        super().scrollContentsBy(dx, dy)
        self.viewport().update()

    def changeEvent(self, event):
        """Un cambio de letra o de estilo (tema, pantalla completa, un menú que se repinta) hace que Qt maquete la
        hoja otra vez, a trozos. Si antes de acabar llega un cambio de unos párrafos (el subrayado del corrector),
        Qt da la maquetación por terminada y los que faltaban se quedan sin líneas: huecos con el texto «debajo»
        hasta cambiar el ancho de la hoja (Nageki 1, epílogo: 61 párrafos de 110). Se maqueta entera al momento."""
        super().changeEvent(event)
        settle(self.document())
        self.viewport().update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        settle(self.document())  # otro ancho, otra maquetación: entera, no a trozos
        self.viewport().update()

    def content(self):
        return dump(self.document())

    def set_bars(self, blocks, color):
        """Barrita en el margen izquierdo junto a cada párrafo de blocks, como el control de cambios de Word: no
        tapa el texto ni parece una selección."""
        self.bars, self.bar_color = list(blocks), QColor(color)
        self.viewport().update()

    def diagnose(self, folder):
        """Guarda en folder lo que hay en la pantalla, lo que la hoja pinta en memoria y su maquetación: los huecos
        de la hoja (párrafos que no se ven hasta cambiar su ancho) no se han podido reproducir fuera de la pantalla
        del usuario. Devuelve la carpeta."""
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        window, view, doc = self.window(), self.viewport(), self.document()
        window.screen().grabWindow(window.winId()).save(str(folder / "pantalla.png"))
        view.grab().save(str(folder / "memoria.png"))
        bar, layout = self.verticalScrollBar(), doc.documentLayout()
        out = [f"hoja {self.geometry().getRect()} vista {view.geometry().getRect()} visible {view.isVisible()} "
               f"repinta {view.updatesEnabled()} ventana {window.windowState()} escala {view.devicePixelRatioF()}",
               f"desplazamiento {bar.value()} de {bar.maximum()} · documento {doc.size().toTuple()} ancho "
               f"{doc.textWidth()} párrafos {doc.blockCount()}", "repintados (zona, desplazamiento):"]
        out += [f"  {rect} {scroll}" for rect, scroll in self.paints]
        out.append("párrafos (y, alto, líneas, y del texto, principio):")
        block = doc.firstBlock()
        while block.isValid():
            box = layout.blockBoundingRect(block)
            out.append(f"  {box.top():.0f} {box.height():.0f} {block.layout().lineCount()} "
                       f"{block.layout().position().y():.0f} {block.text()[:25]!r}")
            block = block.next()
        (folder / "hoja.txt").write_text("\n".join(out), encoding="utf-8")
        return folder

    def paintEvent(self, event):
        self.paints.append((event.rect().getRect(), self.verticalScrollBar().value()))
        if self.mark_current:  # debajo del texto: antes de que QTextEdit lo pinte
            block, doc = self.textCursor().block(), self.document()
            text = block.layout().boundingRect().translated(block.layout().position())  # el texto, sin márgenes
            margin = doc.documentMargin()
            width = doc.textWidth() if doc.textWidth() > 0 else self.viewport().width()
            band = QRectF(margin - 10 - self.horizontalScrollBar().value(),
                          text.top() - 3 - self.verticalScrollBar().value(), width - 2 * margin + 20, text.height() + 6)
            under = QPainter(self.viewport())
            under.setRenderHint(QPainter.RenderHint.Antialiasing)
            under.setPen(Qt.PenStyle.NoPen)
            tint = QColor(QApplication.palette().color(QPalette.ColorRole.Highlight))
            tint.setAlpha(26)
            under.setBrush(tint)
            under.drawRoundedRect(band, 4, 4)
            under.end()
        super().paintEvent(event)
        painter = QPainter(self.viewport())
        layout, doc = self.document().documentLayout(), self.document()
        dx, dy = self.horizontalScrollBar().value(), self.verticalScrollBar().value()
        self.paint_frames(painter, layout, doc, dx, dy, event.rect())
        if not self.bars:
            painter.end()
            return
        x = doc.documentMargin() / 2 - 1.5 - dx
        for block in self.bars:
            if block.isValid() and block.document() is doc:
                box = layout.blockBoundingRect(block)
                top = box.top() + block.blockFormat().topMargin() - dy
                height = box.height() - block.blockFormat().topMargin() - block.blockFormat().bottomMargin()
                painter.fillRect(QRectF(x, top, 3, max(height, 4)), self.bar_color)
        painter.end()

    def paint_frames(self, painter, layout, doc, dx, dy, area):
        """Doble borde alrededor de cada grupo de párrafos seguidos en recuadro, como en el libro. Se mide por el
        texto de cada párrafo (su layout), no por blockBoundingRect, que no cuenta igual los márgenes."""
        pen = QColor(self.palette().color(self.foregroundRole()))
        pen.setAlpha(150)
        painter.setPen(pen)
        width = doc.textWidth() if doc.textWidth() > 0 else self.viewport().width()
        margin, gap = doc.documentMargin(), FRAME_PAD / 2

        def draw(top, bottom):
            for inset in (0, 3):
                painter.drawRect(QRectF(margin + 2 + inset - dx, top + inset - dy,
                                        width - 2 * margin - 4 - 2 * inset, bottom - top - 2 * inset))

        block, top, bottom = doc.firstBlock(), None, None
        while block.isValid():
            text = block.layout()
            y = text.position().y()
            if block.blockFormat().property(FRAMED):
                if top is None:
                    top = y - gap
                bottom = y + text.boundingRect().height() + gap
            elif top is not None:
                draw(top, bottom)
                top = None
            if y - dy > area.bottom():  # lo de más abajo no se ve: el recuadro abierto sigue fuera de la vista
                break
            block = block.next()
        if top is not None:
            draw(top, bottom if not block.isValid() else max(bottom, area.bottom() + dy + 10))

    def set_zoom(self, percent):
        # En la hoja de estilo: la QSS de la ventana (9 pt para todo) pisaría un setFont o la fuente del documento
        self.setStyleSheet(f"QTextEdit#sheet {{ font-family: 'Segoe UI'; font-size: {FONT_SIZE * percent / 100:.1f}pt; }}")
        self.document().setDefaultFont(self.font())
        settle(self.document())  # otra letra, otra maquetación: entera
        self.viewport().update()

    def goto_source(self, k):
        """Cursor al principio del párrafo que viene del bloque k (si está)."""
        block = self.document().firstBlock()
        while block.isValid():
            if source_of(block) == k:
                cursor = self.textCursor()
                cursor.setPosition(block.position())
                self.setTextCursor(cursor)
                return True
            block = block.next()
        return False

    def current_sources(self):
        """Bloques del libro que hay en el párrafo del cursor: el suyo y los siguientes que se le unieron (ya sin
        párrafo propio y con su texto dentro de este). Uno borrado no cuenta. Vacía si no tiene origen."""
        k, block = self.current_source(), self.textCursor().block()
        lines = dict(self.origins.get(self.document(), ()))
        order = [j for j, _ in self.origins.get(self.document(), ())]
        if k is None or source_of(block) != k or k not in order:
            return [k] if k is not None else []
        present, other = set(), self.document().firstBlock()
        while other.isValid():
            present.add(source_of(other))
            other = other.next()
        here, found = block.text(), [k]
        for j in order[order.index(k) + 1:]:
            if j in present:
                break
            start = _strip(convert.TAGS.sub("", lines[j])).strip()[:25]
            if start and start in here:
                found.append(j)
        return found

    def current_source(self, block=None):
        """Bloque del libro del párrafo del cursor (o de block) o, en uno nuevo sin origen, el del anterior que lo
        tenga."""
        block = self.textCursor().block() if block is None else block
        while block.isValid():
            k = source_of(block)
            if k is not None:
                return k
            block = block.previous()
        return None

    def objects(self):
        found, block = set(), self.document().firstBlock()
        while block.isValid():
            found.update(objects_of(block))
            block = block.next()
        return found

    def paragraphs(self):
        """(párrafos de texto, número del del cursor o None, palabras): para la barra de estado."""
        count, current, words, here = 0, None, 0, self.textCursor().block()
        block = self.document().firstBlock()
        while block.isValid():
            if _is_text(block):
                count += 1
                words += len(block.text().split())
                if block == here:
                    current = count
            block = block.next()
        return count, current, words

    def misspelled(self):
        found, block = [], self.document().firstBlock()
        while block.isValid() and self.look.unknown:
            found += [m.group(0) for m in self.look.matches(block.text())]
            block = block.next()
        return found

    # Acciones de la cinta: cada una es un solo paso de deshacer
    def _selected_chars(self, cursor):
        doc, start, end = self.document(), cursor.selectionStart(), cursor.selectionEnd()
        probe = QTextCursor(doc)
        for pos in range(start, end):
            if doc.characterAt(pos) in (" ", "￼"):
                continue
            probe.setPosition(pos + 1)
            yield probe.charFormat()

    def _toggle(self, has, make):
        cursor = self.textCursor()
        if cursor.hasSelection():
            on = not all(has(f) for f in self._selected_chars(cursor))
            f = QTextCharFormat()
            make(f, on)
            cursor.beginEditBlock()
            cursor.mergeCharFormat(f)
            cursor.endEditBlock()
        else:  # sin selección, como Word: lo que se escriba a continuación
            f = QTextCharFormat()
            make(f, not has(self.currentCharFormat()))
            self.mergeCurrentCharFormat(f)

    def toggle_italic(self):
        self._toggle(lambda f: f.fontItalic(), lambda f, on: f.setFontItalic(on))

    def toggle_bold(self):
        self._toggle(lambda f: f.fontWeight() >= QFont.Weight.DemiBold,
                     lambda f, on: f.setFontWeight(QFont.Weight.Bold if on else QFont.Weight.Normal))

    def toggle_sup(self):
        """Superíndice (1.ᵉʳ, m²), como Ctrl+Mayús+= de Word."""
        sup, normal = QTextCharFormat.VerticalAlignment.AlignSuperScript, QTextCharFormat.VerticalAlignment.AlignNormal
        self._toggle(lambda f: f.verticalAlignment() == sup, lambda f, on: f.setVerticalAlignment(sup if on else normal))

    def toggle_small_caps(self):
        """Versalitas: rangos, nombres de habilidades y sonidos que el PDF pone en letra pequeña."""
        sc, mixed = QFont.Capitalization.SmallCaps, QFont.Capitalization.MixedCase
        self._toggle(lambda f: f.fontCapitalization() == sc, lambda f, on: f.setFontCapitalization(sc if on else mixed))

    def set_text(self, text):
        """Pone text (como dump) en la hoja, en un solo deshacer: lo que vuelve de la vista de código. Un párrafo
        igual que uno de antes sigue siendo de su bloque (su página) y, si es el cargado, recuerda su línea."""
        doc = self.document()
        before, levels, block = [], {}, doc.firstBlock()
        while block.isValid():
            for line in block_lines(block):
                if line.strip():
                    before.append((source_of(block), line))
            if block.blockFormat().property(LEVEL):
                levels[source_of(block)] = block.blockFormat().property(LEVEL)
            block = block.next()
        lines = [line for line in text.split("\n\n") if line.strip()]
        pairs, last = [], None
        for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, [line for _, line in before], lines,
                                                          autojunk=False).get_opcodes():
            for n in range(j2 - j1):
                same = op == "equal" or (op == "replace" and i2 - i1 == j2 - j1)
                k = before[i1 + n][0] if same else last
                pairs.append((k, lines[j1 + n]))
                last = k if k is not None else last
        loaded = {line for _, line in self.origins.get(doc, [])}
        cursor = QTextCursor(doc)
        cursor.beginEditBlock()
        cursor.select(QTextCursor.SelectionType.Document)
        cursor.removeSelectedText()
        insert_lines(cursor, pairs, self.pictures, remember=True, color=self.color, levels=levels)
        block = doc.firstBlock()
        while block.isValid():  # solo los párrafos como al cargar recuerdan su línea (Volver al original, cambios)
            f = block.blockFormat()
            if f.property(ORIGINAL) is not None and f.property(ORIGINAL) not in loaded:
                f.clearProperty(ORIGINAL)
                f.clearProperty(CANON)
                cursor.setPosition(block.position())
                cursor.setBlockFormat(f)
            block = block.next()
        cursor.endEditBlock()

    def toggle_underline(self):
        self._toggle(lambda f: f.fontUnderline() and not f.isAnchor(), lambda f, on: f.setFontUnderline(on))

    def toggle_strike(self):
        self._toggle(lambda f: f.fontStrikeOut(), lambda f, on: f.setFontStrikeOut(on))

    def toggle_sub(self):
        """Subíndice (H₂O), como Ctrl+= de Word."""
        sub, normal = QTextCharFormat.VerticalAlignment.AlignSubScript, QTextCharFormat.VerticalAlignment.AlignNormal
        self._toggle(lambda f: f.verticalAlignment() == sub, lambda f, on: f.setVerticalAlignment(sub if on else normal))

    def is_sub(self):
        return self.currentCharFormat().verticalAlignment() == QTextCharFormat.VerticalAlignment.AlignSubScript

    def _each_piece(self, change):
        """change(formato) -> formato nuevo en cada trozo de la selección (o en lo que se escriba, sin selección).
        Un deshacer."""
        cursor = self.textCursor()
        if not cursor.hasSelection():
            self.setCurrentCharFormat(change(QTextCharFormat(self.currentCharFormat())))
            return
        start, end = cursor.selectionStart(), cursor.selectionEnd()
        piece = QTextCursor(self.document())
        piece.beginEditBlock()
        block = self.document().findBlock(start)
        while block.isValid() and block.position() < end:
            it = block.begin()
            while not it.atEnd():
                fragment = it.fragment()
                it += 1
                a, b = max(start, fragment.position()), min(end, fragment.position() + fragment.length())
                if a < b and not fragment.charFormat().isImageFormat():
                    piece.setPosition(a)
                    piece.setPosition(b, QTextCursor.MoveMode.KeepAnchor)
                    piece.setCharFormat(change(QTextCharFormat(fragment.charFormat())))
            block = block.next()
        piece.endEditBlock()

    def _user_style(self, f, key, value):
        """f con la declaración key: value (None la quita) en lo puesto con la cinta (USER_STYLE)."""
        decls = _style(f.property(USER_STYLE) or "")
        if value is None:
            decls.pop(key, None)
        else:
            decls[key] = value
        text = "; ".join(f"{k}: {v}" for k, v in decls.items())
        if text:
            f.setProperty(USER_STYLE, text)
        else:
            f.clearProperty(USER_STYLE)
        return f

    def grow_font(self, step):
        """Letra un paso más grande (1) o más pequeña (-1), como Ctrl+Mayús+> y < de Word: sale como font-size."""
        def change(f):
            now = f.property(QTextFormat.Property.FontSizeAdjustment) or 0
            new = max(-2, min(4, int(now) + step))
            f.setProperty(QTextFormat.Property.FontSizeAdjustment, new)
            return self._user_style(f, "font-size", None if new == 0 else f"{SIZES[new + 2]:g}em")
        self._each_piece(change)

    def set_color(self, color):
        """Color del texto (QColor) o el de siempre (None): sale como color en un <span style>."""
        def change(f):
            if color is None:
                f.clearForeground()
            else:
                f.setForeground(QColor(color))
            return self._user_style(f, "color", QColor(color).name() if color is not None else None)
        self._each_piece(change)

    def set_family(self, family):
        """Tipo de letra (nombre) o la del libro (None): sale como font-family en un <span style>."""
        def change(f):
            if family:
                f.setFontFamilies([family])
            else:
                f.clearProperty(QTextFormat.Property.FontFamilies)
            return self._user_style(f, "font-family", f'"{family}"' if family else None)
        self._each_piece(change)

    def clear_format(self):
        """«Borrar formato» de Word: sin cursiva, negrita, subrayado, tamaño ni color; los enlaces y lo que no se
        dibuja (furigana, notas del libro) se quedan."""
        para = block_look(self.textCursor().block())[0]

        def change(f):
            keep = tuple(t for t in _stack_of(f) if _name(t) not in ("i", "em", "b", "strong", "u", "ins", "s", "del",
                                                                     "strike", "sup", "sub", "sc", "small", "big"))
            new = look_format(visual(keep, para))
            if f.isAnchor():
                link_format(f.anchorHref(), new)
            if keep:
                new.setProperty(STACK, STACK_SEP.join(keep))
            if f.property(VOID):
                new.setProperty(VOID, f.property(VOID))
            return new
        self._each_piece(change)

    def insert_line_break(self):
        """Salto de línea dentro del párrafo (Mayús+Intro): en el libro, <br/>."""
        self.textCursor().insertText(LINE_BREAK)

    def insert_ruby(self, base, reading):
        """Furigana: base con su lectura encima (<ruby>), en el cursor."""
        cursor = self.textCursor()
        cursor.beginEditBlock()
        cursor.removeSelectedText()
        _insert_marked(cursor, f"<ruby>{html.escape(base, quote=False)}<rp>(</rp><rt>{html.escape(reading, quote=False)}"
                               f"</rt><rp>)</rp></ruby>", self.color, block_look(cursor.block())[0])
        cursor.endEditBlock()

    # Estilo de párrafo con su etiqueta: Título 3, Cita, justificado, sin sangría
    def paragraph_style(self):
        """«Normal», «Capítulo», «Subtítulo», «Título 3», «Título 4» o «Cita» del párrafo del cursor."""
        f = self.textCursor().blockFormat()
        tag = (convert.OPENING.match(f.property(OPENING) or "") or [None, ""])[1].lower()
        if tag == "blockquote":
            return "Cita"
        if tag in ("h3", "h4", "h5", "h6") and f.property(HEADING):
            return "Título 3" if tag == "h3" else "Título 4"
        return self.heading_level() or "Normal"

    def set_paragraph_style(self, name):
        """Cambia el estilo del párrafo del cursor: Normal, Capítulo y Subtítulo como siempre (su fila); Título 3 y 4
        son subtítulos con <h3>/<h4>; Cita, un <blockquote>. Si el párrafo tiene su etiqueta (EPUB), cambia la etiqueta
        y se conservan sus atributos."""
        block = self.textCursor().block()
        f = block.blockFormat()
        if objects_of(block) or not block.text().strip():
            QApplication.beep()
            return
        opening = f.property(OPENING) or ""
        tag = (convert.OPENING.match(opening) or [None, ""])[1].lower()
        heading = tag in convert.HEADING_TAGS
        new_tag = {"Normal": "p", "Cita": "blockquote", "Título 3": "h3", "Título 4": "h4",
                   "Capítulo": tag if heading else "h1", "Subtítulo": tag if heading and tag != "h1" else "h2"}[name]
        level = {"Capítulo": "Capítulo", "Subtítulo": "Subtítulo", "Título 3": "Subtítulo", "Título 4": "Subtítulo"}.get(name)
        cursor = QTextCursor(block)
        cursor.beginEditBlock()
        if opening or name in ("Título 3", "Título 4", "Cita"):
            new = _retag(opening or "<p>", new_tag)
            self._restyle(block, opening, new)
            f = block.blockFormat()
            if level:
                f.setProperty(HEADING, True)
                f.setProperty(LEVEL, level)
            else:
                f.clearProperty(HEADING)
                f.clearProperty(LEVEL)
            f.setTopMargin(SPACE * 2 if level else 0)
            cursor.setBlockFormat(f)
        else:
            self.set_heading(level)
        cursor.endEditBlock()
        self.look.rehighlightBlock(block)

    def _restyle(self, block, old, new):
        """Pone al párrafo la etiqueta de apertura new (antes old): su aspecto nuevo, también en sus letras, que
        conservan sus etiquetas (las que pedía lo puesto con la cinta pasan a ser suyas)."""
        old_para, new_para = paragraph_look(old)[0], paragraph_look(new)
        cursor = QTextCursor(self.document())
        it = block.begin()
        while not it.atEnd():
            fragment = it.fragment()
            it += 1
            f = fragment.charFormat()
            if f.isImageFormat():
                continue
            tags = _wanted(f, _stack_of(f), old_para)
            g = look_format(visual(tags, new_para[0]))
            if tags:
                g.setProperty(STACK, STACK_SEP.join(tags))
            if f.property(VOID):
                g.setProperty(VOID, f.property(VOID))
            cursor.setPosition(fragment.position())
            cursor.setPosition(fragment.position() + fragment.length(), QTextCursor.MoveMode.KeepAnchor)
            cursor.setCharFormat(g)
        f = block.blockFormat()
        f.setProperty(OPENING, new)
        f.setProperty(BOOK_LOOK, True)
        shape_format(f, new_para[1])
        f.setObjectIndex(-1)  # ni en una lista
        cursor.setPosition(block.position())
        cursor.setBlockFormat(f)

    def set_paragraph_css(self, key, value):
        """Una declaración de CSS en el style de los párrafos elegidos (text-align: justify, text-indent: 0); value
        None la quita. Un párrafo sin etiqueta propia (PDF) la recibe: <p style="…">."""
        blocks = self._selected_text_blocks() or ([self.textCursor().block()]
                                                  if self.textCursor().blockFormat().property(OPENING) else [])
        if not blocks:
            QApplication.beep()
            return
        edit = QTextCursor(self.document())
        edit.beginEditBlock()
        for block in blocks:
            f = block.blockFormat()
            old = f.property(OPENING) or ""
            if not old:  # un párrafo de un PDF: deja su centrado o su recuadro y pasa a llevar su etiqueta
                f.clearProperty(CENTERED)
                f.clearProperty(RIGHTED)
                edit.setPosition(block.position())
                edit.setBlockFormat(f)
            self._restyle(block, old, _with_style(old or "<p>", key, value))
        edit.endEditBlock()
        self.viewport().update()

    def paragraph_css(self, key):
        """Valor de key en el aspecto del párrafo del cursor (su CSS y su style), o ""."""
        return block_look(self.textCursor().block())[1].get({"text-align": "align", "text-indent": "indent"}[key], "")

    def toggle_justify(self):
        self.set_paragraph_css("text-align", None if self.paragraph_css("text-align") == "justify" else "justify")

    def toggle_indent(self):
        """Con sangría de primera línea o sin ella (text-indent). Sin nada dicho, el párrafo la lleva (el estilo de la
        app y casi todos los EPUB sangran): el primer clic la quita."""
        indent = self.paragraph_css("text-indent")
        flat = bool(indent) and re.fullmatch(r"0+(\.0+)?\s*(em|px|%)?", indent)
        self.set_paragraph_css("text-indent", "1.5em" if flat else "0")

    def is_sup(self):
        return self.currentCharFormat().verticalAlignment() == QTextCharFormat.VerticalAlignment.AlignSuperScript

    def is_small_caps(self):
        return self.currentCharFormat().fontCapitalization() == QFont.Capitalization.SmallCaps

    def heading_level(self):
        """Estilo del párrafo del cursor: None (normal), "Capítulo" o "Subtítulo" (o el tipo de su fila)."""
        f = self.textCursor().blockFormat()
        return (f.property(LEVEL) or "Capítulo") if f.property(HEADING) else None

    def set_heading(self, level):
        """Párrafo normal (None/False), capítulo ("Capítulo" o True) o subtítulo ("Subtítulo"); no en un
        párrafo vacío ni en una ilustración. Un título no va centrado."""
        level = "Capítulo" if level is True else level or None
        cursor = self.textCursor()
        block = cursor.block()
        if objects_of(block) or not block.text().strip():
            QApplication.beep()
            return
        f = cursor.blockFormat()
        if level:
            f.setProperty(HEADING, True)
            f.setProperty(LEVEL, level)
            if f.property(FRAMED):  # un título no va en recuadro
                unframe_format(f)
            f.setObjectIndex(-1)  # ni en una lista
            align_format(f)
        else:
            f.clearProperty(HEADING)
            f.clearProperty(LEVEL)
        f.setTopMargin(SPACE * 2 if level else 0)
        cursor.setBlockFormat(f)
        self.look.rehighlightBlock(block)

    def toggle_heading(self):
        self.set_heading(None if self.heading_level() else "Capítulo")

    def _selected_text_blocks(self):
        """Párrafos de texto que no son título de la selección (o el del cursor), en orden."""
        cursor, doc = self.textCursor(), self.document()
        block, last = doc.findBlock(cursor.selectionStart()), doc.findBlock(cursor.selectionEnd())
        blocks = []
        while block.isValid():
            if _is_text(block):
                blocks.append(block)
            if block == last:
                break
            block = block.next()
        return blocks

    def set_align(self, how):
        """Alinea los párrafos elegidos: "center", "right" o None (izquierda); también dentro de un recuadro. Un
        elemento de lista que se centra o va a la derecha sale de ella (la lista va a la izquierda). Un deshacer."""
        blocks = self._selected_text_blocks()
        if not blocks:
            QApplication.beep()
            return
        edit = QTextCursor(self.document())
        edit.beginEditBlock()
        for block in blocks:
            old = block.blockFormat().property(OPENING)
            if old:  # con su etiqueta (EPUB, o estilo de la cinta): en su style, que gana a sus clases
                new = _with_style(old, "text-align", how)
                if not how and paragraph_look(new)[1]["align"] not in ("", "left", "start"):
                    new = _with_style(old, "text-align", "left")
                self._restyle(block, old, new)
                continue
            f = align_format(block.blockFormat(), how)
            if how:
                f.setObjectIndex(-1)
            edit.setPosition(block.position())
            edit.setBlockFormat(f)
        edit.endEditBlock()

    def toggle_center(self):
        """Ctrl+E: centra el párrafo (carta, cartel) o lo devuelve a su sitio. Solo párrafos de texto."""
        self.set_align(None if self.is_centered() else "center")

    def toggle_right(self):
        """Ctrl+D, como Word en español: el párrafo a la derecha (firma, fecha de una carta) o a su sitio."""
        self.set_align(None if self.is_right() else "right")

    def list_style(self):
        """Estilo de la lista del párrafo del cursor ("disc", "decimal"…) o None."""
        return list_style(self.textCursor().block())

    def set_list(self, style):
        """Pone los párrafos elegidos en una lista de viñetas o numerada (style de convert.LIST_STYLES), como en
        Word, o los saca (None, o el estilo que ya tienen). Se unen a la lista vecina del mismo estilo: la
        numeración sigue. Un elemento de lista no va en recuadro ni alineado. Un solo deshacer.
        shortcut: dos listas del mismo estilo que quedan pegadas al borrar lo de en medio se numeran aparte en la
        hoja y seguidas en el EPUB; unirlas aquí si molesta."""
        blocks = self._selected_text_blocks()
        here = self.textCursor().block()
        if not blocks and not self.textCursor().hasSelection() and not here.text() and not here.blockFormat().property(
                HEADING):
            blocks = [here]  # como Word: la lista se empieza en el párrafo vacío y luego se escribe en ella
        if not blocks:
            QApplication.beep()
            return
        if style == list_style(blocks[0]):
            style = None
        edit = QTextCursor(self.document())
        edit.beginEditBlock()
        group = None
        for block in blocks:
            f = block.blockFormat()
            f.setObjectIndex(-1)
            if style:
                align_format(f)
                if f.property(FRAMED):
                    unframe_format(f)
            edit.setPosition(block.position())
            edit.setBlockFormat(f)
            if style and group is None:
                near = [n for n in (block.previous(), blocks[-1].next()) if n.isValid() and list_style(n) == style]
                if near:
                    group = near[0].textList()
                    group.add(block)
                else:
                    group = edit.createList(LISTS[style])
            elif style:
                group.add(block)
        edit.endEditBlock()
        self.viewport().update()

    def is_centered(self):
        f = self.textCursor().blockFormat()
        return self.paragraph_css("text-align") == "center" if f.property(OPENING) else bool(f.property(CENTERED))

    def is_right(self):
        f = self.textCursor().blockFormat()
        return self.paragraph_css("text-align") == "right" if f.property(OPENING) else bool(f.property(RIGHTED))

    def toggle_frame(self):
        """Mete en un recuadro los párrafos de la selección (ventana de estado, cartel) o los saca. Solo texto."""
        cursor = self.textCursor()
        doc = self.document()
        first, last = doc.findBlock(cursor.selectionStart()), doc.findBlock(cursor.selectionEnd())
        blocks, block = [], first
        while block.isValid():
            if _is_text(block):
                blocks.append(block)
            if block == last:
                break
            block = block.next()
        if not blocks:
            QApplication.beep()
            return
        on = not first.blockFormat().property(FRAMED)
        edit = QTextCursor(doc)
        edit.beginEditBlock()
        for n, block in enumerate(blocks):
            f = block.blockFormat()
            if on:
                f.clearProperty(HEADING)
                f.clearProperty(LEVEL)
                f.setObjectIndex(-1)  # un elemento de lista sale de ella
                frame_format(f, n == 0 and not block.previous().blockFormat().property(FRAMED),
                             n == len(blocks) - 1 and not block.next().blockFormat().property(FRAMED))
            else:
                unframe_format(f)
            edit.setPosition(block.position())
            edit.setBlockFormat(f)
        edit.endEditBlock()
        self.viewport().update()

    def is_framed(self):
        return bool(self.textCursor().blockFormat().property(FRAMED))

    def link_at(self):
        """Dirección del enlace donde está el cursor (o el principio de la selección), o ""."""
        cursor = QTextCursor(self.textCursor())
        cursor.setPosition(cursor.selectionStart() + (1 if cursor.hasSelection() else 0))
        f = cursor.charFormat()
        return f.anchorHref() if f.isAnchor() else ""

    def set_link(self, href):
        """Pone el enlace href en la selección (o en el enlace del cursor); con href vacío, lo quita."""
        cursor = self.textCursor()
        if not cursor.hasSelection():  # sobre un enlace sin seleccionar: todo el enlace
            here = self.link_at()
            if not here:
                QApplication.beep()
                return
            block, pos = cursor.block(), cursor.position()
            it = block.begin()
            while not it.atEnd():
                fragment = it.fragment()
                if fragment.contains(pos) or fragment.position() + fragment.length() == pos:
                    cursor.setPosition(fragment.position())
                    cursor.setPosition(fragment.position() + fragment.length(), QTextCursor.MoveMode.KeepAnchor)
                    break
                it += 1
        cursor.beginEditBlock()
        if href:
            cursor.mergeCharFormat(link_format(href))
        else:  # mergeCharFormat no borra el color: quitado el enlace, el texto se quedaba azul
            start, end = cursor.selectionStart(), cursor.selectionEnd()
            piece = QTextCursor(self.document())
            block = self.document().findBlock(start)
            while block.isValid() and block.position() < end:
                it = block.begin()
                while not it.atEnd():
                    fragment = it.fragment()
                    a, b = max(start, fragment.position()), min(end, fragment.position() + fragment.length())
                    if a < b:
                        piece.setPosition(a)
                        piece.setPosition(b, QTextCursor.MoveMode.KeepAnchor)
                        piece.setCharFormat(link_format("", fragment.charFormat()))
                    it += 1
                block = block.next()
        cursor.endEditBlock()

    def insert_note(self):
        """Nota del traductor en el cursor, con el cursor dentro para escribirla: «[Nota: |]»."""
        cursor = self.textCursor()
        cursor.beginEditBlock()
        cursor.insertText("[Nota: ]", _plain_format())
        cursor.endEditBlock()
        cursor.movePosition(QTextCursor.MoveOperation.Left)
        self.setTextCursor(cursor)

    def change_case(self):
        """Mayús+F3, como Word: la selección pasa de minúsculas a Primera Mayúscula y a MAYÚSCULAS. Conserva
        la cursiva, la negrita y los enlaces de cada letra."""
        cursor = self.textCursor()
        if not cursor.hasSelection():
            cursor.select(QTextCursor.SelectionType.WordUnderCursor)
        text = cursor.selectedText()
        if not text.strip():
            return
        if text == text.lower():
            starts = {m.start() for m in re.finditer(r"(?<![\w'’])\w", text)}
            mapped = (c.upper() if n in starts else c for n, c in enumerate(text))
        elif text == text.upper():
            mapped = (c.lower() for c in text)
        else:
            mapped = (c.upper() for c in text)
        start, end, doc = cursor.selectionStart(), cursor.selectionEnd(), self.document()
        at = start  # posición de Qt: un emoji ocupa dos
        cursor.beginEditBlock()
        try:
            for c, new in zip(text, mapped):
                size = 2 if ord(c) > 0xFFFF else 1
                # «ß» → «SS» o «ﬁ» → «FI» cambiarían el largo: esas letras se quedan como están
                if (new != c and len(new) == 1 and (ord(new) > 0xFFFF) == (size == 2)
                        and doc.characterAt(at) not in (" ", "￼")):
                    one = QTextCursor(doc)
                    one.setPosition(at)
                    one.setPosition(at + size, QTextCursor.MoveMode.KeepAnchor)
                    one.insertText(new, one.charFormat())
                at += size
        finally:
            cursor.endEditBlock()
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        self.setTextCursor(cursor)

    @staticmethod
    def _flags(backward=False, whole=False, case=False):
        flags = QTextDocument.FindFlag(0)
        for on, flag in ((backward, QTextDocument.FindFlag.FindBackward),
                         (whole, QTextDocument.FindFlag.FindWholeWords),
                         (case, QTextDocument.FindFlag.FindCaseSensitively)):
            if on:
                flags |= flag
        return flags

    def _find(self, term, cursor, backward=False, whole=False, case=False, regex=False):
        """document().find de term desde cursor. Con regex, term es una expresión regular (sintaxis de Python, que
        QRegularExpression entiende igual en lo común); una coincidencia vacía («a*») cuenta como no encontrada:
        reemplazarla no avanzaría."""
        if not regex:
            return self.document().find(term, cursor, self._flags(backward, whole, case))
        expr = QRegularExpression(rf"(?<!\w)(?:{term})(?!\w)" if whole else term,
                                  QRegularExpression.PatternOption(0) if case
                                  else QRegularExpression.PatternOption.CaseInsensitiveOption)
        found = self.document().find(expr, cursor, self._flags(backward))
        return QTextCursor() if found.isNull() or not found.hasSelection() else found

    @staticmethod
    def _replacement(term, new, selected, case=False, regex=False):
        """Lo que va en lugar de selected, o None si selected no es una aparición de term. Con regex, new admite
        \\1 o \\g<nombre> de los grupos."""
        if regex:
            m = re.fullmatch(term, selected, 0 if case else re.I)
            return m.expand(new) if m else None
        return new if (selected == term if case else selected.lower() == term.lower()) else None

    def find_next(self, term, backward=False, whole=False, case=False, regex=False):
        """Selecciona la siguiente aparición de term desde el cursor (whole: palabra completa; case: distingue
        mayúsculas; regex: expresión regular); False si no hay más en esta hoja."""
        found = self._find(term, self.textCursor(), backward, whole, case, regex)
        if found.isNull():
            return False
        self.setTextCursor(found)
        return True

    def replace_current(self, term, new, whole=False, case=False, regex=False):
        """Si la selección es term, la cambia por new (con su formato). True si cambió algo."""
        cursor = self.textCursor()
        text = self._replacement(term, new, cursor.selectedText(), case, regex) if cursor.hasSelection() else None
        if text is None:
            return False
        cursor.beginEditBlock()
        cursor.insertText(text, cursor.charFormat())
        cursor.endEditBlock()
        return True

    def replace_all(self, term, new, whole=False, case=False, regex=False):
        """Cambia todas las apariciones de term en la hoja, en un solo paso de deshacer. Devuelve cuántas."""
        doc, count = self.document(), 0
        cursor = QTextCursor(doc)
        edit = QTextCursor(doc)
        edit.beginEditBlock()
        while True:
            cursor = self._find(term, cursor, False, whole, case, regex)
            if cursor.isNull():
                break
            text = self._replacement(term, new, cursor.selectedText(), case, regex)
            if text is not None:  # una expresión con (?<=…) no casa con lo elegido suelto: se deja como está
                cursor.insertText(text, cursor.charFormat())
                count += 1
        edit.endEditBlock()
        return count

    def join_next(self):
        """Une el párrafo del cursor con el siguiente, con un espacio entre los dos: arregla un párrafo partido
        por un salto de página. No une títulos ni ilustraciones."""
        cursor = self.textCursor()
        block = cursor.block()
        following = block.next()
        if not following.isValid() or not _is_text(block) or not _is_text(following):
            QApplication.beep()
            return
        doc = self.document()
        cursor.beginEditBlock()
        cursor.setPosition(block.position() + block.length() - 1)
        cursor.deleteChar()  # el salto entre los dos: el siguiente pasa a este párrafo
        while doc.characterAt(cursor.position() - 1) == " ":
            cursor.deletePreviousChar()
        while doc.characterAt(cursor.position()) == " ":
            cursor.deleteChar()
        cursor.insertText(" ", _plain_format())
        cursor.endEditBlock()
        self.setTextCursor(cursor)

    def split_here(self):
        """Enter: parte el párrafo; el nuevo hereda su origen (el PDF sigue mostrando su página)."""
        cursor = self.textCursor()
        cursor.beginEditBlock()
        if cursor.block().textList() is not None and not cursor.block().text().strip():
            f = cursor.blockFormat()  # Intro en un elemento vacío sale de la lista, como en Word
            f.setObjectIndex(-1)
            cursor.setBlockFormat(f)
        else:
            cursor.insertBlock()
        cursor.endEditBlock()
        self.setTextCursor(cursor)

    def insert_break(self):
        """Separador de escena escrito, como párrafo propio tras el del cursor."""
        cursor = self.textCursor()
        cursor.beginEditBlock()
        cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)
        insert_lines(cursor, [(source_of(cursor.block()), "* * *")], self.pictures, color=self.color)
        cursor.endEditBlock()
        self.setTextCursor(cursor)

    def store_picture(self, data):
        """Guarda la imagen en IMAGES con el nombre de su contenido; devuelve el nombre de [Imagen nombre]."""
        name = hashlib.sha1(data).hexdigest()[:16] + ".jpg"
        path = Path(IMAGES) / name
        if not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.pictures[name] = QImage.fromData(data)
        return name

    def adopt(self, line):
        """[Ilustración k] de otro capítulo (cortada allí, pegada aquí): su imagen como [Imagen nombre]. Al aceptar,
        edit_section solo recupera las ilustraciones de su capítulo, y esta se quedaba como un párrafo con ese texto:
        la ilustración se perdía al moverla de capítulo."""
        m = convert.TOKEN.fullmatch(line)
        if not m or m.group(1) != "Ilustración" or not self.foreign:
            return line
        data = self.foreign(int(m.group(2)))
        return f"[Imagen {self.store_picture(data)}]" if data else line

    def insert_picture(self, data):
        """Imagen nueva (JPEG) como párrafo propio tras el del cursor. Se guarda en IMAGES con el nombre de su
        contenido y va como [Imagen nombre]: al aceptar, edit_section la vuelve ilustración del libro."""
        name = self.store_picture(data)
        cursor = self.textCursor()
        cursor.beginEditBlock()
        cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)
        insert_lines(cursor, [(self.current_source(cursor.block()), f"[Imagen {name}]")], self.pictures,
                     color=self.color)
        cursor.endEditBlock()
        self.setTextCursor(cursor)

    @staticmethod
    def pasted_pictures(source):
        """Imágenes (JPEG) de lo pegado: archivos de imagen copiados en el Explorador, o una imagen (captura, «Copiar
        imagen» del navegador) si no trae texto. [] si no hay o no se pueden leer."""
        files = [url.toLocalFile() for url in source.urls() if url.isLocalFile()] if source.hasUrls() else []
        try:
            if files and all(f.lower().endswith(PICTURE_FILES) for f in files):
                return [convert.load_cover(f) for f in files]
            if source.hasImage():
                buffer = QBuffer()
                buffer.open(QBuffer.OpenModeFlag.WriteOnly)
                QImage(source.imageData()).save(buffer, "PNG")  # PNG siempre está en Qt; JPEG lo hace MuPDF
                return [convert.to_jpeg(bytes(buffer.data()))]
        except ValueError:
            pass
        return []

    # Símbolos y tipografía
    def insert_symbol(self, text):
        """Inserta text en el cursor (en lugar de lo elegido), con el formato de ahí: un paso de deshacer."""
        cursor = self.textCursor()
        cursor.beginEditBlock()
        cursor.insertText(text)
        cursor.endEditBlock()
        self.setTextCursor(cursor)

    def wrap(self, opening, closing):
        """Envuelve lo elegido en opening…closing («», ¿?, ¡!); sin selección, pone los dos con el cursor en
        medio para escribir dentro."""
        cursor = self.textCursor()
        start, end = cursor.selectionStart(), cursor.selectionEnd()
        cursor.beginEditBlock()
        cursor.setPosition(end)
        cursor.insertText(closing)
        cursor.setPosition(start)
        cursor.insertText(opening)
        cursor.endEditBlock()
        cursor.setPosition(start + len(opening))
        if end > start:
            cursor.setPosition(end + len(opening), QTextCursor.MoveMode.KeepAnchor)
        self.setTextCursor(cursor)

    def fix_typography(self):
        """La limpieza tipográfica del EPUB (convert._typography) escrita en la hoja: rayas de diálogo,
        comillas «», puntos suspensivos. Conserva cursiva, notas y enlaces. Devuelve cuántos párrafos cambió."""
        doc, changed = self.document(), 0
        edit = QTextCursor(doc)
        edit.beginEditBlock()
        block = doc.firstBlock()
        while block.isValid():
            lines = [] if objects_of(block) or not block.text().strip() else _lines(block)
            if len(lines) == 1:
                heading = bool(block.blockFormat().property(HEADING))
                body = _strip(lines[0])
                typed = convert._typography(convert._inline(body), dashes=not heading)
                new = html.unescape(convert.NOTE.sub(r"[Nota: \1]", typed))
                if new != body:
                    self._set_block_text(block, new)
                    changed += 1
            block = block.next()
        edit.endEditBlock()
        return changed

    def _set_block_text(self, block, line):
        """Cambia el texto del párrafo por line (con <i>/<b>/<a href>), sin tocar su formato de párrafo."""
        cursor = QTextCursor(self.document())
        cursor.setPosition(block.position())
        cursor.setPosition(block.position() + block.length() - 1, QTextCursor.MoveMode.KeepAnchor)
        cursor.removeSelectedText()
        _insert_marked(cursor, line, self.color, block_look(block)[0])  # los iconos, del color de los de la hoja

    # Cambios frente a lo cargado
    def changed_blocks(self):
        """Párrafos con texto distinto del cargado, o nuevos (sin línea original). Cada párrafo recuerda la
        respuesta hasta que se toca: compararlos todos en cada pausa al escribir tardaba medio segundo."""
        known = self.known.get(self.document(), {})
        found, block = [], self.document().firstBlock()
        while block.isValid():
            seen = known.get(block.fragmentIndex())
            if seen is None or seen[0] != block.revision():
                lines = [line for line in block_lines(block) if line.strip()]
                original = original_of(block)
                seen = known[block.fragmentIndex()] = (block.revision(),
                                                       bool(lines and (original is None or lines != [original])))
            if seen[1]:
                found.append(block)
            block = block.next()
        return found

    def can_restore(self, block, loaded):
        """¿Se puede devolver el párrafo a su línea original? No si se partió (otro párrafo tiene su origen) o
        se le unió el siguiente (el origen que lo seguía ya no está). loaded: orígenes cargados, en orden."""
        f = block.blockFormat()
        k = f.property(SOURCE)
        if f.property(ORIGINAL) is None or k is None or objects_of(block) or block not in self.changed_blocks():
            return False
        sources, other = [], self.document().firstBlock()
        while other.isValid():
            sources.append(source_of(other))
            other = other.next()
        if sources.count(k) != 1:
            return False
        later = loaded[loaded.index(k) + 1:] if k in loaded else []
        return not later or later[0] in sources

    def restore(self, block):
        """Devuelve el párrafo a su línea original (título, centrado y texto): un paso de deshacer."""
        original = original_of(block)
        heading, align = is_heading_line(original), align_of(original)
        framed = align.startswith("frame")
        opened = convert.OPENING.match(original)
        cursor = QTextCursor(self.document())
        cursor.setPosition(block.position())
        cursor.beginEditBlock()
        f = block.blockFormat()
        if heading:
            f.setProperty(HEADING, True)
        else:
            f.clearProperty(HEADING)
            f.clearProperty(LEVEL)
        align_format(f, side_of(align))
        if opened:  # su etiqueta de entonces (estilo, alineación y sangría del libro)
            f.setProperty(OPENING, opened[0])
            f.setProperty(BOOK_LOOK, True)
            shape_format(f, paragraph_look(opened[0])[1])
        else:
            f.clearProperty(OPENING)
            f.clearProperty(BOOK_LOOK)
        if framed:
            frame_format(f, not block.previous().blockFormat().property(FRAMED),
                         not block.next().blockFormat().property(FRAMED))
        elif f.property(FRAMED):
            unframe_format(f)
        f.setObjectIndex(-1)
        cursor.setBlockFormat(f)
        if align.startswith("list-"):  # vuelve a su lista: la del vecino del mismo estilo o una nueva
            near = [n for n in (block.previous(), block.next()) if n.isValid() and list_style(n) == align[5:]]
            if near:
                near[0].textList().add(block)
            else:
                cursor.createList(LISTS[align[5:]])
        self._set_block_text(block, _strip(original))
        cursor.endEditBlock()
        self.look.rehighlightBlock(block)

    def block_of(self, k):
        """Párrafos (en orden) cuyo origen es el bloque k del libro."""
        found, block = [], self.document().firstBlock()
        while block.isValid():
            if source_of(block) == k:
                found.append(block)
            block = block.next()
        return found

    def replace_sources(self, start, end, text):
        """Cambia los párrafos de los bloques start..end-1 por text (líneas de section_text separadas por línea
        en blanco; vacío los quita): el arreglo de un aviso del informe. Un paso de deshacer. False si alguno
        de esos bloques no está en la hoja."""
        first = self.block_of(start)
        last = next((self.block_of(k) for k in range(end - 1, start - 1, -1) if self.block_of(k)), [])
        if not first or not last:
            return False
        first, last = first[0], last[-1]
        cursor = QTextCursor(self.document())
        cursor.beginEditBlock()
        lines = [line for line in text.split("\n\n") if line.strip()]
        if lines:
            cursor.setPosition(first.position())
            cursor.setPosition(last.position() + last.length() - 1, QTextCursor.MoveMode.KeepAnchor)
            cursor.removeSelectedText()
            insert_lines(cursor, [(start, line) for line in lines], self.pictures, color=self.color)
        else:  # quitar: también el salto de párrafo, para no dejar uno vacío
            following = last.next()
            if following.isValid():
                cursor.setPosition(first.position())
                cursor.setPosition(following.position(), QTextCursor.MoveMode.KeepAnchor)
            else:
                previous = first.previous()
                cursor.setPosition(previous.position() + previous.length() - 1 if previous.isValid()
                                   else first.position())
                cursor.setPosition(last.position() + last.length() - 1, QTextCursor.MoveMode.KeepAnchor)
            cursor.removeSelectedText()
        cursor.endEditBlock()
        self.setTextCursor(cursor)
        return True

    def split_chapter(self, title):
        """Capítulo nuevo desde el cursor: parte el párrafo ahí (si está en medio) y pone antes del resto un
        título de tipo Capítulo. Un paso de deshacer. False en un título o una ilustración."""
        cursor = self.textCursor()
        block = cursor.block()
        if not title.strip() or block.blockFormat().property(HEADING) or objects_of(block):
            QApplication.beep()
            return False
        k = source_of(block)
        cursor.beginEditBlock()
        at_end = cursor.position() == block.position() + block.length() - 1 and block.text().strip()
        if at_end:  # tras el párrafo: el título va después
            cursor.insertBlock()
        else:
            cursor.insertBlock()  # el resto pasa a un párrafo nuevo; el título ocupa el sitio del corte
            cursor.movePosition(QTextCursor.MoveOperation.PreviousBlock)
            if cursor.block().text().strip():  # el cursor estaba en medio: hace falta un párrafo para el título
                cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)
                cursor.insertBlock()
        f = QTextBlockFormat()
        f.setBottomMargin(SPACE)
        f.setTopMargin(SPACE * 2)
        if k is not None:
            f.setProperty(SOURCE, k)
        f.setProperty(HEADING, True)
        f.setProperty(LEVEL, "Capítulo")
        cursor.setBlockFormat(f)
        cursor.insertText(title.strip(), _plain_format())
        cursor.endEditBlock()
        self.setTextCursor(cursor)
        self.look.rehighlightBlock(cursor.block())
        return True

    def keyPressEvent(self, event):
        mods = event.modifiers()
        ctrl, alt = mods & Qt.KeyboardModifier.ControlModifier, mods & Qt.KeyboardModifier.AltModifier
        key = event.key()
        actions = {Qt.Key.Key_K: self.toggle_italic, Qt.Key.Key_I: self.toggle_italic,
                   Qt.Key.Key_N: self.toggle_bold, Qt.Key.Key_B: self.toggle_bold,
                   Qt.Key.Key_T: self.toggle_heading, Qt.Key.Key_J: self.join_next, Qt.Key.Key_E: self.toggle_center,
                   Qt.Key.Key_D: self.toggle_right,
                   Qt.Key.Key_F: lambda: self.command.emit("find"), Qt.Key.Key_H: lambda: self.command.emit("replace")}
        typed = event.text()
        symbols = {Qt.Key.Key_Minus: lambda: self.insert_symbol("—"), Qt.Key.Key_Period: lambda: self.insert_symbol("…"),
                   Qt.Key.Key_Q: lambda: self.wrap("«", "»")}
        if ctrl and alt and key == Qt.Key.Key_K:  # como Word en español: Ctrl+K ya es cursiva
            self.command.emit("link")
        elif ctrl and alt and key in symbols and not (typed and typed.isprintable()):
            symbols[key]()  # AltGr es Ctrl+Alt: si escribe un carácter (AltGr+2 = @), se deja escribir
        elif ctrl and key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.command.emit("reviewed")
        elif key == Qt.Key.Key_F7:
            self.command.emit("next_change")
        elif key == Qt.Key.Key_F8:
            self.command.emit("next_issue")
        elif key == Qt.Key.Key_F9:
            self.command.emit("nav")
        elif key == Qt.Key.Key_F11:
            self.command.emit("focus")
        elif key == Qt.Key.Key_F12 and ctrl and mods & Qt.KeyboardModifier.ShiftModifier:
            self.command.emit("diagnose")
        elif ctrl and not alt and key in (Qt.Key.Key_Equal, Qt.Key.Key_Plus) and mods & Qt.KeyboardModifier.ShiftModifier:
            self.toggle_sup()  # Ctrl+Mayús+= de Word; en el teclado español «=» es Mayús+0
        elif ctrl and not alt and key == Qt.Key.Key_Equal:
            self.toggle_sub()  # Ctrl+= de Word
        elif ctrl and not alt and key in (Qt.Key.Key_Greater, Qt.Key.Key_Less):
            self.grow_font(1 if key == Qt.Key.Key_Greater else -1)  # Ctrl+Mayús+> y < de Word
        elif ctrl and not alt and key == Qt.Key.Key_S:
            self.toggle_underline()  # Ctrl+S de Word en español
        elif ctrl and not alt and key in actions:
            actions[key]()
        elif key == Qt.Key.Key_F3 and mods & Qt.KeyboardModifier.ShiftModifier:
            self.change_case()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and mods & Qt.KeyboardModifier.ShiftModifier:
            self.insert_line_break()  # Mayús+Intro: nueva línea en el mismo párrafo (<br/>)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.split_here()
        else:
            super().keyPressEvent(event)

    # Portapapeles
    def createMimeDataFromSelection(self):
        cursor = self.textCursor()
        copy = QTextDocument()
        QTextCursor(copy).insertFragment(cursor.selection())
        mime = QMimeData()
        mime.setText(cursor.selection().toPlainText().replace("￼", "").replace(" ", "\n"))
        mime.setData(MIME, dump(copy).encode("utf-8"))
        if cursor.selectedText().endswith("\u2029"):  # dump no guarda el último salto
            mime.setData(WHOLE, b"1")
        # La imagen, para pegarla fuera (Paint, historial de Windows): solo con el texto salía vacía. Dentro de la
        # hoja manda MIME, así que no se duplica
        block = copy.firstBlock()
        while block.isValid() and not mime.hasImage():
            for name in objects_of(block):
                n = name.split()[1]
                picture = self.pictures.get(int(n) if n.isdigit() else n)
                if isinstance(picture, QImage) and not picture.isNull():
                    mime.setImageData(picture)
                    break
            block = block.next()
        return mime

    def canInsertFromMimeData(self, source):
        return source.hasFormat(MIME) or source.hasHtml() or source.hasText() or source.hasImage() or source.hasUrls()

    def insertFromMimeData(self, source):
        """De la hoja: con su formato, sin repetir una ilustración que ya está. De Word, Google Docs o la web
        (HTML): con cursiva, negrita, superíndice, versalitas y enlaces, sin colores ni tamaños. Si no, solo el
        texto."""
        raw, objects = False, True
        if source.hasFormat(MIME):
            present = self.objects()
            lines = [self.adopt(line) for line in bytes(source.data(MIME)).decode("utf-8").split("\n\n")
                     if not (convert.TOKEN.fullmatch(line) and line[1:-1] in present)]
        elif source.hasHtml():
            outside = QTextDocument()
            outside.setHtml(source.html())
            lines, objects = [line for line in dump(outside).split("\n\n") if line.strip()], False
        else:
            lines, raw = [line.strip() for line in source.text().splitlines() if line.strip()], True
        if not source.hasFormat(MIME) and (not lines or source.hasUrls()):
            pictures = self.pasted_pictures(source)
            if pictures:
                group = self.textCursor()
                group.beginEditBlock()  # varias imágenes: un solo Ctrl+Z
                for data in pictures:
                    self.insert_picture(data)
                group.endEditBlock()
                return
        if not lines:
            return
        cursor = self.textCursor()
        cursor.beginEditBlock()
        cursor.removeSelectedText()
        whole = source.hasFormat(WHOLE)
        objects_only = all(convert.TOKEN.fullmatch(line) or convert.PICTURE.fullmatch(line) for line in lines)
        if objects_only and not cursor.atBlockStart():  # una ilustración soltada en medio de un párrafo: tras él
            cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)
            whole = False
        before = whole and cursor.atBlockStart() and cursor.block().previous().isValid()
        if before:  # párrafos enteros al principio de uno: tras el de arriba, y este queda como estaba
            cursor.movePosition(QTextCursor.MoveOperation.PreviousCharacter)
            lines = [""] + lines  # el primero, en un párrafo nuevo
        k = self.current_source(cursor.block())
        insert_lines(cursor, [(k, line) for line in lines], self.pictures, raw=raw, color=self.color,
                     objects=objects)
        if before:
            cursor.movePosition(QTextCursor.MoveOperation.NextBlock)
        elif whole:  # en medio de un párrafo: lo que sigue va en el suyo, no pegado al último
            cursor.insertBlock()
        cursor.endEditBlock()
        self.setTextCursor(cursor)
