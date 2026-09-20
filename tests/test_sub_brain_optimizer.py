import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from core.drawing.optimizer import SubBrainOptimizer, extract_image_refs
from core.schemas import SubBrainConfig


def build_optimizer(completion_text: str) -> SubBrainOptimizer:
    context = SimpleNamespace(
        llm_generate=AsyncMock(
            return_value=SimpleNamespace(completion_text=completion_text)
        )
    )
    config = SubBrainConfig(
        cmd_enabled=True,
        provider_id="test-provider",
        system_prompt="optimize the prompt",
    )
    return SubBrainOptimizer(context=context, sub_brain_config=config)


def test_extract_image_refs_supports_image_and_chinese_syntax() -> None:
    assert extract_image_refs("replace image 1 with 图2") == {1, 2}
    assert extract_image_refs("no references here") == set()


def test_optimized_prompt_dropping_an_image_reference_falls_back() -> None:
    optimizer = build_optimizer("Replace the character with the target avatar")

    result = asyncio.run(
        optimizer.optimize_prompt(None, "把 image 1 的人物换成 image 2")
    )

    assert result is None


def test_optimized_prompt_preserving_image_references_is_used() -> None:
    optimized = "Replace the character in image 1 with image 2"
    optimizer = build_optimizer(optimized)

    result = asyncio.run(
        optimizer.optimize_prompt(None, "把 image 1 的人物换成 image 2")
    )

    assert result == optimized
