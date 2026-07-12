"""
embedding_runtime.py — 统一 Embedding 模型加载与编码接口

支持三种推理后端，通过环境变量配置：
    EMBEDDING_MODEL_NAME   模型名称（默认 BAAI/bge-small-zh-v1.5）
    EMBEDDING_BACKEND      推理后端：torch | onnx_st | onnx_direct（默认 torch）
    EMBEDDING_MODEL_PATH   本地模型目录（onnx_st/onnx_direct 可选/必须）
    EMBEDDING_ONNX_FILE    ONNX 文件名（默认 onnx/model.onnx）
    EMBEDDING_PROVIDER     ONNX Provider（默认 CPUExecutionProvider）

后端说明：
    torch        — SentenceTransformer + PyTorch（默认，兼容性最好）
    onnx_st      — SentenceTransformer ONNX 后端（仍加载 torch，不节省内存）
    onnx_direct  — Direct ONNX Runtime（不加载 torch，生产环境推荐）
"""

import logging
import os
import time

import numpy as np

logger = logging.getLogger('agent.embedding')

# ── 配置 ─────────────────────────────────────────────────────────
ALLOWED_BACKENDS = {'torch', 'onnx_st', 'onnx_direct'}

EMBEDDING_MODEL_NAME = os.getenv('EMBEDDING_MODEL_NAME', 'BAAI/bge-small-zh-v1.5')
EMBEDDING_BACKEND = os.getenv('EMBEDDING_BACKEND', 'torch').strip().lower()
EMBEDDING_MODEL_PATH = os.getenv('EMBEDDING_MODEL_PATH', '').strip()
EMBEDDING_ONNX_FILE = os.getenv('EMBEDDING_ONNX_FILE', 'onnx/model.onnx').strip()
EMBEDDING_PROVIDER = os.getenv('EMBEDDING_PROVIDER', 'CPUExecutionProvider').strip()

# ── 模块级缓存 ───────────────────────────────────────────────────
_model = None
_model_loaded = False


def _validate_config() -> None:
    """校验配置合法性，非法时立即报错。"""
    if EMBEDDING_BACKEND not in ALLOWED_BACKENDS:
        raise ValueError(
            f'EMBEDDING_BACKEND={EMBEDDING_BACKEND!r} 非法，允许值: {sorted(ALLOWED_BACKENDS)}'
        )

    if EMBEDDING_BACKEND in ('onnx_st', 'onnx_direct'):
        if EMBEDDING_PROVIDER != 'CPUExecutionProvider':
            raise ValueError(
                f'EMBEDDING_PROVIDER={EMBEDDING_PROVIDER!r} 非法，ONNX 模式必须使用 CPUExecutionProvider'
            )

        if EMBEDDING_MODEL_PATH:
            model_dir = EMBEDDING_MODEL_PATH
            if not os.path.isdir(model_dir):
                raise FileNotFoundError(
                    f'EMBEDDING_MODEL_PATH 目录不存在: {model_dir}'
                )
            onnx_file = os.path.join(model_dir, EMBEDDING_ONNX_FILE)
            if not os.path.isfile(onnx_file):
                raise FileNotFoundError(
                    f'ONNX 模型文件不存在: {onnx_file}'
                )

    if EMBEDDING_BACKEND == 'onnx_direct' and not EMBEDDING_MODEL_PATH:
        raise ValueError('onnx_direct 模式必须配置 EMBEDDING_MODEL_PATH')


def load_model():
    """加载并缓存 Embedding 模型。

    Returns:
        模型实例（SentenceTransformer 或 None）
    """
    global _model, _model_loaded

    if _model_loaded:
        return _model

    _validate_config()

    t0 = time.time()

    if EMBEDDING_BACKEND == 'onnx_direct':
        # Direct ONNX: 不加载 torch/sentence_transformers
        from app.retrieval.direct_onnx_embedding import load_model as direct_load
        direct_load()
        _model = None  # Direct 模式不返回模型对象
    elif EMBEDDING_BACKEND == 'onnx_st':
        # SentenceTransformer ONNX 后端
        from sentence_transformers import SentenceTransformer
        model_source = EMBEDDING_MODEL_PATH if EMBEDDING_MODEL_PATH else EMBEDDING_MODEL_NAME
        _model = SentenceTransformer(
            model_source,
            backend='onnx',
            model_kwargs={
                'provider': EMBEDDING_PROVIDER,
                'file_name': EMBEDDING_ONNX_FILE,
                'export': False,
            },
        )
    else:
        # Torch 后端
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(EMBEDDING_MODEL_NAME)

    elapsed = time.time() - t0

    logger.info(
        'Embedding 模型加载完成: model=%s, backend=%s, provider=%s, 耗时=%.1fs',
        EMBEDDING_MODEL_NAME,
        EMBEDDING_BACKEND,
        EMBEDDING_PROVIDER if 'onnx' in EMBEDDING_BACKEND else 'N/A',
        elapsed,
    )

    _model_loaded = True
    return _model


def encode(texts: str | list[str], normalize: bool = True) -> np.ndarray:
    """编码文本为归一化向量。

    Args:
        texts: 单条字符串或字符串列表
        normalize: 是否 L2 归一化（默认 True）

    Returns:
        numpy.ndarray，单条时 shape=(dim,)，批量时 shape=(n, dim)
    """
    if EMBEDDING_BACKEND == 'onnx_direct':
        # Direct ONNX 路径
        from app.retrieval.direct_onnx_embedding import encode as direct_encode
        return direct_encode(texts, normalize=normalize)

    # SentenceTransformer 路径（torch / onnx_st）
    model = load_model()
    embeddings = model.encode(texts, normalize_embeddings=normalize)

    # 确保返回 numpy.float32
    if hasattr(embeddings, 'numpy'):
        embeddings = embeddings.numpy()
    embeddings = np.asarray(embeddings, dtype=np.float32)

    return embeddings
