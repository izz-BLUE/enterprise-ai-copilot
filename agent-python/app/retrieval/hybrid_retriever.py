from app.core.config import logger
from app.retrieval import faiss_retriever, keyword_retriever


def retrieve(query: str, top_k: int = 3) -> list[dict]:
    """Hybrid 检索：Faiss 语义检索 + keyword 关键词检索，Faiss 结果优先，keyword 补充。"""

    # 1. 分别调用两套检索器
    faiss_results = faiss_retriever.retrieve(query, top_k)
    keyword_results = keyword_retriever.retrieve(query, top_k)

    # 2. 按 chunk id 去重，faiss 优先在前
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

    # 3. 截取 top_k
    final = merged[:top_k]

    # 4. 日志
    logger.info(
        'Hybrid检索: faiss=%d, keyword=%d, 去重后=%d, 最终返回=%d, ids=%s',
        len(faiss_results), len(keyword_results),
        len(merged), len(final),
        [c['id'] for c in final],
    )

    return final
