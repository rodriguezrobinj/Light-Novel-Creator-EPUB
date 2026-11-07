# Editor estilo Word, segunda entrega — plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Spec: `docs/superpowers/specs/2026-11-06-editor-word-2-design.md`.

**Goal:** puntos 1–10 de la especificación. **Architecture:** `convert.py` (lectura Symbol, enlaces,
centrado), `editor.py` (propiedades de párrafo y carácter nuevas, operaciones de hoja), `app.TextEditor`
(cinta, navegación desplegable, búsqueda, sincronización). **Tech:** PySide6, PyMuPDF.

## Global Constraints
- Lo no cambiado se guarda igual (línea original); textos en español con tildes; colores del tema.

## Review Focus
1. URL con `&` o comillas: el XHTML del EPUB sigue válido (`check_epub` sin errores).
2. Reemplazar todos con una palabra que aparece dentro de un enlace: no rompe el `href`.
3. Pasar de capítulo con el PDF desde una página sin texto (ilustración): la hoja va al capítulo de esa página.
4. Centrar un título: no aplica (solo párrafos), suena.
5. Revisión vieja con edición de un párrafo centrado: sigue centrado tras reabrir.

### Task 1: «♠♠» de la fuente Symbol (convert.py) — prueba con PDF sintético `symb`; regresión.
### Task 2: enlaces y centrado en section_text / edit_section / EPUB (convert.py) — pruebas de ida y vuelta.
### Task 3: editor.py — tipo de título, enlaces, centrado, mayúsculas, buscar/reemplazar en documento.
### Task 4: app.TextEditor — navegación desplegable con indicador y ◀ ▶, sincronización PDF → hoja,
desplegable Normal/Capítulo/Subtítulo con tipos al guardar, botones Enlace/Nota/Centrar, barra de búsqueda.
### Task 5: /verificar-ventana, /build-exe, /commit.
