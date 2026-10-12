"""Реєстрація всіх Blueprints API під префіксом /api."""
from app.api.auth import bp as auth_bp
from app.api.users import bp as users_bp
from app.api.skills import bp as skills_bp
from app.api.categories import bp as categories_bp
from app.api.catalog import bp as catalog_bp
from app.api.ideas import bp as ideas_bp
from app.api.paths import bp as paths_bp


def register_blueprints(app):
    app.register_blueprint(auth_bp, url_prefix="/api/auth")
    app.register_blueprint(users_bp, url_prefix="/api/users")
    app.register_blueprint(skills_bp, url_prefix="/api/skills")
    app.register_blueprint(categories_bp, url_prefix="/api/categories")
    app.register_blueprint(catalog_bp, url_prefix="/api/catalog")
    app.register_blueprint(ideas_bp, url_prefix="/api/ideas")
    app.register_blueprint(paths_bp, url_prefix="/api/paths")
