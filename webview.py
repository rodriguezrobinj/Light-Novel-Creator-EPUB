"""Vista previa del EPUB dibujada por Chromium (QtWebEngine): todo el HTML y el CSS del libro, como en un navegador o
en un lector como Apple Books, Kobo o Thorium. Pagina como un lector, con columnas CSS del tamaño del dispositivo.
Los archivos se sirven desde el zip del EPUB (esquema epub://), sin escribirlos en disco.

Chromium pide dos cosas antes de crear la QApplication: importar QtWebEngine y registrar el esquema. qtk.app() llama
a prepare(); si QtWebEngine no está (otro equipo, un .exe sin él), available() es False y la ventana usa MuPDF."""
import json
import logging
import mimetypes
import posixpath
import time
import zipfile

try:
    from PySide6.QtCore import QBuffer, QByteArray, Qt, QTimer, QUrl, Signal
    from PySide6.QtGui import QPixmap
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
RECOVER_S = 5  # segundos tras morir el proceso de Chromium antes de cargar otra página en la hoja web
LOG = logging.getLogger("chromium")  # registro.log: lo que falla dentro de Chromium


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


def _alive(view, fn):
    """La respuesta de Chromium para view, si view sigue: llega tarde, a veces con el editor ya cerrado y la vista
    borrada («Internal C++ object (EpubView) already deleted»)."""
    import shiboken6
    return lambda result: fn(result) if shiboken6.isValid(view) else None


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
# Hoja web en modo oscuro: como DARK, sobre la hoja
SHEET_DARK = """
html, body { background: #1a1a1a !important; color: #ececec !important; }
#lnc-sheet, #lnc-sheet *:not(img):not(svg):not(image) { color: #ececec !important; background-color: transparent !important;
                                                        border-color: #555555 !important; }
#lnc-sheet a { color: #8fb4ff !important; }
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
    var own = e.target.closest && e.target.closest('[data-lnc]');  // sellado (convert._stamp): su línea, sin adivinar
    if (own) {
      console.log('lnc-line ' + own.getAttribute('data-lnc'));
      return;
    }
    var pic = e.target.closest && e.target.closest('img, svg');  // una ilustración: su número, como en MARK_JS
    if (pic) {
      console.log('lnc-pick ' + Array.prototype.indexOf.call(document.body.querySelectorAll('img, svg'), pic));
      return;
    }
    var b = e.target.closest && e.target.closest('p, h1, h2, h3, h4, h5, h6, li, blockquote, figcaption');
    if (b) console.log('lnc-click ' + b.textContent.replace(/\\s+/g, ' ').trim().slice(0, 200));
  });
  return true;
})()
"""
# Notas emergentes, como en un lector: un clic en la llamada (epub:type o role «noteref») pide la nota a Python, que la
# saca del EPUB (también de otro archivo, notas.xhtml), y sale en un recuadro bajo la llamada. Los demás enlaces no
# se siguen: la vista se quedaba en otro archivo. Clic fuera o Esc la cierran
NOTE_JS = """
(function () {
  if (window.lncNotes) return true;
  window.lncNotes = true;
  function close() { var n = document.getElementById('lnc-note'); if (n) n.remove(); }
  function noteref(a) {
    var t = (a.getAttributeNS('http://www.idpf.org/2007/ops', 'type') || a.getAttribute('epub:type') || '') + ' ' +
            (a.getAttribute('role') || '');
    return /noteref/.test(t);
  }
  document.addEventListener('click', function (e) {
    var inNote = e.target.closest && e.target.closest('#lnc-note');
    var a = e.target.closest && e.target.closest('a[href]');
    if (!inNote) close();
    if (!a) return;
    e.preventDefault();
    if (inNote || !noteref(a)) return;
    window.lncNoteLink = a;
    console.log('lnc-note ' + a.getAttribute('href'));
  }, true);
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') close(); });
  window.lncShowNote = function (html, dark) {
    close();
    var a = window.lncNoteLink;
    if (!a || !html) return false;
    var box = document.createElement('div');
    box.id = 'lnc-note';
    try { box.innerHTML = html; } catch (e) { box.textContent = html.replace(/<[^>]*>/g, ''); }
    var r = a.getBoundingClientRect(), root = document.documentElement.getBoundingClientRect();
    box.style.cssText = 'position:absolute;z-index:20;box-sizing:border-box;max-width:min(92vw,32em);' +
      'padding:0.6em 0.9em;border-radius:6px;font-size:0.9em;line-height:1.4;text-indent:0;' +
      'box-shadow:0 4px 16px rgba(0,0,0,0.35);border:1px solid ' + (dark ? '#555' : '#bbb') + ';' +
      'background:' + (dark ? '#222' : '#fff') + ';color:' + (dark ? '#ddd' : '#222') + ';';
    document.documentElement.appendChild(box);  // fuera del <body>: el modo oscuro no la vuelve transparente
    var left = Math.max(8, Math.min(r.left - root.left - 20, document.documentElement.clientWidth - box.offsetWidth - 8));
    box.style.left = left + 'px';
    box.style.top = (r.bottom - root.top + 6) + 'px';
    var below = r.bottom + 6 + box.offsetHeight;
    if (below > window.innerHeight && r.top > box.offsetHeight + 12)  // sin sitio debajo: encima
      box.style.top = (r.top - root.top - box.offsetHeight - 6) + 'px';
    return true;
  };
  return true;
})()
"""
# El párrafo del cursor en la vista del EPUB: recuadrado como en el panel del original, y a la vista
# El párrafo n.º nth cuyas letras contienen t (solo las letras: la tipografía cambia comillas y rayas, y una nota o un
# <br/> parten el texto). Sin letras («—…», «※ ・ ※»), los párrafos sin letras
FIND_JS = """
window.lncFind = function (t, nth) {
  var all = document.body.querySelectorAll('p, h1, h2, h3, h4, h5, h6, li, blockquote, figcaption, div, td');
  var found = [];
  for (var i = 0; i < all.length; i++) {
    var e = all[i], letters = e.textContent.toLowerCase().replace(/[^\\p{L}]+/gu, '');
    if (t ? letters.indexOf(t) >= 0 : !letters && e.textContent.trim() && !e.querySelector('img, svg')) found.push(e);
  }
  // el de más dentro: un <div> que envuelve el capítulo también contiene el texto
  found = found.filter(function (e) { return !found.some(function (o) { return o !== e && e.contains(o); }); });
  return found[Math.min(nth, found.length - 1)] || null;
};
"""
MARK_JS = FIND_JS + """
(function (t, color, nth, img, line) {
  if (!document.body) return false;  // a medio cargar: la marca llega con la página
  if (window.lncMarked) { window.lncMarked.style.outline = ''; window.lncMarked = null; }
  var b = null;
  if (document.querySelector('[data-lnc]')) {  // sellado: el elemento de la línea, o ninguno (línea vacía, descartada)
    b = line >= 0 ? document.querySelector('[data-lnc="' + line + '"]') : null;
  } else if (img >= 0) {  // una ilustración: la n.º img del capítulo
    var pics = document.body.querySelectorAll('img, svg');
    b = pics[Math.min(img, pics.length - 1)] || null;
  } else if (t !== null) {
    b = lncFind(t, nth);
  }
  if (!b) return false;
  b.style.outline = '2px solid ' + color; b.style.outlineOffset = '3px';
  window.lncMarked = b;
  b.scrollIntoView({block: 'center'});
  return true;
})(%s, %s, %s, %s, %s)
"""
# Cada párrafo de la vista del EPUB con el n.º de su párrafo en la hoja (data-lnc-b), buscado como en FIND_JS pero de
# una vez: items [[n.º, letras, n.º de los que también las tienen, línea sellada o -1]]. Así lo escrito se pone al momento (PATCH_JS)
TAG_JS = """
(function (items) {
  if (!document.body) return false;  // a medio cargar
  var sel = 'p, h1, h2, h3, h4, h5, h6, li, blockquote, figcaption, div, td';
  var leaves = Array.prototype.filter.call(document.body.querySelectorAll(sel), function (e) { return !e.querySelector(sel); });
  var letters = leaves.map(function (e) { return e.textContent.toLowerCase().replace(/[^\\p{L}]+/gu, ''); });
  var bare = leaves.map(function (e) { return !!e.textContent.trim() && !e.querySelector('img, svg'); });
  document.querySelectorAll('[data-lnc-b]').forEach(function (e) { e.removeAttribute('data-lnc-b'); });
  items.forEach(function (it) {
    if (it[3] >= 0) {  // sellado (convert._stamp): el de su línea
      var own = document.querySelector('[data-lnc="' + it[3] + '"]');
      if (own && !own.hasAttribute('data-lnc-b')) own.setAttribute('data-lnc-b', it[0]);
      return;
    }
    var t = it[1], seen = -1, last = null;
    for (var i = 0; i < leaves.length && seen < it[2]; i++)
      if (t ? letters[i].indexOf(t) >= 0 : !letters[i] && bare[i]) { seen++; last = leaves[i]; }
    if (last && !last.hasAttribute('data-lnc-b')) last.setAttribute('data-lnc-b', it[0]);
  });
  return true;
})(%s)
"""
PATCH_JS = """
(function (n, html) {
  var e = document.querySelector('[data-lnc-b="' + n + '"]');
  if (!e) return false;
  try { e.innerHTML = html; } catch (err) { e.textContent = html.replace(/<[^>]*>/g, ''); }
  return true;
})(%s, %s)
"""
# Modo oscuro de lector: fondo oscuro y letra clara; las ilustraciones, tal cual
DARK = """
html, body { background: #1a1a1a !important; color: #ececec !important; }
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
        note = Signal(str)  # href de la llamada a nota pulsada (NOTE_JS)
        picked = Signal(int)  # número de la ilustración pulsada (CLICK_JS)
        lined = Signal(int)  # línea (data-lnc) del elemento pulsado, en una vista sellada (CLICK_JS)

        def javaScriptConsoleMessage(self, level, message, line, source):
            if message.startswith("lnc-line "):
                self.lined.emit(int(message.split()[1]))
            elif message.startswith("lnc-pick "):
                self.picked.emit(int(message.split()[1]))
            elif message.startswith("lnc-note "):
                self.note.emit(message[len("lnc-note "):])
            elif message.startswith("lnc-click "):
                self.clicked.emit(message[len("lnc-click "):])
            elif message.startswith("lnc-scroll "):
                top, height, view = (int(x) for x in message.split()[1:4])
                self.scrolled.emit(top, height, view)
            elif level == QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel:
                LOG.warning("JavaScript: %s (%s:%s)", message, source, line)

    class EpubView(QWebEngineView):
        """Un archivo del EPUB como en un lector de size=(ancho, alto) en px de CSS, encogido o agrandado a lo que
        mide la vista, seguido y con barra de desplazamiento. paged(páginas) avisa al cargar (una página: lo que cabe
        en la vista); go(n) va a una, step(±1) a la de al lado de donde se esté. La barra de desplazamiento (bar) es
        de Qt y va fuera, donde la ponga quien la usa, como la del panel del original; moved() avisa al moverse."""
        paged = Signal(int)
        moved = Signal()

        def __init__(self, parent=None):
            super().__init__(parent)
            self.profile_ = QWebEngineProfile()  # propio y sin disco: nada del libro se queda en la caché
            self.profile_.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
            self.zip = _Zip(self.profile_)
            self.profile_.installUrlSchemeHandler(SCHEME, self.zip)
            self.setPage(_Page(self.profile_, self))
            # El perfil, hijo de la página: se destruye tras ella. Hijo de la vista, caía antes y Qt avisaba al cerrar
            # («Release of profile requested but WebEnginePage still not deleted»)
            self.profile_.setParent(self.page())
            settings = self.page().settings()
            settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
            settings.setAttribute(QWebEngineSettings.WebAttribute.ShowScrollBars, False)  # la de Qt, fuera
            self.bar = QScrollBar(Qt.Orientation.Vertical)
            self.bar.setRange(0, 0)
            self.bar.valueChanged.connect(lambda v: self.page().runJavaScript(f"window.scrollTo(0, {v})"))
            self.page().scrolled.connect(self._scrolled)
            self.page().note.connect(self._note)
            self.scale = 1.0  # el zoom de quien la mira (− y +), sobre el del tamaño del lector
            self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
            self.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # solo se mira: el teclado sigue en la lista
            self.page().setBackgroundColor(Qt.GlobalColor.white)
            self.size_, self.dark, self.page_, self.pages, self.target = (300, 400), False, 0, 0, None
            self.loads = 0  # páginas ya cargadas y medidas: quien pasa de capítulo sabe cuándo ha llegado el nuevo
            # Lo que se ve de antes tapa la página mientras carga la nueva: sin el blanco de una web que recarga
            self.cover = QLabel(self)
            self.cover.hide()
            self.loadFinished.connect(lambda ok: self.url().scheme() == SCHEME.decode() and self._loaded(ok))
            self.loadFinished.connect(lambda ok: ok or LOG.warning("No se pudo cargar %s", self.url().toString()))
            # Chromium dibuja en otro proceso: si muere (memoria, GPU), la vista se queda en blanco sin más aviso
            self.page().renderProcessTerminated.connect(
                lambda status, code: LOG.log(logging.DEBUG if status.name == "NormalTerminationStatus" else logging.ERROR,
                                             "El proceso de Chromium terminó: %s (código %s)", status.name, code))
            # Una página vacía ya crea la superficie de Chromium: la ventana nace preparada para ella. Si no, Windows
            # la vuelve a crear al mostrarse la vista (parpadeaba como si se abriera dos veces)
            self.setHtml("")

        def sizeHint(self):
            """El tamaño máximo que le da la ventana (la página del lector), si lo tiene."""
            biggest = self.maximumSize()
            return biggest if biggest.height() < 10000 else super().sizeHint()

        def hold(self):
            """Deja a la vista lo que se ve ahora hasta unveil(): se llama antes de cargar otra página."""
            # con su proceso muerto no hay qué enseñar: grab() cerraba la aplicación (access violation)
            if self.isVisible() and self.url().scheme() == SCHEME.decode() and not getattr(self, "dead", False):
                self.cover.setPixmap(self.grab())
                self.cover.setGeometry(self.rect())
                self.cover.show()
                self.cover.raise_()

        def unveil(self):
            """Quita lo tapado por hold() cuando Chromium ya dibujó la página nueva."""
            from PySide6.QtCore import QTimer
            QTimer.singleShot(60, self.cover.hide)

        def show_file(self, data, path, size, dark=False, page=0, find=None, override=None):
            """Muestra path del EPUB data (bytes) como en un lector de size; page: la página (-1: la última);
            find: un texto que buscar, y se va a su página. override: {ruta: bytes} que sustituyen a los del zip."""
            self.hold()
            self.zip.load(data)
            self.zip.files.update(override or {})
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

        def _note(self, href):
            """La nota de la llamada href, sacada del EPUB que se muestra, en su recuadro."""
            from urllib.parse import unquote, urlsplit
            here = unquote(self.url().path()).lstrip("/")
            target = urlsplit(href)
            path = posixpath.normpath(posixpath.join(posixpath.dirname(here), unquote(target.path))) if target.path else here
            data = self.zip.files.get(path, b"").decode("utf-8", "replace")
            inner = note_html(data, unquote(target.fragment))
            if inner:
                self.page().runJavaScript(f"window.lncShowNote({json.dumps(inner)}, {json.dumps(self.dark)})", 0)

        def tag(self, items):
            """Cada párrafo de la vista con su n.º en la hoja (TAG_JS): items [[n.º, letras, n.º]] como mark."""
            self.page().runJavaScript(TAG_JS % json.dumps(items), 0)

        def patch(self, n, html, missing=None):
            """El párrafo n.º n de la hoja (TAG_JS) con html, al momento, sin rehacer la página. missing(): no está."""
            self.page().runJavaScript(PATCH_JS % (int(n), json.dumps(html)), 0,
                                      lambda ok: ok is False and missing and missing())

        def mark(self, text, color, nth=0, image=-1, line=-1):
            """Recuadra el párrafo n.º nth cuyas letras (minúsculas, sin lo demás) contienen text ("": el n.º nth sin
            letras) y lo trae a la vista; con image, la ilustración n.º image. text None quita el recuadro. En una
            vista sellada (convert._stamp) solo cuenta line, la línea del capítulo: -1 quita el recuadro."""
            self.page().runJavaScript(MARK_JS % (json.dumps(text), json.dumps(color), int(nth), int(image),
                                                 int(line)), 0)

        def _loaded(self, ok):
            if not ok:
                self.loads += 1
                self.pages = 0
                self.cover.hide()
                self.paged.emit(0)
                return
            pad_x, pad_y = round(self.size_[0] * 0.06), round(self.size_[1] * 0.04)
            css = SCROLLED % {"pad_x": pad_x, "pad_y": pad_y, "pad_y2": pad_y * 2}
            css += DARK if self.dark else ""
            self.page().runJavaScript(SCROLL_JS, 0)
            self.page().runJavaScript(CLICK_JS, 0)
            self.page().runJavaScript(NOTE_JS, 0)
            script = ("(function(){var s=document.createElement('style');s.textContent=%s;"
                      "(document.head||document.documentElement).appendChild(s);"
                      "return Math.max(1,Math.ceil(document.documentElement.scrollHeight/window.innerHeight));})()"
                      % json.dumps(css))
            self.page().runJavaScript(script, 0, _alive(self, self._paged))

        def _paged(self, pages):
            self.loads += 1
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
                                      _alive(self, lambda _: self.unveil()))

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
            self.page().runJavaScript(script, 0, _alive(self, lambda n: done(int(n or 0))))

        def evaluate(self, script, done):
            """JavaScript en la página; done(resultado). Para las pruebas: estilos calculados, páginas."""
            self.page().runJavaScript(script, 0, done)


if AVAILABLE:
    class WebSheet(EpubView):
        """Hoja editable de Chromium (websheet): el capítulo con el CSS del libro, sin paginar. ready() avisa al cargar;
        las órdenes de la cinta y el volcado van por JavaScript (window.lnc de websheet.SHEET_JS)."""
        ready = Signal()
        menu_at = Signal(object)  # QPoint del clic derecho
        died = Signal()  # el proceso de Chromium murió (memoria, GPU): lo pendiente ya contestó None

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)  # aquí sí se escribe
            self.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)  # copiar, pegar
            self.loaded, self.closing = False, False  # closing: el editor se cerró (TextEditor.on_destroy)
            # respuestas de Chromium por llegar: si su proceso muere no llegan nunca, y Aceptar y Cancelar esperaban
            # para siempre (el editor no se podía cerrar)
            self.waiting, self.asked, self.dead = {}, 0, False
            self.page().renderProcessTerminated.connect(self._died)

        def load_sheet(self, files, path, text):
            """Muestra text (la página de websheet.page) como el archivo path, con files (ruta -> bytes) a mano."""
            import websheet  # noqa: F401  (solo para que el .exe lo incluya junto a esta vista)
            self.hold()
            self.zip.files = dict(files)
            self.zip.files[path] = text.encode("utf-8")
            self.loaded, self.dead = False, False  # cargar otra vez arranca otro proceso
            self.setZoomFactor(self.scale)
            self.setUrl(QUrl("epub:///" + path))

        def fit(self):
            self.setZoomFactor(self.scale)  # sin la escala del lector: se lee a su tamaño, por el zoom del panel

        def set_dark(self, on):
            """Fondo oscuro y letra clara (las ilustraciones, tal cual); se vuelve a poner al cargar otra página."""
            self.dark = on
            css = (SHEET_DARK if on else "")
            self.page().runJavaScript(
                "(function(c){var s=document.getElementById('lnc-dark');if(!s){s=document.createElement('style');"
                "s.id='lnc-dark';document.documentElement.appendChild(s);}s.textContent=c;return true;})(%s)"
                % json.dumps(css), 0)

        def show_note(self, text):
            """Un aviso en lugar del capítulo (lo que no se edita aquí)."""
            import html
            self.loaded = False
            if self.dead:  # setHtml con el proceso muerto cerraba la aplicación (Chromium, 0x80000003): encima, en Qt
                self.cover.setPixmap(QPixmap())
                self.cover.setText(text)
                self.cover.setWordWrap(True)
                self.cover.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
                self.cover.setAutoFillBackground(True)  # el fondo y la letra del tema (claro u oscuro)
                self.cover.setStyleSheet("font: 14px; padding: 24px")
                self.cover.setGeometry(self.rect())
                self.cover.show()
                self.cover.raise_()
                return
            self.setHtml(f"<p style='font:14px sans-serif;color:#777;margin:24px'>{html.escape(text)}</p>")

        def _loaded(self, ok):
            import websheet
            if ok:
                self.page().runJavaScript(SCROLL_JS, 0)
                self.page().runJavaScript(websheet.SHEET_JS, 0, _alive(self, lambda _: self._ready()))
            else:
                self.loads += 1

        def _ready(self):
            # al borrarse la página Chromium contesta igual a SHEET_JS: el editor ya cerrado no tiene a quién pintar
            if self.closing:
                return
            self.loads += 1
            if self.dark:
                self.set_dark(True)
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
            if self.dead:  # sin proceso no contesta nadie: None al momento, como una página sin lnc
                if done:
                    QTimer.singleShot(0, lambda: shiboken6.isValid(self) and not self.closing and done(None))
                return
            self.asked += 1
            key = self.asked
            if done:
                self.waiting[key] = done

            def answer(result):  # la respuesta puede llegar con la ventana ya cerrada: entonces no se hace nada
                late = self.waiting.pop(key, None) if shiboken6.isValid(self) else None
                if late and not self.closing:
                    late(result)
            self.page().runJavaScript(script, 0, answer)

        def recovering(self):
            """¿Murió su proceso hace poco? Cargar otra página en los primeros segundos cerraba la aplicación entera
            (access violation en Chromium, en la pantalla de verdad): se espera."""
            return self.dead and time.monotonic() - self.died_at < RECOVER_S

        def _died(self, status, code):
            if status == QWebEnginePage.RenderProcessTerminationStatus.NormalTerminationStatus:
                return
            self.loaded, self.dead, self.died_at = False, True, time.monotonic()
            waiting, self.waiting = self.waiting, {}
            self.died.emit()  # antes de las respuestas: el editor ya sabe que la web no está
            for done in waiting.values():
                if not self.closing:
                    done(None)


def note_html(xhtml, note_id):
    """HTML de dentro del elemento con id note_id en xhtml, sin el enlace de vuelta a la llamada ("" si no está)."""
    import re
    start = re.search(r'<([\w:]+)\b[^>]*\bid="%s"[^>]*>' % re.escape(note_id), xhtml) if note_id else None
    if not start:
        return ""
    tag, depth, pos = start[1], 1, start.end()
    for m in re.compile(r"<(/?)%s\b[^>]*?(/?)>" % re.escape(tag)).finditer(xhtml, pos):
        if m[2]:  # <tag/>: no abre ni cierra
            continue
        depth += -1 if m[1] else 1
        if depth == 0:
            inner = xhtml[pos:m.start()]
            # la flecha de vuelta (↩ o «Volver») no sirve en el recuadro
            return re.sub(r'<a\b[^>]*(?:backlink|doc-backlink|rearnote)[^>]*>.*?</a>', "", inner, flags=re.S).strip()
    return ""


def merge_files(files, order, base):
    """El archivo base con el <body> de los demás de order delante o detrás, en su orden: un capítulo de varios
    archivos (portadilla y texto) en una sola página, con la hoja de estilo del de texto."""
    import re

    def inner(name):
        text = files.get(name, b"").decode("utf-8", "replace")
        m = re.search(r"<body\b[^>]*>(.*)</body>", text, re.S)
        return m[1] if m else ""
    at = order.index(base)
    text = files[base].decode("utf-8", "replace")
    before, after = "".join(inner(n) for n in order[:at]), "".join(inner(n) for n in order[at + 1:])
    text = re.sub(r"(<body\b[^>]*>)", lambda m: m[1] + before, text, count=1)
    cut = text.rfind("</body>")
    return (text[:cut] + after + text[cut:]).encode("utf-8")


def section_path(data):
    """Ruta en el zip de la sección de un EPUB de vista previa: la segunda del spine (la primera es la portada)."""
    import io

    import faithful
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        _, _, items, order, _ = faithful.package(z)
    paths = [items[i][0] for i in order if "html" in items[i][1]]
    return paths[1] if len(paths) > 1 else paths[0]
