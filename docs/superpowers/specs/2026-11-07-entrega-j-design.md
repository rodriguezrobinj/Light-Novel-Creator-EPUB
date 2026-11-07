# Entrega J: contraste e-ink, partición de palabras, envíos y honoríficos — Diseño

Fecha: 2026-11-07

## Objetivo

Mejorar la lectura en e-readers (ilustraciones con más contraste, texto justificado sin huecos en Kindle),
saber qué tomos de una serie faltan o no se han enviado al lector, y avisar de honoríficos escritos de
varias formas en el mismo libro.

Fuera de esta entrega: aviso de actualizaciones (no hay sitio de descarga), audiolibro, envío por correo,
OCR y traducción automática.

## 1. Contraste para e-ink

- `convert._gray`, tras pasar a gris: autocontraste (Pillow `ImageOps.autocontrast`, recorte del 0,5 % por
  cada extremo) y una gamma leve que oscurece los grises medios. En tinta electrónica las ilustraciones
  escaneadas salen lavadas.
- Sin opción nueva: va con «Imágenes en gris» (perfiles «E-reader de tinta» y «Kobo», o a mano).
- La portada no cambia (sigue en color, como ahora).
- Con transparencia, la imagen sigue en PNG y conserva su canal alfa.

## 2. Partición de palabras

- `write_epub(..., hyphenate=False)`: con `True`, inserta guiones blandos (U+00AD) en el texto de los
  párrafos. No en etiquetas, títulos, notas ni enlaces.
- Solo palabras de 7 letras o más; al menos 2 letras a cada lado del corte. Palabras con dígitos o con
  guion propio se dejan como están.
- Motor: `pyphen` (dependencia nueva, fijada en `requirements.txt`) con su `hyph_es.dic` (patrones de
  hyph-utf8, los mismos que LibreOffice). El .exe lleva solo ese diccionario: el `.spec` quita los de los
  demás idiomas (unos 6 MB).
- Solo con idioma del EPUB «es»: los patrones son españoles.
- Ventana: casilla «Partir palabras» (preferencia `hyphenate`). El perfil Kindle la activa; los demás la
  apagan.
- Riesgo: en algunos lectores la búsqueda o el diccionario al tocar una palabra pueden fallar con guiones
  blandos. Probar en un Kindle real antes de darlo por bueno.

## 3. Panel de serie: tomos que faltan y envíos

- Línea sobre la tabla: «Faltan los tomos 5 y 7» con los volúmenes enteros que faltan entre el menor y el
  mayor de la carpeta (`_series_volume` del nombre de cada archivo). Sin huecos, no se muestra.
- Columna «Enviado»: último envío del EPUB de ese tomo, «USB · 07/11», «Wi-Fi · 07/11», «Calibre · 07/11»,
  «Kindle (web) · 07/11», o «—».
- Registro en `PDF a EPUB/ajustes.envios.json`: `{ruta del EPUB: {"how": ..., "when": "AAAA-MM-DD"}}`.
- Cuándo se apunta:
  - USB: al terminar bien la copia.
  - Wi-Fi: si el móvil descargó el archivo (el servidor lo marca; se apunta al cerrar la ventana).
  - Calibre: al abrir Calibre sin error.
  - Kindle: al pulsar «Enviar a Kindle…» (la web no confirma el envío).
- Un fallo al leer o escribir el registro no impide el envío: el registro es informativo.

## 4. Aviso de honoríficos

- `quality_report`, tipo `honorific`, nivel «Menor».
- Una forma de honorífico es su texto tras quitar mayúsculas, guiones y espacios, con «sempai» igual a
  «senpai»: «Subaru-kun», «Subaru kun» y «Subarukun» son la misma; «senpai» y «sempai», también.
- Si el libro usa varias formas de la misma, cada párrafo con una forma que no es la más usada recibe un
  aviso con arreglo automático hacia la más usada. «Arreglar todos» las une.
- Juntas («Subarukun») solo cuentan si la forma separada sale también en el libro: así una palabra normal
  que acaba en «san» o «kun» no se toma por un honorífico.

## Pruebas

- `_gray` estira una imagen de grises 60–200 a casi 0–255; con alfa sigue en PNG.
- `write_epub(hyphenate=True)` pone U+00AD en palabras largas de los párrafos, no en títulos ni en
  `href`; sin la opción, el EPUB no lleva ninguno. `check_epub` sigue sin errores.
- PDF sintético con «senpai» y «sempai», y otro con «Subaru-kun» y «Subaru kun»: avisos en la forma
  minoritaria y arreglo hacia la mayoritaria. Un libro con una sola forma no avisa.
- Ventana offscreen: el panel de serie muestra «Faltan los tomos 2» con tomos 1 y 3, y la columna
  «Enviado» tras apuntar un envío. El perfil Kindle marca «Partir palabras».
