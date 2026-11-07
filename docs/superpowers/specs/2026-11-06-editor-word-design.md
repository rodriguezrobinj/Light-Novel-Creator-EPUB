# Editor de texto estilo Word — diseño

Fecha: 2026-11-06 · Aprobado en conversación el mismo día.

## Objetivo

El editor «Texto · Capítulo…» (`app.TextEditor`) se usa para corregir erratas, unir o partir párrafos, marcar
títulos y mover ilustraciones de una sección. Hoy muestra las marcas tal cual (`<i>…</i>`, `# Título`,
`[Ilustración 3]`) y la página del PDF de al lado es pequeña (unos 300×420 px). El usuario quiere que se
parezca a Microsoft Word y ver el PDF más grande.

Éxito: se edita como en Word (lo que se ve es lo que sale en el EPUB, sin marcas escritas), la página del PDF
se lee sin esfuerzo, y el libro queda igual que con el editor de ahora: aceptar sin cambios no toca nada.

## Lo que pidió el usuario y lo que se decidió

- WYSIWYG real: cursiva y negrita se ven, sin marcas; títulos grandes; ilustraciones como miniatura;
  separadores como su símbolo centrado. (Elegido entre «WYSIWYG real» y «solo el aspecto».)
- Página del PDF en un panel ajustable con zoom. (Elegido entre panel, pestañas y ventana aparte.)
- Decisiones de diseño aceptadas: atajos de Word en español (Ctrl+K cursiva, Ctrl+N negrita) además de
  Ctrl+I y Ctrl+B; pegar trae solo texto; una ilustración pegada dos veces se ignora; el editor va en un
  módulo nuevo `editor.py`.

## Fuera de alcance

Tamaños, fuentes y colores de letra, subrayado, alineación, buscar dentro del editor, paginación como Word.

## Ventana

- Se abre maximizada (`Dialog`, modal como ahora: la lista no cambia por debajo).
- Cinta «Inicio» arriba, con grupos y botones con icono y texto corto:
  - Portapapeles: Deshacer (Ctrl+Z), Rehacer (Ctrl+Y).
  - Fuente: **N** negrita (Ctrl+N, Ctrl+B), *K* cursiva (Ctrl+K, Ctrl+I). Botones marcables que siguen el
    formato del cursor, como en Word.
  - Párrafo: desplegable «Normal / Título» (Ctrl+T alterna) y «Unir párrafos» (Ctrl+J: une con el siguiente
    y deja un espacio entre las dos partes).
  - Insertar: «Separador» (❖): un separador de escena escrito como párrafo propio tras el cursor.
- Izquierda: la hoja. Fondo gris, hoja del color `sheet` del tema, centrada, de ancho fijo (como A5 a 100 %),
  con márgenes. Párrafos con espacio entre ellos.
- Derecha: panel del original, separado por un `QSplitter` que se arrastra (empieza 50/50). La página del
  PDF (o la ilustración) se ajusta al ancho del panel; zoom −/+ y Ctrl+rueda (50–300 %) con scroll; ◀ ▶
  hojean; el párrafo del cursor sigue recuadrado como ahora.
- Abajo: barra de estado como Word. Izquierda «Página 245 del PDF · párrafo 4 de 349 · 11.144 palabras»;
  derecha zoom del texto (−, deslizador 50–200 %, +, porcentaje). Luego Aceptar y Cancelar.

## Contenido WYSIWYG

Cada bloque de la sección es un párrafo del documento Qt (`QTextDocument`):

| Bloque / línea de `section_text` | En el editor |
|---|---|
| Párrafo con `<i>`/`<b>` | Texto con cursiva/negrita reales |
| `# Título` | Párrafo con formato «Título»: letra grande y negrita (propiedad de bloque `heading`) |
| `[Ilustración N]` | Párrafo centrado con una miniatura (alto ~160 px) de la ilustración N, objeto único |
| `[Separador N]` | Párrafo centrado con su símbolo (texto original o miniatura del adorno), objeto único |
| Línea de solo símbolos (`* * *`) | Párrafo centrado con ese texto (separador escrito) |
| `[Nota: …]` | Texto normal, pintado en gris por el resaltador |

Las ilustraciones y separadores del PDF son `QTextImageFormat` con nombre `bloque:N` (recurso del documento)
y propiedad con N. Se borran como un carácter y se mueven cortando y pegando.

Palabras de «Ortografía…» (`app.unknown`): subrayado ondulado rojo con un `QSyntaxHighlighter`, que solo
pinta y no cambia el texto ni entra en deshacer.

## Teclado

- Enter parte el párrafo (Shift+Enter igual: el formato no tiene saltos de línea dentro de un párrafo).
- Retroceso al principio de un párrafo lo une con el anterior; Supr al final, con el siguiente (Qt).
- Ctrl+Z / Ctrl+Y deshacer y rehacer (pila de Qt; cada acción de la cinta es un solo paso).
- Ctrl+K / Ctrl+I cursiva, Ctrl+N / Ctrl+B negrita, Ctrl+T título, Ctrl+J unir.
- Pegar: solo el texto plano; cada línea, un párrafo. Una ilustración o separador del PDF que ya está en la
  hoja no se duplica: al pegar la copia se ignora (al cortar y pegar, se mueve).

## Por dentro

`convert.py` no cambia. Módulo nuevo `editor.py` (Qt, sin PyMuPDF):

- `load(document, lines, images)`: llena el documento desde las líneas de `section_text` (una por bloque,
  con su índice de origen). `images`: {N: bytes} de ilustraciones y adornos.
- `dump(document) -> str`: el texto en formato `section_text` (párrafos con `<i>`/`<b>` bien anidados,
  `# ` delante de los títulos, `[Ilustración N]` / `[Separador N]`). Lo lee `edit_section` al aceptar.
- Cada párrafo lleva en su formato de bloque la propiedad `source` = índice del bloque del libro del que
  viene. Qt copia el formato de bloque al partir con Enter: el párrafo nuevo sigue apuntando a su página.
- `WordEdit(QTextEdit)`: pegar solo texto, objetos únicos, atajos, formato al escribir (sin arrastrar
  cursiva al párrafo siguiente si no está activa).

`app.TextEditor` se queda en `app.py` (ventana, panel del PDF, aceptar/cancelar) y usa `editor.py` para la
hoja. Mantiene su interfaz: `TextEditor(app, item, start, end)`, `win`, `item`, `start`, `end`, `dirty()`,
`save()`, `cancel()`, `draw_source()`, `shown`, `box`, `turn()`, `status`, `find_box()`.

- `dirty()`: compara `dump()` con el texto original, sin contar líneas en blanco ni espacios sobrantes.
- `save()`: solo si cambió, `app.apply_edit([start, end, dump()], item)` (igual que ahora).
- Bloque del cursor (`source_block`): la propiedad `source` del párrafo del cursor, o el objeto `bloque:N`
  de ese párrafo.

## Errores

- Un objeto de ilustración que ya no está en el libro (no debería pasar) sale como texto `[Ilustración N]`
  en gris y `edit_section` lo trata como hoy.
- Texto con `<` o `>` escritos: `dump` escribe exactamente lo que se ve; `edit_section` lo escapa como hoy.
- Sin el PDF original: el panel dice «Sin el PDF original no se puede mostrar la página», como ahora.

## Pruebas (`test_convert.py`, ventana sin pantalla)

- Ida y vuelta: `dump(load(section_text))` da el mismo texto para párrafos, cursiva, negrita anidadas,
  títulos, ilustraciones, separadores del PDF y escritos, notas, `&`, `<`. Aceptar sin cambios no toca el
  libro (páginas incluidas); cancelar con cambios pregunta.
- Cinta y atajos: cursiva y negrita sobre una selección y sin ella; título; unir; separador; un Ctrl+Z por
  acción.
- Enter parte y el párrafo nuevo conserva su `source`; Retroceso une.
- Mover una ilustración cortando y pegando; pegar una copia de una que ya está se ignora; pegar texto con
  formato de fuera trae solo texto.
- El cambio llega al libro y al EPUB.
- Panel: el PDF sigue al cursor y recuadra el párrafo; ilustración bajo el cursor; ◀ ▶; zoom; sin PDF.
- Tamaño: la página del PDF mide al menos el 40 % del ancho de la ventana del editor.
- Ortografía: las palabras desconocidas salen subrayadas.
- `/verificar-ventana`: fotos en los dos temas, a 1366×728 y con zoom 125 %.

Los tests del editor de ahora que usan índices de Tk (`editor.text.get("1.0"…)`) se reescriben con la
nueva interfaz y cubren lo mismo.
