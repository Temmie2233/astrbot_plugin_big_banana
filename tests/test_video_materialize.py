import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from core.schemas import GenerationResult, VideoResource
from core.video.pipeline import VideoPipeline


def _plugin(tmp: Path, downloader) -> SimpleNamespace:
    return SimpleNamespace(
        temp_dir=tmp,
        downloader=downloader,
        common_config=SimpleNamespace(video_transcode=False),
    )


def test_materialize_videos_downloads_to_local_path() -> None:
    tmp = Path(tempfile.mkdtemp())

    async def fake_download(url: str, dest, **kwargs) -> bool:
        Path(dest).write_bytes(b"video")
        return True

    plugin = _plugin(
        tmp, SimpleNamespace(download_to_file=AsyncMock(side_effect=fake_download))
    )
    result = GenerationResult(videos=[VideoResource(url="https://e.com/a/b.mp4")])

    asyncio.run(VideoPipeline(plugin)._materialize_videos(result))

    assert result.videos[0].path
    assert result.videos[0].path.endswith(".mp4")
    assert Path(result.videos[0].path).exists()


def test_materialize_videos_falls_back_to_url_on_failure() -> None:
    tmp = Path(tempfile.mkdtemp())
    plugin = _plugin(tmp, SimpleNamespace(download_to_file=AsyncMock(return_value=False)))
    result = GenerationResult(videos=[VideoResource(url="https://e.com/x.mp4")])

    asyncio.run(VideoPipeline(plugin)._materialize_videos(result))

    assert result.videos[0].path is None
