import importlib

import pytest


def _reload_config(monkeypatch, *, mode='DISABLED', dsn='', timeout='3'):
    import dotenv

    monkeypatch.setattr(dotenv, 'load_dotenv', lambda *args, **kwargs: None)
    monkeypatch.setenv('LANGGRAPH_CHECKPOINT_MODE', mode)
    monkeypatch.setenv('LANGGRAPH_CHECKPOINT_DSN', dsn)
    monkeypatch.setenv('LANGGRAPH_CHECKPOINT_CONNECT_TIMEOUT_SECONDS', timeout)
    import app.core.config as config
    return importlib.reload(config)


def test_checkpoint_configuration_defaults_and_normalizes_mode(monkeypatch):
    config = _reload_config(monkeypatch, mode='postgres', dsn='postgresql://checkpoint')
    assert config.LANGGRAPH_CHECKPOINT_MODE == 'POSTGRES'
    assert config.LANGGRAPH_CHECKPOINT_DSN == 'postgresql://checkpoint'
    assert config.LANGGRAPH_CHECKPOINT_CONNECT_TIMEOUT_SECONDS == 3


def test_postgres_mode_requires_dsn(monkeypatch):
    with pytest.raises(ValueError, match='LANGGRAPH_CHECKPOINT_DSN'):
        _reload_config(monkeypatch, mode='POSTGRES')


def test_checkpoint_configuration_rejects_invalid_mode_and_timeout(monkeypatch):
    with pytest.raises(ValueError, match='只允许'):
        _reload_config(monkeypatch, mode='memory')
    with pytest.raises(ValueError, match=r'\[1, 60\]'):
        _reload_config(monkeypatch, timeout='0')
