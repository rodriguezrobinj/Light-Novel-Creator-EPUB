"""Pruebas de convert.py con PDFs sintéticos. Ejecutar: python test_convert.py"""
import re
import sys
import tempfile
import traceback
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pymupdf

import convert

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


def epub_file(path, name):
    with zipfile.ZipFile(path) as z:
        return z.read(name).decode()


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
    p.insert_image(p.rect, pixmap=pix, keep_proportion=False, overlay=False)  # fondo decorativo
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


def test_scene_breaks_keep_their_original_form():
    """Cada separador guarda lo que traía el PDF: el texto de un separador escrito, la imagen de un adorno."""
    text_breaks = [b for b in convert.analyze(make_novel()).blocks if b.kind == "break"]
    ornament_breaks = [b for b in convert.analyze(make_ornament_pdf()).blocks if b.kind == "break"]
    assert [b.html for b in text_breaks] == ["* * *"], [b.html for b in text_breaks]
    assert len(ornament_breaks) == 1 and pymupdf.Pixmap(ornament_breaks[0].image).width == 10


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


def visible_window():
    """Ventana mapeada pero transparente: el Treeview solo calcula posiciones si está en pantalla."""
    import tkinter as tk

    import app

    app.SETTINGS = TMP / f"ajustes-{time_ns()}.json"  # cada ventana sin preferencias de otra prueba
    root = tk.Tk()
    root.attributes("-alpha", 0.0)
    root.geometry("780x560+0+0")
    window = app.App(root)
    root.update()
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
        return "".join(z.read(n).decode() for n in z.namelist() if n.endswith(".xhtml"))


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
    caption = window.pdf_caption.cget("text")
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
    bottom = window.root.winfo_rooty() + window.root.winfo_height()
    shown = [(w.winfo_ismapped(), w.winfo_rooty() + w.winfo_height() <= bottom) for w in (window.gen_btn, window.progress)]
    image_height = window.pdf_view.image.height() if window.pdf_view.image else 0
    window.close()
    assert shown == [(1, True), (1, True)], shown
    assert 0 < image_height < 560, image_height  # se ajustó al alto disponible


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
    preview = (window.epub_view.itemcget("message", "text"), window.epub_view.image)
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
    boxes = canvas.find_withtag("title")
    coords = canvas.coords(boxes[0]) if boxes else []
    size = (int(canvas.cget("width")), int(canvas.cget("height")))  # el marco donde va centrada la página
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
    pdf_caption = window.pdf_caption.cget("text")
    first = window.epub_caption.cget("text")
    window.epub_step(1)
    second = window.epub_caption.cget("text")
    window.epub_step(-5)  # no baja de la primera
    back = window.epub_caption.cget("text")
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
    styles = [window.tree.item(item, "tags")]
    for _ in range(2):
        window.on_click(SimpleNamespace(x=x + w // 2, y=y + h // 2))
        styles.append(window.tree.item(item, "tags"))
    window.close()
    assert [tuple(s) for s in styles] == [("chapter",), ("subtitle",), ("cut",)], styles


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
    light = sum(window.epub_view.image.get(2, 2))
    window.dark_var.set(True)
    window.draw_epub()
    dark = sum(window.epub_view.image.get(2, 2))
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
    marks = {n: window.strip.itemcget(f"b{n}", "outline") for n in (1, 2, 4)}  # páginas 2, 3 y 5
    window.jump(3)  # página 4: tiene "Capitulo 2: Final"
    root.update()
    caption = window.pdf_caption.cget("text")
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
    message = window.pdf_view.itemcget("message", "text")
    sheets = [len(view.find_withtag("sheet")) for view in (window.pdf_view, window.epub_view)]
    placeholders = len(window.strip.find_withtag("placeholder"))
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
        sizes.add((window.views.winfo_width(), window.epub_view.winfo_width(), window.epub_view.winfo_height(),
                   window.tree.winfo_width()))
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
    click(art)    # Ilustración -> Quitar
    window.tree.selection_set(art)
    root.update()
    message = window.epub_view.itemcget("message", "text")
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
    assert types == (app.IMG, app.DROPPED), types
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
    """Cada clic rota Capítulo → Subtítulo → Quitar → Capítulo. Tk manda el segundo clic rápido
    a <Double-1>: también cuenta."""
    from types import SimpleNamespace

    import app

    root, window = visible_window()
    window.tree.insert("", "end", iid="0", values=(app.ON, "Uno", 1))
    root.update()
    x, y, w, h = window.tree.bbox("0", "inc")
    click = SimpleNamespace(x=x + w // 2, y=y + h // 2)
    states = []
    for handler in (window.on_click, window.on_double_click, window.on_click):
        handler(click)
        states.append(window.tree.set("0", "inc"))
    root.destroy()
    assert states == [app.OFF, app.CUT, app.ON], states


def test_window_title_editor_closes_on_scroll():
    """El cuadro de edición no sigue al scroll: al desplazarse se guarda y se cierra."""
    from tkinter import ttk
    from types import SimpleNamespace

    import app

    root, window = visible_window()
    for i in range(60):
        window.tree.insert("", "end", iid=str(i), values=(app.ON, f"Titulo {i}", i))
    root.update()
    x, y, w, h = window.tree.bbox("0", "title")
    window.on_double_click(SimpleNamespace(x=x + w // 2, y=y + h // 2))
    editor = [c for c in window.tree.winfo_children() if isinstance(c, ttk.Entry)]
    assert len(editor) == 1
    editor[0].delete(0, "end")
    editor[0].insert(0, "Nuevo titulo")
    window.tree.yview_moveto(1.0)
    root.update()
    left = [c for c in window.tree.winfo_children() if isinstance(c, ttk.Entry)]
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
        assert float(window.progress.cget("value")) == 5
        window.finish(("error", "fallo"))
        value = float(window.progress.cget("value"))
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
    import tkinter as tk

    import app

    root = tk.Tk()
    root.withdraw()
    window = app.App(root)
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
    import tkinter as tk

    import app

    root = tk.Tk()
    root.withdraw()
    window = app.App(root)
    shown = []
    original = app.messagebox.showerror
    app.messagebox.showerror = lambda title, message: shown.append(message)
    try:
        window.run(os._exit, (3,), lambda _: None)
        deadline = time.time() + 30
        while not shown and time.time() < deadline:
            root.update()
            time.sleep(0.05)
        unlocked = window.open_btn.instate(["!disabled"])
    finally:
        app.messagebox.showerror = original
        root.destroy()
    assert shown and "inesperadamente" in shown[0], shown
    assert unlocked


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
    window.tree.focus_force()  # Tk entrega las teclas al widget con el foco
    seen = []
    for item, key in ((title, "s"), (title, "Q"), (title, "i"), (title, "c"), (image, "q"), (image, "s"), (image, "i")):
        window.tree.selection_set(item)
        root.update()
        window.tree.event_generate(f"<KeyPress-{key}>")
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
                window.style_var.get(), window.description, window.cover_choice, window.status.cget("text"))
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
        label = window.cover_label.cget("text")
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
    _, removed = app._plan(book, window.rows())
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
    bound = window.tree.bind("<Control-z>")  # el atajo; pulsarlo de verdad depende del foco de la ventana
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


def test_window_new_volume_inherits_series_data():
    folder = series_folder()
    review_volume_one(folder)
    root, window = visible_window()
    window.pdf = folder / "Mi Serie - Volumen 02.pdf"
    window.show_book(convert.analyze(window.pdf))
    got = (window.publisher_var.get(), window.translator_var.get(), window.style_var.get(), window.replacements,
           window.description, window.volume_var.get(), window.tree.set(first_title(window), "title"))
    status = window.status.cget("text")
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
        status = window.status.cget("text")
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
    status = window.status.cget("text")
    window.close()
    assert rows == {"Capitulo 1: Inicio": app.ON, "Agradecimientos": app.CUT, "Capitulo 2: Final": app.ON}, rows
    assert "1 secciones del grupo en Quitar" in status, status


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


def test_window_find_moves_previews_to_the_match():
    root, window = visible_window()
    window.pdf = make_group_pdf()
    window.show_book(convert.analyze(window.pdf))
    root.update()
    k = next(k for k, b in enumerate(window.book.blocks) if "final de la historia" in b.html)
    window.show_match(k, 3, "final de la historia")
    root.update()
    selected = window.tree.set(window.tree.selection()[0], "title")
    caption = window.pdf_caption.cget("text")
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


def test_check_epub_reports_problems():
    out = TMP / "revisar.epub"
    convert.write_epub(sectioned_book(), [(0, "Cap 1"), (6, "Cap 2")], out)
    assert convert.check_epub(out) == []
    broken = TMP / "roto.epub"
    with zipfile.ZipFile(out) as src, zipfile.ZipFile(broken, "w") as dst:
        for name in src.namelist():
            data = src.read(name)
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
    window.close()
    app.SETTINGS = settings
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    window = app.App(root)
    got = (window.style_var.get(), window.gray_var.get(), window.recent_menu.entrycget(0, "label"),
           window.settings["folder"])
    root.destroy()
    assert loaded
    assert got == ("Moderno", True, "novela.pdf", str(pdf.parent.resolve())), got


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


if __name__ == "__main__":
    failed = 0
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
