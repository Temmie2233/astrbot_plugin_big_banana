import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from core.drawing.tasks import DrawingTaskManager
from core.llm_tools.image_generation import BigBananaImageGenerationTool


def test_task_manager_session_llm_task_tracking() -> None:
    async def scenario() -> None:
        manager = DrawingTaskManager()
        session_id = "group_123"

        assert manager.get_session_llm_task_count(session_id) == 0

        fut = asyncio.Future()

        async def worker():
            await fut

        task = asyncio.create_task(worker())

        manager.start_llm_task(session_id, task)
        assert manager.get_session_llm_task_count(session_id) == 1

        fut.set_result(None)
        await task
        # Done callback will clean up automatically
        assert manager.get_session_llm_task_count(session_id) == 0

    asyncio.run(scenario())


def test_task_manager_cancel_all_clears_session_llm_tasks() -> None:
    async def scenario() -> None:
        manager = DrawingTaskManager()
        session_id = "group_123"

        assert manager.get_session_llm_task_count(session_id) == 0

        fut = asyncio.Future()

        async def worker() -> None:
            await fut

        task1 = asyncio.create_task(worker())
        task2 = asyncio.create_task(worker())

        manager.start_llm_task(session_id, task1)
        manager.start_llm_task(session_id, task2)
        assert manager.get_session_llm_task_count(session_id) == 2

        await manager.cancel_all()

        assert manager.get_session_llm_task_count(session_id) == 0
        assert manager.session_llm_tasks == {}

        fut.set_result(None)
        await asyncio.gather(task1, task2, return_exceptions=True)

    asyncio.run(scenario())


def test_submit_drawing_task_respects_session_limit() -> None:
    async def scenario() -> None:
        task_manager = DrawingTaskManager()
        tool = BigBananaImageGenerationTool()

        plugin = SimpleNamespace(
            task_manager=task_manager,
            llm_tools_config=SimpleNamespace(
                llm_tool_max_tasks_per_session=1,
                llm_tool_use_background_task=True,
                llm_tool_direct_send_result=True,
            ),
            preference_config=SimpleNamespace(
                enable_llm_tool_drawing_message=False,
                drawing_message="drawing...",
            ),
            background_callback=SimpleNamespace(enabled=lambda: False),
            whitelist_guard=SimpleNamespace(check=lambda *a, **k: SimpleNamespace(allowed=True)),
            cooldown_guard=SimpleNamespace(
                check=lambda *a, **k: SimpleNamespace(allowed=True),
                mark_cooldown=lambda *a, **k: None,
            ),
        )

        event1 = SimpleNamespace(
            unified_msg_origin="session_A",
            message_obj=SimpleNamespace(message_id="msg_1"),
        )
        event2 = SimpleNamespace(
            unified_msg_origin="session_A",
            message_obj=SimpleNamespace(message_id="msg_2"),
        )
        event_other = SimpleNamespace(
            unified_msg_origin="session_B",
            message_obj=SimpleNamespace(message_id="msg_3"),
        )

        # Mock _generate_and_send_result to block until signaled
        started_event = asyncio.Event()
        release_event = asyncio.Event()

        async def mock_generate(*args, **kwargs):
            started_event.set()
            await release_event.wait()
            return "done"

        tool._generate_and_send_result = mock_generate

        # 1st call in session_A -> starts background task
        res1 = await tool._submit_drawing_task(plugin, event1, {"prompt": "test1"})
        assert "后台绘图任务已启动" in res1
        await started_event.wait()
        assert task_manager.get_session_llm_task_count("session_A") == 1

        # 2nd call in session_A -> blocked by session limit
        res2 = await tool._submit_drawing_task(plugin, event2, {"prompt": "test2"})
        assert "当前会话已有正在执行的图片生成任务" in res2
        assert "已达会话上限" in res2

        # 3rd call in session_B -> allowed
        started_event_b = asyncio.Event()
        async def mock_generate_b(*args, **kwargs):
            started_event_b.set()
            await release_event.wait()
            return "done"
        
        tool._generate_and_send_result = mock_generate_b
        res_b = await tool._submit_drawing_task(plugin, event_other, {"prompt": "test3"})
        assert "后台绘图任务已启动" in res_b
        await started_event_b.wait()
        assert task_manager.get_session_llm_task_count("session_B") == 1

        # Release first task and deterministically wait for cleanup
        release_event.set()
        for _ in range(100):
            if (
                task_manager.get_session_llm_task_count("session_A") == 0
                and task_manager.get_session_llm_task_count("session_B") == 0
            ):
                break
            await asyncio.sleep(0.01)

        assert task_manager.get_session_llm_task_count("session_A") == 0
        assert task_manager.get_session_llm_task_count("session_B") == 0

    asyncio.run(scenario())


def test_session_limit_can_be_disabled() -> None:
    async def scenario() -> None:
        task_manager = DrawingTaskManager()
        tool = BigBananaImageGenerationTool()

        plugin = SimpleNamespace(
            task_manager=task_manager,
            llm_tools_config=SimpleNamespace(
                llm_tool_max_tasks_per_session=0,  # 0 means unlimited
                llm_tool_use_background_task=True,
                llm_tool_direct_send_result=True,
            ),
            preference_config=SimpleNamespace(
                enable_llm_tool_drawing_message=False,
                drawing_message="drawing...",
            ),
            background_callback=SimpleNamespace(enabled=lambda: False),
        )

        event1 = SimpleNamespace(
            unified_msg_origin="session_A",
            message_obj=SimpleNamespace(message_id="msg_1"),
        )
        event2 = SimpleNamespace(
            unified_msg_origin="session_A",
            message_obj=SimpleNamespace(message_id="msg_2"),
        )

        release_event = asyncio.Event()

        async def mock_generate(*args, **kwargs):
            await release_event.wait()
            return "done"

        tool._generate_and_send_result = mock_generate

        res1 = await tool._submit_drawing_task(plugin, event1, {"prompt": "test1"})
        res2 = await tool._submit_drawing_task(plugin, event2, {"prompt": "test2"})

        assert "后台绘图任务已启动" in res1
        assert "后台绘图任务已启动" in res2

        release_event.set()
        for _ in range(100):
            if task_manager.get_session_llm_task_count("session_A") == 0:
                break
            await asyncio.sleep(0.01)

        assert task_manager.get_session_llm_task_count("session_A") == 0

    asyncio.run(scenario())
