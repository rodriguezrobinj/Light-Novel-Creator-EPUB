# Conversor de novelas ligeras PDF a EPUB — Diseño

Fecha: 2026-10-25

## Objetivo

Programa de escritorio con ventana gráfica que convierte PDFs de novelas ligeras
(traducciones fan generadas desde Word u otros editores) en EPUB con texto fluido,
legibles en celular o e-reader. El EPUB debe tener portada, ilustraciones en su
lugar, capítulos navegables e índice, sin encabezados ni pies de página del PDF.

### Criterios de éxito

- El EPUB abre sin errores en lectores comunes (Calibre, Google Play Libros, Moon+ Reader).
- No aparecen encabezados, pies ni números de página del PDF dentro del texto.
- Los párrafos están completos: no se cortan por línea ni por cambio de página.
- Las ilustraciones a página completa aparecen como imagen.
- Los capítulos detectados se pueden revisar y corregir en la ventana antes de generar.
- Funciona con al menos los dos PDFs de muestra de `Novelas PDF/`, que tienen formatos distintos.

### PDFs de muestra y diferencias observadas

| | Nageki no Bourei (Astral) | Am I Actually the Strongest (World Project) |
|---|---|---|
| Encabezado | Título y volumen, texto simple | Barra azul con título y grupo |
| Pie | Grupo + número de página | Barra con "VOLUMEN 1 - LIGHT NOVEL" + número |
| Párrafos | Con sangría | Sin sangría, separados por espacio vertical |
| Títulos de capítulo | En la página del texto, fuente grande | En página propia con decoraciones |
| Separadores de escena | — | `☆`, `★` |
| Índice en el PDF | Página "Contenido" | Página "Tabla de contenido" con puntos y números |

Conclusión: la detección debe ser automática y no depender de un formato concreto.

## Fuera de alcance

- OCR para PDFs escaneados (solo se avisa al usuario).
- Agregar capítulos a mano en la ventana.
- Empaquetar como `.exe`.

## Arquitectura

Tres archivos en la raíz del proyecto:

- `convert.py` — lógica pura, sin interfaz.
  - `analyze(pdf_path, progress=None) -> Book`
  - `write_epub(book, chapters, out_path)`
- `app.py` — ventana `tkinter`/`ttk`; solo llama a `convert.py`.
- `test_convert.py` — prueba automática con un PDF sintético.

Dependencias: `pymupdf` (lectura del PDF y renderizado de páginas). El EPUB se escribe
con `zipfile` de la biblioteca estándar.

### Modelo de datos

```python
@dataclass
class Block:
    kind: str        # "p" | "heading" | "image" | "break"
    page: int        # número de página (1-based) de origen
    html: str = ""   # contenido XHTML del párrafo o título (con <i>/<b>)
    image: bytes = b""  # JPEG si kind == "image"

@dataclass
class Book:
    title: str
    author: str
    cover: bytes          # JPEG de la página 1
    blocks: list[Block]
    candidates: list[int] # índices en blocks de los títulos de capítulo detectados
```

`chapters` en `write_epub` es una lista de `(indice_de_bloque, titulo)` elegida por el usuario.

## Análisis del PDF (`analyze`)

1. **Lectura:** `page.get_text("dict")` por página. Por cada línea: texto, spans con
   tamaño de fuente y flags (negrita, cursiva), bbox.
2. **Tamaño de texto normal:** el tamaño de fuente más frecuente ponderado por número
   de caracteres.
3. **Encabezados y pies:** una línea situada en la franja superior o inferior de la
   página (12 % de la altura) se normaliza (dígitos sustituidos por `#`, espacios
   colapsados). Si la misma línea normalizada aparece en más del 30 % de las páginas,
   se elimina en todas. Los números de página sueltos en esas franjas también se eliminan.
4. **Páginas de imagen:** si, tras quitar encabezado y pie, una página tiene poco texto
   y sus imágenes cubren más de la mitad del área, y su texto no es un título de capítulo,
   la página se renderiza completa a JPEG (unos 150 dpi) como bloque `image`. Si el texto
   restante es un título de capítulo, se conserva solo el título y se descartan las
   decoraciones.
   - **Portada de capítulo** (World Project Vol 7): la ilustración tapa el título (texto
     oculto) y la página siguiente lo repite. Si el título de la página siguiente coincide
     (comparando solo letras y números), se emite el título, después la ilustración, y se
     quita el título repetido. Sin repetición, la imagen es un fondo decorativo.
   - **Ilustración bajo el texto** (Maryoku Vol 5): una página con texto normal y una imagen
     que cubre más de la mitad conserva el texto y añade la imagen incrustada original
     (extraída, sin el texto encima). Una imagen usada en más de 2 páginas es un fondo y se
     ignora.
   - **Páginas de diseño de los preliminares** (Kage Vol 6, portadilla con logo y créditos): antes de
     la primera página con un capítulo, una página con menos de 200 caracteres y alguna imagen
     propia (al menos un 2 % y usada en 2 páginas o menos) se conserva entera como imagen, para no
     perder su composición. Si el libro no tiene ningún capítulo detectable, no hay preliminares.
   - **Imágenes descartadas:** las que no entran solas quedan apuntadas como bloque `image`
     con `keep=False` (extraídas sin el texto encima), para poder recuperarlas en la ventana:
     las de dentro del texto (al menos un 2 % de la página y usadas en 2 páginas o menos) y el
     fondo de una página de título. No se apuntan los fondos y barras repetidos ni las
     diminutas. Una imagen con transparencia (máscara aparte o ya aplicada) se guarda en PNG
     para conservarla; el resto, en JPEG. `write_epub` omite las de `keep=False`; cada imagen se guarda como
     `img/bNNNNN.jpg` o `.png` con su tipo en el manifiesto (por bloque: una página puede tener varias).
5. **Página de índice del PDF:** una página con `Contenido`, `Índice`, `Tabla de contenido` o
   `Contents` (con o sin `:`) como una de sus 3 primeras líneas (puede haber créditos encima),
   o con tres o más líneas acabadas en puntos o guiones hasta el número de página
   (`.....  12`, `| Capítulo 1 | ---- Pág. 54`), se descarta completa.
6. **Párrafos:** las líneas se agrupan por línea base (`span["origin"]`), no por la caja
   del span: en algunas fuentes (Cambria) la caja mide el triple de la línea real, y el
   texto justificado parte una línea en varios spans. Empieza párrafo nuevo si:
   - la línea tiene sangría de más de 0,8 veces el tamaño de letra respecto al margen
     izquierdo de la página, o
   - el salto vertical con la línea anterior supera 1,3 veces el interlineado más
     frecuente del libro (medido: 1,43 en World Project, 1,84 en Astral), o
   - la línea anterior terminó antes del 80 % del ancho del texto.
   En la primera línea de una página, el párrafo anterior continúa salvo que haya
   sangría o la línea anterior fuera corta. Si el libro no usa sangría (menos del 5 %
   de líneas sangradas), además se corta cuando el texto anterior termina en
   puntuación final.
   Una palabra cortada con guion al final de línea se une.
   Una línea que empieza en minúscula continúa el párrafo anterior si este no terminó en
   puntuación final, aunque tenga sangría (Maryoku Vol 9 pone así el final de algunas frases).
7. **Formato:** spans en cursiva se envuelven en `<i>`, en negrita en `<b>`. El texto se
   escapa para XHTML.
8. **Separadores de escena:** una línea formada solo por símbolos (`☆★*◆◇●○#~`, etc.)
   se convierte en bloque `break`.
9. **Capítulos candidatos:**
   - Si el PDF tiene marcadores (`doc.get_toc()`), se usan los de primer nivel. Además
     cuenta un título detectado que empieza con palabra clave y está en una página sin
     ningún marcador: hay PDF con marcadores incompletos (Maryoku Vol 9 no marca los
     capítulos 25 y 29).
   - Si no, es candidata una línea de 120 caracteres o menos que cumpla una de estas
     condiciones: fuente de al menos 1,3 veces el tamaño normal; o negrita con fuente
     de al menos 1,1 veces el normal; o negrita y empieza con
     `capítulo|prólogo|epílogo|interludio|historia paralela|palabras de cierre|extra|parte|chapter|prologue|epilogue|afterword|interlude`.
     Exigir negrita junto a la palabra clave evita que un párrafo como "Parte de mí…"
     se tome por capítulo.
   - También es título una línea que coincide con un marcador del PDF de esa página
     (entera; o en parte si va en negrita, para títulos partidos). Así se reconocen
     subtítulos del tamaño del texto que el PDF marca como nivel 2 (Maryoku: "El lado de…").
     Esos títulos no son capítulos: salen como subtítulo.
   - Una línea de título que es solo la etiqueta del capítulo ("» Capítulo 1 «", "«Prólogo»",
     "CAPITULO UNO:"; o cualquier texto corto entre adornos `» « - |`) se une con hasta 3
     líneas en negrita pegadas debajo (a menos de 2,2 veces el tamaño de la anterior), aunque
     no lleguen a tamaño de título: "Capítulo 1: Nombre" (Kage, UntitledT). Se quitan los adornos.
   - Sin marcadores, los títulos anteriores al primer capítulo con palabra clave son portadilla
     ("The Eminence in Shadow", "- Volumen 5 -") y no cuentan como capítulo.
   - Líneas de título consecutivas del mismo tamaño se unen en un solo bloque `heading`
     si están pegadas (a menos de 2 veces su tamaño de letra) y la segunda no abre un
     marcador. Un subtítulo justo debajo del capítulo queda más lejos (Maryoku Vol 6: 67 pt
     frente a unos 20 pt entre las líneas de un título partido).
   - En una página que solo tiene títulos, se conserva solo el primer grupo: World
     Project repite el título en otro tamaño sobre la imagen de fondo.
10. **Metadatos:** título del PDF o, si está vacío, el nombre del archivo; autor del PDF.
    - **Serie y volumen:** del nombre del archivo ("Volumen", "Vol.", "Tomo" o "Volume" y
      un número, sin ceros a la izquierda). La serie es el texto anterior, sin etiquetas
      entre corchetes (`[WP]`); si no hay, el título del PDF ("Volumen 1 World Project").
    - Si hay serie y volumen y el título del PDF no incluye el número, el título pasa a
      "Serie - Volumen N": algunos lectores no muestran la serie y todos los tomos se
      llamarían igual.
    - En el EPUB: `belongs-to-collection` (EPUB 3, con `collection-type` y
      `group-position`) y `calibre:series` / `calibre:series_index`. Sin serie, nada.
11. **Errores:** PDF cifrado o sin texto en ninguna página lanza `ValueError` con un
    mensaje en español apto para mostrar al usuario.

## Escritura del EPUB (`write_epub`)

- EPUB 3 con `nav.xhtml` (y `toc.ncx` por compatibilidad con lectores antiguos).
- Índice en dos niveles: cada sección y, anidados dentro, sus subtítulos (`<h2 id="tN">`,
  con N el índice del bloque) enlazados a su ancla. En `toc.ncx`, `navPoint` anidados con
  `playOrder` correlativo (el capítulo antes que sus subtítulos).
- `mimetype` primero en el zip y sin comprimir.
- Un XHTML por capítulo. Los bloques anteriores al primer capítulo van a una sección
  "Inicio". Un título que no es capítulo (desmarcado, o subtítulo detectado) se escribe
  como subtítulo `<h2>`, para que se vea como título y no como texto normal.
- Secciones quitadas (`removed`: índices de títulos): un capítulo se quita con todo lo que
  hay hasta el siguiente capítulo que se conserva, subtítulos e ilustraciones incluidos; un
  subtítulo, hasta el siguiente título. No aparecen ni en el texto ni en el índice.
- Limpieza tipográfica opcional (`typography=True`; en la ventana, casilla marcada por
  defecto), al estilo RAE, por párrafo y sin tocar las etiquetas: `...` → `…`; comillas
  dobles (rectas o tipográficas) → `« »`, y `“ ”` dentro de otras; comillas simples → `‘ ’`
  y apóstrofo `’`; `─` → `—`; espacios dobles y espacio antes de `, . ; : ! ?` fuera. Una
  comilla abre tras espacio, signo de apertura o un cierre pegado (`“Hola.”“Adiós.”`), y
  cierra en otro caso o si la sigue espacio o puntuación. La cuenta de comillas abiertas
  empieza en cada párrafo. No convierte diálogo con comillas en diálogo con raya.
  Medido: los párrafos con comillas desparejadas que quedan ya lo estaban en el PDF
  (número impar de comillas: el diálogo sigue en el párrafo siguiente).
- Estilos (`style`; en la ventana, desplegable, por defecto "Clásico"): Clásico (serif,
  justificado, sangría), Moderno (sin serif, a la izquierda, párrafos separados) y Novela
  ligera (número en versalitas, nombre hasta 1,5 veces el texto y menor si pasa de 45
  caracteres, línea decorativa, separador `❖`). Todos llevan
  la tipografía de libro impreso: partición de palabras (`hyphens: auto`), sin sangría tras
  título, separador o ilustración, y `orphans`/`widows` 2.
- Un capítulo cuyo título empieza por "Capítulo 3:", "Prólogo:", "Parte Uno:" o similar se
  escribe con número y nombre en dos líneas (`<span class="num">` y `<span class="name">`); el
  resto, en una. El índice conserva el título entero.
- Navegación: página "Contenido" (`contents.xhtml`) justo después de la portada, con los
  capítulos y sus subtítulos enlazados (`contents=True`; la vista previa la omite). Puntos de
  referencia en `nav.xhtml` (`landmarks`: portada, índice e inicio de la lectura, que es el
  primer capítulo y no "Inicio") y `<guide>` en el OPF con lo mismo, para Kindle y lectores
  antiguos.
- Portada: `cover.jpg` marcada como `cover-image` y una página `cover.xhtml`.
- CSS mínimo: márgenes, sangría de primera línea, imágenes al 100 % de ancho, separador centrado.
- Se escribe en un archivo temporal junto al destino y se renombra al terminar. Si falla,
  se borra el temporal.

## Ventana (`app.py`)

- Botón **Abrir PDF…** (`filedialog.askopenfilename`).
- El análisis corre en un proceso aparte (`multiprocessing.Process`), no en un hilo:
  PyMuPDF retiene el GIL hasta 0,3 s por llamada y, en un hilo, cada llamada de Tk
  espera a recuperarlo (medido: la ventana se trababa 1–6 s; con proceso, 0,02 s).
  El progreso y el resultado llegan por una `multiprocessing.Queue` que la ventana lee
  con `root.after`; de cada lectura se pinta solo el último progreso ("Analizando… N %").
- Campos editables **Título** y **Autor**.
- `ttk.Treeview` con columnas: tipo, título, página. Lista todos los títulos: los capítulos
  detectados como "Capítulo" y los subtítulos como "Subtítulo". Cada clic en la columna
  tipo rota Capítulo → Subtítulo → Quitar. Un título en "Quitar" no llega al EPUB, y su
  vista previa del EPUB lo avisa. Estilo por tipo: capítulo en negrita, subtítulo gris en
  cursiva, quitar gris claro tachado.
- Las ilustraciones van intercaladas en la lista, en el orden del libro, como "Ilustración ·
  página N" (en verde, sin edición del nombre). Tipos: Ilustración (entra), Quitar y
  Descartada (apuntada por el análisis); un clic quita o recupera. Al generar, la ventana pone
  `keep` según el tipo. Su vista previa del EPUB muestra solo esa imagen, o avisa si no entra.
- Vista previa del PDF en un `Canvas`, con un recuadro rojo sobre la línea tomada como título
  (`Block.rect`, calculado desde línea base y tamaño de letra). Botones ◀ ▶ en cada vista:
  el PDF recorre todo el documento; el EPUB, la sección completa (del título al siguiente).
- Las dos vistas previas son marcos de tamaño fijo (proporción 3:4, según el alto de la
  ventana) con la página centrada dentro: cambiar de dispositivo o de página no mueve el
  resto de la ventana. Sin libro, o sin título seleccionado, muestran una hoja en blanco con
  un aviso, y la tira de miniaturas, rectángulos grises de relleno.
- Vista del EPUB por dispositivo (Celular 300×560, E-reader 6" 420×560, Tablet 560×750 pt,
  con `doc.layout`) y modo oscuro aproximado invirtiendo la imagen (también las ilustraciones).
- Tira de miniaturas del PDF bajo las vistas previas: borde azul si la página tiene título,
  verde si tiene ilustración, naranja la página mostrada. Se dibujan por tandas de 6 para no
  congelar la ventana (medido: 183 páginas en 1,2 s, la ventana nunca se traba más de 0,11 s).
  Clic en una miniatura: esa página y, si tiene un título, se selecciona en la lista.
  - Clic en la columna incluir alterna la marca.
  - Doble clic en el título abre un campo para editarlo.
- Botón **Generar EPUB…** (`filedialog.asksaveasfilename`, por defecto misma carpeta y
  nombre del PDF con `.epub`). La escritura también corre en el proceso aparte.
- Cualquier excepción se muestra con `messagebox.showerror`.

## Pruebas

`test_convert.py` (ejecutable con `python test_convert.py`, sin frameworks):

1. Crea con PyMuPDF un PDF de 3 páginas con encabezado y pie repetidos, un título de
   capítulo en fuente grande, un párrafo cortado entre la página 1 y la 2, y una frase
   en cursiva.
2. Comprueba:
   - el encabezado y el pie no aparecen en ningún bloque;
   - el párrafo cortado es un solo bloque;
   - el título está en `candidates`;
   - la cursiva produce `<i>`;
   - el EPUB generado tiene `mimetype` como primera entrada con contenido
     `application/epub+zip`, y contiene `nav.xhtml` y un XHTML por capítulo.

Además, prueba manual con los dos PDFs de muestra revisando el EPUB en un lector.
