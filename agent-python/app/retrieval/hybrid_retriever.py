"""
Hybrid Retriever — 支持 vector / hybrid / hybrid_rerank 三种模式。

- vector: Faiss 语义检索 + keyword 补充（原有逻辑）
- hybrid: Faiss 语义检索 + BM25 + RRF 融合排序（默认）
- hybrid_rank: Hybrid 候选召回 + Cross Encoder 精排（实验）
"""

from app.core.config import RERANK_CANDIDATE_K, logger
from app.retrieval import faiss_retriever, keyword_retriever, bm25_retriever
from app.retrieval import cross_encoder_reranker
from app.retrieval.retrieval_gate import CandidateSignals

# RRF 常数
RRF_K = 60

# 合法检索模式
_VALID_MODES = {'vector', 'hybrid', 'hybrid_rerank'}


def _rrf_fusion(*ranked_lists: list[dict], top_k: int = 3) -> list[dict]:
    """Reciprocal Rank Fusion：融合多个排序结果。

    score += 1 / (RRF_K + rank)，rank 从 1 开始。
    """
    scores: dict[str, float] = {}
    chunk_map: dict[str, dict] = {}

    for ranked in ranked_lists:
        for rank, chunk in enumerate(ranked, 1):
            cid = chunk['id']
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (RRF_K + rank)
            chunk_map[cid] = chunk

    # 按 RRF 分数降序排列
    sorted_ids = sorted(scores.keys(), key=lambda cid: -scores[cid])
    return [chunk_map[cid] for cid in sorted_ids[:top_k]]


def retrieve_vector(query: str, top_k: int = 3) -> list[dict]:
    """Vector 模式：Faiss + keyword 合并去重（原有逻辑）。"""
    faiss_results = faiss_retriever.retrieve(query, top_k)
    keyword_results = keyword_retriever.retrieve(query, top_k)

    seen_ids = set()
    merged = []
    for chunk in faiss_results:
        cid = chunk['id']
        if cid not in seen_ids:
            seen_ids.add(cid)
            merged.append(chunk)
    for chunk in keyword_results:
        cid = chunk['id']
        if cid not in seen_ids:
            seen_ids.add(cid)
            merged.append(chunk)

    final = merged[:top_k]

    logger.info(
        'Vector检索: faiss=%d, keyword=%d, 去重后=%d, 最终=%d, ids=%s',
        len(faiss_results), len(keyword_results),
        len(merged), len(final),
        [c['id'] for c in final],
    )
    return final


def retrieve_hybrid(query: str, top_k: int = 3, candidate_k: int = 10) -> list[dict]:
    """Hybrid 模式：Faiss + BM25 + RRF 融合排序。"""
    final, _signals = retrieve_hybrid_with_signals(query, top_k, candidate_k)
    return final


def retrieve_hybrid_with_signals(
    query: str, top_k: int = 3, candidate_k: int = 10,
) -> tuple[list[dict], list[CandidateSignals]]:
    """Hybrid 检索，同时返回按 chunk_id 合并的原始检索信号。"""
    faiss_scored = faiss_retriever.retrieve_with_scores(query, candidate_k)
    bm25_scored = bm25_retriever.retrieve_with_scores(query, candidate_k)
    faiss_results = [chunk for chunk, _score in faiss_scored]
    bm25_results = [chunk for chunk, _score in bm25_scored]

    final = _rrf_fusion(faiss_results, bm25_results, top_k=top_k)
    signals = _merge_candidate_signals(faiss_scored, bm25_scored)

    logger.info(
        'Hybrid检索(RRF): faiss=%d, bm25=%d, fused=%d, ids=%s',
        len(faiss_results), len(bm25_results),
        len(final),
        [c['id'] for c in final],
    )
    return final, signals


def _merge_candidate_signals(
    faiss_scored: list[tuple[dict, float]],
    bm25_scored: list[tuple[dict, float]],
) -> list[CandidateSignals]:
    """按同一 chunk_id 合并信号，绝不跨候选拼接分数。"""
    merged: dict[str, dict] = {}
    order: list[str] = []

    for rank, (chunk, score) in enumerate(faiss_scored, 1):
        chunk_id = chunk['id']
        if chunk_id not in merged:
            merged[chunk_id] = {'chunk_id': chunk_id}
            order.append(chunk_id)
        merged[chunk_id].update(vector_score=score, vector_rank=rank)

    for rank, (chunk, score) in enumerate(bm25_scored, 1):
        chunk_id = chunk['id']
        if chunk_id not in merged:
            merged[chunk_id] = {'chunk_id': chunk_id}
            order.append(chunk_id)
        merged[chunk_id].update(bm25_score=score, bm25_rank=rank)

    return [CandidateSignals(**merged[chunk_id]) for chunk_id in order]


def retrieve_hybrid_rerank(query: str, top_k: int = 3,
                           candidate_k: int | None = None) -> list[dict]:
    """Hybrid + Cross Encoder Re-rank 模式。

    1. 通过 Hybrid Retrieval 获取 candidate_k 个候选
    2. 调用 Cross Encoder 精排，返回最终 top_k
    """
    if candidate_k is None:
        candidate_k = RERANK_CANDIDATE_K

    # 先用 Hybrid 召回候选
    candidates = retrieve_hybrid(query, top_k=candidate_k, candidate_k=candidate_k)

    # Cross Encoder 精排
    final = cross_encoder_reranker.rerank(query, candidates, top_k=top_k)

    logger.info(
        'Hybrid+Re-rank: 候选=%d, 精排后=%d, ids=%s',
        len(candidates), len(final),
        [c['id'] for c in final],
    )
    return final


def retrieve(query: str, top_k: int = 3, mode: str = 'hybrid') -> list[dict]:
    """统一检索入口。

    mode:
        'vector'         — Faiss + keyword 合并去重（原有逻辑）
        'hybrid'         — Faiss + BM25 + RRF 融合（默认）
        'hybrid_rerank'  — Hybrid 候选召回 + Cross Encoder 精排（实验）

    Raises:
        ValueError: mode 不在合法值范围内
    """
    if mode not in _VALID_MODES:
        raise ValueError(
            f'不支持的检索模式: {mode!r}，可选值: {sorted(_VALID_MODES)}'
        )

    if mode == 'vector':
        return retrieve_vector(query, top_k)
    elif mode == 'hybrid':
        return retrieve_hybrid(query, top_k)
    else:
        return retrieve_hybrid_rerank(query, top_k)


def retrieve_with_signals(
    query: str, top_k: int = 3, mode: str = 'hybrid', candidate_k: int = 10,
) -> tuple[list[dict], list[CandidateSignals]]:
    """供生成链路使用的共享 scored retrieval 入口。"""
    if mode != 'hybrid':
        chunks = retrieve(query, top_k=top_k, mode=mode)
        return chunks, []
    return retrieve_hybrid_with_signals(query, top_k=top_k, candidate_k=candidate_k)
