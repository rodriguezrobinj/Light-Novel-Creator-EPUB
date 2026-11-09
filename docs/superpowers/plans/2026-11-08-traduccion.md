# Traducción automática Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ventana «Traducir…» que traduce el libro párrafo a párrafo con Google, DeepL o IA, revisión lado a lado y
«Aplicar al libro».

**Architecture:** `translate.py` (motor sin UI, red por `_post`/`_get`) + `App.translate()` en `app.py` (diálogo,
hilo de traducción, cola leída con `after`, aplicar con `apply_edits`).

**Tech Stack:** Python 3.14, urllib, ctypes (DPAPI), PySide6 vía `qtk`.

**Spec:** `docs/superpowers/specs/2026-11-08-traduccion-design.md`

## Global Constraints

- Sin dependencias nuevas.
- Textos visibles en español con tildes; comentarios en español; identificadores en inglés.
- Pruebas en `test_convert.py`, funciones `test_*` con `assert`, sin red real.
- Nada de archivos de prueba junto a los PDF del usuario (todo en `TMP`).
- Claves API nunca en texto plano en `ajustes.json`.

## Review Focus

- Respuesta de IA con texto alrededor del JSON (```json … ```): se extrae el objeto igual.
- IA que devuelve menos párrafos o los junta: el lote se parte, no se pierde ni se desplaza ninguno.
- Párrafo con `<i>` mal cerrado en la traducción: `edit_section` lo limpia, el EPUB no se rompe.
- Cerrar la ventana a mitad de lote: el hilo para, lo ya traducido queda guardado.
- Aplicar con títulos de capítulo traducidos: la lista conserva Capítulo/Subtítulo.

---

### Task 1: `translate.py` — unidades, caché y lotes de IA

**Files:** Create `translate.py`; Test `test_convert.py`.

**Produces:**
- `ENGINES: dict[str, dict]`, `LANGS: dict[str, str]` (código -> nombre en español).
- `key(text) -> str`; `units(book, start, end) -> [(k, prefix, text)]`;
  `translated_section(book, start, end, done) -> str`.
- `translate(engine, texts, src, dst, api_key="", model="", base="", glossary=(), context=()) -> [str]`.
- `batches(texts, limit) -> [[int]]`; `TranslateError(Exception)`.

Tests: `test_translate_units_and_section`, `test_translate_llm_batch_and_split` (falso `_post` que la primera vez
devuelve un párrafo de menos dentro de ```json), `test_translate_google_and_deepl_requests` (URL, cabeceras, claves
`:fx`, `<i>` reparado), `test_translate_errors` (401 -> «La clave no es válida», 429 reintenta).

### Task 2: DPAPI

`protect(text) -> str` y `unprotect(data) -> str` en `translate.py` (base64 de `CryptProtectData`).
Test `test_translate_dpapi_roundtrip`.

### Task 3: Ventana «Traducir…»

**Files:** Modify `app.py` (menú de herramientas, `review_state`, `apply_state`, `INHERITED`, `FIELD_LABELS`,
`load_book` vacía `tr_glossary`), Test `test_convert.py`.

- `_translation_path(pdf)`, `_read_translation(pdf)`, `App.translate()`, `App.apply_translation(done)`.
- Hilo `_translate_thread(job, out, stop)` que pone `("done", {clave: texto})`, `("error", mensaje)`, `("end",)`.
- Test `test_window_translate_apply_and_undo`: `translate._post` falso, traducir el libro, editar uno a mano,
  aplicar, revisar texto, idioma, versión «Antes de traducir», tipos de la lista, Ctrl+Z vuelve al original.

### Task 4: Verificar y construir

`/verificar-ventana` en pantalla real (clics), `/build-exe`, commit y merge con `/commit`.
