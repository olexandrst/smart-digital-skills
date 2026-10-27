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
  посилання, марковані й нумеровані списки, таблиці. Колір тексту
  підбирається до найближчого з набору хабу, підкреслення й маркер
  переносяться (ті самі <span class="tc-…">, <u>, <mark>, що ставить
  редактор, див. app/static/editor.js). Редактор картки показує його
  оформленим;
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


def extract(filename, data, on_image=None):
    """Повертає {'text', 'kind', 'truncated'} або кидає ApiError.

    on_image(назва, байти) → посилання для Markdown або None: так картинки з
    .docx потрапляють у текст на своє місце. Без нього картинки пропускаються.
    """
    ext = os.path.splitext(filename or "")[1].lower()
    if ext in TEXT_EXTENSIONS:
        text, kind = _decode_text(data), "text"
    elif ext in DOCX_EXTENSIONS:
        text, kind = _docx_text(data, on_image), "docx"
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
# (за стилями з styles.xml, зокрема локалізованими, або жирні рядки-заголовки
# в документі без таких стилів), абзаци, жирний і курсив, посилання,
# марковані й нумеровані списки (тип — із numbering.xml), таблиці з рядком
# заголовка. Шрифти, кольори й відступи не переносимо.

_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_V = "{urn:schemas-microsoft-com:vml}"
# Більше картинок із одного документа не беремо: решта лишається у файлі.
MAX_DOCX_IMAGES = 50
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


def _docx_links(zf, kind="hyperlink"):
    """Id зв'язку → адреса: гіперпосилання або (kind="image") файл картинки."""
    root = _docx_xml(zf, "word/_rels/document.xml.rels")
    out = {}
    if root is not None:
        for rel in root.iter(_REL + "Relationship"):
            if (rel.get("Type") or "").endswith("/" + kind):
                out[rel.get("Id")] = rel.get("Target") or ""
    return out


def _docx_images(zf, on_image):
    """Функція «id зв'язку → посилання на картинку в тексті» (з кешем)."""
    media = _docx_links(zf, "image")
    cache = {}

    def image(rid):
        if rid in cache:
            return cache[rid]
        ref = None
        target = media.get(rid, "")
        if on_image and target and len([v for v in cache.values() if v]) < MAX_DOCX_IMAGES:
            path = os.path.normpath(os.path.join("word", target)).replace(os.sep, "/")
            try:
                ref = on_image(os.path.basename(path), zf.read(path))
            except KeyError:
                ref = None
        cache[rid] = ref
        return ref
    return image


# Набір кольорів тексту — той самий, що в редакторі (editor.js, TEXT_COLORS):
# назва → відтінок (градуси) для підбору найближчого.
_HUES = {"red": 355, "orange": 30, "green": 140, "blue": 212, "purple": 285}


def nearest_color(hex_value):
    """Найближчий колір набору до кольору Word. Чорний, темно-сірий і білий —
    звичайний текст (None): так у документах пишуть основний текст."""
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", (hex_value or "").strip())
    if not m:
        return None
    r, g, b = (int(m.group(1)[i:i + 2], 16) / 255 for i in (0, 2, 4))
    hi, lo = max(r, g, b), min(r, g, b)
    light = (hi + lo) / 2
    sat = 0 if hi == lo else (hi - lo) / (1 - abs(2 * light - 1))
    if sat < 0.2:
        return None if light < 0.3 or light > 0.85 else "gray"
    if hi == r:
        hue = 60 * (((g - b) / (hi - lo)) % 6)
    elif hi == g:
        hue = 60 * ((b - r) / (hi - lo) + 2)
    else:
        hue = 60 * ((r - g) / (hi - lo) + 4)
    hue %= 360
    return min(_HUES, key=lambda k: min(abs(hue - _HUES[k]), 360 - abs(hue - _HUES[k])))


def _run_fmt(rpr):
    """(колір, підкреслення, маркер) відрізка тексту Word."""
    if rpr is None:
        return (None, False, False)
    color = rpr.find(_W + "color")
    under = rpr.find(_W + "u")
    hl = rpr.find(_W + "highlight")
    shd = rpr.find(_W + "shd")
    marked = ((hl is not None and (hl.get(_W + "val") or "none") != "none")
              or (shd is not None and (shd.get(_W + "fill") or "auto").lower()
                  not in ("auto", "ffffff", "")))
    return (nearest_color(color.get(_W + "val")) if color is not None else None,
            under is not None and (under.get(_W + "val") or "single") != "none",
            marked)


_NO_FMT = (None, False, False)


def _flag(rpr, tag):
    if rpr is None:
        return False
    el = rpr.find(_W + tag)
    return el is not None and (el.get(_W + "val") or "true").lower() not in _OFF


def _md_escape(text):
    return text.replace("\\", "\\\\").replace("*", "\\*").replace("`", "\\`")


def _wrap(text, mark, close=None):
    """Розмітка щільно до слова: «** слово**» Markdown не розпізнає."""
    m = re.match(r"^(\s*)(.*?)(\s*)$", text, re.S)
    if not m.group(2):
        return text
    return f"{m.group(1)}{mark}{m.group(2)}{mark if close is None else close}{m.group(3)}"


def _docx_runs(node, ctx, link=None, out=None):
    """Відрізки тексту абзацу: (текст, жирний, курсив, посилання, оформлення)."""
    if out is None:
        out = []
    for ch in node:
        tag = ch.tag
        if tag == _W + "r":
            rpr = ch.find(_W + "rPr")
            bold, italic = _flag(rpr, "b"), _flag(rpr, "i")
            fmt = _run_fmt(rpr)
            for part in ch:
                if part.tag == _W + "t":
                    out.append((part.text or "", bold, italic, link, fmt))
                elif part.tag in (_W + "tab", _W + "ptab"):
                    out.append((" ", bold, italic, link, fmt))
                elif part.tag in (_W + "br", _W + "cr"):
                    out.append(("\n", False, False, None, _NO_FMT))
                elif part.tag == _W + "noBreakHyphen":
                    out.append(("-", bold, italic, link, fmt))
                elif part.tag in (_W + "drawing", _W + "pict"):
                    # Картинка: сучасна (a:blip) або стара VML (v:imagedata).
                    for el in part.iter():
                        rid = (el.get(_R + "embed") if el.tag == _A + "blip"
                               else el.get(_R + "id") if el.tag == _V + "imagedata" else None)
                        ref = ctx["image"](rid) if rid else None
                        if ref:
                            out.append(("", False, False, ("img", ref, len(out)), _NO_FMT))
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
    for text, bold, italic, link, fmt in _docx_runs(p, ctx):
        if merged and merged[-1][1:] == (bold, italic, link, fmt) and text != "\n":
            merged[-1] = (merged[-1][0] + text, bold, italic, link, fmt)
        else:
            merged.append((text, bold, italic, link, fmt))
    parts = []
    for text, bold, italic, link, (color, under, marked) in merged:
        if isinstance(link, tuple):        # картинка
            parts.append(f"![]({link[1]})")
            continue
        piece = _md_escape(text)
        if bold and italic:
            piece = _wrap(piece, "***")
        elif bold:
            piece = _wrap(piece, "**")
        elif italic:
            piece = _wrap(piece, "*")
        if color:
            piece = _wrap(piece, f'<span class="tc-{color}">', "</span>")
        if under and not link:      # посилання й так підкреслене
            piece = _wrap(piece, "<u>", "</u>")
        if marked:
            piece = _wrap(piece, "<mark>", "</mark>")
        if link:
            label = piece.strip()
            piece = link if not label or label == link else f"[{label}]({link})"
        parts.append(piece)
    text = "".join(parts)
    # Порожні рядки посеред абзацу розірвали б його на два.
    return re.sub(r"\n\s*\n+", "\n", re.sub(r"[ \t]+", " ", text)).strip()


def _docx_plain(p, ctx):
    return re.sub(r"\s+", " ", "".join(t for t, *_ in _docx_runs(p, ctx))).strip()


def _docx_bold_only(p, ctx):
    """Абзац, увесь текст якого жирний, — так у Word часто оформлюють
    заголовки замість стилю «Заголовок»."""
    runs = [(t, b) for t, b, _i, link, _f in _docx_runs(p, ctx) if t.strip() and not link]
    return bool(runs) and all(b for _t, b in runs)


# Схожий на заголовок: короткий, з літерами, без крапки, коми чи двокрапки
# в кінці («Спробуйте:» — це вступ до списку, а не заголовок).
_HEADING_LIKE = re.compile(r"^(?=.*\w).{1,120}(?<![.,;:])$", re.S)


def _docx_paragraph(p, ctx):
    """(рядок Markdown, тип, список, чистий текст) для абзацу Word.

    Тип — «heading», «item», «para» або «bold» (абзац лише з жирного тексту,
    схожий на заголовок). Список — (numId, маркер) для пунктів, інакше None.
    """
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
        return ("#" * level + " " + text if text else ""), "heading", None, text
    text = _docx_inline(p, ctx)
    if not text:
        return "", "para", None, ""
    if num is not None:
        num_id = num.find(_W + "numId")
        ilvl = num.find(_W + "ilvl")
        num_id = num_id.get(_W + "val") if num_id is not None else None
        if num_id != "0":      # «0» — нумерацію вимкнено вручну
            fmt = ctx["numbering"].get((num_id, ilvl.get(_W + "val") if ilvl is not None else "0"),
                                       "bullet")
            marker = "- " if fmt in ("bullet", "none", "") else "1. "
            return marker + text.replace("\n", " "), "item", (num_id or "1", marker), ""
    plain = _docx_plain(p, ctx)
    if _docx_bold_only(p, ctx) and _HEADING_LIKE.match(plain):
        return text, "bold", None, plain
    return text, "para", None, plain


def _docx_headings_from_bold(blocks):
    """Жирні рядки-заголовки → заголовки Markdown.

    Лише для документа без стилів заголовків (інакше жирний рядок — це просто
    виділення) і лише перед звичайним текстом: два жирні рядки поспіль або
    жирний рядок у кінці (підпис) заголовками не стають. Перший рядок
    документа — назва («#»), решта — розділи («##»); без назви розділи
    отримують «#».
    """
    if any(b[1] == "heading" for b in blocks):
        return blocks
    picked = [i for i, b in enumerate(blocks)
              if b[1] == "bold" and i + 1 < len(blocks) and blocks[i + 1][1] != "bold"]
    titled = bool(picked) and picked[0] == 0 and len(picked) > 1
    out = list(blocks)
    for i in picked:
        level = "##" if titled and i else "#"
        out[i] = (f"{level} {blocks[i][3]}", "heading", None, blocks[i][3])
    return out


def _docx_join(blocks):
    """Склеює блоки: пункти одного списку — рядок за рядком, решта — через
    порожній рядок. Нумерований список отримує номери 1, 2, 3 замість
    «1.» у кожному пункті: так його зручніше правити на вкладці Markdown."""
    out, n = [], 0
    for i, (line, _kind, lst, _plain) in enumerate(blocks):
        same = bool(lst) and i > 0 and lst == blocks[i - 1][2]
        if i:
            out.append("\n" if same else "\n\n")
        if lst and lst[1] == "1. ":
            n = n + 1 if same else 1
            line = f"{n}. " + line[3:]
        out.append(line)
    return "".join(out)


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


def _docx_text(data, on_image=None):
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
    with zf:
        ctx = {"styles": _docx_styles(zf), "numbering": _docx_numbering(zf),
               "links": _docx_links(zf), "image": _docx_images(zf, on_image)}
        body = root.find(_W + "body")
        if body is None:
            return ""
        blocks = []   # (рядок, тип, список, чистий текст)
        for node in body:
            if node.tag == _W + "p":
                block = _docx_paragraph(node, ctx)
                if block[0]:
                    blocks.append(block)
            elif node.tag == _W + "tbl":
                table = _docx_table(node, ctx)
                if table:
                    blocks.append((table, "table", None, ""))
    return _docx_join(_docx_headings_from_bold(blocks))


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
