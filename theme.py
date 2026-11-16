"""Aspecto Fluent claro y oscuro. Fusion dibuja todos los controles él mismo: así la ventana se ve igual en
Windows 10 y 11 (el estilo nativo de Qt en Windows 10 es el antiguo)."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPalette

CHOICES = ("Sistema", "Claro", "Oscuro")

# Formato fijo de la interfaz (docs/ui-style-guide.md). El código nuevo usa estos valores, no números sueltos
SPACE = {"tight": 4, "gap": 6, "pad": 8, "section": 12}  # px: entre iconos y texto, entre controles, margen, entre bloques
FONT = {"body": 9, "title": 11, "big": 13}                # pt, los de QSS (cuerpo, QLabel#title, QLabel#big)
DIALOG = {"s": (560, 260), "m": (780, 440), "l": (980, 600)}  # tamaño mínimo de un cuadro: corto, mediano, de trabajo
# Un icono es un glifo de texto monocromo que toma el color del tema. Nunca un emoji: Windows 10 y 11 lo dibujan
# distinto y sale a color (Qt ignora U+FE0E). Un glifo = una acción en toda la app
ICONS = {"warn": "▲", "ok": "✓", "close": "✕", "menu": "▾", "menu_open": "▴", "prev": "◀", "next": "▶",
         "first": "◀◀", "last": "▶▶", "undo": "↶", "redo": "↷", "note": "✎", "split": "✂", "center": "≡",
         "frame": "▭", "right": "⇥", "join": "⤒", "scene": "❖", "nav": "☰", "search": "⌕", "read": "♪",
         "link": "↗", "doc": "🗎", "dot": "●", "minus": "−", "symbols": "Ω", "changes": "±", "stats": "Σ",
         "show": "◉", "image": "▣"}

LIGHT = {"bg": "#f3f3f3", "card": "#ffffff", "base": "#ffffff", "line": "#e1e1e1", "ink": "#1b1b1b",
         "muted": "#6b6b6b", "acc": "#2a62c8", "acc_hover": "#2353ab", "acc_ink": "#ffffff", "sel": "#dce8fc",
         "hover": "#ececec", "press": "#e2e2e2", "warn": "#c62828", "ok": "#2e7d32", "drop": "#f5f8fe",
         "drop_line": "#b9c8e8", "sheet": "#ffffff", "sheet_line": "#d4d4d4", "hint": "#9a9a9a",
         "zebra": "#f5f6f8"}
DARK = {"bg": "#202020", "card": "#2b2b2b", "base": "#1f1f1f", "line": "#3d3d3d", "ink": "#ececec",
        "muted": "#a0a0a0", "acc": "#7099f5", "acc_hover": "#85a9f7", "acc_ink": "#111111", "sel": "#2f3d5c",
        "hover": "#353535", "press": "#3d3d3d", "warn": "#ff6b6b", "ok": "#7bc47f", "drop": "#262a33",
        "drop_line": "#4a5a7a", "sheet": "#1a1a1a", "sheet_line": "#3d3d3d", "hint": "#7a7a7a",
        "zebra": "#313131"}

# Colores de los tipos de fila de la lista ("" = el del texto normal). Todo texto llega a 4,5:1 (WCAG AA)
KINDS = {False: {"chapter": "", "subtitle": "#555555", "cut": "#737373", "image": "#2e7d32", "dropped": "#6a6a6a",
                 "separator": "#7a5c00"},
         True: {"chapter": "", "subtitle": "#b0b0b8", "cut": "#96969e", "image": "#7bc47f", "dropped": "#a2a2aa",
                "separator": "#d9b54a"}}
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
                  "heading": {"foreground": "#1f6fd1", "weight": "bold", "size": 14},
                  "mark": {"foreground": "#737373"}, "token": {"background": "#e3efe5", "foreground": "#1b5e20"},
                  "scene": {"foreground": "#7a5c00"}, "broken": {"foreground": "#c62828", "underline": True},
                  "spell": {"background": "#fde2e1", "underline": True}},
          True: {"italic": {"italic": True}, "bold": {"weight": "bold"},
                 "bolditalic": {"italic": True, "weight": "bold"},
                 "heading": {"foreground": "#7aa7ff", "weight": "bold", "size": 14},
                 "mark": {"foreground": "#96969e"}, "token": {"background": "#24352a", "foreground": "#8fd694"},
                 "scene": {"foreground": "#d9b54a"}, "broken": {"foreground": "#ff6b6b", "underline": True},
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
QWidget {{ font-family: "Segoe UI"; font-size: 9pt; color: {ink}; }}
QMainWindow, QDialog {{ background: {bg}; }}
QPushButton, QToolButton {{ border: 1px solid {line}; border-radius: 6px; padding: 4px 12px; background: {card}; }}
QPushButton:hover, QToolButton:hover {{ background: {hover}; }}
QPushButton:pressed, QToolButton:pressed {{ background: {press}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {hint}; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}
QPushButton#primary {{ background: {acc}; border-color: {acc}; color: {acc_ink}; font-weight: 600; padding: 6px 18px; }}
QPushButton#primary:hover {{ background: {acc_hover}; }}
QPushButton#primary:disabled {{ background: {line}; border-color: {line}; color: {hint}; }}
QPushButton#warn {{ color: {warn}; font-weight: 600; }}
QPushButton#ok {{ color: {ok}; }}
QPushButton#chip {{ border-radius: 12px; padding: 3px 12px; color: {muted}; }}
QPushButton#chip:checked {{ background: {sel}; border-color: {acc}; color: {ink}; font-weight: 600; }}
QPushButton#link {{ border: 0; background: transparent; color: {acc}; text-decoration: underline; padding: 0; }}
QLineEdit, QComboBox, QPlainTextEdit, QSpinBox, QListWidget {{ border: 1px solid {line}; border-radius: 5px;
    background: {base}; selection-background-color: {acc}; selection-color: {acc_ink}; }}
QLineEdit, QComboBox, QSpinBox {{ padding: 3px 6px; }}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus, QSpinBox:focus {{ border: 1px solid {acc}; }}
QComboBox QAbstractItemView {{ background: {card}; border: 1px solid {line}; selection-background-color: {sel};
    selection-color: {ink}; }}
QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid {muted}; border-radius: 3px; background: {base}; }}
QCheckBox::indicator:checked {{ background: {acc}; border-color: {acc}; }}
QRadioButton::indicator {{ width: 12px; height: 12px; border: 1px solid {muted}; border-radius: 7px; background: {base}; }}
QRadioButton::indicator:checked {{ background: {acc}; border-color: {acc}; }}
QRadioButton::indicator:disabled {{ border-color: {line}; }}
QFrame#card {{ background: {card}; border: 1px solid {line}; border-radius: 8px; }}
QFrame#bar {{ background: {card}; border: 0; }}
QFrame#drop {{ background: {drop}; border: 2px dashed {drop_line}; border-radius: 12px; }}
QTreeWidget {{ background: {card}; border: 1px solid {line}; border-radius: 8px; alternate-background-color: {card};
    selection-background-color: {sel}; selection-color: {ink}; outline: 0; }}
QTreeWidget::item {{ padding: 2px 0; }}
QTreeWidget::item:selected {{ background: {sel}; color: {ink}; }}
QTableWidget {{ background: {card}; border: 1px solid {line}; border-radius: 8px; alternate-background-color: {zebra};
    gridline-color: {card}; selection-background-color: {sel}; selection-color: {ink}; outline: 0; }}
QTableWidget::item {{ padding: 4px 6px; }}
QTableWidget::item:selected {{ background: {sel}; color: {ink}; }}
QTabWidget::pane {{ border: 0; border-top: 1px solid {line}; }}
QListWidget {{ show-decoration-selected: 1; outline: 0; }}
QListWidget::item:selected {{ background: {sel}; color: {ink}; }}
QHeaderView::section {{ background: {card}; border: 0; border-bottom: 1px solid {line}; padding: 4px 6px;
    color: {muted}; }}
QTabBar::tab {{ padding: 4px 12px; border: 0; border-bottom: 2px solid transparent; color: {muted};
    background: transparent; }}
QTabBar::tab:selected {{ color: {acc}; border-bottom-color: {acc}; font-weight: 600; }}
QMenu {{ background: {card}; border: 1px solid {line}; padding: 4px; }}
QMenu::item {{ padding: 5px 24px 5px 22px; border-radius: 4px; }}
QMenu::item:selected {{ background: {sel}; color: {ink}; }}
QMenu::separator {{ height: 1px; background: {line}; margin: 4px 8px; }}
QProgressBar {{ border: 0; background: {line}; border-radius: 3px; max-height: 6px; min-height: 6px; }}
QProgressBar::chunk {{ background: {acc}; border-radius: 3px; }}
QLabel#muted {{ color: {muted}; }}
QLabel#warn {{ color: {warn}; font-weight: 600; }}
QLabel#ok {{ color: {ok}; }}
QLabel#title {{ font-size: 11pt; font-weight: 600; }}
QLabel#big {{ font-size: 13pt; font-weight: 600; }}
QSplitter::handle {{ background: transparent; }}
QScrollArea {{ border: 0; background: transparent; }}
QFrame#desk {{ background: {line}; }}
QTextEdit#sheet {{ background: {sheet}; border: 1px solid {sheet_line}; selection-background-color: {acc};
    selection-color: {acc_ink}; }}
QFrame#ribbon {{ background: {card}; border-bottom: 1px solid {line}; }}
QToolButton#tool {{ border: 1px solid transparent; background: transparent; padding: 4px 8px; }}
QToolButton#tool:hover {{ background: {hover}; border-color: {line}; }}
QToolButton#tool:checked {{ background: {sel}; border-color: {acc}; }}
QFrame#sep {{ background: {line}; }}
"""


def colors(dark):
    return DARK if dark else LIGHT


def kind_colors(dark):
    return KINDS[bool(dark)]


# Marcas de la hoja del editor, como Word: ondulado rojo (Grave), naranja (Revisar) y azul (Menor, como la
# gramática de Word) bajo el problema; barrita verde en el margen de los párrafos cambiados
# reading: fondo del párrafo que se lee en voz alta; comment: el de un párrafo con comentario del revisor
MARKS = {False: {"Grave": "#d32f2f", "Revisar": "#e07b00", "Menor": "#1f6fd1", "changed": "#2e9d4a",
                 "reading": "#cfe3fb", "comment": "#fff3b0"},
         True: {"Grave": "#ff6b6b", "Revisar": "#ffa94d", "Menor": "#6ea8ff", "changed": "#5cc777",
                "reading": "#1f3a5c", "comment": "#4a4318"}}


def mark_colors(dark):
    return MARKS[bool(dark)]


def state_colors(dark):
    return STATES[bool(dark)]


def level_colors(dark):
    return LEVELS[bool(dark)]


def editor_tags(dark):
    return EDITOR[bool(dark)]


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


def apply(app, choice):
    """Pone el tema elegido (CHOICES; otro valor, como un ajustes.json escrito a mano, es «Sistema»).
    Devuelve True si queda oscuro."""
    hints = app.styleHints()
    scheme = {"Claro": Qt.ColorScheme.Light, "Oscuro": Qt.ColorScheme.Dark}.get(choice, Qt.ColorScheme.Unknown)
    hints.setColorScheme(scheme)  # también la barra de título: Windows la pinta oscura
    # Lo elegido manda aunque la plataforma no acepte el cambio (sin pantalla, en las pruebas, no lo acepta)
    dark = scheme == Qt.ColorScheme.Dark or (scheme == Qt.ColorScheme.Unknown
                                             and hints.colorScheme() == Qt.ColorScheme.Dark)
    if app.property("theme_dark") == dark:  # rehacer el QSS repinta todas las ventanas: solo si cambia
        return dark
    app.setProperty("theme_dark", dark)
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 9))
    app.setPalette(palette(dark))
    app.setStyleSheet(QSS.format(**colors(dark)))
    return dark


def follow(app, callback):
    """callback() cuando Windows cambia entre modo claro y oscuro."""
    app.styleHints().colorSchemeChanged.connect(lambda _: callback())
