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
    rid = _resource(client, u1).get_json()["id"]
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
