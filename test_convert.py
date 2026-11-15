"""Pruebas de convert.py con PDFs sintéticos. Ejecutar: python test_convert.py"""
import html
import os
import re
import sys
import tempfile
import traceback
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pymupdf

import convert

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # las ventanas Qt de las pruebas no salen en pantalla

HEADER = "Mi Novela - Volumen 1"
FOOTER = "Grupo Traductor"
TMP = None  # carpeta temporal; se crea al ejecutar las pruebas, no al importar en procesos hijos


def line(text, width):
    """Rellena text hasta width caracteres: en Courier todas las líneas llenas acaban en el mismo x."""
    return (text + " relleno" * 20)[:width]


def add_page(doc, number, lines):
    """lines: [(x, y, texto, tamaño, fuente)]. Añade encabezado y pie como haría Word."""
    p = doc.new_page(width=595, height=842)
    p.insert_text((72, 40), HEADER, fontsize=10, fontname="hebo")
    p.insert_text((72, 815), f"{FOOTER}   {number}", fontsize=10, fontname="helv")
    for x, y, text, size, font in lines:
        p.insert_text((x, y), text, fontsize=size, fontname=font)
    return p


def make_novel():
    doc = pymupdf.open()
    # 1: índice del PDF (no debe generar capítulos)
    add_page(doc, 1, [
        (72, 100, "Contenido", 20, "hebo"),
        (72, 140, "Capitulo 1: Inicio .......... 3", 11, "helv"),
        (72, 160, "Capitulo 2: Final .......... 4", 11, "helv"),
        (72, 180, "Parte especial .......... 5", 11, "helv"),
    ])
    # 2: ilustración a página completa
    p = add_page(doc, 2, [])
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 85), False)
    pix.clear_with(180)
    p.insert_image(p.rect, pixmap=pix, keep_proportion=False)
    # 3: capítulo 1
    add_page(doc, 3, [
        (72, 100, "Capitulo 1: Inicio", 20, "helv"),
        (92, 140, line("Primera linea del primer parrafo", 57), 11, "cour"),
        (72, 154, line("Segunda linea del primer parrafo", 60), 11, "cour"),
        (72, 168, "fin del primer parrafo.", 11, "cour"),
        (280, 196, "* * *", 11, "helv"),
        (92, 224, "Ella penso ", 11, "cour"),        # dos spans en la misma línea base,
        (164.6, 224, "que era tarde.", 11, "coit"),  # el segundo en cursiva
        (92, 252, line("Parte de mi queria quedarse en casa", 57), 11, "cour"),
        (72, 266, line("Tom & Jerry <3 en la linea siguiente", 60), 11, "cour"),
        (72, 280, line("y la historia sigue en la", 60), 11, "cour"),
    ])
    # 4: continuación del párrafo y capítulo 2
    add_page(doc, 4, [
        (72, 100, "pagina siguiente sin problemas.", 11, "cour"),
        (72, 150, "Capitulo 2: Final", 20, "helv"),
        (92, 190, "Fin.", 11, "cour"),
        (92, 218, "Dijo ", 11, "cour"),          # cursiva pegada a la coma: sin espacio entre ellas
        (125, 218, "hola", 11, "coit"),
        (151.4, 218, ", y se fue.", 11, "cour"),
        (92, 246, "Palabra", 11, "cour"),        # palabras en spans separados por un hueco visible
        (160, 246, "separada.", 11, "cour"),
    ])
    # 5: guion de partición dentro de una cursiva
    add_page(doc, 5, [
        (92, 142, (("relleno " * 10) + "con una ")[-52:], 11, "cour"),
        (435.2, 142, "pala-", 11, "coit"),                # guion dentro de una cursiva
        (72, 156, "bra", 11, "coit"),
        (91.8, 156, " final.", 11, "cour"),
    ])
    path = TMP / "novela.pdf"
    doc.save(path)
    return path


def plain(html_text):
    return re.sub(r"<[^>]+>", "", html_text)


def test_analyze():
    book = convert.analyze(make_novel())
    texts = [plain(b.html) for b in book.blocks]
    everything = " ".join(texts)
    assert HEADER not in everything and FOOTER not in everything, everything
    titles = [plain(book.blocks[i].html) for i in book.candidates]
    assert titles == ["Capitulo 1: Inicio", "Capitulo 2: Final"], titles
    assert book.blocks[0].kind == "image" and book.blocks[0].image[:3] == b"\xff\xd8\xff"
    first = [t for t in texts if t.startswith("Primera linea")]
    assert len(first) == 1 and first[0].endswith("fin del primer parrafo."), texts
    assert any(b.kind == "break" for b in book.blocks)
    ella = [b.html for b in book.blocks if "Ella penso" in b.html]
    assert len(ella) == 1 and "<i>que era tarde.</i>" in ella[0], ella
    parte = [t for t in texts if t.startswith("Parte de mi")]
    assert len(parte) == 1 and parte[0].endswith("pagina siguiente sin problemas."), texts
    assert any("Tom &amp; Jerry &lt;3" in b.html for b in book.blocks)
    assert any(b.html == "Dijo <i>hola</i>, y se fue." for b in book.blocks), [b.html for b in book.blocks[-3:]]
    assert "Palabra separada." in texts, texts[-3:]
    assert any(t.endswith("con una palabra final.") for t in texts), texts[-2:]
    assert book.title == "novela" and book.cover[:3] == b"\xff\xd8\xff"


def test_soft_hyphen_at_line_end_joins_word():
    """Algunos PDF entregan el guion de partición como U+00AD: al final de línea une la palabra."""
    def span(text):
        return {"text": text, "flags": 0, "font": "Courier", "size": 11.0,
                "origin": (72.0, 100.0), "bbox": (72.0, 90.0, 72 + 6.6 * len(text), 102.0)}

    first = convert._row([span("una pala­")])
    second = convert._row([span("bra in­visible.")])
    assert convert._join(first.html, second.html) == "una palabra invisible."


def test_series_and_volume_from_file_name():
    cases = [
        ("Maryoku Cheat na Majo ni Narimashita - Volumen 01 [WP]", "", ("Maryoku Cheat na Majo ni Narimashita", "1")),
        ("Nageki no Bourei wa Intai Shitai - Volumen 1", "", ("Nageki no Bourei wa Intai Shitai", "1")),
        ("Volumen 1 World Project", "Am I Actually the Strongest?", ("Am I Actually the Strongest?", "1")),
        ("Volumen 7 World Project", "KAGE NO JITSURYOKUSHA NI NARITAKUTE!", ("KAGE NO JITSURYOKUSHA NI NARITAKUTE!", "7")),
        ("Mi Serie Vol. 2.5", "", ("Mi Serie", "2.5")),
        ("Otra serie - Tomo 3", "", ("Otra serie", "3")),
        ("Una novela suelta", "Una novela suelta", ("", "")),
    ]
    for stem, meta, expected in cases:
        assert convert._series_volume(stem, meta) == expected, (stem, convert._series_volume(stem, meta))


def test_analyze_titles_volume_after_series():
    """Si el título del PDF no dice el volumen, el título del libro pasa a "Serie - Volumen N"."""
    path = TMP / "Mi Serie - Volumen 03 [WP].pdf"
    make_novel().replace(path)
    book = convert.analyze(path)
    assert (book.series, book.volume, book.title) == ("Mi Serie", "3", "Mi Serie - Volumen 3"), (
        book.series, book.volume, book.title)


def test_epub_has_series_metadata():
    ns = {"o": "http://www.idpf.org/2007/opf"}
    book = convert.analyze(make_novel())
    book.series, book.volume = "Mi Serie", "3"
    out = TMP / "serie.epub"
    convert.write_epub(book, [], out)
    with zipfile.ZipFile(out) as z:
        opf = ET.fromstring(z.read("OEBPS/content.opf"))
    metas = opf.findall("o:metadata/o:meta", ns)
    collection = [m for m in metas if m.get("property") == "belongs-to-collection"]
    assert len(collection) == 1 and collection[0].text == "Mi Serie", [(m.attrib, m.text) for m in metas]
    refined = {m.get("property"): m.text for m in metas if m.get("refines") == "#" + collection[0].get("id")}
    assert refined == {"collection-type": "series", "group-position": "3"}, refined
    calibre = {m.get("name"): m.get("content") for m in metas if m.get("name", "").startswith("calibre:")}
    assert calibre == {"calibre:series": "Mi Serie", "calibre:series_index": "3"}, calibre
    book.series = ""
    convert.write_epub(book, [], out)
    with zipfile.ZipFile(out) as z:
        assert b"belongs-to-collection" not in z.read("OEBPS/content.opf")  # sin serie, nada


def test_counted_uses_singular_for_one():
    """«1 capítulo detectado», no «1 capítulos detectados»."""
    assert convert.counted(1, "capítulo detectado", "capítulos detectados") == "1 capítulo detectado"
    assert convert.counted(3, "capítulo detectado", "capítulos detectados") == "3 capítulos detectados"
    assert (convert.counted(0, "aviso"), convert.counted(1, "vez", "veces")) == ("0 avisos", "1 vez")


def test_typography_rae_style():
    cases = [
        ('Dijo "hola" y se fue...', "Dijo «hola» y se fue…"),
        ('“Hola" , dijo', "«Hola», dijo"),                      # comillas mezcladas y espacio antes de coma
        ('<i>"Hola</i>"', "<i>«Hola</i>»"),                     # la posición se mira a través de etiquetas
        ('"Ella dijo "ven" ayer"', "«Ella dijo “ven” ayer»"),   # comillas dentro de comillas
        ("Tom's 'casa'", "Tom’s ‘casa’"),                       # apóstrofo y comillas simples
        ("Espera─dijo  él .", "Espera—dijo él."),               # raya de caja, espacio doble y antes de punto
        ('"...Bueno"', "«…Bueno»"),
        ("«Ya» lo sé", "«Ya» lo sé"),                           # ya correcto: no cambia
        ("“Hola.”“Adiós.”", "«Hola.»«Adiós.»"),                 # cierre y apertura pegados
        ("Tom &amp; Jerry &lt;3", "Tom &amp; Jerry &lt;3"),     # no toca entidades
    ]
    for source, expected in cases:
        assert convert._typography(source) == expected, (source, convert._typography(source))


def test_write_epub_applies_typography_only_when_asked():
    book = convert.Book("Libro", "", b"", [convert.Block("heading", 1, "Cap 1"),
                                           convert.Block("p", 1, 'Dijo "hola"...')], [0])
    out = TMP / "tipografia.epub"
    convert.write_epub(book, [(0, "Cap 1")], out, typography=True)
    assert "Dijo «hola»…" in epub_text(out)
    convert.write_epub(book, [(0, "Cap 1")], out)
    assert 'Dijo "hola"...' in epub_text(out)


def styled_book():
    blocks = [convert.Block("heading", 1, "Capítulo 3: La Guarida"), convert.Block("p", 1, "Uno."),
              convert.Block("break", 1), convert.Block("p", 1, "Dos."),
              convert.Block("heading", 2, "Epílogo"), convert.Block("p", 2, "Fin.")]
    return convert.Book("Libro", "", JPEG, blocks, [0, 4])


PAGE_MARK = re.compile(r'<span epub:type="pagebreak"[^>]*></span>')


def epub_file(path, name, marks=False):
    """Un archivo del EPUB; sin las marcas de página, salvo que la prueba sea de ellas."""
    with zipfile.ZipFile(path) as z:
        text = z.read(name).decode()
    return text if marks else PAGE_MARK.sub("", text)


def test_epub_styles_and_print_typography():
    signatures = {"Clásico": ("serif", "justify"), "Moderno": ("sans-serif",), "Novela ligera": ("small-caps",)}
    for style, marks in signatures.items():
        out = TMP / f"estilo-{style}.epub"
        convert.write_epub(styled_book(), [(0, "Capítulo 3: La Guarida"), (4, "Epílogo")], out, style=style)
        css, text = epub_file(out, "OEBPS/style.css"), epub_text(out)
        assert all(m in css for m in marks), (style, css)
        for rule in ("hyphens: auto", "orphans", "widows", "h1 + p"):  # tipografía de libro, en todos
            assert rule in css, (style, rule)
        separator = "❖" if style == "Novela ligera" else "* * *"
        assert f'<p class="brk">{separator}</p>' in text, (style, separator)


def test_novela_ligera_chapter_name_is_not_oversized():
    """El nombre del capítulo no pasa de 1,5 veces el texto normal; los nombres largos, menos aún."""
    css = convert.STYLES["Novela ligera"]
    h1 = float(re.search(r"h1 \{[^}]*font-size: ([\d.]+)em", css).group(1))
    name = float(re.search(r"h1 \.name \{[^}]*font-size: ([\d.]+)em", css).group(1))
    long_name = float(re.search(r"h1 \.name\.long \{[^}]*font-size: ([\d.]+)em", css).group(1))
    assert h1 * name <= 1.5 and h1 * long_name < h1 * name, (h1, name, long_name)
    long_title = "Capítulo 2: Una Mañana Impactante, ¡El Caso de Asesinato por Empalamiento!"
    assert 'class="name long"' in convert._chapter_heading(long_title)
    assert 'class="name"' in convert._chapter_heading("Capítulo 3: La Guarida")


def test_joined_title_rectangle_covers_the_name():
    """El recuadro del título unido ("» Capitulo 1 «" + nombre debajo) abarca también el nombre."""
    doc = pymupdf.open()
    chapter_page(doc, 1, [(230, 120, "» Capitulo 1 «", 20, "hebo"), (200, 140, "¡El regreso!", 12, "hebo")])
    path = TMP / "recuadro-unido.pdf"
    doc.save(path)
    book = convert.analyze(path)
    x0, y0, x1, y1 = book.blocks[book.candidates[0]].rect
    assert y0 < 120 and y1 > 140, book.blocks[book.candidates[0]].rect


def test_chapter_title_split_into_number_and_name():
    out = TMP / "titulo-partido.epub"
    convert.write_epub(styled_book(), [(0, "Capítulo 3: La Guarida"), (4, "Epílogo")], out)
    text = epub_text(out)
    assert '<h1><span class="num">Capítulo 3</span> <span class="name">La Guarida</span></h1>' in text, text
    assert "<h1>Epílogo</h1>" in text  # sin número ni nombre: una sola línea
    assert "Capítulo 3: La Guarida" in epub_file(out, "OEBPS/nav.xhtml")  # el índice conserva el título entero


def test_epub_landmarks_guide_and_contents_page():
    """Puntos de referencia del lector (portada, índice, inicio de la lectura), <guide> para Kindle y
    una página "Contenido" tras la portada con enlaces a capítulos y subtítulos."""
    ops, opf_ns = "{http://www.idpf.org/2007/ops}type", {"o": "http://www.idpf.org/2007/opf"}
    book = convert.analyze(make_novel())  # tiene "Inicio" (ilustración) antes del primer capítulo
    out = TMP / "navegacion.epub"
    convert.write_epub(book, [(book.candidates[0], "Capitulo 1: Inicio")], out)
    with zipfile.ZipFile(out) as z:
        nav = ET.fromstring(z.read("OEBPS/nav.xhtml"))
        opf = ET.fromstring(z.read("OEBPS/content.opf"))
        contents = z.read("OEBPS/contents.xhtml").decode()
        first_chapter = next(n for n in sorted(z.namelist()) if re.fullmatch(r"OEBPS/sec\d+\.xhtml", n)
                             and "<h1>" in z.read(n).decode().replace('<h1><span', "<h1>"))
    landmarks = next(e for e in nav.iter() if e.get(ops) == "landmarks")
    marks = {a.get(ops): a.get("href") for a in landmarks.iter() if a.get("href")}
    assert marks == {"cover": "cover.xhtml", "toc": "contents.xhtml",
                     "bodymatter": first_chapter.split("/")[-1]}, marks  # el primer capítulo, no "Inicio"
    guide = {r.get("type"): r.get("href") for r in opf.findall("o:guide/o:reference", opf_ns)}
    assert guide == {"cover": "cover.xhtml", "toc": "contents.xhtml", "text": marks["bodymatter"]}, guide
    spine = [r.get("idref") for r in opf.findall("o:spine/o:itemref", opf_ns)]
    assert spine[:2] == ["cover", "contents"], spine
    assert 'href="' + marks["bodymatter"] + '"' in contents and "Capitulo 2: Final" in contents
    assert re.search(r'href="sec\d+\.xhtml#t\d+"', contents), contents  # subtítulos enlazados


def test_scanned_pdf_is_rejected():
    doc = pymupdf.open()
    p = doc.new_page()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 10, 10), False)
    p.insert_image(p.rect, pixmap=pix)
    path = TMP / "escaneado.pdf"
    doc.save(path)
    try:
        convert.analyze(path)
    except ValueError as e:
        assert "OCR" in str(e)
    else:
        raise AssertionError("debía lanzar ValueError")


def test_write_epub():
    book = convert.analyze(make_novel())
    chapters = [(i, plain(book.blocks[i].html)) for i in book.candidates]
    out = TMP / "novela.epub"
    convert.write_epub(book, chapters, out)
    with zipfile.ZipFile(out) as z:
        first = z.infolist()[0]
        assert first.filename == "mimetype" and first.compress_type == zipfile.ZIP_STORED
        assert z.read("mimetype") == b"application/epub+zip"
        names = z.namelist()
        assert "OEBPS/nav.xhtml" in names and "OEBPS/cover.jpg" in names
        sections = sorted(n for n in names if re.fullmatch(r"OEBPS/sec\d+\.xhtml", n))
        assert len(sections) == 3, sections  # Inicio (ilustración) + 2 capítulos
        for name in names:
            if name.endswith((".xhtml", ".opf", ".ncx", ".xml")):
                ET.fromstring(z.read(name))  # XML bien formado
        assert "Capitulo 1: Inicio" in z.read("OEBPS/nav.xhtml").decode()
        assert '<span class="num">Capitulo 2</span> <span class="name">Final</span>' in z.read(sections[2]).decode()
    assert not Path(f"{out}.tmp").exists()


def test_pdf_without_novel_text_is_rejected():
    doc = pymupdf.open()
    add_page(doc, 1, [
        (72, 150, "Contenido", 20, "hebo"),
        (72, 190, "Capitulo 1: Inicio .......... 3", 11, "helv"),
    ])
    add_page(doc, 2, [(72, 150, "Contenido", 20, "hebo")])
    path = TMP / "solo-indice.pdf"
    doc.save(path)
    try:
        convert.analyze(path)
    except ValueError as e:
        assert "texto de la novela" in str(e), e
    else:
        raise AssertionError("debía lanzar ValueError: un EPUB sin texto no es válido")


def make_covers():
    """Página 1: ilustración que tapa el título, repetido en la 2. Página 3: fondo decorativo bajo un título."""
    doc = pymupdf.open()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 85), False)
    pix.clear_with(20)
    p = add_page(doc, 1, [(150, 150, "Capitulo 1: Inicio!", 16, "hebo")])  # la repetición puede perder el "!"
    p.insert_image(p.rect, pixmap=pix, keep_proportion=False)  # la ilustración tapa el título
    add_page(doc, 2, [
        (150, 150, "Capitulo 1: Inicio", 16, "hebo"),
        (92, 190, line("Primera linea del capitulo", 57), 11, "cour"),
        (72, 204, line("Segunda linea del capitulo", 60), 11, "cour"),
        (72, 218, "fin.", 11, "cour"),
    ])
    p = add_page(doc, 3, [(150, 400, "Capitulo 2: Final", 16, "hebo")])
    deco = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 85), False)
    deco.clear_with(120)  # otra imagen: si fuera la misma, saldría como ilustración repetida
    p.insert_image(p.rect, pixmap=deco, keep_proportion=False, overlay=False)  # fondo decorativo
    add_page(doc, 4, [
        (92, 150, line("Texto del segundo capitulo", 57), 11, "cour"),
        (72, 164, line("que sigue en otra linea", 60), 11, "cour"),
        (72, 178, "fin.", 11, "cour"),
    ])
    path = TMP / "portadas.pdf"
    doc.save(path)
    return path


def test_chapter_art_with_hidden_title_is_kept_once():
    """Portada de capítulo: ilustración que tapa el título (texto oculto) y título repetido en la página siguiente.
    Debe quedar el título una vez, seguido de la ilustración. Sin repetición, es un fondo decorativo:
    queda apuntado como descartado, por si se quiere recuperar."""
    book = convert.analyze(make_covers())
    titles = [plain(book.blocks[i].html) for i in book.candidates]
    assert titles == ["Capitulo 1: Inicio!", "Capitulo 2: Final"], titles
    kinds = [(b.kind, b.page, b.keep) for b in book.blocks]
    assert kinds[:3] == [("heading", 1, True), ("image", 1, True), ("p", 2, True)], kinds
    assert [k for k in kinds if k[:2] == ("image", 3)] == [("image", 3, False)], kinds


def test_inline_image_is_noted_as_discarded():
    """Una imagen mediana dentro del texto queda descartada (se puede recuperar); un fondo repetido, no aparece."""
    doc = pymupdf.open()
    art = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 30), False)
    art.clear_with(60)
    bar = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 50, 4), False)
    bar.clear_with(30)
    body = [(92, 150, line("Texto de la pagina", 57), 11, "cour"), (72, 164, "fin.", 11, "cour")]
    xref = 0
    for n in (1, 2, 3):
        p = add_page(doc, n, body)
        if xref:
            p.insert_image(pymupdf.Rect(0, 0, 595, 30), xref=xref)  # barra repetida en todas
        else:
            p.insert_image(pymupdf.Rect(0, 0, 595, 30), pixmap=bar)
            xref = p.get_images()[0][0]
        if n == 2:
            p.insert_image(pymupdf.Rect(150, 300, 450, 525), pixmap=art)  # ~13 % de la página
    path = TMP / "imagen-en-texto.pdf"
    doc.save(path)
    images = [(b.page, b.keep) for b in convert.analyze(path).blocks if b.kind == "image"]
    assert images == [(2, False)], images


def test_transparent_image_keeps_its_transparency():
    """Un logo con transparencia (color negro y la forma en el canal alfa) no debe salir como un
    rectángulo negro: se guarda en PNG con su transparencia, y el EPUB lo declara como PNG."""
    doc = pymupdf.open()
    logo = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 30), True)
    logo.clear_with(0)  # negro y del todo transparente
    for x in range(10, 30):
        logo.set_pixel(x, 15, (0, 0, 0, 255))  # una raya opaca: la forma del logo
    add_page(doc, 1, [(92, 150, line("Texto de la pagina", 57), 11, "cour"), (72, 164, "fin.", 11, "cour")])
    doc[0].insert_image(pymupdf.Rect(150, 300, 450, 525), pixmap=logo)
    path = TMP / "logo-transparente.pdf"
    doc.save(path)
    book = convert.analyze(path)
    image = next(b for b in book.blocks if b.kind == "image")
    picture = pymupdf.Pixmap(image.image)
    assert image.image[:8] == b"\x89PNG\r\n\x1a\n" and picture.alpha, "se perdió la transparencia"
    image.keep = True
    out = TMP / "logo-transparente.epub"
    convert.write_epub(book, [], out)
    with zipfile.ZipFile(out) as z:
        names = [n for n in z.namelist() if n.startswith("OEBPS/img/")]
        opf = z.read("OEBPS/content.opf").decode()
    assert names and names[0].endswith(".png"), names
    assert f'href="{names[0][6:]}" media-type="image/png"' in opf, opf


def test_mask_wins_over_opaque_alpha():
    """PyMuPDF a veces entrega la imagen con un canal alfa opaco y la forma en una máscara aparte
    (el logo de Kage Vol 06): manda la máscara, o el logo sale como un rectángulo negro."""
    rect = pymupdf.IRect(0, 0, 20, 10)
    image = pymupdf.Pixmap(pymupdf.csRGB, rect, True)
    image.clear_with(0)
    for x in range(20):
        for y in range(10):
            image.set_pixel(x, y, (0, 0, 0, 255))  # negro y opaco: la forma no está aquí
    mask = pymupdf.Pixmap(pymupdf.csGRAY, rect, False)
    mask.clear_with(0)  # transparente…
    for x in range(5, 15):
        mask.set_pixel(x, 5, (255,))  # …salvo una raya: la forma del logo
    result = convert._with_mask(image, mask)
    assert result.alpha and result.pixel(0, 0)[3] == 0 and result.pixel(10, 5)[3] == 255, (
        result.pixel(0, 0), result.pixel(10, 5))


def make_ornament_pdf():
    """Página 1: adorno centrado entre dos párrafos. Página 2: la misma imagen pegada al margen."""
    doc = pymupdf.open()
    ornament = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 10, 16), False)
    ornament.clear_with(40)
    lines = [(92, 150, line("Primer parrafo de la escena", 57), 11, "cour"), (72, 164, "fin uno.", 11, "cour"),
             (92, 240, line("Segundo parrafo tras el adorno", 57), 11, "cour"), (72, 254, "fin dos.", 11, "cour")]
    add_page(doc, 1, [(72, 110, "Capitulo 1: Inicio", 20, "helv")] + lines)
    doc[0].insert_image(pymupdf.Rect(290, 190, 305, 214), pixmap=ornament)  # centrado entre los dos
    add_page(doc, 2, lines)
    doc[1].insert_image(pymupdf.Rect(40, 190, 55, 214), pixmap=ornament)  # pegado al margen
    path = TMP / "adorno.pdf"
    doc.save(path)
    return path


def test_ornament_image_between_paragraphs_is_a_scene_break():
    """Un adorno pequeño y centrado entre dos párrafos (Kage, Nageki) es un separador de escena;
    una imagen pequeña pegada al margen, no."""
    blocks = convert.analyze(make_ornament_pdf()).blocks
    page1 = [b.kind for b in blocks if b.page == 1]
    page2 = [b.kind for b in blocks if b.page == 2]
    assert page1 == ["heading", "p", "break", "p"], page1
    assert "break" not in page2, page2


def test_drawn_ornament_between_paragraphs_is_a_scene_break():
    """Eminence 7: el separador de escena va dibujado con trazos (dos barras y una raya cruzada), sin imagen ni
    texto. Es un separador y guarda su dibujo; el subrayado ancho bajo el título y el fondo de página, no."""
    doc = pymupdf.open()
    page = add_page(doc, 1, [(72, 110, "Capitulo 1: Inicio", 20, "helv"),
                             (92, 150, line("Primer parrafo de la escena", 57), 11, "cour"), (72, 164, "fin uno.", 11, "cour"),
                             (92, 250, line("Segundo parrafo de la escena", 57), 11, "cour"), (72, 264, "fin dos.", 11, "cour")])
    page.draw_rect(pymupdf.Rect(0, 0, 595, 842), color=None, fill=(1, 1, 1), overlay=False)  # fondo de página
    page.draw_line((72, 116), (400, 116))  # subrayado del título
    page.draw_rect(pymupdf.Rect(292, 185, 298, 220), color=None, fill=(0, 0, 0))  # las dos barras
    page.draw_rect(pymupdf.Rect(297, 200, 303, 235), color=None, fill=(0, 0, 0))
    page.draw_line((285, 225), (310, 195))  # la raya cruzada
    # 2: el adorno cierra la página, sin texto debajo (pág. 58): la escena sigue en la 3
    page = add_page(doc, 2, [(92, 150, line("Tercer parrafo de la escena", 57), 11, "cour"), (72, 164, "fin tres.", 11, "cour")])
    page.draw_rect(pymupdf.Rect(292, 600, 298, 635), color=None, fill=(0, 0, 0))
    page.draw_rect(pymupdf.Rect(297, 615, 303, 650), color=None, fill=(0, 0, 0))
    # 3: raya corta justo bajo un título («Afterword», pág. 246): subrayado, no separador
    page = add_page(doc, 3, [(250, 110, "Epilogo", 20, "helv"),
                             (92, 150, line("Cuarto parrafo de otra escena", 57), 11, "cour"), (72, 164, "fin cuatro.", 11, "cour")])
    page.draw_line((250, 120), (345, 120))
    # 4: enlace centrado y subrayado entre dos párrafos (créditos de Re Zero 6): la raya es del enlace
    page = add_page(doc, 4, [(92, 150, line("Quinto parrafo de otra escena", 57), 11, "cour"), (72, 164, "fin.", 11, "cour"),
                             (230, 300, "http://grupo.net/", 11, "helv"),
                             (92, 400, line("Sexto parrafo de otra escena", 57), 11, "cour"), (72, 414, "fin.", 11, "cour")])
    page.draw_line((230, 303), (325, 303))
    path = TMP / "adorno-dibujado.pdf"
    doc.save(path)
    blocks = convert.analyze(path).blocks
    kinds = [[b.kind for b in blocks if b.page == n] for n in (1, 2, 3, 4)]
    image = next((b.image for b in blocks if b.kind == "break"), b"")
    assert kinds == [["heading", "p", "break", "p"], ["p", "break"], ["heading", "p"], ["p", "p", "p"]], kinds
    assert image[:4] == b"\x89PNG", image[:8]


def test_wide_colored_title_image_is_not_a_scene_break():
    """Mitsuha 4, pág. 190: «Ilustrador», un título dorado hecho imagen (ancho, 4:1) entre dos párrafos de los
    créditos, salía como separador. Ancho y de color es un título; una floritura ancha en negro sí es un adorno."""
    def picture(color):
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 160, 40), False)
        pix.set_rect(pix.irect, (255, 255, 255))
        pix.set_rect(pymupdf.IRect(10, 10, 150, 30), color)
        return pix.tobytes("png")

    doc = pymupdf.open()
    for n, color in ((1, (230, 170, 20)), (2, (20, 20, 20))):
        page = add_page(doc, n, [(92, 150, line("Un parrafo antes de la imagen", 57), 11, "cour"), (72, 164, "fin.", 11, "cour"),
                                 (92, 250, line("Un parrafo despues de la imagen", 57), 11, "cour"), (72, 264, "fin.", 11, "cour")])
        page.insert_image(pymupdf.Rect(237, 190, 357, 220), stream=picture(color))
    path = TMP / "titulo-imagen.pdf"
    doc.save(path)
    blocks = convert.analyze(path).blocks
    kinds = [[b.kind for b in blocks if b.page == n and b.keep] for n in (1, 2)]
    assert kinds == [["p", "p"], ["p", "break", "p"]], kinds


def test_inline_image_in_a_text_line_is_not_a_scene_break():
    """Una imagen pequeña y centrada dentro de una línea de texto («LEVEL UP! Lv. 2 → Lv. 3», con la flecha como
    imagen: The World's Strongest Witch), ni la pegada al final de una línea corta (♡, ♪), es un separador de
    escena: va en la línea, no entre párrafos."""
    doc = pymupdf.open()
    arrow = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 42, 33), False)
    arrow.clear_with(40)
    add_page(doc, 1, [(72, 110, "Capitulo 1: Inicio", 20, "helv"),
                      (92, 150, line("Primer parrafo de la escena", 57), 11, "cour"), (72, 164, "fin uno.", 11, "cour"),
                      (230, 200, "LEVEL UP! Lv. 2", 11, "helv"), (310, 200, "Lv. 3", 11, "helv"),
                      (92, 240, line("Segundo parrafo tras la linea", 57), 11, "cour"), (72, 254, "fin dos.", 11, "cour"),
                      (190, 290, "Hmph. No he perdido.", 11, "helv"),
                      (92, 330, line("Tercer parrafo tras el corazon", 57), 11, "cour"), (72, 344, "fin tres.", 11, "cour")])
    doc[0].insert_image(pymupdf.Rect(296, 191.4, 307, 200), pixmap=arrow)  # en la línea, entre «Lv. 2» y «Lv. 3»
    doc[0].insert_image(pymupdf.Rect(300, 280.4, 309, 290), pixmap=arrow)  # ♡ pegado al final de una línea corta
    # Kage 5, pág. 99: adorno de verdad con la línea siguiente pegada debajo (base a una letra de su borde)
    add_page(doc, 2, [(92, 150, line("Fin de la escena anterior", 57), 11, "cour"), (72, 164, "fin uno.", 11, "cour"),
                      (92, 240.5, line("Nueva escena justo debajo", 57), 11, "cour"), (72, 254.5, "fin dos.", 11, "cour")])
    doc[1].insert_image(pymupdf.Rect(283, 182, 311, 230), pixmap=arrow)
    path = TMP / "flecha-en-linea.pdf"
    doc.save(path)
    blocks = convert.analyze(path).blocks
    assert "break" not in [b.kind for b in blocks if b.page == 1], [b.kind for b in blocks]
    assert [b.kind for b in blocks if b.page == 2] == ["p", "break", "p"], [b.kind for b in blocks]


def test_unknown_inline_icon_is_kept_as_image():
    """Un icono en imagen dentro de la línea que no se parece a ningún símbolo de GLYPHS (dos puntos, como unos
    ojos) se perdía sin aviso. Va en el párrafo como imagen pequeña, sobrevive al editor, a edit_section y a los
    reemplazos, sale en el EPUB como archivo y el informe de calidad lo avisa."""
    import qtk
    from PySide6.QtGui import QTextDocument

    import editor

    icon = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 40), False)
    icon.set_rect(icon.irect, (255, 255, 255))
    for cell in ((4, 16, 12, 24), (28, 16, 36, 24)):
        icon.set_rect(pymupdf.IRect(*cell), (20, 20, 20))
    doc = pymupdf.open()
    add_page(doc, 1, [(72, 110, "Capitulo 1: Inicio", 20, "helv"),
                      (92, 150, line("Primer parrafo de la escena", 57), 11, "cour"), (72, 164, "fin uno.", 11, "cour"),
                      (92, 200, "Me miro asi", 11, "cour"), (184, 200, "y se fue.", 11, "cour")])
    doc[0].insert_image(pymupdf.Rect(169, 191, 179, 201), pixmap=icon)  # entre «asi» y «y se fue.»
    path = TMP / "icono-desconocido.pdf"
    doc.save(path)
    book = convert.analyze(path)
    k, b = next((k, b) for k, b in enumerate(book.blocks) if "Me miro" in b.html)
    assert convert.ICON.search(b.html), b.html
    assert convert._plain(b) == "Me miro asi y se fue.", convert._plain(b)
    # Editor de texto: el icono es un objeto de la hoja y vuelve a la misma línea
    qtk.app()
    line_text = convert._line(b, k)
    sheet = QTextDocument()
    editor.load(sheet, [(k, line_text)], {})
    block = sheet.firstBlock()
    assert "￼" in block.text(), block.text()
    assert editor._lines(block) == [line_text], editor._lines(block)
    assert editor.plain_line(line_text) == "Me miro asi ￼ y se fue.", editor.plain_line(line_text)
    # Editado a mano y con un reemplazo que nombra la clase: el icono sigue
    convert.edit_section(book, k, k + 1, line_text.replace("asi", "así"))
    assert convert.ICON.search(book.blocks[k].html) and "así" in book.blocks[k].html, book.blocks[k].html
    for edit in convert.replace_text(book, "icono", "x"):
        convert.edit_section(book, *edit)
    assert convert.ICON.search(book.blocks[k].html), book.blocks[k].html
    issues = [i for i in convert.quality_report(book) if i.kind == "icon"]
    assert [i.block for i in issues] == [k], issues
    out = TMP / "icono-desconocido.epub"
    convert.write_epub(book, [(c, "Capítulo 1") for c in book.candidates], out)
    assert convert.check_epub(out) == [], convert.check_epub(out)
    with zipfile.ZipFile(out) as z:
        names = [n for n in z.namelist() if "/img/icono-" in n]
        text = "".join(z.read(n).decode() for n in z.namelist() if n.endswith(".xhtml"))
    assert len(names) == 1 and names[0].endswith(".png"), names
    assert f'src="{names[0].removeprefix("OEBPS/")}"' in text and "data:" not in text, names
    # Nageki 1, pág. 9: el adorno-separador (unas huellas) queda dentro del bbox, muy alto, de la línea de debajo;
    # está dos letras por encima de su línea base: es el separador, no un icono del párrafo. Cambria da ese bbox;
    # aquí, el span medido
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(pymupdf.Rect(223.4, 451.7, 230.1, 462.9), pixmap=icon)
    spans = [{"text": "Me desperté con el peor de los humores.", "bbox": (110.0, 449.3, 318.6, 516.3),
              "origin": (110.0, 486.7), "size": 12.0, "flags": 0, "font": "Cambria"}]
    convert._inline_glyphs(page, spans)
    assert len(spans) == 1, spans[1:]


def test_ornament_at_page_top_opens_a_scene():
    """Un adorno arriba del todo, sin texto encima (la escena cambia con la página: The World's Strongest Witch
    86 y 144), es un separador. Con un título debajo es la decoración del capítulo, y la misma imagen en el mismo
    sitio de la página anterior es una cabecera; una imagen ancha es un título hecho imagen (Mitsuha), y lo que
    tiene otra imagen debajo, su decoración: nada de eso."""
    doc = pymupdf.open()
    ornament = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 50, 50), False)
    ornament.clear_with(40)
    text = [(92, 150, line("Un parrafo cualquiera de la escena", 57), 11, "cour"), (72, 164, "fin uno.", 11, "cour"),
            (92, 192, line("Otro parrafo de la misma escena", 57), 11, "cour"), (72, 206, "fin dos.", 11, "cour")]
    top = pymupdf.Rect(288, 62, 307, 81)  # dentro del margen de arriba, por encima de todo el texto
    add_page(doc, 1, [(72, 110, "Capitulo 1: Inicio", 20, "helv")] + text)
    add_page(doc, 2, text)  # 2: adorno arriba y texto: separador
    doc[1].insert_image(top, pixmap=ornament)
    add_page(doc, 3, [(72, 110, "Capitulo 2: Otro", 20, "helv")] + text)  # 3: adorno sobre un título
    doc[2].insert_image(top, pixmap=ornament)
    add_page(doc, 4, text)  # 4 y 5: la misma imagen arriba en las dos, una cabecera
    add_page(doc, 5, text)
    doc[3].insert_image(top, pixmap=ornament)
    doc[4].insert_image(top, pixmap=ornament)
    # 6: título de capítulo hecho imagen, ancho (Mitsuha). 7: adorno pequeño sobre esa imagen
    add_page(doc, 6, text)
    doc[5].insert_image(pymupdf.Rect(230, 92, 368, 132), pixmap=ornament, keep_proportion=False)
    add_page(doc, 7, text)
    doc[6].insert_image(pymupdf.Rect(290, 50, 305, 59), pixmap=ornament, keep_proportion=False)
    doc[6].insert_image(pymupdf.Rect(230, 92, 368, 132), pixmap=ornament, keep_proportion=False)
    path = TMP / "adorno-arriba.pdf"
    doc.save(path)
    breaks = [b.page for b in convert.analyze(path).blocks if b.kind == "break"]
    assert breaks == [2], breaks


def test_image_info_matches_pymupdf_xrefs():
    """_image_info guarda las huellas para todo el PDF: da los mismos xref que get_image_info(xrefs=True)."""
    with pymupdf.open(make_ornament_pdf()) as doc:
        digests = {}
        for page in doc:
            ours = [(i["bbox"], i["xref"]) for i in convert._image_info(page, digests)]
            theirs = [(i["bbox"], i["xref"]) for i in doc[page.number].get_image_info(xrefs=True)]
            assert ours == theirs and all(x for _, x in ours), (ours, theirs)


def test_jpeg_encoder_is_fast_and_keeps_the_picture():
    """Las páginas-ilustración se codifican con Pillow: el JPEG de MuPDF tardaba 270–600 ms por página, casi la
    mitad del análisis (Kage 6). La imagen conserva tamaño, color y gris."""
    import time

    color = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 1240, 1754), False)
    color.clear_with(200)
    color.set_rect(pymupdf.IRect(0, 0, 620, 877), (200, 30, 30))
    gray = pymupdf.Pixmap(pymupdf.csGRAY, pymupdf.IRect(0, 0, 40, 30), False)
    gray.clear_with(90)
    convert._jpeg(gray, 85)  # el primer uso carga el codificador
    start = time.process_time()
    data = convert._jpeg(color, 85)
    took = time.process_time() - start
    start = time.process_time()
    color.tobytes("jpg", jpg_quality=85)
    mupdf = time.process_time() - start
    back, back_gray = pymupdf.Pixmap(data), pymupdf.Pixmap(convert._jpeg(gray, 85))
    assert data[:3] == b"\xff\xd8\xff" and (back.width, back.height) == (1240, 1754), (back.width, back.height)
    assert all(abs(a - b) < 8 for a, b in zip(back.pixel(100, 100), (200, 30, 30))), back.pixel(100, 100)
    assert back_gray.n == 1 and abs(back_gray.pixel(5, 5)[0] - 90) < 4, back_gray.pixel(5, 5)
    assert took * 3 < mupdf, f"{took:.3f} s frente a {mupdf:.3f} s de MuPDF"


def make_strips_pdf():
    """Una ilustración cortada en 6 tiras del mismo tamaño en píxeles, de arriba abajo (Re Zero 8, pág. 8)."""
    doc = pymupdf.open()
    page = add_page(doc, 1, [])
    height = page.rect.height / 6
    for n in range(6):
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 10), False)
        pix.clear_with(30 * n + 10)
        page.insert_image(pymupdf.Rect(0, n * height, page.rect.width, (n + 1) * height), pixmap=pix,
                          keep_proportion=False)
    path = TMP / "tiras.pdf"
    doc.save(path)
    return path


def test_image_info_finds_xrefs_without_decoding():
    """Saber qué xref es cada imagen dibujada decodificaba todas las imágenes para su huella MD5: un tercio del
    análisis de Kage 6. Si el tamaño en píxeles no se repite en la página basta con él; si se repite (tiras
    iguales), la huella."""
    for path, decodes in ((make_ornament_pdf(), 0), (make_strips_pdf(), None)):
        with pymupdf.open(path) as doc:
            original, made = pymupdf.Pixmap, []

            class Counted(original):
                def __init__(self, *args):
                    made.append(args)
                    super().__init__(*args)

            pymupdf.Pixmap = Counted
            try:
                digests = {}
                ours = [[(i["bbox"], i["xref"]) for i in convert._image_info(page, digests)] for page in doc]
            finally:
                pymupdf.Pixmap = original
            theirs = [[(tuple(i["bbox"]), i["xref"]) for i in page.get_image_info(xrefs=True)] for page in doc]
        assert ours == theirs, (path.name, ours, theirs)
        assert decodes is None or len(made) == decodes, (path.name, len(made))


def test_rotated_full_page_illustration_is_not_cropped():
    """Re Zero 8: ilustración doble en una página girada 90°. Las imágenes vienen en coordenadas sin
    girar; mezcladas con las de la página girada, el recorte la dejaba en un cuadrado."""
    doc = pymupdf.open()
    add_page(doc, 1, [(72, 100, "Capitulo 1: Inicio", 20, "helv"), (92, 140, "Texto del capitulo.", 11, "cour")])
    p = doc.new_page(width=595, height=842)
    art = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 85), False)
    art.clear_with(180)
    p.insert_image(p.rect, pixmap=art, keep_proportion=False)
    p.set_rotation(90)
    path = TMP / "girada.pdf"
    doc.save(path)
    image = next(b.image for b in convert.analyze(path).blocks if b.kind == "image" and b.page == 2)
    pix = pymupdf.Pixmap(image)
    assert pix.width > pix.height * 1.3, (pix.width, pix.height)  # apaisada entera, no un cuadrado


def test_scene_breaks_keep_their_original_form():
    """Cada separador guarda lo que traía el PDF: el texto de un separador escrito, la imagen de un adorno."""
    text_breaks = [b for b in convert.analyze(make_novel()).blocks if b.kind == "break"]
    ornament_breaks = [b for b in convert.analyze(make_ornament_pdf()).blocks if b.kind == "break"]
    assert [b.html for b in text_breaks] == ["* * *"], [b.html for b in text_breaks]
    assert len(ornament_breaks) == 1 and pymupdf.Pixmap(ornament_breaks[0].image).width == 10


def test_symbol_font_suits_are_scene_breaks_not_chapters():
    """Mitsuha 6 [Bell T]: los separadores «♠♠» van en la fuente Symbol, que PyMuPDF lee como «ªª» (0xAA),
    centrados y en letra de título (35,6 pt). Salían como capítulos «ªª»; son separadores de escena «♠♠»."""
    doc = pymupdf.open()
    for n, title in ((1, "Capitulo 1: Inicio"), (2, "Capitulo 2: Final")):
        add_page(doc, n, [(72, 100, title, 20, "helv"),
                          (92, 140, line("Primera linea del primer parrafo", 57), 11, "cour"),
                          (72, 154, "fin del primer parrafo.", 11, "cour"),
                          (282, 200, "ªª", 35.6, "symb"),
                          (92, 240, line("Primera linea del segundo parrafo", 57), 11, "cour"),
                          (72, 254, "fin del segundo parrafo.", 11, "cour")])
    path = TMP / "simbolos.pdf"
    doc.save(path)
    book = convert.analyze(path)
    titles = [book.blocks[k].html for k in book.candidates]
    breaks = [b.html for b in book.blocks if b.kind == "break"]
    headings = [b.html for b in book.blocks if b.kind == "heading"]
    assert titles == ["Capitulo 1: Inicio", "Capitulo 2: Final"], titles
    assert breaks == ["♠♠", "♠♠"] and "ªª" not in headings, (breaks, headings)


def test_links_and_centered_paragraphs_reach_the_epub():
    """El editor escribe enlaces (<a href="https://…">) y párrafos centrados (<center> delante): llegan a los
    bloques y al EPUB, que check_epub no da por roto, y section_text los devuelve igual. Una edición vieja de
    un párrafo centrado por el PDF (sin <center>) lo sigue reconociendo sin cambios."""
    blocks = [convert.Block("heading", 1, "Cap 1"), convert.Block("p", 1, "Querida Teto:", align="center"),
              convert.Block("p", 1, "Uno.")]
    book = convert.Book("Libro", "", JPEG, blocks, [0])
    shown = convert.section_text(book, 1, 3)
    convert.edit_section(book, 1, 3, "Querida Teto:\n\nUno.")  # revisión vieja: sin <center>
    old_kept = book.blocks[1].align == "center"
    lines = ['Ver <a href="https://example.com/?a=1&b=2">la <i>web</i></a> ahora.', "<center>Fin de la carta.",
             '<a href="javascript:alert(1)">no</a>']
    convert.edit_section(book, 1, 3, "\n\n".join(lines))
    htmls = [(b.html, b.align) for b in book.blocks[1:]]
    again = convert.section_text(book, 1, 4).split("\n\n")
    out = TMP / "enlaces.epub"
    convert.write_epub(book, [(0, "Cap 1")], out)
    text, problems = epub_text(out), convert.check_epub(str(out))
    assert shown == "<center>Querida Teto:\n\nUno.", shown
    assert old_kept
    assert htmls == [('Ver <a href="https://example.com/?a=1&amp;b=2">la <i>web</i></a> ahora.', ""),
                     ("Fin de la carta.", "center"),
                     ('&lt;a href="javascript:alert(1)"&gt;no&lt;/a&gt;', "")], htmls
    assert again == lines, again
    assert 'href="https://example.com/?a=1&amp;b=2"' in text and 'class="center"' in text, text
    assert not problems, problems


def test_superscript_small_caps_and_right_reach_the_epub():
    """El editor escribe superíndice (<sup>), versalitas (<sc>) y párrafos a la derecha (<derecha> delante): llegan
    a los bloques y al EPUB con su CSS, section_text los devuelve igual (también dentro de una nota) y una <sc> sin
    cerrar se quita para no romper el libro."""
    blocks = [convert.Block("heading", 1, "Cap 1"), convert.Block("p", 1, "Uno.")]
    book = convert.Book("Libro", "", JPEG, blocks, [0])
    lines = ["El 1.<sup>er</sup> aventurero de <sc>Rango S</sc>.", "<derecha>Firmado: Rudeus",
             "Entró[Nota: el <sc>gremio</sc> de Rosa] al salón.", "Mal <sc>cerrado."]
    convert.edit_section(book, 1, 2, "\n\n".join(lines))
    htmls = [(b.html, b.align) for b in book.blocks[1:]]
    again = convert.section_text(book, 1, 5).split("\n\n")
    out = TMP / "versalitas.epub"
    convert.write_epub(book, [(0, "Cap 1")], out)
    text, problems = epub_text(out), convert.check_epub(str(out))
    with zipfile.ZipFile(out) as z:
        css = "".join(z.read(n).decode() for n in z.namelist() if n.endswith(".css"))
    assert htmls[0] == ('El 1.<sup>er</sup> aventurero de <small class="sc">Rango S</small>.', ""), htmls
    assert htmls[1] == ("Firmado: Rudeus", "right"), htmls
    assert htmls[3] == ("Mal cerrado.", ""), htmls
    assert again == lines[:3] + ["Mal cerrado."], again
    assert convert._plain(book.blocks[1]) == "El 1.er aventurero de Rango S.", convert._plain(book.blocks[1])
    assert 'class="right"' in text and '<small class="sc">Rango S</small>' in text and "<sup>er</sup>" in text, text
    assert "small.sc { font-size: 1em; font-variant: small-caps; }" in css and "p.right" in css, css
    assert not problems, problems


def test_separator_options():
    blocks = [convert.Block("heading", 1, "Cap 1"), convert.Block("p", 1, "Uno."),
              convert.Block("break", 1, "☆"), convert.Block("p", 1, "Dos."),
              convert.Block("break", 1, image=JPEG), convert.Block("p", 1, "Tres.")]
    book = convert.Book("Libro", "", JPEG, blocks, [0])
    expected = {
        "Según el estilo": ['<p class="brk">* * *</p>'] * 2,
        "Original del PDF": ['<p class="brk">☆</p>', '<div class="brk-img"><img src="img/b00004.jpg" alt="* * *"/></div>'],
        "✦": ['<p class="brk">✦</p>'] * 2,
        "Línea": ['<hr class="brk"/>'] * 2,
        "Espacio en blanco": ['<div class="brk-gap"></div>'] * 2,
    }
    for option, marks in expected.items():
        out = TMP / "separador.epub"
        convert.write_epub(book, [(0, "Cap 1")], out, separator=option)
        text = epub_text(out)
        found = re.findall(r'<p class="brk">[^<]*</p>|<div class="brk-img">.*?</div>|<hr class="brk"/>|<div class="brk-gap"></div>', text)
        assert found == marks, (option, found)
    assert set(convert.SEPARATOR_CHOICES) >= set(expected)


def test_full_page_illustration_has_no_white_frame():
    """La ilustración rara vez llega al borde exacto de la página (medido: 0,2 a 2,8 pt). Dibujar la
    página entera dejaba el blanco del papel como un marco; se recorta a la zona de la ilustración."""
    doc = pymupdf.open()
    art = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 85), False)
    art.clear_with(40)
    page = doc.new_page(width=595, height=842)
    page.insert_image(pymupdf.Rect(0, 0, 593, 839.5), pixmap=art, keep_proportion=False)  # se queda corta
    chapter_page(doc, 2, [(72, 150, "Capitulo 1: Inicio", 20, "helv")])
    page = doc.new_page(width=595, height=842)
    page.insert_image(pymupdf.Rect(1.5, 0, 595, 842), pixmap=art, keep_proportion=False)  # corta a la izquierda
    path = TMP / "marco-blanco.pdf"
    doc.save(path)
    book = convert.analyze(path)
    images = [book.cover] + [b.image for b in book.blocks if b.kind == "image" and b.keep]
    for data in images:
        pix = pymupdf.Pixmap(data)
        w, h = pix.width, pix.height
        edges = [pix.pixel(w - 1, h // 2), pix.pixel(w // 2, h - 1), pix.pixel(0, h // 2), pix.pixel(w // 2, 0)]
        assert all(max(px) < 120 for px in edges), (w, h, edges)  # ningún borde blanco


def test_write_epub_leaves_out_images_not_kept():
    blocks = [convert.Block("heading", 1, "Cap 1"), convert.Block("image", 1, image=JPEG, keep=True),
              convert.Block("image", 1, image=JPEG, keep=False), convert.Block("p", 1, "Texto.")]
    out = TMP / "imagenes.epub"
    convert.write_epub(convert.Book("Libro", "", JPEG, blocks, [0]), [(0, "Cap 1")], out)
    with zipfile.ZipFile(out) as z:
        pictures = [n for n in z.namelist() if n.startswith("OEBPS/img/")]
    assert len(pictures) == 1, pictures


JPEG = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 4, 4), False).tobytes("jpg")


def test_index_without_title_is_skipped():
    """Un índice sin título "Contenido" se reconoce por las líneas de puntos, aunque vayan en negrita."""
    doc = pymupdf.open()
    add_page(doc, 1, [
        (72, 100, "Capitulo 1: Inicio .......... 2", 13, "hebo"),
        (72, 130, "Capitulo 2: Final .......... 3", 13, "hebo"),
        (72, 160, "Epilogo .......... 4", 13, "hebo"),
    ])
    add_page(doc, 2, [
        (72, 100, "Capitulo 1: Inicio", 20, "helv"),
        (92, 140, line("Primera linea del parrafo", 57), 11, "cour"),
        (72, 154, line("Segunda linea del parrafo", 60), 11, "cour"),
        (72, 168, line("Tercera linea del parrafo", 60), 11, "cour"),
        (72, 182, "fin.", 11, "cour"),
    ])
    path = TMP / "indice-sin-titulo.pdf"
    doc.save(path)
    book = convert.analyze(path)
    titles = [plain(book.blocks[i].html) for i in book.candidates]
    assert titles == ["Capitulo 1: Inicio"], titles


def chapter_page(doc, number, title_lines):
    """Página con unas líneas de título y un párrafo debajo."""
    add_page(doc, number, title_lines + [
        (92, 230, line("Primera linea del parrafo", 57), 11, "cour"),
        (72, 244, line("Segunda linea del parrafo", 60), 11, "cour"),
        (72, 258, "fin.", 11, "cour"),
    ])


def test_point_of_view_titles_under_chapter_are_subtitles_without_bookmarks():
    """Maryoku 6, sin marcadores: "El lado de Beretta" justo bajo "Capítulo 21: …" era otro capítulo y
    dejaba el 21 vacío. Es subtítulo, y también los demás "El lado de…" del libro."""
    doc = pymupdf.open()
    chapter_page(doc, 1, [(72, 100, "Capitulo 1: Inicio", 20, "helv"), (72, 140, "El lado de Beretta", 16, "helv")])
    chapter_page(doc, 2, [(72, 100, "El lado de Selene", 16, "helv")])
    chapter_page(doc, 3, [(72, 100, "Capitulo 2: Final", 20, "helv")])
    chapter_page(doc, 4, [(72, 100, "Palabras del autor", 20, "helv")])
    path = TMP / "el-lado-de.pdf"
    doc.save(path)
    book = convert.analyze(path)
    titles = [book.blocks[k].html for k in book.candidates]
    assert titles == ["Capitulo 1: Inicio", "Capitulo 2: Final", "Palabras del autor"], titles


def test_index_with_credit_line_and_dashed_leaders_is_skipped():
    """Índice con una línea de créditos encima del título y líneas "| Capítulo 1 | ---- Pág. 3"."""
    doc = pymupdf.open()
    add_page(doc, 1, [
        (200, 130, "Grupo Scan Team", 10, "hebo"),
        (200, 170, "Tabla De Contenido", 24, "helv"),
        (54, 210, "| Capitulo 1 | ------------------ Pág. 2", 16, "helv"),
        (54, 240, "| Capitulo 2 | ------------------ Pág. 3", 16, "helv"),
        (54, 270, "| Epilogo | -------------------- Pág. 4", 16, "helv"),
    ])
    chapter_page(doc, 2, [(72, 150, "Capitulo 1: Inicio", 20, "helv")])
    path = TMP / "indice-guiones.pdf"
    doc.save(path)
    book = convert.analyze(path)
    titles = [plain(book.blocks[i].html) for i in book.candidates]
    assert titles == ["Capitulo 1: Inicio"], titles


def test_chapter_label_joins_the_name_below():
    """"» Capitulo 1 «" con el nombre debajo en negrita (aunque no llegue a tamaño de título), o
    "«Capitulo 2»" con un nombre en dos líneas de otro tamaño: un solo capítulo, sin adornos."""
    doc = pymupdf.open()
    chapter_page(doc, 1, [(230, 120, "» Capitulo 1 «", 20, "hebo"),
                          (200, 140, "¡El regreso!", 12, "hebo")])
    chapter_page(doc, 2, [(230, 120, "«Capitulo 2»", 18, "hebo"),
                          (180, 145, "Un titulo largo", 14, "hebo"),
                          (190, 165, "en dos lineas", 14, "hebo")])
    path = TMP / "etiquetas.pdf"
    doc.save(path)
    book = convert.analyze(path)
    titles = [plain(book.blocks[i].html) for i in book.candidates]
    assert titles == ["Capitulo 1: ¡El regreso!", "Capitulo 2: Un titulo largo en dos lineas"], titles
    assert not any(plain(b.html) == "¡El regreso!" for b in book.blocks if b.kind == "p")


def test_small_label_joins_a_bigger_one_line_name():
    """Making Magic (EPUB de la propia app, reabierto): «Chapter 3» en 11 pt y el nombre debajo en una línea de
    16,5 pt, 27 pt más abajo. Con el nombre en dos líneas (13,8 pt, 23,5 pt) se unían; en una, no, y salían
    «Chapter 3» y el nombre como subtítulo: el hueco entre líneas base crece con la letra de la línea de abajo."""
    doc = pymupdf.open()
    chapter_page(doc, 1, [(172, 130, "Chapter 2", 11, "helv"), (116, 157.2, "A Strange Fruit a Day", 16.5, "hebo")])
    chapter_page(doc, 2, [(172, 130, "Chapter 3", 11, "helv"), (54, 153.5, "One Day, About Five Hundred", 13.8, "hebo"),
                          (166, 174.1, "Years into My Life", 13.8, "hebo")])
    doc.set_toc([[1, "Chapter 2: A Strange Fruit a Day", 1], [1, "Chapter 3: One Day, About Five Hundred Years into My Life", 2]])
    path = TMP / "etiqueta-nombre-mayor.pdf"
    doc.save(path)
    book = convert.analyze(path)
    heads = [(plain(b.html), k in book.candidates) for k, b in enumerate(book.blocks) if b.kind == "heading"]
    assert heads == [("Chapter 2: A Strange Fruit a Day", True),
                     ("Chapter 3: One Day, About Five Hundred Years into My Life", True)], heads


def test_front_matter_before_first_chapter_is_not_a_chapter():
    """Sin marcadores, la portadilla ("Mi Serie", "- Volumen 5 -") no cuenta como capítulo."""
    doc = pymupdf.open()
    add_page(doc, 1, [(150, 200, "Mi Serie", 30, "hebo"), (150, 400, "- Volumen 5 -", 30, "helv")])
    chapter_page(doc, 2, [(72, 150, "Capitulo 1: Inicio", 20, "helv")])
    path = TMP / "portadilla.pdf"
    doc.save(path)
    book = convert.analyze(path)
    headings = [(plain(b.html), k in book.candidates) for k, b in enumerate(book.blocks) if b.kind == "heading"]
    assert ("Capitulo 1: Inicio", True) in headings, headings
    assert all(not chosen for title, chosen in headings if title != "Capitulo 1: Inicio"), headings


def design_book():
    """Página 1: portada. Página 2: portadilla con logo y créditos. Página 3: capítulo.
    Página 4: agradecimiento con logo."""
    doc = pymupdf.open()
    cover = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 85), False)
    cover.clear_with(150)
    logo = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 20), False)
    logo.clear_with(70)
    add_page(doc, 1, [])
    doc[0].insert_image(doc[0].rect, pixmap=cover, keep_proportion=False)
    add_page(doc, 2, [(200, 500, "Volumen 6", 20, "helv"), (72, 760, "Autor", 9, "helv"),
                      (72, 772, "Fulano de Tal", 9, "hebo")])
    doc[1].insert_image(pymupdf.Rect(100, 250, 500, 450), pixmap=logo)  # ~16 % de la página
    chapter_page(doc, 3, [(72, 150, "Capitulo 1: Inicio", 20, "helv")])
    add_page(doc, 4, [(200, 500, "Gracias por leer", 20, "helv")])
    doc[3].insert_image(pymupdf.Rect(100, 250, 500, 450), pixmap=logo)
    path = TMP / "diseno.pdf"
    doc.save(path)
    return path


def test_design_page_before_first_chapter_is_kept_whole():
    """La portadilla con logo y créditos se conserva entera, como una imagen de la página."""
    book = convert.analyze(design_book())
    first = [(b.kind, b.keep) for b in book.blocks if b.page == 2]
    page = [b for b in book.blocks if b.page == 2 and b.kind == "image"]
    assert first == [("image", True)], first  # ni el logo suelto ni los textos por separado
    picture = pymupdf.Pixmap(page[0].image)
    assert abs(picture.height / picture.width - 842 / 595) < 0.02, (picture.width, picture.height)


def test_design_like_page_after_first_chapter_is_unchanged():
    """Después del primer capítulo no se toca: título como texto y el logo como descartado."""
    book = convert.analyze(design_book())
    last = [(b.kind, b.keep) for b in book.blocks if b.page == 4]
    assert ("heading", True) in last and ("image", False) in last, last


def test_outline_bookmarks_choose_chapters():
    """Si el PDF tiene marcadores, los de primer nivel mandan sobre los títulos detectados."""
    doc = pymupdf.open(make_novel())
    doc.set_toc([[1, "Ilustraciones", 2], [1, "Capitulo 1: Inicio", 3], [2, "Seccion", 3]])
    path = TMP / "marcadores.pdf"
    doc.save(path)
    book = convert.analyze(path)
    titles = [plain(book.blocks[i].html) for i in book.candidates]
    # "Capitulo 2: Final" no tiene marcador, pero es un capítulo: se añade (hay PDF con marcadores incompletos)
    assert titles == ["Ilustraciones", "Capitulo 1: Inicio", "Capitulo 2: Final"], titles
    assert book.blocks[0].kind == "heading" and book.blocks[1].kind == "image"


def test_illustration_under_text_is_kept():
    """Ilustración a página completa con el texto de la novela encima: se guarda el texto y la imagen
    incrustada original (sin el texto). Una imagen repetida en muchas páginas es un fondo y se ignora."""
    doc = pymupdf.open()
    art = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 85), False)
    art.clear_with(90)
    texture = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 30, 40), False)
    texture.clear_with(240)
    body = [(92, 150, line("Texto encima de la ilustracion", 57), 11, "cour"),
            (72, 164, line("que sigue en la segunda linea", 60), 11, "cour"),
            (72, 178, line("y en la tercera linea tambien", 60), 11, "cour"),
            (72, 192, line("y en la cuarta linea tambien", 60), 11, "cour"),
            (72, 206, "fin.", 11, "cour")]
    p = add_page(doc, 1, [(150, 110, "Capitulo 1: Inicio", 20, "helv")] + body)
    p.insert_image(p.rect, pixmap=texture, keep_proportion=False, overlay=False)
    xref = p.get_images()[0][0]
    p = add_page(doc, 2, body)
    p.insert_image(p.rect, pixmap=art, keep_proportion=False, overlay=False)  # ilustración bajo el texto
    for n in (3, 4):
        p = add_page(doc, n, body)
        p.insert_image(p.rect, xref=xref, keep_proportion=False, overlay=False)  # el mismo fondo en todas
    path = TMP / "ilustracion-bajo-texto.pdf"
    doc.save(path)
    book = convert.analyze(path)
    images = [b for b in book.blocks if b.kind == "image"]
    assert [b.page for b in images] == [2], [b.page for b in images]
    picture = pymupdf.Pixmap(images[0].image)
    assert (picture.width, picture.height) == (60, 85), (picture.width, picture.height)  # la original, no la página
    assert sum(1 for b in book.blocks if b.kind == "p" and b.page == 2) == 1
    # El texto de la página va antes: la imagen sale al acabar la página (Maryoku 5, página 47)
    kinds = [b.kind for b in book.blocks if b.page == 2]
    assert kinds == ["p", "image"], kinds


def test_indented_lowercase_line_continues_unfinished_paragraph():
    """Algunos PDF ponen en línea nueva y con sangría el final de una frase: si el párrafo anterior no
    terminó y la línea empieza en minúscula, es continuación. Tras un punto, es otro párrafo."""
    doc = pymupdf.open()
    add_page(doc, 1, [
        (72, 110, "Capitulo 1: Inicio", 20, "helv"),
        (92, 150, line("Primera linea del parrafo", 57), 11, "cour"),
        (72, 164, line("Asenti, liberando el aliento que habia estado", 60), 11, "cour"),
        (92, 178, "conteniendo la risa.", 11, "cour"),
        (92, 206, "Termina aqui.", 11, "cour"),
        (92, 220, "del mundo que faltaba.", 11, "cour"),
    ])
    add_page(doc, 2, [(92, 150, "Otra pagina.", 11, "cour")])
    path = TMP / "continuacion.pdf"
    doc.save(path)
    texts = [plain(b.html) for b in convert.analyze(path).blocks if b.kind == "p"]
    assert any(t.startswith("Primera linea") and t.endswith("conteniendo la risa.") for t in texts), texts
    assert "Termina aqui." in texts and "del mundo que faltaba." in texts, texts


def first_title(window):
    """Primera fila de título de la lista: las ilustraciones van intercaladas entre los títulos."""
    return next(i for i in window.tree.get_children() if not window.is_image(i))


class Root:
    """Lo que las pruebas usan de la ventana principal, con los nombres de Tk que ya tenían."""

    def __init__(self, window):
        self.window = window

    def update(self):
        import qtk
        qtk.pump()

    def geometry(self, spec):
        size, x, y = spec.split("+")
        width, height = size.split("x")
        self.window.root.showNormal()  # se abre maximizada: las pruebas la quieren de un tamaño fijo
        self.window.root.setGeometry(int(x), int(y), int(width), int(height))
        self.update()

    def winfo_exists(self):
        import shiboken6
        return shiboken6.isValid(self.window.root) and self.window.root.isVisible()

    def destroy(self):
        if self.winfo_exists():
            self.window.root.on_close = None
            self.window.close()

    def dialogs(self):
        """Ventanas secundarias abiertas (los Toplevel de Tk)."""
        from PySide6.QtWidgets import QDialog
        return [w for w in self.window.root.findChildren(QDialog) if w.isVisible()]


def visible_window(settings=None):
    """Ventana Qt sin pantalla (QT_QPA_PLATFORM=offscreen), con su tamaño: la lista calcula posiciones.
    settings: ajustes.json ya escrito, para probar lo que la ventana hace con él."""
    import app

    app.SETTINGS = settings or TMP / f"ajustes-{time_ns()}.json"  # cada ventana sin preferencias de otra prueba
    app._start_spell_process = lambda: None  # sin el proceso de ortografía del editor (su prueba pone uno falso)
    app._start_thumbs = lambda jobs: None  # ni el de las miniaturas de recientes (su prueba llama a _render_thumbs)
    window = app.App(app.Window())
    window.theme_var.set("Claro")
    window.apply_theme()
    root = Root(window)
    window.root.show()
    root.geometry("780x560+0+0")
    return root, window


def test_bold_line_with_paragraph_indent_is_not_a_subtitle():
    """Una línea en negrita algo más grande que el texto (×1,15) que empieza con la sangría del párrafo y no va
    centrada es texto destacado («Obtained Title: …» de The World's Strongest Witch), no un subtítulo; la misma
    línea pegada al margen («El lado de la bruja», Maryoku) sí lo es."""
    doc = pymupdf.open()
    text = [(92, 150, line("Un parrafo cualquiera del capitulo", 57), 11, "cour"), (72, 164, "fin uno.", 11, "cour")]
    after = [(92, 230, line("Otro parrafo despues de la linea", 57), 11, "cour"), (72, 244, "fin dos.", 11, "cour")]
    add_page(doc, 1, [(72, 110, "Capitulo 1: Inicio", 20, "helv")] + text + [(92, 196, "Titulo obtenido: Bruja", 12.6, "hebo")]
             + after)
    add_page(doc, 2, text + [(72, 196, "El lado de la bruja", 12.6, "hebo")] + after)
    path = TMP / "negrita-sangria.pdf"
    doc.save(path)
    heads = [(b.page, plain(b.html)) for b in convert.analyze(path).blocks if b.kind == "heading"]
    assert heads == [(1, "Capitulo 1: Inicio"), (2, "El lado de la bruja")], heads


def test_bookmarked_subtitles_at_body_size_are_headings():
    """Subtítulos del tamaño del texto ("El lado de la bruja") que el PDF marca como nivel 2:
    se reconocen como títulos por el marcador, sin ser capítulos."""
    doc = pymupdf.open()
    paragraph = [(92, 0, line("Primera linea del parrafo", 57), 11, "cour"),
                 (72, 14, line("Segunda linea del parrafo", 60), 11, "cour"),
                 (72, 28, "fin.", 11, "cour")]

    def at(y, lines):
        return [(x, y + dy, t, s, f) for x, dy, t, s, f in lines]

    add_page(doc, 1, [(72, 120, "Capitulo 1: Inicio", 20, "helv")] + at(160, paragraph))
    add_page(doc, 2, [(72, 120, "Capitulo 2: Final", 11, "cobo"),        # capítulo y subtítulo seguidos,
                      (72, 140, "El lado de la bruja", 11, "cobo")]      # mismo tamaño: no deben unirse
             + at(180, paragraph))
    add_page(doc, 3, [(72, 120, "Lado de Teto", 11, "cour")] + at(160, paragraph))  # ni siquiera negrita
    add_page(doc, 4, [(72, 120, "El lado del heredero", 11, "cobo"),     # subtítulo partido en dos líneas
                      (72, 140, "Sutherland Olvart", 11, "cobo")] + at(180, paragraph))
    doc.set_toc([[1, "Capitulo 1: Inicio", 1], [1, "Capitulo 2: Final", 2], [2, "El lado de la bruja", 2],
                 [2, "Lado de Teto", 3], [2, "El lado del heredero Sutherland Olvart", 4]])
    path = TMP / "subtitulos.pdf"
    doc.save(path)
    book = convert.analyze(path)
    headings = [(plain(b.html), k in book.candidates) for k, b in enumerate(book.blocks) if b.kind == "heading"]
    assert headings == [("Capitulo 1: Inicio", True), ("Capitulo 2: Final", True), ("El lado de la bruja", False),
                        ("Lado de Teto", False), ("El lado del heredero Sutherland Olvart", False)], headings


def test_distant_heading_lines_are_separate_titles():
    """Sin marcadores: capítulo y subtítulo seguidos, mismo tamaño pero separados en vertical, son dos títulos.
    Las líneas de un título partido van juntas y sí se unen."""
    doc = pymupdf.open()
    paragraph = [(92, 0, line("Primera linea del parrafo", 57), 11, "cour"),
                 (72, 14, line("Segunda linea del parrafo", 60), 11, "cour"),
                 (72, 28, "fin.", 11, "cour")]

    def at(y, lines):
        return [(x, y + dy, t, s, f) for x, dy, t, s, f in lines]

    add_page(doc, 1, [(180, 120, "Capitulo 1: Defendiendo", 16, "hebo"),
                      (72, 190, "El lado de la fortaleza", 16, "hebo")] + at(230, paragraph))
    add_page(doc, 2, [(150, 120, "Capitulo 2: Un titulo tan largo", 16, "hebo"),
                      (200, 140, "que ocupa dos lineas", 16, "hebo")] + at(180, paragraph))
    path = TMP / "titulos-separados.pdf"
    doc.save(path)
    headings = [plain(b.html) for b in convert.analyze(path).blocks if b.kind == "heading"]
    assert headings == ["Capitulo 1: Defendiendo", "El lado de la fortaleza",
                        "Capitulo 2: Un titulo tan largo que ocupa dos lineas"], headings


def test_heading_that_is_not_a_chapter_is_written_as_subtitle():
    """Un título que no es capítulo (p. ej. "El lado de la bruja") se ve como subtítulo, no como texto normal."""
    book = convert.analyze(make_novel())
    first = book.candidates[0]
    out = TMP / "subtitulo.epub"
    convert.write_epub(book, [(first, "Capitulo 1: Inicio")], out)
    with zipfile.ZipFile(out) as z:
        body = "".join(z.read(n).decode() for n in z.namelist() if re.fullmatch(r"OEBPS/sec\d+\.xhtml", n))
    assert re.search(r"<h2[^>]*>Capitulo 2: Final</h2>", body), re.findall(r"<[^>]+>Capitulo 2[^<]*<", body)


def test_epub_index_nests_subtitles_under_their_chapter():
    """En el índice del lector, los subtítulos salen como subentradas de su capítulo, con enlace a su sitio."""
    book = convert.analyze(make_novel())
    out = TMP / "indice.epub"
    convert.write_epub(book, [(book.candidates[0], "Capitulo 1: Inicio")], out)
    toc = [(level, title) for level, title, _ in pymupdf.open(out).get_toc()]
    assert toc == [(1, "Inicio"), (1, "Capitulo 1: Inicio"), (2, "Capitulo 2: Final")], toc
    ns = {"n": "http://www.daisy.org/z3986/2005/ncx/"}
    with zipfile.ZipFile(out) as z:
        ncx = ET.fromstring(z.read("OEBPS/toc.ncx"))
        nav = z.read("OEBPS/nav.xhtml").decode()
        sections = {n: z.read(n).decode() for n in z.namelist() if re.fullmatch(r"OEBPS/sec\d+\.xhtml", n)}
    chapter = ncx.findall("n:navMap/n:navPoint", ns)[1]  # [0] es Inicio
    sub = chapter.findall("n:navPoint", ns)
    assert [p.find("n:navLabel/n:text", ns).text for p in sub] == ["Capitulo 2: Final"]
    orders = [int(p.get("playOrder")) for p in ncx.iter("{%s}navPoint" % ns["n"])]
    assert orders == list(range(1, len(orders) + 1)), orders  # correlativos, sin repetir
    target = sub[0].find("n:content", ns).get("src")
    page, anchor = target.split("#")
    assert f'id="{anchor}"' in sections["OEBPS/" + page], target  # el enlace lleva al subtítulo
    assert f'href="{target}"' in nav


def sectioned_book():
    """Libro mínimo: dos capítulos, el primero con dos subtítulos."""
    blocks = [("heading", "Cap 1"), ("p", "Uno."), ("heading", "Lado A"), ("p", "Texto A."),
              ("heading", "Lado B"), ("p", "Texto B."), ("heading", "Cap 2"), ("p", "Dos.")]
    return convert.Book("Libro", "", b"", [convert.Block(k, 1, h) for k, h in blocks], [0, 6])


def epub_text(path):
    with zipfile.ZipFile(path) as z:
        return PAGE_MARK.sub("", "".join(z.read(n).decode() for n in z.namelist() if n.endswith(".xhtml")))


def test_removed_subtitle_leaves_out_only_its_part():
    out = TMP / "quitar-subtitulo.epub"
    convert.write_epub(sectioned_book(), [(0, "Cap 1"), (6, "Cap 2")], out, removed=[2])
    text = epub_text(out)
    assert "Lado A" not in text and "Texto A." not in text, text
    assert all(x in text for x in ("Uno.", "Lado B", "Texto B.", "Cap 2", "Dos.")), text


def test_removed_chapter_takes_its_subtitles_and_index_entry():
    out = TMP / "quitar-capitulo.epub"
    convert.write_epub(sectioned_book(), [(6, "Cap 2")], out, removed=[0])
    text = epub_text(out)  # incluye nav.xhtml: tampoco debe estar en el índice
    assert not any(x in text for x in ("Cap 1", "Uno.", "Lado A", "Texto A.", "Lado B", "Texto B.")), text
    assert "Cap 2" in text and "Dos." in text, text


def test_window_lists_all_headings_with_chapters_checked():
    """La lista muestra también los subtítulos, desmarcados, para poder convertirlos en capítulo."""
    import app

    root, window = visible_window()
    book = convert.Book("Libro", "", b"", [
        convert.Block("heading", 1, "Capitulo 1: Inicio"),
        convert.Block("p", 1, "Texto."),
        convert.Block("heading", 2, "El lado de la bruja"),
        convert.Block("p", 2, "Mas texto."),
    ], [0])
    window.show_book(book)
    rows = [(window.tree.set(i, "inc"), window.tree.set(i, "title")) for i in window.tree.get_children()]
    root.destroy()
    assert rows == [(app.ON, "Capitulo 1: Inicio"), (app.OFF, "El lado de la bruja")], rows


def test_window_subtitles_continue_their_chapter():
    """Maryoku 6: la vista previa de un capítulo sigue con sus subtítulos («El lado del gran anciano»), no se
    corta en el primero; un subtítulo quitado no sale. En la columna Tipo, una flecha baja de cada capítulo a
    sus filas (subtítulos, quitados…); lo de antes del primer capítulo no cuelga de nada."""
    import app

    root, window = visible_window()
    book = convert.Book("Libro", "", b"", [
        convert.Block("heading", 1, "Lado previo"), convert.Block("p", 1, "Antes."),
        convert.Block("heading", 2, "Capítulo 22: Seguimos"), convert.Block("p", 2, "Uno."),
        convert.Block("heading", 3, "El lado del gran anciano"), convert.Block("p", 3, "Texto A. " + "palabra " * 690),
        convert.Block("heading", 4, "El lado quitado"), convert.Block("p", 4, "Texto B."),
        convert.Block("heading", 5, "Cazadores furtivos"), convert.Block("p", 5, "Dos."),
        convert.Block("heading", 6, "El lado de la bruja"), convert.Block("p", 6, "Texto C."),
    ], [2, 8])
    window.show_book(book)
    window.tree.selection_set("6")
    window.shortcut(app.CUT)
    window.tree.selection_set("2")
    root.update()
    with zipfile.ZipFile(window.preview_path) as z:
        body = "".join(z.read(n).decode() for n in z.namelist() if re.fullmatch(r"OEBPS/sec\d+\.xhtml", n))
    branches = dict(window.tree.branches)
    values = [window.tree.set(i, "inc") for i in ("0", "4", "10")]
    reading = window.tree.set("2", "min")  # el capítulo se lee con sus subtítulos: 3 min, no 1
    window.tree.selection_set("8")
    window.epub_step(-1)  # hacia atrás desde el capítulo siguiente: el capítulo 22, no su subtítulo
    back = window.tree.selection()
    window.close()
    assert "El lado del gran anciano" in body and "Texto A." in body and "Uno." in body, body
    assert "Texto B." not in body and "Dos." not in body, body
    assert branches == {"2": "root", "4": "mid", "6": "last", "8": "root", "10": "last"}, branches
    assert values == [app.OFF] * 3, values
    assert back == ("2",), back
    assert reading == "3 min", reading


def test_window_previews_pdf_page_and_epub_for_selected_title():
    """Al seleccionar un título se ven su página del PDF y cómo quedará en el EPUB."""
    root, window = visible_window()
    window.pdf = make_novel()
    window.show_book(convert.analyze(window.pdf))
    window.tree.selection_set(first_title(window))  # Capitulo 1: Inicio, página 3
    root.update()
    widths = [view.image.width() if view.image else 0 for view in (window.pdf_view, window.epub_view)]
    caption = window.pdf_caption.text()
    window.close()
    assert widths[0] > 0, "sin vista previa del PDF"
    assert widths[1] > 0, "sin vista previa del EPUB"
    assert "página 3" in caption, caption


def test_window_bottom_bar_stays_visible_with_previews():
    """En pantallas bajas las vistas previas no deben empujar fuera la barra de progreso y el botón."""
    root, window = visible_window()
    root.geometry("1300x600+0+0")
    window.pdf = make_novel()
    window.show_book(convert.analyze(window.pdf))
    window.tree.selection_set(first_title(window))
    root.update()
    from PySide6.QtCore import QPoint

    bottom = window.root.height()
    shown = [(w.isVisible(), w.mapTo(window.root, QPoint(0, w.height())).y() <= bottom)
             for w in (window.gen_btn, window.progress, window.quality_btn)]
    image_height = window.pdf_view.image.height() if window.pdf_view.image else 0
    fits = window.root.minimumSizeHint().height() <= 600 and window.root.height() == 600  # no crece por debajo
    window.close()
    assert shown == [(True, True)] * 3, shown
    assert 0 < image_height < 560, image_height  # se ajustó al alto disponible
    assert fits, window.root.minimumSizeHint()


def test_window_fits_the_free_screen_area():
    """Al abrir, la ventana cabe en la pantalla sin la barra de tareas (1366×768: «Generar EPUB» a la vista)."""
    import app

    app.SETTINGS = TMP / f"ajustes-{time_ns()}.json"
    window = app.App(app.Window())
    area = window.root.screen().availableGeometry()
    inside = area.contains(window.root.geometry())
    window.root.on_close = None
    window.close()
    assert inside, (area, window.root.geometry())


def test_window_epub_preview_follows_include_mark():
    """Desmarcar un título regenera la vista previa del EPUB: pasa de capítulo a subtítulo."""
    from types import SimpleNamespace

    root, window = visible_window()
    window.pdf = make_novel()
    window.show_book(convert.analyze(window.pdf))
    item = first_title(window)
    window.tree.selection_set(item)
    root.update()
    x, y, w, h = window.tree.bbox(item, "inc")
    window.on_click(SimpleNamespace(x=x + w // 2, y=y + h // 2))  # desmarcar
    root.update()
    with zipfile.ZipFile(window.preview_path) as z:
        body = "".join(z.read(n).decode() for n in z.namelist() if re.fullmatch(r"OEBPS/sec\d+\.xhtml", n))
    window.close()
    assert re.search(r"<h2[^>]*>Capitulo 1: Inicio</h2>", body), re.findall(r"<h\d[^>]*>[^<]*</h\d>", body)


def test_window_series_fields_reach_the_epub():
    """La ventana muestra serie y volumen deducidos; lo que el usuario escriba llega al EPUB."""
    import time

    import app

    root, window = visible_window()
    window.pdf = TMP / "Mi Serie - Volumen 03.pdf"
    make_novel().replace(window.pdf)
    window.show_book(convert.analyze(window.pdf))
    shown = (window.series_var.get(), window.volume_var.get())
    window.series_var.set("Serie Corregida")
    window.volume_var.set("4")
    out = TMP / "desde-ventana.epub"
    originals = app.filedialog.asksaveasfilename, app.messagebox.showinfo
    app.filedialog.asksaveasfilename = lambda **kwargs: str(out)
    app.messagebox.showinfo = lambda *args: None
    try:
        window.generate()
        deadline = time.time() + 60
        while window.done and time.time() < deadline:
            root.update()
            time.sleep(0.05)
    finally:
        app.filedialog.asksaveasfilename, app.messagebox.showinfo = originals
        window.close()
    assert shown == ("Mi Serie", "3"), shown
    with zipfile.ZipFile(out) as z:
        opf = z.read("OEBPS/content.opf").decode()
    assert "Serie Corregida" in opf and 'content="4"' in opf, opf[:400]


def test_window_removed_section_preview_and_epub():
    """Un título en "Quitar": la vista previa lo avisa y la sección no llega al EPUB."""
    import time

    import app

    root, window = visible_window()
    window.pdf = make_novel()
    window.show_book(convert.analyze(window.pdf))
    item = first_title(window)  # Capitulo 1: Inicio
    window.tree.selection_set(item)
    root.update()
    window.tree.set(item, "inc", app.CUT)
    window.update_preview()
    preview = (window.epub_view.message, window.epub_view.image)
    out = TMP / "quitado-desde-ventana.epub"
    originals = app.filedialog.asksaveasfilename, app.messagebox.showinfo
    app.filedialog.asksaveasfilename = lambda **kwargs: str(out)
    app.messagebox.showinfo = lambda *args: None
    try:
        window.generate()
        deadline = time.time() + 60
        while window.done and time.time() < deadline:
            root.update()
            time.sleep(0.05)
    finally:
        app.filedialog.asksaveasfilename, app.messagebox.showinfo = originals
        window.close()
    assert preview == ("Esta sección no irá en el EPUB", None), preview
    text = epub_text(out)
    assert "Capitulo 1: Inicio" not in text and "Primera linea" not in text
    assert "Capitulo 2: Final" in text


def test_window_typography_checkbox_reaches_the_epub():
    """La casilla viene marcada; desmarcada, el EPUB conserva la tipografía original."""
    import time

    import app

    root, window = visible_window()
    window.pdf = make_novel()
    window.show_book(convert.Book("Libro", "", b"", [convert.Block("heading", 1, "Cap 1"),
                                                      convert.Block("p", 1, 'Dijo "hola"...')], [0]))
    default = window.typography_var.get()
    window.typography_var.set(False)
    out = TMP / "sin-tipografia.epub"
    originals = app.filedialog.asksaveasfilename, app.messagebox.showinfo
    app.filedialog.asksaveasfilename = lambda **kwargs: str(out)
    app.messagebox.showinfo = lambda *args: None
    try:
        window.generate()
        deadline = time.time() + 60
        while window.done and time.time() < deadline:
            root.update()
            time.sleep(0.05)
    finally:
        app.filedialog.asksaveasfilename, app.messagebox.showinfo = originals
        window.close()
    assert default is True
    assert 'Dijo "hola"...' in epub_text(out)


def test_heading_keeps_its_rectangle_on_the_page():
    book = convert.analyze(make_novel())
    title = book.blocks[book.candidates[0]]  # "Capitulo 1: Inicio" en (72, 100), 20 pt
    x0, y0, x1, y1 = title.rect
    assert abs(x0 - 72) < 2 and y0 < 100 < y1 and x1 > 150, title.rect


def long_book(paragraphs=80):
    blocks = [convert.Block("heading", 1, "Cap 1")]
    blocks += [convert.Block("p", 1, f"Parrafo {n} " + "texto de relleno " * 12) for n in range(paragraphs)]
    return convert.Book("Libro", "", b"", blocks, [0])


def test_window_highlights_detected_title_on_pdf_preview():
    root, window = visible_window()
    window.pdf = make_novel()
    window.show_book(convert.analyze(window.pdf))
    window.tree.selection_set(first_title(window))
    root.update()
    canvas = window.pdf_view
    boxes = [rect for rect, _ in canvas.marks]
    coords = [boxes[0].left(), boxes[0].top(), boxes[0].right(), boxes[0].bottom()] if boxes else []
    size = (canvas.image.width(), canvas.image.height())  # la página sobre la que va el recuadro
    window.close()
    assert coords, "sin recuadro sobre el título"
    assert 0 <= coords[0] < coords[2] <= size[0] and 0 <= coords[1] < coords[3] <= size[1], (coords, size)


def test_window_preview_page_navigation():
    root, window = visible_window()
    window.pdf = make_novel()
    window.show_book(long_book())
    window.tree.selection_set(first_title(window))
    root.update()
    window.pdf_step(1)
    pdf_caption = window.pdf_caption.text()
    first = window.epub_caption.text()
    window.epub_step(1)
    second = window.epub_caption.text()
    window.epub_step(-5)  # no baja de la primera
    back = window.epub_caption.text()
    window.close()
    assert "página 2" in pdf_caption, pdf_caption
    assert "1/" in first and "2/" in second and "1/" in back, (first, second, back)


def test_window_list_styles_follow_type():
    from types import SimpleNamespace

    root, window = visible_window()
    window.pdf = make_novel()
    window.show_book(convert.analyze(window.pdf))
    item = first_title(window)
    root.update()
    x, y, w, h = window.tree.bbox(item, "inc")
    styles = [window.tree.item(item)["tags"]]
    for _ in range(3):
        window.on_click(SimpleNamespace(x=x + w // 2, y=y + h // 2))
        styles.append(window.tree.item(item)["tags"])
    window.close()
    assert [tuple(s) for s in styles] == [("chapter",), ("subtitle",), ("separator",), ("cut",)], styles


def test_window_device_preview_and_dark_mode():
    root, window = visible_window()
    window.pdf = make_novel()
    window.show_book(long_book())
    window.tree.selection_set(first_title(window))
    root.update()
    shapes = {}
    for device in ("Celular", "Tablet"):
        window.device_var.set(device)
        window.draw_epub()
        shapes[device] = window.epub_view.image.height() / window.epub_view.image.width()
    light = sum(window.epub_view.image.pixelColor(2, 2).getRgb()[:3])
    window.dark_var.set(True)
    window.draw_epub()
    dark = sum(window.epub_view.image.pixelColor(2, 2).getRgb()[:3])
    window.close()
    assert shapes["Celular"] > shapes["Tablet"], shapes  # el celular es más alargado
    assert light > 600 and dark < 150, (light, dark)


def test_window_dark_mode_keeps_illustrations():
    """Modo oscuro de las vistas previas (PDF y EPUB): invierte papel y texto, no las ilustraciones."""
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=400)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 40), False)
    pix.set_rect(pix.irect, (200, 30, 30))  # ilustración roja
    page.insert_image(pymupdf.Rect(100, 100, 200, 200), pixmap=pix)
    page.insert_text((40, 300), "Texto de la novela", fontsize=14)
    root, window = visible_window()
    image, zoom = window.render(page, dark=True)
    art = image.pixelColor(int(150 * zoom), int(150 * zoom)).getRgb()[:3]
    paper = image.pixelColor(int(20 * zoom), int(20 * zoom)).getRgb()[:3]
    window.close()
    assert art[0] > 150 and art[1] < 80, art  # sigue roja, no cian
    assert sum(paper) < 100, paper  # el papel, oscuro


def test_window_thumbnail_strip():
    import time

    root, window = visible_window()
    window.pdf = make_novel()  # 5 páginas: 2 con ilustración, 3 y 4 con título
    window.show_book(convert.analyze(window.pdf))
    deadline = time.time() + 30
    while len(window.thumbs) < 5 and time.time() < deadline:
        root.update()
        time.sleep(0.02)
    count = len(window.thumbs)
    marks = {n: window.strip.outline(n) for n in (1, 2, 4)}  # páginas 2, 3 y 5
    window.jump(3)  # página 4: tiene "Capitulo 2: Final"
    root.update()
    caption = window.pdf_caption.text()
    selected = window.tree.set(window.tree.selection()[0], "title") if window.tree.selection() else ""
    window.close()
    assert count == 5, count
    assert marks[1] == app_colors()["image"] and marks[2] == app_colors()["title"] and marks[4] == "", marks
    assert "página 4" in caption and selected == "Capitulo 2: Final", (caption, selected)


def app_colors():
    import app
    return app.MARK_COLORS


def test_window_starts_with_blank_canvases():
    """Al iniciar, las vistas previas y la tira muestran una hoja en blanco, no un hueco vacío."""
    root, window = visible_window()
    root.update()
    message = window.pdf_view.message
    sheets = [int(view.sheet) for view in (window.pdf_view, window.epub_view)]
    placeholders = len(window.strip.blanks)
    window.close()
    assert sheets == [1, 1] and "Abre un PDF" in message, (sheets, message)
    assert placeholders > 0


def test_window_device_change_keeps_layout():
    """Cambiar de dispositivo no cambia el tamaño de las vistas previas ni mueve la lista."""
    root, window = visible_window()
    root.geometry("1366x690+0+0")  # como una pantalla de portátil: ahí se notaba el salto
    window.pdf = make_novel()
    window.show_book(long_book())
    window.tree.selection_set(first_title(window))
    root.update()
    sizes = set()
    for device in ("Celular", 'E-reader 6"', "Tablet", "Celular"):
        window.device_var.set(device)
        window.draw_epub()
        root.update()
        sizes.add((window.views.width(), window.epub_view.size_[0], window.epub_view.size_[1], window.tree.width()))
    window.close()
    assert len(sizes) == 1, sizes


def test_window_reviews_illustrations():
    """Las ilustraciones salen en la lista: se pueden quitar, recuperar las descartadas, y eso llega al EPUB."""
    import time
    from types import SimpleNamespace

    import app

    root, window = visible_window()
    window.pdf = make_covers()
    window.show_book(convert.analyze(window.pdf))
    root.update()
    rows = {window.tree.set(i, "title"): i for i in window.tree.get_children()}
    art, decor = rows["Ilustración · página 1"], rows["Ilustración · página 3"]
    types = (window.tree.set(art, "inc"), window.tree.set(decor, "inc"))

    def click(item):
        x, y, w, h = window.tree.bbox(item, "inc")
        window.on_click(SimpleNamespace(x=x + w // 2, y=y + h // 2))

    click(decor)  # Descartada -> Ilustración
    click(art)    # Descartada (es la misma imagen que la portada, la página 1) -> Ilustración
    click(art)    # Ilustración -> Quitar
    window.tree.selection_set(art)
    root.update()
    message = window.epub_view.message
    out = TMP / "ilustraciones-elegidas.epub"
    originals = app.filedialog.asksaveasfilename, app.messagebox.showinfo
    app.filedialog.asksaveasfilename = lambda **kwargs: str(out)
    app.messagebox.showinfo = lambda *args: None
    try:
        window.generate()
        deadline = time.time() + 60
        while window.done and time.time() < deadline:
            root.update()
            time.sleep(0.05)
    finally:
        app.filedialog.asksaveasfilename, app.messagebox.showinfo = originals
        after = (window.tree.set(art, "inc"), window.tree.set(decor, "inc"))
        window.close()
    assert types == (app.DROPPED, app.DROPPED), types  # repetida de la portada; fondo de título
    assert after == (app.CUT, app.IMG), after
    assert message == "Esta ilustración no irá en el EPUB", message
    with zipfile.ZipFile(out) as z:
        pictures = [n for n in z.namelist() if n.startswith("OEBPS/img/")]
    assert len(pictures) == 1, pictures  # entra el fondo recuperado, no la ilustración quitada


def test_window_style_reaches_preview_and_epub():
    import time

    import app

    root, window = visible_window()
    window.pdf = make_novel()
    window.show_book(convert.analyze(window.pdf))
    default = window.style_var.get()
    window.style_var.set("Novela ligera")
    window.tree.selection_set(first_title(window))
    root.update()
    preview_css = epub_file(window.preview_path, "OEBPS/style.css")
    out = TMP / "estilo-desde-ventana.epub"
    originals = app.filedialog.asksaveasfilename, app.messagebox.showinfo
    app.filedialog.asksaveasfilename = lambda **kwargs: str(out)
    app.messagebox.showinfo = lambda *args: None
    try:
        window.generate()
        deadline = time.time() + 60
        while window.done and time.time() < deadline:
            root.update()
            time.sleep(0.05)
    finally:
        app.filedialog.asksaveasfilename, app.messagebox.showinfo = originals
        window.close()
    assert default == "Clásico", default
    assert "small-caps" in preview_css and "small-caps" in epub_file(out, "OEBPS/style.css")
    with zipfile.ZipFile(out) as z:
        assert "OEBPS/contents.xhtml" in z.namelist()  # el EPUB final lleva la página de índice


def test_window_preview_shows_the_section_not_the_contents_page():
    """La vista previa dibuja el capítulo seleccionado, no la página "Contenido"."""
    root, window = visible_window()
    window.pdf = make_novel()
    window.show_book(convert.analyze(window.pdf))
    window.tree.selection_set(first_title(window))
    root.update()
    with zipfile.ZipFile(window.preview_path) as z:
        names = z.namelist()
    window.close()
    assert "OEBPS/contents.xhtml" not in names, names


def test_window_separator_reaches_preview_and_epub():
    import time

    import app

    root, window = visible_window()
    window.pdf = make_novel()  # su capítulo 1 tiene un separador "* * *"
    window.show_book(convert.analyze(window.pdf))
    default = window.separator_var.get()
    window.separator_var.set("✦")
    window.tree.selection_set(first_title(window))
    root.update()
    preview = epub_text(window.preview_path)
    out = TMP / "separador-desde-ventana.epub"
    originals = app.filedialog.asksaveasfilename, app.messagebox.showinfo
    app.filedialog.asksaveasfilename = lambda **kwargs: str(out)
    app.messagebox.showinfo = lambda *args: None
    try:
        window.generate()
        deadline = time.time() + 60
        while window.done and time.time() < deadline:
            root.update()
            time.sleep(0.05)
    finally:
        app.filedialog.asksaveasfilename, app.messagebox.showinfo = originals
        window.close()
    assert default == "Según el estilo", default
    assert '<p class="brk">✦</p>' in preview and '<p class="brk">✦</p>' in epub_text(out)


def test_window_double_click_on_include_toggles_twice():
    """Cada clic rota Capítulo → Subtítulo → Separador → Quitar → Capítulo. Tk manda el segundo clic rápido
    a <Double-1>: también cuenta."""
    from types import SimpleNamespace

    import app

    root, window = visible_window()
    window.tree.insert("", "end", iid="0", values=(app.ON, "Uno", 1))
    root.update()
    x, y, w, h = window.tree.bbox("0", "inc")
    click = SimpleNamespace(x=x + w // 2, y=y + h // 2)
    states = []
    for handler in (window.on_click, window.on_double_click, window.on_click, window.on_click):
        handler(click)
        states.append(window.tree.set("0", "inc"))
    root.destroy()
    assert states == [app.OFF, app.SEP, app.CUT, app.ON], states


def test_window_title_editor_closes_on_scroll():
    """El cuadro de edición no sigue al scroll: al desplazarse se guarda y se cierra."""
    from types import SimpleNamespace

    from PySide6.QtWidgets import QLineEdit

    import app

    root, window = visible_window()
    for i in range(60):
        window.tree.insert("", "end", iid=str(i), values=(app.ON, f"Titulo {i}", i))
    root.update()
    x, y, w, h = window.tree.bbox("0", "title")
    window.on_double_click(SimpleNamespace(x=x + w // 2, y=y + h // 2))
    editor = window.tree.viewport().findChildren(QLineEdit)
    assert len(editor) == 1
    editor[0].setText("Nuevo titulo")
    bar = window.tree.verticalScrollBar()
    bar.setValue(bar.maximum())
    root.update()
    left = [c for c in window.tree.viewport().findChildren(QLineEdit) if c.isVisible()]
    title = window.tree.set("0", "title")
    root.destroy()
    assert not left and title == "Nuevo titulo", (left, title)


def test_window_error_resets_progress_bar():
    import app

    root, window = visible_window()
    original = app.messagebox.showerror
    app.messagebox.showerror = lambda title, message: None
    try:
        window.report(5, 10)
        window.poll()
        assert window.progress.value() == 5
        window.finish(("error", "fallo"))
        value = window.progress.value()
    finally:
        app.messagebox.showerror = original
        root.destroy()
    assert value == 0, value


def test_worker_error_without_message_names_the_exception():
    import queue

    import app

    def boom():
        raise MemoryError()

    events = queue.Queue()
    app._worker(events, boom, (), False)
    assert events.get_nowait() == ("error", "MemoryError")


def test_window_close_stops_work_and_removes_partial_epub():
    import time

    root, window = visible_window()
    window.run(time.sleep, (30,), lambda _: None)
    partial = TMP / "cerrar.epub.tmp"
    partial.write_bytes(b"a medio escribir")
    window.partial = str(partial)
    window.close()
    assert not partial.exists()
    assert not window.proc.is_alive()


def test_window_runs_work_in_another_process():
    """PyMuPDF retiene el GIL: en un hilo traba la ventana, así que el trabajo va en otro proceso."""
    import os
    import time
    import app

    root, window = visible_window()
    result = []
    window.run(os.getpid, (), result.append)
    deadline = time.time() + 30
    while not result and time.time() < deadline:
        root.update()
        time.sleep(0.05)
    root.destroy()
    assert result and result[0] != os.getpid(), result


def test_window_open_file_with_too_long_path():
    """Making Magic: el PDF se guardó en una ruta de 298 caracteres y «Abrir PDF» decía «[WinError 2] El sistema no
    puede encontrar el archivo», aunque estaba ahí: Windows y los visores (Foxit) no abren rutas de 260 o más. La
    ventana lo explica y ofrece abrir su carpeta; «Abrir carpeta» abre la carpeta sin seleccionar el archivo, que
    con esa ruta tampoco funciona."""
    import app

    folder = TMP / ("carpeta muy larga de una serie " * 4).strip() / ("otra carpeta larga con el nombre " * 3).strip()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / ("Nombre largo del libro vol 1 Premium (LNC) (A5)" + "x" * max(0, 262 - len(str(folder)) - 52) + ".pdf")
    path.write_bytes(b"%PDF")
    root, window = visible_window()
    asked, launched = [], []
    originals = app.os.startfile, app.subprocess.Popen, app.messagebox.askyesno, app.messagebox.showerror

    def startfile(target):  # lo que hace Windows con una ruta así
        raise FileNotFoundError(2, "El sistema no puede encontrar el archivo especificado", str(target))

    app.os.startfile, app.subprocess.Popen = startfile, lambda args, **_: launched.append(args)
    app.messagebox.askyesno = lambda title, message, **_: asked.append(message) or True
    app.messagebox.showerror = lambda title, message, **_: asked.append("error: " + message)
    try:
        window.open_external(path)
        window.open_external(path, folder=True)
    finally:
        app.os.startfile, app.subprocess.Popen, app.messagebox.askyesno, app.messagebox.showerror = originals
        root.destroy()
    assert len(str(path)) >= 260, len(str(path))
    assert len(asked) == 1 and "260" in asked[0] and "caracteres" in asked[0], asked
    assert launched == [[app.EXPLORER, str(folder)], [app.EXPLORER, str(folder)]], launched
    assert Path(app.EXPLORER).is_absolute() and Path(app.POWERSHELL).is_file(), (app.EXPLORER, app.POWERSHELL)
    assert os.environ["NoDefaultCurrentDirectoryInExePath"] == "1"  # ni java ni calibre salen de la carpeta actual


def test_window_reports_worker_crash():
    """Si el proceso de trabajo muere sin avisar, la ventana muestra el error y se desbloquea."""
    import os
    import time
    import app

    root, window = visible_window()
    shown = []
    original = app.messagebox.showerror
    app.messagebox.showerror = lambda title, message: shown.append(message)
    try:
        window.run(os._exit, (3,), lambda _: None)
        deadline = time.time() + 30
        while not shown and time.time() < deadline:
            root.update()
            time.sleep(0.05)
        unlocked = window.open_btn.isEnabled()
    finally:
        app.messagebox.showerror = original
        root.destroy()
    assert shown and "inesperadamente" in shown[0], shown
    assert unlocked


def test_window_starts_work_with_big_data_without_freezing():
    """Generar un libro con ilustraciones congelaba la ventana ~3 s: los datos iban por la tubería del
    proceso nuevo y la ventana esperaba a que arrancara para leerlos. Ahora van en un archivo temporal."""
    import time

    root, window = visible_window()
    results = []
    started = time.time()
    window.run(len, (b"x" * 3_000_000,), results.append)
    took = time.time() - started
    deadline = time.time() + 30
    while not results and time.time() < deadline:
        root.update()
        time.sleep(0.05)
    root.destroy()
    assert results == [3_000_000], results
    assert took < 0.5, took


def test_window_quality_report_not_repeated_for_the_same_text():
    """El informe tarda ~1 s en un libro largo: deshacer, cambiar de tema o abrir el informe con el mismo
    texto no lo calculan otra vez (la ventana se congelaba cada vez). Un cambio del texto sí."""
    import app

    root, window = visible_window()
    calls = []
    original = app.convert.quality_report
    app.convert.quality_report = lambda book, *options: calls.append(1) or original(book, *options)
    try:
        window.show_book(convert.Book("Libro", "", b"", [convert.Block("heading", 1, "Capítulo 1"),
                                                         convert.Block("p", 1, "Que haces?")], [0]))
        opened, before = len(calls), window.quality_btn.text()
        window.theme_var.set("Oscuro")
        window.apply_theme()
        window.quality()
        root.update()
        same = len(calls)
        window.apply_edits([(1, 2, "¿Qué haces?")], "0")
        edited, fixed = len(calls), window.quality_btn.text()
        window.undo()
        undone, after = len(calls), window.quality_btn.text()
        # Al abrir un PDF, el informe llega hecho del proceso de trabajo con el análisis (_analyze)
        book = convert.Book("Otro", "", b"", [convert.Block("heading", 1, "Capítulo 1"),
                                              convert.Block("p", 1, "Otro texto.")], [0])
        window.show_book(book, [], original(book))
        seeded = len(calls)
    finally:
        app.convert.quality_report = original
        window.close()
    assert (opened, same, edited, undone, seeded) == (1, 1, 2, 2, 2), (opened, same, edited, undone, seeded)
    assert before == after != fixed, (before, fixed, after)


def test_epub_metadata_group_translator_synopsis():
    book = sectioned_book()
    book.publisher, book.translator, book.description = "Grupo & Co", "Ana", "Una bruja <muy> fuerte."
    out = TMP / "metadatos.epub"
    convert.write_epub(book, [(0, "Cap 1")], out)
    opf = epub_file(out, "OEBPS/content.opf")
    ET.fromstring(opf.encode())  # sigue siendo XML válido con & y <>
    assert "<dc:publisher>Grupo &amp; Co</dc:publisher>" in opf, opf
    assert '<dc:contributor id="trl">Ana</dc:contributor>' in opf and ">trl</meta>" in opf, opf
    assert "<dc:description>Una bruja &lt;muy&gt; fuerte.</dc:description>" in opf, opf



def test_epub_accessibility_metadata_and_alt_text():
    """EPUB 3 accesible: metadatos schema.org (modo visual y texto alternativo solo con ilustraciones) y cada
    ilustración con su descripción: la del usuario o «Ilustración de «capítulo»». En EPUB 2, sin metadatos."""
    pic = jpeg(300, 400)
    other = jpeg(320, 400)
    B = convert.Block
    book = convert.Book("Libro", "", pic, [B("image", 1, image=other), B("heading", 2, "Cap 1"), B("p", 2, "Texto."),
                                           B("image", 3, image=pic)], [1])
    out = TMP / "accesible.epub"
    convert.write_epub(book, [(1, "Cap <1>")], out, alts={convert.image_key(other): "Emilia & Subaru en el mercado"})
    opf = epub_file(out, "OEBPS/content.opf")
    ET.fromstring(opf.encode())
    pages = " ".join(epub_file(out, f"OEBPS/{n}") for n in ("sec001.xhtml", "sec002.xhtml"))
    plain = TMP / "sin-ilustraciones.epub"
    convert.write_epub(convert.Book("Libro", "", pic, book.blocks[1:3], [0]), [(0, "Cap 1")], plain)
    plain_opf = epub_file(plain, "OEBPS/content.opf")
    old = TMP / "epub2.epub"
    convert.write_epub(book, [(1, "Cap 1")], old, epub2=True)
    assert '<meta property="schema:accessMode">visual</meta>' in opf and "alternativeText" in opf, opf
    assert '<meta property="schema:accessibilityHazard">none</meta>' in opf and "conformsTo" not in opf
    assert 'alt="Emilia &amp; Subaru en el mercado"' in pages and 'alt="Ilustración de «Cap &lt;1&gt;»"' in pages, pages
    assert "visual" not in plain_opf and "alternativeText" not in plain_opf and "tableOfContents" in plain_opf
    assert "schema:" not in epub_file(old, "OEBPS/content.opf")
    assert not convert.check_epub(out), convert.check_epub(out)



def test_window_illustrations_export_and_replace_with_comparison():
    """«Ilustraciones…»: exportar todas con nombres estables; «Reemplazar desde carpeta» empareja por nombre, se
    salta las que no cambiaron y pregunta con antes y después (aceptar una, saltar otra); cancelar no cambia nada;
    un Ctrl+Z lo deshace todo, y la revisión guarda una copia propia: borrar la carpeta no pierde el cambio."""
    import app
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QPushButton

    B = convert.Block
    pics = [jpeg(300, 400), jpeg(320, 440), jpeg(340, 480)]
    blocks = [B("heading", 1, "Capítulo 1"), B("image", 1, image=pics[0]), B("p", 1, "Texto."),
              B("heading", 2, "Capítulo 2"), B("image", 2, image=pics[1]), B("image", 3, image=pics[2])]
    folder = TMP / "ilustraciones-exportadas"
    root, window = visible_window()
    window.show_book(convert.Book("Libro", "", b"", list(blocks), [0, 3]))
    saved = app.filedialog.askdirectory, app.filedialog.askopenfilename
    answers = ["replace", "skip"]
    asked = []
    try:
        app.filedialog.askdirectory = lambda **options: str(folder)
        window.images_dialog()
        root.update()
        dialog = root.dialogs()[-1]
        listed = dialog.parts["list"].count()
        click = lambda text: next(b for b in dialog.findChildren(QPushButton) if b.text() == text).click()
        click("Exportar todas…")
        exported = sorted(f.name for f in folder.iterdir())
        new2, new3 = jpeg(640, 880), jpeg(680, 960)
        (folder / exported[1]).write_bytes(new2)
        (folder / exported[2]).write_bytes(new3)
        window.confirm_image = lambda k, data, batch=False, parent=None: asked.append((k, batch)) or answers.pop(0)
        click("Reemplazar desde carpeta…")
        note = dialog.parts["note"].text()
        after = [b.image for b in window.book.blocks if b.kind == "image"]
        state = app.json.loads(app.json.dumps(window.review_state()))  # como se guarda: Ctrl+Z ya no la toca
        # Cancelar a mitad de un lote: nada cambia
        window.confirm_image = lambda k, data, batch=False, parent=None: None
        before_cancel = [b.image for b in window.book.blocks]
        cancelled = window.replace_from_folder(folder)
        unchanged = [b.image for b in window.book.blocks] == before_cancel
        window.undo()
        undone = [b.image for b in window.book.blocks if b.kind == "image"]
        # La comparación de verdad: Reemplazar responde "replace"
        del window.confirm_image
        QTimer.singleShot(300, lambda: next(b for b in window.image_compare.findChildren(QPushButton)
                                            if b.text() == "Reemplazar").click())
        real = window.confirm_image(1, new2)
        window.close()
    finally:
        app.filedialog.askdirectory, app.filedialog.askopenfilename = saved
    for f in folder.iterdir():  # la carpeta de exportación ya no está
        f.unlink()
    folder.rmdir()
    fresh = convert.Book("Libro", "", b"", list(blocks), [0, 3])
    app._replay(fresh, state)
    assert listed == 3, listed
    assert exported == ["ilustracion-01-pag-001.jpg", "ilustracion-02-pag-002.jpg", "ilustracion-03-pag-003.jpg"], \
        exported
    assert asked == [(4, True), (5, True)], asked
    assert after == [pics[0], new2, pics[2]], "la aceptada cambia; la saltada y la igual, no"
    assert note.startswith("1 ilustración reemplazada · 1 saltadas"), note
    assert cancelled is None and unchanged
    assert undone == pics, "un Ctrl+Z deshace el lote"
    assert real == "replace", real
    assert fresh.blocks[4].image == new2, "al reabrir, sin la carpeta de exportación, el cambio sigue"


def test_window_illustrations_list_rows_are_even():
    """Lista de «Ilustraciones…»: una apaisada (doble página) tiene la fila tan alta como una vertical, con la
    miniatura en el mismo marco, y un título de capítulo largo no abre barra horizontal (Kage 7: filas
    desiguales). El título entero, al pasar el ratón."""
    from PySide6.QtCore import Qt

    import app

    B = convert.Block
    title = "Capítulo II: ¡Esta Vez Seguro Que No Descubren Nuestro Escondite En El Bosque Del Norte!"
    blocks = [B("heading", 1, title), B("image", 1, image=jpeg(300, 420)), B("image", 2, image=jpeg(900, 640)),
              B("image", 3, image=jpeg(1200, 300))]
    root, window = visible_window()
    window.show_book(convert.Book("Libro", "", b"", blocks, [0]))
    window.images_dialog()
    root.update()
    listing = root.dialogs()[-1].parts["list"]
    rows = [listing.visualItemRect(listing.item(n)).height() for n in range(listing.count())]
    icons = [listing.item(n).icon().pixmap(app.QSize(*app.THUMB_BOX)).size().toTuple() for n in range(listing.count())]
    scroll = listing.horizontalScrollBar().isVisible(), listing.horizontalScrollBarPolicy()
    tip = listing.item(0).toolTip()
    window.close()
    assert len(set(rows)) == 1 and rows[0] >= app.THUMB_BOX[1], rows
    assert set(icons) == {app.THUMB_BOX}, icons
    assert scroll == (False, Qt.ScrollBarPolicy.ScrollBarAlwaysOff), scroll
    assert title in tip, tip

def test_window_describe_image_reaches_the_epub():
    """«Describir ilustración…» guarda la descripción por la imagen (no por la fila) en la revisión, y el EPUB la
    lleva como texto alternativo; sin una ilustración elegida, un aviso lo pide."""
    import app

    pic = jpeg(300, 400)
    B = convert.Block
    blocks = [B("heading", 1, "Capítulo 1"), B("p", 1, "Texto."), B("image", 2, image=pic)]
    original, told = app._ask_line, []
    info = app.messagebox.showinfo
    app._ask_line = lambda parent, title, label, text: "Emilia en el mercado"
    app.messagebox.showinfo = lambda title, message, **_: told.append(message)
    try:
        root, window = visible_window()
        window.show_book(convert.Book("Libro", "", b"", blocks, [0]))  # sin portada: la parecida se descarta
        window.tree.selection_set("0")
        window.describe_image()  # un título: no es una ilustración
        window.tree.selection_set("2")
        window.describe_image()
        state = window.review_state()
        out = TMP / "descrita.epub"
        chapters, removed, breaks = app._plan(window.book, window.rows())
        convert.write_epub(window.book, chapters, out, removed=removed, **window.write_options(breaks))
        window.close()
    finally:
        app._ask_line, app.messagebox.showinfo = original, info
    assert told == ["Elige primero una ilustración en la lista."], told
    assert state["alts"] == {convert.image_key(pic): "Emilia en el mercado"}, state["alts"]
    with zipfile.ZipFile(out) as z:
        pages = {n: z.read(n).decode() for n in z.namelist() if n.endswith(".xhtml")}
    assert any('alt="Emilia en el mercado"' in p for p in pages.values()), {
        n: re.findall(r"<img[^>]*>", p) for n, p in pages.items()}


def test_epubcheck_messages_in_spanish():
    """El informe JSON de EPUBCheck se lee como (gravedad, dónde, mensaje) en español."""
    import app

    data = {"messages": [{"severity": "ERROR", "message": "Falta el atributo «alt». ",
                          "locations": [{"path": "OEBPS/sec001.xhtml", "line": 12}]},
                         {"severity": "WARNING", "message": "Aviso", "locations": [{"path": "OEBPS/content.opf",
                                                                                     "line": -1}]},
                         {"severity": "FATAL", "message": "Roto"}]}
    assert app._epubcheck_messages(data) == [("Error", "OEBPS/sec001.xhtml, línea 12", "Falta el atributo «alt»."),
                                             ("Advertencia", "OEBPS/content.opf", "Aviso"), ("Error grave", "", "Roto")]


def test_window_epubcheck_asks_before_downloading():
    """Sin Java, un aviso dice cómo instalarlo; sin EPUBCheck, pregunta antes de descargar nada; el resultado se
    muestra en una lista o como «cumple el estándar»."""
    import app

    root, window = visible_window()
    told, asked, ran = [], [], []
    saved = app.shutil.which, app.messagebox.showinfo, app.messagebox.askyesno, window.run
    app.messagebox.showinfo = lambda title, message, **_: told.append(message)
    app.messagebox.askyesno = lambda title, message, **_: asked.append(message) or False
    window.run = lambda work, args, done, **options: ran.append(work) or True
    try:
        app.shutil.which = lambda name: None
        window.validate_epub(TMP / "libro.epub")
        app.shutil.which = lambda name: "java.exe"
        window.validate_epub(TMP / "libro.epub")  # sin el .jar: pregunta, y con «No» no descarga
        window.show_epubcheck(TMP / "libro.epub", [])
        clean = root.dialogs()[-1].findChildren(app.QLabel)[1].text()
        window.show_epubcheck(TMP / "libro.epub", [("Error", "OEBPS/sec001.xhtml", "Mal"), ("Advertencia", "", "Ojo")])
        status = window.status.text()
    finally:
        app.shutil.which, app.messagebox.showinfo, app.messagebox.askyesno, window.run = saved
        window.close()
    assert "Java" in told[0] and "33 MB" in asked[0] and ran == [], (told, asked, ran)
    assert clean == "Sin errores ni advertencias: el EPUB cumple el estándar.", clean
    assert status == "EPUBCheck: 1 error, 1 advertencia", status


def test_write_pdf_bookmarks_numbers_and_metadata():
    """PDF maquetado desde el libro: tamaño elegido, índice como marcadores, título y autor en las propiedades,
    número de página al pie salvo en la portada y en las ilustraciones a página completa; sin restos .tmp."""
    B = convert.Block
    text = "Ella abrió la puerta y miró el pasillo vacío antes de seguir. " * 12
    blocks = ([B("heading", 1, "Capítulo 1: Inicio")] + [B("p", 1, text) for _ in range(10)]
              + [B("image", 2, image=jpeg(600, 900))]
              + [B("heading", 3, "Capítulo 2: Final")] + [B("p", 3, text) for _ in range(4)])
    book = convert.Book("Mi novela", "Autora X", jpeg(400, 600), blocks, [0, 12])
    out = TMP / "libro.pdf"
    convert.write_pdf(book, [(0, "Capítulo 1: Inicio"), (12, "Capítulo 2: Final")], out, size="A5",
                      typography=True)
    pdf = pymupdf.open(out)
    width, height = pdf[-1].rect.width, pdf[-1].rect.height  # una de texto: la portada mide lo que su imagen
    widths = {round(page.rect.width) for page in pdf}
    toc = [title for _, title, _ in pdf.get_toc()]
    meta = pdf.metadata["title"], pdf.metadata["author"]
    foot = lambda page: page.get_text(clip=pymupdf.Rect(0, height - 30, width, height)).strip()
    feet = [foot(page) for page in pdf]
    image_pages = [n for n, page in enumerate(pdf) if not page.get_text(clip=pymupdf.Rect(0, 0, width, height - 30)).strip()
                   and page.get_images()]
    pages = len(pdf)
    pdf.close()
    assert (round(width), round(height)) == (420, 595) and widths == {420}, (width, height, widths)
    assert "Capítulo 1: Inicio" in toc and "Capítulo 2: Final" in toc, toc
    assert meta == ("Mi novela", "Autora X"), meta
    assert feet[0] == "" and all(feet[n] == "" for n in image_pages) and image_pages, (feet, image_pages)
    numbered = [n for n in range(1, pages) if n not in image_pages and feet[n]]
    assert numbered and all(feet[n] == str(n + 1) for n in numbered), feet
    assert not list(TMP.glob("libro.pdf.tmp")), "queda el .tmp"


def test_write_pdf_small_caps_keep_their_letters():
    """Making Magic: en el PDF del estilo «Novela ligera», sin tipografía elegida, «Chapter 3» (versalitas) se
    leía «CƒĵǙǵŧǡ 3»: las versalitas de la letra por defecto de MuPDF no guardan sus letras. Al reabrir el PDF
    no había etiqueta y el capítulo perdía su número; copiar o buscar en el PDF tampoco iba. Se leen las letras, y
    el PDF reabierto da «Chapter 3: Nombre»."""
    B = convert.Block
    text = "Ella abrió la puerta y miró el pasillo vacío antes de seguir. " * 12
    blocks = ([B("heading", 1, "Chapter 3: Becoming a Witch")]
              + [B("p", 1, 'Dijo <small class="sc">versalitas</small> en voz baja. ' + text) for _ in range(6)])
    book = convert.Book("Mi novela", "Autora X", jpeg(400, 600), blocks, [0])
    out = TMP / "versalitas.pdf"
    convert.write_pdf(book, [(0, "Chapter 3: Becoming a Witch")], out, size="A5", style="Novela ligera")
    with pymupdf.open(out) as pdf:
        text = " ".join(page.get_text() for page in pdf)
    again = convert.analyze(out)
    titles = [html.unescape(again.blocks[k].html) for k in again.candidates]
    assert "CHAPTER 3" in text.upper() and "VERSALITAS" in text.upper(), text[:300]
    assert titles == ["Chapter 3: Becoming a Witch"], titles


def test_write_pdf_whole_words_never_splits_a_word():
    """PDF maquetado: el CSS pide hyphens: auto y MuPDF parte «mu-jer», «cue-ro» al final de línea. Con
    whole_words la palabra que no cabe pasa entera a la línea siguiente; sin la opción se sigue partiendo.
    Marcada en Opciones → PDF, la opción llega a write_pdf."""
    B = convert.Block
    text = ("Los que aparecieron ante nosotros eran humanos: un hombre y una mujer cargando a un hombre herido "
            "sobre sus hombros. Su armadura de cuero estaba manchada de sangre y cubierta de suciedad. ") * 4
    book = convert.Book("Mi novela", "Autora X", jpeg(400, 600), [B("heading", 1, "Capítulo 1: Inicio")]
                        + [B("p", 1, text) for _ in range(4)], [0])
    split = {}
    for whole in (False, True):
        out = TMP / f"palabras-{whole}.pdf"
        convert.write_pdf(book, [(0, "Capítulo 1: Inicio")], out, size="A5", hyphenate=True, whole_words=whole)
        with pymupdf.open(out) as pdf:
            split[whole] = sum(line.rstrip().endswith(("-", "­")) for page in pdf
                               for line in page.get_text().splitlines())
    assert split[False] > 0 and split[True] == 0, split
    import app
    root, window = visible_window()
    window.show_book(book)
    window.pdf = TMP / "original.pdf"
    dialog = window.open_options("PDF", "book")  # la casilla vive en Opciones → PDF
    dialog.parts["widgets"]["whole_words"].setChecked(True)
    dialog.parts["accept"]()
    sent = []
    originals = app.filedialog.asksaveasfilename, window.run
    app.filedialog.asksaveasfilename = lambda **kwargs: str(TMP / "menu.pdf")
    window.run = lambda fn, args, *rest, **kwargs: sent.append(args[4]) or True
    try:
        window.generate_pdf("A5")
    finally:
        app.filedialog.asksaveasfilename, window.run = originals
    checked = window.whole_words_var.get()
    window.close()
    assert checked and sent and sent[0]["whole_words"] is True, (checked, sent)


def test_write_epub_and_pdf_report_progress():
    """Generar EPUB o PDF avanza la barra de abajo con su porcentaje, como al abrir: el progreso sube sin
    retroceder y acaba lleno."""
    B = convert.Block
    blocks = ([B("heading", 1, "Capítulo 1: Inicio"), B("p", 1, "Texto.")] + [B("image", 2, image=jpeg(600, 900))]
              + [B("heading", 3, "Capítulo 2: Final"), B("p", 3, "Más texto.")])
    book = convert.Book("Mi novela", "Autora X", jpeg(400, 600), blocks, [0, 3])
    chapters = [(0, "Capítulo 1: Inicio"), (3, "Capítulo 2: Final")]
    for write, out in ((convert.write_epub, TMP / "progreso.epub"), (convert.write_pdf, TMP / "progreso.pdf")):
        seen = []
        write(book, chapters, out, progress=lambda done, total: seen.append((done, total)))
        values = [done / total for done, total in seen]
        assert len(seen) > 2 and values == sorted(values) and seen[-1][0] == seen[-1][1], (write.__name__, seen)


def test_pdf_illustrations_fill_their_own_page():
    """En el PDF cada ilustración ocupa una página entera: la que estaba sola con márgenes se amplía, la que
    compartía página con unas líneas de texto (Re Zero 1, págs. 108, 140, 256) sale a su propia página tras el
    texto, que se queda; un adorno de separador pequeño no se toca."""
    pic, small = jpeg(600, 860), jpeg(60, 20)
    pdf = pymupdf.open()
    alone = pdf.new_page(width=420, height=595)
    alone.insert_image(pymupdf.Rect(60, 80, 360, 510), stream=pic)
    shared = pdf.new_page(width=420, height=595)
    shared.insert_text((40, 60), "Rom le hizo un guiño a Subaru.", fontsize=11)
    shared.insert_image(pymupdf.Rect(40, 100, 380, 560), stream=pic)
    ornament = pdf.new_page(width=420, height=595)
    ornament.insert_text((40, 60), "Fin de la escena.", fontsize=11)
    ornament.insert_image(pymupdf.Rect(180, 80, 240, 100), stream=small)
    moved = convert._full_page_images(pdf)  # en su sitio
    out = pdf
    pages = []
    for page in out:
        boxes = [page.get_image_bbox(i) for i in page.get_images(full=True)]
        big = [b for b in boxes if b.width * b.height > 0.25 * 420 * 595]
        pages.append((page.get_text().strip(), [(round(b.width), round(b.height)) for b in big], len(boxes)))
    assert moved == [0, 1, 3], moved
    assert [p[0] for p in pages] == ["", "Rom le hizo un guiño a Subaru.", "", "Fin de la escena."], pages
    full = [p[1][0] for p in pages if p[1]]
    assert len(full) == 2 and all(w >= 410 or h >= 585 for w, h in full), full  # a toda la página, sin recortar
    assert not pages[1][1] and pages[3][2] == 1, pages  # el texto sin la imagen; el adorno sigue


def test_pdf_illustration_page_height_follows_the_image():
    """Una ilustración doble (Witch 1, págs. 2-3) llena su página: mismo ancho que el libro y el alto de la
    imagen, sin márgenes blancos; una vertical más larga que la hoja alarga su página, no se encoge."""
    pdf = pymupdf.open()
    for size in ((1200, 860), (600, 1200)):
        page = pdf.new_page(width=420, height=595)
        page.insert_image(pymupdf.Rect(20, 20, 400, 580), stream=jpeg(*size))
    convert._full_page_images(pdf)
    sizes = [(round(page.rect.width), round(page.rect.height)) for page in pdf]
    fills = [page.get_image_bbox(page.get_images(full=True)[0]) == page.rect for page in pdf]
    assert sizes == [(420, 301), (420, 840)] and all(fills), (sizes, fills)

def jpeg(width, height):
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), False)
    pix.set_rect(pix.irect, (200, 120, 40))
    pix.set_rect(pymupdf.IRect(0, 0, width // 2, height // 3), (20, 60, 200))
    return pix.tobytes("jpg", jpg_quality=95)


def indexed_jpx(palette, rows):
    """JPEG 2000 de índices con su propia paleta RGB (cajas pclr y cmap), como las ilustraciones de Eminence 7,
    y la tabla de la paleta para el /Indexed del PDF."""
    import io
    import struct

    from PIL import Image

    def box(kind, body):
        return struct.pack(">I4s", 8 + len(body), kind) + body

    im = Image.new("L", (len(rows[0]), len(rows)))
    im.putdata([v for row in rows for v in row])
    out = io.BytesIO()
    im.save(out, "JPEG2000")  # sin pérdida: los índices quedan exactos
    data = out.getvalue()
    start = data.index(b"jp2h") - 4
    end = start + struct.unpack(">I", data[start:start + 4])[0]
    ihdr = data[data.index(b"ihdr", start) - 4:][:22]
    table = b"".join(bytes([v] * 3) for v in palette) + bytes(3 * (256 - len(palette)))
    head = box(b"jp2h", ihdr + box(b"colr", bytes([1, 0, 0]) + struct.pack(">I", 16))  # sRGB
               + box(b"pclr", struct.pack(">HB", 256, 3) + bytes([7, 7, 7]) + table)
               + box(b"cmap", bytes([0, 0, 1, 0, 0, 0, 1, 1, 0, 0, 1, 2])))
    return data[:start] + head + data[end:], table


def test_indexed_jpeg2000_illustration_is_not_noise():
    """Eminence 7: ilustraciones JPEG 2000 con paleta propia y /Indexed en el PDF. Dibujadas más pequeñas que
    su tamaño (vista previa, miniaturas, panel del editor, ilustraciones a página entera), MuPDF promediaba los
    índices y salía ruido. Un damero de los índices 0 y 2 (los dos gris claro) se veía negro: la media es el 1."""
    data, table = indexed_jpx([200, 0, 200], [[0 if (x + y) % 2 else 2 for x in range(256)] for y in range(256)])
    doc = pymupdf.open()
    add_page(doc, 1, [(72, 100, "Capitulo 1: Inicio", 20, "helv"),
                      (92, 150, line("Un parrafo cualquiera de la escena", 57), 11, "cour"), (72, 164, "fin.", 11, "cour")])
    page = doc.new_page(width=100, height=100)
    page.insert_image(page.rect, stream=data)
    xref = page.get_images(full=True)[0][0]
    doc.xref_set_key(xref, "ColorSpace", f"[/Indexed /DeviceRGB 255 <{table.hex()}>]")
    path = TMP / "jpx-paleta.pdf"
    doc.save(path)
    with convert.open_pdf(path) as shown:
        preview = shown[1].get_pixmap().pixel(50, 50)
    picture = next(b.image for b in convert.analyze(path).blocks if b.kind == "image")
    pix = pymupdf.Pixmap(picture)
    middle = pix.pixel(pix.width // 2, pix.height // 2)[:3]
    assert preview == (200, 200, 200), preview
    assert all(abs(v - 200) <= 8 for v in middle), middle


def test_open_pdf_with_a_dangling_object_entry():
    """Re Zero 3 y Megami 3: la última entrada de la tabla de objetos apunta a la palabra «xref», no a un objeto.
    Mirar todos los objetos en busca de JPEG 2000 (open_pdf) daba «cannot find object» y el libro no abría."""
    objects = [b"<</Type/Catalog/Pages 2 0 R>>", b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
               b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 100 100]>>"]
    data, offsets = b"%PDF-1.5\r\n", []
    for n, body in enumerate(objects, 1):
        offsets.append(len(data))
        data += b"%d 0 obj\r\n%s\r\nendobj\r\n" % (n, body)
    xref = len(data)
    data += (b"xref\r\n0 5\r\n0000000000 65535 f\r\n" + b"".join(b"%010d 00000 n\r\n" % o for o in offsets)
             + b"%010d 00000 n\r\n" % xref  # el objeto 4 «está» donde empieza la tabla
             + b"trailer\r\n<</Size 5/Root 1 0 R>>\r\nstartxref\r\n%d\r\n%%%%EOF\r\n" % xref)
    path = TMP / "objeto-perdido.pdf"
    path.write_bytes(data)
    with convert.open_pdf(path) as doc:
        assert doc.page_count == 1


def test_preview_illustration_fits_its_page():
    """Vista previa del EPUB (MuPDF): no entiende max-height, y una ilustración más alta que la página pasaba
    entera a la siguiente, dejando la primera en blanco con un borde de la imagen abajo (Eminence 7, pág. 23).
    Con fit, la ilustración cabe en la primera página del dispositivo; sin fit, el EPUB no cambia."""
    book = convert.Book("Libro", "", jpeg(40, 60), [convert.Block("image", 1, image=jpeg(831, 1239))], [])
    for size in ((420, 560), (300, 560), (560, 750)):
        out = TMP / f"vista-{size[0]}.epub"
        convert.write_epub(book, [], out, contents=False, fit=size)
        with pymupdf.open(out) as epub:
            epub.layout(width=size[0], height=size[1], fontsize=11)
            pages = epub.chapter_page_count(1)
            box = pymupdf.Rect(epub.load_page((1, 0)).get_image_info()[0]["bbox"])
        assert pages == 1 and box in pymupdf.Rect(0, 0, *size), (size, pages, box)
    convert.write_epub(book, [], TMP / "sin-fit.epub", contents=False)
    with zipfile.ZipFile(TMP / "sin-fit.epub") as z:
        assert "style=" not in z.read("OEBPS/sec001.xhtml").decode()


def test_shrink_makes_big_images_lighter():
    big = jpeg(2000, 3000)
    book = convert.Book("Libro", "", big, [convert.Block("heading", 1, "Cap 1"),
                                            convert.Block("image", 1, image=big)], [0])
    out = TMP / "reducido.epub"
    convert.write_epub(book, [(0, "Cap 1")], out, shrink=True)
    with zipfile.ZipFile(out) as z:
        pictures = [z.read(n) for n in z.namelist() if n.startswith("OEBPS/img/") or n == "OEBPS/cover.jpg"]
    assert len(pictures) == 2, len(pictures)
    for data in pictures:
        pix = pymupdf.Pixmap(data)
        assert max(pix.width, pix.height) == convert.SHRINK, (pix.width, pix.height)
        assert len(data) < len(big) and data[:3] == b"\xff\xd8\xff"
    small = jpeg(300, 400)
    assert convert._shrink(small) == small or len(convert._shrink(small)) < len(small)


def test_load_cover_converts_png_to_jpeg():
    path = TMP / "portada.png"
    pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 60), True).save(path)
    data = convert.load_cover(path)
    assert data[:3] == b"\xff\xd8\xff", data[:4]
    (TMP / "portada.jpg").write_bytes(jpeg(40, 60))
    assert convert.load_cover(TMP / "portada.jpg") == (TMP / "portada.jpg").read_bytes()
    try:
        convert.load_cover(TMP / "no-existe.png")
    except ValueError:
        pass
    else:
        raise AssertionError("debía avisar de la imagen que falta")


def test_window_keyboard_shortcuts_change_type():
    import app

    root, window = visible_window()
    window.pdf = make_covers()
    window.show_book(convert.analyze(window.pdf))
    title = first_title(window)
    image = next(i for i in window.tree.get_children() if window.is_image(i))
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    seen = []
    for item, key in ((title, "s"), (title, "Q"), (title, "i"), (title, "c"), (image, "q"), (image, "s"), (image, "i")):
        window.tree.selection_set(item)
        root.update()
        shift = Qt.KeyboardModifier.ShiftModifier if key.isupper() else Qt.KeyboardModifier.NoModifier
        QTest.keyClick(window.tree, getattr(Qt.Key, f"Key_{key.upper()}"), shift)  # Q: con Bloq Mayús
        root.update()
        seen.append(window.tree.set(item, "inc"))
    window.close()
    assert seen == [app.OFF, app.CUT, app.CUT, app.ON, app.CUT, app.CUT, app.IMG], seen


def test_window_review_is_restored_when_reopening_the_pdf():
    """La revisión se guarda al cerrar y vuelve al abrir el mismo PDF, aunque OneDrive le cambie la fecha al
    sincronizar; si el PDF cambia, no."""
    import app

    root, window = visible_window()
    window.pdf = make_novel()
    window.show_book(convert.analyze(window.pdf))
    item = first_title(window)
    window.set_kind(item, app.OFF)
    window.tree.set(item, "title", "Capítulo uno")
    window.publisher_var.set("Grupo X")
    window.style_var.set("Moderno")
    window.description = "Sinopsis."
    window.pdf_page = 2
    window.cover_from_page()
    window.close()
    os.utime(window.pdf, ns=(1_000_000_000, 1_000_000_000))  # OneDrive al sincronizar: otra fecha, mismo PDF

    root, window = visible_window()
    window.pdf = TMP / "novela.pdf"
    window.show_book(convert.analyze(window.pdf))
    restored = (window.tree.set(item, "inc"), window.tree.set(item, "title"), window.publisher_var.get(),
                window.style_var.get(), window.description, window.cover_choice, window.status.text())
    window.close()
    assert restored == (app.OFF, "Capítulo uno", "Grupo X", "Moderno", "Sinopsis.", ["page", 2],
                        "Revisión anterior recuperada"), restored

    root, window = visible_window()
    window.pdf = make_novel()  # PDF nuevo en la misma ruta y del mismo tamaño (otro /ID): la revisión ya no vale
    window.show_book(convert.analyze(window.pdf))
    fresh = (window.tree.set(item, "inc"), window.style_var.get(), window.cover_choice)
    window.close()
    assert fresh == (app.ON, "Clásico", None), fresh


def test_window_metadata_cover_and_shrink_reach_the_epub():
    import time

    import app

    root, window = visible_window()
    window.pdf = make_novel()
    window.show_book(convert.analyze(window.pdf))
    window.translator_var.set("Ana")
    window.lang_var.set("en")
    cover = TMP / "otra-portada.jpg"
    cover.write_bytes(jpeg(2000, 3000))
    out = TMP / "metadatos-desde-ventana.epub"
    originals = app.filedialog.askopenfilename, app.filedialog.asksaveasfilename, app.messagebox.showinfo
    app.filedialog.askopenfilename = lambda **kwargs: str(cover)
    app.filedialog.asksaveasfilename = lambda **kwargs: str(out)
    app.messagebox.showinfo = lambda *args: None
    try:
        window.cover_from_file()
        label = window.cover_label.text()
        window.generate()
        deadline = time.time() + 60
        while window.done and time.time() < deadline:
            root.update()
            time.sleep(0.05)
    finally:
        app.filedialog.askopenfilename, app.filedialog.asksaveasfilename, app.messagebox.showinfo = originals
        window.close()
    assert label == "Portada: otra-portada.jpg", label
    opf = epub_file(out, "OEBPS/content.opf")
    assert "<dc:language>en</dc:language>" in opf and ">Ana</dc:contributor>" in opf, opf
    with zipfile.ZipFile(out) as z:
        pix = pymupdf.Pixmap(z.read("OEBPS/cover.jpg"))
    assert (pix.width, pix.height) == (933, convert.SHRINK), (pix.width, pix.height)  # reducida por defecto


def two_color(width, height):
    """JPEG con la mitad izquierda roja y la derecha azul, para saber qué mitad es cada página."""
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), False)
    pix.set_rect(pix.irect, (255, 0, 0))
    pix.set_rect(pymupdf.IRect(width // 2, 0, width, height), (0, 0, 255))
    return pix.tobytes("jpg", jpg_quality=95)


def test_wide_illustration_split_or_rotated():
    wide = two_color(400, 200)
    assert convert.is_wide(wide) and not convert.is_wide(jpeg(200, 300))
    for layout in ("split", "rotate"):
        book = convert.Book("Libro", "", b"", [convert.Block("heading", 1, "Cap 1"),
                                               convert.Block("image", 1, image=wide, layout=layout)], [0])
        out = TMP / f"doble-{layout}.epub"
        convert.write_epub(book, [(0, "Cap 1")], out)
        with zipfile.ZipFile(out) as z:
            section = z.read("OEBPS/sec001.xhtml").decode()
            srcs = re.findall(r'<img src="([^"]+)"', section)
            pixes = [pymupdf.Pixmap(z.read("OEBPS/" + s)) for s in srcs]
        if layout == "split":
            assert [(p.width, p.height) for p in pixes] == [(200, 200), (200, 200)], srcs
            assert pixes[0].pixel(100, 100)[2] > 200 and pixes[1].pixel(100, 100)[0] > 200  # derecha (azul) primero
        else:
            assert [(p.width, p.height) for p in pixes] == [(200, 400)], srcs
            assert pixes[0].pixel(100, 50)[0] > 200  # girada a la derecha: la mitad izquierda queda arriba


def test_replacements_change_whole_words_outside_tags():
    book = convert.Book("Libro", "", b"", [convert.Block("heading", 1, "Cap Ana"),
                                           convert.Block("p", 1, "<i>Ana</i> y Anabel: Onii-chan &amp; i.")], [0])
    out = TMP / "reemplazos.epub"
    convert.write_epub(book, [(0, "Cap Ana")], out,
                       replacements=[("Ana", "Hana"), ("Onii-chan", "Onii-san"), ("i", "<x>"), ("", "nada")])
    text = epub_file(out, "OEBPS/sec001.xhtml")
    assert "<p><i>Hana</i> y Anabel: Onii-san &amp; &lt;x&gt;.</p>" in text, text
    assert "Cap Hana" in text and "Cap Hana" in epub_file(out, "OEBPS/nav.xhtml")


def wide_book():
    return convert.Book("Libro", "", b"", [convert.Block("heading", 1, "Cap 1"), convert.Block("p", 1, "Texto."),
                                           convert.Block("image", 1, image=two_color(400, 200)),
                                           convert.Block("image", 1, image=jpeg(200, 300))], [0])


def test_window_wide_illustration_types():
    from types import SimpleNamespace

    import app

    root, window = visible_window()
    book = wide_book()
    window.show_book(book)
    root.update()
    titles = [window.tree.set(i, "title") for i in ("2", "3")]

    def click(item):
        x, y, w, h = window.tree.bbox(item, "inc")
        window.on_click(SimpleNamespace(x=x + w // 2, y=y + h // 2))
        return window.tree.set(item, "inc")

    wide = [click("2") for _ in range(4)]
    narrow = [click("3") for _ in range(2)]
    click("2")  # Dividida
    window.tree.selection_set("2")
    root.update()
    with zipfile.ZipFile(window.preview_path) as z:
        preview = [n for n in z.namelist() if n.startswith("OEBPS/img/")]
    _, removed, _ = app._plan(book, window.rows())
    window.close()
    assert titles == ["Ilustración doble · página 1", "Ilustración · página 1"], titles
    assert wide == [app.SPLIT, app.ROTATED, app.CUT, app.IMG], wide
    assert narrow == [app.CUT, app.IMG], narrow
    assert len(preview) == 2, preview  # la vista previa ya muestra las dos mitades
    assert (book.blocks[2].layout, book.blocks[2].keep, removed) == ("split", True, []), book.blocks[2].layout


def test_window_undo_restores_type_and_title():
    import app

    root, window = visible_window()
    window.show_book(sectioned_book())
    window.set_kind("0", app.CUT)
    window.set_kind("2", app.ON)
    window.history.append([window.row_state("2")])
    window.tree.set("2", "title", "Lado A editado")  # como hace el cuadro de edición
    from PySide6.QtGui import QKeySequence, QShortcut

    bound = [sc for sc in window.root.findChildren(QShortcut) if sc.key() == QKeySequence("Ctrl+Z")]  # en toda la ventana
    window.undo()
    after_title = (window.tree.set("2", "inc"), window.tree.set("2", "title"))
    window.undo()
    window.undo()
    window.undo()  # sin más cambios: no hace nada
    rows = [window.tree.set(i, "inc") for i in ("0", "2")]
    window.close()
    assert bound and after_title == (app.ON, "Lado A"), (bound, after_title)
    assert rows == [app.ON, app.OFF], rows


def series_folder():
    folder = TMP / f"serie-{time_ns()}"
    folder.mkdir()
    for n in (1, 2):
        make_novel().replace(folder / f"Mi Serie - Volumen 0{n}.pdf")
    return folder


def time_ns():
    import time
    return time.time_ns()


def review_volume_one(folder):
    """Revisa el tomo 1 en la ventana y la cierra (lo que guarda la revisión)."""
    root, window = visible_window()
    window.pdf = folder / "Mi Serie - Volumen 01.pdf"
    window.show_book(convert.analyze(window.pdf))
    window.tree.set(first_title(window), "title", "Capítulo uno")
    window.publisher_var.set("Grupo X")
    window.translator_var.set("Ana")
    window.style_var.set("Moderno")
    window.description = "Solo del tomo 1."
    window.replacements = [["Primera", "Inicial"]]
    window.close()


def test_series_data_is_inherited_from_another_folder():
    """El tomo 2 en otra carpeta que el 1: hereda reemplazos y datos de la serie guardados al revisar el 1."""
    import app

    app.SETTINGS = TMP / f"ajustes-{time_ns()}.json"
    other = TMP / f"otra-{time_ns()}"
    other.mkdir()
    assert app._inherited(other / "Saga X - Volumen 2.pdf", "Saga X") == {}
    app._remember_series({"series": "Saga X ", "translator": "Ana", "replacements": [["Primera", "Inicial"]],
                          "title": "Solo del tomo 1", "kepub": True, "font": "Mi Letra.ttf",
                          "font_file": "C:/Fuentes/Mi Letra.ttf"})
    got = app._inherited(other / "Saga X - Volumen 2.pdf", "saga x")
    assert got == {"translator": "Ana", "replacements": [["Primera", "Inicial"]], "kepub": True,
                   "font": "Mi Letra.ttf", "font_file": "C:/Fuentes/Mi Letra.ttf"}, got
    assert app._inherited(other / "Otra - Volumen 2.pdf", "Otra") == {}


def test_watched_folder_converts_new_pdfs_once():
    import time

    import app

    folder = TMP / f"vigilada-{time_ns()}"
    folder.mkdir()
    make_novel().replace(folder / "Ya estaba.pdf")
    root, window = visible_window()
    window.set_watch(str(folder))  # lo que había al activarla no se convierte
    make_novel().replace(folder / "Mi Serie - Volumen 05.pdf")
    (folder / "roto.pdf").write_bytes(b"no es un pdf")
    runs = []
    original = window.run
    window.run = lambda *args, **kwargs: runs.append(args[1][0]) or original(*args, **kwargs)
    try:
        window.poll_watch()  # primera vuelta: apunta tamaños (podría estar copiándose)
        assert not runs, runs
        window.poll_watch()
        deadline = time.time() + 120
        while window.done and time.time() < deadline:
            root.update()
            time.sleep(0.05)
        window.poll_watch()  # el roto no se reintenta; el convertido ya tiene EPUB
        status = window.status.text()
    finally:
        window.close()
    assert [sorted(Path(p).name for p in r) for r in runs] == [["Mi Serie - Volumen 05.pdf", "roto.pdf"]], runs
    assert (folder / "Mi Serie - Volumen 05.epub").is_file() and not (folder / "Ya estaba.epub").exists()
    assert "Carpeta vigilada" in status and "roto.pdf" in status, status


ANILIST_SAMPLE = {"data": {"Page": {"media": [{
    "title": {"romaji": "Maryoku Cheat na Majo ni Narimashita", "english": "I Became a Witch with Cheat Mana",
              "native": "魔力チートな魔女になりました"},
    "startDate": {"year": 2019}, "genres": ["Fantasy", "Slice of Life", "Raro"],
    "description": "Chise was an office worker.<br><br>Now she is a <i>witch</i> &amp; more.\n(Source: J-Novel Club)",
    "staff": {"edges": [{"role": "Illustration", "node": {"name": {"full": "Kei"}}},
                        {"role": "Story", "node": {"name": {"full": "Aloha Zaregoto"}}}]},
    "characters": {"edges": [{"role": "MAIN", "node": {"name": {"full": "Chise Itou"}}},
                             {"role": "SUPPORTING", "node": {"name": {"full": None}}},
                             {"role": "BACKGROUND", "node": {"name": {"full": "Teto"}}}]}}]}}}


def test_anilist_results_and_fill_only_empty_fields():
    import app

    found = app._anilist_results(ANILIST_SAMPLE)
    assert found == [{"name": "Maryoku Cheat na Majo ni Narimashita (2019) · I Became a Witch with Cheat Mana",
                      "author": "Aloha Zaregoto", "illustrator": "Kei", "original_title": "魔力チートな魔女になりました",
                      "subjects": "Fantasía, Recuentos de la vida, Raro",
                      "description": "Chise was an office worker.\n\nNow she is a witch & more.",
                      "characters": [("Chise Itou", "MAIN"), ("Teto", "BACKGROUND")]}], found
    assert app._anilist_results({"errors": [{"message": "x"}]}) == []
    original = app.urllib.request.urlopen
    app.urllib.request.urlopen = lambda *args, **kwargs: (_ for _ in ()).throw(OSError("sin red"))
    try:
        app._lookup("Maryoku")
        raise AssertionError("sin red debía fallar")
    except ValueError as e:
        assert "No se pudo consultar AniList" in str(e) and "sin red" in str(e), e
    finally:
        app.urllib.request.urlopen = original
    root, window = visible_window()
    asked = []
    original = app.messagebox.askyesno
    app.messagebox.askyesno = lambda title, message: asked.append(message) or True
    try:
        window.author_var.set("Autor del PDF")
        window.apply_online(found[0])
        got = (window.author_var.get(), window.illustrator_var.get(), window.subjects_var.get(), window.description)
    finally:
        app.messagebox.askyesno = original
        window.close()
    assert got == ("Autor del PDF", "Kei", "Fantasía, Recuentos de la vida, Raro",
                   "Chise was an office worker.\n\nNow she is a witch & more."), got
    assert "Aloha" not in asked[0] and "Ilustraciones: Kei" in asked[0] and "en inglés" in asked[0], asked


def test_window_character_page():
    """«Página de personajes»: solo los principales vienen marcados, cada fila cuenta cuántas veces sale el
    nombre en el libro (y se recalcula al escribir), la página va antes del primer capítulo y Ctrl+Z la quita."""
    import app

    pdf = TMP / "Personajes - Volumen 1.pdf"
    make_novel().replace(pdf)
    root, window = visible_window()
    window.pdf = pdf
    window.show_book(convert.analyze(pdf))
    chapters = [i for i in window.tree.get_children() if window.tree.set(i, "inc") == app.ON]
    blocks = len(window.book.blocks)
    window.character_dialog([("Tom Cat", "MAIN"), ("Jerry", "SUPPORTING")])
    root.update()
    rows = root.dialogs()[-1].parts["rows"]
    checked = [row["check"].isChecked() for row in rows]
    before = rows[0]["count"].text()
    rows[0]["name"].setText("Tom")
    after = rows[0]["count"].text()
    rows[0]["note"].setText("el gato")
    root.dialogs()[-1].parts["save"]()
    root.update()
    first = int(chapters[0])
    page = [b.html for b in window.book.blocks[first:first + 2]], window.tree.set(str(first), "inc")
    window.undo()
    undone = len(window.book.blocks)
    window.close()
    assert checked == [True, False] and before == "0 veces" and after == "1 vez", (checked, before, after)
    assert page == (["Personajes", "<b>Tom</b>: el gato"], app.ON), page
    assert undone == blocks, (undone, blocks)


def test_window_new_volume_inherits_series_data():
    folder = series_folder()
    review_volume_one(folder)
    root, window = visible_window()
    window.pdf = folder / "Mi Serie - Volumen 02.pdf"
    window.show_book(convert.analyze(window.pdf))
    got = (window.publisher_var.get(), window.translator_var.get(), window.style_var.get(), window.replacements,
           window.description, window.volume_var.get(), window.tree.set(first_title(window), "title"))
    status = window.status.text()
    window.close()
    assert got == ("Grupo X", "Ana", "Moderno", [["Primera", "Inicial"]], "", "2", "Capitulo 1: Inicio"), got
    assert "otro tomo" in status, status


def test_window_series_translation_glossary_reaches_every_volume():
    """Making Magic: el glosario de traducción y los personajes se hicieron en el EPUB «(LNC)» del tomo 1, pero en la
    carpeta está también la revisión del EPUB original del tomo 1, sin glosario y guardada después: el tomo 2
    heredaba de esa, vacía. Cada dato sale del tomo más reciente que lo tiene. Un tomo que ya tenía revisión (se
    convirtió la serie antes de traducir) toma el glosario y los personajes si los suyos están vacíos; si tiene los
    suyos, se quedan."""
    import json
    import app

    folder = series_folder()
    terms, notes = [["Lady Teto", "Señorita Teto"]], "Teto: chica alegre, trata de tú"
    for n, (name, glossary, people) in enumerate((("Mi Serie - Volumen 01 (LNC)", terms, notes),
                                                  ("Mi Serie - Volumen 01", [], ""))):
        path = folder / f"{name}.lnepub.json"
        path.write_text(json.dumps({"series": "Mi Serie", "tr_glossary": glossary, "tr_notes": people}), encoding="utf-8")
        os.utime(path, (1_700_000_000 + n, 1_700_000_000 + n))  # la del EPUB original, más reciente
    got = []
    for opened_before in (False, True):
        root, window = visible_window()
        window.pdf = folder / "Mi Serie - Volumen 02.pdf"
        window.show_book(convert.analyze(window.pdf))
        got.append((window.tr_glossary, window.tr_notes))
        if not opened_before:  # deja el tomo 2 con revisión y sin glosario, como al convertir la serie primero
            window.tr_glossary, window.tr_notes = [], ""
        window.close()
    root, window = visible_window()
    window.pdf = folder / "Mi Serie - Volumen 02.pdf"
    window.show_book(convert.analyze(window.pdf))
    window.tr_glossary = [["Lady Teto", "Dama Teto"]]  # el suyo propio
    window.close()
    root, window = visible_window()
    window.pdf = folder / "Mi Serie - Volumen 02.pdf"
    window.show_book(convert.analyze(window.pdf))
    own = window.tr_glossary
    window.close()
    assert got == [(terms, notes), (terms, notes)], got
    assert own == [["Lady Teto", "Dama Teto"]], own


def test_batch_converts_with_review_and_inherited_data():
    import app

    folder = series_folder()
    review_volume_one(folder)
    pdfs = [str(folder / f"Mi Serie - Volumen 0{n}.pdf") for n in (1, 2)] + [str(folder / "roto.pdf")]
    (folder / "roto.pdf").write_bytes(b"no es un pdf")
    seen = []
    results = app._batch(pdfs, {"style": "Clásico", "shrink": False}, progress=lambda d, t: seen.append((d, t)))
    assert [r[0] for r in results] == ["Mi Serie - Volumen 01.pdf", "Mi Serie - Volumen 02.pdf", "roto.pdf"]
    assert results[0][1] == results[1][1] == "" and results[2][1], results
    assert seen[-1] == (3, 3), seen
    one, two = (folder / f"Mi Serie - Volumen 0{n}.epub" for n in (1, 2))
    assert "Capítulo uno" in epub_text(one) and "Solo del tomo 1." in epub_file(one, "OEBPS/content.opf")
    opf = epub_file(two, "OEBPS/content.opf")
    assert "Grupo X" in opf and "Solo del tomo 1." not in opf, opf
    assert "Inicial linea" in epub_text(two) and "Primera linea" not in epub_text(two)
    assert "sans-serif" in epub_file(two, "OEBPS/style.css")  # estilo heredado, no el de la ventana


def test_window_convert_folder_skips_existing_epubs_on_no():
    import time

    import app

    folder = series_folder()
    (folder / "Mi Serie - Volumen 01.epub").write_bytes(b"mio")
    root, window = visible_window()
    asked, done = [], []
    originals = (app.filedialog.askdirectory, app.messagebox.askyesnocancel, app.messagebox.showinfo,
                 app.messagebox.showwarning)
    app.filedialog.askdirectory = lambda **kwargs: str(folder)
    app.messagebox.askyesnocancel = lambda title, message: asked.append(message) or False
    app.messagebox.showinfo = app.messagebox.showwarning = lambda title, message: done.append(message)
    try:
        window.convert_folder()
        deadline = time.time() + 120
        while window.done and time.time() < deadline:
            root.update()
            time.sleep(0.05)
    finally:
        (app.filedialog.askdirectory, app.messagebox.askyesnocancel, app.messagebox.showinfo,
         app.messagebox.showwarning) = originals
        status = window.status.text()
        window.close()
    assert asked and "1 de 2" in asked[0], asked
    assert (folder / "Mi Serie - Volumen 01.epub").read_bytes() == b"mio"  # "No": no se reemplaza
    assert zipfile.is_zipfile(folder / "Mi Serie - Volumen 02.epub")
    assert status == "1 de 1 EPUB generados", (status, done)


def test_dashes_for_dialogue_and_asides():
    cases = {
        "- Hola -dijo él-. Vamos.": "—Hola —dijo él—. Vamos.",
        "Las reliquias -para empezar- eran caras.": "Las reliquias —para empezar— eran caras.",
        "cazadores - incluida la mía - en": "cazadores —incluida la mía— en",
        "-<i>Muy</i> raramente-, los fantasmas": "—<i>Muy</i> raramente—, los fantasmas",
        "Greg-sama vio 1-2 casas a -5 grados": "Greg-sama vio 1-2 casas a -5 grados",
        "—¿Qué? —preguntó—. Nada.": "—¿Qué? —preguntó—. Nada.",
        '-"Hola" -dijo.': "—«Hola» —dijo.",
    }
    got = {k: convert._typography(k) for k in cases}
    assert got == cases, {k: v for k, v in got.items() if v != cases[k]}


def test_line_break_before_honorific_keeps_hyphen():
    assert convert._join("vio a Gilbert-", "kun llegar") == "vio a Gilbert-kun llegar"
    assert convert._join("una pala-", "bra partida") == "una palabra partida"


def test_edit_section_moves_images_and_adds_titles():
    blocks = [("heading", "Cap 1"), ("p", "Uno &amp; <i>dos</i>."), ("image", ""), ("break", "* * *"), ("p", "Tres."),
              ("heading", "Cap 2"), ("p", "Cuatro.")]
    book = convert.Book("Libro", "", b"", [convert.Block(k, 1, h) for k, h in blocks], [0, 5])
    text = convert.section_text(book, 1, 5)
    assert text == "Uno & <i>dos</i>.\n\n[Ilustración 2]\n\n[Separador 3]\n\nTres.", text
    edited = "Uno & <i>dos</i> <b>mal.\n# Nuevo título\n[Ilustración 2]\nTres.\n\n* * *\n[Ilustración 6]"
    mapping = convert.edit_section(book, 1, 5, edited)
    kinds = [(b.kind, b.html) for b in book.blocks]
    assert kinds == [("heading", "Cap 1"), ("p", "Uno &amp; dos mal."), ("heading", "Nuevo título"), ("image", ""),
                     ("p", "Tres."), ("break", "* * *"), ("p", "[Ilustración 6]"), ("heading", "Cap 2"),
                     ("p", "Cuatro.")], kinds  # <b> sin cerrar: se quitan las etiquetas; la marca 6 no es de aquí
    assert mapping[2] == 3 and 3 not in mapping and mapping[5] == 7, mapping  # el separador 3 se borró
    assert book.candidates == [0, 7], book.candidates


def test_name_variants_finds_names_written_two_ways():
    text = ["Vio a Greg-sama y luego a Greg sama.", "Dijo que Tino y Tiño eran la misma.", "Él vio el mar.",
            "Utilizó la llave.", "Utilizo la llave.", "Lo saludó Claire."]
    book = convert.Book("Libro", "", b"", [convert.Block("p", 1, t) for t in text], [])
    groups = [sorted(g) for g in convert.name_variants(book)]
    assert sorted(groups) == [["Greg sama", "Greg-sama"], ["Tino", "Tiño"]], groups


def test_link_sections_and_credits_page():
    blocks = [("heading", "Cap 1"), ("p", "Texto largo. " * 200), ("heading", "Agradecimientos"),
              ("p", "Síguenos en https://discord.gg/abc y en Patreon."), ("heading", "Cap 2"),
              ("p", "Mira mi Twitter, dijo. " + "Más texto. " * 300)]
    book = convert.Book("Mi libro", "Autora", b"", [convert.Block(k, 1, h) for k, h in blocks], [0, 2, 4],
                        series="Serie", volume="2", translator="Ana", publisher="Grupo")
    assert convert.link_sections(book) == [2]  # el capítulo 2 menciona Twitter, pero es largo
    out = TMP / "creditos.epub"
    convert.write_epub(book, [(0, "Cap 1"), (4, "Cap 2")], out, credits=True)
    credits = epub_file(out, "OEBPS/credits.xhtml")
    for line in ("<b>Mi libro</b>", "Serie: Serie, volumen 2", "Autor: Autora", "Traducción: Ana", "Grupo: Grupo"):
        assert line in credits, credits
    opf = epub_file(out, "OEBPS/content.opf")
    assert opf.index('idref="cover"') < opf.index('idref="credits"') < opf.index('idref="contents"'), opf


def make_group_pdf():
    doc = pymupdf.open()
    add_page(doc, 1, [(72, 100, "Capitulo 1: Inicio", 20, "helv"),
                      (72, 140, "La espada Excalibur brillaba.", 11, "cour"),
                      (72, 168, "Otro parrafo del capitulo.", 11, "cour")])
    add_page(doc, 2, [(72, 100, "Agradecimientos", 20, "helv"),
                      (72, 140, "Siguenos en https://discord.gg/grupo", 11, "cour")])
    add_page(doc, 3, [(72, 100, "Capitulo 2: Final", 20, "helv"),
                      (72, 140, "El final de la historia.", 11, "cour")])
    path = TMP / "grupo.pdf"
    doc.save(path)
    return path


def test_window_proposes_removing_group_sections():
    import app

    root, window = visible_window()
    window.pdf = make_group_pdf()
    window.show_book(convert.analyze(window.pdf))
    rows = {window.tree.set(i, "title"): window.tree.set(i, "inc") for i in window.tree.get_children()}
    status = window.status.text()
    window.close()
    assert rows == {"Capitulo 1: Inicio": app.ON, "Agradecimientos": app.CUT, "Capitulo 2: Final": app.ON}, rows
    assert "1 sección del grupo en Quitar" in status, status


def test_window_edited_text_survives_reopening_and_reaches_epub():
    import app

    root, window = visible_window()
    window.pdf = make_group_pdf()
    window.show_book(convert.analyze(window.pdf))
    chapter2 = next(i for i in window.tree.get_children() if window.tree.set(i, "title") == "Capitulo 2: Final")
    window.set_kind(chapter2, app.OFF)
    first = first_title(window)
    window.tree.selection_set(first)
    root.update()
    start = int(first) + 1
    end = next(k for k in range(start, len(window.book.blocks)) if window.book.blocks[k].kind == "heading")
    window.apply_edit([start, end, "La espada Excalibur brillaba mucho.\n# Interludio perdido\nOtro parrafo."], first)
    titles = [(window.tree.set(i, "title"), window.tree.set(i, "inc")) for i in window.tree.get_children()]
    window.close()
    assert titles == [("Capitulo 1: Inicio", app.ON), ("Interludio perdido", app.OFF), ("Agradecimientos", app.CUT),
                      ("Capitulo 2: Final", app.OFF)], titles

    root, window = visible_window()
    window.pdf = TMP / "grupo.pdf"
    window.show_book(convert.analyze(window.pdf))
    again = [(window.tree.set(i, "title"), window.tree.set(i, "inc")) for i in window.tree.get_children()]
    window.close()
    assert again == titles, again
    results = app._batch([str(window.pdf)], {"shrink": False})
    assert results == [("grupo.pdf", "", [])], results
    text = epub_text(TMP / "grupo.epub")
    assert "brillaba mucho." in text and "Interludio perdido" in text and "discord" not in text, text


def test_editor_sheet_round_trip():
    """La hoja WYSIWYG vuelve a dar el texto de section_text tal cual: aceptar sin cambios no toca el libro."""
    import qtk
    from PySide6.QtGui import QImage, QTextDocument

    import editor

    qtk.app()
    lines = [(2, "Uno <i>dos</i> tres"), (3, "<b>uno <i>dos</i></b>"), (4, "# Capítulo <i>1</i>"),
             (5, "[Ilustración 5]"), (6, "* * *"), (7, "[Separador 7]"), (8, "A & B < C [Nota: aclaración]"),
             (9, "<i>a</i><b>b</b>"), (10, "<i><b>al revés</b></i>")]
    doc = QTextDocument()
    picture = QImage(40, 20, QImage.Format.Format_RGB32)
    editor.load(doc, lines, {5: picture, 7: "❖"})
    dumped = editor.dump(doc)
    sources = [editor.source_of(doc.findBlockByNumber(n)) for n in range(doc.blockCount())]
    assert dumped == "\n\n".join(line for _, line in lines), dumped
    assert sources == [2, 3, 4, 5, 6, 7, 8, 9, 10], sources


def test_editor_pastes_html_with_italics():
    """Pegar de Word o de la web (HTML) conserva cursiva, negrita (también la de un estilo font-weight) y
    superíndice; colores y tamaños no. «[Ilustración 1]» de fuera entra como texto, no como ilustración."""
    import qtk
    from PySide6.QtCore import QMimeData
    from PySide6.QtGui import QTextCursor

    import editor

    qtk.app()
    sheet = editor.Sheet()
    sheet.set_content([(1, "Inicio.")], {})
    sheet.moveCursor(QTextCursor.MoveOperation.End)
    mime = QMimeData()
    mime.setHtml('<p>Hola <i>mundo</i></p><p style="color:red;font-size:20pt"><span style="font-weight:700">Fuerte'
                 '</span> 1<sup>o</sup></p><p>[Ilustración 1]</p>')
    sheet.insertFromMimeData(mime)
    assert sheet.content().split("\n\n") == ["Inicio.Hola <i>mundo</i>", "<b>Fuerte</b> 1<sup>o</sup>",
                                             "[Ilustración 1]"], sheet.content()
    assert not sheet.objects(), sheet.objects()


def test_editor_sheet_actions_paste_and_undo():
    """Cursiva, negrita y título como en Word; Enter parte y el párrafo nuevo sigue apuntando a su bloque;
    unir deja un espacio; pegar de fuera trae solo texto; una ilustración no se duplica al pegarla."""
    import qtk
    from PySide6.QtCore import QMimeData
    from PySide6.QtGui import QImage, QTextCursor

    import editor

    qtk.app()
    sheet = editor.Sheet()
    sheet.set_content([(1, "Fin."), (2, "Dijo hola, y se fue."), (3, "[Ilustración 3]")],
                      {3: QImage(40, 20, QImage.Format.Format_RGB32)})

    def select(start, end):
        c = sheet.textCursor()
        c.setPosition(start)
        c.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        sheet.setTextCursor(c)

    def first():
        return sheet.content().split(chr(10) * 2)[0]

    seen = []
    select(0, 3)
    sheet.toggle_italic()
    seen.append(first())                                     # cursiva sobre "Fin"
    sheet.toggle_italic()
    seen.append(first())                                     # otra vez: la quita
    select(0, 3)
    sheet.toggle_bold()
    seen.append(first())
    sheet.undo()
    select(0, 0)
    sheet.toggle_heading()
    seen.append(first())                                     # título
    sheet.undo()
    select(0, 0)
    sheet.join_next()
    seen.append(first())                                     # unido con un espacio
    sheet.undo()
    seen.append(first())                                     # un Ctrl+Z deshace la unión entera
    select(9, 9)                                             # "Fin." / "Dijo| hola"
    sheet.split_here()
    seen.append(sheet.content().split(chr(10) * 2)[1:3])
    seen.append(sheet.current_source())                      # el trozo nuevo sigue siendo del bloque 2
    sheet.undo()
    mime = QMimeData()
    mime.setHtml("<p style='font-size:30pt'><b>Pegado</b> de fuera</p>")
    mime.setText("Pegado de fuera")
    sheet.moveCursor(QTextCursor.MoveOperation.End)
    sheet.split_here()
    sheet.insertFromMimeData(mime)
    seen.append(sheet.content().split(chr(10) * 2)[-1])      # con su negrita, sin el tamaño
    sheet.selectAll()
    copy = sheet.createMimeDataFromSelection()
    sheet.moveCursor(QTextCursor.MoveOperation.End)
    sheet.insertFromMimeData(copy)
    seen.append(sheet.content().count("[Ilustración 3]"))    # la copia de la ilustración no se pega
    select(0, 0)
    sheet.textCursor().insertText("X")
    sheet.selectAll()
    cut = sheet.createMimeDataFromSelection()
    sheet.textCursor().removeSelectedText()
    sheet.insertFromMimeData(cut)
    seen.append(sheet.content().count("[Ilustración 3]"))    # cortar y pegar la mueve
    assert seen == ["<i>Fin</i>.", "Fin.", "<b>Fin</b>.", "# Fin.", "Fin. Dijo hola, y se fue.", "Fin.",
                    ["Dijo", "hola, y se fue."], 2, "<b>Pegado</b> de fuera", 1, 1], seen


def test_editor_sheet_superscript_small_caps_right():
    """Superíndice, versalitas y párrafo a la derecha se cargan y vuelven igual; los botones de la hoja los ponen
    en lo elegido; derecha y centrado se excluyen; Ctrl+D pone a la derecha y Ctrl+Mayús+= el superíndice."""
    import qtk
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QTextCursor
    from PySide6.QtTest import QTest

    import editor

    qtk.app()
    sheet = editor.Sheet()
    lines = [(1, "El 1.<sup>er</sup> de <sc>Rango S</sc>."), (2, "<derecha>Firmado: Rudeus"), (3, "<center>Uno dos"),
             (4, "tres cuatro")]
    sheet.set_content(lines, {})
    same = sheet.content() == "\n\n".join(line for _, line in lines)

    def at(n, start=0, end=None):
        block = sheet.document().findBlockByNumber(n)
        c = sheet.textCursor()
        c.setPosition(block.position() + start)
        if end is not None:
            c.setPosition(block.position() + end, QTextCursor.MoveMode.KeepAnchor)
        sheet.setTextCursor(c)

    def line(n):
        return sheet.content().split("\n\n")[n]

    at(3, 0, 4)
    sheet.toggle_sup()
    at(3, 5, 11)
    sheet.toggle_small_caps()
    marked = line(3)
    at(2)
    sheet.toggle_right()
    right = line(2)
    at(1)
    sheet.toggle_right()  # quita la derecha
    left = line(1)
    at(3)
    QTest.keyClick(sheet, Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier)
    keyed = line(3)
    at(0, 0, 2)
    QTest.keyClick(sheet, Qt.Key.Key_Equal, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    assert same, sheet.content()
    assert marked == "<sup>tres</sup> <sc>cuatro</sc>", marked
    assert right == "<derecha>Uno dos" and left == "Firmado: Rudeus", (right, left)
    assert keyed.startswith("<derecha>"), keyed
    assert line(0).startswith("<sup>El</sup> 1.<sup>er</sup>"), line(0)


def test_editor_sheet_links_center_levels_case_and_search():
    """Enlaces, párrafos centrados y tipo de título se ven en la hoja y vuelven al formato del texto; Mayús+F3
    cambia mayúsculas; buscar y reemplazar en la hoja, con un solo deshacer."""
    import qtk
    from PySide6.QtGui import QTextCursor

    import editor

    qtk.app()
    sheet = editor.Sheet()
    lines = [(1, "# Capítulo 1"), (2, "# El lado de Teto"), (3, 'Ver <a href="https://example.com/?a=1&b=2">la web</a>.'),
             (4, "<center>Querida Teto:"), (5, "hola mundo, Hola otra vez.")]
    sheet.set_content(lines, {}, levels={1: "Capítulo", 2: "Subtítulo"})
    same = sheet.content() == "\n\n".join(line for _, line in lines)

    def at(n, start=0, end=None):
        block = sheet.document().findBlockByNumber(n)
        c = sheet.textCursor()
        c.setPosition(block.position() + start)
        if end is not None:
            c.setPosition(block.position() + end, QTextCursor.MoveMode.KeepAnchor)
        sheet.setTextCursor(c)

    def line(n):
        return sheet.content().split("\n\n")[n]

    seen = []
    at(1)
    seen.append(sheet.heading_level())                       # Subtítulo
    sheet.set_heading("Capítulo")
    seen.append(sheet.heading_level())
    at(2, 5)
    seen.append(sheet.link_at())                             # dentro del enlace
    at(4, 0, 4)                                              # "hola"
    sheet.set_link("https://teto.example")
    seen.append(line(4))
    sheet.undo()
    at(3)
    sheet.toggle_center()
    seen.append(line(3))                                     # ya no centrado
    sheet.undo()
    at(4, 0, 10)                                             # "hola mundo"
    sheet.change_case()
    seen.append(line(4)[:10])                                # Hola Mundo
    sheet.change_case()
    seen.append(line(4)[:10])                                # HOLA MUNDO
    sheet.undo()
    sheet.undo()
    at(0)
    found = [sheet.find_next("hola"), sheet.textCursor().selectedText()]
    seen.append(found)
    seen.append(sheet.replace_all("hola", "adiós"))
    seen.append(line(4))
    sheet.undo()                                             # un solo paso
    seen.append(line(4))
    at(4)
    sheet.insert_note()
    sheet.textCursor().insertText("aclaración")
    seen.append(line(4)[:18])
    assert same, sheet.content()
    assert seen == ["Subtítulo", "Capítulo", "https://example.com/?a=1&b=2",
                    '<a href="https://teto.example">hola</a> mundo, Hola otra vez.', "Querida Teto:",
                    "Hola Mundo", "HOLA MUNDO", [True, "hola"], 2, "adiós mundo, adiós otra vez.",
                    "hola mundo, Hola otra vez.", "[Nota: aclaración]"], seen


def test_editor_sheet_looks_and_spelling():
    """Los títulos se ven grandes y en negrita sin guardar negrita; las palabras desconocidas salen subrayadas."""
    import qtk

    import editor

    qtk.app()
    sheet = editor.Sheet()
    sheet.set_content([(1, "# Título"), (2, "Ella dijó que sí.")], {})
    sheet.look.set_unknown({"dijó"})
    qtk.pump()
    heading = sheet.document().findBlockByNumber(0).layout().formats()
    bold = any(f.format.fontWeight() >= 600 for f in heading)
    assert bold and sheet.content() == "# Título" + chr(10) * 2 + "Ella dijó que sí.", (bold, sheet.content())
    assert sheet.misspelled() == ["dijó"], sheet.misspelled()


def test_text_editor_opens_section_and_saves_only_changes():
    """El editor muestra la sección como section_text; aceptar sin cambios no toca el libro (los párrafos
    conservan su página), un cambio llega al libro, y cancelar con cambios pregunta antes de descartarlos."""
    import app
    from PySide6.QtGui import QTextCursor

    root, window = visible_window()
    window.pdf = make_novel()
    window.show_book(convert.analyze(window.pdf))
    before = [(b.kind, b.html, b.page) for b in window.book.blocks]
    window.tree.selection_set("1")  # "Capitulo 1: Inicio": su sección son los bloques 2..5
    editor = window.edit_text()
    lines = editor.sheet.content().split(chr(10) * 2)
    shown = all(line in lines for line in convert.section_text(window.book, 2, 6).split(chr(10) * 2))
    source = editor.sheet.current_source()
    root.update()  # se maximiza ya mostrada (en Windows, maximizada al crearla quedaba pequeña)
    grabbed = editor.win.isModal() and editor.win.isMaximized()
    editor.sheet.moveCursor(QTextCursor.MoveOperation.End)
    editor.sheet.split_here()  # un párrafo vacío: no es un cambio (aceptar re-paginaría la sección)
    blank_only = not editor.dirty()
    editor.save()
    same = [(b.kind, b.html, b.page) for b in window.book.blocks] == before
    editor = window.edit_text()
    editor.sheet.goto_source(2)
    editor.sheet.textCursor().insertText("Hola ")
    original, answers = app.messagebox.askyesno, []
    try:
        app.messagebox.askyesno = lambda *args, **kwargs: answers.append(args) or False
        editor.cancel()
        kept = editor.win.isVisible()
        app.messagebox.askyesno = lambda *args, **kwargs: True
        editor.cancel()
        closed = not editor.win.isVisible()
    finally:
        app.messagebox.askyesno = original
    editor = window.edit_text()
    editor.sheet.goto_source(2)
    editor.sheet.textCursor().insertText("Hola ")
    editor.save()
    changed = window.book.blocks[2].html
    window.close()
    assert shown and source == 1 and same, (shown, source, same)
    assert grabbed and blank_only, (grabbed, blank_only)
    assert len(answers) == 1 and kept and closed, (answers, kept, closed)
    assert changed.startswith("Hola "), changed


def test_text_editor_navigates_chapters_and_saves_only_changes():
    """Como Word: la cinta dice en qué capítulo se está («Capítulo 2 de 2 · …») y su menú y ◀ ▶ llevan a los
    demás; hojear el PDF hasta otro capítulo lleva la hoja ahí. Lo editado en un capítulo sigue al volver y se
    marca con «•». Al aceptar se guarda solo lo cambiado: un título renombrado conserva su tipo y su ✓, todo se
    deshace con un Ctrl+Z. La página del PDF más pequeña que el panel queda en el medio."""
    import app

    root, window = visible_window()
    window.pdf = make_novel()
    window.show_book(convert.analyze(window.pdf))
    chapters = [i for i in window.tree.get_children() if window.tree.set(i, "inc") == app.ON]
    first, second = chapters[0], chapters[1]
    window.tree.set(first, "title", "Capítulo 1: El inicio")  # renombrado en la lista
    window.tree.set(second, "ok", "✓")
    before = {i: (window.tree.set(i, "inc"), window.tree.set(i, "ok")) for i in window.tree.get_children()}
    blocks_before = [(b.kind, b.html, b.page) for b in window.book.blocks]
    window.tree.selection_set(second)
    editor = window.edit_text()
    root.update()
    sheet = editor.sheet

    def lines():
        return sheet.content().split("\n\n")

    opened = (editor.current == window.section_bounds(int(second)) and lines()[0].startswith("# ")
              and sheet.current_source() == int(second))
    tooltip = editor.chapter_btn.toolTip()
    editor.zoom(-50)  # al 50 %: la página cabe y va en el medio del panel
    root.update()
    view, page = editor.scroll.viewport().geometry(), editor.canvas.geometry()
    centered = abs(page.center().y() - view.height() / 2) <= 2 and abs(page.center().x() - view.width() / 2) <= 2
    editor.mark_changed()
    labels = [editor.chapter_list.item(n).text() for n in range(editor.chapter_list.count())]
    editor.chapter_list.itemClicked.emit(editor.chapter_list.item(labels.index("Capítulo 1: El inicio")))
    shows_first = lines()[0] == "# Capítulo 1: El inicio" and editor.chapter_btn.toolTip().startswith("Capítulo 1 de 2")
    sheet.document().find("Capítulo 1: El inicio").insertText("Capítulo 1: El comienzo")
    editor.mark_changed()
    marked = any(editor.chapter_list.item(n).text().endswith("•") for n in range(editor.chapter_list.count()))
    editor.step_section(1)  # ▶: el capítulo siguiente
    on_second = editor.current == window.section_bounds(int(second))
    sheet.goto_source(int(second) + 1)  # el primer párrafo del segundo capítulo
    sheet.textCursor().insertText("Nuevo. ")
    editor.open_section(*window.section_bounds(int(first)))
    kept = lines()[0] == "# Capítulo 1: El comienzo"  # lo editado sigue al volver
    # ▶ bajo la página del panel: salta al capítulo siguiente, no a la página siguiente
    next(b for b in editor.win.findChildren(app.QPushButton) if b.toolTip() == "Capítulo siguiente").click()
    synced = (editor.current == window.section_bounds(int(second))
              and editor.shown == ("page", window.book.blocks[int(second)].page))
    editor.save()
    rows = {i: (window.tree.set(i, "inc"), window.tree.set(i, "ok")) for i in window.tree.get_children()}
    title = window.tree.set(first, "title")
    paragraph = window.book.blocks[int(second) + 1].html
    edits = list(window.edits)
    window.undo()
    undone = ([(b.kind, b.html, b.page) for b in window.book.blocks] == blocks_before
              and window.tree.set(first, "title") == "Capítulo 1: El inicio")
    window.close()
    assert opened and tooltip.startswith("Capítulo 2 de 2: "), (opened, tooltip)  # el botón se acorta si no cabe
    assert centered, (view, page)
    assert shows_first and marked and on_second and kept and synced, (shows_first, marked, on_second, kept, synced)
    assert title == "Capítulo 1: El comienzo" and paragraph.startswith("Nuevo. "), (title, paragraph)
    assert rows == before, (rows, before)
    assert len(edits) == 1 and edits[0][1] - edits[0][0] == 1, edits  # solo el párrafo cambiado
    assert undone


def test_text_editor_steps_pdf_pages():
    """◀ ▶ bajo el PDF pasan de página (◀◀ ▶▶, de capítulo): la hoja va al primer párrafo de la página, cruzando
    al capítulo siguiente si hace falta, y el panel la dibuja. En la primera y la última página se apagan."""
    import app

    root, window = visible_window()
    window.pdf = make_novel()
    window.show_book(convert.analyze(window.pdf))
    first = next(i for i in window.tree.get_children() if window.tree.set(i, "inc") == app.ON)
    window.tree.selection_set(first)
    editor = window.edit_text()
    root.update()
    blocks, starts = window.book.blocks, editor.page_starts()
    pages = sorted(starts)
    editor.open_section(*next(s[1:3] for s in editor.sections if s[1] <= starts[pages[0]] < s[2]))
    editor.sheet.goto_source(starts[pages[0]])
    editor.draw_source()
    at_start = not editor.prev_page.isEnabled() and editor.next_page.isEnabled()
    seen, sections = [], set()
    for _ in pages[1:]:
        editor.next_page.click()
        k = editor.sheet.current_source()
        seen.append((blocks[k].page, editor.shown, k == starts[blocks[k].page]))
        sections.add(editor.current)
    at_end = editor.prev_page.isEnabled() and not editor.next_page.isEnabled()
    editor.prev_page.click()
    back = blocks[editor.sheet.current_source()].page
    window.close()
    assert len(pages) >= 3, pages
    assert seen == [(p, ("page", p), True) for p in pages[1:]], seen
    assert len(sections) >= 2, sections  # cruzó de capítulo
    assert at_start and at_end and back == pages[-2], (at_start, at_end, back)


def test_text_editor_chapter_list_is_a_small_popup():
    """Con muchos capítulos (Mitsuha 1: 34) la lista ocupaba toda la pantalla en columnas: es un desplegable
    pequeño con scroll bajo el botón, que se filtra escribiendo y se cierra al elegir."""
    import app

    root, window = visible_window()
    blocks = []
    for n in range(40):
        blocks += [convert.Block("heading", n + 1, f"Capítulo {n}: Un título bastante largo para la lista número {n}"),
                   convert.Block("p", n + 1, "Texto.")]
    doc = pymupdf.open()
    for n in range(40):
        add_page(doc, n + 1, [(72, 100, f"Pagina {n}", 11, "helv")])
    window.pdf = TMP / f"cuarenta-{time_ns()}.pdf"
    doc.save(window.pdf)
    window.show_book(convert.Book("Libro", "", b"", blocks, list(range(0, 80, 2))))
    window.tree.selection_set("0")
    editor = app.TextEditor(window, "0", *window.section_bounds(0))
    root.update()
    editor.chapter_btn.click()
    root.update()
    pop = editor.chapter_popup
    shown, size = pop.isVisible(), pop.size()
    screen = pop.screen().availableGeometry()
    inside = screen.contains(pop.frameGeometry())
    editor.chapter_filter.setText("título bastante largo para la lista número 37")
    visible = [editor.chapter_list.item(n).text() for n in range(editor.chapter_list.count())
               if not editor.chapter_list.item(n).isHidden()]
    editor.chapter_filter.returnPressed.emit()
    root.update()
    went = editor.current == window.section_bounds(74) and not pop.isVisible()
    editor.win.done(0)
    window.close()
    assert shown and size.width() <= 480 and size.height() <= 420 and inside, (shown, size, screen, pop.frameGeometry())
    assert len(visible) == 1 and visible[0].startswith("Capítulo 37"), visible
    assert went


def test_text_editor_styles_links_and_find_reach_the_book():
    """La cinta del editor: un párrafo nuevo con estilo Subtítulo llega a la lista como subtítulo, un capítulo
    pasado a Subtítulo cambia su fila; un enlace puesto con la barra llega al libro (una dirección sin http
    no); buscar salta de capítulo y «Reemplazar todo» cambia todos los capítulos."""
    import app

    root, window = visible_window()
    window.pdf = make_novel()
    window.show_book(convert.analyze(window.pdf))
    chapters = [i for i in window.tree.get_children() if window.tree.set(i, "inc") == app.ON]
    first, second = chapters[0], chapters[1]
    window.tree.selection_set(first)
    editor = window.edit_text()
    root.update()
    sheet = editor.sheet
    sheet.goto_source(int(first) + 1)
    sheet.split_here()
    cursor = sheet.textCursor()
    cursor.movePosition(cursor.MoveOperation.PreviousBlock)  # el párrafo nuevo, vacío, queda encima
    sheet.setTextCursor(cursor)
    sheet.textCursor().insertText("Lado de Teto")
    editor.style.activated.emit(app.STYLES.index("Subtítulo"))
    cursor = sheet.document().find("Primera")  # una palabra del primer párrafo
    sheet.setTextCursor(cursor)
    word = cursor.selectedText()
    editor.show_link_bar()
    editor.link_entry.setText("example.com")
    editor.apply_link()
    refused = editor.link_frame.isVisible() and "http" in editor.link_info.text()
    editor.link_entry.setText("https://example.com")
    editor.apply_link()
    editor.find_entry.setText("parrafo")
    jumped = editor.find(1) and editor.find(1)
    editor.find_entry.setText("Final")
    editor.find(1)
    other_chapter = editor.current == window.section_bounds(int(second))
    editor.find_entry.setText("linea")
    editor.replace_entry.setText("fila")
    editor.replace_everywhere()
    replaced = editor.find_info.text()
    editor.open_section(*window.section_bounds(int(second)))
    sheet.goto_source(int(second))
    editor.style.activated.emit(app.STYLES.index("Subtítulo"))
    editor.save()
    kinds = {window.tree.set(i, "title"): window.tree.set(i, "inc") for i in window.tree.get_children()}
    text = " ".join(b.html for b in window.book.blocks)
    window.close()
    assert refused, editor.link_info.text()
    assert kinds.get("Lado de Teto") == app.OFF and kinds["Capitulo 2: Final"] == app.OFF, kinds
    assert f'<a href="https://example.com">{word}</a>' in text, text[:300]
    assert jumped and other_chapter, (jumped, other_chapter)
    assert replaced.endswith("reemplazos") and "linea" not in text.lower() and "fila" in text, (replaced, text[:200])


def sheet_with(lines):
    """Hoja suelta con lines de section_text; el párrafo n viene del bloque n."""
    import editor
    import qtk
    qtk.app()
    sheet = editor.Sheet()
    sheet.set_content(list(enumerate(lines)), {})
    return sheet



def test_sheet_regex_find_and_replace_with_groups():
    """Con expresión regular, buscar casa el patrón y reemplazar admite grupos; una expresión que casa vacío no
    se queda en un bucle."""
    sheet = sheet_with(["Tenía 12 años y <i>3</i> gatos.", "Nada aquí."])
    found = sheet.find_next("[0-9]+", regex=True) and sheet.textCursor().selectedText()
    replaced = sheet.replace_current("([0-9]+)", r"«\1»", regex=True)
    count = sheet.replace_all("([0-9]+) gatos", r"\1 perros", regex=True)
    empty = sheet.replace_all("x*", "y", regex=True)
    text = sheet.toPlainText()
    plain = sheet.replace_all("[0-9]", "N")  # sin regex, los corchetes son texto
    assert found == "12" and replaced, (found, replaced)
    assert count == 1 and empty == 0 and plain == 0, (count, empty, plain)
    assert text.startswith("Tenía «12» años y 3 perros."), text


def test_window_editor_regex_box_marks_bad_expressions():
    """La casilla «.*» de la barra de búsqueda: una expresión mal escrita pone el aviso y no busca ni reemplaza;
    bien escrita, «Reemplazar todo» usa los grupos en todo el libro."""
    import app

    root, window = visible_window()
    window.pdf = make_novel()
    window.show_book(convert.analyze(window.pdf))
    window.tree.selection_set([i for i in window.tree.get_children() if window.tree.set(i, "inc") == app.ON][0])
    editor = window.edit_text()
    root.update()
    editor.show_find_bar(True)
    editor.regex_box.setChecked(True)
    editor.find_entry.setText("(linea")
    bad = editor.find_info.text(), editor.find(1)
    editor.replace_everywhere()
    editor.find_entry.setText(r"(l)inea")
    ok = editor.find_info.text()
    editor.replace_entry.setText(r"\1ínea")
    editor.replace_everywhere()
    editor.save()
    text = " ".join(b.html for b in window.book.blocks)
    window.close()
    assert bad[0] == "Expresión no válida: falta cerrar un paréntesis (carácter 1)" and bad[1] is False, bad
    assert ok == "", ok
    assert "linea" not in text and "línea" in text, text[:300]


def test_window_editor_reads_aloud_paragraph_by_paragraph():
    """«Leer» lee desde el párrafo del cursor, uno a uno, saltando ilustraciones, con el fondo del que se lee; un
    clic en la hoja, Esc o cambiar de capítulo la paran; sin voz en español, un aviso lo explica."""
    import app
    from PySide6.QtCore import QObject, Signal

    class FakeSpeaker(QObject):
        done, failed = Signal(), Signal(str)
        said, stops = [], []

        def say(self, text):
            self.said.append(text)

        def stop(self):
            self.stops.append(1)

    original, info = app.Speaker, app.messagebox.showinfo
    told = []
    app.Speaker = FakeSpeaker
    app.messagebox.showinfo = lambda title, message, **_: told.append(message)
    try:
        root, window = visible_window()
        window.pdf = make_novel()
        window.show_book(convert.analyze(window.pdf))
        window.tree.selection_set([i for i in window.tree.get_children() if window.tree.set(i, "inc") == app.ON][0])
        editor = window.edit_text()
        root.update()
        sheet = editor.sheet
        first = sheet.document().firstBlock().next()  # tras el título
        sheet.setTextCursor(app.QTextCursor(first))
        editor.read_btn.click()
        speaker = editor.speaker
        speaker.done.emit()
        two = list(speaker.said)
        marked = any(s.cursor.block() == editor.reading for s in sheet.extraSelections())
        checked = editor.read_btn.isChecked()
        sheet.setTextCursor(app.QTextCursor(sheet.document().firstBlock()))  # como un clic en otro sitio
        clicked = editor.reading, editor.read_btn.isChecked()
        editor.read_btn.click()
        editor.win.reject()  # Esc: calla la voz y no cierra el editor
        escaped = editor.reading, editor.win.isVisible()
        editor.read_btn.click()
        speaker.failed.emit(app.NO_VOICE)
        failed = editor.reading
        window.close()
    finally:
        app.Speaker, app.messagebox.showinfo = original, info
    texts = [first.text(), first.next().next().text()]  # en medio, una ilustración: se salta
    assert first.next().text() == "￼" and two == texts, (two, texts)
    assert told == [app.NO_VOICE], told
    assert marked and checked, (marked, checked)
    assert clicked == (None, False), clicked
    assert escaped == (None, True), escaped
    assert failed is None


def test_window_reviewer_comments():
    """Un comentario puesto con clic derecho en el editor: fondo amarillo, aviso «Comentario del revisor» en el
    informe, guardado en la revisión y no en el EPUB; sobrevive a reabrir y a editar un poco el párrafo; «Quitar
    comentario» lo borra."""
    import app

    original = app._ask_note
    app._ask_note = lambda parent, note: "Revisar con el traductor: ¿«Tino» o «Tiño»?"
    try:
        root, window = visible_window()
        window.pdf = make_novel()
        window.show_book(convert.analyze(window.pdf))
        window.tree.selection_set([i for i in window.tree.get_children() if window.tree.set(i, "inc") == app.ON][0])
        editor = window.edit_text()
        root.update()
        sheet = editor.sheet
        block = sheet.document().firstBlock().next()
        k = app.editor.source_of(block)
        menu = editor.mark_menu(sheet.cursorRect(app.QTextCursor(block)).center())
        [add] = [a for a in menu.actions() if a.text() == "Comentario…"]
        title = editor.mark_menu(sheet.cursorRect(app.QTextCursor(sheet.document().firstBlock())).center())
        on_title = [a.text() for a in title.actions() if "omentario" in a.text()]  # Re Zero: «Parte 1» fallaba
        add.trigger()
        marked = [s for s in sheet.extraSelections() if s.format.background().color().name() ==
                  app.theme.mark_colors(False)["comment"]]
        editor.save()
        report = [i for i in window.quality_report() if i.kind == "comment"]
        state = window.review_state()
        epub = TMP / "comentario.epub"
        chapters, removed, breaks = app._plan(window.book, window.rows())
        convert.write_epub(window.book, chapters, epub, removed=removed)
        in_epub = "traductor" in epub_text(epub)
        window.save_review()
        # El párrafo cambia un poco (otra edición): el comentario sigue con él
        b = window.book.blocks[k]
        window.book.blocks[k] = convert.Block(b.kind, b.page, b.html.replace("relleno", "Relleno", 1))
        kept = [i.block for i in window.comment_issues()]
        window.show_book(convert.analyze(window.pdf))  # reabrir: la revisión guardada trae el comentario
        reopened = [i.block for i in window.comment_issues()]
        window.remove_comment(window.comment_issues()[0])
        left = window.comments
        window.close()
    finally:
        app._ask_note = original
    assert marked, "sin fondo amarillo en el párrafo comentado"
    assert on_title == [], on_title
    assert [(i.block, i.level, i.problem) for i in report] == [
        (k, "Revisar", "Revisar con el traductor: ¿«Tino» o «Tiño»?")], report
    assert state["comments"][0]["note"].startswith("Revisar con el traductor") and not in_epub, state["comments"]
    assert kept == [k] and reopened == [k], (kept, reopened, k)
    assert left == [], left

def test_sheet_symbols_and_typography():
    """Raya, puntos y comillas con Ctrl+Alt como Word; con texto elegido, las comillas lo envuelven. AltGr+2
    (Ctrl+Alt) sigue escribiendo «@». «Corregir tipografía» pone rayas y comillas conservando la cursiva, en
    un solo paso de deshacer."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent, QTextCursor
    from PySide6.QtCore import QEvent
    sheet = sheet_with(["Hola mundo", '-Ven aquí -dijo <i>"Tino"</i>.', "Nada que cambiar."])

    def key(k, text=""):
        mods = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier
        sheet.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, k, mods, text))

    cursor = sheet.document().find("mundo")  # elegida
    sheet.setTextCursor(cursor)
    key(Qt.Key.Key_Q)
    sheet.moveCursor(QTextCursor.MoveOperation.EndOfBlock)
    key(Qt.Key.Key_Minus)
    key(Qt.Key.Key_Period)
    key(Qt.Key.Key_2, "@")  # AltGr+2 en un teclado español
    first = sheet.document().firstBlock().text()
    before = sheet.content()
    changed = sheet.fix_typography()
    fixed = sheet.content().split("\n\n")[1]
    sheet.undo()
    undone = sheet.content() == before
    assert first == "Hola «mundo»—…@", first
    assert changed == 1 and fixed == "—Ven aquí —dijo <i>«Tino»</i>.", (changed, fixed)
    assert undone, sheet.content()


def test_sheet_changes_and_restore():
    """Un párrafo editado cuenta como cambiado y vuelve a su línea original; uno partido o unido no se
    ofrece para volver (duplicaría o perdería texto): ahí está Deshacer."""
    from PySide6.QtGui import QTextCursor
    sheet = sheet_with(["Uno <i>dos</i>.", "Tres cuatro.", "Cinco seis.", "Siete."])
    loaded = [0, 1, 2, 3]
    doc = sheet.document()
    clean = sheet.changed_blocks()
    cursor = doc.find("dos")
    cursor.insertText("DOS")
    edited = [b.text() for b in sheet.changed_blocks()]
    first = doc.firstBlock()
    ok = sheet.can_restore(first, loaded)
    sheet.restore(first)
    restored = sheet.content().split("\n\n")[0]
    cursor = doc.find("cuatro")  # partir «Tres cuatro.»
    cursor.setPosition(cursor.selectionStart())
    sheet.setTextCursor(cursor)
    sheet.split_here()
    split = [sheet.can_restore(b, loaded) for b in sheet.block_of(1)]
    cursor = doc.find("Cinco")
    sheet.setTextCursor(cursor)
    sheet.join_next()  # «Cinco seis.» + «Siete.»
    joined = sheet.can_restore(sheet.block_of(2)[0], loaded)
    assert clean == [] and edited == ["Uno DOS."] and ok, (clean, edited, ok)
    assert restored == "Uno <i>dos</i>.", restored
    assert split == [False, False] and joined is False, (split, joined)


def test_sheet_replace_sources_and_split_chapter():
    """El arreglo de «Párrafo partido» une dos párrafos con una ilustración en medio y la ilustración sigue;
    un arreglo vacío quita el párrafo. «Dividir capítulo» pone un título de capítulo en el cursor."""
    import editor
    sheet = sheet_with(["Empieza la frase", "[Ilustración 1]", "y sigue aquí.", "Línea suelta 12", "Final."])
    sheet.replace_sources(0, 3, "Empieza la frase y sigue aquí.\n\n[Ilustración 1]")
    joined = sheet.content().split("\n\n")
    sheet.replace_sources(3, 4, "")
    dropped = sheet.content().split("\n\n")
    cursor = sheet.document().find("sigue")
    cursor.setPosition(cursor.selectionStart())
    sheet.setTextCursor(cursor)
    sheet.split_chapter("Capítulo 2: Otro día")
    levels = editor.dump_levels(sheet.document())
    refused = not sheet.split_chapter("Otro")  # el cursor está en el título
    assert joined == ["Empieza la frase y sigue aquí.", "[Ilustración 1]", "Línea suelta 12", "Final."], joined
    assert dropped == ["Empieza la frase y sigue aquí.", "[Ilustración 1]", "Final."], dropped
    assert levels[:3] == [("Empieza la frase y", None), ("# Capítulo 2: Otro día", "Capítulo"),
                          ("sigue aquí.", None)], levels
    assert refused


def editor_on(blocks, candidates):
    """Ventana con un libro hecho a mano (y un PDF de una página por bloque) y su editor en la primera sección."""
    import app
    root, window = visible_window()
    doc = pymupdf.open()
    for n in range(max(b.page for b in blocks)):
        add_page(doc, n + 1, [(72, 100, f"Pagina {n}", 11, "helv")])
    window.pdf = TMP / f"hecho-{time_ns()}.pdf"
    doc.save(window.pdf)
    window.show_book(convert.Book("Libro", "", b"", blocks, candidates))
    window.tree.selection_set("0")
    return root, window, app.TextEditor(window, "0", *window.section_bounds(0))


def test_editor_draft_recovers_after_crash():
    """El editor escribe un borrador con lo cambiado; si la aplicación se cerró sin aceptar, al abrirlo otra vez
    ofrece recuperarlo: vuelve el texto, el cambio se ve como cambio (F7) y el párrafo igual conserva su página.
    Con el libro cambiado desde entonces, esa sección no se recupera y se avisa. Aceptar borra el borrador, y sin
    cambios no se deja ninguno."""
    import app
    from PySide6.QtGui import QTextCursor

    B = convert.Block
    blocks = [B("heading", 1, "Capítulo 1"), B("p", 1, "Primer párrafo."), B("p", 2, "Segundo párrafo.")]
    root, window, editor = editor_on(blocks, [0])
    path = app._draft_path(window.pdf)
    editor.write_draft()
    none_clean = not path.exists()
    c = editor.sheet.textCursor()
    c.setPosition(editor.sheet.document().findBlockByNumber(2).position())
    c.insertText("Nuevo. ")
    editor.write_draft()
    draft = path.read_text(encoding="utf-8")
    editor.win.done(0)  # cerrar sin aceptar: el borrador se va
    closed_drops = not path.exists()
    asked, infos = [], []
    saved = app.messagebox.askyesno, app.messagebox.showinfo
    app.messagebox.askyesno = lambda title, message, **kw: asked.append(message) or True
    app.messagebox.showinfo = lambda title, message, **kw: infos.append(message)
    try:
        path.write_text(draft, encoding="utf-8")  # como si la aplicación se hubiera cerrado de golpe
        editor = app.TextEditor(window, "0", *window.section_bounds(0))
        recovered = editor.sheet.content().split("\n\n")
        changed = [b.blockNumber() for b in editor.sheet.changed_blocks()]
        dirty = editor.dirty()
        editor.save()
        saved_text = [convert._plain(b) for b in window.book.blocks]
        kept_page = window.book.blocks[1].page
        accept_drops = not path.exists()
        path.write_text(draft.replace("Primer párrafo.", "Otro texto."), encoding="utf-8")
        editor = app.TextEditor(window, "0", *window.section_bounds(0))
        stale = editor.sheet.content()
        editor.win.done(0)
    finally:
        app.messagebox.askyesno, app.messagebox.showinfo = saved
        window.close()
    assert none_clean and closed_drops, (none_clean, closed_drops)
    assert len(asked) == 2 and "¿Recuperarlos?" in asked[0], asked
    assert recovered == ["# Capítulo 1", "Primer párrafo.", "Nuevo. Segundo párrafo."], recovered
    assert changed == [2] and dirty, (changed, dirty)
    assert saved_text == ["Capítulo 1", "Primer párrafo.", "Nuevo. Segundo párrafo."] and kept_page == 1, saved_text
    assert accept_drops
    assert "Otro texto." not in stale and infos and "el libro cambió" in infos[0], (stale, infos)


def test_editor_draft_written_while_spelling_repaints():
    """La ortografía repinta la hoja y avisa de cambios sin parar mientras revisa el libro: el borrador se escribe
    igual a los 5 s del primer cambio, no espera a que la hoja se quede quieta (con ella, nunca se escribía)."""
    from PySide6.QtTest import QTest
    import app

    B = convert.Block
    root, window, editor = editor_on([B("heading", 1, "Capítulo 1"), B("p", 1, "Uno.")], [0])
    QTest.keyClicks(editor.sheet, "hola ")
    for _ in range(30):  # 6 s de repintados, uno cada 200 ms
        editor.sheet.textChanged.emit()
        QTest.qWait(200)
    written = app._draft_path(window.pdf).exists()
    editor.win.done(0)
    window.close()
    assert written, "el borrador no se escribió mientras la hoja avisaba de cambios"


def test_editor_resume_bar_goes_where_you_left():
    """Al cerrar el editor se recuerda el párrafo del cursor, también en la revisión; abrirlo en otro capítulo
    ofrece «Lo dejaste en «Capítulo 2»» e «Ir allí» lleva a ese párrafo. En el mismo capítulo no sale la barra."""
    from PySide6.QtGui import QTextCursor
    import app

    B = convert.Block
    blocks = [B("heading", 1, "Capítulo 1"), B("p", 1, "Uno."), B("heading", 2, "Capítulo 2"), B("p", 2, "Dos."),
              B("p", 2, "Tres.")]
    root, window, editor = editor_on(blocks, [0, 2])
    editor.open_section(*window.section_bounds(2), 4)
    editor.win.done(0)
    remembered = window.editor_at
    saved = window.review_state()["editor_at"]
    editor = app.TextEditor(window, "0", *window.section_bounds(0))
    shown = not editor.resume_frame.isHidden() and "«Capítulo 2»" in editor.resume_label.text()
    editor.resume_go.click()
    went = editor.current == window.section_bounds(2) and editor.sheet.current_source() == 4
    hidden_after = editor.resume_frame.isHidden()
    editor.win.done(0)
    editor = app.TextEditor(window, "2", *window.section_bounds(2))
    same_chapter = editor.resume_frame.isHidden()
    editor.win.done(0)
    window.close()
    assert remembered == 4 and saved == 4, (remembered, saved)
    assert shown and went and hidden_after and same_chapter, (shown, went, hidden_after, same_chapter)


def test_editor_navigation_panel():
    """F9 abre el panel de navegación con los títulos, avisos, comentarios y cambios del capítulo y su cuenta; un
    clic lleva al párrafo. Se recuerda abierto para la próxima vez."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    import app

    B = convert.Block
    blocks = [B("heading", 1, "Capítulo 1"), B("p", 1, "Uno dos."), B("heading", 1, "Parte dos"), B("p", 1, "Tres."),
              B("p", 1, "Cuatro.")]
    root, window, editor = editor_on(blocks, [0])
    editor.issues = {3: [convert.Issue("x", 3, 1, "Menor", "Aviso de prueba", "", "")],
                     4: [convert.Issue("comment", 4, 1, "Revisar", "Comentario del revisor", "Mirar esto", "")]}
    c = editor.sheet.textCursor()
    c.setPosition(editor.sheet.document().findBlockByNumber(1).position())
    c.insertText("Cambiado. ")
    editor.refresh_marks()
    hidden = editor.nav.isHidden()
    QTest.keyClick(editor.sheet, Qt.Key.Key_F9)
    groups = [editor.nav.topLevelItem(n) for n in range(editor.nav.topLevelItemCount())]
    names = [g.text(0) for g in groups]
    notes = [groups[2].child(0).text(0)] if groups[2].childCount() else []
    target = groups[1].child(0)
    editor.nav_go(target)
    went = editor.sheet.textCursor().block().blockNumber()
    remembered = window.settings.get("editor_nav")
    editor.win.done(0)
    again = app.TextEditor(window, "0", *window.section_bounds(0))
    shown_again = not again.nav.isHidden()
    QTest.keyClick(again.sheet, Qt.Key.Key_F9)
    again.win.done(0)
    window.close()
    assert hidden, "el panel empieza oculto"
    assert names == ["Títulos (2)", "Avisos (1)", "Comentarios (1)", "Cambios (1)"], names
    assert notes == ["Mirar esto"] and went == 3, (notes, went)
    assert remembered is True and shown_again and window.settings["editor_nav"] is False


def test_editor_shows_changes():
    """«Mostrar cambios»: en el párrafo cambiado se subraya lo puesto («perro») y la barra muestra el original con
    lo quitado tachado y lo puesto subrayado; en un párrafo sin cambios la barra se oculta. Apagarlo quita todo."""
    from PySide6.QtGui import QTextCharFormat, QTextCursor
    import editor as ed

    B = convert.Block
    root, window, editor = editor_on([B("heading", 1, "Capítulo 1"), B("p", 1, "El gato <i>negro</i> duerme."),
                                      B("p", 1, "Sin tocar.")], [0])
    sheet = editor.sheet
    block = sheet.document().findBlockByNumber(1)
    c = QTextCursor(block)
    c.setPosition(block.position() + 3)
    c.setPosition(block.position() + 7, QTextCursor.MoveMode.KeepAnchor)
    c.insertText("perro")
    sheet.setTextCursor(c)
    editor.show_changes_action.setChecked(True)

    def underlined(block, at):
        layout = block.layout()
        return any(r.start <= at < r.start + r.length
                   and r.format.underlineStyle() == QTextCharFormat.UnderlineStyle.SingleUnderline
                   for r in layout.formats())

    marked = underlined(block, 4) and not underlined(block, 0) and not underlined(block, 12)
    shown = not editor.diff_frame.isHidden()
    bar = editor.diff_label.text()
    sheet.setTextCursor(QTextCursor(sheet.document().findBlockByNumber(2)))
    editor.update_diff()
    hidden_clean = editor.diff_frame.isHidden()
    editor.show_changes_action.setChecked(False)
    gone = not underlined(block, 4)
    editor.win.done(0)
    window.close()
    assert ed.added_spans("El gato duerme.", "El perro duerme.") == [(3, 5)]
    assert marked and shown, (marked, shown, bar)
    assert ">gato</s>" in bar and ">perro</u>" in bar and "negro" in bar and "<i>" not in bar, bar
    assert hidden_clean and gone, (hidden_clean, gone)


def test_editor_focus_mode():
    """F11 deja solo la hoja: sin cinta ni panel del PDF. Esc vuelve a como estaba sin preguntar si se descartan
    los cambios, y el editor sigue abierto con ellos."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    import app

    B = convert.Block
    root, window, editor = editor_on([B("heading", 1, "Capítulo 1"), B("p", 1, "Uno.")], [0])
    editor.sheet.textCursor().insertText("Cambio. ")
    asked = []
    saved = app.messagebox.askyesno
    app.messagebox.askyesno = lambda *a, **kw: asked.append(a) or False
    try:
        QTest.keyClick(editor.sheet, Qt.Key.Key_F11)
        focused = editor.ribbon_bar.isHidden() and editor.side.isHidden() and not editor.sheet.isHidden()
        QTest.keyClick(editor.win, Qt.Key.Key_Escape)
        back = not editor.ribbon_bar.isHidden() and not editor.side.isHidden() and editor.win.isVisible()
        kept = editor.dirty()
    finally:
        app.messagebox.askyesno = saved
        editor.win.done(0)
        window.close()
    assert focused and back and kept and not asked, (focused, back, kept, asked)


def test_editor_work_log():
    """«Cifras» cuenta el trabajo: aceptar suma los párrafos cambiados y el tiempo de hoy, marcar revisado suma un
    capítulo (desmarcar no resta), y el resumen da hoy, los últimos 7 días y el total."""
    from datetime import date, timedelta
    import app

    B = convert.Block
    blocks = [B("heading", 1, "Capítulo 1"), B("p", 1, "Uno."), B("p", 1, "Dos."), B("heading", 2, "Capítulo 2"),
              B("p", 2, "Tres.")]
    root, window, editor = editor_on(blocks, [0, 3])
    today = date.today().isoformat()
    window.work = {"2000-01-01": {"minutes": 30, "paragraphs": 5, "reviewed": 1}}
    c = editor.sheet.textCursor()
    c.setPosition(editor.sheet.document().findBlockByNumber(1).position())
    c.insertText("Cambiado. ")
    editor.opened -= 120  # dos minutos abierto
    editor.toggle_reviewed()
    editor.open_section(*window.section_bounds(0))
    editor.toggle_reviewed()  # desmarcar
    editor.show_stats()
    stats = [w.text() for w in editor.stats_window.findChildren(app.QLabel)]
    editor.stats_window.close()
    editor.save()
    day = window.work[today]
    saved = window.review_state()["work"][today]
    old = (date.today() - timedelta(days=3)).isoformat()
    summary = app._work_summary({today: {"minutes": 10, "paragraphs": 2}, old: {"minutes": 50, "reviewed": 1},
                                 "2000-01-01": {"minutes": 5}}, today)
    window.close()
    assert day["paragraphs"] == 1 and day["reviewed"] == 1 and 2 <= day["minutes"] < 3, day
    assert saved == day, saved
    assert "Tu trabajo" in stats and "Párrafos cambiados" in stats and "2 min" in stats, stats
    assert summary == [("Hoy", 10, 2, 0), ("Últimos 7 días", 60, 2, 1), ("Total del libro", 65, 2, 1)], summary
    assert app._duration(65) == "1 h 05 min" and app._duration(45) == "45 min"


def test_editor_changes_illustration():
    """Clic derecho en una ilustración: «Cambiar ilustración…» la cambia en el libro (App.replace_image) y la hoja
    muestra ya la nueva con su forma; el texto no cuenta como cambiado."""
    import dataclasses
    from PySide6.QtGui import QTextCursor

    B = convert.Block
    wide = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 400, 100), False)
    wide.clear_with(200)
    root, window, editor = editor_on([B("heading", 1, "Capítulo 1"), B("image", 1, image=JPEG), B("p", 1, "Uno.")],
                                     [0])

    def fake(k, parent=None):
        window.book.blocks[k] = dataclasses.replace(window.book.blocks[k], image=wide.tobytes("png"))
        return parent is editor.win

    window.replace_image = fake

    def size():
        block = editor.sheet.document().findBlockByNumber(1)
        f = block.begin().fragment().charFormat().toImageFormat()
        return round(f.width() / f.height(), 1)

    before = size()
    pos = editor.sheet.cursorRect(QTextCursor(editor.sheet.document().findBlockByNumber(1))).center()
    actions = [a.text() for a in editor.mark_menu(pos).actions()]
    editor.change_picture(1)
    after = size()
    dirty = editor.dirty()
    editor.win.done(0)
    window.close()
    assert "Cambiar ilustración…" in actions, actions
    assert before != 4.0 and after == 4.0 and not dirty, (before, after, dirty)


def test_synonyms():
    """Sinónimos del tesauro: de la palabra, de su singular y de su masculino, devueltos a su forma («casas» →
    «hogares», «bonita» → «linda»); nada de una inventada. Se respetan
    las mayúsculas, y el menú del clic derecho los ofrece y reemplaza la palabra."""
    from PySide6.QtGui import QTextCursor
    import editor as ed

    house, houses, pretty = ed.synonyms("casa"), ed.synonyms("casas"), ed.synonyms("bonita")
    B = convert.Block
    root, window, editor = editor_on([B("heading", 1, "Capítulo 1"), B("p", 1, "Una Casa grande.")], [0])
    block = editor.sheet.document().findBlockByNumber(1)
    c = QTextCursor(block)
    c.setPosition(block.position() + 5)
    pos = editor.sheet.cursorRect(c).center()
    menu = editor.mark_menu(pos)
    sub = next(a.menu() for a in menu.actions() if a.text() == "Sinónimos")
    first = sub.actions()[0]
    first.trigger()
    text = editor.sheet.content().split("\n\n")[1]
    editor.win.done(0)
    window.close()
    assert "hogar" in house and "casa" not in house and len(house) <= 12, house
    assert "hogares" in houses and "edificaciones" in houses and "linda" in pretty, (houses, pretty)
    assert ed.synonyms("xyzwq") == []
    assert ed.match_case("CASA", "hogar") == "HOGAR" and ed.match_case("Casa", "hogar") == "Hogar"
    assert first.text()[0].isupper() and text == f"Una {first.text()} grande.", (first.text(), text)


def test_window_editor_split_back_keeps_paragraph_source():
    """Unir dos párrafos (Retroceso) y volver a partirlos (Intro) donde estaban: el segundo recupera su origen, la
    vista del PDF lo recuadra a él y no al primero, y se guarda como estaba (sin cambios). Ctrl+Z deshace todo."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QTextCursor
    from PySide6.QtTest import QTest
    import editor as ed

    B = convert.Block
    root, window, editor = editor_on([B("heading", 1, "Capítulo 1"), B("p", 1, "Primer párrafo."),
                                      B("p", 2, "Segundo párrafo."), B("p", 3, "Tercero.")], [0])
    sheet = editor.sheet
    before = sheet.content()
    second = sheet.document().findBlockByNumber(2)
    cursor = QTextCursor(second)
    sheet.setTextCursor(cursor)
    QTest.keyClick(sheet, Qt.Key.Key_Backspace)  # une «Segundo» al primero
    root.update()
    joined = sheet.document().blockCount()
    QTest.keyClick(sheet, Qt.Key.Key_Return)  # lo vuelve a partir en el mismo sitio
    root.update()
    block = sheet.textCursor().block()
    source, shown = ed.source_of(block), editor.source_block()
    changed = [b.text() for b in sheet.changed_blocks()]
    after = sheet.content()
    for _ in range(2):
        QTest.keyClick(sheet, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    undone = sheet.content()
    editor.win.done(0)
    window.close()
    assert joined == 3 and block.text() == "Segundo párrafo.", (joined, block.text())
    assert source == 2 and shown == 2, (source, shown)
    assert changed == [] and after == before, (changed, after)
    assert undone == before, undone


def test_window_editor_joined_paragraphs_box_both():
    """Dos párrafos unidos en el editor: la vista del PDF recuadra los dos originales, no solo el primero. Un
    párrafo borrado no entra en el recuadro del anterior."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QTextCursor
    from PySide6.QtTest import QTest

    B = convert.Block
    root, window, editor = editor_on([B("heading", 1, "Capítulo 1"),
                                      B("p", 1, "Primer párrafo.", rect=(72, 100, 300, 120)),
                                      B("p", 1, "Segundo párrafo.", rect=(80, 130, 320, 150)),
                                      B("p", 1, "Tercero.", rect=(72, 160, 200, 180)),
                                      B("p", 1, "Cuarto.", rect=(72, 190, 200, 210))], [0])
    sheet = editor.sheet
    sheet.setTextCursor(QTextCursor(sheet.document().findBlockByNumber(2)))
    QTest.keyClick(sheet, Qt.Key.Key_Backspace)  # «Segundo» se une a «Primer»
    root.update()
    editor.draw_source()
    joined = tuple(editor.box) if editor.box else None
    cursor = QTextCursor(sheet.document().findBlockByNumber(2))  # «Tercero»: se borra entero
    cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
    cursor.removeSelectedText()
    sheet.setTextCursor(QTextCursor(sheet.document().findBlockByNumber(1)))
    root.update()
    editor.draw_source()
    after_delete = tuple(editor.box) if editor.box else None
    editor.win.done(0)
    window.close()
    assert joined == (72, 100, 320, 150), joined
    assert after_delete == (72, 100, 320, 150), after_delete


def test_text_editor_marks_issues_and_changes():
    """Los avisos del informe se marcan en su párrafo; F8 abre su barra y «Arreglar» une el párrafo partido en
    la hoja; lo cambiado se marca, «Volver al original» lo devuelve y Ctrl+Z deshace el arreglo."""
    B = convert.Block
    root, window, editor = editor_on([B("heading", 1, "Capítulo 1"), B("p", 1, "Empieza la frase"),
                                      B("p", 1, "y sigue aquí."), B("p", 1, "Otro párrafo normal.")], [0])
    sheet = editor.sheet
    marks_issue = len(sheet.extraSelections())
    issues_label = editor.issues_menu.actions()[0].text()
    editor.next_issue()
    bar = editor.issue_frame.isVisible() and "Párrafo partido" in editor.issue_title.text()
    editor.fix_issue()
    fixed = sheet.content().split("\n\n")
    editor.refresh_marks()
    changes_label = editor.changes_menu.actions()[0].text()
    sheet.undo()
    undone = sheet.content().split("\n\n")
    cursor = sheet.document().find("normal")
    cursor.insertText("NORMAL")
    sheet.setTextCursor(cursor)
    editor.restore_current()
    restored = sheet.content().split("\n\n")[-1]
    editor.win.done(0)
    window.close()
    assert marks_issue == 2 and issues_label == "2 avisos en este capítulo", (marks_issue, issues_label)  # y «muy corto»
    assert bar, editor.issue_title.text()
    assert fixed[1] == "Empieza la frase y sigue aquí." and changes_label == "1 párrafo cambiado", (fixed, changes_label)
    assert undone[1:3] == ["Empieza la frase", "y sigue aquí."], undone
    assert restored == "Otro párrafo normal.", restored


def test_text_editor_underlines_issues_like_word():
    """Como Word: ondulado solo bajo el trozo del problema, del color de su gravedad (azul el Menor), sin fondo
    que parezca una selección; el clic derecho sobre él abre su menú con «Arreglar» e «Ignorar este aviso» (el
    izquierdo solo pone el cursor: un menú ahí no dejaba elegir texto); un párrafo cambiado lleva una barrita en
    el margen."""
    import app
    import theme
    from PySide6.QtCore import QEvent, QPoint, Qt
    from PySide6.QtGui import QMouseEvent, QTextCharFormat
    from PySide6.QtWidgets import QApplication
    B = convert.Block
    root, window, editor = editor_on([B("heading", 1, "Capítulo 1"),
                                      B("p", 1, "Se dio cuenta que era muy tarde para todo aquello."),
                                      B("p", 1, "Otro párrafo sin nada raro que avisar aquí.")], [0])
    editor.win.show()
    sheet = editor.sheet
    marks = [(s.cursor.selectedText(), s.format.underlineStyle(), s.format.underlineColor().name(),
              s.format.background().style()) for s in sheet.extraSelections()]
    cursor = sheet.document().find("cuenta")
    pos = sheet.cursorRect(cursor).center()
    for kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
        QApplication.sendEvent(sheet.viewport(), QMouseEvent(kind, pos, sheet.viewport().mapToGlobal(pos),
                                                             Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                                                             Qt.KeyboardModifier.NoModifier))
    for _ in range(5):
        root.update()
        QApplication.processEvents()
    left_menu = getattr(editor, "context", None)
    editor.context_menu(sheet.cursorRect(sheet.document().find("cuenta")).center())  # tras el clic, por si se desplazó
    menu = editor.context
    labels = [a.text() for a in menu.actions() if a.text()]
    ignore = next(a for a in menu.actions() if a.text() == "Ignorar este aviso")
    menu.close()
    ignore.trigger()
    left = [s.cursor.selectedText() for s in sheet.extraSelections()]
    ignored = list(window.ignored)
    sheet.document().find("Otro").insertText("Un")
    editor.refresh_marks()
    bars = [b.text() for b in sheet.bars]
    editor.win.done(0)
    window.close()
    blue = theme.mark_colors(False)["Menor"]
    assert ("cuenta que", QTextCharFormat.UnderlineStyle.WaveUnderline, blue, Qt.BrushStyle.NoBrush) in marks, marks
    assert left_menu is None, left_menu  # el clic izquierdo no abre menú
    assert labels[0].startswith("Arreglar: «") and "de que" in labels[0] and "Ignorar este aviso" in labels, labels
    assert "cuenta que" not in left and len(ignored) == 1, (left, ignored)
    assert bars == ["Un párrafo sin nada raro que avisar aquí."], bars


def test_text_editor_escape_closes_bars_not_the_editor():
    """Esc en el campo de buscar, reemplazar, enlace o dividir cierra su barra: llegaba al diálogo, que lo
    tomaba como cancelar el editor y preguntaba «¿Descartar cambios?»."""
    import app
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    B = convert.Block
    root, window, editor = editor_on([B("heading", 1, "Capítulo 1"), B("p", 1, "Texto del capítulo.")], [0])
    editor.win.show()
    editor.sheet.textCursor().insertText("Cambio ")  # con cambios, cancelar pregunta
    asked, original = [], app.messagebox.askyesno
    app.messagebox.askyesno = lambda *args, **kwargs: asked.append(args) or False
    closed = []
    try:
        for show, bar, entry in ((lambda: editor.show_find_bar(False), editor.find_frame, editor.find_entry),
                                 (lambda: editor.show_find_bar(True), editor.find_frame, editor.replace_entry),
                                 (editor.show_link_bar, editor.link_frame, editor.link_entry),
                                 (editor.show_split_bar, editor.split_frame, editor.split_entry)):
            show()
            if entry is editor.find_entry:  # tras la lista de «Todos» la ventana no está activa: el atajo no saltaba
                entry.setText("Texto")
                editor.show_all_matches()
                editor.matches_popup.hide()
            entry.setFocus()
            root.update()
            QTest.keyClick(entry, Qt.Key.Key_Escape)
            closed.append(not bar.isVisible())
        open_editor = editor.win.isVisible()
    finally:
        app.messagebox.askyesno = original
    editor.win.done(0)
    window.close()
    assert closed == [True] * 4 and not asked and open_editor, (closed, asked, open_editor)


def test_text_editor_ribbon_fits_the_screen():
    """Con las herramientas nuevas la cinta pedía ~1800 px: en 1366 (y 1093 con el zoom al 125 %) los botones
    se montaban. Las pestañas la dejan en menos de 1366 px con todos los nombres; con el zoom de Windows al
    125 %, cada nivel de fit_ribbon la estrecha (iconos, botón de capítulo más corto) y se elige el
    primero que cabe; el texto queda en la ayuda. (Sin pantalla las letras miden más que en Windows: el
    ancho real se mira con /verificar-ventana.)"""
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication
    B = convert.Block
    root, window, editor = editor_on([B("heading", 1, "Capítulo 1: Un título largo para la cinta"),
                                      B("p", 1, "Texto.")], [0])
    import app
    widths = []
    for level in range(len(app.CHAPTER_WIDTHS)):
        editor.ribbon_level = level
        editor.compact_to(level)
        editor.show_chapter(0)
        editor.refresh_marks()  # «Avisos (1) ▾» o «▲ 1 ▾» según el nivel
        QApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest.value)
        widths.append(editor.ribbon_bar.sizeHint().width())
    editor.fit_ribbon()
    room = min(editor.win.width(), editor.win.screen().availableGeometry().width())
    level, undo, tip = editor.ribbon_level, editor.undo_btn.toolButtonStyle(), editor.undo_btn.toolTip()
    editor.win.done(0)
    window.close()
    assert widths == sorted(widths, reverse=True) and len(set(widths)) == len(widths), widths
    assert widths[0] <= 1366, widths  # repartida en pestañas cabe con el nombre de cada botón
    assert level == next((n for n, w in enumerate(widths) if w <= room), len(widths) - 1), (level, widths, room)
    from PySide6.QtCore import Qt
    assert level == 0 or undo == Qt.ToolButtonStyle.ToolButtonIconOnly, (level, undo)  # solo el glifo ↶
    assert "Deshacer" in tip, tip


def test_text_editor_ribbon_tabs_like_word():
    """La cinta va en pestañas como Word (Inicio, Insertar, Revisar, Vista): cada botón está en una sola, el
    capítulo y ◀ ▶ se ven en todas, y «Mostrar cambios» de Vista va a la par con el del menú Cambios."""
    from PySide6.QtWidgets import QAbstractButton
    B = convert.Block
    root, window, editor = editor_on([B("heading", 1, "Capítulo 1"), B("p", 1, "Texto.")], [0])
    tabs, pages = editor.ribbon_tabs, editor.ribbon_pages
    names = [tabs.tabText(n) for n in range(tabs.count())]
    buttons = [pages.widget(n).findChildren(QAbstractButton) for n in range(pages.count())]
    owners = {b: [n for n, page in enumerate(buttons) if b in page] for page in buttons for b in page}
    where = {b.text().split("\n")[0]: names[n[0]] for b, n in owners.items()}
    visible = []
    for n in range(tabs.count()):
        tabs.setCurrentIndex(n)
        visible.append((pages.currentIndex(), editor.chapter_btn.isVisibleTo(editor.win),
                        editor.next_chapter.isVisibleTo(editor.win), editor.review_btn.isVisibleTo(editor.win)))
    editor.show_changes_box.setChecked(True)
    from_box = editor.show_changes_action.isChecked()
    editor.show_changes_action.setChecked(False)
    from_menu = editor.show_changes_box.isChecked()
    big = editor.read_btn.toolButtonStyle(), editor.read_btn.icon().isNull()
    editor.win.done(0)
    window.close()
    assert names == ["Inicio", "Insertar", "Revisar", "Vista"], names
    assert all(len(n) == 1 for n in owners.values()), owners
    assert (where["N"], where["Símbolos ▾"], where["Leer"], where["Navegación"]) == tuple(names), where
    assert visible == [(n, True, True, True) for n in range(4)], visible
    assert from_box and not from_menu, (from_box, from_menu)
    from PySide6.QtCore import Qt
    assert big == (Qt.ToolButtonStyle.ToolButtonTextUnderIcon, False), big


def test_text_editor_closed_schedules_nothing():
    """Al cerrar el editor, el panel del PDF aún avisaba de su nuevo tamaño y se repintaba sobre la ventana
    cerrada: «Algo falló · 'NoneType' object is not subscriptable» con el libro ya cerrado."""
    B = convert.Block
    root, window, editor = editor_on([B("heading", 1, "Capítulo 1"), B("p", 1, "Texto.")], [0])
    editor.win.done(0)
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QResizeEvent
    editor.on_panel_event(QResizeEvent(QSize(400, 300), QSize(300, 300)))
    pending = dict(editor.pending)
    window.close()
    assert not pending, pending


class FakeSpellProcess:
    alive = True

    def is_alive(self):
        return self.alive

    def terminate(self):
        self.alive = False


def test_text_editor_live_spelling():
    """Ortografía en vivo con un proceso falso: el editor manda las palabras del libro, subraya las que no
    están, pide sus sugerencias y el clic derecho las ofrece con «Ignorar» y «Añadir al diccionario de la
    serie». Si el proceso muere, el editor sigue."""
    import queue

    import app
    requests, replies, proc = queue.Queue(), queue.Queue(), FakeSpellProcess()
    B = convert.Block
    original = app._start_spell_process
    root, window = visible_window()
    app._start_spell_process = lambda: (requests, replies, proc)
    try:
        doc = pymupdf.open()
        add_page(doc, 1, [(72, 100, "Pagina", 11, "helv")])
        window.pdf = TMP / f"ortografia-{time_ns()}.pdf"
        doc.save(window.pdf)
        window.show_book(convert.Book("Libro", "", b"", [B("heading", 1, "Capítulo 1"),
                                                          B("p", 1, "La palabrq rara y Subaru.")], [0]))
        editor = app.TextEditor(window, "0", *window.section_bounds(0))
    finally:
        app._start_spell_process = original
    sent = requests.get_nowait()
    replies.put(("unknown", ["palabrq"]))
    editor.poll_spelling()
    underlined = editor.sheet.misspelled()
    asked = requests.get_nowait()
    pos = editor.sheet.cursorRect(editor.sheet.document().find("palabrq")).center()
    editor.context_menu(pos)  # antes de que lleguen: «Buscando sugerencias…» y se pide la primera
    root.update()
    waiting = [a.text() for a in editor.context.actions()][:1]
    first = requests.get_nowait()
    replies.put(("suggestions", "palabrq", ["palabra", "palabras"]))
    editor.poll_spelling()  # llegan con el menú abierto: aparecen en él
    labels = [a.text() for a in editor.context.actions()]
    editor.context.close()
    editor.add_word("palabrq")
    after = editor.sheet.misspelled()
    proc.alive = False
    editor.poll_spelling()
    stopped = editor.spell is None
    words = list(window.words)
    editor.win.done(0)
    window.close()
    assert sent[0] == "check" and "palabrq" in sent[1] and "Subaru" not in sent[1], sent
    assert underlined == ["palabrq"] and asked == ("suggest", "palabrq"), (underlined, asked)
    assert waiting == ["Buscando sugerencias…"] and first == ("first", "palabrq"), (waiting, first)
    assert labels[:4] == ["palabra", "palabras", "Ignorar", "Añadir al diccionario de la serie"], labels
    assert "Deshacer" in labels and "Copiar" in labels, labels
    assert after == [] and words == ["palabrq"] and stopped, (after, words, stopped)


def test_qimage_puts_transparency_on_white():
    """El separador dibujado de Eminence 7 es un PNG con fondo transparente: en el editor salía un cuadro negro,
    porque lo transparente se quedaba en negro. Sobre blanco, como en una página; en gris, igual."""
    import qtk

    qtk.app()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 20, 20), True)
    pix.set_rect(pix.irect, (0, 0, 0, 0))  # todo transparente
    pix.set_rect(pymupdf.IRect(8, 0, 12, 20), (0, 0, 0, 255))  # una barra negra opaca
    gray = pymupdf.Pixmap(pymupdf.csGRAY, pymupdf.IRect(0, 0, 4, 4), True)
    gray.set_rect(gray.irect, (0, 0))
    shown = [(image.pixelColor(1, 1).name(), image.pixelColor(10, 10).name())
             for image in (qtk.qimage(pix), qtk.qimage(pymupdf.Pixmap(pix.tobytes("png"))))]
    assert shown == [("#ffffff", "#000000")] * 2, shown
    assert qtk.qimage(gray).pixelColor(1, 1).name() == "#ffffff"
    # En el editor, el adorno va del color del texto sobre la hoja: un símbolo más, no una foto con fondo blanco
    inked = qtk.qimage(pix, "#ececec")
    assert inked.pixelColor(1, 1).alpha() == 0 and inked.pixelColor(10, 10).name() == "#ececec", (
        inked.pixelColor(1, 1).getRgb(), inked.pixelColor(10, 10).name())
    # Adorno opaco, negro sobre blanco (las calaveras JPEG de Megami): lo oscuro es la forma, el blanco se va
    opaque = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 20, 20), False)
    opaque.set_rect(opaque.irect, (255, 255, 255))
    opaque.set_rect(pymupdf.IRect(8, 0, 12, 20), (0, 0, 0))
    inked = qtk.qimage(pymupdf.Pixmap(opaque.tobytes("jpg")), "#ececec")
    assert inked.pixelColor(1, 1).alpha() < 10 and inked.pixelColor(10, 10).alpha() > 240, (
        inked.pixelColor(1, 1).getRgb(), inked.pixelColor(10, 10).getRgb())


def test_text_editor_many_unknown_words_do_not_freeze():
    """Eminence 7 sin traducir: con la ortografía en español, miles de palabras desconocidas. Al llegar la
    respuesta del proceso, la ventana se congelaba 10 s (una expresión regular por palabra para pedir sugerencias
    y otra gigante para subrayar). Ahora es rápido, subraya igual y pide pocas sugerencias por adelantado."""
    import queue
    from time import perf_counter

    import app
    requests, replies, proc = queue.Queue(), queue.Queue(), FakeSpellProcess()
    B = convert.Block
    words = [f"pal{chr(97 + n % 26)}{chr(97 + n // 26 % 26)}{chr(97 + n // 676)}x" for n in range(4000)]
    paragraphs = [B("p", 1, " ".join(words[n:n + 8]) + ". Fin del Párrafo.") for n in range(0, 4000, 8)]
    original = app._start_spell_process
    root, window = visible_window()
    app._start_spell_process = lambda: (requests, replies, proc)
    try:
        doc = pymupdf.open()
        add_page(doc, 1, [(72, 100, "Pagina", 11, "helv")])
        window.pdf = TMP / f"muchas-{time_ns()}.pdf"
        doc.save(window.pdf)
        window.show_book(convert.Book("Libro", "", b"", [B("heading", 1, "Capítulo 1")] + paragraphs, [0]))
        editor = app.TextEditor(window, "0", *window.section_bounds(0))
    finally:
        app._start_spell_process = original
    while not requests.empty():
        requests.get_nowait()
    replies.put(("unknown", words + ["fin", "otra"]))  # «Fin» con mayúscula también se subraya; «otra» no está
    start = perf_counter()
    editor.poll_spelling()
    took = perf_counter() - start
    asked = []
    while not requests.empty():
        asked.append(requests.get_nowait())
    underlined = editor.sheet.misspelled()
    editor.win.done(0)
    window.close()
    assert took < 2, took  # antes, ~10 s
    assert 0 < len(asked) <= app.SUGGEST_AHEAD and all(a[1] in words + ["fin"] for a in asked), asked[:5]
    assert len(underlined) == 4000 + 500 and underlined.count("Fin") == 500, len(underlined)


def test_spell_server_answers_checks_before_suggestions():
    """El proceso de ortografía (aquí en un hilo con colas) dice qué palabras no están y sus sugerencias."""
    import queue
    import threading

    import app
    requests, replies = queue.Queue(), queue.Queue()
    worker = threading.Thread(target=app._spell_server, args=(requests, replies), daemon=True)
    worker.start()
    requests.put(("check", ["casa", "tambien"]))
    unknown = replies.get(timeout=60)
    worker.join(0.1)
    requests.put(None)
    worker.join(10)
    # Sugerencias: «first» (el clic derecho) se adelanta a las pedidas antes
    requests.put(("suggest", "caballeroz"))
    requests.put(("first", "tambien"))
    requests.put(("suggest", "caballeroz"))  # repetida: una sola respuesta
    worker = threading.Thread(target=app._spell_server, args=(requests, replies), daemon=True)
    worker.start()
    suggestions = [replies.get(timeout=60), replies.get(timeout=60)]
    requests.put(None)
    worker.join(10)
    assert unknown == ("unknown", ["tambien"]), unknown
    assert [s[1] for s in suggestions] == ["tambien", "caballeroz"] and suggestions[0][2][0] == "también", suggestions
    assert replies.empty()
    assert not worker.is_alive()


def test_text_editor_review_split_find_and_stats():
    """Ctrl+Intro marca el capítulo como revisado y abre el siguiente sin revisar; «Dividir» parte el
    capítulo y al aceptar la lista tiene la fila nueva; buscar con palabra completa y mayúsculas, y «Todos»
    lista cada resultado; las cifras cuentan palabras y diálogo."""
    import app
    B = convert.Block
    root, window, editor = editor_on([B("heading", 1, "Capítulo 1"), B("p", 1, "—Él dijo algo. Aquél no."),
                                      B("p", 1, "Narración de él y del Él grande."),
                                      B("heading", 2, "Capítulo 2"), B("p", 2, "Más texto aquí.")], [0, 3])
    editor.toggle_reviewed()
    reviewed = window.tree.set("0", "ok") == "✓" and editor.current == window.section_bounds(3)
    label = editor.review_btn.toolTip()
    editor.open_section(*window.section_bounds(0))
    editor.find_entry.setText("él")
    loose = len(editor.all_matches())
    editor.whole_box.setChecked(True)
    whole = len(editor.all_matches())
    editor.case_box.setChecked(True)
    exact = [text for _, _, text in editor.all_matches()]
    editor.show_all_matches()
    listed = editor.matches_popup.isVisible()
    editor.matches_popup.hide()
    editor.show_stats()
    grid = [w.text() for w in editor.stats_window.findChildren(app.QLabel)]
    editor.stats_window.close()
    cursor = editor.sheet.document().find("Narración")
    cursor.setPosition(cursor.selectionStart())
    editor.sheet.setTextCursor(cursor)
    editor.show_split_bar()
    editor.split_entry.setText("Capítulo 1.5: Interludio")
    editor.apply_split()
    editor.save()
    rows = [(window.tree.set(i, "title"), window.tree.set(i, "inc")) for i in window.tree.get_children()]
    window.close()
    assert reviewed and label.startswith("Capítulos revisados: 1 de 2."), (reviewed, label)
    assert loose == 4 and whole == 3 and len(exact) == 1 and "de él y" in exact[0], (loose, whole, exact)
    assert listed and grid[3:6] == ["Palabras", "12", "15"] and "42 %" in grid, grid  # 5 de 12 en diálogo
    assert ("Capítulo 1.5: Interludio", app.ON) in rows, rows


def test_text_editor_ribbon_follows_the_cursor():
    """Como en Word: la cinta marca N y K donde el texto lo es, el estilo dice Título en un título, y sus
    botones cambian la hoja. Cerrar la ventana principal con cambios sin guardar en el editor pregunta."""
    import app
    from PySide6.QtGui import QTextCursor

    root, window = visible_window()
    window.pdf = make_novel()
    window.show_book(convert.analyze(window.pdf))
    editor = app.TextEditor(window, "1", 0, len(window.book.blocks))  # todo el libro: ilustración incluida
    sheet = editor.sheet
    content = sheet.content()
    found = {"ilustración": "[Ilustración 0]" in content, "cursiva": "<i>que era tarde.</i>" in content}

    def go(needle):
        cursor = sheet.document().find(needle)
        sheet.setTextCursor(cursor)
        editor.update_tools()

    go("que era")
    found["K marcada"] = editor.italic_btn.isChecked() and not editor.bold_btn.isChecked()
    go("Capitulo 1")
    found["estilo Capítulo"] = editor.style.currentText() == "Capítulo"
    go("Ella penso")
    editor.bold_btn.click()
    found["N pone negrita"] = "<b>Ella penso</b>" in sheet.content()
    cursor = sheet.textCursor()
    cursor.clearSelection()
    sheet.setTextCursor(cursor)
    editor.style.activated.emit(1)
    found["estilo pone título"] = any(line.startswith("# ") and "penso" in line for line in sheet.content().split("\n\n"))
    original, asked, errors = (app.messagebox.askyesno, app.messagebox.showerror), [], []
    try:  # cerrar la ventana principal con cambios sin guardar en el editor: pregunta antes
        app.messagebox.showerror = lambda title, message, **kwargs: errors.append(message)
        app.messagebox.askyesno = lambda *args, **kwargs: asked.append(args) or False
        window.close()
        kept = bool(root.winfo_exists())
        app.messagebox.askyesno = lambda *args, **kwargs: asked.append(args) or True
        window.close()  # con el editor abierto: sus after y señales pendientes no deben dar errores
        root.update()
    finally:
        app.messagebox.askyesno, app.messagebox.showerror = original
    found["pregunta al cerrar"] = len(asked) == 2 and kept
    found["sin errores al cerrar"] = not errors
    assert all(found.values()), (found, errors)


def test_text_editor_find_box_skips_repeated_lines():
    """Una línea corta repetida en la página no se recuadra: no se sabe cuál es."""
    import app

    with pymupdf.open() as doc:
        page = doc.new_page()
        for y in (100, 200):
            page.insert_text((72, y), "Si.")
        page.insert_text((72, 300), "Una sola vez")
        boxes = [app.TextEditor.find_box(page, convert.Block("p", 1, t)) for t in ("Si.", "Una sola vez")]
    assert boxes[0] is None and boxes[1] is not None, boxes


def test_text_editor_shows_source_page_and_illustration():
    """El panel sigue al cursor: la página del PDF del párrafo (recuadrado), también en un párrafo nuevo
    escrito debajo; sobre una ilustración, la ilustración. Es grande (al menos el 40 % del ancho), el zoom la
    agranda. Sección vacía y libro sin PDF: sin errores."""
    import app
    import editor as sheet_module

    root, window = visible_window()
    window.pdf = make_novel()
    window.show_book(convert.analyze(window.pdf))
    editor = app.TextEditor(window, "1", 0, len(window.book.blocks))
    editor.win.showNormal()
    editor.win.resize(1300, 760)
    root.update()
    editor.split.setSizes([650, 650])
    root.update()
    sheet = editor.sheet

    def at(needle):
        if needle.startswith("["):  # una ilustración: el párrafo de su objeto
            block = sheet.document().firstBlock()
            while sheet_module.objects_of(block) != [needle[1:-1]]:
                block = block.next()
            cursor = sheet.textCursor()
            cursor.setPosition(block.position())
        else:
            cursor = sheet.document().find(needle)
        sheet.setTextCursor(cursor)
        editor.draw_source()
        return editor.shown

    seen = [at("Fin."), editor.box is not None, at("[Ilustración 0]")]
    seen.append(at("Ella penso"))
    wide = editor.canvas.width() / editor.win.width()
    narrow = editor.canvas.width()
    editor.zoom(50)
    bigger = editor.canvas.width() > narrow
    cursor = sheet.document().find("Fin.")
    cursor.movePosition(cursor.MoveOperation.EndOfBlock)
    sheet.setTextCursor(cursor)
    sheet.split_here()
    sheet.textCursor().insertText("Parrafo nuevo")
    seen.append(at("Parrafo nuevo"))
    editor.update_status()
    status = editor.status.text()
    empty = app.TextEditor(window, "6", 7, 7)  # título sin texto debajo: muestra la página del título
    empty.draw_source()
    seen.append(empty.shown)
    doc, window.pdf_doc = window.pdf_doc, None  # revisión de un PDF que ya no está
    editor.drawn = None
    at("Fin.")
    seen.append(editor.shown)
    window.pdf_doc = doc
    editor.win.done(0)
    empty.win.done(0)
    window.close()
    assert seen == [("page", 4), True, ("image", 0), ("page", 3), ("page", 4), ("page", 4), None], seen
    assert status.startswith("Página 4 del PDF · párrafo "), status
    assert wide >= 0.4 and bigger, (wide, bigger)


def test_window_find_moves_previews_to_the_match():
    root, window = visible_window()
    window.pdf = make_group_pdf()
    window.show_book(convert.analyze(window.pdf))
    root.update()
    k = next(k for k, b in enumerate(window.book.blocks) if "final de la historia" in b.html)
    window.show_match(k, 3, "final de la historia")
    root.update()
    selected = window.tree.set(window.tree.selection()[0], "title")
    caption = window.pdf_caption.text()
    window.find()  # el cuadro de búsqueda se abre sin errores
    root.update()
    window.close()
    assert selected == "Capitulo 2: Final", selected
    assert "página 3" in caption, caption


def test_spanish_case_for_titles():
    body = [convert.Block("p", 1, "Las luchas y el renacimiento de la guarida, al final, con Teto y el Heraldo. "
                                  "Vio a Teto. Habló con el Heraldo y con Teto. Un heraldo más.")]
    words = convert.body_words(convert.Book("Libro", "", b"", body, []))
    cases = {
        "Capítulo Uno: Las Luchas Posteriores Al Renacimiento": "Capítulo uno: Las luchas posteriores al renacimiento",
        "Capítulo 6: Teto Y La Guarida": "Capítulo 6: Teto y la guarida",
        "CAPITULO 2: EL FINAL DE TETO": "Capitulo 2: El final de Teto",
        "Prólogo: ¡Hola, Mundo! ¿Qué Tal?": "Prólogo: ¡Hola, mundo! ¿Qué tal?",
        "La NPC Del Final": "La NPC del final",
    }
    got = {k: convert.spanish_case(k, words) for k in cases}
    assert got == cases, {k: v for k, v in got.items() if v != cases[k]}


def test_gray_illustrations_keep_color_cover():
    color = jpeg(200, 300)
    book = convert.Book("Libro", "", color, [convert.Block("heading", 1, "Cap 1"),
                                             convert.Block("image", 1, image=color)], [0])
    out = TMP / "gris.epub"
    convert.write_epub(book, [(0, "Cap 1")], out, gray=True)
    with zipfile.ZipFile(out) as z:
        art = pymupdf.Pixmap(z.read(next(n for n in z.namelist() if n.startswith("OEBPS/img/"))))
        cover = pymupdf.Pixmap(z.read("OEBPS/cover.jpg"))
    assert (art.n, cover.n) == (1, 3), (art.n, cover.n)


def test_gray_stretches_contrast():
    """En tinta una ilustración escaneada sale lavada (grises 60–200): el gris la estira hasta casi negro y blanco.
    Con transparencia sigue en PNG con su alfa."""
    import io
    from PIL import Image
    washed = Image.linear_gradient("L").resize((64, 64)).point(lambda v: 60 + v * 140 // 255)
    data = io.BytesIO()
    washed.save(data, "PNG")
    low, high = Image.open(io.BytesIO(convert._gray(data.getvalue()))).getextrema()
    clear = io.BytesIO()
    Image.new("RGBA", (20, 20), (200, 100, 50, 0)).save(clear, "PNG")
    out = convert._gray(clear.getvalue())
    assert low < 10 and high > 245, (low, high)
    assert out[:4] == b"\x89PNG" and Image.open(io.BytesIO(out)).mode == "LA", out[:4]


def test_hyphenate_soft_hyphens_only_in_paragraph_text():
    """Kindle apenas parte palabras en español: con hyphenate, guiones blandos en las palabras largas de los
    párrafos, nunca en títulos, enlaces ni en libros en otro idioma."""
    book = convert.Book("Libro", "", b"", [
        convert.Block("heading", 1, "Desconocido"),
        convert.Block("p", 1, 'Algo extraordinariamente raro: <a href="https://ejemplo.com/extraordinario">'
                              'extraordinario</a>, Subaru-kun y «abc&amp;def».')], [0])
    out = TMP / "partir.epub"
    convert.write_epub(book, [(0, "Desconocido")], out, hyphenate=True)
    body = epub_file(out, "OEBPS/sec001.xhtml")
    para = re.search(r"<p>.*</p>", body)[0]
    link = re.search(r"<a .*?</a>", para)[0]
    convert.write_epub(book, [(0, "Desconocido")], out)
    plain = epub_file(out, "OEBPS/sec001.xhtml")
    convert.write_epub(book, [(0, "Desconocido")], out, hyphenate=True, lang="en")
    english = epub_file(out, "OEBPS/sec001.xhtml")
    convert.write_epub(book, [(0, "Desconocido")], out, hyphenate=True)
    assert "ex­tra­or­di­na­ria­men­te" in para, para
    assert "­" not in link and "­" not in body.split("<p>")[0], body
    assert "Subaru-kun" in para and "&amp;" in para, para
    assert "­" not in plain and "­" not in english, (plain, english)
    assert convert.check_epub(out) == [], convert.check_epub(out)


def test_options_table_and_search():
    """Cada opción está una vez, en una categoría que existe; buscar «guion» lleva a Texto y a PDF, sin tildes ni
    mayúsculas; y las claves de libro son las que se guardan por libro."""
    import options
    keys = [o.key for o in options.OPTIONS]
    categories = {c for group in options.CATEGORIES.values() for c in group}
    found = sorted({o.category for o in options.find("GUIÓN")})
    assert len(keys) == len(set(keys)), keys
    assert all(o.category in categories for o in options.OPTIONS)
    assert found == ["PDF", "Texto"], found
    assert options.find("zzz") == []
    assert "font_file" in options.BOOK_KEYS and "theme" not in options.BOOK_KEYS
    assert options.fold("Tipografía ÚNICA") == "tipografia unica"


def test_window_new_books_use_saved_defaults():
    """Libros nuevos: un ajustes.json antiguo (opciones en la raíz, sin «defaults») pasa a «Libros nuevos» sin cambiar
    nada. Un libro sin revisión ni serie abre con esos valores, no con los del libro anterior; uno con revisión, con
    la suya. Un valor mal escrito a mano se ignora."""
    import json
    import options
    settings = TMP / f"ajustes-viejos-{time_ns()}.json"
    settings.write_text(json.dumps({"gray": True, "style": "Novela ligera", "credits": "sí", "translate": [],
                                    "options_page": ["mal"]}), encoding="utf-8")
    root, window = visible_window(settings)
    migrated = dict(window.settings["defaults"])
    page = window.open_options().category  # última página mal escrita a mano: abre en la primera
    window.options_window.parts["cancel"]()
    folder = TMP / f"libros-{time_ns()}"
    folder.mkdir()
    books = {}
    for name in ("con-revision", "nuevo"):
        doc = pymupdf.open()
        add_page(doc, 1, [(72, 100, "Capitulo 1: Uno", 20, "helv"), (72, 150, "Texto.", 11, "cour")])
        doc.save(folder / f"{name}.pdf")
        books[name] = folder / f"{name}.pdf"
    book = lambda: convert.Book("Libro", "", b"", [convert.Block("heading", 1, "Capítulo 1: Uno"),
                                                  convert.Block("p", 1, "Texto.")], [0])
    window.pdf = books["con-revision"]
    window.show_book(book())
    window.gray_var.set(False)  # este libro, sin gris
    window.save_review()
    window.pdf = books["nuevo"]
    window.show_book(book())  # sin revisión ni serie: «Libros nuevos», no lo del libro anterior
    fresh = (window.gray_var.get(), window.style_var.get(), window.credits_var.get())
    window.pdf = books["con-revision"]
    window.show_book(book())
    reviewed = window.gray_var.get()
    defaults = window.new_book_options()
    translation = window.settings["translate"]
    window.close()
    assert migrated["gray"] is True and migrated["style"] == "Novela ligera" and "credits" not in migrated, migrated
    assert fresh == (True, "Novela ligera", False), fresh
    assert reviewed is False
    assert defaults["gray"] is True and set(defaults) == set(options.BOOK_KEYS), defaults
    assert translation == {} and page == "Perfil y formato", (translation, page)


def test_window_options_this_book_live_and_cancel():
    """Opciones en «Este libro»: el estilo cambia el libro y la vista previa al momento; Cancelar lo deja todo
    como estaba. Aceptar lo guarda en la revisión. «Opciones del libro…» abre en «Perfil y formato»."""
    import json
    import app
    from PySide6.QtWidgets import QPushButton
    root, window = visible_window()
    window.pdf = TMP / f"opciones-{time_ns()}.pdf"
    doc = pymupdf.open()
    add_page(doc, 1, [(72, 100, "Capitulo 1: Uno", 20, "helv")])
    doc.save(window.pdf)
    window.show_book(convert.Book("Libro", "", b"", [convert.Block("heading", 1, "Capítulo 1: Uno"),
                                                    convert.Block("p", 1, "Texto.")], [0]))
    redrawn = []
    window.update_preview = lambda _=None: redrawn.append(window.style_var.get())
    before = window.style_var.get()
    other = next(s for s in convert.STYLES if s != before)
    dialog = window.open_options()
    first = dialog.scope
    dialog.parts["show"]("Aspecto")
    dialog.parts["put"]("style", other)
    live = window.style_var.get()
    dialog.parts["cancel"]()
    cancelled, redrawn_after_cancel = window.style_var.get(), redrawn[-1]
    book_button = next(b for b in window.options_row.findChildren(QPushButton) if b.text() == "Opciones del libro…")
    book_button.click()
    root.update()
    dialog = window.options_window
    opened = dialog.category
    dialog.parts["show"]("Aspecto")
    dialog.parts["put"]("style", other)
    dialog.parts["accept"]()
    saved = json.loads(app._review_path(window.pdf).read_text(encoding="utf-8"))["style"]
    window.close()
    assert first == "book" and live == other and redrawn_after_cancel == before, (first, live, redrawn)
    assert cancelled == before and opened == "Perfil y formato" and saved == other, (cancelled, opened, saved)


def test_window_options_new_books_without_book():
    """Sin libro abierto, «Este libro» está desactivado y lo cambiado va a «Libros nuevos» al Aceptar; Cancelar no
    guarda nada."""
    import json
    import app
    root, window = visible_window()
    dialog = window.open_options("Ilustraciones")
    disabled, scope = not dialog.parts["this_book"].isEnabled(), dialog.scope
    dialog.parts["put"]("gray", True)
    dialog.parts["cancel"]()
    after_cancel = window.settings["defaults"].get("gray")
    dialog = window.open_options("Ilustraciones")
    dialog.parts["put"]("gray", True)
    dialog.parts["accept"]()
    stored = json.loads(app.SETTINGS.read_text(encoding="utf-8"))["defaults"]["gray"]
    window.close()
    assert disabled and scope == "new" and after_cancel in (None, False) and stored is True, (disabled, scope, stored)


def test_window_options_search_profile_and_missing_font():
    """Buscar «guion» deja Texto y PDF y resalta la opción; sin coincidencias, un aviso. El perfil Kindle marca los
    guiones con su nota; desmarcarlos pasa a «Personalizado». «Usar también para libros nuevos» copia la página.
    Una letra propia sin archivo avisa en rojo en la página, sin ventana."""
    root, window = visible_window()
    window.show_book(convert.Book("Libro", "", b"", [convert.Block("p", 1, "Texto.")], []))
    dialog = window.open_options("Perfil y formato", "book")
    dialog.parts["search"].setText("guion")
    listing = dialog.parts["list"]
    shown = [listing.item(n).text().strip() for n in range(listing.count()) if not listing.item(n).isHidden()]
    page = dialog.category
    dialog.parts["search"].setText("zzz")
    empty = not dialog.parts["empty"].isHidden()
    dialog.parts["search"].clear()
    dialog.parts["put"]("profile", "Kindle")
    dialog.parts["show"]("Texto")
    note = dialog.parts["notes"]["hyphenate"].text()
    dialog.parts["put"]("hyphenate", False)
    profile = window.profile_var.get()
    dialog.parts["copy"].click()
    copied = dialog.defaults["hyphenate"]
    window.font_file_var.set(str(TMP / "no-existe.ttf"))
    window.font_var.set("no-existe.ttf")
    dialog.parts["show"]("Aspecto")
    error = dialog.parts["font_error"]
    error_text = error.text() if error else ""
    dialog.parts["cancel"]()
    window.close()
    assert shown == ["EL LIBRO", "Texto", "PDF"] and page == "Texto", (shown, page)
    assert empty and "Fijada por el perfil Kindle" in note and profile == "Personalizado", (empty, note, profile)
    assert copied is False and "No se encuentra" in error_text, (copied, error_text)


def test_window_options_app_pages():
    """Tema: se ve al momento y Cancelar lo devuelve. Aceptar guarda dispositivo, modo oscuro y ecos, que la ventana
    usa al abrir la próxima vez. Carpeta vigilada: elegir y aceptar la vigila; una que ya no existe avisa en rojo."""
    import app
    from PySide6.QtWidgets import QPushButton
    root, window = visible_window()
    dialog = window.open_options("Apariencia")
    dialog.parts["put"]("theme", "Oscuro")
    live = window.dark
    dialog.parts["cancel"]()
    back = window.dark
    folder = TMP / f"vigilada-{time_ns()}"
    folder.mkdir()
    window.ask_folder = lambda: str(folder)
    dialog = window.open_options("Carpeta vigilada")
    next(b for b in dialog.win.findChildren(QPushButton) if b.text() == "Elegir…").click()
    dialog.parts["put"]("device", "Tablet")
    dialog.parts["put"]("dark", True)
    dialog.parts["put"]("echoes", True)
    dialog.parts["accept"]()
    watching, echoes = window.watch, window.echoes_var.get()
    folder.rmdir()
    dialog = window.open_options("Carpeta vigilada")
    lost = dialog.parts["watch_error"]
    lost_text = lost.text() if lost else ""
    dialog.parts["cancel"]()
    window.set_watch("")  # que la ventana siguiente no vigile una carpeta borrada
    window.save_settings()
    settings = app.SETTINGS
    window.close()
    root, again = visible_window(settings)
    reopened = (again.device_var.get(), again.dark_var.get(), again.echoes_var.get())
    again.close()
    import qtk
    import theme
    theme.apply(qtk.app(), "Claro")  # las demás pruebas, en claro
    assert live and not back and watching == str(folder) and echoes, (live, back, watching, echoes)
    assert "ya no existe" in lost_text, lost_text
    assert reopened == ("Tablet", True, True), reopened


def test_window_options_translation_page():
    """Traducción en Opciones: clave oculta con 👁; al Aceptar, Traducir abierto usa el motor y la clave nuevos, y
    al cerrarse no los pisa con los de antes."""
    import translate
    from PySide6.QtWidgets import QLineEdit
    root, window = versioned_window()
    tr = window.translate_book().parts
    dialog = window.open_options("Traducción")
    parts = dialog.parts
    parts["engine"].setCurrentIndex(parts["engine"].findText("DeepSeek"))
    parts["engine"].activated.emit(parts["engine"].currentIndex())
    hidden = parts["key"].echoMode() == QLineEdit.EchoMode.Password
    parts["key"].setText("sk-nueva")
    parts["eye"].click()
    shown = parts["key"].echoMode() == QLineEdit.EchoMode.Normal
    parts["accept"]()
    root.update()
    summary, used = tr["summary"].text(), (tr["engine"].currentText(), tr["key"].text())
    tr["close"]()
    root.update()
    stored = translate.unprotect(window.settings["translate"]["keys"]["DeepSeek"])
    window.close()
    assert hidden and shown, (hidden, shown)
    assert "DeepSeek" in summary and used == ("DeepSeek", "sk-nueva") and stored == "sk-nueva", (summary, used, stored)


def test_window_pdf_menu_puts_preferred_size_first():
    """El tamaño preferido en Opciones sale primero en «PDF ▾», con «(preferido)»."""
    root, window = visible_window()
    window.pdf_size_var.set("A4")
    window.fill_pdf_menu()
    first = window.pdf_btn.menu().actions()[0].text()
    window.close()
    assert first.startswith("Generar PDF (A4") and "(preferido)" in first, first


def test_window_options_are_only_in_options():
    """Los ajustes ya no están repartidos: la barra deja Perfil, «Opciones del libro…», PDF y Generar; no hay menú
    «Opciones ▾», ni Tema en Herramientas, ni idioma en «Editar datos». El informe lleva a sus opciones."""
    from PySide6.QtWidgets import QComboBox, QPushButton, QToolButton
    root, window = visible_window()
    window.show_book(convert.Book("Libro", "", b"", [convert.Block("p", 1, "Texto.")], []))
    bar = [b.text() for b in window.options_row.findChildren(QToolButton)]
    combos = window.options_row.findChildren(QComboBox)
    menus = [a.text() for b in window.root.findChildren(QToolButton) if b.menu() for a in b.menu().actions()]
    data_combos = window.data_panel.findChildren(QComboBox)
    window.quality()
    report = root.dialogs()[-1]
    link = [b.text() for b in report.findChildren(QPushButton) if b.text() == "Opciones del informe…"]
    window.close()
    assert "Opciones ▾" not in bar and len(combos) == 1, (bar, len(combos))
    assert "Tema" not in menus and "Opciones…  (Ctrl+,)" in menus, menus
    assert not data_combos and link, (data_combos, link)


def test_window_kindle_profile_hyphenates():
    root, window = visible_window()
    window.profile_var.set("Kindle")
    window.apply_profile()
    kindle = window.write_options()["hyphenate"]
    window.profile_var.set("Celular")
    window.apply_profile()
    phone = window.write_options()["hyphenate"]
    window.close()
    assert kindle and not phone, (kindle, phone)


def test_typography_dash_at_paragraph_end():
    """Kage 5: la portadilla "- Kage no Jitsuryokusha ni Naritakute! -" como párrafo rompía la tipografía."""
    assert convert._typography("- Kage no Jitsuryokusha ni Naritakute! -") == "—Kage no Jitsuryokusha ni Naritakute! —"
    assert convert._typography("-") == "—"


def test_footnotes_leave_body_and_keep_reference_place():
    """Re Zero: llamada elevada en letra pequeña tras una palabra y, al pie, la nota en letra menor que
    empieza por ese número. La llamada se quedaba al principio del párrafo o suelta, y la nota, en el cuerpo."""
    doc = pymupdf.open()
    cw = 11 * 0.6  # ancho de una letra de Courier a 11 pt
    x = 92 + cw * len("Pero estaba grabada en piedra")
    m = 92 + cw * len("Media tres m")
    add_page(doc, 1, [
        (72, 100, "Capitulo 1: Inicio", 20, "helv"),
        (92, 154, "Pero estaba grabada en piedra", 11, "cour"),
        (x, 151, "2", 7, "cour"),            # llamada elevada 3 pt
        (x + 7 * 0.6, 154, ". Fin.", 11, "cour"),
        (92, 182, "Media tres m", 11, "cour"),
        (m, 179, "2", 7, "cour"),            # exponente sin nota: «m2» sigue siendo texto
        (m + 7 * 0.6, 182, " de alto.", 11, "cour"),
        (92, 240, "Un tic", 11, "cour"),
        (92 + cw * 6, 237, "4", 7, "cour"),
        (92 + cw * 6 + 7 * 0.6, 240, " raro.", 11, "cour"),
        (72, 720, "4 Un gesto involuntario.", 9, "cour"),  # Re Zero 3: el número va en el span de la nota
        (72, 737, "2", 7, "cour"),           # la nota al pie, en letra menor
        (72 + 7 * 0.6 + 0.8, 740, "Su respuesta permanecia firme y", 9, "cour"),
        (72, 752, "no cambio", 9, "coit"),
        (72 + 9 * 0.6 * 9, 752, ".", 9, "cour"),
    ])
    for number in (2, 3):
        add_page(doc, number, [(92, 140, line("Otra pagina con texto normal de la novela", 57), 11, "cour"),
                               (72, 154, "y su final.", 11, "cour")])
    path = TMP / "notas-al-pie.pdf"
    doc.save(path)
    book = convert.analyze(path)
    note = next(b for b in book.blocks if "piedra" in b.html)
    assert note.html == ('Pero estaba grabada en piedra<span class="nota">Su respuesta permanecia firme y '
                         '<i>no cambio</i>.</span>. Fin.'), note.html
    assert any(b.html == "Media tres m2 de alto." for b in book.blocks), [b.html for b in book.blocks]
    assert any(b.html == 'Un tic<span class="nota">Un gesto involuntario.</span> raro.' for b in book.blocks), \
        [b.html for b in book.blocks]
    assert not any("permanecia" in b.html for b in book.blocks if b is not note), [b.html for b in book.blocks]


def test_footnotes_become_popup_notes():
    book = convert.Book("Libro", "", jpeg(60, 80), [
        convert.Block("heading", 1, "Capitulo 1"),
        convert.Block("p", 1, 'Hola<span class="nota">Nota <i>uno</i>.</span>. Fin.'),
        convert.Block("heading", 2, "Capitulo 2"),
        convert.Block("p", 2, 'Adios<span class="nota">Nota dos.</span>.')], [0, 2])
    chapters = [(0, "Capitulo 1"), (2, "Capitulo 2")]
    out, old, kobo = TMP / "notas.epub", TMP / "notas2.epub", TMP / "notas.kepub.epub"
    convert.write_epub(book, chapters, out)
    convert.write_epub(book, chapters, old, epub2=True)
    convert.write_epub(book, chapters, kobo, kepub=True)
    for path in (out, old, kobo):
        assert convert.check_epub(path) == [], (path, convert.check_epub(path))
    first, second = epub_file(out, "OEBPS/sec001.xhtml"), epub_file(out, "OEBPS/sec002.xhtml")
    assert 'Hola<a class="nref" epub:type="noteref" href="#nota1" id="ref1">1</a>. Fin.' in first, first
    assert ('<aside epub:type="footnote" class="nota" id="nota1"><p><a href="#ref1">1</a>. Nota <i>uno</i>.</p>'
            '</aside>') in first, first
    assert 'href="#nota2"' in second and 'id="nota2"' in second, second  # numeración seguida en el libro
    epub2 = epub_file(old, "OEBPS/sec001.xhtml")
    assert '<div class="nota" id="nota1">' in epub2 and "epub:type" not in epub2.split("<body>")[1], epub2
    text = convert.section_text(book, 1, 2)
    assert text == "Hola[Nota: Nota <i>uno</i>.]. Fin.", text
    convert.edit_section(book, 1, 2, text + "\n\nNuevo[Nota: Otra.] y [Nota: <i>mal]</i>")
    assert book.blocks[1].html == 'Hola<span class="nota">Nota <i>uno</i>.</span>. Fin.', book.blocks[1].html
    assert book.blocks[2].html == 'Nuevo<span class="nota">Otra.</span> y [Nota: <i>mal]</i>', book.blocks[2].html


def test_kepub_wraps_sentences_and_keeps_text():
    book = convert.Book("Libro", "", jpeg(60, 80), [
        convert.Block("heading", 1, "Capítulo 1: Inicio"),
        convert.Block("p", 1, "Hola. ¿Vienes? Tom &amp; Jerry <i>dijo algo. Y más</i> fin"),
        convert.Block("p", 1, "Sin punto final")], [0])
    plain, kobo = TMP / "normal.epub", TMP / "kobo.kepub.epub"
    convert.write_epub(book, [(0, "Capítulo 1: Inicio")], plain)
    convert.write_epub(book, [(0, "Capítulo 1: Inicio")], kobo, kepub=True)
    assert convert.check_epub(kobo) == [], convert.check_epub(kobo)
    with zipfile.ZipFile(plain) as a, zipfile.ZipFile(kobo) as b:
        before, after = a.read("OEBPS/sec001.xhtml").decode(), b.read("OEBPS/sec001.xhtml").decode()
    text = lambda s: " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s)).split())
    assert text(before) == text(after), (before, after)
    ids = re.findall(r'class="koboSpan" id="(kobo\.\d+\.\d+)"', after)
    assert len(ids) == len(set(ids)) >= 6, ids
    assert '<span class="koboSpan" id="kobo.2.1">Hola. </span>' in after, after
    assert re.search(r'<i><span class="koboSpan" id="kobo\.2\.\d+">dijo algo\. </span>', after), after
    assert 'id="book-columns"' in after and 'id="book-inner"' in after, after
    assert all("koboSpan" in p for p in re.findall(r"<p[^>]*>.*?</p>", after)), after
    assert convert._sentences("Uno. Dos... ¿Tres? «Cuatro.» cinco") == ["Uno. ", "Dos... ", "¿Tres? ",
                                                                        "«Cuatro.» ", "cinco"]


def test_check_epub_reports_problems():
    out = TMP / "revisar.epub"
    convert.write_epub(sectioned_book(), [(0, "Cap 1"), (6, "Cap 2")], out)
    assert convert.check_epub(out) == []
    broken = TMP / "roto.epub"
    with zipfile.ZipFile(out) as src, zipfile.ZipFile(broken, "w") as dst:
        for name in src.namelist():
            data = PAGE_MARK.sub("", src.read(name).decode()).encode()
            if name == "OEBPS/sec001.xhtml":
                data = data.replace(b"<p>Uno.</p>", b'<p>Uno &amp; <img src="img/no.jpg" alt=""/></p><p>sin cerrar')
            if name == "OEBPS/sec002.xhtml":
                data = re.sub(rb"<p>.*?</p>", b"", data)
            dst.writestr(name, data)
    problems = convert.check_epub(broken)
    assert any("sec001.xhtml: XML no válido" in p for p in problems), problems
    assert "Capítulo vacío: Cap 2" in problems, problems
    empty_part = convert.Book("Libro", "", b"", [convert.Block("heading", 1, "Parte uno"),
                                                 convert.Block("heading", 1, "Cap 1"), convert.Block("p", 1, "Ya.")], [0, 1])
    convert.write_epub(empty_part, [(0, "Parte uno"), (1, "Cap 1")], out)
    assert convert.check_epub(out) == []  # una portadilla vacía es a propósito


def test_window_fix_case_and_undo_it_at_once():
    import app

    root, window = visible_window()
    book = convert.Book("Libro", "", b"", [convert.Block("heading", 1, "Capítulo 1: La Gran Aventura"),
                                           convert.Block("p", 1, "Una gran aventura empieza."),
                                           convert.Block("heading", 1, "Capítulo 2: El Final")], [0, 2])
    window.show_book(book)
    window.fix_case()
    fixed = [window.tree.set(i, "title") for i in window.tree.get_children()]
    window.undo()
    back = [window.tree.set(i, "title") for i in window.tree.get_children()]
    window.close()
    assert fixed == ["Capítulo 1: La gran aventura", "Capítulo 2: El final"], fixed
    assert back == ["Capítulo 1: La Gran Aventura", "Capítulo 2: El Final"], back


def test_window_undo_text_edit():
    import app

    root, window = visible_window()
    window.show_book(sectioned_book())
    window.set_kind("6", app.OFF)
    window.tree.selection_set("0")
    root.update()
    window.apply_edit([1, 2, "Uno cambiado.\n# Nuevo"], "0")
    edited = [window.tree.set(i, "title") for i in window.tree.get_children()]
    window.undo()  # el texto
    after_text = ([window.tree.set(i, "title") for i in window.tree.get_children()], window.edits,
                  window.book.blocks[1].html, window.tree.set("6", "inc"))
    window.undo()  # el tipo de Cap 2, de antes de editar
    kind = window.tree.set("6", "inc")
    window.close()
    assert edited == ["Cap 1", "Nuevo", "Lado A", "Lado B", "Cap 2"], edited
    assert after_text == (["Cap 1", "Lado A", "Lado B", "Cap 2"], [], "Uno.", app.OFF), after_text
    assert kind == app.ON, kind


def test_window_remembers_settings_and_recent_pdfs():
    import time

    import app

    root, window = visible_window()
    settings = app.SETTINGS
    window.settings["defaults"].update(style="Moderno", gray=True)  # «Libros nuevos», no lo del último libro
    pdf = make_novel()
    window.open_path(str(pdf))  # como un PDF arrastrado sobre el .exe
    deadline = time.time() + 60
    while window.done and time.time() < deadline:
        root.update()
        time.sleep(0.05)
    loaded = window.book is not None
    window.theme_var.set("Oscuro")
    window.close()
    app.SETTINGS = settings
    window = app.App(app.Window())
    got = (window.style_var.get(), window.gray_var.get(), window.recent_menu.actions()[0].text(),
           window.settings["folder"], window.theme_var.get(), window.dark)
    recent = card_text(window, 0)  # pantalla vacía: el tomo y su estado
    window.root.on_close = None
    window.close()
    import qtk
    import theme
    theme.apply(qtk.app(), "Claro")  # las demás pruebas, en claro
    assert loaded
    assert got == ("Moderno", True, "novela.pdf", str(pdf.parent.resolve()), "Oscuro", True), got
    assert "novela" in recent and ("Revisado" in recent or "EPUB" in recent), recent


def test_window_epub_preview_continues_into_next_section():
    root, window = visible_window()
    book = long_book()
    book.candidates.append(len(book.blocks))
    book.blocks += [convert.Block("heading", 1, "Cap 2"), convert.Block("p", 1, "Fin.")]
    window.show_book(book)
    items = window.tree.get_children()
    window.tree.selection_set(items[0])
    root.update()
    pages = window.epub_pages
    for _ in range(pages - 1):
        window.epub_step(1)
    last = window.epub_page
    window.epub_step(1)  # pasa a la sección siguiente
    root.update()
    moved = (window.tree.selection()[0], window.epub_page)
    window.epub_step(-1)  # vuelve a la última página de la anterior
    root.update()
    back = (window.tree.selection()[0], window.epub_page, window.epub_pages)
    window.close()
    assert pages > 1 and last == pages - 1, (pages, last)
    assert moved == (items[1], 0), moved
    assert back == (items[0], pages - 1, pages), back


def make_rezero_like():
    """Como Re Zero: página de créditos con enlaces, capítulos con "Parte 1, 2…" que vuelven a empezar,
    pie que cambia con el capítulo ("Grupo X Capítulo 1 | 3") y una página girada con texto vertical."""
    doc = pymupdf.open()

    def page(n, lines, footer):
        p = doc.new_page(width=595, height=842)
        p.insert_text((200, 815), footer, fontsize=10, fontname="helv")
        for x, y, text, size, font in lines:
            p.insert_text((x, y), text, fontsize=size, fontname=font)
        return p

    page(1, [(200, 100, "OBRA ORIGINAL", 16, "hebo"), (150, 130, "Autor: Tappei Nagatsuki", 11, "helv"),
             (200, 170, "TRADUCCION", 16, "hebo"), (150, 200, "Traduccion al ingles: YenPress", 11, "helv"),
             (150, 220, "Traduccion al espanol: Ana - Luis -", 11, "helv"), (150, 240, "Eva", 11, "helv"),
             (150, 280, "Visitanos en http://grupo.wordpress.com/", 11, "helv")], "Grupo X Creditos | 1")
    n = 2
    for chapter in (1, 2):
        for part in (1, 2):
            names = {1: "El fin del principio", 2: "Una prueba tardia"}  # distintos, como en los libros
            lines = [(72, 130, f"Capitulo {chapter}: {names[chapter]}", 20, "helv")] if part == 1 else []
            lines += [(72, 160, f"Parte {part}", 11, "hebo")]
            lines += [(72, 190 + 14 * i, line(f"Texto del capitulo {chapter} parte {part} linea {i}", 60), 11, "cour")
                      for i in range(20)]
            page(n, lines, f"Grupo X Capitulo {chapter} | {n}")
            n += 1
    p = page(n, [(72, 140, line("Ultima pagina del libro", 60), 11, "cour")], f"Grupo X Palabras del autor | {n}")
    p.insert_text((300, 400), "Re Zero Volumen 1", fontsize=16, fontname="hebo", rotate=90)  # texto vertical
    path = TMP / "rezero.pdf"
    doc.save(path)
    return path


def test_rezero_like_pdf():
    book = convert.analyze(make_rezero_like())
    heads = [(html.unescape(book.blocks[k].html), k in book.candidates) for k, b in enumerate(book.blocks)
             if b.kind == "heading"]
    assert heads == [("OBRA ORIGINAL", False), ("TRADUCCION", False), ("Capitulo 1: El fin del principio", True),
                     ("Parte 1", False), ("Parte 2", False), ("Capitulo 2: Una prueba tardia", True),
                     ("Parte 1", False), ("Parte 2", False)], heads
    assert (book.author, book.translator) == ("Tappei Nagatsuki", "Ana - Luis - Eva"), (book.author, book.translator)
    text = " ".join(b.html for b in book.blocks)
    assert "Grupo X" not in text and "Re Zero Volumen" not in text, text[-300:]
    cut = [book.blocks[k].html for k in convert.link_sections(book)]
    assert cut == ["OBRA ORIGINAL", "TRADUCCION"], cut  # toda la página de créditos, no solo el último título


def test_named_part_stays_a_chapter():
    """"Parte uno: Stifled Shadow" (Nageki) es una parte del libro: tiene nombre y no se repite."""
    doc = pymupdf.open()
    for n, title in enumerate(("Parte 1: La sombra", "Capitulo 1: Inicio", "Capitulo 2: Final"), 1):
        add_page(doc, n, [(72, 100, title, 20, "helv"), (72, 140, "Texto de la pagina.", 11, "cour")])
    path = TMP / "partes.pdf"
    doc.save(path)
    book = convert.analyze(path)
    assert [html.unescape(book.blocks[k].html) for k in book.candidates] == [
        "Parte 1: La sombra", "Capitulo 1: Inicio", "Capitulo 2: Final"]


def test_title_page_with_label_below_name_and_repeat_on_next_page():
    """Eminence 7: portadilla con el nombre arriba, «Chapter 1» enorme abajo y la marca del libro en letra diminuta;
    la página siguiente repite la etiqueta (pequeña, en negrita) y el nombre antes del texto. Era tres capítulos:
    es uno, «Chapter 1: Nombre», sin la marca ni la repetición."""
    doc = pymupdf.open()
    body = [(92, 230 + 14 * n, line(f"Linea {n} del texto de la novela", 60), 11, "cour") for n in range(30)]
    for number, label, name, typo in ((1, "Prologue", ["Spring is the Season", "for New Journeys!"], "Journeys!"),
                                      (3, "Chapter 1", ["This Time, There's No", "Way You'll See Through"], "Thruogh")):
        add_page(doc, number, [(200, 200, name[0], 18, "helv"), (220, 225, name[1], 18, "helv"),
                               (330, 260, "Mi Novela Ligera", 6, "hebo"), (380, 268, "Volume 7", 6, "hebo"),
                               (40, 700, label, 58, "helv")])
        add_page(doc, number + 1, [(260, 70, label, 7.5, "hebo"), (200, 95, name[0], 14, "helv"),
                                   (200, 113, name[1].replace("Through", typo), 14, "helv")] + body)
    doc.set_toc([[1, "Prologue: Spring is the Season for New Journeys!", 1],
                 [1, "Chapter 1: This Time, There's No Way You'll See Through", 3]])
    path = TMP / "portadilla-etiqueta-abajo.pdf"
    doc.save(path)
    book = convert.analyze(path)
    heads = [(html.unescape(b.html), k in book.candidates) for k, b in enumerate(book.blocks) if b.kind == "heading"]
    after = [plain(book.blocks[k + 1].html)[:8] for k in book.candidates]
    stray = [b.html for b in book.blocks if b.kind == "p" and ("Volume" in b.html or "Way You" in b.html)]
    assert heads == [("Prologue: Spring is the Season for New Journeys!", True),
                     ("Chapter 1: This Time, There's No Way You'll See Through", True)], heads
    assert after == ["Linea 0 ", "Linea 0 "] and not stray, (after, stray)
    # Lo omitido ya estaba en el título: no es «Texto no incluido»; solo la línea con la errata cuenta
    assert [(page, letters) for page, letters, _ in book.skipped] == [(4, 18)], book.skipped


def test_blank_gap_between_paragraphs_is_a_scene_break():
    """Mitsuha (GambaTrans): la escena cambia con un hueco de unas tres líneas en blanco, sin símbolo ni adorno.
    Es un separador de escena (sin imagen ni texto: sale el del estilo). Un hueco con una ilustración o un
    título dentro, no; el espacio normal entre párrafos, tampoco."""
    doc = pymupdf.open()
    para = lambda y, t: [(92, y, line(t, 57), 11, "cour"), (72, y + 14, line("sigue el parrafo", 60), 11, "cour"),
                         (72, y + 28, "fin.", 11, "cour")]
    add_page(doc, 1, [(72, 100, "Capitulo 1: Inicio", 20, "helv")] + para(150, "Primera escena del capitulo")
             + para(194, "Sigue la primera escena") + para(290, "Cayo la noche y empezo otra")  # hueco de ~4 líneas
             + para(334, "Mas de la segunda escena"))
    page = add_page(doc, 2, para(150, "Texto antes de la ilustracion") + para(330, "Texto despues de la ilustracion")
                    + [(72, 470, "Un subtitulo", 16, "hebo")] + para(520, "Texto tras el subtitulo"))
    page.insert_image(pymupdf.Rect(200, 200, 300, 300), stream=jpeg(100, 100))
    # 3: ficha de estado tras un hueco (Maryoku) y la firma del autor al final del epílogo (Eminence 7): no
    add_page(doc, 3, para(150, "Mire mi pantalla de estado") + [(72, 250, "NOMBRE: Chise (Reencarnador)", 11, "cour")]
             + para(350, "Ultimo parrafo del epilogo") + [(300, 450, "Autor Ejemplo", 11, "cour")])
    path = TMP / "hueco-escena.pdf"
    doc.save(path)
    blocks = convert.analyze(path).blocks
    kinds = [[b.kind for b in blocks if b.page == n and b.kind != "image"] for n in (1, 2, 3)]
    gap = next((b for b in blocks if b.kind == "break"), None)
    assert kinds == [["heading", "p", "p", "break", "p", "p"], ["p", "p", "heading", "p"], ["p", "p", "p", "p"]], kinds
    assert gap is not None and not gap.image and not gap.html, gap
    # Megami 4: una línea en blanco entre muchos párrafos, en todas las páginas: es el estilo del libro
    doc = pymupdf.open()
    for n in range(1, 4):
        add_page(doc, n, [(72, 100, f"Capitulo {n}: Otro", 20, "helv")]
                 + [row for y in range(150, 700, 90) for row in para(y, "Parrafo con hueco despues")])
    doc.save(TMP / "huecos-estilo.pdf")
    styled = [b.kind for b in convert.analyze(TMP / "huecos-estilo.pdf").blocks]
    assert "break" not in styled, styled


def test_blank_line_after_bold_subtitle_is_not_a_scene_break():
    """Maryoku 1 (pág. 177): «Historias Cortas Adicionales» y «Un Lindo Monstruo» van en negrita del tamaño del
    texto, con una línea en blanco debajo. Ese hueco es el aire del subtítulo: salían dos separadores de escena
    que el PDF no tiene."""
    doc = pymupdf.open()
    # Sin sangría: interlínea de 14, párrafos a 21 (línea y media) y la línea en blanco deja 42 (tres líneas)
    para = lambda y, t: [(72, y, line(t, 60), 11, "cour"), (72, y + 14, line("sigue el parrafo", 60), 11, "cour"),
                         (72, y + 28, "fin.", 11, "cour")]
    add_page(doc, 1, [(72, 100, "Capitulo 1: Inicio", 20, "helv")] + para(150, "Primer parrafo del epilogo")
             + para(199, "Segundo parrafo del epilogo") + para(248, "Tercer parrafo del epilogo")
             + [(72, 297, "Historias cortas adicionales", 11, "cobo"), (72, 339, "Un lindo monstruo", 11, "cobo")]
             + para(381, "Texto de la historia corta") + para(430, "Sigue la historia corta"))
    path = TMP / "subtitulo-negrita-hueco.pdf"
    doc.save(path)
    blocks = convert.analyze(path).blocks
    kinds = [b.kind for b in blocks]
    assert "break" not in kinds, [(b.kind, b.html[:30]) for b in blocks]
    assert any("Un lindo monstruo" in b.html for b in blocks), [b.html[:30] for b in blocks]


def test_heading_decoration_and_lowercase_continuation_across_pages():
    """Psychology of Harem 2 (Next Novels): los títulos llevan un rombo delante («◆Capítulo 1: El Discurso»),
    que se quedaba en el capítulo. Y sin sangría (párrafos separados por espacio), un párrafo que sigue en la
    página siguiente tras «¡Gran escritor!"» con minúscula («y pasabas…») salía partido en dos."""
    doc = pymupdf.open()
    text = lambda y, t: [(72, y, line(t, 60), 11, "cour"), (72, y + 14, line("y sigue el parrafo", 60), 11, "cour"),
                         (72, y + 28, "fin del parrafo.", 11, "cour")]
    add_page(doc, 1, [(72, 100, "◆Capitulo 1: El Discurso", 16, "china-s")] + text(150, "Primer parrafo")
             + text(210, "Segundo parrafo") + [(72, 270, line("Tercer parrafo que sigue", 60), 11, "cour"),
                                                (72, 284, 'te llamaban "¡Gran escritor!"', 11, "cour")])
    add_page(doc, 2, [(72, 100, line("y pasabas a la historia, que envidia", 60), 11, "cour"), (72, 114, "la era.", 11, "cour")]
             + text(160, "Otro parrafo"))
    path = TMP / "rombo-y-pagina.pdf"
    doc.save(path)
    book = convert.analyze(path)
    heads = [convert._plain(b) for b in book.blocks if b.kind == "heading"]
    joined = [convert._plain(b) for b in book.blocks if "Gran escritor" in b.html]
    assert heads == ["Capitulo 1: El Discurso"], heads
    assert len(joined) == 1 and "y pasabas" in joined[0], joined


def test_bold_bare_number_marks_a_part():
    """Re Zero 4 y 5: los apartados de cada capítulo van marcados con un número solo, en negrita y del tamaño del
    texto («2»), y la numeración vuelve a empezar en cada capítulo. Salía como un párrafo «2» dentro del texto:
    es un apartado, como «Parte 2» (subtítulo, no capítulo). Un número sin negrita (página que sobra) no."""
    doc = pymupdf.open()
    text = lambda y, t: [(92, y, line(t, 57), 11, "cour"), (72, y + 14, "fin.", 11, "cour")]
    for n, chapter in ((1, "Capitulo 1: Inicio"), (2, "Capitulo 2: Final")):
        add_page(doc, n, [(72, 100, chapter, 20, "helv")] + text(150, "Primer apartado del capitulo")
                 + [(72, 200, "2", 11, "cobo")] + text(240, "Segundo apartado del capitulo")
                 + [(72, 290, "61", 11, "cour")] + text(330, "Sigue el segundo apartado"))
    path = TMP / "apartados-numero.pdf"
    doc.save(path)
    book = convert.analyze(path)
    heads = [(html.unescape(b.html).strip(), k in book.candidates) for k, b in enumerate(book.blocks) if b.kind == "heading"]
    assert heads == [("Capitulo 1: Inicio", True), ("2", False), ("Capitulo 2: Final", True), ("2", False)], heads
    assert convert.is_subpart("2") and convert.is_subpart("Parte 3") and not convert.is_subpart("Capitulo 2")


def test_bookmarked_chapter_name_joins_bare_label():
    """Re Zero 3: marcador de nivel 1 "Capitulo 1" y de nivel 2 su nombre, en líneas separadas."""
    doc = pymupdf.open()
    add_page(doc, 1, [(150, 118, "Capitulo 1", 40, "helv"), (100, 170, "El reinicio de Subaru", 24, "helv"),
                      (72, 218, "Parte 1", 11, "hebo"), (72, 250, "Texto del capitulo.", 11, "cour")])
    add_page(doc, 2, [(150, 118, "Capitulo 2", 40, "helv"), (100, 170, "Llore y grite", 24, "helv"),
                      (72, 250, "Mas texto.", 11, "cour")])
    doc.set_toc([[1, "Capitulo 1", 1], [2, "El reinicio de Subaru", 1], [1, "Capitulo 2", 2], [2, "Llore y grite", 2]])
    path = TMP / "marcadores-nombre.pdf"
    doc.save(path)
    book = convert.analyze(path)
    heads = [(html.unescape(b.html), k in book.candidates) for k, b in enumerate(book.blocks) if b.kind == "heading"]
    assert heads == [("Capitulo 1: El reinicio de Subaru", True), ("Parte 1", False),
                     ("Capitulo 2: Llore y grite", True)], heads


def framed_pdf():
    """Página como la ventana de estado de Witch 1: doble línea de tiras finas alrededor de líneas cortas en negrita
    con viñetas en imagen (■ y ◆) y, debajo, un recuadro con prosa de dos líneas."""
    import io
    from PIL import Image, ImageDraw

    def bullet(diamond):
        image = Image.new("RGB", (24, 24), "white")
        draw = ImageDraw.Draw(image)
        if diamond:
            draw.polygon([(12, 0), (23, 12), (12, 23), (0, 12)], fill="black")
        else:
            draw.rectangle([0, 0, 23, 23], fill="black")
        out = io.BytesIO()
        image.save(out, "PNG")
        return out.getvalue()

    doc = pymupdf.open()
    page = add_page(doc, 1, [(72, 100, "Capitulo 1: Inicio", 20, "helv"),
                             (92, 140, line("Abrio su ventana de estado y la miro un rato largo", 60), 11, "cour"),
                             (72, 154, "con calma.", 11, "cour"),
                             (106, 200, "Lorna Hermit, Lv. 1", 11, "hebo"), (110, 214, "HP: 10/10", 11, "hebo"),
                             (106, 236, "Equipment", 11, "hebo"), (110, 250, "Weapon: None", 11, "hebo"),
                             (92, 300, "Siguio andando por el bosque.", 11, "cour"),
                             (100, 350, line("Aviso del sistema que ocupa dos lineas dentro del", 55), 11, "cour"),
                             (100, 364, "recuadro y acaba aqui.", 11, "cour")])
    for x0, y0, x1, y1, gap in ((80, 180, 520, 262, 0), (80, 180, 520, 262, 3), (80, 332, 520, 376, 0)):
        for r in ((x0 + gap, y0 + gap, x1 - gap, y0 + gap + 1.5), (x0 + gap, y1 - gap - 1.5, x1 - gap, y1 - gap),
                  (x0 + gap, y0 + gap, x0 + gap + 1.5, y1 - gap), (x1 - gap - 1.5, y0 + gap, x1 - gap, y1 - gap)):
            page.draw_rect(pymupdf.Rect(r), color=None, fill=(0, 0, 0), width=0)
    page.insert_image(pymupdf.Rect(95, 192, 102, 199), stream=bullet(False))
    page.insert_image(pymupdf.Rect(94, 228, 102, 236), stream=bullet(True))
    path = TMP / "recuadro-estado.pdf"
    doc.save(path)
    return path


def glyph_png(ch):
    """PNG del símbolo ch dibujado con la fuente de símbolos de Windows, como las imágenes de Witch 1."""
    doc = pymupdf.open()
    page = doc.new_page(width=60 * len(ch), height=60)
    page.insert_font(fontname="sym", fontfile=str(convert.SYMBOLS_TTF))
    page.insert_text((8, 48), ch, fontsize=44, fontname="sym")
    import io
    from PIL import Image, ImageOps
    image = Image.open(io.BytesIO(page.get_pixmap().tobytes("png"))).convert("L")
    image = image.crop(ImageOps.invert(image).getbbox())  # justo la tinta, como las imágenes del EPUB
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def test_inline_symbol_images_become_letters_and_image_pages_stay():
    """The World's Strongest Witch 1: ♡, ♪, ➔ y «♥♥» van como imágenes diminutas en el texto y se perdían; ahora son
    su letra, en su sitio aunque caigan dentro del texto de un span («find! ♪”»). Una página solo con una ilustración
    de menos de media página (portadilla, ilustraciones a color) se perdía entera; y una sección del índice del EPUB
    que queda sin nada («Cover», cuya imagen es la portada) ya no sale como capítulo vacío."""
    doc = pymupdf.open()
    cover = doc.new_page(width=595, height=842)
    cover.insert_image(cover.rect, stream=glyph_png("■"))
    page = add_page(doc, 2, [(72, 100, "Capitulo 1: Inicio", 20, "helv"),
                             (92, 140, "Muy bien, Margrave!", 11, "cour"),
                             (92, 160, 'Bien hecho!     " dijo ella.', 11, "cour"),
                             (92, 180, "Sube de nivel 1    2 y celebra.", 11, "cour"),
                             (92, 200, "Registro completo", 11, "cour"),
                             (72, 220, line("Una linea de texto hasta el margen", 60), 11, "cour")])
    width = pymupdf.get_text_length("Muy bien, Margrave! ", "cour", 11)
    page.insert_image(pymupdf.Rect(92 + width, 131, 92 + width + 9, 141), stream=glyph_png("♡"))
    width = pymupdf.get_text_length("Bien hecho!     ", "cour", 11)  # pegado a la comilla, como en Witch
    page.insert_image(pymupdf.Rect(92 + width - 7, 151, 92 + width, 161), stream=glyph_png("♪"))
    width = pymupdf.get_text_length("Sube de nivel 1 ", "cour", 11)
    page.insert_image(pymupdf.Rect(92 + width + 2, 172, 92 + width + 13, 180), stream=glyph_png("➔"))
    width = pymupdf.get_text_length("Registro completo", "cour", 11)
    page.insert_image(pymupdf.Rect(92 + width + 2, 192, 92 + width + 19, 200), stream=glyph_png("♥♥"))
    art = add_page(doc, 3, [])
    art.insert_image(pymupdf.Rect(72, 100, 523, 400), stream=glyph_png("★"))  # un 30 % de la página
    add_page(doc, 4, [(72, 100, "Capitulo 2: Final", 20, "helv"), (92, 140, "Fin del libro.", 11, "cour")])
    doc.set_toc([[1, "Cover", 1], [1, "Capitulo 1: Inicio", 2], [1, "Capitulo 2: Final", 4]])
    path = TMP / "simbolos.pdf"
    doc.save(path)
    book = convert.analyze(path)
    text = [convert._plain(b) for b in book.blocks if b.kind == "p" and "Grupo" not in b.html and "Novela" not in b.html]
    assert text[:3] == ["Muy bien, Margrave! ♡", 'Bien hecho! ♪" dijo ella.', "Sube de nivel 1 ➔ 2 y celebra."], text
    assert text[3].startswith("Registro completo ♥♥"), text
    heads = [convert._plain(book.blocks[k]) for k in book.candidates]
    assert heads == ["Capitulo 1: Inicio", "Capitulo 2: Final"], heads  # «Cover» vacío ya no es capítulo
    kinds = [(b.kind, b.page) for b in book.blocks]
    assert ("image", 3) in kinds, kinds  # la ilustración de menos de media página sigue ahí


def test_status_box_keeps_frame_bullets_and_survives_edits():
    """The World's Strongest Witch 1: las ventanas de estado van en un recuadro de doble línea con viñetas en imagen
    (■, ◆), y salían como párrafos sueltos sin borde ni viñeta. Ahora cada línea va en recuadro («<marco>» en el
    texto del editor), con su viñeta como letra; la prosa de un recuadro se une como siempre; el EPUB los mete en un
    div con borde y sangra las líneas sin viñeta. Traducido sin la marca, sigue en su recuadro."""
    import translate

    book = convert.analyze(framed_pdf())
    rows = [(b.align, convert._plain(b)) for b in book.blocks[book.candidates[0] + 1:] if b.kind == "p"
            and "Grupo Traductor" not in b.html]
    assert rows == [("", "Abrio su ventana de estado y la miro un rato largo relleno r con calma."),
                    ("frame", "■ Lorna Hermit, Lv. 1"), ("frame", "HP: 10/10"), ("frame", "◆ Equipment"),
                    ("frame", "Weapon: None"), ("", "Siguio andando por el bosque."),
                    ("frame", "Aviso del sistema que ocupa dos lineas dentro del relle recuadro y acaba aqui.")], rows
    start = book.candidates[0] + 1
    text = convert.section_text(book, start, len(book.blocks))
    assert "\n\n<marco>■ <b>Lorna Hermit, Lv. 1</b>\n\n<marco><b>HP: 10/10</b>\n\n" in text, text
    units = translate.units(book, start, len(book.blocks))
    assert ("<marco>", "<b>HP: 10/10</b>") in [(p, t) for _, p, t in units], units
    # La traducción vuelve con su prefijo; una revisión de antes de los recuadros, sin él y sin viñeta, también
    old = convert.section_text(book, start, len(book.blocks))
    lines = old.split("\n\n")
    lines[1] = "<b>Lorna Hermit, nv. 1</b>"
    lines[2] = "<marco><b>PV: 10/10</b>"
    convert.edit_section(book, start, len(book.blocks), "\n\n".join(lines))
    again = [(b.align, convert._plain(b)) for b in book.blocks[start:start + 3]]
    assert again[1:] == [("frame", "■ Lorna Hermit, nv. 1"), ("frame", "PV: 10/10")], again
    lines = convert.section_text(book, start, len(book.blocks)).split("\n\n")
    lines[3] = lines[3].removeprefix("<marco>")  # quitar solo el recuadro: se queda sin él
    convert.edit_section(book, start, len(book.blocks), "\n\n".join(lines))
    assert book.blocks[start + 3].align == "" and book.blocks[start + 2].align == "frame"
    out = TMP / "recuadro.epub"
    convert.write_epub(book, [(book.candidates[0], "Capitulo 1")], out)
    import zipfile
    with zipfile.ZipFile(out) as z:
        page = next(z.read(n).decode() for n in z.namelist() if n.endswith(".xhtml") and b"Lorna" in z.read(n))
    box = page[page.index('<div class="marco">'):page.index("</div>", page.index('<div class="marco">'))]
    assert box.count("<p") == 2 and '<p class="sub">' in box and "■" in box, box
    assert '<p class="head">◆' not in page  # «◆ Equipment» quedó fuera del recuadro
    assert page.count('<div class="marco">') == 3, page  # el «◆ Equipment» sacado parte el recuadro en dos
    assert convert.check_epub(out) == [], convert.check_epub(out)


def test_editor_sheet_frames_round_trip_and_toggle():
    """Los párrafos en recuadro se ven juntos y con borde en la hoja, vuelven igual al texto («<marco>») y el botón
    «Recuadro» los mete y los saca, con un solo deshacer."""
    import qtk
    from PySide6.QtGui import QTextCursor

    import editor

    qtk.app()
    sheet = editor.Sheet()
    lines = [(1, "Antes."), (2, "<marco>■ <b>Lorna</b>"), (3, "<marco><b>HP: 10/10</b>"), (4, "Después.")]
    sheet.set_content(lines, {})
    assert sheet.content() == "\n\n".join(line for _, line in lines), sheet.content()
    doc = sheet.document()
    second, third = doc.findBlockByNumber(1).blockFormat(), doc.findBlockByNumber(2).blockFormat()
    assert second.topMargin() > 0 and second.bottomMargin() == 0 and third.leftMargin() > second.leftMargin()
    sheet.resize(500, 400)
    image = sheet.viewport().grab().toImage()  # el doble borde: dos líneas largas arriba y dos abajo
    back = image.pixelColor(5, 5)
    long = [y for y in range(image.height()) if sum(image.pixelColor(x, y) != back for x in range(60, 440, 4)) > 80]
    assert len(long) == 4, long
    c = sheet.textCursor()
    c.setPosition(doc.findBlockByNumber(3).position())
    sheet.setTextCursor(c)
    assert not sheet.is_framed()
    sheet.toggle_frame()
    assert sheet.is_framed() and sheet.content().endswith("<marco>Después."), sheet.content()
    c.setPosition(doc.findBlockByNumber(1).position())
    c.setPosition(doc.findBlockByNumber(3).position() + 2, QTextCursor.MoveMode.KeepAnchor)
    sheet.setTextCursor(c)
    sheet.toggle_frame()
    assert "<marco>" not in sheet.content(), sheet.content()
    sheet.undo()
    assert sheet.content().count("<marco>") == 3, sheet.content()


def test_bookmarked_page_starts_new_paragraph_without_indent():
    """The World's Strongest Witch 1 (EPUB, pág. 204): el Afterword lleva el título solo en una imagen y su primera
    línea va sin sangría; con párrafos marcados por sangría se unía al último párrafo del capítulo anterior, antes
    del título. Una página que el índice marca empieza párrafo; una página sin marca sigue el párrafo abierto."""
    doc = pymupdf.open()
    body = [(92, 140 + 20 * n, line("Una linea de texto con sangria", 60), 11, "cour") for n in range(3)]
    add_page(doc, 1, [(72, 100, "Capitulo 1: Inicio", 20, "helv")] + body
             + [(72, 220, line("y su continuacion sin sangria hasta el borde", 60), 11, "cour"),
                (92, 240, line("Ultimo parrafo del capitulo que sigue", 60), 11, "cour")])
    add_page(doc, 2, [(72, 140, "en la pagina siguiente y acaba.", 11, "cour")])
    add_page(doc, 3, [(72, 300, "Hola, me llamo Autor.", 11, "cour"),
                      (92, 320, line("Gracias por leer esta historia", 60), 11, "cour")])
    doc.set_toc([[1, "Capitulo 1: Inicio", 1], [1, "Epilogo", 3]])
    path = TMP / "marcador-sin-sangria.pdf"
    doc.save(path)
    book = convert.analyze(path)
    text = [(b.kind, convert._plain(b)) for b in book.blocks]
    last = next(t for kind, t in text if "Ultimo parrafo" in t)
    assert last.endswith("en la pagina siguiente y acaba."), text  # sin marca: sigue el párrafo
    k = next(k for k, (kind, t) in enumerate(text) if t == "Epilogo")
    assert text[k + 1] == ("p", "Hola, me llamo Autor.") and "Hola" not in last, text


def test_numbered_chapter_titles():
    cases = {"Capítulo Uno: Las luchas": "Capítulo 1: Las luchas", "Capítulo IV: ¡Mi nombre!": "Capítulo 4: ¡Mi nombre!",
             "Capitulo 3": "Capítulo 3", "CAPITULO DOS: FIN": "CAPÍTULO 2: FIN", "Prologo: Un sueño": "Prólogo: Un sueño",
             "Capítulo Final": "Capítulo Final", "Interludio: Rem": "Interludio: Rem"}
    got = {k: convert.numbered(k) for k in cases}
    assert got == cases, {k: v for k, v in got.items() if v != cases[k]}


def test_move_section_and_replay():
    import app

    book = sectioned_book()  # 0 Cap 1, 1 Uno, 2 Lado A, 3 Texto A, 4 Lado B, 5 Texto B, 6 Cap 2, 7 Dos
    mapping = convert.move_section(book, 6, 8, 2)  # Cap 2 antes de Lado A
    assert [b.html for b in book.blocks] == ["Cap 1", "Uno.", "Cap 2", "Dos.", "Lado A", "Texto A.", "Lado B",
                                             "Texto B."], [b.html for b in book.blocks]
    assert (mapping[6], mapping[2], book.candidates) == (2, 4, [0, 2])
    again = sectioned_book()
    done = app._replay(again, {"edits": [["move", 6, 8, 2], [5, 6, "Texto A cambiado."]]})
    assert done == [["move", 6, 8, 2], [5, 6, "Texto A cambiado."]], done
    assert [b.html for b in again.blocks][4:6] == ["Lado A", "Texto A cambiado."], [b.html for b in again.blocks]


def test_duplicate_images_and_trim():
    art = two_color(300, 400)
    other = jpeg(300, 400)
    book = convert.Book("Libro", "", art, [convert.Block("image", 1, image=other), convert.Block("image", 2, image=art),
                                           convert.Block("image", 3, image=other)], [])
    assert convert.duplicate_images(book) == [1, 2]  # la portada otra vez y la repetida
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 400, 500), False)
    pix.set_rect(pix.irect, (255, 255, 255))
    pix.set_rect(pymupdf.IRect(40, 50, 360, 450), (30, 90, 160))
    trimmed = pymupdf.Pixmap(convert._trim(pix.tobytes("jpg", jpg_quality=95)))
    assert abs(trimmed.width - 320) <= 4 and abs(trimmed.height - 400) <= 4, (trimmed.width, trimmed.height)
    assert convert._trim(other) == other  # sin margen blanco no cambia


def test_printed_toc_finds_chapters_and_is_not_a_grave_warning():
    """Maryoku 1: la página «Tabla de contenido» se omite y salía como aviso grave («Texto no incluido»). Ahora
    se leen sus títulos (uno partido en dos líneas): un capítulo sin palabra clave antes del primero que la
    lleva cuenta como capítulo, y la ventana avisa del título del índice que no está en la lista."""
    doc = pymupdf.open()
    add_page(doc, 1, [(72, 100, "Tabla de contenido", 20, "hebo"),
                      (72, 140, "El comienzo de un viaje muy largo .......... 2", 11, "helv"),
                      (72, 160, "Capitulo 1: Inicio de la historia de la bruja y", 11, "helv"),
                      (72, 174, "su golem .......... 3", 11, "helv"),
                      (72, 194, "Capitulo 2: Perdido .......... 4", 11, "helv")])
    body = [(72, 140 + 14 * n, line(f"Linea {n} del texto de la novela", 60), 11, "cour") for n in range(12)]
    add_page(doc, 2, [(72, 100, "El comienzo de un viaje muy largo", 20, "helv")] + body)
    add_page(doc, 3, [(72, 100, "Capitulo 1: Inicio de la historia de la bruja y su golem", 20, "helv")] + body)
    add_page(doc, 4, body)
    pdf = TMP / "indice-impreso.pdf"
    doc.save(pdf)
    book = convert.analyze(pdf)
    titles = [convert._plain(book.blocks[k]) for k in book.candidates]
    assert book.toc == [("El comienzo de un viaje muy largo", 2),
                        ("Capitulo 1: Inicio de la historia de la bruja y su golem", 3),
                        ("Capitulo 2: Perdido", 4)], book.toc
    assert titles == ["El comienzo de un viaje muy largo", "Capitulo 1: Inicio de la historia de la bruja y su golem"], titles
    assert not [i for i in convert.quality_report(book) if i.kind == "skipped"], "el índice leído no es un aviso grave"
    root, window = visible_window()
    window.pdf = pdf
    window.show_book(book)
    issues = [(i.kind, i.text, i.level) for i in window.toc_issues()]
    status = window.status.text()
    window.close()
    assert issues == [("toc", "Capitulo 2: Perdido", "Menor")], issues  # sin título en el texto: menor
    assert "índice del PDF en la página 1 (3 títulos)" in status, status


def test_printed_toc_lines_and_matching():
    """Índices reales: «| Prólogo | .... 15» (Kage 5), un número sin puntos antes del capítulo siguiente
    (Maryoku 1: «… mundo . 36»), títulos partidos sin puntos ni páginas (Nageki 1). Al comparar, «Capítulo 1»
    del índice es «Capítulo 1: …» de la página pero no «Capítulo 10: …», y la misma etiqueta con el título
    traducido distinto («Thousand Tricks» / «Mil trucos») es el mismo capítulo."""
    from types import SimpleNamespace as R

    rows = lambda *texts: [R(text=t) for t in texts]
    assert convert._toc_entries(rows("Contenido", "| Prólogo | .......... 15", "| Capítulo 1 | ...... 54")) == [
        ("Prólogo", 15), ("Capítulo 1", 54)]
    assert convert._toc_entries(rows("Tabla de contenido", "Capítulo 10: Los viajes y el encuentro con", "otro mundo . 36",
                                     "Capítulo 11: Personas que vienen ......... 43")) == [
        ("Capítulo 10: Los viajes y el encuentro con otro mundo", 36), ("Capítulo 11: Personas que vienen", 43)]
    assert convert._toc_entries(rows("Contenido", "Capítulo 1: Hacer Frente Al Servicio", "Comunitario",
                                     "Epílogo: Que se", "Retire (1)")) == [
        ("Capítulo 1: Hacer Frente Al Servicio Comunitario", None), ("Epílogo: Que se Retire (1)", None)]
    key = convert._key
    assert convert._toc_match(key("Capítulo 1"), key("Capítulo 1: ¡Nee-san regresó!"))
    assert not convert._toc_match(key("Capítulo 1"), key("Capítulo 10: Los viajes"))
    assert convert._toc_match(key("Capítulo 4: Thousand Tricks"), key("Capítulo 4: Mil Trucos"))
    assert not convert._toc_match(key("Capítulo 4: Mil Trucos"), key("Capítulo 5: Mil Trucos"))


def test_quality_report():
    blocks = [("heading", "Cap 1"), ("p", "Texto normal. " * 40), ("p", "y esto sigue en minúscula."),
              ("p", "Visítanos en https://grupo.com"), ("heading", "Cap 2"), ("p", "Corto.")]
    book = convert.Book("Libro", "", b"", [convert.Block(k, 2, h) for k, h in blocks], [0, 4],
                        skipped=[(1, 400, "se omitió como índice del PDF")])
    report = [(i.block, i.kind, i.level) for i in convert.quality_report(book)]
    assert report == [(None, "skipped", "Grave"), (2, "lower", "Revisar"), (3, "link", "Menor"),
                      (4, "short", "Revisar")], report
    # El párrafo anterior ya acaba en punto: unirlos podría mezclar texto desordenado, sin arreglo automático
    assert convert.quality_report(book)[1].edit is None


def test_quality_report_explains_previews_and_fixes():
    """Cada aviso trae problema, solución y vista previa; los que se pueden arreglar solos traen el
    cambio para edit_section, y aplicarlo deja el libro como dice la vista previa."""
    B = convert.Block
    blocks = [B("heading", 1, "Capítulo 1"), B("p", 1, "Ella abrió la puerta y dijo que"),
              B("image", 2, image=b"x"), B("p", 2, "nadie <i>vendría</i> esa noche."),
              B("p", 2, "37"), B("p", 3, "Repetido sin punto final"), B("p", 3, "Otra vez la misma frase larga."),
              B("p", 3, "Otra vez la misma frase larga."), B("p", 3, "La canciÃ³n de la maÃ±ana."),
              B("heading", 4, "Capítulo 3"), B("p", 4, "Texto. " * 60), B("p", 5, "Repetido sin punto final"),
              B("p", 6, "Repetido sin punto final")]
    book = convert.Book("Libro", "", b"", blocks, [0, 9])
    report = convert.quality_report(book)
    kinds = {i.kind: i for i in report}
    assert set(kinds) == {"lower", "number", "repeated", "duplicate", "broken", "gap", "short"}, [i.kind for i in report]
    assert all(i.problem and i.solution and i.level in convert.LEVELS for i in report)
    lower = kinds["lower"]
    assert lower.before == "Ella abrió la puerta y dijo que ¶\nnadie vendría esa noche.", lower.before
    assert lower.after == "Ella abrió la puerta y dijo que nadie vendría esa noche.", lower.after
    assert kinds["broken"].after == "La canción de la mañana." and kinds["broken"].level == "Grave"
    assert kinds["gap"].title == "Falta el capítulo 2" and kinds["gap"].edit is None
    assert kinds["number"].edit == (4, 5, "") and kinds["duplicate"].block == 7
    # Se aplican de atrás adelante, como «Arreglar todos»: los índices de delante no cambian
    for issue in sorted((i for i in report if i.edit), key=lambda i: -i.edit[0]):
        convert.edit_section(book, *issue.edit)
    text = [(b.kind, b.html) for b in book.blocks]
    assert text[:3] == [("heading", "Capítulo 1"), ("p", "Ella abrió la puerta y dijo que nadie <i>vendría</i> esa noche."),
                        ("image", "")], text
    assert ("p", "37") not in text and text.count(("p", "Otra vez la misma frase larga.")) == 1, text
    assert ("p", "La canción de la mañana.") in text and ("p", "Repetido sin punto final") not in text, text
    assert book.candidates == [0, 5], book.candidates


def test_quality_report_finds_untranslated_text():
    """Un párrafo en inglés se avisa; uno español con palabras inglesas sueltas, no."""
    texts = ["The castle stood on the hill above the village, and its towers were dark.",
             "I don't know what you are talking about, he said.",
             "Subaru dijo OK y el juego terminó con un game over para todos.", "—Hola, ¿qué tal estás hoy?"]
    blocks = [convert.Block("heading", 1, "Capítulo 1")] + [convert.Block("p", 1, t) for t in texts]
    book = convert.Book("Libro", "", b"", blocks, [0])
    found = [(i.block, i.level, i.edit) for i in convert.quality_report(book) if i.kind == "english"]
    assert found == [(1, "Revisar", None), (2, "Revisar", None)], found


def test_quality_report_by_book_language():
    """Un libro traducido a otro idioma: las erratas de fansub, calcos y ecos son del español y no saltan; en un
    libro en inglés tampoco «Texto sin traducir». Lo que vale en cualquier idioma (número suelto) sigue."""
    texts = ["The castle stood on the hill above the village, and its towers were dark.", "Haber si llegas pronto.",
             "Lo vi de de lejos.", "42", "El tejado viejo cruje y el tejado nuevo brilla al sol."]
    blocks = [convert.Block("heading", 1, "Capítulo 1")] + [convert.Block("p", 1, t) for t in texts]
    book = convert.Book("Libro", "", b"", blocks, [0])

    def kinds(lang):
        return {i.kind for i in convert.quality_report(book, echoes=True, lang=lang)}

    spanish, english, french = kinds("es"), kinds("en"), kinds("fr")
    assert {"english", "aver", "doubled", "echo", "number"} <= spanish, spanish
    assert "number" in english and not {"english", "aver", "doubled", "echo"} & english, english
    assert "english" in french and not {"aver", "doubled", "echo"} & french, french


def test_quality_report_capital_ignores_footnotes():
    """Psychology of Harem 2: la nota al pie va dentro del párrafo («…más firme[nota: … el ánimo mejora.] y se
    la veía…»). Su punto final seguido de «y se la veía» daba «Minúscula tras un punto»; es otra cosa. Una
    minúscula tras un punto fuera de la nota sigue avisando."""
    note = '<span class="nota"><b>Postura </b> Solo con eso, el ánimo mejora.</span>'
    texts = [f"Su postura era más firme{note} y se la veía segura.",
             f"Era firme{note} y segura. luego se fue."]
    blocks = [convert.Block("heading", 1, "Capítulo 1")] + [convert.Block("p", 1, t) for t in texts]
    book = convert.Book("Libro", "", b"", blocks, [0])
    found = [i.block for i in convert.quality_report(book) if i.kind == "capital"]
    assert found == [2], found


def test_quality_report_fixes_fansub_typos():
    """Signo de apertura que falta, palabra repetida, «haber si» y «¿porque»: avisos con arreglo; lo que
    ya está bien escrito o es «haber» de verdad no salta."""
    texts = ["Qué haces? —preguntó. Ven aquí!", "Lo vi de de lejos.", "Haber si llegas pronto.",
             "¿Porque lo hiciste?", "<i>Qué haces?</i>", "«¿Qué?» Ven aquí!", "Oye, ¿qué haces?", "¡¿En serio?!", "No, no, no.",
             "Mañana va a haber fiesta.", "Tiene que haber sitio si vienes.",
             # Re Zero 2, Nageki 1: puntos suspensivos o incisos con raya dentro de la pregunta
             "«¿El grupo que... los fundó?» Rhuda parpadeó.", "¿Conoces este país —el reino de Lugunica— ahora?",
             "¡Pensé que debían vestirse así… pero me gusta!", "— ¡¿Qué estás haciendo?, me pregunto!",
             # Maryoku: una cita entre comillas dentro de la pregunta no la corta
             "“Oh, idiota. ¿Desde cuándo el concepto de «hacer trampa» se aplica a la Reina Lich?”"]
    blocks = [convert.Block("heading", 1, "Capítulo 1")] + [convert.Block("p", 1, t) for t in texts]
    book = convert.Book("Libro", "", b"", blocks, [0])
    report = [i for i in convert.quality_report(book) if i.kind in ("opening", "doubled", "aver", "porque")]
    assert [(i.block, i.kind, i.level) for i in report] == [
        (1, "opening", "Menor"), (2, "doubled", "Menor"), (3, "aver", "Menor"), (4, "porque", "Menor"),
        (5, "opening", "Menor"), (6, "opening", "Menor")], [(i.block, i.kind) for i in report]
    assert report[0].after.startswith("¿Qué haces?"), report[0].after
    for issue in sorted(report, key=lambda i: -i.edit[0]):
        convert.edit_section(book, *issue.edit)
    assert [b.html for b in book.blocks[1:7]] == ["¿Qué haces? —preguntó. ¡Ven aquí!", "Lo vi de lejos.",
                                                  "A ver si llegas pronto.", "¿Por qué lo hiciste?",
                                                  "<i>¿Qué haces?</i>", "«¿Qué?» ¡Ven aquí!"], [b.html for b in book.blocks]


def test_quality_report_unifies_honorific_variants():
    """«Subaru-kun» y «Subaru kun», «senpai» y «sempai» en el mismo libro: aviso en la forma menos usada, con
    arreglo a la más usada. Lo que solo cambia de mayúscula, una sola forma o «San Pedro» no avisan."""
    texts = ["Subaru-kun llegó tarde.", "Subaru-kun, ven.", "Gracias, Subaru-kun.", "Hola, Subaru kun.",
             "<i>Subaru kun</i> dijo.", "Subarukun es raro.",
             "Senpai, espera.", "Mi senpai es genial.", "Sempai, ¿qué haces?",
             "Fueron a San Pedro.", "La Ciudad San Marcos.", "Emilia-san sonrió.", "Emilia-san asintió.",
             "Onee-san, ayuda.", "La onee-san sonrió.", "Hassan saludó.",
             # Volumen 7 World Project: «Akakidōkutsuno sama» 2 veces y «-sama» 1; con guion es lo habitual
             "Rem sama llegó.", "Gracias, Rem sama.", "Rem-sama sonrió."]
    blocks = [convert.Block("heading", 1, "Capítulo 1")] + [convert.Block("p", 1, t) for t in texts]
    book = convert.Book("Libro", "", b"", blocks, [0])
    report = [i for i in convert.quality_report(book) if i.kind == "honorific"]
    assert [(i.block, i.level) for i in report] == [(4, "Menor"), (5, "Menor"), (6, "Menor"), (9, "Menor"),
                                                    (17, "Menor"), (18, "Menor")], \
        [(i.block, i.title, i.problem) for i in report]
    assert report[0].problem == "El libro escribe «Subaru-kun» 3 veces y «Subaru kun» 2 veces: mejor una sola forma.", \
        report[0].problem
    assert report[-1].problem == "El libro escribe «Rem-sama» 1 vez y «Rem sama» 2 veces: mejor una sola forma.", \
        report[-1].problem
    for issue in sorted(report, key=lambda i: -i.edit[0]):
        convert.edit_section(book, *issue.edit)
    assert [book.blocks[k].html for k in (4, 5, 6, 9, 17, 18)] == [
        "Hola, Subaru-kun.", "<i>Subaru-kun</i> dijo.", "Subaru-kun es raro.", "Senpai, ¿qué haces?",
        "Rem-sama llegó.", "Gracias, Rem-sama."], \
        [b.html for b in book.blocks]


def test_quality_report_spans_point_at_the_problem():
    """Cada aviso dice dónde está el problema en el párrafo tal como lo muestra el editor: el editor lo subraya
    ahí, como Word, y no el párrafo entero. Un «de» que falta subraya las palabras de alrededor."""
    texts = ["Se dio cuenta que <i>era</i> tarde.", "Pasé seis días. para combinar en uno.",
             "y sigue la frase partida.", "Einherjar Project", "Einherjar Project", "Einherjar Project",
             "Habló con Subaru kun ayer.", "Subaru-kun llegó.", "Subaru-kun salió."]
    blocks = [convert.Block("heading", 1, "Capítulo 1")] + [convert.Block("p", 1, t) for t in texts]
    book = convert.Book("Libro", "", b"", blocks, [0])
    spans = {}
    for issue in convert.quality_report(book):
        if issue.block is not None and issue.span:
            shown = convert.TAGS.sub("", convert._line(blocks[issue.block], issue.block))
            spans.setdefault(issue.kind, shown[slice(*issue.span)])
    assert spans["queismo"] == "cuenta que", spans
    assert spans["capital"] == "días. para", spans
    assert spans["lower"] == "y", spans
    assert spans["repeated"] == "Einherjar Project", spans
    assert spans["honorific"] == "Subaru kun", spans  # cambia el espacio: se subrayan sus palabras


def test_quality_report_fixes_grammar_and_typography():
    """Tildes, erratas, gramática, espacios, signos dobles, incisos y cierres que faltan: cada caso con su
    arreglo; lo que se parece pero está bien escrito no salta."""
    cases = [  # (texto, tipo, arreglado)
        ("Lo hizo para mi.", "accent", "Lo hizo para mí."), ("¡Tu! Vuelve aquí.", "accent", "¡Tú! Vuelve aquí."),
        ("¿Si? Claro.", "accent", "¿Sí? Claro."), ("¿Como lo supiste?", "accent", "¿Cómo lo supiste?"),
        ("Tambien vino despues.", "accent", "También vino después."), ("Fui con el, y nada.", "accent", "Fui con él, y nada."),
        ("No tenia la informacion.", "accent", "No tenía la información."), ("Ya no puedo mas.", "accent", "Ya no puedo más."),
        ("Fué ella.", "accent", "Fue ella."), ("Me aferré a el con fuerza.", "accent", "Me aferré a él con fuerza."),
        ("Talvez venga enserio.", "spelling", "Tal vez venga en serio."), ("Tubiera tiempo.", "spelling", "Tuviera tiempo."),
        ("Va ha llover.", "spelling", "Va a llover."), ("Vino ha ver a su madre.", "spelling", "Vino a ver a su madre."),
        ("Eso a sido culpa mía.", "spelling", "Eso ha sido culpa mía."), ("Yo e visto eso.", "spelling", "Yo he visto eso."),
        ("Lo he echo yo. De echo, sí.", "spelling", "Lo he hecho yo. De hecho, sí."),
        ("Otra ves lo mismo.", "spelling", "Otra vez lo mismo."),
        ("Se dio cuenta que era tarde.", "queismo", "Se dio cuenta de que era tarde."),
        ("A pesar que llovía, estaba seguro que vendría.", "queismo", "A pesar de que llovía, estaba seguro de que vendría."),
        ("Pienso de que es tarde.", "dequeismo", "Pienso que es tarde."),
        ("Habían muchos soldados.", "haber", "Había muchos soldados."),
        ("Volvió a el asiento de el carro.", "contraction", "Volvió al asiento del carro."),
        ("Se sintió más peor detrás mío.", "grammar", "Se sintió peor detrás de mí."),
        ("¡ Hay una fila!", "space", "¡Hay una fila!"), ("“Pasa ”, dijo.", "space", "“Pasa”, dijo."),
        ("“¡ Láser!\xa0”", "space", "“¡Láser!”"),  # Maryoku: espacio duro antes del cierre
        ("Lo siento,no puedo.", "nospace", "Lo siento, no puedo."),
        ("Se burló.Shinichi lo miró.", "nospace", "Se burló. Shinichi lo miró."),
        ("“¡Entendido!”dijo Teto.", "nospace", "“¡Entendido!” dijo Teto."),
        ("¿Qué?. Y entonces….", "punct", "¿Qué? Y entonces…"), ("Hola,, adiós.", "punct", "Hola, adiós."),
        ("Lo dejé así. . Además, nada.", "punct", "Lo dejé así. Además, nada."),
        # Mitsuha: coma antes de la raya del narrador, y la frase que sigue tras el inciso
        ("—Sí, — respondió el soldado, —un buque llegó.", "dialog", "—Sí —respondió el soldado—, un buque llegó."),
        ("—Está muerto. —señaló Ferris—. Vamos.", "dialog", "—Está muerto —señaló Ferris—. Vamos."),
        ("¡El comercio va a explotar. ¡Lo sé!", "closing", "¡El comercio va a explotar! ¡Lo sé!"),
        # Aciertos que no deben perderse con las excepciones de abajo
        ("“Si”, decretó Shinichi.", "accent", "“Sí”, decretó Shinichi."),
        ("¿Que estas haciendo?", "accent", "¿Qué estas haciendo?"), ("¿A-Asi que sí?", "accent", "¿A-Así que sí?"),
        ("Cerró. —Un momento, por favor, — dijo, y se fue.", "dialog", "Cerró. —Un momento, por favor —dijo, y se fue."),
    ]
    fine = ["Aun así, vino.", "Lo juzgó estricta, mas no indiferente.", "Acabamos de llegar.", "¿Que no vienes?",
            "Es mi… mi espada.", "Y el... el objeto cayó.", "El Land Dragon rugió.", "¡El, eh, enviado llegó!",
            "¿La ves?", "Ivan sonrió.", "Lo echo de menos.", "No ha lugar.", "Seguro que vendrá.",
            "La cuenta que pagó era alta.", "Me alegro de que vengas.", "Habían muerto muchos.",
            "Subió al rango A el año pasado.", "Son 3,5 kilos.", "Re:Zero es genial.", "¿”amiga” es la palabra?",
            "Bueno...", "Trajo pan, leche, etc., y se fue.", "—¿Qué? —preguntó.", "—Ya veo —dijo—. Vamos.",
            "¿Cómo… No, da igual.", "—¿No sería lo mejor──", "“¿Eh~...”.", "Lo vio el Dr. ramírez.",
            "Un arma simple: un tubo que no parece robusto.",  # Mitsuha: «tubo» es el nombre
            # Mitsuha: la exclamación sigue tras el punto, o el punto es de un número o una sigla
            "¡Oh, wow. Esos son barcos, sin duda!", "Gracias por leer ¡Ahorrando 80.000 oros!", "¡G.I. Samurai ya existe!",
            # Revisión de los 41 libros de muestra: «¿como» de comparación y «¿que» que repite lo dicho
            "¿Como tú? ¿Demonios, quieres decir?", "—¿Como qué?", "¿Como el Sr. Lyp Bariel?",
            "¿Qué? ¿Que el Maestro me está usando?", "¿Que habrá personas que simularán ataques para eso?",
            "¿Y que Mitsuha fundó una empresa?",
            # La palabra citada, «Oh my», frase dicha palabra a palabra, títulos ingleses, risas
            "Las palabras clave fueron “podría” y “si”, nada más.", "Empiezan con “Mi”, como ella.", "Oh mi. ¡Bienvenido!",
            "¡Ese! ¡Es! ¡El! ¡Problema!", "Dónde. Son. El. ¡¿Niños?!", "Lo leí en The Legend of the Heroes hoy.",
            "Hi-ha… Ahi-ha, hi-hi.",
            # Mitsuha: “ y ” sueltas que hacen de raya; Re Zero: signos revueltos
            "La palabra “Torre “ probablemente era mejor.", "“¡El Kalliad “ el buque insignia “ avisa!”, gritó.", "Por mis valiosas piezas” quiero decir, amigas!",
            "—¿¡Qué…¡?", "¿Por qué estarían?¡ campo de prácticas, dices?",
            # Maryoku: la raya cierra el inciso; Re Zero: narración y luego el diálogo
            "Los materiales —huesos, pieles, etc.— que quedaron.", "El guardia contestó, ―tengo entendido que sí.",
            # Mitsuha: el signo de cierre falta antes de la comilla o el paréntesis, no al final
            "“¡Esa sí que es una gran noticia,” celebró Mitsuha dando saltos.", "¡Quizás usan una aleación diferente)."]
    texts = [t for t, _, _ in cases] + fine
    blocks = [convert.Block("heading", 1, "Capítulo 1")] + [convert.Block("p", 1, html.escape(t, quote=False))
                                                            for t in texts]
    book = convert.Book("Libro", "", b"", blocks, [0])
    kinds = {kind for _, kind, _ in cases}
    report = [i for i in convert.quality_report(book) if i.kind in kinds]
    found = sorted((i.block, i.kind) for i in report)
    assert found == [(k + 1, kind) for k, (_, kind, _) in enumerate(cases)], [
        (i.block, i.kind, texts[i.block - 1]) for i in report if (i.block, i.kind) not in
        [(k + 1, kind) for k, (_, kind, _) in enumerate(cases)]] or found
    assert all(i.level == "Menor" and i.after and i.problem and i.solution for i in report)
    for issue in sorted(report, key=lambda i: -i.edit[0]):
        convert.edit_section(book, *issue.edit)
    got = [html.unescape(b.html) for b in book.blocks[1:len(cases) + 1]]
    assert got == [fixed for _, _, fixed in cases], [(g, f) for g, (_, _, f) in zip(got, cases) if g != f]



def _typo_book(texts):
    blocks = [convert.Block("heading", 1, "Capítulo 1")] + [convert.Block("p", 1, html.escape(t, quote=False))
                                                            for t in texts]
    return convert.Book("Libro", "", b"", blocks, [0])


def check_typo_cases(kind, cases, fine, **options):
    """cases [(texto, arreglado)] avisan de kind y su arreglo los deja así; fine no avisa."""
    texts = [t for t, _ in cases] + fine
    book = _typo_book(texts)
    report = [i for i in convert.quality_report(book, **options) if i.kind == kind]
    found = sorted(i.block for i in report)
    assert found == list(range(1, len(cases) + 1)), [(i.block, texts[i.block - 1]) for i in report]
    assert all(i.level == "Menor" and i.after and i.problem and i.solution for i in report)
    for issue in sorted(report, key=lambda i: -i.edit[0]):
        convert.edit_section(book, *issue.edit)
    got = [html.unescape(b.html) for b in book.blocks[1:len(cases) + 1]]
    assert got == [fixed for _, fixed in cases], [(g, f) for g, (_, f) in zip(got, cases) if g != f]


def test_quality_report_leaves_narration_after_aside():
    """Tras «—dijo él.» casi siempre sigue narración, no diálogo (Mitsuha 1: 31 de 31, Re Zero 1: 16 de 16): no se
    pide raya de cierre. El punto antes del inciso sí se corrige."""
    check_typo_cases("dialog", [("—Ven. —dijo él.", "—Ven —dijo él.")],
                     ["—Ven —dijo él. Luego se fue.", "—No te muevas —dijo la chica—. Podía verse en sus ojos.",
                      "—Ven —dijo él.", "—Ven —dijo él—. Vamos ya."])


def test_quality_report_moves_dash_after_exclamation():
    """Making Magic traducido por DeepSeek: «—¡Por favor… Creación!— Y aquello…»: tras ! o ? acaba lo que se dice y
    la narración que sigue abre con su raya pegada: «…Creación! —Y aquello…». Con minúscula es un inciso: no."""
    check_typo_cases("dialog", [("—¡Por favor, Creación!— Y aquello fue una fruta.",
                                 "—¡Por favor, Creación! —Y aquello fue una fruta."),
                                ("—¿Vienes?—Ella no respondió.", "—¿Vienes? —Ella no respondió.")],
                     ["—¡Ven! —dijo él.", "—¡Ven! —Y se fue.", "Gritó: ¡Fuego!—"])


def test_quality_report_finds_calques():
    """Calcos del inglés de frase fija: «en base a», «tomar lugar», «hacer sentido», «en orden de» + infinitivo."""
    check_typo_cases("calque", [
        ("Lo hizo en base a eso.", "Lo hizo con base en eso."),
        ("La boda tomó lugar ayer.", "La boda tuvo lugar ayer."),
        ("Eso no hace sentido.", "Eso no tiene sentido."),
        ("Hace sentido, ¿no?", "Tiene sentido, ¿no?"),
        ("Corrió en orden de llegar antes.", "Corrió para llegar antes.")],
        ["Tomó su lugar en la mesa.", "Fueron en orden de llegada.", "Eso me hace sentir mal."])


def test_quality_report_finds_needless_capitals():
    """Días, meses y tratamientos en mayúscula a mitad de frase; los títulos de fantasía («el Señor Demonio»)
    y los que también son nombres («Abril», «Domingo») no."""
    check_typo_cases("uppercase", [
        ("Llegó el Lunes por la mañana.", "Llegó el lunes por la mañana."),
        ("Nació en Enero, creo.", "Nació en enero, creo."),
        ("Habló con el Señor Pérez ayer.", "Habló con el señor Pérez ayer.")],
        ["Lunes. Otra vez.", "Apareció el Señor Demonio.", "Lo dijo el Rey Demonio.", "Fue a la casa de Abril.",
         "Habló con Domingo ayer.", "Gracias, Señor."])


def test_quality_report_unpaired_and_repeated_marks():
    """Más de tres signos seguidos sobran; comillas o paréntesis sin pareja avisan para revisar, sin arreglo, salvo
    emoticonos, enumeraciones y citas que siguen en el párrafo siguiente."""
    check_typo_cases("punct", [("¡¡¡¡No!!!!", "¡¡¡No!!!"), ("¿¿¿¿Qué????", "¿¿¿Qué???")], ["¡¡¡No!!!", "¿Eh?!"])
    texts = ["Dijo «hola y se fue.", "Lo vio (de lejos y calló.", "Ella dijo «sí».", "¿Qué? :)",
             "a) primero esto.", "Era así: «primero lo uno,", "»y luego lo otro».", "Lo dijo así.)"]
    book = _typo_book(texts)
    report = [i for i in convert.quality_report(book) if i.kind == "unpaired"]
    assert [i.block for i in report] == [1, 2, 8], [(i.block, texts[i.block - 1]) for i in report]
    assert all(i.level == "Revisar" and i.edit is None for i in report)
    assert report[0].span == (5, 6), report[0].span  # el subrayado del editor, en la «
    # Kage, Maryoku: diálogo de varios párrafos que solo cierra el último; párrafo partido por el PDF ("Zenis.”");
    # Nageki: comilla recta que cierra la curva
    fine = ["“Bien. En ese caso será el almuerzo", "Zenis.”", "“Primero lo uno.", "“Y luego lo otro.”",
            '“Lo siento mucho, pero deberíamos empezar."']
    assert not [i for i in convert.quality_report(_typo_book(fine)) if i.kind == "unpaired"]
    # El mismo signo sin cerrar en más de 20 párrafos es el estilo del libro (Maryoku): no se avisa ninguno
    style = ["“Hola, ¿qué tal?", "Ella sonrió."] * 21
    assert not [i for i in convert.quality_report(_typo_book(style)) if i.kind == "unpaired"]
    assert len([i for i in convert.quality_report(_typo_book(style[:40])) if i.kind == "unpaired"]) == 20


def test_quality_report_echoes_only_when_asked():
    """Ecos: la misma palabra cerca, solo si se piden; ni nombres, ni repetición buscada, ni palabras comunes."""
    texts = ["Miró la puerta y luego volvió a mirar la puerta cerrada.", "Espera, espera, ¡espera!",
             "Subaru dijo que Subaru no vendría.", "Estaba cansado y estaba triste."]
    book = _typo_book(texts)
    assert not [i for i in convert.quality_report(book) if i.kind == "echo"]
    report = [i for i in convert.quality_report(book, echoes=True) if i.kind == "echo"]
    assert [(i.block, i.level, i.edit) for i in report] == [(1, "Menor", None)], [(i.block, i.title) for i in report]
    start, end = report[0].span
    assert texts[0][start:end] == "puerta" and start > texts[0].index("puerta"), report[0].span


def test_quality_report_glossary():
    """Glosario de la serie: un nombre que es un término con otras tildes o una letra distinta avisa, con arreglo;
    los cortos («Ram» frente a «Rem»), las formas que también están en el glosario y las de honorífico, no."""
    texts = ["Ayer vi a Emilía en la plaza.", "Luego llegó Reinhart con la espada.", "Mañana vendrá Emilia otra vez.",
             "Hoy vi a Rem y a Ram juntas.", "La saludó Emilia-sama al llegar.", "Vino Felis con Felix."]
    book = _typo_book(texts)
    glossary = ["Emilia", "Reinhard", "Rem", "Felix", "Felis"]
    assert not [i for i in convert.quality_report(book) if i.kind == "glossary"]
    report = [i for i in convert.quality_report(book, glossary=glossary) if i.kind == "glossary"]
    assert [(i.block, i.level) for i in report] == [(1, "Menor"), (2, "Menor")], [(i.block, i.problem) for i in report]
    assert "«Emilia»" in report[0].problem and "«Emilía»" in report[0].problem
    for issue in sorted(report, key=lambda i: -i.edit[0]):
        convert.edit_section(book, *issue.edit)
    assert [b.html for b in book.blocks[1:3]] == ["Ayer vi a Emilia en la plaza.", "Luego llegó Reinhard con la espada."]
    names = convert.proper_names(book)
    assert names["Emilia"] == 2 and "Luego" not in names and "Hoy" not in names, names

def test_quality_report_finds_lowercase_after_period_and_missing_end():
    """Minúscula tras un punto (no tras una abreviatura ni puntos suspensivos) y párrafo sin punto final al que
    sigue otro en mayúscula: avisos sin arreglo automático, porque puede sobrar el punto o faltar texto."""
    texts = ["Lo dejó en la mesa. luego se fue.", "Trajo pan, etc. y se fue... luego volvió.",
             "Necesitamos una opinión de un tercero sin dudarlo", "Hubiera sido genial.",
             "Clic", "Nada más.", "La identidad verdadera de Jack el", "destripador asustaba.", "Era el final del libro",
             # Maryoku y Re Zero: fichas de estado y créditos, una línea por dato
             "TÍTULO: Diosa de la aldea pionera, aventurera de rango C", "NIVEL: 60",
             "Edición de las ilustraciones en español: Kaori - Luilgo", "Corrección: HaruK"]
    blocks = [convert.Block("heading", 1, "Capítulo 1")] + [convert.Block("p", 1, t) for t in texts]
    book = convert.Book("Libro", "", b"", blocks, [0])
    found = [(i.block, i.kind, i.edit) for i in convert.quality_report(book) if i.kind in ("capital", "end")]
    assert found == [(1, "capital", None), (3, "end", None)], found


def test_quality_report_finds_inline_notes():
    """Una nota del traductor entre paréntesis dentro del texto se propone como nota emergente; un paréntesis
    normal no."""
    texts = ["Comió un onigiri (N.T.: bola de arroz) enorme.", "Dijo [NdT: en japonés] adiós.",
             "Ella (Nota del traductor: es su hermana) sonrió.", "(No te preocupes) dijo.", "Era (N.T.: a (b) c) así."]
    blocks = [convert.Block("heading", 1, "Capítulo 1")] + [convert.Block("p", 1, t) for t in texts]
    book = convert.Book("Libro", "", b"", blocks, [0])
    report = [i for i in convert.quality_report(book) if i.kind == "note"]
    assert [i.block for i in report] == [1, 2, 3], [(i.block, i.after) for i in report]
    for issue in sorted(report, key=lambda i: -i.edit[0]):
        convert.edit_section(book, *issue.edit)
    assert [b.html for b in book.blocks[1:4]] == ['Comió un onigiri<span class="nota">bola de arroz</span> enorme.',
                                                  'Dijo<span class="nota">en japonés</span> adiós.',
                                                  'Ella<span class="nota">es su hermana</span> sonrió.'], book.blocks


def test_replace_text():
    """Reemplazar en los párrafos: sin distinguir mayúsculas pero respetando la del principio, palabra completa
    si se pide, sin romper <i>, <b> ni las notas, y solo en los bloques pedidos. Los títulos no se tocan."""
    B = convert.Block
    blocks = [B("heading", 1, "Dijó"), B("p", 1, "Dijó que <i>dijó</i> algo."),
              B("p", 1, convert._inline("Nota: dijó[Nota: dijó]")), B("p", 1, "Contradijó."), B("p", 1, "Nada.")]
    book = convert.Book("Libro", "", b"", blocks, [0])
    edits = convert.replace_text(book, "dijó", "dijo", word=True)
    assert [e[:2] for e in edits] == [(1, 2), (2, 3)], edits
    assert edits[0][2] == "Dijo que <i>dijo</i> algo." and edits[1][2] == "Nota: dijo[Nota: dijo]", edits
    loose = convert.replace_text(book, "dijó", "dijo", blocks=[3, 4])
    assert loose == [(3, 4, "Contradijo.")], loose
    assert convert.replace_text(book, "nota", "aviso") == [(2, 3, "Aviso: dijó[Nota: dijó]")]


def test_unknown_words():
    """Ortografía con el diccionario Hunspell: una errata se avisa con su sugerencia del propio libro; los
    nombres con mayúscula, honoríficos, onomatopeyas, palabras cortas y las del diccionario de la serie, no."""
    texts = ["Subaru dijo que la casa estaba lejos.", "Ella dijó que no. Dijo que sí, kun.",
             "¡Aaaah! La ciudd estaba en la ciudad. Fuuuh.", "El mob atacó otra vez al mob."]
    found = convert.unknown_words(texts, known=["Mob"])
    words = {w: (n, s) for w, n, s in found}
    assert set(words) == {"dijó", "ciudd"}, found
    assert words["dijó"][1][0] == "dijo" and "ciudad" in words["ciudd"][1], found


def test_window_status_line_is_visible():
    """El estado bajo la lista («N capítulos detectados», «Guardado: …») ocupa el hueco libre de su fila:
    con un hueco elástico al lado se quedaba con 0 px y nunca se veía."""
    B = convert.Block
    root, window = visible_window()
    window.show_book(convert.Book("Libro", "", b"", [B("heading", 1, "Capítulo 1"), B("p", 1, "Texto " * 60)], [0]))
    root.update()
    text, width = window.status.text(), window.status.width()
    window.close()
    assert text == "1 capítulo detectado" and width > 200, (text, width)


def test_window_replace_field_keeps_room_in_a_small_window():
    """En una ventana pequeña el campo «Reemplazar por» no se encoge hasta no dejar escribir."""
    root, window = visible_window()
    window.show_book(convert.Book("Libro", "", b"", [convert.Block("p", 1, "Ella dijó que sí.")], []))
    root.geometry("900x600+0+0")
    window.find()
    root.update()
    width = window.replace_entry.width()
    window.close()
    assert width >= 150, width


def test_window_undo_works_with_focus_on_a_button():
    """Ctrl+Z deshace con el foco en un botón («Editar texto…», «Todos»), no solo en la lista: el estado dice
    «Ctrl+Z para deshacer» tras editar o reemplazar, y el foco se queda en el botón pulsado."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QPushButton

    import app

    B = convert.Block
    root, window = visible_window()
    window.show_book(convert.Book("Libro", "", b"", [B("heading", 1, "Capítulo 1"), B("p", 1, "Uno."),
                                                     B("heading", 2, "Capítulo 2"), B("p", 2, "Dos.")], [0, 2]))
    item = window.tree.get_children()[1]
    window.tree.selection_set(item)
    window.tree.setFocus()
    QTest.keyClick(window.tree, Qt.Key.Key_S)  # capítulo -> subtítulo
    changed = window.tree.set(item, "inc")
    button = next(b for b in window.root.findChildren(QPushButton) if b.text() == "Editar texto…")
    window.root.activateWindow()
    button.setFocus()
    root.update()
    QTest.keyClick(button, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    root.update()
    undone = window.tree.set(item, "inc")
    window.close()
    assert (changed, undone) == (app.OFF, app.ON), (changed, undone)


def test_window_ignore_warning():
    """«Ignorar» quita el aviso del informe y del botón de la barra; en «Ignorados» se puede recuperar."""
    B = convert.Block
    root, window = visible_window()
    window.show_book(convert.Book("Libro", "", b"", [B("heading", 1, "Capítulo 1"), B("p", 1, "Texto " * 60),
                                                     B("p", 2, "12")], [0]))
    before = window.quality_btn.text()
    window.quality()
    root.update()
    parts = root.dialogs()[-1].parts
    parts["ignore"].click()
    root.update()
    ignored = (window.quality_btn.text(), window.ignored, parts["table"].get_children())
    parts["show"].set("Ignorados")
    parts["fill"]()
    listed = (parts["table"].get_children(), parts["ignore"].text())
    parts["ignore"].click()
    root.update()
    after = (window.quality_btn.text(), window.ignored)
    window.close()
    assert before == "▲ 1 por revisar" and ignored == ("✓ Sin avisos", [["number", "12"]], ()), (before, ignored)
    assert listed == (("0",), "No ignorar") and after == ("▲ 1 por revisar", []), (listed, after)


def test_window_find_and_replace():
    """La barra de búsqueda reemplaza en el resultado elegido o en todos, Ctrl+Z lo deshace, y «Guardar como
    regla» lo pasa a los reemplazos de la serie. Solo palabras completas, como las reglas: «Tino» por
    «Rhuda» en todos dejaba «desRhuda» en «destino» (Nageki 1)."""
    B = convert.Block
    blocks = [B("heading", 1, "Capítulo 1"), B("p", 1, "Ella dijó que sí."), B("p", 2, "Dijó que no."),
              B("p", 3, "Tino vio su destino.")]
    root, window = visible_window()
    window.show_book(convert.Book("Libro", "", b"", blocks, [0]))
    window.find()
    window.find_entry.setText("dijó")
    window.search_book()
    window.replace_entry.setText("dijo")
    window.find_results.setCurrentRow(0)
    window.replace_found()
    one = [b.html for b in window.book.blocks]
    window.replace_found(every=True)
    every = [b.html for b in window.book.blocks]
    window.undo()
    undone = [b.html for b in window.book.blocks]
    window.save_rule()
    rules = window.replacements
    window.find_entry.setText("Tino")
    window.search_book()
    window.replace_entry.setText("Rhuda")
    window.replace_found(every=True)
    name = window.book.blocks[3].html
    window.close()
    assert one == ["Capítulo 1", "Ella dijo que sí.", "Dijó que no.", "Tino vio su destino."], one
    assert every == ["Capítulo 1", "Ella dijo que sí.", "Dijo que no.", "Tino vio su destino."], every
    assert undone == one and rules == [["dijó", "dijo"]], (undone, rules)
    assert name == "Rhuda vio su destino.", name


def test_window_spelling():
    """Ortografía: la revisión corre en el proceso de trabajo; «Cambiar todas» corrige el libro, «Añadir al
    diccionario» la guarda para la serie, y el editor subraya las que quedan."""
    import time

    B = convert.Block
    blocks = [B("heading", 1, "Capítulo 1"), B("p", 1, "Ella dijó que la ciudd estaba lejos."),
              B("p", 1, "Él dijó que la ciudad era grande y el golem también.")]
    root, window = visible_window()
    window.show_book(convert.Book("Libro", "", b"", blocks, [0]))
    window.spelling()
    deadline = time.time() + 120
    while window.done and time.time() < deadline:
        root.update()
        time.sleep(0.05)
    parts = root.dialogs()[-1].parts
    words = [parts["table"].set(i, "word") for i in parts["table"].get_children()]
    first = (parts["fix"].currentText(), parts["places"].count())
    parts["change"].click()  # «dijó», la más repetida, por su sugerencia «dijo»
    root.update()
    fixed = [b.html for b in window.book.blocks]
    rest = [parts["table"].set(i, "word") for i in parts["table"].get_children()]
    parts["table"].selection_set(str(rest.index("golem")))
    parts["add"].click()
    root.update()
    window.tree.selection_set("0")
    window.pdf_doc = None  # libro sin PDF: el panel del editor queda en blanco
    editor = window.edit_text()
    root.update()
    marked = editor.sheet.misspelled()
    editor.win.done(0)
    state = (window.words, sorted(window.unknown))
    window.close()
    assert words[0] == "dijó" and set(words) == {"dijó", "ciudd", "golem"}, words
    assert first == ("dijo", 2), first
    assert fixed[1:] == ["Ella dijo que la ciudd estaba lejos.", "Él dijo que la ciudad era grande y el golem también."], fixed
    assert state == (["golem"], ["ciudd"]) and marked == ["ciudd"], (state, marked)


def test_window_add_page_change_image_and_reviewed():
    """«Añadir página» crea un capítulo antes del primero; «Cambiar ilustración» cambia la imagen de la fila;
    R marca revisado y salta al siguiente capítulo. Todo vuelve al reabrir el PDF."""
    import app

    pdf = TMP / "Pagina - Volumen 1.pdf"
    make_novel().replace(pdf)
    picture = TMP / "nueva.png"
    pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 30, 40), False).save(picture)
    root, window = visible_window()
    window.pdf = pdf
    book = convert.analyze(pdf)
    window.show_book(book)
    chapters = [i for i in window.tree.get_children() if window.tree.set(i, "inc") == app.ON]
    window.add_page()
    root.update()
    page = root.dialogs()[-1].parts
    page["title"].setText("Créditos del grupo")
    page["body"].setPlainText("Traducción: Grupo.\n\nGracias por leer.")
    page["save"]()
    root.update()
    first = chapters and int(chapters[0])
    added = (window.tree.set(str(first), "inc"), window.tree.set(str(first), "title"),
             [b.html for b in window.book.blocks[first:first + 3]])
    heads = [i for i in window.tree.get_children() if window.tree.set(i, "inc") == app.ON]
    window.tree.selection_set(heads[0])
    window.toggle_reviewed()
    jumped = window.tree.selection()
    header = window.tree.headerItem().text(window.tree.columns.index("ok"))
    # Una ilustración sintética al final para cambiarla
    original = app.filedialog.askopenfilename
    app.filedialog.askopenfilename = lambda **kwargs: str(picture)
    try:
        window.book.blocks.append(convert.Block("image", 1, image=JPEG))
        window.fill_tree(window.rows())
        image = str(len(window.book.blocks) - 1)
        window.tree.selection_set(image)
        window.confirm_image = lambda k, data, batch=False, parent=None: "replace"  # el antes y después: Reemplazar
        window.change_image()
    finally:
        app.filedialog.askopenfilename = original
    changed = pymupdf.Pixmap(window.book.blocks[int(image)].image).width
    window.book.blocks.pop()  # la ilustración no es del PDF: al reabrir no estaría
    window.edits.pop()
    window.words, window.ignored = ["golem"], [["number", "12"]]
    window.save_review()
    window.close()
    root, again = visible_window()
    again.pdf = pdf
    again.show_book(convert.analyze(pdf))
    reopened = (again.tree.set(str(first), "inc"), again.tree.set(str(first), "title"), again.tree.set(heads[0], "ok"),
                again.words, again.ignored)
    again.close()
    assert added == (app.ON, "Créditos del grupo", ["Créditos del grupo", "Traducción: Grupo.", "Gracias por leer."]), added
    assert jumped == (heads[1],) and header == f"✓ 1/{len(heads)}", (jumped, header)
    assert changed == 30, changed
    assert reopened == (app.ON, "Créditos del grupo", "✓", ["golem"], [["number", "12"]]), reopened


def test_window_change_double_image_for_single():
    """Una ilustración doble en «Dividida» cambiada por una imagen vertical vuelve a «Ilustración»: si no, el
    EPUB la partiría por la mitad."""
    import app

    wide = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 10), False).tobytes("jpg")
    picture = TMP / "vertical.png"
    pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 30, 40), False).save(picture)
    root, window = visible_window()
    window.show_book(convert.Book("Libro", "", b"", [convert.Block("heading", 1, "Capítulo 1"),
                                                     convert.Block("image", 1, image=wide)], [0]))
    window.tree.selection_set("1")
    window.shortcut(app.SPLIT)
    split = window.tree.set("1", "inc")
    original = app.filedialog.askopenfilename
    app.filedialog.askopenfilename = lambda **kwargs: str(picture)
    window.confirm_image = lambda k, data, batch=False, parent=None: "replace"  # el antes y después: Reemplazar
    try:
        window.change_image()
    finally:
        app.filedialog.askopenfilename = original
    kind = window.tree.set("1", "inc")
    window.close()
    assert (split, kind) == (app.SPLIT, app.IMG), (split, kind)


def test_window_replace_in_book_without_titles():
    """Un libro sin títulos ni ilustraciones deja la lista vacía: reemplazar y deshacer funcionan igual."""
    root, window = visible_window()
    window.show_book(convert.Book("Libro", "", b"", [convert.Block("p", 1, "Ella dijó que sí.")], []))
    window.find()
    window.find_entry.setText("dijó")
    window.search_book()
    window.replace_entry.setText("dijo")
    window.replace_found(every=True)
    fixed = window.book.blocks[0].html
    window.undo()
    undone = window.book.blocks[0].html
    window.close()
    assert (fixed, undone) == ("Ella dijo que sí.", "Ella dijó que sí."), (fixed, undone)


def test_window_undo_added_page_whose_row_did_not_exist():
    """Ctrl+Z tras «Añadir página» al final, o en un libro sin títulos, quita la página: la fila que estaba
    elegida (la de la página nueva) no existía antes y deshacer no puede volver a elegirla."""
    root, window = visible_window()
    results = []
    for blocks, candidates in (([convert.Block("heading", 1, "Capítulo 1"), convert.Block("p", 1, "Texto.")], [0]),
                               ([convert.Block("p", 1, "Texto sin títulos.")], [])):
        window.show_book(convert.Book("Libro", "", b"", blocks, candidates))
        before = [(b.kind, b.html) for b in window.book.blocks]
        window.insert_page("Nota del traductor", ["Gracias por leer."], first=False)
        root.update()
        window.undo()
        root.update()
        results.append(([(b.kind, b.html) for b in window.book.blocks] == before, window.tree.get_children()))
    window.close()
    assert results == [(True, ("0",)), (True, ())], results


def test_window_lists_scene_breaks():
    """Los separadores de escena del texto salen en la lista («Separador», con su símbolo y página) para
    encontrarlos. No son títulos: las teclas de tipo, renombrar, las mayúsculas y el plan del EPUB no los
    tocan, y «Editar texto…» sobre uno abre la sección que lo contiene."""
    import app

    root, window = visible_window()
    blocks = [convert.Block("heading", 1, "CAPÍTULO 1"), convert.Block("p", 1, "Texto."),
              convert.Block("break", 2, "* * *"), convert.Block("p", 2, "Más texto."),
              convert.Block("break", 3, "", image=b"x"), convert.Block("heading", 4, "CAPÍTULO 2"),
              convert.Block("p", 4, "Fin.")]
    doc = pymupdf.open()
    for n in range(1, 5):
        add_page(doc, n, [(72, 100, f"Pagina {n}", 11, "helv")])
    window.pdf = TMP / f"separadores-{time_ns()}.pdf"
    doc.save(window.pdf)
    window.show_book(convert.Book("Libro", "", b"", blocks, [0, 5]))
    root.update()
    rows = [(i, window.tree.set(i, "inc"), window.tree.set(i, "title")) for i in window.tree.get_children()]
    status = window.status.text()
    window.tree.selection_set("2")
    root.update()
    window.shortcut(app.ON)
    window.edit_title("2")
    after_keys = window.tree.set("2", "inc"), window.tree.set("2", "title")
    window.fix_case()
    steps = []
    for start, delta in (("0", 1), ("2", 1), ("2", -1), ("5", -1)):  # ▶ y ◀ del EPUB se saltan los separadores
        window.tree.selection_set(start)
        root.update()
        window.epub_pages = 0
        window.epub_step(delta)
        steps.append(window.tree.selection()[0])
    window.tree.selection_set("2")
    chapters, removed, breaks = app._plan(window.book, window.rows())
    editor = window.edit_text()
    section = (editor.item, *editor.current) if editor else None
    if editor:
        editor.win.done(0)
    window.close()
    assert rows == [("0", "Capítulo", "CAPÍTULO 1"), ("2", "Separador", "* * * · página 2"),
                    ("4", "Separador", "Adorno · página 3"), ("5", "Capítulo", "CAPÍTULO 2")], rows
    assert "subtítulo" not in status and "2 separadores" in status, status
    assert after_keys == ("Separador", "* * * · página 2"), after_keys
    assert [k for k, _ in chapters] == [0, 5] and not removed and not breaks, (chapters, removed, breaks)
    assert section == ("2", 0, 5), section  # la sección del capítulo, abierta en el separador
    assert steps == ["5", "5", "0", "0"], steps


def test_window_starts_maximized():
    """La ventana se abre maximizada, no en una ventana más pequeña que la pantalla."""
    import app
    from PySide6.QtCore import Qt

    app.SETTINGS = TMP / f"ajustes-{time_ns()}.json"
    root = app.Window()
    window = app.App(root)
    root.show()
    maximized = bool(root.windowState() & Qt.WindowState.WindowMaximized)
    window.close()
    assert maximized


def test_window_dialogs_fit_the_screen():
    """Con el zoom de Windows a 125 % en 1366x768 quedan 1093x582: el informe de calidad (1060x600) salía
    más alto que la pantalla, con sus botones fuera. Cada diálogo se encoge y se coloca dentro."""
    root, window = visible_window()
    window.show_book(convert.Book("Libro", "", b"", [convert.Block("heading", 1, "Capítulo 1"),
                                                     convert.Block("p", 1, "Texto.")], [0]))
    window.quality()
    root.update()
    report = root.dialogs()[-1]
    narrowest = report.minimumSizeHint().width() + report.frameGeometry().width() - report.width()
    tall, _ = window.dialog("Alto")
    tall.resize(600, 2000)
    tall.move(50, 300)
    tall.show()
    root.update()
    screen = tall.screen().availableGeometry()
    inside = screen.contains(tall.frameGeometry())
    sizes = (tall.frameGeometry().getRect(), screen.getRect())
    window.close()
    assert inside, sizes
    assert narrowest <= 1093, narrowest


def test_window_tools_close_when_another_pdf_opens_and_spelling_waits():
    """Ortografía, «Añadir página», Versiones, el informe de calidad, Ilustraciones y la portada se cierran al abrir
    otro PDF (si no, cambiarían el libro nuevo: «Borrar» de Versiones escribía las del libro anterior en el nuevo);
    con un trabajo en curso, «Ortografía…» lo dice en vez de no hacer nada."""
    import time

    pdf = TMP / "Otra - Volumen 1.pdf"
    make_novel().replace(pdf)
    root, window = visible_window()
    window.show_book(convert.Book("Libro", "", b"", [convert.Block("heading", 1, "Capítulo 1"),
                                                     convert.Block("p", 1, "Ella dijó que sí."),
                                                     convert.Block("image", 1, image=jpeg(40, 60))], [0]))
    window.done = lambda result: None  # un trabajo en curso
    window.spelling()
    busy = window.status.text()
    window.done = None
    window.spelling_dialog([("dijó", 1, ["dijo"])])
    window.add_page()
    window.pdf = pdf  # Versiones pide un PDF
    window.versions()
    window.quality()
    window.images_dialog()
    window.choose_cover()
    root.update()
    opened = len(root.dialogs())
    window.open_path(str(pdf))
    root.update()
    left = len(root.dialogs())
    deadline = time.time() + 60
    while window.done and time.time() < deadline:
        root.update()
        time.sleep(0.05)
    window.close()
    assert busy == "Espera a que termine el trabajo en curso", busy
    assert (opened, left) == (6, 0), (opened, left)


def test_editor_page_buttons_below_the_page():
    """Los botones de hojear el PDF del editor van debajo de la página, no encima tapándola."""
    import qtk

    root, window = visible_window()
    window.pdf = TMP / "Hojear - Volumen 1.pdf"
    make_novel().replace(window.pdf)
    window.show_book(convert.analyze(window.pdf))
    window.tree.selection_set(first_title(window))
    editor = window.edit_text()
    editor.win.resize(1250, 700)
    qtk.pump()
    page, label = editor.scroll.geometry(), editor.page_label.geometry()
    editor.win.done(0)
    window.close()
    assert label.top() > page.bottom(), (page, label)


def test_replay_image_and_page_at_start():
    """La revisión repite una ilustración cambiada (o deja la del PDF si el archivo ya no está) y una página
    añadida en el bloque 0."""
    import app

    picture = TMP / "repetida.png"
    pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 20, 10), False).save(picture)
    blocks = [convert.Block("heading", 1, "Capítulo 1"), convert.Block("image", 1, image=JPEG),
              convert.Block("image", 2, image=JPEG)]
    book = convert.Book("Libro", "", b"", blocks, [0])
    stored = app._store_image(picture)  # la ventana guarda así cada ilustración cambiada
    state = {"edits": [["image", 1, stored], ["image", 2, str(TMP / "no-existe.png")], [0, 0, "# Créditos\n\nGracias."]]}
    done = app._replay(book, state)
    assert done == state["edits"], done
    assert [b.kind for b in book.blocks] == ["heading", "p", "heading", "image", "image"], book.blocks
    assert pymupdf.Pixmap(book.blocks[3].image).width == 20 and book.blocks[4].image == JPEG, book.blocks
    assert book.candidates == [2], book.candidates


def test_reviews_of_others_do_not_open_network_paths():
    """Un .lnepub.json ajeno junto al PDF (heredado de otro tomo de la serie) puede traer rutas de red: con solo
    mirarlas, Windows se conecta y entrega el hash de la contraseña. La fuente propia solo vale local, absoluta y
    .ttf/.otf, y una ilustración cambiada solo desde la carpeta de ilustraciones del programa; ni se mira el disco."""
    import pathlib

    import app

    unc, looked = r"\\192.168.1.50\s\x.ttf", []
    assert app._font_file(r"C:\Fuentes\Mia.TTF") and app._font_file("C:/Fuentes/mia.otf")
    assert not [p for p in (unc, "//192.168.1.50/s/x.ttf", r"\\?\UNC\srv\s\x.ttf", "mia.ttf", r"C:\x\mia.exe", "")
                if app._font_file(p)]
    assert app._stored_image(app.SETTINGS.parent / "ilustraciones" / "a.png")
    assert not app._stored_image(app.SETTINGS.parent / "ilustraciones" / ".." / "a.png")
    assert not app._stored_image(r"\\192.168.1.50\s\x.png") and not app._stored_image(TMP / "a.png")
    is_file, read_bytes = pathlib.Path.is_file, pathlib.Path.read_bytes
    pathlib.Path.is_file = lambda self: looked.append(str(self)) or is_file(self)
    pathlib.Path.read_bytes = lambda self: looked.append(str(self)) or read_bytes(self)
    try:
        written = app._write_options({"font": "x.ttf", "font_file": unc})
        book = convert.Book("Libro", "", b"", [convert.Block("image", 1, image=JPEG)], [])
        done = app._replay(book, {"edits": [["image", 0, r"\\192.168.1.50\s\x.png"]]})
        root, window = visible_window()
        window.apply_state({"font": "x.ttf", "font_file": unc})
        kept = window.font_file_var.get(), window.font_var.get()
        window.close()
    finally:
        pathlib.Path.is_file, pathlib.Path.read_bytes = is_file, read_bytes
    assert written["font"] == "" and book.blocks[0].image == JPEG and done, (written["font"], done)
    assert kept == ("", "Del lector"), kept
    assert not [p for p in looked if "192.168" in p], looked



def test_window_echoes_and_glossary_in_quality_report():
    """«Opciones del informe…» → «Buscar ecos» añade los ecos al informe abierto; el glosario guardado va en la
    revisión y el informe avisa de la variante; «Añadir nombres de este tomo» propone los frecuentes."""
    from PySide6.QtWidgets import QPushButton
    B = convert.Block
    blocks = [B("heading", 1, "Capítulo 1"), B("p", 1, "Miró la puerta y luego volvió a mirar la puerta cerrada."),
              B("p", 1, "Ayer vi a Emilía en la plaza.")] + [B("p", 2, "Luego vino Emilia a verla.")] * 5
    root, window = visible_window()
    window.show_book(convert.Book("Libro", "", b"", blocks, [0]))
    window.quality()
    root.update()
    parts = root.dialogs()[-1].parts
    titles = lambda: [parts["table"].set(i, "title") for i in parts["table"].get_children()]
    without = titles()
    parts["echoes"].click()  # «Opciones del informe…»
    root.update()
    dialog = window.options_window
    dialog.parts["widgets"]["echoes"].setChecked(True)
    dialog.parts["accept"]()
    root.update()
    with_echoes = titles()
    window.edit_glossary()
    root.update()
    glossary_win, text, save = window.glossary_window
    [add] = [b for b in glossary_win.findChildren(QPushButton) if b.text() == "Añadir nombres de este tomo"]
    add.click()
    proposed = text.toPlainText()
    save()
    root.update()
    state = window.review_state()
    echoes_saved = window.settings.get("echoes")
    kinds = [i.kind for i in window.quality_report()]
    window.close()
    assert "Palabra repetida cerca" not in without and "Palabra repetida cerca" in with_echoes, (without, with_echoes)
    assert proposed == "Emilia  # 5 veces", proposed
    # Los ecos son de la aplicación (Opciones → Informe de calidad), no del libro
    assert state["glossary"] == ["Emilia"] and "echoes" not in state and echoes_saved is True, state
    assert "glossary" in kinds and "echo" in kinds, kinds


def test_quality_score_and_export():
    """La nota resta 10, 3 o 1 por aviso según su gravedad, cada 10 000 palabras, entre 0 y 100. El informe se
    exporta a .csv para Excel («;», UTF-8 con BOM) y a .html que escapa el texto del libro."""
    issue = lambda level, text: convert.Issue("x", 1, 7, level, "Aviso <b>", "Problema", "Solución", text)
    report = [issue("Grave", "Tom & <Jerry>"), issue("Revisar", "a"), issue("Menor", "«b»")]
    assert convert.quality_score(report, 10000) == 86
    assert convert.quality_score(report, 1000) == 0 and convert.quality_score([], 0) == 100
    csv_path, html_path = TMP / "informe.csv", TMP / "informe.html"
    convert.export_report(report, csv_path, "Libro", 86)
    convert.export_report(report, html_path, "Libro & Co", 86)
    raw = csv_path.read_bytes()
    page = html_path.read_text(encoding="utf-8")
    assert raw.startswith(b"\xef\xbb\xbf") and raw.decode("utf-8-sig").splitlines()[1] == \
        "Grave;7;Aviso <b>;Problema;Solución;Tom & <Jerry>", raw[:200]
    assert "Nota 86/100" in page and "Tom &amp; &lt;Jerry&gt;" in page and "Libro &amp; Co" in page, page[:400]
    assert "<Jerry>" not in page


def test_window_quality_dialog_score_and_export():
    """El informe dice la nota del libro y «Exportar…» guarda el .html con los avisos no ignorados."""
    import app

    B = convert.Block
    blocks = [B("heading", 1, "Capítulo 1"), B("p", 1, "Ella abrió la puerta y dijo que"), B("p", 2, "nadie vendría."),
              B("p", 2, "12")]
    root, window = visible_window()
    window.show_book(convert.Book("Libro", "", b"", blocks, [0]))
    window.quality()
    root.update()
    parts = root.dialogs()[-1].parts
    summary = parts["summary"].text()
    out = TMP / "exportado.html"
    asked = []
    original = app.filedialog.asksaveasfilename
    app.filedialog.asksaveasfilename = lambda **options: asked.append(options) or str(out)
    try:
        [export] = [b for b in root.dialogs()[-1].findChildren(app.QPushButton) if b.text() == "Exportar…"]
        export.click()
    finally:
        app.filedialog.asksaveasfilename = original
    window.close()
    page = out.read_text(encoding="utf-8")
    assert summary.startswith("Nota 0/100 · "), summary  # 2 avisos «Revisar» en 11 palabras
    assert "Párrafo partido" in page and "Número suelto" in page, page[:300]
    assert asked[0]["initialfile"].endswith("informe de calidad.html"), asked


def test_window_quality_report_says_where_and_opens_the_editor_on_the_problem():
    """El informe dice el capítulo, el subtítulo y el número de párrafo de cada aviso; «Abrir en el editor» (o doble
    clic) abre ese capítulo con el trozo exacto del aviso elegido y su barra, no solo el cursor en el párrafo.
    Maryoku 1: «¿Viene alguien, Señora Bruja?» abría el editor sin marcar «Señora»."""
    import app

    B = convert.Block
    blocks = [B("heading", 1, "Capítulo 0: El discípulo regresa"), B("p", 1, "Primero llegó la carta."),
              B("heading", 2, "Lado de Teto"), B("p", 2, "Ella miró."), B("p", 2, html.escape("“¿Viene alguien, Señora Bruja?”, me preguntó Teto.")),
              B("heading", 3, "Capítulo 1: Otro día"), B("p", 3, "Nada más.")]
    doc = pymupdf.open()
    for n in range(3):  # el editor muestra la página del PDF de cada párrafo
        add_page(doc, n + 1, [])
    doc.save(TMP / "donde.pdf")
    root, window = visible_window()
    window.pdf = TMP / "donde.pdf"
    window.show_book(convert.Book("Libro", "", b"", blocks, [0, 5]))
    window.quality()
    root.update()
    dialog = root.dialogs()[-1]
    parts = dialog.parts
    table = parts["table"]
    row = next(i for i in table.get_children() if table.set(i, "title") == "Mayúscula innecesaria")
    table.selection_set(row)
    root.update()
    where, place = table.set(row, "where"), parts["place"].text()
    parts["edit"].click()
    root.update()
    editor = window.text_editor
    chosen = editor.sheet.textCursor().selectedText()
    bar = editor.issue_frame.isVisible(), editor.issue_title.text()
    title = editor.win.windowTitle()
    editor.win.done(0)
    window.close()
    assert where == "Capítulo 0 · ¶3", where
    assert place == "Capítulo 0: El discípulo regresa › Lado de Teto · párrafo 3 de 3 · pág. 2", place
    assert chosen == "Señora", chosen
    assert bar == (True, "● Menor · Mayúscula innecesaria"), bar
    assert title.startswith("Texto · Capítulo 0"), title

def test_window_quality_report_previews_fixes_and_undo():
    """El informe muestra problema, solución y vista previa; «Arreglar todos» quita los números de
    página de una vez, Ctrl+Z los devuelve, y el botón de la barra inferior cuenta los avisos."""
    B = convert.Block
    blocks = [B("heading", 1, "Capítulo 1"), B("p", 1, "Ella abrió la puerta y dijo que"), B("p", 2, "nadie vendría."),
              B("p", 2, "12"), B("p", 3, "Texto. " * 60), B("p", 3, "13")]
    root, window = visible_window()
    window.show_book(convert.Book("Libro", "", b"", blocks, [0]))
    button = window.quality_btn.text()
    window.quality()
    root.update()
    parts = root.dialogs()[-1].parts
    table = parts["table"]
    before, after = parts["previews"]["before"], parts["previews"]["after"]
    listed = [table.set(i, "title") for i in table.get_children()]
    shown = (before.get("1.0", "end").strip(), after.get("1.0", "end").strip(),
             [before.get(*r) for r in zip(*[iter(before.tag_ranges("old"))] * 2)])
    table.selection_set("1")  # el primer número de página
    root.update()
    fix_all = parts["all"]
    label = fix_all.text()
    fix_all.click()
    root.update()
    fixed = ([b.html for b in window.book.blocks], [table.set(i, "title") for i in table.get_children()],
             window.quality_btn.text())
    window.undo()
    undone = [b.html for b in window.book.blocks]
    window.close()
    assert button == "▲ 3 por revisar", button
    assert listed == ["Párrafo partido", "Número suelto", "Número suelto"], listed
    assert shown == ("Ella abrió la puerta y dijo que ¶\nnadie vendría.",
                     "Ella abrió la puerta y dijo que nadie vendría.", ["¶\n"]), shown
    assert label == "Arreglar todos los de este tipo (2)", label
    assert "12" not in fixed[0] and "13" not in fixed[0] and fixed[1] == ["Párrafo partido"], fixed
    assert fixed[2] == "▲ 1 por revisar", fixed
    assert undone == [b.html for b in blocks], undone


def test_regex_replacements_and_counts():
    book = convert.Book("Libro", "", b"", [convert.Block("heading", 1, "Cap 1"),
                                           convert.Block("p", 1, "Onii-chan y Oniichan y Onii chan.")], [0])
    rules = [("re:Onii[- ]?chan", "Onii-chan"), ("re:(", "x"), ("Cap", "Capítulo")]
    counts = convert.count_matches(book, rules)
    assert counts[0] == ("re:Onii[- ]?chan", 3) and "no válida" in counts[1][1] and counts[2] == ("Cap", 1), counts
    out = TMP / "regex.epub"
    convert.write_epub(book, [(0, "Cap 1")], out, replacements=rules)  # la regla mala se salta
    text = epub_file(out, "OEBPS/sec001.xhtml")
    assert "<p>Onii-chan y Onii-chan y Onii-chan.</p>" in text and "Capítulo 1" in text, text


def test_epub_extras_pages_classes_fonts_metadata():
    blocks = [convert.Block("heading", 1, "Cap 1"), convert.Block("p", 1, "Uno."),
              convert.Block("p", 1, "Querida Ana:", align="center"), convert.Block("p", 2, "【Habilidad adquirida】"),
              convert.Block("heading", 2, "Parte 1"), convert.Block("p", 3, "Tres.")]
    book = convert.Book("Libro", "Tappei Nagatsuki", b"", blocks, [0], illustrator="Otsuka", proofreader="HaruK",
                        subjects="Fantasía, Isekai", date="2014", original_title="Re:Zero")
    out = TMP / "extras.epub"
    convert.write_epub(book, [(0, "Cap 1")], out, breaks=[4], font="Literata", reading=True)
    sec, nav = epub_file(out, "OEBPS/sec001.xhtml", marks=True), epub_file(out, "OEBPS/nav.xhtml")
    opf, css = epub_file(out, "OEBPS/content.opf"), epub_file(out, "OEBPS/style.css")
    assert '<p class="center">Querida Ana:</p>' in sec and '<p class="status">' in sec, sec
    assert "<h2" not in sec and "Parte 1" not in sec, sec  # el apartado va como separador
    assert 'id="pg1"' in sec and 'id="pg2"' in sec and 'id="pg3"' in sec, sec
    assert 'epub:type="page-list"' in nav and 'href="sec001.xhtml#pg3"' in nav, nav
    assert ">ill</meta>" in opf and ">pfr</meta>" in opf and "Nagatsuki, Tappei" in opf, opf
    assert "<dc:subject>Isekai</dc:subject>" in opf and "<dc:date>2014</dc:date>" in opf and "Re:Zero" in opf, opf
    assert '@font-face' in css and 'font-family: "Literata"' in css, css
    with zipfile.ZipFile(out) as z:
        assert sum(n.endswith(".ttf") for n in z.namelist()) == 4
    assert "min</span>" in epub_file(out, "OEBPS/contents.xhtml")
    assert convert.check_epub(out) == []
    old = TMP / "epub2.epub"
    convert.write_epub(book, [(0, "Cap 1")], old, epub2=True)
    opf2 = epub_file(old, "OEBPS/content.opf")
    assert 'version="2.0"' in opf2 and "refines" not in opf2 and 'opf:role="aut"' in opf2, opf2
    assert 'properties=' not in opf2 and "page-list" not in epub_file(old, "OEBPS/nav.xhtml")
    ET.fromstring(opf2.encode())


def test_omnibus_joins_volumes():
    import app

    folder = series_folder()
    out = folder / "recopilatorio.epub"
    pdfs = [str(folder / f"Mi Serie - Volumen 0{n}.pdf") for n in (1, 2)]
    problems = app._omnibus(pdfs, str(out), {"shrink": False})
    nav = epub_file(out, "OEBPS/nav.xhtml")
    titles = re.findall(r'<li><a href="sec\d+\.xhtml">([^<]+)</a>', nav)
    assert problems == [], problems
    assert titles[0] == "Mi Serie - Volumen 1" and "Mi Serie - Volumen 2" in titles, titles
    assert titles.count("Capitulo 1: Inicio") == 2, titles
    assert "Mi Serie - Volúmenes 1-2" in epub_file(out, "OEBPS/content.opf")


def test_worker_error_is_logged():
    import app

    events = multiprocessing_queue()

    def boom():
        raise ValueError("roto")

    app._worker(events, boom, (), False)
    first, second = events.get(timeout=5), events.get(timeout=5)
    assert first == ("error", "roto") and second[0] == "log" and "ValueError: roto" in second[1], (first, second)
    path = app._log(second[1])
    assert path.parent == app.SETTINGS.parent and "ValueError: roto" in path.read_text(encoding="utf-8")


def multiprocessing_queue():
    import multiprocessing
    return multiprocessing.Queue()


def test_window_titles_tools_and_profiles():
    import app

    root, window = visible_window()
    book = convert.Book("Libro", "", b"", [convert.Block("heading", 1, "Capitulo Uno: El Inicio"),
                                           convert.Block("p", 1, "Texto del inicio."),
                                           convert.Block("heading", 1, "Parte 1"), convert.Block("p", 1, "Más."),
                                           convert.Block("heading", 2, "Parte 2"), convert.Block("p", 2, "Y más.")], [0])
    window.show_book(book)
    window.fix_numbers()
    numbered = window.tree.set("0", "title")
    window.parts_as_breaks()
    kinds = [window.tree.set(i, "inc") for i in ("2", "4")]
    window.undo()
    undone = [window.tree.set(i, "inc") for i in ("2", "4")]
    window.profile_var.set("E-reader de tinta")
    window.apply_profile()
    profile = (window.gray_var.get(), window.shrink_size_var.get(), window.write_options()["shrink"])
    window.profile_var.set("Lector antiguo (EPUB 2)")
    window.apply_profile()
    epub2 = window.write_options()["epub2"]
    minutes = window.tree.set("0", "min")
    window.close()
    assert numbered == "Capítulo 1: El Inicio", numbered
    assert kinds == [app.SEP, app.SEP] and undone == [app.OFF, app.OFF], (kinds, undone)
    assert profile == (True, 1400, 1400) and epub2, (profile, epub2)
    assert minutes == "1 min", minutes


def test_window_move_section_persists_and_undoes():
    import app

    root, window = visible_window()
    window.pdf = make_group_pdf()
    window.show_book(convert.analyze(window.pdf))
    titles = lambda: [window.tree.set(i, "title") for i in window.tree.get_children()]
    before = titles()
    last = window.tree.get_children()[-1]  # Capitulo 2: Final
    window.tree.selection_set(last)
    root.update()
    window.move_selected(-1)
    moved = titles()
    window.close()
    root, window = visible_window()
    window.pdf = TMP / "grupo.pdf"
    window.show_book(convert.analyze(window.pdf))
    reopened = titles()
    window.undo()  # lo recuperado no se deshace
    still = titles()
    window.tree.selection_set(window.tree.get_children()[1])
    window.move_selected(1)
    window.undo()
    undone = titles()
    window.close()
    assert before == ["Capitulo 1: Inicio", "Agradecimientos", "Capitulo 2: Final"], before
    assert moved == ["Capitulo 1: Inicio", "Capitulo 2: Final", "Agradecimientos"], moved
    assert reopened == moved and still == moved and undone == moved, (reopened, still, undone)


def test_window_drag_row_shows_where_it_goes():
    """Arrastrar una fila por su título: no se veía hacia dónde iba. Ahora una línea del color de acento marca
    dónde caerá (delante de la fila bajo el ratón) y se desliza al cambiar de fila; la selección no salta con el
    ratón. Al soltar, la sección se mueve allí y la guía desaparece."""
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    root, window = visible_window()
    window.pdf = make_group_pdf()
    window.show_book(convert.analyze(window.pdf))
    root.update()
    tree = window.tree
    titles = lambda: [tree.set(i, "title") for i in tree.get_children()]
    first, last = tree.get_children()[1], tree.get_children()[-1]  # se suelta delante de la segunda fila
    tree.selection_set(last)

    def mouse(kind, iid, dy=0):
        x, y, w, h = tree.bbox(iid, "title")
        pos = QPointF(x + 12, y + h / 2 + dy)
        button = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseMove else Qt.MouseButton.LeftButton
        held = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseButtonRelease else Qt.MouseButton.LeftButton
        QApplication.sendEvent(tree.viewport(), QMouseEvent(kind, pos, tree.viewport().mapToGlobal(pos), button, held,
                                                            Qt.KeyboardModifier.NoModifier))
        root.update()

    mouse(QEvent.Type.MouseButtonPress, last)
    mouse(QEvent.Type.MouseMove, last, -8)
    mouse(QEvent.Type.MouseMove, first)
    QApplication.processEvents()
    import time
    time.sleep(0.3)  # la línea termina de deslizarse
    QApplication.processEvents()
    target, selected = tree.drop_iid, tree.selection()
    top = tree.visualItemRect(tree.rows[first]).top()
    accent = QApplication.palette().color(QApplication.palette().ColorRole.Link)
    shot = tree.viewport().grab().toImage()
    line = min((shot.pixelColor(shot.width() * 3 // 4, y) for y in range(top - 2, top + 3)),
               key=lambda c: abs(c.red() - accent.red()) + abs(c.green() - accent.green()) + abs(c.blue() - accent.blue()))
    mouse(QEvent.Type.MouseButtonRelease, first)
    after, gone = titles(), tree.drop_iid is None and tree.drag_iid is None
    window.close()
    assert target == first and selected == (last,), (target, selected)
    assert abs(line.red() - accent.red()) < 40 and abs(line.blue() - accent.blue()) < 40, (line.name(), accent.name())
    assert after[1] == "Capitulo 2: Final" and gone, (after, gone)


def test_window_cover_from_illustration_quality_and_series_panel():
    root, window = visible_window()
    window.pdf = make_covers()
    window.show_book(convert.analyze(window.pdf))
    image = next(i for i in window.tree.get_children() if window.is_image(i))
    window.set_cover(("image", int(image)))
    label = window.cover_label.text()
    same = window.book.cover == window.book.blocks[int(image)].image
    window.choose_cover()
    window.quality()
    window.series_panel()
    root.update()
    panels = root.dialogs()
    table = window.series_window[1]
    states = [table.set(i, "state") for i in table.get_children()]
    window.close()
    assert label.startswith("Portada: ilustración de la página") and same, label
    assert len(panels) == 3, panels
    assert "Sin revisar" in states or "Revisado, sin EPUB" in states, states


def test_missing_volumes_text():
    """El panel de serie dice qué tomos enteros faltan entre el primero y el último de cada serie."""
    import app
    names = lambda *stems: [Path(f"{s}.pdf") for s in stems]
    one = app._missing_volumes(names("Re Zero - Volumen 01", "Re Zero - Volumen 03", "Re Zero - Volumen 1.5",
                                     "Re Zero Extra"))
    two = app._missing_volumes(names("Kage Vol 1", "Kage Vol 4"))
    many = app._missing_volumes(names("Kage Vol 1", "Kage Vol 3", "Kage Vol 5", "Kage Vol 7"))
    series = app._missing_volumes(names("Kage Vol 1", "Kage Vol 3", "Nageki Vol 2", "Nageki Vol 4"))
    none = app._missing_volumes(names("Kage Vol 1", "Kage Vol 2", "Suelto"))
    assert one == "Falta el tomo 2", one
    assert two == "Faltan los tomos 2 y 3", two
    assert many == "Faltan los tomos 2, 4 y 6", many
    assert series == "Kage: falta el tomo 2\nNageki: falta el tomo 3", series
    assert none == "", none


def test_window_series_panel_shows_missing_and_sent():
    """Con los tomos 1 y 3, el panel avisa del 2; el envío al lector apuntado sale en la columna «Enviado»."""
    import app
    folder = TMP / f"serie-{time_ns()}"
    folder.mkdir()
    for n in (1, 3):
        with pymupdf.open() as doc:
            add_page(doc, 1, [(72, 100, "Texto.", 11, "cour")])
            doc.save(folder / f"Serie - Volumen 0{n}.pdf")
    (folder / "Serie - Volumen 01.epub").write_bytes(b"x")
    root, window = visible_window()
    app._record_send(folder / "Serie - Volumen 01.epub", "USB")
    window.pdf = folder / "Serie - Volumen 01.pdf"
    window.series_panel()
    root.update()
    win, table = window.series_window
    sent = [table.set(i, "sent") for i in table.get_children()]
    labels = [w.text() for w in win.findChildren(app.QLabel)]
    window.close()
    assert sent == [f"USB · {app.datetime.now():%d/%m}", "—"], sent
    assert "Falta el tomo 2" in labels, labels


def test_window_status_line_elides_and_gets_report_memos():
    """Una línea de estado larga acaba en «…» con el texto entero al pasar el ratón. Al abrir, la ventana
    recibe del proceso de trabajo lo que el informe recuerda de cada párrafo: si no, la primera edición la
    paraba 2–3 s recalculándolo entero."""
    import app
    root, window = visible_window()
    window.pdf = make_group_pdf()
    result = app._analyze(window.pdf)  # como en el proceso de trabajo
    memos = {name: dict(cache) for name, cache in result[3].items()}
    for f in convert.REPORT_MEMOS:
        f.cache.clear()  # la ventana empieza sin nada, como otro proceso
    window.show_book(*result[:3], memos)
    long_text = "Estado " + "muy largo " * 80 + "FINAL"
    window.status.setText(long_text)
    root.update()
    shown = window.status.fontMetrics().elidedText(long_text, app.Qt.TextElideMode.ElideRight, window.status.width())
    tip, text = window.status.toolTip(), window.status.text()
    paragraphs = {convert._plain(b).strip() for b in window.book.blocks if b.kind == "p"}
    seeded = paragraphs <= set(convert._facts.cache)
    window.close()
    assert shown.endswith("\u2026") and tip == long_text and text == long_text, (tip[-10:], text[-10:])
    assert seeded and memos["_facts"], (len(paragraphs), len(memos["_facts"]))


def test_custom_font_is_embedded():
    """Una fuente propia (.ttf/.otf) va dentro del EPUB; si el archivo desaparece, la ventana vuelve a la del
    lector y lo dice."""
    import shutil
    font = TMP / "Mi Letra.ttf"
    shutil.copy(convert.FONT_DIR / "bitter-400-normal.ttf", font)
    book = convert.Book("Libro", "", b"", [convert.Block("heading", 1, "Cap 1"), convert.Block("p", 1, "Uno.")], [0])
    out = TMP / "fuente.epub"
    convert.write_epub(book, [(0, "Cap 1")], out, font=str(font))
    css = epub_file(out, "OEBPS/style.css")
    with zipfile.ZipFile(out) as z:
        names = [n for n in z.namelist() if n.startswith("OEBPS/fonts/")]
    assert names == ["OEBPS/fonts/propia.ttf"] and css.count("@font-face") == 1, (names, css)
    assert 'font-family: "Mi Letra"' in css and convert.check_epub(out) == [], css
    root, window = visible_window()
    window.show_book(book)
    window.apply_state({"font": "Mi Letra.ttf", "font_file": str(font)})
    dialog = window.open_options("Aspecto", "book")
    shown, option = dialog.parts["widgets"]["font"].currentText(), window.write_options()["font"]
    dialog.parts["cancel"]()
    window.apply_state({"font": "Otra.ttf", "font_file": str(TMP / "Otra.ttf")})
    fallback, status = window.font_var.get(), window.status.text()
    picked = []
    dialog = window.open_options("Aspecto", "book")
    for answer in (str(font), ""):  # elegir el archivo; después, cancelar: se queda la anterior
        window.ask_font_file = lambda: answer
        box = dialog.parts["widgets"]["font"]
        box.setCurrentText("Otra…")
        box.activated.emit(box.currentIndex())
        picked.append((window.font_var.get(), dialog.parts["widgets"]["font"].currentText()))
    dialog.parts["cancel"]()
    window.close()
    assert picked == [("Mi Letra.ttf", "Mi Letra.ttf")] * 2, picked
    assert shown == "Mi Letra.ttf" and option == str(font), (shown, option)
    assert fallback == "Del lector" and "Otra.ttf" in status, (fallback, status)


def test_serve_gives_the_epub_over_http():
    """El servidor de «Enviar al móvil» da el EPUB en cualquier ruta y deja de hacerlo al cerrar la ventana."""
    import urllib.error
    import urllib.request

    import app
    from PySide6.QtWidgets import QLabel

    epub = TMP / "Mi novela.epub"
    epub.write_bytes(b"PK" + os.urandom(5000))
    server = app._serve(epub, "127.0.0.1")
    port = server.server_port
    got = []
    for path in ("x", "otra/ruta"):
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/{path}", timeout=5) as r:
            got.append((r.read() == epub.read_bytes(), r.headers["Content-Type"], r.headers["Content-Disposition"]))
    server.shutdown()
    server.server_close()
    assert got == [(True, "application/epub+zip", "attachment; filename*=UTF-8''Mi%20novela.epub")] * 2, got
    root, window = visible_window()
    shown = []
    original, lan = app.messagebox.showerror, app._lan_ip
    try:
        app.messagebox.showerror = lambda title, message: shown.append(message)
        app._lan_ip = lambda: "127.0.0.1"
        window.send_to_phone(epub)  # sin red local
        window.send_to_phone(epub, ip="127.0.0.1")
    finally:
        app.messagebox.showerror, app._lan_ip = original, lan
    win, server = window.phone_window
    qr = [label for label in win.findChildren(QLabel) if label.pixmap() and not label.pixmap().isNull()]
    texts = " ".join(label.text() for label in win.findChildren(QLabel))
    with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/", timeout=5) as r:
        served = r.read() == epub.read_bytes()
    win.close()
    root.update()
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/", timeout=2)
        stopped = False
    except (urllib.error.URLError, OSError):
        stopped = True
    sent = app._sent_text(epub)  # el móvil lo descargó: el panel de serie lo cuenta como enviado
    window.close()
    assert sent.startswith("Wi-Fi · "), sent
    assert shown == ["No hay red local: conecta el equipo a la Wi-Fi."], shown
    assert len(qr) == 1 and served and stopped, (len(qr), served, stopped)
    assert f"http://127.0.0.1:{server.server_port}/Mi%20novela.epub" in texts and "Firewall" in texts, texts


def test_text_cover():
    """Portada dibujada: JPEG de 1200×1800, otro color con otra serie, y un título larguísimo no falla."""
    book = convert.Book("Re:Zero", "Tappei Nagatsuki", b"", [], [], series="Re:Zero", volume="3")
    cover = convert.text_cover(book)
    pix = pymupdf.Pixmap(cover)
    other = convert.text_cover(convert.Book("Re:Zero", "Tappei Nagatsuki", b"", [], [], series="Otra", volume="3"))
    long = convert.text_cover(convert.Book("Título " * 45, "", b"", [], []))
    assert cover[:3] == b"\xff\xd8\xff" and (pix.width, pix.height) == (1200, 1800), (pix.width, pix.height)
    assert other != cover and long[:3] == b"\xff\xd8\xff"
    root, window = visible_window()
    window.pdf = make_covers()
    window.show_book(convert.analyze(window.pdf))
    window.set_cover(("text", ""))
    label, drawn = window.cover_label.text(), window.book.cover
    window.close()
    assert label == "Portada: con título" and drawn[:3] == b"\xff\xd8\xff", label


def test_window_error_is_logged_and_shown():
    import app

    root, window = visible_window()
    shown = []
    original = app.messagebox.showerror
    app.messagebox.showerror = lambda title, message: shown.append(message)
    try:
        try:
            raise RuntimeError("fallo de prueba")
        except RuntimeError as e:
            window.on_error(type(e), e, e.__traceback__)
    finally:
        app.messagebox.showerror = original
        root.destroy()
    log = (app.SETTINGS.parent / "errores.log").read_text(encoding="utf-8")
    assert shown and "fallo de prueba" in shown[0] and "errores.log" in shown[0], shown
    assert "RuntimeError: fallo de prueba" in log


def test_install_paths_are_per_user():
    import app

    exe, link = app._install_paths()
    assert "Programs" in str(exe) and exe.name == "PDF a EPUB.exe" and link.suffix == ".lnk", (exe, link)


def test_edit_section_keeps_pages_of_unchanged_paragraphs():
    """Corregir una errata no cambia la página de los demás párrafos: la lista de páginas del EPUB y el
    panel del PDF del editor dependen de ella. Lo corregido en su sitio conserva la suya; lo nuevo toma la del
    párrafo anterior."""
    doc = pymupdf.open()
    texts = ("El primer parrafo de la pagina uno.", "El segundo parrafo de la pagina dos.",
             "El tercer parrafo de la pagina tres.")
    for n, text in enumerate(texts, 1):
        title = [(72, 100, "Capitulo 1: Inicio", 20, "helv")] if n == 1 else []
        add_page(doc, n, title + [(92, 140, text, 11, "cour")])
    path = TMP / "paginas.pdf"
    doc.save(path)
    book = convert.analyze(path)
    start, end = book.candidates[0] + 1, len(book.blocks)
    text = convert.section_text(book, start, end).replace("pagina dos.", "pagina dos!") + "\n\nNuevo."
    convert.edit_section(book, start, end, text)
    pages = [b.page for b in book.blocks[start:]]
    assert pages == [1, 2, 3, 3], pages


def test_edit_section_translated_paragraphs_keep_page_and_box():
    """Witch vol. 1 traducido con DeepSeek: tras «Aplicar al libro» el editor no recuadraba nada en el PDF, porque
    cada párrafo traducido perdía su recuadro y su página, y el texto en español no se encuentra en el PDF inglés.
    Cambiado uno por uno, cada párrafo sigue siendo el mismo; unir dos no se queda el recuadro de ninguno."""
    doc = pymupdf.open()
    add_page(doc, 1, [(72, 100, "Chapter 1: Start", 20, "helv"), (92, 140, "The first paragraph is here.", 11, "cour")])
    add_page(doc, 2, [(92, 140, "The second paragraph is here.", 11, "cour")])
    add_page(doc, 3, [(92, 140, "The third paragraph on page three.", 11, "cour")])
    path = TMP / "traducido.pdf"
    doc.save(path)
    book = convert.analyze(path)
    start, end = book.candidates[0], len(book.blocks)
    before = [(b.page, b.rect) for b in book.blocks[start:end]]
    assert all(rect for _, rect in before) and [p for p, _ in before] == [1, 1, 2, 3], before
    spanish = ["# Capítulo 1: Inicio", "El primer párrafo está aquí.", "El segundo párrafo está aquí.",
               "El tercer párrafo en la página tres."]
    convert.edit_section(book, start, end, "\n\n".join(spanish))
    assert [(b.page, b.rect) for b in book.blocks[start:end]] == before, book.blocks[start:end]
    assert book.blocks[start].kind == "heading" and "primer" in book.blocks[start + 1].html
    joined = "\n\n".join(spanish[:1] + [spanish[1] + " " + spanish[2]] + spanish[3:])
    convert.edit_section(book, start, end, joined)
    assert not book.blocks[start + 1].rect and book.blocks[start + 2].rect == before[3][1], book.blocks[start:]


def test_paragraph_box_covers_all_its_lines_and_survives_edits():
    """El recuadro del editor sale del análisis, no de buscar el texto: la búsqueda solo cubría la primera línea
    de las últimas palabras y fallaba con "─" en vez de raya (Kage 6, pág. 15). Lo que no se toca lo conserva."""
    doc = pymupdf.open()
    add_page(doc, 1, [(72, 100, "Capitulo 1: Inicio", 20, "helv"),
                      (92, 140, line("─Primera linea del parrafo", 57), 11, "cour"),
                      (72, 154, line("Segunda linea del parrafo", 60), 11, "cour"),
                      (72, 168, "fin del parrafo.", 11, "cour"),
                      (92, 196, "Otro parrafo distinto y completo.", 11, "cour")])
    path = TMP / "recuadro.pdf"
    doc.save(path)
    book = convert.analyze(path)
    start = book.candidates[0] + 1
    first, second = book.blocks[start:start + 2]
    box = pymupdf.Rect(first.rect)
    assert box.y0 < 140 - 8 and 168 < box.y1 < 196 - 8 and box.x0 <= 72 and box.x1 > 400, box
    import app  # sin recuadro (párrafo editado), se busca: las últimas palabras cruzan de línea
    edited = convert.Block("p", 1, line("Segunda linea del parrafo", 60) + " fin del parrafo.")
    found = app.TextEditor.find_box(pymupdf.open(path)[0], edited)
    assert found and found.y0 < 154 < 168 < found.y1, found
    text = convert.section_text(book, start, len(book.blocks)).replace("distinto", "nuevo")
    convert.edit_section(book, start, len(book.blocks), text)
    assert book.blocks[start].rect == first.rect and book.blocks[start + 1].rect == second.rect, book.blocks[start + 1]


def test_window_keeps_a_copy_of_a_review_that_no_longer_matches():
    """Si el análisis cambia (otra versión del programa), la revisión vieja no se pisa al cerrar: queda en .bak. Los
    datos del libro sí se conservan (Witch 1: el arreglo del análisis apartaba también título, glosario y portada)."""
    import json

    import app

    root, window = visible_window()
    window.pdf = make_novel()
    for old in TMP.glob(app._review_path(window.pdf).name + "*"):  # de otras pruebas con la misma novela
        old.unlink()
    window.show_book(convert.analyze(window.pdf))
    window.set_kind(first_title(window), app.OFF)
    window.title_var.set("Mi título")
    window.tr_glossary = [["Witch", "Bruja"]]
    window.save_review()
    window.close()
    review = app._review_path(window.pdf)
    state = json.loads(review.read_text(encoding="utf-8"))
    state["blocks"] += 1  # como si otra versión del análisis hubiera dado otros bloques
    review.write_text(json.dumps(state), encoding="utf-8")

    root, window = visible_window()
    window.pdf = TMP / "novela.pdf"
    window.show_book(convert.analyze(window.pdf))
    status = window.status.text()
    kept = window.title_var.get(), window.tr_glossary, window.tree.set(first_title(window), "inc")
    window.close()
    backup = review.with_name(review.name + ".bak")
    assert backup.is_file() and json.loads(backup.read_text(encoding="utf-8")) == state, "se perdió la revisión vieja"
    assert status.startswith("La revisión guardada no coincide") and backup.name in status, status  # delante: no se corta
    assert kept[:2] == ("Mi título", [["Witch", "Bruja"]]) and kept[2] != app.OFF, kept
    review.write_text(json.dumps(state), encoding="utf-8")  # otra revisión que no vale: la copia anterior no se pisa
    root, window = visible_window()
    window.pdf = TMP / "novela.pdf"
    window.show_book(convert.analyze(window.pdf))
    window.close()
    second = review.with_name(review.name + ".2.bak")
    assert json.loads(backup.read_text(encoding="utf-8")) == state and second.is_file(), "se pisó la copia anterior"


def test_window_does_not_start_a_second_job():
    """Con un trabajo en curso no se lanza otro: el resultado del primero llegaría al segundo."""
    import app

    root, window = visible_window()
    asked = []
    original = app.filedialog.askopenfilenames
    app.filedialog.askopenfilenames = lambda **kwargs: asked.append(kwargs) or ()
    try:
        window.done = lambda result: None  # análisis en curso
        window.omnibus()
        started = window.run(convert.analyze, (str(TMP / "no-existe.pdf"),), lambda result: None)
    finally:
        app.filedialog.askopenfilenames = original
        window.done = None
    window.close()
    assert not asked and started is False, (asked, started)


def test_window_does_not_decode_illustrations_again():
    """Las ilustraciones repetidas se buscan en el proceso de trabajo y la lista no vuelve a decodificar las
    imágenes en cada edición: en la ventana costaba 4,5 s al abrir Kage 5 y 2 s por edición."""
    import app

    root, window = visible_window()
    window.pdf = make_novel()
    book, twins, report, memos = app._analyze(str(window.pdf))
    found = convert.duplicate_images
    original = convert.pymupdf.Pixmap
    decoded = []
    convert.duplicate_images = lambda b: 1 / 0  # ya viene del proceso de trabajo
    try:
        window.show_book(book, twins, report, memos)
        convert.pymupdf.Pixmap = lambda *args: decoded.append(args) or original(*args)
        window.fill_tree(window.rows())
    finally:
        convert.duplicate_images, convert.pymupdf.Pixmap = found, original
    window.close()
    assert twins == found(book) and not decoded, (twins, len(decoded))


def test_window_warns_when_the_review_cannot_be_saved():
    """Si la revisión no se puede escribir (OneDrive la bloquea, carpeta de solo lectura), se avisa y no
    queda un temporal; la revisión anterior sigue entera. Cerrar o abrir otro libro preguntan antes de perderla, y
    un bloqueo breve (OneDrive sincronizando) se reintenta."""
    import app

    root, window = visible_window()
    window.pdf = make_novel()
    window.show_book(convert.analyze(window.pdf))
    review = app._review_path(window.pdf)
    review.unlink(missing_ok=True)
    review.mkdir()  # en su sitio, una carpeta: el reemplazo falla
    asked, original = [], app.messagebox.askyesno
    app.messagebox.askyesno = lambda title, message, **kwargs: asked.append((title, message)) or False
    try:
        saved = window.save_review()
        status = window.status.text()
        window.open_path(str(window.pdf))  # «No»: sigue con este libro, sin analizar otro
        opened = window.done is None and window.book is not None
        closed = window.close()  # «No»: la ventana sigue abierta
    finally:
        app.messagebox.askyesno = original
        review.rmdir()
    replace, tries = app.os.replace, []

    def busy(src, dst):  # OneDrive bloquea el archivo un momento
        tries.append(dst)
        if len(tries) < 3:
            raise PermissionError(13, "El proceso no tiene acceso al archivo")
        replace(src, dst)

    app.os.replace = busy
    try:
        retried = window.save_review()
    finally:
        app.os.replace = replace
    window.close()
    assert saved is False and "No se pudo guardar la revisión" in status, status
    assert not list(TMP.glob("*.json.tmp")), "quedó un temporal"
    assert [t for t, _ in asked] == ["Abrir", "Cerrar"] and status in asked[1][1], asked
    assert opened and closed is False, (opened, closed)
    assert retried and len(tries) == 3 and review.is_file(), tries


def test_kepub_option_profiles_and_names():
    import app

    assert app._epub_name("C:/x/Libro", True) == "C:/x/Libro.kepub.epub"
    assert app._epub_name("C:/x/Libro.epub", True) == "C:/x/Libro.kepub.epub"
    assert app._epub_name("C:/x/Libro.KEPUB.epub", False) == "C:/x/Libro.epub"
    assert app._epub_name("Vol. 1", False) == "Vol. 1.epub"
    assert app.PROFILES["Kobo"]["kepub"] and not app.PROFILES["Kindle"]["kepub"]
    assert app.PROFILES["Kindle"]["shrink_size"] == 1600 and not app.PROFILES["Kindle"]["gray"]
    assert all("kepub" in p for name, p in app.PROFILES.items() if name != "Personalizado")
    assert app._write_options({"kepub": True})["kepub"] and not app._write_options({})["kepub"]
    assert "kepub" in app.INHERITED and "kepub" in __import__("options").BOOK_KEYS


def test_batch_with_kobo_writes_kepub():
    import app

    pdf = TMP / "lote-kobo" / "Mi Novela - Volumen 1.pdf"
    pdf.parent.mkdir(exist_ok=True)
    make_novel().replace(pdf)
    results = app._batch([str(pdf)], {"kepub": True})
    assert results[0][1] == "", results
    assert pdf.with_name("Mi Novela - Volumen 1.kepub.epub").is_file(), list(pdf.parent.iterdir())


def test_reader_kind_and_size_text():
    import app

    kindle, kobo, usb = (TMP / f"unidad-{name}" for name in ("kindle", "kobo", "usb"))
    for folder in (kindle / "documents", kindle / "system", kobo / ".kobo", usb):
        folder.mkdir(parents=True, exist_ok=True)
    assert [app._reader_kind(r) for r in (kindle, kobo, usb)] == ["kindle", "kobo", "usb"]
    assert app._size_text(18_400_000) == "18,4 MB" and app._size_text(51_000_000) == "51,0 MB"
    assert app.KINDLE_MAIL == 50_000_000
    assert all(len(root) == 3 and root.endswith(":\\") for root, _ in app._drives())


def test_install_quotes_paths_with_apostrophes_for_powershell():
    """Un usuario llamado O'Neil: la ruta del acceso directo no debe romper el script de PowerShell."""
    import subprocess

    import app

    path = r"C:\Users\O'Neil\AppData\PDF a EPUB.lnk"
    out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", f"Write-Output {app._ps_quote(path)}"],
                         capture_output=True, text=True, check=True).stdout.strip()
    assert out == path, out


def test_qtk_rowlist_works_like_treeview():
    import qtk

    qtk.app()
    tree = qtk.RowList(("inc", "title"), {"inc": "Tipo", "title": "Título"}, {"inc": 80, "title": 200})
    for k, title in ((0, "Uno"), (3, "Dos"), (5, "Tres")):
        tree.insert("", "end", iid=str(k), values=("Capítulo", title), tags=("chapter",))
    tree.set("3", "title", "Dos bis")
    tree.selection_set("5")
    seen = (tree.get_children(), tree.set("3", "title"), tree.selection(), tree.index("5"), tree.exists("4"),
            tree.item("3")["tags"])
    tree.delete("0")
    assert seen == (("0", "3", "5"), "Dos bis", ("5",), 2, False, ("chapter",)), seen
    assert tree.get_children() == ("3", "5")
    tree.delete(*tree.get_children())
    assert tree.get_children() == () and not tree.exists("3")


def test_qtk_rowlist_tooltip_shows_cut_text():
    """Con la ventana estrecha (zoom de Windows) los títulos largos salen cortados con «…»: al pasar el
    ratón, el tooltip los muestra enteros. Un título que cabe deja el tooltip de la lista (los atajos)."""
    import qtk

    qtk.app()
    tree = qtk.RowList(("inc", "title"), {"inc": "Tipo", "title": "Título"}, {"inc": 80, "title": 200}, stretch=None)
    long_title = "Capítulo 1: Hacer frente al servicio comunitario de la capital"
    tree.insert("", "end", iid="0", values=("Capítulo", long_title))
    tree.insert("", "end", iid="1", values=("Capítulo", "Uno"))
    tree.resize(320, 200)
    tree.show()
    qtk.pump()
    from PySide6.QtCore import QPoint

    x = tree.columnViewportPosition(1) + 20  # dentro de la columna del título
    cut, fits = (tree.full_text_at(QPoint(x, tree.visualItemRect(tree.rows[i]).center().y())) for i in ("0", "1"))
    tree.close()
    assert (cut, fits) == (long_title, None), (cut, fits)


def test_qtk_textbox_understands_tk_indices():
    import qtk

    qtk.app()
    text = qtk.TextBox()
    text.insert("end", "uno dos\n\ntres", ("src4",))
    text.mark_set("insert", "3.2")
    seen = [text.get("1.0", "end-1c"), text.index("insert"), text.get("insert linestart", "insert lineend"),
            text.get("1.4", "1.end"), text.index("end-1c"), "src4" in text.tag_names("3.1"),
            text.search("dos", "1.0"), text.get("1.0", "end"), text.index("insert linestart+2c")]
    with text.group():
        text.delete("1.0", "1.4")
        text.insert("1.0", "UNO ")
    text.edit_undo()
    seen.append(text.get("1.0", "1.end"))
    text.tag_add("sel", "1.0", "1.3")
    seen.append((text.index("sel.first"), text.index("sel.last"), text.tag_ranges("sel")))
    text.tag_add("italic", "1.4", "1.7")
    text.insert("1.0", "xx")  # los tags siguen al texto
    seen.append("italic" in text.tag_names("1.6") and "italic" not in text.tag_names("1.2"))
    text.tag_remove("italic", "1.0", "end")
    seen.append(text.tag_names("1.6"))
    assert seen == ["uno dos\n\ntres", "3.2", "tres", "dos", "3.4", True, "1.4", "uno dos\n\ntres\n", "3.2",
                    "uno dos", ("1.0", "1.3", ("1.0", "1.3")), True, ("src4",)], seen


def test_theme_text_is_readable():
    """Cada color de texto contra su fondo llega a 4,5:1 (WCAG AA) en los dos temas: el botón azul del
    tema oscuro (3,8:1), las filas «Quitar» y «Descartada», y los avisos «Revisar» y «Menor» no llegaban.
    El gris de lo desactivado (hint) queda fuera: WCAG no lo pide."""
    import theme

    def luminance(color):
        def channel(v):
            v /= 255
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
        return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)

    def ratio(a, b):
        high, low = sorted((luminance(a), luminance(b)), reverse=True)
        return (high + 0.05) / (low + 0.05)

    low = []
    for dark in (False, True):
        c = theme.colors(dark)
        pairs = [(fg, c[fg], bg, c[bg]) for fg, bg in (("ink", "bg"), ("ink", "card"), ("muted", "bg"), ("muted", "card"),
                                                       ("acc_ink", "acc"), ("acc", "bg"), ("acc", "card"), ("acc", "drop"),
                                                       ("warn", "card"), ("ok", "card"))]
        pairs += [(f"fila {k}", v, "card", c["card"]) for k, v in theme.kind_colors(dark).items() if v]
        pairs += [(f"nivel {k}", v, "card", c["card"]) for k, v in theme.level_colors(dark).items()]
        for name, tags in (("editor", theme.editor_tags(dark)), ("informe", theme.diff_tags(dark))):
            pairs += [(f"{name} {k}", v["foreground"], "fondo", v.get("background", c["base"]))
                      for k, v in tags.items() if "foreground" in v]
        low += [f"{'oscuro' if dark else 'claro'}: {fg} sobre {bg} {ratio(a, b):.2f}"
                for fg, a, bg, b in pairs if ratio(a, b) < 4.5]
    assert not low, low


def test_theme_light_dark_and_bad_setting():
    """Claro y oscuro con Fusion en los dos Windows; un tema mal escrito en ajustes.json sigue al sistema."""
    import qtk
    import theme

    q = qtk.app()
    dark = theme.apply(q, "Oscuro")
    window_dark = q.palette().window().color().lightness()
    light = theme.apply(q, "Claro")
    window_light = q.palette().window().color().lightness()
    fallback = theme.apply(q, "Morado")
    assert dark and not light and window_dark < 80 and window_light > 200, (window_dark, window_light)
    system = q.styleHints().colorScheme() == theme.Qt.ColorScheme.Dark
    assert fallback == system and "border-radius" in q.styleSheet()
    assert theme.kind_colors(True)["subtitle"] != theme.kind_colors(False)["subtitle"]
    assert set(theme.editor_tags(True)) == set(theme.editor_tags(False))
    theme.apply(q, "Claro")


def test_analyze_reads_epub():
    """Un EPUB se lee como un PDF (PyMuPDF lo compone en páginas): mismos capítulos, cursiva e ilustración.
    El número del capítulo, pequeño y sin negrita encima del nombre, vuelve a unirse al título."""
    pdf = TMP / "Ida y vuelta - Volumen 2.pdf"
    make_novel().replace(pdf)
    book = convert.analyze(pdf)
    epub = TMP / "Ida y vuelta - Volumen 2 (LNC).epub"
    convert.write_epub(book, [(k, convert._plain(book.blocks[k])) for k in book.candidates], epub)
    again = convert.analyze(epub)
    titles = [convert._plain(again.blocks[k]) for k in again.candidates]
    assert titles[-2:] == ["Capitulo 1: Inicio", "Capitulo 2: Final"], titles
    assert "Ella penso <i>que era tarde.</i>" in [b.html for b in again.blocks], [b.html for b in again.blocks][:8]
    assert any(b.kind == "image" for b in again.blocks)
    assert not any(convert._plain(b) == "Capitulo 1" for b in again.blocks), "el número quedó como párrafo"


def test_analyze_external_epub_long_toc_and_inline_image():
    """EPUB de otro programa: su índice ocupa varias páginas al componerlo y solo la primera dice «Contenido»
    (las demás son enlaces: también se omiten); una imagen en línea deja U+FFFC en el texto, que no debe
    quedar como párrafo suelto."""
    import zipfile

    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 60), False)
    pix.clear_with(120)
    chapters = 60
    places = ["bosque", "río", "ciudad", "torre", "puerto", "castillo", "mercado", "jardín", "desierto", "montaña"]
    heads = {n: f"Capítulo {n}: El {places[n % 10]} y la {places[n // 10]}" for n in range(1, chapters + 1)}
    toc = "".join(f'<p><a href="c{n}.xhtml">{heads[n]}</a></p>' for n in heads)
    page = '<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml"><body>{}</body></html>'
    files = {"toc.xhtml": page.format("<h1>Contenido</h1>" + toc)}
    for n in range(1, chapters + 1):
        files[f"c{n}.xhtml"] = page.format(f'<h1>{heads[n]}</h1>'
                                           f'<p><img src="a.png" alt=""/></p>'
                                           f'<p>{"Texto del capítulo con varias palabras. " * 20}</p>')
    manifest = "".join(f'<item id="{name[:-6]}" href="{name}" media-type="application/xhtml+xml"/>' for name in files)
    spine = "".join(f'<itemref idref="{name[:-6]}"/>' for name in files)
    opf = ('<?xml version="1.0" encoding="utf-8"?><package xmlns="http://www.idpf.org/2007/opf" version="2.0" '
           'unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Externo</dc:title>'
           '<dc:identifier id="id">x</dc:identifier><dc:language>es</dc:language></metadata><manifest>'
           f'{manifest}<item id="a" href="a.png" media-type="image/png"/></manifest><spine>{spine}</spine></package>')
    epub = TMP / "Externo - Volumen 3.epub"
    with zipfile.ZipFile(epub, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", '<?xml version="1.0"?><container version="1.0" '
                   'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                   '<rootfile full-path="content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr("content.opf", opf)
        z.writestr("a.png", pix.tobytes("png"))
        for name, text in files.items():
            z.writestr(name, text)
    assert pymupdf.open(epub)[1].get_text().startswith("Capítulo"), "el índice debía ocupar más de una página"
    book = convert.analyze(epub)
    titles = [convert._plain(book.blocks[k]) for k in book.candidates]
    assert titles == list(heads.values()), titles[:3]
    loose = [b.page for b in book.blocks[:book.candidates[0]] if b.kind == "p"]
    assert not loose, f"restos del índice antes del capítulo 1, páginas {loose}"
    assert not any("￼" in b.html or (b.kind == "p" and not convert._plain(b)) for b in book.blocks)


def test_epub_sources_and_output_name():
    """Un EPUB de entrada no se pisa: su salida lleva «(LNC)». En una carpeta, un EPUB que es la salida de
    otro archivo (mismo nombre que un PDF, «(LNC)» o .kepub.epub) no es una entrada."""
    import app

    folder = TMP / f"fuentes-{time_ns()}"
    folder.mkdir()
    for name in ("A.pdf", "A.epub", "B.epub", "B (LNC).epub", "C.kepub.epub", "D.EPUB", "notas.txt"):
        (folder / name).write_bytes(b"x")
    assert [p.name for p in app._sources(folder)] == ["A.pdf", "B.epub", "D.EPUB"], app._sources(folder)
    assert app._out(folder / "A.pdf") == folder / "A" and app._out(folder / "B.epub") == folder / "B (LNC)"
    assert app._has_epub(folder / "B.epub") and app._has_epub(folder / "A.pdf") and not app._has_epub(folder / "D.EPUB")



def card_text(window, n):
    """Texto de la tarjeta n de recientes en la pantalla de inicio (o del aviso «Aún no has abierto…»)."""
    from PySide6.QtWidgets import QLabel

    item = window.recent_list.item(n)
    card = window.recent_list.itemWidget(item)
    if card is None:
        return item.text()
    return " ".join(label.text() for label in card.findChildren(QLabel))


def test_window_start_screen_shows_only_what_needs_no_book():
    """Sin libro abierto: ni las opciones del EPUB, ni Generar, Calidad, Buscar, «Ningún archivo» o el botón
    Recientes; las herramientas del libro, en gris. Con el libro, todo vuelve. Los recientes son tarjetas con PDF o
    EPUB, estado, portada guardada al abrir y «Quitar de recientes»."""
    import app

    root, window = visible_window()
    hidden = [w.isVisible() for w in (window.bottom, window.options_row, window.gen_btn, window.quality_btn,
                                      window.find_btn, window.recent_btn, window.file_label)]
    tools_off = [a.isVisible() for a in window.book_actions]  # ocultas: en gris parecían activas (tema oscuro)
    pdf = TMP / "inicio.pdf"  # copia propia: otras pruebas dejan revisión y EPUB junto a novela.pdf
    pdf.write_bytes(make_novel().read_bytes())
    gone = TMP / "perdido.epub"
    window.settings["recent"] = [str(pdf), str(gone)]
    window.fill_recent()
    cards = [card_text(window, 0), card_text(window, 1)]
    window.pdf = pdf
    window.show_book(convert.Book("Libro", "", jpeg(300, 420), [convert.Block("heading", 1, "Capítulo 1"),
                                                                 convert.Block("p", 1, "Texto.")], [0]))
    shown = [w.isVisible() for w in (window.bottom, window.options_row, window.gen_btn, window.quality_btn,
                                     window.find_btn, window.recent_btn, window.file_label)]
    tools_on = [a.isVisible() for a in window.book_actions]
    thumb = app._thumb_path(pdf).is_file()
    card = window.recent_card(str(pdf))
    with_cover = any(not label.pixmap().isNull() for label in card.findChildren(app.QLabel))
    window.forget_recent(str(gone))
    left = window.settings["recent"], window.recent_list.count()
    # Un tomo nunca abierto: su miniatura es la primera página (la portada en las novelas); un archivo roto, nada
    never = TMP / "nunca-abierto.pdf"
    never.write_bytes(make_novel().read_bytes())
    broken = TMP / "roto.pdf"
    broken.write_bytes(b"no es un pdf")
    app._render_thumbs([(str(never), str(app._thumb_path(never))), (str(broken), str(app._thumb_path(broken)))])
    first_page = app.QPixmap(str(app._thumb_path(never)))
    window.close()
    assert hidden == [False] * 7 and not any(tools_off), (hidden, tools_off)
    assert shown == [True] * 7 and all(tools_on), (shown, tools_on)
    assert "inicio" in cards[0] and "PDF" in cards[0] and "Sin revisar" in cards[0], cards[0]
    assert "perdido" in cards[1] and "EPUB" in cards[1] and "No se encuentra" in cards[1], cards[1]
    assert thumb and with_cover, (thumb, with_cover)
    assert left == ([str(pdf)], 1), left
    assert first_page.height() == 124 and not app._thumb_path(broken).exists(), first_page.size()

def test_window_empty_screen_and_dropped_pdf():
    """Sin PDF: la pantalla vacía con los recientes. Soltar archivos sobre la ventana abre el primer .pdf y
    no hace nada si no hay ninguno."""
    from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
    from PySide6.QtGui import QDropEvent

    import app

    root, window = visible_window()
    empty = (window.stack.currentIndex(), window.recent_list.item(0).text())
    notes = TMP / "notas.txt"
    notes.write_text("no es un pdf", encoding="utf-8")
    pdf = make_novel()

    def mime(*paths):
        data = QMimeData()
        data.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
        return data

    picked = (app._dropped_pdf(mime(notes)), app._dropped_pdf(mime(notes, pdf)))
    opened = []
    window.root.on_drop = opened.append
    for data in (mime(notes), mime(notes, pdf)):
        window.root.dropEvent(QDropEvent(QPointF(20, 20), Qt.DropAction.CopyAction, data,
                                         Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    window.pdf = pdf
    window.show_book(convert.analyze(pdf))
    book_page = window.stack.currentIndex()
    window.close()
    assert empty == (0, "Aún no has abierto ningún PDF"), empty
    assert picked == (None, str(pdf).replace("\\", "/")) or picked == (None, str(pdf)), picked
    assert len(opened) == 1 and Path(opened[0]) == pdf, opened
    assert book_page == 1



def test_window_generates_pdf_from_menu():
    """«PDF ▾» → «Generar PDF (A5…)»: el PDF sale en el proceso de trabajo, abre «PDF listo» con «Abrir PDF» y sin
    EPUBCheck, y no deja pisar el PDF original."""
    import time

    import app
    from PySide6.QtWidgets import QPushButton

    root, window = visible_window()
    window.pdf = make_novel()
    window.show_book(convert.analyze(window.pdf))
    out = TMP / "desde-menu.pdf"
    saved, errors = app.filedialog.asksaveasfilename, []
    showerror = app.messagebox.showerror
    app.messagebox.showerror = lambda title, message, **_: errors.append(message)
    try:
        app.filedialog.asksaveasfilename = lambda **options: str(window.pdf)  # el original: no
        window.generate_pdf("A5")
        app.filedialog.asksaveasfilename = lambda **options: str(out)
        [action] = [a for a in window.pdf_btn.menu().actions() if a.text().startswith("Generar PDF (A5")]
        action.trigger()
        deadline = time.time() + 60
        while window.done and time.time() < deadline:
            root.update()
            time.sleep(0.05)
        root.update()
        dialog = root.dialogs()[-1]
        title = dialog.windowTitle()
        buttons = [b.text() for b in dialog.findChildren(QPushButton)]
        pages = len(pymupdf.open(out))
        window.close()
    finally:
        app.filedialog.asksaveasfilename, app.messagebox.showerror = saved, showerror
    assert errors == ["Ese es el PDF original: elige otro nombre."], errors
    assert title == "PDF listo" and "Abrir PDF" in buttons and "Validar con EPUBCheck" not in buttons, (title, buttons)
    assert pages > 1, pages

def test_window_find_bar_and_result_dialog():
    """Ctrl+F abre la barra sobre la lista; elegir un resultado lleva la vista previa a su página. Al generar,
    un solo diálogo con los avisos, el tamaño y los botones."""
    from PySide6.QtWidgets import QPlainTextEdit, QPushButton

    root, window = visible_window()
    window.pdf = make_novel()
    window.show_book(convert.analyze(window.pdf))
    window.find()
    shown = window.find_bar.isVisible()
    window.find_entry.setText("hola")
    window.search_book()
    found = (window.find_count.text(), window.find_results.count())
    window.find_results.setCurrentRow(0)
    page = window.pdf_page
    window.close_find()
    hidden = not window.find_bar.isVisible() and not window.find_results.isVisible()
    out = TMP / "resultado.epub"
    out.write_bytes(b"x" * 2_000_000)
    window.result_dialog(str(out), ["sec1.xhtml: algo raro"])
    dialog = window.result_window
    texts = [w.toPlainText() for w in dialog.findChildren(QPlainTextEdit)]
    buttons = [b.text() for b in dialog.findChildren(QPushButton)]
    title = dialog.windowTitle()
    window.close()
    assert shown and found[1] >= 1 and found[0].startswith(f"{found[1]} resultado"), found
    assert page == window.book.blocks[window.matches[0][0]].page - 1 and hidden, page
    assert title == "EPUB guardado con avisos" and texts == ["sec1.xhtml: algo raro"], (title, texts)
    # «Validar con EPUBCheck» va en su fila, debajo: la de los botones ya llena 1093 px con un lector conectado
    assert buttons[:2] == ["Abrir EPUB", "Abrir carpeta"] and buttons[-2:] == ["Cerrar", "Validar con EPUBCheck"], \
        buttons


def test_window_result_dialog_phone_and_calibre():
    """«Enviar al móvil» siempre; «Añadir a Calibre» solo si Calibre está instalado, y lo abre con el EPUB."""
    import app
    from PySide6.QtWidgets import QPushButton

    import app

    out = TMP / "calibre.epub"
    out.write_bytes(b"x")
    root, window = visible_window()
    calibre, popen = app._calibre, app.subprocess.Popen
    launched, seen = [], []
    try:
        app.subprocess.Popen = lambda args, **kw: launched.append(args)
        for found in (None, "C:/Calibre2/calibre.exe"):
            app._calibre = lambda: found
            window.result_dialog(str(out))
            buttons = {b.text(): b for b in window.result_window.findChildren(QPushButton)}
            seen.append(("Enviar al móvil" in buttons, "Añadir a Calibre" in buttons))
            if "Añadir a Calibre" in buttons:
                buttons["Añadir a Calibre"].click()
            window.result_window.close()
    finally:
        app._calibre, app.subprocess.Popen = calibre, popen
    sent = app._sent_text(out)
    window.close()
    assert seen == [(True, False), (True, True)], seen
    assert launched == [["C:/Calibre2/calibre.exe", str(out)]], launched
    assert sent.startswith("Calibre · "), sent


def test_window_guide_names_serious_warnings_and_colors_follow_theme():
    """La barra inferior dice qué falta antes de generar («1 aviso grave…», en singular), y los colores de
    la lista cambian con el tema: en oscuro, sin grises de fondo claro."""
    from types import SimpleNamespace

    import app
    import theme

    B = convert.Block
    blocks = [B("heading", 1, "Capítulo 1"), B("p", 1, "Texto con letras rotas � aquí."), B("heading", 2, "Lado B"),
              B("p", 2, "Más texto.")]
    root, window = visible_window()
    window.show_book(convert.Book("Libro", "", b"", blocks, [0]))
    guide = window.quality_btn.text()
    light = window.tree.rows["2"].foreground(0).color().name()
    window.theme_var.set("Oscuro")
    window.apply_theme()
    dark = window.tree.rows["2"].foreground(0).color().name()
    window.close()
    theme.apply(__import__("qtk").app(), "Claro")
    one = app._quality_summary([SimpleNamespace(level="Grave")])
    two = app._quality_summary([SimpleNamespace(level="Grave")] * 2 + [SimpleNamespace(level="Menor")])
    assert guide.startswith("▲ 1 aviso grave: revísalo antes de generar"), guide
    assert (light, dark) == (theme.kind_colors(False)["subtitle"], theme.kind_colors(True)["subtitle"]), (light, dark)
    assert one == "1 grave" and two == "2 graves · 1 menor", (one, two)


def test_qtk_dialog_filters():
    import qtk

    assert qtk.filters([("PDF", "*.pdf"), ("Imagen", "*.jpg *.png")]) == "PDF (*.pdf);;Imagen (*.jpg *.png)"


def versioned_window():
    """Ventana con la novela de prueba abierta y sin versiones de otras pruebas."""
    import app

    root, window = visible_window()
    window.pdf = make_novel()
    app._versions_path(window.pdf).unlink(missing_ok=True)
    window.show_book(convert.analyze(window.pdf))
    return root, window


def book_text(window):
    return " ".join(plain(b.html) for b in window.book.blocks)


def test_window_versions_compare_and_restore():
    """Como commits de git: se guarda el estado, se compara con lo de ahora (dato, fila y palabras del texto) y
    se restaura sin perder nada: lo de antes de restaurar queda como versión, y se puede volver a ello."""
    import app

    root, window = versioned_window()
    item = first_title(window)
    original_cover = window.book.cover
    assert window.add_version("Primera")
    start, end = window.section_bounds(int(item))
    text = convert.section_text(window.book, start + 1, end)  # sin el título: solo cambia el párrafo
    assert "fin del primer parrafo" in text, text
    window.apply_edit((start + 1, end, text.replace("fin del primer parrafo", "final del primer parrafo")), item)
    window.tree.set(item, "title", "Capítulo uno")
    window.publisher_var.set("Grupo X")
    window.pdf_page = 2
    window.cover_from_page()
    first = app._load_versions(window.pdf)[0][0]
    segments = window.compare(first["state"], None)
    shown = "".join(t for t, _ in segments)
    assert "Grupo: (vacío) → «Grupo X»" in shown and "Capítulo uno" in shown and "Portada" in shown, shown
    assert ("fin", "old") in segments and ("final", "new") in segments, segments

    assert window.restore_version(0)
    back = (window.tree.set(item, "title"), window.publisher_var.get(), window.cover_choice, window.cover_label.text(),
            window.book.cover == original_cover, "final del primer" in book_text(window), window.history)
    names = [v["name"] for v in app._load_versions(window.pdf)[0]]
    assert back == ("Capitulo 1: Inicio", "", None, "Portada: la del PDF", True, False, []), back
    assert names == ["Primera", "Antes de restaurar otra versión"], names

    assert window.restore_version(1)  # la alternativa sigue ahí
    again = (window.tree.set(item, "title"), window.publisher_var.get(), window.cover_choice,
             "final del primer" in book_text(window))
    window.close()
    assert again == ("Capítulo uno", "Grupo X", ["page", 2], True), again
    reopened = app._read_review(window.pdf)
    assert reopened and reopened["publisher"] == "Grupo X", "la revisión no quedó con lo restaurado"


def test_window_version_after_each_new_epub():
    """Generar el EPUB deja una versión automática (la publicada); generar otra vez sin cambios no la repite."""
    import app

    root, window = versioned_window()
    window.saved(str(TMP / "publicado.epub"), [])
    window.saved(str(TMP / "publicado.epub"), ["un aviso"])  # solo cambian los avisos: no es otra versión
    window.publisher_var.set("Grupo Y")
    window.saved(str(TMP / "publicado.epub"), [])
    versions = app._load_versions(window.pdf)[0]
    changes = app._version_changes(versions[0]["state"], versions[1]["state"])
    window.close()
    assert [(v["name"], v["auto"]) for v in versions] == [("EPUB generado · publicado.epub", True)] * 2, versions
    assert changes == "1 dato", changes


def test_window_versions_dialog_saves_renames_and_deletes():
    import app

    root, window = versioned_window()
    window.versions()
    win = window.versions_window
    parts = win.parts
    parts["name"].setText("Antes de corregir")
    parts["save"].click()
    window.publisher_var.set("Grupo Z")
    parts["name"].setText("Con grupo")
    parts["save"].click()
    rows = [parts["table"].set(i, "name") for i in parts["table"].get_children()]
    parts["table"].selection_set("0")
    compared = parts["box"].toPlainText()
    parts["name"].setText("Sin grupo")
    parts["rename"].click()
    original = app.messagebox.askyesno
    app.messagebox.askyesno = lambda *args, **kwargs: True
    try:
        parts["table"].selection_set("1")
        parts["delete"].click()
    finally:
        app.messagebox.askyesno = original
    names = [v["name"] for v in app._load_versions(window.pdf)[0]]
    title = win.windowTitle()
    window.close()
    assert rows == ["Con grupo", "Antes de corregir"], rows  # la más nueva arriba, como git log
    assert "«Grupo Z»" in compared, compared
    assert names == ["Sin grupo"] and title == "Versiones · 1 versión", (names, title)


def test_damaged_versions_file_is_set_aside():
    """Un zip de versiones roto no se pisa: se aparta en .bak y se empieza otro."""
    import app

    root, window = versioned_window()
    path = app._versions_path(window.pdf)
    path.write_bytes(b"no es un zip")
    assert window.add_version("Nueva")
    status = window.status.text()
    versions = app._load_versions(window.pdf)[0]
    window.close()
    backup = path.with_name(path.name + ".bak")
    assert backup.read_bytes() == b"no es un zip" and backup.name in status, status
    assert [v["name"] for v in versions] == ["Nueva"], versions
    path.write_bytes(b"otro roto")  # el segundo .bak no pisa el primero
    assert app._load_versions(window.pdf)[1].name == path.name + ".2.bak" and backup.read_bytes() == b"no es un zip"


def test_version_of_another_analysis_is_not_restored():
    """Una versión con otros bloques (otro PDF u otra forma de leerlo) se compara, pero no se aplica."""
    import app

    root, window = versioned_window()
    window.add_version("Vieja")
    versions = app._load_versions(window.pdf)[0]
    versions[0]["state"]["blocks"] += 1
    app._write_versions(window.pdf, versions)
    window.publisher_var.set("Grupo X")
    errors = []
    original = app.messagebox.showerror
    app.messagebox.showerror = lambda title, message, **kwargs: errors.append(message)
    try:
        restored = window.restore_version(0)
    finally:
        app.messagebox.showerror = original
    shown = "".join(t for t, _ in window.compare(versions[0]["state"], None))
    publisher = window.publisher_var.get()
    window.close()
    assert restored is False and publisher == "Grupo X" and "otro análisis" in errors[0], (restored, errors)
    assert "No se compara el texto" in shown and "Grupo X" in shown, shown


def small_book():
    """Libro en memoria: título, párrafo con cursiva y nota, separador, párrafo centrado e ilustración."""
    blocks = [convert.Block("heading", 1, "Chapter 1: Start"),
              convert.Block("p", 1, 'He said <i>hello</i> to me.<span class="nota">A greeting</span>'),
              convert.Block("break", 1, "* * *"),
              convert.Block("p", 2, "Dear friend &amp; ally", align="center"),
              convert.Block("image", 2, image=b"jpeg"),
              convert.Block("p", 2, "1234")]
    return convert.Book("Libro", "Autor", b"", blocks, [0])


class FakeAI:
    """Hace de API de IA: traduce cada párrafo a "ES:" + texto; con short, la primera respuesta trae uno de menos."""

    def __init__(self, short=False):
        self.calls, self.short = [], short

    def __call__(self, url, headers, body):
        import json
        self.calls.append((url, headers, body))
        user = body["messages"][-1]["content"]
        data = json.loads(user[user.index("Paragraphs:") + len("Paragraphs:"):])
        out = {n: "ES:" + t for n, t in data.items()}
        if self.short:
            self.short = False
            out.pop(next(iter(out)))
        reply = "Aquí tienes:\n```json\n" + json.dumps(out, ensure_ascii=False) + "\n```"
        if "anthropic" in url:
            return {"content": [{"type": "text", "text": reply}]}
        return {"choices": [{"message": {"content": reply}}]}


def test_translate_units_and_section():
    """Se traducen títulos y párrafos con letras, con su prefijo aparte; ilustraciones y separadores quedan igual."""
    import translate

    book = small_book()
    units = translate.units(book, 0, len(book.blocks))
    assert [(k, p, t) for k, p, t in units] == [
        (0, "# ", "Chapter 1: Start"), (1, "", "He said <i>hello</i> to me.[Nota: A greeting]"),
        (3, convert.CENTER, "Dear friend & ally")], units
    done = {translate.key(units[0][2]): "Capítulo 1: Inicio", translate.key(units[2][2]): "Querido amigo\ny aliado"}
    text = translate.translated_section(book, 0, len(book.blocks), done)
    lines = text.split("\n\n")
    assert lines[0] == "# Capítulo 1: Inicio" and lines[1].startswith("He said") and lines[2] == "[Separador 2]", lines
    assert lines[3] == convert.CENTER + "Querido amigo y aliado" and lines[4] == "[Ilustración 4]", lines
    convert.edit_section(book, 0, len(book.blocks), text)
    assert [b.kind for b in book.blocks] == ["heading", "p", "break", "p", "image", "p"], book.blocks
    assert book.blocks[3].align == "center" and book.blocks[1].html.endswith('<span class="nota">A greeting</span>')


def test_translate_hides_inline_icons():
    """Un icono sin reconocer (convert.ICON) no va a la traducción como su PNG en base64: miles de letras que se
    pagan y que la IA puede romper. Va como «⟦1⟧», que la IA deja en su sitio, y al aplicar vuelve el icono; si el
    motor quitó la marca, el icono queda al final del párrafo en vez de perderse."""
    import translate

    tag = '<img class="icono" src="data:image/png;base64,iVBORw0KGgo=" alt=""/>'
    other = tag.replace("iVBORw0KGgo=", "QUJD")
    book = small_book()
    book.blocks[3].html = f"He looked {tag} at me {other} and left."
    units = translate.units(book, 0, len(book.blocks))
    text = units[2][2]
    assert text == "He looked ⟦1⟧ at me ⟦2⟧ and left.", units
    assert "⟦" in translate.instructions("en", "es")
    done = {translate.key(text): "Me miró ⟦2⟧ y ⟦1⟧ se fue."}
    lines = translate.translated_section(book, 0, len(book.blocks), done).split("\n\n")
    assert lines[3] == f"{convert.CENTER}Me miró {other} y {tag} se fue.", lines[3]
    done = {translate.key(text): "Me miró y se fue ⟦1⟧."}
    lines = translate.translated_section(book, 0, len(book.blocks), done).split("\n\n")
    assert lines[3] == f"{convert.CENTER}Me miró y se fue {tag}. {other}", lines[3]
    convert.edit_section(book, 0, len(book.blocks), "\n\n".join(lines))
    assert len(convert.ICON.findall(book.blocks[3].html)) == 2, book.blocks[3].html


def test_translate_ai_batches_split_and_marks():
    """La IA recibe lotes numerados con contexto y glosario; si devuelve un párrafo de menos, el lote se parte en dos
    y no se pierde ni se corre ninguno. Las marcas estropeadas («< i>», «[Note: ») se arreglan."""
    import translate

    fake = FakeAI(short=True)
    original = translate._post
    translate._post = fake
    try:
        texts = [f"Line {n}" for n in range(4)] + ["She <i>smiled</i>.[Nota: x]"]
        out = translate.translate("DeepSeek", texts, "en", "es", api_key="k", glossary=[["Subaru", "Subaru"]],
                                  context=[("Before", "Antes")])
        url, headers, body = fake.calls[0]
        assert out == ["ES:" + t for t in texts], out
        assert len(fake.calls) == 3 and url == "https://api.deepseek.com/chat/completions", fake.calls
        assert headers["Authorization"] == "Bearer k" and body["model"] == "deepseek-flash", (headers, body)
        system = body["messages"][0]["content"]
        assert "Subaru = Subaru" in system and "em dash" in system and "Before\n→ Antes" in body["messages"][1]["content"]
        assert "Line 1\n→ ES:Line 1" in fake.calls[2][2]["messages"][1]["content"], "la segunda mitad ve la primera"
        claude = FakeAI()
        translate._post = claude
        assert translate.translate("Claude", ["Hi"], "auto", "en", api_key="c") == ["ES:Hi"]
        assert claude.calls[0][1]["x-api-key"] == "c" and claude.calls[0][0].endswith("/v1/messages")
        assert "em dash" not in claude.calls[0][2]["system"]  # la raya es cosa del español
        assert translate._fix("[Nota: x]", "< I>Hola</ i>[Note: y]") == "<i>Hola</i>[Nota: y]"
        translate._post = lambda url, headers, body: {"choices": [{"message": {"content": "{}"}}]}
        try:
            translate.translate("OpenAI (ChatGPT)", ["Hi"], "en", "es", api_key="k")
            raise AssertionError("debía fallar")
        except translate.TranslateError as e:
            assert "no devolvió" in str(e), e
    finally:
        translate._post = original


def test_translate_google_deepl_and_errors():
    """Google: sin clave, por formulario. DeepL: clave «:fx» a la API gratuita, marcas como XML y contexto.
    401 da «la clave no es válida»; 429 se reintenta con espera y luego se rinde con un mensaje en español."""
    import io
    import urllib.error
    import urllib.parse
    import translate

    saved = translate._form, translate._post, translate._sleep
    gets, posts, sleeps = [], [], []
    translate._form = lambda url, fields: gets.append((url, fields)) or [[["Hola ", "Hello "], ["mundo", "world"]]]
    translate._post = lambda url, headers, body: posts.append((url, headers, body)) or {
        "translations": [{"text": t.replace("Tom", "Tomás")} for t in body["text"]]}
    translate._sleep = sleeps.append
    try:
        assert translate.translate("Google (gratis, sin clave)", ["Hello world"], "auto", "es") == ["Hola mundo"]
        query = urllib.parse.parse_qs(urllib.parse.urlparse(gets[0][0]).query)
        assert query["sl"] == ["auto"] and query["tl"] == ["es"] and gets[0][1] == {"q": "Hello world"}, gets
        out = translate.translate("DeepL", ["Tom & <i>Jerry</i>"], "en", "pt", api_key="abc:fx",
                                  context=[("Prev", "Ant")])
        url, headers, body = posts[0]
        assert out == ["Tomás & <i>Jerry</i>"], out
        assert url == "https://api-free.deepl.com/v2/translate" and headers["Authorization"] == "DeepL-Auth-Key abc:fx"
        assert body["text"] == ["Tom &amp; <i>Jerry</i>"] and body["target_lang"] == "PT-BR", body
        assert body["source_lang"] == "EN" and body["context"] == "Prev" and body["tag_handling"] == "xml", body

        def fail(code):
            def call(url, headers, body):
                raise urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(b"detalle"))
            return call

        for code, expected in ((401, "la clave no es válida"), (429, "Revisa la conexión"), (400, "error 400")):
            translate._post = fail(code)
            try:
                translate.translate("DeepL", ["Hi"], "en", "es", api_key="k")
                raise AssertionError("debía fallar")
            except translate.TranslateError as e:
                assert expected in str(e), (code, e)
        assert sleeps == [2, 4, 8], sleeps  # solo el 429 espera y reintenta
        try:
            translate.translate("Claude", ["Hi"], "en", "es")
            raise AssertionError("debía fallar")
        except translate.TranslateError as e:
            assert "Falta la clave de Claude" in str(e), e
    finally:
        translate._form, translate._post, translate._sleep = saved


def test_translate_gcloud_models_and_hints():
    """Google Cloud: API oficial por lotes con la clave en la URL. Google gratis con una página de captcha o sin
    respuesta: el aviso dice qué hacer. Un modelo que ya no existe se nombra, y «Buscar modelos» trae los de hoy."""
    import io
    import urllib.error
    import translate

    saved = translate._get, translate._post, translate._sleep, translate._form
    posts, gets = [], []
    translate._post = lambda url, headers, body: posts.append((url, body)) or {
        "data": {"translations": [{"translatedText": t.replace("Tom", "Tomás")} for t in body["q"]]}}
    translate._sleep = lambda s: None
    try:
        assert translate.translate("Google Cloud (clave)", ["Tom &amp; <i>Jerry</i>", "Hi"], "auto", "es",
                                   api_key="AIza") == ["Tomás &amp; <i>Jerry</i>", "Hi"]
        url, body = posts[0]
        assert url.endswith("/language/translate/v2?key=AIza") and body["format"] == "html", (url, body)
        assert body["q"][0] == "Tom &amp;amp; <i>Jerry</i>" and "source" not in body and body["target"] == "es"
        assert translate.batches(["x"] * 150, "gcloud") == [list(range(100)), list(range(100, 150))]

        def captcha(url, headers=None):
            raise ValueError("<html>captcha</html>")

        def down(url, headers=None):
            raise urllib.error.HTTPError(url, 429, "x", {}, io.BytesIO(b""))

        for fake in (captcha, down, lambda url, headers=None: {"raro": 1}):
            translate._form = fake
            try:
                translate.translate("Google (gratis, sin clave)", ["Hi"], "en", "es")
                raise AssertionError("debía fallar")
            except translate.TranslateError as e:
                assert "Google Cloud, DeepL o una IA" in str(e), e

        def no_model(url, headers, body):
            raise urllib.error.HTTPError(url, 404, "x", {}, io.BytesIO(b'{"error": "The model `gpt-9` does not exist"}'))

        translate._post = no_model
        try:
            translate.translate("OpenAI (ChatGPT)", ["Hi"], "en", "es", api_key="k", model="gpt-9")
            raise AssertionError("debía fallar")
        except translate.TranslateError as e:
            assert "«gpt-9» no existe" in str(e) and "Buscar modelos" in str(e), e
        translate._get = lambda url, headers=None: gets.append((url, headers)) or {
            "data": [{"id": "models/gemini-x"}, {"id": "b"}, {"id": "a"}, {"id": "b"}]}
        assert translate.models("Gemini", "g") == ["a", "b", "gemini-x"]
        assert translate.models("Claude", "c") == ["a", "b", "gemini-x"]
        assert gets[0] == ("https://generativelanguage.googleapis.com/v1beta/openai/models", {"Authorization": "Bearer g"})
        assert gets[1][0] == "https://api.anthropic.com/v1/models" and gets[1][1]["x-api-key"] == "c", gets
        translate.models("Compatible con OpenAI (Ollama, LM Studio…)", "", "http://127.0.0.1:1234/v1/")
        assert gets[2] == ("http://127.0.0.1:1234/v1/models", {}), gets
    finally:
        translate._get, translate._post, translate._sleep, translate._form = saved


def test_translate_batches_and_dpapi():
    """Lotes por número de párrafos y letras; la clave API cifrada no se lee en claro y vuelve igual."""
    import translate

    assert translate.batches(["a"] * 30, "google") == [list(range(25)), list(range(25, 30))]
    assert translate.batches(["x" * 5000, "y" * 5000, "z"], "openai") == [[0], [1, 2]]
    assert [len(b) for b in translate.batches(["w"] * 70, "openai")] == [40, 30]
    # El japonés crece al traducirse: pesa el triple y el lote lleva menos, para que la respuesta no se corte
    assert translate.batches(["あ" * 2000] * 3, "openai") == [[0], [1], [2]]
    assert translate.batches(["あ" * 2000] * 3, "deepl") == [[0, 1, 2]]
    secret = translate.protect("sk-secreta-ñ")
    assert secret and "sk-secreta" not in secret and translate.unprotect(secret) == "sk-secreta-ñ"
    assert translate.unprotect("no es base64 válido!") == "" and translate.protect("") == ""


def test_translate_doubts_and_second_pass():
    """Una traducción dudosa (sin ¿, con comillas en vez de raya, sin traducir, cortada, sin el término del glosario,
    sin las cursivas) se nota; la IA la repasa una vez con el motivo y solo se cambia si queda mejor."""
    import json
    import translate

    doubts = translate.doubts
    assert doubts("Where are you going?", "—¿Adónde vas?", "es") == []
    assert "apertura" in doubts("Where are you going?", "—Adónde vas?", "es")[0]
    assert "raya" in doubts('"Hello," she said.', '"Hola", dijo ella.', "es")[0]
    # Making Magic con DeepSeek: el original marca el diálogo con «» (la tipografía de la app) y un capítulo entero
    # salió con comillas mientras los demás iban con raya
    assert "raya" in doubts("«Hello,» she said.", "«Hola», dijo ella.", "es")[0]
    assert "even when" in translate.instructions("en", "es")
    assert doubts('"Hello," she said.', '"Hello," she said in English.', "en") == []
    assert "sin traducir" in doubts("He walked to the door.", "He walked to the door.", "es")[0]
    assert doubts("Subaru.", "Subaru.", "es") == []  # un nombre solo queda igual
    assert "original" in doubts("彼は言った。", "Él dijo 彼。", "es")[0]
    assert "corta" in doubts("x " * 60, "y", "es")[0] and "larga" in doubts("x " * 60, "y " * 200, "es")[0]
    assert doubts("彼は静かにドアを開けて、ゆっくりと部屋の中に入っていった。", "Abrió la puerta en silencio y entró "
                  "despacio en la habitación.", "es") == []
    assert "cursivas" in doubts("He <i>ran</i>.", "Corrió.", "es")[0] and doubts("He <i>ran</i>.", "<i>Corrió</i>.", "es") == []
    assert "Nota" in doubts("Hi.[Nota: a]", "Hola.", "es")[0]
    found = doubts("The Witch Cult came.", "La Secta de la Bruja llegó.", "es", [["Witch Cult", "Culto de la Bruja"]])
    assert found == ["el glosario pide «Culto de la Bruja» para «Witch Cult»"], found
    assert doubts("The Witches came.", "Las brujas llegaron.", "es", [["Witch", "Hechicera"]]) == []  # otra palabra
    # Witch vol. 1 con DeepSeek: el término concuerda en género y número, o lleva otro artículo, y está bien
    assert doubts("Enchanted Wings!", "¡Alas Encantadas!", "es", [["Enchanted", "Encantado"]]) == []
    assert doubts("Forget those Guugelheits.", "Olvida a esos Guugelheit.", "es", [["Guugelheits", "los Guugelheit"]]) == []
    assert doubts("Enchanted Wings!", "¡Alas Mágicas!", "es", [["Enchanted", "Encantado"]]) != []

    calls = []

    def fake(url, headers, body):
        user = body["messages"][-1]["content"]
        calls.append(user)
        data = json.loads(user[user.index("Paragraphs:") + len("Paragraphs:"):])
        fixed = "previous translation" in user
        out = {n: ("—¿" if fixed else "—") + t for n, t in data.items()}
        return {"choices": [{"message": {"content": json.dumps(out)}}]}

    original = translate._post
    translate._post = fake
    try:
        out = translate.translate("DeepSeek", ["Bien.", "Vienes?", "Claro."], "en", "es", api_key="k")
        assert out == ["—Bien.", "—¿Vienes?", "—Claro."], out
        assert len(calls) == 2 and "1: falta el signo de apertura" in calls[1] and '"1": "Vienes?"' in calls[1], calls
        assert "Bien." not in calls[1].split("Paragraphs:")[1], "solo se repasa el dudoso"
        # Si el repaso no mejora, queda la primera y no se pide más
        translate._post = lambda url, headers, body: calls.append(1) or fake(url, headers, {"messages": [
            {"content": body["messages"][-1]["content"].replace("previous translation", "")}]})
        del calls[:]
        assert translate.translate("DeepSeek", ["Vienes?"], "en", "es", api_key="k") == ["—Vienes?"]
        assert calls.count(1) == 2, calls
    finally:
        translate._post = original


def test_translate_inconsistent_terms():
    """Un término del original traducido de dos formas sale con cada forma y sus párrafos; un nombre que no se
    traduce, un plural o una mayúscula distinta y un término dentro de otro más largo no son incoherencias."""
    import translate

    pairs = [(f"a{n}", f"Then the Witch Cult attacked village {n}.", f"Entonces el Culto de la Bruja atacó la aldea {n}.")
             for n in range(8)]
    pairs += [(f"b{n}", f"Nobody saw the Witch Cult near gate {n}.", f"Nadie vio a la Secta de la Bruja junto al portón {n}.")
              for n in range(3)]
    pairs += [(f"c{n}", f"Later Subaru ran to house {n}.", f"Después Subaru corrió a la casa {n}.") for n in range(6)]
    pairs += [(f"d{n}", f"He feared the Witch on day {n}.", f"Temía a la Bruja el día {n}.") for n in range(4)]
    pairs += [(f"e{n}", f"All the Knights left on night {n}.", f"Todos los {'caballeros' if n % 2 else 'Caballero'} "
               f"se fueron la noche {n}.") for n in range(4)]
    found = dict(translate.inconsistent(pairs))
    assert list(found) == ["Witch Cult"], found
    forms = found["Witch Cult"]
    assert [(f, len(keys)) for f, keys in forms] == [("Culto de la Bruja", 8), ("Secta de la Bruja", 3)], forms
    assert forms[1][1] == ["b0", "b1", "b2"]
    assert translate.inconsistent(pairs[:8]) == []  # una sola forma
    # El glosario también da términos, aunque empiecen frase o vayan en minúscula
    ends = ["fluye por el río", "llena la sala", "arde en su mano", "se agota pronto", "vuelve despacio", "brilla hoy"]
    low = [(f"g{n}", f"mana does thing number {n}.", f"El {'maná' if n < 3 else 'poder mágico'} {end}.")
           for n, end in enumerate(ends)]  # frases distintas, como en un libro: solo el término se repite
    assert translate.inconsistent(low) == []
    assert [t for t, _ in translate.inconsistent(low, [["mana", "maná"]])] == ["mana"]


def test_translate_quality_of_translations():
    """Los avisos de texto del informe de calidad, sobre las traducciones: un signo sin pareja o inglés sin traducir
    salen con la clave de su párrafo; los de estructura (número suelto, línea repetida, párrafo partido) no."""
    import translate

    pairs = [("a", "—¿Adónde vas?"), ("b", "Abrió la puerta (y no la cerró."), ("c", "1234"),
             ("d", "empieza en minúscula."), ("e", "He walked to the door and then he was gone with the wind."),
             ("f", "Cartel"), ("g", "Cartel"), ("h", "Cartel"), ("i", "Dijo <i>hola</i> sin más.[Nota: un saludo]")]
    found = translate.quality(pairs)
    assert set(found) == {"b", "e"}, found
    assert found["b"][0][0] == "Signo sin pareja" and found["e"][0][0] == "Texto sin traducir", found
    assert "e" not in translate.quality(pairs, lang="en")
    assert translate.quality([]) == {}


def test_translate_review():
    """La IA revisora recibe cada original con su traducción, el glosario y la ficha, y solo cuenta lo que marca con
    una corrección distinta de lo que hay; la corrección conserva las marcas. Una respuesta rota parte el lote, y
    con un solo párrafo se da por no revisado. Un motor que no es IA no revisa."""
    import json
    import translate

    calls, replies = [], []

    def fake(url, headers, body):
        calls.append(body["messages"])
        reply = replies.pop(0) if replies else {}
        return {"choices": [{"message": {"content": reply if isinstance(reply, str) else json.dumps(reply)}}]}

    pairs = [("He ran.", "Corrió."), ("She fell. Then she cried.", "Ella se cayó."), ("He <i>ran</i> away.", "Huyó.")]
    original = translate._post
    translate._post = fake
    try:
        replies.append({"2": {"problem": "Omite  la segunda frase.", "fix": "Ella se cayó. Luego lloró."}})
        out = translate.review("DeepSeek", pairs, "en", "es", api_key="k", glossary=[["Witch", "Bruja"]],
                               style={"notes": "Emilia: chica, formal"})
        assert out == [None, {"problem": "Omite la segunda frase.", "fix": "Ella se cayó. Luego lloró."}, None], out
        system, user = calls[0][0]["content"], calls[0][1]["content"]
        assert "Witch = Bruja" in system and "Emilia: chica, formal" in system and "editor" in system
        # Making Magic: «los larguísimos anales de este mundo alterno» no se entiende; la revisión lo marca y la
        # traducción pide palabras de todos los días
        assert "bookish" in system and "everyday words" in translate.instructions("en", "es")
        sent = json.loads(user[user.index("Paragraphs:") + len("Paragraphs:"):])
        assert sent["2"] == {"source": "She fell. Then she cried.", "translation": "Ella se cayó."}, sent
        # Lo que no sirve se ignora: número que no existe, sin corrección, corrección igual a lo que hay
        replies.append({"9": {"problem": "x", "fix": "y"}, "1": {"problem": "sin arreglo"},
                        "2": {"problem": "igual", "fix": " Ella se  cayó. "}, "3": "mal"})
        assert translate.review("DeepSeek", pairs, "en", "es", api_key="k") == [None, None, None]
        # Las marcas de la corrección, arregladas como las de una traducción
        replies.append({"3": {"fix": "< i>Huyó</ I> lejos."}})
        out = translate.review("DeepSeek", pairs, "en", "es", api_key="k")
        assert out[2] == {"problem": "La IA propone otra traducción.", "fix": "<i>Huyó</i> lejos."}, out
        # Respuesta rota: el lote se parte; lo que sigue sin entenderse queda sin revisar
        del calls[:]
        replies[:] = ["no es JSON", "tampoco", {"1": {"problem": "Falta el sujeto.", "fix": "Él huyó."}}]
        out = translate.review("DeepSeek", pairs[:1] + pairs[2:], "en", "es", api_key="k")
        assert out == [False, {"problem": "Falta el sujeto.", "fix": "Él huyó."}] and len(calls) == 3, (out, len(calls))
        try:
            translate.review("Google (gratis, sin clave)", pairs, "en", "es")
            assert False, "Google no revisa"
        except translate.TranslateError as e:
            assert "Solo una IA" in str(e)
    finally:
        translate._post = original


def test_translate_style_chapter_and_rejected_params():
    """La instrucción lleva la variante del español, los honoríficos, la ficha de personajes y las correcciones a
    mano como ejemplo; el capítulo entero va al final de la instrucción (en Claude, marcado para la caché) salvo que
    quepa en el lote. Una API que rechaza «temperature» o «response_format» se usa sin ellos y no se le repiten."""
    import io
    import urllib.error
    import translate

    style = {"variant": "es-419", "honorifics": False, "notes": "Emilia: chica, formal",
             "examples": [("Good morning.", "—Buenos días.")]}
    text = translate.instructions("en", "es", [["Witch", "Bruja"]], style)
    assert "Latin American" in text and "Do not keep Japanese honorifics" in text and "vosotros" in text, text
    assert "Emilia: chica, formal" in text and "Good morning.\n→ —Buenos días." in text and "Witch = Bruja" in text
    plain = translate.instructions("en", "es")
    assert "Keep Japanese honorifics" in plain and "Latin American" not in plain and "gender" in plain, plain
    assert "European Spanish" in translate.instructions("en", "es", style={"variant": "es-ES"})
    assert "European Spanish" not in translate.instructions("es", "en", style={"variant": "es-ES"})

    fake, original = FakeAI(), translate._post
    translate._post = fake
    chapter = "\n".join(f"Chapter line {n}" for n in range(40))
    try:
        translate.translate("DeepSeek", ["Chapter line 3"], "en", "es", api_key="k", style=style | {"chapter": chapter})
        body = fake.calls[0][2]
        system = body["messages"][0]["content"]
        assert system.endswith(chapter) and "only as context" in system and "Emilia" in system, system[-300:]
        assert body["response_format"] == {"type": "json_object"} and body["temperature"] == 0.3, body
        translate.translate("Claude", ["Chapter line 3"], "en", "es", api_key="k", style={"chapter": chapter})
        body = fake.calls[1][2]
        assert body["system"][1]["cache_control"] == {"type": "ephemeral"} and body["system"][1]["text"].endswith(chapter)
        assert "response_format" not in body and "em dash" in body["system"][0]["text"], body
        translate.translate("DeepSeek", ["Uno", "Dos"], "en", "es", api_key="k", style={"chapter": "Uno\nDos"})
        assert "only as context" not in fake.calls[2][2]["messages"][0]["content"], "el capítulo ya iba en el lote"
        long = translate.translate("DeepSeek", ["Uno"], "en", "es", api_key="k",
                                   style={"chapter": "x" * (translate.CHAPTER + 1)})
        assert long == ["ES:Uno"] and "only as context" not in fake.calls[3][2]["messages"][0]["content"]

        seen = []

        def picky(url, headers, body):
            seen.append(sorted(k for k in ("temperature", "response_format") if k in body))
            for name in ("temperature", "response_format"):
                if name in body:
                    raise urllib.error.HTTPError(url, 400, "x", {}, io.BytesIO(
                        f'{{"error": "Unsupported parameter: {name}"}}'.encode()))
            return fake(url, headers, body)

        translate._post = picky
        assert translate.translate("OpenAI (ChatGPT)", ["Hi"], "en", "es", api_key="k", model="o9") == ["ES:Hi"]
        assert translate.translate("OpenAI (ChatGPT)", ["Hi"], "en", "es", api_key="k", model="o9") == ["ES:Hi"]
        assert seen == [["response_format", "temperature"], ["response_format"], [], []], seen
    finally:
        translate._post = original


def test_translate_google_lines_retry_after_and_propose():
    """Google gratis: varios párrafos en una petición, uno por línea; si devuelve otras líneas, de uno en uno. Un 429
    con «Retry-After» espera lo que dice el servidor. «Proponer con la IA» devuelve glosario y personajes limpios."""
    import io
    import json
    import urllib.error
    import translate

    saved = translate._form, translate._post, translate._sleep
    asked, sleeps = [], []
    translate._sleep = sleeps.append
    try:
        translate._form = lambda url, fields: asked.append(fields["q"]) or [[[
            "\n".join("ES:" + line for line in fields["q"].split("\n")), ""]]]
        assert translate.translate("Google (gratis, sin clave)", ["One", "Two", "Three"], "en", "es") == [
            "ES:One", "ES:Two", "ES:Three"]
        assert asked == ["One\nTwo\nThree"], asked
        del asked[:]
        translate._form = lambda url, fields: asked.append(fields["q"]) or [[["ES:" + fields["q"].replace("\n", " "), ""]]]
        assert translate.translate("Google (gratis, sin clave)", ["One", "Two"], "en", "es") == ["ES:One", "ES:Two"]
        assert asked == ["One\nTwo", "One", "Two"], asked

        fake, tries = FakeAI(), []

        def busy(url, headers, body):
            tries.append(1)
            if len(tries) == 1:
                raise urllib.error.HTTPError(url, 429, "x", {"Retry-After": "7"}, io.BytesIO(b""))
            return fake(url, headers, body)

        translate._post = busy
        assert translate.translate("DeepSeek", ["Hi"], "en", "es", api_key="k") == ["ES:Hi"] and sleeps == [7], sleeps

        reply = {"glossary": {"Witch Cult": "Culto de la Bruja", "": "x", "Roswaal": 3, "Dress": "Vestido",
                              "Elmina": "Elmina"},
                 "characters": {"Emilia": "chica, formal;\n trata de usted a todos"}}
        posts = []
        translate._post = lambda url, headers, body: posts.append(body) or {
            "choices": [{"message": {"content": "```json\n" + json.dumps(reply) + "\n```"}}]}
        terms, people = translate.propose("DeepSeek", "Emilia met the Witch Cult.", "en", "es", api_key="k",
                                          names=["Emilia", "Roswaal"], text="Her dress. Elmina came. Dress: G")
        assert terms == [["Witch Cult", "Culto de la Bruja"], ["Elmina", "Elmina"]] and people == ["Emilia: chica, formal; trata de usted a todos"]
        assert "Emilia, Roswaal" in posts[0]["messages"][1]["content"] and "JSON" in posts[0]["messages"][0]["content"]
        # Making Magic: «Lady Witch» salía «Dama Bruja»; los tratamientos, como los dice un lector en español
        assert "Señorita" in posts[0]["messages"][0]["content"], posts[0]["messages"][0]["content"]
        try:
            translate.propose("DeepL", "x", "en", "es", api_key="k")
            raise AssertionError("debía fallar")
        except translate.TranslateError as e:
            assert "solo los puede proponer una IA" in str(e), e
    finally:
        translate._form, translate._post, translate._sleep = saved


def wait_translation(root, win, seconds=10):
    import time
    end = time.time() + seconds
    while win.parts["work"]["thread"] is not None and time.time() < end:
        root.update()
        time.sleep(0.02)
    assert win.parts["work"]["thread"] is None, "la traducción no terminó"


def test_window_translate_apply_and_undo():
    """«Traducir…»: la IA traduce el libro entero, un párrafo se corrige a mano, «Aplicar al libro» cambia el texto
    con una versión de antes, los títulos siguen con su tipo y el idioma pasa al destino; Ctrl+Z lo deshace. La
    clave queda cifrada en las preferencias y lo traducido, guardado para la próxima vez."""
    import app
    import translate

    root, window = versioned_window()
    app._translation_path(window.pdf).unlink(missing_ok=True)
    kinds = [window.tree.set(i, "inc") for i in window.tree.get_children()]
    original = translate._post
    translate._post = fake = FakeAI()
    window.tr_glossary = [["Inicio", "Inicio"]]
    try:
        win = window.translate_book()
        parts = win.parts
        parts["engine"].setCurrentIndex(parts["engine"].findText("DeepSeek"))
        parts["engine"].activated.emit(parts["engine"].currentIndex())
        parts["key"].setText("sk-prueba")
        parts["dst"].setCurrentIndex(parts["dst"].findData("en"))
        parts["src"].setCurrentIndex(parts["src"].findData("es"))
        parts["run"]()
        wait_translation(root, win)
        assert "Inicio = Inicio" in fake.calls[0][2]["messages"][0]["content"], "el glosario no llegó a la IA"
        assert window.review_state()["tr_glossary"] == [["Inicio", "Inicio"]]
        table = parts["table"]
        states = {table.set(i, "state") for i in table.get_children()}
        assert states == {"Traducido"}, states
        row = next(i for i in table.get_children() if "fin del primer parrafo" in table.set(i, "orig"))
        table.selection_set(row)
        root.update()
        parts["translation"].setPlainText("Corregido <i>a mano</i>.")
        parts["apply"]()
        text = book_text(window)
        assert "ES:" in text and "Corregido a mano." in text and "fin del primer parrafo" not in text, text
        assert [window.tree.set(i, "inc") for i in window.tree.get_children()] == kinds
        assert window.tree.set(first_title(window), "title").startswith("ES:"), "el título de la lista no cambió"
        assert window.lang_var.get() == "en" and app._load_versions(window.pdf)[0][-1]["name"] == "Antes de traducir"
        assert {table.set(i, "state") for i in table.get_children()} == {"Aplicado"}
        saved = app._read_translation(window.pdf)
        assert saved["dst"] == "en" and any(v["hand"] and "a mano" in v["text"] for v in saved["done"].values())
        window.undo()
        assert "fin del primer parrafo" in book_text(window) and "ES:" not in book_text(window)
        parts["close"]()
        secret = window.settings["translate"]["keys"]["DeepSeek"]
        assert "sk-prueba" not in secret and translate.unprotect(secret) == "sk-prueba", secret
        again = window.translate_book()  # al volver, lo traducido sigue ahí: no se paga dos veces
        rows = again.parts["table"]
        found = [(rows.set(i, "state"), rows.set(i, "orig")) for i in rows.get_children()]
        assert {s for s, _ in found} == {"Traducido", "A mano"}, found
        again.parts["close"]()
    finally:
        translate._post = original
        window.close()


def test_window_glossary_proposal_updates_known_characters():
    """En el tomo 2 y siguientes, «Proponer con la IA» le pasa las notas que ya hay y amplía la de un personaje cuando el
    tomo cuenta algo nuevo de él (antes se saltaba a los conocidos). La nota ampliada sale en cursiva con la anterior
    en el tooltip; una igual a la que hay no cuenta; nada se guarda sin «Aceptar»."""
    import json
    import time
    import app
    import translate

    root, window = versioned_window()
    app._translation_path(window.pdf).unlink(missing_ok=True)
    original, asked = translate._post, []
    reply = {"glossary": {}, "characters": {"Teto": "chica alegre; en este tomo se vuelve la escolta de la bruja",
                                            "Beretta": "sirvienta, formal", "Liriel": "diosa, de tú"}}
    translate._post = lambda url, headers, body: asked.append(body) or {
        "choices": [{"message": {"content": json.dumps(reply)}}]}
    try:
        win = window.translate_book()
        parts = win.parts
        parts["engine"].setCurrentIndex(parts["engine"].findText("DeepSeek"))
        parts["engine"].activated.emit(parts["engine"].currentIndex())
        parts["key"].setText("k")
        parts["glossary"]()
        box = win.glossary_box
        box["add_person"]("Teto", "chica alegre")
        box["add_person"]("Liriel", "diosa, de tú")
        box["suggest"]()
        end = time.time() + 5
        while "Beretta" not in box["notes"]() and time.time() < end:
            root.update()
            time.sleep(0.02)
        item, same = box["people"].item(0, 1), box["people"].item(1, 1)
        got = (item.text(), item.font().italic(), item.toolTip(), same.font().italic(), box["note"].text())
        saved = window.tr_notes
        box["accept"]()
        accepted = window.tr_notes
    finally:
        translate._post = original
        root.destroy()
    sent = asked[0]["messages"][1]["content"]
    assert "Teto: chica alegre" in sent and "Liriel: diosa, de tú" in sent, sent
    assert got[0] == "chica alegre; en este tomo se vuelve la escolta de la bruja" and got[1] and not got[3], got
    assert "chica alegre" in got[2] and "1 personaje nuevo" in got[4] and "1 nota ampliada" in got[4], got
    assert "escolta" not in saved and "Teto: chica alegre; en este tomo se vuelve la escolta" in accepted, accepted


def test_window_translate_chapters_style_and_doubts():
    """Cada capítulo se traduce por su cuenta, viendo su capítulo entero y no el de otro; «Glosario y estilo…» guarda
    la ficha de personajes con la serie y la variante en las preferencias, y «Proponer con la IA» rellena las dos
    cajas. Lo corregido a mano va de ejemplo, y una traducción que la IA deja mal queda «Dudoso» con su motivo."""
    import json
    import threading
    from PySide6.QtWidgets import QPushButton
    import time
    import app
    import translate

    root, window = versioned_window()
    app._translation_path(window.pdf).unlink(missing_ok=True)
    original = translate._post
    translate._post = fake = FakeAI()
    try:
        win = window.translate_book()
        parts = win.parts
        parts["engine"].setCurrentIndex(parts["engine"].findText("DeepSeek"))
        parts["engine"].activated.emit(parts["engine"].currentIndex())
        parts["key"].setText("k")
        parts["src"].setCurrentIndex(parts["src"].findData("es"))
        parts["dst"].setCurrentIndex(parts["dst"].findData("en"))
        # Un libro largo se lee en cuatro trozos a la vez (la ventana lo toma al abrirse)
        window.book.blocks.append(convert.Block("p", 1, "Texto largo del libro. " * 2000))
        parts["glossary"]()
        box = win.glossary_box
        box["add_person"]("Emilia", "chica, formal")
        box["general"].setPlainText("Tono ligero")
        box["add_term"]("Witch", "Bruja")
        assert box["tabs"].tabText(0) == "Glosario (1)" and box["tabs"].tabText(1) == "Personajes (1)"
        box["variant"].setCurrentIndex(box["variant"].findData("es-419"))
        box["honorifics"].setChecked(False)
        reply = {"glossary": {"Culto": "Cult"}, "characters": {"Subaru": "chico, informal"}}
        gate, asked = threading.Event(), []
        translate._post = lambda url, headers, body: asked.append(body) or gate.wait(10) and {
            "choices": [{"message": {"content": json.dumps(reply)}}]}
        # Un libro largo se lee en cuatro trozos a la vez; mientras, el botón y la nota dicen cuánto va
        box["suggest"]()
        end = time.time() + 5
        while len(asked) < 4 and time.time() < end:
            root.update()
            time.sleep(0.02)
        window.book.blocks.pop()
        root.update()
        time.sleep(0.25)
        root.update()
        buttons = [b.text() for b in box["box"].findChildren(QPushButton)]
        assert len(asked) == 4 and "Leyendo el libro… 0 de 4" in buttons and "Van " in box["note"].text(), (
            len(asked), buttons, box["note"].text())
        gate.set()
        end = time.time() + 5
        while "Subaru" not in box["notes"]() and time.time() < end:
            root.update()
            time.sleep(0.02)
        assert box["notes"]() == "Tono ligero\nEmilia: chica, formal\nSubaru: chico, informal", box["notes"]()
        assert ["Culto", "Cult"] in box["pairs"]() and "1 término nuevo y 1 personaje nuevo" in box["note"].text()
        assert box["terms"].item(1, 2).text() == "0 veces" and box["tabs"].tabText(0) == "Glosario (2)"
        box["terms"].selectAll()  # la IA de pega no aplica el glosario: sin él no hay dudosos
        next(b for b in box["box"].findChildren(QPushButton) if b.text() == "Quitar" and b.isVisible()).click()
        assert box["pairs"]() == [] and box["tabs"].tabText(0) == "Glosario (0)", box["pairs"]()
        box["accept"]()
        translate._post = fake
        assert window.review_state()["tr_notes"] == "Tono ligero\nEmilia: chica, formal\nSubaru: chico, informal"
        assert "tr_notes" in app.INHERITED and window.settings["translate"]["variant"] == "es-419"
        assert window.settings["translate"]["honorifics"] is False

        table = parts["table"]
        row = next(i for i in table.get_children() if "fin del primer parrafo" in table.set(i, "orig"))
        table.selection_set(row)
        root.update()
        parts["translation"].setPlainText("Corrected by hand, as the editor wants it.")
        parts["run"]()
        wait_translation(root, win)
        sections = [s for s in window.translation_sections()
                    if translate.units(window.book, s[0], s[1])]
        assert len(sections) >= 2 and len(fake.calls) >= len(sections) - 1, (len(sections), len(fake.calls))
        systems = [body["messages"][0]["content"] for _, _, body in fake.calls]
        assert all("Subaru: chico, informal" in s and "Do not keep Japanese honorifics" in s for s in systems)
        assert all("Corrected by hand, as the editor wants it." in s for s in systems), "falta el ejemplo a mano"
        with_chapter = [s.split("only as context")[1] for s in systems if "only as context" in s]
        for _, _, body in fake.calls:  # el capítulo que acompaña a un lote es el suyo
            system, user = body["messages"][0]["content"], body["messages"][1]["content"]
            if "only as context" in system:
                first = json.loads(user[user.index("Paragraphs:") + len("Paragraphs:"):])["1"]
                assert first in system.split("only as context")[1], first
        assert table.set(row, "state") == "A mano" and {table.set(i, "state") for i in table.get_children()} == {
            "Traducido", "A mano"}, with_chapter

        # Una traducción sin el término del glosario: «Dudoso», y al elegirla se dice por qué
        other = next(i for i in table.get_children() if table.set(i, "state") == "Traducido")
        word = next(w for w in table.set(other, "orig").split() if len(w) > 3 and w.isalpha())
        window.tr_glossary = [[word, "Zzyzx"]]
        parts["merge"]({win.parts["done"] and next(k for k, v in parts["done"].items() if not v["hand"]
                                                    and v["text"] == table.set(other, "trans")): table.set(other, "trans")})
        assert table.set(other, "state") == "Dudoso", table.set(other, "state")
        table.selection_set(other)
        root.update()
        assert "Dudoso: el glosario pide «Zzyzx»" in parts["status"].text(), parts["status"].text()
        assert parts["doubt"].text().startswith("▲ Dudoso: el glosario pide «Zzyzx»"), parts["doubt"].text()

        # Filtros con su cuenta, buscador y «Anterior/Siguiente» entre lo que se ve
        chips, total = parts["chips"], len(table.get_children())
        visible = lambda: [i for i in table.get_children() if not table.rows[i].isHidden()]
        assert chips["all"].text() == f"Todos {total}" and chips["Dudoso"].text() == "Dudosos 1", chips["Dudoso"].text()
        assert chips["A mano"].text() == "A mano 1" and chips["done"].text() == f"Traducidos {total - 2}"
        chips["Dudoso"].click()
        assert visible() == [other], visible()
        chips["all"].click()
        parts["search"].setText("fin del primer")
        assert visible() == [row], visible()
        parts["search"].clear()
        table.selection_set(row)
        root.update()
        parts["step"](1)
        root.update()
        assert table.selection() == (table.get_children()[table.get_children().index(row) + 1],), table.selection()
        assert not parts["progress"].isVisible()
        parts["close"]()
    finally:
        translate._post = original
        window.close()


def test_window_translate_coherence():
    """«Coherencia…»: un término traducido de dos formas sale con ellas; al elegir una entra en el glosario de la
    serie y la lista deja a la vista, elegidos, los párrafos con la otra. Sin nada incoherente, lo dice."""
    import app
    import translate

    root, window = versioned_window()
    app._translation_path(window.pdf).unlink(missing_ok=True)
    texts = {f"Then the Witch Cult attacked village {n}.": f"Entonces el Culto de la Bruja atacó la aldea {n}."
             for n in range(5)}
    texts |= {f"Nobody saw the Witch Cult near gate {n}.": f"Nadie vio a la Secta de la Bruja junto al portón {n}."
              for n in range(2)}
    window.book.blocks += [convert.Block("p", 1, t) for t in texts]
    infos, saved_info = [], app.messagebox.showinfo
    app.messagebox.showinfo = lambda title, message, **_: infos.append(message)
    try:
        win = window.translate_book()
        parts = win.parts
        table, chips = parts["table"], parts["chips"]
        parts["coherence"]()
        assert infos and "nada traducido" in infos[-1], infos
        parts["merge"]({translate.key(a): b for a, b in texts.items()})
        parts["coherence"]()
        box = win.coherence_box
        assert [box["table"].set(i, "term") for i in box["table"].get_children()] == ["Witch Cult"]
        assert "«Culto de la Bruja» 5 · «Secta de la Bruja» 2" in box["table"].set("0", "forms")
        assert [box["forms"].itemText(n) for n in range(box["forms"].count())] == ["Culto de la Bruja",
                                                                                   "Secta de la Bruja"]
        box["use"]()
        assert window.tr_glossary == [["Witch Cult", "Culto de la Bruja"]], window.tr_glossary
        assert window.review_state()["tr_glossary"] == [["Witch Cult", "Culto de la Bruja"]]
        visible = [i for i in table.get_children() if not table.rows[i].isHidden()]
        assert [table.set(i, "orig") for i in visible] == [t for t in texts if "gate" in t], visible
        assert set(table.selection()) == set(visible) and "2 párrafos" in parts["status"].text(), parts["status"].text()
        # El glosario ya pide la forma elegida: los otros quedan «Dudoso», listos para «Retraducir selección»
        assert {table.set(i, "state") for i in visible} == {"Dudoso"}
        chips["all"].click()
        assert not any(item.isHidden() for item in table.rows.values())
        del infos[:]
        parts["done"].clear()
        parts["merge"]({translate.key(a): b.replace("Secta", "Culto") for a, b in texts.items()})
        parts["coherence"]()
        assert infos and "varias formas" in infos[-1], infos
        parts["close"]()
    finally:
        app.messagebox.showinfo = saved_info
        window.close()


def test_window_translate_spanish_warnings():
    """Con destino español, cada traducción lleva sus avisos del informe de calidad y las palabras que no están en
    el diccionario: la tarjeta dice cuántos, el filtro «Con avisos» las junta y al elegirla se leen. Una palabra
    añadida al diccionario de la serie deja de avisar. Con otro destino no hay avisos."""
    import time
    import app
    import translate

    root, window = versioned_window()
    app._translation_path(window.pdf).unlink(missing_ok=True)
    window.book.blocks += [convert.Block("p", 1, "He opened the door and did not close it."),
                           convert.Block("p", 1, "She wrote a single wrong word in the letter.")]
    started, saved_spell = [], app._start_spell_process
    app._start_spell_process = lambda: started.append(1)  # sin el proceso real: lo que diría se pone a mano
    try:
        win = window.translate_book()
        parts = win.parts
        table, chips = parts["table"], parts["chips"]
        assert parts["dst"].currentData() == "es"
        bad, typo = (next(i for i in table.get_children() if word in table.set(i, "orig")) for word in ("door", "letter"))
        parts["merge"]({translate.key(table.set(bad, "orig")): "Abrió la puerta (y no la cerró.",
                        translate.key(table.set(typo, "orig")): "Escribió una sola palbra mal en la carta."})
        parts["check"](later=False)
        assert started, "con traducciones al español, la ortografía arranca"
        assert table.rows[bad].data(2, app.WARN_ROLE) == 1 and table.rows[typo].data(2, app.WARN_ROLE) is None
        assert chips["warn"].text() == "Con avisos 1", chips["warn"].text()
        chips["warn"].click()
        assert [i for i in table.get_children() if not table.rows[i].isHidden()] == [bad]
        chips["all"].click()
        table.selection_set(bad)
        root.update()
        assert "▲ Signo sin pareja" in parts["doubt"].text(), parts["doubt"].text()
        # Lo que dice el proceso de ortografía: la palabra se subraya y la fila avisa
        parts["spell"]["unknown"].add("palbra")
        parts["check"](later=False)
        assert parts["look"].unknown == {"palbra"} and table.rows[typo].data(2, app.WARN_ROLE) == 1
        assert parts["warnings"][translate.key(table.set(typo, "orig"))] == ["No está en el diccionario: «palbra»"]
        assert chips["warn"].text() == "Con avisos 2"
        parts["add_word"]("palbra")
        assert "palbra" in window.words and table.rows[typo].data(2, app.WARN_ROLE) is None
        assert parts["look"].unknown == set() and chips["warn"].text() == "Con avisos 1"
        # Corregida a mano, el aviso se va (el informe nuevo llega del hilo)
        parts["translation"].setPlainText("Abrió la puerta y no la cerró.")
        table.selection_set(typo)
        end = time.time() + 5
        while table.rows[bad].data(2, app.WARN_ROLE) and time.time() < end:
            root.update()
            time.sleep(0.02)
        assert table.rows[bad].data(2, app.WARN_ROLE) is None and chips["warn"].text() == "Con avisos 0"
        # A otro idioma, el español no opina
        parts["translation"].setPlainText("Escribió (una sola palabra mal en la carta.")
        parts["dst"].setCurrentIndex(parts["dst"].findData("en"))
        parts["dst"].activated.emit(parts["dst"].currentIndex())
        assert not parts["warnings"] and chips["warn"].text() == "Con avisos 0"
        parts["close"]()
    finally:
        app._start_spell_process = saved_spell
        window.close()


def write_volume(folder, name, series, volume, done):
    """Un tomo ya traducido, como lo deja la app: su revisión (serie y volumen) y su archivo de traducción."""
    import json
    folder.mkdir(parents=True, exist_ok=True)
    pdf = folder / f"{name}.pdf"
    (folder / f"{name}.lnepub.json").write_text(json.dumps({"series": series, "volume": volume}), encoding="utf-8")
    (folder / f"{name}.lnepub.traduccion.json").write_text(
        json.dumps({"src": "en", "dst": "es", "done": done}, ensure_ascii=False), encoding="utf-8")
    return pdf


def test_translation_memory_of_series():
    """Lo traducido en otros tomos de la serie se encuentra en la misma carpeta y, apuntado al guardar, en cualquier
    otra; lo corregido a mano gana; un tomo de otra serie, uno borrado o un archivo roto no estorban, y sin serie no
    se lee ni se apunta nada. Un párrafo casi igual a uno de la memoria se reconoce; uno corto o distinto, no."""
    import os
    from time import time_ns
    import app
    import translate

    saved_settings = app.SETTINGS
    app.SETTINGS = TMP / f"ajustes-{time_ns()}.json"
    try:
        here, there = TMP / f"memoria-{time_ns()}", TMP / f"memoria-otra-{time_ns()}"
        entry = lambda text, hand=False: {"text": text, "hand": hand, "source": "Original of " + text}
        own = write_volume(here, "Serie 03", "Mi Serie", "3", {"k1": entry("propia")})
        write_volume(here, "Serie 01", "mi serie", "1", {"k1": entry("uno"), "k2": entry("dos a mano", True)})
        write_volume(here, "Otra 01", "Otra Serie", "1", {"k1": entry("ajena"), "k9": entry("ajena")})
        (here / "Rota.lnepub.json").write_text('{"series": "Mi Serie"}', encoding="utf-8")
        (here / "Rota.lnepub.traduccion.json").write_text("{esto no es JSON", encoding="utf-8")
        far = write_volume(there, "Serie 02", "Mi Serie", "2", {"k2": entry("dos nueva"), "k3": entry("tres")})
        os.utime(far.with_name("Serie 02.lnepub.traduccion.json"), (2_000_000_000, 2_000_000_000))  # el más reciente

        memory = app._series_memory(own, "Mi Serie")
        assert {k: (v["text"], v["volume"]) for k, v in memory.items()} == {"k1": ("uno", "1"), "k2": ("dos a mano", "1")}
        assert memory["k1"]["source"] == "Original of uno" and memory["k1"]["dst"] == "es"
        app._remember_translation(far, "Mi Serie")
        app._remember_translation(far, "Mi Serie")  # dos veces, una sola ruta
        assert len(app._memory_index()["mi serie"]) == 1
        memory = app._series_memory(own, " mi serie ")
        assert {k: (v["text"], v["volume"]) for k, v in memory.items()} == {
            "k1": ("uno", "1"), "k2": ("dos a mano", "1"), "k3": ("tres", "2")}, memory
        far.with_name("Serie 02.lnepub.traduccion.json").unlink()
        assert set(app._series_memory(own, "Mi Serie")) == {"k1", "k2"} and app._memory_index()["mi serie"] == []
        assert app._series_memory(own, "") == {} and app._series_memory(own, "Nadie") == {}
        app._remember_translation(own, "  ")
        assert "" not in app._memory_index()
    finally:
        app.SETTINGS = saved_settings

    old = "The knight drew his sword and looked at the dragon without any fear."
    sources = [old, "Something completely different that shares almost nothing with it.", "Short."]
    found = translate.similar("The knight drew her sword and looked at the dragon without fear.", sources)
    assert found and found[0] == old and 0.9 < found[1] < 1, found
    assert translate.similar("The weather was nice on that quiet morning in the village.", sources) is None
    assert translate.similar("Short!", sources) is None and translate.similar(old, [old]) is None


def test_window_translate_series_memory():
    """Al abrir «Traducir» en un tomo de una serie, un párrafo ya traducido en otro tomo sale «De la serie» con su
    traducción (y se guarda con este tomo); corregido pasa a «A mano». Un párrafo pendiente parecido a uno de otro
    tomo lo dice, y «Usar esta» pone esa traducción para corregirla."""
    from time import time_ns
    import app
    import translate

    root, window = versioned_window()
    app._translation_path(window.pdf).unlink(missing_ok=True)
    window.book.blocks += [convert.Block("p", 1, "The knight drew his sword and looked at the dragon without any fear."),
                           convert.Block("p", 1, "The princess opened the old gate and walked slowly into the garden.")]
    same, near = (b.html for b in window.book.blocks[-2:])
    older = near.replace("slowly", "quickly")
    write_volume(TMP / f"memoria-ventana-{time_ns()}", "Tomo 5", "Serie de prueba", "5", {
        translate.key(same): {"text": "El caballero desenvainó la espada y miró al dragón sin miedo.", "hand": False,
                              "source": same},
        translate.key(older): {"text": "La princesa abrió el viejo portón y entró deprisa en el jardín.", "hand": True,
                               "source": older}})
    app._remember_translation(next((TMP).glob("memoria-ventana-*/")) / "Tomo 5.pdf", "Serie de prueba")
    window.series_var.set("Serie de prueba")
    saved_spell = app._start_spell_process
    app._start_spell_process = lambda: None
    try:
        win = window.translate_book()
        parts = win.parts
        table, chips = parts["table"], parts["chips"]
        row, other = (next(i for i in table.get_children() if table.set(i, "orig") == text) for text in (same, near))
        assert table.set(row, "state") == "De la serie" and "desenvainó" in table.set(row, "trans")
        assert chips["De la serie"].text() == "De la serie 1" and "ya traducido" in parts["status"].text()
        saved = app._read_translation(window.pdf)["done"][translate.key(same)]
        assert saved["memory"] and saved["source"] == same and not saved["hand"], saved
        assert str(app._translation_path(window.pdf).resolve()) in app._memory_index()["serie de prueba"]
        table.selection_set(other)
        root.update()
        assert table.set(other, "state") == "Pendiente" and not parts["similar_row"].isHidden()
        assert "en el tomo 5" in parts["similar"].text() and "deprisa" in parts["similar"].text(), parts["similar"].text()
        parts["use_similar"]()
        assert table.set(other, "state") == "A mano" and "deprisa" in table.set(other, "trans")
        table.selection_set(row)
        root.update()
        assert parts["similar_row"].isHidden(), "solo un párrafo pendiente busca parecidos"
        parts["translation"].setPlainText("El caballero desenvainó su espada y miró al dragón sin temor.")
        table.selection_set(other)
        root.update()
        assert table.set(row, "state") == "A mano" and chips["De la serie"].text() == "De la serie 0"
        assert "memory" not in app._read_translation(window.pdf)["done"][translate.key(same)]
        parts["close"]()
    finally:
        app._start_spell_process = saved_spell
        window.close()


def test_window_translate_accept_all_review_fixes():
    """Making Magic con DeepSeek: la revisión marcó 9 de 117 párrafos y 8 de sus correcciones eran buenas; en un tomo
    entero son cientos, una a una. «Aceptar todas» pregunta con cuántas son y pone todas las correcciones; con «No»,
    nada cambia."""
    import json
    import app
    import translate

    root, window = versioned_window()
    app._translation_path(window.pdf).unlink(missing_ok=True)
    original, saved_ask = translate._post, app.messagebox.askyesno
    translate._post = FakeAI()
    asked, answer, flagged = [], [False], []

    def reviewer(url, headers, body):
        user = body["messages"][-1]["content"]
        data = json.loads(user[user.index("Paragraphs:") + len("Paragraphs:"):])
        out = {n: {"problem": "Calco.", "fix": "ARREGLADO " + v["translation"]} for n, v in data.items()
               if v["source"] in flagged}
        return {"choices": [{"message": {"content": json.dumps(out)}}]}

    app.messagebox.askyesno = lambda title, message, **_: asked.append(message) or answer[0]
    try:
        win = window.translate_book()
        parts = win.parts
        table = parts["table"]
        parts["src"].setCurrentIndex(parts["src"].findData("es"))
        parts["dst"].setCurrentIndex(parts["dst"].findData("en"))
        parts["engine"].setCurrentIndex(parts["engine"].findText("DeepSeek"))
        parts["engine"].activated.emit(parts["engine"].currentIndex())
        parts["key"].setText("k")
        parts["run"]()
        wait_translation(root, win)
        bad = [i for i in table.get_children() if len(table.set(i, "orig")) > 20][:2]
        flagged += [table.set(i, "orig") for i in bad]
        translate._post = reviewer
        parts["review"]()
        wait_translation(root, win)
        table.selection_set(bad[0])
        root.update()
        shown = not parts["accept_all_btn"].isHidden()
        before = [table.set(i, "trans") for i in bad]
        parts["accept_all"]()  # «No»: nada cambia
        kept = [table.set(i, "trans") for i in bad] == before
        answer[0] = True
        parts["accept_all"]()
        after = [(table.set(i, "trans")[:10], table.set(i, "state")) for i in bad]
        hidden = parts["accept_all_btn"].isHidden()
    finally:
        translate._post, app.messagebox.askyesno = original, saved_ask
        root.destroy()
    assert shown and kept and len(asked) == 2 and "2 párrafos" in asked[0], (shown, kept, asked)
    assert after == [("ARREGLADO ", "Revisado")] * 2 and hidden, after


def test_window_translate_review():
    """«Revisar con IA»: lo traducido que la IA marca queda «Con fallos» con su problema y su corrección, y lo demás
    «Revisado». «Aceptar corrección» la pone; «Descartar» deja lo que había; las dos lo dan por revisado. Una
    traducción corregida a mano mientras llega su revisión se queda sin ella. Al volver a abrir, todo sigue."""
    import json
    import threading
    import time
    import app
    import translate

    root, window = versioned_window()
    app._translation_path(window.pdf).unlink(missing_ok=True)
    original = translate._post
    translate._post = FakeAI()
    flagged, gate, asked = [], threading.Event(), []
    gate.set()

    def reviewer(url, headers, body):
        user = body["messages"][-1]["content"]
        data = json.loads(user[user.index("Paragraphs:") + len("Paragraphs:"):])
        asked.append(data)
        gate.wait(10)
        out = {n: {"problem": "Omite la segunda frase.", "fix": "ARREGLADO " + v["translation"]}
               for n, v in data.items() if v["source"] in flagged}
        return {"choices": [{"message": {"content": json.dumps(out)}}]}

    infos, saved_info = [], app.messagebox.showinfo
    app.messagebox.showinfo = lambda title, message, **_: infos.append(message)
    try:
        win = window.translate_book()
        parts = win.parts
        table, chips = parts["table"], parts["chips"]
        parts["src"].setCurrentIndex(parts["src"].findData("es"))
        parts["dst"].setCurrentIndex(parts["dst"].findData("en"))
        parts["review"]()  # Google, el motor por defecto, no revisa
        assert infos and "Solo una IA" in infos[-1], infos
        parts["engine"].setCurrentIndex(parts["engine"].findText("DeepSeek"))
        parts["engine"].activated.emit(parts["engine"].currentIndex())
        parts["key"].setText("k")
        parts["run"]()
        wait_translation(root, win)
        total = len(table.get_children())
        first, second, third, fourth = [i for i in table.get_children() if len(table.set(i, "orig")) > 20][:4]
        flagged.append(table.set(first, "orig"))
        translate._post = reviewer
        parts["review"]()
        wait_translation(root, win)
        assert all("source" in v and "translation" in v for data in asked for v in data.values())
        states = {i: table.set(i, "state") for i in table.get_children()}
        assert states[first] == "Con fallos" and set(states.values()) == {"Con fallos", "Revisado"}, states
        assert chips["Con fallos"].text() == "Con fallos 1" and chips["done"].text() == f"Traducidos {total - 1}"
        assert "1 párrafo con fallos" in parts["status"].text(), parts["status"].text()
        table.selection_set(first)
        root.update()
        assert "Revisión de la IA: Omite la segunda frase." in parts["doubt"].text()
        assert parts["fix"].toPlainText().startswith("ARREGLADO ES:") and not parts["fix"].isHidden()
        assert not parts["accept_btn"].isHidden()
        parts["accept"]()
        assert table.set(first, "trans").startswith("ARREGLADO ES:") and table.set(first, "state") == "Revisado"
        assert parts["accept_btn"].isHidden() and parts["fix"].isHidden()
        assert parts["translation"].toPlainText().startswith("ARREGLADO")
        del asked[:]
        parts["review"]()  # ya está todo revisado: no se gasta otra vez
        assert not asked and "sin revisar" in infos[-1], infos

        def choose(*iids):
            table.clearSelection()
            for iid in iids:
                table.rows[iid].setSelected(True)
            root.update()

        # Elegidos dos o más, se revisan otra vez; «Descartar» deja la traducción como estaba
        flagged[:] = [table.set(second, "orig")]
        choose(second, third)
        parts["review"]()
        wait_translation(root, win)
        assert sum(len(data) for data in asked) == 2 and table.set(second, "state") == "Con fallos"
        before = table.set(second, "trans")
        table.selection_set(second)
        root.update()
        parts["discard"]()
        assert table.set(second, "trans") == before and table.set(second, "state") == "Revisado"

        # Corregida a mano mientras la IA la mira: su revisión ya no vale
        flagged[:] = [table.set(third, "orig"), table.set(fourth, "orig")]
        gate.clear()
        choose(third, fourth)
        parts["review"]()
        table.selection_set(third)
        root.update()
        parts["translation"].setPlainText("Corrected by hand in the meantime.")
        table.selection_set(first)
        root.update()
        gate.set()
        wait_translation(root, win)
        assert table.set(third, "state") == "A mano" and table.set(fourth, "state") == "Con fallos"
        assert "review" not in parts["done"][translate.key(table.set(third, "orig"))]
        parts["close"]()
        again = window.translate_book()  # la revisión se guarda con la traducción
        rows = again.parts["table"]
        assert [rows.set(i, "state") for i in (first, second, third, fourth)] == [
            "Revisado", "Revisado", "A mano", "Con fallos"]
        again.parts["close"]()
    finally:
        translate._post = original
        app.messagebox.showinfo = saved_info
        window.close()


def test_window_translate_languages_per_paragraph():
    """Cada párrafo guarda de qué idioma a cuál se tradujo: la fila lo dice («ES → JA», en color de aviso si no es el
    destino elegido), el filtro de idiomas encuentra los traducidos por error, «Retraducir selección» con Ctrl+A solo
    toma los que se ven y «Aplicar al libro» avisa antes de meter otro idioma. Lo guardado sin idiomas toma los del
    archivo."""
    import json
    import app
    import translate

    root, window = versioned_window()
    path = app._translation_path(window.pdf)
    path.unlink(missing_ok=True)
    original, asked = translate._post, []
    translate._post = fake = FakeAI()
    saved_ask = app.messagebox.askyesno
    app.messagebox.askyesno = lambda title, message, **kwargs: asked.append(message) or False
    try:
        win = window.translate_book()
        parts = win.parts
        parts["engine"].setCurrentIndex(parts["engine"].findText("DeepSeek"))
        parts["engine"].activated.emit(parts["engine"].currentIndex())
        parts["key"].setText("k")
        parts["src"].setCurrentIndex(parts["src"].findData("es"))
        parts["dst"].setCurrentIndex(parts["dst"].findData("en"))
        table = parts["table"]
        first = table.get_children()[:2]
        for iid in first:  # dos párrafos, por error al japonés
            table.selection_set(iid) if iid == first[0] else table.rows[iid].setSelected(True)
        parts["dst"].setCurrentIndex(parts["dst"].findData("ja"))
        parts["run"](again=True)
        wait_translation(root, win)
        parts["dst"].setCurrentIndex(parts["dst"].findData("en"))
        parts["dst"].activated.emit(parts["dst"].currentIndex())
        parts["run"]()
        wait_translation(root, win)
        done = parts["done"]
        assert {(v["src"], v["dst"]) for v in done.values()} == {("es", "ja"), ("es", "en")}, done
        assert table.rows[first[0]].data(2, app.LANG_ROLE) == ("ES → JA", True)
        assert table.rows[table.get_children()[3]].data(2, app.LANG_ROLE) == ("ES → EN", False)
        combo = parts["langs"]
        labels = [combo.itemText(n) for n in range(combo.count())]
        assert labels[0] == "Todos los idiomas" and "Español → Japonés (2)" in labels, labels
        combo.setCurrentIndex(combo.findData("es>ja"))
        combo.activated.emit(combo.currentIndex())
        visible = [i for i in table.get_children() if not table.rows[i].isHidden()]
        assert visible == list(first), visible
        table.selectAll()  # Ctrl+A marca también lo que el filtro oculta: solo se retraduce lo que se ve
        calls = len(fake.calls)
        parts["run"](again=True)
        wait_translation(root, win)
        sent = json.loads(fake.calls[-1][2]["messages"][1]["content"].split("Paragraphs:")[1])
        assert len(fake.calls) == calls + 1 and len(sent) == 2, sent
        assert {(v["src"], v["dst"]) for v in done.values()} == {("es", "en")}
        # Uno a mano en japonés: conserva su idioma, y aplicar avisa antes de meterlo en un libro en inglés
        done[translate.key(table.set(first[0], "orig").removeprefix("# "))] = {"text": "日本語", "hand": True, "src": "es",
                                                                              "dst": "ja"}
        parts["apply"]()
        assert asked and "1 párrafo está traducido a otro idioma" in asked[0] and "Japonés: 1" in asked[0], asked
        assert window.lang_var.get() != "en", "aplicó pese a decir que no"
        parts["close"]()
        data = json.loads(path.read_text(encoding="utf-8"))
        data["done"] = {k: {"text": v["text"]} for k, v in data["done"].items()}  # como los guardados antes
        path.write_text(json.dumps(data), encoding="utf-8")
        assert {(v["src"], v["dst"]) for v in app._read_translation(window.pdf)["done"].values()} == {
            (data["src"], data["dst"])}
    finally:
        translate._post = original
        app.messagebox.askyesno = saved_ask
        window.close()


def test_window_translate_close_mid_batch():
    """Cerrar «Traducir» con un lote en camino no lo pierde: la ventana se oculta, lo termina, lo guarda y se
    cierra. Abrirla otra vez mientras tanto devuelve la misma. Salir de la app pregunta antes de perderlo."""
    import threading
    import time
    import shiboken6
    import app
    import translate

    root, window = versioned_window()
    app._translation_path(window.pdf).unlink(missing_ok=True)
    gate, fake, original, sent = threading.Event(), FakeAI(), translate._post, []
    translate._post = lambda url, headers, body: sent.append(1) or gate.wait(10) and fake(url, headers, body)
    groups = sum(bool(translate.units(window.book, a, b)) for a, b, _ in window.translation_sections())
    asked = []
    saved_ask = app.messagebox.askyesno
    app.messagebox.askyesno = lambda title, message, **kwargs: asked.append(message) or False
    try:
        win = window.translate_book()
        parts = win.parts
        parts["engine"].setCurrentIndex(parts["engine"].findText("DeepSeek"))
        parts["engine"].activated.emit(parts["engine"].currentIndex())
        parts["key"].setText("k")
        parts["src"].setCurrentIndex(parts["src"].findData("es"))
        parts["dst"].setCurrentIndex(parts["dst"].findData("en"))
        parts["run"]()
        total = len(parts["table"].get_children())
        end = time.time() + 5  # los lotes ya en camino: pausar antes de pedir nada no tiene nada que guardar
        while len(sent) < min(3, groups) and time.time() < end:
            time.sleep(0.01)
        assert groups <= 3 and len(sent) == groups, (groups, len(sent))
        assert window.close() is False and "traducción en curso" in asked[0], asked  # no sale: se perdería el lote
        parts["close"]()
        root.update()
        assert not win.isVisible() and parts["work"]["thread"] is not None
        assert window.translate_book() is win and win.isVisible(), "abrirla otra vez debía devolver la misma"
        parts["close"]()
        gate.set()
        wait_translation(root, win)
        root.update()
        done = app._read_translation(window.pdf)["done"]
        assert len(done) == total > 3, (len(done), total)
        assert not window.translations_running() and not shiboken6.isValid(win), "la ventana no se cerró"
    finally:
        gate.set()
        translate._post = original
        app.messagebox.askyesno = saved_ask
        window.close()


def test_window_translate_models_and_book_language():
    """Opciones → Traducción: «Buscar modelos» llena la lista con los de la API; Google Cloud pide clave y no
    modelo; Traducir, abierto, muestra el motor elegido. Un libro en inglés: el informe no da erratas del español,
    «Ortografía…» lo dice y el editor no revisa la ortografía."""
    import time
    import app
    import translate

    root, window = versioned_window()
    original = translate._get
    translate._get = lambda url, headers=None: {"data": [{"id": "deepseek-v4-pro"}, {"id": "deepseek-flash"}]}
    try:
        tr = window.translate_book().parts
        parts = window.open_options("Traducción").parts
        parts["engine"].setCurrentIndex(parts["engine"].findText("DeepSeek"))
        parts["engine"].activated.emit(parts["engine"].currentIndex())
        parts["key"].setText("k")
        parts["models"]()
        end = time.time() + 5
        while parts["model"].count() < 2 and time.time() < end:
            root.update()
            time.sleep(0.02)
        assert [parts["model"].itemText(n) for n in range(parts["model"].count())] == ["deepseek-flash",
                                                                                     "deepseek-v4-pro"]
        assert parts["model"].currentText() == "deepseek-flash" and "2 modelos" in parts["status"].text()
        parts["engine"].setCurrentIndex(parts["engine"].findText("Google Cloud (clave)"))
        parts["engine"].activated.emit(parts["engine"].currentIndex())
        assert parts["key"].isVisible() and not parts["model"].isVisible()
        parts["accept"]()
        root.update()
        assert "Google Cloud" in tr["summary"].text() and not tr["key"].isVisible(), tr["summary"].text()
        tr["close"]()
    finally:
        translate._get = original
    item = first_title(window)
    start, end = window.section_bounds(int(item))
    text = convert.section_text(window.book, start + 1, end)
    window.apply_edit((start + 1, end, text + "\n\nHaber si llegas pronto."), item)
    kinds = lambda: {i.kind for i in window.quality_report()}
    assert "aver" in kinds()
    window.lang_var.set("en")
    assert "aver" not in kinds(), "un libro en inglés no tiene erratas del español"
    infos = []
    saved_info = app.messagebox.showinfo
    app.messagebox.showinfo = lambda title, message, **kwargs: infos.append(message)
    started = []
    saved_spell = app._start_spell_process
    app._start_spell_process = lambda: started.append(1)
    try:
        window.spelling()
        editor = window.edit_text()
        assert infos and "solo revisa libros en español" in infos[0] and not started, (infos, started)
        editor.win.done(0)
    finally:
        app.messagebox.showinfo = saved_info
        app._start_spell_process = saved_spell
        window.close()


def no_modal_dialogs():
    """Sin pantalla, un aviso modal bloquearía la prueba: los que ninguna prueba espera se apuntan y responden «No»."""
    import qtk

    def show(icon, title, message, buttons, parent):
        print("aviso no previsto:", title, "·", (message or "").splitlines()[:1])

    qtk._MessageBox._show = staticmethod(show)


def test_style_guide_tokens_icons_and_no_loose_colors():
    """docs/ui-style-guide.md: los tokens existen, ningún icono es un emoji (Windows 10 y 11 lo dibujan distinto y
    no sigue el tema) y los colores salen de theme.py. Se mira el código fuente: sin pantalla no hay fuentes."""
    import re
    import theme

    assert set(theme.SPACE) == {"tight", "gap", "pad", "section"} and set(theme.FONT) == {"body", "title", "big"}
    assert set(theme.DIALOG) == {"s", "m", "l"} and all(w > 0 and h > 0 for w, h in theme.DIALOG.values())
    # Qt ignora U+FE0E: un glifo que Windows trata como emoji sale a color aunque se pida como texto.
    # Excepción: U+1F5CE (documento) sale monocromo, comprobado dibujándolo con la fuente de Windows
    emoji = re.compile("[🀀-🗍🗏-🫿⚠⏮⏭⚙⛓❗⚓]")
    assert not [k for k, v in theme.ICONS.items() if emoji.search(v)], theme.ICONS
    allowed = {"app.py": {"#1f6fd1", "#2e9d4a", "#f08c00"}, "qtk.py": {"#f08c00"},
               "editor.py": {"#2a62c8", "#888888", "#2e9d4a"}}  # marcas que no cambian con el tema
    for name, colors in allowed.items():
        text = (Path(__file__).parent / name).read_text(encoding="utf-8")
        assert not emoji.findall(text), (name, emoji.findall(text))
        loose = set(re.findall(r"#[0-9a-fA-F]{6}\b", text)) - colors
        assert not loose, (name, loose)


if __name__ == "__main__":
    failed = 0
    no_modal_dialogs()
    # prefijo lnepub-: el hook de limpieza borra lo que quede si las pruebas se cortan
    with tempfile.TemporaryDirectory(prefix="lnepub-test-", ignore_cleanup_errors=True) as tmp:
        TMP = Path(tmp)
        import app
        app.SETTINGS = TMP / "ajustes.json"  # nunca las preferencias reales del usuario
        for name, fn in list(globals().items()):
            if name.startswith("test_"):
                try:
                    fn()
                    print("ok", name)
                except Exception:
                    failed += 1
                    print("FAIL", name)
                    traceback.print_exc()
    sys.exit(1 if failed else 0)
