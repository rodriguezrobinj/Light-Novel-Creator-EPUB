"""Hoja del editor de texto estilo Word: lo que se ve es lo que sale en el EPUB. Lee y escribe el formato de
convert.section_text (un párrafo por línea con <i>/<b>, "# " delante de un título, las ilustraciones y los
separadores del PDF como [Ilustración N] / [Separador N]), que es lo que guarda convert.edit_section."""
import base64
import bisect
import collections
import difflib
import hashlib
import html
import re
import unicodedata
from pathlib import Path

from PySide6.QtCore import QBuffer, QEvent, QMimeData, QObject, QPoint, QPointF, QRect, QRectF, QRegularExpression, QSizeF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (QColor, QFont, QFontInfo, QFontMetrics, QImage, QPainter, QPalette, QPen, QStandardItem, QStandardItemModel,
                           QSyntaxHighlighter, QTextBlockFormat,
                           QTextBlockUserData, QTextCharFormat, QTextCursor, QTextDocument, QTextFormat,
                           QTextImageFormat, QTextLength, QTextListFormat, QKeySequence)
from PySide6.QtWidgets import QApplication, QCompleter, QTextEdit, QToolTip, QWidget

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
TABLE_ROW = _USER + 17  # fila de tabla («Nivel | 5»): "table" o "table-head" (cabecera)
FRAME_KIND = _USER + 16  # párrafo en recuadro: su tipo (convert.BOX_KINDS), "" el de siempre
BOOK_LOOK = _USER + 15  # párrafo con el aspecto del libro (su etiqueta y su CSS): sin el título azul de la hoja
LINK_COLOR = "#2a62c8"  # enlaces en la hoja, como en Word; TextEditor pone el azul del tema
NOTE = convert.NOTE_MARK  # la de convert: con «[Arco 2]» dentro se pintaba solo hasta su «]»
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
VOID_TAGS = convert.VOID_ELEMENTS  # los de MDN: sin cierre
# Las que convert._inline conserva al guardar: otro «<…>» es texto («<Habilidad adquirida>» de una ventana de estado);
# como etiqueta se comía su texto en la hoja
KEPT_TAGS = set(convert.RAW_TAG_NAMES) | {"i", "b", "sup", "sc", "a"}
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
    if text.startswith(convert.HASH):  # «# » de texto al principio del párrafo (convert.HASH)
        text = "#" + text[len(convert.HASH):]

    def add_text(piece):
        if piece:  # «&lt;b>»: el texto «<b>» (convert.LITERAL_LT)
            items.append(("text", convert.ESCAPED_LT.sub("<", piece), tuple(stack)))

    for m in TAG.finditer(text):
        add_text(text[pos:m.start()])
        pos = m.end()
        raw, name = m[0], m[2].lower()
        if convert.ICON.fullmatch(raw):
            items.append(("icon", raw, tuple(stack)))
        elif m[2] not in KEPT_TAGS:
            add_text(raw)
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
        out.append("<br/>" if kind == "br" else convert.LITERAL_LT.sub("&lt;", value) if kind == "text" else value)
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
    f"list-{style}": f'<li class="lista-{style}">' for style in convert.LIST_STYLES} | {
    f"frame{'-' + side if side else ''}:{kind}": f'<p class="marco {kind}{" " + side if side else ""}">'
    for kind in convert.BOX_KINDS for side in ("", "center", "right")} | {
    "table": '<p class="tabla">', "table-head": '<p class="tabla cabecera">'}
CODE_LINE = re.compile(r'<(p|h[1-6]|li|div|blockquote|dd|dt|pre)((?:\s+[\w:.-]+="[^"<>]*")*)\s*>(.*)</\1>', re.S)
CODE_TOKEN = re.compile(r"<!--\s*(\[[^\]]+\])\s*-->")  # como las escribía antes (y la hoja web): se siguen leyendo
# Ilustraciones, imágenes nuevas y separadores en el código, como etiquetas: <img> y <hr> (antes un comentario, que
# parecía texto). Su archivo no es el del EPUB: el nombre lleva el número del bloque, para volver a su marca
CODE_OBJECT = re.compile(r'<img\s+src="(?:ilustracion-(\d+)\.jpg|([0-9a-f]{16}\.jpg))"[^<>]*?/?>'
                         r'|<hr\s+class="separador"\s+id="separador-(\d+)"\s*/?>'
                         r'|<img\s+class="separador"\s+src="([0-9a-f]{16}\.jpg)"[^<>]*?/?>')
# etiqueta empezada y sin su «>»; también «<» o «</» solos al final, la primera letra de «<hr>»
HALF_TAG = re.compile(r"</?(?:[A-Za-z][\w:.-]*(?:\s[^<>]*)?)?(?=<|$)")


def code_object(token):
    """[Ilustración N], [Imagen nombre] o [Separador N] -> su etiqueta en «Código HTML» (CODE_OBJECT)."""
    if m := convert.PICTURE.fullmatch(token):
        return f'<img class="separador" src="{m[2]}" alt="* * *"/>' if m[1] == "Separador" else f'<img src="{m[2]}" alt=""/>'
    m = convert.TOKEN.fullmatch(token)
    if m[1] == "Separador":
        return f'<hr class="separador" id="separador-{m[2]}"/>'
    return f'<img src="ilustracion-{m[2]}.jpg" alt=""/>'


def to_code(text):
    """Texto de la hoja (dump) -> su HTML, un elemento por línea."""
    out = []
    for line in (line for line in text.split("\n\n") if line.strip()):
        bare = line.strip()
        if convert.TOKEN.fullmatch(bare) or convert.PICTURE.fullmatch(bare):
            out.append(code_object(bare))
            continue
        if convert.RULE_LINE.fullmatch(bare):  # una raya <hr>: tal cual
            out.append(bare)
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


def from_code(code, known=()):
    """HTML de la vista de código -> texto para la hoja (como dump). ValueError con la línea que no se entiende o
    que deja una etiqueta sin cerrar: así no se pierde nada en silencio. known: las líneas de la hoja de ahora; una
    línea de código que es la de una de ellas vuelve a ser esa tal cual. <p class="center"> es tanto el párrafo
    centrado de la app («<center>») como el elemento de un EPUB: al tocar otra línea del código, el del EPUB salía
    cambiado (y perdía su elemento)."""
    same, kept = {}, set()  # kept: etiquetas de apertura del libro (EPUB): una línea tocada con una sigue con ella
    for old in known:
        same.setdefault(to_code(old), old)
        if m := convert.OPENING.match(old):
            kept.add(m[0])
    lines = []
    code = convert.double_quoted(code)  # <span class='nota'> escrito a mano: también es una etiqueta
    for n, raw in enumerate(code.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if line in same:
            lines.append(same[line])
            continue
        token = CODE_TOKEN.fullmatch(line)
        if token:
            lines.append(token[1])
            continue
        if thing := CODE_OBJECT.fullmatch(line):
            lines.append(f"[Ilustración {thing[1]}]" if thing[1] else f"[Imagen {thing[2]}]" if thing[2]
                         else f"[Separador {thing[3]}]" if thing[3] else f"[Separador {thing[4]}]")
            continue
        if convert.RULE_LINE.fullmatch(line):  # «<hr>» solo en su línea: la raya de HTML, tal cual
            lines.append(re.sub(r"\s*/?>$", "/>", line))  # con su barra, como en XHTML, y sus atributos
            continue
        if half := HALF_TAG.search(line):  # «<hr» sin «>» aún: no se pone en la hoja como texto
            raise ValueError(f"Línea {n}: a «{half[0][:20]}» le falta el «>» que la cierra")
        m = CODE_LINE.fullmatch(line)
        if not m:
            if line.startswith("<") and convert.OPENING.match(line):
                raise ValueError(f"Línea {n}: el párrafo no se cierra en la misma línea: {line[:60]}")
            m = CODE_LINE.fullmatch(f"<p>{line}</p>")  # texto suelto: un párrafo
        tag, attrs, inner = m[1], m[2] or "", m[3]
        if not convert._balanced(inner):
            raise ValueError(f"Línea {n}: una etiqueta no se cierra o se cierra fuera de orden: {line[:60]}")
        text = convert.line_unescape(convert.NOTE.sub(r"[Nota: \1]", inner).replace(convert.SC_OPEN, "<sc>")
                                     .replace(convert.SC_CLOSE, "</sc>"))
        opening = f"<{tag}{attrs}>"
        prefix = (opening if opening in kept else "" if opening == "<p>" else "# " if opening == "<h1>" else
                  convert.ALIGN_PREFIX.get(next((a for a, o in CODE_CLASSES.items() if o == opening), ""), "") or opening)
        text = text.strip()
        lines.append(prefix + (convert.HASH + text[1:] if not prefix and text.startswith("# ") else text))
    return "\n\n".join(lines)


# «Código HTML» como un editor de código: sangría por nivel y, dentro de una línea, cada etiqueta que solo lleva
# otras etiquetas en sus sub-líneas (U+2028). Es presentación: clean() las quita y deja la línea como era
SUB_LINE = " "
MARKUP_TOKEN = re.compile(r"<!--.*?-->|<\?.*?\?>|<![^>]*>|</?[A-Za-z][^<>]*>|[^<]+|<", re.S)
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
INDENT = " " * 6  # un nivel de sangría: unos 20 px (con dos espacios apenas se veía)


def _tag_kind(token):
    """«open», «close» o None (texto, comentario, declaración o etiqueta que se cierra sola) y su nombre."""
    m = re.match(r"<(/?)([A-Za-z][\w:.-]*)", token)
    if not m or token.startswith(("<!", "<?")):
        return None, ""
    name = m[2].lower()
    if m[1]:
        return "close", name
    return (None if token.endswith("/>") or name in VOID_TAGS else "open"), name


def clean(text):
    """Línea del código sin sus sub-líneas: como se escribió."""
    return re.sub(SUB_LINE + " *", "", text)


def pretty_line(line):
    """La línea con cada etiqueta que solo contiene etiquetas partida en sub-líneas sangradas; los espacios de
    entre ellas se quedan (al final de la sub-línea de antes): clean() devuelve la línea igual. Con texto suelto
    (un párrafo con cursivas) va entera, como en VS Code."""
    root, stack = [], []
    for m in MARKUP_TOKEN.finditer(line):
        token = m[0]
        kind, name = _tag_kind(token)
        if kind == "close" and any(n["name"] == name for n in stack):
            while stack:
                node = stack.pop()
                if node["name"] == name:
                    node["close"] = token
                    break
            continue
        children = stack[-1]["kids"] if stack else root
        if kind == "open":
            node = {"name": name, "open": token, "kids": [], "close": ""}
            children.append(node)
            stack.append(node)
        else:
            children.append(token)

    def raw(node):
        return node if isinstance(node, str) else node["open"] + "".join(map(raw, node["kids"])) + node["close"]

    def texty(nodes):
        return any(isinstance(n, str) and not n.startswith("<") and n.strip() for n in nodes)

    def render(nodes, depth, out):
        for n in nodes:
            if isinstance(n, str) and not n.strip():
                if out:
                    out[-1][1] += n  # espacio de entre etiquetas: se conserva
                continue
            if isinstance(n, str) or not n["kids"] or texty(n["kids"]):
                out.append([depth, raw(n)])
                continue
            out.append([depth, n["open"]])
            render(n["kids"], depth + 1, out)
            if n["close"]:
                out.append([depth, n["close"]])

    if texty(root):
        return line
    out = []
    render(root, 0, out)
    if not out:
        return line
    pretty = (SUB_LINE).join(INDENT * depth + text if k else text for k, (depth, text) in enumerate(out))
    return pretty if clean(pretty) == line else line  # nunca cambia lo escrito


def line_depths(lines):
    """Nivel de sangría de cada línea: las etiquetas abiertas antes de ella (una que empieza cerrando, uno menos)."""
    depths, open_ = [], 0
    for line in lines:
        low = level = open_
        for m in MARKUP_TOKEN.finditer(line):
            kind, _ = _tag_kind(m[0])
            level += {"open": 1, "close": -1}.get(kind, 0)
            low = min(low, level)
        depths.append(max(0, low))
        open_ = max(0, level)
    return depths


def page_code(shell, body):
    """El código de la página entera: lo de antes del texto (shell[1]), el texto (body) y lo de después."""
    if not shell:
        return body
    head = [line for line in shell[1].rstrip().split("\n")]
    foot = [line for line in shell[2].strip().split("\n") if line.strip()]
    return "\n".join([line.strip() for line in head] + ([body] if body else []) + [line.strip() for line in foot])


def _content_line(line):
    """¿Una línea del texto del capítulo? (las de to_code: un párrafo o título con algo dentro, una marca, una raya
    o texto suelto). Un ancla vacía o un <div> sin nada de la cabecera de un EPUB no lo es."""
    m = CODE_LINE.fullmatch(line)
    return bool(m and m[3].strip() or CODE_TOKEN.fullmatch(line) or CODE_OBJECT.fullmatch(line)
                or convert.RULE_LINE.fullmatch(line)
                or (line and not line.startswith("<")))


def split_page(lines):
    """(h, f) de las líneas de la página: cabecera lines[:h] (hasta <body> y lo que envuelve el texto), texto
    lines[h:f] y pie lines[f:] (desde la primera que cierra un envoltorio). Sin <body>: (0, len), todo es texto."""
    body = next((n for n, line in enumerate(lines) if re.search(r"<body\b", line)), None)
    if body is None:
        return 0, len(lines)
    h = body + 1
    while h < len(lines) and lines[h].strip() and not _content_line(lines[h].strip()):  # una línea vacía: del texto
        h += 1
    f = h
    while f < len(lines):
        net = sum({"open": 1, "close": -1}.get(_tag_kind(m[0])[0], 0) for m in MARKUP_TOKEN.finditer(lines[f]))
        if net < 0:
            break
        f += 1
    return h, f


XML_ERRORS = {"mismatched tag": "una etiqueta se cierra con otro nombre o queda alguna sin cerrar antes",
              "not well-formed (invalid token)": "hay un carácter o una etiqueta mal escrita",
              "unbound prefix": "un prefijo (como epub:) sin su xmlns en <html>",
              "junk after document element": "hay algo después de </html>",
              "no element found": "falta cerrar alguna etiqueta",
              "undefined entity": "una entidad (&…;) que XHTML no conoce: usa el carácter o &#número;",
              "duplicate attribute": "un atributo repetido en la misma etiqueta",
              "unclosed token": "una etiqueta o un comentario sin cerrar",
              "syntax error": "hay un error de sintaxis"}


def check_shell(head, foot, f):
    """Error de la cabecera editada («Línea N: …») o "": XML bien formado, con <html>, <head> con <title> y <body>,
    y dentro de <body> solo lo que envuelve el texto. head y foot: sus líneas; f: la línea del código donde empieza
    el pie. Así lo escrito a mano no rompe el capítulo en el EPUB."""
    import xml.etree.ElementTree as ET
    doc = "\n".join(head) + "\n<lnc-texto/>\n" + "\n".join(foot)
    try:
        root = ET.fromstring(doc.encode("utf-8"))
    except ET.ParseError as e:
        line, col = e.position
        where = line if line <= len(head) else f + line - len(head) - 1
        reason = XML_ERRORS.get(str(e).split(":")[0], "no es XML bien formado")  # expat lo dice en inglés
        return f"Línea {where}, columna {col + 1}: la cabecera no es XHTML válido: {reason}"
    local = lambda el: el.tag.rsplit("}", 1)[-1]
    if local(root) != "html":
        return "La página tiene que empezar en <html>"
    kids = {local(el): el for el in root}
    if "head" not in kids or not any(local(el) == "title" for el in kids["head"]):
        return "Falta <head> con su <title>"
    if "body" not in kids:
        return "Falta <body>"
    if [local(el) for el in root] != ["head", "body"] or (root.text or "").strip() or \
            any((el.tail or "").strip() for el in root):
        return "Dentro de <html> solo van <head> y, tras él, <body>: lo demás va dentro de uno de los dos"
    if not any(local(el) == "lnc-texto" for el in kids["body"].iter()):
        return "El texto del capítulo tiene que quedar dentro de <body>"
    if "".join(kids["body"].itertext()).strip():  # un párrafo escrito en la cabecera no lo vería la hoja
        return ("Dentro de <body> la cabecera no lleva texto: pon cada párrafo en su propia línea, entre la cabecera "
                "y el pie")
    return ""


class CodeLook(QSyntaxHighlighter):
    """Colores de la vista de código, como un editor de código: < > y /, nombre de la etiqueta, atributos, sus
    valores, entidades (&amp;) y comentarios."""

    def __init__(self, document, colors):
        super().__init__(document)
        self.colors = colors  # theme.CODE: colores del tema
        self.changes, self.removed = None, None  # «Mostrar cambios», como en Look
        # ¿Alguna línea sabe su línea de antes (show_changes)? Sin ninguna, code_group subía hasta la primera línea
        # por cada una: al cargar un capítulo de 900 párrafos, millones de pasos
        self.originals = False
        self.images = None  # n -> imagen de la ilustración o separador n (la ventana: object_image de la hoja)
        watch_groups(self, code_group)

    def highlightBlock(self, text):
        self.paint_code(text)
        block = self.currentBlock()
        group, n = code_group(block) if self.originals else ([block], 0)
        head = group[0].userData()
        before = head.original if isinstance(head, Changes) else None
        texts = [b.text() if b != block else text for b in group]
        above, below = block.userData().whole if isinstance(block.userData(), Changes) else ("", "")
        # lo quitado tras el último párrafo, encima de la línea que lo sigue (</section>): debajo del párrafo tapaba
        # las líneas del pie, que no tienen hueco
        # sin contar las líneas vacías de en medio (un Intro al final): no tienen dónde enseñarlo
        prev = text_beside(block, -1)
        carried = prev.userData().whole[1] if prev.isValid() and isinstance(prev.userData(), Changes) else ""
        if carried:
            above = "  ".join(x for x in (carried, above) if x)
        if text_beside(block, 1).isValid():
            below = ""
        if self.changes and text.strip() and (texts != [before] or above or below):
            wide = any(ord(c) > 0xFFFF for c in text)
            mark_changes(self, text, group_changes(before, texts, n), lambda i: u16(text, i) if wide else i,
                         QColor(self.changes), above, below,
                         (code_pieces(above, self.images), code_pieces(below, self.images)))
        elif isinstance(block.userData(), Changes):
            block.userData().gone, block.userData().above, block.userData().below = [], [], ""
            block.userData().pieces = ([], [])

    def paint_code(self, text):
        # un formato por color y posiciones UTF-16 solo si hace falta: abrir el código de un capítulo largo pinta
        # miles de etiquetas
        at = (lambda i: u16(text, i)) if any(ord(c) > 0xFFFF for c in text) else (lambda i: i)
        formats = {}

        def paint(start, size, key):
            if key not in formats:
                formats[key] = QTextCharFormat()
                formats[key].setForeground(QColor(self.colors[key]))
            self.setFormat(at(start), at(start + size) - at(start), formats[key])
        for m in re.finditer(r"&#?\w+;", text):
            paint(m.start(), m.end() - m.start(), "entity")
        for m in re.finditer(r"(</?)([\w:-]*)([^<>]*?)(/?>)", text):
            paint(m.start(1), len(m[1]), "punct")
            paint(m.start(2), len(m[2]), "tag")
            paint(m.start(4), len(m[4]), "punct")
            for a in re.finditer(r'([\w:.-]+)(=)("[^"]*")', m[3]):
                paint(m.start(3) + a.start(1), len(a[1]), "attr")
                paint(m.start(3) + a.start(2), 1, "punct")
                paint(m.start(3) + a.start(3), len(a[3]), "value")
        for m in re.finditer(r"<!--.*?-->", text):
            paint(m.start(), m.end() - m.start(), "comment")


# Autocompletado de «Código HTML», como las extensiones de HTML de VS Code: etiquetas tras «<», la que cierra tras
# «</», atributos dentro de una etiqueta y sus valores (las clases del CSS del libro en class="…"), entidades y
# símbolos tras «&», abreviaturas tipo Emmet con Tab («p.nota») y la etiqueta de cierre al escribir el «>» de una que
# abre. Las etiquetas, con su descripción de MDN (developer.mozilla.org/es/docs/Web/HTML/Reference/Elements); solo las
# que la vista de código acepta: cada línea es un párrafo (BLOCK_TAGS) y dentro van las que el libro conserva
# (convert.RAW_TAG): otra saldría escrita como texto en el EPUB
BLOCK_TAGS = {
    "p": "Párrafo", "h1": "Título de capítulo (nivel 1, el más importante)", "h2": "Subtítulo (nivel 2)",
    "h3": "Encabezado de nivel 3", "h4": "Encabezado de nivel 4", "h5": "Encabezado de nivel 5",
    "h6": "Encabezado de nivel 6", "blockquote": "Cita en bloque, apartada del texto",
    "div": "Contenedor genérico, sin efecto hasta darle una clase", "pre": "Texto preformateado, tal como se escribe",
    "li": "Elemento de una lista", "dt": "Término de una lista de descripciones",
    "dd": "Descripción del término anterior", "hr": "Separador de escena (cambio de tema entre párrafos)"}
INLINE_TAGS = {
    "i": "Cursiva: términos, pensamientos, palabras extranjeras", "b": "Negrita: llama la atención",
    "em": "Énfasis (cursiva)", "strong": "Importante o urgente (negrita)", "span": "Contenedor en línea (con clase)",
    "a": "Enlace a otra página, nota o dirección web", "br": "Salto de línea dentro del párrafo",
    "wbr": "Posible corte de línea en una palabra larga", "ruby": "Furigana: anotación sobre el texto base",
    "rt": "Lectura del furigana (dentro de ruby)", "rp": "Paréntesis del furigana para lectores sin ruby",
    "sup": "Superíndice (volado): 1.ᵉʳ, m²", "sub": "Subíndice: H₂O", "small": "Letra pequeña: texto secundario",
    "s": "Tachado: ya no es cierto o relevante", "u": "Subrayado: anotación no textual",
    "mark": "Resaltado, como con marcador", "q": "Cita corta en línea", "cite": "Título de una obra",
    "abbr": "Abreviatura o sigla (su forma completa en title)", "code": "Fragmento de código",
    "kbd": "Tecla o texto que se escribe", "samp": "Salida de un programa", "var": "Nombre de variable",
    "dfn": "Término que se define aquí", "del": "Texto suprimido", "ins": "Texto añadido",
    "bdi": "Aísla texto en otra dirección (árabe, hebreo)", "bdo": "Fuerza la dirección del texto (dir)",
    "time": "Fecha u hora (datetime legible por máquina)", "data": "Dato con su valor legible por máquina (value)",
    "img": "Imagen en línea (icono)"}
HTML_TAGS = tuple(BLOCK_TAGS) + tuple(INLINE_TAGS)
HTML_VOID = convert.VOID_ELEMENTS  # los de MDN: sin cierre
# Atributos de cada elemento (MDN); "" son los globales que sirven en un libro
HTML_ATTRS = {"": ("class", "id", "style", "lang", "xml:lang", "title", "dir", "translate", "hidden", "epub:type"),
              "a": ("href", "hreflang", "rel", "target"), "img": ("src", "alt", "width", "height"),
              "q": ("cite",), "blockquote": ("cite",), "del": ("cite", "datetime"), "ins": ("cite", "datetime"),
              "time": ("datetime",), "data": ("value",), "bdo": ("dir",)}
# Valores que MDN da para cada atributo (y el vocabulario de EPUB para epub:type)
ATTR_VALUES = {"dir": ("ltr", "rtl", "auto"), "translate": ("yes", "no"), "target": ("_blank", "_self"),
               "lang": ("es", "en", "ja", "zh", "ko", "fr", "pt", "it", "de"),
               "xml:lang": ("es", "en", "ja", "zh", "ko", "fr", "pt", "it", "de"),
               "epub:type": ("noteref", "footnote", "endnote", "pagebreak", "bridgehead", "title", "subtitle")}
ENTITIES = (("&amp;", "& ampersand"), ("&lt;", "< menor que"), ("&gt;", "> mayor que"), ("&nbsp;", "espacio duro"),
            ("&quot;", "\" comillas"), ("&#160;", "espacio duro (número)"),
            ("—", "— raya"), ("–", "– semirraya"), ("…", "… puntos suspensivos"), ("«", "« comilla latina"),
            ("»", "» comilla latina"), ("“", "“ comilla inglesa"), ("”", "” comilla inglesa"),
            ("‘", "‘ comilla simple"), ("’", "’ apóstrofo"), ("¡", "¡ apertura de exclamación"),
            ("¿", "¿ apertura de interrogación"), ("·", "· punto medio"), ("※", "※ separador japonés"),
            ("♡", "♡ corazón"), ("☆", "☆ estrella"), ("♪", "♪ nota musical"))


def open_tags(text):
    """Etiquetas abiertas sin cerrar en text, de fuera adentro."""
    stack = []
    for close, name, end in re.findall(r"<(/?)([A-Za-z][\w:-]*)[^<>]*?(/?)>", text):
        if end or name.lower() in HTML_VOID:
            continue
        if not close:
            stack.append(name)
        elif name in stack:
            del stack[len(stack) - 1 - stack[::-1].index(name):]
    return stack


def _tag_items(typed, before):
    """Etiquetas para «<typed» con before delante en la línea: al principio, los párrafos (BLOCK_TAGS) y luego las en
    línea (la línea será un párrafo con ella); dentro, solo las en línea; en un <ruby>, rt y rp primero. Las que empiezan por lo escrito y luego las que lo tienen en el nombre o
    en la descripción («<cursiva» da i). Cada una se escribe entera, con el cursor dentro (o tras la vacía)."""
    stack = open_tags(before)
    tags = BLOCK_TAGS | INLINE_TAGS if not stack and not before.strip() else INLINE_TAGS
    order = list(tags)
    if stack and stack[-1].lower() == "ruby":
        order = ["rt", "rp"] + [t for t in order if t not in ("rt", "rp")]
    typed = typed.lower()
    first = [t for t in order if t.startswith(typed)]
    more = [t for t in order if t not in first and len(typed) >= 2 and (typed in t or typed in fold(tags[t]))]

    def item(t):
        if t in HTML_VOID:
            return f"<{t}>", f"{t}/>", 0, tags[t]
        return f"<{t}>", f"{t}></{t}>", len(t) + 3, tags[t]
    return [item(t) for t in first + more]


def fold(text):
    """Sin tildes ni mayúsculas: «cursíva» encuentra «Cursiva»."""
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")


def complete_at(before, classes=()):
    """Sugerencias para lo escrito antes del cursor en su línea: (cuántas letras de atrás sustituyen, [(lo que se
    ve, lo que se escribe, cuántos caracteres atrás queda el cursor, descripción)]). Sin contexto, (0, [])."""
    if m := re.search(r"&([\w#]*)$", before):  # entidades y símbolos
        typed = m[1].lower()
        found = [(label, text, 0, "") for text, label in ENTITIES
                 if not typed or text.lstrip("&").lower().startswith(typed) or typed in label.lower()]
        return len(m[0]), found
    if m := re.search(r"</([\w:-]*)$", before):  # la etiqueta que cierra: la abierta más de dentro
        stack = open_tags(before[:m.start()])
        return len(m[1]), [(f"</{n}>", f"{n}>", 0, "") for n in reversed(stack) if n.startswith(m[1])][:1]
    if m := re.search(r"<([\w:-]*)$", before):  # etiquetas
        return len(m[1]), _tag_items(m[1], before[:m.start()])
    tag = re.search(r"<([A-Za-z][\w:-]*)\b([^<>]*)$", before)
    if not tag:
        return 0, []
    inside = tag[2]
    if m := re.search(r'\b([\w:-]+)="([^"]*)$', inside):  # el valor de un atributo
        if m[1] == "class":  # las clases del CSS del libro
            word = re.search(r"[\w-]*$", m[2])[0]
            used = set(m[2].split())
            return len(word), [(c, c, 0, "") for c in classes if c.startswith(word) and c not in used]
        values = [(v, v, 0, "") for v in ATTR_VALUES.get(m[1], ()) if v.startswith(m[2]) and v != m[2]]
        return (len(m[2]), values) if values else (0, [])  # href, src…: lo que se escriba
    if m := re.search(r"\s([\w:-]*)$", inside):  # atributos que la etiqueta aún no tiene
        have = set(re.findall(r'([\w:-]+)=', inside))
        names = HTML_ATTRS.get(tag[1].lower(), ()) + HTML_ATTRS[""]  # los suyos primero
        return len(m[1]), [(a, "hidden" if a == "hidden" else f'{a}=""', 0 if a == "hidden" else 1, "")
                           for a in names if a.startswith(m[1]) and a not in have]
    return 0, []


def expand_abbreviation(before):
    """Abreviatura tipo Emmet al final de before («p.nota», «span.sc#x», «i»): (cuántos caracteres sustituye, lo
    que se escribe, cuántos atrás queda el cursor), o None si lo de antes no es una."""
    m = re.search(r"(?:^|(?<=[\s>]))([a-z][a-z0-9]*)((?:[.#][\w-]+)*)$", before)
    if not m or m[1] not in HTML_TAGS or "<" in before[m.start():] or open_tag_here(before):
        return None
    classes = re.findall(r"\.([\w-]+)", m[2])
    ids = re.findall(r"#([\w-]+)", m[2])
    attrs = (f' id="{ids[-1]}"' if ids else "") + (f' class="{" ".join(classes)}"' if classes else "")
    if m[1] in HTML_VOID:
        return len(m[0]), f"<{m[1]}{attrs}/>", 0
    return len(m[0]), f"<{m[1]}{attrs}></{m[1]}>", len(m[1]) + 3


def open_tag_here(before):
    """¿El cursor está dentro de una etiqueta («<p clas|»)?"""
    return bool(re.search(r"<[^<>]*$", before))


def closing_tag(before):
    """La etiqueta que cierra la que acaba de abrirse con el «>» final de before («<i>» → «</i>»), o ""."""
    m = re.search(r"<([A-Za-z][\w:-]*)\b[^<>]*>$", before)
    if not m or before.endswith("/>") or m[1].lower() in HTML_VOID:
        return ""
    return f"</{m[1]}>"


class _Gutter(QWidget):
    """Columna de los números de línea de CodeEdit."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code

    def paintEvent(self, event):
        self.code.paint_gutter(event)


class CodeEdit(QTextEdit):
    """Vista «Código HTML» como un editor de código: número de cada línea a la izquierda, la línea del cursor marcada,
    los colores de CodeLook, un poco de aire entre párrafos y la barra de desplazamiento siempre a la vista. Un
    QTextEdit y no un QPlainTextEdit: este no dibuja el margen de los párrafos."""

    def __init__(self, colors):
        super().__init__(objectName="code")
        self.setAcceptRichText(False)
        self.look = CodeLook(self.document(), colors)
        self.gutter = _Gutter(self)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.document().blockCountChanged.connect(lambda _: self.fit_gutter())
        self.verticalScrollBar().valueChanged.connect(lambda _: self.gutter.update())
        self.textChanged.connect(self.gutter.update)
        self.cursorPositionChanged.connect(self.mark_line)
        self.fit_gutter()
        self.mark_line()
        # autocompletado (complete_at): la lista sale bajo el cursor, ya filtrada aquí
        self.classes, self.items, self.cut = [], [], 0  # clases del CSS del libro; sugerencias de ahora
        self.completer = QCompleter(self)
        self.completer.setWidget(self)
        self.completer.setCompletionMode(QCompleter.CompletionMode.UnfilteredPopupCompletion)
        self.completer.setModel(QStandardItemModel(self.completer))
        self.completer.setMaxVisibleItems(12)
        self.completer.activated.connect(self.accept_completion)

    def before_cursor(self):
        cursor = self.textCursor()
        return cursor.block().text()[:cursor.positionInBlock()]

    def suggest(self, force=False):
        """Sugerencias de lo de antes del cursor; sin forzar (Ctrl+Espacio), solo con algo escrito, tras < & / \" o
        tras un espacio dentro de una etiqueta (sus atributos). Cada una con su descripción (MDN) al lado."""
        popup = self.completer.popup()
        before = self.before_cursor()
        self.cut, self.items = complete_at(before, self.classes)
        if not self.items or not (force or self.cut or before[-1:] in ('<', '&', '/', '"')
                                  or before.endswith(" ") and open_tag_here(before)):
            popup.hide()
            return
        model = self.completer.model()
        model.clear()
        self.shown = {}  # lo que se ve en la lista -> la sugerencia
        for item in self.items[:60]:
            label, hint = item[0], item[3] if len(item) > 3 else ""
            text = f"{label}   {hint}" if hint else label
            row = QStandardItem(text)
            row.setToolTip(hint)
            model.appendRow(row)
            self.shown[text] = item
        rect = self.cursorRect()
        rect.setWidth(popup.sizeHintForColumn(0) + popup.verticalScrollBar().sizeHint().width() + 24)
        self.completer.complete(rect)
        popup.setCurrentIndex(model.index(0, 0))

    def accept_completion(self, label):
        """La sugerencia elegida: sustituye lo escrito y deja el cursor donde toca (dentro de class="")."""
        item = getattr(self, "shown", {}).get(label) or next((i for i in self.items if i[0] == label), None)
        if item is None:
            return
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Left, QTextCursor.MoveMode.KeepAnchor, self.cut)
        cursor.insertText(item[1])
        cursor.movePosition(QTextCursor.MoveOperation.Left, QTextCursor.MoveMode.MoveAnchor, item[2])
        self.setTextCursor(cursor)
        if item[1].endswith('=""'):  # tras un atributo, sus valores (las clases)
            QTimer.singleShot(0, self.suggest)

    def replace_before(self, cut, text, back):
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Left, QTextCursor.MoveMode.KeepAnchor, cut)
        cursor.insertText(text)
        cursor.movePosition(QTextCursor.MoveOperation.Left, QTextCursor.MoveMode.MoveAnchor, back)
        self.setTextCursor(cursor)

    def keyPressEvent(self, event):
        key, popup = event.key(), self.completer.popup()
        if popup.isVisible() and key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Tab, Qt.Key.Key_Escape,
                                         Qt.Key.Key_Backtab):
            event.ignore()  # los recoge el QCompleter: elige (activated) o cierra
            return
        mods = event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
        if key == Qt.Key.Key_Space and mods == Qt.KeyboardModifier.ControlModifier:
            self.suggest(force=True)
            return
        if key == Qt.Key.Key_Tab and not mods and (found := expand_abbreviation(self.before_cursor())):
            self.replace_before(*found)  # «p.nota» + Tab
            return
        super().keyPressEvent(event)
        after = self.textCursor().block().text()[self.textCursor().positionInBlock():]
        if event.text() == ">" and (closing := closing_tag(self.before_cursor())):
            if not after.startswith(closing):  # la que cierra, tras el cursor
                self.replace_before(0, closing, len(closing))
        elif event.text() == ">" and (typed := re.search(r"</[\w:-]+>$", self.before_cursor())) \
                and after.startswith(typed[0]):  # escrita a mano la que ya se puso sola: una, no dos
            cursor = self.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, len(typed[0]))
            cursor.removeSelectedText()
        if (event.text() and event.text().isprintable() and event.text() != ">") or key == Qt.Key.Key_Backspace:
            self.suggest()
        elif popup.isVisible() and (event.text() == ">" or key in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Home,
                                                                    Qt.Key.Key_End)):  # tras «>», Intro es línea nueva
            popup.hide()

    def blockCount(self):
        return self.document().blockCount()

    def show_changes(self, color, removed, originals=None, wholes=None):
        """«Mostrar cambios» en el código: lo puesto subrayado (color) y lo quitado tachado (removed) frente a
        originals, la línea de antes de cada una (None: nueva); sin originals, las que ya tenía. wholes: (quitado
        delante, quitado detrás) de cada línea, los párrafos quitados enteros, como en la hoja."""
        if originals is not None:
            block = self.document().firstBlock()
            for n, line in enumerate(originals):
                if not block.isValid():
                    break
                whole = wholes[n] if wholes and n < len(wholes) else ("", "")
                data = Changes(line) if line is not None or any(whole) else None
                if data is not None:
                    data.whole = whole
                block.setUserData(data)
                block = block.next()
            self.look.originals = any(line is not None for line in originals)
        self.look.changes, self.look.removed = color, removed
        self.look.rehighlight()
        self.gutter.update()  # los números de las líneas cambiadas, de verde

    def event(self, event):
        if event.type() == QEvent.Type.ToolTip and removed_tip(self, event):  # lo quitado, entero
            return True
        return super().event(event)

    def changed_lines(self):
        """[(bloque, rectángulo en la vista)] de las líneas a la vista distintas de su línea de antes, o nuevas."""
        out, dy = [], self.verticalScrollBar().value()
        block = self.cursorForPosition(QPoint(0, 0)).block()
        while block.isValid():
            box = self.document().documentLayout().blockBoundingRect(block).translated(0, -dy)
            if box.top() > self.viewport().height():
                break
            data = block.userData()
            if block.text().strip() and not (isinstance(data, Changes) and data.original == block.text()):
                out.append((block, box))
            block = block.next()
        return out

    def paintEvent(self, event):
        if self.look.changes:  # la línea cambiada, de fondo verde claro, como GitHub
            under = QPainter(self.viewport())
            tint = QColor(self.look.changes)
            tint.setAlpha(tint.alpha() // 3)
            for _, box in self.changed_lines():
                under.fillRect(QRectF(0, box.top(), self.viewport().width(), box.height()), tint)
            under.end()
        super().paintEvent(event)
        self.paint_guides()
        if self.look.changes and self.look.removed:
            painter = QPainter(self.viewport())
            paint_removed(self, painter, self.look.removed)
            painter.end()

    def paint_guides(self):
        """Una raya fina por nivel de sangría a la izquierda de cada línea, como las guías de VS Code: se ve qué
        etiqueta va dentro de cuál."""
        step = self.fontMetrics().horizontalAdvance(INDENT)
        if step <= 0:
            return
        painter = QPainter(self.viewport())
        painter.setPen(QColor(self.look.colors["guide"]))
        left = self.document().documentMargin() - self.horizontalScrollBar().value()
        dy, height = self.verticalScrollBar().value(), self.viewport().height()
        block = self.cursorForPosition(QPoint(0, 0)).block()
        layout = self.document().documentLayout()
        while block.isValid():
            box = layout.blockBoundingRect(block).translated(0, -dy)
            if box.top() > height:
                break
            for level in range(round(block.blockFormat().leftMargin() / step)):
                x = round(left + level * step + step / 4)
                painter.drawLine(x, round(box.top()), x, round(box.bottom()))
            block = block.next()
        painter.end()

    def setPlainText(self, text):
        # Sin colores mientras se carga y se espacia: cada paso volvía a colorear el documento entero (8 s al abrir
        # el código de un capítulo de 900 párrafos). Al volver a ponerlos, se colorea una vez
        self.look.setDocument(None)
        super().setPlainText("\n".join(pretty_line(line) for line in text.split("\n")))
        self.space_paragraphs()
        self.look.originals = False  # líneas nuevas: aún sin su línea de antes
        self.look.setDocument(self.document())

    def space_paragraphs(self):
        """Medio renglón bajo cada párrafo (una línea del código): pegados costaban de leer. Es el aspecto, no un
        cambio: sin paso de deshacer, y por eso no se toca con algo que deshacer (el aire de antes se queda)."""
        doc = self.document()
        if doc.isUndoAvailable():
            return
        fmt = QTextBlockFormat()
        fmt.setBottomMargin(round(self.fontMetrics().height() * 0.5))
        cursor = QTextCursor(doc)
        cursor.select(QTextCursor.SelectionType.Document)
        self.blockSignals(True)  # el código no cambió: nada de aplicarlo en vivo
        doc.setUndoRedoEnabled(False)
        cursor.mergeBlockFormat(fmt)
        # sangría por nivel, como un editor de código: en el margen del párrafo, y así sus líneas partidas por el
        # ancho siguen bajo ella (con espacios volvían al borde)
        step = self.fontMetrics().horizontalAdvance(INDENT)
        block = doc.firstBlock()
        for depth in line_depths([clean(b) for b in self.code_lines()]):
            indent = QTextBlockFormat()
            indent.setLeftMargin(depth * step)
            QTextCursor(block).mergeBlockFormat(indent)
            block = block.next()
        doc.setUndoRedoEnabled(True)
        self.blockSignals(False)

    def code_lines(self):
        """Texto de cada línea, con sus sub-líneas (toPlainText las da como líneas aparte)."""
        out, block = [], self.document().firstBlock()
        while block.isValid():
            out.append(block.text())
            block = block.next()
        return out

    def code(self):
        """El código tal como se escribió: sin las sub-líneas de la presentación."""
        return "\n".join(clean(line) for line in self.code_lines())

    def centerCursor(self):
        self.ensureCursorVisible()
        bar = self.verticalScrollBar()
        bar.setValue(bar.value() + self.cursorRect().center().y() - self.viewport().height() // 2)

    def set_colors(self, colors):
        self.look.colors = colors
        self.look.rehighlight()
        self.mark_line()
        self.gutter.update()

    def gutter_width(self):
        return 16 + self.fontMetrics().horizontalAdvance("9") * max(3, len(str(self.blockCount())))

    def fit_gutter(self):
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        area = self.contentsRect()
        self.gutter.setGeometry(QRect(area.left(), area.top(), self.gutter_width(), area.height()))

    def setFont(self, font):
        super().setFont(font)
        self.fit_gutter()
        self.space_paragraphs()  # el aire va con el tamaño de la letra

    def mark_line(self):
        line = QTextEdit.ExtraSelection()
        tint = QColor(QApplication.palette().color(QPalette.ColorRole.Highlight))
        tint.setAlpha(26)  # la banda del párrafo del cursor de la hoja (Sheet.paintEvent)
        line.format.setBackground(tint)
        line.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        line.cursor = self.textCursor()
        line.cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)  # el párrafo entero, con sus líneas partidas
        line.cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
        self.setExtraSelections([line])
        self.gutter.update()  # el número de la línea del cursor, resaltado

    def paint_gutter(self, event):
        painter = QPainter(self.gutter)
        painter.fillRect(event.rect(), QColor(self.look.colors["gutter"]))
        if self.look.changes:  # el número de la línea cambiada, sobre verde, como GitHub
            for _, box in self.changed_lines():
                painter.fillRect(QRectF(0, box.top(), self.gutter.width(), box.height()), QColor(self.look.changes))
        painter.setFont(self.font())
        block, here = self.cursorForPosition(QPoint(0, 0)).block(), self.textCursor().blockNumber()
        scroll, height = self.verticalScrollBar().value(), self.fontMetrics().height()
        while block.isValid():
            top = round(block.layout().position().y()) - scroll
            if top > event.rect().bottom():
                break
            if block.isVisible():
                painter.setPen(QColor(self.look.colors["number_on" if block.blockNumber() == here else "number"]))
                painter.drawText(0, top, self.gutter.width() - 8, height, Qt.AlignmentFlag.AlignRight,
                                 str(block.blockNumber() + 1))
            block = block.next()
        painter.end()


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
    if kind in ("Imagen", "Separador") and isinstance(n, str) and n not in pictures and IMAGES:  # borrador de otra sesión: la imagen está en su archivo
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


def frame_format(f, first=False, last=False, sub=False, kind=None):
    """Formato de un párrafo en recuadro: cerca unos de otros y con sitio para el borde. Conserva su alineación.
    kind: tipo del recuadro (convert.BOX_KINDS, "" el de siempre); None lo deja como estaba."""
    f.setProperty(FRAMED, True)
    if kind is not None:
        f.setProperty(FRAME_KIND, kind)
    f.setLeftMargin(FRAME_PAD + (SUB if sub else 0))
    f.setRightMargin(FRAME_PAD)
    f.setTopMargin(FRAME_PAD if first else FRAME_GAP)
    f.setBottomMargin(FRAME_PAD + SPACE if last else 0)
    return f


def table_format(f, row):
    """Fila de tabla (row: "table" o "table-head"): con fondo, como una celda, y letra fija para ver sus « | »."""
    f.setProperty(TABLE_ROW, row)
    f.setTopMargin(0)
    f.setBottomMargin(2)
    tint = QColor(QApplication.palette().color(QPalette.ColorRole.Highlight))
    tint.setAlpha(40 if row == "table-head" else 20)
    f.setBackground(tint)
    return f


def unframe_format(f):
    """Saca el párrafo del recuadro: sus márgenes de párrafo normal."""
    f.clearProperty(FRAMED)
    f.clearProperty(FRAME_KIND)
    f.setLeftMargin(0)
    f.setRightMargin(0)
    f.setTopMargin(0)
    f.setBottomMargin(SPACE)
    return f


def _frame_runs(lines):
    """{posición en lines: (primera de su recuadro, última, sangrada)} de las líneas con <marco>."""
    out, run = {}, []
    for i, (_, line) in enumerate(lines + [(None, "")]):
        align = align_of(line)
        # seguidas y del mismo tipo: un recuadro; otro tipo empieza otro
        if align.startswith("frame") and (not run or convert.box_kind(align) == convert.box_kind(align_of(lines[run[0]][1]))):
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
        ruler = not raw and bool(convert.RULE_LINE.fullmatch(bare))  # <hr>: se ve como una raya
        drawn = pictures.get(f"título {k}") if heading and k is not None else None
        table = align if align in ("table", "table-head") else None
        special = bool(token or heading or scene or centered or framed or right or listed or opened or table or ruler)
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
                frame_format(f, *frames[i], kind=convert.box_kind(align))
            if table:
                table_format(f, table)
            if ruler:  # la raya que Qt dibuja para el <hr> de HTML, a lo ancho de la hoja
                f.setProperty(QTextFormat.Property.BlockTrailingHorizontalRulerWidth,
                              QTextLength(QTextLength.Type.PercentageLength, 100))
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
    head = line[:len(line) - len(_strip(line))]
    text = convert.HASH + text[1:] if not head and text.startswith("# ") else text  # como _lines
    return head + text if text else ""


def _lines(block):
    """Líneas de section_text de un párrafo de la hoja: su texto con sus etiquetas (las que tenía y las que pide
    lo puesto con la cinta), y cada objeto en su línea."""
    f = block.blockFormat()
    heading = bool(f.property(HEADING))
    side = "center" if f.property(CENTERED) else "right" if f.property(RIGHTED) else ""
    kind = f.property(FRAME_KIND) or ""
    align = f.property(TABLE_ROW) or (f"frame-{side}".rstrip("-") + (f":{kind}" if kind else "") if f.property(FRAMED)
             else f"list-{list_style(block)}" if block.textList() else side)
    opening = f.property(OPENING)
    prefix = opening if opening else "# " if heading else convert.ALIGN_PREFIX.get(align, "")
    para = paragraph_look(opening)[0] if opening else {}
    out, seq = [], []

    def flush():
        text = _serialize(seq).strip()
        seq.clear()
        if text:  # un párrafo que empieza por «# » escrito no es un título
            out.append(prefix + (convert.HASH + text[1:] if not prefix and text.startswith("# ") else text))

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


def dump_levels(document, known=None):
    """Las líneas de dump con el tipo de cada título ("Capítulo", "Subtítulo"…; None en lo demás). known: las líneas
    de cada párrafo, si ya se tienen (Sheet.all_lines)."""
    out, block, n = [], document.firstBlock(), 0
    while block.isValid():
        f = block.blockFormat()
        level = (f.property(LEVEL) or "Capítulo") if f.property(HEADING) else None
        lines = known[n] if known is not None else [line for line in block_lines(block) if line.strip()]
        out += [(line, level if is_heading_line(line) else None) for line in lines]
        block, n = block.next(), n + 1
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


def compared(block, firsts=None):
    """Línea con que «Mostrar cambios» compara el párrafo: la del principio si se aceptaron cambios otras veces
    (firsts: {línea: la del principio, None si el párrafo es nuevo desde entonces}), si no la cargada."""
    original = original_of(block)
    if original is not None and (convert.TOKEN.fullmatch(original.strip()) or convert.PICTURE.fullmatch(original.strip())) \
            and not objects_of(block):
        return None  # era una ilustración o un separador y ya no la tiene: lo escrito en su sitio es nuevo
    return firsts.get(original, original) if firsts and original is not None else original


def _same(line):
    """Línea comparable: sin el número de las marcas («[Separador 7]»), que cambia con otros capítulos."""
    return convert.TOKEN.sub(r"[\1]", line.strip())


def removed_text(line):
    """Cómo se enseña tachado lo quitado entero: el texto, o «[Ilustración]» y «[Separador]» sin su número."""
    if m := convert.PICTURE.fullmatch(line.strip()):
        return "[Separador]" if m[1] == "Separador" else "[Ilustración]"
    return _same(line) if convert.TOKEN.fullmatch(line.strip()) else plain_line(line)


def deleted_paragraphs(doc, baseline, firsts=None):
    """{n.º de párrafo: ([quitados delante], [quitados detrás], [unidos a él])} frente a baseline: los párrafos,
    ilustraciones y separadores quitados enteros, que «Mostrar cambios» no marcaba (no queda texto en que
    marcarlos). Van en el párrafo de texto que seguía (o tras el último). Uno unido al de antes (Retroceso al
    principio o Supr al final) no se quitó: su texto sigue al final de ese, y se marca solo la unión. Se alinea en
    orden: todos los separadores son «[Separador]», y uno quitado no se veía si quedaba otro."""
    blocks, keys, markable = [], [], []
    block = doc.firstBlock()
    while block.isValid():
        line = compared(block, firsts)
        parts = [x for x in _lines(block) if x.strip()] if objects_of(block) else []
        if len(parts) > 1:  # objeto y texto en un párrafo (pegados): cada línea con la suya, como los guarda dump
            for part in parts[:-1]:
                keys.append(_same(part))
                blocks.append(block)
            line = parts[-1]
        # uno nuevo no casa con nada; uno vaciado tampoco (recuerda su original, pero no se guarda: está quitado)
        empty = not block.text().strip() and bool(line and line.strip())
        keys.append(_same(line) if line is not None and not empty else ("nuevo", len(blocks)))
        if block.text().strip():  # también una ilustración: lo quitado justo antes va con ella, no con el texto de tras ella
            markable.append(block.blockNumber())
        blocks.append(block)
        block = block.next()
    out = {}

    def put(n, slot, lines):
        out.setdefault(n, ([], [], []))[slot].extend(lines)
    ops = difflib.SequenceMatcher(None, [_same(line) for line in baseline], keys, autojunk=False).get_opcodes()
    for op, i1, i2, j1, j2 in ops:
        pending = [line for line in baseline[i1:i2] if line.strip()] if op in ("delete", "replace") else []
        before = blocks[j1 - 1] if j1 else None
        if before is not None and pending and before.blockNumber() in markable:
            rest, joined = before.text().rstrip(), []
            while pending and not convert.TOKEN.fullmatch(pending[-1].strip()):
                text = plain_line(pending[-1]).strip()
                if not text or not rest.endswith(text) or len(rest) == len(text):
                    break
                joined.insert(0, pending.pop())
                rest = rest[:-len(text)].rstrip()
            if joined:
                put(before.blockNumber(), 2, joined)
        if pending and markable:
            n = bisect.bisect_left(markable, blocks[j1].blockNumber()) if j1 < len(blocks) else len(markable)
            if n < len(markable):
                put(markable[n], 0, pending)
            else:
                put(markable[-1], 1, pending)
        elif pending and blocks:  # el capítulo entero quitado: debajo del párrafo vacío que queda
            put(blocks[0].blockNumber(), 1, pending)
    return out


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


def format_spans(before, after):
    """[(inicio, largo)] de las letras de after con otro formato (cursiva, negrita, enlace…) que en before, si las dos
    líneas dicen lo mismo. Sin esto, poner una palabra en cursiva solo encendía la barra del margen."""
    def stacks(line):
        out = []
        for kind, value, stack in parse_inline(_strip(line)):
            out += [stack] * (len(value) if kind == "text" else 1)
        return out
    a, b = stacks(before), stacks(after)
    if len(a) != len(b):
        return []
    spans, start = [], None
    for i, (x, y) in enumerate(zip(a + [None], b + [None])):
        if x != y and start is None:
            start = i
        elif x == y and start is not None:
            spans.append((start, i - start))
            start = None
    return spans


def plain_line(line):
    """Texto de una línea de section_text como se ve en la hoja: sin su prefijo ni sus marcas de formato."""
    # el icono es un objeto: un carácter; una etiqueta vacía, el invisible que la lleva
    shown = {"text": None, "br": LINE_BREAK, "void": VOID_CHAR, "icon": "￼"}
    return "".join(shown[kind] or value for kind, value, _ in parse_inline(_strip(line)))


# Piezas que se comparan en «Mostrar cambios»: cada etiqueta entera (el código), cada letra de chino o japonés (no
# llevan espacios: una «palabra» era el párrafo entero), las demás palabras, cada signo y los espacios. El signo va
# aparte: con «atención.» de una pieza, escribir tras «atención» marcaba también la palabra de antes
CJK = "⺀-鿿豈-﫿＀-￯"
WORDS = re.compile(rf"<[^<>]*>|[{CJK}]|\w+|[^\w\s<]|\s+|<")


def diff_ops(before, after):
    """(lo puesto [(inicio, largo)], lo quitado [(posición en after, texto)]) de after frente a before, pieza a pieza
    (WORDS). Lo que solo es espacio no cuenta."""
    x, y = WORDS.findall(before), WORDS.findall(after)
    starts = [0]
    for token in y:
        starts.append(starts[-1] + len(token))
    adds, gone = [], []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, x, y, autojunk=False).get_opcodes():
        if op in ("replace", "insert") and "".join(y[j1:j2]).strip():
            adds.append((starts[j1], starts[j2] - starts[j1]))
        if op == "delete" and x[i1].isspace() and j1 < len(y) and y[j1] == x[i1]:
            j1 += 1  # « dos» quitado de «Uno dos tres»: su sitio es tras el espacio que queda, ante «tres»
        if op in ("replace", "delete") and (old := "".join(x[i1:i2]).strip()):
            gone.append((starts[j1], old))
    return adds, gone


def added_spans(before, after):
    """[(inicio, largo)] de lo puesto en after que no estaba en before."""
    return diff_ops(before, after)[0]


def removed_spans(before, after):
    """[(posición en after, texto)] de lo que estaba en before y ya no: va donde estaba."""
    return diff_ops(before, after)[1]


def group_changes(before, texts, index):
    """diff_ops del párrafo texts[index] de un grupo que viene de un mismo original before (Intro copia el origen del
    párrafo al nuevo de abajo): se compara el grupo entero, unido por saltos de línea. Así un párrafo nuevo tras otro
    sale como puesto, no como el de arriba cambiado. before None: el párrafo es nuevo, todo puesto."""
    if before is None:
        return [(0, len(texts[index]))], []
    start = sum(len(t) + 1 for t in texts[:index])
    end = start + len(texts[index])
    adds, gone = diff_ops(before, "\n".join(texts))
    return ([(max(a, start) - start, min(a + n, end) - max(a, start)) for a, n in adds if a < end and a + n > start],
            [(p - start, old) for p, old in gone if start <= p <= end])


class Changes(QTextBlockUserData):
    """«Mostrar cambios» de un párrafo: con qué texto se compara (en el código, su línea al abrirlo; None: nueva) y
    lo quitado que se pinta tachado en su hueco (mark_changes)."""

    def __init__(self, original=None):
        super().__init__()
        self.original, self.gone = original, []
        # quitados enteros en su propia línea, encima del párrafo (make_room), y con qué deleted_paragraphs
        # se pintó (Look.update_deleted)
        self.above, self.deleted = [], None
        self.whole = ("", "")  # en «Código HTML»: lo quitado entero (como código) delante y detrás de la línea
        self.below = ""  # quitados enteros tras el último párrafo: debajo de él, en el margen de la hoja
        self.pieces = ([], [])  # lo quitado entero de encima y de debajo como se pinta (removed_pieces)


GAP = 3  # píxeles a cada lado de lo quitado
DELETED_THUMB = 64  # alto como mucho de una ilustración quitada en «Mostrar cambios»


def object_image(doc, n):
    """Imagen de la ilustración o separador n (número o nombre) en la hoja doc: su recurso (_insert_object) o, si
    no llegó a cargarse (un borrador sin ella), la de Sheet.pictures, que queda como recurso. None si no hay."""
    url = QUrl(f"bloque:{n}")
    image = doc.resource(QTextDocument.ResourceType.ImageResource, url)
    if isinstance(image, QImage) and not image.isNull():
        return image
    sheet = doc.parent()
    pictures = getattr(sheet, "pictures", {})
    picture = pictures.get(int(n) if str(n).isdigit() else n)
    if picture is None and not str(n).isdigit() and IMAGES:  # imagen nueva de otra sesión: en su archivo
        picture = QImage(str(Path(IMAGES) / str(n)))
    if isinstance(picture, str) and picture:  # separador escrito: su texto, como en la hoja
        picture = _symbol(picture, getattr(sheet, "color", "#888888"))
    if not isinstance(picture, QImage) or picture.isNull():
        return None
    doc.addResource(QTextDocument.ResourceType.ImageResource, url, picture)
    return picture


def removed_pieces(doc, lines, text=None):
    """Lo quitado entero por piezas: texto (text de la línea, removed_text si no se da, unido con « ¶ ») o, en vez
    de «[Ilustración]» o «[Separador]», (su imagen, ese texto) si la hay (object_image)."""
    text = text or removed_text
    out = []
    for line in lines:
        m = convert.TOKEN.fullmatch(line.strip()) or convert.PICTURE.fullmatch(line.strip())
        image = object_image(doc, m[2]) if m else None
        if image is not None:
            out.append((image, text(line)))
        elif out and isinstance(out[-1], str):
            out[-1] += " ¶ " + text(line)
        else:
            out.append(text(line))
    return out


def code_pieces(code, image):
    """Como removed_pieces con lo quitado entero de «Código HTML» (code_wholes: código unido con «  »): cada <img>
    o <hr> de una ilustración o separador, (su imagen, su código) si image(n) la da."""
    out, at = [], 0
    for m in CODE_OBJECT.finditer(code):
        found = image(next(g for g in m.groups() if g)) if image else None
        if found is None:
            continue
        if code[at:m.start()].strip():
            out.append(code[at:m.start()].strip())
        out.append((found, m[0]))
        at = m.end()
    if out and code[at:].strip():
        out.append(code[at:].strip())
    return out


def thumb_size(image, width=420, tall=DELETED_THUMB):
    """Tamaño de una ilustración quitada: tall de alto como mucho, width de ancho."""
    scale = min(1.0, max(tall, 8) / max(image.height(), 1), max(width, 8) / max(image.width(), 1))
    return QSizeF(image.width() * scale, image.height() * scale)


def paint_pieces(painter, x, bottom, pieces, font, width, color, ink, tall=DELETED_THUMB):
    """Lo quitado entero (removed_pieces) en fila desde x con su borde de abajo en bottom, de width y tall como
    mucho: el texto tachado sobre fondo color y las ilustraciones con su imagen (deleted_look). Devuelve
    [(QRectF, texto entero)] para el globo."""
    strike = label_font(font)
    strike.setStrikeOut(True)
    small = QFontMetrics(strike)
    end, out = x + max(width, 60), []
    thumbs = [None if isinstance(p, str) else thumb_size(p[0], 420, tall) for p in pieces]
    for k, piece in enumerate(pieces):
        if end - x < 2 * GAP + 8:
            break
        if isinstance(piece, str):
            # el ancho de las ilustraciones que siguen, apartado: un párrafo largo no las deja fuera
            later = sum(size.width() + GAP for size in thumbs[k + 1:] if size is not None)
            room = min(end - x, max(end - x - later, 40))
            short = small.elidedText(piece, Qt.TextElideMode.ElideMiddle, int(room - 2 * GAP))
            box = QRectF(x, bottom - small.height(), small.horizontalAdvance(short) + 2 * GAP, small.height())
            painter.fillRect(box, QColor(color))
            painter.setFont(strike)
            painter.setPen(ink)
            painter.drawText(box.adjusted(GAP, 0, 0, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             short)
            out.append((box, piece))
        else:
            size = thumb_size(piece[0], end - x, tall)
            box = QRectF(QPointF(x, bottom - size.height()), size)
            painter.drawImage(box, deleted_look(piece[0], size, color))
            out.append((box, piece[1]))
        x = box.right() + GAP
    return out


_LOOKS = {}  # (imagen, ancho, alto, color) -> deleted_look: se pinta en cada repintado de la hoja


def deleted_look(image, size, color):
    """La ilustración quitada como se enseña: de size, tapada del color de lo quitado, recuadrada y tachada."""
    key = (image.cacheKey(), round(size.width()), round(size.height()), QColor(color).name(QColor.NameFormat.HexArgb))
    if key not in _LOOKS:
        if len(_LOOKS) > 100:
            _LOOKS.clear()
        _LOOKS[key] = _deleted_look(image, size, color)
    return _LOOKS[key]


def _deleted_look(image, size, color):
    out = QImage(max(1, round(size.width())), max(1, round(size.height())), QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    box = QRectF(out.rect())
    painter.drawImage(box, image)
    veil = QColor(color)
    veil.setAlpha(110)
    painter.fillRect(box, veil)
    edge = QColor(color).darker(130)
    edge.setAlpha(255)
    painter.setPen(QPen(edge, 2))
    painter.drawRect(box.adjusted(1, 1, -1, -1))
    painter.drawLine(box.bottomLeft(), box.topRight())  # tachada, como el texto quitado
    painter.end()
    return out


def object_boxes(block):
    """Recuadros (en coordenadas de la hoja) de las ilustraciones y separadores del párrafo."""
    out, layout = [], block.layout()
    origin = layout.position()
    it = block.begin()
    while not it.atEnd():
        fragment = it.fragment()
        f = fragment.charFormat()
        if f.isImageFormat() and f.property(OBJECT):
            image, pos = f.toImageFormat(), fragment.position() - block.position()
            line = layout.lineForTextPosition(pos)
            if line.isValid():
                out.append(QRectF(origin.x() + _x(line, pos), origin.y() + line.y() + line.ascent() - image.height(),
                                  image.width(), image.height()))
        it += 1
    return out


def row_height(pieces, font, tall=DELETED_THUMB):
    """Alto de la línea de lo quitado entero: el de su letra o el de la ilustración más alta."""
    return max([QFontMetrics(label_font(font)).height()] +
               [thumb_size(p[0], 420, tall).height() for p in pieces if not isinstance(p, str)])


def mark_changes(look, text, changes, at, color, above="", below="", pieces=([], [])):
    """Desde highlightBlock de look: changes (group_changes) con lo puesto subrayado del color dado y, para cada trozo
    quitado, un hueco: espaciado tras la letra anterior (o tras la primera, que se pinta después de lo quitado: no
    hay letra antes). Uno largo se acorta con «…» (lo entero sale en un globo). paint_removed lo pinta. above: los
    párrafos, ilustraciones o separadores quitados enteros antes de este, en su propia línea encima (make_room);
    below: los de tras el último párrafo, debajo de él. pieces: los dos como se pintan (removed_pieces)."""
    block = look.currentBlock()
    data = block.userData() if isinstance(block.userData(), Changes) else Changes()
    data.gone, data.above, data.below, data.pieces = [], [], below, pieces
    adds, gone = changes
    if above and not make_room(look, block, text, at, above, {max(pos - 1, 0) for pos, _ in gone}, data):
        data.pieces = ([], pieces[1])  # sin hueco: va como texto dentro del párrafo
        gone = [(0, f"¶ {above} {gone[0][1]}")] + gone[1:] if gone and gone[0][0] == 0 else [(0, f"¶ {above}")] + gone
    for start, size in adds:  # fondo verde, como GitHub; por tramos del mismo formato: letra a letra tardaba segundos
        c, end = at(start), at(start + size)
        while c < end:
            base, stop = look.format(c), c + 1
            while stop < end and look.format(stop) == base:
                stop += 1
            f = QTextCharFormat(base)
            f.setBackground(color)
            look.setFormat(c, stop - c, f)
            c = stop
    room = max(40.0, (look.document().textWidth() or 600) * 0.45)  # lo más ancho de un hueco: media hoja
    for pos, old in gone if text else []:
        c = at(max(pos - 1, 0))
        cursor = QTextCursor(block)
        cursor.setPosition(block.position() + c + 1)  # su formato es el de la letra c
        f, shown = QTextCharFormat(look.format(c)), QTextCharFormat(cursor.charFormat())
        shown.merge(f)
        font = shown.font().resolve(look.document().defaultFont())
        metrics = QFontMetrics(font)
        short = metrics.elidedText(old, Qt.TextElideMode.ElideMiddle, int(room))
        width = metrics.horizontalAdvance(short) + 2 * GAP
        f.setFontLetterSpacingType(QFont.SpacingType.AbsoluteSpacing)
        f.setFontLetterSpacing(width)
        ink = f.foreground().color() if f.hasProperty(QTextFormat.Property.ForegroundBrush) else None
        if not pos:  # la primera letra va tras lo quitado: la de su sitio, invisible
            f.setForeground(QColor(0, 0, 0, 0))
        look.setFormat(c, 1, f)
        data.gone.append((c, text[max(pos - 1, 0)], short, old, width, font, not pos, ink))
    look.setCurrentBlockUserData(data)


def make_room(look, block, text, at, above, taken, data):
    """Hueco de una línea encima del párrafo para lo quitado entero (above): una letra de la primera línea, mejor un
    espacio (no se ve), con la fuente más grande sube la línea; su espaciado le devuelve su ancho. Delante del
    párrafo parecía quitado de él. taken: letras con hueco de lo quitado dentro del párrafo. False si no hay letra."""
    space = next((i for i, ch in enumerate(text[:200]) if ch == " " and i not in taken), None)
    c = space if space is not None else 0 if 0 not in taken else None
    if c is None:
        return False
    cursor = QTextCursor(block)
    cursor.setPosition(block.position() + at(c) + 1)  # su formato es el de la letra c
    f, shown = QTextCharFormat(look.format(at(c))), QTextCharFormat(cursor.charFormat())
    shown.merge(f)
    font = shown.font().resolve(look.document().defaultFont())
    metrics = QFontMetrics(font)
    big = QFont(font)
    # la letra grande también baja: cuanto menos crece, menos se separa la línea de debajo. La etiqueta entra en
    # parte en el espacio entre párrafos (SPACE); una ilustración quitada, con su alto
    room = max(row_height(data.pieces[0], font) + 3 - SPACE, GAP)
    tall = _no_descent(round(metrics.ascent() + room)) if space is not None else None  # una letra: sin su dibujo
    if tall is not None:  # sube la línea sin bajarla: con una ilustración quitada, la 2.ª línea se separaba
        big = tall
    else:
        grow = (metrics.ascent() + room) / max(metrics.ascent(), 1)
        if font.pointSizeF() > 0:
            big.setPointSizeF(font.pointSizeF() * grow)
        else:
            big.setPixelSize(round(font.pixelSize() * grow))
    f.setFont(big)
    f.setFontLetterSpacingType(QFont.SpacingType.AbsoluteSpacing)
    f.setFontLetterSpacing(metrics.horizontalAdvance(text[c]) - QFontMetrics(big).horizontalAdvance(text[c]))
    ink = f.foreground().color() if f.hasProperty(QTextFormat.Property.ForegroundBrush) else None
    if space is None:  # una letra: invisible en grande, paint_removed la pinta en su tamaño
        f.setForeground(QColor(0, 0, 0, 0))
    look.setFormat(at(c), 1, f)
    data.above.append((at(c), text[c], space is None, above, font, ink))
    return True


TALL_FAMILY = "Segoe MDL2 Assets"  # de Windows 10 y 11: su espacio tiene alto y no baja de la línea (sin descendente)


def _no_descent(ascent):
    """Letra de TALL_FAMILY con ese alto sobre la línea, o None si no está (otra que la sustituye sí baja)."""
    font = QFont(TALL_FAMILY)
    font.setPixelSize(max(ascent, 1))
    if QFontInfo(font).family() != TALL_FAMILY or QFontMetrics(font).descent() > 1:
        return None
    return font


def label_font(font):
    """Letra de lo quitado entero en su línea (make_room): algo menor que la del párrafo."""
    small = QFont(font)
    if font.pointSizeF() > 0:
        small.setPointSizeF(font.pointSizeF() * 0.85)
    else:
        small.setPixelSize(max(1, round(font.pixelSize() * 0.85)))
    return small


def _x(line, pos):
    x = line.cursorToX(pos)
    return x[0] if isinstance(x, tuple) else x


def paint_removed(edit, painter, color):
    """Lo quitado de cada párrafo a la vista (Changes.gone), tachado del color dado en el hueco que le abrió
    mark_changes. Se mide con el layout de su línea: al final de una línea el hueco sigue a su letra. Deja en
    edit.removed_boxes [(QRectF, texto entero)] para el globo. Lo quitado entero (Changes.above, below) va en su
    propia línea: encima del párrafo, a la izquierda de una ilustración o debajo del último."""
    doc_layout = edit.document().documentLayout()
    dx, dy = edit.horizontalScrollBar().value(), edit.verticalScrollBar().value()
    boxes = edit.removed_boxes = []
    bottom = edit.viewport().height()
    ink = edit.palette().color(QPalette.ColorRole.Text)

    def label(x, y, pieces, font, width, bottom_at=False, tall=DELETED_THUMB):
        """Lo quitado entero en x, y (y es su borde de abajo si bottom_at), de width y tall como mucho."""
        bottom = y if bottom_at else y + row_height(pieces, font, tall)
        boxes.extend(paint_pieces(painter, x, bottom, pieces, font, width, color, ink, tall))
    block = edit.cursorForPosition(QPoint(0, 0)).block()
    while block.isValid() and doc_layout.blockBoundingRect(block).top() - dy <= bottom:
        data = block.userData()
        if isinstance(data, Changes) and (data.above or data.below):
            layout = block.layout()
            origin = layout.position()
            last = layout.lineAt(layout.lineCount() - 1) if layout.lineCount() else None
            if data.below and last is not None:
                under = origin.y() + last.y() + last.height() + 3
                # en el margen de abajo de la hoja: la ilustración, de su alto como mucho (se cortaba por debajo)
                label(origin.x() + last.x() - dx + GAP, under - dy, data.pieces[1] or [data.below],
                      data.above[0][4] if data.above else edit.document().defaultFont(), last.width(),
                      tall=min(DELETED_THUMB, doc_layout.documentSize().height() - under - 2))
            for c, glyph, hidden, old, font, color_of in data.above:
                line = layout.lineForTextPosition(c or 0)
                if not line.isValid():
                    continue
                top = origin.y() + line.y() - dy
                left = origin.x() + line.x() - dx
                pieces = data.pieces[0] or [old]
                if c is None:  # una ilustración o un separador (o nada): a su izquierda, arriba, en su alto
                    label(left + GAP, top + 2, pieces, font, _x(line, 0) - line.x() - 2 * GAP,
                          tall=line.height() - 4)
                    continue
                room = line.ascent() - QFontMetrics(font).ascent()
                if room < GAP:
                    continue
                label(left + GAP, top + room - 1, pieces, font, line.width() - 2 * GAP, bottom_at=True)
                if hidden:  # la letra que abrió el hueco, en su tamaño y en su sitio
                    painter.setFont(font)
                    painter.setPen(color_of or ink)
                    painter.drawText(QPointF(origin.x() + _x(line, c) - dx, top + line.ascent()), glyph)
        if isinstance(data, Changes) and data.gone:
            layout = block.layout()
            origin = layout.position()
            for c, glyph, short, old, width, font, first, color_of in data.gone:
                line = layout.lineForTextPosition(c)
                if not line.isValid():
                    continue
                after = layout.lineForTextPosition(c + 1)
                end = (_x(line, c + 1) if after.isValid() and after.lineNumber() == line.lineNumber()
                       else _x(line, c) + QFontMetrics(font).horizontalAdvance(glyph) + width)
                x = origin.x() + (_x(line, c) if first else end - width) - dx
                y = origin.y() + line.y() - dy
                box = QRectF(x + GAP, y, width - 2 * GAP, line.height())
                strike = QFont(font)
                strike.setStrikeOut(True)
                painter.setFont(strike)
                painter.fillRect(box.adjusted(-2, 1, 2, -1), QColor(color))  # fondo rojo, como GitHub
                painter.setPen(ink)
                painter.drawText(box, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, short)
                boxes.append((box, old))
                if first:  # la primera letra, tras lo quitado, con su color (el de lo puesto si lo es)
                    painter.setFont(font)
                    painter.setPen(color_of or ink)
                    painter.drawText(QRectF(x + width, y, width + 80, line.height()),
                                     Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, glyph)
        block = block.next()


def removed_tip(edit, event):
    """Globo con lo quitado entero al pasar el ratón por su tachado; True si lo enseñó."""
    point = QPointF(edit.viewport().mapFromGlobal(event.globalPos()))
    hit = next((old for box, old in getattr(edit, "removed_boxes", []) if box.contains(point)), None)
    if hit:
        QToolTip.showText(event.globalPos(), f"Quitado: {hit}", edit)
    return bool(hit)


def text_beside(block, step):
    """El párrafo con texto más cercano antes (step -1) o después (step 1) de block; uno no válido si no hay."""
    other = block.previous() if step < 0 else block.next()
    while other.isValid() and not other.text().strip():
        other = other.previous() if step < 0 else other.next()
    return other


def code_group(block):
    """Como original_group en «Código HTML»: la línea con su línea de antes (Changes.original) y las nuevas que la
    siguen (Intro no les pasa el dato): una línea partida o añadida se compara con todas."""
    def known(b):
        return isinstance(b.userData(), Changes) and b.userData().original is not None
    first = block
    while not known(first) and first.previous().isValid():
        first = first.previous()
    if not known(first):
        return [block], 0
    group, b = [first], first.next()
    while b.isValid() and not known(b):
        group.append(b)
        b = b.next()
    return group, block.blockNumber() - first.blockNumber()


def watch_groups(look, group_of):
    """Al tocar un párrafo de un grupo (original_group, code_group), los demás del grupo se repintan también: lo
    quitado y lo puesto se reparten entre todos."""
    def changed(pos, removed, added):
        doc = look.document()
        if not look.changes or doc is None:  # setPlainText suelta los colores mientras carga: nada que repintar
            return
        first, last = doc.findBlock(pos), doc.findBlock(pos + added)
        touched = set()
        block = first
        while block.isValid():
            touched.add(block.blockNumber())
            if block == last:
                break
            block = block.next()
        others = {b.blockNumber() for n in touched for b in group_of(doc.findBlockByNumber(n))[0]} - touched
        if others:
            QTimer.singleShot(0, look, lambda: [look.rehighlightBlock(doc.findBlockByNumber(n)) for n in sorted(others)
                                                if doc.findBlockByNumber(n).isValid()])
    look.document().contentsChange.connect(changed)


def original_group(block):
    """([párrafos], n.º de block en ellos) de los seguidos que vienen del mismo original que block (Intro copia su
    origen al párrafo nuevo de abajo)."""
    f = block.blockFormat()
    key = (f.property(ORIGINAL), f.property(SOURCE))
    if key[0] is None:
        return [block], 0

    def same(b):
        return b.isValid() and (b.blockFormat().property(ORIGINAL), b.blockFormat().property(SOURCE)) == key
    first = block
    while same(first.previous()):
        first = first.previous()
    group, b = [], first
    while same(b):
        group.append(b)
        b = b.next()
    return group, block.blockNumber() - first.blockNumber()


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
        self.jobs = {"spell": 0, "changes": 0}  # tanda de repintado en curso de cada cosa (_repaint)
        self.tags = {}
        self.changes = None  # color con que se subraya lo puesto en cada párrafo cambiado; None: no se marca
        self.removed = None  # color de lo quitado, tachado en su sitio (theme.mark_colors)
        self.painting = False  # repintando: el documento avisa de un cambio que no lo es (Sheet.forget)
        self.firsts = {}  # compared: lo aceptado otras veces se sigue viendo como cambio al reabrir
        # Párrafos quitados enteros (deleted_paragraphs): baseline, las líneas con que se compara (las cargadas o,
        # con historial, las del principio); deleted, lo que devuelve deleted_paragraphs
        self.baseline, self.deleted, self.deleted_due = [], {}, False
        document.contentsChange.connect(self.deleted_later)
        watch_groups(self, original_group)

    def rehighlight(self):
        self.painting = True
        try:
            super().rehighlight()
        finally:
            self.painting = False

    def rehighlightBlock(self, block):
        self.painting = True
        try:
            super().rehighlightBlock(block)
        finally:
            self.painting = False

    def show_changes(self, color, removed=None, blocks=None):
        """«Mostrar cambios», como GitHub: fondo del color dado (None lo quita) en lo que se puso frente al original,
        y lo que se quitó tachado sobre fondo removed, donde estaba. blocks: solo esos párrafos (los cambiados, los
        de la vista primero), por tandas; repintar la hoja entera congelaba la ventana segundos."""
        self.changes = color
        self.removed = removed or self.removed
        old, self.deleted = self.deleted, self.deleted_now()
        if blocks is None:
            self.rehighlight()
            return
        doc = self.document()  # y los párrafos con quitados delante, los de antes y los de ahora
        anchors = [doc.findBlockByNumber(n) for n in old.keys() | self.deleted.keys()]
        self.jobs["changes"] += 1
        self._repaint("changes", self.jobs["changes"], list(blocks) + [b for b in anchors if b.isValid()])

    def deleted_now(self):
        return deleted_paragraphs(self.document(), self.baseline, self.firsts) if self.changes else {}

    def deleted_later(self, *_):
        """Tras cada cambio, una vez: los párrafos quitados se vuelven a buscar al acabar el cambio."""
        if self.changes and not self.deleted_due:
            self.deleted_due = True
            QTimer.singleShot(0, self, self.update_deleted)

    def update_deleted(self):
        """Repinta los párrafos cuyos quitados de delante cambiaron (borrar uno solo repinta el de al lado). Se mira lo
        que cada uno tiene pintado (Changes.deleted), no su número: al volver una ilustración con Ctrl+Z los números
        se corren y el párrafo de al lado seguía con ella tachada."""
        self.deleted_due = False
        self.deleted = self.deleted_now()
        block = self.document().firstBlock()
        while block.isValid():
            data = block.userData()
            painted = data.deleted if isinstance(data, Changes) else None
            if painted != self.deleted.get(block.blockNumber()):
                self.rehighlightBlock(block)
            block = block.next()

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
        self.jobs["spell"] += 1
        self._repaint("spell", self.jobs["spell"], blocks)

    def _repaint(self, kind, job, blocks):
        """Repinta de REPAINT_BATCH en REPAINT_BATCH párrafos y deja respirar a la ventana entre tandas: los ~600
        de un capítulo sin traducir, de golpe, la congelaban 1 s. Una llamada nueva de lo mismo (kind) cancela esta."""
        if job != self.jobs[kind]:
            return
        settle(self.document())  # maquetada entera antes de tocar unos párrafos (ver Sheet.changeEvent)
        for block in blocks[:REPAINT_BATCH]:
            if block.isValid():  # borrado mientras tanto: ya no hay nada que pintar
                self.rehighlightBlock(block)
        if blocks[REPAINT_BATCH:]:
            QTimer.singleShot(0, self, lambda: self._repaint(kind, job, blocks[REPAINT_BATCH:]))  # sin hoja, no corre

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
            f.setForeground(self.color("subheading" if subtitle else "heading"))  # el tamaño solo no los distinguía
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
        original = compared(block, self.firsts)
        changed = self.changes and text.strip() and not objects_of(block) and block_lines(block) != [original]
        # quitados enteros (deleted_paragraphs): tachados delante de este o, tras el último, detrás; los unidos a
        # este se comparan con él, y solo se marca la unión
        before, after, joined = (self.deleted.get(block.blockNumber(), ((), (), ())) if self.changes
                                 else ((), (), ()))
        def lost(lines):
            return " ¶ ".join(removed_text(line) for line in lines)
        pieces = (removed_pieces(self.document(), before), removed_pieces(self.document(), after))
        if (before or after) and (objects_of(block) or not text.strip()):  # una ilustración (o nada): a su lado
            data = block.userData() if isinstance(block.userData(), Changes) else Changes()
            data.gone, data.below, data.pieces = [], lost(after), pieces
            data.above = [(None, "", False, lost(before), self.document().defaultFont(), None)] if before else []
            self.setCurrentBlockUserData(data)
        elif (changed or before or after or joined) and not objects_of(block):
            group, n = original_group(block)
            base = None if original is None else "\n".join([plain_line(original)] + [plain_line(x) for x in joined])
            adds, gone = group_changes(base, [b.text() if b != block else text for b in group], n) if changed \
                else ([], [])
            if changed and not adds and not gone and len(group) == 1 and original is not None and not joined:
                adds = format_spans(original, block_lines(block)[0])  # mismo texto: lo que cambió de formato
            for line in joined:  # el salto de párrafo quitado, tachado donde empieza el unido
                at_join = text.rfind(plain_line(line).strip())
                if at_join > 0:
                    gone.append((at_join, "¶"))
            mark_changes(self, text, (adds, gone), at, QColor(self.changes), lost(before), lost(after), pieces)
        elif isinstance(block.userData(), Changes):
            block.userData().gone, block.userData().above, block.userData().below = [], [], ""
            block.userData().pieces = ([], [])
        if isinstance(block.userData(), Changes):
            block.userData().deleted = self.deleted.get(block.blockNumber()) if self.changes else None


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
        self.lines_known = {}  # documento -> {párrafo: (revisión, [líneas])} de all_lines, hasta que cambie
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
        self.known[document], self.lines_known[document] = {}, {}
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
        look.baseline = [line for _, line in lines]
        look.tags = self.tags
        look.set_unknown(self.unknown)
        # Maqueta la hoja entera ya: Qt lo hace a trozos y pintaba con ella a medias. Sin mostrar todavía (el editor
        # que se abre), no: al mostrarse toma su ancho y resizeEvent la maqueta entera otra vez (dos veces, 0,1 s)
        if self.isVisible():
            settle(document)
        # Recién cargados, ningún párrafo ha cambiado: changed_blocks volcaba los 440 de un capítulo al abrirlo para
        # saberlo (0,1 s). Lo que se toque cambia de revisión (o lo olvida forget) y se compara entonces
        known, block = self.known[document], document.firstBlock()
        while block.isValid():
            if block.blockFormat().property(ORIGINAL) is not None:
                known[block.fragmentIndex()] = (block.revision(), False)
            block = block.next()
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
        look.baseline = [line for _, line in lines]
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

    def dumped(self, document=None):
        """Como dump, con las líneas recordadas por all_lines: aceptar volcaba cada capítulo visitado cuatro veces
        (1,6 s con uno de 900 párrafos)."""
        return "\n\n".join(line for lines in self.all_lines(document) for line in lines)

    def forget(self, document, pos, added):
        """Los párrafos tocados (texto, formato o deshacer) se vuelven a comparar en changed_blocks. Lo que repinta el
        aspecto (subrayar la ortografía) avisa igual y no toca nada: se recuerda."""
        if getattr(self.look, "painting", False) and self.look.document() is document:
            return
        known, block, last = self.known[document], document.findBlock(pos), document.findBlock(pos + added)
        lines = self.lines_known.setdefault(document, {})
        while block.isValid():
            known.pop(block.fragmentIndex(), None)
            lines.pop(block.fragmentIndex(), None)
            if block == last:
                break
            block = block.next()

    def all_lines(self, document=None):
        """[[líneas de dump] de cada párrafo] de la hoja (o de document, otro capítulo ya cargado), en orden. Cada
        párrafo se recuerda hasta que se toca (forget): volcar la hoja entera en cada tecla tardaba 50 ms en un
        capítulo largo."""
        doc = document or self.document()
        known, out, block = self.lines_known.setdefault(doc, {}), [], doc.firstBlock()
        while block.isValid():
            seen = known.get(block.fragmentIndex())
            if seen is None or seen[0] != block.revision():
                seen = known[block.fragmentIndex()] = (block.revision(),
                                                       [line for line in block_lines(block) if line.strip()])
            out.append(seen[1])
            block = block.next()
        return out

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
        if self.look.changes and self.bars:  # «Mostrar cambios»: el párrafo cambiado, de fondo verde claro (GitHub)
            under = QPainter(self.viewport())
            tint = QColor(self.look.changes)
            tint.setAlpha(tint.alpha() // 3)
            doc, dy = self.document(), self.verticalScrollBar().value()
            width = doc.textWidth() if doc.textWidth() > 0 else self.viewport().width()
            for block in self.bars:
                if block.isValid() and block.document() is doc:
                    text = block.layout().boundingRect().translated(block.layout().position())
                    band = QRectF(doc.documentMargin() - 10 - self.horizontalScrollBar().value(), text.top() - 2 - dy,
                                  width - 2 * doc.documentMargin() + 20, text.height() + 4)
                    if band.intersects(QRectF(event.rect())):
                        under.fillRect(band, tint)
            under.end()
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
        if self.look.changes and self.look.removed:
            paint_removed(self, painter, self.look.removed)
        if False:  # experimento: sin marco verde: recuadrados de verde, como lo quitado de rojo
            painter.setPen(QPen(self.bar_color, 3))
            for block in self.bars:
                if block.isValid() and block.document() is doc and objects_of(block):
                    for box in object_boxes(block):
                        painter.drawRect(box.translated(-dx, -dy).adjusted(-2, -2, 2, 2))
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

        block, top, bottom, kind = doc.firstBlock(), None, None, None
        while block.isValid():
            text = block.layout()
            y = text.position().y()
            framed = block.blockFormat().property(FRAMED)
            if top is not None and (not framed or (block.blockFormat().property(FRAME_KIND) or "") != kind):
                draw(top, bottom)  # acaba el recuadro: el párrafo ya no va en uno, o va en uno de otro tipo
                top = None
            if framed:
                if top is None:
                    top, kind = y - gap, block.blockFormat().property(FRAME_KIND) or ""
                bottom = y + text.boundingRect().height() + gap
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
                words += convert.word_count(block.text())
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

    def undo(self):
        self._step(super().undo)

    def redo(self):
        self._step(super().redo)

    def _step(self, run):
        """Deshacer o rehacer, y el cursor a lo que cambió si quedó fuera: Qt no lo mueve con un paso de set_text
        (el historial de otras veces, la vista de código), y el primer Ctrl+Z tras reabrir el editor cambiaba
        un párrafo sin que se viera."""
        spans, doc = [], self.document()

        def seen(pos, removed, added):
            spans.append((pos, max(pos, pos + added - 1)))  # sin el salto de párrafo del final
        doc.contentsChange.connect(seen)
        try:
            run()
        finally:
            doc.contentsChange.disconnect(seen)
        at = self.textCursor().position()
        if spans and not any(a <= at <= b for a, b in spans):
            cursor = self.textCursor()
            cursor.setPosition(min(max(b for _, b in spans), doc.characterCount() - 1))
            self.setTextCursor(cursor)
            self.ensureCursorVisible()

    def _relevel(self, levels):
        """El tipo de cada título según levels {bloque: tipo}, en el último paso del deshacer: con el texto igual,
        set_text no rehacía nada y un título que una versión de antes no tenía se quedaba sin su tipo (al aceptar
        pasaba de Subtítulo a Capítulo)."""
        cursor, block = None, self.document().firstBlock()
        while block.isValid():
            f = block.blockFormat()
            want = levels.get(source_of(block))
            if f.property(HEADING) and want and f.property(LEVEL) != want:
                if cursor is None:
                    cursor = QTextCursor(self.document())
                    cursor.joinPreviousEditBlock()
                f.setProperty(LEVEL, want)
                cursor.setPosition(block.position())
                cursor.setBlockFormat(f)
            block = block.next()
        if cursor is not None:
            cursor.endEditBlock()

    def set_text(self, text, sources=None, levels=None):
        """Pone text (como dump) en la hoja, en un solo deshacer: lo que vuelve de la vista de código. Un párrafo
        igual que uno de antes sigue siendo de su bloque (su página) y, si es el cargado, recuerda su línea.
        sources: [(bloque, línea)] de text y levels sus títulos, como al cargar: la hoja entera queda de esos bloques
        (replay_history acaba en el texto del libro; versión a versión, un párrafo heredaba el bloque de otro y el
        panel enseñaba otro párrafo). False: la hoja ya tenía ese texto."""
        doc = self.document()
        lines = [line for line in text.split("\n\n") if line.strip()]
        old = self.all_lines()
        if lines == [line for group in old for line in group]:
            if levels:  # solo al reabrir (replay_history): recorrer la hoja en cada tecla del código tardaba
                self._relevel(levels)
            return False
        if sources is not None and [line.strip() for _, line in sources if line.strip()] != [x.strip() for x in lines]:
            sources = None  # no es ese texto
        src = [x for x in sources if x[1].strip()] if sources is not None else None
        # Solo los párrafos entre el primero y el último que cambian: rehacer la hoja entera tardaba medio
        # segundo en un capítulo largo, y la hoja web y el código la ponen al día en cada tecla. Con sources, los
        # de fuera solo cambian de bloque si no es el suyo: rehecha entera, el primer Ctrl+Z tras reabrir el editor
        # llevaba el cursor al final del capítulo
        n, a, i = len(old), 0, 0
        while a < n and lines[i:i + len(old[a])] == old[a]:
            i += len(old[a])
            a += 1
        b, j = n, len(lines)
        while b > a and j - len(old[b - 1]) >= i and lines[j - len(old[b - 1]):j] == old[b - 1]:
            j -= len(old[b - 1])
            b -= 1

        def tied(k):  # en una lista o un recuadro: depende de sus vecinos, se rehacen con él
            block = doc.findBlockByNumber(k)
            return block.textList() is not None or bool(block.blockFormat().property(FRAMED))
        while b < n and (a == b or i == j or tied(b)):
            j += len(old[b])
            b += 1
        while a > 0 and (a == b or i == j or tied(a - 1)):
            a -= 1
            i -= len(old[a])
        before, kept, last, block = [], {}, None, doc.findBlockByNumber(a)  # kept: tipo de los títulos de ahora
        for k in range(a - 1, -1, -1):  # el bloque del libro del párrafo de antes: lo heredan los nuevos
            last = source_of(doc.findBlockByNumber(k))
            if last is not None:
                break
        first = block.position()
        for _ in range(b - a):
            for line in block_lines(block):
                if line.strip():
                    before.append((source_of(block), line))
            if block.blockFormat().property(LEVEL):
                kept[source_of(block)] = block.blockFormat().property(LEVEL)
            end, block = block.position() + block.length() - 1, block.next()
        total, lines = len(lines), lines[i:j]
        pairs = []
        for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, [line for _, line in before], lines,
                                                          autojunk=False).get_opcodes():
            for n in range(j2 - j1):
                same = op == "equal" or (op == "replace" and i2 - i1 == j2 - j1)
                k = before[i1 + n][0] if same else last
                pairs.append((k, lines[j1 + n]))
                last = k if k is not None else last
        if src is not None:
            pairs = [(k, line) for (k, _), line in zip(src[i:j], lines)]
        # los de la carga (levels) mandan: un título quitado en una versión de antes volvía sin su tipo
        levels = {**kept, **(levels or {})} if src is not None else kept
        # y las de lo aceptado otras veces (replay_history): al deshacer hasta ellas se comparan con el principio
        loaded = {line for _, line in self.origins.get(doc, [])} | set(self.look.firsts)
        cursor = QTextCursor(doc)
        cursor.beginEditBlock()
        if b > a:
            cursor.setPosition(first)
            cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            cursor.removeSelectedText()
        insert_lines(cursor, pairs, self.pictures, remember=True, color=self.color, levels=levels)
        block = doc.findBlockByNumber(a)
        for _ in pairs:  # solo los párrafos como al cargar recuerdan su línea (Volver al original, cambios)
            f = block.blockFormat()
            if f.property(ORIGINAL) is not None and f.property(ORIGINAL) not in loaded:
                f.clearProperty(ORIGINAL)
                f.clearProperty(CANON)
                cursor.setPosition(block.position())
                cursor.setBlockFormat(f)
            block = block.next()
        if src is not None:  # los de fuera del tramo, del bloque de sources
            outside, at = [], 0
            for g in range(a):
                outside.append((doc.findBlockByNumber(g), src[at][0]))
                at += len(old[g])
            block, at = doc.lastBlock(), total
            for g in range(n - 1, b - 1, -1):
                at -= len(old[g])
                outside.append((block, src[at][0]))
                block = block.previous()
            for block, k in outside:
                f = block.blockFormat()
                if f.property(SOURCE) != k:
                    f.clearProperty(SOURCE) if k is None else f.setProperty(SOURCE, k)
                    cursor.setPosition(block.position())
                    cursor.setBlockFormat(f)
        cursor.endEditBlock()
        return True

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
        level = self.heading_level()  # el tipo de su fila: también «Quitar», «Separador» o «Índice», que son títulos
        return "Subtítulo" if level == "Subtítulo" else "Capítulo" if level else "Normal"

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

    def set_opening(self, opening):
        """Estilo del original (EPUB): la etiqueta de apertura opening («<p class="no-indent">», o "" para un párrafo
        normal) en los párrafos elegidos o el del cursor, en un deshacer. Títulos, ilustraciones y separadores no."""
        cursor = self.textCursor()
        first = self.document().findBlock(cursor.selectionStart())
        last = self.document().findBlock(cursor.selectionEnd())
        edit = QTextCursor(self.document())
        edit.beginEditBlock()
        block = first
        while block.isValid():
            f = block.blockFormat()
            old = f.property(OPENING) or ""
            tag = (convert.OPENING.match(old) or [None, "p"])[1].lower()
            if not objects_of(block) and block.text().strip() and not f.property(HEADING) and tag == "p":
                self._restyle(block, old, opening)
                self.look.rehighlightBlock(block)
            if block == last:
                break
            block = block.next()
        edit.endEditBlock()

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
        if block.blockFormat().property(OPENING):  # con su etiqueta: <p class="x"> pasa a <h2 class="x"> y al revés,
            self.set_paragraph_style(level or "Normal")  # con sus atributos (quitarla perdía la clase del párrafo)
            return
        cursor.beginEditBlock()
        f = block.blockFormat()
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
        cursor.endEditBlock()
        self.look.rehighlightBlock(block)

    def _unopen(self, block):
        """Quita la etiqueta del libro del párrafo (<p class="x">) al ponerlo en una lista o un recuadro: al volcar
        ganaba ella y la lista o el recuadro se perdían al aceptar. En un EPUB de entrada el elemento conserva su clase
        (faithful usa la etiqueta del original); en la hoja se ve sin ella, y la de la cinta (sin sangría) no hace falta."""
        old = block.blockFormat().property(OPENING)
        if not old:
            return
        self._restyle(block, old, "")  # sus letras, sin el aspecto de su clase
        f = block.blockFormat()
        f.clearProperty(OPENING)
        f.clearProperty(BOOK_LOOK)
        QTextCursor(block).setBlockFormat(f)

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
            if style:
                self._unopen(block)
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
        self.set_frame(None if self.is_framed() else "")

    def insert_table(self, columns, rows, head=True):
        """Tabla tras el párrafo del cursor: una línea por fila y « | » entre las celdas, para escribir encima; con
        head, la primera es la cabecera. Un deshacer."""
        cells = lambda word: " | ".join(f"{word} {n + 1}" for n in range(columns))
        lines = ([(None, convert.ALIGN_PREFIX["table-head"] + cells("Columna"))] if head else []) + [
            (None, convert.ALIGN_PREFIX["table"] + cells("Celda")) for _ in range(rows)]
        cursor = self.textCursor()
        cursor.beginEditBlock()
        cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)
        insert_lines(cursor, lines, self.pictures)
        cursor.endEditBlock()
        self.setTextCursor(cursor)

    def set_frame(self, kind):
        """Los párrafos de la selección en un recuadro del tipo kind (convert.BOX_KINDS, "" el de siempre), o fuera
        de él con None. Solo texto; un deshacer."""
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
        on = kind is not None
        edit = QTextCursor(doc)
        edit.beginEditBlock()
        for n, block in enumerate(blocks):
            if on:
                self._unopen(block)
            f = block.blockFormat()
            if on:
                f.clearProperty(HEADING)
                f.clearProperty(LEVEL)
                f.setObjectIndex(-1)  # un elemento de lista sale de ella
                frame_format(f, n == 0 and not block.previous().blockFormat().property(FRAMED),
                             n == len(blocks) - 1 and not block.next().blockFormat().property(FRAMED), kind=kind)
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

    def note_at(self, position):
        """(inicio, fin, texto) de la nota «[Nota: …]» que contiene position, o None."""
        block = self.document().findBlock(position)
        text, offset = block.text(), position - block.position()
        for m in convert.NOTE_MARK.finditer(text):
            if m.start() <= offset <= m.end():
                return block.position() + m.start(), block.position() + m.end(), m[1]
        return None

    def set_note(self, text, where=None):
        """Pone la nota text: en where (inicio, fin) de una que ya está, o en el cursor. Vacía, quita la de where."""
        cursor = self.textCursor()
        cursor.beginEditBlock()
        if where:
            cursor.setPosition(where[0])
            cursor.setPosition(where[1], QTextCursor.MoveMode.KeepAnchor)
            cursor.removeSelectedText()
        else:
            cursor.clearSelection()
        text = " ".join(text.replace("[", "(").replace("]", ")").split())  # dentro, sin corchetes que la corten
        if text:
            cursor.insertText(f"[Nota: {text}]", _plain_format())
        cursor.endEditBlock()
        self.setTextCursor(cursor)

    def event(self, event):
        """Al pasar el ratón por una nota, su texto entero (en la hoja puede ser largo); por lo quitado, lo quitado."""
        if event.type() == QEvent.Type.ToolTip and removed_tip(self, event):
            return True
        if event.type() == QEvent.Type.ToolTip:
            note = self.note_at(self.cursorForPosition(self.viewport().mapFromGlobal(event.globalPos())).position())
            if note:
                QToolTip.showText(event.globalPos(), f"Nota: {note[2]}", self)
                return True
        return super().event(event)

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
        while not found.isNull() and not found.hasSelection():  # vacía: la siguiente («a*» en «baaa» halla «aaa»)
            step = QTextCursor(found)
            if not step.movePosition(QTextCursor.MoveOperation.Left if backward else QTextCursor.MoveOperation.Right):
                return QTextCursor()
            found = self.document().find(expr, step, self._flags(backward))
        return QTextCursor() if found.isNull() else found

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

    def insert_hidden_title(self, text):
        """Título de capítulo que no se ve en el EPUB (sí en el índice), antes del párrafo del cursor: un capítulo de
        solo una ilustración («Contenido») con su nombre. Un deshacer."""
        block = self.textCursor().block()
        cursor = QTextCursor(block)
        cursor.beginEditBlock()
        # el párrafo de ahora pasa entero (con su formato, su línea original) al de abajo; el de arriba es el título
        cursor.insertBlock(block.blockFormat(), cursor.charFormat())
        cursor.movePosition(QTextCursor.MoveOperation.PreviousBlock)
        insert_lines(cursor, [(None, convert.HIDDEN_TITLE + text)], self.pictures, color=self.color)
        f = cursor.blockFormat()
        f.setProperty(LEVEL, "Capítulo")
        cursor.setBlockFormat(f)
        cursor.endEditBlock()
        self.setTextCursor(cursor)
        self.look.rehighlightBlock(cursor.block())

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
        """[Ilustración k] o [Separador k] dibujado de otro capítulo (cortado allí, pegado aquí): su imagen como
        [Imagen nombre] o [Separador nombre]. Al aceptar, edit_section solo recupera los de su capítulo, y este se
        quedaba como un párrafo con ese texto."""
        m = convert.TOKEN.fullmatch(line)
        if not m or not self.foreign:
            return line
        found = self.foreign(int(m.group(2)))
        if not found:
            return line
        kind, data = found
        return f"[{'Separador' if kind == 'break' else 'Imagen'} {self.store_picture(data)}]"

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
                new = convert.line_unescape(convert.NOTE.sub(r"[Nota: \1]", typed))
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
                original = compared(block, self.look.firsts)
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
                         not block.next().blockFormat().property(FRAMED), kind=convert.box_kind(align))
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
        # Ctrl+Z y Ctrl+Y, a la ventana: con «Todo el libro» deshacen lo de otro capítulo (TextEditor.do)
        if getattr(self, "route_undo", False) and (event.matches(QKeySequence.StandardKey.Undo)
                                                    or event.matches(QKeySequence.StandardKey.Redo)):
            self.command.emit("redo" if event.matches(QKeySequence.StandardKey.Redo) else "undo")
            return
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
        if (typed and typed.isprintable() and not ctrl) or key in (
                Qt.Key.Key_Backspace, Qt.Key.Key_Delete, Qt.Key.Key_Return, Qt.Key.Key_Enter)                 or event.matches(QKeySequence.StandardKey.Cut) or event.matches(QKeySequence.StandardKey.Paste):
            self.keep_objects_apart()
        if not mods and key in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete) and self.object_key(key):
            return
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

    def keep_objects_apart(self):
        """Una selección que acaba justo antes de una ilustración o un separador (o empieza justo tras él) se queda
        en el texto: al borrarla, cortarla o escribir encima se iba también el salto de párrafo y el texto se unía al
        párrafo del objeto (salía centrado con la imagen)."""
        cursor = self.textCursor()
        if not cursor.hasSelection():
            return
        doc = self.document()
        start, end = cursor.selectionStart(), cursor.selectionEnd()
        first, last = doc.findBlock(start), doc.findBlock(end)

        def lone(b):  # solo el objeto, sin texto
            return b.isValid() and bool(objects_of(b)) and not b.text().replace("￼", "").strip()
        if first != last and end == last.position() and lone(last) and not lone(last.previous()):
            end -= 1  # sin el salto de párrafo de antes del objeto
        if first != last and start == first.position() + first.length() - 1 and lone(first) and not lone(first.next()):
            start += 1  # ni el de después
        if (start, end) == (cursor.selectionStart(), cursor.selectionEnd()):
            return
        start, end = min(start, end), max(start, end)  # solo el salto elegido: queda vacía
        forward = cursor.anchor() <= cursor.position()
        cursor.setPosition(start if forward else end)
        cursor.setPosition(end if forward else start, QTextCursor.MoveMode.KeepAnchor)
        self.setTextCursor(cursor)

    def cut(self):
        self.keep_objects_apart()
        super().cut()

    def drop_object(self, block, back=True):
        """Quita la ilustración o el separador del párrafo block (solo él, sin unir los de alrededor), un deshacer; el
        cursor queda donde estaba la tecla: al principio del de después (Retroceso) o al final del de antes (Supr).
        También desde la hoja web, que unía los párrafos."""
        if not block.isValid() or not objects_of(block) or block.text().replace("￼", "").strip():
            return
        before, after = block.previous(), block.next()
        gone = QTextCursor(block)
        gone.beginEditBlock()
        if after.isValid():  # el objeto y el salto de párrafo que lo separa del de después
            gone.movePosition(QTextCursor.MoveOperation.NextBlock, QTextCursor.MoveMode.KeepAnchor)
        elif before.isValid():  # el último: con el salto de antes
            gone.setPosition(before.position() + before.length() - 1)
            gone.setPosition(block.position() + block.length() - 1, QTextCursor.MoveMode.KeepAnchor)
        gone.removeSelectedText()
        gone.endEditBlock()
        if back or not before.isValid():
            gone.setPosition(gone.block().position())
        else:
            gone.setPosition(before.position() + before.length() - 1)
        self.setTextCursor(gone)

    def object_key(self, key):
        """Retroceso al principio de un párrafo tras una ilustración o un separador (o Supr al final de uno antes de
        él): quita el objeto entero, un deshacer. Desde el objeto hacia el texto, solo mueve el cursor. Antes el
        párrafo se unía al objeto: el texto salía centrado con la imagen y «Mostrar cambios» lo tachaba como quitado.
        True si lo hizo."""
        cursor = self.textCursor()
        if cursor.hasSelection():
            return False
        block, back = cursor.block(), key == Qt.Key.Key_Backspace
        edge = cursor.atBlockStart() if back else cursor.atBlockEnd()
        other = block.previous() if back else block.next()
        if not edge or not other.isValid():
            return False

        def lone(b):  # solo el objeto, sin texto
            return bool(objects_of(b)) and not b.text().replace("\ufffc", "").strip()
        if lone(other) and not objects_of(block):
            self.drop_object(other, back)
            return True
        if lone(block) and not objects_of(other):  # del objeto al texto de al lado: se va allí, sin unirlos
            cursor.setPosition(other.position() + (other.length() - 1 if back else 0))
            self.setTextCursor(cursor)
            return True
        return False

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
        self.keep_objects_apart()
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
