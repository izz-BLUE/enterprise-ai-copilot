from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_version_uses_safe_defaults(monkeypatch):
    for name in ('APP_VERSION', 'GIT_COMMIT', 'BUILD_TIME'):
        monkeypatch.delenv(name, raising=False)

    response = client.get('/agent/version')

    assert response.status_code == 200
    assert response.json() == {
        'service': 'agent-python',
        'version': 'dev',
        'gitCommit': 'unknown',
        'buildTime': 'unknown',
    }


def test_version_reads_environment_without_exposing_secrets(monkeypatch):
    monkeypatch.setenv('APP_VERSION', '0.4.1-test')
    monkeypatch.setenv('GIT_COMMIT', '0123456789012345678901234567890123456789')
    monkeypatch.setenv('BUILD_TIME', '2026-07-15T06:30:00Z')
    monkeypatch.setenv('ADMIN_TOKEN', 'not-returned')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'not-returned')

    response = client.get('/agent/version')
    payload = response.json()

    assert response.status_code == 200
    assert payload == {
        'service': 'agent-python',
        'version': '0.4.1-test',
        'gitCommit': '0123456789012345678901234567890123456789',
        'buildTime': '2026-07-15T06:30:00Z',
    }
    assert 'ADMIN_TOKEN' not in payload
    assert 'DEEPSEEK_API_KEY' not in payload
