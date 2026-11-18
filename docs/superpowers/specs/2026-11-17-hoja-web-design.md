# Hoja web editable (fase A de la opción C) — diseño

Fecha: 2026-11-17. Aprobado por el usuario en el chat. Entregas: A1 escribir y dar formato (esta), A2 corrección
(ortografía, avisos, cambios con la API de resaltados de CSS), A3 resto del editor (buscar, voz, sincronía con el
original, navegación, borrador), A4 la hoja web por defecto.

## A1

- **Modelo**: no cambia. La vista «Hoja web» (Vista → Hoja web) se carga desde las líneas de la hoja de Qt
  (`editor.dump`) y vuelve a ellas (`websheet.lines_of` → `Sheet.set_text`, un deshacer) al volver a la hoja, al
  guardar y al cambiar de capítulo. El escritor fiel, el borrador, las versiones y la revisión siguen igual.
- **Página** (`websheet.page`): cada línea como su elemento (`editor.to_code`); ilustraciones y separadores como
  bloques `contenteditable="false"` con su marca (`data-token`). Con un EPUB de entrada, la página ocupa el sitio de
  su archivo en el zip, con su `<head>`: su CSS, fuentes e imágenes se resuelven como en el libro. Con un PDF, la
  hoja de estilo de la app.
- **Canónico**: un `<p>` o `<h1>` sin atributos no lleva prefijo en la línea (también al leer un EPUB), así cargar y
  volver sin tocar da las mismas líneas (583 de 583 en el capítulo 1 del Date A Live).
- **Lo que ensucia el navegador** (`websheet.SHEET_JS`, `clean`): Intro con `insertParagraph` nativo y sin repetir
  ids; pegar solo deja cursiva, negrita, enlaces web y saltos; al volver, `<strike>`→`<s>`, sin `<font>`, `style`
  normalizado (sin `caret-color`, `0px`→`0`, sin `;` final), `&nbsp;` como espacio, sin bloques dentro de un párrafo.
- **Cinta**: sus botones piden la acción al momento (`TextEditor.do`): en la hoja web van a `window.lnc` (execCommand,
  `retag`, `align`, `indent`, `size` con una letra falsa que pasa a `font-size` en em, `ruby`). Lo que aún no hace
  (versalitas, recuadro, lista, unir, separador, mayúsculas) lo dice en la barra. La cinta sigue a la selección con un
  sondeo de `lnc.state()` (JSON: un objeto de JavaScript no llega a Python).

## Pruebas

Limpieza y vuelta a líneas; carga y vuelta sin cambios; negrita, Título 3, centrar, Intro sin ids repetidos, pegar
desde Word y la cinta que sigue a la negrita, hasta el EPUB. En pantalla: clic y teclas reales, botón N, vuelta,
aceptar y generar.

## A2

- **Marcas** con la API de resaltados de CSS (`CSS.highlights`, `::highlight`): no tocan el HTML del libro.
  `TextEditor.web_marks` calcula lo mismo que la hoja de Qt (palabras que no están, avisos con su trozo exacto y su
  color, párrafos cambiados) y lo manda a `lnc.setMarks`; cada aviso se busca en su párrafo por su texto, cerca de su
  posición. Un párrafo editado pierde sus avisos (ya no valen) y pasa a ser un cambio; se repinta 300 ms después de
  escribir.
- **Ortografía en vivo**: el sondeo de la cinta recoge el texto si cambió (`lnc.takeText`) y lo manda al proceso de
  ortografía; las palabras nuevas llegan a la hoja web con `lnc.setSpell`.
- **Clic derecho** (`WebSheet.menu_at` → `TextEditor.web_menu`): `lnc.at(x, y)` dice la palabra y los avisos de ese
  punto; sugerencias (cambian la palabra con `lnc.replaceWord`), ignorar, añadir al diccionario, explicación y arreglo
  de cada aviso (sobre la hoja de Qt, `via_sheet`) y deshacer, cortar, copiar, pegar.
- **F7/F8** en la hoja web: el cambio o el aviso siguiente (`lnc.next`); sin más avisos en el capítulo, F8 sigue en la
  hoja con los siguientes.

## A3

- **Sitio de cada párrafo** (`lnc.home`, un `WeakMap`): su número al cargar o al volcar a la hoja de Qt. La hoja de
  Qt, oculta, sigue con esas líneas hasta volcar, así que un párrafo escrito antes no descoloca nada: uno nuevo toma el
  sitio del anterior. `lnc.state()` lo da (`home`), y el sondeo pone el cursor de la hoja de Qt en ese bloque
  (`TextEditor.web_block`); de ahí el panel del original dibuja su página como siempre. Al revés (`web_index`,
  `lnc.goHome`): un clic en el original, el panel de navegación y la carga de la hoja web llevan su cursor al párrafo.
- **Buscar y reemplazar**: la misma expresión de Python (`find_pattern`) sobre el texto de cada párrafo
  (`lnc.texts`), y el resultado se elige en Chromium (`lnc.select`). Sin más en el capítulo, se abre el siguiente con
  resultados y, ya cargado, se elige el primero (`web_then`). Reemplazar cambia lo elegido si es un resultado
  (`lnc.replaceSel`, `\1` con `m.expand`); «Reemplazar todo» y «Todos» vuelcan antes a la hoja de Qt.
- **Leer en voz alta**: `lnc.readAt` da el párrafo con texto desde el cursor, con fondo (resaltado `lnc-reading`) y
  el cursor al principio; `self.reading` es su número. Un clic en otro párrafo la calla.
- **Borrador**: lo escrito en la web (`takeText`) pide el borrador; el capítulo de ahora va con el volcado de la web.
- **Cancelar** pregunta también por lo escrito solo en la web: antes consulta `lnc.state()` (`dirty`:
  `lnc.changedAny`). Cerrar la ventana principal usa el último sondeo (400 ms).
- Ctrl+F, Ctrl+H y F9 también desde la hoja web.

## A4

- **Por defecto**: el editor abre en la hoja web si Chromium está (`settings["editor_web"]`, `True` si falta); apagarla
  con Vista → Hoja web se recuerda. «Contenido» (la lista de capítulos) va en la hoja de Qt: al llegar a él se deja la
  web (`web_resume`) y vuelve en el capítulo siguiente.
- **Lo que Chromium no hace** (versalitas, recuadro, listas, unir, separador, mayúsculas, título con Ctrl+T, enlace,
  dividir, imagen, tipografía, volver al original) pasa por la hoja de Qt (`on_sheet`): se vuelca la web con su
  selección (`web_to_sheet`, posiciones con `websheet.positions`: la hoja de Qt tiene de más saltos de línea, marcas
  vacías y objetos), se hace allí y se vuelve a cargar la web con la selección (`sheet_to_web`).
- **Deshacer**: si Chromium no tiene nada que deshacer (`queryCommandEnabled('undo')`), Ctrl+Z (tecla, botón o
  sondeo de `lnc.wanted`) deshace en la hoja de Qt: lo hecho por `on_sheet` y lo escrito antes de recargar.
- **Atajos** de la hoja que Chromium no tiene (Ctrl+T, Ctrl+J, Ctrl+D, Mayús+F3, Ctrl+Alt+K, Ctrl+Intro, símbolos,
  super y subíndice, tamaño) como `QShortcut` de la hoja web. La cinta marca también Versalitas y Recuadro.
- **Zoom y ancho**: la barra de zoom agranda la hoja web (`setZoomFactor`) y le da el ancho de página de la hoja.
- **Marcas propias en un EPUB fiel**: `<small class="sc">`, `div.marco`, `p.center`… no tienen regla en el CSS del
  original. Con el aspecto «Original», `write_faithful` añade `convert.MARKUP_CSS` (en `lnc/estilo.css`) solo si un
  texto cambiado usa esas clases (sin cambios sale igual byte a byte); la hoja web la lleva siempre.

## Revisión tras A4 (recorrido de todos los botones)

- **Estilo, alineación y sangría** del párrafo pasan por la hoja de Qt (`on_sheet`): en Chromium se cambiaba su
  atributo a mano, fuera de su deshacer, y Ctrl+Z no los quitaba. Ctrl+E y Ctrl+D son atajos de Qt. Se quitan
  `retag`, `align` e `indent` de `window.lnc`.
- **Cola de `on_sheet`** (`web_busy`, `web_queue`): mientras la web se recarga, lo pulsado espera; si no, volcaba la
  página vieja y pisaba lo anterior. La web queda lista (`web_settled`) cuando su selección ya está puesta.
- **Respuestas tardías**: `WebSheet.call` no hace nada con lo que Chromium conteste tras cerrar el editor
  (`closing`); antes llegaba a botones ya borrados.
- **Clic derecho**: también Comentario…, Volver al original, Cambiar ilustración…, Sinónimos y Aviso siguiente;
  `lnc.at` pone el cursor donde se pulsó (fuera de lo elegido). «Arreglar» un aviso conserva la selección.
- **Panel de navegación** (`web_nav`, `lnc.outline`) y **barra de «Mostrar cambios»** (texto del párrafo en
  `lnc.state`) siguen lo escrito en la web. Cerrar la ventana principal pregunta a la web en el momento (`sync_web`).
- **Panel del original con un EPUB** (`source_page`): un bloque solo sabe la primera página de su archivo; su
  texto se busca en las de ese archivo (con caché), y un clic en la página va al párrafo de la línea pulsada.
