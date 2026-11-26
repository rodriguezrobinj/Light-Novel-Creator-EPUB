"""Hoja web del editor: el capítulo dibujado por Chromium con el CSS del libro, y editable (contenteditable). Se carga
desde las líneas del editor (convert.section_text, como editor.dump) y vuelve a ellas: el modelo del libro no cambia.

El navegador ensucia el HTML al editar (por eso Sigil quitó su vista visual): Intro reparte las etiquetas del párrafo
sin repetir su id, pegar solo deja cursiva, negrita y enlaces, y clean() quita lo que Chromium mete (<font>, <strike>,
style vacíos, &nbsp; sueltos, bloques dentro de un párrafo) antes de volver a líneas (editor.from_code)."""
import html
import json
import posixpath
import re
import zipfile

import convert
import editor

SHEET_CSS = """
#lnc-sheet { outline: none; min-height: 90vh; position: relative; z-index: 1; }
#lnc-sheet .lnc-obj { user-select: none; cursor: default; opacity: 0.95; }
#lnc-sheet .lnc-obj img { max-width: 100%; max-height: 60vh; -webkit-user-drag: none; }
#lnc-sheet p.lnc-break { text-align: center; text-indent: 0; color: #888888; }
#lnc-sheet p.marco { text-indent: 0; margin: 0; padding: 0.3em 0.9em; border-left: 3px double #777777;
                     border-right: 3px double #777777; font-family: sans-serif; }
#lnc-sheet p.marco.juego { background: #000000; color: #ffffff; border-color: #00219b; }
#lnc-sheet p.marco.carta { background: #fbf6e9; color: #3a3226; border: 0; border-left: 1px solid #b9a77f;
                           border-right: 1px solid #b9a77f; font-family: serif; font-style: italic; }
#lnc-sheet p.marco.nota { background: #f2f2f2; color: #333333; border: 0; border-left: 4px solid #8a8a8a;
                          font-size: 0.85em; }
#lnc-sheet span.nota { font-size: 0.8em; color: #6b5a1e; background: #fff3cd; border-radius: 3px; padding: 0 0.3em; }
#lnc-sheet span.nota::before { content: "Nota: "; font-weight: bold; }
#lnc-sheet p.tabla { text-indent: 0; margin: 0; padding: 0.15em 0.5em; font-family: monospace;
                     background: #f3f3f3; border-left: 1px solid #bbbbbb; border-right: 1px solid #bbbbbb; }
#lnc-sheet p.tabla.cabecera { font-weight: bold; background: #e6e6e6; border-top: 1px solid #bbbbbb; }
#lnc-sheet p.marco.cita { border: 0; font-family: serif; font-style: italic; font-size: 1.1em; text-align: center; }
html { background: #ffffff; } body { margin: 24px 36px !important; }
"""

# «Mostrar cambios» en Chromium (hoja web y pestaña EPUB), como editor.mark_changes: lo puesto con el resaltado
# lnc-change y lo quitado en un <span class="lnc-del"> vacío, que lo pinta tachado con ::after (el texto del
# párrafo no cambia). key: cómo se comparan las palabras (en el EPUB, sin la tipografía: comillas y rayas)
DIFF_JS = r"""
window.lncDiff = function (a, b, key) {  // [['add', posición en b, largo] | ['del', posición en b, texto]]
  key = key || function (t) { return t; };
  // las piezas de editor.WORDS: etiquetas, cada letra de chino o japonés, palabras y espacios
  var re = /<[^<>]*>|[⺀-鿿豈-﫿＀-￯]|[^\s<⺀-鿿豈-﫿＀-￯]+|\s+|</g;
  var x = a.match(re) || [], y = b.match(re) || [], i, j, pos = 0, out = [], last = null;
  // lo igual del principio y del final, fuera: una edición toca poco y la tabla de abajo crece con el cuadrado
  var head = 0;
  while (head < x.length && head < y.length && key(x[head]) === key(y[head])) { pos += y[head].length; head++; }
  var tail = 0;
  while (tail < x.length - head && tail < y.length - head &&
         key(x[x.length - 1 - tail]) === key(y[y.length - 1 - tail])) tail++;
  x = x.slice(head, x.length - tail); y = y.slice(head, y.length - tail);
  var n = x.length, m = y.length, L = [];
  for (i = 0; i <= n; i++) L.push(new Int32Array(m + 1));
  for (i = n - 1; i >= 0; i--) for (j = m - 1; j >= 0; j--)
    L[i][j] = key(x[i]) === key(y[j]) ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  for (i = 0, j = 0; i < n || j < m;) {
    if (i < n && j < m && key(x[i]) === key(y[j])) { pos += y[j].length; i++; j++; last = null; }
    else if (i < n && (j >= m || L[i + 1][j] >= L[i][j + 1])) {  // lo quitado antes que lo puesto en su sitio
      if (x[i].trim()) {
        if (last && last[0] === 'del' && last[1] === pos) last[2] += x[i]; else out.push(last = ['del', pos, x[i]]);
      } else if (last && last[0] === 'del' && last[1] === pos) last[2] += x[i];
      i++;
    } else {
      if (last && last[0] === 'add') last[2] += y[j].length; else out.push(last = ['add', pos, y[j].length]);
      pos += y[j].length; j++;
    }
  }
  out.forEach(function (op) { if (op[0] === 'del') op[2] = op[2].trim(); });
  return out;
};
// Pinta los cambios de un grupo de párrafos (els) que vienen de un mismo texto before (null: todos nuevos): uno
// partido con Intro se compara entero, como editor.group_changes. Lo quitado, marcado en su sitio; devuelve los
// Range de lo puesto
window.lncPaintGroup = function (els, before, key) {
  function point(b, pos) {  // (nodo de texto, posición) del carácter pos de b
    var w = document.createTreeWalker(b, NodeFilter.SHOW_TEXT), n, from = 0, last = null;
    while ((n = w.nextNode())) { if (pos <= from + n.data.length) return [n, pos - from]; from += n.data.length; last = n; }
    return last ? [last, last.data.length] : null;
  }
  var added = [], texts = els.map(function (e) { return e.textContent; }), starts = [], at = 0;
  if (before === null || before === undefined) {
    els.forEach(function (e) { var r = document.createRange(); r.selectNodeContents(e); added.push(r); });
    return added;
  }
  texts.forEach(function (t) { starts.push(at); at += t.length + 1; });
  var joined = texts.join('\n');
  if (joined === before) return added;
  var ops = lncDiff(before, joined, key);
  ops.forEach(function (op) {
    if (op[0] !== 'add') return;
    els.forEach(function (e, k) {
      var a = Math.max(op[1], starts[k]), z = Math.min(op[1] + op[2], starts[k] + texts[k].length);
      if (z <= a || !joined.slice(a, z).trim()) return;
      var p = point(e, a - starts[k]), q = point(e, z - starts[k]);
      if (p && q) { var r = document.createRange(); r.setStart(p[0], p[1]); r.setEnd(q[0], q[1]); added.push(r); }
    });
  });
  ops.filter(function (op) { return op[0] === 'del' && op[2]; }).reverse().forEach(function (op) {  // de atrás adelante
    var k = els.length - 1;
    while (k > 0 && op[1] < starts[k]) k--;
    var b = els[k], p = point(b, Math.min(op[1] - starts[k], texts[k].length)), mark = document.createElement('span');
    mark.className = 'lnc-del'; mark.setAttribute('contenteditable', 'false'); mark.setAttribute('data-del', op[2]);
    if (!p) { b.appendChild(mark); return; }
    // sin vaciar el nodo de texto que recuerda el deshacer de Chromium: en un borde no se parte, y al partir se
    // queda con la primera mitad (normalize la vuelve a juntar en él)
    var node = p[0], at = p[1];
    if (at > 0 && at < node.data.length) node.splitText(at);
    node.parentNode.insertBefore(mark, at === 0 ? node : node.nextSibling);
  });
  return added;
};
window.lncText = function (code) {  // texto del HTML de un párrafo, como lo enseña Chromium (null sigue null)
  if (code === null || code === undefined) return null;
  // en un documento aparte y como HTML: en la página (XHTML), un fallo de XML sacaba «parsererror» encima de todo
  return new DOMParser().parseFromString('<body>' + code + '</body>', 'text/html').body.textContent;
};
window.lncClearDiff = function (root) {  // quita las marcas de lo quitado y junta el texto que partieron
  var marks = root.querySelectorAll('.lnc-del'), parents = new Set();
  marks.forEach(function (m) { parents.add(m.parentNode); m.remove(); });
  parents.forEach(function (p) { p.normalize(); });
};
"""


DEL_CSS = (".lnc-del::after { content: attr(data-del); background: %s; text-decoration: line-through; "
           "margin: 0 0.2em; padding: 0 0.1em; font-style: normal; text-indent: 0; }"  # fondo rojo, como GitHub
           # párrafos quitados enteros: en su propia línea encima (o debajo) del que los seguía, como en la hoja
           "[data-lnc-above]::before, [data-lnc-below]::after { display: block; width: fit-content; "
           "background: %s; text-decoration: line-through; font-size: 0.85em; font-style: normal; font-weight: normal; "
           "text-indent: 0; text-align: left; padding: 0 0.2em; } "
           "[data-lnc-above]::before { content: attr(data-lnc-above); margin-bottom: 0.2em; } "
           "[data-lnc-below]::after { content: attr(data-lnc-below); margin-top: 0.2em; } "
           # la ilustración o el separador puestos: recuadrados de verde, como en la hoja
           ".lnc-obj[data-lnc-added] img, .lnc-break[data-lnc-added] { outline: 3px solid %s; outline-offset: 2px; }")


def gone_css(pieces, color, dark=False, image_of=None):
    """Valor de content: del CSS con lo quitado entero (editor.removed_pieces) si lleva ilustraciones o
    separadores: la fila entera pintada como en la hoja (editor.paint_pieces), en PNG dentro del CSS: con texto
    y una imagen juntos, el fondo rojo del CSS asomaba alrededor de la imagen. dark: el modo oscuro de la hoja
    web (webview.SHEET_DARK), con su letra clara. image_of(número): la imagen de cada una. "" sin ellas."""
    pieces = [p if isinstance(p, str) else (image, p[1]) if (image := image_of and image_of(p[0])) is not None
              else p[1] for p in pieces]
    if all(isinstance(p, str) for p in pieces):
        return ""
    ink = "#ececec" if dark else "#000000"
    from PySide6.QtCore import QBuffer, QIODevice, Qt
    from PySide6.QtGui import QColor, QFont, QImage, QPainter
    key = (tuple(p if isinstance(p, str) else p[0].cacheKey() for p in pieces), color, ink)
    if key not in _GONE:
        font = QFont("Segoe UI")
        font.setPixelSize(16)  # una imagen no sigue el zoom de la hoja web: letra fija
        width = 480  # cabe en la columna de la hoja web (una imagen en content no se encoge con ella)
        row = QImage(width, round(editor.row_height(pieces, font)), QImage.Format.Format_ARGB32_Premultiplied)
        row.fill(Qt.GlobalColor.transparent)
        painter = QPainter(row)
        boxes = editor.paint_pieces(painter, 0, row.height(), pieces, font, width, color, QColor(ink),
                                    image_of=lambda image: image)  # ya son imágenes
        painter.end()
        row = row.copy(0, 0, max(1, round(max(box.right() for box, _ in boxes))) if boxes else 1, row.height())
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        row.save(buffer, "PNG")
        if len(_GONE) > 200:
            _GONE.clear()
        _GONE[key] = 'url("data:image/png;base64,%s")' % bytes(buffer.data().toBase64()).decode()
    return _GONE[key]


_GONE = {}  # (piezas, color, tinta) -> su url() de gone_css: se pide en cada cambio con «Mostrar cambios»

# Funciones de la hoja: estado para la cinta, órdenes de formato, limpieza al pegar y atajos de Word en español
SHEET_JS = DIFF_JS + r"""
(function () {
  var sheet = document.getElementById('lnc-sheet');
  // Lo quitado («Mostrar cambios») son nodos en la hoja: fuera antes de cada edición, que el deshacer de Chromium
  // no los vea; el repintado tras escribir los vuelve a poner
  var exec = document.execCommand.bind(document);
  document.execCommand = function () { lncClearDiff(sheet); return exec.apply(null, arguments); };
  sheet.addEventListener('beforeinput', function () { lncClearDiff(sheet); }, true);
  document.execCommand('defaultParagraphSeparator', false, 'p');
  document.execCommand('styleWithCSS', false, false);
  function block(node) {  // el párrafo (hijo de la hoja) donde está node
    while (node && node.parentNode !== sheet) node = node.parentNode;
    return node && node.nodeType === 1 ? node : null;
  }
  // Ilustración o separador pulsado: el cursor no entra en ellos (contenteditable=false), pero son el párrafo de ahora
  var picked = null;
  function here() {
    if (picked) return picked;
    var s = window.getSelection(); return s.rangeCount ? block(s.getRangeAt(0).startContainer) : null;
  }
  sheet.addEventListener('mousedown', function (e) {
    var t = e.target.nodeType === 1 ? e.target : e.target.parentElement, o = t && t.closest('.lnc-obj');
    picked = o && o.parentNode === sheet ? o : null;
    if (picked) { lnc.hereAt = index(picked); lnc.placeHere(false); }
  }, true);
  // Retroceso o Supr junto a una ilustración o un separador (o con él elegido): Chromium lo quitaba y además unía los
  // párrafos de alrededor (o borraba una letra del de abajo y lo dejaba); la hoja de Qt quita solo el objeto (el
  // sondeo recoge wants: «objeto atrás|delante N»)
  function objectKey(e, was) {
    if (e.ctrlKey || e.altKey || e.metaKey || (e.keyCode !== 8 && e.keyCode !== 46)) return;
    var s = getSelection(), back = e.keyCode === 8, obj = null;
    if (was) obj = was;
    else if (s.rangeCount) {
      var r = s.getRangeAt(0), b = block(r.startContainer);
      if (!r.collapsed && r.startContainer === sheet && r.endContainer === sheet && r.endOffset - r.startOffset === 1)
        obj = sheet.childNodes[r.startOffset];  // el objeto solo, elegido entero
      else if (b && b.classList.contains('lnc-obj') && block(r.endContainer) === b) obj = b;
      else if (r.collapsed && b) {
        var at = offset(b, r.startContainer, r.startOffset), near = back ? b.previousElementSibling : b.nextElementSibling;
        if ((back ? at === 0 : at === b.textContent.length) && near && near.classList.contains('lnc-obj')) obj = near;
      }
    }
    if (!obj || obj.nodeType !== 1 || !obj.classList.contains('lnc-obj') || obj.parentNode !== sheet) return;
    e.preventDefault();
    lnc.wanted = 'objeto ' + (back ? 'atrás ' : 'delante ') + index(obj);
  }
  sheet.addEventListener('keydown', function (e) { var was = picked; picked = null; objectKey(e, was); }, true);
  function index(b) { return Array.prototype.indexOf.call(sheet.children, b); }
  function offset(b, node, at) {  // posición de (node, at) en el texto de b
    var r = document.createRange(); r.setStart(b, 0); r.setEnd(node, at); return r.toString().length;
  }
  function spot(b, pos, start) {  // (nodo de texto, posición) del carácter pos de b; start: en el límite, el siguiente
    var w = document.createTreeWalker(b, NodeFilter.SHOW_TEXT), n, last = null, from = 0;
    while ((n = w.nextNode())) {
      if (pos < from + n.data.length || (!start && pos === from + n.data.length)) return [n, pos - from];
      from += n.data.length; last = n;
    }
    return last ? [last, last.data.length] : [b, 0];
  }
  function caret(b, a, z, c) {  // elige de a en b a z en c (o en b; o pone el cursor) y lo trae a la vista
    picked = null;
    var p = spot(b, a, z !== undefined && (c || z !== a)), q = spot(c || b, z === undefined ? a : z), r = document.createRange();
    r.setStart(p[0], p[1]); r.setEnd(q[0], q[1]);
    sheet.focus(); var s = getSelection(); s.removeAllRanges(); s.addRange(r);
    (p[0].nodeType === 3 ? p[0].parentElement : b).scrollIntoView({block: 'center'});
  }
  // Una ilustración no se arrastra: ni a otro programa ni a otro sitio del capítulo (tampoco dentro de lo elegido)
  sheet.addEventListener('dragstart', function (e) {
    var t = e.target.nodeType === 1 ? e.target : e.target.parentElement, s = getSelection();
    var objs = sheet.querySelectorAll('.lnc-obj');
    for (var i = 0; i < objs.length; i++)
      if ((t && objs[i].contains(t)) || (s.rangeCount && s.getRangeAt(0).intersectsNode(objs[i]))) { e.preventDefault(); return; }
  });
  // Intro: Chromium copia el párrafo con su id; el nuevo no puede llevarlo
  var repaint = null;
  sheet.addEventListener('input', function () {
    lnc.dirty = true;  // palabras nuevas que revisar
    clearTimeout(repaint);
    repaint = setTimeout(function () { lnc.paint && lnc.paint(); }, 300);  // marcas al día, sin repintar en cada tecla
    var seen = {};
    Array.prototype.forEach.call(sheet.querySelectorAll('[id]'), function (el) {
      if (seen[el.id]) el.removeAttribute('id'); else seen[el.id] = true;
    });
    wrapLoose();
  });
  // Texto o letra suelta entre los párrafos (Chromium la deja al borrar o pegar): a su propio párrafo al momento, para
  // que cada hijo de la hoja sea un párrafo (los índices casan con la hoja de Qt) y nada quede fuera del volcado
  function wrapLoose() {
    var run = [], sel = getSelection(), node = sel.anchorNode, at = sel.anchorOffset;
    function blank(n) { return n.nodeType === 3 && !n.data.trim(); }  // el salto de línea entre párrafos
    function flush() {
      while (run.length && blank(run[0])) run.shift();
      while (run.length && blank(run[run.length - 1])) run.pop();
      if (run.length) {
        var p = document.createElement('p');
        sheet.insertBefore(p, run[0]);
        run.forEach(function (n) { p.appendChild(n); });
      }
      run = [];
    }
    Array.prototype.slice.call(sheet.childNodes).forEach(function (n) {
      if (n.nodeType === 3 || (n.nodeType === 1 && INLINE.test(n.tagName))) run.push(n); else flush();
    });
    flush();
    if (node && node.isConnected && (sel.anchorNode !== node || sel.anchorOffset !== at)) sel.collapse(node, at);
  }
  var INLINE = /^(a|abbr|b|br|cite|code|em|font|i|img|kbd|mark|q|rp|rt|ruby|s|samp|small|span|strike|strong|sub|sup|u|var)$/i;
  function escaped(text) { return text.replace(/[<&>]/g, function (m) { return {'<':'&lt;','&':'&amp;','>':'&gt;'}[m]; }); }
  sheet.addEventListener('paste', function (e) {
    e.preventDefault();
    var data = e.clipboardData, htmlText = data.getData('text/html'), paras = [[]], objects = 0;
    function cur() { return paras[paras.length - 1]; }
    function cut() { if (!Array.isArray(cur()) || cur().length) paras.push([]); }
    if (htmlText) {
      var box = document.createElement('div');
      box.innerHTML = htmlText.replace(/<!--[\s\S]*?-->/g, '');
      (function walk(n) {
        Array.prototype.forEach.call(n.childNodes, function (c) {
          if (c.nodeType === 3) { cur().push(escaped(c.data)); return; }
          if (c.nodeType !== 1) return;
          var tag = c.tagName.toLowerCase(), keep = {i: 'i', em: 'i', b: 'b', strong: 'b'}[tag];
          if (c.getAttribute('data-token')) { cut(); paras.push({token: c.getAttribute('data-token')}); objects++; return; }
          var bold = /bold|[6-9]00/.test(c.style ? c.style.fontWeight : ''), italic = c.style && c.style.fontStyle === 'italic';
          if (tag === 'a' && /^https?:/.test(c.getAttribute('href') || '')) { cur().push('<a href="' + c.getAttribute('href').replace(/"/g, '&quot;') + '">'); walk(c); cur().push('</a>'); return; }
          if (tag === 'br') { cur().push('<br/>'); return; }
          if (keep || bold || italic) { var t = keep || (bold ? 'b' : 'i'); cur().push('<' + t + '>'); walk(c); cur().push('</' + t + '>'); return; }
          var block = /^(p|div|h[1-6]|li|blockquote|figure)$/.test(tag);
          if (block) cut();
          walk(c);
          if (block) cut();
        });
      })(box);
    } else {
      data.getData('text/plain').split(/\r?\n/).forEach(function (line) { cut(); cur().push(escaped(line)); });
    }
    var items = paras.filter(function (p) { return !Array.isArray(p) || p.join('').replace(/<[^>]*>/g, '').trim(); });
    if (items.length > 1 || objects) {
      // varios párrafos o una ilustración: insertHTML los dejaba sueltos fuera de los párrafos (o los quitaba) y se
      // perdían al volcar; se pegan en la hoja de Qt, que los hace párrafos, y vuelven a la web (el sondeo: wants)
      lnc.pasted = JSON.stringify(items.map(function (p) { return Array.isArray(p) ? {html: p.join('')} : p; }));
      lnc.wanted = 'paste';
    } else if (items.length && htmlText) {
      document.execCommand('insertHTML', false, items[0].join(''));
    } else if (items.length) {
      document.execCommand('insertText', false, data.getData('text/plain').split(/\r?\n/).filter(function (l) {
        return l.trim(); })[0]);
    }
  });
  var keys = {78: 'bold', 66: 'bold', 75: 'italic', 73: 'italic', 83: 'underline'};  // N, B, K, I, S (Word en español)
  sheet.addEventListener('keydown', function (e) {
    if (e.ctrlKey && !e.altKey && !e.shiftKey && (e.keyCode === 90 || e.keyCode === 89)) {  // Ctrl+Z, Ctrl+Y
      var redo = e.keyCode === 89;
      if (!document.queryCommandEnabled(redo ? 'redo' : 'undo')) { e.preventDefault(); lnc.wanted = redo ? 'redo' : 'undo'; }
    }
    else if (e.ctrlKey && !e.altKey && !e.shiftKey && keys[e.keyCode]) { e.preventDefault(); document.execCommand(keys[e.keyCode]); }
  });
  window.lnc = {
    state: function () {
      var b = here(), align = b ? getComputedStyle(b).textAlign : '', s = getSelection();
      var at = s.rangeCount ? s.getRangeAt(0).startContainer : null;
      at = at && at.nodeType === 3 ? at.parentElement : at;
      return JSON.stringify({bold: document.queryCommandState('bold'), italic: document.queryCommandState('italic'),
              underline: document.queryCommandState('underline'), strike: document.queryCommandState('strikeThrough'),
              sup: document.queryCommandState('superscript'), sub: document.queryCommandState('subscript'),
              tag: b ? b.tagName.toLowerCase() : '', align: align === 'start' ? 'left' : align,
              indent: b ? getComputedStyle(b).textIndent : '', i: b ? index(b) : -1, home: b ? lnc.homeOf(b) : -1,
              sc: !!(at && at.closest('small.sc')), frame: !!(b && b.closest('.marco')),
              // el párrafo del cursor, para la barra de «Mostrar cambios»: su texto, si cambió y si es nuevo
              text: b ? b.textContent : '', edited: !!(b && lnc.orig && lnc.edited(b)),
              fresh: !!(b && lnc.orig && !lnc.orig.has(b)), obj: !!(b && b.classList.contains('lnc-obj')),
              dirty: lnc.changedAny(), canUndo: document.queryCommandEnabled('undo'),
              canRedo: document.queryCommandEnabled('redo'),
              wants: lnc.take()});  // un objeto no llega a Python: JSON
    },
    // Ctrl+Z (o Ctrl+Y) sin nada que deshacer en Chromium: lo hecho por la hoja de Qt (versalitas, recuadro…) se
    // deshace allí; el sondeo lo recoge
    wanted: '',
    take: function () { var w = lnc.wanted; lnc.wanted = ''; return w; },
    pasted: '',  // lo pegado que va a la hoja de Qt (paste): [{html} o {token}] en JSON
    takePasted: function () { var p = lnc.pasted; lnc.pasted = ''; return p; },
    undo: function (redo) {
      var name = redo ? 'redo' : 'undo';
      if (!document.queryCommandEnabled(name)) return false;
      sheet.focus(); document.execCommand(name); return true;
    },
    // Dónde estaba cada párrafo al cargar (o al volcar a la hoja de Qt): la hoja de Qt sigue con esas líneas, y así
    // el panel del original y el de navegación encuentran su párrafo aunque se haya escrito uno nuevo antes
    // La hoja de Qt cambió y aquí solo se mira: cada [i, j, [html…]] cambia los párrafos de i a j (sin j) por esos,
    // sin recargar la página (tardaba segundos y volvía arriba). Llegan de atrás adelante. false: no se pudo
    splice: function (ops) {
      lncClearDiff(sheet);
      try {
        ops.forEach(function (op) {
          var kids = sheet.children, after = kids[op[1]] || null;
          for (var k = op[1] - 1; k >= op[0]; k--) kids[k].remove();
          if (!op[2].length) return;
          var r = document.createRange();
          r.selectNodeContents(sheet);
          var frag = r.createContextualFragment(op[2].join(''));
          Array.prototype.forEach.call(frag.children, function (e) { if (lnc.orig) lnc.orig.set(e, lnc.inner(e)); });
          sheet.insertBefore(frag, after);
        });
      } catch (e) { return false; }
      lnc.count = sheet.children.length;
      lnc.rehome();
      lnc.placeHere(false);
      return true;
    },
    rehome: function () { lnc.home = new WeakMap(); Array.prototype.forEach.call(sheet.children, function (b, i) { lnc.home.set(b, i); }); },
    homeOf: function (b) { while (b && !lnc.home.has(b)) b = b.previousElementSibling; return b ? lnc.home.get(b) : 0; },
    goHome: function (n) {  // el cursor al principio del párrafo que estaba en el sitio n
      var t = Array.prototype.find.call(sheet.children, function (b) { return lnc.home.has(b) && lnc.home.get(b) >= n; });
      t = t || sheet.lastElementChild;
      if (t) caret(t, 0);
    },
    changedAny: function () {  // ¿algo distinto de lo cargado? (cancelar pregunta)
      if (!lnc.orig) return false;
      return sheet.children.length !== lnc.count || Array.prototype.some.call(sheet.children, function (b) {
        return !b.classList.contains('lnc-obj') && lnc.edited(b); });
    },
    // Buscar: el texto de cada párrafo y lo elegido; Python busca con su expresión y elige el resultado
    texts: function () {
      var s = getSelection(), at = [-1, 0, 0];
      if (s.rangeCount) {
        var r = s.getRangeAt(0), b = block(r.startContainer);
        var c = block(r.endContainer), to = null;
        if (b) { var a = offset(b, r.startContainer, r.startOffset);
                 at = [index(b), a, c === b ? offset(b, r.endContainer, r.endOffset) : a];
                 if (c && c !== b) to = [index(c), offset(c, r.endContainer, r.endOffset)]; }  // varios párrafos
      }
      return JSON.stringify({texts: Array.prototype.map.call(sheet.children, function (b) {
        return b.classList.contains('lnc-obj') ? '' : b.textContent; }), at: at, to: to});
    },
    outline: function () {  // el panel de navegación: títulos, párrafos cambiados y avisos pintados, [número, texto]
      var out = {titles: [], changed: [], issues: []};
      Array.prototype.forEach.call(sheet.children, function (b, i) {
        if (b.classList.contains('lnc-obj')) return;
        if (/^h[1-6]$/i.test(b.tagName)) out.titles.push([i, b.textContent]);  // XHTML: en minúsculas
        if (lnc.edited(b)) out.changed.push([i, b.textContent]);
      });
      (lnc.found || []).forEach(function (f) {
        var b = block(f.range.startContainer); if (b) out.issues.push([index(b), b.textContent, f.id]);
      });
      return JSON.stringify(out);
    },
    select: function (i, a, z, j) {  // de a en el párrafo i a z en el j (o en el i)
      var b = sheet.children[i], c = j === undefined || j === null ? b : sheet.children[j];
      if (b && c) caret(b, a, z, c); return !!b;
    },
    replaceSel: function (text) { sheet.focus(); document.execCommand('insertText', false, text); },
    // Leer en voz alta: el párrafo i (o el del cursor, con -1) o el siguiente con texto, de fondo y con el cursor
    readAt: function (i, color) {
      if (i < 0) { var h = here(); i = h ? index(h) : 0; }
      while (i < sheet.children.length && (sheet.children[i].classList.contains('lnc-obj')
                                           || !sheet.children[i].textContent.trim())) i++;
      lnc.reading = sheet.children[i] || null;
      var css = document.getElementById('lnc-read') || document.head.appendChild(document.createElement('style'));
      css.id = 'lnc-read'; css.textContent = '::highlight(lnc-reading){background-color: ' + color + ';}';
      lnc.paint();
      if (!lnc.reading) return '';
      caret(lnc.reading, 0);
      return JSON.stringify({i: i, text: lnc.reading.innerText});
    },
    stopReading: function () { lnc.reading = null; lnc.paint(); },
    cmd: function (name, value) { sheet.focus(); document.execCommand('styleWithCSS', false, false); document.execCommand(name, false, value || null); },
    css: function (name, value) {  // color o letra: un <span style>
      sheet.focus(); document.execCommand('styleWithCSS', false, true); document.execCommand(name, false, value);
      document.execCommand('styleWithCSS', false, false);
    },
    size: function (em) {  // tamaño en em: Chromium solo sabe 1-7; se marca con una letra falsa y se cambia
      sheet.focus(); document.execCommand('styleWithCSS', false, true);
      document.execCommand('fontName', false, 'lnc-size-' + em);
      document.execCommand('styleWithCSS', false, false);
      Array.prototype.forEach.call(sheet.querySelectorAll('[style*="lnc-size-"], font[face^="lnc-size-"]'), function (el) {
        var m = /lnc-size-([\d.]+)/.exec(el.getAttribute('style') || el.getAttribute('face'));
        el.style.removeProperty('font-family'); el.removeAttribute('face');
        if (m && m[1] !== '1') el.style.fontSize = m[1] + 'em';
        if (!el.getAttribute('style')) el.removeAttribute('style');
      });
    },
    ruby: function (base, reading) {
      lnc.cmd('insertHTML', '<ruby>' + base + '<rp>(</rp><rt>' + reading + '</rt><rp>)</rp></ruby>');
    },
    // Marcas (ortografía, avisos, cambios): resaltados de CSS, que no tocan el HTML del libro
    marks: {spell: new Set(), issues: [], changed: new Set(), show: false, colors: {}},
    setMarks: function (data) {
      var m = lnc.marks, d = JSON.parse(data);
      m.spell = new Set(d.spell); m.issues = d.issues; m.show = d.show; m.colors = d.colors;
      m.changed = new Set(d.changed.map(function (i) { return sheet.children[i]; }));
      m.gone = d.gone || []; m.formats = d.formats || [];
      // el texto de antes de cada párrafo, por su sitio al cargar (null: nuevo), para pintar lo puesto y lo quitado
      m.before = new Map();
      (d.before || []).forEach(function (t, i) { if (sheet.children[i]) m.before.set(sheet.children[i], lncText(t)); });
      var css = document.getElementById('lnc-marks') || document.head.appendChild(document.createElement('style'));
      css.id = 'lnc-marks';
      css.textContent = d.del_css + '::highlight(lnc-spell){text-decoration: underline wavy ' + d.colors.spell + ';}' +
        '::highlight(lnc-change){background-color: ' + d.colors.change + ';}' +
        d.levels.map(function (c, n) { return '::highlight(lnc-issue-' + n + '){text-decoration: underline wavy ' + c + ';}'; }).join('');
      if (!lnc.orig) {  // cómo era cada párrafo al cargar: el que cambia pierde sus avisos y es un cambio
        lnc.orig = new WeakMap();
        Array.prototype.forEach.call(sheet.children, function (b) { lnc.orig.set(b, lnc.inner(b)); });
        lnc.count = sheet.children.length;
      }
      lnc.paint();
    },
    setChanges: function (changed, before, gone, formats) {  // solo «Mostrar cambios» (en vivo): como setMarks
      var m = lnc.marks;
      m.gone = gone || []; m.formats = formats || [];
      m.changed = new Set(changed.map(function (i) { return sheet.children[i]; }));
      m.before = new Map();
      before.forEach(function (t, i) { if (sheet.children[i]) m.before.set(sheet.children[i], lncText(t)); });
      lnc.paint();
    },
    setSpell: function (words) { lnc.marks.spell = new Set(JSON.parse(words)); lnc.paint(); },
    showChanges: function (on) { lnc.marks.show = on; lnc.paint(); },
    edited: function (b) { return lnc.marks.changed.has(b) || !lnc.orig || !lnc.orig.has(b) || lnc.orig.get(b) !== lnc.inner(b); },
    // el HTML del párrafo sin las marcas de lo quitado (en XHTML, con xmlns y quizá como <span …/>)
    inner: function (b) { return b.innerHTML.replace(/<span\b[^>]*\bclass="lnc-del"[^>]*?(?:\/>|><\/span>)/g, ''); },
    ranges: function (b, text, near) {  // Range de text en el párrafo b, la aparición más cercana a near
      var nodes = [], all = '', w = document.createTreeWalker(b, NodeFilter.SHOW_TEXT), n;
      while ((n = w.nextNode())) { nodes.push([n, all.length]); all += n.data; }
      var best = -1, at = all.indexOf(text);
      while (at >= 0) { if (best < 0 || Math.abs(at - near) < Math.abs(best - near)) best = at; at = all.indexOf(text, at + 1); }
      if (best < 0 || !text) return null;
      function point(pos) { for (var i = nodes.length - 1; i >= 0; i--) if (pos >= nodes[i][1]) return [nodes[i][0], pos - nodes[i][1]]; return null; }
      var a = point(best), z = point(best + text.length), r = document.createRange();
      if (!a || !z) return null;
      r.setStart(a[0], a[1]); r.setEnd(z[0], Math.min(z[1], z[0].data.length));
      return r;
    },
    paint: function () {
      if (!window.CSS || !CSS.highlights) return;
      var m = lnc.marks, spell = new Highlight(), change = new Highlight(), levels = {};
      lnc.found = [];  // [{id, range}] de los avisos pintados: para el clic derecho y F8
      lncClearDiff(sheet);
      var own = ['data-lnc-above', 'data-lnc-below', 'data-lnc-ga', 'data-lnc-gb', 'data-lnc-added'];
      Array.prototype.forEach.call(sheet.querySelectorAll(own.map(function (a) { return '[' + a + ']'; }).join(',')),
        function (b) { own.forEach(function (a) { b.removeAttribute(a); }); });
      var pictures = '';  // lo quitado con ilustraciones: su content (con las imágenes) en una regla por párrafo
      // [n.º del de debajo, quitado encima, n.º del de arriba, debajo (, content de encima, de debajo)]
      if (m.show) (m.gone || []).forEach(function (g, k) {
        var up = sheet.children[g[0]], down = sheet.children[g[2]];
        if (g[1] && up) up.setAttribute('data-lnc-above', g[1]);
        if (g[3] && down) down.setAttribute('data-lnc-below', g[3]);
        // con los dos atributos: gana a [data-lnc-above]::before sin depender del orden de los <style>. La imagen ya
        // lleva su fondo: sin el del texto ni el hueco de la línea base (font-size 0)
        var look = 'background:none;padding:0;font-size:0;line-height:0;';
        if (g[4] && up) {
          up.setAttribute('data-lnc-ga', k);
          pictures += '[data-lnc-above][data-lnc-ga="' + k + '"]::before{content:' + g[4] + ';' + look + 'margin-bottom:4px;}';
        }
        if (g[5] && down) {
          down.setAttribute('data-lnc-gb', k);
          pictures += '[data-lnc-below][data-lnc-gb="' + k + '"]::after{content:' + g[5] + ';' + look + 'margin-top:4px;}';
        }
      });
      if (m.show) m.changed.forEach(function (b) { if (b && b.classList.contains('lnc-obj')) b.setAttribute('data-lnc-added', ''); });
      var rules = document.getElementById('lnc-gone') || document.head.appendChild(document.createElement('style'));
      rules.id = 'lnc-gone';
      rules.textContent = pictures;
      if (m.show) {  // grupos: un párrafo con su texto de antes y los nuevos que lo siguen (Intro), comparados juntos
        var groups = [], group = null;
        Array.prototype.forEach.call(sheet.children, function (b) {
          if (b.classList.contains('lnc-obj')) { group = null; return; }
          var before = m.before ? m.before.get(b) : undefined;
          if (typeof before === 'string' || !group) groups.push(group = {before: typeof before === 'string' ? before : null, els: []});
          group.els.push(b);
        });
        groups.forEach(function (g) {
          if (g.els.some(lnc.edited)) lncPaintGroup(g.els, g.before).forEach(function (r) { change.add(r); });
        });
        (m.formats || []).forEach(function (f) {  // [n.º de párrafo, desde, cuántas letras]: otro formato, mismo texto
          var b = sheet.children[f[0]];
          if (!b) return;
          var w = document.createTreeWalker(b, NodeFilter.SHOW_TEXT), n, from = 0, a = null, z = null;
          while ((n = w.nextNode())) {
            if (!a && f[1] < from + n.data.length) a = [n, f[1] - from];
            if (f[1] + f[2] <= from + n.data.length) { z = [n, f[1] + f[2] - from]; break; }
            from += n.data.length;
          }
          if (a && z) { var r = document.createRange(); r.setStart(a[0], a[1]); r.setEnd(z[0], z[1]); change.add(r); }
        });
      }
      Array.prototype.forEach.call(sheet.children, function (b, i) {
        if (b.classList.contains('lnc-obj')) return;
        if (m.spell.size) {
          var w = document.createTreeWalker(b, NodeFilter.SHOW_TEXT), n, re = /[\p{L}\p{M}]+/gu, k;
          while ((n = w.nextNode())) {
            if (n.parentElement && n.parentElement.closest('rt, rp')) continue;
            re.lastIndex = 0;
            while ((k = re.exec(n.data))) if (m.spell.has(k[0].toLowerCase())) {
              var s = document.createRange(); s.setStart(n, k.index); s.setEnd(n, k.index + k[0].length); spell.add(s);
            }
          }
        }
      });
      m.issues.forEach(function (it) {
        var b = sheet.children[it.i];
        if (!b || lnc.edited(b)) return;
        var r = lnc.ranges(b, it.text, it.at);
        if (!r) return;
        (levels[it.level] = levels[it.level] || new Highlight()).add(r);
        lnc.found.push({id: it.id, range: r});
      });
      CSS.highlights.clear();
      CSS.highlights.set('lnc-spell', spell);
      CSS.highlights.set('lnc-change', change);
      Object.keys(levels).forEach(function (n) { CSS.highlights.set('lnc-issue-' + n, levels[n]); });
      if (lnc.reading && lnc.reading.isConnected) {
        var read = document.createRange(); read.selectNodeContents(lnc.reading); CSS.highlights.set('lnc-reading', new Highlight(read));
      }
    },
    at: function (x, y) {  // lo que hay en el punto del clic derecho: palabra y avisos
      var r = document.caretRangeFromPoint(x, y), out = {word: '', issues: []};
      if (!r) return JSON.stringify(out);
      var n = r.startContainer, o = r.startOffset;
      if (n.nodeType === 3) {
        var a = o, z = o, t = n.data, letter = /[\p{L}\p{M}]/u;
        while (a > 0 && letter.test(t[a - 1])) a--;
        while (z < t.length && letter.test(t[z])) z++;
        out.word = t.slice(a, z);
        lnc.wordRange = document.createRange(); lnc.wordRange.setStart(n, a); lnc.wordRange.setEnd(n, z);
      }
      (lnc.found || []).forEach(function (f) { if (f.range.isPointInRange(n, o)) out.issues.push(f.id); });
      // el párrafo (su sitio en la hoja de Qt), si cambió y su marca (ilustración); como Word, el clic fuera de lo
      // elegido pone ahí el cursor: lo que se haga desde el menú es sobre ese punto
      var b = block(n), s = getSelection();
      if (b) { out.home = lnc.homeOf(b); out.edited = lnc.edited(b); out.token = b.getAttribute('data-token') || ''; }
      if (b && !(s.rangeCount && !s.isCollapsed && s.getRangeAt(0).isPointInRange(n, o))) {
        var c = document.createRange(); c.setStart(n, o); c.collapse(true); s.removeAllRanges(); s.addRange(c);
      }
      return JSON.stringify(out);
    },
    replaceWord: function (text) {  // la palabra del último clic derecho
      if (!lnc.wordRange) return;
      var s = getSelection(); s.removeAllRanges(); s.addRange(lnc.wordRange);
      document.execCommand('insertText', false, text);
    },
    next: function (kind) {  // F7 (cambio) o F8 (aviso) siguiente tras el cursor; false si no hay más en el capítulo
      var s = getSelection(), here = s.rangeCount ? s.getRangeAt(0) : null, target = null;
      var items = kind === 'issue' ? (lnc.found || []).map(function (f) { return f.range; }) :
        Array.prototype.filter.call(sheet.children, function (b) { return !b.classList.contains('lnc-obj') && lnc.edited(b); })
          .map(function (b) { var r = document.createRange(); r.selectNodeContents(b); return r; });
      items.sort(function (a, b) { return a.compareBoundaryPoints(Range.START_TO_START, b); });
      for (var i = 0; i < items.length; i++) {
        if (!here || items[i].compareBoundaryPoints(Range.START_TO_END, here) > 0) { target = items[i]; break; }
      }
      if (!target) return false;
      var c = document.createRange(); c.setStart(target.startContainer, target.startOffset); c.collapse(true);
      s.removeAllRanges(); s.addRange(c);
      (target.startContainer.parentElement || sheet).scrollIntoView({block: 'center'});
      return true;
    },
    // El párrafo del cursor con la banda de la hoja de Qt: un div fuera de la hoja, detrás del texto (el HTML de la
    // hoja no cambia). Lo pone el cursor al moverse aquí, o here(n) cuando se escribe en la otra hoja
    here: function (n, scroll, color) {
      lnc.hereAt = n;
      if (color) lnc.hereColor = color;
      lnc.placeHere(scroll);
    },
    placeHere: function (scroll) {
      // en <html> y medido desde él: el CSS del libro puede dar posición o margen al <body>, y la banda bajaba
      var root = document.documentElement, mark = document.getElementById('lnc-here');
      if (!mark) { mark = document.createElement('div'); mark.id = 'lnc-here'; root.appendChild(mark); }
      root.style.position = 'relative';
      var b = lnc.hereAt === undefined ? null : sheet.children[lnc.hereAt];
      if (!b) { mark.style.display = 'none'; return; }
      var r = b.getBoundingClientRect(), s = sheet.getBoundingClientRect(), o = root.getBoundingClientRect();
      mark.style.cssText = 'position:absolute;pointer-events:none;z-index:0;border-radius:4px;background:' +
        (lnc.hereColor || 'rgba(31,111,209,0.1)') + ';left:' + (s.left - o.left - 10) + 'px;top:' +
        (r.top - o.top - 3) + 'px;width:' + (s.width + 20) + 'px;height:' + (r.height + 6) + 'px';
      var d = mark.getBoundingClientRect();  // donde quedó de verdad: se corrige lo que desplace su contenedor
      mark.style.top = (parseFloat(mark.style.top) + r.top - 3 - d.top) + 'px';
      mark.style.left = (parseFloat(mark.style.left) + s.left - 10 - d.left) + 'px';
      if (scroll) b.scrollIntoView({block: 'center'});
    },
    takeText: function () { var t = lnc.dirty ? sheet.innerText : ''; lnc.dirty = false; return t; },
    dump: function () {  // [{tag, attrs, html, token}] de cada párrafo, en orden
      // texto o letra suelta entre los párrafos (Chromium la deja ahí al pegar o borrar): un párrafo más; solo los
      // párrafos se volcaban y eso se perdía al aceptar
      var out = [], loose = '';
      function flush() { if (loose.replace(/<[^>]*>/g, '').trim()) out.push({tag: 'p', attrs: {}, html: loose, token: ''}); loose = ''; }
      Array.prototype.forEach.call(sheet.childNodes, function (b) {
        if (b.nodeType === 3) { loose += escaped(b.data); return; }
        if (b.nodeType !== 1) return;
        if (INLINE.test(b.tagName)) { loose += b.outerHTML; return; }
        flush();
        var attrs = {};
        Array.prototype.forEach.call(b.attributes, function (a) { attrs[a.name] = a.value; });
        out.push({tag: b.tagName.toLowerCase(), attrs: attrs, html: lnc.inner(b), token: b.getAttribute('data-token') || ''});
      });
      flush();
      return JSON.stringify(out);
    }
  };
  lnc.rehome();
  document.addEventListener('selectionchange', function () {
    var b = here();
    if (b) { lnc.hereAt = index(b); lnc.placeHere(false); }
  });
  sheet.addEventListener('input', function () { lnc.placeHere(false); });
  addEventListener('resize', function () { lnc.placeHere(false); });
  try { new ResizeObserver(function () { lnc.placeHere(false); }).observe(sheet); } catch (e) {}
  if (document.fonts) document.fonts.ready.then(function () { lnc.placeHere(false); });
  addEventListener('load', function () { lnc.placeHere(false); });
  return true;
})()
"""


def _element(line, objects, base):
    """Línea del editor -> su elemento en la hoja. objects: {marca: (ruta en el zip, bytes)} de las ilustraciones;
    base: archivo de la página (para la ruta de sus imágenes)."""
    bare = line.strip()
    token = convert.TOKEN.fullmatch(bare) or convert.PICTURE.fullmatch(bare)
    if token:
        mark = html.escape(bare)
        if objects.get(bare, (None,))[0]:  # ilustración: su imagen
            src = posixpath.relpath(objects[bare][0], posixpath.dirname(base) or ".")
            return (f'<figure class="lnc-obj" contenteditable="false" data-token="{mark}">'
                    f'<img src="{html.escape(src)}" alt=""/></figure>')
        text = objects.get(bare, (None, b""))[1].decode("utf-8", "replace") if bare in objects else "* * *"
        return f'<p class="lnc-obj lnc-break" contenteditable="false" data-token="{mark}">{html.escape(text)}</p>'
    return editor.to_code(line)


def page(lines, objects, head="", body_attrs="", path="hoja.xhtml"):
    """XHTML de la hoja con las líneas; head: el <head> del archivo original (su CSS y sus fuentes) o el de la app."""
    body = "\n".join(_element(line, objects, path) for line in lines if line.strip())
    head = head or '<head><meta charset="utf-8"/><title>Hoja</title></head>'
    head = head.replace("</head>", f"<style>{SHEET_CSS}</style></head>", 1)
    return ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n<html xmlns="http://www.w3.org/1999/xhtml" '
            'xmlns:epub="http://www.idpf.org/2007/ops">\n' + head +
            f'\n<body{body_attrs}>\n<div id="lnc-sheet" contenteditable="true" spellcheck="false">\n{body}\n</div>\n'
            '</body>\n</html>\n')


INLINE_JUNK = (
    (re.compile(r'\sxmlns(?::\w+)?="[^"]*"'), ""),  # Chromium lo repite en cada elemento al serializar XHTML
    (re.compile(r"<(/?)strike\b"), r"<\1s"),
    (re.compile(r"<font\b[^>]*>|</font>"), ""),
    (re.compile(r'\sstyle=""'), ""),
    (re.compile(r"<span>(.*?)</span>", re.S), r"\1"),
    (re.compile(r"<(p|div|h[1-6]|li|blockquote)\b[^>]*>|</(p|div|h[1-6]|li|blockquote)>"), ""),
)


def style_attr(value):
    """Un atributo style como lo escribe la app: «k: v; k: v», sin «;» final, 0px como 0 y sin lo que pone la
    propia hoja (caret-color)."""
    decls = []
    for part in (value or "").split(";"):
        key, _, val = part.partition(":")
        key, val = key.strip().lower(), " ".join(val.split())
        if key and val and key != "caret-color":
            decls.append(f"{key}: {re.sub(r'(?<![\d.])0px\b', '0', val)}")
    return "; ".join(decls)


def clean(inner):
    """HTML de un párrafo tal como lo deja Chromium -> como lo escribiría la app."""
    inner = re.sub(r'\sstyle="([^"]*)"', lambda m: f' style="{style_attr(html.unescape(m[1]))}"', inner)
    for pattern, new in INLINE_JUNK:
        inner = pattern.sub(new, inner)
    inner = inner.replace("&nbsp;", " ").replace("\xa0", " ")
    inner = re.sub(r"<br\s*/?>\s*$", "", inner)  # el <br> que Chromium deja al final de un párrafo vacío
    return re.sub(r"<br\s*/?\s*>", "<br/>", inner)


QT_ONLY = {editor.LINE_BREAK, editor.VOID_CHAR, "￼"}  # en el texto de la hoja de Qt, no en el de Chromium


def positions(web, qt):
    """[posición en el texto qt de un párrafo de la hoja de Qt de cada posición 0..len(web) del mismo párrafo en la
    web]. La hoja de Qt tiene de más los saltos de línea (<br/>), las etiquetas vacías y los objetos; un espacio
    duro y uno normal valen igual. Lo que no casa se salta: un párrafo distinto da posiciones aproximadas."""
    out, j = [], 0
    for ch in web:
        while j < len(qt) and qt[j] in QT_ONLY and qt[j] != ch:
            j += 1
        out.append(j)
        if j < len(qt) and (qt[j] == ch or qt[j].isspace() and ch.isspace()):
            j += 1
    out.append(j)
    return out


def web_position(table, pos):
    """Al revés que positions: la posición en la web de la posición pos de la hoja de Qt."""
    return next((k for k, at in enumerate(table) if at >= pos), len(table) - 1)


def lines_of(dumped, known=()):
    """lnc.dump() (JSON) -> texto para la hoja de Qt (como editor.dump), con las ilustraciones como sus marcas."""
    code = []
    for item in json.loads(dumped):
        if item.get("token"):
            code.append(f"<!-- {item['token']} -->")
            continue
        values = {k: (style_attr(v) if k == "style" else v) for k, v in item["attrs"].items()
                  if k not in ("contenteditable", "spellcheck", "data-token") and not k.startswith("data-lnc")}
        attrs = "".join(f' {k}="{html.escape(v)}"' for k, v in values.items() if k != "style" or v)
        inner = clean(item["html"]).strip()
        if inner:
            code.append(f"<{item['tag']}{attrs}>{inner}</{item['tag']}>")
    return editor.from_code("\n".join(code), known)


def text_file(blocks):
    """Archivo del EPUB con más párrafos de blocks: el del texto del capítulo, no su portadilla de página fija (con
    su hoja de estilo, que en los libros de Kadokawa pone la letra a 0)."""
    import collections
    count = collections.Counter(b.src[0] for b in blocks if b.src and b.kind == "p")
    return count.most_common(1)[0][0] if count else next((b.src[0] for b in blocks if b.src), None)


def sources(book, start, end, lines, pictures, horizontal=False):
    """Lo que la hoja web necesita para dibujar el capítulo start..end del libro: (archivos del zip, ruta de la
    página, su <head>, atributos de su <body>). Con un EPUB de entrada, los del original (su CSS, sus fuentes, sus
    imágenes) y la página en el sitio de su archivo; si no, la hoja de estilo de la app."""
    files, objects = {}, {}
    origin = book.origin
    first = text_file(book.blocks[start:end]) if origin else None
    head, body_attrs = "", ""
    if origin and first:
        with zipfile.ZipFile(origin) as z:
            files = {name: z.read(name) for name in z.namelist() if not name.endswith("/")}
        text = files.get(first, b"").decode("utf-8", "replace")
        m = re.search(r"<head\b.*?</head>", text, re.S)
        head = m[0] if m else ""
        # lo que el editor escribe con clases propias (versalitas, recuadro): al EPUB van con él (faithful)
        head = head.replace("</head>", f"<style>{convert.MARKUP_CSS}</style></head>", 1)
        b = re.search(r"<body\b([^>]*)>", text)
        body_attrs = b[1] if b else ""
        path = first
    else:
        files["lnc/estilo.css"] = (convert.BASE_CSS + convert.STYLES["Clásico"]).encode()
        head = ('<head><meta charset="utf-8"/><title>Hoja</title>'
                '<link rel="stylesheet" type="text/css" href="estilo.css"/></head>')
        path = "lnc/hoja.xhtml"
    folder = posixpath.dirname(path)
    for line in lines:
        bare = line.strip()
        token = convert.TOKEN.fullmatch(bare)
        if token and token[1] == "Ilustración" and 0 <= int(token[2]) < len(book.blocks):
            data = book.blocks[int(token[2])].image
            name = posixpath.join(folder, f"lnc-obj/{token[2]}.{'png' if data[:4] == b'\x89PNG' else 'jpg'}")
            files[name], objects[bare] = data, (name, data)
        elif token and 0 <= int(token[2]) < len(book.blocks) and book.blocks[int(token[2])].image:
            # separador dibujado: su imagen (girada, si el libro vertical se lee en horizontal)
            data = book.blocks[int(token[2])].image
            if horizontal and book.vertical:
                import faithful
                data = faithful._turned(data) or data
            name = posixpath.join(folder, f"lnc-obj/{token[2]}.{'png' if data[:4] == b'\x89PNG' else 'jpg'}")
            files[name], objects[bare] = data, (name, data)
        elif token:  # separador: su texto
            blockhtml = book.blocks[int(token[2])].html if 0 <= int(token[2]) < len(book.blocks) else ""
            objects[bare] = (None, (convert.TAGS.sub("", html.unescape(blockhtml)).strip() or "* * *").encode())
        elif convert.PICTURE.fullmatch(bare) and bare in pictures:
            name = posixpath.join(folder, f"lnc-obj/{convert.PICTURE.fullmatch(bare)[2]}")
            files[name], objects[bare] = pictures[bare], (name, pictures[bare])
    return files, objects, path, head, body_attrs
