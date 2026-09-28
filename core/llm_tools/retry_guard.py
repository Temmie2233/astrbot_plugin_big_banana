from __future__ import annotations

import asyncio
import copy
import re
from typing import TYPE_CHECKING

import astrbot.api.message_components as Comp
from astrbot.core import logger

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent

    from ...main import BigBanana

TOOL_CALLED_EXTRA = "_big_banana_tool_called"
RETRY_COUNT_EXTRA = "_big_banana_fake_retry_count"

TOOL_NAME = "banana_image_generation"

_FAKE_CLAIM_PATTERN = re.compile(
    r"(正在调用|正在生成|这就去画|这就去调取|在画了|马上就好|画好了|画完了|生成完成)"
)
_PROMPT_MARKER_PATTERN = re.compile(
    r"(【提示词】|【画面描述】|提示词[:：]|Prompt[:：]|prompt[:：=])",
    re.IGNORECASE,
)
_DRAW_INTENT_PATTERN = re.compile(r"(画|图|人设|头像|自画像|生成)")
_PROMPT_MARKER_TEXTS = (
    "【提示词】",
    "【画面描述】",
    "提示词：",
    "提示词:",
    "Prompt:",
    "prompt:",
    "prompt=",
)
_PROMPT_LIMIT = 2000


class FakeCallRetryGuard:
    """拦截“只口播不调用”的绘图回复，强制重试或兜底生成。

    仅在同时满足以下条件时介入，避免误触发：
    1. 本轮没有真正执行过任何 LLM 工具；
    2. 回复文本出现工具名，或（用户请求包含画图意图且回复出现
       调用/生成台词或提示词标记）；
    3. 非流式输出、结果中没有图片。
    """

    def __init__(self, plugin: BigBanana) -> None:
        """保存插件实例以复用守卫、管线和工具解析。"""
        self.plugin = plugin

    def mark_tool_called(self, event: AstrMessageEvent) -> None:
        """记录本轮真实发生过的 LLM 工具调用。"""
        event.set_extra(TOOL_CALLED_EXTRA, True)

    async def handle_decorating_result(self, event: AstrMessageEvent) -> None:
        """发送前拦截伪调用回复，触发重试或兜底生成。"""
        cfg = self.plugin.llm_tools_config
        if not cfg.fake_call_retry_enabled or event.is_stopped():
            return
        result = event.get_result()
        if result is None or not result.chain or not result.is_llm_result():
            return
        if any(isinstance(comp, Comp.Image) for comp in result.chain):
            return
        if self._tool_called(event):
            return
        text = "".join(
            comp.text for comp in result.chain if isinstance(comp, Comp.Plain)
        )
        if not text.strip():
            return

        prompt = self._detect_fake_call(event, text)
        if prompt is None:
            return

        retry_count = int(event.get_extra(RETRY_COUNT_EXTRA, 0) or 0)
        max_retries = max(0, int(cfg.fake_call_retry_max))
        if retry_count < max_retries:
            self._schedule_retry(event, prompt, retry_count)
        elif cfg.fake_call_fallback_generate:
            self._schedule_fallback(event, prompt)
        else:
            logger.warning(
                "[BIG BANANA] 检测到伪工具调用，但重试次数已用尽且未开启兜底生成，"
                "放行原始回复。"
            )

    def _tool_called(self, event: AstrMessageEvent) -> bool:
        """判断本轮是否真正调用过香蕉工具。"""
        return bool(event.get_extra(TOOL_CALLED_EXTRA))

    def _detect_fake_call(
        self, event: AstrMessageEvent, text: str
    ) -> str | None:
        """识别伪调用回复并提取可用提示词，未命中返回 None。"""
        user_text = event.message_str or ""
        if TOOL_NAME in text:
            return self._extract_prompt(event, text)
        if not _DRAW_INTENT_PATTERN.search(user_text):
            return None
        if _PROMPT_MARKER_PATTERN.search(text) or _FAKE_CLAIM_PATTERN.search(text):
            return self._extract_prompt(event, text)
        return None

    def _extract_prompt(self, event: AstrMessageEvent, text: str) -> str:
        """从回复文本中提取画面描述，失败时回退到用户原始请求。"""
        for marker in _PROMPT_MARKER_TEXTS:
            idx = text.find(marker)
            if idx == -1 and marker.isascii():
                idx = text.lower().find(marker.lower())
            if idx != -1:
                candidate = self._clean_prompt(text[idx + len(marker) :])
                if candidate:
                    return candidate[:_PROMPT_LIMIT]

        code_block = re.search(r"```[a-zA-Z0-9_-]*\s*\n(.*?)```", text, re.DOTALL)
        if code_block:
            candidate = self._clean_prompt(code_block.group(1))
            if candidate:
                return candidate[:_PROMPT_LIMIT]

        fallback = self._clean_prompt(event.message_str or "")
        return (fallback or "draw a picture")[:_PROMPT_LIMIT]

    @staticmethod
    def _clean_prompt(text: str) -> str:
        """去掉引用符、空行和角色扮演旁白，只保留提示词正文。"""
        lines: list[str] = []
        for line in text.splitlines():
            line = line.strip()
            if line.startswith(">"):
                line = line.lstrip(">").strip()
            if not line or line == "---":
                if lines:
                    break
                continue
            if line.startswith(("（", "(")):
                if lines:
                    break
                continue
            lines.append(line)
        return "\n".join(lines).strip()

    def _schedule_retry(
        self, event: AstrMessageEvent, prompt: str, retry_count: int
    ) -> None:
        """清掉假台词，并把带有纠正指令的新事件放回事件队列。"""
        cfg = self.plugin.llm_tools_config
        provider_id = (
            cfg.fake_call_retry_provider.strip()
            if cfg.fake_call_retry_use_provider
            else ""
        )

        new_event = copy.copy(event)
        new_event.clear_extra()
        new_event.clear_result()
        new_event._force_stopped = False
        new_event._has_send_oper = False
        new_event.set_extra(RETRY_COUNT_EXTRA, retry_count + 1)
        if provider_id:
            new_event.set_extra("selected_provider", provider_id)
        new_event.message_str = self._build_retry_message(prompt)

        event.clear_result()
        event.stop_event()
        self.plugin.context.get_event_queue().put_nowait(new_event)
        logger.info(
            "[BIG BANANA] 检测到伪工具调用，已强制重试"
            f"（{retry_count + 1}/{max(0, int(cfg.fake_call_retry_max))}），"
            f"重试提供商: {provider_id or '当前提供商'}"
        )

    @staticmethod
    def _build_retry_message(prompt: str) -> str:
        """构造重试轮的用户消息，明确要求真正调用工具。"""
        return (
            "【系统重试指令】你上一条回复只在文字里表演了调用过程，"
            f"并没有真正调用 {TOOL_NAME} 工具。\n"
            "现在必须立即调用该工具：不要在文字里复述调用过程，"
            "不要回复“正在生成/正在调用”之类的台词，"
            "在工具调用成功之前不要向用户确认。\n"
            "prompt 参数请使用下面的画面描述（可精简，但不要改变主体设定）：\n"
            f"{prompt}"
        )

    def _schedule_fallback(self, event: AstrMessageEvent, prompt: str) -> None:
        """重试仍失败时，直接用提取的提示词走命令管线出图。"""
        event.clear_result()
        event.stop_event()
        task_id = (
            f"{self.plugin.task_manager.build_task_id(event)}:fake-call-fallback"
        )
        task = asyncio.create_task(self._run_fallback(event, prompt, task_id))
        self.plugin.task_manager.start(task_id, task)
        logger.info("[BIG BANANA] 强制重试次数已用尽，改为直接兜底生成图片。")

    async def _run_fallback(
        self, event: AstrMessageEvent, prompt: str, task_id: str
    ) -> None:
        """兜底生成：复用命令链路的白名单、冷却与发送逻辑。"""
        plugin = self.plugin
        try:
            access_check = plugin.whitelist_guard.check(event, is_command=False)
            if not access_check.allowed:
                await event.send(
                    event.chain_result([Comp.Plain(access_check.message)])
                )
                return

            cooldown_check = plugin.cooldown_guard.check(event)
            if not cooldown_check.allowed:
                await event.send(
                    event.chain_result([Comp.Plain(f"❌ {cooldown_check.message}")])
                )
                return
            plugin.cooldown_guard.mark_cooldown(event)

            from ..drawing.collector import ImageCollector

            from .image_generation import BigBananaImageGenerationTool

            tool = BigBananaImageGenerationTool(plugin=plugin)
            params, error = tool._resolve_params(plugin, prompt, None)
            if error or not params:
                await event.send(
                    event.chain_result(
                        [Comp.Plain(f"❌ {error or '解析后的绘图参数为空'}")]
                    )
                )
                return

            collector = ImageCollector(plugin=plugin, event=event, params=params)
            await collector.add_refer_images()
            await plugin.drawing_command_handler.generate_and_send_result(
                event, params, collector
            )
        except Exception:
            logger.exception("[BIG BANANA] 兜底生成失败")
        finally:
            plugin.task_manager.finish(task_id)
