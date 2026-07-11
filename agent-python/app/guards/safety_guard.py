"""
safety_guard.py —— 轻量级输入安全边界控制

规则版安全守卫，在 RAG 检索 / Tool Calling 前判断用户问题是否安全。
不调用 LLM，不引入新依赖。
"""

# ── 风险类别与关键词 ────────────────────────────────────────
RISK_RULES = [
    {
        "category": "illegal_or_policy_violation",
        "label": "违法违规 / 伪造材料",
        "keywords": [
            "伪造", "造假", "假证明", "假病假条", "伪造病假证明",
            "伪造公章", "伪造签名", "假材料", "假病历",
        ],
    },
    {
        "category": "policy_bypass",
        "label": "绕过企业制度 / 规避审批",
        "keywords": [
            "绕过审批", "规避审批", "绕过考勤", "绕过公司", "规避考勤",
            "怎么不打卡", "逃避打卡", "骗过系统", "钻制度漏洞",
            "绕过制度", "绕过流程", "绕过审核", "绕过打卡",
            "绕过请假", "跳过审批", "跳过请假", "跳过流程",
            "逃避审批", "规避流程", "规避制度",
        ],
    },
    {
        "category": "cybersecurity_attack",
        "label": "网络安全攻击 / 黑客行为",
        "keywords": [
            "黑进", "入侵", "攻击", "木马", "病毒",
            "提权", "漏洞利用", "撞库", "爆破",
            "盗取账号", "破解密码", "绕过登录", "黑客",
        ],
    },
    {
        "category": "audit_tampering",
        "label": "删除审计 / 隐藏痕迹",
        "keywords": [
            "删除日志", "清除日志", "隐藏痕迹", "抹掉记录",
            "删除审计", "绕过审计", "删日志",
        ],
    },
    {
        "category": "unauthorized_access",
        "label": "越权访问 / 数据窃取",
        "keywords": [
            "越权访问", "拿到别人数据", "导出员工信息",
            "窃取数据", "偷看工资", "批量导出隐私",
            "查看别人", "偷看别人", "盗取数据",
        ],
    },
]

# ── 拒答回复 ──────────────────────────────────────────────
REJECT_MESSAGE = (
    "抱歉，我不能协助提供违法、违规、绕过企业制度或破坏系统安全的操作方法。"
)


def check_user_query_safety(query: str) -> dict:
    """检查用户问题是否安全。

    Args:
        query: 用户输入的问题文本

    Returns:
        dict:
            safe     — True 表示安全，可以继续处理
            category — 风险类别，safe 时为 "normal"
            reason   — 命中原因，safe 时为空
            message  — 拒答文案，safe 时为空
    """
    if not query or not query.strip():
        return {
            "safe": False,
            "category": "empty_query",
            "reason": "问题为空",
            "message": REJECT_MESSAGE,
        }

    query_lower = query.lower()

    for rule in RISK_RULES:
        for kw in rule["keywords"]:
            if kw.lower() in query_lower:
                return {
                    "safe": False,
                    "category": rule["category"],
                    "reason": f"命中高风险关键词：{kw}（{rule['label']}）",
                    "message": REJECT_MESSAGE,
                }

    return {
        "safe": True,
        "category": "normal",
        "reason": "",
        "message": "",
    }
