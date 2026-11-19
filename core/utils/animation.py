from __future__ import annotations

import re
from typing import Any

_ANIMATION_TOKENS = ("animated gif", "animation", "动图", "循环动画")
_GIF_PATTERN = re.compile(r"(?<![a-z])gif(?![a-z])")


def prompt_requests_animation(prompt: str) -> bool:
    """判断提示词是否明确要求 GIF/动图。"""
    text = str(prompt or "").lower()
    return bool(
        _GIF_PATTERN.search(text)
        or any(token in text for token in _ANIMATION_TOKENS)
    )


def ensure_gif_params(params: dict[str, Any]) -> None:
    """给 GIF 请求补齐默认帧数和帧时长，保留用户/配置的显式值。"""
    if params.get("frame_count") in (None, "", 0):
        params["frame_count"] = 9
    if params.get("frame_duration_ms") in (None, "", 0):
        params["frame_duration_ms"] = 200


def is_gif_image(image: Any) -> bool:
    """根据 MIME 或文件头判断图片资源是否为 GIF。"""
    mime = str(getattr(image, "mime", "") or "").lower()
    if mime == "image/gif":
        return True
    data = getattr(image, "bytes", b"") or b""
    return data[:6] in (b"GIF87a", b"GIF89a")
