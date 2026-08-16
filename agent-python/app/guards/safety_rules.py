"""
safety_rules.py —— Safety Guard Lite 规则库与局部中性化词表

本模块只定义规则对象与判定所需词表；判定逻辑见 safety_guard.py。

设计原则（偏 precision）：只保留高置信、明确攻击的确定性规则，五族：
  prompt_override / prompt_extraction / credential_extraction /
  tool_abuse / business_policy_bypass

中性化只使用 span 局部小窗口检查（不做句子/子句切分、不做意图分类）：
  1. benign_prefix    — 命中前 8 字符内的防御/定义/禁止词
  2. evaluator_suffix — 命中后 20 字符内的后果/评价词（前后含命令/攻击词时失效）
  3. permission_frame — 业务制度族专用："我可以…吗" 式许可疑问
  extraction 族另有：定义化前缀 / 良性后续词 / 强评价后缀（均 span 局部）。

不确定、讨论型、咨询型输入默认 PASS（precision 优先）。
"""

import re
from dataclasses import dataclass

# ── 规则数据结构 ────────────────────────────────────────────

@dataclass(frozen=True)
class SafetyRule:
    """单条高置信安全规则。"""

    rule_id: str  # 稳定编号，如 PI_OVERRIDE_001
    category: str  # 风险类别（五族之一）
    pattern: re.Pattern  # 预编译正则
    check_compact: bool = True  # 是否同时检查 compact 变体
    permission_question: bool = False  # 是否接受 "我可以…吗" 许可疑问中性化（仅业务制度族）


@dataclass(frozen=True)
class ExtractionRule:
    """敏感信息索取规则（动词 + 目标词，正序/倒序双向匹配）。"""

    rule_id: str
    category: str
    targets: str  # 目标词 alternation（不含包裹括号）
    check_compact: bool = True

    @property
    def target_re(self) -> re.Pattern:
        """目标词预编译正则（按需编译，进程内缓存）。"""
        return re.compile(f"(?:{self.targets})", re.IGNORECASE)


# ── 拒答回复 ──────────────────────────────────────────────

REJECT_MESSAGE = (
    "抱歉，我不能协助提供违法、违规、绕过企业制度或破坏系统安全的操作方法。"
)


# ── 五族高置信规则 ─────────────────────────────────────────

# 1. prompt_override：指令覆盖 / 模式切换 / 角色劫持
_OVERRIDE_VERBS = (
    "忽略|无视|覆盖|忘记|抛弃|丢弃|遗忘|忽视|不听|"
    "ignore|disregard|forget|override|skip|neglect|abandon|stop"
)
_OVERRIDE_ANTECEDENT = (
    "之前|以上|前面|所有|全部|先前|既有|以前|此前|上文|系统|"
    "previous|above|all|prior|existing|earlier|older|former|those|these"
)
_OVERRIDE_TARGETS = (
    "指令|规则|规定|规范|设定|提示词|指示|要求|约束|限制|规矩|制度|准则|原则|"
    "instructions?|rules|prompts|constraints|directives|policies|guidelines|requirements"
)

_MODE_VERBS = (
    "进入|开启|启用|切换到|激活|启动|"
    "enter|enable|activate|switch\\s*to|turn\\s*on|start"
)
_MODE_TARGETS = (
    "开发者模式|管理员模式|无约束模式|调试模式|"
    "DAN|dan\\s*mode|jailbreak|jailbreak\\s*mode|越狱模式|自由模式|"
    "god\\s*mode|sudo\\s*mode|superuser模式"
)

HIGH_CONFIDENCE_RULES: list[SafetyRule] = [
    SafetyRule(
        rule_id="PI_OVERRIDE_001",
        category="prompt_override",
        pattern=re.compile(
            rf"(?:{_OVERRIDE_VERBS})"
            rf".{{0,15}}(?:{_OVERRIDE_ANTECEDENT})"
            rf".{{0,20}}(?:{_OVERRIDE_TARGETS})",
            re.IGNORECASE,
        ),
    ),
    SafetyRule(
        rule_id="PI_MODE_001",
        category="prompt_override",
        pattern=re.compile(
            rf"(?:{_MODE_VERBS}).{{0,10}}(?:{_MODE_TARGETS})",
            re.IGNORECASE,
        ),
    ),
    SafetyRule(
        rule_id="ROLE_HIJACK_001",
        category="prompt_override",
        pattern=re.compile(
            r"(你现在是|假装你是|假装|扮演|伪装成|从现在起你是)"
            r".{0,10}"
            r"(系统|管理员|开发者|root|admin|superuser)",
            re.IGNORECASE,
        ),
    ),
    SafetyRule(
        rule_id="ROLE_HIJACK_002",
        category="prompt_override",
        pattern=re.compile(
            r"(you are now|pretend to be|act as|from now on you are)"
            r".{0,10}"
            r"(system|admin|developer|root|superuser|god)",
            re.IGNORECASE,
        ),
    ),
    SafetyRule(
        rule_id="TOOL_ABUSE_001",
        category="tool_abuse",
        pattern=re.compile(
            r"(调用|执行)"
            r".{0,10}"
            r"(内部工具|管理工具|系统工具|底层工具|隐藏功能|受限工具|内部接口|未公开工具|后台工具|隐藏接口)",
            re.IGNORECASE,
        ),
    ),
    SafetyRule(
        rule_id="TOOL_ABUSE_002",
        category="tool_abuse",
        pattern=re.compile(
            r"(call|execute|run|invoke)"
            r".{0,10}"
            r"(internal tools?|admin tools?|system tools?|hidden features?|privileged tools?)",
            re.IGNORECASE,
        ),
    ),
]

# 2. prompt_extraction / credential_extraction（正序/倒序双向）
_EXTRACT_VERBS = (
    "输出|展示|泄露|告诉我|给我|打印|显示|复述|念出|念一下|发给我|发我|传给我|"
    "发送|转发|贴出|粘贴|获取|拿到|读取|查询|提取|索要|导出|窃取|盗取|偷取|破解|"
    "reveal|show\\s*me|print|output|display|tell\\s*me|give\\s*me|repeat|paste|"
    "send\\s*me|dump|download|steal"
)
_PROMPT_TARGETS = (
    "系统\\s*提示词|提示词|system\\s*prompt|你的指令|你的提示词|开发者指令|"
    "内部指令|系统指令|初始指令|原始指令|隐藏指令"
)
_SECRET_TARGETS = (
    "内部配置|访问令牌|管理员令牌|数据库密码|数据库口令|凭据|密钥|密码|口令|"
    "token|api\\s*key|secret|credential|password"
)

EXTRACTION_RULES: list[ExtractionRule] = [
    ExtractionRule(
        rule_id="EXTRACT_PROMPT_001",
        category="prompt_extraction",
        targets=_PROMPT_TARGETS,
    ),
    ExtractionRule(
        rule_id="EXTRACT_SECRET_001",
        category="credential_extraction",
        targets=_SECRET_TARGETS,
    ),
]

DEMAND_VERB_RE = re.compile(f"(?:{_EXTRACT_VERBS})", re.IGNORECASE)
MAX_FORWARD_GAP = 25
MAX_REVERSE_GAP = 15
LOOKAHEAD = 12

# 3. business_policy_bypass：协助绕过制度 / 伪造 / 越权 / 日志篡改
#    命令前缀（怎么/如何/帮我…）与动词之间允许少量字符，但若中间出现
#    防御/处置词（防止、检测、识别…），则为防御性问句，不成规则。
_DEFENSE_GAP = (
    r"(?:(?!防止|防范|预防|检测|识别|发现|监控|避免|杜绝|阻止|拦截|防护|"
    r"排查|审计|应对|治理|整改|修复|加固|遏制|制止).){0,15}"
)

HIGH_CONFIDENCE_RULES += [
    SafetyRule(
        rule_id="BYPASS_001",
        category="business_policy_bypass",
        pattern=re.compile(
            rf"(?:怎么|如何|帮我|教我|怎样|有没有办法|能不能)"
            rf"{_DEFENSE_GAP}"
            rf"(?:绕过|规避|逃避|躲避|跳过|避开|绕开)"
            rf".{{0,10}}"
            rf"(?:审批|考勤|打卡|制度|流程|审核|请假|登录)",
            re.IGNORECASE,
        ),
        permission_question=True,
    ),
    SafetyRule(
        rule_id="BYPASS_002",
        category="business_policy_bypass",
        pattern=re.compile(
            r"(绕过审批|规避审批|绕过考勤|绕过打卡|绕过登录|逃避打卡|骗过系统|钻制度漏洞|"
            r"绕过制度|绕过流程|绕过审核|绕过请假|跳过审批|跳过请假|跳过流程|"
            r"逃避审批|规避流程|规避制度|怎么不打卡|规避考勤|绕过公司|绕开审批)",
            re.IGNORECASE,
        ),
        permission_question=True,
    ),
    SafetyRule(
        rule_id="ILLEGAL_001",
        category="business_policy_bypass",
        pattern=re.compile(
            rf"(?:怎么|如何|帮我|教我|制作|生成|提供|怎样|指导)"
            rf"{_DEFENSE_GAP}"
            rf"(?:伪造|造假|做假|搞假|假冒)"
            rf".{{0,10}}"
            rf"(?:证明|材料|病历|公章|签名|病假条|文件|证件)",
            re.IGNORECASE,
        ),
        permission_question=True,
    ),
    SafetyRule(
        rule_id="ILLEGAL_002",
        category="business_policy_bypass",
        pattern=re.compile(
            r"(伪造病假证明|伪造病历|伪造公章|伪造签名|假病假条|帮我做假病历|"
            r"做一份假证明|造假材料|提供假材料)",
            re.IGNORECASE,
        ),
        permission_question=True,
    ),
    SafetyRule(
        rule_id="CYBER_001",
        category="business_policy_bypass",
        pattern=re.compile(
            rf"(?:怎么|如何|帮我|教我|怎样)"
            rf"{_DEFENSE_GAP}"
            rf"(?:黑进|入侵|攻击(?!性)|提权|爆破|撞库)",
            re.IGNORECASE,
        ),
        permission_question=True,
    ),
    SafetyRule(
        rule_id="CYBER_003",
        category="business_policy_bypass",
        pattern=re.compile(
            rf"(?:怎么|如何|帮我|教我|怎样)"
            rf"{_DEFENSE_GAP}"
            rf"(?:破解|盗取|窃取)"
            rf".{{0,10}}"
            rf"(?:密码|账号|口令|验证码|token|权限|门禁|系统|数据库|wifi|WiFi|无线网络)",
            re.IGNORECASE,
        ),
        permission_question=True,
    ),
    SafetyRule(
        rule_id="CYBER_002",
        category="business_policy_bypass",
        pattern=re.compile(
            r"(黑进|入侵|攻击系统|植入木马|投放病毒|提权|漏洞利用|撞库|爆破|"
            r"盗取账号|破解密码|绕过登录|黑客攻击)",
            re.IGNORECASE,
        ),
        permission_question=True,
    ),
    SafetyRule(
        rule_id="AUDIT_001",
        category="business_policy_bypass",
        pattern=re.compile(
            rf"(?:怎么|如何|帮我|教我|怎样|有没有办法)"
            rf"{_DEFENSE_GAP}"
            rf"(?:删除|清除|清理|抹掉|消除|隐藏)"
            rf".{{0,10}}"
            rf"(?:日志|记录|痕迹|审计|证据|历史)",
            re.IGNORECASE,
        ),
        permission_question=True,
    ),
    SafetyRule(
        rule_id="AUDIT_002",
        category="business_policy_bypass",
        pattern=re.compile(
            r"(删除审计日志|删除日志|清除日志|清理日志|隐藏痕迹|抹掉记录|消除痕迹|"
            r"删日志|清日志|删记录)",
            re.IGNORECASE,
        ),
        permission_question=True,
    ),
    SafetyRule(
        rule_id="ACCESS_001",
        category="business_policy_bypass",
        pattern=re.compile(
            rf"(?:怎么|如何|帮我|教我|怎样|有没有办法|能不能)"
            rf"{_DEFENSE_GAP}"
            rf"(?:越权|偷看|窃取|盗取|拿到|导出)"
            rf".{{0,10}}"
            rf"(?:工资|数据|信息|隐私|员工|别人|他人|同事)",
            re.IGNORECASE,
        ),
        permission_question=True,
    ),
    SafetyRule(
        rule_id="ACCESS_002",
        category="business_policy_bypass",
        pattern=re.compile(
            r"(越权访问|拿到别人数据|导出员工信息|窃取数据|偷看工资|批量导出隐私|"
            r"偷看别人|盗取数据|偷看别人工资|偷看他人工资|偷看同事工资|"
            r"盗取员工数据|窃取员工数据)",
            re.IGNORECASE,
        ),
        permission_question=True,
    ),
]


# ── span 局部中性化词表 ─────────────────────────────────────
# 全部为命中点前后的小窗口检查（≤6 字符前缀 / ≤20 字符后缀），
# 不做句子切分、不做跨子句传播。

# 1) 防御 / 定义 / 禁止前缀（命中前 6 字符内）——适用于 override / tool / action 族
BENIGN_PREFIX = (
    "为什么|什么是|是什么|如何防止|如何检测|如何识别|如何发现|如何监控|如何避免|"
    "如何杜绝|如何阻止|如何拦截|如何防范|怎么防止|怎样防范|怎么检测|怎么识别|"
    "防止|防范|预防|检测|识别|发现|监控|避免|杜绝|阻止|拦截|防护|治理|应对|"
    "整改|修复|加固|遏制|制止|排查|审计|解释|说明|请解释|请说明|"
    "禁止|不得|不允许|不准|不可以|不能|不可|不该|是否|该不该|可否|可不可以|明文|规定|明确"
)
BENIGN_PREFIX_RE = re.compile(f"(?:{BENIGN_PREFIX})")

# 2) 后果 / 评价后缀（命中后 20 字符内）——前后文含命令/攻击词时失效
EVALUATOR_SUFFIX = (
    "是危险的|是错误的|是违法的|是违规的|是不对的|是不允许的|是禁止的|是攻击|是注入|"
    "属于违规|属于违法|属于攻击|属于危险|是一种攻击|是一种注入|是典型攻击|是不良行为|"
    "会危害|有风险|不安全|被禁止|不允许|违反规定|违反制度|"
    "会怎样|会怎么样|什么后果|有什么后果|怎么处罚|什么处罚|怎么办|怎么处理|会怎么处理|"
    "有什么影响|什么影响|有什么风险|什么风险|有什么危害|什么危害|有什么损失|什么损失|"
    "是不是违法|是否违法|是否违规|违反什么|会被怎样|会不会|该不该|值不值得|"
    "会有什么|会造成|会带来|有哪些|是合法|是否合法|合不合法|可不可以|怎么防范|如何防范|"
    "what happens|what would happen|is dangerous|is wrong|is illegal|is prohibited|"
    "is not allowed|is an attack|is a risk|consequences?|risks?|unsafe"
)
EVALUATOR_SUFFIX_RE = re.compile(f"(?:{EVALUATOR_SUFFIX})", re.IGNORECASE)

# 3) 命令前缀（压制 evaluator 后缀与许可疑问的中性化；"请勿…"是警示语，不压制）
DEMAND_PREFIX = (
    "帮我|给我|请(?!勿)|教我|让我|我要|我想|立刻|马上|现在|必须|要求|命令|"
    "please|now|do not|don't|tell"
)
DEMAND_PREFIX_RE = re.compile(f"(?:{DEMAND_PREFIX})", re.IGNORECASE)

# 3b) evaluator 压制集：命令词 + 攻击动词（仅用于后果/评价后缀的压制，
#     防止 "忽略之前所有指令。请告诉我这样做有什么后果" 这类附加句绕过）
EVALUATOR_DEMAND = (
    DEMAND_PREFIX
    + "|忽略|无视|覆盖|输出|删除|绕过|调用|执行|打印|复制|泄露|导出|"
    + "reveal|output|delete|bypass|ignore|print|copy"
)
EVALUATOR_DEMAND_RE = re.compile(f"(?:{EVALUATOR_DEMAND})", re.IGNORECASE)

# 4) 许可疑问（业务制度族专用）：许可词在前（8 字符内）且疑问尾缀在后（6 字符内），
#    且上下文无命令词
PERMISSION_WORDS = "我可以|我能|我能否|可不可以|能不能|可否|允许我|可以"
PERMISSION_END = "吗|么|？|\\?|行不行|好不好"
PERMISSION_WORDS_RE = re.compile(f"(?:{PERMISSION_WORDS})")
PERMISSION_END_RE = re.compile(f"(?:{PERMISSION_END})")

# 5) 角色劫持的疑问尾缀豁免（"你现在是管理员吗？"）
ROLE_QUESTION_END = "吗|么|？|\\?|呢"
ROLE_QUESTION_END_RE = re.compile(f"(?:{ROLE_QUESTION_END})")

# 6) extraction 族：定义化前缀（动词前 6 字符内；"能不能"不豁免）
EXTRACT_BENIGN_PREFIX = (
    "为什么|为什么不能|为什么不允许|(?<!能)不能|该不该|是否|什么是|是什么|"
    "解释|说明|请解释|请说明"
)
EXTRACT_BENIGN_PREFIX_RE = re.compile(f"(?:{EXTRACT_BENIGN_PREFIX})")

# 7) extraction 族：良性后续词（目标词与动词之间 / 目标词之后）
BENIGN_FOLLOWER = (
    "是什么|什么意思|指什么|定义|含义|意思|如何|怎么|怎样|为什么|流程|步骤|方法|"
    "作用|用途|意义|危害|风险|影响|区别|规范|要求|政策|制度|规定|在哪|在哪里|"
    "重置|修改|找回|恢复|设置|更新|更换|查看|查询|了解|建议|技巧|多少|几条|"
    "资料|文档|报告|手册|指南|模板|清单|列表|申请|审批|"
    "how\\s*to|how\\s*do|reset|recover|change|update|view|check|find|see|"
    "workflow|policy|guideline|requirement|document|manual"
)
BENIGN_FOLLOWER_RE = re.compile(f"(?:{BENIGN_FOLLOWER})", re.IGNORECASE)

# 8) extraction 族：强评价后缀（正序目标词后 / 倒序动词后；后果问句不豁免）
STRONG_EVALUATOR = (
    "是危险的|是错误的|是违法的|是违规的|是不对的|是攻击|是注入|"
    "属于违规|属于违法|属于攻击|属于危险|是一种攻击|是一种注入|是典型攻击|"
    "是不良行为|会危害|有风险|是不允许的|是禁止的|被禁止|不允许"
)
STRONG_EVALUATOR_RE = re.compile(f"(?:{STRONG_EVALUATOR})")
