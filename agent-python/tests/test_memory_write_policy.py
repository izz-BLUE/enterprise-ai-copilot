"""test_memory_write_policy.py —— MemoryWritePolicy 行为与安全边界

覆盖：

正常：
  1. NONE proposal → None（不产生 command）
  2. UPSERT proposal（task_state 含业务字段）→ 完整 command
  3. COMPLETE proposal（默认填齐 status=COMPLETED）
  4. ABANDON proposal（默认填齐 status=ABANDONED）

边界 / 安全：
  5. UPSERT 缺 status 抛错
  6. UPSERT 缺 task_state 抛错
  7. COMPLETE 携带 status=ACTIVE 抛错（语义不一致）
  8. ABANDON 携带 status=COMPLETED 抛错
  9. task_state 内顶层 trusted 字段被剥离（employee_id / user_id 等）
 10. task_state 嵌套 dict 内 trusted 字段递归剥离
 11. task_state list 内 dict 同样递归剥离
 12. summary 含 Bearer / JWT 关键字被脱敏
 13. summary 含 password= 关键字被脱敏
 14. summary 大小写不敏感命中
 15. task_state 内字符串值含敏感字段也被脱敏
 16. task_state 序列化字节超限抛错
 17. task_state 序列化字节恰好不超过上限允许通过

序列化 / 契约：
 18. Command 的 action 不含 NONE（None 已在 policy 层处理）
 19. Command extra='forbid'（额外字段拒绝）
 20. Policy 默认 task_type=GENERIC 当 proposal 未提供
 21. Policy 默认 task_state={} 当 proposal 未提供且非 UPSERT
"""

import pytest
from pydantic import ValidationError

from app.memory.memory_write_policy import (
    MAX_TASK_STATE_JSON_BYTES,
    MemoryTerminalActionNotAllowed,
    MemoryWriteCommand,
    MemoryWritePolicy,
)
from app.schemas.memory_schema import MemoryProposal


@pytest.fixture
def policy() -> MemoryWritePolicy:
    return MemoryWritePolicy()


# ---------- 正常路径 ----------

class TestHappyPath:
    def test_none_proposal_returns_none(self, policy):
        proposal = MemoryProposal(action='NONE')
        assert policy.evaluate(proposal) is None

    def test_upsert_proposal_full(self, policy):
        proposal = MemoryProposal(
            action='UPSERT',
            task_type='LEAVE_REQUEST',
            status='ACTIVE',
            task_state={'waiting_for': 'date'},
            summary='等待用户补充请假日期',
        )
        cmd = policy.evaluate(proposal)
        assert isinstance(cmd, MemoryWriteCommand)
        assert cmd.action == 'UPSERT'
        assert cmd.task_type == 'LEAVE_REQUEST'
        assert cmd.status == 'ACTIVE'
        assert cmd.task_state == {'waiting_for': 'date'}
        assert cmd.summary == '等待用户补充请假日期'

    def test_complete_proposal_default_status(self, policy):
        """COMPLETE 不带 status 时 policy 默认填 COMPLETED。"""
        proposal = MemoryProposal(action='COMPLETE')
        cmd = policy.evaluate(proposal)
        assert cmd is not None
        assert cmd.action == 'COMPLETE'
        assert cmd.status == 'COMPLETED'
        # 其他字段填默认值
        assert cmd.task_type == 'GENERIC'
        assert cmd.task_state == {}
        assert cmd.summary == ''

    def test_complete_proposal_explicit_status_matches(self, policy):
        proposal = MemoryProposal(action='COMPLETE', status='COMPLETED', summary='done')
        cmd = policy.evaluate(proposal)
        assert cmd.action == 'COMPLETE'
        assert cmd.status == 'COMPLETED'
        assert cmd.summary == 'done'

    def test_abandon_proposal_default_status(self, policy):
        proposal = MemoryProposal(action='ABANDON')
        cmd = policy.evaluate(proposal)
        assert cmd.action == 'ABANDON'
        assert cmd.status == 'ABANDONED'

    def test_default_task_type_is_generic(self, policy):
        """UPSERT 但未提供 task_type 时使用默认 GENERIC。"""
        proposal = MemoryProposal(
            action='UPSERT',
            status='ACTIVE',
            task_state={'x': 1},
        )
        cmd = policy.evaluate(proposal)
        assert cmd.task_type == 'GENERIC'


# ---------- 业务动作链路终态拦截（allow_terminal_actions=False）----------

class TestTerminalActionGuard:
    """业务动作链路（Pipeline 传 allow_terminal_actions=False）下，Python 侧
    COMPLETE / ABANDON 终态命令被程序级拒绝 —— 终态只能由 Java PendingAction
    生命周期收口。UPSERT + ACTIVE 上下文更新不受影响。"""

    def test_business_action_complete_rejected(self, policy):
        proposal = MemoryProposal(action='COMPLETE', status='COMPLETED')
        with pytest.raises(MemoryTerminalActionNotAllowed, match='终态'):
            policy.evaluate(proposal, allow_terminal_actions=False)

    def test_business_action_abandon_rejected(self, policy):
        proposal = MemoryProposal(action='ABANDON', status='ABANDONED')
        with pytest.raises(MemoryTerminalActionNotAllowed, match='终态'):
            policy.evaluate(proposal, allow_terminal_actions=False)

    def test_business_action_upsert_allowed(self, policy):
        proposal = MemoryProposal(
            action='UPSERT',
            task_type='LEAVE_REQUEST',
            status='ACTIVE',
            task_state={'waiting_for': 'date'},
            summary='等待补充日期',
        )
        cmd = policy.evaluate(proposal, allow_terminal_actions=False)
        assert cmd is not None
        assert cmd.action == 'UPSERT'
        assert cmd.status == 'ACTIVE'

    def test_default_allow_terminal_actions_true_keeps_complete(self, policy):
        """默认（非业务链路调用方）仍允许 COMPLETE / ABANDON，兼容既有行为。"""
        cmd = policy.evaluate(MemoryProposal(action='COMPLETE', status='COMPLETED'))
        assert cmd is not None
        assert cmd.action == 'COMPLETE'
        cmd = policy.evaluate(MemoryProposal(action='ABANDON', status='ABANDONED'))
        assert cmd is not None
        assert cmd.action == 'ABANDON'


# ---------- 边界 / 拒绝 ----------

class TestValidation:
    def test_upsert_without_status_rejected(self, policy):
        proposal = MemoryProposal(
            action='UPSERT',
            task_type='GENERIC',
            task_state={'x': 1},
        )
        with pytest.raises(ValueError, match='UPSERT 必须显式提供 status'):
            policy.evaluate(proposal)

    def test_upsert_without_task_state_rejected(self, policy):
        proposal = MemoryProposal(
            action='UPSERT',
            task_type='GENERIC',
            status='ACTIVE',
        )
        with pytest.raises(ValueError, match='UPSERT 必须显式提供 task_state'):
            policy.evaluate(proposal)

    def test_complete_with_wrong_status_rejected(self, policy):
        proposal = MemoryProposal(action='COMPLETE', status='ACTIVE')
        with pytest.raises(ValueError, match='status=ACTIVE 不匹配'):
            policy.evaluate(proposal)

    def test_abandon_with_wrong_status_rejected(self, policy):
        proposal = MemoryProposal(action='ABANDON', status='COMPLETED')
        with pytest.raises(ValueError, match='status=COMPLETED 不匹配'):
            policy.evaluate(proposal)

    def test_task_state_oversize_rejected(self, policy):
        proposal = MemoryProposal(
            action='UPSERT',
            task_type='GENERIC',
            status='ACTIVE',
            task_state={'big': 'x' * (MAX_TASK_STATE_JSON_BYTES + 100)},
        )
        with pytest.raises(ValueError, match='task_state 序列化字节数超过'):
            policy.evaluate(proposal)

    def test_task_state_at_boundary_accepted(self, policy):
        """构造恰好不超限的 payload；保留足够余量避免测试因边界抖动失败。"""
        # 用 'a' 字符填充直到接近上限；每个 key/value 对约 5 字节包裹
        filler = 'a' * (MAX_TASK_STATE_JSON_BYTES - 100)
        proposal = MemoryProposal(
            action='UPSERT',
            task_type='GENERIC',
            status='ACTIVE',
            task_state={'filler': filler},
        )
        cmd = policy.evaluate(proposal)
        assert cmd is not None


# ---------- trusted 字段剥离 ----------

class TestTrustedFieldStripping:
    def test_top_level_trusted_key_stripped(self, policy):
        proposal = MemoryProposal(
            action='UPSERT',
            task_type='GENERIC',
            status='ACTIVE',
            task_state={
                'employee_id': 'E10001',
                'user_id': 'U1',
                'conversation_id': 'c1',
                'token': 'jwt-xxx',
                'waiting_for': 'date',
            },
        )
        cmd = policy.evaluate(proposal)
        assert cmd is not None
        assert 'employee_id' not in cmd.task_state
        assert 'user_id' not in cmd.task_state
        assert 'conversation_id' not in cmd.task_state
        assert 'token' not in cmd.task_state
        assert cmd.task_state == {'waiting_for': 'date'}

    def test_nested_dict_trusted_key_stripped_recursively(self, policy):
        proposal = MemoryProposal(
            action='UPSERT',
            task_type='GENERIC',
            status='ACTIVE',
            task_state={
                'outer': {
                    'employee_id': 'E10001',
                    'inner': {'nonce': 'n1', 'kept': 1},
                },
                'role': 'ADMIN',  # 顶层 forbidden
            },
        )
        cmd = policy.evaluate(proposal)
        assert cmd.task_state == {'outer': {'inner': {'kept': 1}}}

    def test_camel_case_runtime_keys_stripped_recursively(self, policy):
        proposal = MemoryProposal(
            action='UPSERT',
            task_type='GENERIC',
            status='ACTIVE',
            task_state={
                'userId': 'U1',
                'businessDate': '2026-08-20',
                'nested': {'traceId': 'trace-1', 'kept': True},
            },
        )
        cmd = policy.evaluate(proposal)
        assert cmd.task_state == {'nested': {'kept': True}}

    def test_list_of_dict_trusted_key_stripped_recursively(self, policy):
        proposal = MemoryProposal(
            action='UPSERT',
            task_type='GENERIC',
            status='ACTIVE',
            task_state={
                'items': [
                    {'employee_id': 'E1', 'value': 'a'},
                    {'permission': 'x', 'value': 'b'},
                ],
            },
        )
        cmd = policy.evaluate(proposal)
        assert cmd.task_state == {
            'items': [{'value': 'a'}, {'value': 'b'}],
        }


# ---------- 字符串脱敏 ----------

class TestSummaryRedaction:
    @pytest.mark.parametrize('text', [
        'Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig',
        '请带上 jwt token 提交',
        'password=hunter2',
        'password: hunter2',
        'token=abc.def',
        'token: abc',
        'nonce=xyz',
        'idempotency-key=key-1',
        '凭证：bearer abc',
        'JWT abc',
    ])
    def test_sensitive_marker_redacted(self, policy, text):
        proposal = MemoryProposal(
            action='UPSERT',
            task_type='GENERIC',
            status='ACTIVE',
            task_state={'x': 1},
            summary=text,
        )
        cmd = policy.evaluate(proposal)
        assert cmd.summary == '[REDACTED]'

    def test_safe_summary_passes_through(self, policy):
        proposal = MemoryProposal(
            action='UPSERT',
            task_type='GENERIC',
            status='ACTIVE',
            task_state={'x': 1},
            summary='等待用户补充请假日期',
        )
        cmd = policy.evaluate(proposal)
        assert cmd.summary == '等待用户补充请假日期'

    def test_task_state_string_value_redacted(self, policy):
        proposal = MemoryProposal(
            action='UPSERT',
            task_type='GENERIC',
            status='ACTIVE',
            task_state={'note': 'this is a bearer token leak'},
        )
        cmd = policy.evaluate(proposal)
        assert cmd.task_state == {'note': '[REDACTED]'}

    def test_task_state_string_value_at_max_size_redacted(self, policy):
        """16 KiB 边界值同样完整扫描：不允许长度绕过（历史缺陷反例）。"""
        padded = 'A' * (16 * 1024 - 32) + ' Bearer secret-token'
        proposal = MemoryProposal(
            action='UPSERT',
            task_type='GENERIC',
            status='ACTIVE',
            task_state={'note': padded},
        )
        cmd = policy.evaluate(proposal)
        assert cmd.task_state == {'note': '[REDACTED]'}

    def test_task_state_over_4096_string_value_redacted(self, policy):
        """超过 4096 字符的字符串（原 _MAX_SCAN_VALUE_LEN 短路）必须仍被扫描。"""
        padded = 'A' * 5000 + ' Bearer secret-token'
        proposal = MemoryProposal(
            action='UPSERT',
            task_type='GENERIC',
            status='ACTIVE',
            task_state={'note': padded},
        )
        cmd = policy.evaluate(proposal)
        assert cmd.task_state == {'note': '[REDACTED]'}
        assert 'Bearer' not in cmd.task_state['note']

    def test_task_state_over_4096_safe_string_passes_through(self, policy):
        """超长但无敏感 marker 的字符串原样通过（不误杀）。"""
        padded = 'A' * 5000 + ' 普通内容'
        proposal = MemoryProposal(
            action='UPSERT',
            task_type='GENERIC',
            status='ACTIVE',
            task_state={'note': padded},
        )
        cmd = policy.evaluate(proposal)
        assert cmd.task_state['note'] == padded


# ---------- Command 输出契约 ----------

class TestCommandContract:
    def test_command_extra_forbid(self):
        """MemoryWriteCommand 必须 extra='forbid'。"""
        with pytest.raises(ValidationError):
            MemoryWriteCommand(
                action='UPSERT',
                task_type='GENERIC',
                status='ACTIVE',
                task_state={},
                forbidden_field='x',
            )

    def test_command_action_does_not_include_none(self, policy):
        """NONE 在 policy 层已返回 None，Command action 不含 NONE。"""
        import pytest as _pytest
        # MemoryWriteCommand.model_config 限定 Literal；尝试填 'NONE' 必须失败
        with _pytest.raises(ValidationError):
            MemoryWriteCommand(
                action='NONE',  # type: ignore[arg-type]
                task_type='GENERIC',
                status='ACTIVE',
                task_state={},
            )

    def test_command_roundtrip_json(self, policy):
        """MemoryWriteCommand 可 JSON 序列化往返，便于跨服务传递。"""
        proposal = MemoryProposal(
            action='UPSERT',
            task_type='LEAVE_REQUEST',
            status='ACTIVE',
            task_state={'waiting_for': 'date'},
            summary='等待用户补充请假日期',
        )
        cmd = policy.evaluate(proposal)
        json_text = cmd.model_dump_json()
        restored = MemoryWriteCommand.model_validate_json(json_text)
        assert restored == cmd
