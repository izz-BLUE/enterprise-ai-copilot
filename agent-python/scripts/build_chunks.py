#!/usr/bin/env python3
"""
build_chunks.py — 知识库文档切片脚本

从 data/{bank,hr,it} 读取 .md / .txt 文档，
按段落和字符长度切分为 chunk，支持相邻 chunk 重叠，输出到 data/processed/chunks.json。

参数:
    CHUNK_SIZE    = 500   每个 chunk 的目标字符数
    CHUNK_OVERLAP = 100   相邻 chunk 之间的重叠字符数

用法:
    python agent-python/scripts/build_chunks.py
"""

import json
import os
import re

# ── 路径自动识别 ────────────────────────────────────────────────
# 脚本位于 agent-python/scripts/build_chunks.py
# 项目根目录 = 脚本所在目录的父目录的父目录
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, '..', '..'))

DOMAINS = ['bank', 'hr', 'it']
CHUNK_SIZE = 500
CHUNK_OVERLAP = 100


def _input_dir(domain: str) -> str:
    return os.path.join(PROJECT_ROOT, 'data', domain)


def _output_dir() -> str:
    return os.path.join(PROJECT_ROOT, 'data', 'processed')


def _output_file() -> str:
    return os.path.join(_output_dir(), 'chunks.json')


# ── 切片逻辑 ──────────────────────────────────────────────────────


def _split_paragraphs(text: str) -> list[str]:
    """按空行拆分为段落，忽略空白段落。"""
    raw = re.split(r'\n\s*\n', text.strip())
    return [p.strip() for p in raw if p.strip()]


def _split_long_paragraph(para: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    """将过长的段落按 chunk_size 切分，相邻 chunk 重叠 chunk_overlap 字符。

    重叠策略：
      chunk1: [0          ~ chunk_size)
      chunk2: [chunk_size - overlap  ~ chunk_size * 2 - overlap)
      chunk3: [chunk_size * 2 - overlap * 2  ~ ...)
    """
    step = chunk_size - chunk_overlap  # 每次实际前进的字符数
    chunks = []
    start = 0
    length = len(para)

    while start < length:
        remaining = length - start

        # 剩余不足一步时，直接取到末尾并结束
        if remaining <= step:
            chunks.append(para[start:].strip())
            break

        end = min(start + chunk_size, length)

        # 在最后 chunk_overlap 字符范围内寻找句尾（句号、分号、感叹号、问号、换行）
        search_start = end - chunk_overlap
        tail = para[search_start:end]
        best_pos = -1
        for sep in ('。', '；', '！', '？', '\n'):
            pos = tail.rfind(sep)
            if pos > best_pos:
                best_pos = pos
        MIN_ADVANCE = 25
        if best_pos >= 0 and (search_start + best_pos + 1) > start + MIN_ADVANCE:
            end = search_start + best_pos + 1

        chunks.append(para[start:end].strip())

        # 下一次起点 = 当前结尾 - overlap（实现重叠）
        start = end - chunk_overlap

    return chunks


def _split_file(text: str, chunk_size: int = CHUNK_SIZE, chunk_overlap: int = CHUNK_OVERLAP) -> list[str]:
    """完整切片流程：段落 → 长段落重叠拆分。"""
    paragraphs = _split_paragraphs(text)
    chunks = []
    for para in paragraphs:
        if len(para) <= chunk_size:
            chunks.append(para)
        else:
            chunks.extend(_split_long_paragraph(para, chunk_size, chunk_overlap))
    return chunks


# ── 文件处理 ──────────────────────────────────────────────────────


def _find_doc_files(domain: str) -> list[str]:
    """返回 domain 下所有 .md / .txt 文件（已排序）。"""
    d = _input_dir(domain)
    if not os.path.isdir(d):
        return []
    return sorted(
        f for f in os.listdir(d)
        if f.endswith(('.md', '.txt')) and os.path.isfile(os.path.join(d, f))
    )


def _process_domain(domain: str) -> list[dict]:
    """处理一个 domain 的所有文档，返回 chunk 列表。"""
    chunks = []
    for filename in _find_doc_files(domain):
        filepath = os.path.join(_input_dir(domain), filename)
        with open(filepath, 'r', encoding='utf-8') as f:
            text = f.read()

        parts = _split_file(text)
        base = filename.rsplit('.', 1)[0]

        for i, content in enumerate(parts):
            chunks.append({
                'id': f"{domain}_{base}_{i + 1:03d}",
                'domain': domain,
                'source_file': filename,
                'chunk_index': i + 1,
                'content': content,
            })

        print(f"  {domain}/{filename:25s} → {len(parts)} chunks")
    return chunks


# ── 主入口 ────────────────────────────────────────────────────────


def main():
    total_files = sum(len(_find_doc_files(d)) for d in DOMAINS)
    print(f"发现 {total_files} 个文档\n")
    print(f"chunk_size={CHUNK_SIZE}, chunk_overlap={CHUNK_OVERLAP}\n")

    all_chunks = []
    for domain in DOMAINS:
        all_chunks.extend(_process_domain(domain))

    os.makedirs(_output_dir(), exist_ok=True)
    with open(_output_file(), 'w', encoding='utf-8') as f:
        json.dump(all_chunks, f, ensure_ascii=False, indent=2)

    print(f"\n处理完成！")
    print(f"  处理文件数: {total_files}")
    print(f"  生成 chunk 数: {len(all_chunks)}")
    print(f"  输出文件: {_output_file()}")


if __name__ == '__main__':
    main()
