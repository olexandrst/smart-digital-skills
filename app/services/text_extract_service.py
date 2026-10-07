"""Витяг тексту з файлу, який користувач подає як зміст матеріалу.

Людина, що додає інструкцію чи кейс, здебільшого вже має його у Word або
PDF — вимагати перенабрати текст у поле означає, що матеріал не буде доданий.
Тому майстер створення приймає файл, а сюди приходять байти й назва: назад
повертається текст, який уже можна правити в полі.

Що підтримується і чому саме так:
- текстові файли (.txt, .md, …) — як є; кодування вгадується: utf-8, далі
  cp1251 (старі документи з Windows);
- .docx — читається стандартним zipfile + xml, без сторонніх бібліотек;
  заголовки стають Markdown-заголовками, нумеровані й марковані абзаци —
  пунктами списку, таблиці — рядками з « | ». Решта оформлення не потрібна:
  картка показує Markdown;
- .pdf — через pypdf, якщо він установлений; сканований PDF без текстового
  шару повертає порожній рядок, і майстер пропонує прикріпити файл як
  вкладення замість тексту.

Для всього іншого (.xlsx, .pptx, зображення) тексту не буде — такі файли
додаються до картки як вкладення.
"""
import io
import os
import re
import zipfile
from xml.etree import ElementTree as ET

from app.core.errors import ApiError

try:  # pypdf — необов'язкова залежність: без неї працює все, крім PDF
    from pypdf import PdfReader
except ImportError:  # pragma: no cover — залежить від середовища
    PdfReader = None

TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".text", ".csv", ".json",
                   ".yaml", ".yml"}
DOCX_EXTENSIONS = {".docx"}
PDF_EXTENSIONS = {".pdf"}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | DOCX_EXTENSIONS | PDF_EXTENSIONS

# Стільки ж, скільки приймає поле тексту матеріалу (MAX_BODY у catalog.py):
# довший текст у картку однаково не ляже.
MAX_CHARS = 100_000

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def supported(filename):
    """Чи вміємо витягти текст із файлу з такою назвою."""
    return os.path.splitext(filename or "")[1].lower() in SUPPORTED_EXTENSIONS


def extract(filename, data):
    """Повертає {'text', 'kind', 'truncated'} або кидає ApiError."""
    ext = os.path.splitext(filename or "")[1].lower()
    if ext in TEXT_EXTENSIONS:
        text, kind = _decode_text(data), "text"
    elif ext in DOCX_EXTENSIONS:
        text, kind = _docx_text(data), "docx"
    elif ext in PDF_EXTENSIONS:
        text, kind = _pdf_text(data), "pdf"
    else:
        raise ApiError(
            "Текст можна витягти з .txt, .md, .docx або .pdf. Інші файли "
            "додайте до картки як вкладення", 400, "extract_unsupported")

    text = _tidy(text)
    truncated = len(text) > MAX_CHARS
    return {"text": text[:MAX_CHARS], "kind": kind, "truncated": truncated,
            "chars": min(len(text), MAX_CHARS)}


def _decode_text(data):
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _tidy(text):
    """Прибирає зайве: CRLF, пробіли в кінці рядків, три й більше порожніх рядки."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ------------------------------- DOCX -------------------------------

def _docx_text(data):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            xml = zf.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError):
        raise ApiError("Файл не схожий на документ Word (.docx)",
                       400, "extract_bad_file")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        raise ApiError("Не вдалося прочитати документ Word", 400, "extract_bad_file")

    body = root.find(_W + "body")
    if body is None:
        return ""
    lines = []
    for node in body:
        if node.tag == _W + "p":
            lines.append(_docx_paragraph(node))
        elif node.tag == _W + "tbl":
            for tr in node.iter(_W + "tr"):
                cells = [" ".join(filter(None, (_docx_paragraph(p)
                                                for p in tc.iter(_W + "p"))))
                         for tc in tr.findall(_W + "tc")]
                lines.append(" | ".join(cells))
            lines.append("")
    return "\n".join(lines)


def _docx_paragraph(p):
    """Текст абзацу з позначкою його ролі: заголовок, пункт списку або звичайний."""
    parts = []
    for node in p.iter():
        if node.tag == _W + "t":
            parts.append(node.text or "")
        elif node.tag == _W + "tab":
            parts.append("\t")
        elif node.tag == _W + "br":
            parts.append("\n")
    text = "".join(parts).strip()
    if not text:
        return ""
    ppr = p.find(_W + "pPr")
    if ppr is None:
        return text
    style = ppr.find(_W + "pStyle")
    style_id = (style.get(_W + "val") or "") if style is not None else ""
    heading = re.match(r"(?i)heading\s*(\d)", style_id)
    if heading:
        return "#" * min(int(heading.group(1)), 6) + " " + text
    if style_id.lower() == "title":
        return "# " + text
    if ppr.find(_W + "numPr") is not None:
        return "- " + text
    return text


# -------------------------------- PDF --------------------------------

def _pdf_text(data):
    if PdfReader is None:
        raise ApiError("Витяг тексту з PDF недоступний на цьому сервері — "
                       "прикріпіть файл до картки як вкладення",
                       400, "extract_pdf_unavailable")
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            # Порожній пароль — часта «захищеність» службових PDF; інший
            # пароль ми не знаємо й не питаємо.
            if not reader.decrypt(""):
                raise ApiError("PDF захищений паролем — зніміть захист або "
                               "прикріпіть файл як вкладення",
                               400, "extract_pdf_encrypted")
        pages = [page.extract_text() or "" for page in reader.pages]
    except ApiError:
        raise
    except Exception:  # noqa: BLE001 — pypdf кидає різні типи на битих файлах
        raise ApiError("Не вдалося прочитати PDF", 400, "extract_bad_file")
    return "\n\n".join(p.strip() for p in pages if p.strip())
