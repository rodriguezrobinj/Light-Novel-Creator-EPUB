"""Adaptadores Qt con la API de Tk que usa app.py: la lógica de la ventana se portó a Qt sin reescribirla."""
import bisect
import os
import re
from contextlib import contextmanager

import pymupdf
from PySide6.QtCore import (QEasingCurve, QEvent, QItemSelectionModel, QObject, QPoint, QPointF, QRectF, Qt, QTimer,
                            QVariantAnimation, Signal)
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QImage, QLinearGradient, QPainter, QPainterPath, QPalette,
                           QPen, QPixmap, QPolygonF, QTextCharFormat, QTextCursor, QTextFormat)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
                               QPushButton,
                               QMessageBox, QPlainTextEdit, QScrollArea, QSizePolicy, QStyle, QStyledItemDelegate,
                               QStyleOptionViewItem, QTabBar, QTextEdit, QToolTip, QTreeWidget, QTreeWidgetItem,
                               QWidget)

import registro

IID = Qt.ItemDataRole.UserRole       # fila -> su iid (el índice del bloque, como texto)
TAGS = Qt.ItemDataRole.UserRole + 1  # fila -> sus tags de aspecto
SRC = QTextFormat.Property.UserProperty + 1  # carácter -> tag src<k> (bloque del que viene)


def u16(text, i):
    """Posición de Qt del carácter i (de Python) de text: Qt cuenta en UTF-16 y un emoji ocupa dos."""
    return len(text[:i].encode("utf-16-le")) // 2


def app():
    """La QApplication única: la crea la primera ventana o la primera prueba."""
    if QApplication.instance():
        return QApplication.instance()
    import webview
    webview.prepare()  # Chromium (la vista previa del EPUB) pide su esquema y OpenGL compartido antes que nada
    return QApplication([])


class ActionLog(QObject):
    """Apunta en el registro (registro.note_action) lo que hace quien usa la app: el botón, la opción del menú, la
    pestaña o la fila que pulsa, los atajos de teclado y las ventanas que se abren. Lo que escribe no: solo las
    teclas con Ctrl o Alt y las F. Así un fallo o una congelación dicen tras qué pasaron. Un atajo de menú no llega
    como KeyPress (Qt lo resuelve antes, en ShortcutOverride) y lo que el widget no acepta sube a sus padres: la misma
    acción seguida se apunta una vez."""
    KINDS = (QEvent.Type.MouseButtonRelease, QEvent.Type.KeyPress, QEvent.Type.ShortcutOverride, QEvent.Type.Show)
    last = ("", 0.0)

    def eventFilter(self, obj, event):
        kind = event.type()
        if kind not in self.KINDS or not isinstance(obj, QWidget):  # lo demás (mover el ratón, pintar), sin coste
            return False
        try:
            text = self.describe(obj, event, kind)
        except Exception:  # apuntar nunca rompe el clic
            text = None
        if text:
            import time
            now = time.monotonic()
            if text != self.last[0] or now - self.last[1] > 0.5:
                registro.note_action(text)
            self.last = (text, now)
        return False

    @staticmethod
    def describe(obj, event, kind):
        if kind == QEvent.Type.Show:
            return f"Abre «{obj.windowTitle()}»" if obj.isWindow() and obj.windowTitle() else None
        window = obj.window().windowTitle()
        where = f" ({window})" if window else ""
        if kind in (QEvent.Type.KeyPress, QEvent.Type.ShortcutOverride):
            mods = event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)
            key = event.key()
            if event.isAutoRepeat() or not (mods or Qt.Key.Key_F1 <= key <= Qt.Key.Key_F12 or key == Qt.Key.Key_Escape):
                return None
            from PySide6.QtGui import QKeySequence
            return f"Tecla {QKeySequence(event.keyCombination()).toString()}{where}"
        if event.button() != Qt.MouseButton.LeftButton and event.button() != Qt.MouseButton.RightButton:
            return None
        side = " (derecho)" if event.button() == Qt.MouseButton.RightButton else ""
        from PySide6.QtWidgets import QAbstractButton, QMenu
        if isinstance(obj, QMenu):
            action = obj.actionAt(event.position().toPoint())
            return f"Menú «{action.text().replace('&', '')}»{where}" if action and action.text() else None
        if isinstance(obj, QAbstractButton):
            name = obj.text().replace("&", "") or obj.toolTip().split("\n")[0] or obj.objectName()
            state = (" → no" if obj.isChecked() else " → sí") if obj.isCheckable() else ""  # cambia tras soltar
            return f"Clic{side} «{name}»{state}{where}"
        if isinstance(obj, QTabBar):
            tab = obj.tabAt(event.position().toPoint())
            return f"Pestaña «{obj.tabText(tab)}»{where}" if tab >= 0 else None
        view = obj.parent() if obj.objectName() == "qt_scrollarea_viewport" else None
        if isinstance(view, QAbstractItemView):
            index = view.indexAt(event.position().toPoint())
            return f"Clic{side} en la fila {index.row()} de {type(view).__name__}{where}" if index.isValid() else None
        return None


def log_actions():
    """Instala ActionLog en toda la app (una vez)."""
    if getattr(log_actions, "done", None) is None:
        log_actions.done = ActionLog()
        app().installEventFilter(log_actions.done)


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
    """tkinter.filedialog sobre QFileDialog: "" o () al cancelar, como Tk. El diálogo de Windows no deja latir a la
    ventana: registro.waiting() para que no salga en el registro como congelada."""

    @staticmethod
    def askopenfilename(filetypes=(), initialdir=None, title="Abrir", parent=None):
        with registro.waiting():
            return QFileDialog.getOpenFileName(parent or QApplication.activeWindow(), title, initialdir or "",
                                               filters(filetypes))[0]

    @staticmethod
    def askopenfilenames(filetypes=(), initialdir=None, title="Abrir", parent=None):
        with registro.waiting():
            return tuple(QFileDialog.getOpenFileNames(parent or QApplication.activeWindow(), title, initialdir or "",
                                                      filters(filetypes))[0])

    @staticmethod
    def asksaveasfilename(defaultextension="", filetypes=(), initialdir=None, initialfile="", title="Guardar como",
                          parent=None):
        start = os.path.join(initialdir or "", initialfile or "")
        with registro.waiting():
            path = QFileDialog.getSaveFileName(parent or QApplication.activeWindow(), title, start,
                                               filters(filetypes))[0]
        if path and defaultextension and not os.path.splitext(path)[1]:
            path += defaultextension
        return path

    @staticmethod
    def askdirectory(initialdir=None, title="Elegir carpeta", parent=None):
        with registro.waiting():
            return QFileDialog.getExistingDirectory(parent or QApplication.activeWindow(), title, initialdir or "")


messagebox, filedialog = _MessageBox(), _FileDialog()


class _Branches(QStyledItemDelegate):
    """Celda con un margen a la izquierda donde se dibuja la flecha que une la fila con su capítulo:
    línea vertical que baja del capítulo y ▸ hacia cada fila suya, como las ramas de un árbol."""
    PAD = 18

    def paint(self, painter, option, index):
        tree = self.parent()
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        style = opt.widget.style()
        style.drawPrimitive(QStyle.PrimitiveElement.PE_PanelItemViewItem, opt, painter, opt.widget)
        opt.rect = option.rect.adjusted(self.PAD, 0, 0, 0)
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        shape = tree.branches.get(tree.itemFromIndex(index).data(0, IID))
        if not shape:
            return
        r = option.rect
        x, y = r.left() + 8, r.center().y()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = opt.palette.color(QPalette.ColorRole.Text)
        color.setAlpha(150)
        painter.setPen(QPen(color, 1.2))
        if shape == "root":  # el origen: un punto en el capítulo y la línea que baja
            painter.setBrush(color)
            painter.drawEllipse(QPointF(x, y), 2.5, 2.5)
            painter.drawLine(x, y, x, r.bottom() + 1)
        else:
            painter.drawLine(x, r.top(), x, r.bottom() + 1 if shape == "mid" else y)
            painter.drawLine(x, y, x + 6, y)
            painter.setBrush(color)
            painter.drawPolygon(QPolygonF([QPointF(x + 6, y - 3.5), QPointF(x + 11, y), QPointF(x + 6, y + 3.5)]))
        painter.restore()


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
        # Filas fijas arriba (la portada): se ven y se eligen, pero no están en get_children, selection ni
        # identify_row, así la lógica de la ventana, que va por bloques del libro, no las ve
        self.pinned = set()
        self.looks = {}  # tag -> (fuente, color)
        self.branches = {}  # iid -> forma de su flecha de pertenencia (link)
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
        # Arrastre de una fila (la ventana pone drag_iid al pulsar en su título): sin guía no se veía hacia
        # dónde iba. Una línea marca dónde caerá, delante de drop_iid, y se desliza de una fila a otra
        self.drag_iid = self.drop_iid = None
        self.press_at = self.drag_pos = None
        self.slide = QVariantAnimation(self, duration=120, easingCurve=QEasingCurve.Type.OutCubic)
        self.slide.valueChanged.connect(lambda _: self.viewport().update())

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
            self.press_at = event.position().toPoint()
            self.pressed.emit(self.Event(self.press_at))

    def mouseDoubleClickEvent(self, event):
        super().mouseDoubleClickEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit(self.Event(event.position().toPoint()))

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        if not (self.drag_iid in self.rows and event.buttons() & Qt.MouseButton.LeftButton):
            super().mouseMoveEvent(event)
            return
        if self.drop_iid is None and (pos - (self.press_at or pos)).manhattanLength() < QApplication.startDragDistance():
            return  # un clic que tiembla no es un arrastre
        self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
        self.drag_pos = pos
        self.viewport().update()  # la tarjeta sigue al ratón aunque la raya no cambie de fila
        bar, row = self.verticalScrollBar(), self.visualItemRect(self.rows[self.drag_iid]).height() or 20
        if pos.y() < row:  # cerca del borde: la lista se desplaza para llegar a las filas que no se ven
            bar.setValue(bar.value() - 1)
        elif pos.y() > self.viewport().height() - row:
            bar.setValue(bar.value() + 1)
        target = self.identify_row(pos.y())
        self.drop_to(target if target != self.drag_iid else None)
        # sin super(): la selección no sigue al ratón mientras se arrastra

    def drop_to(self, iid):
        """La guía pasa a la fila iid (None: en su sitio, sin guía), deslizándose desde donde estaba."""
        if iid == self.drop_iid:
            return
        y = float(self.visualItemRect(self.rows[iid]).top()) if iid else None
        if iid and self.drop_iid is not None:
            self.slide.stop()
            self.slide.setStartValue(self.slide.currentValue() if self.slide.currentValue() is not None else y)
            self.slide.setEndValue(y)
            self.slide.start()
        else:
            self.slide.stop()
            self.slide.setStartValue(y)
            self.slide.setEndValue(y)
        self.drop_iid = iid
        self.viewport().update()

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self.released.emit(self.Event(event.position().toPoint()))
            self.drag_iid = self.drop_iid = self.press_at = self.drag_pos = None
            self.slide.stop()
            self.viewport().unsetCursor()
            self.viewport().update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not (self.drop_iid in self.rows and self.drag_iid in self.rows):
            return
        # Como las listas de Windows 11: la fila se levanta (queda tenue en su sitio) y una tarjeta con su título
        # sigue al ratón; una raya fina del acento marca dónde caerá
        pal = QApplication.palette()
        accent, card = pal.color(QPalette.ColorRole.Link), pal.color(QPalette.ColorRole.AlternateBase)
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        source = self.visualItemRect(self.rows[self.drag_iid])
        veil = QColor(card)
        veil.setAlpha(170)
        painter.fillRect(source, veil)
        running = self.slide.state() == QVariantAnimation.State.Running
        y = self.slide.currentValue() if running else self.visualItemRect(self.rows[self.drop_iid]).top()
        width = self.viewport().width()
        painter.setPen(QPen(accent, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(QPointF(8, y), QPointF(width - 8, y))
        item = self.rows[self.drag_iid]
        title_col = self.columns.index(self.stretch)
        metrics = QFontMetrics(item.font(title_col))
        title = metrics.elidedText(item.text(title_col), Qt.TextElideMode.ElideRight, max(80, width // 2))
        pos = self.drag_pos or QPoint(0, int(y))
        box = QRectF(0, 0, metrics.horizontalAdvance(title) + 24, source.height() + 8)
        box.moveTo(min(pos.x() + 14, width - box.width() - 4), pos.y() + 10)  # al lado del puntero, sin taparlo
        shadow = QColor(0, 0, 0, 40)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(shadow)
        painter.drawRoundedRect(box.translated(0, 2), 6, 6)
        painter.setBrush(card)
        painter.setPen(QPen(pal.color(QPalette.ColorRole.Mid), 1))
        painter.drawRoundedRect(box, 6, 6)
        painter.setFont(item.font(title_col))
        painter.setPen(item.foreground(title_col).color() if item.foreground(title_col).style() != Qt.BrushStyle.NoBrush
                       else pal.color(QPalette.ColorRole.Text))
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, title)
        painter.end()

    def insert(self, parent, index, iid, values=(), tags=()):
        """Fila nueva; con parent, hija de esa fila (como Treeview: el informe agrupa sus avisos por tipo)."""
        item = QTreeWidgetItem([str(v) for v in values])
        item.setData(0, IID, iid)
        for n, col in enumerate(self.columns):
            if col != self.stretch:
                item.setTextAlignment(n, Qt.AlignmentFlag.AlignCenter)
        if parent:
            self.setRootIsDecorated(True)  # la flecha para plegar el grupo
            if index == "end":
                self.rows[parent].addChild(item)
            else:
                self.rows[parent].insertChild(index, item)
        elif index == "end":
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
        if len(iids) == len(self.rows) - len(self.pinned) or (iids and set(iids) == set(self.get_children())):
            self.rows.clear()  # vaciar la lista: de golpe, no fila a fila, y las fijas (y las hijas)
            self.pinned.clear()
            self.clear()
        else:
            for iid in iids:
                item = self.rows.pop(iid)
                for n in range(item.childCount()):  # sus hijas se van con ella
                    self.rows.pop(item.child(n).data(0, IID), None)
                if item.parent():
                    item.parent().removeChild(item)
                else:
                    self.takeTopLevelItem(self.indexOfTopLevelItem(item))
        self.blockSignals(False)
        if self.selection() != before:
            self.selected.emit()

    def get_children(self, parent=""):
        if parent:
            item = self.rows[parent]
            return tuple(item.child(n).data(0, IID) for n in range(item.childCount()))
        rows = (self.topLevelItem(n).data(0, IID) for n in range(self.topLevelItemCount()))
        return tuple(iid for iid in rows if iid not in self.pinned)

    def pin(self, iid, values=(), tags=(), index=0):
        """Fila fija al principio de la lista (ver pinned); index: su sitio entre las fijas."""
        self.insert("", index, iid, values, tags)
        self.pinned.add(iid)

    def current(self):
        """La fila elegida, también si es una fija; "" sin ninguna."""
        items = self.selectedItems()
        return items[0].data(0, IID) if items else ""

    def exists(self, iid):
        return iid in self.rows

    def set(self, iid, column, value=None):
        n = self.columns.index(column)
        if value is None:
            return self.rows[iid].text(n)
        self.rows[iid].setText(n, str(value))
        return None

    def link(self, shapes, column):
        """Flechas de pertenencia en una columna: {iid: "root" (de ella cuelgan las siguientes), "mid" o "last"
        (cuelga de la fila de arriba con "root")}. Sustituye las de antes."""
        if not isinstance(self.itemDelegateForColumn(self.columns.index(column)), _Branches):
            self.setItemDelegateForColumn(self.columns.index(column), _Branches(self))
        self.branches = shapes
        self.viewport().update()

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
        tags, old = tuple(tags), item.data(0, TAGS) or ()
        item.setData(0, TAGS, tags)
        if not any(t in self.looks for t in tags + tuple(old)):
            return  # sin aspecto antes ni ahora: poner letra y color a cada celda era la mitad de llenar «Traducir…»
        # Como Tk, las etiquetas se suman: la letra de una y el color de otra (el grupo del informe: negrita y color)
        looks = [self.looks[t] for t in tags if t in self.looks]
        font, color = next((f for f, _ in looks if f), None), next((c for _, c in looks if c), None)
        for n in range(len(self.columns)):
            item.setFont(n, font or self.font())
            item.setData(n, Qt.ItemDataRole.ForegroundRole, QColor(color) if color else None)

    def selection(self):
        return tuple(item.data(0, IID) for item in self.selectedItems() if item.data(0, IID) not in self.pinned)

    def selection_set(self, iid):
        # Como Tk, sustituye la selección: con selección múltiple, Qt sumaría la fila si hay Ctrl pulsado
        flags = QItemSelectionModel.SelectionFlag
        if self.rows[iid].parent():
            self.rows[iid].parent().setExpanded(True)  # una hija elegida se ve
        self.setCurrentItem(self.rows[iid], 0, flags.ClearAndSelect | flags.Rows)

    def see(self, iid):
        self.scrollToItem(self.rows[iid])

    def index(self, iid):
        item = self.rows[iid]
        return item.parent().indexOfChild(item) if item.parent() else self.indexOfTopLevelItem(item)

    def bbox(self, iid, column):
        """(x, y, ancho, alto) de una celda en el viewport: para que las pruebas hagan clic en ella."""
        rect, n = self.visualItemRect(self.rows[iid]), self.columns.index(column)
        return self.columnViewportPosition(n), rect.y(), self.columnWidth(n), rect.height()

    def identify_row(self, y, pinned=False):
        item = self.itemAt(QPoint(4, y))
        iid = item.data(0, IID) if item else ""
        return iid if pinned or iid not in self.pinned else ""

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

    def combo(self, widget, own=False):
        """own: un valor que no está en la lista (uno propio de «Otro…») se añade antes de «Otro…» (set_choice)."""
        put = (lambda v: set_choice(widget, str(v))) if own else (lambda v: widget.setCurrentText(str(v)))
        put(self.value)
        self.setters.append((widget, put))
        widget.currentTextChanged.connect(lambda text: self.set(text, widget))
        return widget

    def check(self, widget):
        """QCheckBox o QAction marcable."""
        widget.setChecked(self.value)
        self.setters.append((widget, widget.setChecked))
        widget.toggled.connect(lambda on: self.set(on, widget))
        return widget


def set_choice(box, text):
    """Elige text en un desplegable que no se escribe; si no está (uno propio, de «Otro…»), lo pone antes de
    «Otro…». Vacío: nada elegido (se ve su placeholderText)."""
    n = box.findText(text) if text else -1
    if n < 0 and text:
        n = box.count() - 1 if box.count() and box.itemText(box.count() - 1).endswith("…") else box.count()
        box.insertItem(n, text)
    box.setCurrentIndex(n)
    box.setProperty("chosen", text)  # al que vuelve «Otro…» si se cancela, también con las señales paradas


def fill_choices(box, items, current, other="Otro…"):
    """items y, al final, other (ask_other), con current elegido aunque no esté entre ellos."""
    box.clear()
    box.addItems([i for i in items if i] + [other])
    set_choice(box, current)


def ask_other(box, ask):
    """El último elemento del desplegable («Otro…») pide el texto propio: ask(el de antes) -> texto, o None si se
    cancela (vuelve el de antes). Un desplegable que se escribe parecía una caja de texto. Conectar antes que los
    demás activated, que así ven ya lo elegido."""
    box.setProperty("chosen", box.currentText())
    box.currentTextChanged.connect(lambda text: text != box.itemText(box.count() - 1) and box.setProperty("chosen", text))

    def picked(n):
        if n == box.count() - 1:
            before = box.property("chosen") or ""
            set_choice(box, (ask(before) or "").strip() or before)
    box.activated.connect(picked)


def qimage(pix, ink=None):
    """Pixmap de PyMuPDF -> QImage (copia: la memoria del pixmap se libera con él). Lo transparente va sobre
    blanco, como en una página: quitando sin más el canal alfa salía negro (el separador dibujado de Eminence 7
    era un cuadro negro en el editor). Con ink, la forma se pinta de ese color sobre fondo transparente: un
    adorno se ve como un símbolo del texto, no como una foto con fondo blanco."""
    if ink and not pix.alpha:  # adorno opaco, oscuro sobre blanco (las calaveras de Megami): lo oscuro es la forma
        shape = pymupdf.Pixmap(pymupdf.csGRAY, pix) if pix.n != 1 else pymupdf.Pixmap(pix, 0)
        shape.invert_irect()
        pix = pymupdf.Pixmap(pymupdf.Pixmap(pymupdf.csRGB, pix) if pix.n != 3 else pix, shape)
    if pix.alpha:
        if pix.n != 4:  # gris o CMYK con alfa: a RGB con alfa
            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        image = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGBA8888_Premultiplied)
        out = QImage(pix.width, pix.height, QImage.Format.Format_ARGB32_Premultiplied if ink
                     else QImage.Format.Format_RGB888)
        out.fill(QColor(0, 0, 0, 0) if ink else QColor("white"))
        painter = QPainter(out)
        painter.drawImage(0, 0, image)
        if ink:
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)  # la forma, del color ink
            painter.fillRect(out.rect(), QColor(ink))
        painter.end()
        return out
    if pix.n not in (1, 3):
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    form = QImage.Format.Format_Grayscale8 if pix.n == 1 else QImage.Format.Format_RGB888
    return QImage(pix.samples, pix.width, pix.height, pix.stride, form).copy()


class ArrowButton(QPushButton):
    """Botón ◀ ▶ dibujado a trazos con el color del texto del tema: el glifo de texto se veía pixelado a 9 pt.
    direction: -1 a la izquierda, 1 a la derecha; double: dos flechas (de capítulo en capítulo)."""

    def __init__(self, direction, command, tip="", double=False, size=6, subtle=False):
        super().__init__()
        self.direction, self.double, self.arrow, self.subtle = direction, double, size, subtle
        self.setToolTip(tip)
        self.setFixedWidth(36)
        self.clicked.connect(lambda: command())

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        group = QPalette.ColorGroup.Active if self.isEnabled() else QPalette.ColorGroup.Disabled
        role = QPalette.ColorRole.PlaceholderText if self.subtle and not self.underMouse() else QPalette.ColorRole.ButtonText
        painter.setPen(QPen(self.palette().color(group, role), 1.6, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        d, half = self.direction, self.arrow / 2
        x, y = self.width() / 2, self.height() / 2
        for dx in ((-half * 0.8, half * 0.8) if self.double else (0,)):
            tip = x + dx + d * half / 2
            painter.drawPolyline([QPointF(tip - d * half, y - self.arrow * 0.75), QPointF(tip, y),
                                  QPointF(tip - d * half, y + self.arrow * 0.75)])
        painter.end()


class Shimmer(QWidget):
    """Esqueleto de carga encima de un widget: barras grises con un brillo que las recorre, para que se vea que
    algo llega. bars(ancho, alto) da los rectángulos que se pintan."""

    def __init__(self, target, bars):
        super().__init__(target)
        self.bars, self.phase, self.colors = bars, 0.0, (QColor(), QColor())
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.timer = QTimer(self, interval=40)
        self.timer.timeout.connect(self.tick)
        target.installEventFilter(self)
        self.hide()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Resize:
            self.setGeometry(obj.rect())
        return False

    def start(self, base, shine):
        self.colors = QColor(base), QColor(shine)
        self.setGeometry(self.parentWidget().rect())
        self.raise_()
        self.show()
        self.timer.start()

    def stop(self):
        self.timer.stop()
        self.hide()

    def tick(self):
        self.phase = (self.phase + 0.025) % 1.6  # pasa y descansa un momento fuera de la vista
        self.update()

    def paintEvent(self, _):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        base, shine = self.colors
        x, band = (self.phase - 0.3) * self.width(), max(80, self.width() * 0.25)
        glow = QLinearGradient(x - band, 0, x + band, 0)  # fuera de la franja queda el gris de base
        glow.setColorAt(0, base)
        glow.setColorAt(0.5, shine)
        glow.setColorAt(1, base)
        path = QPainterPath()
        for rect in self.bars(self.width(), self.height()):
            path.addRoundedRect(rect, 4, 4)
        painter.fillPath(path, glow)
        painter.end()


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
            label.hide()  # deleteLater espera al bucle de eventos: mientras, se veían las del libro anterior
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
ASTRAL = re.compile("[\U00010000-\U0010ffff]")  # un carácter de Python, dos posiciones de Qt (un emoji)
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
        self.astral = None  # (índices de Python, posiciones de Qt) de los emojis del texto; None: hay que mirarlo
        self.document().contentsChanged.connect(lambda: setattr(self, "astral", None))

    def _end(self):
        return self.document().characterCount() - 1  # sin el separador final de Qt

    def _py(self, pos):
        """Posición de Qt -> índice en el texto de Python, que es como cuenta Tk: un emoji es un carácter."""
        if self.astral is None:
            found = [m.start() for m in ASTRAL.finditer(self.toPlainText())]
            self.astral = found, [i + n for n, i in enumerate(found)]
        return pos - bisect.bisect_left(self.astral[1], pos)

    def _qt(self, i):
        """Índice en el texto de Python -> posición de Qt."""
        self._py(0)
        return i + bisect.bisect_left(self.astral[0], i)

    def _span(self, first, last):
        """Posiciones de Qt de first a last; sin last, el carácter de first (dos posiciones si es un emoji)."""
        a = self._pos(first)
        return a, self._qt(self._py(a) + 1) if last is None else self._pos(last)

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
                column = room if m.group(2) == "end" else u16(block.text(), int(m.group(2)))
                pos = block.position() + min(column, room)
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
                pos = self._qt(self._py(pos) + int(s.group(2)) * (1 if s.group(1) == "+" else -1))
            pos = max(0, min(pos, end + 1))
            rest = rest[s.end():]
        return pos

    def index(self, index):
        pos = self._pos(index)
        if pos > self._end():
            return f"{self.document().blockCount() + 1}.0"
        block = self.document().findBlock(pos)
        return f"{block.blockNumber() + 1}.{self._py(pos) - self._py(block.position())}"

    def get(self, first, last=None):
        text = self.toPlainText() + "\n"
        a, b = map(self._py, self._span(first, last))
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
        a, b = self._span(first, last)
        if b > a:
            self._cursor(a, b).removeSelectedText()

    def mark_set(self, mark, index):
        cursor = self.textCursor()
        cursor.setPosition(min(self._pos(index), self._end()))
        self.setTextCursor(cursor)

    def search(self, needle, start):
        text = self.toPlainText()
        pos = text.find(needle, self._py(self._pos(start)))
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
        a, b = self._span(first, last)
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
        a, b = self._span(first, last)
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
            return tuple(self.index(f"1.0+{self._py(p)}c") for p in (cursor.selectionStart(), cursor.selectionEnd()))
        return tuple(self.index(f"1.0+{self._py(p)}c") for c in self.ranges.get(tag, [])
                     for p in (c.selectionStart(), c.selectionEnd()))

    def _repaint(self):
        """Pinta los tags una vez al volver al bucle: highlight añade cientos seguidos."""
        if self.painting is None:
            self.painting = True
            QTimer.singleShot(0, self, self._paint)  # con dueño: si la caja se cierra antes, no corre

    def _paint(self):
        self.painting = None
        selections = []
        for tag, look in self.styles.items():
            for cursor in self.ranges.get(tag, []):
                selection = QTextEdit.ExtraSelection()
                selection.cursor, selection.format = cursor, look
                selections.append(selection)
        self.setExtraSelections(selections)


# Barra de título propia (Windows). La de Windows 10 solo sabe ser negra o blanca: no casaba con el gris del tema
CAPTION = 32  # alto de la barra, en píxeles lógicos
_HT = {"caption": 2, "top": 12, "topleft": 13, "topright": 14}  # respuestas a WM_NCHITTEST


class Caption(QWidget):
    """Icono, título y minimizar, maximizar y cerrar con los colores del tema, encima de la ventana. El marco sigue
    siendo de Windows (caption_event): arrastrar, ajustar a los bordes, doble clic, el menú del sistema, la sombra
    y los bordes para cambiar el tamaño son los suyos."""

    def __init__(self, window):
        super().__init__(window)
        self.setObjectName("caption")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.window_ = window
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 0, 0, 0)
        row.setSpacing(8)
        self.icon = QLabel()
        self.title = QLabel(window.windowTitle(), objectName="captionTitle")
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)  # largo: se recorta
        row.addWidget(self.icon)
        row.addWidget(self.title, 1)
        flags = window.windowFlags()
        self.buttons = {}
        for name, glyph, tip, action, shown in (
                ("min", "", "Minimizar", window.showMinimized, flags & Qt.WindowType.WindowMinimizeButtonHint),
                ("max", "", "Maximizar", self.toggle, flags & Qt.WindowType.WindowMaximizeButtonHint),
                ("close", "", "Cerrar", window.close, True)):
            button = QPushButton(glyph, objectName="captionClose" if name == "close" else "captionButton")
            button.setFont(QFont("Segoe MDL2 Assets", 8))  # los glifos de Windows 10 y 11
            button.setFixedSize(46, CAPTION)
            button.setToolTip(tip)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(action)
            row.addWidget(button)
            # después de addWidget: sin padre aún, Windows abría cada botón un instante como ventana suelta
            button.setVisible(bool(shown))
            self.buttons[name] = button
        window.windowTitleChanged.connect(self.title.setText)
        window.windowIconChanged.connect(self.set_icon)
        self.set_icon(window.windowIcon())
        window.installEventFilter(self)
        margins = window.contentsMargins()
        window.setContentsMargins(margins.left(), margins.top() + CAPTION, margins.right(), margins.bottom())
        self.setGeometry(0, 0, window.width(), CAPTION)
        self.raise_()
        self.show()  # creada al mostrarse la ventana: un hijo nuevo de una ventana visible nace oculto

    def set_icon(self, icon):
        self.icon.setPixmap(icon.pixmap(16, 16) if not icon.isNull() else QPixmap())

    def toggle(self):
        self.window_.showNormal() if self.window_.isMaximized() else self.window_.showMaximized()

    def eventFilter(self, obj, event):
        kind = event.type()
        if kind == QEvent.Type.Resize:
            self.setGeometry(0, 0, obj.width(), CAPTION)
            self.raise_()
        elif kind == QEvent.Type.WindowStateChange:
            full = obj.isFullScreen()
            if full != self.isHidden():  # a pantalla completa, sin barra ni su sitio
                margins = obj.contentsMargins()
                obj.setContentsMargins(margins.left(), margins.top() + (-CAPTION if full else CAPTION),
                                       margins.right(), margins.bottom())
                self.setVisible(not full)
            maximized = obj.isMaximized()
            self.buttons["max"].setText("" if maximized else "")
            self.buttons["max"].setToolTip("Restaurar" if maximized else "Maximizar")
        elif kind == QEvent.Type.ActivationChange:  # inactiva, el título en gris, como Windows
            self.title.setEnabled(obj.isActiveWindow())
        return False


def caption_event(window, kind, message):
    """nativeEvent de una ventana con Caption: (atendido, resultado). Quita la barra de Windows (WM_NCCALCSIZE) sin
    quitar el marco, y dice a Windows qué es la barra propia y el borde de arriba (WM_NCHITTEST)."""
    caption = getattr(window, "caption", None)
    if kind != b"windows_generic_MSG" or caption is None:
        return False, 0
    import ctypes
    from ctypes import wintypes
    msg = wintypes.MSG.from_address(int(message))
    user32 = ctypes.windll.user32
    if msg.message == 0x0083:  # WM_NCCALCSIZE: el área de la ventana menos los bordes, sin barra
        # También sin wParam (lParam es entonces el RECT): sin atenderlo, Windows calculaba su marco con barra de
        # título y la dibujaba encima de la propia (una barra fantasma). A pantalla completa, todo es ventana
        if window.isFullScreen():
            return True, 0
        dpi = user32.GetDpiForWindow(msg.hWnd)
        pad = user32.GetSystemMetricsForDpi(92, dpi)  # SM_CXPADDEDBORDER
        side, foot = user32.GetSystemMetricsForDpi(32, dpi) + pad, user32.GetSystemMetricsForDpi(33, dpi) + pad
        rect = ctypes.cast(msg.lParam, ctypes.POINTER(wintypes.RECT)).contents  # el RECT o NCCALCSIZE_PARAMS.rgrc[0]
        rect.left += side
        rect.right -= side
        rect.bottom -= foot
        if window.isMaximized():  # maximizada, Windows la saca un borde de la pantalla también por arriba
            rect.top += foot
        return True, 0
    if msg.message == 0x0084:  # WM_NCHITTEST
        from PySide6.QtGui import QCursor
        pos = window.mapFromGlobal(QCursor.pos())
        if not window.isMaximized() and pos.y() < 5 and window.minimumSize() != window.maximumSize():
            return True, _HT["topleft" if pos.x() < 16 else "topright" if pos.x() > window.width() - 16 else "top"]
        if 0 <= pos.y() < CAPTION and not isinstance(caption.childAt(caption.mapFrom(window, pos)), QPushButton):
            return True, _HT["caption"]
    return False, 0


def use_caption(window):
    """Pone la barra propia a window (en Windows; sin pantalla, en las pruebas, sigue la del sistema)."""
    if QApplication.platformName() == "windows" and getattr(window, "caption", None) is None:
        window.caption = Caption(window)
        if window.testAttribute(Qt.WidgetAttribute.WA_WState_Created):
            # Un diálogo ya creado tiene el marco calculado con la barra de Windows: se veían las dos. FRAMECHANGED
            # hace que Windows lo vuelva a pedir (WM_NCCALCSIZE, caption_event) sin moverlo ni cambiar su tamaño
            import ctypes
            ctypes.windll.user32.SetWindowPos(int(window.winId()), 0, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020)
