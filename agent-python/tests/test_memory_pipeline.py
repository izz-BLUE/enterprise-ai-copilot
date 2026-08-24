"""test_memory_pipeline.py —— Memory Pipeline Orchestrator 测试

覆盖（Phase 3C-Fix Error Boundary）：

Pipeline 不触发路径：
  1. 完全空执行 → triggered=False；proposal=None；command=None
  2. tool_history 全 blocked → triggered=False
  3. existing_memory 空 → triggered=False

Pipeline 触发 + Extractor 显式可预期失败（fail-safe noop）：
  4. 未注入 llm_callable → NotImplementedError 降级（triggered=True, proposal=None）
  5. 注入 llm_callable 但返回 invalid JSON → MemoryExtractionParseError 降级
  6. 注入 llm_callable 但返回未知 action → parse_proposal 失败降级

Pipeline 触发 + 完整执行：
  7. action_proposal 触发 → Extractor NONE → command=None
  8. action_proposal 触发 → Extractor UPSERT → WritePolicy 返回 command
  9. action_proposal 触发 → Extractor COMPLETE → WritePolicy 返回 command
 10. action_proposal 触发 → Extractor ABANDON → WritePolicy 返回 command

Error Boundary（Phase 3C-Fix）：
 11. trigger_policy 抛 RuntimeError → MemoryPipelineError 上抛（含触发链）
 12. write_policy 抛 ValueError → MemoryPipelineError 上抛
 13. extractor 抛 RuntimeError → MemoryPipelineError 上抛
 14. 非 dict 入参 → MemoryPipelineError
 15. MemoryPipelineResult 含 error 字段；error=None 表示成功路径
 16. error 是 MemoryPipelineError 类型
 17. MemoryPipelineResult extra='forbid' 仍然成立（含 error 字段）

依赖注入：
 18. 自定义 trigger_policy → 替换生效
 19. 自定义 extractor → 替换生效
 20. 自定义 write_policy → 替换生效
 21. 自定义 llm_callable → 注入生效

契约 / 行为：
 22. trigger_reason 透传 trigger_decision.reason
 23. Pipeline 多次 process 同一输入幂等
 24. triggered=False 时 command/proposal 必为 None
 25. 默认组件类型为真实组件
"""

import json
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from app.memory.memory_extractor import (
    MemoryExtractor,
)
from app.memory.memory_pipeline import (
    MemoryPipeline,
    MemoryPipelineError,
    MemoryPipelineResult,
)
from app.memory.memory_trigger_policy import MemoryTriggerPolicy
from app.memory.memory_write_policy import MemoryWritePolicy
from app.schemas.memory_schema import MemoryProposal

# ---------- Pipeline 不触发路径 ----------

class TestNoTrigger:
    def test_empty_execution_no_trigger(self):
        pipeline = MemoryPipeline()
        result = pipeline.process({'question': 'hi', 'answer': '...'})
        assert result.triggered is False
        assert result.proposal is None
        assert result.command is None
        assert result.error is None  # 成功路径，error 必为 None
        assert result.trigger_reason == 'no_trigger_signal'

    def test_tool_history_all_blocked_no_trigger(self):
        pipeline = MemoryPipeline()
        result = pipeline.process({
            'tool_history': [
                {'tool_name': 'rag_answer_tool', 'status': 'blocked',
                 'arguments': {}, 'observation': ''},
            ],
        })
        assert result.triggered is False
        assert result.proposal is None
        assert result.command is None
        assert result.error is None

    def test_existing_memory_empty_no_trigger(self):
        pipeline = MemoryPipeline()
        result = pipeline.process({'memory_context': {}})
        assert result.triggered is False
        assert result.error is None


# ---------- Pipeline 触发 + Extractor 显式可预期失败（fail-safe noop）----------

class TestTriggeredButExtractorExpectedFailure:
    def test_no_llm_callable_degrades_to_noop(self):
        """未注入 llm_callable → NotImplementedError → triggered=True, proposal=None（不抛错）。"""
        pipeline = MemoryPipeline()  # llm_callable=None
        result = pipeline.process({
            'action_proposal': {'action_type': 'ANNUAL_LEAVE_REQUEST'},
        })
        assert result.triggered is True
        assert result.proposal is None
        assert result.command is None
        assert result.error is None  # NotImplementedError 是显式预期失败
        assert result.trigger_reason == 'action_proposal_present'

    def test_invalid_json_from_llm_degrades_to_parse_error(self):
        """Extractor 抛 MemoryExtractionParseError → fail-safe noop。"""
        def fake_llm(system, user):
            return '{invalid json'

        pipeline = MemoryPipeline(llm_callable=fake_llm)
        result = pipeline.process({
            'action_proposal': {'action_type': 'ANNUAL_LEAVE_REQUEST'},
        })
        assert result.triggered is True
        assert result.proposal is None
        assert result.command is None
        assert result.error is None  # parse 失败是合法预期
        assert result.trigger_reason == 'action_proposal_present'

    def test_unknown_action_in_llm_output_degrades(self):
        """未知 action 应被 parse_proposal 拒绝（fail-loud）；pipeline 降级。"""
        def fake_llm(system, user):
            return json.dumps({'action': 'UNKNOWN_ACTION'})

        pipeline = MemoryPipeline(llm_callable=fake_llm)
        result = pipeline.process({
            'action_proposal': {'action_type': 'ANNUAL_LEAVE_REQUEST'},
        })
        assert result.triggered is True
        assert result.proposal is None
        assert result.command is None
        assert result.error is None


# ---------- Pipeline 触发 + 完整执行 ----------

class TestTriggeredFullPath:
    def test_extractor_none_yields_no_command(self):
        def fake_llm(system, user):
            return json.dumps({'action': 'NONE'})

        pipeline = MemoryPipeline(llm_callable=fake_llm)
        result = pipeline.process({
            'tool_history': [
                {'tool_name': 'leave_proposal_tool', 'status': 'success',
                 'arguments': {}, 'observation': 'ok'},
            ],
        })
        assert result.triggered is True
        assert result.proposal is not None
        assert result.proposal.action == 'NONE'
        assert result.command is None
        assert result.error is None

    def test_extractor_upsert_yields_command(self):
        def fake_llm(system, user):
            return json.dumps({
                'action': 'UPSERT',
                'task_type': 'LEAVE_REQUEST',
                'status': 'ACTIVE',
                'task_state': {'waiting_for': 'date'},
                'summary': '等待用户补充请假日期',
            }, ensure_ascii=False)

        pipeline = MemoryPipeline(llm_callable=fake_llm)
        result = pipeline.process({
            'action_proposal': {'action_type': 'ANNUAL_LEAVE_REQUEST'},
        })
        assert result.triggered is True
        assert result.proposal is not None
        assert result.proposal.action == 'UPSERT'
        assert result.command is not None
        assert result.command.action == 'UPSERT'
        assert result.command.task_state == {'waiting_for': 'date'}
        assert result.command.summary == '等待用户补充请假日期'
        assert result.error is None

    def test_complete_is_blocked(self):
        """LLM 输出 COMPLETE 时终态命令被程序级拦截。"""
        def fake_llm(system, user):
            return json.dumps({
                'action': 'COMPLETE',
                'status': 'COMPLETED',
                'summary': 'done',
            }, ensure_ascii=False)

        pipeline = MemoryPipeline(llm_callable=fake_llm)
        result = pipeline.process({
            'action_proposal': {'action_type': 'ANNUAL_LEAVE_REQUEST'},
        })
        assert result.proposal.action == 'COMPLETE'
        assert result.command is None
        assert result.error is None

    def test_abandon_is_blocked(self):
        """LLM 输出 ABANDON 时终态命令被程序级拦截。"""
        def fake_llm(system, user):
            return json.dumps({
                'action': 'ABANDON',
                'status': 'ABANDONED',
                'summary': 'user cancelled',
            }, ensure_ascii=False)

        pipeline = MemoryPipeline(llm_callable=fake_llm)
        result = pipeline.process({
            'action_proposal': {'action_type': 'ANNUAL_LEAVE_REQUEST'},
        })
        assert result.proposal.action == 'ABANDON'
        assert result.command is None
        assert result.error is None

    def test_business_action_upsert_still_yields_command(self):
        """业务动作链路 + UPSERT + ACTIVE：上下文更新仍然正常通过。"""
        def fake_llm(system, user):
            return json.dumps({
                'action': 'UPSERT',
                'task_type': 'LEAVE_REQUEST',
                'status': 'ACTIVE',
                'task_state': {'waiting_for': 'date'},
                'summary': '等待用户补充请假日期',
            }, ensure_ascii=False)

        pipeline = MemoryPipeline(llm_callable=fake_llm)
        result = pipeline.process({
            'action_proposal': {'action_type': 'ANNUAL_LEAVE_REQUEST'},
        })
        assert result.command is not None
        assert result.command.action == 'UPSERT'
        assert result.command.status == 'ACTIVE'
        assert result.error is None

    def test_leave_proposal_tool_success_terminal_is_blocked(self):
        """Clarification 场景的终态命令同样被拦截。"""
        def fake_llm(system, user):
            return json.dumps({
                'action': 'ABANDON',
                'status': 'ABANDONED',
                'summary': 'cancelled',
            }, ensure_ascii=False)

        pipeline = MemoryPipeline(llm_callable=fake_llm)
        result = pipeline.process({
            'tool_history': [
                {'tool_name': 'leave_proposal_tool', 'status': 'success',
                 'arguments': {}, 'observation': 'ok'},
            ],
        })
        assert result.proposal.action == 'ABANDON'
        assert result.command is None
        assert result.error is None

    def test_plain_flow_complete_is_also_blocked(self):
        """仅 existing_memory 触发时，Python 也不能生成终态命令。"""
        def fake_llm(system, user):
            return json.dumps({
                'action': 'COMPLETE',
                'status': 'COMPLETED',
                'summary': 'done',
            }, ensure_ascii=False)

        pipeline = MemoryPipeline(llm_callable=fake_llm)
        result = pipeline.process({
            'memory_context': {'task_type': 'GENERIC', 'status': 'ACTIVE'},
        })
        assert result.triggered is True
        assert result.proposal.action == 'COMPLETE'
        assert result.command is None
        assert result.error is None

    def test_upsert_with_terminal_status_is_blocked(self):
        """反例：UPSERT 不能伪装携带 COMPLETED 状态绕过终态守卫。"""
        def fake_llm(system, user):
            return json.dumps({
                'action': 'UPSERT',
                'task_type': 'GENERIC',
                'status': 'COMPLETED',
                'task_state': {'phase': 'done'},
                'summary': 'done',
            })

        pipeline = MemoryPipeline(llm_callable=fake_llm)
        result = pipeline.process({
            'memory_context': {'task_type': 'GENERIC', 'status': 'ACTIVE'},
        })
        assert result.proposal.action == 'UPSERT'
        assert result.proposal.status == 'COMPLETED'
        assert result.command is None
        assert result.error is None

    # ---------- Error Boundary（Phase 3C-Fix：不再吞所有异常）----------

class TestErrorBoundary:
    """Pipeline 不再吞所有异常；只 swallow 子组件显式声明的可预期失败，
    其他异常一律包装为 MemoryPipelineError 上抛。
    """

    def test_trigger_policy_runtime_error_raises_memory_pipeline_error(self):
        fake_trigger = MagicMock(spec=MemoryTriggerPolicy)
        fake_trigger.evaluate.side_effect = RuntimeError('boom')
        pipeline = MemoryPipeline(trigger_policy=fake_trigger)
        with pytest.raises(MemoryPipelineError) as exc_info:
            pipeline.process({'action_proposal': {'action_type': 'X'}})
        # 必须包装原始异常（__cause__ 链）
        assert isinstance(exc_info.value.__cause__, RuntimeError)
        assert 'boom' in str(exc_info.value.__cause__)

    def test_trigger_policy_value_error_raises_memory_pipeline_error(self):
        fake_trigger = MagicMock(spec=MemoryTriggerPolicy)
        fake_trigger.evaluate.side_effect = ValueError('bad trigger')
        pipeline = MemoryPipeline(trigger_policy=fake_trigger)
        with pytest.raises(MemoryPipelineError):
            pipeline.process({'action_proposal': {'action_type': 'X'}})

    def test_write_policy_value_error_raises_memory_pipeline_error(self):
        """write_policy 抛 ValueError 视为 Pipeline 调度失败（不是 fail-safe noop）。"""
        def fake_llm(system, user):
            return json.dumps({
                'action': 'UPSERT',
                'task_type': 'GENERIC',
                'status': 'ACTIVE',
                'task_state': {'x': 1},
            })
        fake_wp = MagicMock(spec=MemoryWritePolicy)
        fake_wp.evaluate.side_effect = ValueError('bad state')
        pipeline = MemoryPipeline(llm_callable=fake_llm, write_policy=fake_wp)
        with pytest.raises(MemoryPipelineError) as exc_info:
            pipeline.process({'action_proposal': {'action_type': 'X'}})
        assert isinstance(exc_info.value.__cause__, ValueError)

    def test_extractor_runtime_error_raises_memory_pipeline_error(self):
        """Extractor 抛非 MemoryExtractionParseError 异常时视为 Pipeline 调度失败。"""
        fake_extractor = MagicMock(spec=MemoryExtractor)
        fake_extractor.extract.side_effect = RuntimeError('LLM 503')
        pipeline = MemoryPipeline(extractor=fake_extractor)
        with pytest.raises(MemoryPipelineError) as exc_info:
            pipeline.process({'action_proposal': {'action_type': 'X'}})
        assert isinstance(exc_info.value.__cause__, RuntimeError)
        assert 'LLM 503' in str(exc_info.value.__cause__)

    def test_extractor_attribute_error_raises_memory_pipeline_error(self):
        """Extractor 抛 AttributeError（典型 bug 信号）也上抛。"""
        fake_extractor = MagicMock(spec=MemoryExtractor)
        fake_extractor.extract.side_effect = AttributeError("'NoneType' has no attribute 'x'")
        pipeline = MemoryPipeline(extractor=fake_extractor)
        with pytest.raises(MemoryPipelineError):
            pipeline.process({'action_proposal': {'action_type': 'X'}})

    def test_non_dict_input_raises_memory_pipeline_error(self):
        """Pipeline 自己的契约错误也通过 MemoryPipelineError 抛出。"""
        pipeline = MemoryPipeline()
        with pytest.raises(MemoryPipelineError) as exc_info:
            pipeline.process('not a dict')
        assert 'dict' in str(exc_info.value)
        with pytest.raises(MemoryPipelineError):
            pipeline.process(None)

    def test_memory_pipeline_error_is_runtime_error(self):
        """MemoryPipelineError 必须可作为 RuntimeError 捕获（向后兼容）。"""
        err = MemoryPipelineError('test')
        assert isinstance(err, RuntimeError)
        with pytest.raises(RuntimeError):
            raise err

    def test_memory_pipeline_error_can_chain_cause(self):
        """MemoryPipelineError 必须保留 __cause__ 链路（Python raise from）。"""
        original = RuntimeError('original')
        try:
            try:
                raise original
            except RuntimeError as exc:
                raise MemoryPipelineError('wrapped') from exc
        except MemoryPipelineError as wrapped:
            assert wrapped.__cause__ is original


# ---------- MemoryPipelineResult 契约 ----------

class TestResultContract:
    def test_result_has_error_field(self):
        """MemoryPipelineResult 含 error 字段；默认 None。"""
        result = MemoryPipelineResult(triggered=False)
        assert result.error is None

    def test_result_error_carries_memory_pipeline_error(self):
        err = MemoryPipelineError('boom')
        result = MemoryPipelineResult(triggered=True, error=err)
        assert result.error is err

    def test_result_extra_forbid_includes_error_field(self):
        """error 字段存在但 extra 仍禁止。"""
        err = MemoryPipelineError('boom')
        MemoryPipelineResult(triggered=True, error=err)
        with pytest.raises(ValidationError):
            MemoryPipelineResult(
                triggered=True,
                error=err,
                forged_field='x',
            )


# ---------- 依赖注入 ----------

class TestDependencyInjection:
    def test_custom_trigger_policy(self):
        fake_trigger = MagicMock(spec=MemoryTriggerPolicy)
        from app.memory.memory_trigger_policy import MemoryTriggerDecision
        fake_trigger.evaluate.return_value = MemoryTriggerDecision(
            should_extract=False, reason='custom_reason',
        )
        pipeline = MemoryPipeline(trigger_policy=fake_trigger)
        result = pipeline.process({'question': 'hi'})
        assert result.triggered is False
        assert result.trigger_reason == 'custom_reason'
        fake_trigger.evaluate.assert_called_once()

    def test_custom_extractor(self):
        fake_extractor = MagicMock(spec=MemoryExtractor)
        fake_extractor.extract.return_value = MemoryProposal(action='NONE')
        pipeline = MemoryPipeline(extractor=fake_extractor)
        result = pipeline.process({
            'action_proposal': {'action_type': 'X'},
        })
        assert result.proposal.action == 'NONE'
        assert result.command is None
        fake_extractor.extract.assert_called_once()

    def test_custom_write_policy(self):
        fake_wp = MagicMock(spec=MemoryWritePolicy)
        fake_wp.evaluate.return_value = None
        fake_extractor = MagicMock(spec=MemoryExtractor)
        fake_extractor.extract.return_value = MemoryProposal(action='NONE')
        pipeline = MemoryPipeline(extractor=fake_extractor, write_policy=fake_wp)
        pipeline.process({'action_proposal': {'action_type': 'X'}})
        fake_wp.evaluate.assert_called_once()

    def test_custom_llm_callable_invoked(self):
        captured = {}

        def fake_llm(system, user):
            captured['system'] = system
            captured['user'] = user
            return json.dumps({'action': 'NONE'})

        pipeline = MemoryPipeline(llm_callable=fake_llm)
        pipeline.process({'action_proposal': {'action_type': 'X'}})
        # P1-A：system prompt 是渲染后的字符串；Pipeline 默认 Extractor 共享
        # 默认 policy，因此 captured['system'] 等于 extractor.system_prompt。
        extractor = pipeline.extractor
        assert captured['system'] == extractor.system_prompt
        assert '当前事实信息' in captured['user']


# ---------- Pipeline 行为 ----------

class TestPipelineBehavior:
    def test_trigger_reason_propagates(self):
        pipeline = MemoryPipeline()
        result = pipeline.process({
            'memory_context': {'taskType': 'GENERIC', 'status': 'ACTIVE'}
        })
        assert result.trigger_reason == 'existing_memory_present'

    def test_idempotent_same_input(self):
        pipeline = MemoryPipeline()
        result_a = pipeline.process({'question': 'hi', 'answer': 'x'})
        result_b = pipeline.process({'question': 'hi', 'answer': 'x'})
        assert result_a == result_b

    def test_not_triggered_command_and_proposal_are_none(self):
        pipeline = MemoryPipeline()
        result = pipeline.process({})
        assert result.triggered is False
        assert result.proposal is None
        assert result.command is None
        assert result.error is None

    def test_default_uses_internal_components(self):
        pipeline = MemoryPipeline()
        assert isinstance(pipeline.trigger_policy, MemoryTriggerPolicy)
        assert isinstance(pipeline.extractor, MemoryExtractor)
        assert isinstance(pipeline.write_policy, MemoryWritePolicy)
