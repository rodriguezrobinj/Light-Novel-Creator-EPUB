# Editor: corrección (K) y trabajo (L) — Diseño

Fecha: 2026-11-07 · K aprobada en conversación (ortografía con proceso propio, opción a). L pedida a la vez;
sus funciones son las elegidas por el usuario en las preguntas.

## Objetivo

Que «Editar texto…» sirva para corregir un libro de principio a fin sin volver a la ventana principal:
ver las faltas y los avisos donde están, ver lo que uno cambió, escribir la tipografía española sin
buscar símbolos, marcar capítulos revisados, buscar con precisión, partir capítulos y medir el texto.

## K0. Hoja centrada

El escritorio gris deja 16 px alrededor de la hoja también abajo (hoy 0: la hoja tocaba el borde).

## K1. Símbolos y tipografía

- Botón «Ω Símbolos ▾» en el grupo Insertar:
  - «— Raya» (Ctrl+Alt+-), «… Puntos suspensivos» (Ctrl+Alt+.).
  - «« » Comillas» (Ctrl+Alt+Q), «¿ ? Pregunta», «¡ ! Exclamación»: con texto elegido lo envuelven; sin
    él insertan los dos signos con el cursor en medio.
  - «Corregir tipografía del capítulo»: `convert._typography` en cada párrafo de la hoja (títulos sin
    rayas), un solo paso de deshacer; la barra de estado dice cuántos párrafos cambiaron.
- Conserva cursiva, negrita, notas y enlaces (la línea pasa por `_inline` y vuelve).

## K2. Ver mis cambios

- Un párrafo está cambiado si su volcado no es su línea original (o es nuevo). Franja de color de lado a
  lado (`extraSelections` con ancho completo): solo se pinta, no se guarda ni entra en deshacer.
- Botón «Cambios ▾»: «N párrafos cambiados» (informativo), «Siguiente cambio» (F7), «Volver al original».
- «Volver al original» (también en el clic derecho): el párrafo vuelve a su línea de antes, un paso de
  deshacer. No se ofrece si el párrafo se partió (su origen está en otro párrafo) o se le unió el
  siguiente (el origen siguiente ya no está): ahí hay que usar Deshacer.

## K3. Avisos de calidad en la hoja

- Los avisos del informe (sin los ignorados) de los párrafos del capítulo, tomados del libro al abrir:
  franja con el color de su gravedad (`theme.level_colors`) en los párrafos sin cambiar.
- F8 (y «Aviso siguiente» en el botón «Avisos ▾» con su recuento): el siguiente aviso, también en los
  capítulos siguientes. Abre una barra con título, problema, solución y «Arreglar» si el aviso trae arreglo.
- «Arreglar» cambia los párrafos del aviso en la hoja (su `edit` de `edit_section`): un paso de deshacer.
  Al cambiar un párrafo, su marca desaparece.

## K4. Ortografía en vivo

- Un proceso de ortografía (`_spell_server`) vive mientras el editor está abierto: carga Hunspell una vez
  (~4 s), responde qué palabras no están y sus sugerencias. Nunca en la ventana ni en un hilo.
- Al abrir revisa todas las palabras del libro; luego, tras 1 s sin escribir, las palabras nuevas de la
  hoja. Mismas reglas que «Ortografía…» (`convert.spell_candidate`): minúscula, 3 letras o más, sin el
  diccionario de la serie, honoríficos ni onomatopeyas.
- Subrayado rojo ondulado (el de hoy); al llegar palabras nuevas solo se repintan sus párrafos (repintar
  la hoja entera tardaba 1 s).
- Sugerencias como en «Ortografía…»: la palabra con o sin tilde y las correctas parecidas del propio libro,
  al instante. Las de Hunspell tardaban de 1 a más de 15 s por palabra y el clic derecho se quedaba en
  «Buscando sugerencias…». Se piden las del capítulo abierto; la del clic derecho, la primera, y si llega
  con el menú abierto aparece en él.
- Clic derecho sobre una palabra subrayada: sus sugerencias (o «Buscando sugerencias…»), «Ignorar» (hasta
  cerrar el editor) y «Añadir al diccionario de la serie» (`App.words`, el de «Ortografía…»).
- Si el proceso no arranca o muere, el editor sigue sin subrayado nuevo (sin aviso que bloquee). Si
  muere la ventana, el proceso lo nota y termina (si no, quedaba esperando para siempre).

## L1. Revisado y siguiente

- Ctrl+Intro o botón «✓ Revisado» (grupo Capítulo): marca el capítulo como revisado (la ✓ de la lista,
  como la tecla R) y abre el siguiente sin revisar. En uno ya revisado, lo desmarca y se queda.
- Se aplica al momento, también si luego se cancela el editor: es la marca de la lista, no texto.
- La cinta dice «✓ 12/25» y la lista de capítulos pone «✓» delante de los revisados.

## L2. Búsqueda avanzada

- En la barra de buscar: casillas «Palabra completa» y «Mayúsculas» (distinguir), que valen para
  buscar, reemplazar y reemplazar todo.
- Botón «Todos»: lista con cada resultado del libro («Capítulo 3 · …texto alrededor…») bajo la barra;
  clic va a ese resultado. Busca en lo editado de los capítulos visitados y en el libro de los demás.

## L3. Dividir capítulo aquí

- Botón «✂ Dividir capítulo» (grupo Párrafo): barra «Título del capítulo nuevo» + «Dividir». Parte el
  párrafo en el cursor (si está en medio) e inserta antes del resto un título de tipo Capítulo. Un paso de
  deshacer. Al aceptar, la lista gana esa fila de capítulo (como un título escrito en la hoja).
- No en un título ni en una ilustración.

## L4. Estadísticas

- Botón «Estadísticas» (grupo Revisar, junto a Cambios y Avisos): ventana pequeña con, del capítulo,
  palabras, párrafos, minutos de lectura (230 por minuto, como `reading_minutes`) y el porcentaje de
  palabras en diálogo (párrafos que empiezan por raya, guion o comillas) frente a narración; de la
  selección, sus palabras; del libro, palabras, capítulos y minutos.

## Cinta adaptable

Con las herramientas nuevas la cinta pedía ~1800 px. Si no cabe en la pantalla (1366 px, o 1093 con el zoom
de Windows al 125 %), por niveles: deshacer, insertar y buscar se quedan en su icono y el botón de capítulo
se estrecha; luego párrafo y revisar; al final los grupos se juntan y «✓ 3/25» pasa a «✓». El texto de
cada botón sigue en su ayuda. Se mide contra la pantalla antes de mostrar el editor (maximizada con una
cinta ancha, la ventana quedaba más ancha que la pantalla).

## Arreglos que salieron al probar

- Esc en el campo de buscar, reemplazar, enlace o dividir cerraba el editor («¿Descartar cambios?») cuando
  la ventana no estaba activa (tras la lista de «Todos»): un filtro de eventos cierra la barra.
- El clic derecho de la hoja sale en español (el estándar de Qt salía en inglés).
- La lista de capítulos («•» en los cambiados) se rehace al abrirla, no en cada cambio de capítulo ni tras
  cada pausa al escribir (volcaba todos los capítulos visitados).

## Pruebas

Un test por función en `test_convert.py` (ventana sin pantalla; el proceso de ortografía se prueba
llamando a `_spell_server` con colas, y la ventana con uno falso). Antes del build, `/verificar-ventana`
en la pantalla real con clics.
