from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING

import astrbot.api.message_components as Comp
from PIL import Image as PILImage
from PIL import ImageOps
from astrbot.api import logger

from ..drawing.saver import save_images_to_local
from ..schemas import MAX_SIZE_B64_LEN, GenerationResult
from .animation import is_gif_image

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent
    from astrbot.core.message.components import BaseMessageComponent


# QQ/NapCat 的富媒体上传对图片大小很敏感。发送前压缩到接近此前稳定成功的尺寸，
# 同时避免一次 sendMsg 携带多张大图。
_QQ_SEND_MAX_SIDE = 1600
_QQ_SEND_MAX_BYTES = 3 * 1024 * 1024
_QQ_SEND_MIN_QUALITY = 55


def _compress_image_for_qq(image_bytes: bytes) -> bytes:
    """把静态图片压缩到 QQ 可稳定上传的尺寸和体积；失败时退回原图。"""
    try:
        with PILImage.open(BytesIO(image_bytes)) as image:
            if getattr(image, "is_animated", False) or getattr(image, "n_frames", 1) > 1:
                logger.info("[BIG BANANA] 多帧图片不压缩，按原图发送")
                return image_bytes

            original_size = len(image_bytes)
            width, height = image.size
            if original_size <= _QQ_SEND_MAX_BYTES and max(width, height) <= _QQ_SEND_MAX_SIDE:
                return image_bytes

            clean = ImageOps.exif_transpose(image).copy()
            has_alpha = clean.mode in {"RGBA", "LA"} or (
                clean.mode == "P" and "transparency" in clean.info
            )
            if has_alpha:
                rgba = clean.convert("RGBA")
                background = PILImage.new("RGB", rgba.size, (255, 255, 255))
                background.paste(rgba, mask=rgba.getchannel("A"))
                clean = background
            else:
                clean = clean.convert("RGB")

            resample = getattr(PILImage, "Resampling", PILImage).LANCZOS
            if max(clean.size) > _QQ_SEND_MAX_SIDE:
                clean.thumbnail((_QQ_SEND_MAX_SIDE, _QQ_SEND_MAX_SIDE), resample)

            output = BytesIO()
            for quality in (85, 75, 65, _QQ_SEND_MIN_QUALITY):
                output = BytesIO()
                clean.save(output, format="JPEG", quality=quality, optimize=True)
                if output.tell() <= _QQ_SEND_MAX_BYTES:
                    break
            else:
                # 极端复杂图片再缩一档，避免仍超过 NapCat 的富媒体限制。
                clean.thumbnail((1280, 1280), resample)
                output = BytesIO()
                clean.save(output, format="JPEG", quality=75, optimize=True)

            compressed = output.getvalue()
            logger.info(
                "[BIG BANANA] QQ 发送前压缩图片: "
                f"{width}x{height} {original_size} -> "
                f"{clean.width}x{clean.height} {len(compressed)} bytes"
            )
            return compressed
    except Exception as exc:
        logger.warning(f"[BIG BANANA] QQ 图片压缩失败，按原图发送: {exc}")
        return image_bytes


def get_message_id(event: AstrMessageEvent) -> str | None:
    """安全获取事件关联的消息 ID，若不存在或不是有效 message_obj 则返回 None。"""
    message_obj = getattr(event, "message_obj", None)
    if message_obj is None:
        return None
    message_id = getattr(message_obj, "message_id", None)
    if message_id is None:
        return None
    id_str = str(message_id).strip()
    return id_str if id_str != "" else None


def build_reply_component(
    event: AstrMessageEvent,
    quote_reply_mode: str = "both",
    is_command: bool = True,
) -> BaseMessageComponent | None:
    """若满足配置的回复引用条件且事件中包含有效 message_id，则构造 Reply 引用组件，否则返回 None。"""
    if quote_reply_mode == "none":
        return None
    if is_command and quote_reply_mode == "tool_only":
        return None
    if not is_command and quote_reply_mode == "command_only":
        return None

    message_id = get_message_id(event)
    if message_id is not None:
        return Comp.Reply(id=message_id)
    return None


def build_message_chain(
    event: AstrMessageEvent,
    components: BaseMessageComponent | list[BaseMessageComponent] | None = None,
    quote_reply_mode: str = "both",
    is_command: bool = True,
) -> list[BaseMessageComponent]:
    """构造包含可选 Reply 引用和目标组件的完整消息组件列表。"""
    chain: list[BaseMessageComponent] = []
    if reply := build_reply_component(
        event, quote_reply_mode=quote_reply_mode, is_command=is_command
    ):
        chain.append(reply)
    if components is not None:
        if isinstance(components, list):
            chain.extend(components)
        else:
            chain.append(components)
    return chain


def build_result_message_chain(
    event: AstrMessageEvent,
    result: GenerationResult,
    url_only: bool = False,
    quote_reply_mode: str = "both",
    is_command: bool = True,
    temporary_paths: list[Path] | None = None,
    temp_dir: Path | None = None,
) -> list[BaseMessageComponent]:
    """构造适配平台限制的媒体生成结果消息链。"""
    result_urls = [url for url in result.urls if url is not None]
    videos = [video for video in result.videos if video.url]
    video_urls = [video.url for video in videos if video.url]
    # 视频消息不带引用回复，避免部分协议端（如 NapCat/QQ）处理异常。
    reply_mode = "none" if videos and not url_only else quote_reply_mode
    msg_chain = build_message_chain(
        event,
        components=None,
        quote_reply_mode=reply_mode,
        is_command=is_command,
    )
    if videos:
        logger.info(
            f"[BIG BANANA] 发送视频: {[video.local_path or video.url for video in videos]}"
        )

    # 如果仅 url，这里尝试检查有无 url，无则报错
    if url_only:
        urls = video_urls or result_urls
        if urls:
            msg_chain.append(Comp.Plain("\n".join(urls)))
        else:
            msg_chain.append(Comp.Plain("❌ 生成失败：没有可用的媒体 URL"))
        return msg_chain

    if video_urls:
        videos = [video for video in result.videos if video.url]
        for video in videos:
            if video.local_path is not None:
                if video.local_path.is_file():
                    msg_chain.append(Comp.Video.fromFileSystem(str(video.local_path)))
                else:
                    msg_chain.append(
                        Comp.Plain(f"❌ 本地视频文件不存在，视频链接：{video.url}")
                    )
            else:
                msg_chain.append(Comp.Video.fromURL(video.url))
        return msg_chain

    images_with_bytes = [image for image in result.images if image.bytes]
    # 对 telegram 做特殊处理
    platform_meta = getattr(event, "platform_meta", None)
    platform_name = getattr(platform_meta, "name", None) if platform_meta else None
    if platform_name == "telegram" and any(
        (image.base64 and len(image.base64) > MAX_SIZE_B64_LEN)
        for image in images_with_bytes
    ):
        if temp_dir is not None:
            save_results = save_images_to_local(images_with_bytes, temp_dir)
            if temporary_paths is not None:
                temporary_paths.extend(path for _name, path in save_results)
            for name_, path_ in save_results:
                msg_chain.append(Comp.File(name=name_, file=str(path_)))
            return msg_chain

    # 其他平台目前默认不特殊处理图片大小限制
    if images_with_bytes:
        msg_chain.extend(
            Comp.Image.fromBase64(image.base64) for image in images_with_bytes
        )
    elif result_urls:
        msg_chain.append(Comp.Plain("\n".join(result_urls)))
    else:
        msg_chain.append(Comp.Plain("❌ 图片生成失败：响应中未包含图片数据"))
    return msg_chain


def build_result_message_chains(
    event: AstrMessageEvent,
    result: GenerationResult,
    url_only: bool = False,
    quote_reply_mode: str = "both",
    is_command: bool = True,
    temporary_paths: list[Path] | None = None,
    temp_dir: Path | None = None,
    expected_gif: bool = False,
) -> list[list[BaseMessageComponent]]:
    """构造结果消息链；QQ 多图拆成多条，并可在非 GIF 结果时追加提示。"""
    platform_meta = getattr(event, "platform_meta", None)
    platform_name = getattr(platform_meta, "name", None) if platform_meta else None
    images_with_bytes = [image for image in result.images if image.bytes]

    # 视频、URL-only、其他平台和 Telegram 的大图/文件逻辑继续走原路径。
    if (
        platform_name != "aiocqhttp"
        or url_only
        or any(video.url for video in result.videos)
        or not images_with_bytes
    ):
        chains = [
            build_result_message_chain(
                event,
                result=result,
                url_only=url_only,
                quote_reply_mode=quote_reply_mode,
                is_command=is_command,
                temporary_paths=temporary_paths,
                temp_dir=temp_dir,
            )
        ]
    else:
        chains = []
        for index, image in enumerate(images_with_bytes):
            compressed = _compress_image_for_qq(image.bytes)
            encoded = base64.b64encode(compressed).decode("ascii")
            chains.append(
                build_message_chain(
                    event,
                    components=[Comp.Image.fromBase64(encoded)],
                    quote_reply_mode=quote_reply_mode if index == 0 else "none",
                    is_command=is_command,
                )
            )
        if len(chains) > 1:
            logger.info(f"[BIG BANANA] QQ 多图拆分为 {len(chains)} 条消息发送")

    if expected_gif and result.images and not any(
        is_gif_image(image) for image in result.images
    ):
        logger.warning("[BIG BANANA] 请求 GIF，但 provider 返回的不是 GIF")
        if chains:
            chains[-1].append(
                Comp.Plain("⚠️ 提供方返回的是静态图，未生成 GIF。")
            )
    return chains
