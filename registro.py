"""Registro de la aplicación (registro.log, junto a las preferencias): qué hizo cada parte y cuánto tardó, para saber
qué pasó cuando algo falla. Cada parte escribe con su nombre: app (la ventana), trabajo (el proceso de trabajo),
convert y faithful (el motor), translate, chromium y qt; el editor, en app como «Editor: …»; acciones (clics, atajos
y ventanas que se abren) y congelada (la ventana que no responde). El .exe no tiene consola: sin esto, solo quedaba
errores.log con los fallos, sin lo que pasó antes. `python registro.py [carpeta]` resume el registro."""
import collections
import contextlib
import functools
import logging
import re
import reprlib
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

FORMAT = "%(asctime)s %(levelname)-7s %(process)5d [%(name)s] %(message)s"
SIZE, KEPT = 2_000_000, 4  # 2 MB por archivo y 4 de antes: unos días de uso, sin llenar el disco
# Las partes de la app: su DEBUG se apunta. De las bibliotecas, solo avisos: PIL apuntaba cada etiqueta TIFF de cada
# ilustración (7 de cada 10 líneas del registro con un EPUB japonés)
OURS = {"app", "trabajo", "convert", "faithful", "translate", "chromium", "qt", "congelada", "acciones", "editor",
        "websheet", "webview", "options", "qtk", "registro", "__main__"}


class OnlyOurs(logging.Filter):
    def filter(self, record):
        return record.levelno >= logging.WARNING or record.name.split(".")[0] in OURS


def setup(folder):
    """Escribe el registro en folder/registro.log. Una vez por proceso; sin permisos, la app sigue sin registro."""
    root = logging.getLogger()
    if any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        return
    try:
        Path(folder).mkdir(parents=True, exist_ok=True)
        # delay: el archivo se abre al escribir la primera línea, no al importar
        handler = RotatingFileHandler(Path(folder) / "registro.log", maxBytes=SIZE, backupCount=KEPT,
                                      encoding="utf-8", delay=True)
    except OSError:
        return
    handler.setFormatter(logging.Formatter(FORMAT))
    handler.addFilter(OnlyOurs())
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)


def path(folder):
    return Path(folder) / "registro.log"


class ToQueue(logging.Handler):
    """En el proceso de trabajo: cada línea va a la ventana por su cola de eventos ("rec", nivel, parte, texto) y la
    ventana la escribe. Dos procesos escribiendo el mismo archivo chocaban al rotarlo (Windows lo tiene abierto)."""

    def __init__(self, events):
        super().__init__(logging.DEBUG)
        self.events = events
        self.addFilter(OnlyOurs())  # el ruido de las bibliotecas, ni por la cola

    def emit(self, record):
        try:
            self.events.put(("rec", record.levelno, record.name, self.format(record)))
        except Exception:
            pass  # la cola cerrada (la ventana se cerró): la línea se pierde, el trabajo sigue


def replay(event):
    """Escribe en este proceso una línea que mandó otro con ToQueue."""
    _, level, name, text = event
    logging.getLogger(name).log(level, "%s", text)


# Lo último que hizo quien usa la app (note_action): la congelación y el fallo dicen tras qué pasaron
LAST_ACTION = ""
_waiting, _resumed = 0, 0.0  # dentro de un diálogo de Windows (waiting): la ventana espera, no está congelada


def note_action(text):
    """Apunta una acción (clic, atajo, ventana que se abre) y la recuerda como la última."""
    global LAST_ACTION
    LAST_ACTION = text
    logging.getLogger("acciones").debug("%s", text)


@contextlib.contextmanager
def waiting():
    """Mientras dura, la ventana espera a quien la usa (el diálogo «Guardar como» de Windows no deja latir a Qt): no
    es una congelación. Sin esto, cada «Guardar como» salía en el registro como ventana congelada."""
    global _waiting, _resumed
    _waiting += 1
    try:
        yield
    finally:
        _waiting -= 1
        _resumed = time.monotonic()


def _where(samples):
    """La pila más vista entre las muestras, contando cada una por sus funciones (no por sus líneas: dentro de un
    bucle, cada muestra cae en una línea distinta y todas eran «la más vista» con 1)."""
    if not samples:
        return "(sin pila de Python: dentro de Qt o de MuPDF)"
    import traceback
    counts = collections.Counter(tuple((f[0], f[2]) for f in s) for s in samples)
    key, seen = counts.most_common(1)[0]
    stack = next(s for s in reversed(samples) if tuple((f[0], f[2]) for f in s) == key)
    leaves = collections.Counter(f"{Path(s[-1][0]).name}:{s[-1][2]}" for s in samples if s)
    hot = ", ".join(f"{name} {n * 100 // len(samples)} %" for name, n in leaves.most_common(3))
    return (f"{seen} de {len(samples)} muestras; lo más visto: {hot}\n"
            + "".join(traceback.StackSummary.from_list(list(stack)).format()))


class Freezes:
    """Congelaciones de la ventana: beat() lo llama un temporizador de la ventana cada 100 ms; si entre dos latidos
    pasa más de limit s, apunta cuánto duró, tras qué acción y dónde estaba la ventana (las pilas que un hilo aparte
    tomó mientras tanto). Así una ventana que «se congela» deja dicho por qué."""

    def __init__(self, limit=0.7):
        import threading
        self.limit, self.last, self.samples = limit, time.monotonic(), []
        self.thread = threading.main_thread()
        self.log = logging.getLogger("congelada")
        self.count, self.worst = 0, 0.0  # para el resumen al cerrar
        threading.Thread(target=self._watch, daemon=True, name="congelada").start()

    def _watch(self):
        import gc
        import traceback
        while True:
            time.sleep(0.25)
            if not _waiting and time.monotonic() - self.last > self.limit / 2:
                # Sin el recolector de ciclos mientras: los objetos que crea la muestra lo hacían saltar en este hilo,
                # y la basura con objetos de Qt se destruía fuera del suyo (el proceso caía en sitios al azar)
                was = gc.isenabled()
                gc.disable()
                try:
                    frame = sys._current_frames().get(self.thread.ident)
                    if frame is not None:  # solo archivo, línea y función: leer el código para dar formato tardaba
                        # 0,6 s y la muestra llegaba tarde; el texto se forma al apuntarla. 30 marcos: con 14 se
                        # perdía qué acción había empezado lo que tardaba
                        stack = traceback.StackSummary.extract(traceback.walk_stack(frame), limit=30,
                                                               lookup_lines=False)
                        self.samples.append(tuple((f.filename, f.lineno, f.name, None) for f in reversed(stack)))
                    del frame
                finally:
                    if was:
                        gc.enable()

    def beat(self):
        now = time.monotonic()
        gap, self.last = now - max(self.last, _resumed), now
        samples, self.samples = self.samples, []
        started, self.started = getattr(self, "started", False), True
        if gap > self.limit and started:  # el primer latido mide el arranque (crear la ventana), no una congelación
            self.count, self.worst = self.count + 1, max(self.worst, gap)
            after = f" tras «{LAST_ACTION}»" if LAST_ACTION else ""
            self.log.warning("La ventana no respondió %.1f s%s. Estaba en: %s", gap, after, _where(samples))


def peak_memory_mb():
    """Memoria máxima que usó este proceso (MB), o None fuera de Windows: lo que pesa un libro grande."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    try:
        # el proceso es un HANDLE de 64 bits (-1): sin restype, ctypes lo cortaba a int y la llamada fallaba
        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.K32GetProcessMemoryInfo.argtypes = (wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD)
        if not kernel32.K32GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return None
    except (AttributeError, OSError):
        return None
    return counters.PeakWorkingSetSize // 1_000_000


_short = reprlib.Repr(maxstring=120, maxother=120, maxlist=6, maxdict=6, maxlevel=2)


def crash_details(tb):
    """Las variables de la función donde saltó el fallo, cortas: en el .exe la pila no trae el código y, sin ellas,
    «'NoneType' object has no attribute…» no decía qué valía qué."""
    while tb is not None and tb.tb_next is not None:
        tb = tb.tb_next
    if tb is None:
        return ""
    lines = []
    for name, value in list(tb.tb_frame.f_locals.items())[:20]:
        try:
            shown = _short.repr(value)
        except Exception as e:  # un __repr__ que falla no tapa el fallo de verdad
            shown = f"<{type(value).__name__}: {type(e).__name__}>"
        lines.append(f"    {name} = {shown}")
    return "Variables en " + tb.tb_frame.f_code.co_name + ":\n" + "\n".join(lines) if lines else ""


def timed(fn):
    """Apunta cada llamada a fn: su archivo o nombre (el primer argumento de texto), cuánto tardó y, si falló, por
    qué. Lo rápido va en DEBUG; lo que tarda más de 1 s, en INFO."""
    log = logging.getLogger(fn.__module__)

    @functools.wraps(fn)
    def run(*args, **kwargs):
        what = next((str(a) for a in args if isinstance(a, (str, Path))), "")
        start = time.perf_counter()
        try:
            result = fn(*args, **kwargs)
        except Exception as e:
            log.warning("%s(%s) falló tras %.2f s: %s: %s", fn.__name__, what, time.perf_counter() - start,
                        type(e).__name__, e)
            raise
        spent = time.perf_counter() - start
        log.log(logging.INFO if spent >= 1 else logging.DEBUG, "%s(%s): %.2f s", fn.__name__, what, spent)
        return result
    return run


LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ (\w+)\s+(\d+) \[([\w.]+)\] (.*)$")
TIMED = re.compile(r"^(\w+)\(.*\): (\d+\.\d+) s$")
WORK = re.compile(r"^Trabajo (\w+): hecho en (\d+\.\d+) s")
FROZEN = re.compile(r"^La ventana no respondió (\d+\.\d+) s")
APP_FRAME = re.compile(r'File "(?:.*[\\/])?(app|editor|websheet|webview|qtk|options|convert|faithful)\.py", line \d+, '
                       r"in (\w+)")


def summary(text):
    """Auditoría del registro, en texto: sesiones, fallos (agrupados), congelaciones por dónde estaba la ventana,
    las operaciones más lentas y qué partes escriben más. Para leer de un vistazo días de uso."""
    entries = []  # [fecha, nivel, proceso, parte, texto con sus líneas de detrás]
    for line in text.splitlines():
        m = LINE.match(line)
        if m:
            entries.append(list(m.groups()))
        elif entries:
            entries[-1][4] += "\n" + line
    if not entries:
        return "El registro está vacío."
    sessions = sum(1 for e in entries if e[3] == "app" and e[4].startswith("=== "))
    errors = collections.Counter()
    for e in entries:
        if e[1] in ("ERROR", "CRITICAL"):
            last = [x for x in e[4].splitlines() if x.strip() and not x.startswith(" ")]
            errors[last[-1][:160] if last else e[4][:160]] += 1
    freezes = collections.defaultdict(list)
    for e in entries:
        m = FROZEN.match(e[4]) if e[3] == "congelada" else None
        if m:
            frames = APP_FRAME.findall(e[4])
            # el marco más adentro que es de la app: ahí está lo que hay que hacer más rápido
            freezes[f"{frames[-1][0]}.{frames[-1][1]}" if frames else "(fuera de Python)"].append(float(m[1]))
    slow = collections.defaultdict(list)
    for e in entries:
        m = TIMED.match(e[4]) or WORK.match(e[4])
        if m:
            slow[f"{e[3]}.{m[1]}"].append(float(m[2]))
    parts = collections.Counter(e[3] for e in entries)
    out = [f"Registro del {entries[0][0]} al {entries[-1][0]}: {len(entries)} líneas, {sessions} sesiones.", ""]
    out.append(f"Fallos: {sum(errors.values())}")
    out += [f"  {n} × {what}" for what, n in errors.most_common(10)]
    out += ["", f"Congelaciones de la ventana: {sum(len(v) for v in freezes.values())}"]
    for where, gaps in sorted(freezes.items(), key=lambda kv: -sum(kv[1]))[:10]:
        out.append(f"  {len(gaps)} × {where}: {sum(gaps) / len(gaps):.1f} s de media, {max(gaps):.1f} s la peor")
    out += ["", "Operaciones (veces · media · peor · total):"]
    for name, spent in sorted(slow.items(), key=lambda kv: -sum(kv[1]))[:15]:
        out.append(f"  {name}: {len(spent)} · {sum(spent) / len(spent):.2f} s · {max(spent):.2f} s · "
                   f"{sum(spent):.1f} s")
    out += ["", "Líneas por parte: " + ", ".join(f"{name} {n}" for name, n in parts.most_common())]
    return "\n".join(out)


if __name__ == "__main__":  # python registro.py [carpeta]: el resumen de registro.log y sus copias rotadas
    import os
    here = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(os.environ.get("APPDATA", Path.home())) / "PDF a EPUB"
    files = sorted(here.glob("registro.log*"), key=lambda p: p.stat().st_mtime)
    sys.stdout.reconfigure(encoding="utf-8")
    print(summary("\n".join(p.read_text(encoding="utf-8", errors="replace") for p in files)))
