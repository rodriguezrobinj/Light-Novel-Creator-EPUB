# EPUB fiel — diseño

Fecha: 2026-11-17. Aprobado por el usuario en el chat.

## Problema

Un EPUB de entrada pasa hoy por `analyze()`: MuPDF lo maqueta como un PDF y la app vuelve a leer el texto visible.
Se pierden niveles de título (`h1`–`h6`), clases, CSS, fuentes, furigana, enlaces y saltos de línea. El EPUB que
sale no se parece al original (Date A Live 14 de ZeePubs).

## Objetivo

Abrir un EPUB y que el EPUB generado sea el mismo libro, byte a byte en todo lo que no se cambió, con todas las
herramientas de la app funcionando sobre él (editor, informe, ortografía, reemplazos, tipografía, traducción, lista
de capítulos, datos, portada, opciones de salida).

## Enfoque: parchear el original

### Lectura (`convert.read_epub`, la usa `analyze` con un .epub)

- Spine en orden; cada XHTML se recorre con sus posiciones en el texto.
- Bloque hoja: `p`, `h1`–`h6`, `li`, `dt`, `dd`, `pre`, `blockquote`/`div` sin bloques dentro. Imagen: `img`, `image`
  de SVG o `figure` con imagen fuera de un bloque de texto (o en un párrafo sin texto). `hr` es separador.
- `Block` nuevo: `tag` (etiqueta), `src` (archivo, inicio y fin del contenido, inicio y fin del elemento), `orig`
  (html leído). `html` es el contenido interior con `em`→`i` y `strong`→`b`; lo demás tal cual (`ruby`, `a`, `span`,
  `br`, `sup`, `small`).
- Cada archivo del spine es un capítulo de la lista. Su título sale del `nav` (o `toc.ncx`), si no del primer título
  del archivo, si no del `<title>`. Un archivo sin título visible lleva un título sintético (`src` con inicio -1)
  que no se escribe.
- `Book.origin` = ruta del EPUB; `page` de cada bloque = primera página de su archivo en MuPDF (la vista del original).
- Metadatos del OPF al libro: título, autor, serie y volumen (Calibre o `belongs-to-collection`), grupo, traductor,
  ilustrador, sinopsis, géneros, fecha, portada.

### Herramientas

- Editor: las etiquetas que no sabe mostrar se ven como texto (`<ruby>…`) y vuelven a ser etiquetas al guardar
  (`_inline` deja pasar etiquetas XHTML de una lista blanca, bien cerradas). Un párrafo editado conserva su `tag`,
  sus atributos y su sitio (`src`).
- El resto trabaja sobre `html`, como con un PDF.

### Escritura (`write_epub` con `book.origin`, salvo EPUB 2)

- Copia el EPUB original. Cada XHTML con cambios se reescribe pegando:
  - bloque sin cambios (`html == orig`): bytes originales;
  - cambiado: etiqueta de apertura original + html nuevo + cierre;
  - quitado (sección quitada, imagen descartada, párrafo borrado): nada;
  - nuevo (sin `src` o con un `src` ya usado o fuera de orden): `<p>`/`<hN>` nuevo tras el anterior de su archivo.
- Archivo que se queda sin bloques: fuera del spine, del manifiesto, del `nav` y del `toc.ncx`.
- Orden del spine: el de los archivos en la lista. Título renombrado: etiqueta del `nav`/`toc.ncx` y texto del título
  (si es «Número: Nombre» y el título tiene dos líneas, cada parte en la suya).
- OPF: los datos de la ventana sustituyen a los del original (título, autor, serie, grupo…); lo demás se queda.
  `dcterms:modified` nuevo.
- Portada cambiada: nueva imagen en el archivo de portada. Páginas de la app (sinopsis, página de título, logo,
  «Contenido», notas) se añaden si están activadas.
- Salida: reducir imágenes, gris, recortar, KEPUB y partir palabras se aplican sobre los archivos copiados.
- Aspecto: «Estilo», «Letra» y «Separador» tienen el valor «Original» (de fábrica al abrir un EPUB): no se toca el
  CSS. Otro valor añade la hoja de la app al final de cada XHTML.
- EPUB 2: no puede ser fiel; se reconstruye con el escritor normal y la ventana avisa.

### Revisiones antiguas

Una revisión de un EPUB hecha con el análisis de MuPDF no casa con los bloques nuevos: al abrir se guarda una
versión y se empieza de cero, con aviso.

## Pruebas

EPUB sintéticos (h1–h3, ruby, enlaces, `@font-face`, clases, SVG de portada, nav anidado): sin cambios los XHTML
salen idénticos; con un cambio solo cambia ese párrafo; quitar, renombrar, mover; editor con ruby; traducción;
portada; EPUB 2. Date A Live 14: abrir, generar sin tocar, comparar y EPUBCheck. Ventana en pantalla y .exe.
