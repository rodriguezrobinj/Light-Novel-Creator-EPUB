"""Hoja del editor de texto estilo Word: lo que se ve es lo que sale en el EPUB. Lee y escribe el formato de
convert.section_text (un párrafo por línea con <i>/<b>, "# " delante de un título, las ilustraciones y los
separadores del PDF como [Ilustración N] / [Separador N]), que es lo que guarda convert.edit_section."""
import base64
import difflib
import hashlib
import html
import re
from pathlib import Path

from PySide6.QtCore import QBuffer, QMimeData, QObject, QRectF, QRegularExpression, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QImage, QPainter, QSyntaxHighlighter, QTextBlockFormat,
                           QTextCharFormat, QTextCursor, QTextDocument, QTextFormat, QTextImageFormat)
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
SUB = 18              # sangría de una línea sin viñeta en un recuadro con viñetas («HP: 10/10» bajo «■ Lorna»)
MIME = "application/x-lnc-hoja"  # copiar y pegar dentro de la hoja conserva formato e ilustraciones
WHOLE = MIME + "-parrafos"  # lo copiado acaba en un salto de párrafo: se pegan párrafos enteros
ICON = _USER + 10     # imagen: la etiqueta <img> de un icono del texto (convert.ICON), que vuelve a section_text
MARKS = re.compile(r'(</?(?:i|b|sup|sc)>|<a href="[^"]*">|</a>|<img class="icono" [^>]*>)')
LINK = re.compile(r'<a href="([^"]*)">')
LINK_COLOR = "#2a62c8"  # enlaces en la hoja, como en Word; TextEditor pone el azul del tema
NOTE = re.compile(r"\[Nota: [^\]]*\]")
THUMB = 160  # alto de las miniaturas de ilustración en la hoja, en píxeles
SPACE = 10  # píxeles entre párrafos, como el espaciado de Word (no se guarda)
FONT_SIZE = 12  # puntos del texto a 100 %
IMAGES = None  # carpeta de las imágenes nuevas ([Imagen nombre]); TextEditor pone la del programa (app.IMAGES)
PICTURE_FILES = (".jpg", ".jpeg", ".png", ".webp")  # archivos copiados en el Explorador que se pegan como imagen


def _plain_format():
    return QTextCharFormat()


def _runs(text):
    """Trozos de texto con <i>, <b>, <sup>, <sc> y <a href>: [(texto, cursiva, negrita, enlace, sup, sc)]."""
    runs, on, href = [], {"i": False, "b": False, "sup": False, "sc": False}, ""
    for part in MARKS.split(text):
        tag = part.strip("</>")
        if tag in on and part in (f"<{tag}>", f"</{tag}>"):
            on[tag] = not part.startswith("</")
        elif part == "</a>":
            href = ""
        elif LINK.fullmatch(part):
            href = LINK.fullmatch(part).group(1)
        elif part:
            runs.append((part, on["i"], on["b"], href, on["sup"], on["sc"]))
    return runs


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


def _insert_marked(cursor, text, color="#888888"):
    """Texto con <i>/<b>/<sup>/<sc>/<a href> como cursiva, negrita, superíndice, versalitas y enlaces reales, y
    los iconos (convert.ICON) como imagen de la altura de la letra, del color del texto como los separadores."""
    for part, *marks in _runs(text):
        m = convert.ICON.fullmatch(part)
        if not m:
            cursor.insertText(part, char_format(*marks))
            continue
        picture = QImage.fromData(base64.b64decode(m[1]))
        tinted = QPainter(picture)
        tinted.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        tinted.fillRect(picture.rect(), QColor(color))
        tinted.end()
        name = f"icono:{hash(m[1])}"
        cursor.document().addResource(QTextDocument.ResourceType.ImageResource, QUrl(name), picture)
        f = QTextImageFormat()
        f.merge(char_format(*marks))
        f.setName(name)
        height = QFontMetrics(cursor.document().defaultFont()).height() * 0.8
        f.setHeight(height)
        f.setWidth(picture.width() * height / max(picture.height(), 1))
        f.setVerticalAlignment(QTextCharFormat.VerticalAlignment.AlignMiddle)
        f.setProperty(ICON, part)
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


def frame_format(f, first=False, last=False, sub=False):
    """Formato de un párrafo en recuadro: juntos, sin el espacio entre párrafos, y con sitio para el borde."""
    f.setProperty(FRAMED, True)
    align_format(f)
    f.setLeftMargin(FRAME_PAD + (SUB if sub else 0))
    f.setRightMargin(FRAME_PAD)
    f.setTopMargin(FRAME_PAD if first else 0)
    f.setBottomMargin(FRAME_PAD + SPACE if last else 0)
    return f


def _frame_runs(lines):
    """{posición en lines: (primera de su recuadro, última, sangrada)} de las líneas con <marco>."""
    out, run = {}, []
    for i, (_, line) in enumerate(lines + [(None, "")]):
        if line.startswith(convert.FRAME):
            run.append(i)
            continue
        bulleted = any(lines[j][1][len(convert.FRAME):].lstrip()[:1] in convert.BULLETS for j in run)
        for j in run:
            out[j] = (j == run[0], j == run[-1],
                      bulleted and lines[j][1][len(convert.FRAME):].lstrip()[:1] not in convert.BULLETS)
        run = []
    return out


def insert_lines(cursor, lines, pictures, remember=False, raw=False, color="#888888", levels=None, objects=True):
    """Pone lines ([(bloque, línea de section_text)]) en el cursor. La primera línea de texto va dentro del
    párrafo del cursor (pegar una palabra no parte el párrafo); el resto, en párrafos nuevos. remember: carga
    del editor, cada párrafo recuerda su línea. raw: texto de fuera, sin leer marcas. levels: {bloque: tipo
    de su fila} de los títulos. objects=False: "[Ilustración N]" queda como texto (pegado de fuera)."""
    frames = {} if raw else _frame_runs(list(lines))
    for i, (k, line) in enumerate(lines):
        bare = line.strip()
        token = None if raw or not objects else convert.TOKEN.fullmatch(bare) or convert.PICTURE.fullmatch(bare)
        heading = not raw and line.startswith("# ")
        centered = not raw and line.startswith(convert.CENTER)
        right = not raw and line.startswith(convert.RIGHT)
        framed = i in frames
        scene = not raw and not token and bool(bare) and bool(convert.SCENE_BREAK.fullmatch(bare))
        special = bool(token or heading or scene or centered or framed or right)
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
                f.setTopMargin(SPACE * 2)
                if levels and levels.get(k):
                    f.setProperty(LEVEL, levels[k])
            cursor.setBlockFormat(f)
        if token:
            n = token.group(2)
            _insert_object(cursor, token.group(1), int(n) if n.isdigit() else n, pictures, color)
        elif raw:
            cursor.insertText(line, _plain_format())
        else:
            _insert_marked(cursor, line if raw else _strip(line), color)
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


def _marked(runs):
    """[(texto, cursiva, negrita, enlace, sup, sc)] -> texto con <a href>/<b>/<i>/<sc>/<sup> bien anidados: se
    cierra lo justo y se abre lo que falta (enlace fuera, luego negrita, cursiva, versalitas, superíndice dentro)."""
    def opening(t):
        return f'<a href="{t[2:]}">' if t.startswith("a:") else f"<{t}>"

    out, stack = [], []
    for text, italic, bold, href, sup, sc in runs:
        want = ([f"a:{href}"] if href else []) + [t for t, on in (("b", bold), ("i", italic), ("sc", sc),
                                                                  ("sup", sup)) if on]
        while any(t not in want for t in stack):
            t = stack.pop()
            out.append("</a>" if t.startswith("a:") else f"</{t}>")
        for t in want:
            if t not in stack:
                out.append(opening(t))
                stack.append(t)
        out.append(text)
    out += ["</a>" if t.startswith("a:") else f"</{t}>" for t in reversed(stack)]
    return "".join(out)


def _canonical(line):
    """Lo que _lines daría de la línea recién cargada, sin recorrer el documento (cargar el libro entero)."""
    bare = line.strip()
    if convert.TOKEN.fullmatch(bare):
        return bare
    text = _marked(_runs(_strip(line))).strip()
    return line[:len(line) - len(_strip(line))] + text if text else ""


def _lines(block):
    """Líneas de section_text de un párrafo de la hoja: su texto con marcas, y cada objeto en su línea."""
    f = block.blockFormat()
    heading = bool(f.property(HEADING))
    prefix = ("# " if heading else convert.CENTER if f.property(CENTERED) else convert.FRAME if f.property(FRAMED)
              else convert.RIGHT if f.property(RIGHTED) else "")
    out, runs = [], []

    def flush():
        text = _marked(runs).strip()
        runs.clear()
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
        icon = f.property(ICON) if f.isImageFormat() else None
        text = icon or fragment.text().replace(" ", " ").replace("￼", "")
        if text:
            runs.append((text, f.fontItalic(), f.fontWeight() >= QFont.Weight.DemiBold,
                         f.anchorHref() if f.isAnchor() else "",
                         f.verticalAlignment() == QTextCharFormat.VerticalAlignment.AlignSuperScript,
                         f.fontCapitalization() == QFont.Capitalization.SmallCaps))
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
        out += [(line, level if line.startswith("# ") else None) for line in block_lines(block) if line.strip()]
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
    return MARKS.sub("", convert.ICON.sub("￼", _strip(line)))  # el icono es un objeto: un carácter


def added_spans(before, after):
    """[(inicio, largo)] de lo puesto en after que no estaba en before, palabra a palabra."""
    x, y = re.findall(r"\S+|\s+", before), re.findall(r"\S+|\s+", after)
    starts = [0]
    for token in y:
        starts.append(starts[-1] + len(token))
    return [(starts[j1], starts[j2] - starts[j1]) for op, _, _, j1, j2 in
            difflib.SequenceMatcher(None, x, y, autojunk=False).get_opcodes() if op in ("replace", "insert")]


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
        if block.blockFormat().property(HEADING):
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
        # Fondo tenue del párrafo del cursor: el original lo recuadra, y en la hoja no se veía dónde se estaba
        self.current_color = None  # TextEditor pone el del tema
        self.cursorPositionChanged.connect(lambda: self.current_color and self.viewport().update())
        self.origins = {}  # documento -> [(bloque, línea)] tal como se cargó
        self.known = {}  # documento -> {párrafo: (revisión, ¿cambiado?)} de changed_blocks, hasta que cambie
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
        document.pageCount()  # maqueta la hoja entera ya: Qt lo hace a trozos y pintaba con ella a medias
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
        document.pageCount()  # maquetada entera, como al cargarla

    def scrollContentsBy(self, dx, dy):
        """Al desplazarse Qt mueve lo ya pintado y solo dibuja la franja nueva: si la maquetación cambió entre
        tanto (salto a un párrafo lejano en un capítulo largo), quedaban restos y huecos. Se pinta todo."""
        super().scrollContentsBy(dx, dy)
        self.viewport().update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.document().pageCount()  # otro ancho, otra maquetación: entera, no a trozos
        self.viewport().update()

    def content(self):
        return dump(self.document())

    def set_bars(self, blocks, color):
        """Barrita en el margen izquierdo junto a cada párrafo de blocks, como el control de cambios de Word: no
        tapa el texto ni parece una selección."""
        self.bars, self.bar_color = list(blocks), QColor(color)
        self.viewport().update()

    def paintEvent(self, event):
        if self.current_color:  # debajo del texto: antes de que QTextEdit lo pinte
            block, doc = self.textCursor().block(), self.document()
            text = block.layout().boundingRect().translated(block.layout().position())  # el texto, sin márgenes
            margin = doc.documentMargin()
            width = doc.textWidth() if doc.textWidth() > 0 else self.viewport().width()
            band = QRectF(margin - 10 - self.horizontalScrollBar().value(),
                          text.top() - 3 - self.verticalScrollBar().value(), width - 2 * margin + 20, text.height() + 6)
            under = QPainter(self.viewport())
            under.setRenderHint(QPainter.RenderHint.Antialiasing)
            under.setPen(Qt.PenStyle.NoPen)
            under.setBrush(self.current_color)
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
        self.document().pageCount()  # otra letra, otra maquetación: entera
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
                f.clearProperty(FRAMED)
                f.setLeftMargin(0)
                f.setRightMargin(0)
                f.setBottomMargin(SPACE)
            align_format(f)
        else:
            f.clearProperty(HEADING)
            f.clearProperty(LEVEL)
        f.setTopMargin(SPACE * 2 if level else 0)
        cursor.setBlockFormat(f)
        self.look.rehighlightBlock(block)

    def toggle_heading(self):
        self.set_heading(None if self.heading_level() else "Capítulo")

    def _toggle_align(self, how, prop):
        cursor = self.textCursor()
        block = cursor.block()
        if not _is_text(block) or block.blockFormat().property(FRAMED):  # el recuadro va siempre a la izquierda
            QApplication.beep()
            return
        f = cursor.blockFormat()
        cursor.setBlockFormat(align_format(f, None if f.property(prop) else how))

    def toggle_center(self):
        """Ctrl+E: centra el párrafo (carta, cartel) o lo devuelve a su sitio. Solo párrafos de texto."""
        self._toggle_align("center", CENTERED)

    def toggle_right(self):
        """Ctrl+D, como Word en español: el párrafo a la derecha (firma, fecha de una carta) o a su sitio."""
        self._toggle_align("right", RIGHTED)

    def is_centered(self):
        return bool(self.textCursor().blockFormat().property(CENTERED))

    def is_right(self):
        return bool(self.textCursor().blockFormat().property(RIGHTED))

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
                align_format(f)
                f.clearProperty(HEADING)
                f.clearProperty(LEVEL)
                frame_format(f, n == 0 and not block.previous().blockFormat().property(FRAMED),
                             n == len(blocks) - 1 and not block.next().blockFormat().property(FRAMED))
            else:
                f.clearProperty(FRAMED)
                f.setLeftMargin(0)
                f.setRightMargin(0)
                f.setTopMargin(0)
                f.setBottomMargin(SPACE)
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

    def insert_picture(self, data):
        """Imagen nueva (JPEG) como párrafo propio tras el del cursor. Se guarda en IMAGES con el nombre de su
        contenido y va como [Imagen nombre]: al aceptar, edit_section la vuelve ilustración del libro."""
        name = hashlib.sha1(data).hexdigest()[:16] + ".jpg"
        path = Path(IMAGES) / name
        if not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.pictures[name] = QImage.fromData(data)
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
        _insert_marked(cursor, line, self.color)  # los iconos, del color de los de la hoja

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
        heading, centered = original.startswith("# "), original.startswith(convert.CENTER)
        framed, right = original.startswith(convert.FRAME), original.startswith(convert.RIGHT)
        cursor = QTextCursor(self.document())
        cursor.setPosition(block.position())
        cursor.beginEditBlock()
        f = block.blockFormat()
        if heading:
            f.setProperty(HEADING, True)
        else:
            f.clearProperty(HEADING)
            f.clearProperty(LEVEL)
        align_format(f, "center" if centered else "right" if right else None)
        if framed:
            frame_format(f, not block.previous().blockFormat().property(FRAMED),
                         not block.next().blockFormat().property(FRAMED))
        elif f.property(FRAMED):
            f.clearProperty(FRAMED)
            f.setLeftMargin(0)
            f.setRightMargin(0)
            f.setTopMargin(0)
            f.setBottomMargin(SPACE)
        cursor.setBlockFormat(f)
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
        elif ctrl and not alt and key in (Qt.Key.Key_Equal, Qt.Key.Key_Plus) and mods & Qt.KeyboardModifier.ShiftModifier:
            self.toggle_sup()  # Ctrl+Mayús+= de Word; en el teclado español «=» es Mayús+0
        elif ctrl and not alt and key in actions:
            actions[key]()
        elif key == Qt.Key.Key_F3 and mods & Qt.KeyboardModifier.ShiftModifier:
            self.change_case()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.split_here()  # también con Mayús: el formato no tiene saltos de línea dentro de un párrafo
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
            lines = [line for line in bytes(source.data(MIME)).decode("utf-8").split("\n\n")
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
