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
