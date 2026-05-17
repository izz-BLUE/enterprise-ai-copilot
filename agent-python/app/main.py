import json
import logging
import os
import re
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI
from openai import OpenAI
from pydantic import BaseModel

load_dotenv(override=True)

logger = logging.getLogger('agent')
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')

DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY')
DEEPSEEK_BASE_URL = os.getenv('DEEPSEEK_BASE_URL')
DEEPSEEK_MODEL = os.getenv('DEEPSEEK_MODEL')

SYSTEM_PROMPT = '你是一个企业 AI Copilot 助手。\n你的职责是帮助企业员工理解制度、流程、知识库内容。\n回答要求：\n1. 回答要简洁、准确。\n2. 不要编造没有依据的信息。\n3. 如果不确定，请明确说明「当前信息不足，无法确定」。\n4. 优先使用分点说明。\n5. 如果用户询问具体制度、流程、规定，但当前没有提供知识库内容或依据，你必须提醒“当前未接入企业制度知识库，以下仅为通用建议，不能作为正式制度依据”。'

if not DEEPSEEK_API_KEY:
    raise RuntimeError('环境变量 DEEPSEEK_API_KEY 未配置，请在 .env 文件中设置')

client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)

app = FastAPI(title='Agent Python Service')

# ── 检索模块 ──────────────────────────────────────────────────────

_CHUNKS = []            # 启动时加载，全局持有
_TOP_K = 3

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


def _load_chunks():
    """启动时加载 chunks.json，失败时仅记录日志，不影响服务启动。"""
    global _CHUNKS
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.abspath(os.path.join(script_dir, '..', '..'))
    chunks_file = os.path.join(project_root, 'data', 'processed', 'chunks.json')
    if not os.path.isfile(chunks_file):
        logger.warning('chunks.json 不存在，检索功能不可用: %s', chunks_file)
        return
    with open(chunks_file, 'r', encoding='utf-8') as f:
        _CHUNKS = json.load(f)
    logger.info('知识库加载完成: %d 个 chunk', len(_CHUNKS))


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


def retrieve(query: str, top_k: int = _TOP_K) -> list[dict]:
    """从全局 _CHUNKS 中检索与 query 最相关的 top_k 个 chunk。"""
    if not _CHUNKS:
        return []

    keywords = _extract_keywords(query)
    scored = []
    for chunk in _CHUNKS:
        score = _score_chunk(chunk['content'], keywords)
        if score > 0:
            scored.append((score, chunk))

    scored.sort(key=lambda x: (-x[0], _CHUNKS.index(x[1])))
    return [chunk for _, chunk in scored[:top_k]]


def build_rag_prompt(query: str, chunks: list[dict]) -> str:
    """将检索结果拼接为带上下文的 Prompt。"""
    if not chunks:
        return (
            '当前知识库未检索到相关内容。\n'
            '请明确说明“当前知识库暂无相关信息”，不要编造具体制度或流程。\n'
            f'用户问题：{query}'
        )

    knowledge_sections = []
    for i, chunk in enumerate(chunks, 1):
        knowledge_sections.append(
            f'【知识{i}】来源：{chunk["domain"]}/{chunk["source_file"]}\n'
            f'内容：{chunk["content"]}'
        )

    return (
        '你是企业内部 AI 助手。\n'
        '\n'
        '以下是企业知识库内容：\n'
        '\n'
        f'{"".join(knowledge_sections)}'
        '\n'
        '请基于以上知识回答用户问题。\n'
        '如果知识库中没有明确答案，请明确说明"当前知识库暂无相关信息"，不要编造。\n'
        '\n'
        f'用户问题：{query}'
    )


# 启动时加载知识库
_load_chunks()


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    answer: str
    model: str
    traceId: str
    success: bool


@app.get('/agent/health')
def health():
    return {'service': 'agent-python', 'status': 'UP'}


@app.post('/agent/chat')
def chat(request: ChatRequest) -> ChatResponse:

    try:
        # 1. 检索
        chunks = retrieve(request.message)
        logger.info('用户问题: %s | 命中 chunk: %d', request.message, len(chunks))
        for c in chunks:
            logger.info('  - %s [%s] %s', c['id'], c['domain'], c['source_file'])

        # 2. 拼接 RAG Prompt
        user_prompt = build_rag_prompt(request.message, chunks)

        # 3. 调用 LLM
        response = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {'role': 'system', 'content': SYSTEM_PROMPT},
                {'role': 'user', 'content': user_prompt},
            ],
        )
        return ChatResponse(
            answer=response.choices[0].message.content,
            model=DEEPSEEK_MODEL,
            traceId=str(uuid4()),
            success=True,
        )
    except Exception:
        logger.exception('调用 LLM 失败')
        return ChatResponse(
            answer='当前 AI 服务暂时不可用，请稍后重试。',
            model=DEEPSEEK_MODEL,
            traceId=str(uuid4()),
            success=False,
        )
