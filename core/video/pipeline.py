from __future__ import annotations

import asyncio
import shutil
import uuid
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from astrbot.api import logger

from ..schemas import GenerationResult, ImageResource

if TYPE_CHECKING:
    from ...main import BigBanana


class VideoPipeline:
    """Prepare reference images and run a video provider."""

    def __init__(self, plugin: BigBanana) -> None:
        """Store the active plugin instance.

        Args:
            plugin: Active plugin instance.
        """
        self.plugin = plugin

    async def run(
        self,
        params: dict,
        image_list: list[ImageResource] | None,
    ) -> GenerationResult:
        """Prepare reference images and generate videos.

        Args:
            params: Resolved generation parameters.
            image_list: Optional input images.

        Returns:
            Generated videos or an error result.
        """
        if self.plugin.common_config.strip_metadata and image_list:
            cleaned_images: list[ImageResource] = []
            for image in image_list:
                stripped = ImageResource.strip_metadata(image.bytes)
                if stripped is None:
                    logger.warning("[BIG BANANA] 无法处理视频参考图，已移除")
                    continue
                image.bytes = stripped
                image._b64_cache = None
                cleaned_images.append(image)
            image_list[:] = cleaned_images

        result = await self.plugin.video_dispatcher.dispatch(params, image_list)
        if result.videos:
            await self._materialize_videos(result)
            return result
        return GenerationResult(
            error_message=result.error_message or "视频生成未返回视频 URL"
        )

    async def _materialize_videos(self, result: GenerationResult) -> None:
        """把远程视频下载到本地并做平台兼容处理，便于平台端直接发送。"""
        for video in result.videos:
            if video.path or not video.url.startswith(("http://", "https://")):
                continue
            suffix = urlparse(video.url).path
            suffix = suffix[suffix.rfind(".") :] if "." in suffix else ""
            if not suffix or len(suffix) > 5:
                suffix = ".mp4"
            dest = self.plugin.temp_dir / f"big_banana_video_{uuid.uuid4().hex}{suffix}"
            if not await self.plugin.downloader.download_to_file(video.url, dest):
                logger.warning(
                    f"[BIG BANANA] 视频下载失败，将退回使用原始 URL: {video.url}"
                )
                continue
            video.path = str(dest)
            if self.plugin.common_config.video_transcode:
                normalized = await self._transcode_video(dest)
                if normalized is not None:
                    video.path = str(normalized)

    def _resolve_ffmpeg(self) -> tuple[str | None, str | None]:
        """解析 ffmpeg/ffprobe 可执行文件路径。"""
        configured = (self.plugin.common_config.ffmpeg_path or "").strip()
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

    @staticmethod
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

    async def _transcode_video(self, src: Path) -> Path | None:
        """用 ffmpeg 转成平台友好的 H.264/yuv420p/CFR mp4，失败返回 None。"""
        ffmpeg, ffprobe = self._resolve_ffmpeg()
        if not ffmpeg:
            logger.info("[BIG BANANA] 未找到 ffmpeg，跳过视频转码")
            return None
        out = src.with_name(f"{src.stem}_platform.mp4")
        cmd = [ffmpeg, "-y", "-i", str(src)]
        if ffprobe and await self._probe_has_audio(ffprobe, src):
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
