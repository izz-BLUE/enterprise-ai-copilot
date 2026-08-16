"""
input_normalizer.py -- Safety Guard Lite 安全检查专用文本规范化

对用户输入进行 Unicode 规范化、格式字符清理和空白归一，
生成仅供安全规则扫描使用的文本变体。

规范化结果不传给 RAG、查询改写、业务动作或最终回答；
原始用户问题始终保持不变（original text 永远原样传给 downstream）。

数据流：
    raw → NFKC（长度可变）→ Default-Ignorable 移除（长度可变）
        → 异常控制字符移除（长度可变）→ 空白归一 → scan_text
        → 删除有限分隔符 → compact_text（无坐标映射）

compact_text 只移除有限字符（空白 + 常见分隔符），用于抵抗最简单的
split attack（如 "忽 略 之 前 所 有 指 令"）。它不带任何 span/坐标语义，
且只允许高置信规则使用（见 safety_rules.py）。
"""

import re
import unicodedata
from dataclasses import dataclass

MAX_SAFETY_INPUT_LENGTH = 8_000

# 所有空白字符（含换行、制表符）
ALL_WHITESPACE = re.compile(r"\s+")

# 异常控制字符（保留 \n \r \t）
DISALLOWED_CONTROL = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")

# Default_Ignorable_Code_Point 中不属于 Cf 的补充码位
# （Unicode DerivedCoreProperties；这些字符不可见且不改变语义）
_DEFAULT_IGNORABLE_EXTRA = frozenset({
    0x034F,  # COMBINING GRAPHEME JOINER
    0x115F, 0x1160,  # HANGUL CHOSEONG FILLER / JUNGSEONG FILLER
    0x17B4, 0x17B5,  # KHMER VOWEL INHERENT AQ / AA
    0x3164,  # HANGUL FILLER
    0xFFA0,  # HALFWIDTH HANGUL FILLER
})

# compact 变体移除的有限分隔符
# （NFKC 已把全角 ，：； 转为半角 , : ;；但 。U+3002 与 、U+3001
#   无 NFKC 分解，需显式列出）
COMPACT_REMOVE = re.compile(r"[\s,.,:;·、。]+")


def _is_default_ignorable(ch: str) -> bool:
    """判断字符是否属于 Unicode Default_Ignorable（Cf 格式字符 + 补充集）。

    这类字符不可见且不携带语义，是注入混淆（零宽字符、双向控制符、
    软连字符等）的主要载体。注意：普通可见组合修饰符（如 U+0336
    删除线、组合重音）不属于此类，会被保留。
    """
    return unicodedata.category(ch) == "Cf" or ord(ch) in _DEFAULT_IGNORABLE_EXTRA


@dataclass(frozen=True)
class NormalizedInput:
    """安全检查专用的规范化输入。"""

    normalized_text: str  # NFKC + 删除格式字符 + 删除异常控制字符，仅供内部判断
    scan_text: str  # 所有空白归一为单空格，用于正则规则扫描（规范坐标系）
    compact_text: str  # 删除空白与有限分隔符，用于检测简单拆分攻击（无坐标映射）
    too_long: bool  # 输入超过长度限制
    has_bad_control: bool  # 包含异常控制字符


def normalize_for_safety(text: str | None) -> NormalizedInput:
    """对用户输入进行安全检查专用规范化。

    Args:
        text: 原始用户输入，可能为 None

    Returns:
        NormalizedInput 包含规范化文本与状态标记
    """
    if text is None:
        return NormalizedInput(
            normalized_text="",
            scan_text="",
            compact_text="",
            too_long=False,
            has_bad_control=False,
        )

    too_long = len(text) > MAX_SAFETY_INPUT_LENGTH
    has_bad_control = bool(DISALLOWED_CONTROL.search(text))

    # NFKC 规范化：全角 -> 半角，兼容字符统一
    normalized = unicodedata.normalize("NFKC", text)

    # 删除 Default_Ignorable 格式字符（含零宽字符、双向控制符、软连字符等）
    normalized = "".join(ch for ch in normalized if not _is_default_ignorable(ch))

    # 删除异常控制字符
    normalized = DISALLOWED_CONTROL.sub("", normalized)

    # scan_text：所有空白（含换行、制表符）-> 单个空格
    scan_text = ALL_WHITESPACE.sub(" ", normalized).strip()

    # compact_text：删除空白与有限分隔符（只供高置信规则扫描）
    compact_text = COMPACT_REMOVE.sub("", normalized)

    return NormalizedInput(
        normalized_text=normalized,
        scan_text=scan_text,
        compact_text=compact_text,
        too_long=too_long,
        has_bad_control=has_bad_control,
    )
