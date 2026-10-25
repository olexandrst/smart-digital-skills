"""Вкладення до карток каталогу (FR-03): хто бачить, хто додає й видаляє.

Особистого сховища файлів більше немає — файл існує лише як вкладення
картки й видимий рівно тим, хто бачить саму картку.
"""
import io
import os
from tests.conftest import login, auth


def test_users_get_distinct_storage_uid(app):
    from app.models import User
    from app.services import file_service
    u1 = User.query.filter_by(username="u1").first()
    u2 = User.query.filter_by(username="u2").first()
    d1 = file_service.user_dir(u1)
    d2 = file_service.user_dir(u2)
    assert d1 != d2
    assert u1.storage_uid and u2.storage_uid and u1.storage_uid != u2.storage_uid


def test_public_files_route_is_gone(client):
    """Колишній маршрут /files/<GUID>/<FILE> віддавав файл без авторизації."""
    admin = login(client, "admin", "Admin123!")
    rid = _resource(client, admin).get_json()["id"]
    _attach(client, admin, rid)
    from app.models import UserFile
    uf = UserFile.query.filter_by(resource_id=rid).one()
    assert client.get(f"/files/{uf.user.storage_uid}/{uf.stored_name}").status_code == 404
    assert "url" not in uf.to_dict()


# ------------------ Вкладення до картки каталогу (FR-03) ------------------

def _resource(client, token, **over):
    payload = {"resource_type": "prompt", "name": "Промпт", "description": "Опис",
               "body": "Текст", "status": "published"}
    payload.update(over)
    return client.post("/api/catalog/resources", json=payload, headers=auth(token))


def _attach(client, token, rid, name="guide.txt", content=b"how to"):
    return client.post(f"/api/catalog/resources/{rid}/files", headers=auth(token),
                       data={"file": (io.BytesIO(content), name)},
                       content_type="multipart/form-data")


def test_attachment_visible_to_everyone_who_sees_card(client):
    admin = login(client, "admin", "Admin123!")
    rid = _resource(client, admin).get_json()["id"]
    assert _attach(client, admin, rid).status_code == 201

    u1 = login(client, "u1", "pass")
    files = client.get(f"/api/catalog/resources/{rid}/files", headers=auth(u1)).get_json()
    assert [f["filename"] for f in files] == ["guide.txt"]

    dl = client.get(files[0]["download_url"], headers=auth(u1))
    assert dl.status_code == 200
    assert dl.data == b"how to"


def test_plain_user_may_not_attach_to_foreign_card(client):
    admin = login(client, "admin", "Admin123!")
    rid = _resource(client, admin).get_json()["id"]
    assert _attach(client, login(client, "u1", "pass"), rid).status_code == 403


def test_author_attaches_to_own_draft(client):
    """Автор наповнює свою чернетку файлами ще до того, як її опублікують."""
    u1 = login(client, "u1", "pass")
    rid = _resource(client, u1, status="draft").get_json()["id"]
    assert _attach(client, u1, rid).status_code == 201
    files = client.get(f"/api/catalog/resources/{rid}/files", headers=auth(u1)).get_json()
    assert [f["filename"] for f in files] == ["guide.txt"]
    # Чужий користувач не бачить ні чернетки, ні її файлів.
    u2 = login(client, "u2", "pass")
    assert client.get(f"/api/catalog/resources/{rid}/files",
                      headers=auth(u2)).status_code == 403


def test_manager_may_attach_and_delete(client):
    admin = login(client, "admin", "Admin123!")
    rid = _resource(client, admin).get_json()["id"]
    fid = _attach(client, admin, rid).get_json()["id"]

    sm = login(client, "sm", "pass")          # інший менеджер каталогу
    assert client.delete(f"/api/catalog/resources/{rid}/files/{fid}",
                         headers=auth(sm)).status_code == 200
    assert client.get(f"/api/catalog/resources/{rid}/files",
                      headers=auth(admin)).get_json() == []


def test_plain_user_may_not_delete_attachment(client):
    admin = login(client, "admin", "Admin123!")
    rid = _resource(client, admin).get_json()["id"]
    fid = _attach(client, admin, rid).get_json()["id"]
    assert client.delete(f"/api/catalog/resources/{rid}/files/{fid}",
                         headers=auth(login(client, "u1", "pass"))).status_code == 403


def test_deleting_card_removes_its_attachments(client, app):
    from app.models import UserFile
    admin = login(client, "admin", "Admin123!")
    rid = _resource(client, admin).get_json()["id"]
    fid = _attach(client, admin, rid).get_json()["id"]

    with app.app_context():
        stored = UserFile.query.get(fid)
        path = file_abs_path_for(app, stored)
        assert os.path.exists(path)

    assert client.delete(f"/api/catalog/resources/{rid}",
                         headers=auth(admin)).status_code == 200
    with app.app_context():
        assert UserFile.query.get(fid) is None
    assert not os.path.exists(path)


def file_abs_path_for(app, uf):
    """Фізичний шлях до файлу вкладення (для перевірки, що він зник з диска)."""
    from app.services import file_service
    with app.app_context():
        return os.path.join(file_service.user_dir(uf.user), uf.stored_name)


def test_attachment_of_hidden_card_not_listed(client):
    admin = login(client, "admin", "Admin123!")
    rid = _resource(client, admin, status="draft").get_json()["id"]
    _attach(client, admin, rid)
    assert client.get(f"/api/catalog/resources/{rid}/files",
                      headers=auth(login(client, "u1", "pass"))).status_code == 403


# ------------------ Перегляд зображень і відео з вкладень ------------------

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
       b"\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01"
       b"\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")


def _media_card(client):
    admin = login(client, "admin", "Admin123!")
    rid = _resource(client, admin).get_json()["id"]
    _attach(client, admin, rid, "schema.png", PNG)
    _attach(client, admin, rid, "demo.mp4", b"\x00\x00\x00\x18ftypmp42" + b"0" * 2000)
    _attach(client, admin, rid, "notes.docx", b"PK docx")
    _attach(client, admin, rid, "logo.svg", b"<svg onload='alert(1)'/>")
    return admin, rid


def test_images_and_videos_get_preview_links(client):
    admin, rid = _media_card(client)
    files = {f["filename"]: f for f in client.get(
        f"/api/catalog/resources/{rid}/files", headers=auth(login(client, "u1", "pass"))).get_json()}
    assert files["schema.png"]["media"] == "image" and files["schema.png"]["preview_url"]
    assert files["demo.mp4"]["media"] == "video" and files["demo.mp4"]["preview_url"]
    # Документи й SVG (може містити скрипти) переглядом не відкриваються.
    for name in ("notes.docx", "logo.svg"):
        assert files[name]["media"] is None and files[name]["preview_url"] is None


def test_preview_serves_media_without_jwt_and_safely(client):
    admin, rid = _media_card(client)
    files = {f["filename"]: f for f in client.get(
        f"/api/catalog/resources/{rid}/files", headers=auth(admin)).get_json()}
    img = client.get(files["schema.png"]["preview_url"])     # без заголовка Authorization
    assert img.status_code == 200
    assert img.mimetype == "image/png" and img.data == PNG
    assert img.headers["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in img.headers["Content-Security-Policy"]
    assert "attachment" not in img.headers.get("Content-Disposition", "")
    # Відео можна перемотувати: сервер відповідає на Range частиною файлу.
    part = client.get(files["demo.mp4"]["preview_url"], headers={"Range": "bytes=0-99"})
    assert part.status_code == 206 and len(part.data) == 100
    assert part.mimetype == "video/mp4"


def test_preview_link_cannot_be_forged_or_reused_for_other_file(client):
    admin, rid = _media_card(client)
    files = client.get(f"/api/catalog/resources/{rid}/files", headers=auth(admin)).get_json()
    url = next(f["preview_url"] for f in files if f["filename"] == "schema.png")
    other = next(f["id"] for f in files if f["filename"] == "demo.mp4")
    sig = url.split("sig=", 1)[1]
    assert client.get(f"/api/catalog/files/{other}/preview?sig={sig}").status_code == 403
    assert client.get(url[:-3] + "xyz").status_code == 403
    assert client.get(url.split("?")[0]).status_code == 403


def test_preview_link_expires(client, monkeypatch):
    from app.services import file_service
    admin, rid = _media_card(client)
    url = next(f["preview_url"] for f in client.get(
        f"/api/catalog/resources/{rid}/files", headers=auth(admin)).get_json()
        if f["filename"] == "schema.png")
    monkeypatch.setattr(file_service, "PREVIEW_MAX_AGE", -1)
    assert client.get(url).status_code == 403


def test_preview_respects_card_visibility(client):
    """Користувач отримав посилання, а картку потім зняли з публікації —
    за старим посиланням він файл уже не відкриє."""
    admin, rid = _media_card(client)
    u1 = login(client, "u1", "pass")
    url = next(f["preview_url"] for f in client.get(
        f"/api/catalog/resources/{rid}/files", headers=auth(u1)).get_json()
        if f["filename"] == "schema.png")
    assert client.get(url).status_code == 200
    client.post(f"/api/catalog/resources/{rid}/status", headers=auth(admin),
                json={"status": "draft"})
    assert client.get(url).status_code == 403


def test_preview_refuses_files_without_preview(client, app):
    from app.models import UserFile, User
    from app.services import file_service
    admin, rid = _media_card(client)
    with app.app_context():
        doc = UserFile.query.filter_by(resource_id=rid, filename="logo.svg").one()
        url = file_service.preview_url(doc, User.query.filter_by(username="admin").one())
    assert client.get(url).status_code == 415


# ------------------ Зображення в тексті матеріалу (п. 3.2) ------------------

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00"
       b"\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18"
       b"\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")


def _image(client, token, name="screen.png", content=PNG):
    return client.post("/api/catalog/images", headers=auth(token),
                       data={"file": (io.BytesIO(content), name)},
                       content_type="multipart/form-data")


def test_inline_image_upload_and_pending_preview(client):
    """Картинка для ще не збереженого матеріалу: постійне посилання для
    тексту й підписане для показу, яке працює лише для автора."""
    u1 = login(client, "u1", "pass")
    res = _image(client, u1)
    assert res.status_code == 201, res.get_json()
    data = res.get_json()
    assert data["ref"] == f"/api/catalog/files/{data['id']}/preview"
    img = client.get(data["url"])
    assert img.status_code == 200 and img.data == PNG
    assert img.headers["Content-Type"] == "image/png"
    # Чужий підпис на цей файл не підробити, а без підпису — відмова.
    assert client.get(data["ref"]).status_code == 403


def test_inline_image_must_really_be_an_image(client):
    u1 = login(client, "u1", "pass")
    res = _image(client, u1, name="fake.png", content=b"<svg onload=alert(1)>")
    assert res.status_code == 400
    assert res.get_json()["error"] == "unsupported_media"


def test_inline_image_attached_on_save_and_removed_with_text(client, app):
    """Під час збереження картинка прив'язується до картки; прибрана з тексту —
    видаляється разом із файлом. У вкладеннях вона позначена як inline."""
    from app.models import UserFile
    from app.services import file_service
    u1 = login(client, "u1", "pass")
    img = _image(client, u1).get_json()
    body = f"## Як подати заявку\n\n![Форма заявки]({img['ref']})\n\nЗаповніть поля."
    rid = _resource(client, u1, resource_type="instruction", body=body,
                    status="draft").get_json()["id"]
    uf = UserFile.query.get(img["id"])
    assert uf.resource_id == rid and uf.source == "inline"
    files = client.get(f"/api/catalog/resources/{rid}/files", headers=auth(u1)).get_json()
    assert [(f["id"], f["source"], f["media"]) for f in files] == [(img["id"], "inline", "image")]
    assert files[0]["preview_url"]

    path = os.path.join(file_service.user_dir(uf.user), uf.stored_name)
    assert os.path.exists(path)
    upd = client.patch(f"/api/catalog/resources/{rid}", json={"body": "Без картинки"},
                       headers=auth(u1))
    assert upd.status_code == 200
    assert UserFile.query.get(img["id"]) is None
    assert not os.path.exists(path)


def test_foreign_pending_image_not_claimed(client):
    """Посилання на чужу картинку в тексті не прив'язує її до своєї картки."""
    from app.models import UserFile
    u1, u2 = login(client, "u1", "pass"), login(client, "u2", "pass")
    img = _image(client, u1).get_json()
    _resource(client, u2, resource_type="instruction", body=f"![]({img['ref']})", status="draft")
    assert UserFile.query.get(img["id"]).resource_id is None


def test_attached_image_follows_card_visibility(client):
    """Картинку чернетки бачать лише ті, хто бачить саму чернетку."""
    u1, u2 = login(client, "u1", "pass"), login(client, "u2", "pass")
    img = _image(client, u1).get_json()
    rid = _resource(client, u1, resource_type="instruction", body=f"![]({img['ref']})",
                    status="draft").get_json()["id"]
    assert client.get(f"/api/catalog/resources/{rid}/files", headers=auth(u2)).status_code == 403
    assert client.get(img["url"]).status_code == 200      # автор


def test_stale_pending_images_purged(client, app):
    """Картинки з покинутого майстра прибираються через дві доби."""
    from datetime import datetime, timedelta
    from app.extensions import db
    from app.models import UserFile
    u1 = login(client, "u1", "pass")
    old = _image(client, u1).get_json()
    UserFile.query.get(old["id"]).created_at = datetime.utcnow() - timedelta(days=3)
    db.session.commit()
    _image(client, u1)
    assert UserFile.query.get(old["id"]) is None
