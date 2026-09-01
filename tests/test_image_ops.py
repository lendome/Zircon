from __future__ import annotations

import pytest

from zirconAgent.tools.base import ToolResult
from zirconAgent.tools.image_ops import ViewImageTool, _MAX_IMAGE_BYTES


@pytest.mark.asyncio
async def test_view_local_png_returns_model_image_content(tmp_path):
    image = tmp_path / "pixel.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"image-data")

    result = await ViewImageTool(str(tmp_path)).run("pixel.png", detail="low")

    assert isinstance(result, ToolResult)
    assert "pixel.png" in result
    assert "base64" not in result
    image_part = result.model_content[1]
    assert image_part["type"] == "image_url"
    assert image_part["image_url"]["url"].startswith("data:image/png;base64,")
    assert image_part["image_url"]["detail"] == "low"


@pytest.mark.asyncio
async def test_view_remote_image_preserves_url(tmp_path):
    source = "https://example.com/screenshot.webp"

    result = await ViewImageTool(str(tmp_path)).run(source)

    assert isinstance(result, ToolResult)
    assert result.model_content[1]["image_url"]["url"] == source


@pytest.mark.asyncio
async def test_view_image_rejects_unsupported_file(tmp_path):
    (tmp_path / "not-image.txt").write_text("hello")

    result = await ViewImageTool(str(tmp_path)).run("not-image.txt")

    assert result.startswith("Error: unsupported image format")


@pytest.mark.asyncio
async def test_view_image_rejects_oversized_file(tmp_path):
    image = tmp_path / "large.png"
    with image.open("wb") as handle:
        handle.seek(_MAX_IMAGE_BYTES)
        handle.write(b"x")

    result = await ViewImageTool(str(tmp_path)).run("large.png")

    assert "maximum supported size" in result
