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
  sheet.addEventListener('input', function () {
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
    if (e.ctrlKey && !e.altKey && !e.shiftKey && keys[e.keyCode]) { e.preventDefault(); document.execCommand(keys[e.keyCode]); }
    else if (e.ctrlKey && !e.altKey && e.keyCode === 69) { e.preventDefault(); lnc.align(lnc.state().align === 'center' ? null : 'center'); }
  });
  window.lnc = {
    state: function () {
      var b = here(), align = b ? getComputedStyle(b).textAlign : '';
      return JSON.stringify({bold: document.queryCommandState('bold'), italic: document.queryCommandState('italic'),
              underline: document.queryCommandState('underline'), strike: document.queryCommandState('strikeThrough'),
              sup: document.queryCommandState('superscript'), sub: document.queryCommandState('subscript'),
              tag: b ? b.tagName.toLowerCase() : '', align: align === 'start' ? 'left' : align,
              indent: b ? getComputedStyle(b).textIndent : ''});  // un objeto no llega a Python: JSON
    },
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
    dump: function () {  // [{tag, attrs, html, token}] de cada párrafo, en orden
      return JSON.stringify(Array.prototype.map.call(sheet.children, function (b) {
        var attrs = {};
        Array.prototype.forEach.call(b.attributes, function (a) { attrs[a.name] = a.value; });
        return {tag: b.tagName.toLowerCase(), attrs: attrs, html: b.innerHTML, token: b.getAttribute('data-token') || ''};
      }));
    }
  };
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
