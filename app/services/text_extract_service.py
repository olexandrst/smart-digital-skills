"""Витяг тексту з файлу, який користувач подає як зміст матеріалу.

Людина, що додає інструкцію чи кейс, здебільшого вже має його у Word або
PDF — вимагати перенабрати текст у поле означає, що матеріал не буде доданий.
Тому майстер створення приймає файл, а сюди приходять байти й назва: назад
повертається текст, який уже можна правити в полі.

Що підтримується і чому саме так:
- текстові файли (.txt, .md, …) — як є; кодування вгадується: utf-8, далі
  cp1251 (старі документи з Windows);
- .docx — читається стандартним zipfile + xml, без сторонніх бібліотек, і
  перетворюється на Markdown з оформленням: заголовки, жирний і курсив,
  посилання, марковані й нумеровані списки, таблиці. Редактор картки
  показує його оформленим;
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
#
# Результат — Markdown, який редактор картки показує оформленим: заголовки
# (за стилями з styles.xml, зокрема локалізованими), абзаци, жирний і
# курсив, посилання, марковані й нумеровані списки (тип — із numbering.xml),
# таблиці з рядком заголовка. Шрифти, кольори й відступи не переносимо.

_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_OFF = {"0", "false", "off", "none"}


def _docx_xml(zf, name):
    try:
        return ET.fromstring(zf.read(name))
    except (KeyError, ET.ParseError):
        return None


def _docx_styles(zf):
    """styleId → назва стилю («heading 1», «Title»…), щоб розпізнати заголовки
    й у локалізованому Word, де styleId буває «1» чи «Заголовок1»."""
    root = _docx_xml(zf, "word/styles.xml")
    out = {}
    if root is not None:
        for st in root.iter(_W + "style"):
            name = st.find(_W + "name")
            out[st.get(_W + "styleId") or ""] = (name.get(_W + "val") if name is not None else "") or ""
    return out


def _docx_numbering(zf):
    """(numId, ilvl) → формат нумерації: «bullet», «decimal», «lowerLetter»…"""
    root = _docx_xml(zf, "word/numbering.xml")
    if root is None:
        return {}
    abstract = {}
    for an in root.iter(_W + "abstractNum"):
        levels = {}
        for lvl in an.findall(_W + "lvl"):
            fmt = lvl.find(_W + "numFmt")
            levels[lvl.get(_W + "ilvl") or "0"] = fmt.get(_W + "val") if fmt is not None else ""
        abstract[an.get(_W + "abstractNumId")] = levels
    out = {}
    for num in root.iter(_W + "num"):
        ref = num.find(_W + "abstractNumId")
        levels = abstract.get(ref.get(_W + "val") if ref is not None else None, {})
        for ilvl, fmt in levels.items():
            out[(num.get(_W + "numId"), ilvl)] = fmt
    return out


def _docx_links(zf):
    """Id зв'язку → адреса для гіперпосилань документа."""
    root = _docx_xml(zf, "word/_rels/document.xml.rels")
    out = {}
    if root is not None:
        for rel in root.iter(_REL + "Relationship"):
            if (rel.get("Type") or "").endswith("/hyperlink"):
                out[rel.get("Id")] = rel.get("Target") or ""
    return out


def _flag(rpr, tag):
    if rpr is None:
        return False
    el = rpr.find(_W + tag)
    return el is not None and (el.get(_W + "val") or "true").lower() not in _OFF


def _md_escape(text):
    return text.replace("\\", "\\\\").replace("*", "\\*").replace("`", "\\`")


def _wrap(text, mark):
    """Розмітка щільно до слова: «** слово**» Markdown не розпізнає."""
    m = re.match(r"^(\s*)(.*?)(\s*)$", text, re.S)
    if not m.group(2):
        return text
    return f"{m.group(1)}{mark}{m.group(2)}{mark}{m.group(3)}"


def _docx_runs(node, ctx, link=None, out=None):
    """Відрізки тексту абзацу: (текст, жирний, курсив, посилання)."""
    if out is None:
        out = []
    for ch in node:
        tag = ch.tag
        if tag == _W + "r":
            rpr = ch.find(_W + "rPr")
            bold, italic = _flag(rpr, "b"), _flag(rpr, "i")
            for part in ch:
                if part.tag == _W + "t":
                    out.append((part.text or "", bold, italic, link))
                elif part.tag in (_W + "tab", _W + "ptab"):
                    out.append((" ", bold, italic, link))
                elif part.tag in (_W + "br", _W + "cr"):
                    out.append(("\n", False, False, None))
                elif part.tag == _W + "noBreakHyphen":
                    out.append(("-", bold, italic, link))
        elif tag == _W + "hyperlink":
            target = ctx["links"].get(ch.get(_R + "id"), "")
            _docx_runs(ch, ctx, target if re.match(r"(?i)^(https?:|mailto:)", target) else None, out)
        elif tag in (_W + "del", _W + "moveFrom", _W + "pPr"):
            continue   # видалене в режимі виправлень і властивості абзацу
        elif tag == _W + "sdt":
            content = ch.find(_W + "sdtContent")
            if content is not None:
                _docx_runs(content, ctx, link, out)
        else:      # ins, smartTag, fldSimple тощо — заходимо всередину
            _docx_runs(ch, ctx, link, out)
    return out


def _docx_inline(p, ctx):
    """Текст абзацу з Markdown-оформленням."""
    merged = []
    for text, bold, italic, link in _docx_runs(p, ctx):
        if merged and merged[-1][1:] == (bold, italic, link) and text != "\n":
            merged[-1] = (merged[-1][0] + text, bold, italic, link)
        else:
            merged.append((text, bold, italic, link))
    parts = []
    for text, bold, italic, link in merged:
        piece = _md_escape(text)
        if bold and italic:
            piece = _wrap(piece, "***")
        elif bold:
            piece = _wrap(piece, "**")
        elif italic:
            piece = _wrap(piece, "*")
        if link:
            label = piece.strip()
            piece = link if not label or label == link else f"[{label}]({link})"
        parts.append(piece)
    text = "".join(parts)
    # Порожні рядки посеред абзацу розірвали б його на два.
    return re.sub(r"\n\s*\n+", "\n", re.sub(r"[ \t]+", " ", text)).strip()


def _docx_plain(p, ctx):
    return re.sub(r"\s+", " ", "".join(t for t, *_ in _docx_runs(p, ctx))).strip()


def _docx_paragraph(p, ctx):
    """(рядок Markdown, список) для абзацу Word: список — numId пункту або None."""
    ppr = p.find(_W + "pPr")
    style_id = ""
    num = None
    if ppr is not None:
        style = ppr.find(_W + "pStyle")
        style_id = (style.get(_W + "val") or "") if style is not None else ""
        num = ppr.find(_W + "numPr")
    name = ctx["styles"].get(style_id, "") or style_id
    heading = re.match(r"(?i)^(?:heading|заголовок)\s*(\d)$", name.strip()) \
        or re.match(r"(?i)^heading\s*(\d)$", style_id)
    if heading or name.strip().lower() in ("title", "назва"):
        text = _docx_plain(p, ctx)
        level = min(int(heading.group(1)), 3) if heading else 1
        return ("#" * level + " " + text if text else ""), None
    text = _docx_inline(p, ctx)
    if not text:
        return "", None
    if num is not None:
        num_id = num.find(_W + "numId")
        ilvl = num.find(_W + "ilvl")
        num_id = num_id.get(_W + "val") if num_id is not None else None
        if num_id == "0":      # нумерацію вимкнено вручну
            return text, None
        fmt = ctx["numbering"].get((num_id, ilvl.get(_W + "val") if ilvl is not None else "0"), "bullet")
        marker = "- " if fmt in ("bullet", "none", "") else "1. "
        return marker + text.replace("\n", " "), (num_id or "1", marker)
    return text, None


def _docx_table(tbl, ctx):
    rows = []
    for tr in tbl.findall(_W + "tr"):
        cells = []
        for tc in tr.findall(_W + "tc"):
            texts = [_docx_inline(p, ctx) for p in tc.iter(_W + "p")]
            cell = " ".join(t.replace("\n", " ") for t in texts if t).replace("|", "&#124;")
            cells.append(cell or " ")
        if cells:
            rows.append(cells)
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [" "] * (width - len(r)) for r in rows]
    line = lambda r: "| " + " | ".join(r) + " |"   # noqa: E731
    return "\n".join([line(rows[0]), line(["---"] * width)] + [line(r) for r in rows[1:]])


def _docx_text(data):
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        xml = zf.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError):
        raise ApiError("Файл не схожий на документ Word (.docx)",
                       400, "extract_bad_file")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        raise ApiError("Не вдалося прочитати документ Word", 400, "extract_bad_file")
    ctx = {"styles": _docx_styles(zf), "numbering": _docx_numbering(zf), "links": _docx_links(zf)}
    zf.close()

    body = root.find(_W + "body")
    if body is None:
        return ""
    blocks = []   # (рядок, список)
    for node in body:
        if node.tag == _W + "p":
            line, item = _docx_paragraph(node, ctx)
            if line:
                blocks.append((line, item))
        elif node.tag == _W + "tbl":
            table = _docx_table(node, ctx)
            if table:
                blocks.append((table, None))
    # Пункти одного списку — рядок за рядком; решта блоків — через порожній рядок.
    out = []
    for i, (line, item) in enumerate(blocks):
        if i:
            out.append("\n" if item and item == blocks[i - 1][1] else "\n\n")
        out.append(line)
    return "".join(out)


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
