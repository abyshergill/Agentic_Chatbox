"""Attachment storage. Files go to a private folder and are never executed."""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from pathlib import Path

from .fs_guard import is_secret_name
from .permissions import PermissionManager
from .redact import redact

DEFAULT_EXTENSIONS = {"py", "js", "ts", "md", "txt", "json", "csv", "pdf"}
CHUNK = 1024 * 1024


class UploadRejected(Exception):
    pass


@dataclass
class Attachment:
    id: str
    name: str
    path: Path
    size: int
    kind: str


def upload_rules(permissions: PermissionManager) -> tuple[set[str], int]:
    decision = permissions.check("uploads", "upload")
    if not decision.allowed:
        raise UploadRejected(decision.reason)
    app = permissions.app("uploads")
    extensions = {item.lower().lstrip(".") for item in app.csv("Extensions")} or DEFAULT_EXTENSIONS
    try:
        megabytes = float(app.fields.get("Max Size MB", "25"))
    except ValueError:
        megabytes = 25
    return extensions, int(megabytes * CHUNK)


def clean_filename(name: str) -> str:
    base = Path(name.replace("\\", "/")).name
    base = re.sub(r"[^\w.\- ]", "_", base).strip(" .")
    if not base:
        raise UploadRejected("the file name is not usable")
    return base[:120]


def validate_name(name: str, extensions: set[str]) -> str:
    cleaned = clean_filename(name)
    original = Path(name.replace("\\", "/")).name  # Cleaning strips leading dots, so check the original too.
    if is_secret_name(cleaned) or is_secret_name(original):
        raise UploadRejected(f"'{cleaned}' looks like a secret and was not accepted")
    extension = cleaned.rsplit(".", 1)[-1].lower() if "." in cleaned else ""
    if extension not in extensions:
        raise UploadRejected(f"'.{extension}' files are not accepted. Allowed: {', '.join(sorted(extensions))}")
    return cleaned


def validate_content(name: str, head: bytes) -> str:
    """Checks that the bytes match the claimed type. Returns the attachment kind."""
    if name.lower().endswith(".pdf"):
        if not head.startswith(b"%PDF-"):
            raise UploadRejected(f"'{name}' does not contain a valid PDF")
        return "pdf"
    lowered = name.lower()
    if lowered.endswith((".xlsx", ".docx", ".odt")):
        if not head.startswith(b"PK\x03\x04"):  # These are zip archives.
            raise UploadRejected(f"'{name}' is not a valid {lowered.rsplit('.', 1)[-1]} file")
        return "excel" if lowered.endswith(".xlsx") else "document"
    if lowered.endswith(".doc"):
        if not head.startswith(b"\xd0\xcf\x11\xe0"):  # Old Word files are OLE compound documents.
            raise UploadRejected(f"'{name}' is not a valid Word .doc file")
        return "document"
    if lowered.endswith(".rtf"):
        if not head.startswith(b"{\\rtf"):
            raise UploadRejected(f"'{name}' is not a valid RTF file")
        return "document"
    if b"\x00" in head:
        raise UploadRejected(f"'{name}' looks like a binary file, not text")
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as exc:
        if exc.start < len(head) - 4:  # A multibyte character cut off by the sample is fine.
            raise UploadRejected(f"'{name}' is not valid UTF-8 text") from exc
    return "text"


def store_path(uploads_dir: Path, session_id: str, attachment_id: str, name: str) -> Path:
    directory = uploads_dir / session_id
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    return directory / f"{attachment_id}_{name}"


def new_attachment_id() -> str:
    return secrets.token_hex(6)


def read_attachment_text(attachment: Attachment, limit: int) -> str:
    from .documents import DocumentError, extract_text

    try:
        text, truncated = extract_text(attachment.path, limit)
    except DocumentError as exc:
        raise UploadRejected(str(exc)) from exc
    return redact(text + ("\n(truncated)" if truncated else ""))
