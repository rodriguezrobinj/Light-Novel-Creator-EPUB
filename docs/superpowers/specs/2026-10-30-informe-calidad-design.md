# Informe de calidad visual — Diseño

Fecha: 2026-10-30

## Objetivo

Que el informe de calidad se vea sin buscarlo, revise más cosas y diga con claridad qué pasa, qué
hacer y cómo queda el texto con el arreglo.

## Decisiones

- `quality_report(book)` devuelve `Issue`: tipo, bloque, página, gravedad (`LEVELS`: Grave, Revisar,
  Menor), título, problema, solución, vista previa de antes y después, y el arreglo automático como
  `(inicio, fin, texto)` de `edit_section`. Así el arreglo se guarda en la revisión, se repite al reabrir
  el PDF y se deshace con Ctrl+Z como cualquier texto editado.
- Comprobaciones nuevas: caracteres rotos (� o UTF-8 leído como Windows-1252, que se arregla solo),
  párrafo duplicado seguido y capítulo que falta en la numeración ("Capítulo 3" tras el 1).
- Párrafo que empieza en minúscula: se une al anterior (saltando ilustraciones y separadores) solo si
  el anterior no termina la frase. Si la termina (Maryoku 9), el texto puede estar desordenado: aviso
  sin arreglo automático.
- Descartado: palabra partida con guion ("pala- bra"). En los PDF de prueba todo lo que encontraba eran
  incisos entre guiones.
- Ventana: lista con la gravedad en color y filtro por gravedad. Del aviso elegido: problema, solución,
  «Ahora» con lo que se quita en rojo tachado y «Con el arreglo» con lo nuevo en verde (difflib).
  Botones «Arreglar», «Arreglar todos los de este tipo (N)» y «Editar texto…».
- «Arreglar todos» aplica de atrás adelante y salta los arreglos que se pisan; es un solo Ctrl+Z.
- Barra inferior: botón con el resumen ("⚠ 2 graves · 5 por revisar"), en rojo si hay algo grave. Se
  recalcula cada vez que cambia la lista.

## Pruebas

`test_quality_report`, `test_quality_report_explains_previews_and_fixes` (vistas previas y arreglos
aplicados con `edit_section`) y `test_window_quality_report_previews_fixes_and_undo` (resaltado,
«Arreglar todos», Ctrl+Z y el botón de la barra inferior).
