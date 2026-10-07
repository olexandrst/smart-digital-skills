"""Витяг тексту з файлу для майстра створення матеріалу (.txt/.md/.docx/.pdf)."""
import io
import zipfile

import pytest

from tests.conftest import login, auth

URL = "/api/catalog/resources/extract-text"


def _send(client, token, name, data):
    return client.post(URL, headers=auth(token),
                       data={"file": (io.BytesIO(data), name)},
                       content_type="multipart/form-data")


def _docx(paragraphs):
    """Мінімальний .docx: лише word/document.xml і типи вмісту.

    paragraphs — список (текст, стиль|None, список?). Таблиця додається окремо
    рядком-кортежем ("table", [[...], [...]]).
    """
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

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
        else:
            body.append(para(*item))
    xml = (f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{w}">'
           f'<w:body>{"".join(body)}</w:body></w:document>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml",
                    '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
                    'package/2006/content-types"/>')
        zf.writestr("word/document.xml", xml)
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
        "# Інструкція\n## Вступ\nЗвичайний абзац\n- Перший пункт\n- Другий пункт\n"
        "Поле | Значення\nМодель | gpt-4o")


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
