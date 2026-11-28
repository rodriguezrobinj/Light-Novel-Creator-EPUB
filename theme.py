"""Aspecto Fluent claro y oscuro. Fusion dibuja todos los controles él mismo: así la ventana se ve igual en
Windows 10 y 11 (el estilo nativo de Qt en Windows 10 es el antiguo)."""
import ctypes
import re
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPalette, QPen, QTransform
from PySide6.QtWidgets import (QAbstractButton, QAbstractSpinBox, QApplication, QComboBox, QLineEdit, QProxyStyle,
                               QStyle, QWidget)

CHOICES = ("Sistema", "Claro", "Oscuro")

# Formato fijo de la interfaz (docs/ui-style-guide.md). El código nuevo usa estos valores, no números sueltos
SPACE = {"tight": 4, "gap": 6, "pad": 8, "section": 12}  # px: entre iconos y texto, entre controles, margen, entre bloques
FONT = {"body": 9, "title": 11, "big": 13}                # pt, los de QSS (cuerpo, QLabel#title, QLabel#big)
DIALOG = {"s": (560, 260), "m": (780, 440), "l": (980, 600)}  # tamaño mínimo de un cuadro: corto, mediano, de trabajo
# Un icono es un glifo de texto monocromo que toma el color del tema. Nunca un emoji: Windows 10 y 11 lo dibujan
# distinto y sale a color (Qt ignora U+FE0E). Un glifo = una acción en toda la app
ICONS = {"warn": "▲", "ok": "✓", "close": "✕", "menu": "▾", "menu_open": "▴", "prev": "◀", "next": "▶",
         "first": "◀◀", "last": "▶▶", "undo": "↶", "redo": "↷", "note": "✎", "split": "✂", "center": "≡", "left": "⇤",
         "frame": "▭", "right": "⇥", "join": "⤒", "scene": "❖", "nav": "☰", "search": "⌕", "read": "▷",
         "link": "↗", "doc": "🗎", "dot": "●", "minus": "−", "symbols": "Ω", "changes": "±", "stats": "Σ",
         "show": "◉", "image": "▣", "justify": "≣", "indent": "↦", "ruby": "ふ", "newline": "↵", "code": "</>", "clear": "⌫", "web": "◎",
         "style": "¶", "table": "▦", "inlink": "⤷", "files": "▤"}

# edge: borde de botones, campos y casillas; line: el de tarjetas y separadores. En claro, con el mismo gris
# para los dos, los controles se perdían sobre el blanco (en oscuro se veían bien)
LIGHT = {"bg": "#eef0f3", "card": "#ffffff", "base": "#ffffff", "line": "#d9dce1", "edge": "#9ea4ac",
         "ink": "#16181b", "muted": "#545a63", "acc": "#0f5fc2", "acc_hover": "#0c4fa3", "acc_press": "#093f84",
         "acc_ink": "#ffffff", "sel": "#d6e6fb", "hover": "#eef3fa", "press": "#dde7f5", "warn": "#c62828",
         "ok": "#2e7d32", "drop": "#f3f7fd", "drop_line": "#9fb7dd", "sheet": "#ffffff", "sheet_line": "#c9cdd3",
         "hint": "#80868f", "zebra": "#f4f6f9", "shine": "#f5f6f8", "close": "#c42b1c", "close_ink": "#ffffff",
         "scroll": "#c1c6ce", "scroll_hover": "#9aa1ab"}
DARK = {"bg": "#202020", "card": "#2b2b2b", "base": "#1f1f1f", "line": "#3d3d3d", "ink": "#ececec",
        "muted": "#a0a0a0", "acc": "#7099f5", "acc_hover": "#85a9f7", "acc_press": "#5b86ea", "acc_ink": "#111111",
        "sel": "#2f3d5c", "hover": "#353535", "press": "#3d3d3d", "warn": "#ff6b6b", "ok": "#7bc47f",
        "drop": "#262a33", "drop_line": "#4a5a7a", "sheet": "#1a1a1a", "sheet_line": "#3d3d3d", "hint": "#7a7a7a",
        "zebra": "#313131", "shine": "#555555", "edge": "#4a4a4a", "close": "#c42b1c", "close_ink": "#ffffff",
        "scroll": "#555555", "scroll_hover": "#757575"}

# Colores de los tipos de fila de la lista ("" = el del texto normal). Todo texto llega a 4,5:1 (WCAG AA).
# Ilustración en violeta y separador en pardo: el verde es «correcto» y el naranja «Revisar» en el resto de la app
KINDS = {False: {"chapter": "", "subtitle": "#555555", "cut": "#737373", "image": "#6a4fb3", "dropped": "#6a6a6a",
                 "separator": "#6d5c46"},
         True: {"chapter": "", "subtitle": "#b0b0b8", "cut": "#96969e", "image": "#b39ddb", "dropped": "#a2a2aa",
                "separator": "#c9b79c"}}
# Estado de cada párrafo en «Traducir»: el punto y el texto de su columna
STATES = {False: {"Pendiente": "#6b6b6b", "Traducido": "#2e7d32", "Dudoso": "#b84300", "A mano": "#1f6fd1",
                  "Aplicado": "#546e7a", "De la serie": "#6a4fb3", "Con fallos": "#c62828", "Revisado": "#00796b"},
          True: {"Pendiente": "#a0a0a0", "Traducido": "#7bc47f", "Dudoso": "#ffa94d", "A mano": "#7aa7ff",
                 "Aplicado": "#9aaab4", "De la serie": "#b39ddb", "Con fallos": "#ff6b6b", "Revisado": "#4db6ac"}}
LEVELS = {False: {"Grave": "#c62828", "Revisar": "#b84300", "Menor": "#546e7a"},
          True: {"Grave": "#ff6b6b", "Revisar": "#ffa94d", "Menor": "#9aaab4"}}
# Aspecto de las marcas en el editor de texto, en orden de prioridad (si dos fijan lo mismo, manda el último)
EDITOR = {False: {"italic": {"italic": True}, "bold": {"weight": "bold"},
                  "bolditalic": {"italic": True, "weight": "bold"},
                  "heading": {"foreground": LIGHT["ink"], "weight": "semibold", "size": 14},  # tinta del tema, no azul de enlace
                  "subheading": {"foreground": "#7a3fb0"},  # subtítulo: otro color que el capítulo
                  "mark": {"foreground": "#737373"}, "token": {"background": "#e3efe5", "foreground": "#1b5e20"},
                  "scene": {"foreground": "#6d5c46"}, "broken": {"foreground": "#c62828", "underline": True},
                  "spell": {"background": "#fde2e1", "underline": True}},
          True: {"italic": {"italic": True}, "bold": {"weight": "bold"},
                 "bolditalic": {"italic": True, "weight": "bold"},
                 "heading": {"foreground": DARK["ink"], "weight": "semibold", "size": 14},
                 "subheading": {"foreground": "#c49bff"},
                 "mark": {"foreground": "#96969e"}, "token": {"background": "#24352a", "foreground": "#8fd694"},
                 "scene": {"foreground": "#c9b79c"}, "broken": {"foreground": "#ff6b6b", "underline": True},
                 "spell": {"background": "#4a1f24", "underline": True}}}
# Vista previa del informe de calidad y comparación de versiones: lo que se quita, lo que entra y los apartados
# (en color: un tag del TextBox no cambia el grosor de la letra)
DIFF = {False: {"old": {"background": "#f8d7da", "foreground": "#8a1c1c", "overstrike": True},
                "new": {"background": "#d4edda", "foreground": "#1b5e20"}, "note": {"foreground": "#6b6b6b"},
                "head": {"foreground": "#1f6fd1"}},
        True: {"old": {"background": "#4a1f24", "foreground": "#ffb3b8", "overstrike": True},
               "new": {"background": "#1f3d27", "foreground": "#a6e3b0"}, "note": {"foreground": "#8a8a8a"},
               "head": {"foreground": "#7aa7ff"}}}

QSS = """
QWidget {{ font-family: "Segoe UI"; font-size: {size}pt; color: {ink}; }}
QMainWindow, QDialog {{ background: {bg}; }}
QToolTip {{ background: {card}; color: {ink}; border: 1px solid {line}; border-radius: 6px; padding: 5px 8px; }}
QPushButton, QToolButton {{ border: 1px solid {edge}; border-radius: 6px; padding: 4px 12px; background: {card}; }}
QPushButton:hover, QToolButton:hover {{ background: {hover}; }}
QPushButton:pressed, QToolButton:pressed {{ background: {press}; }}
QPushButton:focus[keyfocus="true"], QToolButton:focus[keyfocus="true"] {{ border-color: {acc}; }}
QPushButton:open, QToolButton:open {{ background: {card}; border-bottom-color: {card}; border-bottom-left-radius: 0;
    border-bottom-right-radius: 0; }}
QPushButton[up="true"]:open, QToolButton[up="true"]:open {{ border-bottom-color: {edge}; border-bottom-left-radius: 6px;
    border-bottom-right-radius: 6px; border-top-color: {card}; border-top-left-radius: 0; border-top-right-radius: 0; }}
QPushButton:disabled, QToolButton:disabled {{ color: {hint}; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}
QToolButton#menu {{ padding: 4px 28px 4px 12px; }}
QToolButton#menu::menu-indicator {{ image: url("{chevron}"); subcontrol-origin: padding; subcontrol-position: center right;
    width: 12px; height: 12px; right: 9px; }}
QPushButton#primary {{ background: {acc}; border-color: {acc}; color: {acc_ink}; font-weight: 600; padding: 5px 16px; }}
QPushButton#primary:hover {{ background: {acc_hover}; border-color: {acc_hover}; }}
QPushButton#primary:pressed {{ background: {acc_press}; border-color: {acc_press}; }}
QPushButton#primary:disabled {{ background: {line}; border-color: {line}; color: {hint}; }}
QPushButton#accent {{ background: {sel}; border-color: {acc}; color: {ink}; font-weight: 600; padding: 4px 12px; }}
QPushButton#accent:hover {{ background: {hover}; }}
QPushButton#accent:pressed {{ background: {press}; }}
QPushButton#accent:disabled {{ background: {card}; border-color: {line}; color: {hint}; }}
QPushButton#warn {{ color: {warn}; font-weight: 600; }}
QPushButton#ok {{ color: {ok}; }}
QPushButton#warn:disabled, QPushButton#ok:disabled {{ color: {hint}; font-weight: normal; }}
QPushButton#ok[flat="true"] {{ border: 0; background: transparent; padding: 2px 4px; }}
QPushButton#warn[flat="true"] {{ border: 0; background: transparent; padding: 2px 4px; }}
QPushButton#warn[flat="true"]:hover {{ background: {hover}; border-radius: 4px; }}
QPushButton#chip {{ border-radius: 12px; padding: 3px 12px; color: {muted}; }}
QPushButton#chip:checked {{ background: {sel}; border-color: {acc}; color: {ink}; }}
QPushButton#chip:checked:hover {{ background: {hover}; }}
QPushButton#link {{ border: 0; background: transparent; color: {acc}; text-decoration: underline; padding: 2px 4px; }}
QPushButton#link:hover {{ color: {acc_hover}; }}
QCheckBox:focus[keyfocus="true"], QRadioButton:focus[keyfocus="true"] {{ color: {acc}; }}
QLineEdit, QComboBox, QPlainTextEdit, QSpinBox, QListWidget {{ border: 1px solid {edge}; border-radius: 5px;
    background: {base}; selection-background-color: {acc}; selection-color: {acc_ink}; }}
QLineEdit, QComboBox, QSpinBox {{ padding: 3px 6px; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QPlainTextEdit:disabled {{ background: {bg}; border-color: {line};
    color: {hint}; }}
QAbstractSpinBox {{ padding: 3px 24px 3px 8px; min-width: 40px; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ subcontrol-origin: border; width: 22px; border: 0;
    background: transparent; }}
QAbstractSpinBox::up-button {{ subcontrol-position: top right; border-top-right-radius: 5px; }}
QAbstractSpinBox::down-button {{ subcontrol-position: bottom right; border-bottom-right-radius: 5px; }}
QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover {{ background: {hover}; }}
QAbstractSpinBox::up-arrow {{ image: url("{chevron_up}"); width: 10px; height: 10px; }}
QAbstractSpinBox::down-arrow {{ image: url("{chevron}"); width: 10px; height: 10px; }}
QAbstractSpinBox::up-arrow:disabled, QAbstractSpinBox::up-arrow:off,
QAbstractSpinBox::down-arrow:disabled, QAbstractSpinBox::down-arrow:off {{ image: none; }}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus, QSpinBox:focus {{ border: 1px solid {acc}; }}
QTextEdit#code, QTextEdit#code:focus {{ border: 0; border-radius: 0; background: {base}; selection-background-color: {acc};
    selection-color: {acc_ink}; font-family: "Cascadia Mono", "Consolas", monospace; }}
QComboBox:hover {{ background: {hover}; }}
QComboBox:on {{ background: {base}; border-bottom-color: {base}; border-bottom-left-radius: 0; border-bottom-right-radius: 0; }}
QComboBox {{ combobox-popup: 0; }}
QComboBox::drop-down {{ border: 0; background: transparent; width: 22px; }}
QComboBoxPrivateContainer {{ border: 0; padding: 0; margin: 0; background: {base}; }}
QComboBox[up="true"]:on {{ border-bottom-color: {edge}; border-bottom-left-radius: 5px; border-bottom-right-radius: 5px;
    border-top-color: {base}; border-top-left-radius: 0; border-top-right-radius: 0; }}
QComboBox[up="true"] QAbstractItemView {{ border-top: 1px solid {edge}; border-bottom: 0; }}
QComboBox::down-arrow {{ image: url("{chevron}"); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{ background: {base}; border: 1px solid {edge}; border-top: 0; padding: 2px; outline: 0;
    selection-background-color: {sel}; selection-color: {ink}; }}
QComboBox QAbstractItemView::item {{ min-height: 24px; padding: 0 6px; }}
QFrame#card {{ background: {card}; border: 1px solid {line}; border-radius: 8px; }}
QFrame#bar {{ background: {card}; border: 0; }}
QFrame#topBar {{ background: {card}; border: 0; border-bottom: 1px solid {line}; }}
QFrame#bottomBar {{ background: {card}; border: 0; border-top: 1px solid {line}; }}
QFrame#drop {{ background: {drop}; border: 2px dashed {drop_line}; border-radius: 12px; }}
QWidget#hit {{ background: {sel}; border-radius: 6px; }}
QTreeWidget {{ background: {card}; border: 1px solid {line}; border-radius: 8px; alternate-background-color: {card};
    selection-background-color: {sel}; selection-color: {ink}; outline: 0; }}
QTreeWidget::item {{ padding: 2px 0; }}
QTreeWidget::item:selected {{ background: {sel}; color: {ink}; }}
QTableWidget {{ background: {card}; border: 1px solid {line}; border-radius: 8px; alternate-background-color: {zebra};
    gridline-color: {card}; selection-background-color: {sel}; selection-color: {ink}; outline: 0; }}
QTableWidget::item {{ padding: 4px 6px; }}
QTableWidget::item:selected {{ background: {sel}; color: {ink}; }}
QTabWidget::pane {{ border: 0; border-top: 1px solid {line}; }}
QListWidget {{ show-decoration-selected: 1; outline: 0; padding: 4px; }}
QListWidget::item {{ padding: 2px 4px; border-radius: 4px; }}
QListWidget::item:selected {{ background: {sel}; color: {ink}; }}
QHeaderView::section {{ background: {card}; border: 0; border-bottom: 1px solid {line}; padding: 4px 6px;
    color: {muted}; }}
QTabBar::tab {{ padding: 4px 12px; border: 0; border-bottom: 2px solid transparent; color: {muted};
    background: transparent; }}
QTabBar::tab:hover {{ color: {ink}; }}
QTabBar::tab:selected {{ color: {acc}; border-bottom-color: {acc}; font-weight: 600; }}
QMenu {{ background: {card}; border: 1px solid {edge}; border-radius: 8px; padding: 4px; }}
QMenu#drop {{ border-top: 0; }}
QMenu#drop {{ border-top-left-radius: 0; border-top-right-radius: 0; }}
QMenu#drop[up="true"] {{ border-top: 1px solid {edge}; border-bottom: 0; border-top-left-radius: 8px;
    border-top-right-radius: 8px; border-bottom-left-radius: 0; border-bottom-right-radius: 0; }}
QFrame#seam {{ background: {edge}; }}
QMenu::item {{ padding: 5px 24px 5px 22px; min-height: 16px; border-radius: 4px; }}
QMenu::item:selected {{ background: {sel}; color: {ink}; }}
QMenu::separator {{ height: 1px; background: {line}; margin: 4px 8px; }}
QProgressBar {{ border: 0; background: {line}; border-radius: 3px; max-height: 6px; min-height: 6px; }}
QProgressBar::chunk {{ background: {acc}; border-radius: 3px; }}
QLabel#muted {{ color: {muted}; }}
QLabel#warn {{ color: {warn}; font-weight: 600; }}
QLabel#ok {{ color: {ok}; }}
QLabel#section {{ font-weight: 600; }}
QLabel#title {{ font-size: {title_size}pt; font-weight: 600; }}
QLabel#big {{ font-size: {big_size}pt; font-weight: 600; }}
QLabel#key {{ border: 1px solid {edge}; border-bottom-width: 2px; border-radius: 4px; background: {card};
    padding: 1px 6px; font-weight: 600; min-width: 14px; qproperty-alignment: AlignCenter; }}
QLabel#key[sign="true"] {{ font-family: "Segoe UI Symbol"; font-size: {key_sign_size}pt; padding: 0px 6px; }}
QSplitter::handle {{ background: transparent; }}
QSplitter::handle:horizontal {{ width: 6px; }}
QSplitter::handle:vertical {{ height: 6px; }}
QSplitter::handle:hover {{ background: {line}; border-radius: 3px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px 1px; }}
QScrollBar::handle:vertical {{ background: {scroll}; border-radius: 4px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: {scroll_hover}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; height: 0; border: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 1px 2px; }}
QScrollBar::handle:horizontal {{ background: {scroll}; border-radius: 4px; min-width: 28px; }}
QScrollBar::handle:horizontal:hover {{ background: {scroll_hover}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; width: 0; border: 0; }}
QScrollArea {{ border: 0; background: transparent; }}
QFrame#desk {{ background: {line}; }}
QWidget#caption {{ background: {bg}; }}
QLabel#captionTitle {{ color: {ink}; }}
QLabel#captionTitle:disabled {{ color: {muted}; }}
QPushButton#captionButton, QPushButton#captionClose {{ border: 0; border-radius: 0; background: transparent;
    color: {ink}; padding: 0; min-height: 0; }}
QPushButton#captionButton:hover {{ background: {hover}; }}
QPushButton#captionButton:pressed {{ background: {press}; }}
QPushButton#captionClose:hover, QPushButton#captionClose:pressed {{ background: {close}; color: {close_ink}; }}
QTextEdit#sheet {{ background: {sheet}; border: 1px solid {sheet_line}; selection-background-color: {acc};
    selection-color: {acc_ink}; }}
QFrame#ribbon {{ background: {card}; border-bottom: 1px solid {line}; }}
QToolButton#tool {{ border: 1px solid transparent; background: transparent; padding: 4px 8px; }}
QToolButton#tool:hover {{ background: {hover}; border-color: {line}; }}
QToolButton#tool:checked {{ background: {sel}; border-color: {acc}; }}
QToolButton#tool[drop="inline"] {{ padding-right: 22px; }}
QToolButton#tool[drop="inline"]::menu-indicator {{ image: url("{chevron}"); subcontrol-origin: padding;
    subcontrol-position: center right; width: 10px; height: 10px; right: 6px; }}
QToolButton#tool[drop="big"] {{ padding: 4px 12px 4px 8px; }}
QToolButton#tool[drop="big"]::menu-indicator {{ image: url("{chevron}"); subcontrol-origin: padding;
    subcontrol-position: bottom right; width: 10px; height: 10px; right: 2px; bottom: 5px; }}
QFrame#sep {{ background: {line}; border: 0; }}
QPushButton#edge {{ border: 0; background: transparent; border-radius: 8px; }}
QPushButton#edge:hover {{ background: {hover}; }}
"""


# Colores de acento de Opciones → Apariencia: el principal, el de encima del ratón y el de lo elegido, en claro y en
# oscuro. «Azul» es el de siempre (LIGHT y DARK). Cada uno llega a 4,5:1 con su texto (test_theme_text_is_readable)
ACCENTS = {
    "Azul": None,
    "Verde": {False: {"acc": "#1b6e30", "acc_hover": "#155a26", "acc_press": "#0f471d", "sel": "#d7eedc"},
              True: {"acc": "#6ccf85", "acc_hover": "#86d99a", "acc_press": "#56bf71", "sel": "#24432c"}},
    "Morado": {False: {"acc": "#6a3db5", "acc_hover": "#57319a", "acc_press": "#462580", "sel": "#e9e0f8"},
               True: {"acc": "#b49af0", "acc_hover": "#c4aef3", "acc_press": "#9f82eb", "sel": "#3a3058"}},
    "Naranja": {False: {"acc": "#a8430a", "acc_hover": "#8a3708", "acc_press": "#6e2b06", "sel": "#fae3d3"},
                True: {"acc": "#f5a35c", "acc_hover": "#f7b57a", "acc_press": "#f0903e", "sel": "#4a3424"}},
    "Rosa": {False: {"acc": "#ad2e72", "acc_hover": "#90265f", "acc_press": "#741d4c", "sel": "#f8dfec"},
             True: {"acc": "#f08cc0", "acc_hover": "#f3a3cd", "acc_press": "#ea72b1", "sel": "#4a2a3c"}},
}
UI_SIZES = {"9 pt": 9, "10 pt": 10, "11 pt": 11}  # letra de la ventana (Opciones → Apariencia)
_accent, _size = "Azul", 9  # los de ahora: colors() los usa en toda la app


def colors(dark, accent=None):
    """Los colores del tema, con el acento elegido (o el de ahora)."""
    base = DARK if dark else LIGHT
    chosen = ACCENTS.get(accent or _accent)
    return {**base, **chosen[bool(dark)]} if chosen else base


def kind_colors(dark):
    return KINDS[bool(dark)]


# Marcas de la hoja del editor, como Word: ondulado rojo (Grave), naranja (Revisar) y azul (Menor, como la
# gramática de Word) bajo el problema; barrita verde en el margen de los párrafos cambiados
# reading: fondo del párrafo que se lee en voz alta; comment: el de un párrafo con comentario del revisor
MARKS = {False: {"Grave": "#d32f2f", "Revisar": "#e07b00", "Menor": "#1f6fd1", "changed": "#2e9d4a",
                 "reading": "#cfe3fb", "comment": "#fff3b0"},
         True: {"Grave": "#ff6b6b", "Revisar": "#ffa94d", "Menor": "#6ea8ff", "changed": "#5cc777",
                "reading": "#1f3a5c", "comment": "#4a4318"}}
# «Mostrar cambios» como GitHub: fondo verde en lo puesto y rojo en lo quitado, y uno más claro en el párrafo
# cambiado. Con transparencia (#AARRGGBB): valen sobre la hoja clara y la oscura
for _marks in MARKS.values():
    _marks.update({"added": "#662ea043", "removed": "#66f85149", "added_line": "#1f2ea043", "removed_line": "#1ff85149"})


def mark_colors(dark):
    return MARKS[bool(dark)]


def css_color(color):
    """Un color de Qt (#AARRGGBB) en CSS, que lee #RRGGBBAA al revés."""
    c = QColor(color)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {c.alphaF():.2f})"


def state_colors(dark):
    return STATES[bool(dark)]


def level_colors(dark):
    return LEVELS[bool(dark)]


def editor_tags(dark):
    return EDITOR[bool(dark)]


# Vista de código del editor: etiquetas, atributos, valores y comentarios (las ilustraciones). 4,5:1 sobre la hoja
# Colores de la vista «Código HTML» y «CSS», los de los editores de código (Light+ y Dark+ de VS Code)
CODE = {False: {"tag": "#800000", "punct": "#800000", "attr": "#e50000", "value": "#0000ff", "entity": "#a31515",
                "comment": "#008000", "gutter": "#f5f5f5", "number": "#237893", "number_on": "#0b216f",
                "guide": "#d3d3d3", "selector": "#800000", "prop": "#001080", "css_val": "#0451a5",
                "atrule": "#af00db", "error": "#c62828"},
        True: {"tag": "#569cd6", "punct": "#808080", "attr": "#9cdcfe", "value": "#ce9178", "entity": "#d7ba7d",
               "comment": "#6a9955", "gutter": "#1e1e1e", "number": "#858585", "number_on": "#c6c6c6",
               "guide": "#404040", "selector": "#d7ba7d", "prop": "#9cdcfe", "css_val": "#ce9178",
               "atrule": "#c586c0", "error": "#ff6b6b"}}  # guide: las guías de la sangría, como VS Code


# Colores de texto del editor (cinta → Formato → Color), como los de Word: (nombre, color o None = el del libro)
TEXT_COLORS = [("Automático", None), ("Rojo", "#c00000"), ("Azul", "#1f5fbf"), ("Verde", "#2e7d32"),
               ("Gris", "#6b6b6b"), ("Morado", "#7030a0"), ("Naranja", "#c55a11")]


def code_colors(dark):
    return CODE[bool(dark)]


def diff_tags(dark):
    return DIFF[bool(dark)]


def palette(dark):
    c = colors(dark)
    pal = QPalette()
    for role, key in ((QPalette.ColorRole.Window, "bg"), (QPalette.ColorRole.WindowText, "ink"),
                      (QPalette.ColorRole.Base, "base"), (QPalette.ColorRole.AlternateBase, "card"),
                      (QPalette.ColorRole.Text, "ink"), (QPalette.ColorRole.Button, "card"),
                      (QPalette.ColorRole.ButtonText, "ink"), (QPalette.ColorRole.Highlight, "acc"),
                      (QPalette.ColorRole.HighlightedText, "acc_ink"), (QPalette.ColorRole.ToolTipBase, "card"),
                      (QPalette.ColorRole.ToolTipText, "ink"), (QPalette.ColorRole.PlaceholderText, "hint"),
                      (QPalette.ColorRole.Link, "acc"), (QPalette.ColorRole.Mid, "line")):
        pal.setColor(role, QColor(c[key]))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(c["hint"]))
    return pal


class FluentStyle(QProxyStyle):
    """Fusion con casillas, botones de opción y flechas como los de Windows 11. Con QSS solo, una casilla marcada
    era un cuadro azul lleno, sin ✓ (dibujarla en QSS pide un archivo de imagen)."""
    SIZE = 16

    def __init__(self, dark=False):
        super().__init__("Fusion")
        self.c = colors(dark)

    def pixelMetric(self, metric, option=None, widget=None):
        if metric in (QStyle.PixelMetric.PM_IndicatorWidth, QStyle.PixelMetric.PM_IndicatorHeight,
                      QStyle.PixelMetric.PM_ExclusiveIndicatorWidth, QStyle.PixelMetric.PM_ExclusiveIndicatorHeight):
            return self.SIZE
        return super().pixelMetric(metric, option, widget)

    def drawPrimitive(self, element, option, painter, widget=None):
        PE, State = QStyle.PrimitiveElement, QStyle.StateFlag
        if element not in (PE.PE_IndicatorCheckBox, PE.PE_IndicatorRadioButton, PE.PE_IndicatorArrowDown):
            return super().drawPrimitive(element, option, painter, widget)
        c, state = self.c, option.state
        enabled, on = bool(state & State.State_Enabled), bool(state & (State.State_On | State.State_NoChange))
        painter.save()
        painter.setRenderHint(painter.RenderHint.Antialiasing)
        r = QRectF(option.rect)
        if element == PE.PE_IndicatorArrowDown:  # chevron fino, como las listas de Windows 11
            painter.setPen(QPen(QColor(c["muted"] if enabled else c["hint"]), 1.4, Qt.PenStyle.SolidLine,
                                Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            x, y = r.center().x(), r.center().y()
            path = QPainterPath(QPointF(x - 4, y - 2))
            path.lineTo(x, y + 2)
            path.lineTo(x + 4, y - 2)
            painter.drawPath(path)
            painter.restore()
            return
        side = min(r.width(), r.height()) - 1
        box = QRectF(r.x() + (r.width() - side) / 2, r.y() + (r.height() - side) / 2, side, side)
        fill = QColor(c["acc"] if on and enabled else c["line"] if on else c["base"])
        edge = QColor(c["acc"] if on and enabled else c["ink"] if state & State.State_MouseOver else c["edge"])
        painter.setPen(Qt.PenStyle.NoPen if on else QPen(edge, 1))
        painter.setBrush(fill)
        mark = QColor(c["acc_ink"] if enabled else c["hint"])
        if element == PE.PE_IndicatorRadioButton:
            painter.drawEllipse(box.adjusted(0.5, 0.5, -0.5, -0.5))
            if on:
                painter.setBrush(mark)
                painter.drawEllipse(box.center(), side * 0.2, side * 0.2)
        else:
            painter.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5), 3.5, 3.5)
            if on:
                painter.setPen(QPen(mark, 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                                    Qt.PenJoinStyle.RoundJoin))
                if state & State.State_NoChange:  # a medias: una raya
                    painter.drawLine(QPointF(box.left() + side * 0.28, box.center().y()),
                                     QPointF(box.right() - side * 0.28, box.center().y()))
                else:
                    path = QPainterPath(QPointF(box.left() + side * 0.26, box.top() + side * 0.53))
                    path.lineTo(box.left() + side * 0.43, box.top() + side * 0.70)
                    path.lineTo(box.left() + side * 0.75, box.top() + side * 0.33)
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawPath(path)
        painter.restore()


_style = None  # la aplicación no se queda con el estilo de Python: sin esta referencia se borraría


FOCUS_RING = (QAbstractButton,)  # botones y casillas: su foco solo se ve si se llegó con Tab
_SHORTCUT_TAIL = re.compile(r"\s*\([^()]*\)\s*$")  # atajo al final de una ayuda emergente: «Cerrar (Esc)» -> «Cerrar»


class _Shown(QObject):
    """Cada ventana que se abre lleva la barra de título del tema: al arrancar la app salía la de Windows. Una
    lista desplegable que no cabe debajo de su campo se abre encima: el campo se une a ella por arriba (up).
    También da nombre accesible desde la ayuda emergente a los botones sin letras (glifo o icono)."""

    keyboard = False  # lo último fue Tab o Mayús+Tab (no un clic): solo entonces se marca el foco

    def eventFilter(self, obj, event):
        kind = event.type()  # una vez: mira cada evento de la app, y cada type() crea un enum de Python
        # Borde azul del foco en botones y casillas solo al llegar con el teclado: al abrir una ventana o tras un
        # clic, «Editar texto…», «Herramientas» u «Opciones…» salían recuadrados sin que nadie los eligiera
        if kind == QEvent.Type.KeyPress:
            _Shown.keyboard = event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab)
        elif kind == QEvent.Type.MouseButtonPress:
            _Shown.keyboard = False
        elif kind == QEvent.Type.FocusIn and isinstance(obj, FOCUS_RING):
            ring = _Shown.keyboard and event.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason)
            if obj.property("keyfocus") != ring:
                obj.setProperty("keyfocus", ring)
                obj.style().unpolish(obj)  # el QSS mira la propiedad al repulir
                obj.style().polish(obj)
        # Una lista sin campo de texto no toma el foco con el ratón: al cerrarla le quedaba el borde azul del foco
        if (kind == QEvent.Type.Polish and isinstance(obj, QComboBox) and not obj.isEditable()
                and obj.focusPolicy() in (Qt.FocusPolicy.StrongFocus, Qt.FocusPolicy.WheelFocus)):
            obj.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        if kind == QEvent.Type.Polish and isinstance(obj, QAbstractSpinBox):  # el número pegado al borde: el QSS
            obj.findChild(QLineEdit).setTextMargins(4, 0, 0, 0)               # no mueve el campo de dentro
        if kind != QEvent.Type.Show or not isinstance(obj, QWidget):
            return False
        if isinstance(obj, QAbstractButton):
            # Botones de solo glifo o icono: el lector de pantalla lee su acción y no el nombre Unicode del símbolo
            if not obj.accessibleName():
                tip = obj.toolTip().strip()
                if tip and not any(ch.isalpha() for ch in obj.text().replace("&", "")):
                    obj.setAccessibleName(_SHORTCUT_TAIL.sub("", tip).strip() or tip)
        elif obj.windowType() in (Qt.WindowType.Window, Qt.WindowType.Dialog):
            title_bar(obj, bool(QApplication.instance().property("theme_dark")))
        elif obj.metaObject().className() == "QComboBoxPrivateContainer" and obj.parentWidget():
            combo = obj.parentWidget()
            up = obj.geometry().top() < combo.mapToGlobal(combo.rect().topLeft()).y()
            if combo.property("up") != up:
                combo.setProperty("up", up)
                for widget in (combo, combo.view()):  # el QSS mira la propiedad al repulir
                    widget.style().unpolish(widget)
                    widget.style().polish(widget)
        return False


_shown = None


def chevron(color, up=False):
    """PNG de la flecha ▾ de las listas (▴ con up, la de subir de las casillas de números), del color dado: el QSS
    solo la pone con un archivo de imagen."""
    path = Path(tempfile.gettempdir()) / "LightNovelCreator" / f"chevron-{'up-' if up else ''}{color.lstrip('#')}.png"
    if not path.exists():
        image = QImage(24, 24, QImage.Format.Format_ARGB32)  # al doble: nítida con zoom de Windows
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(color), 2.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                            Qt.PenJoinStyle.RoundJoin))
        path_ = QPainterPath(QPointF(5, 9))
        path_.lineTo(12, 16)
        path_.lineTo(19, 9)
        painter.drawPath(path_ if not up else QTransform(1, 0, 0, -1, 0, 25).map(path_))  # la misma, del revés
        painter.end()
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(str(path))
    return path.as_posix()


def apply(app, choice, accent=None, size=None):
    """Pone el tema elegido (CHOICES; otro valor, como un ajustes.json escrito a mano, es «Sistema»), con su color de
    acento (ACCENTS) y el tamaño de letra de la ventana (puntos). Devuelve True si queda oscuro."""
    global _accent, _size
    _accent = accent if accent in ACCENTS else _accent if accent is None else "Azul"
    _size = size if size in UI_SIZES.values() else _size if size is None else 9
    hints = app.styleHints()
    scheme = {"Claro": Qt.ColorScheme.Light, "Oscuro": Qt.ColorScheme.Dark}.get(choice, Qt.ColorScheme.Unknown)
    hints.setColorScheme(scheme)  # también la barra de título: Windows la pinta oscura
    # Lo elegido manda aunque la plataforma no acepte el cambio (sin pantalla, en las pruebas, no lo acepta)
    dark = scheme == Qt.ColorScheme.Dark or (scheme == Qt.ColorScheme.Unknown
                                             and hints.colorScheme() == Qt.ColorScheme.Dark)
    look = (dark, _accent, _size)
    if app.property("theme_look") == look:  # rehacer el QSS repinta todas las ventanas: solo si cambia
        return dark
    app.setProperty("theme_look", look)
    app.setProperty("theme_dark", dark)
    global _style
    if _style is None:  # uno solo: cambiar de estilo borra el de antes, que Python aún tendría
        _style = FluentStyle(dark)
        app.setStyle(_style)
        global _shown
        _shown = _Shown()
        app.installEventFilter(_shown)
    _style.c = colors(dark)  # el QSS de abajo repinta todo con los colores nuevos
    app.setFont(QFont("Segoe UI", _size))
    app.setPalette(palette(dark))
    app.setStyleSheet(QSS.format(**colors(dark), chevron=chevron(colors(dark)["muted"]),
                                 chevron_up=chevron(colors(dark)["muted"], up=True), size=_size,
                                 title_size=_size + 2, big_size=_size + 4, key_sign_size=_size + 4))
    for effect in (Qt.UIEffect.UI_AnimateCombo, Qt.UIEffect.UI_AnimateMenu, Qt.UIEffect.UI_FadeMenu):
        QApplication.setEffectEnabled(effect, False)  # las listas y menús se abren de golpe, sin deslizarse
    for window in app.topLevelWidgets():
        if window.isVisible():
            title_bar(window, dark)
    return dark


def title_bar(window, dark):
    """Barra de título clara u oscura al momento: Windows no la repintaba hasta que la ventana perdía el foco."""
    if sys.platform != "win32" or QApplication.platformName() != "windows":
        return
    hwnd, value = int(window.winId()), ctypes.c_int(1 if dark else 0)
    ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))  # modo oscuro
    active = window.isActiveWindow()
    ctypes.windll.user32.SendMessageW(hwnd, 0x0086, int(not active), 0)  # WM_NCACTIVATE: repinta la barra
    ctypes.windll.user32.SendMessageW(hwnd, 0x0086, int(active), 0)


def follow(app, callback):
    """callback() cuando Windows cambia entre modo claro y oscuro."""
    app.styleHints().colorSchemeChanged.connect(lambda _: callback())
