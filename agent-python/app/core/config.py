import logging
import os
from collections.abc import Mapping

from dotenv import load_dotenv

load_dotenv(override=True)


def _load_rag_gate_settings(environ: Mapping[str, str]) -> tuple[str, float, float, float]:
    """加载并校验实验性检索 Gate 配置；仅允许 off / shadow。"""
    mode = environ.get('RAG_GATE_MODE', 'off').strip().lower()
    if mode not in {'off', 'shadow', 'enforce'}:
        raise ValueError(
            f'RAG_GATE_MODE={mode!r} 非法，允许值为 off|shadow|enforce'
        )
    if mode == 'enforce':
        raise ValueError('RAG_GATE_MODE=enforce 尚未开放；精简 V1 仅允许 off 或 shadow')

    try:
        vector_strong = float(environ.get('RAG_VECTOR_STRONG_THRESHOLD', '0.65'))
        vector_weak = float(environ.get('RAG_VECTOR_WEAK_THRESHOLD', '0.61'))
        bm25_weak = float(environ.get('RAG_BM25_WEAK_THRESHOLD', '2.10'))
    except ValueError as exc:
        raise ValueError(f'RAG 门控阈值必须是数字: {exc}') from exc

    if not 0.0 <= vector_weak <= 1.0:
        raise ValueError('RAG_VECTOR_WEAK_THRESHOLD 必须处于 [0, 1]')
    if not 0.0 <= vector_strong <= 1.0:
        raise ValueError('RAG_VECTOR_STRONG_THRESHOLD 必须处于 [0, 1]')
    if vector_strong < vector_weak:
        raise ValueError('RAG_VECTOR_STRONG_THRESHOLD 必须大于或等于 RAG_VECTOR_WEAK_THRESHOLD')
    if not 0.0 <= bm25_weak <= 1000.0:
        raise ValueError('RAG_BM25_WEAK_THRESHOLD 必须处于 [0, 1000]')

    return mode, vector_strong, vector_weak, bm25_weak

# Logger
logger = logging.getLogger('agent')
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')

# DeepSeek env
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY')
DEEPSEEK_BASE_URL = os.getenv('DEEPSEEK_BASE_URL')
DEEPSEEK_MODEL = os.getenv('DEEPSEEK_MODEL')
DEEPSEEK_TEMPERATURE = float(os.getenv('DEEPSEEK_TEMPERATURE', '0'))

if not DEEPSEEK_API_KEY:
    logger.warning('环境变量 DEEPSEEK_API_KEY 未配置，LLM 调用将不可用（retrieval eval 仍可运行）')

# Paths (project root = enterprise-ai-copilot/)
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))          # app/core/
PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, '..', '..', '..'))
CHUNKS_FILE = os.path.join(PROJECT_ROOT, 'data', 'processed', 'chunks.json')
FAISS_INDEX_FILE = os.path.join(PROJECT_ROOT, 'data', 'processed', 'faiss.index')
FAISS_META_FILE = os.path.join(PROJECT_ROOT, 'data', 'processed', 'faiss_metadata.json')

# Cross Encoder Re-ranker
RERANK_MODEL = os.getenv('RERANK_MODEL', 'BAAI/bge-reranker-base')
RERANK_CANDIDATE_K = int(os.getenv('RERANK_CANDIDATE_K', '10'))

# Query Rewrite
REWRITE_MODE = os.getenv('REWRITE_MODE', 'none')  # none / rule

# LLM Timeout (seconds)
LLM_TIMEOUT = int(os.getenv('LLM_TIMEOUT', '30'))

# Input Validation
MAX_MESSAGE_LENGTH = int(os.getenv('MAX_MESSAGE_LENGTH', '2000'))

# Experimental retrieval relevance gate (off by default; enforce is prohibited).
# These thresholds only reproduce the first Shadow experiment and are not
# validated deployment parameters. See docs/rag-retrieval-gate-experiment.md.
(
    RAG_GATE_MODE,
    RAG_VECTOR_STRONG_THRESHOLD,
    RAG_VECTOR_WEAK_THRESHOLD,
    RAG_BM25_WEAK_THRESHOLD,
) = _load_rag_gate_settings(os.environ)

# Constants
TOP_K = 3
