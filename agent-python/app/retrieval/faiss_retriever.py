import json
import os

import numpy as np

from app.core.config import FAISS_INDEX_FILE, FAISS_META_FILE, logger

# Module-level state
_index = None
_metadata: list[dict] = []
_available = False
_load_error: str | None = None


def _load_resources():
    """启动时加载 Faiss 索引和 metadata。
    Embedding 模型由 embedding_runtime 统一管理，首次 encode 时延迟加载。
    如果索引文件不存在，仅记录警告，不阻塞服务启动。
    """
    global _index, _metadata, _available, _load_error

    # 1. 检查文件是否存在
    if not os.path.isfile(FAISS_INDEX_FILE):
        _load_error = f'faiss.index 不存在: {FAISS_INDEX_FILE}'
        logger.warning(_load_error)
        return
    if not os.path.isfile(FAISS_META_FILE):
        _load_error = f'faiss_metadata.json 不存在: {FAISS_META_FILE}'
        logger.warning(_load_error)
        return

    # 2. 加载 Faiss 索引（使用 deserialize 避免中文路径问题）
    try:
        import faiss
    except ImportError:
        _load_error = 'faiss 未安装'
        logger.warning('%s，Faiss 检索不可用', _load_error)
        return

    try:
        with open(FAISS_INDEX_FILE, 'rb') as file:
            buf = file.read()
        _index = faiss.deserialize_index(np.frombuffer(buf, dtype='uint8'))
        logger.info('Faiss 索引加载完成: %d 条, 维度 %d', _index.ntotal, _index.d)

        # 3. 加载 metadata，并拒绝索引/元数据数量漂移。
        with open(FAISS_META_FILE, 'r', encoding='utf-8') as file:
            metadata = json.load(file)
        if not isinstance(metadata, list) or len(metadata) != _index.ntotal:
            raise ValueError('Faiss metadata 与索引数量不一致')
        _metadata = metadata
        logger.info('Faiss metadata 加载完成: %d 条', len(_metadata))
    except Exception as exc:
        _index = None
        _metadata = []
        _available = False
        _load_error = f'Faiss 资源加载失败: {type(exc).__name__}'
        logger.exception(_load_error)
        return

    _available = True
    _load_error = None
    logger.info('Faiss 检索就绪')


def faiss_status() -> dict[str, object]:
    return {
        'ready': _available,
        'index_count': int(_index.ntotal) if _index is not None else 0,
        'metadata_count': len(_metadata),
        'error': _load_error,
    }


def retrieve(query: str, top_k: int = 3) -> list[dict]:
    """使用 Faiss 索引进行语义检索。

    返回的每个 dict 包含:
        id, domain, source_file, chunk_index, content
    """
    return [chunk for chunk, _score in retrieve_with_scores(query, top_k)]


def retrieve_with_scores(query: str, top_k: int = 3) -> list[tuple[dict, float]]:
    """使用 Faiss 检索并保留 cosine similarity 原始分数。"""
    if not _available:
        logger.warning('Faiss 检索不可用，返回空结果')
        return []

    import faiss

    from app.retrieval.embedding_runtime import encode

    # 1. 用户问题 → embedding（通过统一 Runtime，支持 torch / onnx）
    query_emb = encode(query)

    # 2. L2 normalize（与索引构建时的 normalize 保持一致）
    query_emb = np.expand_dims(query_emb, axis=0).astype(np.float32)
    faiss.normalize_L2(query_emb)

    # 3. 检索
    similarities, indices = _index.search(query_emb, top_k)

    # 4. 组装结果（保持与 keyword_retriever 相同的 dict 结构）
    results = []
    for i in range(top_k):
        idx = indices[0][i]
        score = float(similarities[0][i])
        if idx < 0 or idx >= len(_metadata) or score < 0.01:
            continue
        chunk = _metadata[idx]
        results.append(({
            'id': chunk['id'],
            'domain': chunk['domain'],
            'source_file': chunk['source_file'],
            'chunk_index': chunk['chunk_index'],
            'content': chunk['content'],
        }, score))

    return results


# 模块加载时初始化
_load_resources()
