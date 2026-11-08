# Entregas M, N y O: calidad, edición y salida de nivel editorial — Diseño

Fecha: 2026-11-08

## Objetivo

Llevar la revisión del libro al nivel de una editorial: más avisos de estilo y ortotipografía según la RAE,
un glosario que mantenga los nombres iguales en toda la serie, herramientas de corrección (expresiones
regulares, lectura en voz alta, comentarios del revisor, informe exportable) y un EPUB accesible y
validado con EPUBCheck.

Tres entregas, cada una con su rama, tests, `/regresion-series`, build y merge:

- **M — Calidad** (`feat/calidad-m`): avisos 1–6, sobre todo en `convert.py`.
- **N — Edición** (`feat/edicion-n`): herramientas 7–10, en `app.py` y `editor.py`.
- **O — Salida** (`feat/salida-o`): 11–12, en `write_epub` y la ventana «EPUB listo».

Criterio para cada aviso nuevo: se cuenta cuántos salen en los PDF reales (`/regresion-series`). Si uno
da falsos avisos en más de un tercio de los casos mirados, se rebaja a «Revisar» sin arreglo o se quita.

## M — Calidad

### 1. Puntuación del diálogo (RAE)

- **Punto antes del inciso** («—Hola. —dijo él» → «—Hola —dijo él»): ya lo arreglaba la entrada `"dialog"`.
- **Raya de cierre tras el inciso** («—Ven —dijo él. Vamos» → «…él—. Vamos»): **descartada** tras la regresión.
  Tras «—dijo él.» casi siempre sigue narración (Mitsuha 1: 31 de 31, Re Zero 1: 16 de 16), y desde el texto
  no se distingue. Un test fija que no se toca.

### 2. Calcos del inglés

Tipo `calque`, «Calco del inglés». Solo frases fijas sin ambigüedad:

- «en base a» → «con base en»
- «tomar lugar» y sus formas (toma, tomó, tomaba, tomará, tomaría, tomado) → «tener lugar» conjugado
- «hacer sentido» y sus formas (hace, hizo, hacía, hará, haría) → «tener sentido» conjugado
- «en orden de/a» + infinitivo → «para»

Fuera: «eventualmente», «asumir», «aplicar para» (dependen del sentido).

### 3. Mayúsculas de más

Tipo `uppercase`, «Mayúscula innecesaria»: días de la semana y meses tras una palabra en minúscula o una
coma («el Lunes», «de Enero» → «el lunes», «de enero»), y «Señor/Señora/Señorita/Don/Doña» seguidos de un
nombre con mayúscula a mitad de frase («el Señor Pérez» → «el señor Pérez»). Los cargos («el Rey Demonio»,
«el Héroe») no se tocan: en las novelas ligeras hacen de nombre propio.

### 4. Signos sin pareja

- Tipo `unpaired`, «Revisar», «Signo sin pareja», sin arreglo: en un párrafo, « » “ ” o ( ) que no cuadran
  (¿ ? ya los cubren «Falta el signo de apertura» y «Falta el signo de cierre»). No avisa:
  - si el párrafo siguiente (o este, para un cierre sin abrir) empieza con comillas: diálogo de varios párrafos;
  - si el signo que falta está en el párrafo de al lado: párrafo partido por el PDF;
  - de emoticonos «:)» ni de enumeraciones «a)»; la comilla recta " cierra la “ abierta;
  - de un signo que falla en más de 20 párrafos del libro: es el estilo del grupo (Maryoku), no una errata.
- Dentro de `"punct"`: más de tres «!» o «?» seguidos → tres.

### 5. Ecos

- `quality_report(book, echoes=False)`. Con `True`, tipo `echo`, «Menor», «Palabra repetida cerca», sin
  arreglo: la misma palabra en minúscula, de 5 letras o más y que no es palabra vacía, dos veces a menos de
  12 palabras en un párrafo. Un aviso por párrafo, marcando la segunda.
- El diálogo «Informe de calidad» tiene la casilla «Buscar ecos (repeticiones cercanas)», apagada por
  defecto, guardada en las preferencias; el editor usa la misma opción.

### 6. Glosario de la serie

- Campo de revisión `glossary` (lista de términos), en `INHERITED`: pasa de tomo a tomo como los reemplazos.
- «Editar datos» → botón «Glosario…»: lista editable de términos (añadir, quitar, cambiar) y botón «Añadir
  nombres de este tomo», que propone los nombres propios con 5 o más apariciones que aún no están, con
  su recuento como comentario (`Emilia  # 120 veces`), en el mismo cuadro de texto que «Reemplazos…».
- `quality_report(book, glossary=())`: tipo `glossary`, «Menor», «Nombre distinto al glosario». Un nombre con
  mayúscula del libro que no está en el glosario pero es la misma palabra sin tildes ni guiones (`_fold`) o
  difiere en una letra de un término de 5 letras o más. Arreglo: cambiar la forma del libro por la del
  glosario en ese párrafo; «Arreglar todos» los cambia todos.

## N — Edición

### 7. Expresiones regulares en la búsqueda

- Casilla «.*» («Expresión regular») en la barra de búsqueda del editor. Con ella, el término se usa tal
  cual y el reemplazo admite `\1`, `\g<nombre>`. La barra de la ventana principal no comparte `find_pattern`
  y se queda como está; «Reemplazos…» ya admitía expresiones con «re:».
- Expresión mal escrita: el campo en rojo y el error junto a la barra; no se busca ni reemplaza.
- En la hoja, `QRegularExpression` busca y el `re` de Python expande los grupos del reemplazo. Una
  coincidencia vacía («a*») no cuenta: reemplazarla no avanzaría.

### 8. Leer en voz alta

- Botón «🔊 Leer» en el grupo Revisar del editor. Lee desde el párrafo del cursor, resalta el que lee y
  pasa al siguiente hasta el final del capítulo.
- Para con el mismo botón (queda pulsado mientras lee), con Esc, con un clic o una tecla en la hoja, o al
  cambiar de capítulo o cerrar. El párrafo se marca de fondo, sin elegirlo: teclear no lo borraría.
- Motor: un proceso `powershell` de larga duración con `System.Speech`; recibe un párrafo por línea en
  stdin, responde «ok» al terminar cada uno. Voz: la primera con cultura `es-*`.
- Sin voz en español: mensaje con cómo instalarla (Configuración → Hora e idioma → Voz).

### 9. Comentarios del revisor

- Clic derecho en un párrafo → «Comentario…» (añadir o cambiar) y «Quitar comentario».
- Guardados en `.lnepub.json` (`comments`: `[{"text": inicio del párrafo, "page": n, "note": "…"}]`). El
  párrafo es el que empieza por ese texto o, si se editó, el más parecido de su página. No salen en el EPUB.
- En la hoja: fondo amarillo suave (color del tema); la nota sale en el clic derecho y en la barra de F8.
- La ventana los suma al informe como avisos `comment` («Revisar», «Comentario del revisor»): salen en F8,
  en «Avisos ▾», en el diálogo de calidad y en la exportación. «Ignorar» un comentario lo borra.

### 10. Exportar el informe y puntuación

- Diálogo de calidad: botón «Exportar…» a HTML (página propia, legible sin la app) o CSV (UTF-8 con BOM,
  separador `;` para Excel en español), según la extensión elegida.
- Puntuación: `100 − (10·graves + 3·revisar + 1·menores) × 10 000 / palabras`, entre 0 y 100, redondeada.
  En la cabecera del diálogo y en la exportación. `convert.quality_score(report, words)`.

## O — Salida

### 11. Accesibilidad del EPUB

- EPUB 3: en el OPF `schema:accessMode` (textual; visual si hay ilustraciones),
  `schema:accessModeSufficient` (textual), `schema:accessibilityFeature` (tableOfContents, readingOrder,
  structuralNavigation, alternativeText si hay ilustraciones), `schema:accessibilityHazard` none y
  `schema:accessibilitySummary` en español. Sin `dcterms:conformsTo`: no se ha verificado WCAG.
- Texto alternativo de las ilustraciones: «Ilustración de «título del capítulo»» (o «Ilustración» antes del
  primero). Herramientas → «Describir ilustración…» pone uno propio a la ilustración elegida en la lista
  («Cambiar ilustración…» es un selector de archivo, sin diálogo propio). Se guarda en la revisión (`alts`) por
  la huella de la imagen (`convert.image_key`, SHA-1), no por su índice, que cambia al editar.

### 12. EPUBCheck

- «EPUB listo» → botón «Validar con EPUBCheck», en su propia fila.
- Sin Java: mensaje que lo explica. Sin el programa: ofrece descargarlo una vez (EPUBCheck 5.4.0, 33 MB, de
  las versiones publicadas por w3c en GitHub) a la carpeta de preferencias; solo tras confirmar. El .zip solo
  se descomprime si todo va dentro de su carpeta.
- Comprobado con el EPUBCheck real: Re Zero 1 generado con accesibilidad, 0 errores y 0 advertencias (21 s).
- Corre `java -jar epubcheck.jar <epub> --json <tmp> --locale es` fuera de la ventana y muestra
  errores y advertencias (gravedad, archivo, mensaje) en una lista; «Sin errores» si no hay.

## Pruebas

- Cada aviso de M: test con PDF sintético en `test_convert.py`, también los casos que no deben avisar.
- N y O: tests offscreen de la ventana y de `write_epub`/`check_epub`; la voz se prueba con un motor falso
  (el proceso real no corre en los tests).
- Al final de cada entrega: `/verificar-ventana` sin `--oculto`, con clics reales.
