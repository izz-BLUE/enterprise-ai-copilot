"""P4-3 Purchase Extension Proof：最小真实领域链路测试。"""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import Mock, patch

import pytest

from app.agents.domain_provider_registry import (
    DOMAIN_PROVIDER_REGISTRY,
    DomainContext,
    DomainProviderAmbiguityError,
    DomainToolCallRejected,
    PurchaseProvider,
)
from app.agents.langgraph_agent import run_langgraph_agent
from app.agents.planner_node import visible_tools
from app.agents.tool_executor_node import tool_executor_node
from app.schemas.planner_schema import (
    PURCHASE_BUDGET_TOOL_NAME,
    PURCHASE_POLICY_TOOL_NAME,
    PURCHASE_PROPOSAL_TOOL_NAME,
    PlannerDecision,
)
from app.tools.enterprise_tools import (
    purchase_budget_tool,
    purchase_policy_tool,
    purchase_proposal_tool,
)
from tests.runtime_helpers import checkpoint_safe_state, runtime_for_state

PURCHASE_QUESTION = '请帮我申请购买一台 MacBook Pro，预算 6800 元，用于开发工作。'

PURCHASE_QUERY_CASES = [
    '公司的采购政策是什么？',
    '采购电脑有什么规定？',
    '申请采购需要什么流程？',
    '如何申请采购？',
    '我想申请采购，需要什么流程？',
    '我要申请采购，需要什么材料？',
    '采购申请需要哪些材料？',
    '采购怎么申请？',
    '如何提交采购申请？',
    '提交采购申请需要什么流程？',
    '提交采购申请需要哪些材料？',
    '发起采购申请有什么规定？',
    '发起采购申请需要哪些条件？',
    '申请采购的流程是什么？',
    '申请购买的规定是什么？',
    '申请采购电脑需要什么流程？',
    '申请购买 MacBook 需要哪些材料？',
    '我要采购电脑需要走什么流程？',
    '我想购买电脑有什么限制？',
    '如何帮我采购一台开发电脑？',
]

PURCHASE_WRITE_CASES = [
    '帮我申请购买一台 MacBook',
    '帮我采购一台开发电脑',
    '我要购买一台 MacBook',
    '我想采购一台开发电脑',
    '请提交采购申请',
    '发起采购申请',
    '帮我提交采购申请',
    '帮我发起采购申请',
    '请尽快帮我采购一台开发电脑',
    '按照公司的采购政策，帮我申请购买一台 MacBook，预算15000，原因是移动端开发',
    '根据采购规定，帮我申请采购一台开发电脑，预算10000，原因是开发测试',
    '帮我购买一台 MacBook，需要符合公司的采购规定',
]

PURCHASE_CROSS_CLAUSE_CASES = [
    '我想申请年假，顺便问下采购政策',
    '帮我申请年假，另外采购政策是什么？',
    '我要申请年假，同时想了解采购流程',
    '帮我请明天年假，再告诉我采购电脑有什么规定',
]


@pytest.mark.parametrize('question', [
    '公司的采购政策是什么？',
    '采购电脑有什么规定？',
    '申请采购需要什么流程？',
    '如何申请采购？',
])
def test_purchase_policy_and_process_questions_are_not_write_intent(question):
    assert PurchaseProvider.is_purchase_request_intent(question) is False


@pytest.mark.parametrize('question', [
    '按照公司的采购政策，帮我申请购买一台 MacBook，预算15000，原因是移动端开发。',
    '根据采购规定，帮我申请采购一台开发电脑，预算10000，原因是开发测试。',
])
def test_explicit_purchase_write_wins_over_policy_or_process_words(question):
    assert PurchaseProvider.is_purchase_request_intent(question) is True


@pytest.mark.parametrize('question', PURCHASE_QUERY_CASES)
def test_purchase_query_matrix_never_matches_write_intent(question):
    provider = PurchaseProvider()

    assert provider.matches(DomainContext(question=question)) is False
    assert DOMAIN_PROVIDER_REGISTRY.capability_tools_for_question(question) == []


@pytest.mark.parametrize('question', PURCHASE_WRITE_CASES)
def test_purchase_write_matrix_matches_purchase_provider(question):
    assert PurchaseProvider().matches(DomainContext(question=question)) is True


@pytest.mark.parametrize('question', PURCHASE_CROSS_CLAUSE_CASES)
def test_purchase_query_in_another_domain_clause_never_matches_purchase(question):
    provider = PurchaseProvider()

    assert provider.matches(DomainContext(question=question)) is False
    assert DOMAIN_PROVIDER_REGISTRY.capability_tools_for_question(question) == []


def test_purchase_query_plus_leave_write_does_not_expose_purchase_capability():
    question = '我想申请年假，顺便问下采购预算'

    visible = visible_tools(
        employee_id='E10001',
        allow_eval=False,
        allow_business_actions=True,
        java_base_url='',
        java_internal_token='',
        question=question,
    )

    assert PurchaseProvider().matches(DomainContext(question=question)) is False
    assert DOMAIN_PROVIDER_REGISTRY.resolve(
        DomainContext(question=question)
    ).domain_key == 'leave'
    assert all(name not in visible for name in (
        PURCHASE_BUDGET_TOOL_NAME,
        PURCHASE_POLICY_TOOL_NAME,
        PURCHASE_PROPOSAL_TOOL_NAME,
    ))


def test_purchase_write_plus_leave_query_keeps_purchase_match():
    question = '帮我采购一台开发电脑，另外想了解年假政策'

    assert PurchaseProvider().matches(DomainContext(question=question)) is True
    assert DOMAIN_PROVIDER_REGISTRY.resolve(
        DomainContext(question=question)
    ).domain_key == 'purchase'


def test_purchase_write_plus_leave_write_remains_fail_closed_ambiguous():
    question = '帮我请明天年假，并帮我采购一台开发电脑'

    assert PurchaseProvider().matches(DomainContext(question=question)) is True
    with pytest.raises(DomainProviderAmbiguityError):
        DOMAIN_PROVIDER_REGISTRY.resolve(DomainContext(question=question))


def test_purchase_write_plus_expense_query_keeps_purchase_match():
    question = '帮我采购一台开发电脑，另外想了解报销流程'

    assert PurchaseProvider().matches(DomainContext(question=question)) is True
    assert DOMAIN_PROVIDER_REGISTRY.resolve(
        DomainContext(question=question)
    ).domain_key == 'purchase'


def test_purchase_query_plus_expense_write_does_not_match_purchase():
    question = '帮我报销最近一次出差，另外问下采购预算'

    assert PurchaseProvider().matches(DomainContext(question=question)) is False
    assert DOMAIN_PROVIDER_REGISTRY.resolve(
        DomainContext(question=question)
    ).domain_key == 'expense'
    assert all(name not in DOMAIN_PROVIDER_REGISTRY.capability_tools_for_question(question)
               for name in (
                   PURCHASE_BUDGET_TOOL_NAME,
                   PURCHASE_POLICY_TOOL_NAME,
                   PURCHASE_PROPOSAL_TOOL_NAME,
               ))


def test_purchase_write_plus_expense_write_remains_fail_closed_ambiguous():
    question = '帮我采购一台开发电脑，并报销最近一次出差'

    assert PurchaseProvider().matches(DomainContext(question=question)) is True
    with pytest.raises(DomainProviderAmbiguityError):
        DOMAIN_PROVIDER_REGISTRY.resolve(DomainContext(question=question))


def _decision(tool_name: str, reason_code: str, **semantic) -> str:
    return json.dumps({
        'action': 'tool',
        'tool_name': tool_name,
        'arguments': {},
        'reason_code': reason_code,
        **semantic,
    }, ensure_ascii=False)


def _finish(answer: str) -> str:
    return json.dumps({
        'action': 'finish', 'answer': answer, 'reason_code': 'task_complete',
    }, ensure_ascii=False)


def _finish_decision(answer: str = '已完成。') -> PlannerDecision:
    return PlannerDecision.model_validate(json.loads(_finish(answer)))


def _purchase_state(**changes) -> dict:
    state = {
        'question': PURCHASE_QUESTION,
        'allow_business_actions': True,
        'business_date': date(2026, 9, 2),
        'employee_id': 'E10001',
        'action_proposal': None,
        'tool_history': [],
        'tool_call_count': 0,
        'step_count': 0,
        'request_expense_reason': None,
        'observation': '',
        'planner_decision': None,
    }
    state.update(changes)
    return state


def test_purchase_capability_is_visible_only_for_authorized_purchase_request():
    common = {
        'employee_id': 'E10001',
        'allow_eval': False,
        'java_base_url': '',
        'java_internal_token': '',
    }
    assert PURCHASE_BUDGET_TOOL_NAME not in visible_tools(
        allow_business_actions=False, question=PURCHASE_QUESTION, **common)
    visible = visible_tools(
        allow_business_actions=True, question=PURCHASE_QUESTION, **common)
    assert visible[-3:] == [
        PURCHASE_BUDGET_TOOL_NAME,
        PURCHASE_POLICY_TOOL_NAME,
        PURCHASE_PROPOSAL_TOOL_NAME,
    ]
    assert visible_tools(
        allow_business_actions=True, question='采购政策怎么规定？', **common
    ) == ['rag_answer_tool', 'leave_proposal_tool', 'expense_proposal_tool']

    direct_purchase = visible_tools(
        allow_business_actions=True, question='请帮我采购一台开发用 MacBook Pro', **common
    )
    assert direct_purchase[-3:] == [
        PURCHASE_BUDGET_TOOL_NAME,
        PURCHASE_POLICY_TOOL_NAME,
        PURCHASE_PROPOSAL_TOOL_NAME,
    ]


def test_purchase_provider_requires_budget_then_policy_then_proposal_facts():
    provider = PurchaseProvider()
    context = DomainContext(
        question=PURCHASE_QUESTION,
        purchase_item='MacBook Pro',
        purchase_budget='6800.00',
        purchase_justification='开发工作',
        step_count=1,
    )
    assert provider.legal_tools([
        PURCHASE_BUDGET_TOOL_NAME, PURCHASE_POLICY_TOOL_NAME, PURCHASE_PROPOSAL_TOOL_NAME,
    ], context) == [PURCHASE_BUDGET_TOOL_NAME]
    budget_history = ({
        'tool_name': PURCHASE_BUDGET_TOOL_NAME,
        'status': 'success',
        'observation': json.dumps({'success': True, 'available_budget': '20000.00'}),
    },)
    context = DomainContext(**{**context.__dict__, 'tool_history': budget_history})
    assert provider.legal_tools([
        PURCHASE_BUDGET_TOOL_NAME, PURCHASE_POLICY_TOOL_NAME, PURCHASE_PROPOSAL_TOOL_NAME,
    ], context) == [PURCHASE_POLICY_TOOL_NAME]
    with pytest.raises(DomainToolCallRejected, match='事实尚未完成'):
        provider.validate_tool_call(PURCHASE_PROPOSAL_TOOL_NAME, {}, context)


def test_purchase_tools_are_deterministic_and_proposal_is_read_only():
    budget = json.loads(purchase_budget_tool.invoke({'employee_id': 'E10001'}))
    assert budget['success'] is True
    assert budget['available_budget'] == '20000.00'

    policy = json.loads(purchase_policy_tool.invoke({
        'item_name': 'MacBook Pro',
        'requested_budget': '6800',
        'justification': '开发工作',
    }))
    assert policy['success'] is True
    assert policy['policy_result'] == 'PASS'

    proposal = json.loads(purchase_proposal_tool.invoke({
        'item_name': 'MacBook Pro',
        'requested_budget': '6800',
        'justification': '开发工作',
        'context': {
            'purchase_budget': budget,
            'purchase_policy': policy,
        },
    }))
    assert proposal['kind'] == 'proposal'
    assert proposal['action_proposal']['action_type'] == 'PURCHASE_REQUEST'


def test_purchase_missing_fields_and_rejections_never_create_proposal():
    facts = {
        'purchase_budget': {'success': True, 'available_budget': '20000.00'},
        'purchase_policy': {'success': True, 'policy_result': 'PASS'},
    }
    missing_item = json.loads(purchase_proposal_tool.invoke({
        'item_name': '', 'requested_budget': '6800', 'justification': '开发工作',
        'context': facts,
    }))
    assert missing_item['kind'] == 'clarification'
    assert missing_item['action_proposal'] is None

    missing_justification = json.loads(purchase_proposal_tool.invoke({
        'item_name': 'MacBook Pro', 'requested_budget': '6800', 'justification': '',
        'context': facts,
    }))
    assert missing_justification['kind'] == 'clarification'
    assert missing_justification['action_proposal'] is None

    over_budget = json.loads(purchase_proposal_tool.invoke({
        'item_name': 'MacBook Pro', 'requested_budget': '25000', 'justification': '开发工作',
        'context': facts,
    }))
    assert over_budget['kind'] == 'rejection'
    assert over_budget['action_proposal'] is None

    denied_policy = json.loads(purchase_proposal_tool.invoke({
        'item_name': '办公椅', 'requested_budget': '6800', 'justification': '个人娱乐',
        'context': {
            'purchase_budget': facts['purchase_budget'],
            'purchase_policy': {
                'success': True, 'item_name': '办公椅', 'requested_budget': '6800',
                'policy_result': 'FAIL', 'policy_reason': 'denied',
            },
        },
    }))
    assert denied_policy['kind'] == 'rejection'
    assert denied_policy['action_proposal'] is None


def _proposal_with_policy_fact(*, current_item='MacBook Pro', current_budget='6800',
                               fact_item='MacBook Pro', fact_budget='6800') -> dict:
    return json.loads(purchase_proposal_tool.invoke({
        'item_name': current_item,
        'requested_budget': current_budget,
        'justification': '开发工作',
        'context': {
            'purchase_budget': {
                'success': True,
                'available_budget': '20000.00',
            },
            'purchase_policy': {
                'success': True,
                'item_name': fact_item,
                'requested_budget': fact_budget,
                'policy_result': 'PASS',
            },
        },
    }))


def test_purchase_proposal_fails_closed_when_policy_item_does_not_match():
    result = _proposal_with_policy_fact(fact_item='MacBook Air')

    assert result['success'] is False
    assert result['error_code'] == 'PURCHASE_FACTS_MISMATCH'
    assert result.get('action_proposal') is None


def test_purchase_proposal_fails_closed_when_policy_budget_does_not_match():
    result = _proposal_with_policy_fact(fact_budget='6800.01')

    assert result['success'] is False
    assert result['error_code'] == 'PURCHASE_FACTS_MISMATCH'
    assert result.get('action_proposal') is None


def test_purchase_proposal_accepts_equivalent_decimal_policy_budget():
    result = _proposal_with_policy_fact(current_budget='15000.00', fact_budget='15000')

    assert result['success'] is True
    assert result['kind'] == 'proposal'


def test_purchase_proposal_accepts_matching_policy_fact():
    result = _proposal_with_policy_fact()

    assert result['success'] is True
    assert result['kind'] == 'proposal'
    assert result['action_proposal']['item_name'] == 'MacBook Pro'


def test_executor_second_gate_rejects_purchase_proposal_without_fresh_facts():
    state = _purchase_state(
        planner_decision={
            'action': 'tool', 'tool_name': PURCHASE_PROPOSAL_TOOL_NAME,
            'arguments': {}, 'reason_code': 'need_purchase_proposal',
            'purchase_item': 'MacBook Pro', 'purchase_budget': '6800',
            'purchase_justification': '开发工作',
        },
    )
    result = tool_executor_node(checkpoint_safe_state(state), runtime_for_state(state))
    assert result['stop_reason'] == 'purchase_prerequisite_missing'
    assert result['tool_call_count'] == 0


def _purchase_completion_item(payload: dict) -> dict:
    return {
        'tool_name': PURCHASE_PROPOSAL_TOOL_NAME,
        'arguments': {},
        'status': 'success',
        'observation': json.dumps(payload, ensure_ascii=False),
    }


def _purchase_completion_context(history: tuple[dict, ...]) -> DomainContext:
    return DomainContext(
        question=PURCHASE_QUESTION,
        tool_history=history,
        purchase_item='MacBook Pro',
        purchase_budget='6800',
        purchase_justification='开发工作',
        step_count=1,
    )


@pytest.mark.parametrize(
    ('tools', 'history'),
    [
        ([PURCHASE_BUDGET_TOOL_NAME], ()),
        (
            [PURCHASE_POLICY_TOOL_NAME],
            ({
                'tool_name': PURCHASE_BUDGET_TOOL_NAME,
                'status': 'success',
                'observation': json.dumps({
                    'success': True, 'available_budget': '20000.00',
                }),
            },),
        ),
        (
            [PURCHASE_PROPOSAL_TOOL_NAME],
            (
                {
                    'tool_name': PURCHASE_BUDGET_TOOL_NAME,
                    'status': 'success',
                    'observation': json.dumps({
                        'success': True, 'available_budget': '20000.00',
                    }),
                },
                {
                    'tool_name': PURCHASE_POLICY_TOOL_NAME,
                    'status': 'success',
                    'observation': json.dumps({
                        'success': True, 'policy_result': 'PASS',
                    }),
                },
            ),
        ),
    ],
)
def test_purchase_finish_is_rejected_before_successful_proposal(tools, history):
    with pytest.raises(ValueError, match='purchase_proposal_tool'):
        PurchaseProvider().validate_completion(
            _finish_decision(),
            tools,
            _purchase_completion_context(history),
        )


def test_purchase_completion_contract_warns_when_proposal_is_hidden():
    contract = PurchaseProvider().completion_contract([
        PURCHASE_BUDGET_TOOL_NAME, PURCHASE_POLICY_TOOL_NAME,
    ])

    assert '采购申请前置事实阶段' in contract
    assert '不得直接 finish' in contract
    assert PURCHASE_PROPOSAL_TOOL_NAME not in contract


@pytest.mark.parametrize('kind', ['proposal', 'clarification', 'rejection'])
def test_purchase_successful_proposal_kinds_still_allow_finish(kind):
    PurchaseProvider().validate_completion(
        _finish_decision('已处理。'),
        [PURCHASE_PROPOSAL_TOOL_NAME],
        _purchase_completion_context((
            _purchase_completion_item({
                'success': True,
                'kind': kind,
                'action_proposal': {'action_type': 'PURCHASE_REQUEST'}
                if kind == 'proposal' else None,
            }),
        )),
    )


def test_purchase_planner_executor_proposal_loop_reaches_confirmable_result():
    decisions = [
        _decision(
            PURCHASE_BUDGET_TOOL_NAME, 'need_purchase_budget',
            purchase_item='MacBook Pro', purchase_budget='6800',
            purchase_justification='开发工作',
        ),
        _decision(PURCHASE_POLICY_TOOL_NAME, 'need_purchase_policy'),
        _decision(PURCHASE_PROPOSAL_TOOL_NAME, 'need_purchase_proposal'),
        _finish('已生成采购申请草稿，请确认后提交。'),
    ]
    budget_tool = Mock()
    budget_tool.invoke.return_value = json.dumps({
        'success': True,
        'available_budget': '20000.00', 'source': 'fixture:purchase_budget',
    })
    policy_tool = Mock()
    policy_tool.invoke.return_value = json.dumps({
        'success': True,
        'item_name': 'MacBook Pro', 'requested_budget': '6800',
        'policy_result': 'PASS', 'policy_reason': 'ok',
    })
    with patch('app.agents.planner_node.call_llm', side_effect=decisions), \
            patch('app.agents.tool_executor_node.purchase_budget_tool', budget_tool), \
            patch('app.agents.tool_executor_node.purchase_policy_tool', policy_tool):
        result = run_langgraph_agent(
            PURCHASE_QUESTION,
            allow_business_actions=True,
            employee_id='E10001',
            business_date=date(2026, 9, 2),
            use_planner=True,
        )

    assert result['stop_reason'] == 'task_complete'
    assert result['step_count'] == 4
    assert result['tool_call_count'] == 3
    assert result['action_proposal']['action_type'] == 'PURCHASE_REQUEST'
    assert str(result['action_proposal']['requested_budget']) == '6800'
    assert [item['tool_name'] for item in result['tool_history']] == [
        PURCHASE_BUDGET_TOOL_NAME,
        PURCHASE_POLICY_TOOL_NAME,
        PURCHASE_PROPOSAL_TOOL_NAME,
    ]
