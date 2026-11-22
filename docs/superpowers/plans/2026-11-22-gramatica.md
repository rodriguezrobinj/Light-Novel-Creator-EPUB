# Gramática con LanguageTool: plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Avisos de gramática de LanguageTool (local o remoto) en el Informe de calidad, con arreglo en un clic.

**Architecture:** `grammar.py` es el motor, sin UI.
- Hace HTTP a `/v2/check`.
- Devuelve cada fallo a su párrafo, con posiciones UTF-16 de Java pasadas a índices de Python.
- Guarda una caché JSON por libro, lote a lote, desde el proceso de trabajo.
- Convierte los resultados en `convert.Issue`.

`app.py` arranca el servidor local una vez por sesión, ejecuta `grammar.review` con `self.run` y suma
`grammar_issues()` en `quality_report()`.

**Tech Stack:** Python 3.14, solo la biblioteca estándar (`urllib`, `subprocess`, `zipfile`), LanguageTool 6.6 con Java 17 o superior, PySide6.

**Spec:** `docs/superpowers/specs/2026-11-22-gramatica-design.md`

## Global Constraints

- LanguageTool 6.6. URL `https://languagetool.org/download/LanguageTool-6.6.zip`, SHA256 `53600506b399bb5ffe1e4c8dec794fd378212f14aaf38ccef9b6f89314d11631`.
- API pública `https://api.languagetool.org`, con lotes de 18 000 letras como máximo.
- Ningún texto visible en inglés. Comentarios en español, breves, que digan el porqué.
- Sin dependencias nuevas.
- Nada se escribe junto a los PDF del usuario en los tests: usar `TMP`.
- `GrammarError` hereda de `convert.BookError`: el proceso de trabajo lo registra como aviso, no como fallo.

## Review Focus

- Texto con caracteres fuera del BMP (emoji, kanji raros): LanguageTool da posiciones en UTF-16. El fallo debe caer en las letras correctas. Lo pinza un test de la tarea 1.
- Fragmento erróneo repetido en el párrafo o partido por `<i>`: nada de arreglo automático, solo aviso manual. Lo pinza un test de la tarea 2.
- Corte a mitad (429 tras los reintentos, red caída): los lotes ya revisados quedan en la caché. Lo pinza un test de la tarea 3.
- Caché de otra versión de reglas o de otro idioma: se descarta y no da avisos falsos. Lo pinza un test de la tarea 3.
- Servidor local que muere al arrancar (Java viejo): mensaje «Java 17» en vez de esperar 60 s. Lo pinza un test de la tarea 3.

---

### Task 1: `grammar.check` (lotes, posiciones, reintentos)

**Files:** Create `grammar.py`. Test: `test_convert.py`.

**Produces:**
- `grammar.key(text) -> str`
- `grammar.batches(texts, limit) -> [(first, end)]`
- `grammar.check(texts, base, lang, user="", api_key="", limit=50_000, progress=None) -> [[fallo]]`
- `grammar.GrammarError`
- Cada fallo es la lista `[start, length, message, replacements, rule_id, category_id]`, con índices de Python dentro del texto del párrafo.

- [ ] Tests con un servidor falso (`_fake_lt(replies)`: `http.server` en un hilo, que guarda los POST recibidos):
  - Dos párrafos en un lote: el fallo del segundo cae en sus letras.
  - Texto con «😀» antes del fallo: posición correcta.
  - Un fallo que cruza dos párrafos se descarta.
  - `disabledRules`, `username` y `apiKey` llegan en el POST.
  - 429 con `Retry-After: 0` se reintenta.
  - 401 da `GrammarError` «clave».
- [ ] Implementar `grammar.py` (código en el commit de la tarea).
- [ ] Ejecutar `.venv/Scripts/python.exe test_convert.py --paralelo 1` filtrando con `-k grammar` si existe; si no, toda la suite.
- [ ] Commit `feat: motor de gramática con LanguageTool`.

### Task 2: `grammar.issues`

**Consumes:** `check`. **Produces:** `grammar.issues(blocks, found) -> [convert.Issue]`, `grammar.KINDS`.

- [ ] Tests:
  - Arreglo seguro: `edit` cambia solo el fragmento, y `_issue_span` lo subraya.
  - Fragmento repetido: `edit` es None y la solución dice «Editar texto».
  - Fragmento partido por `<i>`: `edit` es None.
  - Categoría `PUNCTUATION`: tipo `grammar-style`, gravedad Menor.
  - Párrafo cambiado (otra clave): sin avisos.
- [ ] Implementar.
- [ ] Commit.

### Task 3: caché, `review` y servidor local

**Produces:**
- `grammar.load(path, lang) -> dict`: `found` vacío si `rules` o `lang` no coinciden.
- `grammar.review(path, base, texts, lang, user, api_key, limit, wait, log, progress=None) -> (revisados, fallos)`.
- `grammar.download(folder, progress=None) -> str`, `grammar.installed(folder) -> bool`.
- `grammar.start(folder, java) -> (proc, base, log)`, `grammar.ready(base, seconds, log)`.

- [ ] Tests:
  - `review` manda solo los párrafos sin revisar y guarda cada lote (un 401 en el segundo lote deja el primero guardado).
  - Caché con otras reglas se descarta.
  - `ready` con un log que contiene `UnsupportedClassVersionError` falla al momento con «Java 17».
  - `download`: un zip falso con otro SHA256 se rechaza; con el SHA256 correcto se descomprime y reemplaza la carpeta entera.
- [ ] Implementar.
- [ ] Commit.

### Task 4: ventana

**Files:** `app.py`, `options.py`, `test_convert.py`.

- [ ] Ajustes: `"grammar": {"engine": "local", "url": "", "user": "", "key": ""}`, con la clave cifrada (`translate.protect`).
- [ ] `options.py`: opción «Revisión de gramática» en Ortografía (motor, URL, usuario, clave).
- [ ] `App.grammar_path()`, `App.grammar_issues()` (memo por mtime + `report_key`), suma en `quality_report()`. `quality_kinds()` añade los dos tipos.
- [ ] `App.check_grammar(parent)`:
  - Elige base, límite y clave según el motor.
  - Motor local: Java (`shutil.which`), descarga con pregunta y arranque (`self.lt_server`).
  - Ejecuta `self.run(grammar.review, …, report=True, busy="Revisando gramática…")`, y al terminar escribe el estado y rellena el informe.
- [ ] `close()` termina `self.lt_server`.
- [ ] Botón «Revisar gramática» en el informe, junto a «Opciones del informe…».
- [ ] Test de ventana:
  - Motor «Servidor propio» apuntando al servidor falso; `window.run` ejecuta en línea.
  - Los avisos salen en `quality_report()` y la caché existe.
  - Tras `edit_section` del párrafo, el aviso desaparece.
  - Sin Java y con motor local: mensaje de Java y nada se ejecuta.
- [ ] Commit.

### Task 5: verificación real

- [ ] `/probar-novela` con LanguageTool local en dos libros: contar avisos por regla. Desactivar las reglas con más del 30 % de falsos en la muestra.
- [ ] Suite completa, `/verificar-ventana` (informe y Opciones), `/build-exe`.
- [ ] Commit y merge local (preaprobados).
