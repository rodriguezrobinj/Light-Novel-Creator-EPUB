# Editor completo — diseño

## Para qué

«Editar texto…» ya tiene formato, búsqueda, ortografía, avisos, comentarios, voz y la página del PDF al
lado. Le faltan cosas que un corrector usa a diario o que evitan perder trabajo: recuperar tras un cierre,
pegar desde Word con cursivas, moverse por el capítulo sin F7/F8 uno a uno, ver qué cambió, más formato,
sinónimos y medir el trabajo.

Fuera de alcance (ya existen): glosario en el editor (aviso «Nombre distinto al glosario» con «Arreglar» y
F8) y rayas de diálogo («Corregir tipografía»; la raya de cierre tras un inciso se descartó en M por falsos
avisos).

Éxito:
- Si la app se cierra con cambios sin guardar, al volver a abrir el editor se recuperan.
- Pegar un párrafo de Word con cursiva lo deja en cursiva.
- Un clic en el panel lleva a cualquier título, aviso, comentario o cambio del capítulo.
- El EPUB lleva superíndice, versalitas y párrafos a la derecha.

## Funciones

### 1. Recuperar tras un cierre inesperado

- `TextEditor.write_draft`, diferido 5 s tras cada `textChanged` (`later("draft", 5000, …)`): si `dirty()`,
  escribe `<pdf>.lnepub.borrador.json` (`_draft_path`) con `_write_json` en un temporal y `os.replace`.
  Sin cambios, borra el borrador.
- Contenido: `{"saved": iso fecha, "pages": [{"start", "end", "lines": [líneas cargadas], "text": dump}]}`.
- Al abrir el editor (`__init__`, tras `open_section`), si hay borrador: «Hay cambios sin guardar del
  <fecha hora>. ¿Recuperarlos?». Sí: para cada página cuya `lines` coincide con las que da hoy el libro
  (`section_lines`), se carga con `Sheet.set_draft(lines, text)`. Las que no coinciden se cuentan en el
  aviso «N capítulos no se recuperaron: el libro cambió desde entonces». No: se borra el borrador.
- `Sheet.set_draft`: carga las líneas originales (con su origen y `ORIGINAL`) y reemplaza el texto según
  `difflib` línea a línea: iguales se quedan; las cambiadas toman el origen de la línea vieja que
  reemplazan (o de la anterior) sin `ORIGINAL` igual, así salen como cambios (F7) y `changes()` las aplica.
  El deshacer empieza vacío después.
- `save()` y `cancel()` (al cerrar) borran el borrador. Un fallo al escribir (`OSError`) se dice en
  la barra de estado y no interrumpe.

### 2. Pegar con formato

- `Sheet.insertFromMimeData`: si no es MIME propio pero `hasHtml()`, carga el HTML en un `QTextDocument`
  y sus líneas salen de `dump(doc)`. Cursiva, negrita, superíndice y versalitas se conservan; títulos,
  colores y tamaños no. Una línea que es `TOKEN` (`[Ilustración 3]`) se escapa para entrar como texto.
- Sin HTML: como hoy, texto plano.

### 3. Panel de navegación (F9)

- `QTreeWidget` a la izquierda de la hoja, dentro del `QSplitter` (primer widget), oculto por defecto,
  ancho 240. Botón «☰ Navegación» en el grupo «Buscar» de la cinta y F9 (`command "nav"`).
- Cuatro grupos con su cuenta: «Títulos», «Avisos», «Comentarios», «Cambios». Cada hoja: el texto
  recortado a 60 caracteres. Clic → `goto_block`.
- Se rellena en `refresh_marks` (ya diferido 250 ms) y en `open_section`, solo si está visible.
- La preferencia de visible se guarda en ajustes (`editor_nav`).

### 4. Seguir donde lo dejé

- Al cerrar el editor (`on_destroy`) se guarda en `App.editor_at` el origen del párrafo del cursor y entra
  en la revisión (`review_state`/`apply_state`, clave `editor_at`).
- Al abrir, si `editor_at` está en otra sección que la abierta: barra «Lo dejaste en «<título>» · [Ir allí]
  ✕». Ir → `open_section` de esa sección con el foco en ese bloque.

### 5. Ver los cambios

- «Cambios ▾» → «Mostrar cambios» (casilla). Con ella: `Look` subraya con el color `changed` las palabras
  puestas (`app._word_diff` contra `ORIGINAL`) en cada párrafo cambiado; una barra (`diff_bar`) muestra el
  original del párrafo del cursor con lo quitado tachado y lo puesto subrayado (texto enriquecido escapado).
- Se calcula solo para bloques cambiados; sin cambios en el párrafo, la barra se oculta.

### 6. Más formato

| Formato | Línea del editor | XHTML | Hoja (Qt) | Cinta y atajo |
|---|---|---|---|---|
| Superíndice | `<sup>…</sup>` | `<sup>…</sup>` | `AlignSuperScript` | «x²», Ctrl+Mayús+= |
| Versalitas | `<sc>…</sc>` | `<span class="sc">…</span>` | `SmallCaps` | «Vᴇʀs» |
| A la derecha | prefijo `<derecha>` | `<p class="right">` | `AlignRight`, propiedad `RIGHT` | «⇥ Derecha», Ctrl+D |

- `convert.py`: `INLINE` acepta `sup` y `sc`; `_inline` cambia `<sc>` por el `span`; `_balanced`/`TAGS` las
  cuentan; `_plain` las quita; `section_text` escribe `<sc>` desde el span y `<derecha>` desde
  `align="right"`; `edit_section` lee `<derecha>`; `write_epub` da la clase `right` y el CSS
  `.sc{font-variant:small-caps}` y `p.right{text-align:right;text-indent:0}` en todos los estilos.
- Centrar, recuadro y derecha se excluyen entre sí.

### 7. Modo concentración (F11)

- Oculta cinta, barras, panel de navegación, panel del PDF y la fila de estado salvo `status`;
  `showFullScreen()`. F11 o Esc vuelven (Esc sale del modo antes que preguntar si descartar).

### 8. Cambiar ilustración desde el editor

- Menú contextual sobre una ilustración: «Cambiar ilustración…» → `App.replace_image(k, parent=win)`;
  si cambió, la hoja redibuja sus imágenes (`pictures` de nuevo y `Sheet.refresh_pictures`).
- Tooltip del objeto: «Para moverla, córtala (Ctrl+X) y pégala donde vaya».

### 9. Sinónimos

- `editor.synonyms(word)`: lee `dict/th_es_v2.dat` (ISO-8859-1, formato MyThes: `palabra|n` y n líneas
  `-|sin1|sin2`) una vez, en un diccionario por palabra en minúsculas. Si no está, prueba singular
  (`-es`, `-s`) y masculino (`-a`→`-o`). Hasta 12 distintos, sin la propia palabra.
- Límite: formas verbales conjugadas no tienen sinónimos (el tesauro solo trae lemas).
- Menú contextual sobre una palabra sin subrayado de ortografía: «Sinónimos ▸». Elegir uno reemplaza la
  palabra con su mayúscula inicial o todo mayúsculas si el original la tenía.
- Archivo: OpenThesaurus-es de LibreOffice, LGPL 2.1 (ya citada en `dict/LICENSE.md`).

### 10. Medir la sesión

- Revisión, clave `work`: `{"AAAA-MM-DD": {"minutes", "paragraphs", "reviewed"}}`.
- Al guardar con «Aceptar»: párrafos cambiados de esta sesión (`len(changes)` en bloques) y minutos desde
  que se abrió; al marcar revisado (`toggle_reviewed`), `reviewed += 1`. Cancelar suma solo minutos.
- «Σ Cifras» añade «Tu trabajo»: hoy, últimos 7 días y total (minutos, párrafos, capítulos revisados).

## Pruebas

En `test_convert.py`, offscreen:
- Formato nuevo: `edit_section` + `write_epub` con `<sup>`, `<sc>`, `<derecha>`; vuelta por `section_text`;
  `Sheet` load/dump de ida y vuelta; `_plain` sin etiquetas.
- Borrador: escribir, reabrir y recuperar; con el libro cambiado no se recupera ese capítulo; Aceptar lo borra.
- Pegar HTML con `<i>` y `<b>`; HTML con `[Ilustración 1]` entra como texto.
- `synonyms`: palabra, plural, femenino, inexistente; reemplazo con mayúscula.
- Panel: grupos y cuentas; clic lleva al bloque.
- `editor_at` en la revisión; `work` suma minutos y párrafos.

Después: `/verificar-ventana` en pantalla real con clics, `/regresion-series`, `/build-exe`.
