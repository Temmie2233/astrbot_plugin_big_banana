from __future__ import annotations

import re
from typing import TYPE_CHECKING

from astrbot.api import logger

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent
    from astrbot.api.star import Context

    from ..schemas import SubBrainConfig

_ERR_KEYWORDS = ["ServerError", "All chat models failed"]
_IMAGE_REF_RE = re.compile(r"(?:image|图)\s*(\d+)", re.IGNORECASE)


def extract_image_refs(text: str) -> set[int]:
    """提取提示词中的图片编号引用，兼容 image N 与图N。"""
    return {int(match) for match in _IMAGE_REF_RE.findall(text)}


class SubBrainOptimizer:
    """基于已配置副脑 LLM 提供商的提示词优化器。"""

    def __init__(
        self,
        *,
        context: Context,
        sub_brain_config: SubBrainConfig,
    ) -> None:
        """保存上下文和副脑配置读取回调。"""
        self.context = context
        self.sub_brain_config = sub_brain_config

    async def optimize_prompt(
        self,
        event: AstrMessageEvent,
        prompt: str,
    ) -> str | None:
        """调用副脑模型优化绘图提示词，失败时返回 None。"""
        provider_id = (
            self.sub_brain_config.provider_id
            or self._resolve_current_provider_id(event)
        )
        if not provider_id:
            logger.warning(
                "[BIG BANANA] 已启用副脑优化但未能解析到有效的副脑模型供应商，跳过优化"
            )
            return None

        try:
            logger.info(
                f"[BIG BANANA] 正在使用副脑进行提示词优化，模型提供商: {provider_id}"
            )
            resp = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=prompt,
                system_prompt=self.sub_brain_config.system_prompt,
            )
            optimized_prompt = resp.completion_text
            if optimized_prompt and not any(
                keyword in optimized_prompt for keyword in _ERR_KEYWORDS
            ):
                optimized_prompt = optimized_prompt.strip()
                if not optimized_prompt:
                    logger.warning(
                        "[BIG BANANA] 副脑优化返回了空文本，将使用原始提示词"
                    )
                    return None
                missing_refs = extract_image_refs(prompt) - extract_image_refs(
                    optimized_prompt
                )
                if missing_refs:
                    logger.warning(
                        "[BIG BANANA] 副脑优化丢失了图片编号引用 "
                        f"{sorted(missing_refs)}，将使用原始提示词"
                    )
                    return None
                logger.info(
                    f"[BIG BANANA] 副脑优化完成，优化后提示词: {optimized_prompt[:120]}..."
                )
                return optimized_prompt
            else:
                logger.warning(
                    "[BIG BANANA] 副脑优化返回了空文本/异常文本，将使用原始提示词"
                )
        except Exception as e:
            logger.error(
                f"[BIG BANANA] 副脑提示词优化失败: {e}，将使用原始提示词生成图片"
            )
        return None

    def _resolve_current_provider_id(self, event: AstrMessageEvent) -> str | None:
        """解析当前会话正在使用的 LLM 提供商 ID。"""
        umo = event.unified_msg_origin if event else None
        try:
            using_provider = self.context.get_using_provider(umo)
            return using_provider.meta().id if using_provider else None
        except Exception as e:
            logger.warning(f"[BIG BANANA] 获取当前会话正在使用的提供商失败: {e}")
            return None
