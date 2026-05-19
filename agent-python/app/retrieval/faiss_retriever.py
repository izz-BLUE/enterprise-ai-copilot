import json
import os
import time

import numpy as np

from app.core.config import FAISS_INDEX_FILE, FAISS_META_FILE, logger

MODEL_NAME = 'BAAI/bge-small-zh-v1.5'

# Module-level state
_index = None
_metadata: list[dict] = []
_model = None
_available = False


def _load_resources():
    """启动时加载 Faiss 索引、metadata 和 embedding 模型。
    如果索引文件不存在，仅记录警告，不阻塞服务启动。
    """
    global _index, _metadata, _model, _available

    # 1. 检查文件是否存在
    if not os.path.isfile(FAISS_INDEX_FILE):
        logger.warning('faiss.index 不存在，Faiss 检索不可用: %s', FAISS_INDEX_FILE)
        return
    if not os.path.isfile(FAISS_META_FILE):
        logger.warning('faiss_metadata.json 不存在，Faiss 检索不可用: %s', FAISS_META_FILE)
        return

    # 2. 加载 Faiss 索引（使用 deserialize 避免中文路径问题）
    try:
        import faiss
    except ImportError:
        logger.warning('faiss 未安装，Faiss 检索不可用')
        return

    with open(FAISS_INDEX_FILE, 'rb') as f:
        buf = f.read()
    _index = faiss.deserialize_index(np.frombuffer(buf, dtype='uint8'))
    logger.info('Faiss 索引加载完成: %d 条, 维度 %d', _index.ntotal, _index.d)

    # 3. 加载 metadata
    with open(FAISS_META_FILE, 'r', encoding='utf-8') as f:
        _metadata = json.load(f)
    logger.info('Faiss metadata 加载完成: %d 条', len(_metadata))

    # 4. 加载 embedding 模型
    t0 = time.time()
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        logger.warning('sentence-transformers 未安装，Faiss 检索不可用')
        _index = None
        _metadata = []
        return

    _model = SentenceTransformer(MODEL_NAME)
    logger.info('Embedding 模型加载完成: %s (耗时 %.1fs)', MODEL_NAME, time.time() - t0)

    _available = True
    logger.info('Faiss 检索就绪')


def retrieve(query: str, top_k: int = 3) -> list[dict]:
    """使用 Faiss 索引进行语义检索。

    返回的每个 dict 包含:
        id, domain, source_file, chunk_index, content
    """
    if not _available:
        logger.warning('Faiss 检索不可用，返回空结果')
        return []

    import faiss

    # 1. 用户问题 → embedding
    query_emb = _model.encode(query, normalize_embeddings=True)

    # 2. L2 normalize（与索引构建时的 normalize 保持一致）
    query_emb = np.expand_dims(query_emb, axis=0).astype(np.float32)
    faiss.normalize_L2(query_emb)

    # 3. 检索
    distances, indices = _index.search(query_emb, top_k)

    # 4. 组装结果（保持与 keyword_retriever 相同的 dict 结构）
    results = []
    for i in range(top_k):
        idx = indices[0][i]
        score = float(distances[0][i])
        if score < 0.01:
            continue
        chunk = _metadata[idx]
        results.append({
            'id': chunk['id'],
            'domain': chunk['domain'],
            'source_file': chunk['source_file'],
            'chunk_index': chunk['chunk_index'],
            'content': chunk['content'],
        })

    return results


# 模块加载时初始化
_load_resources()
