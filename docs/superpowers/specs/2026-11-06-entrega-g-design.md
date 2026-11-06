# Entrega G: página de personajes desde AniList — Diseño

Fecha: 2026-11-06

## Objetivo

Una página «Personajes» antes del primer capítulo con los personajes de la serie tomados de AniList, con los
nombres escritos como en la traducción del grupo y sin destripar la historia.

## Decisiones

- «Herramientas → Página de personajes…». La consulta de «Buscar en internet» (`ANILIST_QUERY`) pide
  también los personajes (`characters(sort: [ROLE, RELEVANCE], perPage: 25)`): una sola petición, en el
  proceso de trabajo. `_anilist_results` añade `characters: [(nombre, papel)]`, papel `MAIN`/`SUPPORTING`/
  `BACKGROUND`.
- Se elige la serie en la misma lista de resultados de AniList (`pick_online`), que ahora recibe qué hacer
  con el resultado: rellenar datos o abrir los personajes.
- Diálogo «Personajes»: una fila por personaje con casilla, nombre editable, nota editable (vacía) y
  «Principal»/«Secundario»/«De fondo». Solo los principales vienen marcados: los secundarios pueden ser
  spoilers. Cada fila dice cuántas veces sale el nombre en el libro (`count_matches`) y se recalcula al
  escribir: con 0 el nombre no coincide con la traducción.
- Ni descripciones (en inglés, con spoilers) ni imágenes de AniList (arte del anime, con derechos).
- «Añadir» crea el capítulo «Personajes» antes del primer capítulo con lo mismo que «Añadir página…»
  (`apply_edits`): se edita con E, se quita con Ctrl+Z y queda en la revisión. Cada personaje es un párrafo
  `<b>Nombre</b>` o `<b>Nombre</b>: nota` (con raya, la limpieza tipográfica la pegaría a la nota).
- Sin personajes marcados, «Añadir» avisa. Si AniList no tiene personajes de esa serie, se dice en un aviso.

## Pruebas

- `test_anilist_results_and_fill_only_empty_fields` comprueba también `characters`.
- `test_window_character_page`: red simulada (`_lookup`), solo el principal marcado, el conteo cambia al
  editar el nombre, la página queda antes del primer capítulo y Ctrl+Z la quita.
