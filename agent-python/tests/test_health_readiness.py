from unittest.mock import Mock

from fastapi.testclient import TestClient

from app import main

client = TestClient(main.app)


def test_liveness_does_not_depend_on_provider_or_indexes():
    response = client.get('/agent/health')
    assert response.status_code == 200
    assert response.json()['status'] == 'UP'


def test_readiness_returns_503_with_structured_failed_checks(monkeypatch):
    monkeypatch.setattr(main, 'DEEPSEEK_API_KEY', '')
    monkeypatch.setattr(main, 'chunk_store_status', lambda: {'ready': False, 'count': 0})
    monkeypatch.setattr(main, 'faiss_status', lambda: {'ready': False, 'reason': 'missing'})

    response = client.get('/agent/ready')

    assert response.status_code == 503
    assert response.json()['status'] == 'NOT_READY'
    assert response.json()['checks']['provider_config']['ready'] is False


def test_readiness_returns_200_only_when_all_dependencies_are_ready(monkeypatch):
    monkeypatch.setattr(main, 'DEEPSEEK_API_KEY', 'configured')
    monkeypatch.setattr(main, 'DEEPSEEK_BASE_URL', 'https://provider.example')
    monkeypatch.setattr(main, 'DEEPSEEK_MODEL', 'model')
    monkeypatch.setattr(main, 'chunk_store_status', lambda: {'ready': True, 'count': 1})
    monkeypatch.setattr(main, 'faiss_status', lambda: {'ready': True, 'count': 1})
    runtime = Mock()
    runtime.readiness.return_value = {'enabled': True, 'ready': True}
    monkeypatch.setattr(main.app.state, 'checkpoint_runtime', runtime, raising=False)

    response = client.get('/agent/ready')

    assert response.status_code == 200
    assert response.json()['status'] == 'READY'
