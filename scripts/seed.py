"""Ідемпотентне наповнення початковими даними (п.8 архітектури).

Запуск: python -m scripts.seed
Створює: ролі, супер-адміна, демо-моделі, демо-скіл, демо-групу та демо-користувачів.
"""
import os
import json
from app import create_app
from app.extensions import db
from app.core.security import hash_password
from app.models import (
    Role, User, UserRole, Group, GroupMembership,
    Model, Skill, SkillInput,
)
from app.services import skill_service


def _get_or_create(model, defaults=None, **kwargs):
    instance = model.query.filter_by(**kwargs).first()
    if instance:
        return instance, False
    params = dict(kwargs)
    params.update(defaults or {})
    instance = model(**params)
    db.session.add(instance)
    db.session.flush()
    return instance, True


def seed():
    app = create_app()
    with app.app_context():
        db.create_all()

        # 1. Ролі
        admin_role, _ = _get_or_create(
            Role, code="admin",
            defaults={"name": "Адміністратор системи",
                      "description": "Повний контроль над системою"})
        sm_role, _ = _get_or_create(
            Role, code="skill_manager",
            defaults={"name": "Skill-менеджер",
                      "description": "Управління скілами"})

        # 2. Супер-адмін
        admin_username = os.getenv("ADMIN_USERNAME", "admin")
        admin_password = os.getenv("ADMIN_PASSWORD", "Admin123!")
        admin, created = _get_or_create(
            User, username=admin_username,
            defaults={
                "full_name": "Системний адміністратор",
                "email": "admin@profihub.local",
                "password_hash": hash_password(admin_password),
                "is_system_admin": True,
                "is_active": True,
            })
        if created:
            db.session.add(UserRole(user_id=admin.id, role_id=admin_role.id))

        # 3. Демо Skill-менеджер
        sm_user, sm_created = _get_or_create(
            User, username="skillmanager",
            defaults={
                "full_name": "Демо Skill-менеджер",
                "email": "skill@profihub.local",
                "password_hash": hash_password("Skill123!"),
                "is_active": True,
            })
        if sm_created:
            db.session.add(UserRole(user_id=sm_user.id, role_id=sm_role.id))

        # 4. Демо-користувачі (мембери)
        member1, _ = _get_or_create(
            User, username="user1",
            defaults={"full_name": "Олена Мембер",
                      "email": "user1@profihub.local",
                      "password_hash": hash_password("User123!"),
                      "is_active": True})
        member2, _ = _get_or_create(
            User, username="user2",
            defaults={"full_name": "Іван Мембер",
                      "email": "user2@profihub.local",
                      "password_hash": hash_password("User123!"),
                      "is_active": True})

        # 5. Демо-моделі (різні провайдери)
        llm, _ = _get_or_create(
            Model, name="gpt-4o (Azure)",
            defaults={"model_type": "llm", "provider": "azure_ai_foundry",
                      "deployment_name": "gpt-4o",
                      "api_version": "2024-02-15-preview",
                      "context_window": 128000,
                      "config": json.dumps({"temperature": 0.7})})
        _get_or_create(
            Model, name="gpt-4o-mini (OpenAI)",
            defaults={"model_type": "llm", "provider": "openai",
                      "deployment_name": "gpt-4o-mini",
                      "context_window": 128000,
                      "config": json.dumps({"temperature": 0.7})})
        _get_or_create(
            Model, name="gemini-1.5-pro (Gemini)",
            defaults={"model_type": "llm", "provider": "gemini",
                      "deployment_name": "gemini-1.5-pro",
                      "context_window": 1000000,
                      "config": json.dumps({"temperature": 0.7})})
        _get_or_create(
            Model, name="image-analyzer",
            defaults={"model_type": "cv", "provider": "azure_ai_foundry",
                      "deployment_name": "image-analyzer",
                      "config": json.dumps({})})

        # 6. Демо-скіл Summarizer (published)
        skill, skill_created = _get_or_create(
            Skill, name="Summarizer",
            defaults={
                "description": "Стисло підсумовує наданий текст.",
                "author": "Smart Digital Skills",
                "category": "Текст",
                "model_id": llm.id,
                "prompt_template": "Зроби стислий підсумок тексту мовою {language}:\n\n{text}",
                "parameters": json.dumps({"temperature": 0.3}),
                "status": "published",
                "version": "1.0.0",
                "created_by": admin.id,
            })
        if skill_created:
            db.session.add(SkillInput(
                skill_id=skill.id, name="text", label="Текст",
                data_type="string", is_required=True,
                description="Текст для опрацювання", position=0))
            db.session.add(SkillInput(
                skill_id=skill.id, name="language", label="Мова відповіді",
                data_type="string", is_required=False,
                default_value="українською", position=1))

        # 7. Демо-група з адміном-менеджером (правило безперервності управління)
        group, group_created = _get_or_create(
            Group, name="Default Department",
            defaults={"description": "Демонстраційний відділ",
                      "created_by": admin.id})
        if group_created:
            db.session.add(GroupMembership(
                group_id=group.id, user_id=admin.id,
                role="manager", status="active"))
            db.session.add(GroupMembership(
                group_id=group.id, user_id=member1.id,
                role="member", status="active"))
        db.session.commit()

        # 8. Призначення демо-скіла групі + самостійна активація для member2
        skill_service.assign_to_group(group.id, skill.id, admin.id)
        skill_service.self_activate(member2.id, skill.id)

        # 9. Демо-скіл-ПАКЕТ (виконання Python-коду з архіву)
        _seed_package_skill(admin)

        print("✓ Seed завершено.")
        print(f"  Адмін:          {admin_username} / {admin_password}")
        print("  Skill-менеджер: skillmanager / Skill123!")
        print("  Користувачі:    user1 / User123!,  user2 / User123!")


def _zip_dir_to_bytes(src_dir):
    """Пакує вміст теки у zip-байти (шляхи відносні до src_dir)."""
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(src_dir):
            for fname in files:
                full = os.path.join(root, fname)
                rel = os.path.relpath(full, src_dir)
                zf.write(full, rel)
    return buf.getvalue()


def _seed_package_skill(admin):
    """Збирає приклад examples/skills/word-counter у архів та реєструє як скіл-пакет."""
    from datetime import datetime
    from app.services import package_service
    from app.models import SkillInput

    if Skill.query.filter_by(name="Word Counter").first():
        return

    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
    example_dir = os.path.join(base_dir, "examples", "skills", "word-counter")
    if not os.path.isdir(example_dir):
        print("  (приклад word-counter не знайдено — пропускаю скіл-пакет)")
        return

    file_bytes = _zip_dir_to_bytes(example_dir)
    meta = package_service.parse_package(file_bytes)

    skill = Skill(
        name=meta["name"], description=meta["description"],
        author=meta.get("author") or "Smart Digital Skills",
        category=meta.get("category") or "Загальне",
        skill_kind="package", runtime=meta["runtime"], entrypoint=meta["entrypoint"],
        version=meta["version"], prompt_template=meta.get("instructions"),
        status="published", published_at=datetime.utcnow(),
        package_filename="word-counter.zip", created_by=admin.id,
    )
    db.session.add(skill)
    db.session.flush()
    skill.package_path = package_service.store_package(skill.id, file_bytes, "word-counter.zip")
    for spec in meta["inputs"]:
        db.session.add(SkillInput(
            skill_id=skill.id, name=spec["name"], label=spec.get("label"),
            data_type=spec.get("data_type", "string"),
            is_required=spec.get("is_required", True),
            default_value=spec.get("default_value"),
            description=spec.get("description"), position=spec.get("position", 0)))
    db.session.commit()


if __name__ == "__main__":
    seed()
