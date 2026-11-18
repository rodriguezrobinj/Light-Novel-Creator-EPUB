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


# Paginación de lector: el cuerpo en columnas del ancho de la vista, una por página, y sin barra de desplazamiento.
# Las imágenes caben en una página (como en los lectores, que nunca las parten)
PAGED = """
html { height: 100vh !important; overflow: hidden !important; margin: 0 !important; padding: 0 !important; }
body { box-sizing: border-box !important; height: 100vh !important; margin: 0 !important;
       padding: %(pad_y)spx %(pad_x)spx !important;
       column-width: calc(100vw - %(gap)spx) !important; column-gap: %(gap)spx !important; column-fill: auto !important; }
img, svg, video { max-width: 100%% !important; max-height: calc(100vh - %(pad_y2)spx) !important; object-fit: contain; }
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

    class EpubView(QWebEngineView):
        """Un archivo del EPUB paginado como en un lector de size=(ancho, alto) en px de CSS, encogido o agrandado a
        lo que mide la vista. paged(páginas) avisa al cargar; go(n) va a una página."""
        paged = Signal(int)

        def __init__(self, parent=None):
            super().__init__(parent)
            self.profile_ = QWebEngineProfile(self)  # propio y sin disco: nada del libro se queda en la caché
            self.profile_.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
            self.zip = _Zip(self)
            self.profile_.installUrlSchemeHandler(SCHEME, self.zip)
            self.setPage(QWebEnginePage(self.profile_, self))
            settings = self.page().settings()
            settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
            settings.setAttribute(QWebEngineSettings.WebAttribute.ShowScrollBars, False)
            self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
            self.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # solo se mira: el teclado sigue en la lista
            self.page().setBackgroundColor(Qt.GlobalColor.white)
            self.size_, self.dark, self.page_, self.pages, self.target = (300, 400), False, 0, 0, None
            self.loadFinished.connect(self._loaded)

        def show_file(self, data, path, size, dark=False, page=0, find=None):
            """Muestra path del EPUB data (bytes) como en un lector de size; page: la página (-1: la última);
            find: un texto que buscar, y se va a su página."""
            self.zip.load(data)
            self.size_, self.dark, self.page_, self.target = tuple(size), dark, page, find
            self.setZoomFactor(max(0.25, min(5.0, self.width() / max(size[0], 1))))
            self.setUrl(QUrl("epub:///" + path))

        def resizeEvent(self, event):
            super().resizeEvent(event)
            self.setZoomFactor(max(0.25, min(5.0, self.width() / max(self.size_[0], 1))))

        def _loaded(self, ok):
            if not ok:
                self.pages = 0
                self.paged.emit(0)
                return
            pad_x, pad_y = round(self.size_[0] * 0.06), round(self.size_[1] * 0.04)
            css = PAGED % {"pad_x": pad_x, "pad_y": pad_y, "pad_y2": pad_y * 2, "gap": pad_x * 2}
            css += DARK if self.dark else ""
            script = ("(function(){var s=document.createElement('style');s.textContent=%s;"
                      "(document.head||document.documentElement).appendChild(s);"
                      "return Math.max(1,Math.round(document.documentElement.scrollWidth/window.innerWidth));})()"
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
            self.page().runJavaScript(f"document.scrollingElement.scrollLeft = {self.page_} * window.innerWidth;", 0)

        def find_page(self, text, done):
            """done(página) de la primera vez que sale text (o 0)."""
            script = ("(function(t){var w=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);var n;"
                      "while((n=w.nextNode())){var i=n.data.indexOf(t);if(i>=0){var r=document.createRange();"
                      "r.setStart(n,i);r.setEnd(n,i+Math.min(t.length,n.data.length-i));var b=r.getBoundingClientRect();"
                      "return Math.floor((b.left+document.scrollingElement.scrollLeft)/window.innerWidth);}}return 0;})"
                      "(%s)" % json.dumps(text))
            self.page().runJavaScript(script, 0, lambda n: done(int(n or 0)))

        def evaluate(self, script, done):
            """JavaScript en la página; done(resultado). Para las pruebas: estilos calculados, páginas."""
            self.page().runJavaScript(script, 0, done)


def section_path(data):
    """Ruta en el zip de la sección de un EPUB de vista previa: la segunda del spine (la primera es la portada)."""
    import io

    import faithful
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        _, _, items, order, _ = faithful.package(z)
    paths = [items[i][0] for i in order if "html" in items[i][1]]
    return paths[1] if len(paths) > 1 else paths[0]
