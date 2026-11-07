"""Hoja del editor de texto estilo Word: lo que se ve es lo que sale en el EPUB. Lee y escribe el formato de
convert.section_text (un párrafo por línea con <i>/<b>, "# " delante de un título, las ilustraciones y los
separadores del PDF como [Ilustración N] / [Separador N]), que es lo que guarda convert.edit_section."""
import re

from PySide6.QtCore import QEvent, QMimeData, QObject, Qt, QUrl
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
MIME = "application/x-lnc-hoja"  # copiar y pegar dentro de la hoja conserva formato e ilustraciones
MARKS = re.compile(r"(</?[ib]>)")
NOTE = re.compile(r"\[Nota: [^\]]*\]")
THUMB = 160  # alto de las miniaturas de ilustración en la hoja, en píxeles
FONT_SIZE = 12  # puntos del texto a 100 %


def _plain_format():
    return QTextCharFormat()


def _insert_marked(cursor, text):
    """Texto con <i>/<b> como cursiva y negrita reales."""
    italic = bold = False
    for part in MARKS.split(text):
        if part in ("<i>", "</i>"):
            italic = part == "<i>"
        elif part in ("<b>", "</b>"):
            bold = part == "<b>"
        elif part:
            f = QTextCharFormat()
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


def insert_lines(cursor, lines, pictures, remember=False, raw=False, color="#888888"):
    """Pone lines ([(bloque, línea de section_text)]) en el cursor. La primera línea de texto va dentro del
    párrafo del cursor (pegar una palabra no parte el párrafo); el resto, en párrafos nuevos. remember: carga
    del editor, cada párrafo recuerda su línea. raw: texto de fuera, sin leer marcas."""
    for i, (k, line) in enumerate(lines):
        bare = line.strip()
        token = None if raw else convert.TOKEN.fullmatch(bare)
        heading = not raw and line.startswith("# ")
        scene = not raw and not token and bool(bare) and bool(convert.SCENE_BREAK.fullmatch(bare))
        special = bool(token or heading or scene)
        if i or (special and cursor.block().length() > 1):
            cursor.insertBlock(QTextBlockFormat(), _plain_format())
        if i or special or remember:
            f = QTextBlockFormat()
            if k is not None:
                f.setProperty(SOURCE, k)
            if token or scene:
                f.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            if heading:
                f.setProperty(HEADING, True)
            cursor.setBlockFormat(f)
        if token:
            _insert_object(cursor, token.group(1), int(token.group(2)), pictures, color)
        elif raw:
            cursor.insertText(line, _plain_format())
        else:
            _insert_marked(cursor, line[2:] if heading else line)
        if remember:
            f = cursor.blockFormat()
            f.setProperty(ORIGINAL, line)
            f.setProperty(CANON, "\n\n".join(_lines(cursor.block())))
            cursor.setBlockFormat(f)


def load(document, lines, pictures, color="#888888"):
    """Llena la hoja con las líneas de section_text de cada bloque ([(k, línea)]); pictures: {N: QImage o
    texto del separador}. Deshacer empieza vacío."""
    document.clear()
    insert_lines(QTextCursor(document), lines, pictures, remember=True, color=color)
    document.clearUndoRedoStacks()


def _marked(runs):
    """[(texto, cursiva, negrita)] -> texto con <b>/<i> bien anidados: se cierra lo justo y se abre lo que
    falta (negrita fuera, cursiva dentro)."""
    out, stack = [], []
    for text, italic, bold in runs:
        want = [t for t, on in (("b", bold), ("i", italic)) if on]
        while any(t not in want for t in stack):
            out.append(f"</{stack.pop()}>")
        for t in want:
            if t not in stack:
                out.append(f"<{t}>")
                stack.append(t)
        out.append(text)
    out += [f"</{t}>" for t in reversed(stack)]
    return "".join(out)


def _lines(block):
    """Líneas de section_text de un párrafo de la hoja: su texto con marcas, y cada objeto en su línea."""
    heading = bool(block.blockFormat().property(HEADING))
    out, runs = [], []

    def flush():
        text = _marked(runs).strip()
        runs.clear()
        if text:
            out.append(("# " if heading else "") + text)

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
            runs.append((text, f.fontItalic(), f.fontWeight() >= QFont.Weight.DemiBold))
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

    def set_unknown(self, words):
        self.unknown = re.compile(r"(?<!\w)(?:" + "|".join(map(re.escape, sorted(words, key=len, reverse=True)))
                                  + r")(?!\w)", re.I) if words else None
        self.rehighlight()

    def color(self, tag):
        return QColor(self.tags.get(tag, {}).get("foreground", "#888888"))

    def highlightBlock(self, text):
        block = self.currentBlock()
        if block.blockFormat().property(HEADING):
            f = QTextCharFormat()
            f.setFontWeight(QFont.Weight.Bold)
            f.setProperty(QTextFormat.Property.FontSizeAdjustment, 2)
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

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sheet")
        self.document().setDocumentMargin(48)
        self.look = Look(self.document())
        self.pictures, self.color = {}, "#888888"
        self.set_zoom(100)

    def set_content(self, lines, pictures, color="#888888"):
        self.pictures, self.color = dict(pictures), color
        load(self.document(), lines, self.pictures, color)
        self.moveCursor(QTextCursor.MoveOperation.Start)

    def content(self):
        return dump(self.document())

    def set_zoom(self, percent):
        font = QFont("Segoe UI")
        font.setPointSizeF(FONT_SIZE * percent / 100)
        self.document().setDefaultFont(font)

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

    def set_heading(self, on):
        """Título o párrafo normal; no en un párrafo vacío ni en una ilustración."""
        cursor = self.textCursor()
        block = cursor.block()
        if objects_of(block) or not block.text().strip():
            QApplication.beep()
            return
        f = cursor.blockFormat()
        if on:
            f.setProperty(HEADING, True)
        else:
            f.clearProperty(HEADING)
        cursor.setBlockFormat(f)
        self.look.rehighlightBlock(block)

    def toggle_heading(self):
        self.set_heading(not self.textCursor().blockFormat().property(HEADING))

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

    def keyPressEvent(self, event):
        ctrl = event.modifiers() & Qt.KeyboardModifier.ControlModifier
        key = event.key()
        actions = {Qt.Key.Key_K: self.toggle_italic, Qt.Key.Key_I: self.toggle_italic,
                   Qt.Key.Key_N: self.toggle_bold, Qt.Key.Key_B: self.toggle_bold,
                   Qt.Key.Key_T: self.toggle_heading, Qt.Key.Key_J: self.join_next}
        if ctrl and key in actions:
            actions[key]()
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
