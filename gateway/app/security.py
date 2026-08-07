from __future__ import annotations

import hashlib
import re
from pathlib import Path

from docx import Document


ALLOWED_EXTENSIONS = {
    ".md",
    ".txt",
    ".docx",
    ".pdf",
    ".epub",
    ".json",
    ".jsonl",
    ".csv",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".zip",
}
SECRET_PATTERNS = [
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s]+"),
    re.compile(r"(?i)((?:api[_-]?key|auth[_-]?token|token)\s*[=:]\s*)[^\s,;]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
]


def redact_secrets(value: str) -> str:
    result = value
    for pattern in SECRET_PATTERNS:
        if pattern.pattern.startswith("\\bsk-"):
            result = pattern.sub("[REDACTED]", result)
        else:
            result = pattern.sub(r"\1[REDACTED]", result)
    return result


def sanitize_filename(filename: str) -> str:
    name = Path(filename).name.strip()
    safe = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]", "_", name)
    if not safe or safe in {".", ".."}:
        raise ValueError("无效文件名")
    if Path(safe).suffix.lower() not in ALLOWED_EXTENSIONS:
        raise ValueError("不支持该文件格式")
    return safe


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def ensure_within(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    root_resolved = root.resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        # SQLite may be reused between the native macOS process and Docker.
        # Older asset rows contain the host's absolute .../runtime/... path;
        # remap only its runtime-relative suffix, and only when the mapped
        # file already exists under the current runtime root.
        try:
            runtime_index = path.parts.index(root_resolved.name)
        except ValueError:
            runtime_index = -1
        if runtime_index >= 0:
            relative = Path(*path.parts[runtime_index + 1 :])
            remapped = (root_resolved / relative).resolve()
            if (
                remapped != root_resolved
                and root_resolved in remapped.parents
                and remapped.exists()
            ):
                return remapped
        raise ValueError("路径超出允许运行目录")
    return resolved


def convert_to_markdown(source: Path, target: Path) -> Path:
    suffix = source.suffix.lower()
    if suffix == ".md":
        target.write_bytes(source.read_bytes())
        return target
    if suffix == ".txt":
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        return target
    if suffix == ".docx":
        document = Document(source)
        paragraphs = [paragraph.text.strip() for paragraph in document.paragraphs]
        target.write_text("\n\n".join(item for item in paragraphs if item), encoding="utf-8")
        return target
    raise ValueError("PDF 不需要转换为 Markdown")
