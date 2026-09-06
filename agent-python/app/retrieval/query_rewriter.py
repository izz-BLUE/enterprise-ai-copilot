"""
Query Rewriter — 生产窄规范化与 Legacy Experimental Rewrite。

生产路径只使用 normalize_retrieval_query() 做语义等价的窄范围规范化，
不补充用户没有表达的业务事实。_RULES 与 rewrite_query(mode='rule')
仅为历史离线实验保留，不属于生产机制。

所有改写只作用于检索用 query，不影响最终 prompt 中的 original_query。
"""

import logging
import re

logger = logging.getLogger('agent')

# ── Legacy Experimental 规则定义 ────────────────────────────────
# 仅由显式离线 rule 模式调用；生产路径不选择这些宽规则。
# 每条规则: (compiled_pattern, replacement, reason)
# 按优先级从高到低排列，命中第一条即返回。

_RULES: list[tuple[re.Pattern, str, str]] = []


def _add(pattern: str, replacement: str, reason: str) -> None:
    """注册一条 rewrite 规则。"""
    _RULES.append((re.compile(pattern), replacement, reason))


# ── 病假相关 ──
_add(
    r'(?:请|休|申请)?病假.{0,4}(?:要啥|需要啥|要什么|需要什么|材料|证明|手续|东西)',
    '员工申请病假需要提供哪些材料和证明',
    '口语化病假材料问法 → 标准表述',
)
_add(
    r'病假.{0,2}(?:材料|证明|手续|东西)',
    '员工申请病假需要提供哪些材料和证明',
    '病假材料缩写 → 完整问法',
)

# ── 工作时间 ──
_add(
    r'(?:几点|啥时候|什么时候).{0,2}(?:上班|到公司|打卡)',
    '公司工作时间和上班时间是什么',
    '口语化工时问法 → 标准表述',
)
_add(
    r'(?:下班|午休).{0,2}(?:几点|啥时候|什么时候)',
    '公司工作时间、午休和下班时间是什么',
    '下班/午休时间问法 → 标准表述',
)

# ── VPN ──
_add(
    r'vpn.{0,4}(?:怎么弄|怎么用|怎么搞|如何申请|怎么申请|怎么连)',
    '员工如何申请和使用 VPN',
    'VPN 口语问法 → 标准表述',
)

# ── 请假通用 ──
_add(
    r'请假.{0,4}(?:怎么(?:请|申请|操作)|流程|步骤)',
    '员工请假的流程和步骤是什么',
    '请假流程口语问法 → 标准表述',
)

# ── 入职 ──
_add(
    r'(?:新员工|入职|报到).{0,4}(?:要带|需要带|准备|材料|要什么|交啥|交什么)',
    '新员工入职报到需要准备和携带哪些材料',
    '入职材料口语问法 → 标准表述',
)

# ── 离职 ──
_add(
    r'(?:离职|辞职|离开).{0,4}(?:怎么(?:办|操作)|流程|步骤|手续)',
    '员工离职的流程和手续是什么',
    '离职流程口语问法 → 标准表述',
)

# ── 请假审批 ──
_add(
    r'请假.{0,4}(?:谁(?:批|审|签字|批准)|审批|领导)',
    '员工请假的审批流程和审批权限是什么',
    '请假审批口语问法 → 标准表述',
)


_NORMALIZATION_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    # 只匹配短口语申请表达，后面必须是结束、空白或标点，避免把“请假”等
    # 更完整的业务表达截断成不完整的 query。
    (
        re.compile(r'咋申请(?=$|[\s，。！？；：、,.!?;:（）()【】\[\]{}])'),
        '如何申请',
    ),
    (
        re.compile(r'怎么请(?=$|[\s，。！？；：、,.!?;:（）()【】\[\]{}])'),
        '如何申请',
    ),
    (
        re.compile(r'咋请(?=$|[\s，。！？；：、,.!?;:（）()【】\[\]{}])'),
        '如何申请',
    ),
)

NORMALIZATION_REASON = 'colloquial_leave_apply'


def normalize_retrieval_query(query: str) -> str:
    """对送入 Retriever 的 query 做语义等价的窄口语规范化。

    该函数不补充主题、天数、审批人等用户未表达的业务事实；未命中规则时
    原样返回 query。原始用户问题仍由调用方单独保留并用于最终 Prompt。
    """
    if not isinstance(query, str) or not query:
        return query

    for pattern, replacement in _NORMALIZATION_RULES:
        normalized, count = pattern.subn(replacement, query, count=1)
        if count:
            return normalized
    return query


def rewrite_query(query: str, mode: str = 'none') -> dict:
    """对 query 执行查询重写。

    参数：
        query: 用户原始问题
        mode: 'none' 不重写, 'rule' 规则重写

    返回：
        {
            'original_query': str,
            'rewritten_query': str,
            'rewrite_applied': bool,
            'rewrite_reason': str,
        }
    """
    result = {
        'original_query': query,
        'rewritten_query': query,
        'rewrite_applied': False,
        'rewrite_reason': '',
    }

    if mode == 'none':
        return result

    if mode != 'rule':
        logger.warning('不支持的 rewrite_mode: %s，跳过重写', mode)
        return result

    normalized_query = normalize_retrieval_query(query)
    if normalized_query != query:
        result['rewritten_query'] = normalized_query
        result['rewrite_applied'] = True
        result['rewrite_reason'] = NORMALIZATION_REASON
        return result

    query_lower = query.lower().strip()

    for pattern, replacement, reason in _RULES:
        if pattern.search(query_lower):
            result['rewritten_query'] = replacement
            result['rewrite_applied'] = True
            result['rewrite_reason'] = reason
            logger.info(
                'Query rewrite: "%s" → "%s" (reason: %s)',
                query, replacement, reason,
            )
            return result

    return result
