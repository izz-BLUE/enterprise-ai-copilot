import json
import os
from threading import Lock

from app.core.config import CHUNKS_FILE, logger

_lock = Lock()
_chunks: tuple[dict, ...] | None = None
_load_error: str | None = None


def get_chunks() -> tuple[dict, ...]:
    """进程内共享、只读的 chunks 快照。"""
    global _chunks, _load_error
    if _chunks is not None:
        return _chunks
    with _lock:
        if _chunks is not None:
            return _chunks
        if not os.path.isfile(CHUNKS_FILE):
            _load_error = f'chunks.json 不存在: {CHUNKS_FILE}'
            logger.warning(_load_error)
            _chunks = ()
            return _chunks
        try:
            with open(CHUNKS_FILE, 'r', encoding='utf-8') as file:
                loaded = json.load(file)
            if not isinstance(loaded, list):
                raise ValueError('chunks.json 顶层必须是数组')
            _chunks = tuple(loaded)
            _load_error = None
            logger.info('共享知识库加载完成: %d 个 chunk', len(_chunks))
        except Exception as exc:
            _load_error = f'chunks.json 加载失败: {type(exc).__name__}'
            logger.exception(_load_error)
            _chunks = ()
        return _chunks


def chunk_store_status() -> dict[str, object]:
    chunks = get_chunks()
    return {
        'ready': bool(chunks),
        'count': len(chunks),
        'error': _load_error,
    }
