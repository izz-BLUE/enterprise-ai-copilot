"""
direct_onnx_embedding.py — 不依赖 Torch 的 Direct ONNX Runtime Embedding

使用 onnxruntime.InferenceSession 直接加载 ONNX 模型，
运行时不导入 torch、sentence_transformers 或 optimum。

支持的环境变量：
    EMBEDDING_MODEL_PATH   本地 ONNX 模型目录（必须）
    EMBEDDING_ONNX_FILE    ONNX 文件名（默认 onnx/model.onnx）
    EMBEDDING_PROVIDER     ONNX Provider（默认 CPUExecutionProvider）
"""

import json
import logging
import os
import time

import numpy as np
import onnxruntime as ort

logger = logging.getLogger('agent.embedding.direct')

# ── 配置 ─────────────────────────────────────────────────────────
EMBEDDING_MODEL_PATH = os.getenv('EMBEDDING_MODEL_PATH', '').strip()
EMBEDDING_ONNX_FILE = os.getenv('EMBEDDING_ONNX_FILE', 'onnx/model.onnx').strip()
EMBEDDING_PROVIDER = os.getenv('EMBEDDING_PROVIDER', 'CPUExecutionProvider').strip()

EXPECTED_DIM = 512

# ── 模块级缓存 ───────────────────────────────────────────────────
_session = None
_tokenizer = None
_pooling_config = None
_model_loaded = False


def _load_config(model_dir: str) -> dict:
    """加载模型配置。"""
    config_path = os.path.join(model_dir, 'config.json')
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def _load_pooling_config(model_dir: str) -> dict:
    """加载 pooling 配置。"""
    pooling_path = os.path.join(model_dir, '1_Pooling', 'config.json')
    with open(pooling_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def _load_tokenizer(model_dir: str):
    """加载 tokenizer（使用 transformers 的 BertTokenizerFast）。"""
    from tokenizers import Tokenizer
    tokenizer_path = os.path.join(model_dir, 'tokenizer.json')
    tokenizer = Tokenizer.from_file(tokenizer_path)

    # 读取 tokenizer 配置获取特殊 token
    config_path = os.path.join(model_dir, 'tokenizer_config.json')
    with open(config_path, 'r', encoding='utf-8') as f:
        tok_config = json.load(f)

    # 设置最大长度
    max_length = tok_config.get('model_max_length', 512)

    # 从 added_tokens_decoder 获取特殊 token ID
    added_tokens = tok_config.get('added_tokens_decoder', {})
    cls_token_id = None
    sep_token_id = None
    pad_token_id = None

    for tid, info in added_tokens.items():
        if info.get('content') == '[CLS]':
            cls_token_id = int(tid)
        elif info.get('content') == '[SEP]':
            sep_token_id = int(tid)
        elif info.get('content') == '[PAD]':
            pad_token_id = int(tid)

    return {
        'tokenizer': tokenizer,
        'max_length': max_length,
        'cls_token_id': cls_token_id or 101,
        'sep_token_id': sep_token_id or 102,
        'pad_token_id': pad_token_id or 0,
    }


def _validate_config() -> None:
    """校验配置。"""
    if not EMBEDDING_MODEL_PATH:
        raise ValueError('EMBEDDING_MODEL_PATH 未配置，Direct ONNX 模式必须指定本地模型目录')

    if not os.path.isdir(EMBEDDING_MODEL_PATH):
        raise FileNotFoundError(f'EMBEDDING_MODEL_PATH 目录不存在: {EMBEDDING_MODEL_PATH}')

    onnx_file = os.path.join(EMBEDDING_MODEL_PATH, EMBEDDING_ONNX_FILE)
    if not os.path.isfile(onnx_file):
        raise FileNotFoundError(f'ONNX 模型文件不存在: {onnx_file}')

    if EMBEDDING_PROVIDER != 'CPUExecutionProvider':
        raise ValueError(f'EMBEDDING_PROVIDER={EMBEDDING_PROVIDER!r} 非法，必须使用 CPUExecutionProvider')


def load_model() -> None:
    """加载 ONNX Session、Tokenizer 和配置。"""
    global _session, _tokenizer, _pooling_config, _model_loaded

    if _model_loaded:
        return

    _validate_config()

    model_dir = EMBEDDING_MODEL_PATH
    onnx_file = os.path.join(model_dir, EMBEDDING_ONNX_FILE)

    # 1. 加载 ONNX Session
    t0 = time.time()
    _session = ort.InferenceSession(onnx_file, providers=[EMBEDDING_PROVIDER])
    load_time = time.time() - t0

    # 验证输入输出
    input_names = {inp.name for inp in _session.get_inputs()}
    expected_inputs = {'input_ids', 'attention_mask'}
    if not expected_inputs.issubset(input_names):
        raise RuntimeError(f'ONNX Session 输入不匹配: 期望 {expected_inputs}, 实际 {input_names}')

    output_names = {out.name for out in _session.get_outputs()}
    if 'last_hidden_state' not in output_names:
        raise RuntimeError(f'ONNX Session 输出不包含 last_hidden_state: {output_names}')

    # 2. 加载 Tokenizer
    _tokenizer = _load_tokenizer(model_dir)

    # 3. 加载 Pooling 配置
    _pooling_config = _load_pooling_config(model_dir)

    _model_loaded = True

    logger.info(
        'Direct ONNX 模型加载完成: path=%s, provider=%s, 耗时=%.1fs',
        model_dir, EMBEDDING_PROVIDER, load_time,
    )


def _tokenize(texts: list[str]) -> dict[str, np.ndarray]:
    """对文本进行 tokenize。"""
    tok_info = _tokenizer
    tokenizer = tok_info['tokenizer']
    max_length = tok_info['max_length']
    pad_id = tok_info['pad_token_id']

    # 批量编码
    encoded = tokenizer.encode_batch(texts)

    # 获取最大长度
    max_len = min(max(len(e.ids) for e in encoded), max_length)

    # 构造输入
    batch_size = len(texts)
    input_ids = np.full((batch_size, max_len), pad_id, dtype=np.int64)
    attention_mask = np.zeros((batch_size, max_len), dtype=np.int64)
    token_type_ids = np.zeros((batch_size, max_len), dtype=np.int64)

    for i, enc in enumerate(encoded):
        ids = enc.ids[:max_len]
        input_ids[i, :len(ids)] = ids
        attention_mask[i, :len(ids)] = 1

    return {
        'input_ids': input_ids,
        'attention_mask': attention_mask,
        'token_type_ids': token_type_ids,
    }


def _pool(last_hidden_state: np.ndarray, attention_mask: np.ndarray) -> np.ndarray:
    """应用 pooling。根据配置使用 CLS 或 Mean pooling。"""
    pooling = _pooling_config
    mode = pooling.get('pooling_mode', 'cls')

    if mode == 'cls':
        # CLS pooling: 取每个序列的第一个 token
        return last_hidden_state[:, 0, :]
    elif mode == 'mean':
        # Mean pooling: 对非 padding token 求平均
        mask_expanded = np.expand_dims(attention_mask, axis=-1).astype(np.float32)
        sum_embeddings = np.sum(last_hidden_state * mask_expanded, axis=1)
        sum_mask = np.clip(np.sum(mask_expanded, axis=1), 1e-9, None)
        return sum_embeddings / sum_mask
    elif mode == 'max':
        # Max pooling（最大池化）
        mask_expanded = np.expand_dims(attention_mask, axis=-1).astype(np.float32)
        masked = last_hidden_state * mask_expanded + (1 - mask_expanded) * (-1e9)
        return np.max(masked, axis=1)
    else:
        raise ValueError(f'不支持的 pooling 模式: {mode}')


def _normalize(embeddings: np.ndarray) -> np.ndarray:
    """L2 归一化。"""
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms = np.clip(norms, 1e-12, None)
    return embeddings / norms


def encode(texts: str | list[str], normalize: bool = True) -> np.ndarray:
    """编码文本为向量。

    参数：
        texts: 单条字符串或字符串列表
        normalize: 是否 L2 归一化（默认 True）

    返回：
        numpy.ndarray，单条时 shape=(dim,)，批量时 shape=(n, dim)
    """
    load_model()

    single = isinstance(texts, str)
    if single:
        texts = [texts]

    # Tokenize（分词）
    inputs = _tokenize(texts)

    # ONNX 推理
    outputs = _session.run(None, inputs)
    last_hidden_state = outputs[0].astype(np.float32)

    # Pooling（池化）
    embeddings = _pool(last_hidden_state, inputs['attention_mask'])

    # 验证维度
    if embeddings.shape[1] != EXPECTED_DIM:
        raise RuntimeError(f'输出维度异常: 期望 {EXPECTED_DIM}, 实际 {embeddings.shape[1]}')

    # Normalize（归一化）
    if normalize:
        embeddings = _normalize(embeddings)

    # 验证无 NaN/Inf
    if np.isnan(embeddings).any() or np.isinf(embeddings).any():
        raise RuntimeError('输出包含 NaN 或 Inf')

    if single:
        return embeddings[0]
    return embeddings
