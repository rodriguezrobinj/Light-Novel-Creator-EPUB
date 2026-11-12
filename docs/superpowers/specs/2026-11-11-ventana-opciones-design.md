# Ventana «Opciones» — diseño

## Para qué

Hoy los ajustes están repartidos en seis sitios: la barra de abajo (Perfil, Separador, Estilo, Letra,
«Opciones ▾» con nueve casillas, «PDF ▾»), el panel «Editar datos» (Idioma, «Títulos ▾»), Herramientas
(Tema, Vigilar una carpeta), encima de la vista previa (dispositivo, modo oscuro), el Informe de calidad
(ecos) y Traducir (motor, modelos, claves, idiomas). El usuario no sabe dónde buscar una opción.

Objetivo: un solo sitio para todos los ajustes, como las Preferencias de Acrobat o las Opciones de
Word. La UX manda: encontrar cualquier opción en segundos y saber siempre sobre qué actúa (este libro o
la aplicación).

Éxito:
- Cualquier opción se encuentra desde la lista de categorías o el buscador.
- La barra de abajo queda con Perfil, «Opciones del libro…», «PDF ▾» y «Generar EPUB…».
- Un libro nuevo abre con los valores elegidos para «Libros nuevos», no con los del último libro abierto.
- Un `ajustes.json` antiguo da los mismos valores que antes de la actualización.

Fuera de alcance: las acciones (Generar, PDF ▾, Buscar, Traducir, herramientas) y los datos del libro
(título, autor, portada, reemplazos, glosario, sinopsis) se quedan donde están.

## Forma

Una ventana modal «Opciones», 860 × 560 px (se ajusta a pantallas de 1366 px y al zoom del 125 %):

- Izquierda: buscador (`Buscar (Ctrl+F)`) y lista de categorías en dos grupos con encabezado, «El libro» y
  «La aplicación».
- Derecha: la página de la categoría elegida, con desplazamiento vertical si no cabe.
- Abajo a la derecha: «Aceptar» (botón principal) y «Cancelar».
- En las categorías de «El libro», encima de la página: **«Aplicar a: (•) Este libro ( ) Libros nuevos»**.
  Sin libro abierto, «Este libro» sale desactivado y marcado «Libros nuevos». Las categorías de «La
  aplicación» no muestran el selector.

## Categorías y opciones

### El libro (con selector de alcance)

| Categoría | Opciones (clave) |
|---|---|
| Perfil y formato | Perfil (`profile`), EPUB 2 (`epub2`), Formato Kobo KEPUB (`kepub`), Idioma del libro (`lang`) |
| Aspecto | Estilo (`style`), Separador (`separator`), Letra (`font`, `font_file`; «Otra…» pide el archivo) |
| Texto | Limpieza tipográfica (`typography`), Partir palabras con guiones (Kindle) (`hyphenate`), Minutos de lectura en el índice (`reading`), Página de créditos (`credits`), Corregir mayúsculas al abrir si vienen en MAYÚSCULAS (`autocase`) |
| Ilustraciones | Reducir imágenes (`shrink`) con el lado mayor en píxeles al lado (`shrink_size`), Imágenes en gris (`gray`), Recortar márgenes blancos (`trim`) |
| PDF | No partir palabras al final de línea (`whole_words`), Tamaño preferido (`pdf_size`: A5, e-reader 6″, A4) |

`pdf_size` es nueva: el tamaño preferido sale primero en «PDF ▾», con «(preferido)».

Idioma del libro sale de «Editar datos»: decide la partición de palabras y la voz de lectura, no es un
dato que se lea.

### La aplicación (sin selector)

| Categoría | Opciones |
|---|---|
| Apariencia | Tema (`theme`: Sistema, Claro, Oscuro) |
| Vista previa | Dispositivo al abrir (`device`), Modo oscuro al abrir (`dark`) |
| Traducción | Motor (`translate.engine`), modelo y clave de API de cada servicio (`translate.models`, `translate.keys`), idiomas de origen y destino por defecto (`translate.src`, `translate.dst`) |
| Carpeta vigilada | Ruta (`watch`), «Elegir…» y «Dejar de vigilar» |
| Informe de calidad | Buscar ecos, palabras repetidas cerca (`echoes`): hoy es la única opción del informe; hoy se guarda también en la revisión del libro y pasa a ser solo de la aplicación |

Los controles rápidos de encima de la vista previa (dispositivo, modo oscuro) se quedan; la página
«Vista previa» fija con qué valores arrancan.

## Comportamiento

### Aceptar y Cancelar

- Al abrir, la ventana guarda una copia de los valores de los dos alcances y de la aplicación.
- Los cambios de «Este libro» se aplican ya a los `*_var` de la ventana principal: la vista previa EPUB
  se redibuja mientras Opciones sigue abierta. Los de «Libros nuevos» y «La aplicación» quedan
  pendientes en el diálogo, salvo el Tema, que se aplica al momento para verlo.
- **Cancelar**, Esc o la ✕: vuelve todo a la copia (los `*_var`, el tema) y redibuja la vista previa si
  cambió algo.
- **Aceptar**: lo del libro va a su revisión (`save_review`) y a la memoria de la serie
  (`_save_series`); «Libros nuevos» va a `settings["defaults"]`; lo de la aplicación, a sus claves de
  `settings`; y se escribe `ajustes.json`.
- Cambiar el selector a media edición no pierde nada: cada alcance conserva lo tocado y Aceptar guarda
  los dos.

### De dónde salen las opciones de un libro

Al abrir un PDF, en este orden:

1. su revisión guardada (`<pdf>.lnepub.json`);
2. si no tiene, lo heredado de otro tomo de la serie (`INHERITED`);
3. si tampoco, `settings["defaults"]`.

Hoy el paso 3 no existe: un libro sin revisión ni serie se queda con lo que tenía el libro anterior.

### Migración

Si `ajustes.json` no tiene `defaults`, se crea con los valores de libro que ya guarda en su raíz
(`PREFS`: lo último usado). El usuario no ve ningún cambio hasta que toque «Libros nuevos». Las claves
de la raíz se siguen escribiendo para lo que aún las lee (convertir carpeta, carpeta vigilada,
recopilatorio): toman los valores de `defaults`.

### Perfil

- Elegir un perfil fija sus opciones (`PROFILES`), como hoy. Esas opciones muestran debajo «Fijada por
  el perfil Kindle».
- Cambiar a mano una opción fijada pasa el perfil a «Personalizado».

### Buscador

- Filtra la lista de categorías a las que tienen alguna opción cuyo nombre o descripción contiene el
  texto (sin tildes ni mayúsculas: «guion» encuentra «guiones»).
- En la página elegida, las opciones que coinciden se resaltan con el color de selección del tema.
- Sin coincidencias: «Ninguna opción contiene «…»» en la lista.
- Esc con texto en el buscador lo vacía; sin texto, cierra como Cancelar.

### Ayuda en cada opción

Cada opción lleva debajo una línea gris (objeto `muted`) que dice para qué sirve: «Para e-readers de
tinta: pesa menos y se ve igual». En «Este libro», cada página tiene «Usar también para libros nuevos»,
que copia sus valores a «Libros nuevos».

### Errores en la página, sin ventanas emergentes

- Letra propia cuyo archivo ya no existe: texto rojo «No se encuentra el archivo de la letra» y la
  opción vuelve a «Del lector» al generar.
- Carpeta vigilada que ya no existe: texto rojo «La carpeta ya no existe» y «Dejar de vigilar».
- Claves de API: campo oculto con un botón 👁 para mostrarla.

## Cómo se llega

- Herramientas → «Opciones…» y `Ctrl+,`: abre en la última categoría vista.
- «Opciones del libro…» en la barra de abajo: abre en «Perfil y formato» con «Este libro».
- Traducir: «Motor y claves…» abre en «Traducción». El diálogo Traducir conserva solo el ámbito y los
  idiomas de este libro.
- Informe de calidad: «Opciones del informe…» abre en «Informe de calidad».
- Se quitan: «Opciones ▾» y los combos Separador, Estilo y Letra de la barra; «Tema» de Herramientas;
  la casilla de «PDF ▾»; la casilla de «Títulos ▾»; el combo Idioma de «Editar datos»; los controles de
  motor, modelo y clave de Traducir; la casilla de ecos del informe. «Vigilar una carpeta…» de
  Herramientas abre Opciones en «Carpeta vigilada».

## Código

- **`options.py`** (nuevo): la tabla `OPTIONS`, una entrada por opción: categoría, clave, tipo
  (`check`, `choice`, `number`, `path`, `folder`, `secret`), etiqueta, descripción, alcance (`book` o
  `app`) y, para `choice`, sus valores. La ventana, el buscador y las ayudas se construyen desde la
  tabla: una opción nueva es una línea. `OptionsDialog(app, scope="book", category=None)` usa `qtk` y
  `theme` como los demás diálogos.
- **`app.py`**: quita los controles citados; añade «Opciones del libro…», «Herramientas → Opciones…» y
  `Ctrl+,`; `settings["defaults"]` con su migración en `load_settings`; el paso 3 al abrir un libro;
  `pdf_size` en el menú «PDF ▾». Los `*_var` siguen siendo la fuente de verdad del libro abierto.
- **Traducir e Informe de calidad** leen motor, claves y ecos de `settings` como ahora: solo cambia dónde
  se editan.
- **`convert.py`** no cambia.

## Pruebas (`test_convert.py`, sin pantalla)

- Opciones en «Este libro», cambiar el Estilo: la vista previa cambia; Cancelar la devuelve y el
  `style_var` vuelve a su valor.
- «Libros nuevos» con gris y Aceptar: un libro sin revisión ni serie abre en gris; uno con revisión
  conserva la suya; uno con serie hereda de la serie.
- Migración: un `ajustes.json` sin `defaults` da los mismos valores que antes.
- Buscador: «guion» deja solo Texto y PDF; un texto sin coincidencias muestra el aviso.
- Perfil Kindle: activa los guiones y muestra «Fijada por el perfil Kindle»; desmarcar la casilla pasa el
  perfil a «Personalizado».
- Sin libro abierto: «Este libro» desactivado.
- «Opciones del libro…» abre en «Perfil y formato»; «Motor y claves…» en «Traducción».
- Se adaptan los tests que usan el menú «Opciones ▾», los combos de la barra o la casilla de «PDF ▾»
  (entre ellos `test_write_pdf_whole_words_never_splits_a_word` y `test_window_kindle_profile_hyphenates`).

Verificación: `/verificar-ventana` con capturas en los dos temas, a 1366 px y con `--escala 1.25`;
después `/build-exe`.
