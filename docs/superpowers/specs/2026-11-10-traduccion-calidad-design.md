# Traducción de calidad: revisor, coherencia, memoria de la serie y español

Fecha: 2026-11-10. Aprobado en conversación. Amplía `2026-11-08-traduccion-design.md`.

## Objetivo

Dar a quien traduce herramientas para **revisar** lo traducido, no para traducir más rápido. Hoy los avisos
«Dudoso» son reglas fijas (longitud, ¿¡, cursivas, glosario): no ven un sentido cambiado, un término traducido
de dos formas ni una errata. Cuatro mejoras en la ventana «Traducir», en una rama `feat/traduccion-calidad`.

Éxito: con un libro traducido, el usuario encuentra desde la ventana los párrafos con errores de sentido, los
términos incoherentes y las erratas, y los arregla sin salir de ella; un tomo nuevo de la serie reutiliza lo ya
traducido en los anteriores.

Orden de construcción: coherencia, español, memoria, revisor. Lo local primero (se prueba sin claves).

## 1. Revisor IA

`translate.review(name, pairs, src, dst, api_key, model, base, glossary, style)`: `pairs` es
`[(original, traducción)]`; devuelve una lista con `None` (bien) o `{"problem", "fix"}` por par.

- Solo motores de `AI`; con otro, `TranslateError` («Solo una IA puede revisar: elige una en «Motor».»).
- Instrucción de editora: comparar cada traducción con su original y marcar solo fallos reales: sentido
  cambiado, frase omitida o añadida, hablante o género equivocado, trato (tú/usted) incoherente, glosario sin
  aplicar, frase que no suena natural. No marcar preferencias de estilo. `problem` en español, una frase;
  `fix` es el párrafo entero corregido, con las marcas (`<i>`, `<b>`, `[Nota: …]`) intactas.
- Respuesta JSON solo con los párrafos que fallan: `{"3": {"problem": "…", "fix": "…"}}`; `{}` si todo va bien.
  Un número fuera del lote o una entrada sin `fix` se ignora. Respuesta que no es JSON: `_Mismatch`, el lote se
  parte en dos como al traducir; con un solo par, se da por no revisado (no es un error).
- Lotes con la mitad del límite de `LIMITS` (cada párrafo va dos veces). El capítulo original va en caché
  como al traducir (`style["chapter"]`), con el glosario y la ficha de personajes.
- `fix` pasa por `_fix` (marcas) antes de devolverse. Un `fix` igual a la traducción se descarta.

Datos, en `done[k]`: `"review": {"for": key(traducción), "problem", "fix"}` o `{"for", "ok": true}`. Si la
traducción cambia, `for` deja de coincidir y la revisión no cuenta.

Ventana:

- Botón «Revisar con IA». Revisa las filas elegidas si hay más de una; si no, lo traducido sin revisión
  vigente del alcance. No revisa lo corregido a mano ni lo pendiente.
- Mismo hilo y cola que traducir: `_translate_thread` con `job["mode"] == "review"` pone
  `("reviewed", {clave: revisión})`. «Pausar» y cerrar a mitad funcionan igual.
- Estados nuevos: «Con fallos» (revisión vigente con `problem`) y «Revisado» (`ok`). Filtro «Con fallos».
- Panel de abajo, con el párrafo elegido «Con fallos»: el problema, la corrección propuesta y los botones
  «Aceptar corrección» (pone `fix` como traducción, estado «Revisado») y «Descartar» (marca `ok`).

## 2. Coherencia de términos

Local, sin IA, con cualquier motor. `translate.inconsistent(pairs, glossary=())`: `pairs` es
`[(clave, original, traducción)]`; devuelve `[(término, [(forma, [claves])])]` con dos formas o más, de la más
usada a la menos.

- Términos candidatos del original: rachas de palabras con mayúscula inicial que no empiezan frase (con
  enlaces en minúscula dentro: «Cult of Diablos»; sin el posesivo), secuencias de katakana de 2 letras o más y
  los originales del glosario. Solo los que salen en 3 párrafos o más. Un término no cuenta donde forma parte
  de otro más largo. Entre formas que casi empatan gana la que conserva más mayúsculas y la más larga.
  Probado con la traducción real de Eminence 7 (3.073 párrafos, 0,5 s): ninguna incoherencia falsa.
- Formas: para cada término, entre los n-gramas (1 a 4 palabras, sin artículos en los bordes) de las
  traducciones de sus párrafos, el de mayor coeficiente de Dice con el término (párrafos donde salen juntos
  contra párrafos donde sale cada uno). Los párrafos sin esa forma se vuelven a mirar para hallar la segunda,
  y así hasta que no quede forma con Dice de 0,5 o más y 2 párrafos o más.
- Dos formas que solo cambian en mayúsculas, plural o artículo (`_stem`) son la misma.
- Un término cuya única forma es él mismo (un nombre que no se traduce) no es incoherente.

Ventana: botón «Coherencia…» abre un diálogo con una fila por término: término, formas con sus veces. Al
elegir una forma y pulsar «Usar esta forma», el par entra en `tr_glossary` (de la serie), se cierra el diálogo
y la lista queda con los párrafos de las otras formas seleccionados y a la vista, listos para «Retraducir
selección» o corregir a mano. No se reemplaza texto: el género y la flexión romperían frases.

## 3. Memoria de la serie

- Índice `ajustes.memoria.json`, junto a las preferencias: `{serie en minúsculas: [rutas de
  .lnepub.traduccion.json]}`. Se actualiza al guardar una traducción de un libro con serie. No copia
  traducciones: cada tomo sigue con su archivo.
- `_series_memory(pdf, series, dst)`: lee los archivos del índice y los `*.lnepub.traduccion.json` de la misma
  carpeta cuya revisión es de la misma serie (un tomo movido pierde su ruta), salvo el del propio libro.
  Devuelve `{clave: {"text", "volume"}}` de lo traducido a `dst`; entre dos tomos gana lo corregido a mano y
  luego el más reciente. Las rutas que ya no existen salen del índice.
- Igual exacto: al llenar la lista, un párrafo pendiente cuya clave está en la memoria toma esa traducción con
  `"memory": true`; estado «De la serie», con su filtro. Editarlo lo pasa a «A mano». Se aplica al libro como
  cualquier traducción.
- Parecido: `translate.similar(text, memory_sources)` con `difflib` devuelve el más parecido con 75 % o más
  (solo originales de 30 letras o más). La memoria guarda para ello el original de cada párrafo: las entradas
  de `done` llevan desde ahora `"source"` (las antiguas, sin él, solo sirven para el igual exacto). Bajo el
  párrafo pendiente elegido: «Parecido en el tomo N: original → traducción» y «Usar esta».

## 4. Español dentro de «Traducir»

Solo cuando el destino es español.

- Ortografía: el cuadro de traducción subraya las palabras que Hunspell no conoce, con el mismo proceso
  `_spell_server` y las mismas palabras de la serie (`words`) que el editor; clic derecho da sugerencias y
  «Añadir al diccionario de la serie». El proceso arranca al abrir la ventana y termina al cerrarla.
- Avisos: `translate.quality(pairs, glossary, lang)`, con `pairs = [(clave, traducción)]`, monta un `Book`
  ligero (un bloque `p` por traducción) y pasa `convert.quality_report`; devuelve `{clave: [(título,
  problema)]}` solo de los tipos de texto (erratas, calcos, mayúsculas de más, signos sin pareja o repetidos,
  inglés sin traducir, honoríficos). Los de estructura (párrafo partido, número suelto, línea repetida, muy
  largo) no aplican.
- Se calculan en un hilo al abrir y al acabar cada tanda o corrección a mano del párrafo. La tarjeta muestra
  «⚠ N» junto al estado; el panel de abajo, el detalle; filtro «Con avisos».

## Estados y filtros

Orden de prioridad del estado de una fila: A mano, Con fallos, Dudoso, Revisado, De la serie, Traducido,
Aplicado, Pendiente. Filtros: Todos, Pendientes, Dudosos, Con fallos, Con avisos, De la serie, A mano,
Traducidos (Traducido + Revisado + Aplicado).

## Fuera de alcance

Revisión automática tras cada lote, modelo revisor distinto del traductor, reemplazo automático de formas,
copia de traducciones fuera de su tomo, avisos de calidad para destinos que no son español.

## Pruebas

- `translate.review` con `_post` falso: fallos y aciertos, respuesta rota que parte el lote, `fix` con marcas.
- `translate.inconsistent`: dos formas de un término, nombre sin traducir, plural y mayúsculas como la misma
  forma, término poco frecuente fuera.
- Memoria: exacto desde otra carpeta por el índice, desde la misma carpeta sin índice, parecido, ruta perdida.
- `translate.quality`: errata y calco en una traducción, nada de avisos de estructura.
- Ventana offscreen por cada botón: revisar y aceptar corrección, coherencia y elegir forma, fila «De la
  serie», avisos y subrayado. Luego `/verificar-ventana` en pantalla real y `/build-exe`.

## Riesgo

El revisor, como la traducción con IA, solo se prueba con respuestas simuladas: no hay claves reales.
