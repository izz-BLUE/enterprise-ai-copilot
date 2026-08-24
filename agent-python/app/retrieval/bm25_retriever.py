"""
BM25 Retriever — 轻量实现，无外部依赖。

基于现有 chunks.json 构建 BM25 索引，
使用字符级 n-gram 分词（2-gram + 3-gram），对中文友好。
"""

import math
from collections import Counter

from app.core.config import logger
from app.retrieval.chunk_store import get_chunks
from app.retrieval.text_tokenizer import character_ngrams

# ── BM25 参数 ────────────────────────────────────────────────────
_K1 = 1.5
_B = 0.75

# ── 模块级状态 ────────────────────────────────────────────────────
_chunks: list[dict] = []
_tokenized_chunks: list[list[str]] = []
_doc_freqs: list[Counter] = []
_avgdl: float = 0.0
_idf: dict[str, float] = {}
_doc_count: int = 0


def _build_index():
    """构建 BM25 索引：词频、文档频率、IDF。"""
    global _chunks, _tokenized_chunks, _doc_freqs, _avgdl, _idf, _doc_count

    _chunks = list(get_chunks())

    _doc_count = len(_chunks)
    if _doc_count == 0:
        return

    # 分词
    _tokenized_chunks = [character_ngrams(c['content']) for c in _chunks]
    _doc_freqs = [Counter(toks) for toks in _tokenized_chunks]

    # 平均文档长度
    total_len = sum(len(toks) for toks in _tokenized_chunks)
    _avgdl = total_len / _doc_count if _doc_count > 0 else 1.0

    # 计算 IDF
    df: dict[str, int] = Counter()
    for toks in _tokenized_chunks:
        for t in set(toks):
            df[t] += 1

    for term, freq in df.items():
        _idf[term] = math.log((_doc_count - freq + 0.5) / (freq + 0.5) + 1.0)

    logger.info('BM25 索引构建完成: %d chunks, %d terms', _doc_count, len(_idf))


def retrieve(query: str, top_k: int = 3) -> list[dict]:
    """BM25 检索，返回 top_k 个 chunk。"""
    return [chunk for chunk, _score in retrieve_with_scores(query, top_k)]


def retrieve_with_scores(query: str, top_k: int = 3) -> list[tuple[dict, float]]:
    """BM25 检索，返回 (chunk, score) 列表，供 RRF 使用。"""
    if not _chunks:
        return []

    query_tokens = character_ngrams(query)
    if not query_tokens:
        return []

    scores = []
    for i, doc_freq in enumerate(_doc_freqs):
        doc_len = len(_tokenized_chunks[i])
        score = 0.0
        for qt in query_tokens:
            if qt not in doc_freq:
                continue
            tf = doc_freq[qt]
            idf = _idf.get(qt, 0.0)
            tf_norm = (tf * (_K1 + 1)) / (tf + _K1 * (1 - _B + _B * doc_len / _avgdl))
            score += idf * tf_norm
        scores.append((score, i))

    scores.sort(key=lambda x: -x[0])

    results = []
    for score, idx in scores[:top_k]:
        if score <= 0:
            break
        chunk = _chunks[idx]
        results.append((
            {
                'id': chunk['id'],
                'domain': chunk['domain'],
                'source_file': chunk['source_file'],
                'chunk_index': chunk['chunk_index'],
                'content': chunk['content'],
            },
            score,
        ))

    return results


# 模块加载时构建索引
_build_index()
