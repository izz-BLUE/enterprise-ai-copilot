"""
Query Rewriter — 规则版查询重写。

在检索前对用户口语化问题做轻量改写，提升检索召回率。
仅改写检索用 query，不影响最终 prompt 中的 original_query。

当前只实现 rule 模式（规则匹配），不调用 LLM。
"""

import logging
import re

logger = logging.getLogger('agent')

# ── 规则定义 ──────────────────────────────────────────────────────
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

# ── 年假 ──
_add(
    r'(?:年假|年休).{0,4}(?:怎么请|怎么休|怎么申请|几天|多少天|多少|咋请|咋休)',
    '员工如何申请年假以及年假天数规定',
    '年假口语问法 → 标准表述',
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


def rewrite_query(query: str, mode: str = 'none') -> dict:
    """对 query 执行查询重写。

    Args:
        query: 用户原始问题
        mode: 'none' 不重写, 'rule' 规则重写

    Returns:
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
