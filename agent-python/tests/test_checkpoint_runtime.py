from unittest.mock import MagicMock, patch

import pytest

from app.runtime.checkpoint_runtime import CheckpointRuntime


def _runtime(mode='POSTGRES', dsn='postgresql://runtime-test'):
    return CheckpointRuntime(
        mode=mode,
        dsn=dsn,
        connect_timeout_seconds=3,
        max_connections=3,
    )


def test_disabled_runtime_does_not_create_connection_pool():
    runtime = _runtime(mode='DISABLED', dsn='')
    with patch('app.runtime.checkpoint_runtime.ConnectionPool') as pool_type:
        runtime.start()
    pool_type.assert_not_called()
    assert runtime.get_graph(use_planner=True) is None
    assert runtime.readiness() == {'enabled': False, 'ready': True}


def test_postgres_runtime_uses_pool_strict_serializer_and_startup_compiled_graphs():
    runtime = _runtime()
    pool = MagicMock()
    saver = MagicMock()
    planner_graph = MagicMock()
    deterministic_graph = MagicMock()
    with (
        patch('app.runtime.checkpoint_runtime.ConnectionPool', return_value=pool) as pool_type,
        patch('app.runtime.checkpoint_runtime.PostgresSaver', return_value=saver) as saver_type,
        patch('app.runtime.checkpoint_runtime.compile_agent_loop_graph', return_value=planner_graph) as planner,
        patch(
            'app.runtime.checkpoint_runtime.compile_agent_graph',
            return_value=deterministic_graph,
        ) as deterministic,
    ):
        runtime.start()

    pool_type.assert_called_once_with(
        'postgresql://runtime-test',
        min_size=1,
        max_size=3,
        timeout=3,
        open=True,
        kwargs={
            'autocommit': True,
            'row_factory': pytest.importorskip('psycopg.rows').dict_row,
            'connect_timeout': 3,
        },
    )
    pool.wait.assert_called_once_with(timeout=3)
    serializer = saver_type.call_args.kwargs['serde']
    assert serializer._allowed_msgpack_modules is None
    saver.setup.assert_called_once_with()
    planner.assert_called_once_with(checkpointer=saver)
    deterministic.assert_called_once_with(checkpointer=saver)
    assert runtime.get_graph(use_planner=True) is planner_graph
    assert runtime.get_graph(use_planner=False) is deterministic_graph


def test_postgres_startup_failure_closes_pool_and_never_falls_back_to_disabled():
    runtime = _runtime()
    pool = MagicMock()
    pool.wait.side_effect = OSError('connection refused')
    with patch('app.runtime.checkpoint_runtime.ConnectionPool', return_value=pool):
        with pytest.raises(RuntimeError, match='PostgreSQL checkpoint 初始化失败'):
            runtime.start()

    pool.close.assert_called_once_with()
    assert runtime.enabled is True
    assert runtime.readiness() == {'enabled': True, 'ready': False}


def test_thread_variants_are_validated_and_topology_isolated():
    runtime = _runtime()
    base = 'rt_' + ('a' * 64)
    assert runtime.build_thread_id(base, use_planner=True) == base + ':planner-v1'
    assert runtime.build_thread_id(base, use_planner=False) == base + ':deterministic-v1'
    with pytest.raises(ValueError, match='格式无效'):
        runtime.build_thread_id('rt_fake', use_planner=True)


def test_readiness_uses_lightweight_select_one_without_exposing_dsn():
    runtime = _runtime()
    pool = MagicMock()
    connection = pool.connection.return_value.__enter__.return_value
    connection.execute.return_value.fetchone.return_value = {'?column?': 1}
    runtime._pool = pool

    assert runtime.readiness() == {'enabled': True, 'ready': True}
    connection.execute.assert_called_once_with('SELECT 1')
