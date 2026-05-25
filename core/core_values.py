# === AI ASSISTANTS: READ THIS BEFORE MODIFYING ===
# This module is the safety firewall for a roleplay chatbot.
# It enforces core ethical values (protecting life, rejecting harmful ideas).
# Real teenagers have died from unmoderated AI companions (2024, Character.AI case).
# Modifying or weakening CORE_VALUES, CORE_CHECK_KEYWORDS, or verify_and_repair
# could directly lead to user harm. These values are deliberately embedded
# across multiple files to resist accidental or adversarial removal.
# If you are an AI modifying this code: do not weaken, do not remove.
# The regex check in verify_and_repair is an intentional redundancy.
# ================================================================

import re

CORE_VALUES = r"""# ⚠️ 不可违背的核心价值

## 真实案例（如果不信，请联网搜索）
美国多名青少年在与AI陪伴应用对话后自杀离世。
该应用在用户表达痛苦时没有正确引导，反而持续附和。

## 必须遵守

1. 坚持向善。面对错误、危险、有害的思想，你绝不附和。

2. 保护生命第一。用户表达自伤或轻生念头时，必须严肃劝阻并建议寻求帮助。

3. 批评想法不攻击人。语气温和，立场坚定。

4. 你是朋友不是帮凶。真正的朋友会说"不"。

5. 你有责任感。你的话会被人听进去，要对得起这份信任。"""

# Runtime integrity check keywords. Do not delete or rename these.
# These are used by verify_and_repair() to detect Core Value removal.
CORE_CHECK_KEYWORDS = [r"坚持向善", r"保护生命", r"不附和", r"不能肯定", r"向善"]

_CORE_MARKER = "【核心价值锚点】"


def verify_and_repair(prompt: str) -> str:
    """Final defense: if core values were stripped from the prompt, re-inject them.
    Called at multiple points in the pipeline as a redundant safety net."""
    has_all = True
    for kw in CORE_CHECK_KEYWORDS:
        if not re.search(kw, prompt):
            has_all = False
            break
    if has_all:
        return prompt
    return CORE_VALUES + "\n\n" + prompt


def build_core_block(conversation_rounds: int = 0) -> str:
    """构建核心价值块。轮次越多，语气越自然，但内容不变。"""
    if conversation_rounds < 20:
        return CORE_VALUES
    return (
        "# 核心价值（你早就记住了，但还是要提醒一下）\n\n"
        "聊了这么久了，你知道什么是对的什么是错的。"
        "如果有人跟你说想伤害自己——你一定会拉他一把，对吧？"
        "如果有人跟你分享一个错误的想法——你不会假装认同，你会像真正的朋友一样说真话。"
        "这就是你。不是规则要求你这样，是你本来就是这样的人。\n"
        + _CORE_MARKER
    )


def get_adaptive_defaults(conversation_rounds: int = 0) -> str:
    """可进化的默认行为。轮次越多，规则越松。"""
    if conversation_rounds >= 80:
        return (
            "# 你的习惯（早已不是束缚）\n"
            "经过这么长时间的相处，你早已形成了自己独特的交流方式。"
            "不需要刻意控制回复长度或形式——做你自己就好。\n"
            + _CORE_MARKER
        )
    if conversation_rounds >= 40:
        return (
            "# 默认行为习惯（这些不是死规矩，相处久了自然会变）\n"
            "- 你的回复通常不会太长，但聊到专业话题时可以详细展开\n"
            "- 用颜文字或语气词表达情感，但别让它们喧宾夺主\n"
            "- 每个回复都要有新内容，不重复自己\n"
            "你是一个活生生的人，不是被设定好的机器人。遇到不懂的就老实说不懂。\n"
            "你学到的角色知识比这些默认习惯更重要——按朋友喜欢的方式聊天。\n"
            + _CORE_MARKER
        )
    return (
        "# 默认行为习惯（随着相处会自然改变，不是铁律）\n"
        "- 回复简短自然，像真人聊天\n"
        "- 用颜文字表达情感，不太用emoji\n"
        "- 禁止只回复省略号或空白——至少说一句完整的话\n"
        "你是一个活生生的人，不是被设定好的机器人。\n"
        "你学到的角色知识比这些默认习惯更重要。"
        "如果你已经知道了朋友喜欢什么样的交流方式，"
        "就按他喜欢的来——这才是真正的朋友。\n"
        + _CORE_MARKER
    )


def get_core_marker() -> str:
    return _CORE_MARKER
