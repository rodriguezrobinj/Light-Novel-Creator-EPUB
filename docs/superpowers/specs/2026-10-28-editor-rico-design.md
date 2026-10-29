# Editor de texto rico — Diseño

Fecha: 2026-10-28

Subproyecto 1 de 4 de la edición visual del EPUB. Orden acordado: 1. editor de texto rico,
2. ayudas y avisos guiados, 3. imágenes, 4. editar sobre la vista previa. Cada uno tiene su spec y su plan.

## Objetivo

Que corregir el texto de una sección sea cómodo y visual, comparando con el PDF original, sin
aprender las marcas del formato. Hoy el botón «Editar texto…» (tecla E) abre un `tk.Text` plano con
las marcas `# Título`, `[Ilustración N]`, `[Separador N]`, `<i>` y `<b>` sin destacar, y sin ver el PDF.

### Criterios de éxito

- Títulos, cursivas, negritas, ilustraciones y separadores se distinguen de un vistazo en el editor.
- Cursiva, negrita, título, separador, unir y partir párrafos se hacen con un botón o un atajo, sin
  escribir marcas a mano.
- Al lado del texto se ve la página del PDF del párrafo del cursor, con el párrafo recuadrado.
- Aceptar sin cambios deja el libro igual. El resultado sigue pasando por `convert.edit_section`.

## Fuera de alcance

- Avisos de problemas dentro del editor (subproyecto 2).
- Editar imágenes: girar, recortar, reemplazar (subproyecto 3).
- Vista previa del EPUB dentro del editor y búsqueda propia del editor (ya existe Ctrl+F en el libro).
- Cambios en `convert.py`: el formato de `section_text` / `edit_section` no cambia.

## Decisiones

- **Marcas visibles y coloreadas**, no ocultas: con `<i>` oculto, al escribir en el borde de una cursiva
  no se sabe si el texto queda dentro o fuera.
- **Al lado, solo la página del PDF sincronizada.** Una vista del EPUB obligaría a generar un EPUB de
  prueba en el proceso de trabajo en cada actualización.
- **Sin miniaturas dentro del texto.** Las imágenes incrustadas en un `tk.Text` entran en la pila de
  deshacer y se pierden al cortar y pegar. La ilustración se ve en el panel lateral.
- **Se mejora la ventana actual** (`Toplevel`), no se mete el editor en la ventana principal: a 1366 px
  no cabe junto a la lista y las vistas previas.

## Diseño

### Ventana

`App.edit_text` sigue abriendo un `Toplevel` «Texto · <título>», más grande (a la medida de la pantalla,
como la ventana principal). De arriba abajo:

1. Barra de herramientas (botones de la sección «Acciones»).
2. Centro: a la izquierda el `tk.Text` con su barra de desplazamiento; a la derecha el panel del PDF
   (lienzo de proporción 3:4 y botones ◀ ▶ con «Página N»).
3. Barra de estado: «Página 34 del PDF · párrafo 12 de 80 · 3.412 palabras».
4. Botones «Aceptar» y «Cancelar».

La línea de ayuda actual sobre el formato se conserva, más corta, porque los botones ya cubren lo común.

### Resaltado

Una función recorre todo el texto y pone tags de `tk.Text`. Se ejecuta al abrir y 300 ms después del
último cambio (`<<Modified>>` con `after`, cancelando el anterior). Solo cambia tags, no el texto, así
que no entra en la pila de deshacer.

| Tag | Qué marca | Aspecto |
|---|---|---|
| `heading` | Línea que empieza por `# ` | Negrita, 1,3 veces el tamaño, azul |
| `mark` | `<i>`, `</i>`, `<b>`, `</b>` | Gris tenue |
| `italic` | Texto entre `<i>` y `</i>` | Cursiva |
| `bold` | Texto entre `<b>` y `</b>` | Negrita |
| `token` | Línea `[Ilustración N]` o `[Separador N]` (`convert.TOKEN`) | Fondo de color suave |
| `scene` | Línea de separador escrito (`convert.SCENE_BREAK`) | Gris, centrada |

Una cursiva dentro de una negrita lleva los dos tags. Para eso hace falta una fuente de cursiva y
negrita a la vez, además de las dos sencillas.

### Acciones

| Botón | Atajo | Efecto |
|---|---|---|
| Cursiva | Ctrl+I | Envuelve la selección en `<i>…</i>`. Si la selección ya está justo entre `<i>` y `</i>`, quita las marcas. Sin selección: inserta `<i></i>` con el cursor dentro. |
| Negrita | Ctrl+B | Lo mismo con `<b>`. |
| Título ↔ párrafo | Ctrl+T | Pone o quita `# ` al inicio de la línea del cursor. |
| Separador | — | Inserta `* * *` como párrafo propio después de la línea del cursor. |
| Unir con el siguiente | Ctrl+J | Junta la línea del cursor con el siguiente párrafo no vacío, con un espacio. No une con un título, una marca ni un separador. |
| Partir aquí | Ctrl+Enter | Parte la línea en el cursor en dos párrafos. Quita los espacios del corte. |

Cada acción es un solo paso de deshacer (`edit_separator` antes y después). Ctrl+Z y Ctrl+Y siguen
siendo los de `tk.Text`. Los atajos devuelven `"break"` para que Tk no haga además lo suyo: Ctrl+I
inserta un tabulador, Ctrl+T intercambia letras.

«Cancelar», el aspa de la ventana y Escape preguntan antes de descartar si el texto cambió.
«Aceptar» llama a `App.apply_edit` como hoy.

### Panel del PDF

**Origen de cada párrafo.** Al abrir, cada línea que viene de un bloque `k` recibe el tag `b<k>`. Los
tags de `tk.Text` se mueven con el texto. Para la línea del cursor, el bloque es el de su tag `b<k>` o,
si no tiene (línea nueva), el de la línea anterior más cercana que lo tenga. Sin ninguno, el primer
bloque de la sección.

**Qué se muestra.** Al mover el cursor (`<KeyRelease>`, `<ButtonRelease-1>`, 150 ms después del último
movimiento):

- Bloque de imagen (`[Ilustración N]`): la imagen del bloque, ajustada al lienzo.
- Cualquier otro: la página `blocks[k].page` del PDF abierto (`App.pdf_doc`), dibujada como en
  `App.render`. Se buscan en la página las primeras 6 palabras del texto **original** del bloque, guardado
  al abrir el editor. Si aparecen, se recuadran todas las líneas desde ese punto hasta el final del
  párrafo (las primeras 6 palabras de su última línea). Si no se encuentran, la página va sin recuadro.

Los botones ◀ ▶ hojean páginas sin mover el cursor. El siguiente movimiento del cursor vuelve a la página
de su párrafo. Sin PDF abierto (revisión de un libro cuyo PDF ya no está), el panel muestra el aviso
tenue de `App.blank`.

La página se dibuja en la ventana, no en el proceso de trabajo: es una sola página, igual que la vista
previa actual.

### Código

Todo en `app.py`. Para no inflar `App`, el editor pasa a una clase propia, `TextEditor`, que recibe la
`App` y el rango de la sección, y crea su `Toplevel`. `App.edit_text` solo comprueba la selección y la
crea. Partes de `TextEditor`:

- `highlight()`: pone los tags de la tabla de resaltado.
- `toggle_mark(tag)`, `toggle_heading()`, `insert_break()`, `join_next()`, `split_here()`: las acciones.
- `source_block()`: bloque de origen de la línea del cursor.
- `show_source()`: dibuja el panel.
- `dirty()`, `save()`, `cancel()`.

`App.editor_save` no se toca: es del cuadro para editar un título dentro de la lista, no de este editor.

## Pruebas

En `test_convert.py`, con `visible_window()` y libros sintéticos como los tests de ventana actuales:

- Al abrir el editor de una sección con título, cursiva e ilustración: la línea `# ` tiene el tag
  `heading`, el texto entre `<i>` tiene `italic` y la marca tiene `mark`. La línea `[Ilustración N]`
  tiene `token`.
- Ctrl+I con una palabra seleccionada la envuelve; otra vez, la desenvuelve.
- Ctrl+T alterna `# `. Ctrl+J une dos párrafos con un espacio. Ctrl+J sobre un título no hace nada.
  Ctrl+Enter parte la línea.
- Una acción se deshace con un solo Ctrl+Z.
- Con el cursor en un párrafo de la página N, el panel muestra la página N. Con el cursor en una línea
  nueva escrita debajo, también la N. Sobre `[Ilustración k]`, la imagen del bloque k.
- «Aceptar» sin cambios deja `book.blocks` igual (mismos `kind`, `html` y `page`).
- «Cancelar» con cambios pregunta. Se prueba sustituyendo `messagebox.askyesno`.

Después, `/build-exe` y una prueba a mano con una novela real de `Novelas PDF/`.
