"""Validation and Markdown rendering for immutable vault attachments."""
from __future__ import annotations

import base64
import binascii
import hashlib
import mimetypes
import re
from pathlib import PurePosixPath
from urllib.parse import quote


MAX_FILE = 10 * 1024 * 1024
MAX_TOTAL = 30 * 1024 * 1024
MAX_COUNT = 10
DIRECTORY = "8 Вложения"
ID_RE = re.compile(r"[0-9a-f]{64}\.[a-z0-9]{1,12}\Z")
START = "<!-- mmm:attachments -->"
END = "<!-- /mmm:attachments -->"
BLOCK_RE = re.compile(r"(?m)^<!-- mmm:attachments -->\r?\n.*?^<!-- /mmm:attachments -->(?:\r?\n)?", re.S)


def safe_name(name: object) -> str:
    if (not isinstance(name, str) or not name or len(name) > 200
            or name in {".", ".."} or any(c in name for c in "/\\\r\n\x00")
            or any(ord(c) < 32 for c in name)):
        raise ValueError("Некорректное имя вложения")
    return name


def extension(name: str) -> str:
    suffix = name.rpartition(".")[2].lower() if "." in name else ""
    if suffix in {"html", "htm", "svg", "xhtml", "xml"}:
        return "bin"
    return suffix if re.fullmatch(r"[a-z0-9]{1,12}", suffix) else "bin"


def attachment_id(data: bytes, name: str) -> str:
    image_ext = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}.get(image_mime(data))
    return hashlib.sha256(data).hexdigest() + "." + (image_ext or extension(name))


def decode_base64(value: object) -> bytes:
    if not isinstance(value, str) or len(value) > 4 * ((MAX_FILE + 2) // 3) + 4:
        raise ValueError("Вложение превышает 10 МиБ")
    try:
        data = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("Некорректное содержимое вложения") from exc
    if len(data) > MAX_FILE:
        raise ValueError("Размер вложения должен быть до 10 МиБ")
    return data


def image_mime(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24 and data[12:16] == b"IHDR":
        return "image/png"
    if data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9"):
        return "image/jpeg"
    if data[:6] in {b"GIF87a", b"GIF89a"} and len(data) >= 14:
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP" and len(data) >= 16:
        return "image/webp"
    return None


def mime_for(data: bytes, name: str) -> str:
    image = image_mime(data)
    if image:
        return image
    guessed = mimetypes.guess_type(name)[0]
    if guessed and not guessed.startswith("image/") and guessed not in {"text/html", "image/svg+xml", "application/xhtml+xml", "application/xml", "text/xml"}:
        return guessed
    return "application/octet-stream"


def strip_block(body: str) -> str:
    return BLOCK_RE.sub("", body)


def render_block(body: str, attachments: list[dict], note_path: str) -> str:
    body = strip_block(body)
    if not attachments:
        return body
    depth = len(PurePosixPath(note_path).parts) - 1
    prefix = "../" * depth
    lines = [START]
    for item in attachments:
        label = (item["name"].replace("\\", "\\\\").replace("[", "\\[")
                 .replace("]", "\\]").replace("<", "&lt;").replace(">", "&gt;"))
        target = prefix + quote(DIRECTORY, safe="") + "/" + quote(item["id"], safe="")
        leader = "!" if item["mime"].startswith("image/") else ""
        lines.append(f"{leader}[{label}]({target})")
    lines.append(END)
    return body.rstrip("\n") + "\n\n" + "\n".join(lines) + "\n"
