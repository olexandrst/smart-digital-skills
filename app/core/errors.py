"""Уніфікована обробка помилок API."""
from flask import jsonify


class ApiError(Exception):
    def __init__(self, message, status_code=400, code=None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code or "error"


def register_error_handlers(app):
    @app.errorhandler(ApiError)
    def handle_api_error(err):
        return jsonify({"error": err.code, "message": err.message}), err.status_code

    @app.errorhandler(404)
    def handle_404(err):
        return jsonify({"error": "not_found", "message": "Ресурс не знайдено"}), 404

    @app.errorhandler(405)
    def handle_405(err):
        return jsonify({"error": "method_not_allowed",
                        "message": "Метод не дозволено"}), 405

    @app.errorhandler(500)
    def handle_500(err):
        return jsonify({"error": "internal_error",
                        "message": "Внутрішня помилка сервера"}), 500
