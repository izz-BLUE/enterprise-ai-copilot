import logging
import os

from dotenv import load_dotenv

load_dotenv(override=True)

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

# Constants
TOP_K = 3
