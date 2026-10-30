# Salida para lectores — Diseño

Fecha: 2026-10-29

Entrega A de 4 (orden acordado: A. salida para lectores, B. notas del traductor emergentes,
C. carpeta vigilada y reemplazos de serie entre carpetas, D. datos del libro desde internet).

## Objetivo

Que el EPUB llegue bien al lector de cada usuario (celular, tablet, Kobo, Kindle, otros e-readers por
USB) sin pasos manuales: perfiles para Kindle y Kobo, formato KEPUB para Kobo, el tamaño real del
archivo con aviso del límite de Send to Kindle, y copiar el EPUB al e-reader conectado con un clic.

### Criterios de éxito

- El perfil «Kobo» genera un `.kepub.epub` que pasa `check_epub`, con cada frase en un `koboSpan`, sin
  cambiar el texto ni perder cursivas y negritas.
- El perfil «Kindle» fija color, 1600 px y EPUB 3; si el EPUB supera 50 MB la ventana final lo avisa.
- La ventana «EPUB listo» muestra el tamaño y un botón por cada unidad extraíble (no Kindle) que copia
  el EPUB allí. Con el perfil Kindle, un botón abre Send to Kindle y selecciona el archivo.
- Convertir carpeta y Volumen recopilatorio respetan el formato KEPUB en el nombre del archivo.

## Fuera de alcance

- Dispositivos MTP sin letra de unidad (algunos Kindle, Tolino y Boox recientes): queda «Abrir carpeta».
- Enviar por correo a Kindle (haría falta SMTP y credenciales).
- AZW3/MOBI: Amazon convierte el EPUB por Send to Kindle.
- Estimar el tamaño antes de generar.

## Decisiones

- **KEPUB dentro de `write_epub`** (`kepub=False`): las secciones `secNNN.xhtml` se transforman con
  `_kepub` justo al escribirlas en el zip, así la escritura sigue siendo atómica (`.tmp` + `os.replace`).
  Se descartó posprocesar el zip (segunda escritura) y `kepubify.exe` (dependencia externa).
- **`_kepub` trabaja sobre el XHTML propio**, no con un parser: el writer genera marcado conocido. Dentro
  de cada `<p>`, `<h1>` y `<h2>`, cada nodo de texto se parte en frases y cada frase va en
  `<span class="koboSpan" id="kobo.P.S">` (P: párrafo del archivo, S: frase). Las etiquetas (`<i>`, `<b>`,
  marcas de página) quedan igual, así el anidado es válido. El `<body>` se envuelve en
  `<div id="book-columns"><div id="book-inner">`, como hace kepubify.
- **Perfiles**: «Kindle» = color, reducir a 1600 px, EPUB 3, sin KEPUB. «Kobo» = gris, 1400 px, EPUB 3,
  KEPUB. Todos los perfiles fijan `kepub` (los demás a `False`). Casilla «Formato Kobo (KEPUB)» en
  «Opciones» para el perfil «Personalizado».
- **`kepub` se guarda** en la revisión, en las preferencias (`PREFS`) y se hereda en la serie (`INHERITED`).
- **Nombre del archivo**: `_epub_name(path, kepub)` da `x.kepub.epub` o `x.epub`, también si el usuario
  escribió otra extensión en el diálogo. Convertir carpeta considera «ya tiene EPUB» si existe cualquiera
  de los dos.
- **Tamaño**: real, al terminar, en MB decimales con coma («18,4 MB»). Aviso con perfil Kindle y más de
  50 MB: por correo no llega; la web de Send to Kindle admite hasta 200 MB.
- **Unidades**: `ctypes` (`GetLogicalDrives`, `GetDriveTypeW` = 2 extraíble, `GetVolumeInformationW` para
  la etiqueta). `_reader_kind(root)`: «kindle» si tiene `documents` y `system`, «kobo» si tiene `.kobo`,
  si no «usb». Kindle no recibe botón de copia (no lee EPUB por USB) y se muestra una nota. Destino:
  `Books/` si existe, si no la raíz.
- **La copia** corre en el proceso de trabajo (`self.run(shutil.copy2, …)`), como el resto del trabajo
  pesado; al terminar, mensaje «Copiado en …». Un error (unidad expulsada o llena) pasa por `on_error`.
- **Send to Kindle**: `webbrowser.open("https://www.amazon.com/sendtokindle")` y el Explorador con el
  archivo seleccionado, para arrastrarlo.

## Pruebas

- `test_convert.py`: KEPUB de un libro sintético pasa `check_epub`, todo `<p>` con texto lleva
  `koboSpan`, la cursiva se conserva, el texto plano es idéntico al EPUB normal, ids únicos.
- `_write_options` y `_epub_name`; `_reader_kind` con carpetas temporales; perfil Kobo fija `kepub`.
- Generar desde la ventana con el perfil Kobo guarda `.kepub.epub`.
- Manual con el .exe: la detección de unidades y la copia necesitan un dispositivo real.
