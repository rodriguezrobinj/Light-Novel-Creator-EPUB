# Traducción automática del libro

Fecha: 2026-11-08. Aprobado en conversación.

## Objetivo

Traducir el libro abierto, de cualquier idioma a cualquiera, por capítulo o entero, párrafo a párrafo, con
diálogos naturales. Revisión lado a lado (como Ebook Translator de Calibre) antes de aplicar. El resultado
reemplaza al original; el original queda en una versión.

## Motores (`translate.py`, sin UI)

`ENGINES`: nombre visible -> `{"kind", "base", "model", "key"}`.

| Motor | kind | Lotes | Notas |
|---|---|---|---|
| Google (gratis, sin clave) | `google` | 1 párrafo por petición | `translate.googleapis.com/translate_a/single?client=gtx`, no oficial |
| DeepL | `deepl` | hasta 50 | clave `…:fx` va a `api-free.deepl.com`; `tag_handling=xml`; `context` con párrafos anteriores |
| OpenAI, DeepSeek, Gemini, OpenRouter, Compatible con OpenAI (Ollama, LM Studio) | `openai` | ~30 párrafos / 6000 caracteres | `/chat/completions`, URL base y modelo editables |
| Claude | `anthropic` | igual que `openai` | `/v1/messages`, `x-api-key` |

IA: instrucción de traducción literaria (diálogos naturales y coloquiales según el personaje, puntuación del
idioma destino —raya en español—, conservar `<i>`, `<b>`, `[Nota: …]` y honoríficos, aplicar el glosario).
Entrada y salida en JSON `{"1": "…", "2": "…"}`; contexto: los 5 párrafos anteriores (original y traducción).
Si falta algún número o llega vacío, el lote se parte en dos y se reintenta; con un solo párrafo, error.

Red: `_post`/`_get` con urllib (las pruebas los cambian). 429 y fallos de red: 3 reintentos con espera
creciente; después `TranslateError` con mensaje en español. 401/403: «La clave no es válida».

## Unidades

`units(book, start, end)`: los bloques `p` y `heading` con letras, como `(k, prefijo, texto)` en el formato de
`section_text` (prefijo `# ` o `<center>`). `key(texto)`: sha1 de 16 hex del texto. `translated_section(book,
start, end, done)`: el texto de `section_text` con cada línea traducida si su clave está en `done`.

## Datos

- `<pdf>.lnepub.traduccion.json`: `{"src", "dst", "done": {clave: {"text", "hand"}}}`. Por clave: sobrevive a
  otras ediciones y retomar no repite gasto. Se guarda tras cada lote.
- Glosario de traducción `tr_glossary`: `[[original, traducción]]`, en la revisión y en `INHERITED` (pasa a los
  demás tomos). Botón «Añadir nombres de este tomo» con `convert.proper_names`.
- Preferencias en `ajustes.json`, clave `translate`: motor, idiomas, modelo, URL. Claves API cifradas con DPAPI
  de Windows (`CryptProtectData` por ctypes), en base64; nunca en texto plano.

## Ventana «Traducir…» (menú de herramientas, necesita libro)

- Fila 1: De (Detectar + idiomas), A, Motor, Modelo, URL (solo «Compatible con OpenAI»), Clave.
- Fila 2: Alcance (Todo el libro o un capítulo), «Glosario de traducción…», estimación de párrafos y
  caracteres pendientes.
- Tabla Original | Traducción | Estado (Pendiente, Traducido, A mano, Aplicado). Debajo, el original y la
  traducción editable del párrafo elegido (editar marca «A mano»).
- Botones: Traducir / Pausar, Retraducir selección, Aplicar al libro, Cerrar.
- Traducción en `threading.Thread` (urllib suelta el GIL; la regla de procesos es por PyMuPDF); los resultados
  llegan por una cola que la ventana lee con `after`.
- Aplicar: versión «Antes de traducir», luego `apply_edits` de atrás adelante con los tipos de los títulos
  (`kinds`), un solo Ctrl+Z; el idioma del libro pasa al destino. Un párrafo cuyo texto ya es una traducción
  guardada sale «Aplicado».

## Fuera de alcance

EPUB bilingüe. Avisos de calidad para destinos que no son español (los de hoy son de español).

## Pruebas

Motor falso (`_post` cambiado): lotes, partir lote al no cuadrar, marcas conservadas, caché por clave, DPAPI
ida y vuelta, `translated_section`. Ventana offscreen: traducir con motor falso, editar a mano, aplicar,
deshacer, idioma cambiado, versión creada. Luego `/verificar-ventana` en pantalla real.
