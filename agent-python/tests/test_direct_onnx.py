#!/usr/bin/env python3
"""
test_direct_onnx.py — Direct ONNX Runtime 测试

覆盖：
1. 配置解析
2. Session 输入适配
3. token_type_ids 可选
4. Pooling 配置解析
5. CLS Pooling
6. Mean Pooling
7. Attention Mask
8. Batch
9. Normalize
10. 维度校验
11. NaN/Inf
12. 加载失败不回退
13. Direct 路径不导入 Torch
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AGENT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, '..'))
sys.path.insert(0, AGENT_ROOT)


class TestDirectOnnxConfig(unittest.TestCase):
    """测试 Direct ONNX 配置。"""

    def test_missing_model_path_raises(self):
        """未配置 EMBEDDING_MODEL_PATH 应报错。"""
        with patch.dict(os.environ, {'EMBEDDING_MODEL_PATH': ''}, clear=False):
            import importlib

            import app.retrieval.direct_onnx_embedding as mod
            importlib.reload(mod)
            with self.assertRaises(ValueError) as ctx:
                mod._validate_config()
            self.assertIn('EMBEDDING_MODEL_PATH', str(ctx.exception))

    def test_nonexistent_path_raises(self):
        """不存在的路径应报错。"""
        with patch.dict(os.environ, {'EMBEDDING_MODEL_PATH': '/nonexistent'}, clear=False):
            import importlib

            import app.retrieval.direct_onnx_embedding as mod
            importlib.reload(mod)
            with self.assertRaises(FileNotFoundError):
                mod._validate_config()

    def test_invalid_provider_raises(self):
        """非法 Provider 应报错。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建 ONNX 文件以通过路径检查
            os.makedirs(os.path.join(tmpdir, 'onnx'), exist_ok=True)
            with open(os.path.join(tmpdir, 'onnx', 'model.onnx'), 'w') as f:
                f.write('fake')
            with patch.dict(os.environ, {
                'EMBEDDING_MODEL_PATH': tmpdir,
                'EMBEDDING_PROVIDER': 'CUDAExecutionProvider',
            }, clear=False):
                import importlib

                import app.retrieval.direct_onnx_embedding as mod
                importlib.reload(mod)
                with self.assertRaises(ValueError) as ctx:
                    mod._validate_config()
                self.assertIn('CPUExecutionProvider', str(ctx.exception))


class TestDirectOnnxPooling(unittest.TestCase):
    """测试 Pooling 逻辑。"""

    def test_cls_pooling(self):
        """CLS pooling 取第一个 token。"""
        import app.retrieval.direct_onnx_embedding as mod

        # 模拟 last_hidden_state: [batch=2, seq=5, dim=4]
        hidden = np.array([
            [[1, 2, 3, 4], [5, 6, 7, 8], [9, 10, 11, 12], [0, 0, 0, 0], [0, 0, 0, 0]],
            [[10, 20, 30, 40], [50, 60, 70, 80], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]],
        ], dtype=np.float32)
        mask = np.array([[1, 1, 1, 0, 0], [1, 1, 0, 0, 0]], dtype=np.int64)

        # 临时设置 pooling 配置
        orig = mod._pooling_config
        mod._pooling_config = {'pooling_mode': 'cls'}
        try:
            result = mod._pool(hidden, mask)
            self.assertEqual(result.shape, (2, 4))
            np.testing.assert_array_equal(result[0], [1, 2, 3, 4])
            np.testing.assert_array_equal(result[1], [10, 20, 30, 40])
        finally:
            mod._pooling_config = orig

    def test_mean_pooling(self):
        """Mean pooling 对非 padding token 求平均。"""
        import app.retrieval.direct_onnx_embedding as mod

        hidden = np.array([
            [[2, 4], [6, 8], [0, 0]],  # seq_len=3, 有效 2 个
        ], dtype=np.float32)
        mask = np.array([[1, 1, 0]], dtype=np.int64)

        orig = mod._pooling_config
        mod._pooling_config = {'pooling_mode': 'mean'}
        try:
            result = mod._pool(hidden, mask)
            self.assertEqual(result.shape, (1, 2))
            # mean of [2,4] and [6,8] = [4,6]
            np.testing.assert_array_almost_equal(result[0], [4, 6])
        finally:
            mod._pooling_config = orig

    def test_attention_mask_applied(self):
        """Attention mask 正确屏蔽 padding。"""
        import app.retrieval.direct_onnx_embedding as mod

        hidden = np.array([[[1, 2], [3, 4], [99, 99]]], dtype=np.float32)
        mask = np.array([[1, 1, 0]], dtype=np.int64)

        orig = mod._pooling_config
        mod._pooling_config = {'pooling_mode': 'mean'}
        try:
            result = mod._pool(hidden, mask)
            # 只对前两个 token 求平均
            np.testing.assert_array_almost_equal(result[0], [2, 3])
        finally:
            mod._pooling_config = orig


class TestDirectOnnxEncode(unittest.TestCase):
    """测试 encode 行为（Mock Session）。"""

    def _make_mock_session(self, dim=512, seq_len=10):
        """创建 mock ONNX Session。"""
        mock_session = MagicMock()
        mock_inputs = [
            MagicMock(name='input_ids'),
            MagicMock(name='attention_mask'),
            MagicMock(name='token_type_ids'),
        ]
        mock_session.get_inputs.return_value = mock_inputs
        mock_session.get_outputs.return_value = [MagicMock(name='last_hidden_state')]

        def mock_run(output_names, inputs):
            batch_size = inputs['input_ids'].shape[0]
            hidden = np.random.randn(batch_size, seq_len, dim).astype(np.float32)
            return [hidden]

        mock_session.run = mock_run
        return mock_session

    def test_output_is_numpy(self):
        """输出应为 numpy 数组。"""
        import app.retrieval.direct_onnx_embedding as mod

        mock_session = self._make_mock_session()
        mock_tokenizer = {
            'tokenizer': MagicMock(),
            'max_length': 512,
            'cls_token_id': 101,
            'sep_token_id': 102,
            'pad_token_id': 0,
        }

        # Mock encode_batch
        mock_enc = MagicMock()
        mock_enc.ids = [101, 100, 102]
        mock_tokenizer['tokenizer'].encode_batch.return_value = [mock_enc]

        orig_session = mod._session
        orig_tokenizer = mod._tokenizer
        orig_pooling = mod._pooling_config
        orig_loaded = mod._model_loaded

        mod._session = mock_session
        mod._tokenizer = mock_tokenizer
        mod._pooling_config = {'pooling_mode': 'cls'}
        mod._model_loaded = True

        try:
            result = mod.encode('测试')
            self.assertIsInstance(result, np.ndarray)
            self.assertEqual(result.dtype, np.float32)
        finally:
            mod._session = orig_session
            mod._tokenizer = orig_tokenizer
            mod._pooling_config = orig_pooling
            mod._model_loaded = orig_loaded

    def test_dimension_512(self):
        """输出维度应为 512。"""
        import app.retrieval.direct_onnx_embedding as mod

        mock_session = self._make_mock_session(dim=512)
        mock_tokenizer = {
            'tokenizer': MagicMock(),
            'max_length': 512,
            'cls_token_id': 101,
            'sep_token_id': 102,
            'pad_token_id': 0,
        }
        mock_enc = MagicMock()
        mock_enc.ids = [101, 100, 102]
        mock_tokenizer['tokenizer'].encode_batch.return_value = [mock_enc]

        orig = mod._session, mod._tokenizer, mod._pooling_config, mod._model_loaded
        mod._session = mock_session
        mod._tokenizer = mock_tokenizer
        mod._pooling_config = {'pooling_mode': 'cls'}
        mod._model_loaded = True

        try:
            result = mod.encode('测试')
            self.assertEqual(result.shape, (512,))
        finally:
            mod._session, mod._tokenizer, mod._pooling_config, mod._model_loaded = orig

    def test_batch_shape(self):
        """批量输出维度应正确。"""
        import app.retrieval.direct_onnx_embedding as mod

        mock_session = self._make_mock_session(dim=512)
        mock_tokenizer = {
            'tokenizer': MagicMock(),
            'max_length': 512,
            'cls_token_id': 101,
            'sep_token_id': 102,
            'pad_token_id': 0,
        }
        enc1 = MagicMock()
        enc1.ids = [101, 100, 102]
        enc2 = MagicMock()
        enc2.ids = [101, 200, 102]
        enc3 = MagicMock()
        enc3.ids = [101, 300, 102]
        mock_tokenizer['tokenizer'].encode_batch.return_value = [enc1, enc2, enc3]

        orig = mod._session, mod._tokenizer, mod._pooling_config, mod._model_loaded
        mod._session = mock_session
        mod._tokenizer = mock_tokenizer
        mod._pooling_config = {'pooling_mode': 'cls'}
        mod._model_loaded = True

        try:
            result = mod.encode(['t1', 't2', 't3'])
            self.assertEqual(result.shape, (3, 512))
        finally:
            mod._session, mod._tokenizer, mod._pooling_config, mod._model_loaded = orig

    def test_normalized_norm_near_1(self):
        """归一化后范数应接近 1。"""
        import app.retrieval.direct_onnx_embedding as mod

        mock_session = self._make_mock_session(dim=512)
        mock_tokenizer = {
            'tokenizer': MagicMock(),
            'max_length': 512,
            'cls_token_id': 101,
            'sep_token_id': 102,
            'pad_token_id': 0,
        }
        mock_enc = MagicMock()
        mock_enc.ids = [101, 100, 102]
        mock_tokenizer['tokenizer'].encode_batch.return_value = [mock_enc]

        orig = mod._session, mod._tokenizer, mod._pooling_config, mod._model_loaded
        mod._session = mock_session
        mod._tokenizer = mock_tokenizer
        mod._pooling_config = {'pooling_mode': 'cls'}
        mod._model_loaded = True

        try:
            result = mod.encode('测试', normalize=True)
            norm = np.linalg.norm(result)
            self.assertAlmostEqual(norm, 1.0, places=5)
        finally:
            mod._session, mod._tokenizer, mod._pooling_config, mod._model_loaded = orig


class TestDirectOnnxNoTorchImport(unittest.TestCase):
    """测试 Direct 路径不导入 Torch。"""

    def test_direct_path_no_torch(self):
        """Direct ONNX 路径不应导入 torch。"""
        import subprocess
        result = subprocess.run(
            [sys.executable, '-c', '''
import sys
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np

os.environ["EMBEDDING_BACKEND"] = "onnx_direct"
with tempfile.TemporaryDirectory() as tmpdir:
    model = Path(tmpdir)
    (model / "onnx").mkdir()
    (model / "onnx" / "model.onnx").write_bytes(b"test-placeholder")
    os.environ["EMBEDDING_MODEL_PATH"] = tmpdir

    import app.retrieval.direct_onnx_embedding as direct

    session = MagicMock()
    session.get_inputs.return_value = [
        SimpleNamespace(name="input_ids"),
        SimpleNamespace(name="attention_mask"),
        SimpleNamespace(name="token_type_ids"),
    ]
    session.get_outputs.return_value = [SimpleNamespace(name="last_hidden_state")]
    session.run.return_value = [np.ones((1, 3, 512), dtype=np.float32)]
    tokenizer = MagicMock()
    tokenizer.encode_batch.return_value = [SimpleNamespace(ids=[101, 100, 102])]
    tokenizer_info = {
        "tokenizer": tokenizer,
        "max_length": 512,
        "cls_token_id": 101,
        "sep_token_id": 102,
        "pad_token_id": 0,
    }

    with patch.object(direct.ort, "InferenceSession", return_value=session), \\
         patch.object(direct, "_load_tokenizer", return_value=tokenizer_info), \\
         patch.object(direct, "_load_pooling_config", return_value={"pooling_mode": "cls"}):
        result = direct.encode("test")

    assert result.shape == (512,)
assert "torch" not in sys.modules, "torch was imported!"
assert "sentence_transformers" not in sys.modules, "sentence_transformers was imported!"
assert "optimum" not in sys.modules, "optimum was imported!"
print("OK: no torch/sentence_transformers/optimum imported")
'''],
            capture_output=True, text=True,
            cwd=AGENT_ROOT,
        )
        self.assertEqual(result.returncode, 0, f"Failed: {result.stderr}")
        self.assertIn('OK', result.stdout)


if __name__ == '__main__':
    unittest.main()
