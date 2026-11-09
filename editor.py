"""Hoja del editor de texto estilo Word: lo que se ve es lo que sale en el EPUB. Lee y escribe el formato de
convert.section_text (un párrafo por línea con <i>/<b>, "# " delante de un título, las ilustraciones y los
separadores del PDF como [Ilustración N] / [Separador N]), que es lo que guarda convert.edit_section."""
import html
import re

from PySide6.QtCore import QEvent, QMimeData, QObject, QRectF, QRegularExpression, Qt, QUrl, Signal
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QImage, QPainter, QSyntaxHighlighter, QTextBlockFormat,
                           QTextCharFormat, QTextCursor, QTextDocument, QTextFormat, QTextImageFormat)
from PySide6.QtWidgets import QApplication, QTextEdit

import convert

_USER = QTextFormat.Property.UserProperty.value
SOURCE = _USER + 1    # párrafo: índice del bloque del libro del que viene (el PDF sigue al cursor)
ORIGINAL = _USER + 2  # párrafo: su línea al cargar; si vuelve a quedar igual se guarda tal cual (no pierde página)
CANON = _USER + 3     # párrafo: cómo lo volcaba dump al cargar, para saber si cambió
HEADING = _USER + 4   # párrafo: es un título ("# ")
OBJECT = _USER + 5    # imagen: "Ilustración 12" / "Separador 7", la marca que vuelve a section_text
LEVEL = _USER + 6     # título: tipo de su fila ("Capítulo", "Subtítulo"); al aceptar, la fila queda así
CENTERED = _USER + 7  # párrafo centrado (carta, cartel): vuelve a section_text con <center> delante
FRAMED = _USER + 8    # párrafo dentro de un recuadro (ventana de estado): vuelve con <marco> delante
FRAME_PAD = 16        # píxeles entre el recuadro y su texto
SUB = 18              # sangría de una línea sin viñeta en un recuadro con viñetas («HP: 10/10» bajo «■ Lorna»)
MIME = "application/x-lnc-hoja"  # copiar y pegar dentro de la hoja conserva formato e ilustraciones
MARKS = re.compile(r'(</?[ib]>|<a href="[^"]*">|</a>)')
LINK = re.compile(r'<a href="([^"]*)">')
LINK_COLOR = "#2a62c8"  # enlaces en la hoja, como en Word; app.py pone el del tema
NOTE = re.compile(r"\[Nota: [^\]]*\]")
THUMB = 160  # alto de las miniaturas de ilustración en la hoja, en píxeles
SPACE = 10  # píxeles entre párrafos, como el espaciado de Word (no se guarda)
FONT_SIZE = 12  # puntos del texto a 100 %


def _plain_format():
    return QTextCharFormat()


def _runs(text):
    """Trozos de texto con <i>, <b> y <a href>: [(texto, cursiva, negrita, enlace)]."""
    runs, italic, bold, href = [], False, False, ""
    for part in MARKS.split(text):
        if part in ("<i>", "</i>"):
            italic = part == "<i>"
        elif part in ("<b>", "</b>"):
            bold = part == "<b>"
        elif part == "</a>":
            href = ""
        elif LINK.fullmatch(part):
            href = LINK.fullmatch(part).group(1)
        elif part:
            runs.append((part, italic, bold, href))
    return runs


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


def _insert_marked(cursor, text):
    """Texto con <i>/<b>/<a href> como cursiva, negrita y enlaces reales."""
    for part, italic, bold, href in _runs(text):
        f = link_format(href) if href else QTextCharFormat()
        f.setFontItalic(italic)
        f.setFontWeight(QFont.Weight.Bold if bold else QFont.Weight.Normal)
        cursor.insertText(part, f)


def _symbol(text, color, height=28):
    """Imagen con el texto de un separador ("* * *", "☆"): en la hoja va como un objeto que no se edita."""
    font = QFont("Segoe UI", 14)
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
    """[Ilustración N] / [Separador N]: su imagen (o el texto del separador, o la marca si falta) como objeto."""
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
    """La línea sin su prefijo de section_text: "# ", "<center>" o "<marco>"."""
    for prefix in ("# ", convert.CENTER, convert.FRAME):
        if line.startswith(prefix):
            return line[len(prefix):]
    return line


def frame_format(f, first=False, last=False, sub=False):
    """Formato de un párrafo en recuadro: juntos, sin el espacio entre párrafos, y con sitio para el borde."""
    f.setProperty(FRAMED, True)
    f.setAlignment(Qt.AlignmentFlag.AlignLeft)
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


def insert_lines(cursor, lines, pictures, remember=False, raw=False, color="#888888", levels=None):
    """Pone lines ([(bloque, línea de section_text)]) en el cursor. La primera línea de texto va dentro del
    párrafo del cursor (pegar una palabra no parte el párrafo); el resto, en párrafos nuevos. remember: carga
    del editor, cada párrafo recuerda su línea. raw: texto de fuera, sin leer marcas. levels: {bloque: tipo
    de su fila} de los títulos."""
    frames = {} if raw else _frame_runs(list(lines))
    for i, (k, line) in enumerate(lines):
        bare = line.strip()
        token = None if raw else convert.TOKEN.fullmatch(bare)
        heading = not raw and line.startswith("# ")
        centered = not raw and line.startswith(convert.CENTER)
        framed = i in frames
        scene = not raw and not token and bool(bare) and bool(convert.SCENE_BREAK.fullmatch(bare))
        special = bool(token or heading or scene or centered or framed)
        if i or (special and cursor.block().length() > 1):
            cursor.insertBlock(QTextBlockFormat(), _plain_format())
        if i or special or remember:
            f = QTextBlockFormat()
            f.setBottomMargin(SPACE)
            if k is not None:
                f.setProperty(SOURCE, k)
            if token or scene or centered:
                f.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            if centered:
                f.setProperty(CENTERED, True)
            if framed:
                frame_format(f, *frames[i])
            if heading:
                f.setProperty(HEADING, True)
                f.setTopMargin(SPACE * 2)
                if levels and levels.get(k):
                    f.setProperty(LEVEL, levels[k])
            cursor.setBlockFormat(f)
        if token:
            _insert_object(cursor, token.group(1), int(token.group(2)), pictures, color)
        elif raw:
            cursor.insertText(line, _plain_format())
        else:
            _insert_marked(cursor, line if raw else _strip(line))
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
    """[(texto, cursiva, negrita, enlace)] -> texto con <a href>/<b>/<i> bien anidados: se cierra lo justo y
    se abre lo que falta (enlace fuera, luego negrita, cursiva dentro)."""
    def opening(t):
        return f'<a href="{t[2:]}">' if t.startswith("a:") else f"<{t}>"

    out, stack = [], []
    for text, italic, bold, href in runs:
        want = ([f"a:{href}"] if href else []) + [t for t, on in (("b", bold), ("i", italic)) if on]
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
              else "")
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
        text = fragment.text().replace(" ", " ").replace("￼", "")
        if text:
            runs.append((text, f.fontItalic(), f.fontWeight() >= QFont.Weight.DemiBold,
                         f.anchorHref() if f.isAnchor() else ""))
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


def source_of(block):
    """Bloque del libro del que viene el párrafo: el de su objeto, o el que recuerda su formato."""
    objects = objects_of(block)
    if objects:
        return int(objects[0].split()[1])
    value = block.blockFormat().property(SOURCE)
    return int(value) if value is not None else None


def _is_text(block):
    """Párrafo de texto normal (ni título, ni objeto, ni separador escrito, ni vacío)."""
    bare = block.text().strip()
    return (bool(bare) and not block.blockFormat().property(HEADING) and not objects_of(block)
            and not convert.SCENE_BREAK.fullmatch(bare))


class Look(QSyntaxHighlighter):
    """Aspecto que no se guarda: títulos grandes, notas en gris, separadores escritos y palabras desconocidas
    subrayadas. Solo pinta: no cambia el texto ni entra en deshacer."""

    def __init__(self, document):
        super().__init__(document)
        self.unknown = None
        self.tags = {}

    def set_tags(self, tags):
        """Colores de theme.editor_tags del tema actual."""
        self.tags = tags
        self.rehighlight()

    def set_unknown(self, words, changed=None):
        """Palabras que se subrayan. changed: solo se repintan los párrafos con esas (las nuevas o las que dejan
        de estarlo); repintar la hoja entera tardaba 1 s en un capítulo largo."""
        self.unknown = re.compile(r"(?<!\w)(?:" + "|".join(map(re.escape, sorted(words, key=len, reverse=True)))
                                  + r")(?!\w)", re.I) if words else None
        if changed is None:
            self.rehighlight()
            return
        probe = re.compile(r"(?<!\w)(?:" + "|".join(map(re.escape, changed)) + r")(?!\w)", re.I) if changed else None
        block = self.document().firstBlock()
        while probe and block.isValid():
            if probe.search(block.text()):
                self.rehighlightBlock(block)
            block = block.next()

    def color(self, tag):
        return QColor(self.tags.get(tag, {}).get("foreground", "#888888"))

    def highlightBlock(self, text):
        block = self.currentBlock()
        if block.blockFormat().property(HEADING):
            f = QTextCharFormat()
            f.setFontWeight(QFont.Weight.Bold)
            # Capítulo grande; subtítulo un punto menor, como Título 1 y Título 2 en Word
            subtitle = block.blockFormat().property(LEVEL) not in (None, "Capítulo")
            f.setProperty(QTextFormat.Property.FontSizeAdjustment, 1 if subtitle else 2)
            f.setForeground(self.color("heading"))
            self.setFormat(0, len(text), f)
        elif text.strip() and convert.SCENE_BREAK.fullmatch(text.strip()):
            f = QTextCharFormat()
            f.setForeground(self.color("scene"))
            self.setFormat(0, len(text), f)
        for m in NOTE.finditer(text):
            f = QTextCharFormat()
            f.setForeground(self.color("mark"))
            self.setFormat(m.start(), m.end() - m.start(), f)
        for m in self.unknown.finditer(text) if self.unknown else ():
            for c in range(m.start(), m.end()):  # encima de lo que ya tenga (título, nota)
                f = QTextCharFormat(self.format(c))
                f.setUnderlineStyle(QTextCharFormat.UnderlineStyle.SpellCheckUnderline)
                f.setUnderlineColor(self.color("broken"))
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
        self.origins = {}  # documento -> [(bloque, línea)] tal como se cargó
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
        look = Look(document)  # después de cargar: colorea una vez, no párrafo a párrafo
        look.tags = self.tags
        look.set_unknown(self.unknown)
        self.use(document, look)
        self.moveCursor(QTextCursor.MoveOperation.Start)
        return document, look

    def relink(self, document, loaded, pos, added):
        """Al partir un párrafo, el nuevo copia el formato del de arriba y con él su origen. Si el trozo nuevo
        vuelve a ser un párrafo cargado cuyo bloque ya no tiene párrafo (se unió y se vuelve a separar),
        recupera ese bloque: el PDF lo recuadra a él y se guarda tal cual. En el mismo paso de deshacer."""
        if getattr(self, "relinking", False):
            return
        block, last = document.findBlock(pos), document.findBlock(pos + added)
        while block.isValid():
            prev = block.previous()
            k = source_of(block)
            if prev.isValid() and k is not None and k == source_of(prev) and not objects_of(block):
                used, other = set(), document.firstBlock()
                while other.isValid():
                    used.add(source_of(other))
                    other = other.next()
                text = "\n\n".join(_lines(block))
                match = next(((j, line) for j, line in loaded.get(text, ()) if j not in used), None)
                if match:
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
            if block == last:
                break
            block = block.next()

    def use(self, document, look):
        """Muestra otra hoja ya cargada, con el tamaño de letra de ahora."""
        self.setDocument(document)
        document.setDefaultFont(self.font())
        self.look = look

    def content(self):
        return dump(self.document())

    def set_bars(self, blocks, color):
        """Barrita en el margen izquierdo junto a cada párrafo de blocks, como el control de cambios de Word: no
        tapa el texto ni parece una selección."""
        self.bars, self.bar_color = list(blocks), QColor(color)
        self.viewport().update()

    def paintEvent(self, event):
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

    def current_source(self):
        """Bloque del libro del párrafo del cursor o, en uno nuevo sin origen, el del anterior que lo tenga."""
        block = self.textCursor().block()
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
            found += [m.group(0) for m in self.look.unknown.finditer(block.text())]
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
            f.clearProperty(CENTERED)
            if f.property(FRAMED):  # un título no va en recuadro
                f.clearProperty(FRAMED)
                f.setLeftMargin(0)
                f.setRightMargin(0)
                f.setBottomMargin(SPACE)
            f.setAlignment(Qt.AlignmentFlag.AlignLeft)
        else:
            f.clearProperty(HEADING)
            f.clearProperty(LEVEL)
        f.setTopMargin(SPACE * 2 if level else 0)
        cursor.setBlockFormat(f)
        self.look.rehighlightBlock(block)

    def toggle_heading(self):
        self.set_heading(None if self.heading_level() else "Capítulo")

    def toggle_center(self):
        """Ctrl+E: centra el párrafo (carta, cartel) o lo devuelve a su sitio. Solo párrafos de texto."""
        cursor = self.textCursor()
        block = cursor.block()
        if not _is_text(block):
            QApplication.beep()
            return
        f = cursor.blockFormat()
        on = not f.property(CENTERED)
        if on:
            f.setProperty(CENTERED, True)
        else:
            f.clearProperty(CENTERED)
        f.setAlignment(Qt.AlignmentFlag.AlignHCenter if on else Qt.AlignmentFlag.AlignLeft)
        cursor.setBlockFormat(f)

    def is_centered(self):
        return bool(self.textCursor().blockFormat().property(CENTERED))

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
                f.clearProperty(CENTERED)
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
        cursor.mergeCharFormat(link_format(href))
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
            new = re.sub(r"(?<![\w'’])(\w)", lambda m: m.group(1).upper(), text)
        elif text == text.upper():
            new = text.lower()
        else:
            new = text.upper()
        start, doc = cursor.selectionStart(), self.document()
        cursor.beginEditBlock()
        for n, c in enumerate(new):
            if c != text[n] and doc.characterAt(start + n) not in (" ", "￼"):
                one = QTextCursor(doc)
                one.setPosition(start + n)
                one.setPosition(start + n + 1, QTextCursor.MoveMode.KeepAnchor)
                one.insertText(c, one.charFormat())
        cursor.endEditBlock()
        cursor.setPosition(start)
        cursor.setPosition(start + len(new), QTextCursor.MoveMode.KeepAnchor)
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
        _insert_marked(cursor, line)

    # Cambios frente a lo cargado
    def changed_blocks(self):
        """Párrafos con texto distinto del cargado, o nuevos (sin línea original)."""
        found, block = [], self.document().firstBlock()
        while block.isValid():
            lines = [line for line in block_lines(block) if line.strip()]
            original = block.blockFormat().property(ORIGINAL)
            if lines and (original is None or lines != [original]):
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
        original = block.blockFormat().property(ORIGINAL)
        heading, centered = original.startswith("# "), original.startswith(convert.CENTER)
        framed = original.startswith(convert.FRAME)
        cursor = QTextCursor(self.document())
        cursor.setPosition(block.position())
        cursor.beginEditBlock()
        f = block.blockFormat()
        if heading:
            f.setProperty(HEADING, True)
        else:
            f.clearProperty(HEADING)
            f.clearProperty(LEVEL)
        if centered:
            f.setProperty(CENTERED, True)
        else:
            f.clearProperty(CENTERED)
        f.setAlignment(Qt.AlignmentFlag.AlignHCenter if centered else Qt.AlignmentFlag.AlignLeft)
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
        return mime

    def canInsertFromMimeData(self, source):
        return source.hasFormat(MIME) or source.hasText()

    def insertFromMimeData(self, source):
        """De la hoja: con su formato, sin repetir una ilustración que ya está. De fuera: solo el texto."""
        k = self.current_source()
        if source.hasFormat(MIME):
            present = self.objects()
            lines = [line for line in bytes(source.data(MIME)).decode("utf-8").split("\n\n")
                     if not (convert.TOKEN.fullmatch(line) and line[1:-1] in present)]
            raw = False
        else:
            lines, raw = [line.strip() for line in source.text().splitlines() if line.strip()], True
        if not lines:
            return
        cursor = self.textCursor()
        cursor.beginEditBlock()
        cursor.removeSelectedText()
        insert_lines(cursor, [(k, line) for line in lines], self.pictures, raw=raw, color=self.color)
        cursor.endEditBlock()
        self.setTextCursor(cursor)
