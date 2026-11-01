# Entrega I: herramientas para editar el EPUB — Diseño

Fecha: 2026-11-01

## Objetivo

Siete funciones para corregir el libro antes de generarlo: ortografía, ignorar avisos, reemplazar desde
Buscar, notas del traductor escritas en el texto, página propia, cambiar una ilustración y marcar
capítulos revisados. Todas usan lo que ya hay: `edit_section` para cambiar texto, la revisión
`.lnepub.json` para recordar, el informe de calidad para avisar.

## Decisiones

### 1. Ortografía

- Diccionario Hunspell `es_ES` de LibreOffice (RLA-ES, triple licencia GPL/LGPL/MPL) en `dict/`, junto
  a su `LICENSE.md`, leído con `spylls` (Python puro, MIT, nueva dependencia fijada). `pyspellchecker` se
  descartó: su diccionario español solo trae lemas («estaba» y «dijo» salían como errores).
- Cargar el diccionario tarda unos 4 s y revisar un tomo unos 2 s: se hace en el proceso de trabajo,
  como el análisis. Las sugerencias de Hunspell tardan ~1 s por palabra, así que se sugieren las palabras
  correctas del propio libro más parecidas (`difflib`), primero la que solo cambia en tildes.
- `convert.unknown_words(texts, known)`: `[(palabra, veces, sugerencias)]`, las más repetidas primero.
  Solo palabras en minúscula de 3 letras o más: con mayúscula suelen ser nombres. No cuentan los
  honoríficos (`HONORIFICS`), las onomatopeyas con una letra tres veces seguida («aaah») ni las palabras
  del diccionario de la serie.
- «Herramientas → Ortografía…»: lista de palabras con sus veces; de la elegida, dónde sale (clic: las
  vistas previas van ahí), «Cambiar por» con las sugerencias, «Cambiar todas» y «Añadir al diccionario».
- El diccionario de la serie (`words`) se guarda en la revisión y pasa a los demás tomos (`INHERITED`).
- El editor de texto subraya en rojo las palabras desconocidas de la última revisión ortográfica.

### 2. Ignorar un aviso

- Botón «Ignorar» en el informe de calidad. Un aviso se reconoce por su tipo y su texto (o su página, si
  no tiene texto), no por el número de bloque, que cambia al editar.
- Los ignorados se guardan en la revisión (`ignored`), no cuentan en el botón de la barra y se ven en
  «Mostrar: Ignorados», donde el botón pasa a «No ignorar».

### 3. Reemplazar desde Buscar

- La barra de búsqueda gana «Reemplazar por», «Reemplazar» (el resultado elegido), «Reemplazar todos»
  y «Guardar como regla» (la añade a los reemplazos de la serie).
- `convert.replace_text(book, find, new, word=False, blocks=None)`: arreglos para `edit_section` de los
  párrafos que cambian. Sin distinguir mayúsculas; si la coincidencia empieza en mayúscula, el reemplazo
  también. No toca las marcas `<i>`, `<b>` ni `[Nota: `. Los títulos no se tocan: se cambian en la lista.
- Se deshace con Ctrl+Z, como cualquier texto editado.

### 4. Notas del traductor en el texto

- En 38 PDF reales no hay ninguna «(N.T.: …)» dentro del texto, así que es un aviso más del informe, sin
  ventana propia: «(N.T.: …)», «(N/T: …)», «(NdT: …)», «[N.T. …]» o «(Nota del traductor: …)» →
  aviso «Menor» que la convierte en nota emergente (`[Nota: …]`).

### 5. Página propia

- «Herramientas → Añadir página…»: título, texto (un párrafo por línea, con `<i>`/`<b>`) y dónde va:
  antes del primer capítulo o al final. Entra como capítulo con `edit_section` y se edita después con
  «Editar texto…». Se deshace con Ctrl+Z y se guarda en la revisión como cualquier texto editado.

### 6. Cambiar una ilustración

- «Herramientas → Cambiar ilustración…» sobre la ilustración elegida en la lista: una imagen de archivo
  la sustituye. Se apunta en la revisión como `["image", bloque, ruta]` y se repite al reabrir; si el
  archivo ya no está, se deja la del PDF. Recortar bordes ya lo hace la opción «Recortar márgenes».

### 7. Capítulos revisados

- Columna «✓» en la lista. Tecla R: marca el título elegido como revisado y salta al siguiente capítulo
  sin revisar; sobre uno ya revisado, lo desmarca. La cabecera de la columna dice «✓ 12/30».
- Se guarda como tercer valor de cada fila de la revisión (`[tipo, título, "✓"]`); las revisiones
  antiguas, de dos valores, siguen valiendo.

## Pruebas

- `test_unknown_words` (errata, nombre con mayúscula, honorífico, onomatopeya, diccionario de la serie,
  sugerencia por tildes).
- `test_replace_text` (mayúsculas, palabra completa, marcas intactas, solo los bloques pedidos).
- `test_quality_report_finds_inline_notes`.
- `test_replay_image_and_page_edits` (imagen cambiada y página al principio sobreviven al reabrir).
- Ventana: ignorar y recuperar un aviso; reemplazar desde Buscar y deshacer; ortografía (cambiar todas,
  añadir al diccionario, subrayado en el editor); añadir página; cambiar ilustración; tecla R.
