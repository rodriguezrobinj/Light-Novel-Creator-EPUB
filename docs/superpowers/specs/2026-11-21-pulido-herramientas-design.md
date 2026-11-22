# Pulido de herramientas: menú, informe de calidad, ortografía y opciones

Fecha: 2026-11-21 · Rama: `feat/pulido-herramientas` (sobre `feat/explorador-archivos`)

El usuario revisó la app con un libro real (Backstabbed in a Backwater Dungeon) y pidió cuatro grupos de cambios. Se
hacen en cuatro entregas seguidas, cada una con sus pruebas, `/verificar-ventana` en pantalla real, `.exe` y commit.

## Entrega 1 · Arreglos y orden

### Menú Herramientas
- Fuera «Cambiar ilustración…» y «Describir ilustración…»: pasan a la ventana «Ilustraciones…». Su botón
  «Reemplazar…» se llama «Cambiar…»; nuevo botón «Describir…»; la descripción (texto alternativo, `self.alts`) se ve
  bajo la vista previa.
- Fuera «Instalar en este equipo» y «Desinstalar», y el código que solo usaban ellas (`install`, `uninstall` y sus
  pruebas). Habrá un instalador aparte en el futuro.
- Fuera «Añadir página…». En el editor: Insertar → Estructura → «Página nueva…», con Título, Texto y «Dónde» (Tras este
  capítulo · Antes del primer capítulo · Al final del libro). Al aceptar se guarda lo editado (como «Aceptar»), se
  inserta la página (`insert_page`) y el editor se abre en ella.

### Atajos de teclado
Ventana con una tabla por grupos (Lista de capítulos · Editor · General): cada atajo con su tecla dibujada como tecla
(borde redondeado, fondo del tema) y su acción. Incluye los del editor (F7, F8, F9, F11, Ctrl+Intro, Ctrl+F, Ctrl+H,
Ctrl+Alt+K, Ctrl+Z/Y…). Una sola tabla de datos (`SHORTCUTS`) la alimenta.

### Subtítulos sin etiquetas
El título de una fila se tomaba de `html.unescape(block.html)` sin quitar etiquetas (app.py `_prepare`, `fill_tree`,
`apply_edits`): un subtítulo en negrita salía como «<b>La Tienda…». Se usa el texto sin etiquetas (`convert._plain`)
en los tres sitios, y los títulos guardados con etiquetas en revisiones de antes se limpian al cargarlas (si no, el
«<b>» llegaba también al índice del EPUB).

### Código HTML
- Sangría por nivel de unos 20 px (antes, dos espacios: unos 6 px) y guías verticales finas por nivel, como VS Code.
- Las sub-líneas usan la misma sangría.

### Imágenes en el explorador de archivos
El visor de una imagen del EPUB lleva «Cambiar…», «Describir…» y «Exportar…» y enseña su descripción. La imagen se
reconoce en el libro: con un PDF por su nombre (`img/bNNNNN.ext` = bloque NNNNN), con un EPUB por `Block.ref`. Usa las
mismas funciones que «Ilustraciones…» (`replace_image`, `describe`, exportar) y, tras cambiarla, el explorador vuelve a
escribir su EPUB.

### Fallo «Sr. ¡Drago!»
El arreglo de «Falta el signo de apertura» (`_opening_marks`) tomaba el punto de una abreviatura (Sr., Sra., Dr., …,
`ABBREVIATIONS`) como fin de frase y ponía «¡» en medio: «¡Lo siento mucho, Sr. Drago!» → «Sr. ¡Drago!». La frase
sigue tras una abreviatura, y no se pone signo si la frase ya lo abrió antes.

## Entrega 2 · Informe de calidad

### Resumen visual
- Nota en un anillo de color (verde ≥ 85, ámbar ≥ 60, rojo) con el número dentro.
- Fichas Grave · Revisar · Menor con su cuenta; un clic filtra la lista.
- Barras con los tipos de aviso más frecuentes; un clic filtra.
- Franja de capítulos: un cuadro por capítulo, más intenso cuantos más avisos por palabra; un clic filtra por capítulo.

### Lista y detalle
- Lista agrupada por tipo, plegable, con su cuenta («Palabra repetida cerca (120)»).
- «¶16» pasa a «párr. 16»; la nota no se parte en dos líneas.
- Detalle: el párrafo entero con lo señalado (`Issue.span`) resaltado; «Ahora → Con el arreglo» como diff (lo quitado
  en rojo tachado, lo puesto en verde); «Problema» y «Solución» como texto plano (no se leen como HTML).

### Arreglos más seguros
- «Arreglar todos los de este tipo» abre antes una lista de los cambios (antes → después, resaltados) con una casilla
  cada uno; solo se aplican los marcados, en un único Ctrl+Z.
- Se repasan los arreglos automáticos existentes con los libros reales buscando casos como el de «Sr.».

### Avisos nuevos (cada uno con `/nuevo-aviso-calidad`, probado en los libros reales)
- `dialog-style` «Diálogos con dos estilos»: el libro abre los diálogos con raya y este párrafo con comillas (o al
  revés); solo si el estilo mayoritario pasa del 80 %. Sin arreglo automático.
- `double-space` «Espacio doble»: dos o más espacios seguidos dentro del párrafo; arreglo: uno.
- `mr` «Tratamiento en inglés»: Mr., Mrs., Ms., Miss; arreglo: Sr., Sra., Srta.
- `name-variant` «Nombre escrito de dos formas»: dos nombres propios (en mitad de frase, de 4 letras o más) a una
  letra de distancia, uno al menos 8 veces más frecuente; arreglo: la forma frecuente. Si en los libros reales da
  falsas alarmas a menudo, se deja fuera y se dice.

## Entrega 3 · Ortografía

- Lista de palabras con buscador y «Revisadas X de N».
- «Dónde sale»: cada aparición con su página como ficha y la frase entera (no 40 letras a cada lado), con la palabra
  resaltada en color.
- Sugerencias como botones; campo «Otra…» para escribir una.
- Al elegir un cambio, la aparición enseña el diff antes de aplicarlo.
- Acciones: «Cambiar esta» (solo la aparición elegida, nueva), «Cambiar todas», «Ignorar» (en esta sesión) y
  «Añadir al diccionario».

## Entrega 4 · Opciones

### Organización
Cada página con secciones con título, ayuda bajo cada opción y «Restablecer» (vuelve a los valores de fábrica de esa
página).

- El libro
  - Perfil y formato
  - Aspecto (nueva): estilo, separador, letra del texto, fuente de títulos, recuadros, llamada de nota (las cuatro
    primeras hoy solo en la barra de abajo)
  - Texto
  - Páginas (nueva): índice, créditos, sinopsis, notas al final, minutos de lectura, logo
  - Ilustraciones
  - PDF
  - Salida (nueva): nombre del archivo — «Como el PDF» (hoy), «Título del libro», «Serie - Volumen N»
- La aplicación
  - Apariencia: tema, color de acento (nuevo), tamaño de letra de la interfaz 9/10/11 pt (nuevo)
  - Inicio (nueva): reabrir el último libro al abrir la app, cuántos recientes guardar, carpeta de salida por defecto
  - Vista previa, Editor, Traducción, Carpeta vigilada (como ahora)
  - Ortografía (nueva): revisar también palabras en mayúscula; ver y quitar las palabras del diccionario del libro y
    de la serie
  - Informe de calidad: activar o desactivar cada tipo de aviso y cambiar su gravedad (hoy solo «ecos»)

### Riesgos
- Letra de 11 pt: la cinta del editor puede no caber a 1366 px; se comprueba con `fit_ribbon` y, si no cabe, el
  máximo es 10 pt.
- Cambiar la gravedad de un aviso cambia la nota del informe (los pesos van por gravedad).

## Pruebas
Cada entrega añade sus pruebas a `test_convert.py` (funciones `test_*`, ventanas sin pantalla) y se verifica en
pantalla real con clics (`/verificar-ventana`, temas claro y oscuro). Cambios en `convert.py`: `/regresion-series`.
