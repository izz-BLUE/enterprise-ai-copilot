#!/usr/bin/env python3
"""
simple_retrieval.py — 基于关键词匹配的简单检索脚本

从 data/processed/chunks.json 读取 chunk，
根据用户问题做关键词匹配，返回 top 3 相关 chunk。

用法:
    python agent-python/scripts/simple_retrieval.py "请假三天怎么走流程"
"""

import json
import os
import re
import sys

# ── 路径自动识别 ────────────────────────────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, '..', '..'))
CHUNKS_FILE = os.path.join(PROJECT_ROOT, 'data', 'processed', 'chunks.json')

TOP_K = 3

# 常见中文停用词（检索时忽略，避免噪声）
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


# ── 关键词提取 ──────────────────────────────────────────────────


def _extract_keywords(query: str) -> list[str]:
    """从用户问题中提取关键词。

    策略:
      1. 用标点/空格切分为 tokens
      2. 去掉停用词和过短的词
      3. 对每个 token 生成 2-gram 作为关键词
      4. 保留长 token（>=4 字）本身作为完整关键词
    """
    # 按标点符号和空格切分
    tokens = re.split(r'[，。！？、；：""''（）【】《》\s,\.!?;:()\[\]{}<>/\\|]+', query)
    tokens = [t.strip() for t in tokens if t.strip()]

    keywords = []
    for token in tokens:
        if token in _STOP_WORDS or len(token) < 2:
            continue
        # 长度 >= 4 的 token 整体作为关键词
        if len(token) >= 4:
            keywords.append(token)
        # 滑动 2-gram 提取子串
        for i in range(len(token) - 1):
            gram = token[i:i + 2]
            if gram not in _STOP_WORDS:
                keywords.append(gram)
        # 滑动 3-gram（对长词更精确）
        if len(token) >= 3:
            for i in range(len(token) - 2):
                gram = token[i:i + 3]
                if gram not in _STOP_WORDS:
                    keywords.append(gram)
    # 去重，保留顺序
    seen = set()
    unique = []
    for kw in keywords:
        if kw not in seen:
            seen.add(kw)
            unique.append(kw)
    return unique


def _score_chunk(chunk: dict, keywords: list[str]) -> int:
    """计算一个 chunk 的关键词匹配得分。"""
    content = chunk['content']
    score = 0
    for kw in keywords:
        # 统计关键词在 content 中出现次数
        count = content.count(kw)
        if count > 0:
            # 更长的关键词匹配加分更多
            score += count * (1 + len(kw) * 0.1)
    return int(score)


# ── 主入口 ────────────────────────────────────────────────────────


def main():
    if not os.path.isfile(CHUNKS_FILE):
        print(f"错误: 未找到 chunks.json，请先运行 build_chunks.py")
        print(f"  python {os.path.join('agent-python', 'scripts', 'build_chunks.py')}")
        sys.exit(1)

    if len(sys.argv) < 2:
        print("用法: python agent-python/scripts/simple_retrieval.py \"<问题>\"")
        sys.exit(1)

    query = sys.argv[1]

    with open(CHUNKS_FILE, 'r', encoding='utf-8') as f:
        chunks = json.load(f)

    if not chunks:
        print("chunks.json 为空，请先运行 build_chunks.py 生成 chunk。")
        sys.exit(1)

    keywords = _extract_keywords(query)
    print(f"问题: {query}")
    print(f"关键词: {keywords}\n")

    # 计算每个 chunk 的得分
    scored = []
    for chunk in chunks:
        score = _score_chunk(chunk, keywords)
        if score > 0:
            scored.append((score, chunk))

    # 按得分降序 → chunk 原始顺序升序
    scored.sort(key=lambda x: (-x[0], chunks.index(x[1])))

    results = scored[:TOP_K]

    if not results:
        print("未找到匹配结果。")
        return

    print(f"找到 {len(results)} 个匹配结果:\n")
    for i, (score, chunk) in enumerate(results, 1):
        print(f"[{i}] score={score}")
        print(f"    id:          {chunk['id']}")
        print(f"    domain:      {chunk['domain']}")
        print(f"    source_file: {chunk['source_file']}")
        print(f"    content:     {chunk['content'][:100]}{'...' if len(chunk['content']) > 100 else ''}")
        print()


if __name__ == '__main__':
    main()
