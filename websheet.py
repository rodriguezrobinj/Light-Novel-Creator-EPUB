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
#lnc-sheet { outline: none; min-height: 90vh; }
#lnc-sheet .lnc-obj { user-select: none; cursor: default; opacity: 0.95; }
#lnc-sheet .lnc-obj img { max-width: 100%; max-height: 60vh; }
#lnc-sheet p.lnc-break { text-align: center; text-indent: 0; color: #888888; }
html { background: #ffffff; } body { margin: 24px 36px !important; }
"""

# Funciones de la hoja: estado para la cinta, órdenes de formato, limpieza al pegar y atajos de Word en español
SHEET_JS = r"""
(function () {
  var sheet = document.getElementById('lnc-sheet');
  document.execCommand('defaultParagraphSeparator', false, 'p');
  document.execCommand('styleWithCSS', false, false);
  function block(node) {  // el párrafo (hijo de la hoja) donde está node
    while (node && node.parentNode !== sheet) node = node.parentNode;
    return node && node.nodeType === 1 ? node : null;
  }
  function here() { var s = window.getSelection(); return s.rangeCount ? block(s.getRangeAt(0).startContainer) : null; }
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
    var p = spot(b, a, z !== undefined && (c || z !== a)), q = spot(c || b, z === undefined ? a : z), r = document.createRange();
    r.setStart(p[0], p[1]); r.setEnd(q[0], q[1]);
    sheet.focus(); var s = getSelection(); s.removeAllRanges(); s.addRange(r);
    (p[0].nodeType === 3 ? p[0].parentElement : b).scrollIntoView({block: 'center'});
  }
  function blocks() {  // los párrafos de la selección
    var s = window.getSelection(); if (!s.rangeCount) return [];
    var r = s.getRangeAt(0), a = block(r.startContainer), b = block(r.endContainer), out = [], n = a;
    while (n) { if (n.nodeType === 1 && !n.classList.contains('lnc-obj')) out.push(n); if (n === b) break; n = n.nextSibling; }
    return out;
  }
  function style(el, key, value) {
    el.style.setProperty(key, value === null ? '' : value);
    if (value === null) el.style.removeProperty(key);
    if (!el.getAttribute('style')) el.removeAttribute('style');
  }
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
  });
  sheet.addEventListener('paste', function (e) {
    e.preventDefault();
    var data = e.clipboardData, htmlText = data.getData('text/html'), out;
    if (htmlText) {
      var box = document.createElement('div');
      box.innerHTML = htmlText.replace(/<!--[\s\S]*?-->/g, '');
      (function walk(n, into) {
        Array.prototype.forEach.call(n.childNodes, function (c) {
          if (c.nodeType === 3) { into.push(c.data.replace(/[<&>]/g, function (m) { return {'<':'&lt;','&':'&amp;','>':'&gt;'}[m]; })); return; }
          if (c.nodeType !== 1) return;
          var tag = c.tagName.toLowerCase(), keep = {i: 'i', em: 'i', b: 'b', strong: 'b'}[tag];
          var bold = /bold|[6-9]00/.test(c.style ? c.style.fontWeight : ''), italic = c.style && c.style.fontStyle === 'italic';
          if (tag === 'a' && /^https?:/.test(c.getAttribute('href') || '')) { into.push('<a href="' + c.getAttribute('href').replace(/"/g, '&quot;') + '">'); walk(c, into); into.push('</a>'); return; }
          if (tag === 'br') { into.push('<br/>'); return; }
          if (keep || bold || italic) { var t = keep || (bold ? 'b' : 'i'); into.push('<' + t + '>'); walk(c, into); into.push('</' + t + '>'); return; }
          walk(c, into);
          if (/^(p|div|h[1-6]|li)$/.test(tag)) into.push('<br/>');
        });
      })(box, out = []);
      document.execCommand('insertHTML', false, out.join('').replace(/(<br\/>)+$/, ''));
    } else {
      document.execCommand('insertText', false, data.getData('text/plain'));
    }
  });
  var keys = {78: 'bold', 66: 'bold', 75: 'italic', 73: 'italic', 83: 'underline'};  // N, B, K, I, S (Word en español)
  sheet.addEventListener('keydown', function (e) {
    if (e.ctrlKey && !e.altKey && !e.shiftKey && (e.keyCode === 90 || e.keyCode === 89)) {  // Ctrl+Z, Ctrl+Y
      var redo = e.keyCode === 89;
      if (!document.queryCommandEnabled(redo ? 'redo' : 'undo')) { e.preventDefault(); lnc.wanted = redo ? 'redo' : 'undo'; }
    }
    else if (e.ctrlKey && !e.altKey && !e.shiftKey && keys[e.keyCode]) { e.preventDefault(); document.execCommand(keys[e.keyCode]); }
    else if (e.ctrlKey && !e.altKey && e.keyCode === 69) { e.preventDefault(); lnc.align(lnc.state().align === 'center' ? null : 'center'); }
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
              dirty: lnc.changedAny(), canUndo: document.queryCommandEnabled('undo'),
              wants: lnc.take()});  // un objeto no llega a Python: JSON
    },
    // Ctrl+Z (o Ctrl+Y) sin nada que deshacer en Chromium: lo hecho por la hoja de Qt (versalitas, recuadro…) se
    // deshace allí; el sondeo lo recoge
    wanted: '',
    take: function () { var w = lnc.wanted; lnc.wanted = ''; return w; },
    undo: function (redo) {
      var name = redo ? 'redo' : 'undo';
      if (!document.queryCommandEnabled(name)) return false;
      sheet.focus(); document.execCommand(name); return true;
    },
    // Dónde estaba cada párrafo al cargar (o al volcar a la hoja de Qt): la hoja de Qt sigue con esas líneas, y así
    // el panel del original y el de navegación encuentran su párrafo aunque se haya escrito uno nuevo antes
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
    retag: function (tag) {  // estilo de párrafo: otra etiqueta, mismos atributos y contenido
      blocks().forEach(function (b) {
        if (b.tagName.toLowerCase() === tag) return;
        var n = document.createElement(tag);
        if (lnc.home.has(b)) lnc.home.set(n, lnc.home.get(b));
        Array.prototype.forEach.call(b.attributes, function (a) { n.setAttribute(a.name, a.value); });
        while (b.firstChild) n.appendChild(b.firstChild);
        b.parentNode.replaceChild(n, b);
      });
    },
    align: function (how) { blocks().forEach(function (b) { style(b, 'text-align', how); if (how) style(b, 'text-indent', '0'); }); },
    indent: function (value) { blocks().forEach(function (b) { style(b, 'text-indent', value); }); },
    ruby: function (base, reading) {
      lnc.cmd('insertHTML', '<ruby>' + base + '<rp>(</rp><rt>' + reading + '</rt><rp>)</rp></ruby>');
    },
    // Marcas (ortografía, avisos, cambios): resaltados de CSS, que no tocan el HTML del libro
    marks: {spell: new Set(), issues: [], changed: new Set(), show: false, colors: {}},
    setMarks: function (data) {
      var m = lnc.marks, d = JSON.parse(data);
      m.spell = new Set(d.spell); m.issues = d.issues; m.show = d.show; m.colors = d.colors;
      m.changed = new Set(d.changed.map(function (i) { return sheet.children[i]; }));
      var css = document.getElementById('lnc-marks') || document.head.appendChild(document.createElement('style'));
      css.id = 'lnc-marks';
      css.textContent = '::highlight(lnc-spell){text-decoration: underline wavy ' + d.colors.spell + ';}' +
        '::highlight(lnc-change){text-decoration: underline ' + d.colors.change + '; color: ' + d.colors.change + ';}' +
        d.levels.map(function (c, n) { return '::highlight(lnc-issue-' + n + '){text-decoration: underline wavy ' + c + ';}'; }).join('');
      if (!lnc.orig) {  // cómo era cada párrafo al cargar: el que cambia pierde sus avisos y es un cambio
        lnc.orig = new WeakMap();
        Array.prototype.forEach.call(sheet.children, function (b) { lnc.orig.set(b, b.innerHTML); });
        lnc.count = sheet.children.length;
      }
      lnc.paint();
    },
    setSpell: function (words) { lnc.marks.spell = new Set(JSON.parse(words)); lnc.paint(); },
    showChanges: function (on) { lnc.marks.show = on; lnc.paint(); },
    edited: function (b) { return lnc.marks.changed.has(b) || !lnc.orig || !lnc.orig.has(b) || lnc.orig.get(b) !== b.innerHTML; },
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
      Array.prototype.forEach.call(sheet.children, function (b, i) {
        if (b.classList.contains('lnc-obj')) return;
        if (m.show && lnc.edited(b)) { var r = document.createRange(); r.selectNodeContents(b); change.add(r); }
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
    takeText: function () { var t = lnc.dirty ? sheet.innerText : ''; lnc.dirty = false; return t; },
    dump: function () {  // [{tag, attrs, html, token}] de cada párrafo, en orden
      return JSON.stringify(Array.prototype.map.call(sheet.children, function (b) {
        var attrs = {};
        Array.prototype.forEach.call(b.attributes, function (a) { attrs[a.name] = a.value; });
        return {tag: b.tagName.toLowerCase(), attrs: attrs, html: b.innerHTML, token: b.getAttribute('data-token') || ''};
      }));
    }
  };
  lnc.rehome();
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


def lines_of(dumped):
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
    return editor.from_code("\n".join(code))


def sources(book, start, end, lines, pictures):
    """Lo que la hoja web necesita para dibujar el capítulo start..end del libro: (archivos del zip, ruta de la
    página, su <head>, atributos de su <body>). Con un EPUB de entrada, los del original (su CSS, sus fuentes, sus
    imágenes) y la página en el sitio de su archivo; si no, la hoja de estilo de la app."""
    files, objects = {}, {}
    origin = book.origin
    first = next((b.src[0] for b in book.blocks[start:end] if b.src), None) if origin else None
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
        elif token:  # separador: su texto
            blockhtml = book.blocks[int(token[2])].html if 0 <= int(token[2]) < len(book.blocks) else ""
            objects[bare] = (None, (convert.TAGS.sub("", html.unescape(blockhtml)).strip() or "* * *").encode())
        elif convert.PICTURE.fullmatch(bare) and bare in pictures:
            name = posixpath.join(folder, f"lnc-obj/{convert.PICTURE.fullmatch(bare)[2]}")
            files[name], objects[bare] = pictures[bare], (name, pictures[bare])
    return files, objects, path, head, body_attrs
