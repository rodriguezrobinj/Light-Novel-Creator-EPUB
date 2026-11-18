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
