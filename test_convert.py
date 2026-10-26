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


def test_chapter_title_split_into_number_and_name():
    out = TMP / "titulo-partido.epub"
    convert.write_epub(styled_book(), [(0, "Capítulo 3: La Guarida"), (4, "Epílogo")], out)
    text = epub_text(out)
    assert '<h1><span class="num">Capítulo 3</span> <span class="name">La Guarida</span></h1>' in text, text
    assert "<h1>Epílogo</h1>" in text  # sin número ni nombre: una sola línea
    assert "Capítulo 3: La Guarida" in epub_file(out, "OEBPS/nav.xhtml")  # el índice conserva el título entero


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


if __name__ == "__main__":
    failed = 0
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        TMP = Path(tmp)
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
