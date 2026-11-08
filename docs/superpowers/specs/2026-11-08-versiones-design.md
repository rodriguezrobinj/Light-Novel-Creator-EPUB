# Versiones del libro (control de versiones tipo git) — diseño

## Para qué

Tres usos con la misma herramienta:

1. **Red de seguridad**: volver a un estado anterior más allá del Ctrl+Z de la sesión.
2. **Probar alternativas**: dos maquetaciones o traducciones del mismo tomo, ir y volver entre ellas.
3. **Registro de cambios**: qué cambió entre la versión publicada (el EPUB generado) y la actual.

## Qué es una versión

Una copia completa del estado de la revisión (lo que `save_review` escribe en `<pdf>.lnepub.json`:
campos, filas, ediciones, portada, reemplazos, palabras, avisos ignorados, huella del PDF), con
nombre, fecha y si es automática.

Se guardan en `<pdf>.lnepub.versiones.zip`, junto al PDF: una entrada `NNNN.json` por versión, de la más
antigua a la más nueva, comprimida (un estado real ocupa ~6 KB, ~2 KB comprimido; con capítulos editados,
decenas o cientos de KB). El zip se escribe entero a un `.tmp` y se cambia con `os.replace`. Un zip dañado
se aparta como `.bak` y se empieza otro, avisando.

Sin ramas, merge ni staging: restaurar no borra las versiones posteriores, así que las alternativas
conviven en la misma lista.

## Cuándo se crean

- A mano: «Versiones…» en el menú de herramientas, campo «Nombre» y «Guardar versión».
- Automáticas:
  - al terminar «Generar EPUB»: «EPUB generado · <archivo>»;
  - antes de restaurar: «Antes de restaurar otra versión» (sin el nombre: restaurar una de estas lo anidaría).
- Una automática igual que la última versión (sin contar los avisos del EPUB) no se guarda.

## Diálogo «Versiones…»

- Lista: nombre, fecha, cambios respecto a la versión anterior («2 datos · 3 filas · 1 edición de texto»).
- Abajo, la comparación:
  - una versión elegida: con el estado actual;
  - dos elegidas (Ctrl+clic): entre ellas, de la más antigua a la más nueva.
- Comparación, por partes:
  - **Datos del libro y opciones**: «Grupo: «A» → «B»», sinopsis, portada, reemplazos.
  - **Lista**: títulos añadidos, quitados, renombrados y cambios de tipo, en orden.
  - **Texto**: párrafos cambiados con las palabras quitadas tachadas en rojo y las nuevas en verde
    (colores `theme.diff_tags`), con su página. Como mucho 300 párrafos.
- Botones: «Guardar versión», «Restaurar», «Renombrar» (con lo escrito en «Nombre») y «Borrar» (pregunta).

## Restaurar

- Solo si la versión es del mismo análisis: mismos bloques (`analyzed`) y mismo tamaño de PDF. La fecha
  del PDF no cuenta: OneDrive la cambia al sincronizar. Si no, se explica y no se restaura; se puede comparar
  igualmente (el texto, solo si coincide el análisis).
- Antes, la versión automática del estado actual: restaurar nunca pierde nada.
- En memoria, sin volver a analizar: la ventana guarda al abrir el libro una copia de sus bloques,
  capítulos y portada del análisis (las ediciones crean listas nuevas, no cambian los bloques), vuelve a ella
  y aplica la versión con el mismo `load_review` que al reabrir el PDF. Se cierran Ortografía, Añadir
  página e Informe de calidad (tienen índices del libro de antes). El Ctrl+Z de la sesión se vacía.

## Pruebas (offscreen, en TMP)

- Guardar, cambiar, comparar: la comparación nombra el campo, la fila y el párrafo cambiados.
- Restaurar devuelve filas, campos, texto editado y portada; deja la versión «Antes de restaurar».
- Generar EPUB crea la versión automática una vez (otra generación igual no repite).
- Zip dañado: se aparta en `.bak`, la lista sale vacía y se avisa.
- Versión de otro análisis: no se restaura.
