import asyncio
from types import SimpleNamespace

import astrbot.api.message_components as Comp

from core.llm_tools.retry_guard import (
    RETRY_COUNT_EXTRA,
    TOOL_CALLED_EXTRA,
    FakeCallRetryGuard,
)


class FakeResult:
    def __init__(self, chain):
        self.chain = chain

    def is_llm_result(self):
        return True


class FakeEvent:
    def __init__(
        self,
        message_str,
        chain,
        retry_count=0,
        tool_called=False,
    ):
        self.message_str = message_str
        self._extras = {}
        if retry_count:
            self._extras[RETRY_COUNT_EXTRA] = retry_count
        if tool_called:
            self._extras[TOOL_CALLED_EXTRA] = True
        self._result = FakeResult(chain)
        self.stopped = False
        self.sent = []

    def get_result(self):
        return self._result

    def clear_result(self):
        self._result = None

    def get_extra(self, key, default=None):
        return self._extras.get(key, default)

    def set_extra(self, key, value):
        self._extras[key] = value

    def clear_extra(self):
        self._extras.clear()

    def stop_event(self):
        self.stopped = True

    def is_stopped(self):
        return self.stopped

    def chain_result(self, chain):
        return chain

    async def send(self, chain):
        self.sent.append(chain)


class FakeQueue:
    def __init__(self):
        self.items = []

    def put_nowait(self, item):
        self.items.append(item)


def make_plugin(
    queue,
    *,
    fallback=True,
    retry_use_provider=True,
    retry_provider="siliconflow/deepseek-ai/DeepSeek-V4-Flash",
    max_retries=1,
    whitelist_allowed=True,
):
    started = []
    finished = []
    check = lambda event, is_command: SimpleNamespace(  # noqa: E731
        allowed=whitelist_allowed,
        message="当前群不在白名单内，无法使用图片生成功能。",
    )
    checks = []
    plugin = SimpleNamespace(
        llm_tools_config=SimpleNamespace(
            fake_call_retry_enabled=True,
            fake_call_retry_max=max_retries,
            fake_call_retry_use_provider=retry_use_provider,
            fake_call_retry_provider=retry_provider,
            fake_call_fallback_generate=fallback,
        ),
        context=SimpleNamespace(get_event_queue=lambda: queue),
        task_manager=SimpleNamespace(
            build_task_id=lambda event: "umo:1",
            start=lambda tid, task: started.append((tid, task)),
            finish=lambda tid: finished.append(tid),
        ),
        whitelist_guard=SimpleNamespace(check=check),
        cooldown_guard=SimpleNamespace(
            check=lambda event: SimpleNamespace(allowed=True, message=""),
            mark_cooldown=lambda event: checks.append(event),
        ),
        drawing_command_handler=SimpleNamespace(),
    )
    plugin.started_tasks = started
    plugin.finished_tasks = finished
    plugin.cooldown_marks = checks
    return plugin


def test_real_tool_call_is_not_intercepted():
    async def scenario():
        queue = FakeQueue()
        guard = FakeCallRetryGuard(make_plugin(queue))
        event = FakeEvent(
            "画一张你自己的人设图看看",
            [Comp.Plain("正在调用 banana_image_generation 工具！")],
            tool_called=True,
        )

        await guard.handle_decorating_result(event)

        assert queue.items == []
        assert event.get_result() is not None

    asyncio.run(scenario())


def test_fake_call_is_retried_with_strong_model():
    async def scenario():
        queue = FakeQueue()
        plugin = make_plugin(queue)
        guard = FakeCallRetryGuard(plugin)
        event = FakeEvent(
            "画一张你自己的人设图看看",
            [
                Comp.Plain(
                    "喵～正在调用 banana_image_generation 工具！\n"
                    "【提示词】日系可爱猫娘，茶色长发，绿色眼睛"
                )
            ],
        )

        await guard.handle_decorating_result(event)

        assert event.is_stopped()
        assert event.get_result() is None
        assert len(queue.items) == 1
        queued = queue.items[0]
        assert queued.get_extra(RETRY_COUNT_EXTRA) == 1
        assert (
            queued.get_extra("selected_provider")
            == "siliconflow/deepseek-ai/DeepSeek-V4-Flash"
        )
        assert "banana_image_generation" in queued.message_str
        assert "日系可爱猫娘，茶色长发，绿色眼睛" in queued.message_str

    asyncio.run(scenario())


def test_provider_switch_off_keeps_current_provider():
    async def scenario():
        queue = FakeQueue()
        plugin = make_plugin(queue, retry_use_provider=False)
        guard = FakeCallRetryGuard(plugin)
        event = FakeEvent(
            "画一张你自己的人设图看看",
            [Comp.Plain("正在调用 banana_image_generation 工具！")],
        )

        await guard.handle_decorating_result(event)

        assert len(queue.items) == 1
        assert queue.items[0].get_extra("selected_provider") is None

    asyncio.run(scenario())


def test_fake_claim_without_draw_intent_is_ignored():
    async def scenario():
        queue = FakeQueue()
        guard = FakeCallRetryGuard(make_plugin(queue))
        event = FakeEvent("晚上吃什么", [Comp.Plain("正在生成中喵～")])

        await guard.handle_decorating_result(event)

        assert queue.items == []
        assert event.get_result() is not None

    asyncio.run(scenario())


def test_retry_limit_falls_back_to_generation():
    async def scenario():
        queue = FakeQueue()
        plugin = make_plugin(queue, fallback=True, whitelist_allowed=False)
        guard = FakeCallRetryGuard(plugin)
        event = FakeEvent(
            "画一张你自己的人设图看看",
            [Comp.Plain("正在调用 banana_image_generation 工具！")],
            retry_count=1,
        )

        await guard.handle_decorating_result(event)

        assert event.get_result() is None
        assert queue.items == []
        assert len(plugin.started_tasks) == 1
        task = plugin.started_tasks[0][1]
        await asyncio.gather(task, return_exceptions=True)
        assert event.sent, "兜底路径应向用户发送白名单拒绝说明"
        assert plugin.finished_tasks == ["umo:1:fake-call-fallback"]

    asyncio.run(scenario())


def test_retry_disabled_by_config_gives_up_after_limit():
    async def scenario():
        queue = FakeQueue()
        plugin = make_plugin(queue, fallback=False)
        guard = FakeCallRetryGuard(plugin)
        event = FakeEvent(
            "画一张你自己的人设图看看",
            [Comp.Plain("正在调用 banana_image_generation 工具！")],
            retry_count=1,
        )

        await guard.handle_decorating_result(event)

        assert queue.items == []
        assert plugin.started_tasks == []
        assert event.get_result() is not None

    asyncio.run(scenario())


def test_retry_defaults_match_schema():
    import json
    from pathlib import Path

    from core.schemas import LlmToolsConfig

    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "_conf_schema.json").read_text(
            encoding="utf-8"
        )
    )
    items = schema["llm_tools"]["items"]
    cfg = LlmToolsConfig()

    assert cfg.fake_call_retry_enabled is True
    assert items["fake_call_retry_enabled"]["default"] is True
    assert cfg.fake_call_retry_max == 1
    assert items["fake_call_retry_max"]["default"] == 1
    assert cfg.fake_call_retry_provider == ""
    assert items["fake_call_retry_provider"]["default"] == ""
    assert items["fake_call_retry_provider"]["_special"] == "select_provider"
    assert items["fake_call_retry_provider"]["condition"] == {
        "fake_call_retry_use_provider": True
    }
    assert cfg.fake_call_retry_use_provider is True
    assert items["fake_call_retry_use_provider"]["default"] is True
    assert cfg.fake_call_fallback_generate is True
    assert items["fake_call_fallback_generate"]["default"] is True


if __name__ == "__main__":
    test_real_tool_call_is_not_intercepted()
    test_fake_call_is_retried_with_strong_model()
    test_provider_switch_off_keeps_current_provider()
    test_fake_claim_without_draw_intent_is_ignored()
    test_retry_limit_falls_back_to_generation()
    test_retry_disabled_by_config_gives_up_after_limit()
    test_retry_defaults_match_schema()
    print("test_fake_call_retry: all scenarios passed")
