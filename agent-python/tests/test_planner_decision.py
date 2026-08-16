"""test_planner_decision.py —— PlannerDecision 字段一致性校验测试

覆盖规格要求的合法结构、非法结构与 Schema 边界。
"""

import pytest
from pydantic import ValidationError

from app.schemas.planner_schema import (
    EVAL_TOOL_NAME,
    RAG_TOOL_NAME,
    PlannerDecision,
    PlannerDecisionError,
)


def _rag(**changes):
    value = {
        'action': 'tool',
        'tool_name': RAG_TOOL_NAME,
        'arguments': {'question': '公司的年假制度是什么'},
        'answer': None,
        'reason_code': 'need_knowledge',
    }
    value.update(changes)
    return value


def _eval(**changes):
    value = {
        'action': 'tool',
        'tool_name': EVAL_TOOL_NAME,
        'arguments': {'report_type': 'all'},
        'answer': None,
        'reason_code': 'need_eval',
    }
    value.update(changes)
    return value


def _finish(**changes):
    value = {
        'action': 'finish',
        'tool_name': None,
        'arguments': None,
        'answer': '年假制度规定：入职满1年享有5天年假。',
        'reason_code': 'task_complete',
    }
    value.update(changes)
    return value


def _refuse(**changes):
    value = {
        'action': 'refuse',
        'tool_name': None,
        'arguments': None,
        'answer': '该请求不允许处理。',
        'reason_code': 'not_allowed',
    }
    value.update(changes)
    return value


class TestValidDecisions:
    def test_rag_tool_decision(self):
        decision = PlannerDecision.model_validate(_rag()).validate_decision()
        assert decision.action == 'tool'
        assert decision.tool_name == RAG_TOOL_NAME
        assert decision.reason_code == 'need_knowledge'

    def test_eval_tool_decision(self):
        decision = PlannerDecision.model_validate(_eval()).validate_decision()
        assert decision.action == 'tool'
        assert decision.tool_name == EVAL_TOOL_NAME
        assert decision.reason_code == 'need_eval'

    def test_finish_decision(self):
        decision = PlannerDecision.model_validate(_finish()).validate_decision()
        assert decision.action == 'finish'
        assert decision.answer

    def test_refuse_decision(self):
        decision = PlannerDecision.model_validate(_refuse()).validate_decision()
        assert decision.action == 'refuse'
        assert decision.answer


class TestToolDecisionConsistency:
    def test_tool_missing_tool_name_rejected(self):
        with pytest.raises(PlannerDecisionError):
            PlannerDecision.model_validate(_rag(tool_name=None)).validate_decision()

    def test_tool_missing_arguments_rejected(self):
        with pytest.raises(PlannerDecisionError):
            PlannerDecision.model_validate(_rag(arguments=None)).validate_decision()

    def test_tool_empty_arguments_rejected(self):
        with pytest.raises(PlannerDecisionError):
            PlannerDecision.model_validate(_rag(arguments={})).validate_decision()

    def test_rag_tool_missing_question_rejected(self):
        with pytest.raises(PlannerDecisionError):
            PlannerDecision.model_validate(_rag(arguments={'other': 'x'})).validate_decision()

    def test_rag_tool_blank_question_rejected(self):
        with pytest.raises(PlannerDecisionError):
            PlannerDecision.model_validate(_rag(arguments={'question': '   '})).validate_decision()

    def test_eval_tool_invalid_report_type_rejected(self):
        with pytest.raises(PlannerDecisionError):
            PlannerDecision.model_validate(_eval(arguments={'report_type': 'secret'})).validate_decision()


class TestFinishRefuseConsistency:
    def test_finish_with_tool_name_rejected(self):
        with pytest.raises(PlannerDecisionError):
            PlannerDecision.model_validate(_finish(tool_name=RAG_TOOL_NAME)).validate_decision()

    def test_finish_missing_answer_rejected(self):
        with pytest.raises(PlannerDecisionError):
            PlannerDecision.model_validate(_finish(answer=None)).validate_decision()

    def test_finish_blank_answer_rejected(self):
        with pytest.raises(PlannerDecisionError):
            PlannerDecision.model_validate(_finish(answer='   ')).validate_decision()

    def test_refuse_with_tool_name_rejected(self):
        with pytest.raises(PlannerDecisionError):
            PlannerDecision.model_validate(_refuse(tool_name=EVAL_TOOL_NAME)).validate_decision()

    def test_refuse_missing_answer_rejected(self):
        with pytest.raises(PlannerDecisionError):
            PlannerDecision.model_validate(_refuse(answer=None)).validate_decision()

    def test_refuse_blank_answer_rejected(self):
        with pytest.raises(PlannerDecisionError):
            PlannerDecision.model_validate(_refuse(answer='')).validate_decision()


class TestArgumentsWhitelist:
    """arguments 严格白名单：模型不能通过参数夹带系统控制字段。"""

    def test_rag_arguments_with_trace_id_rejected(self):
        with pytest.raises(PlannerDecisionError):
            PlannerDecision.model_validate(
                _rag(arguments={'question': '公司的年假制度是什么', 'trace_id': 'fake'})
            ).validate_decision()

    def test_rag_arguments_with_permission_field_rejected(self):
        with pytest.raises(PlannerDecisionError):
            PlannerDecision.model_validate(
                _rag(arguments={'question': '公司的年假制度是什么', 'allow_eval': True})
            ).validate_decision()

    def test_eval_arguments_with_allow_eval_rejected(self):
        with pytest.raises(PlannerDecisionError):
            PlannerDecision.model_validate(
                _eval(arguments={'report_type': 'all', 'allow_eval': True})
            ).validate_decision()

    def test_arguments_with_unknown_key_rejected(self):
        with pytest.raises(PlannerDecisionError):
            PlannerDecision.model_validate(
                _rag(arguments={'question': '公司的年假制度是什么', 'user_id': 'u1'})
            ).validate_decision()

    def test_rag_arguments_with_original_question_rejected(self):
        with pytest.raises(PlannerDecisionError):
            PlannerDecision.model_validate(
                _rag(arguments={'question': '公司的年假制度是什么', 'original_question': 'x'})
            ).validate_decision()


class TestSchemaBoundaries:
    def test_invalid_action_rejected(self):
        with pytest.raises(ValidationError):
            PlannerDecision.model_validate(_rag(action='hack'))

    def test_invalid_tool_name_rejected(self):
        with pytest.raises(ValidationError):
            PlannerDecision.model_validate(_rag(tool_name='sql_tool'))

    def test_invalid_reason_code_rejected(self):
        with pytest.raises(ValidationError):
            PlannerDecision.model_validate(_rag(reason_code='whatever'))

    def test_extra_fields_rejected(self):
        with pytest.raises(ValidationError):
            PlannerDecision.model_validate(_rag(trace_id='model-injected', system_role='admin'))

    def test_missing_required_fields_rejected(self):
        with pytest.raises(ValidationError):
            PlannerDecision.model_validate({'action': 'tool'})
