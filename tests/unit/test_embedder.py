"""测试嵌入模型加载与编码功能。

Coverage targets:
- _get_model: 全局单例加载逻辑、index_settings 集成
- embed: 批量编码、参数传递
- embed_one: 单文本编码
- 边界条件：空输入、None、大批量
- 错误处理：模型加载失败、编码异常
"""
import numpy as np
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from typing import Optional

from scripts.embedder import _get_model, embed, embed_one


class TestEmbedder:
    """嵌入模型测试套件。"""

    @pytest.fixture(autouse=True)
    def reset_model_singleton(self):
        """每个测试前重置全局单例。"""
        import scripts.embedder
        scripts.embedder._model = None
        yield
        scripts.embedder._model = None

    def test_get_model_singleton(self):
        """测试模型单例加载：第一次加载后缓存，后续复用。"""
        mock_params = {
            "model": "BAAI/bge-m3",
            "device": "cpu",
            "batch_size": 8,
            "use_fp16": False
        }

        with patch("scripts.embedder.index_settings.embedding_params", return_value=mock_params), \
             patch("scripts.embedder.BGEM3FlagModel") as mock_model_class:

            mock_instance = MagicMock()
            mock_model_class.return_value = mock_instance

            # 第一次调用：应该加载模型
            model1 = _get_model()
            assert model1 is mock_instance
            mock_model_class.assert_called_once_with(
                "BAAI/bge-m3",
                use_fp16=False,
                devices="cpu",
                batch_size=8
            )

            # 第二次调用：应该复用缓存
            model2 = _get_model()
            assert model2 is model1
            assert mock_model_class.call_count == 1  # 不应该再次加载

    def test_get_model_loads_custom_params(self):
        """测试模型加载使用自定义参数。"""
        custom_params = {
            "model": "custom/model-path",
            "device": "cuda:0",
            "batch_size": 16,
            "use_fp16": True
        }

        with patch("scripts.embedder.index_settings.embedding_params", return_value=custom_params), \
             patch("scripts.embedder.BGEM3FlagModel") as mock_model_class:

            mock_instance = MagicMock()
            mock_model_class.return_value = mock_instance

            model = _get_model()

            # 验证参数传递
            mock_model_class.assert_called_once_with(
                "custom/model-path",
                use_fp16=True,
                devices="cuda:0",
                batch_size=16
            )

    def test_embed_basic(self):
        """测试基本批量编码功能。"""
        texts = ["测试文本1", "测试文本2", "测试文本3"]
        mock_dense = np.random.rand(3, 1024).astype(np.float32)
        mock_output = {"dense_vecs": mock_dense}

        with patch("scripts.embedder._get_model") as mock_get_model:
            mock_model = MagicMock()
            mock_model.encode.return_value = mock_output
            mock_get_model.return_value = mock_model

            result = embed(texts)

            # 验证返回值
            assert "dense_vecs" in result
            assert result["dense_vecs"].shape == (3, 1024)

            # 验证模型调用参数
            mock_model.encode.assert_called_once_with(
                texts,
                return_dense=True,
                return_sparse=False,
                return_colbert_vecs=False
            )

    def test_embed_single_text(self):
        """测试单文本编码（通过 list）。"""
        text = "单个测试文本"
        mock_dense = np.random.rand(1, 1024).astype(np.float32)
        mock_output = {"dense_vecs": mock_dense}

        with patch("scripts.embedder._get_model") as mock_get_model:
            mock_model = MagicMock()
            mock_model.encode.return_value = mock_output
            mock_get_model.return_value = mock_model

            result = embed([text])

            assert "dense_vecs" in result
            assert result["dense_vecs"].shape == (1, 1024)

    def test_embed_empty_list(self):
        """测试空列表编码（边界条件）。"""
        mock_dense = np.empty((0, 1024), dtype=np.float32)
        mock_output = {"dense_vecs": mock_dense}

        with patch("scripts.embedder._get_model") as mock_get_model:
            mock_model = MagicMock()
            mock_model.encode.return_value = mock_output
            mock_get_model.return_value = mock_model

            result = embed([])

            assert "dense_vecs" in result
            assert result["dense_vecs"].shape[0] == 0
            mock_model.encode.assert_called_once()

    def test_embed_large_batch(self):
        """测试大批量编码。"""
        # 模拟 100 个文本
        texts = [f"测试文本 {i}" for i in range(100)]
        mock_dense = np.random.rand(100, 1024).astype(np.float32)
        mock_output = {"dense_vecs": mock_dense}

        with patch("scripts.embedder._get_model") as mock_get_model:
            mock_model = MagicMock()
            mock_model.encode.return_value = mock_output
            mock_get_model.return_value = mock_model

            result = embed(texts)

            assert result["dense_vecs"].shape == (100, 1024)
            mock_model.encode.assert_called_once()

    def test_embed_with_special_characters(self):
        """测试包含特殊字符的文本编码。"""
        texts = [
            "包含emoji😊的文本",
            "换行符\n和制表符\t",
            "中英混合 mixed text",
            "标点符号！@#$%^&*()",
            ""  # 空字符串
        ]
        mock_dense = np.random.rand(5, 1024).astype(np.float32)
        mock_output = {"dense_vecs": mock_dense}

        with patch("scripts.embedder._get_model") as mock_get_model:
            mock_model = MagicMock()
            mock_model.encode.return_value = mock_output
            mock_get_model.return_value = mock_model

            result = embed(texts)

            assert result["dense_vecs"].shape == (5, 1024)
            # 验证传递的是原始文本
            call_args = mock_model.encode.call_args[0][0]
            assert call_args == texts

    def test_embed_one_basic(self):
        """测试单文本编码便捷函数。"""
        text = "单个文本测试"
        mock_dense = np.random.rand(1, 1024).astype(np.float32)
        mock_output = {"dense_vecs": mock_dense}

        with patch("scripts.embedder._get_model") as mock_get_model:
            mock_model = MagicMock()
            mock_model.encode.return_value = mock_output
            mock_get_model.return_value = mock_model

            result = embed_one(text)

            # 验证返回的是 1D 向量（从 2D 的第一行提取）
            assert result.shape == (1024,)
            assert isinstance(result, np.ndarray)

            # 验证调用时包装成列表
            mock_model.encode.assert_called_once()
            call_args = mock_model.encode.call_args[0][0]
            assert call_args == [text]

    def test_embed_one_empty_string(self):
        """测试 embed_one 处理空字符串。"""
        text = ""
        mock_dense = np.random.rand(1, 1024).astype(np.float32)
        mock_output = {"dense_vecs": mock_dense}

        with patch("scripts.embedder._get_model") as mock_get_model:
            mock_model = MagicMock()
            mock_model.encode.return_value = mock_output
            mock_get_model.return_value = mock_model

            result = embed_one(text)

            assert result.shape == (1024,)
            call_args = mock_model.encode.call_args[0][0]
            assert call_args == [""]

    def test_embed_one_long_text(self):
        """测试 embed_one 处理长文本。"""
        # 模拟超长文本（1000 个字）
        text = "测试文本" * 200
        mock_dense = np.random.rand(1, 1024).astype(np.float32)
        mock_output = {"dense_vecs": mock_dense}

        with patch("scripts.embedder._get_model") as mock_get_model:
            mock_model = MagicMock()
            mock_model.encode.return_value = mock_output
            mock_get_model.return_value = mock_model

            result = embed_one(text)

            assert result.shape == (1024,)

    def test_embed_model_encode_exception(self):
        """测试模型编码异常传播。"""
        with patch("scripts.embedder._get_model") as mock_get_model:
            mock_model = MagicMock()
            mock_model.encode.side_effect = RuntimeError("编码失败")
            mock_get_model.return_value = mock_model

            with pytest.raises(RuntimeError, match="编码失败"):
                embed(["测试"])

    def test_embed_model_load_exception(self):
        """测试模型加载异常传播。"""
        with patch("scripts.embedder.index_settings.embedding_params", return_value={
            "model": "invalid/path",
            "device": "cpu",
            "batch_size": 8,
            "use_fp16": False
        }), \
             patch("scripts.embedder.BGEM3FlagModel", side_effect=OSError("模型文件不存在")):

            with pytest.raises(OSError, match="模型文件不存在"):
                _get_model()

    def test_embed_output_format(self):
        """测试 embed 输出格式符合预期。"""
        texts = ["文本1", "文本2"]
        mock_dense = np.random.rand(2, 1024).astype(np.float32)
        # 模拟返回的 dict（只有 dense_vecs，无 sparse）
        mock_output = {"dense_vecs": mock_dense}

        with patch("scripts.embedder._get_model") as mock_get_model:
            mock_model = MagicMock()
            mock_model.encode.return_value = mock_output
            mock_get_model.return_value = mock_model

            result = embed(texts)

            # 验证输出是 dict
            assert isinstance(result, dict)
            # 验证只包含 dense_vecs
            assert "dense_vecs" in result
            # 验证 sparse 相关字段不存在（return_sparse=False）
            assert "lexical_weights" not in result

    def test_embed_disable_sparse_and_colbert(self):
        """测试编码时禁用稀疏向量和 ColBERT。"""
        with patch("scripts.embedder._get_model") as mock_get_model:
            mock_model = MagicMock()
            mock_dense = np.random.rand(1, 1024).astype(np.float32)
            mock_model.encode.return_value = {"dense_vecs": mock_dense}
            mock_get_model.return_value = mock_model

            embed(["测试"])

            # 验证调用参数明确禁用 sparse 和 colbert
            call_kwargs = mock_model.encode.call_args[1]
            assert call_kwargs["return_dense"] is True
            assert call_kwargs["return_sparse"] is False
            assert call_kwargs["return_colbert_vecs"] is False

    def test_embed_vector_dimension(self):
        """测试向量维度为 1024（BGE-M3 标准维度）。"""
        texts = ["测试"]
        mock_dense = np.random.rand(1, 1024).astype(np.float32)
        mock_output = {"dense_vecs": mock_dense}

        with patch("scripts.embedder._get_model") as mock_get_model:
            mock_model = MagicMock()
            mock_model.encode.return_value = mock_output
            mock_get_model.return_value = mock_model

            result = embed(texts)

            # 验证维度
            assert result["dense_vecs"].shape[1] == 1024

    def test_embed_one_vector_dimension(self):
        """测试 embed_one 返回 1024 维向量。"""
        mock_dense = np.random.rand(1, 1024).astype(np.float32)
        mock_output = {"dense_vecs": mock_dense}

        with patch("scripts.embedder._get_model") as mock_get_model:
            mock_model = MagicMock()
            mock_model.encode.return_value = mock_output
            mock_get_model.return_value = mock_model

            result = embed_one("测试")

            assert len(result) == 1024

    def test_get_model_with_default_params(self):
        """测试使用默认参数加载模型。"""
        # 模拟 index_settings 返回默认值
        default_params = {
            "model": "BAAI/bge-m3",
            "device": "cpu",
            "batch_size": 8,
            "use_fp16": True
        }

        with patch("scripts.embedder.index_settings.embedding_params", return_value=default_params), \
             patch("scripts.embedder.BGEM3FlagModel") as mock_model_class:

            mock_instance = MagicMock()
            mock_model_class.return_value = mock_instance

            model = _get_model()

            # 验证使用默认参数
            mock_model_class.assert_called_once_with(
                "BAAI/bge-m3",
                use_fp16=True,
                devices="cpu",
                batch_size=8
            )

    def test_embed_preserves_order(self):
        """测试编码保持输入顺序。"""
        texts = ["第一", "第二", "第三"]
        # 创建可区分的向量（每个向量第一个元素不同）
        mock_dense = np.array([
            [1.0] + [0.0] * 1023,
            [2.0] + [0.0] * 1023,
            [3.0] + [0.0] * 1023,
        ], dtype=np.float32)
        mock_output = {"dense_vecs": mock_dense}

        with patch("scripts.embedder._get_model") as mock_get_model:
            mock_model = MagicMock()
            mock_model.encode.return_value = mock_output
            mock_get_model.return_value = mock_model

            result = embed(texts)

            # 验证顺序保持（第一个向量第一个元素是 1.0）
            assert result["dense_vecs"][0, 0] == 1.0
            assert result["dense_vecs"][1, 0] == 2.0
            assert result["dense_vecs"][2, 0] == 3.0

    def test_embed_one_extracts_first_row(self):
        """测试 embed_one 正确提取第一行向量。"""
        # 创建 2D 数组，第一行是 [1, 2, 3, ..., 1024]
        mock_dense = np.arange(1, 1025).reshape(1, 1024).astype(np.float32)
        mock_output = {"dense_vecs": mock_dense}

        with patch("scripts.embedder._get_model") as mock_get_model:
            mock_model = MagicMock()
            mock_model.encode.return_value = mock_output
            mock_get_model.return_value = mock_model

            result = embed_one("测试")

            # 验证提取的是第一行
            assert result.shape == (1024,)
            assert result[0] == 1.0
            assert result[-1] == 1024.0

    def test_embed_whitespace_texts(self):
        """测试包含仅空白字符的文本。"""
        texts = [
            "   ",  # 仅空格
            "\n\n",  # 仅换行
            "\t\t",  # 仅制表符
            "  正常文本  ",  # 前后空白
        ]
        mock_dense = np.random.rand(4, 1024).astype(np.float32)
        mock_output = {"dense_vecs": mock_dense}

        with patch("scripts.embedder._get_model") as mock_get_model:
            mock_model = MagicMock()
            mock_model.encode.return_value = mock_output
            mock_get_model.return_value = mock_model

            result = embed(texts)

            assert result["dense_vecs"].shape == (4, 1024)
            # 验证传递原始文本（不做 strip）
            call_args = mock_model.encode.call_args[0][0]
            assert call_args == texts

    def test_embed_unicode_normalization(self):
        """测试 Unicode 正规化处理（如果模型有）。"""
        # 不同的 Unicode 表示（ é = e + 组合重音 vs 单字符 é）
        texts = [
            "café",  # 单字符 é (U+00E9)
            "café",  # 可能是 e + 组合重音 (U+0065 U+0301)
        ]
        mock_dense = np.random.rand(2, 1024).astype(np.float32)
        mock_output = {"dense_vecs": mock_dense}

        with patch("scripts.embedder._get_model") as mock_get_model:
            mock_model = MagicMock()
            mock_model.encode.return_value = mock_output
            mock_get_model.return_value = mock_model

            result = embed(texts)

            # 验证能够处理（不抛异常）
            assert result["dense_vecs"].shape == (2, 1024)

    def test_get_model_params_precedence(self):
        """测试参数读取优先级（从 index_settings）。"""
        # 模拟 index_settings.embedding_params() 调用
        custom_params = {
            "model": "custom-model",
            "device": "mps",
            "batch_size": 32,
            "use_fp16": False
        }

        with patch("scripts.embedder.index_settings.embedding_params", return_value=custom_params) as mock_params, \
             patch("scripts.embedder.BGEM3FlagModel") as mock_model_class:

            mock_instance = MagicMock()
            mock_model_class.return_value = mock_instance

            model = _get_model()

            # 验证调用了 index_settings
            mock_params.assert_called_once()
            # 验证传递了自定义参数
            call_kwargs = mock_model_class.call_args[1]
            assert call_kwargs["use_fp16"] is False
            assert call_kwargs["devices"] == "mps"
            assert call_kwargs["batch_size"] == 32
