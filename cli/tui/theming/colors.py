"""
Color utilities — RGBA color type, tinting, alpha blending, ANSI conversion.

All colors in the theme system are represented as Color(r, g, b, a) with
float components 0-255 (a = 0-1). This supports tint, alpha blending,
luminance calculation, and contrast selection.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Color:
    r: float
    g: float
    b: float
    a: float = 1.0

    @property
    def hex(self) -> str:
        return f"#{int(self.r):02x}{int(self.g):02x}{int(self.b):02x}"

    @property
    def is_transparent(self) -> bool:
        return self.a < 0.01

    def to_rich(self) -> str:
        """Return a Rich-compatible color string."""
        if self.is_transparent:
            return "transparent"
        return self.hex

    def with_alpha(self, alpha: float) -> "Color":
        return Color(self.r, self.g, self.b, alpha)

    def __str__(self) -> str:
        return self.hex


def parse_hex(hex_str: str) -> Color:
    """Parse a hex color string (#rgb, #rrggbb, #rrggbbaa) into a Color."""
    s = hex_str.lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) == 6:
        s += "ff"
    r = int(s[0:2], 16)
    g = int(s[2:4], 16)
    b = int(s[4:6], 16)
    a = int(s[6:8], 16) / 255.0 if len(s) == 8 else 1.0
    return Color(float(r), float(g), float(b), a)


def luminance(color: Color) -> float:
    """Calculate perceived luminance (0.0 = black, 1.0 = white)."""
    return (0.299 * color.r + 0.587 * color.g + 0.114 * color.b) / 255.0


def tint(base: Color, overlay: Color, alpha: float) -> Color:
    """Blend overlay onto base at the given alpha (0 = base, 1 = overlay)."""
    return Color(
        r=base.r + (overlay.r - base.r) * alpha,
        g=base.g + (overlay.g - base.g) * alpha,
        b=base.b + (overlay.b - base.b) * alpha,
        a=base.a,
    )


def with_alpha(color: Color, alpha: float) -> Color:
    """Return a copy of the color with reduced alpha."""
    return color.with_alpha(alpha)


def selected_foreground(theme_bg: Color) -> Color:
    """Determine appropriate text color for a selected list item."""
    if theme_bg.is_transparent:
        return Color(255, 255, 255) if luminance(theme_bg) < 0.5 else Color(0, 0, 0)
    return Color(255, 255, 255) if luminance(theme_bg) > 0.5 else Color(0, 0, 0)


def ansi_to_color(code: int) -> Color:
    """Convert an ANSI color code (0-255) to a Color.

    0-15:   Standard ANSI colors
    16-231: 6x6x6 color cube
    232-255: Grayscale ramp
    """
    if code < 0 or code > 255:
        return Color(0, 0, 0)

    if code < 16:
        # Standard ANSI — approximate sRGB
        standard = [
            (0, 0, 0), (128, 0, 0), (0, 128, 0), (128, 128, 0),
            (0, 0, 128), (128, 0, 128), (0, 128, 128), (192, 192, 192),
            (64, 64, 64), (255, 0, 0), (0, 255, 0), (255, 255, 0),
            (0, 0, 255), (255, 0, 255), (0, 255, 255), (255, 255, 255),
        ]
        r, g, b = standard[code]
        return Color(float(r), float(g), float(b))

    if code < 232:
        # 6x6x6 color cube
        code -= 16
        r = (code // 36) % 6
        g = (code // 6) % 6
        b = code % 6
        levels = [0, 95, 135, 175, 215, 255]
        return Color(float(levels[r]), float(levels[g]), float(levels[b]))

    # Grayscale ramp (232-255)
    level = 8 + (code - 232) * 10
    return Color(float(level), float(level), float(level))


def generate_gray_scale(bg: Color, is_dark: bool, steps: int = 12) -> list[Color]:
    """Generate a grayscale ramp from the background color."""
    base_lum = luminance(bg)
    grays: list[Color] = []
    for i in range(steps):
        if is_dark:
            t = (i + 1) / steps * 0.8
        else:
            t = (i + 1) / steps * 0.8
            t = 1.0 - t
        lum = base_lum + (t - base_lum) * 0.5
        v = lum * 255
        grays.append(Color(v, v, v))
    return grays


def generate_muted_text(bg: Color, is_dark: bool) -> Color:
    """Generate a muted text color based on background luminance."""
    bg_lum = luminance(bg)
    if is_dark:
        target = min(0.7, bg_lum + 0.35)
    else:
        target = max(0.3, bg_lum - 0.35)
    v = target * 255
    return Color(v, v, v)
