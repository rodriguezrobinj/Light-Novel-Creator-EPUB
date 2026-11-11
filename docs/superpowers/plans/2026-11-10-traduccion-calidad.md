# Traducción de calidad: plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dar a la ventana «Traducir» cuatro herramientas de revisión: coherencia de términos, ortografía y avisos de español, memoria de la serie y revisor con IA.

**Architecture:** La lógica sin ventana va en `translate.py` (`inconsistent`, `quality`, `similar`, `review`), probada con datos sintéticos y `_post` falso. `app.py` añade a `translate_book` los botones, estados y filtros, y dos ayudas de módulo para la memoria (`_remember_translation`, `_series_memory`). El revisor reutiliza `_translate_thread` con `job["mode"] == "review"`.

**Tech Stack:** Python 3.14, PySide6, `difflib`, Hunspell por `_spell_server` (ya existe), `convert.quality_report` (ya existe).

**Spec:** `docs/superpowers/specs/2026-11-10-traduccion-calidad-design.md`

## Global Constraints

- Texto visible en español con acentos; comentarios y docstrings en español, cortos, con el porqué; identificadores en inglés.
- `app.py` usa la API de `qtk` (`RowList`, `after`, `messagebox`); nada de trabajo pesado en el hilo de la ventana.
- Pruebas: funciones `test_*` en `test_convert.py`, ejecutadas con `.venv/Scripts/python.exe test_convert.py`. Nunca escribir junto a los PDF del usuario.
- No ejecutar la suite y el build a la vez.
- Commits: tipo en inglés, asunto y cuerpo en español, sin firma. Rama `feat/traduccion-calidad`.
- Sin dependencias nuevas.
- Ortografía y avisos solo con destino español; revisor solo con motores de `translate.AI`.

## Review Focus

1. Libro con serie vacía: la memoria no debe leer ni escribir nada (prueba en Task 4).
2. Archivo de traducción de otro tomo roto o borrado: la ventana abre igual y la ruta sale del índice (Task 4).
3. La IA revisora devuelve un `fix` sin las marcas `<i>` o con números que no existen: se arregla con `_fix` o se ignora, sin excepción (Task 5).
4. Traducción cambiada a mano mientras llega su revisión: la revisión vieja no se pone (Task 5).
5. Término contenido en otro («Witch» dentro de «Witch Cult»): no sale como incoherente por las formas del largo (Task 1).

---

### Task 1: `translate.inconsistent`

**Files:** Modify `translate.py` (tras `doubts`/`_stem`); Test `test_convert.py` (tras `test_translate_doubts_and_second_pass`).

**Interfaces:**
- Produces: `inconsistent(pairs, glossary=()) -> [(término, [(forma, [claves])])]`; `pairs = [(clave, original, traducción)]`. Solo términos con 2 formas o más; formas de más a menos párrafos; términos ordenados por párrafos en formas minoritarias, de más a menos.

- [ ] **Step 1: prueba que falla**

```python
def test_translate_inconsistent_terms():
    """Un término del original traducido de dos formas sale con cada forma y sus párrafos; un nombre que no se
    traduce, un plural o una mayúscula distinta y un término dentro de otro más largo no son incoherencias."""
    import translate

    pairs = [(f"a{n}", f"Then the Witch Cult attacked village {n}.", f"Entonces el Culto de la Bruja atacó la aldea {n}.")
             for n in range(8)]
    pairs += [(f"b{n}", f"Nobody saw the Witch Cult near gate {n}.", f"Nadie vio a la Secta de la Bruja junto al portón {n}.")
              for n in range(3)]
    pairs += [(f"c{n}", f"Later Subaru ran to house {n}.", f"Después Subaru corrió a la casa {n}.") for n in range(6)]
    pairs += [(f"d{n}", f"He feared the Witch on day {n}.", f"Temía a la Bruja el día {n}.") for n in range(4)]
    pairs += [(f"e{n}", f"All the Knights left on night {n}.", f"Todos los {'caballeros' if n % 2 else 'Caballero'} "
               f"se fueron la noche {n}.") for n in range(4)]
    found = dict(translate.inconsistent(pairs))
    assert list(found) == ["Witch Cult"], found
    forms = found["Witch Cult"]
    assert [(f, len(keys)) for f, keys in forms] == [("Culto de la Bruja", 8), ("Secta de la Bruja", 3)], forms
    assert forms[1][1] == ["b0", "b1", "b2"]
    assert translate.inconsistent(pairs[:8]) == []  # una sola forma
    # El glosario también da términos, aunque empiecen frase o vayan en minúscula
    low = [(f"g{n}", f"mana flows in river {n}.", f"El {'maná' if n < 3 else 'poder mágico'} fluye en el río {n}.")
           for n in range(6)]
    assert translate.inconsistent(low) == []
    assert [t for t, _ in translate.inconsistent(low, [["mana", "maná"]])] == ["mana"]
```

- [ ] **Step 2:** `.venv/Scripts/python.exe test_convert.py test_translate_inconsistent_terms` (o la suite si no admite nombre): FAIL, `inconsistent` no existe.

- [ ] **Step 3: implementación** en `translate.py`:
  - `_terms(source)`: quita `<i>`/`<b>`; recorre palabras (`[^\W\d_][\w'’-]*`); una racha de palabras con mayúscula separadas por un solo espacio, que no empieza frase (antes hay texto y no acaba en `.!?…"“”«»—–:;([¿¡`), da un término (máximo 3 palabras; una sola palabra, 3 letras o más). Más las secuencias de katakana `[ァ-ヺー]{2,}`.
  - Candidatos: términos de `_terms` en 3 párrafos o más, más los originales del glosario.
  - Pertenencia: el término sale como palabra entera en el original, tras quitar de él los candidatos más largos que lo contienen (Review Focus 5). Se necesitan 3 párrafos o más.
  - Formas: palabras de cada traducción en minúscula; n-gramas de 1 a 4 sin `BORDER` (artículos y `de del al y e en a of and to in`) en los bordes, normalizados con `_stem`. Cuenta global por n-grama (párrafos de todo `pairs`). En cada ronda, sobre los párrafos que quedan: Dice = 2·juntos / (quedan + total global); candidatos con Dice ≥ 0,5 y 2 párrafos o más; se elige el más largo entre los que llegan al 80 % del mejor Dice. Sus párrafos salen de «quedan»; se repite hasta no haber candidato.
  - La forma visible es la escritura más frecuente del n-grama en esos párrafos.

- [ ] **Step 4:** prueba en verde; suite entera en verde.
- [ ] **Step 5:** commit `feat: detectar términos traducidos de varias formas`.

### Task 2: botón «Coherencia…»

**Files:** Modify `app.py` (`translate_book`); Test `test_convert.py`.

**Interfaces:**
- Consumes: `translate.inconsistent`.
- Produces en `win.parts`: `"coherence"` (abre el diálogo), y en `win.coherence_box`: `{"box", "table", "forms" (QComboBox), "use"}`. `work["only"]`: conjunto de claves a la vista, o None; `show_rows` lo respeta y pulsar un filtro lo quita.

- [ ] **Step 1: prueba** `test_window_translate_coherence`: ventana con `versioned_window()`, `done` rellenado por `parts["merge"]` con traducciones hechas a mano en la prueba (dos formas de un nombre que sale 3 veces o más en la novela de prueba; si no hay tal nombre, se añaden bloques `p` al libro antes de abrir la ventana). `parts["coherence"]()`; la tabla tiene el término; se elige la forma mayoritaria y `use()`; se comprueba: `window.tr_glossary` tiene el par, las filas visibles son las de la otra forma y están seleccionadas, y pulsar «Todos» vuelve a mostrar todas. Sin incoherencias: `messagebox` informativo (capturado por `no_modal_dialogs`).
- [ ] **Step 2:** falla (`KeyError: 'coherence'`).
- [ ] **Step 3:** implementación: botón «Coherencia…» en la fila de botones; `coherence()` monta `pairs` con todo el libro (`translate.units(self.book, 0, len(blocks))` y `done`), diálogo modal con `qtk.RowList(("term", "forms"))`, combo de formas del término elegido y «Usar esta forma» (`primary`). Al usarla: quita del glosario el par anterior del término, añade `[término, forma]`, `self.save_review()`, `refresh(set(done))`, `work["only"]` = claves de las otras formas, selecciona esas filas, estado: «N párrafos con otra forma: pulsa «Retraducir selección» o corrígelos a mano. Un filtro vuelve a mostrar todos.»
- [ ] **Step 4:** verde. **Step 5:** commit `feat: «Coherencia…» en Traducir`.

### Task 3: español dentro de «Traducir»

**Files:** Modify `translate.py` (`quality`), `app.py` (`_Cards`, `translate_book`), `theme.py` no cambia; Test `test_convert.py`.

**Interfaces:**
- Produces: `translate.quality(pairs, glossary=(), lang="es") -> {clave: [(título, problema)]}`; `pairs = [(clave, traducción)]`. Tipos excluidos: `STRUCTURE = {"skipped", "lower", "long", "number", "repeated", "duplicate", "end", "link"}`.
- `WARN_ROLE = Qt.ItemDataRole.UserRole + 8`: número de avisos de la fila (columna del estado); `_Cards` pinta «⚠ N» en su línea.
- `win.parts`: `"warnings"` (dict clave → lista de textos), `"check"` (recalcula ya, síncrono, para pruebas), `"look"` (el `editor.Look` del cuadro), `"spell"` (dict con `unknown`).

- [ ] **Step 1: pruebas**

```python
def test_translate_quality_of_translations():
    """Los avisos de texto del informe de calidad, sobre las traducciones: un signo sin pareja o inglés sin traducir
    salen con la clave de su párrafo; los de estructura (número suelto, línea repetida, párrafo partido) no."""
    import translate

    pairs = [("a", "—¿Adónde vas?"), ("b", "Abrió la puerta (y no la cerró."), ("c", "1234"), ("d", "empieza en minúscula."),
             ("e", "He walked to the door and then he was gone with the wind."), ("f", "Cartel"), ("g", "Cartel"), ("h", "Cartel")]
    found = translate.quality(pairs)
    assert set(found) == {"b", "e"}, found
    assert found["b"][0][0] == "Signo sin pareja" and found["e"][0][0] == "Texto sin traducir", found
    assert translate.quality(pairs, lang="en") .get("e") is None
```

  y `test_window_translate_spanish_warnings`: destino `es`, `merge` con una traducción con signo sin pareja; `parts["check"]()`; la fila tiene `WARN_ROLE == 1`, el filtro «Con avisos 1» la deja sola, al elegirla `parts["doubt"]` contiene «Signo sin pareja». Con `parts["spell"]["unknown"] = {"palbra"}` y una traducción con «palbra», `check()` añade «No está en el diccionario: «palbra»» y `parts["look"].unknown == {"palbra"}`. Con destino `en`, sin avisos.
- [ ] **Step 2:** fallan.
- [ ] **Step 3:** `translate.quality`: un `convert.Block("p", n + 1, html)` por par (texto escapado, `<i>`/`<b>` restaurados), `convert.Book("", "", b"", blocks, [])`, `convert.quality_report(book, glossary=glossary, lang=lang)`; agrupa por `issue.block` los que no son de `STRUCTURE`.
- [ ] **Step 4:** ventana:
  - `check(later=True)`: si destino no es español, vacía avisos. Si no, en un hilo `translate.quality` de todas las traducciones de las filas; al llegar (`after` + cola) une con las palabras desconocidas (`convert.spell_words(text)` ∩ `spell["unknown"]`), pone `WARN_ROLE`, cuenta el filtro. `later=False` lo hace en el momento (pruebas). Se llama al final de `fill()`, al acabar una tanda (`busy(False)`), tras `keep()` con cambio y al cambiar `dst`.
  - Ortografía: `spell = {"proc": _start_spell_process() si destino es español, "unknown": set(), "checked": set(), "suggestions": {}}`; `send_words(texts)` con `convert.spell_skip(self.words)` y `spell_candidate`; `poll_spell()` cada 300 ms recoge `unknown` y `suggestions`, actualiza `look.set_unknown` y relanza `check()` si hubo nuevas. `editor.Look(translation_box.document())` con `theme.editor_tags(self.dark)`. Menú contextual del cuadro: sobre una palabra desconocida, sus sugerencias (se piden con `("first", palabra)`; mientras, «Buscando sugerencias…») y «Añadir al diccionario de la serie» (como hace `TextEditor`). El proceso termina en `close()` y al destruirse la ventana.
  - Filtro `chips["warn"]` «Con avisos»; `pick()` añade al texto de `doubt` las líneas de avisos.
- [ ] **Step 5:** verde; commit `feat: ortografía y avisos de español en Traducir`.

### Task 4: memoria de la serie

**Files:** Modify `translate.py` (`similar`), `app.py` (módulo: `_memory_path`, `_remember_translation`, `_series_memory`, `_read_translation_file`; ventana), `theme.py` (`STATES`: «De la serie» `#6a4fb3` / `#b39ddb`); Test `test_convert.py`.

**Interfaces:**
- `translate.similar(text, sources, least=0.75) -> (original parecido, parecido) | None`; solo si `len(text) >= 30`; candidatos con longitud entre 0,6 y 1,6 veces la del texto.
- `app._memory_path() -> Path` (`SETTINGS.with_suffix(".memoria.json")`).
- `app._remember_translation(pdf, series)`: añade la ruta del archivo de traducción al índice de la serie; nada si la serie está vacía.
- `app._series_memory(pdf, series) -> {clave: {"text", "dst", "volume", "source", "hand"}}`: de los archivos del índice y de la carpeta del PDF cuya revisión hermana (`X.lnepub.json`) es de la misma serie, sin el propio; entre dos gana `hand`, luego el archivo más reciente; limpia del índice las rutas que no existen.
- Entradas de `done` ganan `"source"` (original) al traducir o corregir, y `"memory": True` si vienen de la serie.

- [ ] **Step 1: pruebas** `test_translation_memory_of_series` (en carpetas temporales de la suite, con `app.SETTINGS` como lo dejen las demás pruebas de ajustes): dos «tomos» como archivos `.lnepub.json` + `.lnepub.traduccion.json` escritos a mano; (a) misma carpeta sin índice: se lee; (b) otra carpeta tras `_remember_translation`: se lee; (c) ruta borrada: fuera del índice y sin excepción; (d) JSON roto: se salta; (e) serie vacía: `{}` y el índice no se crea; (f) `hand` gana. `translate.similar`: 90 % parecido lo da, texto corto o distinto no. Y `test_window_translate_series_memory`: el libro de prueba con serie puesta, memoria con la clave de un párrafo suyo: la fila sale «De la serie» con su traducción, el filtro la cuenta, corregirla la pasa a «A mano»; un párrafo pendiente parecido a un original de la memoria muestra «Parecido en el tomo N» y `parts["use_similar"]()` pone esa traducción.
- [ ] **Step 2:** fallan.
- [ ] **Step 3:** implementación. En la ventana: `memory = _series_memory(pdf, serie)` al abrir; en `fill()`, fila pendiente con clave en memoria y `dst` igual al elegido: entra en `done` con `memory: True`; `state_of` da «De la serie»; `save()` llama a `_remember_translation`; `merge` y `keep` guardan `source`. `pick()`: fila «Pendiente» con `translate.similar(...)`: etiqueta y botón «Usar esta».
- [ ] **Step 4:** verde; commit `feat: memoria de traducción de la serie`.

### Task 5: revisor IA

**Files:** Modify `translate.py` (`review`, `REVIEW`), `app.py` (`_translate_thread`, ventana), `theme.py` (`STATES`: «Con fallos» `#c62828` / `#ff6b6b`, «Revisado» `#00796b` / `#4db6ac`); Test `test_convert.py`.

**Interfaces:**
- `translate.review(name, pairs, src, dst, api_key="", model="", base="", glossary=(), style=None) -> [None | False | {"problem", "fix"}]`: `None` bien, `False` no se pudo revisar.
- `_translate_thread`: con `job["mode"] == "review"`, cada grupo lleva `items = [(clave, original, traducción)]`; pone `("reviewed", {clave: (traducción, resultado)})`.
- `done[k]["review"] = {"for": translate.key(traducción), "ok": True}` o `{"for", "problem", "fix"}`.
- `win.parts`: `"review"` (lanza), `"accept"`, `"discard"`.

- [ ] **Step 1: pruebas** `test_translate_review` con `_post` falso: (a) respuesta `{"2": {"problem": "Omite la segunda frase.", "fix": "…<i>…</i>"}}` da `[None, {...}, None]`; (b) el mensaje del usuario lleva original y traducción de cada par y la instrucción lleva glosario y ficha; (c) número inexistente, entrada sin `fix` y `fix` igual a la traducción se ignoran; (d) respuesta que no es JSON con 2 pares: se parte y cada mitad se pide; con 1 par: `[False]`; (e) `fix` sin la cursiva del original: pasa por `_fix`; (f) motor Google: `TranslateError`. Y `test_window_translate_review`: traduce con `FakeAI`, cambia `_post` por un revisor falso que marca un párrafo; `parts["review"]()`; estados «Con fallos» (1) y «Revisado» (resto); al elegir la fila, `doubt` trae el problema y la corrección; `accept()` pone el `fix`, estado «Revisado»; otra fila marcada y `discard()` queda «Revisado» con su texto; una traducción cambiada a mano antes de que llegue su revisión se queda «A mano» sin revisión; reabrir la ventana conserva los estados.
- [ ] **Step 2:** fallan.
- [ ] **Step 3:** `translate.review`: instrucción `REVIEW` (editora; solo fallos reales; `problem` en español; `fix` párrafo entero con marcas; JSON solo con los que fallan), más glosario y ficha de `style`, capítulo en caché como `translate`. Usuario: `"Paragraphs:\n" + json {"1": {"source", "translation"}}`. `_Mismatch`: partir en dos; con uno, `[False]`.
- [ ] **Step 4:** hilo y ventana: botón «Revisar con IA»; elige filas (selección de más de una, o lo traducido sin revisión vigente del alcance, nunca «A mano» ni «Pendiente»); lotes con `translate.batches([o + t ...], kind)`; `merge_review` pone la revisión solo si `done[k]["text"]` sigue siendo la traducción revisada; `state_of` con la prioridad de la spec; filtro «Con fallos»; «Traducidos» cuenta también «Revisado»; botones «Aceptar corrección» y «Descartar» bajo el cuadro, visibles solo en «Con fallos».
- [ ] **Step 5:** verde; commit `feat: «Revisar con IA» en Traducir`.

### Task 6: verificación y cierre

- [ ] Suite entera en verde.
- [ ] `/verificar-ventana` en pantalla real con clics: los cuatro botones, filtros a 1366 px y al 125 %, temas claro y oscuro.
- [ ] `/regresion-series` no aplica (`convert.py` no cambia); si cambia, se ejecuta.
- [ ] `/build-exe`, `/commit` (merge local a `master`), memoria `roadmap-entregas` al día.
