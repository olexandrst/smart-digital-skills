"""Витяг тексту з файлу для майстра створення матеріалу (.txt/.md/.docx/.pdf)."""
import io
import zipfile

import pytest

from tests.conftest import login, auth

URL = "/api/catalog/resources/extract-text"


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _send(client, token, name, data):
    return client.post(URL, headers=auth(token),
                       data={"file": (io.BytesIO(data), name)},
                       content_type="multipart/form-data")


def _docx(paragraphs, parts=None):
    """Мінімальний .docx: word/document.xml, типи вмісту й додаткові parts.

    paragraphs — список (текст, стиль|None, список?). Таблиця додається окремо
    рядком-кортежем ("table", [[...], [...]]), готовий XML абзацу — ("xml", "<w:p>…").
    """
    w = W

    def para(text, style=None, bullet=False):
        ppr = ""
        if style or bullet:
            ppr = "<w:pPr>" + (f'<w:pStyle w:val="{style}"/>' if style else "") \
                + ("<w:numPr><w:numId w:val=\"1\"/></w:numPr>" if bullet else "") \
                + "</w:pPr>"
        return f"<w:p>{ppr}<w:r><w:t>{text}</w:t></w:r></w:p>"

    body = []
    for item in paragraphs:
        if item[0] == "table":
            rows = "".join(
                "<w:tr>" + "".join(f"<w:tc>{para(c)}</w:tc>" for c in row) + "</w:tr>"
                for row in item[1])
            body.append(f"<w:tbl>{rows}</w:tbl>")
        elif item[0] == "xml":
            body.append(item[1])
        else:
            body.append(para(*item))
    xml = (f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{w}" xmlns:r="{R}">'
           f'<w:body>{"".join(body)}</w:body></w:document>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml",
                    '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
                    'package/2006/content-types"/>')
        zf.writestr("word/document.xml", xml)
        for name, content in (parts or {}).items():
            zf.writestr(name, content)
    return buf.getvalue()


def _pdf(text):
    """Однасторінковий PDF із коректною таблицею xref, щоб pypdf не «лікував» файл."""
    stream = f"BT /F1 24 Tf 72 700 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream
        + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
              f"startxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def test_plain_text_and_markdown(client):
    token = login(client, "u1", "pass")   # будь-який користувач, не лише менеджер
    res = _send(client, token, "нотатка.txt", "Перший рядок\r\n\r\n\r\n\r\nДругий  \n".encode())
    assert res.status_code == 200, res.get_json()
    data = res.get_json()
    assert data["text"] == "Перший рядок\n\nДругий"
    assert data["kind"] == "text"
    assert data["truncated"] is False
    assert data["filename"] == "нотатка.txt"

    md = _send(client, token, "guide.md", "# Заголовок\n\nТекст".encode()).get_json()
    assert md["text"] == "# Заголовок\n\nТекст"


def test_cp1251_fallback(client):
    token = login(client, "u1", "pass")
    res = _send(client, token, "old.txt", "Старий документ".encode("cp1251"))
    assert res.get_json()["text"] == "Старий документ"


def test_docx_headings_lists_tables(client):
    token = login(client, "u1", "pass")
    data = _docx([
        ("Інструкція", "Heading1"),
        ("Вступ", "Heading2"),
        ("Звичайний абзац", None),
        ("Перший пункт", None, True),
        ("Другий пункт", None, True),
        ("table", [["Поле", "Значення"], ["Модель", "gpt-4o"]]),
    ])
    res = _send(client, token, "інструкція.docx", data)
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["kind"] == "docx"
    assert res.get_json()["text"] == (
        "# Інструкція\n\n## Вступ\n\nЗвичайний абзац\n\n- Перший пункт\n- Другий пункт\n\n"
        "| Поле | Значення |\n| --- | --- |\n| Модель | gpt-4o |")


def test_docx_keeps_formatting(client):
    """Жирний і курсив, посилання, нумерований список і локалізовані заголовки
    переходять у Markdown, тож у редакторі текст виглядає як у Word."""
    token = login(client, "u1", "pass")
    styles = (f'<w:styles xmlns:w="{W}"><w:style w:styleId="1"><w:name w:val="heading 1"/>'
              f'</w:style><w:style w:styleId="a3"><w:name w:val="Заголовок 2"/></w:style></w:styles>')
    numbering = (f'<w:numbering xmlns:w="{W}">'
                 f'<w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:numFmt w:val="decimal"/></w:lvl></w:abstractNum>'
                 f'<w:abstractNum w:abstractNumId="1"><w:lvl w:ilvl="0"><w:numFmt w:val="bullet"/></w:lvl></w:abstractNum>'
                 f'<w:num w:numId="5"><w:abstractNumId w:val="0"/></w:num>'
                 f'<w:num w:numId="6"><w:abstractNumId w:val="1"/></w:num></w:numbering>')
    rels = ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId9" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/hyperlink" Target="https://example.com/doc" TargetMode="External"/>'
            '</Relationships>')

    def item(text, num):
        return ("xml", f'<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="{num}"/>'
                       f'</w:numPr></w:pPr><w:r><w:t>{text}</w:t></w:r></w:p>')

    data = _docx([
        ("Регламент", "1"),
        ("Кроки", "a3"),
        ("xml", '<w:p><w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve">Важливо: </w:t></w:r>'
                '<w:r><w:t xml:space="preserve">перевірте </w:t></w:r>'
                '<w:r><w:rPr><w:i/></w:rPr><w:t>кошторис</w:t></w:r>'
                '<w:r><w:rPr><w:b w:val="0"/></w:rPr><w:t xml:space="preserve"> і 2*2.</w:t></w:r></w:p>'),
        item("Відкрийте портал", 5),
        item("Подайте заявку", 5),
        item("Окремий маркер", 6),
        ("xml", '<w:p><w:r><w:t xml:space="preserve">Див. </w:t></w:r><w:hyperlink r:id="rId9">'
                '<w:r><w:t>повний регламент</w:t></w:r></w:hyperlink></w:p>'),
    ], parts={"word/styles.xml": styles, "word/numbering.xml": numbering,
              "word/_rels/document.xml.rels": rels})
    res = _send(client, token, "регламент.docx", data)
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["text"] == (
        "# Регламент\n\n## Кроки\n\n**Важливо:** перевірте *кошторис* і 2\\*2.\n\n"
        "1. Відкрийте портал\n2. Подайте заявку\n\n- Окремий маркер\n\n"
        "Див. [повний регламент](https://example.com/doc)")


def _bold(text):
    return ("xml", f'<w:p><w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve">{text}</w:t></w:r></w:p>')


_DECIMAL = (f'<w:numbering xmlns:w="{W}">'
            f'<w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:numFmt w:val="decimal"/></w:lvl></w:abstractNum>'
            f'<w:abstractNum w:abstractNumId="1"><w:lvl w:ilvl="0"><w:numFmt w:val="bullet"/></w:lvl></w:abstractNum>'
            f'<w:num w:numId="1"><w:abstractNumId w:val="1"/></w:num>'
            f'<w:num w:numId="2"><w:abstractNumId w:val="0"/></w:num></w:numbering>')


def _item(text, num):
    return ("xml", f'<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="{num}"/>'
                   f'</w:numPr></w:pPr><w:r><w:t>{text}</w:t></w:r></w:p>')


def test_docx_bold_lines_become_headings(client):
    """Документ без стилів заголовків, як FAQ від колег (фідбек, п. 3): назва й
    розділи — це просто жирні рядки. Вони мають стати заголовками, а
    нумерований список — отримати номери 1, 2, 3."""
    token = login(client, "u1", "pass")
    data = _docx([
        _bold("Питання та відповіді (FAQ)"),
        ("Відповіді на поширені запитання.", None),
        _bold("Не можу знайти матеріал"),
        ("Спробуйте:", None),
        _item("короткі ключові слова;", 1),
        _item("фільтри.", 1),
        _bold("Важливо:"),                       # двокрапка — вступ, не заголовок
        ("Не додавайте паролі.", None),
        _bold("Виявив неактуальну інформацію?"),
        _item("Натисніть «Повідомити».", 2),
        _item("Додайте посилання.", 2),
        _item("Опишіть проблему.", 2),
        _bold("Перший жирний рядок"),            # два поспіль — не заголовки
        _bold("Другий жирний рядок"),
        ("Текст.", None),
        _bold("Команда хабу"),                   # підпис наприкінці
    ], parts={"word/numbering.xml": _DECIMAL})
    res = _send(client, token, "faq.docx", data)
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["text"] == (
        "# Питання та відповіді (FAQ)\n\n"
        "Відповіді на поширені запитання.\n\n"
        "## Не можу знайти матеріал\n\n"
        "Спробуйте:\n\n"
        "- короткі ключові слова;\n- фільтри.\n\n"
        "**Важливо:**\n\n"
        "Не додавайте паролі.\n\n"
        "## Виявив неактуальну інформацію?\n\n"
        "1. Натисніть «Повідомити».\n2. Додайте посилання.\n3. Опишіть проблему.\n\n"
        "**Перший жирний рядок**\n\n"
        "## Другий жирний рядок\n\n"
        "Текст.\n\n"
        "**Команда хабу**")


def test_docx_with_heading_styles_keeps_bold_lines_bold(client):
    """Якщо в документі є справжні заголовки, жирний рядок — це виділення."""
    token = login(client, "u1", "pass")
    data = _docx([
        ("Регламент", "Heading1"),
        _bold("Увага"),
        ("Текст.", None),
    ])
    res = _send(client, token, "reg.docx", data)
    assert res.get_json()["text"] == "# Регламент\n\n**Увага**\n\nТекст."


def test_docx_images_land_in_text(client):
    """Картинки з документа Word стають картинками в тексті на своєму місці;
    формати, яких браузер не покаже (EMF), пропускаються."""
    from tests.test_files import PNG
    token = login(client, "u1", "pass")
    a = "http://schemas.openxmlformats.org/drawingml/2006/main"

    def picture(rid):
        return ("xml", f'<w:p><w:r><w:drawing><a:graphic xmlns:a="{a}"><a:graphicData>'
                       f'<a:blip r:embed="{rid}"/></a:graphicData></a:graphic></w:drawing></w:r></w:p>')

    rels = ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId7" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/image" Target="media/image1.png"/>'
            '<Relationship Id="rId8" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/image" Target="media/image2.emf"/></Relationships>')
    data = _docx([("Перед картинкою", None), picture("rId7"), picture("rId8"),
                  ("Після картинки", None)],
                 parts={"word/_rels/document.xml.rels": rels,
                        "word/media/image1.png": PNG, "word/media/image2.emf": b"\x01\x00\x00\x00"})
    res = _send(client, token, "з картинкою.docx", data)
    assert res.status_code == 200, res.get_json()
    body = res.get_json()
    assert len(body["images"]) == 1
    image = body["images"][0]
    assert body["text"] == (f"Перед картинкою\n\n![](/api/catalog/files/{image['id']}/preview)"
                            "\n\nПісля картинки")
    assert client.get(image["url"]).status_code == 200


def test_docx_that_is_not_a_zip(client):
    token = login(client, "u1", "pass")
    res = _send(client, token, "broken.docx", b"not a zip at all")
    assert res.status_code == 400
    assert res.get_json()["error"] == "extract_bad_file"


def test_pdf(client):
    pytest.importorskip("pypdf")
    token = login(client, "u1", "pass")
    res = _send(client, token, "case.pdf", _pdf("Hello Hub"))
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["kind"] == "pdf"
    assert "Hello Hub" in res.get_json()["text"]


def test_unsupported_extension(client):
    token = login(client, "u1", "pass")
    res = _send(client, token, "data.xlsx", b"PK\x03\x04")
    assert res.status_code == 400
    assert res.get_json()["error"] == "extract_unsupported"


def test_requires_file_and_auth(client):
    token = login(client, "u1", "pass")
    assert client.post(URL, headers=auth(token)).status_code == 400
    assert client.post(URL, data={"file": (io.BytesIO(b"x"), "a.txt")},
                       content_type="multipart/form-data").status_code == 401


def test_long_text_is_truncated_to_body_limit(client):
    token = login(client, "u1", "pass")
    res = _send(client, token, "big.txt", b"a" * 120_000)
    data = res.get_json()
    assert data["truncated"] is True
    assert len(data["text"]) == 100_000
