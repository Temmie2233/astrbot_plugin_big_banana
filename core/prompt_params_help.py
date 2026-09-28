"""提示词参数帮助文本的数据与渲染。"""

from __future__ import annotations

from .schemas.constants import PARAMS_LIST

_USAGE = "用法：<触发词> 提示词 --参数 值 --参数 值（参数可放在提示词后的任意位置）"

# 参数名 -> (取值, 说明)
_PARAM_HELP: dict[str, tuple[str, str]] = {
    "min_images": ("<整数>", "最少参考图数量，不足时自动补 At/用户/机器人头像"),
    "max_images": ("<整数>", "最多参考图数量，上限受提供商限制"),
    "refer_images": ("<文件名[,文件名]>", "注入 refer_images 目录里的固定参考图"),
    "image_size": ("1K/2K/4K", "图片分辨率（gemini-3 系列生效）"),
    "aspect_ratio": ("1:1、16:9、21:9 等", "图片比例（Gemini/Grok 生效）"),
    "google_search": ("true/false", "启用谷歌搜索补充实时信息（Gemini）"),
    "negative_prompt": ("<文本>", "负面提示词，空格可用英文逗号代替"),
    "num_inference_steps": ("<整数>", "推理步数（部分提供商生效）"),
    "guidance_scale": ("<数字>", "提示词引导强度（部分提供商生效）"),
    "seed": ("<整数>", "随机种子（部分提供商生效）"),
    "preset_append": ("true/false", "把用户文本追加在预设提示词之后"),
    "gather_mode": ("true/false", "消息收集模式，发「开始」后生成"),
    "providers": ("<名[/模型][,名]>", "指定提供商与顺序，逗号分隔逐个回退"),
    "n": ("<整数>", "生成张数（OpenAI Images）"),
    "partial_images": ("0-3", "流式预览张数（OpenAI Images）"),
    "size": ("1536x1024、auto 等", "输出尺寸（OpenAI Images/Responses）"),
    "video_size": ("480p/720p/1080p 等", "视频尺寸"),
    "background": ("auto/transparent/opaque", "图片背景（OpenAI）"),
    "output_format": ("png/jpeg/webp", "输出格式（OpenAI）"),
    "output_compression": ("0-100", "压缩程度（OpenAI jpeg/webp）"),
    "input_fidelity": ("low/high", "参考图保真度（OpenAI，有参考图时）"),
    "action": ("auto/generate/edit", "生成或编辑动作（OpenAI Responses）"),
    "url": ("true/false", "只返回图片 URL，不直接发图"),
    "sub_brain": ("true/false", "是否用副脑优化提示词"),
    "moderation": ("low/auto", "内容审核级别（OpenAI Images）"),
    "capability": ("image_generation/video_generation", "本次使用的生成能力"),
    "quality": ("low/medium/high、speed/quality 等", "图片/视频质量"),
    "fps": ("30/60", "视频帧率（CogVideoX）"),
    "with_audio": ("true/false", "视频是否生成音频"),
    "duration": ("1-15", "视频时长（秒，Grok）"),
    "watermark_enabled": ("true/false", "是否添加 AI 水印"),
}

def build_prompt_params_help() -> str:
    """渲染提示词参数帮助文本。"""
    lines = [
        f"🍌 绘图提示词参数（全部 {len(PARAMS_LIST)} 个）：",
        _USAGE,
        "",
    ]
    for name in PARAMS_LIST:
        values, desc = _PARAM_HELP.get(name, ("", "按提供商支持情况生效"))
        if values:
            lines.append(f"• --{name} {values}：{desc}")
        else:
            lines.append(f"• --{name}：{desc}")
    lines.extend(["", "未列出的取值以对应提供商文档为准。"])
    return "\n".join(lines)
