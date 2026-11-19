"""Vista previa del EPUB dibujada por Chromium (QtWebEngine): todo el HTML y el CSS del libro, como en un navegador o
en un lector como Apple Books, Kobo o Thorium. Pagina como un lector, con columnas CSS del tamaño del dispositivo.
Los archivos se sirven desde el zip del EPUB (esquema epub://), sin escribirlos en disco.

Chromium pide dos cosas antes de crear la QApplication: importar QtWebEngine y registrar el esquema. qtk.app() llama
a prepare(); si QtWebEngine no está (otro equipo, un .exe sin él), available() es False y la ventana usa MuPDF."""
import json
import mimetypes
import posixpath
import zipfile

try:
    from PySide6.QtCore import QBuffer, QByteArray, Qt, QUrl, Signal
    from PySide6.QtWebEngineCore import (QWebEnginePage, QWebEngineProfile, QWebEngineSettings, QWebEngineUrlRequestJob,
                                         QWebEngineUrlScheme, QWebEngineUrlSchemeHandler)
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWidgets import QLabel, QScrollBar
    AVAILABLE, ERROR = True, ""
except ImportError as e:  # sin PySide6-WebEngine (o sin una de sus DLL): la vista previa sigue con MuPDF
    AVAILABLE, ERROR = False, str(e)

SCHEME = b"epub"
MEDIA = {".xhtml": "application/xhtml+xml", ".html": "text/html", ".htm": "text/html", ".css": "text/css",
         ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif", ".svg": "image/svg+xml",
         ".ttf": "font/ttf", ".otf": "font/otf", ".woff": "font/woff", ".woff2": "font/woff2"}
_prepared = False


def prepare():
    """Lo que Chromium necesita antes de la QApplication: el esquema epub:// y OpenGL compartido."""
    global _prepared
    if not AVAILABLE or _prepared:
        return
    from PySide6.QtCore import QCoreApplication
    scheme = QWebEngineUrlScheme(SCHEME)
    scheme.setSyntax(QWebEngineUrlScheme.Syntax.Path)
    scheme.setFlags(QWebEngineUrlScheme.Flag.SecureScheme | QWebEngineUrlScheme.Flag.LocalScheme
                    | QWebEngineUrlScheme.Flag.LocalAccessAllowed)
    QWebEngineUrlScheme.registerScheme(scheme)
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    _prepared = True


def available():
    """¿Se puede usar Chromium? LNC_SIN_CHROMIUM=1 lo apaga (las pruebas de la vista de MuPDF, que es síncrona)."""
    import os
    return AVAILABLE and _prepared and not os.environ.get("LNC_SIN_CHROMIUM")


# El capítulo seguido, con barra de desplazamiento; una «página» es lo que cabe en la vista. Las imágenes caben en
# ella, como en los lectores, que nunca las parten
SCROLLED = """
html { margin: 0 !important; padding: 0 !important; }
body { box-sizing: border-box !important; margin: 0 !important; padding: %(pad_y)spx %(pad_x)spx !important; }
img, svg, video { -webkit-user-drag: none !important; max-width: 100%% !important;
                  max-height: calc(100vh - %(pad_y2)spx) !important; object-fit: contain; }
"""
# Chromium avisa de su desplazamiento por la consola (sin QWebChannel): la barra de Qt de fuera lo sigue
SCROLL_JS = """
(function () {
  if (window.lncScroll) return true;
  window.lncScroll = true;
  function report() {
    console.log('lnc-scroll ' + Math.round(window.scrollY) + ' ' +
                Math.round(document.documentElement.scrollHeight) + ' ' + window.innerHeight);
  }
  addEventListener('scroll', report, {passive: true});
  addEventListener('resize', report);
  try { new ResizeObserver(report).observe(document.body || document.documentElement); } catch (e) {}
  report();
  return true;
})()
"""
# Clic en un párrafo de la vista del EPUB (solo se mira): su texto, para llevar ahí la hoja y las demás vistas
CLICK_JS = """
(function () {
  if (window.lncClick) return true;
  window.lncClick = true;
  document.addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('p, h1, h2, h3, h4, h5, h6, li, blockquote, figcaption');
    if (b) console.log('lnc-click ' + b.textContent.replace(/\\s+/g, ' ').trim().slice(0, 200));
  });
  return true;
})()
"""
# El párrafo del cursor en la vista del EPUB: recuadrado como en el panel del original, y a la vista
MARK_JS = """
(function (t, color) {
  if (window.lncMarked) { window.lncMarked.style.outline = ''; window.lncMarked = null; }
  if (!t) return false;
  // por el texto de cada párrafo entero: una cursiva o una nota parten el texto en varios trozos
  var all = document.body.querySelectorAll('p, h1, h2, h3, h4, h5, h6, li, blockquote, figcaption');
  for (var i = 0; i < all.length; i++) {
    var b = all[i];
    if (b.textContent.replace(/\\s+/g, ' ').indexOf(t) < 0) continue;
    b.style.outline = '2px solid ' + color; b.style.outlineOffset = '3px';
    window.lncMarked = b;
    b.scrollIntoView({block: 'center'});
    return true;
  }
  return false;
})(%s, %s)
"""
# Modo oscuro de lector: fondo oscuro y letra clara; las ilustraciones, tal cual
DARK = """
html, body { background: #111111 !important; color: #dddddd !important; }
body *:not(img):not(svg):not(image) { color: inherit !important; background-color: transparent !important;
                                      border-color: #555555 !important; }
a { color: #8fb4ff !important; }
"""


if AVAILABLE:
    class _Zip(QWebEngineUrlSchemeHandler):
        """Sirve epub:///ruta/dentro/del/zip desde los bytes del EPUB que se muestra."""

        def __init__(self, parent):
            super().__init__(parent)
            self.files = {}

        def load(self, data):
            import io
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                self.files = {name: z.read(name) for name in z.namelist() if not name.endswith("/")}

        def requestStarted(self, job):
            from urllib.parse import unquote
            path = unquote(job.requestUrl().path()).lstrip("/")
            data = self.files.get(path)
            if data is None:
                job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
                return
            ext = posixpath.splitext(path)[1].lower()
            buffer = QBuffer(job)
            buffer.setData(QByteArray(data))
            media = MEDIA.get(ext) or mimetypes.guess_type(path)[0] or "application/octet-stream"
            job.reply(media.encode(), buffer)

    class _Page(QWebEnginePage):
        """Página que recoge los avisos de desplazamiento de SCROLL_JS."""
        scrolled = Signal(int, int, int)  # arriba, alto del documento y de la vista, en px de CSS
        clicked = Signal(str)  # texto del párrafo pulsado (CLICK_JS)

        def javaScriptConsoleMessage(self, level, message, line, source):
            if message.startswith("lnc-click "):
                self.clicked.emit(message[len("lnc-click "):])
            elif message.startswith("lnc-scroll "):
                top, height, view = (int(x) for x in message.split()[1:4])
                self.scrolled.emit(top, height, view)

    class EpubView(QWebEngineView):
        """Un archivo del EPUB como en un lector de size=(ancho, alto) en px de CSS, encogido o agrandado a lo que
        mide la vista, seguido y con barra de desplazamiento. paged(páginas) avisa al cargar (una página: lo que cabe
        en la vista); go(n) va a una, step(±1) a la de al lado de donde se esté. La barra de desplazamiento (bar) es
        de Qt y va fuera, donde la ponga quien la usa, como la del panel del original; moved() avisa al moverse."""
        paged = Signal(int)
        moved = Signal()

        def __init__(self, parent=None):
            super().__init__(parent)
            self.profile_ = QWebEngineProfile(self)  # propio y sin disco: nada del libro se queda en la caché
            self.profile_.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
            self.zip = _Zip(self)
            self.profile_.installUrlSchemeHandler(SCHEME, self.zip)
            self.setPage(_Page(self.profile_, self))
            settings = self.page().settings()
            settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
            settings.setAttribute(QWebEngineSettings.WebAttribute.ShowScrollBars, False)  # la de Qt, fuera
            self.bar = QScrollBar(Qt.Orientation.Vertical)
            self.bar.setRange(0, 0)
            self.bar.valueChanged.connect(lambda v: self.page().runJavaScript(f"window.scrollTo(0, {v})"))
            self.page().scrolled.connect(self._scrolled)
            self.scale = 1.0  # el zoom de quien la mira (− y +), sobre el del tamaño del lector
            self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
            self.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # solo se mira: el teclado sigue en la lista
            self.page().setBackgroundColor(Qt.GlobalColor.white)
            self.size_, self.dark, self.page_, self.pages, self.target = (300, 400), False, 0, 0, None
            # Lo que se ve de antes tapa la página mientras carga la nueva: sin el blanco de una web que recarga
            self.cover = QLabel(self)
            self.cover.hide()
            self.loadFinished.connect(lambda ok: self.url().scheme() == SCHEME.decode() and self._loaded(ok))
            # Una página vacía ya crea la superficie de Chromium: la ventana nace preparada para ella. Si no, Windows
            # la vuelve a crear al mostrarse la vista (parpadeaba como si se abriera dos veces)
            self.setHtml("")

        def sizeHint(self):
            """El tamaño máximo que le da la ventana (la página del lector), si lo tiene."""
            biggest = self.maximumSize()
            return biggest if biggest.height() < 10000 else super().sizeHint()

        def hold(self):
            """Deja a la vista lo que se ve ahora hasta unveil(): se llama antes de cargar otra página."""
            if self.isVisible() and self.url().scheme() == SCHEME.decode():
                self.cover.setPixmap(self.grab())
                self.cover.setGeometry(self.rect())
                self.cover.show()
                self.cover.raise_()

        def unveil(self):
            """Quita lo tapado por hold() cuando Chromium ya dibujó la página nueva."""
            from PySide6.QtCore import QTimer
            QTimer.singleShot(60, self.cover.hide)

        def show_file(self, data, path, size, dark=False, page=0, find=None):
            """Muestra path del EPUB data (bytes) como en un lector de size; page: la página (-1: la última);
            find: un texto que buscar, y se va a su página."""
            self.hold()
            self.zip.load(data)
            self.size_, self.dark, self.page_, self.target = tuple(size), dark, page, find
            self.fit()
            self.setUrl(QUrl("epub:///" + path))

        def fit(self):
            """El zoom: el ancho del lector al de la vista, por el de quien la mira."""
            self.setZoomFactor(max(0.25, min(5.0, self.width() / max(self.size_[0], 1) * self.scale)))

        def resizeEvent(self, event):
            super().resizeEvent(event)
            self.fit()

        def _scrolled(self, top, height, view):
            """Chromium se desplazó (o cambió de alto): la barra de fuera y la página de ahora."""
            self.bar.blockSignals(True)
            self.bar.setRange(0, max(0, height - view))
            self.bar.setPageStep(max(1, view))
            self.bar.setSingleStep(max(1, view // 12))
            self.bar.setValue(top)
            self.bar.blockSignals(False)
            self.pages = max(1, -(-height // max(1, view)))
            self.page_ = min(self.pages - 1, round(top / max(1, view)))
            self.moved.emit()

        def mark(self, text, color):
            """Recuadra el párrafo donde sale text y lo trae a la vista (o quita el recuadro, sin text)."""
            self.page().runJavaScript(MARK_JS % (json.dumps(text), json.dumps(color)), 0)

        def _loaded(self, ok):
            if not ok:
                self.pages = 0
                self.cover.hide()
                self.paged.emit(0)
                return
            pad_x, pad_y = round(self.size_[0] * 0.06), round(self.size_[1] * 0.04)
            css = SCROLLED % {"pad_x": pad_x, "pad_y": pad_y, "pad_y2": pad_y * 2}
            css += DARK if self.dark else ""
            self.page().runJavaScript(SCROLL_JS, 0)
            self.page().runJavaScript(CLICK_JS, 0)
            script = ("(function(){var s=document.createElement('style');s.textContent=%s;"
                      "(document.head||document.documentElement).appendChild(s);"
                      "return Math.max(1,Math.ceil(document.documentElement.scrollHeight/window.innerHeight));})()"
                      % json.dumps(css))
            self.page().runJavaScript(script, 0, self._paged)

        def _paged(self, pages):
            self.pages = int(pages or 1)
            if self.target:
                self.find_page(self.target, lambda n: (self.go(n), self.paged.emit(self.pages)))
                self.target = None
                return
            self.go(self.pages - 1 if self.page_ < 0 else self.page_)
            self.paged.emit(self.pages)

        def go(self, n):
            self.page_ = max(0, min(n, self.pages - 1))
            self.page().runJavaScript(f"document.scrollingElement.scrollTop = {self.page_} * window.innerHeight;", 0,
                                      lambda _: self.unveil())

        def step(self, delta):
            """Una pantalla más abajo (o más arriba) de donde se está, como la barra de fuera."""
            self.bar.setValue(self.bar.value() + delta * self.bar.pageStep())

        def find_page(self, text, done):
            """done(página) de la primera vez que sale text (o 0)."""
            script = ("(function(t){var w=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);var n;"
                      "while((n=w.nextNode())){var i=n.data.indexOf(t);if(i>=0){var r=document.createRange();"
                      "r.setStart(n,i);r.setEnd(n,i+Math.min(t.length,n.data.length-i));var b=r.getBoundingClientRect();"
                      "return Math.floor((b.top+document.scrollingElement.scrollTop)/window.innerHeight);}}return 0;})"
                      "(%s)" % json.dumps(text))
            self.page().runJavaScript(script, 0, lambda n: done(int(n or 0)))

        def evaluate(self, script, done):
            """JavaScript en la página; done(resultado). Para las pruebas: estilos calculados, páginas."""
            self.page().runJavaScript(script, 0, done)


if AVAILABLE:
    class WebSheet(EpubView):
        """Hoja editable de Chromium (websheet): el capítulo con el CSS del libro, sin paginar. ready() avisa al cargar;
        las órdenes de la cinta y el volcado van por JavaScript (window.lnc de websheet.SHEET_JS)."""
        ready = Signal()
        menu_at = Signal(object)  # QPoint del clic derecho

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)  # aquí sí se escribe
            self.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)  # copiar, pegar
            self.loaded, self.closing = False, False  # closing: el editor se cerró (TextEditor.on_destroy)

        def load_sheet(self, files, path, text):
            """Muestra text (la página de websheet.page) como el archivo path, con files (ruta -> bytes) a mano."""
            import websheet  # noqa: F401  (solo para que el .exe lo incluya junto a esta vista)
            self.hold()
            self.zip.files = dict(files)
            self.zip.files[path] = text.encode("utf-8")
            self.loaded = False
            self.setZoomFactor(self.scale)
            self.setUrl(QUrl("epub:///" + path))

        def fit(self):
            self.setZoomFactor(self.scale)  # sin la escala del lector: se lee a su tamaño, por el zoom del panel

        def show_note(self, text):
            """Un aviso en lugar del capítulo (lo que no se edita aquí)."""
            import html
            self.loaded = False
            self.setHtml(f"<p style='font:14px sans-serif;color:#777;margin:24px'>{html.escape(text)}</p>")

        def _loaded(self, ok):
            import websheet
            if ok:
                self.page().runJavaScript(SCROLL_JS, 0)
                self.page().runJavaScript(websheet.SHEET_JS, 0, lambda _: self._ready())

        def _ready(self):
            self.loaded = True
            self.ready.emit()
            self.unveil()

        def contextMenuEvent(self, event):
            """El clic derecho lo pone el editor (sugerencias, avisos), en español."""
            self.menu_at.emit(event.pos())

        def call(self, name, *args, done=None):
            """window.lnc.name(*args); done(resultado)."""
            import shiboken6
            script = f"window.lnc && window.lnc.{name}({', '.join(json.dumps(a) for a in args)})"
            # la respuesta puede llegar con la ventana ya cerrada: entonces no se hace nada con ella
            self.page().runJavaScript(script, 0, lambda result: done and shiboken6.isValid(self) and not self.closing
                                      and done(result))


def section_path(data):
    """Ruta en el zip de la sección de un EPUB de vista previa: la segunda del spine (la primera es la portada)."""
    import io

    import faithful
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        _, _, items, order, _ = faithful.package(z)
    paths = [items[i][0] for i in order if "html" in items[i][1]]
    return paths[1] if len(paths) > 1 else paths[0]
