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
5. **Página de índice del PDF:** una página cuyo primer texto coincide con
   `contenido|índice|tabla de contenido|contents`, o que tiene tres o más líneas
   terminadas en puntos y número (`.....  12`), se descarta completa.
6. **Párrafos:** las líneas consecutivas se unen en un párrafo. Empieza párrafo nuevo si:
   - la línea tiene sangría respecto al margen izquierdo del texto, o
   - el salto vertical con la línea anterior supera 1,5 veces el interlineado típico, o
   - la línea anterior terminó claramente antes del margen derecho.
   Un párrafo que termina una página sin puntuación final continúa en la siguiente.
   Una palabra cortada con guion al final de línea se une.
7. **Formato:** spans en cursiva se envuelven en `<i>`, en negrita en `<b>`. El texto se
   escapa para XHTML.
8. **Separadores de escena:** una línea formada solo por símbolos (`☆★*◆◇●○#~`, etc.)
   se convierte en bloque `break`.
9. **Capítulos candidatos:**
   - Si el PDF tiene marcadores (`doc.get_toc()`), se usan los de primer nivel.
   - Si no, son candidatos las líneas cuya fuente es al menos 1,3 veces el tamaño
     normal, o las que empiezan con `capítulo|prólogo|epílogo|interludio|historia
     paralela|palabras de cierre|extra|parte|chapter|prologue|epilogue|afterword`.
   - Líneas consecutivas de título (título partido en varias líneas) se unen en un
     solo bloque `heading`.
10. **Metadatos:** título del PDF o, si está vacío, el nombre del archivo; autor del PDF.
11. **Errores:** PDF cifrado o sin texto en ninguna página lanza `ValueError` con un
    mensaje en español apto para mostrar al usuario.

## Escritura del EPUB (`write_epub`)

- EPUB 3 con `nav.xhtml` (y `toc.ncx` por compatibilidad con lectores antiguos).
- `mimetype` primero en el zip y sin comprimir.
- Un XHTML por capítulo. Los bloques anteriores al primer capítulo van a una sección
  "Inicio". Un candidato desmarcado por el usuario se escribe como párrafo normal.
- Portada: `cover.jpg` marcada como `cover-image` y una página `cover.xhtml`.
- CSS mínimo: márgenes, sangría de primera línea, imágenes al 100 % de ancho, separador centrado.
- Se escribe en un archivo temporal junto al destino y se renombra al terminar. Si falla,
  se borra el temporal.

## Ventana (`app.py`)

- Botón **Abrir PDF…** (`filedialog.askopenfilename`).
- El análisis corre en un `threading.Thread`; la barra de progreso y el texto
  "Analizando página X/Y" se actualizan con `root.after`.
- Campos editables **Título** y **Autor**.
- `ttk.Treeview` con columnas: incluir (✔/✘), título, página.
  - Clic en la columna incluir alterna la marca.
  - Doble clic en el título abre un campo para editarlo.
- Botón **Generar EPUB…** (`filedialog.asksaveasfilename`, por defecto misma carpeta y
  nombre del PDF con `.epub`). La escritura también corre en un hilo.
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
