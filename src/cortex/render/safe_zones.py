from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SafeZone(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    top: float = Field(ge=0, le=1)
    right: float = Field(ge=0, le=1)
    bottom: float = Field(ge=0, le=1)
    left: float = Field(ge=0, le=1)


SAFE_ZONES_VERSION = 2

SAFE_ZONES: dict[str, SafeZone] = {
    "9:16": SafeZone(top=0.07, right=0.07, bottom=0.16, left=0.07),
    "1:1": SafeZone(top=0.08, right=0.08, bottom=0.14, left=0.08),
    "16:9": SafeZone(top=0.10, right=0.10, bottom=0.12, left=0.10),
}

# Safe zones v2: for 9:16 we also model the rectangles that each platform's UI
# draws over the video. Normalized (x0, y0, x1, y1) in canvas fractions. These
# are conservative approximations of the Reels/TikTok chrome (it changes between
# app versions and devices), meant to keep captions/headlines clear of it, not
# a pixel-exact specification.
Rect = tuple[float, float, float, float]

PLATFORM_OCCLUSIONS: dict[str, dict[str, Rect]] = {
    "instagram_reels": {
        "top_bar": (0.0, 0.0, 1.0, 0.085),
        "right_actions": (0.86, 0.43, 1.0, 0.90),
        "bottom_meta": (0.0, 0.80, 1.0, 1.0),
    },
    "tiktok": {
        "top_tabs": (0.0, 0.0, 1.0, 0.095),
        "right_actions": (0.85, 0.38, 1.0, 0.86),
        "bottom_meta": (0.0, 0.78, 1.0, 1.0),
    },
}

CAPTION_ACTION_MARGIN_PX = 16


def canvas_key(width: int, height: int) -> str:
    ratio = width / max(1, height)
    if ratio > 1.35:
        return "16:9"
    if ratio < 0.8:
        return "9:16"
    return "1:1"


def occlusion_rects(canvas: str) -> list[tuple[str, str, Rect]]:
    if canvas != "9:16":
        return []
    return [
        (platform, name, rect)
        for platform, rects in PLATFORM_OCCLUSIONS.items()
        for name, rect in rects.items()
    ]


def right_actions_limit(canvas: str, y_top: float, y_bottom: float) -> float | None:
    """Smallest right_actions x0 whose y-range the vertical span (fractions of
    the canvas height) touches, or None when the span clears every column."""
    xs = [
        rect[0]
        for _platform, name, rect in occlusion_rects(canvas)
        if name == "right_actions" and y_top < rect[3] and y_bottom > rect[1]
    ]
    return min(xs) if xs else None


def occlusion_issues(
    *, canvas: str, width: int, height: int, element: str, bbox: Rect | None,
) -> list[str]:
    """Issue codes like ``caption_occluded:tiktok:right_actions`` for every
    platform UI rectangle that the pixel bbox (x0, y0, x1, y1) intersects."""
    if bbox is None:
        return []
    x0, y0, x1, y1 = bbox
    issues: list[str] = []
    for platform, name, rect in occlusion_rects(canvas):
        rx0, ry0, rx1, ry1 = (rect[0] * width, rect[1] * height, rect[2] * width, rect[3] * height)
        if x0 < rx1 and x1 > rx0 and y0 < ry1 and y1 > ry0:
            issues.append(f"{element}_occluded:{platform}:{name}")
    return issues
