# Editor estilo Word, segunda entrega — diseño

Fecha: 2026-11-06 · Aprobado en conversación (puntos 1–5 y funciones elegidas por el usuario).

## Objetivo

Que editar un libro en «Editar texto…» sea cómodo como en Word: saber siempre en qué capítulo se está,
moverse sin perder sitio, que la hoja y el PDF vayan juntos, y tener las herramientas habituales.

## Cambios

1. **Navegación desplegable.** Sin panel fijo: un botón en la cinta «☰ Capítulo 3 de 31 · Título ▾» abre el
   listado de capítulos; ◀ ▶ a sus lados van al capítulo anterior y al siguiente.
2. **Indicador.** Ese botón dice siempre el capítulo de la hoja.
3. **Sincronización PDF → hoja.** Si ◀ ▶ del panel del PDF llegan a una página de otro capítulo, la hoja
   cambia a ese capítulo y pone el cursor en el primer párrafo de esa página. (Hoja → PDF ya existía.)
4. **Capítulo / Subtítulo.** El desplegable de párrafo: «Normal / Capítulo / Subtítulo». La hoja guarda el
   tipo de cada título (propiedad del párrafo); al aceptar, la fila de la lista queda con ese tipo (uno
   nuevo, uno cambiado de tipo o uno renombrado).
5. **«♠♠» de la fuente Symbol.** Los textos en una fuente Symbol se traducen a Unicode (0xAA → ♠, 0xA7 ♣,
   0xA8 ♦, 0xA9 ♥, 0xB7 •, …); ♠♣♥ cuentan como separador de escena; una línea solo de símbolos es
   separador aunque tenga letra de título (Mitsuha 6 [Bell T]: los ♠♠ salían como capítulos «ªª»).
6. **Enlaces.** Ctrl+Alt+K (Word en español) o botón «Enlace»: pide la dirección (http/https) para el texto
   elegido. En `section_text` y `edit_section`: `<a href="https://…">texto</a>`; en el EPUB, enlace.
7. **Buscar y reemplazar.** Ctrl+F / Ctrl+H: barra en la hoja; busca en todos los capítulos (salta de
   capítulo), reemplaza el actual o todos, con deshacer. Sin distinguir mayúsculas.
8. **Nota al pie.** Botón «Nota»: inserta «[Nota: …]» en el cursor y deja escribir dentro; en el EPUB,
   nota emergente (como hoy).
9. **Centrar párrafo.** Botón «Centrar» (Ctrl+E, como Word): el párrafo va centrado en el EPUB (cartas,
   carteles). En `section_text`, una línea centrada empieza por `<center>`; los párrafos que el PDF ya trae
   centrados se ven centrados en la hoja.
10. **Mayúsculas.** Mayús+F3 como Word: la selección pasa por minúsculas → Primera Mayúscula → MAYÚSCULAS.

## Compatibilidad

- Revisiones guardadas: una edición vieja de un párrafo centrado (sin `<center>`) sigue reconociendo el
  párrafo sin cambios (`edit_section` busca la línea con y sin el prefijo).
- `convert.py` cambia en: lectura de spans (Symbol), orden título/separador, `SCENE_BREAK`, `_line`,
  `_inline`, `edit_section` (centrado). `/regresion-series` antes del commit.

## Pruebas

Por cada punto, una prueba en `test_convert.py` (PDF sintético con fuente `symb` para el 5; ida y vuelta de
enlaces y centrado hasta el EPUB; ventana sin pantalla para 1–4 y 6–10). `/verificar-ventana` en los dos
temas.
