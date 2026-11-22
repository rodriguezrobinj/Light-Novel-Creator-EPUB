# Mejorar ilustraciones con Real-ESRGAN

Fecha: 2026-11-22. Rama: `feat/mejorar-ilustraciones`.

## Objetivo

Las ilustraciones sacadas de PDF suelen ser JPEG pequeños y comprimidos: Re Zero 1 las tiene de 882×1237. Con esto
se agrandan con IA hasta el tamaño del perfil, sin artefactos de JPEG. Se hace a mano desde «Ilustraciones…»: el
usuario ve el antes y el después y puede deshacerlo con Ctrl+Z.

## Datos medidos (spike 2026-11-22, Intel HD Graphics 620, Vulkan)

- `realesrgan-ncnn-vulkan` v0.2.5.0, BSD-3:
  - URL: `https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesrgan-ncnn-vulkan-20220424-windows.zip`
  - Tamaño: 45 474 481 bytes.
  - SHA256: `abc02804e17982a3be33675e4d471e91ea374e65b70167abc09e31acb412802d`.
  - Los archivos van en la raíz del zip.
- Ilustración de 882×1237 de Re Zero 1:
  - `realesrgan-x4plus-anime` x4: 120-160 s y deforma el texto que se transparenta del dorso.
  - `realesr-animevideov3` x2: 12-16 s, limpio y nítido.
- Se elige `realesr-animevideov3`. Del zip solo se descomprimen el `.exe`, `vcomp140.dll` y los modelos
  `realesr-animevideov3-x2/x3/x4`: unos 10 MB en vez de 140.

## Diseño

### `upscale.py` (motor, sin UI)

- `download(base, progress)`: descarga el zip, comprueba el SHA256 y descomprime solo `KEEP` en
  `realesrgan-20220424/`. Luego reemplaza la carpeta entera de una vez. Se ejecuta en el proceso de trabajo.
- `scale_for(width, height, target)`: 0 si el lado mayor ya llega a `target`; si no, 2, 3 o 4, el menor que llega.
- `upscale(base, data, target)`:
  - Escribe un PNG temporal y ejecuta el `.exe` con `-n realesr-animevideov3 -s N`, sin consola y con un límite de
    600 s.
  - Reduce el resultado hasta `target` de lado mayor. Si la original era gris, el resultado también lo es.
  - Devuelve un JPEG de calidad 92, o PNG si tiene transparencia.
  - Sin Vulkan o con el `.exe` fallido, lanza `UpscaleError` con un mensaje en español.
- `upscale_many(base, items, target, progress)`: `[(bloque, imagen)]` → `[(bloque, imagen nueva)]`. Salta las que
  ya tienen buen tamaño.

### Ventana

- `target`: el «Tamaño de las imágenes» del libro si «Reducir» está puesto; si no, 2000.
- En «Ilustraciones…», debajo de la vista previa, dos botones:
  - «Mejorar esta»: si la ilustración ya llega al tamaño, lo dice en la nota y no hace nada.
  - «Mejorar las pequeñas…»: todas las que no llegan.
- Si falta Real-ESRGAN, pregunta antes de descargarlo (unos 45 MB).
- El trabajo corre en el proceso de trabajo con progreso. Luego `confirm_image` muestra el antes y el después de cada
  una («Saltar esta» si hay varias), y `replace_images` aplica lo aceptado en un solo Ctrl+Z.
- `_store_bytes(data, suffix)`: guarda en `ilustraciones/` por hash, como `_store_image`. Las revisiones apuntan ahí
  y el cambio sobrevive al reabrir.

## Pruebas

- Con un ejecutable falso: un `.cmd` que copia la entrada a la salida, con `python -c` y una imagen el doble de
  grande.
  - Escala y reducción a `target`.
  - Gris sigue gris.
  - Una que ya tiene buen tamaño se salta.
  - Un fallo con «vkCreateInstance» da el mensaje de Vulkan.
  - Descarga: SHA256 distinto rechazado; solo se descomprime `KEEP`.
- Ventana: «Mejorar las pequeñas…» con `upscale.upscale_many` falso → antes y después → la ilustración cambia, Ctrl+Z
  la devuelve y el cambio sigue al reabrir.
- Real: `/verificar-ventana` en tu GPU y `/build-exe`.

## Fuera de alcance

Mejorar automáticamente al convertir, elegir el modelo, usar la CPU sin Vulkan y mejorar la portada aparte (se
mejora si es una ilustración del libro).
