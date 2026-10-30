# Datos del libro desde internet — Diseño

Fecha: 2026-10-30

Entrega D de 4 (A, B y C hechas).

## Objetivo

Rellenar autor, ilustrador, título original, géneros y sinopsis sin escribirlos a mano.

## Decisiones

- **AniList** (`https://graphql.anilist.co`): API GraphQL pública, sin clave, con las novelas ligeras
  (`type: MANGA, format: NOVEL`). MangaUpdates exige más pasos y no da nada que AniList no tenga.
- Botón «Buscar en internet (AniList)…» dentro de «Más datos…»: en la fila principal no cabe a 1366 px.
- Busca por Serie (o Título si no hay serie) en el proceso de trabajo (`_lookup`), con 15 s de límite.
  Sin red o con error: «No se pudo consultar AniList: …» por el aviso de error habitual.
- Lista de hasta 6 coincidencias (romaji, año e inglés): el usuario elige; la primera no siempre es la
  buena (Nageki: sale antes un spin-off).
- **Solo rellena campos vacíos** y antes muestra qué va a poner y pide confirmación: lo leído del PDF o
  escrito a mano no se pisa.
- Géneros traducidos al español (`GENRES`); los desconocidos quedan tal cual.
- La sinopsis de AniList está en inglés: se avisa al confirmar. Se le quitan etiquetas y «(Source: …)».
- Rol del autor: «Story» u «Original…»; ilustrador: «Art» o «Illustration» sin «Story».

## Pruebas

`test_anilist_results_and_fill_only_empty_fields`: respuesta de ejemplo, géneros, sinopsis limpia,
respuesta con errores, sin red, y que el autor ya escrito no se pisa. La consulta real se comprobó a
mano con Maryoku, Re Zero, Kage y Nageki.
