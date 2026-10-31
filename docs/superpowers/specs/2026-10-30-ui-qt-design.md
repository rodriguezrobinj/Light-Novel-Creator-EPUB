# UI nueva en Qt — Diseño

Fecha: 2026-10-30

## Objetivo

Rehacer la ventana en Qt (PySide6) para que el flujo abrir → revisar → generar sea claro para alguien
nuevo, se vea moderna en Windows 10 y 11, y deje más espacio a revisar. Qt porque la app va a seguir
creciendo: modelo/vista, paneles, texto enriquecido y DPI alto que tkinter no da. Mismas funciones que
hoy: no se añade ninguna salvo soltar un PDF sobre la ventana.

## Decisiones

- **Distribución B.** Barra superior (Abrir PDF, Recientes, Herramientas, Buscar). Debajo, una tarjeta del
  libro: portada, título, autor · grupo · idioma, y «Editar datos ▾», que despliega todos los campos
  (título, autor, serie, volumen, grupo, traductor, idioma, ilustraciones, corrección, edición, título
  original, géneros, fecha, sinopsis), Portada, Reemplazos, Títulos y «Buscar en internet (AniList)».
  Abajo, la lista a la izquierda y las vistas previas a la derecha con todo el alto, con un divisor que
  se arrastra; pestañas PDF | EPUB | Ambos y la tira de miniaturas debajo. Barra inferior: perfil,
  separador, estilo, letra, Opciones, el estado y «Generar EPUB» como botón principal.
- **Aspecto Fluent claro y oscuro.** Estilo Fusion con paleta y QSS propios (`theme.py`): se ve igual en
  Windows 10 y 11 (el estilo nativo de Qt en Windows 10 es el antiguo). Segoe UI (Segoe UI Variable solo
  existe en Windows 11). Sin Mica. Sigue el modo de aplicación de Windows (`QStyleHints.colorScheme`);
  «Herramientas → Tema: Sistema / Claro / Oscuro» lo fija y se guarda en `ajustes.json` (`theme`).
  Los colores de la lista, del editor y del informe salen de la paleta activa, no fijos.
- **Guía sin pasos.** Sin PDF: zona grande «Suelta aquí un PDF de novela ligera», «Abrir PDF…»
  destacado, enlace «Convertir una carpeta entera» y los recientes con su estado (EPUB generado,
  revisado, sin revisar; el mismo cálculo que el panel de serie). Con libro: la barra inferior dice qué
  falta («1 aviso grave: revísalo antes de generar» + «Ver informe») y «Generar EPUB» siempre a la
  derecha.
- **Ventanas secundarias** (siguen siendo métodos de `App`).
  - Dentro de la ventana: Buscar (Ctrl+F) es una barra sobre la lista con los resultados debajo.
    Sinopsis y Más datos pasan al panel «Editar datos». El aviso de guardado y «EPUB listo» son un solo
    diálogo con avisos, tamaño y Abrir / Carpeta / Copiar al lector / Enviar a Kindle.
  - Ventanas propias con el estilo nuevo: informe de calidad, editor de texto, panel de serie.
    Diálogos: portada, reemplazos, resultados de AniList, carpeta vigilada.
  - La línea de atajos sale de la ventana: «Herramientas → Atajos de teclado» y tooltip de la lista.
- **Nombre visible «Light Novel Creator»** (título de la ventana y de la pantalla vacía). El .exe
  (`PDF a EPUB.exe`), `%APPDATA%\PDF a EPUB\ajustes.json`, las revisiones `.lnepub.json` y la instalación
  no cambian.
- Textos: «1 grave» en singular (hoy «1 graves»).

## Código

- `app.py` sigue con las funciones sin ventana, `App` (ventana principal y sus diálogos) y `TextEditor`.
  No se parte en más módulos: los tests sustituyen globales de `app` (`SETTINGS`, `messagebox`,
  `urllib.request`) y, con la ventana en otro módulo, esas sustituciones dejarían de llegarle sin que
  ningún test lo note.
- `qtk.py`: adaptadores Qt con la API de Tk que ya usa el código, para portar la lógica casi tal cual:
  `RowList` (`QTreeWidget` con `get_children`, `set`, `item`, `selection`, `selection_set`, `exists`,
  `see`, `delete`, `insert`, `index`, `bbox`), `TextBox` (`QPlainTextEdit` con índices `"L.C"`,
  `insert`/`end`/`sel.first`, `+Nc`, `linestart`/`lineend`, tags y deshacer agrupado), y `messagebox` y
  `filedialog` sobre `QMessageBox`/`QFileDialog` con botones en español.
- `theme.py`: paletas, QSS, seguimiento del modo de Windows.
- `convert.py` no cambia.
- El proceso de trabajo y su cola no cambian; un `QTimer` de 100 ms sustituye a `root.after`. Las páginas
  de PDF y EPUB se pintan con PyMuPDF a `QImage`. Arrastrar un PDF sobre la ventana lo abre
  (`dragEnterEvent`/`dropEvent`).
- tkinter desaparece del proyecto.

## Pruebas y build

- `test_convert.py` sigue siendo el único archivo de tests. Los tests de ventana crean un
  `QApplication` con `QT_QPA_PLATFORM=offscreen`; los que simulaban clics con `bbox`/`event_generate`
  se reescriben con `QTest`. Tests nuevos: tema (claro/oscuro/sistema y su ajuste), pantalla vacía y
  recientes con estado, soltar un PDF, barra de búsqueda, diálogo de resultado único.
- `requirements.txt`: `pyside6-essentials==6.12.0` (wheel abi3, vale para Python 3.14).
- `/build-exe`: PyInstaller excluye los módulos Qt que no se usan; se mide y se informa el tamaño del
  .exe frente al de tk.
- Antes de cerrar: probar con PDFs reales desde el .exe y capturas en claro y oscuro a 1366×768.

## Fuera de alcance

Funciones nuevas, Mica, iconos propios por botón, cambiar el nombre del .exe o de la carpeta de ajustes.
