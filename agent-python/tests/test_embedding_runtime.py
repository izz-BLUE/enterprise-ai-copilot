#!/usr/bin/env python3
"""
test_embedding_runtime.py — Embedding Runtime 配置与行为测试

覆盖：
1. 配置默认 backend=torch
2. backend=onnx 可正确解析
3. 非法 backend 报错
4. onnx 本地模型路径不存在时报错
5. ONNX 文件不存在时报错
6. ONNX 模式不允许静默回退 torch
7. encode 输出 numpy 数组
8. 单条输出维度 512
9. 批量输出维度正确
10. normalize 后向量范数接近 1
11. faiss_retriever 公共返回结构不变
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

# 确保能导入项目模块
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, '..'))
sys.path.insert(0, PROJECT_ROOT)


class TestEmbeddingConfig(unittest.TestCase):
    """测试 Embedding Runtime 配置校验。"""

    def test_default_backend_is_torch(self):
        """默认 backend 应为 torch。"""
        with patch.dict(os.environ, {}, clear=False):
            # 移除可能存在的环境变量
            os.environ.pop('EMBEDDING_BACKEND', None)
            # 重新导入以获取默认值
            import importlib
            import app.retrieval.embedding_runtime as mod
            importlib.reload(mod)
            self.assertEqual(mod.EMBEDDING_BACKEND, 'torch')

    def test_onnx_st_backend_parsed(self):
        """backend=onnx_st 可正确解析。"""
        with patch.dict(os.environ, {'EMBEDDING_BACKEND': 'onnx_st'}, clear=False):
            import importlib
            import app.retrieval.embedding_runtime as mod
            importlib.reload(mod)
            self.assertEqual(mod.EMBEDDING_BACKEND, 'onnx_st')

    def test_onnx_direct_backend_parsed(self):
        """backend=onnx_direct 可正确解析。"""
        with patch.dict(os.environ, {'EMBEDDING_BACKEND': 'onnx_direct'}, clear=False):
            import importlib
            import app.retrieval.embedding_runtime as mod
            importlib.reload(mod)
            self.assertEqual(mod.EMBEDDING_BACKEND, 'onnx_direct')

    def test_invalid_backend_raises(self):
        """非法 backend 应立即报错。"""
        with patch.dict(os.environ, {'EMBEDDING_BACKEND': 'invalid'}, clear=False):
            import importlib
            import app.retrieval.embedding_runtime as mod
            importlib.reload(mod)
            with self.assertRaises(ValueError) as ctx:
                mod._validate_config()
            self.assertIn('非法', str(ctx.exception))

    def test_onnx_must_use_cpu_provider(self):
        """ONNX 模式必须使用 CPUExecutionProvider。"""
        with patch.dict(os.environ, {
            'EMBEDDING_BACKEND': 'onnx_st',
            'EMBEDDING_PROVIDER': 'CUDAExecutionProvider',
        }, clear=False):
            import importlib
            import app.retrieval.embedding_runtime as mod
            importlib.reload(mod)
            with self.assertRaises(ValueError) as ctx:
                mod._validate_config()
            self.assertIn('CPUExecutionProvider', str(ctx.exception))

    def test_onnx_model_path_not_exist_raises(self):
        """ONNX 模式配置了不存在的本地路径应报错。"""
        with patch.dict(os.environ, {
            'EMBEDDING_BACKEND': 'onnx_st',
            'EMBEDDING_MODEL_PATH': '/nonexistent/path',
        }, clear=False):
            import importlib
            import app.retrieval.embedding_runtime as mod
            importlib.reload(mod)
            with self.assertRaises(FileNotFoundError) as ctx:
                mod._validate_config()
            self.assertIn('不存在', str(ctx.exception))

    def test_onnx_file_not_exist_raises(self):
        """ONNX 文件不存在时应报错。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建目录但不创建 ONNX 文件
            with patch.dict(os.environ, {
                'EMBEDDING_BACKEND': 'onnx_st',
                'EMBEDDING_MODEL_PATH': tmpdir,
            }, clear=False):
                import importlib
                import app.retrieval.embedding_runtime as mod
                importlib.reload(mod)
                with self.assertRaises(FileNotFoundError) as ctx:
                    mod._validate_config()
                self.assertIn('ONNX', str(ctx.exception))

    def test_onnx_no_silent_fallback_to_torch(self):
        """ONNX 模式配置错误时不应静默回退到 torch。"""
        with patch.dict(os.environ, {
            'EMBEDDING_BACKEND': 'onnx_st',
            'EMBEDDING_MODEL_PATH': '/nonexistent/path',
        }, clear=False):
            import importlib
            import app.retrieval.embedding_runtime as mod
            importlib.reload(mod)
            # 必须抛出异常，不能静默成功
            with self.assertRaises(FileNotFoundError):
                mod._validate_config()


class TestEmbeddingEncode(unittest.TestCase):
    """测试 encode 行为（Mock 模型加载）。"""

    def _make_mock_model(self, dim=512):
        """创建 mock SentenceTransformer。"""
        mock_model = MagicMock()

        def mock_encode(texts, normalize_embeddings=False, **kwargs):
            if isinstance(texts, str):
                return np.random.randn(dim).astype(np.float32)
            return np.random.randn(len(texts), dim).astype(np.float32)

        mock_model.encode = mock_encode
        return mock_model

    def test_encode_returns_numpy(self):
        """encode 应返回 numpy 数组。"""
        import importlib
        import app.retrieval.embedding_runtime as mod
        importlib.reload(mod)

        mock_model = self._make_mock_model()
        mod._model = mock_model
        mod._model_loaded = True

        result = mod.encode('测试')
        self.assertIsInstance(result, np.ndarray)

    def test_single_encode_dim_512(self):
        """单条输出维度应为 512。"""
        import importlib
        import app.retrieval.embedding_runtime as mod
        importlib.reload(mod)

        mock_model = self._make_mock_model(dim=512)
        mod._model = mock_model
        mod._model_loaded = True

        result = mod.encode('测试')
        self.assertEqual(result.shape, (512,))

    def test_batch_encode_dim_correct(self):
        """批量输出维度应正确。"""
        import importlib
        import app.retrieval.embedding_runtime as mod
        importlib.reload(mod)

        mock_model = self._make_mock_model(dim=512)
        mod._model = mock_model
        mod._model_loaded = True

        result = mod.encode(['文本1', '文本2', '文本3'])
        self.assertEqual(result.shape, (3, 512))

    def test_normalized_vector_norm_near_1(self):
        """normalize 后向量范数应接近 1。"""
        import importlib
        import app.retrieval.embedding_runtime as mod
        importlib.reload(mod)

        # 使用真实归一化的 mock
        mock_model = MagicMock()

        def mock_encode(texts, normalize_embeddings=False, **kwargs):
            if isinstance(texts, str):
                vec = np.random.randn(512).astype(np.float32)
                if normalize_embeddings:
                    vec = vec / np.linalg.norm(vec)
                return vec
            vecs = np.random.randn(len(texts), 512).astype(np.float32)
            if normalize_embeddings:
                norms = np.linalg.norm(vecs, axis=1, keepdims=True)
                vecs = vecs / norms
            return vecs

        mock_model.encode = mock_encode
        mod._model = mock_model
        mod._model_loaded = True

        result = mod.encode('测试')
        norm = np.linalg.norm(result)
        self.assertAlmostEqual(norm, 1.0, places=5)


class TestFaissRetrieverStructure(unittest.TestCase):
    """测试 faiss_retriever 公共返回结构不变。"""

    @patch('app.retrieval.faiss_retriever._available', True)
    @patch('app.retrieval.faiss_retriever._index')
    @patch('app.retrieval.faiss_retriever._metadata')
    def test_retrieve_returns_list_of_dicts(self, mock_metadata, mock_index):
        """retrieve 应返回 list[dict]，包含预期字段。"""
        # 设置 mock
        mock_metadata.__getitem__ = MagicMock(return_value={
            'id': 'test_001',
            'domain': 'hr',
            'source_file': 'test.md',
            'chunk_index': 0,
            'content': '测试内容',
        })
        mock_metadata.__len__ = MagicMock(return_value=10)

        mock_search_result = (np.array([[0.9, 0.8, 0.7]]), np.array([[0, 1, 2]]))
        mock_index.search = MagicMock(return_value=mock_search_result)

        import importlib
        import app.retrieval.embedding_runtime as emb_mod
        importlib.reload(emb_mod)

        # Mock encode
        mock_model = MagicMock()
        mock_model.encode = MagicMock(return_value=np.random.randn(512).astype(np.float32))
        emb_mod._model = mock_model
        emb_mod._model_loaded = True

        import app.retrieval.faiss_retriever as mod
        importlib.reload(mod)
        mod._available = True
        mod._index = mock_index
        mod._metadata = [mock_metadata.__getitem__(0)] * 3

        from app.retrieval.faiss_retriever import retrieve_with_scores
        results = retrieve_with_scores('测试问题', top_k=3)

        self.assertIsInstance(results, list)
        if results:
            chunk, score = results[0]
            self.assertIsInstance(chunk, dict)
            self.assertIsInstance(score, float)
            for key in ['id', 'domain', 'source_file', 'chunk_index', 'content']:
                self.assertIn(key, chunk)


if __name__ == '__main__':
    unittest.main()
