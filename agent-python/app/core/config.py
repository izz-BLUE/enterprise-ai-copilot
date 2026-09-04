import logging
import os
from collections.abc import Mapping

from dotenv import load_dotenv

load_dotenv(override=True)


def _load_phoenix_settings(
    environ: Mapping[str, str],
) -> tuple[bool, str, str, float, bool]:
    """加载 Phoenix/OpenTelemetry 配置并验证采样率。"""
    tracing = environ.get('PHOENIX_TRACING', 'false').strip().lower() == 'true'
    endpoint = environ.get(
        'PHOENIX_COLLECTOR_ENDPOINT', 'http://localhost:4317',
    ).strip()
    project_name = environ.get(
        'PHOENIX_PROJECT_NAME', 'enterprise-ai-copilot',
    ).strip()
    capture_content = (
        environ.get('PHOENIX_CAPTURE_CONTENT', 'false').strip().lower() == 'true'
    )
    try:
        sample_rate = float(environ.get('PHOENIX_SAMPLE_RATE', '1.0'))
    except ValueError as exc:
        raise ValueError('PHOENIX_SAMPLE_RATE 必须是 [0, 1] 范围内的数字') from exc
    if not 0.0 <= sample_rate <= 1.0:
        raise ValueError('PHOENIX_SAMPLE_RATE 必须处于 [0, 1]')
    if tracing and not endpoint:
        raise ValueError('PHOENIX_TRACING=true 时 PHOENIX_COLLECTOR_ENDPOINT 不能为空')
    if tracing and not project_name:
        raise ValueError('PHOENIX_TRACING=true 时 PHOENIX_PROJECT_NAME 不能为空')
    return tracing, endpoint, project_name, sample_rate, capture_content

# 日志器
logger = logging.getLogger('agent')
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')

# DeepSeek 环境变量
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY')
DEEPSEEK_BASE_URL = os.getenv('DEEPSEEK_BASE_URL')
DEEPSEEK_MODEL = os.getenv('DEEPSEEK_MODEL')
DEEPSEEK_TEMPERATURE = float(os.getenv('DEEPSEEK_TEMPERATURE', '0'))

if not DEEPSEEK_API_KEY:
    logger.warning('环境变量 DEEPSEEK_API_KEY 未配置，LLM 调用将不可用（retrieval eval 仍可运行）')

# 路径（项目根目录 = enterprise-ai-copilot/）
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))          # app/core/
PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, '..', '..', '..'))
CHUNKS_FILE = os.path.join(PROJECT_ROOT, 'data', 'processed', 'chunks.json')
FAISS_INDEX_FILE = os.path.join(PROJECT_ROOT, 'data', 'processed', 'faiss.index')
FAISS_META_FILE = os.path.join(PROJECT_ROOT, 'data', 'processed', 'faiss_metadata.json')

# Cross Encoder 重排序器
RERANK_MODEL = os.getenv('RERANK_MODEL', 'BAAI/bge-reranker-base')
RERANK_CANDIDATE_K = int(os.getenv('RERANK_CANDIDATE_K', '10'))

# LLM 超时（秒）
LLM_TIMEOUT = int(os.getenv('LLM_TIMEOUT', '30'))
LLM_MAX_RETRIES = int(os.getenv('LLM_MAX_RETRIES', '0'))
LLM_MAX_OUTPUT_TOKENS = int(os.getenv('LLM_MAX_OUTPUT_TOKENS', '1024'))
if LLM_TIMEOUT < 1:
    raise ValueError('LLM_TIMEOUT 必须大于等于 1')
if not 0 <= LLM_MAX_RETRIES <= 2:
    raise ValueError('LLM_MAX_RETRIES 必须处于 [0, 2]')
if not 64 <= LLM_MAX_OUTPUT_TOKENS <= 8192:
    raise ValueError('LLM_MAX_OUTPUT_TOKENS 必须处于 [64, 8192]')

# Phase B：Shadow Routing 默认关闭；开启后只增加一次旁路 Planner 调用，
# 不改变正式 Planner / Tool Executor 的业务路径。
PLANNER_SHADOW_ROUTING_ENABLED = (
    os.getenv('PLANNER_SHADOW_ROUTING_ENABLED', 'false').strip().lower() == 'true'
)

# Phoenix/OpenTelemetry Observability（默认关闭）。启用时统一采用 BatchSpanProcessor，
# Collector 故障不进入业务异常路径；默认不采集 Prompt、用户输入和模型输出正文。
(
    PHOENIX_TRACING,
    PHOENIX_COLLECTOR_ENDPOINT,
    PHOENIX_PROJECT_NAME,
    PHOENIX_SAMPLE_RATE,
    PHOENIX_CAPTURE_CONTENT,
) = _load_phoenix_settings(os.environ)

# AI 端点的有界并发。保护单 worker Demo，避免接纳超出小型主机承载能力的
# 检索/LLM 工作量。
AI_MAX_CONCURRENT_REQUESTS = int(os.getenv('AI_MAX_CONCURRENT_REQUESTS', '3'))
AI_QUEUE_TIMEOUT_MS = int(os.getenv('AI_QUEUE_TIMEOUT_MS', '500'))

if AI_MAX_CONCURRENT_REQUESTS < 1:
    raise ValueError('AI_MAX_CONCURRENT_REQUESTS 必须大于等于 1')
if AI_QUEUE_TIMEOUT_MS < 1:
    raise ValueError('AI_QUEUE_TIMEOUT_MS 必须大于等于 1')

# LangGraph 执行快照：生产运行固定使用 PostgreSQL，DSN 缺失即拒绝启动。
LANGGRAPH_CHECKPOINT_DSN = os.getenv('LANGGRAPH_CHECKPOINT_DSN', '').strip()
if not LANGGRAPH_CHECKPOINT_DSN:
    raise ValueError('LANGGRAPH_CHECKPOINT_DSN 不能为空')
try:
    LANGGRAPH_CHECKPOINT_CONNECT_TIMEOUT_SECONDS = int(
        os.getenv('LANGGRAPH_CHECKPOINT_CONNECT_TIMEOUT_SECONDS', '3')
    )
except ValueError as exc:
    raise ValueError('LANGGRAPH_CHECKPOINT_CONNECT_TIMEOUT_SECONDS 必须是整数') from exc
if not 1 <= LANGGRAPH_CHECKPOINT_CONNECT_TIMEOUT_SECONDS <= 60:
    raise ValueError('LANGGRAPH_CHECKPOINT_CONNECT_TIMEOUT_SECONDS 必须处于 [1, 60]')

# /agent/langgraph/chat 固定使用 Planner-first Graph（safety → planner ⇄ tool_executor → finalize）。
AGENT_REQUEST_TIMEOUT_SECONDS = int(os.getenv('AGENT_REQUEST_TIMEOUT_SECONDS', '40'))
if AGENT_REQUEST_TIMEOUT_SECONDS < 5:
    raise ValueError('AGENT_REQUEST_TIMEOUT_SECONDS 必须大于等于 5')

# 输入校验
MAX_MESSAGE_LENGTH = int(os.getenv('MAX_MESSAGE_LENGTH', '2000'))

# 企业只读 Tool：Python → Java 内部 HTTP 客户端配置。
# JAVA_BASE_URL: Java 后端地址，示例 http://localhost:8080；空值 = Tool 直接返回 LEAVE_READ_DISABLED。
# JAVA_INTERNAL_TOKEN: 与 Java leave.read.internal-token 完全一致；缺失时只读 Tool 不可用。
# JAVA_TIMEOUT_SECONDS: 固定请求超时，不做重试 / fallback。
JAVA_BASE_URL = os.getenv('JAVA_BASE_URL', '').strip()
JAVA_INTERNAL_TOKEN = os.getenv('JAVA_INTERNAL_TOKEN', '').strip()
try:
    JAVA_TIMEOUT_SECONDS = int(os.getenv('JAVA_TIMEOUT_SECONDS', '5'))
except ValueError:
    JAVA_TIMEOUT_SECONDS = 5
if JAVA_TIMEOUT_SECONDS < 1:
    raise ValueError('JAVA_TIMEOUT_SECONDS 必须大于等于 1')

# Scoped Conversation Memory P0：写入模式默认关闭，避免未显式配置时产生额外
# Extractor LLM 成本或 Memory 提案。模式合法性在 Runtime Hook 构造时再次校验。
MEMORY_WRITE_MODE = os.getenv('MEMORY_WRITE_MODE', 'DISABLED').strip()

# 常量
TOP_K = 3
