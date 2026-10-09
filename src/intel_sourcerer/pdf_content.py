"""Extract selectable PDF text for the article reader."""
from io import BytesIO
import re
from collections import Counter
from html import escape
from urllib.parse import unquote, urlsplit

from pypdf import PdfReader
from .content import extract_markdown


def extract_pdf(data, url):
    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("This PDF is password protected.")
        if len(reader.pages) > 500:
            raise ValueError("This PDF exceeds the 500-page limit.")
        pages = strip_page_furniture([page.extract_text() or "" for page in reader.pages])
        title = str((reader.metadata or {}).get("/Title", "")).strip()
    except ValueError:
        raise
    except Exception as error:
        raise ValueError("This PDF could not be read. Check that the link points to a valid PDF.") from error
    content = "\n\n".join(page.strip() for page in pages if page.strip())
    if not content:
        raise ValueError("No selectable text found in this PDF. Scanned PDFs need OCR before import.")
    title = title or unquote(urlsplit(url).path.rsplit("/", 1)[-1]).removesuffix(".pdf") or "PDF document"
    html = "<article>" + "".join(page_html(page) for page in pages) + "</article>"
    markdown, _ = extract_markdown(html, url)
    return title, content, markdown


def page_html(text):
    """Keep list boundaries while joining wrapped lines inside each item."""
    blocks = []
    paragraph = []
    items = []
    list_kind = None
    list_start = 1
    in_references = False

    def flush_paragraph():
        if paragraph:
            blocks.append("<p>" + escape(" ".join(paragraph)) + "</p>")
            paragraph.clear()

    def flush_list():
        nonlocal list_kind
        if items:
            start = f' start="{list_start}"' if list_kind == "ol" else ""
            blocks.append(f"<{list_kind}{start}>" + "".join("<li>" + escape(item) + "</li>" for item in items) + f"</{list_kind}>")
            items.clear()
        list_kind = None

    text = normalize_section_heading(text)
    text = re.sub(r"^Abstract\s*[—–:\-]\s*", "Abstract\n", text, flags=re.I)
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            flush_paragraph()
            flush_list()
            continue
        ieee_heading = re.match(r"^([IVXLCDM]+)\.\s+([A-Z][A-Z0-9 ,&’'()/-]+)$", line)
        letter_heading = is_lettered_heading(line)
        if ieee_heading or (letter_heading and len(line.split()) <= 14):
            flush_paragraph()
            flush_list()
            level = 2 if ieee_heading else 3
            blocks.append(f"<h{level}>" + escape(line) + f"</h{level}>")
            in_references = bool(ieee_heading and ieee_heading[2].lower() in {'references', 'bibliography', 'works cited'})
            continue
        section_name = re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", line).strip().lower()
        if section_name in {"abstract", "references", "bibliography", "works cited"} or re.match(r"^appendix(?:\s+[A-Z0-9])?(?:[:.\s]|$)", line, re.I):
            flush_paragraph()
            flush_list()
            blocks.append("<h2>" + escape(line) + "</h2>")
            in_references = section_name in {"references", "bibliography", "works cited"}
            continue
        if re.match(r"^(?:Author|Authors|Advisor)\s*:", line, re.I) and not any("Authors &amp; publication details" in block for block in blocks):
            flush_paragraph()
            blocks.append("<h2>Authors &amp; publication details</h2>")
        if in_references and re.match(r"^(?:\[\d+\]|[A-Z][^\n]{0,100}\(\d{4}[a-z]?\))", line):
            flush_paragraph()
        subsection = re.match(r"^\d+(?:\.\d+)+\.?\s+\S", line)
        if subsection and len(line.split()) <= 16:
            flush_paragraph()
            flush_list()
            level = min(3, len(re.match(r"[\d.]+", line)[0].rstrip(".").split(".")) + 1)
            blocks.append(f"<h{level}>" + escape(line) + f"</h{level}>")
            continue
        bullet = re.match(r"^[•●▪◦‣⁃*-]\s+(.+)$", line)
        numbered = re.match(r"^(\d+)[.)]?\s+(.+)$", line)
        # Bare numeric quantities are prose; headings need a title-like capital.
        if numbered and numbered[2][0].isupper() and len(numbered[2].split()) <= 5 and not items:
            flush_paragraph()
            blocks.append("<h2>" + escape(line) + "</h2>")
            continue
        explicit_number = re.match(r"^\d+[.)]\s+", line)
        if numbered and (not explicit_number or (numbered[2][0].islower() and not items)):
            numbered = None
        kind = "ul" if bullet else "ol" if numbered else None
        if kind:
            flush_paragraph()
            if list_kind != kind:
                flush_list()
                list_kind = kind
                list_start = int(numbered[1]) if numbered else 1
            items.append(bullet[1] if bullet else numbered[2])
        elif items:
            items[-1] += " " + line
        else:
            paragraph.append(line)
    flush_paragraph()
    flush_list()
    return "".join(blocks)


def strip_page_furniture(pages):
    """Remove short repeated edge lines, retaining matching text in the body."""
    lines_by_page = [page.splitlines() for page in pages]
    candidates = Counter()

    def key(line):
        return " ".join(line.split())

    def edges(lines):
        nonblank = [i for i, line in enumerate(lines) if line.strip()]
        return set(nonblank[:3] + nonblank[-3:])

    for lines in lines_by_page:
        candidates.update({key(lines[i]) for i in edges(lines) if len(key(lines[i])) <= 120})
    threshold = max(3, (len(pages) + 1) // 2)
    repeated = {text for text, count in candidates.items() if count >= threshold}
    result = []
    for lines in lines_by_page:
        boundary = edges(lines)
        cleaned = []
        for i, line in enumerate(lines):
            text = key(line)
            page_number = re.fullmatch(r"(?:Page\s+)?\d+(?:\s+(?:of|/)\s+\d+)?", text, re.I)
            next_line = next((key(v) for v in lines[i + 1:] if v.strip()), '')
            section_number = bool(re.fullmatch(r"\d+", text) and next_line and len(next_line.split()) <= 12 and next_line[0].isupper() and i < len(lines) - 3)
            if i in boundary and (text in repeated or (page_number and not section_number)):
                continue
            cleaned.append(line)
        result.append("\n".join(cleaned))
    return result


def normalize_section_heading(text):
    """PDF extraction may put a section number on a line before its title."""
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if len(lines) == 2 and re.fullmatch(r"\d+(?:\.\d+)*\.?", lines[0]) and len(lines[1].split()) <= 12:
        number = lines[0].rstrip('.')
        return number + ('. ' if '.' not in number else '. ') + lines[1]
    return text


def is_lettered_heading(line):
    """Single-initial surnames and citation author lists are ordinary text."""
    match = re.fullmatch(r"[A-Z]\.\s+(.+)", line)
    if not match:
        return False
    words = match[1].split()
    if not 2 <= len(words) <= 13:
        return False
    if re.search(r"(?:\bet al\b|[,;]|\b[A-Z]\.)", match[1]):
        return False
    return words[0][0].isupper() and sum(word[0].isupper() for word in words) >= 2
