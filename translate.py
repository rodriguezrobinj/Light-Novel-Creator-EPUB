"""Traducción automática del libro, párrafo a párrafo, sin interfaz: Google (gratis), DeepL o una IA."""
import base64
import ctypes
import hashlib
import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import convert

# Nombre visible -> tipo de API, URL base y modelo por defecto (los modelos se pueden cambiar en la ventana)
ENGINES = {
    "Google (gratis, sin clave)": {"kind": "google"},
    "Google Cloud (clave)": {"kind": "gcloud", "key": True},  # la API oficial: no bloquea, 500.000 caracteres gratis
    "DeepL": {"kind": "deepl", "key": True},
    "OpenAI (ChatGPT)": {"kind": "openai", "base": "https://api.openai.com/v1", "model": "gpt-4.1-mini", "key": True},
    "DeepSeek": {"kind": "openai", "base": "https://api.deepseek.com", "model": "deepseek-chat", "key": True},
    "Claude": {"kind": "anthropic", "base": "https://api.anthropic.com/v1", "model": "claude-sonnet-5-5", "key": True},
    "Gemini": {"kind": "openai", "base": "https://generativelanguage.googleapis.com/v1beta/openai",
               "model": "gemini-2.5-flash", "key": True},
    "OpenRouter": {"kind": "openai", "base": "https://openrouter.ai/api/v1", "model": "deepseek/deepseek-chat",
                   "key": True},
    # Ollama y LM Studio hablan la API de OpenAI en local: la clave sobra, la URL y el modelo los pone el usuario
    "Compatible con OpenAI (Ollama, LM Studio…)": {"kind": "openai", "base": "http://localhost:11434/v1", "model": "",
                                                   "custom": True},
}
LANGS = {"auto": "Detectar", "es": "Español", "en": "Inglés", "ja": "Japonés", "zh": "Chino", "ko": "Coreano",
         "pt": "Portugués", "fr": "Francés", "it": "Italiano", "de": "Alemán", "ru": "Ruso"}
# Para la instrucción de la IA, que entiende mejor los nombres en inglés
LANG_NAMES = {"auto": "the original language (detect it)", "es": "Spanish", "en": "English", "ja": "Japanese",
              "zh": "Chinese", "ko": "Korean", "pt": "Portuguese", "fr": "French", "it": "Italian", "de": "German",
              "ru": "Russian"}
DEEPL_TARGET = {"en": "EN-US", "pt": "PT-BR", "zh": "ZH-HANS"}  # DeepL ya no acepta "EN" ni "PT" como destino
LIMITS = {"google": (1, 4500), "gcloud": (100, 25000), "deepl": (50, 30000), "openai": (30, 5000), "anthropic": (30, 5000)}  # párrafos, letras
CONTEXT = 5  # párrafos anteriores que ve la IA: el tono y quién habla no se pierden entre lotes
RETRIES = 3
LETTER = re.compile(r"[^\W\d_]")
TAG = re.compile(r"<\s*(/?)\s*([ibIB])\s*>")  # Google a veces devuelve "< i>" o "</ I>"
NOTE_WORD = re.compile(r"\[[^\W\d_]{2,12}:\s*")  # "[Note: " de vuelta a "[Nota: ", la marca del editor


class TranslateError(Exception):
    """Fallo que la ventana muestra tal cual: el mensaje va en español."""


class _Mismatch(Exception):
    """La IA no devolvió un párrafo por cada uno de la entrada."""


def _sleep(seconds):
    time.sleep(seconds)


def _post(url, headers, body):
    request = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"} | headers)
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.loads(response.read().decode("utf-8"))


def _get(url, headers=None):
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"} | (headers or {}))
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def key(text):
    """Clave de un párrafo original: la traducción se encuentra aunque el párrafo cambie de sitio."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _split(line, kind):
    """(prefijo, texto) de una línea de section_text: "# " de un título, "<center>" de un párrafo centrado."""
    for prefix in ("# ",) if kind == "heading" else (convert.CENTER,):
        if line.startswith(prefix):
            return prefix, line[len(prefix):]
    return "", line


def units(book, start, end):
    """Párrafos y títulos de los bloques start..end-1 que tienen letras: [(bloque, prefijo, texto)]."""
    out = []
    for k in range(start, end):
        b = book.blocks[k]
        if b.kind in ("p", "heading"):
            prefix, text = _split(convert._line(b, k), b.kind)
            if LETTER.search(text):
                out.append((k, prefix, text))
    return out


def translated_section(book, start, end, done):
    """Texto de section_text de start..end-1 con cada párrafo traducido si su clave está en done."""
    lines = []
    for k in range(start, end):
        b, line = book.blocks[k], convert._line(book.blocks[k], k)
        if b.kind in ("p", "heading"):
            prefix, text = _split(line, b.kind)
            new = " ".join(str(done.get(key(text)) or "").split())  # un salto de línea partiría el párrafo
            if new:
                line = prefix + new
        lines.append(line)
    return "\n\n".join(lines)


def batches(texts, kind):
    """Lotes de índices de texts que caben en una petición del tipo de motor."""
    most, letters = LIMITS[kind]
    out, size = [], 0
    for n, text in enumerate(texts):
        if out and len(out[-1]) < most and size + len(text) <= letters:
            out[-1].append(n)
            size += len(text)
        else:
            out.append([n])
            size = len(text)
    return out


def _fix(source, out):
    """Arregla lo que el motor pudo estropear de las marcas: etiquetas con espacios y la palabra «Nota»."""
    out = TAG.sub(lambda m: f"<{m[1]}{m[2].lower()}>", out.strip())
    return NOTE_WORD.sub("[Nota: ", out) if "[Nota: " in source else out


GOOGLE_TIP = (" Google gratis corta cuando se le pide mucho seguido: espera un rato, o usa Google Cloud, DeepL "
              "o una IA.")


def _request(name, call, tip="", model=""):
    """Hace la petición con reintentos ante límites de uso y cortes de red; los demás fallos, en español. tip: qué
    hacer si el motor sigue sin responder. model: el pedido, para decir si es él lo que no existe."""
    for attempt in range(RETRIES + 1):
        try:
            return call()
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise TranslateError(f"{name}: la clave no es válida o no tiene permiso.{tip}") from e
            if e.code == 456:
                raise TranslateError("Se acabaron los caracteres de DeepL de este mes.") from e
            if e.code != 429 and e.code < 500:
                detail = e.read().decode("utf-8", "replace")[:300] if e.fp else ""
                if model and e.code in (400, 404) and "model" in detail.lower():
                    raise TranslateError(f"{name}: el modelo «{model}» no existe o no está disponible. Pulsa «Buscar "
                                         f"modelos» y elige otro.") from e
                raise TranslateError(f"{name} respondió con un error {e.code}. {detail}".strip()) from e
            reason = f"error {e.code}" + (" (demasiadas peticiones)" if e.code == 429 else "")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            reason = str(getattr(e, "reason", e) or type(e).__name__)
        except ValueError as e:  # una página de aviso o de captcha en vez de la respuesta
            raise TranslateError(f"{name} devolvió una respuesta que no se entiende.{tip}") from e
        if attempt < RETRIES:
            _sleep(2 ** attempt * 2)
    raise TranslateError(f"No se pudo traducir con {name}: {reason}. Revisa la conexión y vuelve a "
                         f"intentarlo.{tip}")


def _google(texts, src, dst):
    out = []
    for text in texts:
        query = urllib.parse.urlencode({"client": "gtx", "sl": src, "tl": dst, "dt": "t", "q": text})
        data = _request("Google", lambda: _get("https://translate.googleapis.com/translate_a/single?" + query),
                        GOOGLE_TIP)
        try:
            out.append("".join(part[0] for part in data[0] or () if part and part[0]))
        except (TypeError, IndexError, KeyError):
            raise TranslateError("Google devolvió una respuesta que no se entiende." + GOOGLE_TIP) from None
    return out


def _markup(text):
    """Línea como XML/HTML para DeepL y Google Cloud: escapada, salvo <i> y <b>."""
    return convert.INLINE.sub(r"<\1\2>", html.escape(text, quote=False))


def _gcloud(texts, src, dst, api_key):
    if not api_key:
        raise TranslateError("Falta la clave de Google Cloud.")
    body = {"q": [_markup(t) for t in texts], "target": dst, "format": "html"}
    if src != "auto":
        body["source"] = src
    url = "https://translation.googleapis.com/language/translate/v2?" + urllib.parse.urlencode({"key": api_key.strip()})
    data = _request("Google Cloud", lambda: _post(url, {}, body))
    return [html.unescape(t["translatedText"]) for t in data["data"]["translations"]]


def _deepl(texts, src, dst, api_key, context):
    if not api_key:
        raise TranslateError("Falta la clave de DeepL.")
    host = "api-free.deepl.com" if api_key.strip().endswith(":fx") else "api.deepl.com"  # las gratuitas acaban en :fx
    body = {"text": [_markup(t) for t in texts],
            "target_lang": DEEPL_TARGET.get(dst, dst.upper()), "tag_handling": "xml"}
    if src != "auto":
        body["source_lang"] = src.upper()
    if context:
        body["context"] = "\n".join(original for original, _ in context)  # DeepL no cobra el contexto
    data = _request("DeepL", lambda: _post(f"https://{host}/v2/translate",
                                           {"Authorization": f"DeepL-Auth-Key {api_key.strip()}"}, body))
    return [html.unescape(t["text"]) for t in data["translations"]]


def instructions(src, dst, glossary=()):
    """Lo que se le pide a la IA: traducción literaria, diálogos naturales y las marcas intactas."""
    target = LANG_NAMES.get(dst, dst)
    rules = [
        f"You are a professional literary translator of light novels. Translate each paragraph from "
        f"{LANG_NAMES.get(src, src)} into {target}.",
        f"- Translate meaning, tone and intent, not word by word: it must read like a novel written in {target}.",
        "- Dialogue must sound natural and spoken, fitting each character's personality, age and relationships. "
        "Use the colloquial expressions a native speaker would use; avoid stiff or calqued phrasing.",
        f"- Use the punctuation and typographic conventions of {target}.",
        "- Keep <i> and <b> tags around the equivalent words. Keep every \"[Nota: ...]\" mark with the word "
        "\"Nota\" unchanged, translating only the text inside.",
        "- Keep Japanese honorifics (-san, -sama, -kun, -chan, senpai...) and character names as they are, "
        "unless the glossary says otherwise.",
        "- Do not merge, split, skip, summarize or explain paragraphs. Do not add notes or comments.",
        "- Reply only with a JSON object that maps each paragraph number to its translation, with the same "
        "numbers as the input.",
    ]
    if dst == "es":
        rules.insert(4, "- In Spanish, dialogue lines use the em dash (—) instead of quotation marks, with "
                        "dialogue tags also set off by em dashes, and opening ¿ and ¡ marks.")
    pairs = [(a, b) for a, b in glossary if a and b]
    if pairs:
        rules.append("Glossary (always use these translations):\n" + "\n".join(f"{a} = {b}" for a, b in pairs))
    return "\n".join(rules)


def _message(texts, context):
    parts = []
    if context:
        parts.append("Previous paragraphs, for context only (do not translate them):\n"
                     + "\n".join(f"{a}\n→ {b}" for a, b in context))
    parts.append("Paragraphs:\n" + json.dumps({str(n + 1): t for n, t in enumerate(texts)}, ensure_ascii=False,
                                               indent=0))
    return "\n\n".join(parts)


def _parse(reply, count):
    """Las traducciones de la respuesta de la IA, en orden; _Mismatch si falta o sobra alguna."""
    start, end = reply.find("{"), reply.rfind("}")  # la IA a veces envuelve el JSON en ```json … ``` o lo comenta
    try:
        data = json.loads(reply[start:end + 1])
    except ValueError:
        raise _Mismatch() from None
    out = [data.get(str(n + 1)) if isinstance(data, dict) else None for n in range(count)]
    if not all(isinstance(t, str) and t.strip() for t in out) or len(data) != count:
        raise _Mismatch()
    return out


def _ai(name, engine, texts, src, dst, api_key, model, base, glossary, context):
    if engine.get("key") and not api_key:
        raise TranslateError(f"Falta la clave de {name}.")
    if not model:
        raise TranslateError(f"Falta el modelo de {name}.")
    system, user = instructions(src, dst, glossary), _message(texts, context)
    base = base.rstrip("/")
    if engine["kind"] == "anthropic":
        data = _request(name, lambda: _post(f"{base}/messages",
                                            {"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                                            {"model": model, "max_tokens": 8192, "temperature": 0.3, "system": system,
                                             "messages": [{"role": "user", "content": user}]}), model=model)
        reply = "".join(part.get("text", "") for part in data.get("content", ()))
    else:
        data = _request(name, lambda: _post(f"{base}/chat/completions",
                                            {"Authorization": f"Bearer {api_key}"} if api_key else {},
                                            {"model": model, "temperature": 0.3,
                                             "messages": [{"role": "system", "content": system},
                                                          {"role": "user", "content": user}]}), model=model)
        reply = data["choices"][0]["message"]["content"] or ""
    return _parse(reply, len(texts))


def translate(name, texts, src, dst, api_key="", model="", base="", glossary=(), context=()):
    """Traduce texts (un lote de batches) con el motor name; devuelve una traducción por texto, en orden.
    context: [(original, traducción)] de los párrafos anteriores. TranslateError si no se puede."""
    engine = ENGINES[name]
    if engine["kind"] == "google":
        out = _google(texts, src, dst)
    elif engine["kind"] == "gcloud":
        out = _gcloud(texts, src, dst, api_key)
    elif engine["kind"] == "deepl":
        out = _deepl(texts, src, dst, api_key, context)
    else:
        model, base = model or engine.get("model", ""), base or engine["base"]

        def run(part, before):
            try:
                return _ai(name, engine, part, src, dst, api_key, model, base, glossary, before)
            except _Mismatch:
                if len(part) == 1:
                    raise TranslateError(f"{name} no devolvió la traducción de un párrafo. Prueba otro modelo.") \
                        from None
                half = len(part) // 2  # juntó o saltó párrafos: con menos de golpe suele cuadrar
                first = run(part[:half], before)
                return first + run(part[half:], (list(before) + list(zip(part[:half], first)))[-CONTEXT:])

        out = run(list(texts), list(context)[-CONTEXT:])
    return [_fix(source, t) for source, t in zip(texts, out)]


def models(name, api_key="", base=""):
    """Modelos que la API del motor ofrece hoy con esa clave, ordenados: los de ENGINES envejecen."""
    engine = ENGINES[name]
    base = (base or engine.get("base", "")).rstrip("/")
    if engine["kind"] == "anthropic":
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    else:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    data = _request(name, lambda: _get(f"{base}/models", headers))
    try:
        return sorted({str(m["id"]).removeprefix("models/") for m in data["data"] if m.get("id")})
    except (TypeError, KeyError, AttributeError):
        raise TranslateError(f"{name} devolvió una lista de modelos que no se entiende.") from None


# Claves API cifradas con DPAPI de Windows: solo este usuario en este equipo las puede leer
class _Blob(ctypes.Structure):
    _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data, protect):
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    buffer = ctypes.create_string_buffer(data, len(data))
    source, out = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), _Blob()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    if not fn(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError("DPAPI")
    try:
        return ctypes.string_at(out.data, out.size)
    finally:
        kernel32.LocalFree(ctypes.cast(out.data, ctypes.c_void_p))


def protect(text):
    """Clave API cifrada, en base64, para guardarla en las preferencias."""
    return base64.b64encode(_dpapi(text.encode("utf-8"), True)).decode("ascii") if text else ""


def unprotect(data):
    """La clave guardada con protect; "" si no se puede leer (otro usuario u otro equipo)."""
    try:
        return _dpapi(base64.b64decode(data), False).decode("utf-8") if data else ""
    except (OSError, ValueError):
        return ""
