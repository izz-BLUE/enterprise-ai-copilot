import json

from app.main import _busy_response


def test_standard_busy_response_preserves_contract():
    response = _busy_response('/agent/chat', 'trace-standard')
    body = json.loads(response.body)

    assert response.status_code == 429
    assert response.headers['retry-after'] == '1'
    assert response.headers['x-trace-id'] == 'trace-standard'
    assert body['traceId'] == 'trace-standard'
    assert body['success'] is False
    assert isinstance(body['model'], str)


def test_agent_busy_response_is_explicit():
    response = _busy_response('/agent/langgraph/chat', 'trace-agent')
    body = json.loads(response.body)

    assert response.status_code == 429
    assert body['route'] == 'busy'
    assert body['category'] == 'overloaded'
    assert body['sources'] == []
    assert body['traceId'] == 'trace-agent'
    assert body['success'] is False
