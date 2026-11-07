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


def visible_window():
    """Ventana Qt sin pantalla (QT_QPA_PLATFORM=offscreen), con su tamaño: la lista calcula posiciones."""
    import app

    app.SETTINGS = TMP / f"ajustes-{time_ns()}.json"  # cada ventana sin preferencias de otra prueba
    window = app.App(app.Window())
    window.theme_var.set("Claro")
    window.apply_theme()
    root = Root(window)
    window.root.show()
    root.geometry("780x560+0+0")
    return root, window


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
    app.convert.quality_report = lambda book: calls.append(1) or original(book)
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


def jpeg(width, height):
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), False)
    pix.set_rect(pix.irect, (200, 120, 40))
    pix.set_rect(pymupdf.IRect(0, 0, width // 2, height // 3), (20, 60, 200))
    return pix.tobytes("jpg", jpg_quality=95)


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
    """La revisión se guarda al cerrar y vuelve al abrir el mismo PDF; si el PDF cambia, no."""
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

    root, window = visible_window()
    window.pdf = TMP / "novela.pdf"
    window.show_book(convert.analyze(window.pdf))
    restored = (window.tree.set(item, "inc"), window.tree.set(item, "title"), window.publisher_var.get(),
                window.style_var.get(), window.description, window.cover_choice, window.status.text())
    window.close()
    assert restored == (app.OFF, "Capítulo uno", "Grupo X", "Moderno", "Sinopsis.", ["page", 2],
                        "Revisión anterior recuperada"), restored

    root, window = visible_window()
    window.pdf = make_novel()  # PDF nuevo en la misma ruta: la revisión guardada ya no vale
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
    seen.append(sheet.content().split(chr(10) * 2)[-1])      # solo texto, sin negrita
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
                    ["Dijo", "hola, y se fue."], 2, "Pegado de fuera", 1, 1], seen


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
    indicator = editor.chapter_btn.text()
    editor.zoom(-50)  # al 50 %: la página cabe y va en el medio del panel
    root.update()
    view, page = editor.scroll.viewport().geometry(), editor.canvas.geometry()
    centered = abs(page.center().y() - view.height() / 2) <= 2 and abs(page.center().x() - view.width() / 2) <= 2
    editor.mark_changed()
    labels = [editor.chapter_list.item(n).text() for n in range(editor.chapter_list.count())]
    editor.chapter_list.itemClicked.emit(editor.chapter_list.item(labels.index("Capítulo 1: El inicio")))
    shows_first = lines()[0] == "# Capítulo 1: El inicio" and editor.chapter_btn.text().find("Capítulo 1 de 2") > 0
    sheet.document().find("Capítulo 1: El inicio").insertText("Capítulo 1: El comienzo")
    editor.mark_changed()
    marked = any(editor.chapter_list.item(n).text().endswith("•") for n in range(editor.chapter_list.count()))
    editor.step_section(1)  # ▶: el capítulo siguiente
    on_second = editor.current == window.section_bounds(int(second))
    sheet.goto_source(int(second) + 1)  # el primer párrafo del segundo capítulo
    sheet.textCursor().insertText("Nuevo. ")
    editor.open_section(*window.section_bounds(int(first)))
    kept = lines()[0] == "# Capítulo 1: El comienzo"  # lo editado sigue al volver
    editor.page = window.book.blocks[int(second)].page - 2
    editor.turn(1)  # hojeando el PDF hasta la página del segundo capítulo, la hoja va a él
    synced = editor.current == window.section_bounds(int(second))
    editor.save()
    rows = {i: (window.tree.set(i, "inc"), window.tree.set(i, "ok")) for i in window.tree.get_children()}
    title = window.tree.set(first, "title")
    paragraph = window.book.blocks[int(second) + 1].html
    edits = list(window.edits)
    window.undo()
    undone = ([(b.kind, b.html, b.page) for b in window.book.blocks] == blocks_before
              and window.tree.set(first, "title") == "Capítulo 1: El inicio")
    window.close()
    assert opened and indicator.startswith("☰  Capítulo 2 de 2 · "), (opened, indicator)
    assert centered, (view, page)
    assert shows_first and marked and on_second and kept and synced, (shows_first, marked, on_second, kept, synced)
    assert title == "Capítulo 1: El comienzo" and paragraph.startswith("Nuevo. "), (title, paragraph)
    assert rows == before, (rows, before)
    assert len(edits) == 1 and edits[0][1] - edits[0][0] == 1, edits  # solo el párrafo cambiado
    assert undone


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
    agranda y ◀ ▶ hojean. Sección vacía y libro sin PDF: sin errores."""
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
    editor.turn(1)  # ▶ desde una ilustración: la página siguiente a la suya
    seen.append(editor.shown)
    editor.open_section(0, len(window.book.blocks))  # hojear llevó la hoja al capítulo de esa página
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
    assert seen == [("page", 4), True, ("image", 0), ("page", 3), ("page", 3), ("page", 4), ("page", 4), None], seen
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
    window.style_var.set("Moderno")
    window.gray_var.set(True)
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
    recent = window.recent_list.item(0).text()  # pantalla vacía: el tomo y su estado
    window.root.on_close = None
    window.close()
    import qtk
    import theme
    theme.apply(qtk.app(), "Claro")  # las demás pruebas, en claro
    assert loaded
    assert got == ("Moderno", True, "novela.pdf", str(pdf.parent.resolve()), "Oscuro", True), got
    assert recent.startswith("novela") and ("Revisado" in recent or "EPUB" in recent), recent


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
             "Onee-san, ayuda.", "La onee-san sonrió.", "Hassan saludó."]
    blocks = [convert.Block("heading", 1, "Capítulo 1")] + [convert.Block("p", 1, t) for t in texts]
    book = convert.Book("Libro", "", b"", blocks, [0])
    report = [i for i in convert.quality_report(book) if i.kind == "honorific"]
    assert [(i.block, i.level) for i in report] == [(4, "Menor"), (5, "Menor"), (6, "Menor"), (9, "Menor")], \
        [(i.block, i.title, i.problem) for i in report]
    assert "«Subaru-kun»" in report[0].problem and "«Subaru kun»" in report[0].problem, report[0].problem
    for issue in sorted(report, key=lambda i: -i.edit[0]):
        convert.edit_section(book, *issue.edit)
    assert [book.blocks[k].html for k in (4, 5, 6, 9)] == [
        "Hola, Subaru-kun.", "<i>Subaru-kun</i> dijo.", "Subaru-kun es raro.", "Senpai, ¿qué haces?"], \
        [b.html for b in book.blocks]


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
    assert before == "⚠ 1 por revisar" and ignored == ("✓ Sin avisos", [["number", "12"]], ()), (before, ignored)
    assert listed == (("0",), "No ignorar") and after == ("⚠ 1 por revisar", []), (listed, after)


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
    """Ortografía y «Añadir página» se cierran al abrir otro PDF (si no, cambiarían el libro nuevo); con un
    trabajo en curso, «Ortografía…» lo dice en vez de no hacer nada."""
    import time

    pdf = TMP / "Otra - Volumen 1.pdf"
    make_novel().replace(pdf)
    root, window = visible_window()
    window.show_book(convert.Book("Libro", "", b"", [convert.Block("heading", 1, "Capítulo 1"),
                                                     convert.Block("p", 1, "Ella dijó que sí.")], [0]))
    window.done = lambda result: None  # un trabajo en curso
    window.spelling()
    busy = window.status.text()
    window.done = None
    window.spelling_dialog([("dijó", 1, ["dijo"])])
    window.add_page()
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
    assert (opened, left) == (2, 0), (opened, left)


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
    state = {"edits": [["image", 1, str(picture)], ["image", 2, str(TMP / "no-existe.png")], [0, 0, "# Créditos\n\nGracias."]]}
    done = app._replay(book, state)
    assert done == state["edits"], done
    assert [b.kind for b in book.blocks] == ["heading", "p", "heading", "image", "image"], book.blocks
    assert pymupdf.Pixmap(book.blocks[3].image).width == 20 and book.blocks[4].image == JPEG, book.blocks
    assert book.candidates == [2], book.candidates


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
    assert button == "⚠ 3 por revisar", button
    assert listed == ["Párrafo partido", "Número suelto", "Número suelto"], listed
    assert shown == ("Ella abrió la puerta y dijo que ¶\nnadie vendría.",
                     "Ella abrió la puerta y dijo que nadie vendría.", ["¶\n"]), shown
    assert label == "Arreglar todos los de este tipo (2)", label
    assert "12" not in fixed[0] and "13" not in fixed[0] and fixed[1] == ["Párrafo partido"], fixed
    assert fixed[2] == "⚠ 1 por revisar", fixed
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
    window.apply_state({"font": "Mi Letra.ttf", "font_file": str(font)})
    shown, option = window.font_box.currentText(), window.write_options()["font"]
    window.apply_state({"font": "Otra.ttf", "font_file": str(TMP / "Otra.ttf")})
    fallback, status = window.font_var.get(), window.status.text()
    import app
    original = app.filedialog.askopenfilename
    try:
        picked = []
        for answer in (str(font), ""):  # elegir el archivo; después, cancelar: se queda la anterior
            app.filedialog.askopenfilename = lambda **kw: answer
            window.font_box.setCurrentText("Otra…")
            window.font_changed()
            picked.append((window.font_var.get(), window.font_box.currentText()))
    finally:
        app.filedialog.askopenfilename = original
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
    panel del PDF del editor dependen de ella. Lo cambiado o nuevo toma la del párrafo anterior."""
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
    assert pages == [1, 1, 3, 3], pages


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
    assert book.blocks[start].rect == first.rect and not book.blocks[start + 1].rect, book.blocks[start + 1]


def test_window_keeps_a_copy_of_a_review_that_no_longer_matches():
    """Si el análisis cambia (otra versión del programa), la revisión vieja no se pisa al cerrar: queda en .bak."""
    import json

    import app

    root, window = visible_window()
    window.pdf = make_novel()
    window.show_book(convert.analyze(window.pdf))
    window.set_kind(first_title(window), app.OFF)
    window.close()
    review = app._review_path(window.pdf)
    state = json.loads(review.read_text(encoding="utf-8"))
    state["blocks"] += 1  # como si otra versión del análisis hubiera dado otros bloques
    review.write_text(json.dumps(state), encoding="utf-8")

    root, window = visible_window()
    window.pdf = TMP / "novela.pdf"
    window.show_book(convert.analyze(window.pdf))
    status = window.status.text()
    window.close()
    backup = review.with_name(review.name + ".bak")
    assert backup.is_file() and json.loads(backup.read_text(encoding="utf-8")) == state, "se perdió la revisión vieja"
    assert backup.name in status, status


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
    book, twins, report = app._analyze(str(window.pdf))
    found = convert.duplicate_images
    original = convert.pymupdf.Pixmap
    decoded = []
    convert.duplicate_images = lambda b: 1 / 0  # ya viene del proceso de trabajo
    try:
        window.show_book(book, twins, report)
        convert.pymupdf.Pixmap = lambda *args: decoded.append(args) or original(*args)
        window.fill_tree(window.rows())
    finally:
        convert.duplicate_images, convert.pymupdf.Pixmap = found, original
    window.close()
    assert twins == found(book) and not decoded, (twins, len(decoded))


def test_window_warns_when_the_review_cannot_be_saved():
    """Si la revisión no se puede escribir (OneDrive la bloquea, carpeta de solo lectura), se avisa y no
    queda un temporal; la revisión anterior sigue entera."""
    import app

    root, window = visible_window()
    window.pdf = make_novel()
    window.show_book(convert.analyze(window.pdf))
    review = app._review_path(window.pdf)
    review.unlink(missing_ok=True)
    review.mkdir()  # en su sitio, una carpeta: el reemplazo falla
    try:
        window.save_review()
        status = window.status.text()
    finally:
        review.rmdir()
    window.close()
    assert "No se pudo guardar la revisión" in status, status
    assert not list(TMP.glob("*.json.tmp")), "quedó un temporal"


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
    assert "kepub" in app.INHERITED and "kepub" in app.PREFS


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
    assert buttons[:2] == ["Abrir EPUB", "Abrir carpeta"] and buttons[-1] == "Cerrar", buttons


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
    assert guide.startswith("⚠ 1 aviso grave: revísalo antes de generar"), guide
    assert (light, dark) == (theme.kind_colors(False)["subtitle"], theme.kind_colors(True)["subtitle"]), (light, dark)
    assert one == "1 grave" and two == "2 graves · 1 menor", (one, two)


def test_qtk_dialog_filters():
    import qtk

    assert qtk.filters([("PDF", "*.pdf"), ("Imagen", "*.jpg *.png")]) == "PDF (*.pdf);;Imagen (*.jpg *.png)"


def no_modal_dialogs():
    """Sin pantalla, un aviso modal bloquearía la prueba: los que ninguna prueba espera se apuntan y responden «No»."""
    import qtk

    def show(icon, title, message, buttons, parent):
        print("aviso no previsto:", title, "·", (message or "").splitlines()[:1])

    qtk._MessageBox._show = staticmethod(show)


if __name__ == "__main__":
    failed = 0
    no_modal_dialogs()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
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
