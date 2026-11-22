# Pulido de herramientas — plan de implementación

> Ejecución: nativa (en esta sesión), entrega por entrega, sin parar entre ellas (el usuario pidió «todas seguidas» y
> tiene preaprobados los planes). Cada tarea: prueba que falla → código → prueba que pasa. Cada entrega: suite
> completa, `/verificar-ventana` en pantalla real (claro y oscuro), `/regresion-series` si cambió `convert.py`,
> `/build-exe`, commit.

**Objetivo:** las cuatro entregas de la especificación.
**Arquitectura:** cambios en las ventanas existentes de `app.py` (Qt con la API de `qtk`), avisos en
`convert.quality_report`, opciones en `options.py` (tabla `OPTIONS` + `OptionsDialog`), colores en `theme.py`.
**Especificación:** `docs/superpowers/specs/2026-11-21-pulido-herramientas-design.md`.

## Reglas globales
- Todo texto visible en español con tildes; comentarios en español, cortos, el porqué; identificadores en inglés.
- Pruebas: funciones `test_*` en `test_convert.py`; ventanas con `visible_window()` / `editor_on()`; lanzador suelto
  `scratchpad/run1.py` para una prueba; suite: `.venv/Scripts/python.exe test_convert.py`.
- Nada de pruebas a la vez que el build o el `.exe`.
- Commits: `tipo: asunto` en español, sin firma.

## Foco de revisión (lo que más puede fallar a un usuario)
1. Revisiones guardadas de antes: títulos con `<b>`, palabras del diccionario y avisos ignorados siguen valiendo.
2. Ctrl+Z en la ventana principal tras cada acción movida (cambiar/describir ilustración desde otra ventana, página
   nueva desde el editor, «Arreglar todos» con casillas).
3. Libros con muchos avisos (300+): el informe abre sin congelar la ventana (agrupar y pintar el resumen es lineal).
4. Tema oscuro: anillo, fichas, franja de capítulos, diff y teclas de atajo legibles (contraste del tema).
5. Opciones «Este libro | Libros nuevos» con las opciones movidas desde la barra de abajo: la barra y la ventana dicen
   lo mismo.

## Entrega 1 · Arreglos y orden
- [ ] T1 `_opening_marks` respeta abreviaturas: prueba con «¡Lo siento mucho, Sr. Drago!» y «Hola Sr. Drago!» sin
  arreglo partido; `convert.py` `SENTENCE`/`_opening_marks`. `/nuevo-aviso-calidad` (caso que falla) + regresión.
- [ ] T2 Títulos sin etiquetas: `_prepare`, `fill_tree`, `apply_edits` usan `convert._plain`; al cargar filas de una
  revisión, títulos con etiquetas se limpian. Prueba: editor convierte una línea `<b>…</b>` en subtítulo → fila sin
  `<b>`; revisión con «<b>X</b>» → fila «X» y EPUB sin «&lt;b&gt;».
- [ ] T3 Menú: quitar Cambiar/Describir ilustración, Instalar/Desinstalar (y `install`/`uninstall` + pruebas), Añadir
  página. «Ilustraciones…»: «Cambiar…», «Describir…», descripción bajo la vista previa. Prueba de la ventana.
- [ ] T4 «Página nueva…» en el editor (Insertar → Estructura): guarda, `insert_page`, reabre el editor en la página.
  Prueba: tras este capítulo / al final; Ctrl+Z en la ventana la quita.
- [ ] T5 Atajos: datos `SHORTCUTS` como [(grupo, [(tecla, acción)])], ventana con tabla y teclas dibujadas. Prueba: la
  ventana enseña los grupos y F9.
- [ ] T6 Código HTML: sangría ~20 px por nivel (`editor.INDENT_PX`) y guías verticales en `CodeEdit.paintEvent`.
  Prueba: `leftMargin` de un párrafo a nivel 3 ≥ 60.
- [ ] T7 Explorador: imagen → bloque (PDF `img/bNNNNN`, EPUB `Block.ref`); visor con Cambiar/Describir/Exportar.
  Prueba: describir desde el visor llega a `app.alts`.
- [ ] Cierre de entrega 1: suite, regresión, verificación en pantalla, build, commit.

## Entrega 2 · Informe de calidad
- [ ] T8 Widgets de resumen en `app.py` (o `report.py` si crece): `ScoreRing`, fichas, barras por tipo, franja de
  capítulos; filtros conectados al estado de la lista. Prueba: clic en ficha «Grave» deja solo graves.
- [ ] T9 Lista agrupada por tipo (`RowList` con filas padre), «párr. N», nota en una línea. Prueba: grupos y cuentas.
- [ ] T10 Detalle: párrafo con `Issue.span` resaltado; diff en un solo cuadro (quitado rojo tachado, puesto verde);
  Problema/Solución como texto plano. Prueba: formato de los tramos.
- [ ] T11 «Arreglar todos» con lista de casillas; solo los marcados, un Ctrl+Z. Prueba: desmarcar uno.
- [ ] T12–T15 Avisos `dialog-style`, `double-space`, `mr`, `name-variant` con `/nuevo-aviso-calidad` cada uno
  (PDF sintético + libros reales; `name-variant` se descarta si da falsas alarmas).
- [ ] Cierre de entrega 2.

## Entrega 3 · Ortografía
- [ ] T16 Buscador y «Revisadas X de N»; apariciones con ficha de página y frase entera resaltada (rich text).
- [ ] T17 Sugerencias como botones + «Otra…»; diff de la aparición elegida antes de aplicar.
- [ ] T18 «Cambiar esta» (solo esa aparición, un Ctrl+Z), «Ignorar» (sesión). Pruebas de cada acción.
- [ ] Cierre de entrega 3.

## Entrega 4 · Opciones
- [ ] T19 Secciones con título y «Restablecer» por página en `OptionsDialog`.
- [ ] T20 Categorías nuevas: Aspecto (style, separator, font, heading_font, box_style, note_style), Páginas
  (contents, credits, synopsis, endnotes, reading, logo), Salida (`out_name`), Inicio (`reopen_last`, `recent_max`,
  `out_folder`), Ortografía (`spell_caps`, diccionario del libro y de la serie), Informe (`quality_off`,
  `quality_levels`); Apariencia: `accent`, `ui_font`. Cada opción nueva con su efecto y su prueba.
- [ ] T21 `ui_font` 11 pt: `fit_ribbon` a 1366 px; si no cabe, máximo 10 pt.
- [ ] Cierre de entrega 4.
