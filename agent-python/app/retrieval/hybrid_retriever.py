"""
Hybrid Retriever — 支持 vector / hybrid 两种模式。

- vector: Faiss 语义检索 + keyword 补充（原有逻辑）
- hybrid: Faiss 语义检索 + BM25 + RRF 融合排序（新增）
"""

from app.core.config import logger
from app.retrieval import faiss_retriever, keyword_retriever, bm25_retriever

# RRF 常数
RRF_K = 60


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
    faiss_results = faiss_retriever.retrieve(query, candidate_k)
    bm25_results = bm25_retriever.retrieve(query, candidate_k)

    final = _rrf_fusion(faiss_results, bm25_results, top_k=top_k)

    logger.info(
        'Hybrid检索(RRF): faiss=%d, bm25=%d, fused=%d, ids=%s',
        len(faiss_results), len(bm25_results),
        len(final),
        [c['id'] for c in final],
    )
    return final


def retrieve(query: str, top_k: int = 3, mode: str = 'hybrid') -> list[dict]:
    """统一检索入口。

    mode:
        'vector' — Faiss + keyword 合并去重（原有逻辑）
        'hybrid' — Faiss + BM25 + RRF 融合（默认）
    """
    if mode == 'vector':
        return retrieve_vector(query, top_k)
    else:
        return retrieve_hybrid(query, top_k)
