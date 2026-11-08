# Plan — versiones del libro

Spec: `docs/superpowers/specs/2026-11-08-versiones-design.md`. Todo en `app.py` y `test_convert.py`.

1. **Pruebas primero** (`test_convert.py`): guardar/comparar/restaurar, versión automática al generar,
   zip dañado, versión de otro análisis. Fallan.
2. **Almacén** (funciones de módulo junto a `_review_path`): `_versions_path`, `_load_versions`
   (aparta un zip dañado), `_write_versions` (tmp + `os.replace`, borra el zip si no queda ninguna).
3. **Comparación** (funciones de módulo): `_compare(old, new, old_blocks, new_blocks)` → segmentos
   `(texto, tag)`; `_version_changes(prev, cur)` → resumen de una línea.
4. **Ventana** (`App`):
   - `review_state()` sale de `save_review`;
   - `show_book` guarda `self.pristine` (bloques, capítulos, portada);
   - `load_review(state=None)`;
   - `add_version`, `restorable`, `restore_version`, `version_blocks`;
   - versión automática en `saved`;
   - diálogo `versions()` y su entrada en el menú de herramientas.
5. Suite completa, `/verificar-ventana` a mano, `/build-exe`, `/commit`.
