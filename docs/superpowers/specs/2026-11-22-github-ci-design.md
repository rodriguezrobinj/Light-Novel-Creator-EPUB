# Repositorio autónomo en GitHub — diseño

## Objetivo

Que el repositorio se cuide solo: cada PR se prueba antes de entrar en `master`, cada versión se construye y
publica sin pasos a mano, y las dependencias avisan cuando hay versiones nuevas o fallos de seguridad.

Éxito:
- Un PR con tests en rojo no se puede fusionar en `master`.
- `git tag v0.5.0 && git push origin v0.5.0` deja una pre-release con `LightNovelCreator-portable.zip`.

## Piezas

1. **CI** (`.github/workflows/tests.yml`): en cada PR y cada push a `master`. `windows-latest`, Python 3.14,
   caché de pip, `pip install -r requirements.txt`, `python test_convert.py` (el propio runner pone
   `QT_QPA_PLATFORM=offscreen` y `LNC_SIN_CHROMIUM=1`). Una ejecución nueva de la misma rama cancela la vieja.
2. **Protección de `master`**: el check `tests` es obligatorio para fusionar. Sin revisiones obligatorias (un
   solo programador); el administrador puede saltarla en una emergencia.
3. **Release** (`.github/workflows/release.yml`): al empujar un tag `v*`. Construye con `PDF a EPUB.spec`,
   comprueba Chromium con `--comprobar-chromium` (exige `ok`), arma el zip con la carpeta `LightNovelCreator/`
   (el build, `datos/` vacía, `LEEME.txt`, `LICENSE`) y crea la pre-release con notas generadas desde los PR.
   El zip se hace con 7-Zip: `Compress-Archive` pierde la carpeta vacía `datos/`, y sin ella el programa
   escribe en `%APPDATA%`.
4. **Dependabot** (`.github/dependabot.yml`): mensual, `pip` y `github-actions`. Los tres paquetes de PySide6
   van en un solo PR porque deben subir juntos. Sin fusión automática: un Qt nuevo puede romper el .exe
   aunque los tests pasen.
5. **Plantillas y seguridad**: formularios de issue en español («Error», «Sugerencia») y `config.yml`,
   plantilla de PR con el checklist de CLAUDE.md, `SECURITY.md` (avisos privados de GitHub), CodeQL con la
   configuración por defecto de GitHub, alertas de Dependabot y secret scanning.

## Fuera de alcance

Bots de issues viejos, fusión automática, firma del .exe, macOS y Linux.

## Riesgos

- La suite puede depender de algo del PC del usuario (fuentes, rutas). Se ve en el primer CI y se arregla.
- La comprobación de Chromium puede fallar en un runner sin GPU. Si pasa, se decide con el resultado delante.
