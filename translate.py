"""Traducción automática del libro, párrafo a párrafo, sin interfaz: Google (gratis), DeepL o una IA."""
import base64
import collections
import difflib
import ctypes
import hashlib
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import convert
import registro

# Nombre visible -> tipo de API, URL base y modelo por defecto (los modelos se pueden cambiar en la ventana)
ENGINES = {
    "Google (gratis, sin clave)": {"kind": "google"},
    "Google Cloud (clave)": {"kind": "gcloud", "key": True},  # la API oficial: no bloquea, 500.000 caracteres gratis
    "DeepL": {"kind": "deepl", "key": True},
    "OpenAI (ChatGPT)": {"kind": "openai", "base": "https://api.openai.com/v1", "model": "gpt-4.1-mini", "key": True},
    # «deepseek-chat» ya no sale en la lista de modelos de DeepSeek (noviembre de 2026): flash y v4-pro
    "DeepSeek": {"kind": "openai", "base": "https://api.deepseek.com", "model": "deepseek-flash", "key": True},
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
LIMITS = {"google": (25, 4500), "gcloud": (100, 25000), "deepl": (50, 30000), "openai": (40, 9000), "anthropic": (40, 9000)}  # párrafos, letras
CONTEXT = 5  # párrafos anteriores que ve la IA: el tono y quién habla no se pierden entre lotes
CHAPTER = 60000  # letras del capítulo original que la IA puede ver entero; uno más largo va sin él
RETRIES = 3
AI = ("openai", "anthropic")
CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯]")  # kana, hanzi y hangul
VARIANTS = {"": "Sin preferencia", "es-419": "Latinoamérica (neutro)", "es-ES": "España"}
LETTER = re.compile(r"[^\W\d_]")
TAG = re.compile(r"<\s*(/?)\s*([ibIB])\s*>")  # Google a veces devuelve "< i>" o "</ I>"
NOTE_WORD = re.compile(r"\[[^\W\d_]{2,12}:\s*")  # "[Note: " de vuelta a "[Nota: ", la marca del editor
# Icono del texto (convert.ICON) mientras se traduce: su PNG en base64 son miles de letras que se pagan y la IA rompe
ICON_MARK = re.compile(r"⟦(\d+)⟧")


class TranslateError(Exception):
    """Fallo que la ventana muestra tal cual: el mensaje va en español."""
    code, detail = 0, ""  # del error HTTP: _chat mira si la API rechazó un parámetro opcional


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


def _form(url, fields):
    """POST de formulario: el texto de Google gratis no cabe siempre en la URL de un GET."""
    request = urllib.request.Request(url, urllib.parse.urlencode(fields).encode(), {"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def key(text):
    """Clave de un párrafo original: la traducción se encuentra aunque el párrafo cambie de sitio."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _split(line, kind):
    """(prefijo, texto) de una línea de section_text: "# " de un título, "<center>" de un párrafo centrado,
    "<marco>" de uno en recuadro, "<derecha>" de uno a la derecha. El prefijo no se traduce ni cuenta para la clave."""
    prefix, text = convert.split_prefix(line)
    return (prefix, text) if (prefix == "# ") == (kind == "heading") else ("", line)


def _hide_icons(text):
    """Los iconos del texto como «⟦1⟧», «⟦2⟧»… en su orden: es lo que se traduce y lo que da la clave."""
    n = iter(range(1, 1000))
    return convert.ICON_TAG.sub(lambda _: f"⟦{next(n)}⟧", text) if "icono" in text else text


def _show_icons(source, text):
    """La traducción text con los iconos de source donde quedaron sus marcas; los que el motor quitó, al final
    del párrafo: mejor fuera de sitio que perdidos."""
    icons, used = convert.ICON_TAG.findall(source), set()

    def put(m):
        i = int(m[1]) - 1
        if i < len(icons) and i not in used:
            used.add(i)
            return icons[i]
        return ""

    text = ICON_MARK.sub(put, text)
    return " ".join([text] + [icon for i, icon in enumerate(icons) if i not in used])


def units(book, start, end):
    """Párrafos y títulos de los bloques start..end-1 que tienen letras: [(bloque, prefijo, texto)]."""
    out = []
    for k in range(start, end):
        b = book.blocks[k]
        if b.kind in ("p", "heading"):
            prefix, text = _split(convert._line(b, k), b.kind)
            if LETTER.search(text):
                out.append((k, prefix, _hide_icons(text)))
    return out


def translated_section(book, start, end, done):
    """Texto de section_text de start..end-1 con cada párrafo traducido si su clave está en done."""
    lines = []
    for k in range(start, end):
        b, line = book.blocks[k], convert._line(book.blocks[k], k)
        if b.kind in ("p", "heading"):
            prefix, text = _split(line, b.kind)
            new = " ".join(str(done.get(key(_hide_icons(text))) or "").split())  # un salto de línea partiría el párrafo
            if new:
                line = prefix + _show_icons(text, new)
        lines.append(line)
    return "\n\n".join(lines)


def batches(texts, kind):
    """Lotes de índices de texts que caben en una petición del tipo de motor."""
    most, letters = LIMITS[kind]
    out, size = [], 0
    for n, text in enumerate(texts):
        # Una letra japonesa o china da unas tres en español: la respuesta de la IA no debe cortarse a medias
        weight = len(text) + (2 * len(CJK.findall(text)) if kind in AI else 0)
        if out and len(out[-1]) < most and size + weight <= letters:
            out[-1].append(n)
            size += weight
        else:
            out.append([n])
            size = weight
    return out


def doubts(source, text, dst, glossary=()):
    """Por qué una traducción merece otro vistazo, en frases cortas; [] si no se ve nada raro. Solo señales claras:
    cada una cuesta volver a pedir el párrafo a la IA."""
    out, plain = [], " ".join(text.split())
    if plain == " ".join(source.split()) and len(plain.split()) >= 4:
        out.append("quedó sin traducir")
    if dst not in ("ja", "zh", "ko"):
        if CJK.search(text):
            out.append("quedan caracteres del idioma original")
        ratio, asian = len(text) / max(1, len(source)), len(CJK.findall(source)) > len(source) / 3
        if len(source) >= (30 if asian else 80) and not (0.9 if asian else 0.5) <= ratio <= (8 if asian else 2.2):
            out.append("es mucho más " + ("corta" if ratio < 1 else "larga") + " que el original: falta o sobra texto")
    opened, closed = len(re.findall(r"<[ib]>", text)), len(re.findall(r"</[ib]>", text))
    if opened != closed or (not opened and re.search(r"<[ib]>", source)):
        out.append("las cursivas o negritas (<i>, <b>) del original se perdieron o no se cierran")
    if source.count("[Nota: ") != text.count("[Nota: "):
        out.append("las marcas [Nota: …] no coinciden con las del original")
    if dst == "es":
        if plain[:1] in ('"', "“", "«"):  # Making Magic: con el original en «», un capítulo entero salió con comillas
            out.append("el diálogo va con comillas en vez de raya (—)")
        if ("?" in plain and "¿" not in plain) or ("!" in plain and "¡" not in plain):
            out.append("falta el signo de apertura (¿ o ¡)")
    low = text.lower()
    for a, b in glossary:
        if a and b and a in source and re.search(rf"(?<!\w){re.escape(a)}(?!\w)", source) and not all(
                _stem(w) in low for w in b.lower().split() if w not in ARTICLES):
            out.append(f"el glosario pide «{b}» para «{a}»")
    return out


ARTICLES = {"el", "la", "los", "las", "lo", "un", "una", "unos", "unas", "the", "a", "an", "le", "les", "il", "o", "os",
            "as", "der", "die", "das"}


def _stem(word):
    """Raíz sin género ni número: «Encantado» vale como «Encantadas», «Guugelheit» como «los Guugelheit»."""
    word = word.removesuffix("s")
    return word[:-1] if len(word) > 4 and word[-1] in "aoe" else word


WORD = re.compile(r"[^\W\d_][\w'’-]*")
KATAKANA = re.compile(r"[ァ-ヺー]{2,}")
SENTENCE_START = set('.!?…"“”«»—–:;([¿¡')  # tras esto, la mayúscula es de empezar frase, no de nombre
# Palabras que no empiezan ni acaban la forma de un término: «la Secta de la Bruja» es «Secta de la Bruja»
BORDER = ARTICLES | {"de", "del", "al", "y", "e", "en", "of", "and", "to", "in"}
LINKS = {"of", "the", "de", "no"}  # palabras en minúscula dentro de un nombre: «Cult of Diablos»
POSSESSIVE = re.compile(r"['’]s$")
DICE = 0.5  # coincidencia mínima entre un término y una forma para darla por su traducción


def _terms(source):
    """Términos de un párrafo original que merece la pena seguir: rachas de hasta 3 palabras con mayúscula que no
    empiezan frase (nombres, lugares, técnicas) y palabras en katakana."""
    plain = TAG.sub("", source)
    found, run, links, last = set(KATAKANA.findall(plain)), [], [], 0

    def close():
        if run and (len(run) > 1 or len(run[0]) >= 3):
            found.add(" ".join(run))
        run.clear()
        links.clear()

    for m in WORD.finditer(plain):
        word, before = POSSESSIVE.sub("", m.group(0)), plain[:m.start()].rstrip()
        if run and plain[last:m.start()] != " ":
            close()
        capital = word[:1].isupper() and word.replace("’", "'").split("'")[0] != "I"  # «I’m» no es un nombre
        if capital and (run or (before and before[-1] not in SENTENCE_START)) and len(run) + len(links) < 5:
            run += links + [word]  # «Cult of Diablos»: el enlace entra si lo sigue otra mayúscula
            del links[:]
        elif run and not links and word in LINKS:
            links.append(word)
        else:
            close()
        if m.group(0) != word:  # tras un posesivo empieza otra cosa: «Lalanoa’s Sword»
            close()
        last = m.end()
    close()
    return found


def _grams(text):
    """{n-grama de raíces: cómo está escrito} de una traducción, de 1 a 4 palabras, sin artículos en los bordes."""
    words, out = WORD.findall(TAG.sub("", text)), {}
    low = [w.lower() for w in words]
    for i in range(len(words)):
        if low[i] in BORDER:
            continue
        for j in range(i + 1, min(i + 4, len(words)) + 1):
            if low[j - 1] not in BORDER:
                out.setdefault(tuple(_stem(w) for w in low[i:j]), " ".join(words[i:j]))
    return out


def inconsistent(pairs, glossary=()):
    """Términos del original traducidos de varias formas: [(término, [(forma, [claves de sus párrafos])])], cada uno
    con sus formas de más a menos usada. pairs: [(clave, original, traducción)]. Sin IA: la forma de un término es
    el grupo de palabras que más coincide con él (Dice) en las traducciones de los párrafos donde sale."""
    pairs = [p for p in pairs if p[2].strip()]
    seen = collections.Counter(t for _, source, _ in pairs for t in _terms(source))
    terms = {t for t, n in seen.items() if n >= 3} | {a.strip() for a, b in glossary if a.strip() and b.strip()}
    grams = [_grams(text) for _, _, text in pairs]
    total = collections.Counter(g for found in grams for g in found)
    out = []
    for term in terms:
        longer = [t for t in set(seen) | terms if t != term and term in t]  # «Witch» no cuenta donde dice «Witch Cult»
        cut = re.compile("|".join(sorted(map(re.escape, longer), key=len, reverse=True))) if longer else None
        word = re.compile(rf"(?<!\w){re.escape(term)}(?!\w)")
        left = {n for n, (_, source, _) in enumerate(pairs)
                if term in source and word.search(cut.sub(" ", source) if cut else source)}
        if len(left) < 3:
            continue
        forms, proper = [], term[:1].isupper()
        while len(left) >= 2:
            here = collections.Counter(g for n in left for g in grams[n])
            scored = {g: 2 * n / (len(left) + total[g]) for g, n in here.items() if n >= 2}
            best = max(scored.values(), default=0)
            if best < DICE:
                break
            written = {g: next(grams[n][g] for n in left if g in grams[n]) for g, score in scored.items()
                       if score >= max(DICE, 0.8 * best)}
            # De las que casi empatan, la que más mayúsculas conserva (un nombre las lleva también traducido) y la más
            # larga: «Culto de la Bruja» antes que «Bruja», que comparten las dos formas
            gram = max(written, key=lambda g: (sum(w[:1].isupper() for w in written[g].split()) if proper else 0,
                                               len(g), scored[g]))
            mine = sorted(n for n in left if gram in grams[n])
            written = collections.Counter(grams[n][gram] for n in mine).most_common(1)[0][0]
            forms.append((written, [pairs[n][0] for n in mine]))
            left -= set(mine)
        if len(forms) >= 2:
            out.append((term, sorted(forms, key=lambda f: -len(f[1]))))
    return sorted(out, key=lambda t: (-sum(len(keys) for _, keys in t[1][1:]), t[0]))


def similar(text, sources, least=0.75):
    """(original, parecido de 0 a 1) del texto de sources que más se parece a text, o None si ninguno llega a
    least. Para reutilizar la traducción de un párrafo casi igual de otro tomo; uno corto se parece a cualquiera."""
    if len(text) < 30:
        return None
    matcher, best = difflib.SequenceMatcher(None, autojunk=False), None
    matcher.set_seq2(text)
    for source in sources:
        if source == text or not 0.6 * len(text) <= len(source) <= 1.6 * len(text):
            continue
        matcher.set_seq1(source)
        # Las dos cotas baratas primero: la comparación entera, con miles de párrafos, tardaría
        if matcher.real_quick_ratio() >= least and matcher.quick_ratio() >= least:
            ratio = matcher.ratio()
            if ratio >= least and (best is None or ratio > best[1]):
                best = source, ratio
    return best


# Avisos del informe de calidad que son de la maquetación del PDF, no del texto de una traducción
STRUCTURE = {"skipped", "lower", "long", "number", "repeated", "duplicate", "link", "end", "short", "gap"}


def quality(pairs, glossary=(), lang="es"):
    """Avisos del informe de calidad sobre las traducciones, antes de aplicarlas al libro: {clave: [(título,
    problema)]}. pairs: [(clave, traducción)]. Solo los de texto (signos sin pareja, sin traducir, erratas, calcos,
    honoríficos, nombres del glosario): los de estructura hablan del PDF. glossary: los nombres de la serie."""
    blocks = [convert.Block("p", n + 1, convert._inline(text)) for n, (_, text) in enumerate(pairs)]
    out = {}
    for issue in convert.quality_report(convert.Book("", "", b"", blocks, []), glossary=glossary, lang=lang):
        if issue.block is not None and issue.kind not in STRUCTURE:
            found = out.setdefault(pairs[issue.block][0], [])
            if (issue.title, issue.problem) not in found:
                found.append((issue.title, issue.problem))
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
                error = TranslateError(f"{name} respondió con un error {e.code}. {detail}".strip())
                error.code, error.detail = e.code, detail
                raise error from e
            reason = f"error {e.code}" + (" (demasiadas peticiones)" if e.code == 429 else "")
            wait = str(e.headers.get("Retry-After") or "") if e.headers else ""
            if wait.isdigit() and attempt < RETRIES:  # el servidor dice cuánto: ni antes (otro 429) ni mucho después
                _sleep(min(int(wait), 60))
                continue
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            reason = str(getattr(e, "reason", e) or type(e).__name__)
        except ValueError as e:  # una página de aviso o de captcha en vez de la respuesta
            raise TranslateError(f"{name} devolvió una respuesta que no se entiende.{tip}") from e
        if attempt < RETRIES:
            _sleep(2 ** attempt * 2)
    raise TranslateError(f"No se pudo traducir con {name}: {reason}. Revisa la conexión y vuelve a "
                         f"intentarlo.{tip}")


def _google(texts, src, dst):
    query = urllib.parse.urlencode({"client": "gtx", "sl": src, "tl": dst, "dt": "t"})

    def ask(text):
        data = _request("Google", lambda: _form("https://translate.googleapis.com/translate_a/single?" + query,
                                                {"q": text}), GOOGLE_TIP)
        try:
            return "".join(part[0] for part in data[0] or () if part and part[0])
        except (TypeError, IndexError, KeyError):
            raise TranslateError("Google devolvió una respuesta que no se entiende." + GOOGLE_TIP) from None

    if len(texts) > 1:  # un párrafo por línea en una sola petición: muchas menos, y Google corta menos
        lines = ask("\n".join(" ".join(t.split()) for t in texts)).split("\n")
        if len(lines) == len(texts) and all(line.strip() for line in lines):
            return lines
    return [ask(text) for text in texts]  # juntó o perdió líneas: de uno en uno, como antes


def _markup(text):
    """Línea como XML/HTML para DeepL y Google Cloud: escapada, salvo <i> y <b>."""
    return convert.INLINE.sub(r"<\1\2>", html.escape(text, quote=False))


def _gcloud(texts, src, dst, api_key):
    if not api_key:
        raise TranslateError("Falta la clave de Google Cloud.")
    body = {"q": [_markup(t) for t in texts], "target": dst, "format": "html"}
    if src != "auto":
        body["source"] = src
    # la clave en una cabecera y no en la URL: las URL acaban en registros de proxies y de errores
    url = "https://translation.googleapis.com/language/translate/v2"
    data = _request("Google Cloud", lambda: _post(url, {"X-Goog-Api-Key": api_key.strip()}, body))
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


def instructions(src, dst, glossary=(), style=None):
    """Lo que se le pide a la IA: traducción literaria, diálogos naturales y las marcas intactas. style: lo elegido
    en «Glosario y estilo» (variant, honorifics, notes, examples [(original, traducción aprobada)])."""
    style = style or {}
    target = LANG_NAMES.get(dst, dst)
    rules = [
        f"You are a professional literary translator of light novels. Translate each paragraph from "
        f"{LANG_NAMES.get(src, src)} into {target}.",
        f"- Translate meaning, tone and intent, not word by word: it must read like a novel written in {target}.",
        # Making Magic con DeepSeek: «los larguísimos anales de este mundo alterno» no lo entiende quien lee novelas ligeras
        f"- Use the everyday words a young reader of light novels in {target} knows. Avoid rare, bookish or archaic "
        f"words: annals, alternate, verdant become history, other, green"
        + (" («la larga, larguísima historia de este otro mundo», not «los larguísimos anales de este mundo alterno»)"
           if dst == "es" else "")
        + ". Keep a formal word only where the original is deliberately formal (a goddess, a king, an old text).",
        "- Dialogue must sound natural and spoken, fitting each character's personality, age and relationships. "
        "Use the colloquial expressions a native speaker would use; avoid stiff or calqued phrasing.",
        "- Work out who is speaking and each character's gender from the context and the character notes. Keep "
        f"gender agreement and the way each pair of characters address each other (formal or informal) consistent "
        f"in {target}.",
        "- Adapt interjections, onomatopoeia, idioms and set phrases to what a native reader expects; never leave "
        "them half translated.",
        f"- Use the punctuation and typographic conventions of {target}.",
    ]
    if dst == "es":
        rules.append("- In Spanish, dialogue lines use the em dash (—) instead of quotation marks, even when the "
                     "source marks them with «», \"\" or “”, with dialogue tags also set off by em dashes, and opening "
                     "¿ and ¡ marks. After ! or ? the narration that follows opens with its own dash: «—¡Ven! —Y "
                     "se fue.».")
        if style.get("variant") == "es-ES":
            rules.append("- Write in European Spanish (Spain): «vosotros» for the informal plural and the vocabulary "
                         "used in Spain.")
        elif style.get("variant") == "es-419":
            rules.append("- Write in neutral Latin American Spanish: «ustedes», never «vosotros», and vocabulary "
                         "understood across Latin America; avoid words used only in Spain or in a single country.")
    rules.append("- Keep <i> and <b> tags around the equivalent words. Keep every \"[Nota: ...]\" mark with the word "
                 "\"Nota\" unchanged, translating only the text inside. Keep every ⟦1⟧, ⟦2⟧... mark (a small "
                 "picture inside the sentence) unchanged, next to the words it goes with.")
    if style.get("honorifics", True):
        rules.append("- Keep Japanese honorifics (-san, -sama, -kun, -chan, senpai...) and character names as they "
                     "are, unless the glossary says otherwise.")
    else:
        rules.append(f"- Do not keep Japanese honorifics (-san, -sama, -kun, -chan, senpai...): drop them or show the "
                     f"relationship the way {target} does (forms of address, titles, tone). Keep character names as "
                     f"they are, unless the glossary says otherwise.")
    rules += ["- Do not merge, split, skip, summarize or explain paragraphs. Do not add notes or comments.",
              "- Reply only with a JSON object that maps each paragraph number to its translation, with the same "
              "numbers as the input."]
    pairs = [(a, b) for a, b in glossary if a and b]
    if pairs:
        rules.append("Glossary (always use these translations):\n" + "\n".join(f"{a} = {b}" for a, b in pairs))
    if str(style.get("notes") or "").strip():
        rules.append("Characters and style notes from the editor (always follow them):\n" + style["notes"].strip())
    if style.get("examples"):
        rules.append("Translations the editor approved, as a reference for the style wanted:\n"
                     + "\n".join(f"{a}\n→ {b}" for a, b in style["examples"]))
    return "\n".join(rules)


def _message(texts, context, hints=None):
    """hints: {número de párrafo: [fallos de la traducción anterior]}, en la segunda pasada."""
    parts = []
    if context:
        parts.append("Previous paragraphs, for context only (do not translate them):\n"
                     + "\n".join(f"{a}\n→ {b}" for a, b in context))
    if hints:
        parts.append("Your previous translation of these paragraphs had problems (described in Spanish). Translate "
                     "them again without them:\n" + "\n".join(f"{n}: {'; '.join(found)}" for n, found in hints.items()))
    parts.append("Paragraphs:\n" + json.dumps({str(n + 1): t for n, t in enumerate(texts)}, ensure_ascii=False,
                                               indent=0))
    return "\n\n".join(parts)


def _json(reply):
    """El objeto JSON de la respuesta de la IA, que a veces lo envuelve en ```json … ``` o lo comenta."""
    try:
        data = json.loads(reply[reply.find("{"):reply.rfind("}") + 1])
    except ValueError:
        raise _Mismatch() from None
    if not isinstance(data, dict):
        raise _Mismatch()
    return data


def _parse(reply, count):
    """Las traducciones de la respuesta de la IA, en orden; _Mismatch si falta o sobra alguna."""
    data = _json(reply)
    out = [data.get(str(n + 1)) for n in range(count)]
    if not all(isinstance(t, str) and t.strip() for t in out) or len(data) != count:
        raise _Mismatch()
    return out


_DROPPED = {}  # (URL, modelo) -> parámetros opcionales que esa API rechazó: no se le vuelven a mandar


def _chat(name, engine, system, user, api_key, model, base, cached=""):
    """Texto con que la IA responde. cached: final de la instrucción que se repite de lote en lote (el capítulo
    original): Claude lo guarda en caché si se le marca; OpenAI, DeepSeek y Gemini guardan solos el principio."""
    if engine.get("key") and not api_key:
        raise TranslateError(f"Falta la clave de {name}.")
    if not model:
        raise TranslateError(f"Falta el modelo de {name}.")
    base = base.rstrip("/")
    dropped = _DROPPED.setdefault((base, model), set())
    while True:
        # JSON obligado: menos respuestas con un párrafo de más o de menos, que cuestan partir el lote y repetirlo
        extra = {k: v for k, v in (("temperature", 0.3), ("response_format", {"type": "json_object"}))
                 if k not in dropped and (k, engine["kind"]) != ("response_format", "anthropic")}
        try:
            if engine["kind"] == "anthropic":
                whole = [{"type": "text", "text": system},
                         {"type": "text", "text": cached, "cache_control": {"type": "ephemeral"}}] if cached else system
                data = _request(name, lambda: _post(f"{base}/messages",
                                                    {"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                                                    {"model": model, "max_tokens": 8192, "system": whole,
                                                     "messages": [{"role": "user", "content": user}]} | extra),
                                model=model)
                return "".join(part.get("text", "") for part in data.get("content", ()))
            data = _request(name, lambda: _post(f"{base}/chat/completions",
                                                {"Authorization": f"Bearer {api_key}"} if api_key else {},
                                                {"model": model,
                                                 "messages": [{"role": "system",
                                                               "content": system + ("\n\n" + cached if cached else "")},
                                                              {"role": "user", "content": user}]} | extra),
                            model=model)
            return data["choices"][0]["message"]["content"] or ""
        except TranslateError as e:
            # Modelos que no admiten temperatura (los de razonamiento) o servidores sin modo JSON: sin eso vale igual
            bad = next((k for k in extra if e.code == 400 and k in e.detail), None)
            if not bad:
                raise
            dropped.add(bad)
        except (KeyError, IndexError, TypeError, AttributeError):
            raise TranslateError(f"{name} devolvió una respuesta que no se entiende.") from None


CHAPTER_INTRO = ("The whole chapter in the original language, only as context (who speaks, each character's gender, "
                 "the tone, what comes next). Translate only the paragraphs you are asked for:\n")


@registro.timed
def translate(name, texts, src, dst, api_key="", model="", base="", glossary=(), context=(), style=None):
    """Traduce texts (un lote de batches) con el motor name; devuelve una traducción por texto, en orden.
    context: [(original, traducción)] de los párrafos anteriores. style: lo de instructions más "chapter", el
    capítulo original entero (solo la IA lo usa). TranslateError si no se puede."""
    engine = ENGINES[name]
    if engine["kind"] == "google":
        out = _google(texts, src, dst)
    elif engine["kind"] == "gcloud":
        out = _gcloud(texts, src, dst, api_key)
    elif engine["kind"] == "deepl":
        out = _deepl(texts, src, dst, api_key, context)
    else:
        model, base = model or engine.get("model", ""), base or engine["base"]
        system, chapter = instructions(src, dst, glossary, style), str((style or {}).get("chapter") or "")
        # Un capítulo que cabe en el lote ya va entero en él
        cached = CHAPTER_INTRO + chapter if sum(map(len, texts)) + 200 < len(chapter) <= CHAPTER else ""

        def ask(part, before, hints=None):
            return _parse(_chat(name, engine, system, _message(part, before, hints), api_key, model, base, cached),
                          len(part))

        def run(part, before):
            try:
                return ask(part, before)
            except _Mismatch:
                if len(part) == 1:
                    raise TranslateError(f"{name} no devolvió la traducción de un párrafo. Prueba otro modelo.") \
                        from None
                half = len(part) // 2  # juntó o saltó párrafos: con menos de golpe suele cuadrar
                first = run(part[:half], before)
                return first + run(part[half:], (list(before) + list(zip(part[:half], first)))[-CONTEXT:])

        out = run(list(texts), list(context)[-CONTEXT:])
        flagged = {n: found for n, (source, t) in enumerate(zip(texts, out))
                   if (found := doubts(source, _fix(source, t), dst, glossary))}
        if flagged:  # segunda pasada: solo los dudosos, con su motivo; una petición más por lote como mucho
            order = sorted(flagged)
            try:
                again = ask([texts[n] for n in order], list(context)[-CONTEXT:],
                            {i + 1: flagged[n] for i, n in enumerate(order)})
            except (_Mismatch, TranslateError):
                again = []  # la primera traducción vale
            for n, new in zip(order, again):
                if len(doubts(texts[n], _fix(texts[n], new), dst, glossary)) < len(flagged[n]):
                    out[n] = new
    return [_fix(source, t) for source, t in zip(texts, out)]


REVIEW_CHAPTER = "The whole chapter in the original language, only as context (who speaks, each character's gender):\n"


def review(name, pairs, src, dst, api_key="", model="", base="", glossary=(), style=None):
    """La IA hace de editora: compara cada traducción con su original y dice solo lo que está mal. pairs: [(original,
    traducción)] (un lote). Devuelve, por par: None si está bien, {"problem" (en español), "fix" (el párrafo
    corregido)} si no, y False si no se pudo revisar (la respuesta no se entendía). style: "notes" y "chapter",
    como en translate. Ve lo que las reglas de doubts no: sentido cambiado, frases omitidas, quién habla."""
    engine = ENGINES[name]
    if engine["kind"] not in AI:
        raise TranslateError("Solo una IA puede revisar la traducción: elige una en «Motor».")
    style, target = style or {}, LANG_NAMES.get(dst, dst)
    rules = [
        f"You are the editor of a light novel translated from {LANG_NAMES.get(src, src)} into {target}. Each numbered "
        f"paragraph comes with its source and its translation. Compare them and report only real errors:",
        "- the meaning changed, or a sentence or a detail was left out or added;",
        "- wrong speaker, gender or number, or a form of address (formal or informal) that does not fit the "
        "characters;",
        "- a glossary term that was not used;",
        f"- a literal calque or a phrase that does not sound natural in {target};",
        f"- a rare, bookish or archaic word where a young reader of light novels would expect an everyday one "
        f"(«anales», «alterno»): this is an error, not a style preference.",
        "Do not report style preferences, and do not rewrite a correct translation just to word it differently.",
        "Reply only with a JSON object with the paragraphs that have errors: map each one's number to an object with "
        f"\"problem\" (one short sentence in Spanish saying what is wrong) and \"fix\" (the whole corrected paragraph "
        f"in {target}, keeping its <i> and <b> tags and its \"[Nota: ...]\" marks). If every paragraph is fine, reply "
        "{}.",
    ]
    terms = [(a, b) for a, b in glossary if a and b]
    if terms:
        rules.append("Glossary (these translations are mandatory):\n" + "\n".join(f"{a} = {b}" for a, b in terms))
    if str(style.get("notes") or "").strip():
        rules.append("Characters and style notes from the editor:\n" + style["notes"].strip())
    system, chapter = "\n".join(rules), str(style.get("chapter") or "")
    model, base = model or engine.get("model", ""), base or engine["base"]
    cached = REVIEW_CHAPTER + chapter if sum(len(a) for a, _ in pairs) + 200 < len(chapter) <= CHAPTER else ""

    def ask(part):
        user = "Paragraphs:\n" + json.dumps({str(n + 1): {"source": a, "translation": b}
                                             for n, (a, b) in enumerate(part)}, ensure_ascii=False, indent=0)
        data, out = _json(_chat(name, engine, system, user, api_key, model, base, cached)), [None] * len(part)
        for n, (source, text) in enumerate(part):
            found = data.get(str(n + 1))
            # Sin corrección no hay nada que ofrecer, y una igual a lo que hay no es un fallo
            if isinstance(found, dict) and isinstance(found.get("fix"), str) and found["fix"].strip():
                fix = " ".join(_fix(source, found["fix"]).split())
                if fix != " ".join(text.split()):
                    out[n] = {"problem": " ".join(str(found.get("problem") or "").split())
                              or "La IA propone otra traducción.", "fix": fix}
        return out

    def run(part):
        try:
            return ask(part)
        except _Mismatch:
            if len(part) == 1:
                return [False]
            half = len(part) // 2  # con menos de golpe suele responder bien
            return run(part[:half]) + run(part[half:])

    return run(list(pairs))


def propose(name, sample, src, dst, api_key="", model="", base="", names=(), text="", known=()):
    """Glosario y ficha de personajes que la IA saca de una muestra del libro, para que el usuario los revise:
    ([[original, traducción]], ["Nombre: género, cómo habla…"]). names: nombres propios frecuentes del tomo. text: el
    libro entero, para quitar las palabras comunes que la IA propone con mayúscula («Dress», «Twelve»). known: las
    notas que ya hay («Nombre: nota», de tomos anteriores); la IA devuelve la de uno de ellos, entera y ampliada, solo
    si la muestra cuenta algo que le falta."""
    engine = ENGINES[name]
    if engine["kind"] not in AI:
        raise TranslateError("El glosario y los personajes solo los puede proponer una IA: elige una en «Motor».")
    target = LANG_NAMES.get(dst, dst)
    system = (f"You prepare the translation of a light novel from {LANG_NAMES.get(src, src)} into {target}. Read the "
              f"sample and reply only with a JSON object with two keys:\n"
              f"\"glossary\": an object that maps each recurring term of this story in the original (character "
              f"names, places, organizations, skills, spells, items, ranks, titles, invented words; 60 at most) to "
              f"the translation to use always in {target}. Names keep their usual romanization. Forms of address and "
              f"titles (Lady, Lord, Sir, Miss, Mistress, Master) become what a reader of fantasy novels in {target} "
              f"expects, never a word-for-word calque"
              + (" (in Spanish: Lady Teto → Señorita Teto, a maid's Mistress → Ama, Sir → Señor; never «Dama»)"
                 if dst == "es" else "") + f". Never include common "
              f"words that only appear capitalized in a heading or a game status screen (Chapter, Dress, Twelve, "
              f"Killer), nor the names of the author, illustrator or translators.\n"
              f"\"characters\": an object that maps each character's name to a short note written in Spanish: "
              f"gender, approximate age or role, how they speak (formal, rough, childish...) and how they address "
              f"the others (de tú, de usted). Only what the sample shows; do not invent. Only what helps to translate "
              f"them: never plot events, fights, skills or numbers.")
    if known:
        system += ("\nSome characters already have a note from earlier volumes (given below). Include one of them in "
                   "\"characters\" only if the sample shows something important its note lacks (a new role, "
                   "relationship, way of speaking or of addressing someone); then write the whole note again, keeping "
                   "everything it already says and adding the new part.")
    user = (("Frequent names in the book: " + ", ".join(names) + "\n\n" if names else "")
            + ("Character notes already written:\n" + "\n".join(known) + "\n\n" if known else "") + "Sample:\n" + sample)
    try:
        data = _json(_chat(name, engine, system, user, api_key, model or engine.get("model", ""),
                           base or engine["base"]))
    except _Mismatch:
        raise TranslateError(f"{name} no devolvió una propuesta que se entienda. Prueba otro modelo.") from None
    both = [data.get(k) if isinstance(data.get(k), dict) else {} for k in ("glossary", "characters")]
    clean = [[(" ".join(str(a).split()), " ".join(str(b).split())) for a, b in d.items() if str(a).strip()
              and isinstance(b, str) and b.strip()] for d in both]
    lower = set(re.findall(r"(?<![^\W\d_])[a-zà-ÿ]+(?![^\W\d_])", text))
    # Una palabra sola que el libro también usa en minúscula es común: la IA ya la traduce bien según la frase
    terms = [[a, b] for a, b in clean[0] if " " in a or a.lower() not in lower or a.lower() == a]
    return terms, [f"{a}: {b}" for a, b in clean[1]]


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


# Claves API cifradas con DPAPI de Windows: solo este usuario en este equipo las puede leer.
# CRYPTPROTECT_UI_FORBIDDEN (0x01) evita que Windows abra diálogos en el proceso de trabajo o en pruebas.
_DPAPI_UI_FORBIDDEN = 0x01


class _Blob(ctypes.Structure):
    _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data, protect):
    if sys.platform != "win32":
        raise OSError("DPAPI solo está disponible en Windows.")
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    blob_ptr = ctypes.POINTER(_Blob)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    fn.argtypes = [blob_ptr, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                   ctypes.c_void_p, ctypes.c_ulong, blob_ptr]
    fn.restype = ctypes.c_int
    buffer = ctypes.create_string_buffer(data, len(data))
    source, out = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), _Blob()
    try:
        if not fn(ctypes.byref(source), None, None, None, None, _DPAPI_UI_FORBIDDEN, ctypes.byref(out)):
            raise OSError("DPAPI")
        try:
            return ctypes.string_at(out.data, out.size)
        finally:
            if out.data and out.size:
                ctypes.memset(out.data, 0, out.size)  # limpia la clave descifrada antes de liberar
            kernel32.LocalFree(ctypes.cast(out.data, ctypes.c_void_p))
    finally:
        if data:
            ctypes.memset(buffer, 0, len(data))


def protect(text):
    """Clave API cifrada, en base64, para guardarla en las preferencias."""
    return base64.b64encode(_dpapi(text.encode("utf-8"), True)).decode("ascii") if text else ""


def unprotect(data):
    """La clave guardada con protect; "" si no se puede leer (otro usuario u otro equipo)."""
    try:
        return _dpapi(base64.b64decode(data), False).decode("utf-8") if data else ""
    except (OSError, ValueError, AttributeError):
        return ""
