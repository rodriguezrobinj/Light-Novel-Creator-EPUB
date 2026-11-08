"""Adaptadores Qt con la API de Tk que usa app.py: la lógica de la ventana se portó a Qt sin reescribirla."""
import os
import re
from contextlib import contextmanager

import pymupdf
from PySide6.QtCore import QEvent, QItemSelectionModel, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPen, QPixmap, QTextCharFormat, QTextCursor, QTextFormat
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
                               QMessageBox, QPlainTextEdit, QScrollArea, QSizePolicy, QTextEdit, QToolTip,
                               QTreeWidget, QTreeWidgetItem, QWidget)

IID = Qt.ItemDataRole.UserRole       # fila -> su iid (el índice del bloque, como texto)
TAGS = Qt.ItemDataRole.UserRole + 1  # fila -> sus tags de aspecto
SRC = QTextFormat.Property.UserProperty + 1  # carácter -> tag src<k> (bloque del que viene)


def app():
    """La QApplication única: la crea la primera ventana o la primera prueba."""
    return QApplication.instance() or QApplication([])


_timers = set()  # referencias: un QTimer sin dueño que se recoge no dispara


def after(ms, fn, *args):
    """root.after de Tk: fn(*args) una vez dentro de ms milisegundos. Devuelve el temporizador."""
    timer = QTimer()
    timer.setSingleShot(True)

    def fire():
        _timers.discard(timer)
        fn(*args)

    timer.timeout.connect(fire)
    _timers.add(timer)
    timer.start(ms)
    return timer


def cancel(timer):
    timer.stop()
    _timers.discard(timer)


def pump():
    """root.update de Tk: procesa lo pendiente (pintar, temporizadores vencidos, señales en cola)."""
    app().processEvents()


def filters(filetypes):
    """[("PDF", "*.pdf")] de Tk -> "PDF (*.pdf)" de QFileDialog."""
    return ";;".join(f"{name} ({pattern})" for name, pattern in filetypes)


class _MessageBox:
    """tkinter.messagebox sobre QMessageBox, con los botones en español."""

    @staticmethod
    def _show(icon, title, message, buttons, parent):
        box = QMessageBox(icon, title, message, parent=parent or QApplication.activeWindow())
        answers = {}
        for answer, text, role in buttons:
            answers[box.addButton(text, role)] = answer
        box.exec()
        return answers.get(box.clickedButton())

    def showinfo(self, title, message, parent=None):
        self._show(QMessageBox.Icon.Information, title, message, [(None, "Aceptar", QMessageBox.ButtonRole.AcceptRole)],
                   parent)

    def showwarning(self, title, message, parent=None):
        self._show(QMessageBox.Icon.Warning, title, message, [(None, "Aceptar", QMessageBox.ButtonRole.AcceptRole)],
                   parent)

    def showerror(self, title, message, parent=None):
        self._show(QMessageBox.Icon.Critical, title, message, [(None, "Aceptar", QMessageBox.ButtonRole.AcceptRole)],
                   parent)

    def askyesno(self, title, message, parent=None):
        return self._show(QMessageBox.Icon.Question, title, message,
                          [(True, "Sí", QMessageBox.ButtonRole.YesRole), (False, "No", QMessageBox.ButtonRole.NoRole)],
                          parent) is True

    def askyesnocancel(self, title, message, parent=None):
        return self._show(QMessageBox.Icon.Question, title, message,
                          [(True, "Sí", QMessageBox.ButtonRole.YesRole), (False, "No", QMessageBox.ButtonRole.NoRole),
                           (None, "Cancelar", QMessageBox.ButtonRole.RejectRole)], parent)


class _FileDialog:
    """tkinter.filedialog sobre QFileDialog: "" o () al cancelar, como Tk."""

    @staticmethod
    def askopenfilename(filetypes=(), initialdir=None, title="Abrir", parent=None):
        return QFileDialog.getOpenFileName(parent or QApplication.activeWindow(), title, initialdir or "",
                                           filters(filetypes))[0]

    @staticmethod
    def askopenfilenames(filetypes=(), initialdir=None, title="Abrir", parent=None):
        return tuple(QFileDialog.getOpenFileNames(parent or QApplication.activeWindow(), title, initialdir or "",
                                                  filters(filetypes))[0])

    @staticmethod
    def asksaveasfilename(defaultextension="", filetypes=(), initialdir=None, initialfile="", title="Guardar como",
                          parent=None):
        start = os.path.join(initialdir or "", initialfile or "")
        path = QFileDialog.getSaveFileName(parent or QApplication.activeWindow(), title, start, filters(filetypes))[0]
        if path and defaultextension and not os.path.splitext(path)[1]:
            path += defaultextension
        return path

    @staticmethod
    def askdirectory(initialdir=None, title="Elegir carpeta", parent=None):
        return QFileDialog.getExistingDirectory(parent or QApplication.activeWindow(), title, initialdir or "")


messagebox, filedialog = _MessageBox(), _FileDialog()


class RowList(QTreeWidget):
    """ttk.Treeview de una sola altura: filas con iid, columnas con nombre y tags de aspecto."""
    selected = Signal()            # <<TreeviewSelect>>
    pressed = Signal(object)       # <Button-1>: evento con .x e .y del viewport
    double_clicked = Signal(object)
    released = Signal(object)

    class Event:
        def __init__(self, pos):
            self.x, self.y = pos.x(), pos.y()

    def __init__(self, columns, headings, widths, stretch="title"):
        super().__init__()
        self.columns = list(columns)
        self.rows = {}   # iid -> QTreeWidgetItem
        self.looks = {}  # tag -> (fuente, color)
        self.setColumnCount(len(columns))
        self.setHeaderLabels([headings[c] for c in columns])
        self.setRootIsDecorated(False)
        self.setUniformRowHeights(True)
        self.setAllColumnsShowFocus(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        header = self.header()
        header.setStretchLastSection(False)
        for n, col in enumerate(columns):
            header.setSectionResizeMode(n, QHeaderView.ResizeMode.Stretch if col == stretch
                                        else QHeaderView.ResizeMode.Interactive)
            self.setColumnWidth(n, widths.get(col, 80))
            self.headerItem().setTextAlignment(n, Qt.AlignmentFlag.AlignLeft if col == stretch
                                               else Qt.AlignmentFlag.AlignCenter)
        self.stretch = stretch
        self.keys = {}  # (tecla, modificadores) -> función, antes que la búsqueda por teclado de Qt
        self.itemSelectionChanged.connect(self.selected)

    def bind_key(self, key, fn, modifiers=Qt.KeyboardModifier.NoModifier):
        self.keys[(key, modifiers)] = fn

    def full_text_at(self, pos):
        """Texto de la celda en pos (del viewport) si no cabe y Qt lo corta con «…»; None si cabe."""
        index = self.indexAt(pos)
        item = self.itemFromIndex(index) if index.isValid() else None
        if item is None:
            return None
        text, col = item.text(index.column()), index.column()
        room = self.visualRect(index).width() - 8  # márgenes de la celda
        return text if QFontMetrics(item.font(col)).horizontalAdvance(text) > room else None

    def viewportEvent(self, event):
        if event.type() == QEvent.Type.ToolTip:  # con la ventana estrecha, el título cortado se lee entero
            text = self.full_text_at(event.pos())
            if text:
                QToolTip.showText(event.globalPos(), text, self.viewport())
                return True
        return super().viewportEvent(event)

    def keyPressEvent(self, event):
        mods = event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
        fn = self.keys.get((event.key(), mods))
        if fn is None and mods == Qt.KeyboardModifier.ShiftModifier:  # con Mayús o Bloq Mayús: la misma letra
            fn = self.keys.get((event.key(), Qt.KeyboardModifier.NoModifier))
        if fn:
            fn()
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self.pressed.emit(self.Event(event.position().toPoint()))

    def mouseDoubleClickEvent(self, event):
        super().mouseDoubleClickEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit(self.Event(event.position().toPoint()))

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self.released.emit(self.Event(event.position().toPoint()))

    def insert(self, parent, index, iid, values=(), tags=()):
        item = QTreeWidgetItem([str(v) for v in values])
        item.setData(0, IID, iid)
        for n, col in enumerate(self.columns):
            if col != self.stretch:
                item.setTextAlignment(n, Qt.AlignmentFlag.AlignCenter)
        if index == "end":
            self.addTopLevelItem(item)
        else:
            self.insertTopLevelItem(index, item)
        self.rows[iid] = item
        self._look(item, tags)
        return iid

    def delete(self, *iids):
        # Qt avisa del cambio de selección a mitad del borrado, con la fila a medio quitar: se avisa al final
        before = self.selection()
        self.blockSignals(True)
        if len(iids) == len(self.rows):  # vaciar la lista: de golpe, no fila a fila
            self.rows.clear()
            self.clear()
        else:
            for iid in iids:
                self.takeTopLevelItem(self.indexOfTopLevelItem(self.rows.pop(iid)))
        self.blockSignals(False)
        if self.selection() != before:
            self.selected.emit()

    def get_children(self, _=""):
        return tuple(self.topLevelItem(n).data(0, IID) for n in range(self.topLevelItemCount()))

    def exists(self, iid):
        return iid in self.rows

    def set(self, iid, column, value=None):
        n = self.columns.index(column)
        if value is None:
            return self.rows[iid].text(n)
        self.rows[iid].setText(n, str(value))
        return None

    def item(self, iid, tags=None):
        if tags is not None:
            self._look(self.rows[iid], tags)
        return {"tags": self.rows[iid].data(0, TAGS) or ()}

    def tag_configure(self, tag, font=None, foreground=None):
        self.looks[tag] = (font, foreground)
        for item in self.rows.values():
            if tag in (item.data(0, TAGS) or ()):
                self._look(item, item.data(0, TAGS))

    def _look(self, item, tags):
        tags = tuple(tags)
        item.setData(0, TAGS, tags)
        font, color = next((self.looks[t] for t in tags if t in self.looks), (None, None))
        for n in range(len(self.columns)):
            item.setFont(n, font or self.font())
            item.setData(n, Qt.ItemDataRole.ForegroundRole, QColor(color) if color else None)

    def selection(self):
        return tuple(item.data(0, IID) for item in self.selectedItems())

    def selection_set(self, iid):
        # Como Tk, sustituye la selección: con selección múltiple, Qt sumaría la fila si hay Ctrl pulsado
        flags = QItemSelectionModel.SelectionFlag
        self.setCurrentItem(self.rows[iid], 0, flags.ClearAndSelect | flags.Rows)

    def see(self, iid):
        self.scrollToItem(self.rows[iid])

    def index(self, iid):
        return self.indexOfTopLevelItem(self.rows[iid])

    def bbox(self, iid, column):
        """(x, y, ancho, alto) de una celda en el viewport: para que las pruebas hagan clic en ella."""
        rect, n = self.visualItemRect(self.rows[iid]), self.columns.index(column)
        return self.columnViewportPosition(n), rect.y(), self.columnWidth(n), rect.height()

    def identify_row(self, y):
        item = self.itemAt(QPoint(4, y))
        return item.data(0, IID) if item else ""

    def identify_column(self, x):
        n = self.header().logicalIndexAt(x)
        return f"#{n + 1}" if n >= 0 else ""


class Var:
    """tk.StringVar/BooleanVar/IntVar: un valor con get/set que mantiene al día los controles enlazados.
    set con un valor del tipo que no toca (ajustes.json escrito a mano) da ValueError o TypeError."""

    def __init__(self, value=""):
        self.value, self.type = value, type(value)
        self.setters = []    # (control, función que le pone el valor)
        self.listeners = []  # funciones a llamar tras cada cambio

    def get(self):
        return self.value

    def set(self, value, origin=None):
        if self.type is bool:
            if not isinstance(value, (bool, int)):
                raise TypeError(f"se esperaba sí o no: {value!r}")
            value = bool(value)
        else:
            value = self.type(value)
        if value == self.value:
            return
        self.value = value
        for widget, setter in self.setters:
            if widget is not origin:  # al control que escribe no se le devuelve: movería su cursor
                widget.blockSignals(True)
                setter(value)
                widget.blockSignals(False)
        for listener in self.listeners:
            listener()

    def entry(self, widget):
        widget.setText(str(self.value))
        self.setters.append((widget, lambda v: widget.setText(str(v))))
        widget.textChanged.connect(lambda text: self.set(text, widget))
        return widget

    def combo(self, widget):
        widget.setCurrentText(str(self.value))
        self.setters.append((widget, lambda v: widget.setCurrentText(str(v))))
        widget.currentTextChanged.connect(lambda text: self.set(text, widget))
        return widget

    def check(self, widget):
        """QCheckBox o QAction marcable."""
        widget.setChecked(self.value)
        self.setters.append((widget, widget.setChecked))
        widget.toggled.connect(lambda on: self.set(on, widget))
        return widget


def qimage(pix):
    """Pixmap de PyMuPDF -> QImage (copia: la memoria del pixmap se libera con él)."""
    if pix.alpha:
        pix = pymupdf.Pixmap(pix, 0)
    if pix.n not in (1, 3):
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    form = QImage.Format.Format_Grayscale8 if pix.n == 1 else QImage.Format.Format_RGB888
    return QImage(pix.samples, pix.width, pix.height, pix.stride, form).copy()


class Preview(QLabel):
    """Hoja de tamaño fijo con una página centrada, o en blanco con un aviso tenue."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        # La hoja no impone tamaño a la ventana: se dibuja a la medida del sitio que le dejan (App.box_size)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.image, self.message, self.sheet, self.marks = None, "", False, []
        self.size_ = (0, 0)

    def show_blank(self, size, text, colors):
        width, height = size
        pixmap = QPixmap(width, height)
        pixmap.fill(QColor(colors["sheet"]))
        painter = QPainter(pixmap)
        painter.setPen(QColor(colors["sheet_line"]))
        painter.drawRect(0, 0, width - 1, height - 1)
        painter.setPen(QColor(colors["hint"]))
        painter.drawText(pixmap.rect().adjusted(20, 0, -20, 0),
                         Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, text)
        painter.end()
        self.setPixmap(pixmap)
        self.size_ = size
        self.image, self.message, self.sheet, self.marks = None, text, True, []

    def show_image(self, size, image, marks=()):
        """image centrada; marks: [(QRectF en píxeles de la imagen, color)]. Devuelve el desplazamiento."""
        width, height = size
        pixmap = QPixmap(width, height)
        pixmap.fill(Qt.GlobalColor.transparent)
        x, y = (width - image.width()) // 2, (height - image.height()) // 2
        painter = QPainter(pixmap)
        painter.drawImage(x, y, image)
        for rect, color in marks:
            painter.setPen(QPen(QColor(color), 2))
            painter.drawRect(rect.translated(x, y))
        painter.end()
        self.setPixmap(pixmap)
        self.size_ = size
        self.image, self.message, self.sheet, self.marks = image, "", False, list(marks)
        return x, y


class Strip(QScrollArea):
    """Tira horizontal de miniaturas del PDF con un borde de color por página."""
    clicked = Signal(int)

    def __init__(self, height):
        super().__init__()
        self.height_ = height
        self.setWidgetResizable(True)
        self.setFixedHeight(height + 30)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        self.row = QHBoxLayout(inner)
        self.row.setContentsMargins(4, 4, 4, 4)
        self.row.setSpacing(6)
        self.row.addStretch(1)
        self.setWidget(inner)
        self.labels, self.colors, self.current, self.blanks = {}, {}, None, []

    def clear(self):
        for label in list(self.labels.values()) + self.blanks:
            label.deleteLater()
        self.labels, self.colors, self.current, self.blanks = {}, {}, None, []

    def placeholders(self, count, width, color):
        """Rectángulos tenues mientras no hay miniaturas: la tira no queda como un hueco."""
        if self.labels or self.blanks:
            return
        for _ in range(count):
            label = QLabel()
            label.setFixedSize(width, self.height_)
            label.setStyleSheet(f"background: {color}; border-radius: 3px;")
            self.row.insertWidget(self.row.count() - 1, label)
            self.blanks.append(label)

    def add(self, n, image, color):
        for label in self.blanks:
            label.deleteLater()
        self.blanks = []
        label = QLabel()
        label.setPixmap(QPixmap.fromImage(image))
        label.setFixedSize(image.width() + 6, image.height() + 6)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.mousePressEvent = lambda _, n=n: self.clicked.emit(n)
        label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.row.insertWidget(self.row.count() - 1, label)
        self.labels[n], self.colors[n] = label, color
        self._border(n)

    def _border(self, n):
        color = "#f08c00" if n == self.current else self.colors[n]
        self.labels[n].setStyleSheet(f"border: 3px solid {color or 'transparent'}; border-radius: 3px;")

    def mark(self, n):
        """Destaca la miniatura de la página que muestra la vista previa."""
        old, self.current = self.current, n
        for page in (old, n):
            if page in self.labels:
                self._border(page)
        if n in self.labels:
            self.ensureWidgetVisible(self.labels[n])

    def outline(self, n):
        return self.colors.get(n, "")


class Box(QWidget):
    """QWidget que avisa al cambiar de tamaño (el <Configure> de Tk)."""
    resized = Signal()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resized.emit()


BASE = re.compile(r"(\d+)\.(\d+|end)|insert|end|sel\.first|sel\.last")
STEP = re.compile(r"\s*([+-])\s*(\d+)\s*c|\s*(linestart|lineend)")


class TextBox(QPlainTextEdit):
    """tk.Text sobre QPlainTextEdit: índices "línea.columna" con sus sufijos, tags y deshacer.
    "sel" es la selección; src<k> va en el formato de los caracteres y se mueve con el texto, como un tag de
    Tk; los demás tags son ExtraSelection sobre QTextCursor: siguen al texto y no entran en deshacer."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.ranges = {}  # tag -> [QTextCursor]
        self.styles = {}  # tag -> QTextCharFormat, en orden de prioridad (el último manda)
        self.painting = None

    def _end(self):
        return self.document().characterCount() - 1  # sin el separador final de Qt

    def _pos(self, index):
        doc, end = self.document(), self._end()
        m = BASE.match(index)
        if not m:
            raise ValueError(f"índice no válido: {index!r}")
        if m.group(1):
            block = doc.findBlockByNumber(int(m.group(1)) - 1)
            if not block.isValid():
                pos = end + 1
            else:
                room = block.length() - 1
                pos = block.position() + (room if m.group(2) == "end" else min(int(m.group(2)), room))
        elif m.group(0) == "insert":
            pos = self.textCursor().position()
        elif m.group(0) == "end":
            pos = end + 1  # tras el salto final que Tk tiene siempre
        else:
            cursor = self.textCursor()
            if not cursor.hasSelection():
                raise ValueError("no hay texto seleccionado")
            pos = cursor.selectionStart() if m.group(0) == "sel.first" else cursor.selectionEnd()
        rest = index[m.end():]
        while rest.strip():
            s = STEP.match(rest)
            if not s:
                raise ValueError(f"índice no válido: {index!r}")
            if s.group(3):
                block = doc.findBlock(min(pos, end))
                pos = block.position() + (0 if s.group(3) == "linestart" else block.length() - 1)
            else:
                pos += int(s.group(2)) * (1 if s.group(1) == "+" else -1)
            pos = max(0, min(pos, end + 1))
            rest = rest[s.end():]
        return pos

    def index(self, index):
        pos = self._pos(index)
        if pos > self._end():
            return f"{self.document().blockCount() + 1}.0"
        block = self.document().findBlock(pos)
        return f"{block.blockNumber() + 1}.{pos - block.position()}"

    def get(self, first, last=None):
        text = self.toPlainText() + "\n"
        a = self._pos(first)
        b = a + 1 if last is None else self._pos(last)
        return text[a:b] if b > a else ""

    def _cursor(self, a, b=None):
        cursor = QTextCursor(self.document())
        end = self._end()
        cursor.setPosition(min(a, end))
        if b is not None:
            cursor.setPosition(min(b, end), QTextCursor.MoveMode.KeepAnchor)
        return cursor

    def insert(self, index, text, tags=()):
        cursor = self._cursor(self._pos(index))
        source = next((t for t in tags if t.startswith("src")), None)
        if source:
            look = QTextCharFormat()
            look.setProperty(SRC, source)
            cursor.insertText(text, look)
        else:  # como Tk sin tags: el texto nuevo hereda el bloque del de antes
            cursor.insertText(text)

    def delete(self, first, last=None):
        a = self._pos(first)
        b = a + 1 if last is None else self._pos(last)
        if b > a:
            self._cursor(a, b).removeSelectedText()

    def mark_set(self, mark, index):
        cursor = self.textCursor()
        cursor.setPosition(min(self._pos(index), self._end()))
        self.setTextCursor(cursor)

    def search(self, needle, start):
        text = self.toPlainText()
        pos = text.find(needle, self._pos(start))
        if pos < 0:
            pos = text.find(needle)  # Tk da la vuelta al principio
        return self.index(f"1.0+{pos}c") if pos >= 0 else ""

    @contextmanager
    def group(self):
        """Lo que se haga dentro es un solo paso de deshacer."""
        cursor = QTextCursor(self.document())
        cursor.beginEditBlock()
        try:
            yield
        finally:
            cursor.endEditBlock()

    def edit_undo(self):
        self.undo()

    def edit_reset(self):
        self.document().clearUndoRedoStacks()

    def bell(self):
        QApplication.beep()

    def tag_configure(self, tag, foreground=None, background=None, underline=False, overstrike=False,
                      weight=None, italic=False, size=None):
        look = QTextCharFormat()
        if foreground:
            look.setForeground(QColor(foreground))
        if background:
            look.setBackground(QColor(background))
        look.setFontUnderline(underline)
        look.setFontStrikeOut(overstrike)
        if weight == "bold":
            look.setFontWeight(QFont.Weight.Bold)
        if italic:
            look.setFontItalic(True)
        if size:
            look.setFontPointSize(size)
        self.styles[tag] = look
        self._repaint()

    def tag_add(self, tag, first, last=None):
        a = self._pos(first)
        b = a + 1 if last is None else self._pos(last)
        if tag == "sel":
            self.setTextCursor(self._cursor(a, b))
        elif tag.startswith("src"):
            look = QTextCharFormat()
            look.setProperty(SRC, tag)
            self._cursor(a, b).mergeCharFormat(look)
        else:
            self.ranges.setdefault(tag, []).append(self._cursor(a, b))
            self._repaint()

    def tag_remove(self, tag, first, last=None):
        if tag == "sel":
            cursor = self.textCursor()
            cursor.clearSelection()
            self.setTextCursor(cursor)
            return
        a = self._pos(first)
        b = a + 1 if last is None else self._pos(last)
        # shortcut: quita solo los tramos enteros dentro de [a, b]; app.py siempre quita de "1.0" a "end"
        self.ranges[tag] = [c for c in self.ranges.get(tag, []) if not (a <= c.selectionStart() and c.selectionEnd() <= b)]
        self._repaint()

    def _source(self, pos):
        block = self.document().findBlock(pos)
        it = block.begin()
        while not it.atEnd():
            fragment = it.fragment()
            if fragment.contains(pos):
                return fragment.charFormat().property(SRC)
            it += 1
        return None

    def tag_names(self, index):
        pos = self._pos(index)
        names = [tag for tag, cursors in self.ranges.items()
                 if any(c.selectionStart() <= pos < c.selectionEnd() for c in cursors)]
        source = self._source(pos)
        if source:
            names.append(source)
        cursor = self.textCursor()
        if cursor.hasSelection() and cursor.selectionStart() <= pos < cursor.selectionEnd():
            names.append("sel")
        return tuple(names)

    def tag_ranges(self, tag):
        if tag == "sel":
            cursor = self.textCursor()
            if not cursor.hasSelection():
                return ()
            return self.index(f"1.0+{cursor.selectionStart()}c"), self.index(f"1.0+{cursor.selectionEnd()}c")
        return tuple(self.index(f"1.0+{p}c") for c in self.ranges.get(tag, [])
                     for p in (c.selectionStart(), c.selectionEnd()))

    def _repaint(self):
        """Pinta los tags una vez al volver al bucle: highlight añade cientos seguidos."""
        if self.painting is None:
            self.painting = after(0, self._paint)

    def _paint(self):
        self.painting = None
        selections = []
        for tag, look in self.styles.items():
            for cursor in self.ranges.get(tag, []):
                selection = QTextEdit.ExtraSelection()
                selection.cursor, selection.format = cursor, look
                selections.append(selection)
        self.setExtraSelections(selections)
