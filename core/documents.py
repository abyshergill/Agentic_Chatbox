"""Reading documents as text and writing Word documents.

Reads PDF, Word (.docx, .doc, .rtf), OpenDocument (.odt, .ods, .odp), PowerPoint (.pptx), Excel (.xlsx, .xlsm, .xls),
Parquet, Jupyter notebooks, HTML, and any text file (CSV, Markdown, code, JSON, logs...) in any common encoding.

Reading is shared by the assistant's read_file tool, the editor, chat attachments and files added as context, so every
part of the app understands the same formats. Writing turns Markdown-style text (headings, lists, tables, bold,
italics, code) into a formatted .docx, which is much easier for a model to produce reliably than Word's own format.
"""

from __future__ import annotations

import io
import os
import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path

from .textcodec import decode, looks_binary

# Formats that need converting to text, and are shown read-only in the editor. Everything else is read as text.
DOCUMENT_TYPES = {".pdf", ".docx", ".doc", ".rtf", ".odt", ".odp", ".ods", ".xlsx", ".xlsm", ".xls", ".pptx", ".parquet"}
HTML_TYPES = {".html", ".htm"}


class DocumentError(ValueError):
    """A document could not be read. The message is safe to show."""


def kind_of(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in DOCUMENT_TYPES:
        return "document"
    if suffix in HTML_TYPES:
        return "html"
    return "text"


def extract_text(path: Path, limit: int, *, html_as_text: bool = True) -> tuple[str, bool]:
    """Returns (text, truncated). Raises DocumentError for unreadable or unsupported binary files."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        text = _pdf(path, limit)
    elif suffix == ".docx":
        text = _docx(path)
    elif suffix in {".odt", ".odp"}:
        text = _odf_text(path)
    elif suffix in {".doc", ".rtf"}:
        text = _textutil(path)
    elif suffix in {".xlsx", ".xlsm"}:
        text = _xlsx(path, limit)
    elif suffix in {".xls", ".ods", ".parquet"}:
        text = _table_text(path, limit)
    elif suffix == ".pptx":
        text = _pptx(path)
    elif suffix == ".ipynb":
        text, truncated = _plain(path, limit * 4)
        return _notebook(text, limit)
    else:
        text, truncated = _plain(path, limit)
        if suffix in HTML_TYPES and html_as_text:
            text = html_to_text(text)
        return text, truncated
    return text[:limit], len(text) > limit


def _plain(path: Path, limit: int) -> tuple[str, bool]:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as handle:
        data = handle.read(limit + 1)
    if looks_binary(data):
        raise DocumentError(f"'{path.name}' is a binary file, not text, so it cannot be read")
    # UTF-8, UTF-16 (Excel "Unicode Text"), Windows-1252 and Latin-1 files are all understood.
    text = decode(data[: limit + 3], partial=len(data) > limit)
    return text[:limit], len(data) > limit


def _pdf(path: Path, limit: int) -> str:
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        pages, total = [], 0
        for number, page in enumerate(reader.pages, 1):
            text = page.extract_text() or ""
            pages.append(f"--- page {number} of {len(reader.pages)} ---\n{text}")
            total += len(text)
            if total > limit:
                break
    except Exception as exc:  # pypdf raises many types on malformed or encrypted files.
        raise DocumentError(f"could not read this PDF: {exc}") from exc
    if not any(p.split("\n", 1)[-1].strip() for p in pages):
        return "(this PDF has no extractable text; it is probably scanned images)"
    return "\n".join(pages)


def _docx(path: Path) -> str:
    try:
        from docx import Document

        document = Document(str(path))
    except Exception as exc:  # python-docx raises many types for damaged files.
        raise DocumentError(f"could not read this Word document: {exc}") from exc
    lines: list[str] = []
    body = document.element.body
    tables = iter(document.tables)
    paragraphs = iter(document.paragraphs)
    # Walk the body in order so tables appear where they are in the document.
    for child in body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            paragraph = next(paragraphs)
            text = paragraph.text.strip()
            if not text:
                continue
            style = (paragraph.style.name or "").lower() if paragraph.style is not None else ""
            if style.startswith("heading"):
                level = int(re.sub(r"\D", "", style) or 1)
                lines.append(f"{'#' * min(level, 6)} {text}")
            elif style == "title":
                lines.append(f"# {text}")
            elif "list" in style:
                lines.append(f"- {text}")
            else:
                lines.append(text)
        elif tag == "tbl":
            table = next(tables)
            rows = [[cell.text.strip().replace("\n", " ") for cell in row.cells] for row in table.rows]
            if rows:
                lines.append("| " + " | ".join(rows[0]) + " |")
                lines.append("|" + " --- |" * len(rows[0]))
                lines += ["| " + " | ".join(row) + " |" for row in rows[1:]]
    return "\n\n".join(lines) if lines else "(this document has no text)"


def _odf_text(path: Path) -> str:
    """OpenDocument text and presentations, on any platform."""
    try:
        from odf import opendocument, teletype, text as odf_text
        from odf.draw import Page
    except ImportError:
        if path.suffix.lower() == ".odt":
            return _textutil(path)
        raise DocumentError("reading .odp files needs odfpy: pip install odfpy") from None
    try:
        doc = opendocument.load(str(path))
    except Exception as exc:  # noqa: BLE001
        raise DocumentError(f"could not read {path.name}: {exc}") from exc
    if path.suffix.lower() == ".odp":
        slides = []
        for number, page in enumerate(doc.presentation.getElementsByType(Page), 1):
            lines = [teletype.extractText(p).strip() for p in page.getElementsByType(odf_text.P)]
            slides.append(f"--- slide {number} ---\n" + "\n".join(line for line in lines if line))
        return "\n\n".join(slides) or "(this presentation has no text)"
    blocks = []
    for element in doc.text.childNodes:
        content = teletype.extractText(element).strip()
        if not content:
            continue
        if element.qname[1] == "h":
            level = int(element.attributes.get((element.qname[0], "outline-level"), 1) or 1)
            content = f"{'#' * min(level, 6)} {content}"
        blocks.append(content)
    return "\n\n".join(blocks) or "(this document has no text)"


def _pptx(path: Path) -> str:
    try:
        from pptx import Presentation

        deck = Presentation(str(path))
    except Exception as exc:  # noqa: BLE001
        raise DocumentError(f"could not read this PowerPoint file: {exc}") from exc
    slides = []
    for number, slide in enumerate(deck.slides, 1):
        lines = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                text = shape.text_frame.text.strip()
                if text:
                    lines.append(text)
            if getattr(shape, "has_table", False) and shape.has_table:
                for row in shape.table.rows:
                    lines.append("| " + " | ".join(cell.text.strip() for cell in row.cells) + " |")
        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame.text.strip()
            if notes:
                lines.append(f"(speaker notes) {notes}")
        slides.append(f"--- slide {number} of {len(deck.slides)} ---\n" + "\n".join(lines))
    return "\n\n".join(slides) or "(this presentation has no slides)"


def _table_text(path: Path, limit: int) -> str:
    """Old Excel, OpenDocument spreadsheets and Parquet, shown as CSV text (first sheet with data)."""
    import csv

    from .dataviz import DataError, load_table

    try:
        table = load_table(path)
    except DataError as exc:
        raise DocumentError(str(exc)) from exc
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    if table.sheets:
        buffer.write(f"--- sheet {table.sheet} (sheets: {', '.join(table.sheets)}) ---\n")
    writer.writerow(table.columns)
    for row in table.rows:
        writer.writerow(["" if row[c] is None else row[c] for c in table.columns])
        if buffer.tell() > limit:
            break
    return buffer.getvalue()


def _notebook(text: str, limit: int) -> tuple[str, bool]:
    """A Jupyter notebook as its cells, without the JSON and embedded images."""
    import json

    try:
        cells = json.loads(text).get("cells", [])
    except (ValueError, AttributeError):
        return text[:limit], len(text) > limit
    parts = []
    for number, cell in enumerate(cells, 1):
        source = "".join(cell.get("source", [])) if isinstance(cell.get("source"), list) else str(cell.get("source", ""))
        kind = cell.get("cell_type", "code")
        parts.append(f"--- cell {number} ({kind}) ---\n{source}")
        for output in cell.get("outputs", []) if kind == "code" else []:
            out = output.get("text") or output.get("data", {}).get("text/plain")
            if out:
                parts.append("[output]\n" + ("".join(out) if isinstance(out, list) else str(out))[:2000])
    joined = "\n\n".join(parts)
    return joined[:limit], len(joined) > limit


def _textutil(path: Path) -> str:
    """Old .doc, .rtf and .odt via macOS's built-in textutil."""
    if not Path("/usr/bin/textutil").exists():
        raise DocumentError(f"reading {path.suffix} files needs macOS. Save it as .docx or PDF and try again.")
    try:
        result = subprocess.run(["/usr/bin/textutil", "-convert", "txt", "-stdout", str(path)],
                                capture_output=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DocumentError(f"could not convert {path.name}: {exc}") from exc
    if result.returncode != 0:
        raise DocumentError(f"could not read {path.name}: {result.stderr.decode(errors='replace')[:200]}")
    return result.stdout.decode("utf-8", errors="replace")


def _xlsx(path: Path, limit: int) -> str:
    try:
        from openpyxl import load_workbook

        workbook = load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:
        raise DocumentError(f"could not read this Excel workbook: {exc}") from exc
    parts, total = [], 0
    try:
        for sheet in workbook.worksheets:
            parts.append(f"--- sheet {sheet.title} ---")
            for row in sheet.iter_rows(values_only=True):
                if not any(cell is not None for cell in row):
                    continue
                line = ",".join("" if cell is None else str(cell) for cell in row)
                parts.append(line)
                total += len(line)
                if total > limit:
                    return "\n".join(parts)
    finally:
        workbook.close()
    return "\n".join(parts)


class _TextOnly(HTMLParser):
    SKIP = {"script", "style", "noscript", "template", "svg"}
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "header", "footer", "table"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skipping = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skipping += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("- ")
        if tag in {"h1", "h2", "h3"}:
            self.parts.append("#" * int(tag[1]) + " ")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skipping:
            self.skipping -= 1
        elif tag in {"td", "th"}:
            self.parts.append(" | ")

    def handle_data(self, data):
        if not self.skipping:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _TextOnly()
    parser.feed(html)
    text = "".join(parser.parts)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


# ---- writing Word documents ------------------------------------------------------

INLINE = re.compile(r"(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)")


def _inline(paragraph, text: str) -> None:
    for piece in INLINE.split(text):
        if not piece:
            continue
        if piece.startswith("**") and piece.endswith("**") and len(piece) > 4:
            paragraph.add_run(piece[2:-2]).bold = True
        elif piece.startswith("`") and piece.endswith("`") and len(piece) > 2:
            run = paragraph.add_run(piece[1:-1])
            run.font.name = "Menlo"
        elif piece.startswith("*") and piece.endswith("*") and len(piece) > 2:
            paragraph.add_run(piece[1:-1]).italic = True
        else:
            paragraph.add_run(piece)


def _table_rows(lines: list[str]) -> list[list[str]]:
    rows = []
    for line in lines:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", cell) for cell in cells if cell):
            continue  # The |---|---| separator.
        rows.append(cells)
    return rows


def build_docx(title: str, content: str, author: str = "", subtitle: str = "") -> bytes:
    """Markdown-style text to a .docx: # headings, - and 1. lists, | tables |, > quotes, ``` code, **bold**, *italic*,
    and a line with only --- for a page break."""
    from docx import Document
    from docx.enum.text import WD_BREAK
    from docx.shared import Pt, RGBColor

    document = Document()
    styles = document.styles
    styles["Normal"].font.name = "Calibri"
    styles["Normal"].font.size = Pt(11)
    if title:
        document.add_heading(title, level=0)
    if subtitle:
        para = document.add_paragraph()
        run = para.add_run(subtitle)
        run.italic, run.font.color.rgb = True, RGBColor(0x5B, 0x65, 0x70)
    if author:
        document.core_properties.author = author
    document.core_properties.title = title

    lines = content.replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        if stripped.startswith("```"):
            block = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                block.append(lines[i])
                i += 1
            para = document.add_paragraph()
            run = para.add_run("\n".join(block))
            run.font.name, run.font.size = "Menlo", Pt(9.5)
            i += 1
            continue
        if stripped.startswith("|"):
            block = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                block.append(lines[i])
                i += 1
            rows = _table_rows(block)
            if rows:
                width = max(len(r) for r in rows)
                table = document.add_table(rows=len(rows), cols=width)
                table.style = "Light Grid Accent 1"
                for r, row in enumerate(rows):
                    for c in range(width):
                        cell = table.cell(r, c)
                        cell.text = ""
                        _inline(cell.paragraphs[0], row[c] if c < len(row) else "")
                        if r == 0:
                            for run in cell.paragraphs[0].runs:
                                run.bold = True
                document.add_paragraph()
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            document.add_heading(heading.group(2).strip(), level=min(len(heading.group(1)), 4))
        elif stripped in {"---", "***", "___"}:
            document.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
        elif re.match(r"^[-*+]\s+", stripped):
            indent = (len(line) - len(line.lstrip())) // 2
            _inline(document.add_paragraph(style="List Bullet 2" if indent else "List Bullet"), re.sub(r"^[-*+]\s+", "", stripped))
        elif re.match(r"^\d+[.)]\s+", stripped):
            _inline(document.add_paragraph(style="List Number"), re.sub(r"^\d+[.)]\s+", "", stripped))
        elif stripped.startswith(">"):
            _inline(document.add_paragraph(style="Intense Quote"), stripped.lstrip("> ").strip())
        else:
            # Consecutive text lines form one paragraph.
            block = [stripped]
            while i + 1 < len(lines) and lines[i + 1].strip() and not re.match(r"^(#|[-*+]\s|\d+[.)]\s|\||>|```|---$)", lines[i + 1].strip()):
                i += 1
                block.append(lines[i].strip())
            _inline(document.add_paragraph(), " ".join(block))
        i += 1
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()
