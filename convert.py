"""Convierte PDFs de novelas ligeras en EPUB con texto fluido."""
import collections
import html
import os
import re
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pymupdf

BAND = 0.12     # franja superior/inferior (fracción de la altura) donde viven encabezados y pies
REPEAT = 0.30   # fracción de páginas en que una línea debe repetirse para ser encabezado o pie
KEYWORDS = re.compile(
    r"(cap[ií]tulo|pr[oó]logo|ep[ií]logo|interludio|historia paralela|palabras de cierre"
    r"|extra|parte|chapter|prologue|epilogue|afterword|interlude)\b", re.I)
TOC_TITLE = re.compile(r"(contenidos?|[ií]ndice|tabla de contenidos?|contents|table of contents):?", re.I)
DOT_LEADER = re.compile(r"\.{4,}\s*\d+$")
SCENE_BREAK = re.compile(r"[\s☆★*＊◆◇●○■□♦♢※✦✧❖◈•·#~◎◯]+")
END_PUNCT = (".", "!", "?", "…", '"', "'", "»", "”", "’", ":", ")")
BAD_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿­]")


@dataclass
class Row:
    text: str     # texto plano
    html: str     # texto con <i>/<b>, escapado para XHTML
    x0: float
    x1: float
    y: float      # línea base
    size: float
    bold: bool


@dataclass
class Block:
    kind: str           # "p" | "heading" | "image" | "break"
    page: int           # página de origen (empieza en 1)
    html: str = ""
    image: bytes = b""  # JPEG si kind == "image"


@dataclass
class Book:
    title: str
    author: str
    cover: bytes        # JPEG
    blocks: list
    candidates: list    # índices en blocks de los títulos de capítulo detectados


def _bold(s):
    return bool(s["flags"] & pymupdf.TEXT_FONT_BOLD) or "bold" in s["font"].lower()


def _italic(s):
    font = s["font"].lower()
    return bool(s["flags"] & pymupdf.TEXT_FONT_ITALIC) or "italic" in font or "oblique" in font


def _row(spans):
    spans.sort(key=lambda s: s["origin"][0])
    texts, parts = [], []
    for s in spans:
        t = BAD_XML.sub("", s["text"])
        if texts and not texts[-1].endswith(" ") and not t.startswith(" "):
            texts.append(" ")
            parts.append(" ")
        texts.append(t)
        part = html.escape(t, quote=False)
        if _italic(s):
            part = f"<i>{part}</i>"
        if _bold(s):
            part = f"<b>{part}</b>"
        parts.append(part)
    main = max(spans, key=lambda s: len(s["text"].strip()))
    return Row(" ".join("".join(texts).split()), "".join(parts).strip(),
               spans[0]["bbox"][0], spans[-1]["bbox"][2], spans[0]["origin"][1],
               round(main["size"], 1), all(_bold(s) for s in spans))


def _rows(page):
    """Agrupa los spans por línea base: el texto justificado parte una línea en varios spans."""
    spans = [s for b in page.get_text("dict")["blocks"] for line in b.get("lines", [])
             for s in line["spans"] if s["text"].strip()]
    rows, group = [], []
    for s in sorted(spans, key=lambda s: s["origin"][1]):
        if group and abs(s["origin"][1] - group[0]["origin"][1]) > 2:
            rows.append(_row(group))
            group = []
        group.append(s)
    if group:
        rows.append(_row(group))
    return rows


def _norm(text):
    return re.sub(r"\d+", "#", " ".join(text.lower().split()))


def _strip_margins(pages, heights):
    """Quita encabezados y pies: líneas de las franjas que se repiten en muchas páginas."""
    def in_band(r, h):
        return r.y < h * BAND or r.y > h * (1 - BAND)

    counts = collections.Counter()
    for rows, h in zip(pages, heights):
        counts.update({_norm(r.text) for r in rows if in_band(r, h)})
    repeated = {k for k, n in counts.items() if n >= max(2, REPEAT * len(pages))}
    repeated.add("#")  # números de página sueltos
    return [[r for r in rows if not (in_band(r, h) and _norm(r.text) in repeated)]
            for rows, h in zip(pages, heights)]


def _is_heading(r, body):
    if len(r.text) > 120:
        return False
    return r.size >= body * 1.3 or (r.bold and (r.size >= body * 1.1 or bool(KEYWORDS.match(r.text))))


def _is_toc(rows):
    return bool(TOC_TITLE.fullmatch(rows[0].text)) or sum(bool(DOT_LEADER.search(r.text)) for r in rows) >= 3


def _first_group(rows):
    """Primer título de una página que solo tiene títulos (el resto son duplicados decorativos)."""
    group = [rows[0]]
    for r in rows[1:]:
        if abs(r.size - rows[0].size) >= 0.5:
            break
        group.append(r)
    return group


def _image_coverage(page):
    area = page.rect
    return sum(abs(pymupdf.Rect(i["bbox"]) & area) for i in page.get_image_info()) / abs(area)


def _render(page):
    return page.get_pixmap(dpi=150).tobytes("jpg", jpg_quality=85)


def _join(a, b):
    if re.search(r"\w-$", a) and b[:1].islower():
        return a[:-1] + b
    return f"{a} {b}"


def analyze(pdf_path, progress=None):
    """Lee el PDF y lo devuelve como bloques, con los títulos de capítulo candidatos."""
    try:
        doc = pymupdf.open(pdf_path)
    except Exception as e:
        raise ValueError(f"No se pudo abrir el PDF: {e}") from e
    with doc:
        if doc.needs_pass:
            raise ValueError("El PDF está protegido con contraseña.")
        n = doc.page_count
        pages = []
        for i, page in enumerate(doc):
            pages.append(_rows(page))
            if progress:
                progress(i + 1, 2 * n)
        pages = _strip_margins(pages, [page.rect.height for page in doc])
        if not any(pages):
            raise ValueError("El PDF no tiene texto (parece escaneado). Hace falta OCR, que no está soportado.")

        sizes = collections.Counter(r.size for rows in pages for r in rows for _ in r.text)
        body = sizes.most_common(1)[0][0]
        gaps = collections.Counter()
        indents = total = 0
        for rows in pages:
            normal = [r for r in rows if r.size == body]
            gaps.update(round(b.y - a.y) for a, b in zip(normal, normal[1:]) if 0 < b.y - a.y < body * 3)
            if normal:
                left = min(r.x0 for r in normal)
                total += len(normal)
                indents += sum(r.x0 > left + body * 0.8 for r in normal)
        line_gap = gaps.most_common(1)[0][0] if gaps else body * 1.2
        indent_style = indents > total * 0.05  # los párrafos se marcan con sangría

        blocks = []
        para, short, last_text = None, False, ""
        for i, (page, rows) in enumerate(zip(doc, pages)):
            num = i + 1
            if progress:
                progress(n + num, 2 * n)
            headings = [r for r in rows if _is_heading(r, body)]
            # ponytail: solo ilustraciones a página completa; las pequeñas dentro del texto se descartan
            if not headings and sum(len(r.text) for r in rows) < 200 and _image_coverage(page) > 0.5:
                blocks.append(Block("image", num, image=_render(page)))
                para = None
                continue
            if not rows or _is_toc(rows):
                continue
            if len(headings) == len(rows):
                rows = _first_group(rows)
            normal = [r for r in rows if not _is_heading(r, body)]
            left = min((r.x0 for r in normal), default=0)
            right = max((r.x1 for r in normal), default=0)
            prev = None
            for r in rows:
                if _is_heading(r, body):
                    if prev is not None and _is_heading(prev, body) and abs(prev.size - r.size) < 0.5:
                        blocks[-1].html += " " + html.escape(r.text)
                    else:
                        blocks.append(Block("heading", num, html.escape(r.text)))
                    para = None
                elif SCENE_BREAK.fullmatch(r.text):
                    blocks.append(Block("break", num))
                    para = None
                else:
                    indented = r.x0 > left + body * 0.8
                    if para is None or indented or short:
                        new = True
                    elif prev is None:  # primera línea de la página: ¿sigue el párrafo anterior?
                        new = not indent_style and last_text.endswith(END_PUNCT)
                    else:
                        new = r.y - prev.y > line_gap * 1.3
                    if new:
                        para = Block("p", num, r.html)
                        blocks.append(para)
                    else:
                        para.html = _join(para.html, r.html)
                    short = r.x1 < right - (right - left) * 0.2
                    last_text = r.text
                prev = r

        if blocks and blocks[0].kind == "image" and blocks[0].page == 1:
            cover = blocks.pop(0).image
        else:
            cover = _render(doc[0])

        outline = [(title, p) for level, title, p in doc.get_toc() if level == 1]
        if outline:
            chosen = set()
            for title, p in outline:
                k = next((k for k, b in enumerate(blocks) if b.kind == "heading" and b.page == p), None)
                if k is None:
                    k = next((k for k, b in enumerate(blocks) if b.page >= p), len(blocks))
                    blocks.insert(k, Block("heading", p, html.escape(title)))
                chosen.add(id(blocks[k]))
            candidates = [k for k, b in enumerate(blocks) if id(b) in chosen]
        else:
            candidates = [k for k, b in enumerate(blocks) if b.kind == "heading"]

        meta = doc.metadata or {}
        return Book(meta.get("title") or Path(pdf_path).stem, meta.get("author") or "", cover, blocks, candidates)
