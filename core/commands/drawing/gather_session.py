from __future__ import annotations

from typing import TYPE_CHECKING

from astrbot.api import logger
from astrbot.core.utils.session_waiter import session_waiter

from ...utils import get_message_id

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from astrbot.api.event import AstrMessageEvent
    from astrbot.core.message.message_event_result import MessageEventResult
    from astrbot.core.utils.session_waiter import SessionController

    from ....main import BigBanana
    from ...drawing.collector import ImageCollector


class DrawingGatherSession:
    """用于追加文本和图片的交互式收集模式会话。"""

    def __init__(
        self,
        *,
        plugin: BigBanana,
        event: AstrMessageEvent,
        params: dict,
        collector: ImageCollector,
    ) -> None:
        """初始化绘图命令组件所需的依赖和状态。"""
        self.plugin = plugin
        self.event = event
        self.params = params
        self.collector = collector
        self.cancelled = False
        self._handled_event_keys: set[str] = set()

    async def run(self) -> AsyncGenerator[MessageEventResult, None]:
        """运行收集模式会话并等待用户追加文本或图片。"""
        yield self.event.plain_result(
            self._build_gather_message(title="绘图收集模式已启用")
        )

        @session_waiter(
            timeout=self.plugin.preference_config.gather_timeout,
            record_history_chains=False,
        )
        async def waiter(
            controller: SessionController, waiter_event: AstrMessageEvent
        ) -> None:
            """处理交互式等待期间收到的后续消息。"""
            event_key = get_message_id(waiter_event) or f"event-{id(waiter_event)}"
            if event_key in self._handled_event_keys:
                # AstrBot 会按会话内每个活跃 session filter 各触发一次，同一事件只处理一次
                return
            self._handled_event_keys.add(event_key)

            if waiter_event.get_sender_id() != self.event.get_sender_id():
                return

            message_text = waiter_event.message_str.strip()
            if message_text == "取消":
                self.cancelled = True
                await waiter_event.send(waiter_event.plain_result("🍌 操作已取消。"))
                controller.stop()
                return

            if message_text == "开始":
                controller.stop()
                return

            # 纯文本消息已包含@的文本化，本插件的处理与之兼容
            user_params = self.plugin.prompt_config_manager.parse_prompt_params(
                message_text
            )
            # 取出并去除用户提示词
            user_prompt = user_params.pop("prompt", "")
            previous_prompt = self.params["prompt"]
            previous_images = len(self.collector.images)

            # 将文本拼接到prompt后面
            if user_prompt:
                self.params["prompt"] += " " + user_prompt

            # 更新参数
            self.params.update(user_params)

            # 收集消息中的图片
            await self.collector.add_msg_images(waiter_event)

            # 只在真正新增内容时回一行短提示，避免状态文本反复刷屏
            if (
                self.params["prompt"] != previous_prompt
                or len(self.collector.images) > previous_images
            ):
                await waiter_event.send(
                    waiter_event.plain_result(self._build_gather_ack())
                )
            controller.keep(
                timeout=self.plugin.preference_config.gather_timeout, reset_timeout=True
            )

        try:
            await waiter(self.event)
        except TimeoutError:
            self.cancelled = True
            yield self.event.plain_result("❌ 超时了，操作已取消！")
        except Exception as e:
            self.cancelled = True
            logger.error(f"绘图提示词追加模式出现错误: {e}", exc_info=True)
            yield self.event.plain_result("❌ 处理时发生了一个内部错误。")
        finally:
            if self.cancelled:
                self.event.stop_event()

    def _build_gather_message(self, title: str) -> str:
        """生成收集模式的当前状态提示文本。"""
        return (
            f"📝 {title}：\n"
            f"文本：{self.params['prompt']}\n"
            f"图片：{len(self.collector.images)} 张\n\n"
            f"💡 继续发送图片或文本，或者：\n"
            f"• 发送「开始」开始生成\n"
            f"• 发送「取消」取消操作\n"
            f"• {self.plugin.preference_config.gather_timeout} 秒内有效\n"
        )

    def _build_gather_ack(self) -> str:
        """生成一行式收集提示，避免长状态文本在追加时反复刷屏。"""
        return (
            f"📝 已收集：文本 {len(self.params['prompt'])} 字，"
            f"图片 {len(self.collector.images)} 张"
            f"（发送「开始」生成 / 「取消」放弃）"
        )
