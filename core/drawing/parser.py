from __future__ import annotations

from typing import TYPE_CHECKING, Any

import astrbot.api.message_components as Comp

from ..utils.animation import ensure_gif_params, prompt_requests_animation
from .mention_utils import (
    QQ_OFFICIAL_MENTION_RE,
    format_mention,
    get_qq_official_mention_names,
)

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent

    from ...main import BigBanana


def _detect_gif_params(params: dict[str, Any]) -> None:
    """提示词明确要求 GIF/动图时，自动补齐 output_format 和帧参数。"""
    output_format = str(params.get("output_format") or "").strip().lower()
    if output_format not in {"", "default"}:
        return
    if prompt_requests_animation(str(params.get("prompt") or "")):
        params["output_format"] = "gif"
        ensure_gif_params(params)


def parse_params(plugin: BigBanana, event: AstrMessageEvent) -> dict[str, Any] | None:
    """解析消息事件中的绘图参数。未命中指令时快速返回 None。这里不负责收集图片，也不能修改消息链。"""

    # 提取首个有内容的 Plain 文本（目的是跳过开头是@的情况）
    first_text = ""
    first_component_idx = -1
    for idx, component in enumerate(event.get_messages()):
        if isinstance(component, Comp.Plain) and component.text.strip():
            first_text = component.text.strip()
            first_component_idx = idx
            break

    # 无有效文本，则不可能触发命令，直接返回 None
    if not first_text or first_component_idx == -1:
        return None

    # 命令前缀匹配
    matched_prefix = False
    for prefix in plugin.prefix_config.prefix_list:
        if first_text.startswith(prefix):
            # 去掉前缀，并去除前缀后的空格
            first_text = first_text.removeprefix(prefix).lstrip()
            matched_prefix = True
            break

    # 未 @ 机器人、未开启混合模式、配置了前缀但未匹配到前缀
    if (
        not event.is_at_or_wake_command
        and not plugin.prefix_config.coexist_enabled
        and plugin.prefix_config.prefix_list
        and not matched_prefix
    ):
        return None

    prompt_config = plugin.prompt_config_manager.prompt_config

    # 提供商前缀匹配。首词如果已经是预设名称，必须优先按预设处理，
    # 否则同名的提供商会先被剥离，导致后续无法找到预设。
    provider_names: list[str] = []
    if plugin.prefix_config.provider_prefix:
        token, _, rest = first_text.partition(" ")
        # 如果首词不是预设，才尝试将其解析为提供商前缀。
        if token and rest and token not in prompt_config:
            # token按逗号分割
            tokens = token.split(",")
            for t in tokens:
                provider_name = t.strip()
                template_config = plugin.provider_config_manager.provider_configs.get(
                    provider_name
                )
                if template_config:
                    provider_name = template_config.name
                elif provider_name not in plugin.conf.get("default_astr_providers", []):
                    provider_name = None
                # 匹配成功，去除提供商前缀和后续空格
                if provider_name:
                    provider_names.append(provider_name)
            if provider_names:
                first_text = rest.lstrip()

    # 提取预设指令和该组件内剩余文本
    cmd, _, cmd_rest = first_text.partition(" ")
    cmd = cmd.strip()

    # 未匹配提供商前缀时，必须命中预设；匹配到提供商前缀但没有预设时，
    # 将提供商前缀后的全部内容作为普通提示词，使用默认配置参数生成。
    if not cmd or cmd not in prompt_config:
        if not provider_names:
            return None

        params = plugin.prompt_config_manager.parse_prompt_params(
            _collect_user_text(event, first_component_idx, first_text)
        )
        params["providers"] = provider_names
        _detect_gif_params(params)
        return params

    # 复制预设参数，防止污染全局预设。
    # 上游约束了非引用类型，这里只需浅拷贝即可。
    params = prompt_config[cmd].copy()
    # 如果有手动指定提供商，将覆盖预设中的提供商
    if provider_names:
        params["providers"] = provider_names

    preset_prompt = params.get("prompt", "")
    should_append_user_text = params.get(
        "preset_append", plugin.common_config.preset_append
    )
    # 有占位符时替换指定位置；启用补充时把用户文本追加到固定预设后。
    if "{{user_text}}" in preset_prompt or should_append_user_text:
        user_text = _collect_user_text(event, first_component_idx, cmd_rest)
        # 解析用户输入文本中的参数
        user_params = plugin.prompt_config_manager.parse_prompt_params(user_text)
        # 取出并删除用户提示词
        user_prompt = user_params.pop("prompt", "")
        if "{{user_text}}" in preset_prompt:
            params["prompt"] = preset_prompt.replace("{{user_text}}", user_prompt)
        elif user_prompt:
            params["prompt"] = f"{preset_prompt.rstrip()} {user_prompt}".strip()
        # 更新参数
        params.update(user_params)

    _detect_gif_params(params)
    return params


def _collect_user_text(
    event: AstrMessageEvent,
    first_component_idx: int,
    first_component_text: str,
) -> str:
    """收集首个文本组件中已去除命令部分的用户文本及其余消息内容。"""

    # 只有 QQ 官方 Bot 才会把用户 @ 保留成 <@ID> 文本；其他平台的普通文本
    # 不做正则替换，避免误把用户实际输入的相同格式文本改掉。
    is_qq_official = event.platform_meta.name in {
        "qq_official",
        "qq_official_webhook",
    }

    # 先从原始 mention 对象建立 ID -> 昵称映射。这里不直接使用 message_str：
    # 官 Bot 的 message_str 可能只保留 <@ID>，而昵称只在原始 mentions 中可用。
    # 也不在这里逐个调用成员查询接口：该函数是同步的，而且群聊/C2C 不一定有
    # 通用的成员昵称查询接口；查不到时由后面的 format_mention 回退到 ID。
    mention_names = get_qq_official_mention_names(event) if is_qq_official else {}

    # 先扫描全部 mention，再开始拼接文本。这样可以在遇到第一个同名用户前
    # 就知道是否需要追加 ID；如果边扫描边输出，后面发现重名时还要回头修改
    # 已输出的内容。
    mention_refs = _collect_mention_refs(event, is_qq_official, mention_names)

    # 用“昵称 -> 不同用户 ID 集合”判断重名，而不是直接统计 mention 次数。
    # 同一个用户在一句话里重复 @ 不应该被当成重名；只有不同用户共用昵称时，
    # 才需要生成 @昵称(ID)。
    users_by_nickname: dict[str, set[str]] = {}
    for user_id, nickname in mention_refs:
        if nickname:
            users_by_nickname.setdefault(nickname, set()).add(user_id)
    duplicate_nicknames = {
        nickname
        for nickname, user_ids in users_by_nickname.items()
        if len(user_ids) > 1
    }

    # 按消息组件原有顺序重新组装提示词，避免只读取 message_str 时丢失
    # 独立的 At 组件。首个 Plain 使用调用方传入的文本，是因为其中可能已经
    # 去掉了命令/预设名称，不能再直接使用原始 comp.text。
    message_parts: list[str] = []
    for idx, comp in enumerate(event.get_messages()):
        if idx == first_component_idx:
            if first_component_text:
                message_parts.append(
                    _clean_qq_official_mentions(
                        first_component_text,
                        mention_names,
                        duplicate_nicknames,
                        is_qq_official,
                    )
                )
        # 官 Bot 的用户 @ 位于 Plain 文本中，需要把 <@ID> 清洗成统一的
        # @昵称或@ID；其他平台的 Plain 保持原文，避免改变用户真实提示词。
        elif isinstance(comp, Comp.Plain) and comp.text:
            message_parts.append(
                _clean_qq_official_mentions(
                    comp.text,
                    mention_names,
                    duplicate_nicknames,
                    is_qq_official,
                )
            )
        # AstrBot 的 At 组件已经提供 name，优先使用它；没有 name 时由
        # format_mention 使用 qq。这里不再保留旧的 @昵称(ID) 格式，统一交给
        # 同名判断决定是否追加 ID。
        elif isinstance(comp, Comp.At) and comp.qq:
            nickname = comp.name.strip() if comp.name else None
            message_parts.append(
                "@" + format_mention(str(comp.qq), nickname, duplicate_nicknames)
            )
    # 首尾空格主要来自命令剥离和组件拼接，最终提示词不需要保留它们。
    return " ".join(message_parts).strip()


def _collect_mention_refs(
    event: AstrMessageEvent,
    is_qq_official: bool,
    mention_names: dict[str, str],
) -> list[tuple[str, str | None]]:
    refs: list[tuple[str, str | None]] = []
    for comp in event.get_messages():
        if isinstance(comp, Comp.At) and comp.qq:
            nickname = comp.name.strip() if comp.name else None
            refs.append((str(comp.qq), nickname))
        elif is_qq_official and isinstance(comp, Comp.Plain):
            refs.extend(
                (user_id, mention_names.get(user_id))
                for user_id in QQ_OFFICIAL_MENTION_RE.findall(comp.text)
            )
    return refs


def _clean_qq_official_mentions(
    text: str,
    mention_names: dict[str, str],
    duplicate_nicknames: set[str],
    is_qq_official: bool,
) -> str:
    if not is_qq_official:
        return text
    return QQ_OFFICIAL_MENTION_RE.sub(
        lambda match: (
            "@"
            + format_mention(
                match.group(1),
                mention_names.get(match.group(1)),
                duplicate_nicknames,
            )
        ),
        text,
    )
