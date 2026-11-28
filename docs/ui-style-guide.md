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
| `bg` | `#eef0f3` | `#202020` | Fondo de ventana y diálogo |
| `card` | `#ffffff` | `#2b2b2b` | Tarjetas, barras, listas y tablas |
| `base` | `#ffffff` | `#1f1f1f` | Fondo de campos de texto y casillas |
| `line` | `#d9dce1` | `#3d3d3d` | Bordes de tarjetas y separadores |
| `edge` | `#9ea4ac` | `#4a4a4a` | Bordes de botones, campos y casillas |
| `ink` | `#16181b` | `#ececec` | Texto normal |
| `muted` | `#545a63` | `#a0a0a0` | Texto de apoyo: subtítulos, estado, cabeceras |
| `hint` | `#80868f` | `#7a7a7a` | Solo lo desactivado y el texto de ejemplo |
| `acc` / `acc_hover` / `acc_press` | `#0f5fc2` / `#0c4fa3` / `#093f84` | `#7099f5` / `#85a9f7` / `#5b86ea` | Acción principal, enlaces, foco, pestaña activa (acento configurable en `ACCENTS`) |
| `acc_ink` | `#ffffff` | `#111111` | Texto sobre `acc` |
| `sel` | `#d6e6fb` | `#2f3d5c` | Fila o botón elegido |
| `hover` / `press` | `#eef3fa` / `#dde7f5` | `#353535` / `#3d3d3d` | Botón con el ratón encima / pulsado |
| `warn` | `#c62828` | `#ff6b6b` | Error o algo grave |
| `ok` | `#2e7d32` | `#7bc47f` | Hecho o correcto |
| `scroll` / `scroll_hover` | `#c1c6ce` / `#9aa1ab` | `#555555` / `#757575` | Tirador de barras de desplazamiento Fluent |

Reglas:

- **El color no va solo.** Un estado lleva también texto o glifo (`▲ 3 avisos`, `✓ Sin avisos`), para quien
  no distingue colores.
- **Niveles de aviso:** `Grave` (rojo), `Revisar` (naranja), `Menor` (gris azulado). Los mismos en el
  informe de calidad, el editor y los avisos del botón (`theme.level_colors`, `theme.mark_colors`).
- **Estados de párrafo** de «Traducir»: `theme.state_colors`. **Colores de fila** de la lista de
  capítulos: `theme.kind_colors` (ilustración en violeta y separador en pardo: el verde ya es «correcto» y el
  naranja «Revisar»). **Lo que se ve ahora** (miniatura, recuadro del título en la vista del PDF, párrafo en el
  editor) va en naranja `MARK_COLORS["current"]`, nunca en rojo, que es error. **Marcas del editor y comparaciones:** `theme.editor_tags`,
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
| `accent` | Relleno `sel`, borde `acc`, seminegrita | Acción destacada dentro de una barra contextual (sin competir con el `primary` de la ventana) |
| `warn` | Texto rojo en botón o etiqueta | Algo grave o destructivo; lleva siempre su texto |
| `ok` | Texto verde | Confirmación pequeña |
| `chip` | Botón-etiqueta redondo, marcable | Filtros que se activan y desactivan. Marcado = fondo `sel` y borde `acc`, sin seminegrita (el texto más ancho se cortaba). La cuenta entre paréntesis: «Todos (12)» |
| `link` | Texto `acc` subrayado, sin borde | Acción secundaria dentro de un texto: «¿Muchos tomos? …» |
| `tool` | Botón de la cinta del editor, sin borde | Solo en `ribbon`; marcado = `sel` con borde `acc`. Grande (`icon=`): glifo de `GLYPH` px arriba y nombre abajo, para lo principal de un grupo |
| `sep` | Línea vertical de 1 px, color `line` | Entre grupos de una pestaña de la cinta o paneles paralelos |
| `card` | Tarjeta: `card`, borde `line`, radio 8 | Agrupar contenido relacionado |
| `bar` / `topBar` / `bottomBar` | Barra fondo `card`, con borde inferior (`topBar`) o superior (`bottomBar`) | Barras de arriba y de abajo de la ventana y del editor |
| `ribbon` | Cinta con línea inferior | Barras del editor (buscar, enlace, comparar) |
| `drop` | Zona de soltar, borde discontinuo | Solo la pantalla de inicio |
| `hit` | Resaltado suave (`sel`, radio 6) | Coincidencia de búsqueda en Opciones |
| `muted` / `title` / `big` / `key` | Etiquetas | Ver colores y tipografía; `key` dibuja una tecla en «Atajos de teclado» |
| `section` | Etiqueta en seminegrita, color del texto | Nombre de un apartado dentro de un panel: «Problema», «Solución» |

- Los campos (`QLineEdit`, `QComboBox`, `QSpinBox`) ya traen borde, radio 5 y foco azul: no se retocan.
- **Foco con teclado:** botones y casillas solo se marcan en azul al llegar con Tab (`theme._Shown` pone la propiedad `keyfocus`). Al abrir una ventana o tras un clic no se marcan: parecía que algo estaba elegido.
- **Alineación** (izquierda, centro, derecha, justificado): cuatro rayas dibujadas (`app.ALIGN_LINES`), como Word.
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
- Botones en una fila abajo, con `1` a la izquierda para empujarlos a la derecha. Estándar Windows Fluent: **el botón `primary` va a la izquierda de «Cancelar»** (o de «Cerrar») y lleva el verbo de la acción: «Aceptar», «Reemplazar», «Usar esta forma». Todo diálogo editable lleva «Cancelar» explícito y toda herramienta de consulta lleva «Cerrar».
- **Botón «Cerrar» en herramientas de consulta:** «Cerrar» es `primary` solo si es la única acción del diálogo (como «Atajos de teclado»); si hay otras acciones (como en «Ilustraciones», «Ortografía», «Informe de calidad» o «Versiones»), «Cerrar» va como secundario.
- Un aviso previsto usa `messagebox` de `qtk`; el título dice qué pasa, el cuerpo qué hacer.
- La ventana pone su estado en una línea `muted` abajo, no en un cuadro que haya que cerrar.
- **Listas de tamaño fijo:** la lista de capítulos y las de los diálogos no se ensanchan arrastrando (`app._lock`); el reparto lo dan los factores de estiramiento.
- «Editar datos» ocupa el sitio de las vistas previas, nunca encima de la lista.
- «Opciones…» vive solo en Herramientas (y Ctrl+,): ni en la bienvenida ni repetido en la barra.
- Lo que va encima de una barra de abajo (las flechas y el zoom del panel del editor) deja 8 px hasta ella: con 0 la tocaba.
- El resultado de un trabajo que tardó (Ortografía, AniList) no se lleva el foco: si mientras tanto se abrió otra herramienta, sale detrás (`App.show_late`) y la línea de estado dice dónde quedó.

## 7. Iconografía

Un icono es un **glifo de texto monocromo** que toma el color del tema. Los glifos viven en
`theme.ICONS`; un glifo = una acción en toda la app.

| Clave | Glifo | | Clave | Glifo | | Clave | Glifo |
|---|---|---|---|---|---|---|---|
| `warn` | ▲ | | `undo` | ↶ | | `search` | ⌕ |
| `ok` | ✓ | | `redo` | ↷ | | `read` | ▷ |
| `close` | ✕ | | `note` | ✎ | | `link` | ↗ |
| `menu` / `menu_open` | ▾ / ▴ | | `split` | ✂ | | `doc` | 🗎 |
| `prev` / `next` | ◀ / ▶ | | `join` | ⤒ | | `dot` | ● |
| `first` / `last` | ◀◀ / ▶▶ | | `center` / `left` | ≡ / ⇤ | | `minus` | − |
| `nav` | ☰ | | `frame` | ▭ | | `right` / `justify` | ⇥ / ≣ |
| `scene` | ❖ | | `symbols` | Ω | | `changes` | ± |
| `stats` | Σ | | `show` | ◉ | | `image` | ▣ |
| `indent` | ↦ | | `ruby` | ふ | | `newline` | ↵ |
| `code` | `</>` | | `clear` | ⌫ | | `web` | ◎ |
| `style` | ¶ | | `table` | ▦ | | `inlink` / `files` | ⤷ / ▤ |

Reglas:

- **Flechas de pasar página** de las vistas y del editor: las dibuja `qtk.ArrowButton` (‹ › y « »), no son glifos de texto; `prev` / `next` quedan para texto.
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
