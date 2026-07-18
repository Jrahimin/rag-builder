"""Failure-path coverage for upload type and signature validation."""

from __future__ import annotations

import io
import zipfile

import pytest

from app.core.exceptions import BadRequestError
from app.modules.knowledge.services.file_validation_service import FileValidationService

pytestmark = pytest.mark.unit


def test_rejects_unsupported_type_before_processing() -> None:
    with pytest.raises(BadRequestError, match="not supported") as exc_info:
        FileValidationService().validate(
            filename="payload.exe",
            content_type="application/octet-stream",
            file=io.BytesIO(b"MZ"),
        )
    assert exc_info.value.code == "document_type_unsupported"


def test_rejects_mime_signature_mismatch() -> None:
    with pytest.raises(BadRequestError) as exc_info:
        FileValidationService().validate(
            filename="invoice.pdf",
            content_type="application/pdf",
            file=io.BytesIO(b"not a pdf"),
        )
    assert exc_info.value.code == "document_signature_mismatch"


def test_rejects_password_protected_pdf() -> None:
    data = b"%PDF-1.7\n1 0 obj <</Encrypt 2 0 R>>\n%%EOF"
    with pytest.raises(BadRequestError) as exc_info:
        FileValidationService().validate(
            filename="locked.pdf", content_type="application/pdf", file=io.BytesIO(data)
        )
    assert exc_info.value.code == "document_password_protected"


def test_accepts_valid_docx_signature_and_container() -> None:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("word/document.xml", "<document />")
    content_type = FileValidationService().validate(
        filename="policy.docx",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        file=io.BytesIO(output.getvalue()),
    )
    assert content_type.endswith("wordprocessingml.document")
