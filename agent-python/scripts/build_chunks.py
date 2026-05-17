#!/usr/bin/env python3
"""
build_chunks.py — 知识库文档切片脚本

从 data/{bank,hr,it} 读取 .md / .txt 文档，
按段落和字符长度切分为 chunk，输出到 data/processed/chunks.json。

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
CHUNK_MAX_CHARS = 500


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


def _split_long_paragraph(para: str) -> list[str]:
    """将过长的段落按 500 字符左右切分，优先在句末断开。"""
    chunks = []
    start = 0
    length = len(para)

    while start < length:
        end = min(start + CHUNK_MAX_CHARS, length)

        if end < length:
            # 在最后 100 字符内寻找合适的断句位置
            search_start = max(start + CHUNK_MAX_CHARS - 100, start)
            tail = para[search_start:end]
            # 优先中文句号、分号、感叹号、问号、换行
            break_at = -1
            for sep in ('。', '；', '！', '？', '\n'):
                pos = tail.rfind(sep)
                if pos > break_at:
                    break_at = pos
            if break_at >= 25:  # 至少向后推进 25 个字符
                end = search_start + break_at + 1

        chunk_text = para[start:end].strip()
        if chunk_text:
            chunks.append(chunk_text)
        start = end

    return chunks


def _split_file(text: str) -> list[str]:
    """完整切片流程：段落 → 长段落拆分。"""
    paragraphs = _split_paragraphs(text)
    chunks = []
    for para in paragraphs:
        if len(para) <= CHUNK_MAX_CHARS:
            chunks.append(para)
        else:
            chunks.extend(_split_long_paragraph(para))
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
