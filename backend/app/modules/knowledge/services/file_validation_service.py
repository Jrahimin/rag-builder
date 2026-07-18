"""Supported-type, MIME/signature, corruption, and password validation."""

from __future__ import annotations

import io
import os
import zipfile
from typing import BinaryIO

from app.core.exceptions import BadRequestError

_MIME_BY_EXTENSION = {
    ".pdf": ("application/pdf",),
    ".docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document",),
    ".txt": ("text/plain",),
    ".md": ("text/markdown", "text/plain"),
    ".markdown": ("text/markdown", "text/plain"),
    ".png": ("image/png",),
    ".jpg": ("image/jpeg",),
    ".jpeg": ("image/jpeg",),
    ".tif": ("image/tiff",),
    ".tiff": ("image/tiff",),
    ".webp": ("image/webp",),
}


class FileValidationService:
    def validate(self, *, filename: str, content_type: str | None, file: BinaryIO) -> str:
        extension = os.path.splitext(filename.lower())[1]
        supported_mimes = _MIME_BY_EXTENSION.get(extension)
        if supported_mimes is None:
            raise BadRequestError(
                message="The uploaded file type is not supported.", code="document_type_unsupported"
            )
        declared = (content_type or "").split(";", 1)[0].strip().lower()
        if declared and declared not in supported_mimes:
            raise BadRequestError(
                message="The declared MIME type does not match the filename.",
                code="document_mime_mismatch",
            )
        file.seek(0)
        data = file.read()
        file.seek(0)
        if not data:
            raise BadRequestError(message="The uploaded file is empty.", code="document_empty")
        if extension == ".pdf":
            if not data.startswith(b"%PDF-"):
                self._signature_mismatch()
            if b"/Encrypt" in data:
                raise BadRequestError(
                    message="Password-protected PDFs are not supported.",
                    code="document_password_protected",
                )
            if b"%%EOF" not in data[-4096:]:
                raise BadRequestError(
                    message="The PDF is corrupt or incomplete.", code="document_corrupt"
                )
        elif extension == ".docx":
            if not data.startswith(b"PK"):
                self._signature_mismatch()
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    if (
                        archive.testzip() is not None
                        or "[Content_Types].xml" not in archive.namelist()
                    ):
                        raise BadRequestError(
                            message="The DOCX file is corrupt.", code="document_corrupt"
                        )
            except (zipfile.BadZipFile, RuntimeError) as exc:
                raise BadRequestError(
                    message="The DOCX file is corrupt or password-protected.",
                    code="document_corrupt",
                ) from exc
        elif extension in {".txt", ".md", ".markdown"}:
            if b"\x00" in data:
                self._signature_mismatch()
            try:
                data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise BadRequestError(
                    message="Text uploads must be valid UTF-8.", code="document_corrupt"
                ) from exc
        else:
            signatures = {
                ".png": (b"\x89PNG\r\n\x1a\n",),
                ".jpg": (b"\xff\xd8\xff",),
                ".jpeg": (b"\xff\xd8\xff",),
                ".tif": (b"II*\x00", b"MM\x00*"),
                ".tiff": (b"II*\x00", b"MM\x00*"),
                ".webp": (b"RIFF",),
            }
            if not any(data.startswith(signature) for signature in signatures[extension]):
                self._signature_mismatch()
            if extension == ".webp" and data[8:12] != b"WEBP":
                self._signature_mismatch()
        return supported_mimes[0]

    @staticmethod
    def _signature_mismatch() -> None:
        raise BadRequestError(
            message="The file signature does not match its declared type.",
            code="document_signature_mismatch",
        )
