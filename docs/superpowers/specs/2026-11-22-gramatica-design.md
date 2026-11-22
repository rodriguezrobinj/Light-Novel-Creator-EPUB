# Gramática con LanguageTool

Fecha: 2026-11-22. Rama: `feat/gramatica`.

## Objetivo

Hunspell solo ve palabras sueltas. Las traducciones de fans fallan sobre todo en tildes diacríticas
(«el/él», «aun/aún», «que/qué»), queísmo, concordancia y verbos sin tilde («tomo/tomó»). LanguageTool
las detecta. Sus avisos entran en el Informe de calidad como los demás: filtrar, ignorar, arreglar en
un clic, «Arreglar todos», F8 en el editor y exportación.

## Decisiones del usuario

- Motor: los dos modos, local por defecto. Local = LanguageTool descargado una vez + Java. Alternativas
  en Opciones: languagetool.org gratis (con límites, el texto sale del PC), servidor propio (URL) y
  Premium (usuario + clave).
- Dónde: solo en el Informe de calidad (botón «Revisar gramática»). Sin subrayado al escribir.

## Datos medidos (spike 2026-11-22)

- LanguageTool 6.6 (última, 2025-03-27): `https://languagetool.org/download/LanguageTool-6.6.zip`,
  251 998 221 bytes, SHA256 `53600506b399bb5ffe1e4c8dec794fd378212f14aaf38ccef9b6f89314d11631`.
  Descomprimido: 390 MB (todos los idiomas: sirve para libros en otro idioma).
- `java -cp languagetool-server.jar org.languagetool.server.HTTPServer --port N`: listo en ~2 s con
  Java 25.
- Re Zero 1 (2 927 párrafos, 412 000 letras) en lotes de 50 KB: 42 s, 479 avisos, casi todos reales.
- Ruido: `UPPERCASE_SENTENCE_START` (diálogos que empiezan con «…»), `ES_QUESTION_MARK`,
  `ES_INITIAL_QUESTION_MARK`, `ES_UNPAIRED_BRACKETS` (ya los cubre «Signo sin pareja»),
  `ESPACIO_DESPUES_DE_PUNTO` («—…», lo arregla la tipografía), `HA_A` («Ha-ha»),
  `PRONOMBRE_SIN_VERBO` (diálogo cortado).

## Diseño

### `grammar.py` (motor, sin UI)

- `LT_VERSION`, `LT_URL`, `LT_SHA256`, `PUBLIC_URL = "https://api.languagetool.org"`.
- `DISABLED`: `MORFOLOGIK_RULE_ES` (Hunspell ya ve la ortografía), `WHITESPACE_RULE`,
  `UNPAIRED_BRACKETS` y las siete reglas de ruido de arriba. `RULES_ID`: el texto de `LT_VERSION` +
  `DISABLED`. Si cambia, la caché vieja no vale.
- `check(texts, base, lang, user="", key="", limit=50_000, progress=None)` devuelve, para cada texto,
  su lista de fallos `[offset, length, message, replacements[:3], rule_id, category_id]`.
  - Junta los párrafos con `"\n\n"` en lotes de `limit` letras y hace POST a `{base}/v2/check` con
    `urllib`: UTF-8, `language`, `disabledRules`, y `username`/`apiKey` si hay clave.
  - Devuelve cada fallo a su párrafo según las posiciones acumuladas. Un fallo que cruza dos párrafos
    se descarta.
  - API pública: `limit=18_000` (máximo 20 KB por petición). Ante un 429, espera lo que diga
    `Retry-After` (o 60 s) y reintenta hasta 3 veces.
  - Cualquier otro error lanza `GrammarError` con un mensaje en español.
- `STYLE = {"PUNCTUATION", "STYLE", "TYPOGRAPHY", "REDUNDANCY"}`: los fallos de esas categorías
  son del tipo `grammar-style` (gravedad Menor). Los demás son del tipo `grammar` (Revisar).
- `issues(blocks, found)` crea `convert.Issue` para los párrafos cuyo texto (por `key(texto)`) está
  en `found`.
  - `title`: «Gramática: «palabra»» o «Estilo: …».
  - `problem`: el mensaje de LanguageTool. `solution`: «Cambiar «x» por «y».», o «Revísalo con
    «Editar texto…»» si no hay sugerencia.
  - `before`/`after`: la frase alrededor.
  - `text`: unas 60 letras alrededor del fallo. Sirve para buscarlo en las vistas previas y para
    ignorarlo sin ignorar el resto del párrafo.
  - `edit`: solo si el fragmento aparece una sola vez en `convert._line(bloque, k)`; entonces se
    cambia ahí. Si no, el aviso queda como manual, para no tocar etiquetas.
- `key(texto)`: SHA1 de 16 letras, como `translate.key`.
- Servidor local, que llama la ventana:
  - `download(folder)`: descarga el zip, comprueba el SHA256 y lo descomprime en una carpeta temporal.
    Luego lo renombra a `LanguageTool-6.6`, de forma atómica. Se ejecuta en el proceso de trabajo.
  - `start(folder)`: `Popen` con `CREATE_NO_WINDOW` en un puerto libre. Devuelve `(proc, base)`.
  - `wait(base, proc, seconds=60)`: consulta `/v2/languages` hasta que responda. Si el proceso
    muere, lanza `GrammarError` con la última línea de stderr. `UnsupportedClassVersionError` se
    traduce como «necesita Java 17 o posterior».

### Ventana (`app.py`)

- Caché `<pdf>.lnepub.gramatica.json`: `{"rules": RULES_ID, "lang": "es", "found": {clave: [fallos]}}`.
  Guarda también los párrafos sin fallos (lista vacía): así se sabe qué ya está revisado.
- `grammar_issues()` se suma en `quality_report()`, junto a `toc_issues()` y `comment_issues()`.
  Un párrafo editado cambia de clave y pierde sus avisos hasta la próxima revisión.
- Botón «Revisar gramática» en la ventana del Informe de calidad. Manda solo los párrafos sin revisar
  y vuelve a rellenar el informe.
  - Motor local: si falta Java, muestra el mismo mensaje que EPUBCheck. Si falta LanguageTool, ofrece
    descargarlo (unos 250 MB) y luego sigue.
  - El servidor arranca una vez por sesión (`self.lt_server`) y se cierra al salir de la app.
  - La revisión corre en el proceso de trabajo (`self.run`), con estado «Revisando gramática…».
- Opciones → Ortografía, nueva opción «Revisión de gramática»: motor (Local, languagetool.org,
  Servidor propio, LanguageTool Premium), URL del servidor propio, usuario y clave. La clave se cifra
  con DPAPI, igual que las de traducción.
- `quality_kinds()`: añade «Gramática» (Revisar) y «Estilo y puntuación» (Menor). Se pueden apagar o
  cambiarles la gravedad en Opciones.
- El idioma que se envía es el del libro (`lang_var`).

## Errores

Todos los mensajes son en español y no se pierde nada: lo ya revisado se guarda lote a lote.
- Sin internet al descargar o al usar la API pública.
- SHA256 distinto: no se usa la descarga.
- Puerto ocupado: se elige otro libre.
- El servidor no arranca: mensaje con el motivo.
- 429 tras 3 intentos.
- Libro sin párrafos nuevos: «Ya está revisado».

## Pruebas

- `test_convert.py`, con un servidor HTTP falso (`http.server` en un hilo) que responde JSON real de
  LanguageTool 6.6:
  - lotes y posiciones;
  - un fallo que cruza dos párrafos se descarta;
  - `disabledRules` y clave en el POST;
  - un 429 con `Retry-After` se reintenta;
  - `issues` con arreglo seguro y sin él (fragmento repetido o dentro de etiquetas);
  - tipos y gravedades;
  - caché por clave.
- Ventana: Opciones con «Servidor propio» apuntando al servidor falso → «Revisar gramática» → los
  avisos salen en el informe y la caché se escribe. Tras editar el párrafo, su aviso desaparece.
  «Arreglar» aplica la sugerencia.
- Real: LanguageTool local sobre una muestra con `/probar-novela`, para contar el ruido. Recorrido
  con `/verificar-ventana` y `/build-exe`.

## Fuera de alcance

Subrayado al escribir en el editor, reglas por serie, botón para borrar LanguageTool descargado y
recortar los idiomas de la descarga.
