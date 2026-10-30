# Notas del traductor emergentes — Diseño

Fecha: 2026-10-30

Entrega B de 4 (A. salida para lectores, hecha; C. carpeta vigilada y reemplazos de serie; D. datos
desde internet).

## Problema

Re Zero 02-08 (~38 notas) trae notas al pie: llamada en número pequeño y elevado tras una palabra, y
al pie de la página una línea en letra menor que empieza por ese número. Hoy el análisis rompe las dos
cosas: la llamada elevada queda en otra línea base y sale al principio del párrafo («1 Sintió…») o como
párrafo suelto («2»), y el texto de la nota entra como párrafo del cuerpo en mitad del capítulo.

### Criterios de éxito

- La llamada queda en su sitio y la nota sale del cuerpo del capítulo.
- En el EPUB 3 la llamada es un `noteref` y la nota un `aside` `footnote`: Kobo, Kindle y Apple Books
  la muestran emergente; los demás lectores, al final de la sección, con enlace de vuelta.
- El editor de texto muestra la nota como `[Nota: …]` y la conserva al editar.
- Sin notas en el PDF, nada cambia (las otras series de muestra).

## Decisiones

- **Llamada**: un span de cifras (o `*`, `†`) con una línea vecina de letra al menos 1,25 veces mayor,
  pegada en horizontal y con la línea base hasta 0,6 de su tamaño más abajo, toma la línea base de esa
  vecina. Así se une a su línea aunque el PDF la tenga en otra. En la fila queda como marca provisional
  `<sup class="ref">N</sup>` y fuera del texto plano.
- **Nota**: fila en la mitad inferior de la página, de letra menor que el cuerpo, que empieza por una
  marca. Las filas siguientes de la misma letra menor la continúan. Salen de la página antes de armar
  párrafos.
- **Resolución, al acabar cada página**: la marca N se cambia por `<span class="nota">texto</span>` dentro
  del párrafo; sin nota N en la página, vuelve a ser el número tal cual (p. ej. «m²»). Una nota sin
  llamada se pega al último párrafo de la página para no perder texto.
- **Nota dentro del párrafo** (no un bloque aparte): sobrevive a editar, mover y quitar secciones, y le
  llegan los reemplazos y la tipografía, que ya saltan etiquetas.
- **Escritura**: numeración seguida en todo el libro; `<a class="nref" epub:type="noteref" href="#notaN"
  id="refN">N</a>` y al final de la sección `<aside epub:type="footnote" class="nota" id="notaN">` con
  enlace de vuelta. EPUB 2: `<div class="nota">` y sin `epub:type`.
- **Editor**: `_line` muestra `[Nota: …]`; `_inline` lo vuelve a convertir tras validar `<i>`/`<b>`.

## Fuera de alcance

- Notas escritas en el texto como «(N.T.: …)»: ya se leen bien en su sitio.
- Notas que siguen en la página siguiente.

## Pruebas

PDF sintético con llamada elevada y nota al pie en letra menor: posición de la llamada, nota fuera del
cuerpo, `noteref`/`aside` en el EPUB, `check_epub` vacío, EPUB 2, ida y vuelta en el editor, número sin
nota que vuelve a ser texto. Real: Re Zero 04 (18 notas).
