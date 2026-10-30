# Carpeta vigilada y datos de serie entre carpetas — Diseño

Fecha: 2026-10-30

Entrega C de 4 (A. salida para lectores y B. notas del traductor, hechas; D. datos desde internet).

## Datos de la serie en cualquier carpeta

Hoy un tomo nuevo hereda los datos de la serie (`INHERITED`: autor, grupo, traductor, reemplazos,
estilo, opciones…) solo de una revisión `.lnepub.json` en su misma carpeta.

- Al guardar una revisión, `_remember_series` guarda también esos datos con el nombre de la serie (en
  minúsculas) en `ajustes.series.json`, junto a las preferencias (`SETTINGS`).
- `_inherited` busca primero en la misma carpeta, como antes; si no hay nada, usa ese almacén.
- Un error al escribirlo no se avisa: solo se pierde la herencia entre carpetas.

## Carpeta vigilada

- Herramientas → «Vigilar una carpeta…» la activa; con una activa, pregunta si dejar de vigilarla. Se
  recuerda en las preferencias (`"watch"`) y sigue al volver a abrir la app.
- Cada `WATCH_MS` (10 s), con la ventana libre, `_ready` da los PDF sin `.epub` ni `.kepub.epub` cuyo
  tamaño no cambió desde la vuelta anterior (no se toma uno a medio descargar), y se convierten con
  `_batch` (revisión, datos de la serie u opciones de la ventana) en el proceso de trabajo.
- Lo que ya había al activarla se salta (para eso está «Convertir carpeta…»); al reabrir la app, lo
  llegado con la ventana cerrada sí se convierte. Un PDF que falla no se reintenta en la sesión.
- El resultado va a la barra de estado, sin ventanas emergentes: la conversión pasa sola.
- Fuera de alcance: vigilar con la app cerrada (servicio o bandeja del sistema) y subcarpetas.

## Pruebas

`test_series_data_is_inherited_from_another_folder` y `test_watched_folder_converts_new_pdfs_once`
(lo previo se salta, primera vuelta solo mide, el roto no se reintenta, estado sin diálogos).
