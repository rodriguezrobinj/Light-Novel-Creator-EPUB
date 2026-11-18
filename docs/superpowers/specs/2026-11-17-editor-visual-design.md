# Editor visual tipo Word con vista de código — diseño

Fecha: 2026-11-17. Aprobado por el usuario en el chat.

## Problema

Con un EPUB abierto, el editor mostraba las etiquetas que no sabía dibujar (furigana, notas enlazadas, span con
clase) como texto en gris, y la cinta solo tenía cursiva, negrita, superíndice, versalitas y tres alineaciones. El
usuario quiere ver el texto como en el libro, sin código, con una cinta como la de Word, y además poder ver y editar
el HTML.

## Diseño

- **Etiquetas en cada letra.** `editor.parse_inline` lee una línea del editor como texto con su pila de etiquetas;
  cada letra guarda la suya (`STACK`). `visual()` calcula el aspecto (etiqueta + CSS del libro + `style`) y
  `look_format()` lo dibuja. Al volcar, `_wanted()` conserva las etiquetas cuyo formato sigue (las que la cinta pone o
  quita: i, b, u, s, sup, sub, sc, a) y todas las demás, y añade las que pide lo puesto con la cinta; `_serialize`
  las anida. Un párrafo sin tocar vuelve igual.
- **Vacías y saltos.** `<br/>` es U+2028 (Mayús+Intro); una etiqueta vacía (`<a id>`, `<span/>`) va en un carácter
  invisible (U+2060, `VOID`).
- **Etiqueta del párrafo.** Una línea puede empezar por la etiqueta de apertura de su elemento (`Block.opening`):
  `<p class="no-indent">`, `<h3>`, `<p style="text-align: justify">`. La hoja la guarda en `OPENING` y la dibuja con
  `paragraph_look` (CSS + style). Estilo, alineación y sangría la cambian (`_retag`, `_with_style`, `_restyle`). El
  escritor fiel cambia la etiqueta del elemento en su sitio; el normal usa `opening` para los párrafos de PDF.
- **CSS del EPUB.** `faithful.BookStyle`: reglas de etiqueta y clase y fuentes incrustadas (registradas en Qt con
  `editor.use_css`). Selectores compuestos y `@media` se ignoran.
- **Cinta.** Inicio: N, K, S, tachado, x², x₂, mayúsculas, versalitas; estilos Normal, Capítulo, Subtítulo, Título 3,
  Título 4, Cita; cuatro alineaciones. Formato (pestaña nueva): letra, A+/A−, color, borrar formato, sangría, salto de
  línea, furigana. Vista: «Código HTML».
- **Vista de código.** `to_code`/`from_code`: un elemento por línea; alineaciones de la app con sus clases,
  ilustraciones como comentario. Al volver se valida (etiqueta sin cerrar: aviso y no se sale) y `Sheet.set_text`
  lo pone en la hoja en un deshacer, cada párrafo igual con su bloque.

## Pruebas

Ida y vuelta de la hoja con etiquetas de EPUB y CSS; cada botón con el HTML que escribe y el deshacer; vista de código
de ida y vuelta y errores; estilos que llegan al EPUB (fiel y de PDF); ventana con la vista de código hasta el libro.
