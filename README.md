<div align="center">

# Light Novel Creator EPUB

**Aplicación de escritorio para Windows diseñada para convertir, editar, traducir y maquetar novelas ligeras en libros EPUB profesionales.**

[![Plataforma](https://img.shields.io/badge/Windows-10%20%2F%2011%20(64--bit)-0078D6?logo=windows&logoColor=white)](#descargar-y-usar)
[![Python](https://img.shields.io/badge/Python-3.14-3776AB?logo=python&logoColor=white)](#ejecutar-y-compilar-desde-el-código)
[![Interfaz](https://img.shields.io/badge/Qt%206-PySide6%20%2B%20Chromium-41CD52?logo=qt&logoColor=white)](#arquitectura-del-proyecto)
[![Licencia](https://img.shields.io/badge/Licencia-GNU%20AGPL%20v3-blue.svg)](LICENSE)
[![Tests](https://github.com/rodriguezrobinj/Light-Novel-Creator-EPUB/actions/workflows/tests.yml/badge.svg)](https://github.com/rodriguezrobinj/Light-Novel-Creator-EPUB/actions/workflows/tests.yml)
[![Releases](https://img.shields.io/github/v/release/rodriguezrobinj/Light-Novel-Creator-EPUB?label=Release&color=success)](https://github.com/rodriguezrobinj/Light-Novel-Creator-EPUB/releases)

[Características](#características-principales) · [Descargar y usar](#descargar-y-usar) · [Perfiles de salida](#perfiles-y-formatos) · [Atajos](#atajos-de-teclado) · [Desarrollo](#ejecutar-y-compilar-desde-el-código) · [Arquitectura](#arquitectura-del-proyecto) · [Apoyar](#apoyar-el-proyecto) · [Licencia](#licencia-y-atribuciones)

</div>

---

**Light Novel Creator EPUB** transforma archivos **PDF** de novelas ligeras (especialmente traducciones al español) en libros **EPUB** con texto fluido que se adaptan con limpieza a cualquier pantalla —celular, e-reader, tablet o computadora—, y también permite **abrir y editar archivos EPUB existentes sin pérdida** de su maquetación original.

Reúne en un solo flujo todo el proceso editorial de una novela ligera: análisis automático del diseño, OCR para títulos gráficos, edición visual tipo Word con vista previa real en Chromium, corrección ortográfica y de calidad, traducción asistida por IA o traductores clásicos, gestión de series y exportación lista para cada lector.

---

## Características principales

### Conversión inteligente de PDF y edición fiel de EPUB
- **Análisis automático de PDF**: detecta capítulos, subtítulos, ilustraciones, separadores de escena, cursivas, negritas, texto centrado y recuadros, eliminando encabezados, pies de página, números de página e índices impresos redundantes.
- **OCR integrado en español**: reconoce automáticamente títulos de capítulo incrustados como imagen usando Tesseract (`tessdata_fast` en español) a través de PyMuPDF.
- **Edición fiel de EPUB (sin pérdida)**: abre archivos `.epub` ya maquetados (por ejemplo, creados en Sigil o Calibre) y parchea únicamente el texto, los títulos, las secciones o los metadatos modificados, conservando intactos el CSS, las fuentes, el *furigana* y los recursos no editados.
- **Soporte para EPUB japoneses**: convierte el texto vertical en lectura horizontal de izquierda a derecha y respeta capítulos e ilustraciones del original.

### Editor visual tipo Word, Código HTML y Vista previa en Chromium
- **Tres modos sincronizados**: edita en una hoja visual con cinta de formato (`Normal`, `Capítulo`, `Subtítulo`, `Título 3`, `Título 4`, `Cita`), en la **Hoja web** editable renderizada con el CSS real del libro o directamente en **Código HTML** con cabecera editable.
- **Panel lateral configurable**: compara en tiempo real con la página **Original**, la vista previa **EPUB** renderizada en Chromium (`QtWebEngine`) o la **Hoja web**, con soporte para modo oscuro y notas emergentes.
- **Herramientas editoriales completas**: explorador de archivos internos del EPUB, notas al pie o al final del libro, tablas, recuadros temáticos (incluyendo estilo *Pantalla de juego*), gestión de ilustraciones (dividir o girar láminas dobles, recortar márgenes blancos, reemplazar y exportar), control de cambios, panel de navegación (`F9`), modo concentración (`F11`) y deshacer/rehacer por capítulo o en todo el libro.
- **Ortografía y sinónimos en español**: verificación con diccionario Hunspell `es_ES`, subrayado al escribir, diccionario compartido por serie y menú contextual de **Sinónimos** (OpenThesaurus).

### Informe de calidad y validación oficial W3C
- **Diagnóstico visual con nota sobre 100**: anillo de puntuación, barras filtrables por tipo de aviso y mapa por capítulo.
- **19 comprobaciones editoriales**: detecta texto omitido, caracteres rotos, saltos de numeración de capítulos, párrafos partidos o duplicados, números sueltos, texto sin traducir, signos sin pareja, discrepancias con el glosario, variantes de nombres propios, honoríficos inconsistentes, ecos y enlaces residuales.
- **Corrección asistida y exportación**: botón **«Arreglar todos»** con selección por casillas y exportación del informe en **HTML** o **CSV**.
- **Validación con EPUBCheck**: verifica el cumplimiento del estándar oficial del W3C directamente desde la app (descarga opcional bajo demanda; requiere Java).

### Traducción integrada párrafo a párrafo
- **Múltiples motores**: **Google Translate** (gratuito, sin clave), **Google Cloud**, **DeepL**, **OpenAI (ChatGPT)**, **DeepSeek**, **Claude**, **Gemini**, **OpenRouter** y servidores locales compatibles con OpenAI (**Ollama**, **LM Studio**, etc.).
- **Pensada para novelas ligeras**: envía lotes con contexto de párrafos anteriores, protege etiquetas HTML e iconos en línea, aplica el glosario de la serie, permite pausar y reanudar guardando cada lote en disco y crea automáticamente una instantánea en **Versiones** antes de aplicar cambios.

### Gestión de series, automatización y envío
- **Memoria de series y AniList**: hereda automáticamente autor, ilustrador, traductor, glosario, diccionario personalizado, reglas de reemplazo, personajes y estilos entre volúmenes; incluye **Panel de serie** y búsqueda de metadatos en **AniList**.
- **Historial de versiones**: guarda, compara y restaura instantáneas del libro sin sobrescribir tu trabajo.
- **Automatización**: conversión por lotes de carpetas completas, **Carpeta vigilada** que convierte automáticamente nuevos PDF y generador de **Volúmenes recopilatorios (Ómnibus)**.
- **Tipografía y maquetación**: estilos *Clásico*, *Moderno*, *Novela ligera* y *Pantalla de juego*, fuentes integradas (**Literata**, **Bitter**, **Liberation Serif** y **Eagle Lake** para títulos) o tipografía propia (`.ttf` / `.otf`), limpieza tipográfica al estilo RAE, página de créditos, sinopsis, índice con tiempo estimado de lectura y envío directo al móvil por **código QR** en red local.
- **Interfaz moderna**: tema Fluent claro u oscuro (sincronizado con Windows), colores de acento configurables y ventana centralizada de **Opciones** (`Ctrl+,`) con buscador.

---

## Descargar y usar

> Compatible con **Windows 10 y Windows 11 (64 bits)**. No requiere Python.

Ve a la sección de [**Releases**](https://github.com/rodriguezrobinj/Light-Novel-Creator-EPUB/releases) y elige el formato que prefieras:

### Instalador (recomendado)
1. Descarga **`LightNovelCreator_<versión>_x64-setup.exe`** y ejecútalo.
2. Por defecto instala para tu usuario (sin pedir permisos de administrador), crea el acceso directo en el Menú Inicio y registra la app en **«Abrir con…»** para archivos PDF y EPUB.
3. Tus preferencias, memoria de series y cachés se guardan en `%APPDATA%\PDF a EPUB` y se conservan al actualizar a nuevas versiones.

### Versión portable (sin instalación)
1. Descarga **`LightNovelCreator-portable.zip`** y descomprime la carpeta completa en cualquier ubicación (incluida una memoria USB), manteniendo `PDF a EPUB.exe` junto a su carpeta `_internal`.
2. Ejecuta **`PDF a EPUB.exe`** y arrastra un archivo **PDF** o **EPUB** a la ventana.

> [!NOTE]
> **Aviso de Windows SmartScreen**: al no estar firmado con un certificado comercial, Windows puede mostrar *«Windows protegió su PC»*. Pulsa **Más información** y luego **Ejecutar de todas formas**. El primer arranque puede tardar unos segundos.

### Datos y archivos generados
- **Versión instalada**: guarda las preferencias (`ajustes.json`), la memoria de series, las cachés y el registro (`registro.log`) en `%APPDATA%\PDF a EPUB`.
- **Versión portable (carpeta `datos` junto al `.exe`)**: guarda toda la configuración dentro de `datos`, así puedes llevar la carpeta completa a otro equipo o USB. Si eliminas `datos`, pasa a usar `%APPDATA%\PDF a EPUB`.
- Junto a cada libro abierto, el programa guarda su archivo de revisión (`<archivo>.lnepub.json`) y el EPUB o PDF generado.

---

## Perfiles y formatos

- **Formatos de entrada**: `PDF`, `EPUB` (EPUB 2, EPUB 3 y EPUB japoneses).
- **Formatos de salida**: `EPUB 3`, `EPUB 2`, `KEPUB` (`.kepub.epub` para Kobo) y `PDF maquetado` (`A5`, `e-reader 6″`, `A4`).

| Perfil | Formato | Imágenes | Partición silábica (`pyphen`) | Uso recomendado |
| :--- | :--- | :--- | :---: | :--- |
| **Celular** | EPUB 3 | Color · máx. 1200 px | No | Lectores en Android / iOS |
| **E-reader de tinta** | EPUB 3 | Escala de grises · máx. 1400 px | No | Lectores de tinta electrónica genéricos |
| **Tablet** | EPUB 3 | Color · máx. 2000 px | No | Pantallas grandes e ilustraciones en alta resolución |
| **Kindle** | EPUB 3 | Color · máx. 1600 px | Sí (español) | Envío a Kindle (evita huecos en texto justificado) |
| **Kobo** | KEPUB | Escala de grises · máx. 1400 px | No | Paginación y estadísticas nativas en Kobo |
| **Lector antiguo** | EPUB 2 | Color · máx. 1400 px | No | Dispositivos que no soportan EPUB 3 |

---

## Atajos de teclado

<details>
<summary>Ver referencia rápida de atajos (Lista de capítulos, Editor y General)</summary>

#### Lista de capítulos
| Atajo | Acción |
| :--- | :--- |
| `C` / `S` / `X` / `Q` | Marcar fila como **Capítulo**, **Subtítulo**, **Separador** o **Quitar** |
| `I` / `D` / `G` | Marcar como **Ilustración**; en láminas dobles: **Dividir** o **Girar 90°** |
| `F2` (o doble clic) | Renombrar título |
| `E` | Abrir el editor de texto en la fila seleccionada |
| `R` | Marcar o desmarcar como revisado |
| `Alt+↑` / `Alt+↓` | Reordenar filas (también arrastrando) |

#### Editor de texto
| Atajo | Acción |
| :--- | :--- |
| `Ctrl+K` / `Ctrl+I` | Cursiva |
| `Ctrl+N` / `Ctrl+B` | Negrita |
| `Ctrl+S` | Subrayado |
| `Ctrl+T` | Alternar título / párrafo |
| `Ctrl+E` / `Ctrl+D` | Centrar / Alinear a la derecha |
| `Ctrl+J` | Unir con el párrafo siguiente |
| `Mayús+Intro` | Salto de línea dentro del mismo párrafo |
| `Mayús+F3` | Alternar mayúsculas y minúsculas |
| `Ctrl+Alt+-` / `Ctrl+Alt+.` / `Ctrl+Alt+Q` | Insertar raya (`—`), puntos suspensivos (`…`) o comillas (`«»`) |
| `Ctrl+Alt+K` | Insertar enlace |
| `Ctrl+F` / `Ctrl+H` | Buscar / Reemplazar |
| `F7` / `F8` | Ir al cambio siguiente / aviso de calidad siguiente |
| `F9` / `F11` | Panel de navegación / Modo concentración |
| `Ctrl+Intro` | Marcar capítulo como revisado y pasar al siguiente sin revisar |
| `Ctrl+RePág` / `Ctrl+AvPág` | Capítulo anterior / siguiente |

#### General
| Atajo | Acción |
| :--- | :--- |
| `Ctrl+Z` / `Ctrl+Y` | Deshacer / Rehacer |
| `Ctrl+F` | Buscar en todo el libro |
| `Ctrl+,` | Abrir **Opciones** |

</details>

---

## Ejecutar y compilar desde el código

### Requisitos
- **Windows 10 / 11 (64 bits)**
- **Python 3.14**

### Instalación y ejecución

```powershell
git clone https://github.com/rodriguezrobinj/Light-Novel-Creator-EPUB.git
cd Light-Novel-Creator-EPUB

python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe app.py
```

### Pruebas automáticas

La suite incluye más de 500 pruebas automatizadas (conversión con PDF sintéticos, edición fiel de EPUB, traducción, informe de calidad y pruebas de interfaz Qt *offscreen*):

```powershell
# Ejecución en paralelo (4 procesos)
.venv\Scripts\python.exe test_convert.py

# Ejecución secuencial en un solo proceso
.venv\Scripts\python.exe test_convert.py --paralelo 1
```

### Compilar el ejecutable, instalador y paquete portable

```powershell
.venv\Scripts\python.exe -m PyInstaller --noconfirm "PDF a EPUB.spec"
```

- El ejecutable se genera en modo carpeta dentro de `dist\Light Novel Creator\` (`PDF a EPUB.exe` junto a `_internal\`, optimizado para el arranque rápido con Chromium).
- **Para compilar el instalador (`setup.exe`)** con [Inno Setup 6](https://jrsoftware.org/isinfo.php):
  ```powershell
  & "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /DAppVersion=0.5.0 /DAppNumericVersion=0.5.0.0 installer\setup.iss
  ```
  El instalador queda en `dist\installer\LightNovelCreator_0.5.0_x64-setup.exe`.
- **Para armar el `.zip` portable**: copia el contenido de `dist\Light Novel Creator\` junto con `portable\LEEME.txt` y `LICENSE`, crea dentro una carpeta vacía llamada `datos` y comprime la carpeta resultante.

### Integración continua y versiones

- **Tests** ([`tests.yml`](.github/workflows/tests.yml)): cada PR y cada push a `master` ejecutan la suite en Windows. `master` solo acepta PR con el check `tests` en verde.
- **Release** ([`release.yml`](.github/workflows/release.yml)): al empujar un tag `v*`, GitHub construye el `.exe`, comprueba Chromium, compila el instalador `LightNovelCreator_<versión>_x64-setup.exe` con Inno Setup, arma `LightNovelCreator-portable.zip` y publica ambos en la pre-release con las notas de los PR:
  ```powershell
  git tag v0.5.0; git push origin v0.5.0
  ```
  Ejecutado a mano desde la pestaña *Actions*, deja el instalador y el zip como artefactos sin publicar nada.
- **Dependabot** ([`dependabot.yml`](.github/dependabot.yml)): cada mes abre PR con versiones nuevas de `requirements.txt` y de las acciones; las tres de PySide6 van juntas. Se fusionan a mano tras probar el `.exe`.
- **Seguridad**: CodeQL analiza el código en cada PR; los fallos se avisan en privado según [`SECURITY.md`](SECURITY.md).

### Diagnóstico y registro

La aplicación guarda un registro rotativo en `registro.log` (junto a los ajustes), accesible desde **Herramientas › Registro de la aplicación**. Para imprimir un resumen de auditoría (errores, congelamientos y operaciones lentas) desde consola:

```powershell
.venv\Scripts\python.exe registro.py
```

---

## Arquitectura del proyecto

| Archivo / Carpeta | Responsabilidad |
| :--- | :--- |
| [`app.py`](app.py) | Ventana principal (`Light Novel Creator`), diálogos, gestión de series, lotes, ómnibus, QR, EPUBCheck y proceso trabajador aislado. |
| [`convert.py`](convert.py) | Motor de análisis de PDF (sin interfaz), generación de EPUB 3 / EPUB 2 / KEPUB / PDF, OCR, limpieza tipográfica, ortografía e informe de calidad. |
| [`faithful.py`](faithful.py) | Lectura y escritura fiel de EPUB de entrada parcheando únicamente los bloques modificados. |
| [`editor.py`](editor.py) | Hoja de edición visual tipo Word (`Sheet`), conversión bidireccional con texto/HTML y vista **Código HTML**. |
| [`webview.py`](webview.py) | Integración con Chromium (`QtWebEngine`) para la vista previa real de EPUB y la hoja web. |
| [`websheet.py`](websheet.py) | Pestaña **Hoja web** editable con sincronización en vivo respecto a la hoja de texto y la cinta. |
| [`translate.py`](translate.py) | Motor de traducción por lotes sin interfaz (Google, Google Cloud, DeepL, OpenAI, DeepSeek, Claude, Gemini, OpenRouter y APIs locales). |
| [`options.py`](options.py) | Ventana centralizada de **Opciones** por categorías, con buscador y alcance por libro o global. |
| [`report.py`](report.py) | Componentes gráficos del informe de calidad (anillo de nota, barras por tipo de aviso y mapa de capítulos). |
| [`theme.py`](theme.py) | Paletas Fluent claro/oscuro, acentos y hojas de estilo QSS consistentes en Windows 10 y 11. |
| [`qtk.py`](qtk.py) | Capa de widgets Qt reutilizables y registro de acciones de usuario. |
| [`registro.py`](registro.py) | Sistema de logging rotativo, detector de bloqueos de interfaz y utilidad de auditoría CLI. |
| [`test_convert.py`](test_convert.py) | Suite completa de pruebas unitarias, de conversión y de interfaz. |
| [`fonts/`](fonts/) · [`dict/`](dict/) · [`tessdata/`](tessdata/) | Tipografías SIL OFL, diccionario Hunspell `es_ES` + tesauro de sinónimos y modelo OCR `spa` incluidos. |

---

## Apoyar el proyecto

Light Novel Creator EPUB es gratuito y de código abierto. Si te resulta útil, puedes apoyar su desarrollo con una donación:

[![GitHub Sponsors](https://img.shields.io/badge/GitHub%20Sponsors-Apoyar-EA4AAA?logo=githubsponsors&logoColor=white)](https://github.com/sponsors/rodriguezrobinj)
[![Ko-fi](https://img.shields.io/badge/Ko--fi-Invitar%20un%20café-FF5E5B?logo=kofi&logoColor=white)](https://ko-fi.com/rodriguezrobinj)

Dentro de la aplicación: **Herramientas › Apoyar el proyecto**.

---

## Licencia y atribuciones

Este proyecto se distribuye bajo la licencia [**GNU Affero General Public License v3 (AGPL v3)**](LICENSE).

Incluye o utiliza los siguientes componentes y recursos de terceros:

- [**PyMuPDF**](https://github.com/pymupdf/PyMuPDF) — GNU AGPL v3.
- [**PySide6 / Qt 6 y QtWebEngine**](https://doc.qt.io/qtforpython-6/) — GNU LGPL v3.
- [**Pillow**](https://python-pillow.org/), [**segno**](https://github.com/heuer/segno), [**spylls**](https://github.com/zverok/spylls) y [**Pyphen**](https://pyphen.org/) — dependencias Python fijadas en [`requirements.txt`](requirements.txt).
- **Tipografías incluidas** ([`fonts/`](fonts/)): **Literata**, **Bitter**, **Liberation Serif** y **Eagle Lake** — SIL Open Font License 1.1.
- **Diccionarios de español** ([`dict/`](dict/)): diccionario ortográfico `es_ES` de [**RLA-ES**](https://github.com/sbosio/rla-es) (GPL v3 / LGPL v3 / MPL 1.1) y tesauro de sinónimos **OpenThesaurus** (LGPL v2.1) — ver [`dict/LICENSE.md`](dict/LICENSE.md).
- **Datos OCR** ([`tessdata/`](tessdata/)): modelo `spa` de [**Tesseract `tessdata_fast`**](https://github.com/tesseract-ocr/tessdata_fast) — Apache License 2.0.
- [**EPUBCheck**](https://github.com/w3c/epubcheck) (W3C, licencia BSD) — validador opcional descargado bajo demanda por el usuario.
