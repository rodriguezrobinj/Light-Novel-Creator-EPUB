# Guía de estilo de la interfaz — plan

Spec: `docs/superpowers/specs/2026-11-13-guia-de-estilo-design.md`. Rama `feat/guia-de-estilo`.

1. **Test primero** (`test_style_guide` en `test_convert.py`): falla porque no existen los tokens, hay
   emoji y `ICONS` no existe.
2. **`theme.py`**: añadir `SPACE`, `FONT`, `DIALOG`, `ICONS`.
3. **Glifos**: en `app.py` cambiar `⚠`→`▲`, `🔗`→`↗`, `🔍`→`⌕`, `🔊`→`♪`, `📄`→`🗎`; actualizar los
   `assert` de `test_convert.py` que citan `⚠`. Comentario de `app.py:6333` sin los emoji.
4. **`docs/ui-style-guide.md`**: escribir la guía con los valores reales de `theme.py`.
5. **Verificar**: suite completa (`.venv/Scripts/python.exe test_convert.py`), luego `/verificar-ventana`
   (el glifo se ve monocromo en claro y oscuro), luego `/build-exe`. No se corren a la vez.
6. **Commit** en español sin firma de Claude (`/commit`): `feat: guía de estilo de la interfaz` y merge
   local a `master`. Sin push.
