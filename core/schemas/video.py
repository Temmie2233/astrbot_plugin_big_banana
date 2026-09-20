from dataclasses import dataclass


@dataclass(repr=False, slots=True)
class VideoResource:
    """A generated video."""

    url: str
    path: str | None = None
    """插件下载得到的本地文件路径；存在时优先用于发送。"""
