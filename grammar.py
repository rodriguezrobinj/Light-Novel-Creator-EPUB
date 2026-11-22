"""Revisión de gramática con LanguageTool: el servidor local (descargado una vez, corre con Java) o uno remoto
(languagetool.org, uno propio o Premium). Sin interfaz: la ventana llama a review en el proceso de trabajo y a
issues para el informe de calidad."""
import bisect
import hashlib
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

import convert

LT_VERSION = "6.6"
LT_URL = f"https://languagetool.org/download/LanguageTool-{LT_VERSION}.zip"
LT_SHA256 = "53600506b399bb5ffe1e4c8dec794fd378212f14aaf38ccef9b6f89314d11631"
LT_SIZE = "250 MB"
PUBLIC_URL = "https://api.languagetool.org"
PREMIUM_URL = "https://api.languagetoolplus.com"
# Lo que ya revisa la app (Hunspell, «Signo sin pareja», la limpieza tipográfica: rayas, espacios, «....») o lo
# que en las novelas es ruido, medido en Re Zero 1 y World Project 7: diálogos que empiezan con «…», «Ha-ha»,
# frases cortadas a propósito, la coma tras cada adverbio («Probablemente,») y mayúsculas en «battle royale»
DISABLED = ("MORFOLOGIK_RULE_ES", "WHITESPACE_RULE", "UNPAIRED_BRACKETS", "ES_UNPAIRED_BRACKETS",
            "ES_QUESTION_MARK", "ES_INITIAL_QUESTION_MARK", "UPPERCASE_SENTENCE_START", "ESPACIO_DESPUES_DE_PUNTO",
            "HA_A", "PRONOMBRE_SIN_VERBO", "COMMA_ADVERB", "WRONG_DASH", "INCORRECT_SPACES", "CUATRO_PUNTOS",
            "ES_MULTITOKEN_SPELLING_TWO", "MORFOLOGIK_RULE_EN_US", "MORFOLOGIK_RULE_EN_GB")
RULES_ID = LT_VERSION + ":" + ",".join(DISABLED)  # si cambia, lo revisado antes no vale
STYLE = {"PUNCTUATION", "STYLE", "TYPOGRAPHY", "REDUNDANCY", "CASING"}  # sugerencias, no faltas
KINDS = [("grammar", "Gramática", "Revisar"), ("grammar-style", "Estilo y puntuación", "Menor")]
LIMITS = {"local": 50_000, "custom": 50_000, "public": 18_000, "premium": 50_000}  # letras por petición
ENGINES = {"local": "En este equipo (descarga LanguageTool, necesita Java)",
           "public": "languagetool.org, gratis (el texto sale del equipo)",
           "custom": "Servidor propio", "premium": "LanguageTool Premium"}


class GrammarError(convert.BookError):
    """LanguageTool no pudo revisar y el porqué, en español: no es un fallo de la app."""


def key(text):
    """Cómo se recuerda un párrafo revisado: al editarlo cambia y pierde sus avisos hasta otra revisión."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _units(text):
    """Largo en unidades UTF-16: las posiciones de LanguageTool (Java) cuentan así, y un emoji ocupa dos."""
    return len(text.encode("utf-16-le")) // 2


def _index(text, units):
    """Índice de Python que corresponde a units unidades UTF-16 de text."""
    return len(text.encode("utf-16-le")[:units * 2].decode("utf-16-le", "ignore"))


def batches(texts, limit):
    """[(primero, fin)] de los textos que caben juntos en una petición de limit letras."""
    out, start, size = [], 0, 0
    for n, text in enumerate(texts):
        if n > start and size + len(text) + 2 > limit:
            out.append((start, n))
            start, size = n, 0
        size += len(text) + 2
    if start < len(texts):
        out.append((start, len(texts)))
    return out


def _post(base, fields, tries=3):
    request = urllib.request.Request(base.rstrip("/") + "/v2/check", urllib.parse.urlencode(fields).encode("utf-8"),
                                     {"Accept": "application/json", "User-Agent": "LightNovelCreator"})
    for attempt in range(tries + 1):
        try:
            with urllib.request.urlopen(request, timeout=300) as reply:
                return json.load(reply)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < tries:  # languagetool.org: 20 peticiones por minuto
                wait = e.headers.get("Retry-After") or ""
                time.sleep(min(float(wait), 120) if wait.replace(".", "", 1).isdigit() else 60)
                continue
            if e.code in (401, 403):
                raise GrammarError("LanguageTool no aceptó el usuario o la clave: revísalos en Opciones → "
                                   "Ortografía.") from None
            if e.code == 429:
                raise GrammarError("LanguageTool pide esperar: se llegó al límite de peticiones. Lo revisado ya "
                                   "está guardado; vuelve a pulsar «Revisar gramática» en unos minutos.") from None
            detail = e.read()[:300].decode("utf-8", "replace").strip()
            raise GrammarError(f"LanguageTool respondió con un error ({e.code})." + (f"\n{detail}" if detail else "")) \
                from None
        except (OSError, ValueError) as e:
            raise GrammarError("No se pudo hablar con LanguageTool: comprueba la conexión a internet o la dirección "
                               "del servidor.") from e


def check(texts, base, lang, user="", api_key="", limit=50_000, progress=None, done=None):
    """Los fallos de cada texto: [[inicio, largo, mensaje, sugerencias, regla, categoría]…], con índices de Python.
    done(primero, fin, fallos) recibe cada lote al acabarlo (para guardarlo ya); progress(hechos, total)."""
    found = [[] for _ in texts]
    for first, end in batches(texts, limit):
        chunk = texts[first:end]
        # shortcut: un párrafo más largo que el límite de la petición se queda sin revisar; el informe ya lo avisa
        if len(chunk) == 1 and len(chunk[0]) > limit:
            continue
        fields = {"text": "\n\n".join(chunk), "language": lang, "disabledRules": ",".join(DISABLED)}
        if user and api_key:
            fields.update(username=user, apiKey=api_key)
        starts, at = [], 0
        for text in chunk:
            starts.append(at)
            at += _units(text) + 2
        for m in _post(base, fields).get("matches", []):
            offset, length = int(m.get("offset", 0)), int(m.get("length", 0))
            n = bisect.bisect_right(starts, offset) - 1
            text, local = chunk[n], offset - starts[n]
            if local + length > _units(text):  # cruza al párrafo siguiente: no hay dónde ponerlo
                continue
            a, b = _index(text, local), _index(text, local + length)
            rule = m.get("rule") or {}
            found[first + n].append([a, b - a, m.get("message", ""),
                                     [r.get("value", "") for r in (m.get("replacements") or [])[:3]],
                                     rule.get("id", ""), (rule.get("category") or {}).get("id", "")])
        if done:
            done(first, end, found[first:end])
        if progress:
            progress(end, len(texts))
    return found


def issues(blocks, found):
    """Avisos del informe para los párrafos de blocks ya revisados (found: {key(texto): fallos})."""
    out = []
    for k, b in enumerate(blocks):
        if b.kind != "p" or not found:
            continue
        text = convert._plain(b).strip()
        for start, length, message, options, rule, category in found.get(key(text), ()):
            wrong = text[start:start + length]
            if not wrong:
                continue
            kind, name, level = KINDS[1] if category in STYLE else KINDS[0]
            head, tail = max(0, start - 50), start + length + 50
            dots = ("…" if head else "", "…" if tail < len(text) else "")
            before = dots[0] + text[head:tail].strip() + dots[1]
            line, edit, after = convert._line(b, k), None, ""
            if options and line.count(wrong) == 1:  # una sola vez y sin etiquetas dentro: se cambia sin dudar
                edit = (k, k + 1, line.replace(wrong, options[0]))
                after = dots[0] + (text[head:start] + options[0] + text[start + length:tail]).strip() + dots[1]
            if edit:
                solution = f"Cambiar «{wrong}» por «{options[0]}»."
            elif options:
                solution = (f"Cambia «{wrong}» por «{options[0]}» con «Editar texto…» (tecla E): está en una "
                            "cursiva o más de una vez en el párrafo.")
            else:
                solution = "Revisa la frase con «Editar texto…» (tecla E)."
            if len(options) > 1:
                solution += " Otras opciones: " + ", ".join(f"«{o}»" for o in options[1:]) + "."
            shown = wrong if len(wrong) <= 40 else wrong[:39] + "…"
            out.append(convert.Issue(kind, k, b.page, level, f"{name}: «{shown}»", message or "LanguageTool lo marca.",
                                     solution, before, after, edit, text[max(0, start - 30):start + length + 30],
                                     None))
    return out


def load(path, lang):
    """{key: fallos} de la caché de path, vacía si es de otras reglas u otro idioma (sus avisos ya no valen)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("rules") != RULES_ID or data.get("lang") != lang:
        return {}
    found = data.get("found")
    return found if isinstance(found, dict) else {}


def _save(path, lang, found):
    """Escribe la caché de una vez: un corte a mitad no deja un JSON roto."""
    tmp = Path(str(path) + ".tmp")
    try:
        tmp.write_text(json.dumps({"rules": RULES_ID, "lang": lang, "found": found}, ensure_ascii=False),
                       encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        raise GrammarError(f"No se pudo guardar la revisión de gramática junto al libro ({Path(path).name}): "
                           "comprueba que la carpeta no es de solo lectura.") from e


def review(path, base, texts, lang, user="", api_key="", limit=50_000, wait=0, log="", progress=None):
    """Revisa los textos que la caché de path aún no tiene y la guarda lote a lote: si se corta (sin red, el límite
    de languagetool.org), lo ya revisado queda. wait: segundos para que el servidor local arranque. Proceso de
    trabajo. Devuelve (párrafos revisados, fallos encontrados)."""
    found = load(path, lang)
    todo = list(dict.fromkeys(t for t in texts if t.strip() and key(t) not in found))
    if not todo:
        return 0, 0
    if wait:
        ready(base, wait, log)
    count = [0]

    def keep(first, end, results):
        for text, matches in zip(todo[first:end], results):
            found[key(text)] = matches
            count[0] += len(matches)
        _save(path, lang, found)

    check(todo, base, lang, user, api_key, limit, progress, keep)
    return len(todo), count[0]


# --- servidor local

def folder(base):
    return Path(base) / f"LanguageTool-{LT_VERSION}"


def installed(base):
    return (folder(base) / "languagetool-server.jar").is_file()


def download(base, progress=None):
    """Descarga LanguageTool, lo compara con el oficial (Java ejecutaría lo que traiga) y lo pone en su sitio de
    una vez. Proceso de trabajo. Devuelve su carpeta."""
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True)
    part, digest = base / "languagetool.zip.part", hashlib.sha256()
    try:
        with urllib.request.urlopen(LT_URL, timeout=60) as reply, open(part, "wb") as f:
            total, got = int(reply.headers.get("Content-Length") or 0), 0
            while chunk := reply.read(1 << 20):
                f.write(chunk)
                digest.update(chunk)
                got += len(chunk)
                if progress and total:
                    progress(got, total)
    except OSError as e:
        part.unlink(missing_ok=True)
        raise GrammarError("No se pudo descargar LanguageTool: comprueba la conexión a internet.") from e
    tmp = None
    try:
        if digest.hexdigest() != LT_SHA256:
            raise GrammarError("El LanguageTool descargado no coincide con el oficial: no se usa.")
        tmp = Path(tempfile.mkdtemp(prefix="lnepub-languagetool-", dir=base))
        with zipfile.ZipFile(part) as z:
            z.extractall(tmp)
        shutil.rmtree(folder(base), ignore_errors=True)  # uno a medias de antes
        os.replace(tmp / folder(base).name, folder(base))
    except (OSError, zipfile.BadZipFile) as e:
        raise GrammarError("No se pudo descomprimir LanguageTool: comprueba que queda sitio en el disco.") from e
    finally:
        part.unlink(missing_ok=True)
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
    return str(folder(base))


def start(base, java):
    """Arranca el servidor local en un puerto libre, solo para este equipo. Devuelve (proceso, dirección, registro).
    Su salida va a un archivo: por una tubería sin leer, el servidor se pararía al llenarla."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    log = Path(base) / "languagetool.log"
    with open(log, "wb") as out:
        proc = subprocess.Popen([java, "-cp", str(folder(base) / "languagetool-server.jar"),
                                 "org.languagetool.server.HTTPServer", "--port", str(port)],
                                cwd=folder(base), stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return proc, f"http://127.0.0.1:{port}", str(log)


def _start_problem(log):
    """Por qué no arrancó el servidor, según su registro, o "" si no se ve ningún fallo."""
    try:
        text = Path(log).read_text(encoding="utf-8", errors="replace") if log else ""
    except OSError:
        return ""
    if "UnsupportedClassVersionError" in text:
        return "LanguageTool necesita Java 17 o posterior: actualízalo gratis desde java.com."
    lines = [line for line in text.splitlines() if "Exception" in line or line.startswith("Error")]
    return f"LanguageTool no pudo arrancar.\n{lines[0].strip()}" if lines else ""


def ready(base, seconds, log=""):
    """Espera a que el servidor responda; un fallo en su registro corta la espera al momento."""
    end = time.monotonic() + seconds
    while True:
        try:
            urllib.request.urlopen(base.rstrip("/") + "/v2/languages", timeout=5).close()
            return
        except OSError:
            pass
        problem = _start_problem(log)
        if problem:
            raise GrammarError(problem)
        if time.monotonic() > end:
            raise GrammarError("LanguageTool tarda demasiado en arrancar. Cierra otros programas y vuelve a probar.")
        time.sleep(0.3)
