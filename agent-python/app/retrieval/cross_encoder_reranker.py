"""
Cross Encoder Re-ranker — 对 query-candidate pair 逐对精排。

使用 sentence_transformers.CrossEncoder 加载本地或 HuggingFace 模型，
对 Hybrid Retrieval 返回的候选 chunk 进行精排，返回最终 TopK。

模型延迟加载，全局单例复用。
"""

from app.core.config import RERANK_MODEL, logger

# ── 模块级单例 ──────────────────────────────────────────────────────
_model = None
_model_loaded = False


def _get_model():
    """延迟加载 CrossEncoder 模型（单例）。"""
    global _model, _model_loaded
    if _model_loaded:
        return _model

    _model_loaded = True

    try:
        from sentence_transformers import CrossEncoder
    except ImportError:
        logger.warning('sentence-transformers 未安装，Cross Encoder Re-rank 不可用')
        return None

    try:
        logger.info('加载 Cross Encoder 模型: %s', RERANK_MODEL)
        _model = CrossEncoder(RERANK_MODEL)
        logger.info('Cross Encoder 模型加载完成')
    except Exception:
        logger.exception('Cross Encoder 模型加载失败: %s', RERANK_MODEL)
        _model = None

    return _model


def rerank(query: str, candidates: list[dict], top_k: int = 3) -> list[dict]:
    """对候选 chunk 进行 Cross Encoder 精排。

    参数：
        query: 用户问题
        candidates: Hybrid Retrieval 返回的候选 chunk 列表
        top_k: 最终返回数量

    返回：
        按 rerank_score 降序排列的 top_k 个 chunk 副本
    """
    model = _get_model()
    if model is None:
        logger.warning('Cross Encoder 不可用，降级返回原始排序前 %d 个', top_k)
        return candidates[:top_k]

    if not candidates:
        return []

    # 构造 query-document 文本对
    pairs = [(query, c['content']) for c in candidates]

    # 批量打分
    scores = model.predict(pairs)

    # 构造带分数的副本（不修改原始对象）
    scored = []
    for chunk, score in zip(candidates, scores):
        copy = dict(chunk)
        copy['rerank_score'] = float(score)
        scored.append(copy)

    # 按 rerank_score 降序排列
    scored.sort(key=lambda x: -x['rerank_score'])

    logger.info(
        'Cross Encoder Re-rank: 输入 %d 个候选, 返回 top %d, 最高分 %.4f, 最低分 %.4f',
        len(candidates), top_k,
        scored[0]['rerank_score'] if scored else 0,
        scored[min(top_k, len(scored)) - 1]['rerank_score'] if scored else 0,
    )

    return scored[:top_k]
