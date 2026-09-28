import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.video.transcode import transcode_for_platform


def test_transcode_skips_without_ffmpeg() -> None:
    plugin = SimpleNamespace(common_config=SimpleNamespace(ffmpeg_path=""))
    with patch("core.video.transcode.shutil.which", return_value=None):
        result = asyncio.run(transcode_for_platform(plugin, Path("missing.mp4")))
    assert result is None


def test_transcode_returns_platform_file_on_success() -> None:
    tmp = Path(tempfile.mkdtemp())
    src = tmp / "clip.mp4"
    src.write_bytes(b"orig")
    out = tmp / "clip_platform.mp4"
    out.write_bytes(b"done")

    plugin = SimpleNamespace(common_config=SimpleNamespace(ffmpeg_path="ffmpeg"))
    proc = SimpleNamespace(
        returncode=0, communicate=AsyncMock(return_value=(b"", b""))
    )
    with (
        patch(
            "core.video.transcode._resolve_ffmpeg",
            return_value=("ffmpeg", "ffprobe"),
        ),
        patch(
            "core.video.transcode._probe_has_audio",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "core.video.transcode.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ),
    ):
        result = asyncio.run(transcode_for_platform(plugin, src))
    assert result == out


def test_transcode_returns_none_on_failure() -> None:
    tmp = Path(tempfile.mkdtemp())
    src = tmp / "clip.mp4"
    src.write_bytes(b"orig")

    plugin = SimpleNamespace(common_config=SimpleNamespace(ffmpeg_path="ffmpeg"))
    proc = SimpleNamespace(
        returncode=1, communicate=AsyncMock(return_value=(b"", b"boom"))
    )
    with (
        patch(
            "core.video.transcode._resolve_ffmpeg",
            return_value=("ffmpeg", "ffprobe"),
        ),
        patch(
            "core.video.transcode._probe_has_audio",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "core.video.transcode.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ),
    ):
        result = asyncio.run(transcode_for_platform(plugin, src))
    assert result is None
