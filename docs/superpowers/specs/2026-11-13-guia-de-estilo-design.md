# Guía de estilo de la interfaz — diseño

## Para qué

Cada ventana nueva decide a mano márgenes (2/4/6/8/10/12 mezclados en `app.py`), tamaños de letra, tamaños
de diálogo y glifos de los botones. Los colores ya están en `theme.py`, pero nada dice cuándo usar
`primary`, `warn`, `chip` o `link`. Los iconos son glifos Unicode dentro del texto, mezclando monocromos
(`↶ ✎ ✂`) con emoji a color (`🔗 🔍 🔊 📄 ⚠`) que en Windows 10 y 11 se ven distintos y no siguen el tema.

Objetivo: un formato fijo, escrito y comprobable, para que un cambio de interfaz no abra decisiones.

Éxito:
- Una sola página responde «¿qué uso para esto?» (color, letra, espacio, botón, diálogo, icono, texto).
- `theme.py` guarda los tokens que el código nuevo usa en vez de números sueltos.
- Un test falla si entra un emoji o un color hex nuevo fuera de la lista permitida.
- La app se ve igual, salvo los cinco emoji, que pasan a glifos monocromos.

Fuera de alcance: galería visual de componentes, migrar los márgenes y espacios viejos (moverían píxeles
sin que nadie lo pida), iconos SVG.

## Piezas

1. **`docs/ui-style-guide.md`** — la guía, en español. Secciones: principios, colores por rol, tipografía,
   espaciado, componentes (`primary`, `warn`, `ok`, `chip`, `link`, `card`, `bar`, `drop`, `muted`,
   `title`, `big`), diálogos, iconografía, textos, qué no hacer.
2. **Tokens en `theme.py`**:
   - `SPACE = {"tight": 4, "gap": 6, "pad": 8, "section": 12}` — los valores que ya dominan.
   - `FONT = {"body": 9, "title": 11, "big": 13}` — puntos; coinciden con el QSS actual.
   - `DIALOG = {"s": (560, 260), "m": (780, 440), "l": (980, 600)}` — mínimos de los cuadros existentes.
   - `ICONS` — el glifo de cada acción. Solo monocromos: los dibuja la fuente de texto y toman el color
     del tema.
3. **Test `test_style_guide`** en `test_convert.py`:
   - `ICONS` no tiene emoji y los tokens existen con las claves de la guía.
   - Ningún `.py` de la interfaz contiene un emoji (`U+1F000` en adelante ni `⚠ ⏮ ⏭ ⚙ ⛓ ❗`).
   - Ningún hex nuevo en `app.py`, `editor.py` y `qtk.py` fuera de la lista permitida (los once actuales,
     tonos de marcas que no cambian con el tema).
   - El contraste ya lo cubre `test_theme_text_is_readable`; no se repite.

## Iconografía (regla)

- Un icono es un glifo de texto monocromo, nunca un emoji. Un emoji sale a color por Segoe UI Emoji y
  Windows 10 y 11 lo dibujan distinto.
- Qt ignora el selector `U+FE0E`, así que no hay forma de forzar «texto» en un glifo que es emoji: se
  cambia de glifo. Comprobado dibujando cada candidato sobre fondo transparente con la fuente de Windows
  (monocromo = todos los píxeles negros).
- Botón con icono: `«<glifo> Texto»`, con la variante corta solo el glifo (`short=`) y el nombre en la
  ayuda emergente. Un botón sin texto siempre lleva ayuda emergente.
- Un icono = una acción en toda la app.

Cambios de glifo, todos comprobados monocromos:

| Antes | Ahora | Dónde |
|---|---|---|
| `⚠` | `▲` | avisos del botón de calidad, guía de ventana, dudas |
| `🔗` | `↗` | «Enlace» |
| `🔍` | `⌕` | «Buscar» |
| `🔊` | `♪` | «Leer» |
| `📄` | `🗎` (`U+1F5CE`) | zona de soltar PDF y miniatura que falta |

Los textos de los tests que citan `⚠` se actualizan al glifo nuevo.

## Riesgos

- `🗎` (`U+1F5CE`) sale monocromo en esta máquina (Windows 10). Si en Windows 11 saliera a color, la
  zona de soltar cambia a `▤` (`U+25A4`, mismo resultado en la prueba).
- Quien tenga una fuente de sistema rara puede ver cuadros vacíos en `⌕` o `↗`; son glifos de Segoe UI
  Symbol, que viene con Windows 10 y 11.

## Después

Reconstruir el .exe (se toca `theme.py` y `app.py`).
