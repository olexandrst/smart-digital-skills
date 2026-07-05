"""Реєстрація всіх Blueprints API під префіксом /api."""
from app.api.auth import bp as auth_bp
from app.api.users import bp as users_bp
from app.api.groups import bp as groups_bp
from app.api.skills import bp as skills_bp
from app.api.models import bp as models_bp
from app.api.chat import bp as chat_bp
from app.api.usage import bp as usage_bp
from app.api.files import bp as files_bp


def register_blueprints(app):
    app.register_blueprint(auth_bp, url_prefix="/api/auth")
    app.register_blueprint(users_bp, url_prefix="/api/users")
    app.register_blueprint(groups_bp, url_prefix="/api/groups")
    app.register_blueprint(skills_bp, url_prefix="/api/skills")
    app.register_blueprint(models_bp, url_prefix="/api/models")
    app.register_blueprint(chat_bp, url_prefix="/api/chat")
    app.register_blueprint(usage_bp, url_prefix="/api/usage")
    app.register_blueprint(files_bp, url_prefix="/api/files")
