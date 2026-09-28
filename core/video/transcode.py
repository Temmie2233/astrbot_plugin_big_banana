from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from astrbot.api import logger

if TYPE_CHECKING:
    from ...main import BigBanana


async def transcode_for_platform(plugin: BigBanana, src: Path) -> Path | None:
    """用 ffmpeg 把视频转成平台友好的 H.264/yuv420p/CFR mp4，失败返回 None。"""
    ffmpeg, ffprobe = _resolve_ffmpeg(plugin)
    if not ffmpeg:
        logger.info("[BIG BANANA] 未找到 ffmpeg，跳过视频转码")
        return None
    out = src.with_name(f"{src.stem}_platform.mp4")
    cmd = [ffmpeg, "-y", "-i", str(src)]
    if ffprobe and await _probe_has_audio(ffprobe, src):
        cmd += ["-map", "0:v:0", "-map", "0:a:0", "-c:a", "aac", "-b:a", "128k"]
    else:
        cmd += [
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-shortest",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
        ]
    cmd += [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-r",
        "30",
        "-vf",
        "scale=trunc(iw/2)*2:trunc(ih/2)*2",
        "-movflags",
        "+faststart",
        str(out),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode == 0 and out.exists() and out.stat().st_size > 0:
            logger.info(f"[BIG BANANA] 视频已转码为平台友好格式: {out.name}")
            return out
        tail = stderr.decode("utf-8", "ignore")[-300:]
        logger.warning(
            f"[BIG BANANA] 视频转码失败(returncode={proc.returncode}): {tail}"
        )
    except Exception as exc:
        logger.warning(f"[BIG BANANA] 视频转码异常: {exc}")
    return None


def _resolve_ffmpeg(plugin: BigBanana) -> tuple[str | None, str | None]:
    """解析 ffmpeg/ffprobe 可执行文件路径。"""
    configured = (plugin.common_config.ffmpeg_path or "").strip()
    if configured:
        ffmpeg = configured if Path(configured).exists() else None
    else:
        ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None, None
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        candidate = Path(ffmpeg).with_name("ffprobe.exe")
        ffprobe = str(candidate) if candidate.exists() else None
    return ffmpeg, ffprobe


async def _probe_has_audio(ffprobe: str, path: Path) -> bool:
    """探测视频是否包含音频轨。"""
    try:
        proc = await asyncio.create_subprocess_exec(
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=index",
            "-of",
            "csv=p=0",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
        return bool(out.strip())
    except Exception:
        return False
