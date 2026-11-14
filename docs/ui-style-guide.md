# Guía de estilo de la interfaz

Formato fijo de «Light Novel Creator»: antes de decidir un color, un margen o un icono, mira aquí. Los
valores viven en `theme.py` (colores, `SPACE`, `FONT`, `DIALOG`, `ICONS`); esta guía dice cuándo usar
cada uno. `test_style_guide_tokens_icons_and_no_loose_colors` falla si entra un emoji o un color suelto.

## 1. Principios

- **Fusion + Fluent**, claro y oscuro. Fusion dibuja todos los controles: la ventana se ve igual en
  Windows 10 y 11. Nada de estilo nativo.
- **Todo cambia con el tema.** Un color nuevo se define en `LIGHT` y en `DARK` a la vez, y se lee con
  `theme.colors(dark)`. Nunca un hex en `app.py`, `editor.py` ni `qtk.py`.
- **Texto legible:** todo texto llega a 4,5:1 (WCAG AA) sobre su fondo, en los dos temas
  (`test_theme_text_is_readable`). Solo lo desactivado (`hint`) queda fuera.
- **Toda la interfaz en español** con sus acentos, también los avisos de `check_epub` y `quality_report`.
- **Un solo botón principal por ventana.** Lo demás es secundario.

## 2. Colores

Se leen por rol, no por valor. Claro / oscuro:

| Rol | Claro | Oscuro | Cuándo |
|---|---|---|---|
| `bg` | `#f3f3f3` | `#202020` | Fondo de ventana y diálogo |
| `card` | `#ffffff` | `#2b2b2b` | Tarjetas, barras, listas y tablas |
| `base` | `#ffffff` | `#1f1f1f` | Fondo de campos de texto y casillas |
| `line` | `#e1e1e1` | `#3d3d3d` | Bordes y separadores |
| `ink` | `#1b1b1b` | `#ececec` | Texto normal |
| `muted` | `#6b6b6b` | `#a0a0a0` | Texto de apoyo: subtítulos, estado, cabeceras |
| `hint` | `#9a9a9a` | `#7a7a7a` | Solo lo desactivado y el texto de ejemplo |
| `acc` / `acc_hover` | `#2a62c8` / `#2353ab` | `#7099f5` / `#85a9f7` | Acción principal, enlaces, foco, pestaña activa |
| `acc_ink` | `#ffffff` | `#111111` | Texto sobre `acc` |
| `sel` | `#dce8fc` | `#2f3d5c` | Fila o botón elegido |
| `hover` / `press` | `#ececec` / `#e2e2e2` | `#353535` / `#3d3d3d` | Botón con el ratón encima / pulsado |
| `warn` | `#c62828` | `#ff6b6b` | Error o algo grave |
| `ok` | `#2e7d32` | `#7bc47f` | Hecho o correcto |

Reglas:

- **El color no va solo.** Un estado lleva también texto o glifo (`▲ 3 avisos`, `✓ Sin avisos`), para quien
  no distingue colores.
- **Niveles de aviso:** `Grave` (rojo), `Revisar` (naranja), `Menor` (gris azulado). Los mismos en el
  informe de calidad, el editor y los avisos del botón (`theme.level_colors`, `theme.mark_colors`).
- **Estados de párrafo** de «Traducir»: `theme.state_colors`. **Colores de fila** de la lista de
  capítulos: `theme.kind_colors`. **Marcas del editor y comparaciones:** `theme.editor_tags`,
  `theme.diff_tags`. No se repiten en otro sitio.
- Un color que no cambia con el tema (borde de miniatura, barrita de cambios) va en una constante con
  comentario y se añade a la lista del test.

## 3. Tipografía

Una sola familia, **Segoe UI**; los tomos del lector usan Literata o Bitter, solo dentro del EPUB.

| Token | Tamaño | Uso | En QSS |
|---|---|---|---|
| `FONT["body"]` | 9 pt | Todo el texto de la ventana | `QWidget` |
| `FONT["title"]` | 11 pt, seminegrita | Título de un bloque o tarjeta | `QLabel#title` |
| `FONT["big"]` | 13 pt, seminegrita | Un solo encabezado por pantalla | `QLabel#big` |

- Énfasis con seminegrita (600), no con mayúsculas ni subrayado. Subrayado solo en `link`.
- Texto de apoyo: `muted`, nunca un gris propio.
- El tamaño del texto de la hoja del editor lo da el zoom (`editor.FONT_SIZE`), no esta tabla.

## 4. Espaciado

Cuatro valores, en píxeles:

| Token | px | Uso |
|---|---|---|
| `SPACE["tight"]` | 4 | Entre glifo y texto, dentro de una barra de iconos |
| `SPACE["gap"]` | 6 | Entre controles de una fila (el valor por defecto de `_row`) |
| `SPACE["pad"]` | 8 | Margen interior de una tarjeta o de una zona |
| `SPACE["section"]` | 12 | Entre bloques de una ventana; margen de un diálogo |

- Una fila se arma con `_row(...)`: un número es un hueco elástico con ese peso (`1` empuja a la derecha).
- Los márgenes viejos (2, 10…) se quedan donde están; el código nuevo usa estos cuatro. Si algo no cuadra,
  se cambia el diseño, no se inventa un quinto valor.

## 5. Componentes

Se eligen por nombre de objeto (`_button(texto, acción, nombre)`, `_label(texto, nombre)`,
`QFrame(objectName=…)`).

| Nombre | Qué es | Cuándo |
|---|---|---|
| (sin nombre) | Botón secundario: borde, radio 6 | Cualquier acción que no sea la principal |
| `primary` | Relleno `acc`, seminegrita | La acción que cierra la tarea: «Generar EPUB…», «Aceptar». **Uno por ventana**, abajo a la derecha |
| `warn` | Texto rojo en botón o etiqueta | Algo grave o destructivo; lleva siempre su texto |
| `ok` | Texto verde | Confirmación pequeña |
| `chip` | Botón-etiqueta redondo, marcable | Filtros que se activan y desactivan |
| `link` | Texto `acc` subrayado, sin borde | Acción secundaria dentro de un texto: «¿Muchos tomos? …» |
| `tool` | Botón de la cinta del editor, sin borde | Solo en `ribbon`; marcado = `sel` con borde `acc`. Grande (`icon=`): glifo de `GLYPH` px arriba y nombre abajo, para lo principal de un grupo |
| `sep` | Línea vertical de 1 px, color `line` | Entre grupos de una pestaña de la cinta |
| `card` | Tarjeta: `card`, borde `line`, radio 8 | Agrupar contenido relacionado |
| `bar` | Barra sin borde, fondo `card` | Barras de arriba y de abajo de la ventana |
| `ribbon` | Cinta con línea inferior | Barras del editor (buscar, enlace, comparar) |
| `drop` | Zona de soltar, borde discontinuo | Solo la pantalla de inicio |
| `muted` / `title` / `big` | Etiquetas | Ver colores y tipografía |

- Los campos (`QLineEdit`, `QComboBox`, `QSpinBox`) ya traen borde, radio 5 y foco azul: no se retocan.
- Estado de error en un campo: texto `warn` con `setStyleSheet` leyendo `theme.level_colors`, no un rojo propio.
- Un botón sin texto lleva siempre ayuda emergente.

## 6. Diálogos y ventanas

| Token | Mínimo | Para |
|---|---|---|
| `DIALOG["s"]` | 560 × 260 | Un texto o un campo: ver, pegar, confirmar |
| `DIALOG["m"]` | 780 × 440 | Una lista o tabla con su vista previa |
| `DIALOG["l"]` | 980 × 600 | Una herramienta de trabajo: «Traducir», «Glosario y estilo», comparar |

- Entran en una pantalla de 1366 px con el zoom del 125 % (como «Opciones», 860 × 560). Lo que no cabe se
  desplaza, no se corta.
- Botones en una fila abajo, con `1` a la izquierda para empujarlos a la derecha. **Cancelar** va a la
  izquierda del botón `primary`, que lleva el verbo de la acción: «Aceptar», «Reemplazar», «Usar esta
  forma». Si el cuadro solo muestra algo, basta «Cerrar»; es `primary` si es lo único que se puede pulsar.
- Un aviso previsto usa `messagebox` de `qtk`; el título dice qué pasa, el cuerpo qué hacer.
- La ventana pone su estado en una línea `muted` abajo, no en un cuadro que haya que cerrar.

## 7. Iconografía

Un icono es un **glifo de texto monocromo** que toma el color del tema. Los glifos viven en
`theme.ICONS`; un glifo = una acción en toda la app.

| Clave | Glifo | | Clave | Glifo | | Clave | Glifo |
|---|---|---|---|---|---|---|---|
| `warn` | ▲ | | `undo` | ↶ | | `search` | ⌕ |
| `ok` | ✓ | | `redo` | ↷ | | `read` | ♪ |
| `close` | ✕ | | `note` | ✎ | | `link` | ↗ |
| `menu` / `menu_open` | ▾ / ▴ | | `split` | ✂ | | `doc` | 🗎 |
| `prev` / `next` | ◀ / ▶ | | `join` | ⤒ | | `dot` | ● |
| `first` / `last` | ◀◀ / ▶▶ | | `center` | ≡ | | `minus` | − |
| `nav` | ☰ | | `frame` | ▭ | | `right` | ⇥ |
| `scene` | ❖ | | `symbols` | Ω | | `changes` | ± |
| `stats` | Σ | | | | | | |

Reglas:

- **Nunca un emoji.** Sale a color por Segoe UI Emoji, no sigue el tema y Windows 10 y 11 lo dibujan
  distinto. Qt ignora el selector `U+FE0E`, así que `⚠` o `⚙` salen a color aunque se pida texto: se
  cambia de glifo. Ya salieron a color `⚠ ⏮ ⏭ ⚙ ⛓ ❗ ⚓` y todo lo de `U+1F000` en adelante, salvo `🗎`.
- **Glifo nuevo:** se dibuja con la fuente de Windows sobre fondo transparente con tinta negra; si algún
  píxel sale con color, es emoji y no vale. En pruebas sin pantalla no hay fuentes: el test mira códigos.
- **Con texto:** fuera de la cinta, `«<glifo> Texto»`. En la cinta el glifo no va en el texto: lo dibuja
  `_glyph_icon` como icono escalado a su caja (`glyph=`, 16 px al lado del nombre; `icon=`, 24 px encima),
  porque como letra `≡`, `⇥` o `⤒` apenas se veían. Su forma corta (`compact_to`) deja solo el glifo y el
  nombre pasa a la ayuda emergente.
- **Glifo fino, palabra:** `⌕` y `↗` como letra se ven pálidos y pequeños (el aviso de contraste del guion de
  `/verificar-ventana` marcó 1,0:1 y 3,8:1): fuera de la cinta, su variante corta es la palabra. Un glifo
  nuevo se mira en la cinta antes de aceptarlo.
- **Cinta del editor:** pestañas como Word (Inicio, Insertar, Revisar, Vista), grupos separados por `sep` y con
  su nombre abajo. Lo pequeño va en dos filas; lo principal, en botón grande. El capítulo y ◀ ▶ van a la
  derecha de las pestañas, a la vista en todas.
- **Flechas de menú:** `▾` al final del texto de un botón que abre un menú (`_menu_button`); `▴` al
  abrirse un panel.
- **Tamaño:** el del texto. Solo la cinta del editor (16 y 24 px), la pantalla de inicio (`doc`, 40 pt) y la
  miniatura que falta (20 pt) lo agrandan.
- **Color:** el del texto del botón. Un icono de estado toma `warn`, `ok` o `muted` según el rol.
- No hay iconos de imagen salvo `icon.ico` (ventana y .exe) y las miniaturas de páginas.

## 8. Textos

- Frase normal: mayúscula solo al principio («Generar EPUB…», «Editar texto…»).
- **«…»** al final si el botón abre un cuadro para pedir algo; sin puntos suspensivos si actúa ya.
- Verbo en infinitivo para acciones («Guardar», «Quitar»); el nombre de un lugar para menús («Opciones»).
- Un aviso dice qué pasa y qué hacer, sin disculpas ni siglas: «Windows no tiene ninguna voz en español
  instalada. Instálala en…».
- Comillas latinas «…» para citar texto del libro o el nombre de un botón.
- Plurales con `convert.counted(n, "aviso")`, no a mano.

## 9. Qué no hacer

- Un hex en el código de la ventana, o `setStyleSheet` con un color fijo: se lee del tema.
- Un emoji como icono, o un glifo distinto para la misma acción.
- Dos botones `primary` en una ventana, o un `warn` que no diga qué pasará.
- Un margen o un tamaño de letra sueltos cuando hay un token.
- Estado solo por color.
- Texto en inglés o sin acentos.

## 10. Al cambiar este formato

1. Cambia el valor en `theme.py` (claro y oscuro a la vez).
2. Cambia esta guía en la misma rama.
3. Si hay un test que lo vigila, actualiza su lista permitida con un comentario de por qué.
4. `/build-exe` y `/verificar-ventana` en los dos temas.
