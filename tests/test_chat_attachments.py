"""Тести вкладень у чат (зображення/PDF) — лише для моделей Azure OpenAI."""
import io
from app.models import Model, Skill, User
from tests.conftest import login, auth


def _upload(client, token, name, data=b"binary-bytes"):
    res = client.post("/api/files", headers=auth(token),
                      data={"file": (io.BytesIO(data), name)},
                      content_type="multipart/form-data")
    return res.get_json()


def _azure_session(client, token):
    # Seed-модель gpt-4o — Azure OpenAI + системна (доступна всім).
    mid = Model.query.filter_by(name="gpt-4o").first().id
    return client.post("/api/chat/sessions", headers=auth(token),
                       json={"model_id": mid}).get_json()["id"]


def test_image_attachment_on_azure(client):
    token = login(client, "u1", "pass")
    f = _upload(client, token, "photo.png")
    assert f["content_type"] == "image/png"
    sid = _azure_session(client, token)
    res = client.post(f"/api/chat/sessions/{sid}/messages", headers=auth(token),
                      json={"content": "Що на зображенні?", "file_ids": [f["id"]]})
    assert res.status_code == 200

    # Вкладення прив'язане до повідомлення користувача й показується при перевідкритті.
    sess = client.get(f"/api/chat/sessions/{sid}", headers=auth(token)).get_json()
    umsg = next(m for m in sess["messages"] if m["role"] == "user")
    assert [x["id"] for x in umsg["files"]] == [f["id"]]
    # Файл лишається доступним у «Файли».
    assert any(x["id"] == f["id"] for x in client.get("/api/files", headers=auth(token)).get_json())


def test_pdf_attachment_on_azure(client):
    token = login(client, "u1", "pass")
    f = _upload(client, token, "report.pdf", b"%PDF-1.4 ...")
    assert f["content_type"] == "application/pdf"
    sid = _azure_session(client, token)
    res = client.post(f"/api/chat/sessions/{sid}/messages", headers=auth(token),
                      json={"content": "Підсумуй документ", "file_ids": [f["id"]]})
    assert res.status_code == 200


def test_text_only_message_still_works(client):
    token = login(client, "u1", "pass")
    sid = _azure_session(client, token)
    res = client.post(f"/api/chat/sessions/{sid}/messages", headers=auth(token),
                      json={"content": "Просто привіт"})
    assert res.status_code == 200


def test_unsupported_attachment_type_rejected(client):
    token = login(client, "u1", "pass")
    f = _upload(client, token, "notes.txt")
    sid = _azure_session(client, token)
    res = client.post(f"/api/chat/sessions/{sid}/messages", headers=auth(token),
                      json={"content": "ось файл", "file_ids": [f["id"]]})
    assert res.status_code == 400


def test_attachment_rejected_on_local_model(client):
    admin = login(client, "admin", "Admin123!")
    token = login(client, "u1", "pass")
    # Локальна модель + доступ через групу.
    mid = client.post("/api/models", headers=auth(admin), json={
        "name": "Local", "provider": "ollama", "deployment_name": "llama3.1",
        "base_url": "http://localhost:11434/v1"}).get_json()["id"]
    gid = client.post("/api/groups", headers=auth(admin), json={"name": "G"}).get_json()["id"]
    uid = User.query.filter_by(username="u1").first().id
    client.post(f"/api/groups/{gid}/members", headers=auth(admin), json={"user_id": uid})
    client.post("/api/models/access", headers=auth(admin),
                json={"group_id": gid, "model_id": mid, "granted": True})

    sid = client.post("/api/chat/sessions", headers=auth(token),
                      json={"model_id": mid}).get_json()["id"]
    f = _upload(client, token, "photo.png")
    res = client.post(f"/api/chat/sessions/{sid}/messages", headers=auth(token),
                      json={"content": "глянь", "file_ids": [f["id"]]})
    assert res.status_code == 400
    assert res.get_json()["error"] == "attachments_unsupported"


def test_attachment_with_skill_extracts_then_runs(client):
    """Вкладення + навичка: крок вилучення (vision) → навичка на витягнутому
    тексті. Токени двох викликів підсумовуються; файл прив'язано до повідомлення."""
    from app.models import TokenUsageLog
    token = login(client, "u1", "pass")
    skill = Skill.query.filter_by(name="Summarizer").first()
    client.post(f"/api/skills/{skill.id}/activate", headers=auth(token))
    sid = _azure_session(client, token)

    # Той самий запуск навички без файлу — для порівняння обсягу токенів.
    plain = client.post(f"/api/chat/sessions/{sid}/messages", headers=auth(token),
                        json={"content": "стисни це", "skill_id": skill.id}).get_json()

    f = _upload(client, token, "doc.pdf", b"%PDF-1.4 ...")
    res = client.post(f"/api/chat/sessions/{sid}/messages", headers=auth(token),
                      json={"content": "стисни документ", "skill_id": skill.id,
                            "file_ids": [f["id"]]})
    assert res.status_code == 200
    data = res.get_json()
    assert data["mode"] == "offline"
    # Два виклики (вилучення + навичка) → токенів більше, ніж за один.
    assert data["usage"]["total_tokens"] > plain["usage"]["total_tokens"]

    log = TokenUsageLog.query.filter_by(is_system=False).order_by(
        TokenUsageLog.id.desc()).first()
    assert log.skill_id == skill.id and log.total_tokens == data["usage"]["total_tokens"]

    # Вкладення прив'язане до повідомлення користувача й лишається у «Файли».
    sess = client.get(f"/api/chat/sessions/{sid}", headers=auth(token)).get_json()
    umsgs = [m for m in sess["messages"] if m["role"] == "user"]
    assert any(x["id"] == f["id"] for m in umsgs for x in m["files"])


def test_attachment_with_skill_still_rejects_local_model(client):
    """Навичка + вкладення на не-Azure моделі сесії — усе одно 400 (потрібен vision)."""
    admin = login(client, "admin", "Admin123!")
    token = login(client, "u1", "pass")
    mid = client.post("/api/models", headers=auth(admin), json={
        "name": "Local", "provider": "ollama", "deployment_name": "llama3.1",
        "base_url": "http://localhost:11434/v1"}).get_json()["id"]
    gid = client.post("/api/groups", headers=auth(admin), json={"name": "G"}).get_json()["id"]
    uid = User.query.filter_by(username="u1").first().id
    client.post(f"/api/groups/{gid}/members", headers=auth(admin), json={"user_id": uid})
    client.post("/api/models/access", headers=auth(admin),
                json={"group_id": gid, "model_id": mid, "granted": True})
    skill = Skill.query.filter_by(name="Summarizer").first()
    client.post(f"/api/skills/{skill.id}/activate", headers=auth(token))

    sid = client.post("/api/chat/sessions", headers=auth(token),
                      json={"model_id": mid}).get_json()["id"]
    f = _upload(client, token, "photo.png")
    res = client.post(f"/api/chat/sessions/{sid}/messages", headers=auth(token),
                      json={"content": "стисни", "skill_id": skill.id, "file_ids": [f["id"]]})
    assert res.status_code == 400
    assert res.get_json()["error"] == "attachments_unsupported"


def test_cannot_attach_other_users_file(client):
    u1 = login(client, "u1", "pass")
    u2 = login(client, "u2", "pass")
    other = _upload(client, u2, "photo.png")
    sid = _azure_session(client, u1)
    res = client.post(f"/api/chat/sessions/{sid}/messages", headers=auth(u1),
                      json={"content": "чуже", "file_ids": [other["id"]]})
    assert res.status_code == 404
