import json
import os
import re

from app.core.config import CHUNKS_FILE, logger

_STOP_WORDS = frozenset({
    '的', '了', '在', '是', '我', '有', '和', '就', '不', '人', '都', '一',
    '一个', '可以', '我们', '你们', '他们', '它们', '这个', '那个', '什么',
    '怎么', '如何', '哪', '哪儿', '哪里', '谁', '为什么', '哪些', '多少',
    '请', '问', '请问', '帮', '帮忙', '想', '要', '需要', '应该', '能',
    '能够', '会', '可能', '好', '吗', '吧', '呢', '啊', '哦', '嗯',
    '对', '对于', '关于', '把', '被', '让', '给', '跟', '与', '以',
    '从', '到', '去', '来', '上', '下', '大', '小', '多', '少', '很',
    '太', '非常', '比较', '也', '还', '又', '再', '才', '刚', '已经',
    '正在', '着', '过', '了', '呢', '吧', '吗', '啊', '嗯',
})

_chunks: list[dict] = []


def _load_chunks():
    """启动时加载 chunks.json，失败时仅记录日志，不影响服务启动。"""
    global _chunks
    if not os.path.isfile(CHUNKS_FILE):
        logger.warning('chunks.json 不存在，检索功能不可用: %s', CHUNKS_FILE)
        return
    with open(CHUNKS_FILE, 'r', encoding='utf-8') as f:
        _chunks = json.load(f)
    logger.info('知识库加载完成: %d 个 chunk', len(_chunks))


def _extract_keywords(query: str) -> list[str]:
    """将用户问题拆分为关键词列表（2-gram + 3-gram，过滤停用词）。"""
    tokens = re.split(r'[，。！？、；：""''（）【】《》\s,\.!?;:()\[\]{}<>/\\|]+', query)
    tokens = [t.strip() for t in tokens if t.strip()]

    keywords = []
    for token in tokens:
        if token in _STOP_WORDS or len(token) < 2:
            continue
        if len(token) >= 4:
            keywords.append(token)
        for i in range(len(token) - 1):
            gram = token[i:i + 2]
            if gram not in _STOP_WORDS:
                keywords.append(gram)
        if len(token) >= 3:
            for i in range(len(token) - 2):
                gram = token[i:i + 3]
                if gram not in _STOP_WORDS:
                    keywords.append(gram)

    seen = set()
    unique = []
    for kw in keywords:
        if kw not in seen:
            seen.add(kw)
            unique.append(kw)
    return unique


def _score_chunk(content: str, keywords: list[str]) -> int:
    """计算一个 chunk 内容的关键词匹配得分。"""
    score = 0
    for kw in keywords:
        count = content.count(kw)
        if count > 0:
            score += count * (1 + len(kw) * 0.1)
    return int(score)


def retrieve(query: str, top_k: int = 3) -> list[dict]:
    """从全局 _chunks 中检索与 query 最相关的 top_k 个 chunk。"""
    if not _chunks:
        return []

    keywords = _extract_keywords(query)
    scored = []
    for chunk in _chunks:
        score = _score_chunk(chunk['content'], keywords)
        if score > 0:
            scored.append((score, chunk))

    scored.sort(key=lambda x: (-x[0], _chunks.index(x[1])))
    return [chunk for _, chunk in scored[:top_k]]


# 模块加载时初始化知识库
_load_chunks()
