from fastapi.testclient import TestClient

from app import main
from app.schemas.chat_schema import ChatResponse

client = TestClient(main.app)


def test_standard_rag_technical_failure_uses_502(monkeypatch):
    monkeypatch.setattr(main, 'process_chat', lambda *_args, **_kwargs: ChatResponse(
        answer='unavailable', model='model', traceId='trace', success=False,
    ))

    response = client.post('/agent/chat', json={'message': 'question'})

    assert response.status_code == 502
    assert response.json()['success'] is False


def test_agent_technical_failure_uses_502(monkeypatch):
    monkeypatch.setattr(main, 'run_langgraph_agent', lambda *_args, **_kwargs: {
        'answer': 'unavailable',
        'route': 'error',
        'safe': True,
        'category': 'error',
        'reason': '',
        'sources': [],
    })

    response = client.post('/agent/langgraph/chat', json={'message': 'question'})

    assert response.status_code == 502
    assert response.json()['success'] is False


def test_python_direct_input_limit_uses_422():
    response = client.post('/agent/chat', json={'message': 'x' * (main.MAX_MESSAGE_LENGTH + 1)})
    assert response.status_code == 422
