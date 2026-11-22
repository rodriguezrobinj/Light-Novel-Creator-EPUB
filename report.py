"""Piezas del informe de calidad que se ven de un vistazo: la nota en un anillo, barras por tipo de aviso, una franja
con un cuadro por capítulo y el texto del aviso con lo señalado resaltado o el arreglo como un diff. Solo dibujan:
qué filtrar lo decide la ventana (App.quality)."""
import difflib
import html

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

import theme


def score_color(score, dark):
    """Verde de 85 arriba, naranja de 60 arriba, rojo por debajo: como un semáforo, y con el número dentro."""
    levels, colors = theme.level_colors(dark), theme.colors(dark)
    return colors["ok"] if score >= 85 else levels["Revisar"] if score >= 60 else levels["Grave"]


class ScoreRing(QWidget):
    """La nota sobre 100 en un anillo que se llena con ella, del color de score_color."""

    def __init__(self, dark=False):
        super().__init__()
        self.score, self.dark = 100, dark
        self.setFixedSize(84, 84)

    def set_score(self, score):
        self.score = max(0, min(100, int(score)))
        self.setToolTip(f"Nota {self.score} de 100: baja con cada aviso, más con los graves y menos con los menores, "
                        "según lo largo del libro")
        self.update()

    def paintEvent(self, event):
        colors = theme.colors(self.dark)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(6, 6, self.width() - 12, self.height() - 12)
        painter.setPen(QPen(QColor(colors["line"]), 7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawEllipse(box)
        painter.setPen(QPen(QColor(score_color(self.score, self.dark)), 7, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap))
        painter.drawArc(box, 90 * 16, -round(self.score / 100 * 360 * 16))  # desde arriba, en el sentido del reloj
        font = QFont(self.font())
        font.setPointSizeF(theme.FONT["big"] + 3)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor(colors["ink"]))
        painter.drawText(box.adjusted(0, -6, 0, -6), Qt.AlignmentFlag.AlignCenter, str(self.score))
        small = QFont(self.font())
        small.setPointSizeF(theme.FONT["body"] - 1)
        painter.setFont(small)
        painter.setPen(QColor(colors["muted"]))
        painter.drawText(box.adjusted(0, 26, 0, 0), Qt.AlignmentFlag.AlignCenter, "de 100")
        painter.end()


class KindBars(QWidget):
    """Los tipos de aviso más frecuentes, cada uno con su barra y su cuenta; un clic filtra la lista por él."""
    picked = Signal(str)  # kind; "" quita el filtro

    ROW = 20

    def __init__(self, dark=False, top=6):
        super().__init__()
        self.dark, self.top, self.rows, self.chosen = dark, top, [], ""
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_rows(self, rows, chosen=""):
        """rows: [(kind, título, cuenta, nivel)], de más a menos."""
        self.rows, self.chosen = rows[:self.top], chosen
        self.setFixedHeight(max(1, len(self.rows)) * self.ROW)
        self.update()

    def row_at(self, y):
        n = int(y // self.ROW)
        return self.rows[n] if 0 <= n < len(self.rows) else None

    def mousePressEvent(self, event):
        row = self.row_at(event.position().y())
        if row:
            self.picked.emit("" if row[0] == self.chosen else row[0])

    def mouseMoveEvent(self, event):
        row = self.row_at(event.position().y())
        self.setCursor(Qt.CursorShape.PointingHandCursor if row else Qt.CursorShape.ArrowCursor)
        if row:
            QToolTip.showText(event.globalPosition().toPoint(),
                              f"{row[1]}: {row[2]} · clic para ver solo estos" if row[0] != self.chosen
                              else "Clic para verlos todos otra vez", self)

    def paintEvent(self, event):
        if not self.rows:
            return
        colors, levels = theme.colors(self.dark), theme.level_colors(self.dark)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        most = max(count for _, _, count, _ in self.rows)
        metrics = painter.fontMetrics()
        label_w = min(self.width() // 2, 190)
        count_w = metrics.horizontalAdvance("8888") + 6
        for n, (kind, title, count, level) in enumerate(self.rows):
            y = n * self.ROW
            bold = QFont(self.font())
            bold.setWeight(QFont.Weight.DemiBold if kind == self.chosen else QFont.Weight.Normal)
            painter.setFont(bold)
            painter.setPen(QColor(colors["ink"] if kind == self.chosen or not self.chosen else colors["muted"]))
            text = painter.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, label_w - 8)
            painter.drawText(QRectF(0, y, label_w - 8, self.ROW), Qt.AlignmentFlag.AlignVCenter, text)
            room = max(10, self.width() - label_w - count_w)
            track = QRectF(label_w, y + 6, room, self.ROW - 12)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(colors["line"]))
            painter.drawRoundedRect(track, 4, 4)
            painter.setBrush(QColor(levels.get(level, colors["acc"])))
            painter.drawRoundedRect(QRectF(track.x(), track.y(), max(6, room * count / most), track.height()), 4, 4)
            painter.setPen(QColor(colors["muted"]))
            painter.drawText(QRectF(label_w + room + 6, y, count_w, self.ROW), Qt.AlignmentFlag.AlignVCenter, str(count))
        painter.end()


class ChapterStrip(QWidget):
    """Un cuadro por capítulo, más intenso cuantos más avisos por palabra tiene; un clic filtra por ese capítulo."""
    picked = Signal(int)  # número del capítulo; -1 quita el filtro

    def __init__(self, dark=False):
        super().__init__()
        self.dark, self.cells, self.chosen = dark, [], -1
        self.setMouseTracking(True)
        self.setFixedHeight(22)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_cells(self, cells, chosen=-1):
        """cells: [(título, avisos, palabras, nivel más grave o "")]."""
        self.cells, self.chosen = cells, chosen
        self.update()

    def cell_width(self):
        return self.width() / max(1, len(self.cells))

    def cell_at(self, x):
        n = int(x // self.cell_width()) if self.cells else -1
        return n if 0 <= n < len(self.cells) else -1

    def mousePressEvent(self, event):
        n = self.cell_at(event.position().x())
        if n >= 0:
            self.picked.emit(-1 if n == self.chosen else n)

    def mouseMoveEvent(self, event):
        n = self.cell_at(event.position().x())
        self.setCursor(Qt.CursorShape.PointingHandCursor if n >= 0 else Qt.CursorShape.ArrowCursor)
        if n >= 0:
            title, count, words, _ = self.cells[n]
            QToolTip.showText(event.globalPosition().toPoint(),
                              f"{title}\n{count} aviso{'' if count == 1 else 's'} · {words:,} palabras".replace(",", ".")
                              + ("\nClic para verlos todos otra vez" if n == self.chosen else "\nClic para ver solo estos"),
                              self)

    def paintEvent(self, event):
        if not self.cells:
            return
        colors, levels = theme.colors(self.dark), theme.level_colors(self.dark)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        densities = [count / max(words, 200) for _, count, words, _ in self.cells]
        top = max(densities) or 1
        width = self.cell_width()
        for n, ((title, count, words, level), density) in enumerate(zip(self.cells, densities)):
            box = QRectF(n * width + 1, 2, max(2.0, width - 2), self.height() - 4)
            color = QColor(levels.get(level) or colors["line"])
            color.setAlphaF(0.15 + 0.85 * density / top if count else 1.0)
            if not count:
                color = QColor(colors["line"])
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawRoundedRect(box, 3, 3)
            if n == self.chosen:
                painter.setPen(QPen(QColor(colors["acc"]), 2))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(box.adjusted(-1, -1, 1, 1), 3, 3)
        painter.end()


def _span_html(text, color, background, strike=False):
    style = f"color: {color}; background-color: {background};" + (" text-decoration: line-through;" if strike else "")
    return f'<span style="{style}">{html.escape(text)}</span>'


def marked_html(text, span, dark):
    """El párrafo con lo que señala el aviso (span) resaltado; sin span, entero sin marcas."""
    tags = theme.diff_tags(dark)
    if not span or not 0 <= span[0] < span[1] <= len(text):
        return html.escape(text)
    look = tags["old"]
    return (html.escape(text[:span[0]]) + _span_html(text[span[0]:span[1]], look["foreground"], look["background"])
            + html.escape(text[span[1]:]))


def diff_html(before, after, dark):
    """El arreglo como un diff en una sola línea: lo que se quita tachado en rojo, lo que se pone en verde."""
    tags = theme.diff_tags(dark)
    old, new = tags["old"], tags["new"]
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
        if op == "equal":
            out.append(html.escape(before[i1:i2]))
            continue
        if i2 > i1:
            out.append(_span_html(before[i1:i2], old["foreground"], old["background"], strike=True))
        if j2 > j1:
            out.append(_span_html(after[j1:j2], new["foreground"], new["background"]))
    return "".join(out)


def change_snippet(before, after, room=40):
    """«…contexto [quitado → puesto] contexto…» corto, para la lista de «Arreglar todos»."""
    ops = [o for o in difflib.SequenceMatcher(None, before, after, autojunk=False).get_opcodes() if o[0] != "equal"]
    if not ops:
        return before[:room * 2]
    i1, i2, j1, j2 = ops[0][1], ops[-1][2], ops[0][3], ops[-1][4]
    left = before[max(0, i1 - room):i1]
    right = before[i2:i2 + room]
    return (("…" if i1 > room else "") + left + "[" + before[i1:i2] + " → " + after[j1:j2] + "]" + right
            + ("…" if len(before) - i2 > room else ""))
