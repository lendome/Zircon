from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from core.exclusions import ZIRCON_DIR, is_excluded

from .base import Tool, ToolResult


_MAX_IMAGE_BYTES = 10 * 1024 * 1024
_IMAGE_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def _image_mime(data: bytes) -> str | None:
    for signature, mime in _IMAGE_SIGNATURES:
        if data.startswith(signature):
            return mime
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


class ViewImageTool(Tool):
    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path).resolve()

    @property
    def name(self) -> str:
        return "view_image"

    @property
    def description(self) -> str:
        return (
            "View an image with the active vision-capable model. Accepts an http(s) URL "
            "or a local image path (PNG, JPEG, GIF, or WebP; maximum 10 MB)."
        )

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "HTTP(S) image URL or relative/absolute local image path",
                },
                "detail": {
                    "type": "string",
                    "enum": ["auto", "low", "high"],
                    "description": "Requested image inspection detail",
                },
            },
            "required": ["source"],
        }

    async def run(self, source: str, detail: str = "auto") -> str | ToolResult:
        parsed = urlparse(source)
        if parsed.scheme in ("http", "https"):
            if not parsed.netloc:
                return f"Error: invalid image URL: {source}"
            return self._result(source, source, detail)
        if parsed.scheme:
            return "Error: image source must be an HTTP(S) URL or local file path"

        target = Path(source).expanduser()
        if not target.is_absolute():
            target = self.repo_path / target
        target = target.resolve()
        if is_excluded(source) or is_excluded(target):
            return f"Error: {source} is inside {ZIRCON_DIR}/ and is not readable."
        if not target.is_file():
            return f"Error: image file not found: {source}"

        size = target.stat().st_size
        if size > _MAX_IMAGE_BYTES:
            return f"Error: image is {size} bytes; maximum supported size is {_MAX_IMAGE_BYTES} bytes"
        data = await asyncio.to_thread(target.read_bytes)
        mime = _image_mime(data)
        if mime is None:
            return "Error: unsupported image format; expected PNG, JPEG, GIF, or WebP"
        data_url = f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
        label = str(target.relative_to(self.repo_path)) if target.is_relative_to(self.repo_path) else str(target)
        return self._result(label, data_url, detail, size=size, mime=mime)

    @staticmethod
    def _result(
        label: str,
        image_url: str,
        detail: str,
        *,
        size: int | None = None,
        mime: str | None = None,
    ) -> ToolResult:
        metadata = f" ({mime}, {size} bytes)" if mime and size is not None else ""
        text = f"Image loaded from {label}{metadata}. Inspect the attached image."
        return ToolResult(text, [
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": image_url, "detail": detail}},
        ])
