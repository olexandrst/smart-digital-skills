"""Файли користувача: перелік, завантаження (upload), звантаження, видалення."""
from flask import Blueprint, request, jsonify, send_from_directory
from app.core.permissions import require_auth
from app.core.security import current_user
from app.core.errors import ApiError
from app.services import file_service

bp = Blueprint("files", __name__)


@bp.get("")
@require_auth
def list_files():
    user = current_user()
    return jsonify([f.to_dict() for f in file_service.list_files(user)])


@bp.post("")
@require_auth
def upload_file():
    """Завантаження довільного файлу користувачем (без скіла)."""
    user = current_user()
    file = request.files.get("file")
    if file is None or not file.filename:
        raise ApiError("Файл не надіслано (поле 'file')", 400, "validation_error")
    uf = file_service.save_bytes(user, file.filename, file.read(), source="upload")
    return jsonify(uf.to_dict()), 201


@bp.get("/<int:file_id>/download")
@require_auth
def download_file(file_id):
    user = current_user()
    uf = file_service.get_owned_file(user, file_id)
    directory = file_service.user_dir(user)
    import os
    return send_from_directory(
        directory, uf.stored_name, as_attachment=True,
        download_name=os.path.basename(uf.filename) or uf.stored_name)


@bp.delete("/<int:file_id>")
@require_auth
def delete_file(file_id):
    user = current_user()
    file_service.delete_file(user, file_id)
    return jsonify({"message": "Файл видалено"})
