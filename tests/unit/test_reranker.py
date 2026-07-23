"""scripts/reranker.py 单元测试

测试目标：从 29% 提升到 70%+

覆盖重点：
1. rerank_candidates() - 主函数（多种输入场景）
2. _get_reranker() - 单例模型加载
3. _passage() - passage 文本提取
4. 错误处理和 fallback
5. 分数归一化和排序
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest
import torch


class TestPassageExtraction:
    """测试 _passage() 文本提取逻辑"""

    def test_passage_with_text_column(self):
        """测试优先使用 text 列"""
        from scripts.reranker import _passage

        row = pd.Series({"text": "prefixed text", "raw_text": "raw text"})
        assert _passage(row) == "prefixed text"

    def test_passage_fallback_to_raw_text(self):
        """测试当 text 为空时回退到 raw_text"""
        from scripts.reranker import _passage

        row = pd.Series({"text": None, "raw_text": "raw text"})
        assert _passage(row) == "raw text"

    def test_passage_with_empty_text(self):
        """测试当 text 为空字符串时回退到 raw_text"""
        from scripts.reranker import _passage

        row = pd.Series({"text": "", "raw_text": "raw text"})
        assert _passage(row) == "raw text"

    def test_passage_missing_columns(self):
        """测试缺失列时返回空字符串"""
        from scripts.reranker import _passage

        row = pd.Series({"other": "data"})
        assert _passage(row) == ""

    def test_passage_all_none(self):
        """测试所有列都为 None 时返回空字符串"""
        from scripts.reranker import _passage

        row = pd.Series({"text": None, "raw_text": None})
        assert _passage(row) == ""


class TestRerankCandidates:
    """测试 rerank_candidates() 主函数"""

    @patch("scripts.reranker._get_reranker")
    def test_rerank_basic_functionality(self, mock_get_reranker):
        """测试正常的 reranking 流程"""
        from scripts.reranker import rerank_candidates

        # Mock reranker
        mock_reranker = MagicMock()
        mock_reranker.predict.return_value = np.array([0.9, 0.7, 0.5])
        mock_get_reranker.return_value = mock_reranker

        # 准备测试数据
        hits = pd.DataFrame(
            {
                "text": ["passage 1", "passage 2", "passage 3"],
                "score": [0.5, 0.6, 0.4],
            }
        )

        result = rerank_candidates("test query", hits, top_k=2)

        # 验证结果
        assert len(result) == 2
        assert "rerank_score" in result.columns
        # 结果应该按 rerank_score 降序排列
        assert result["rerank_score"].iloc[0] >= result["rerank_score"].iloc[1]
        # 第一条应该是原始分数最高的
        assert result["text"].iloc[0] == "passage 1"

    @patch("scripts.reranker._get_reranker")
    def test_rerank_with_raw_text_fallback(self, mock_get_reranker):
        """测试使用 raw_text 作为 fallback"""
        from scripts.reranker import rerank_candidates

        mock_reranker = MagicMock()
        mock_reranker.predict.return_value = np.array([0.8, 0.6])
        mock_get_reranker.return_value = mock_reranker

        hits = pd.DataFrame(
            {
                "text": [None, "passage 2"],
                "raw_text": ["raw passage 1", "raw passage 2"],
            }
        )

        result = rerank_candidates("query", hits, top_k=2)

        # 验证调用参数（应该用 raw_text 作为第一个 passage）
        call_args = mock_reranker.predict.call_args[0][0]
        assert call_args[0][1] == "raw passage 1"
        assert call_args[1][1] == "passage 2"

    @patch("scripts.reranker._get_reranker")
    def test_rerank_empty_dataframe(self, mock_get_reranker):
        """测试空 DataFrame 输入"""
        from scripts.reranker import rerank_candidates

        hits = pd.DataFrame()
        result = rerank_candidates("query", hits, top_k=5)

        # 空输入应该返回空 DataFrame
        assert len(result) == 0
        mock_get_reranker.assert_not_called()

    @patch("scripts.reranker._get_reranker")
    def test_rerank_none_input(self, mock_get_reranker):
        """测试 None 输入"""
        from scripts.reranker import rerank_candidates

        result = rerank_candidates("query", None, top_k=5)

        assert result is None
        mock_get_reranker.assert_not_called()

    @patch("scripts.reranker._get_reranker")
    def test_rerank_top_k_larger_than_hits(self, mock_get_reranker):
        """测试 top_k 大于候选数"""
        from scripts.reranker import rerank_candidates

        mock_reranker = MagicMock()
        mock_reranker.predict.return_value = np.array([0.9, 0.7])
        mock_get_reranker.return_value = mock_reranker

        hits = pd.DataFrame({"text": ["passage 1", "passage 2"]})
        result = rerank_candidates("query", hits, top_k=10)

        # 应该返回所有候选
        assert len(result) == 2

    @patch("scripts.reranker._get_reranker")
    def test_rerank_score_sigmoid_normalization(self, mock_get_reranker):
        """测试分数 sigmoid 归一化"""
        from scripts.reranker import rerank_candidates

        # 模拟 reranker 返回 logits
        mock_reranker = MagicMock()
        raw_logits = np.array([2.0, 0.0, -2.0])
        mock_reranker.predict.return_value = raw_logits
        mock_get_reranker.return_value = mock_reranker

        hits = pd.DataFrame({"text": ["p1", "p2", "p3"]})
        result = rerank_candidates("query", hits, top_k=3)

        # 验证分数在 0-1 之间
        scores = result["rerank_score"].tolist()
        assert all(0 <= s <= 1 for s in scores)
        # 验证 sigmoid 计算正确性（高 logit → 高分数）
        assert scores[0] > scores[1] > scores[2]
        # sigmoid(2.0) ≈ 0.88, sigmoid(0.0) = 0.5, sigmoid(-2.0) ≈ 0.12
        assert abs(scores[1] - 0.5) < 0.1

    @patch("scripts.reranker._get_reranker")
    def test_rerank_preserves_original_columns(self, mock_get_reranker):
        """测试保留原始 DataFrame 的所有列"""
        from scripts.reranker import rerank_candidates

        mock_reranker = MagicMock()
        mock_reranker.predict.return_value = np.array([0.9, 0.8])
        mock_get_reranker.return_value = mock_reranker

        hits = pd.DataFrame(
            {
                "text": ["p1", "p2"],
                "score": [0.5, 0.6],
                "date": ["2024-01", "2024-02"],
                "extra": ["a", "b"],
            }
        )

        result = rerank_candidates("query", hits, top_k=2)

        # 验证所有原始列都保留
        assert "text" in result.columns
        assert "score" in result.columns
        assert "date" in result.columns
        assert "extra" in result.columns
        assert "rerank_score" in result.columns

    @patch("scripts.reranker._get_reranker")
    def test_rerank_resets_index(self, mock_get_reranker):
        """测试结果 DataFrame 重置索引"""
        from scripts.reranker import rerank_candidates

        mock_reranker = MagicMock()
        mock_reranker.predict.return_value = np.array([0.5, 0.9, 0.7])
        mock_get_reranker.return_value = mock_reranker

        # 使用非连续索引
        hits = pd.DataFrame({"text": ["p1", "p2", "p3"]}, index=[5, 10, 15])
        result = rerank_candidates("query", hits, top_k=3)

        # 验证索引已重置
        assert list(result.index) == [0, 1, 2]


class TestRerankFallback:
    """测试错误处理和 fallback 机制"""

    @patch("scripts.reranker._get_reranker")
    def test_rerank_predict_exception_fallback(self, mock_get_reranker):
        """测试 predict 异常时 fallback 到原顺序"""
        from scripts.reranker import rerank_candidates

        mock_reranker = MagicMock()
        mock_reranker.predict.side_effect = RuntimeError("Model error")
        mock_get_reranker.return_value = mock_reranker

        hits = pd.DataFrame({"text": ["p1", "p2", "p3"], "score": [0.9, 0.8, 0.7]})
        result = rerank_candidates("query", hits, top_k=2)

        # 应该返回原顺序的前 top_k 条
        assert len(result) == 2
        assert result["text"].iloc[0] == "p1"
        assert result["text"].iloc[1] == "p2"
        # 不应该有 rerank_score 列
        assert "rerank_score" not in result.columns

    @patch("scripts.reranker._get_reranker")
    def test_rerank_score_count_mismatch_fallback(self, mock_get_reranker):
        """测试分数数量不匹配时 fallback"""
        from scripts.reranker import rerank_candidates

        mock_reranker = MagicMock()
        # 返回错误数量的分数
        mock_reranker.predict.return_value = np.array([0.9, 0.8])  # 只有 2 个分数
        mock_get_reranker.return_value = mock_reranker

        hits = pd.DataFrame({"text": ["p1", "p2", "p3"]})  # 有 3 个候选
        result = rerank_candidates("query", hits, top_k=2)

        # 应该 fallback 到原顺序
        assert len(result) == 2
        assert "rerank_score" not in result.columns

    @patch("scripts.reranker._get_reranker")
    def test_rerank_sigmoid_conversion_exception_fallback(self, mock_get_reranker):
        """测试 sigmoid 转换异常时 fallback"""
        from scripts.reranker import rerank_candidates

        mock_reranker = MagicMock()
        # 返回无法处理的数据
        mock_reranker.predict.return_value = "invalid"
        mock_get_reranker.return_value = mock_reranker

        hits = pd.DataFrame({"text": ["p1", "p2"]})
        result = rerank_candidates("query", hits, top_k=2)

        # 应该 fallback
        assert len(result) == 2
        assert "rerank_score" not in result.columns

    @patch("scripts.reranker._get_reranker")
    def test_rerank_get_reranker_exception_fallback(self, mock_get_reranker):
        """测试 _get_reranker() 异常时 fallback"""
        from scripts.reranker import rerank_candidates

        mock_get_reranker.side_effect = RuntimeError("Model load error")

        hits = pd.DataFrame({"text": ["p1", "p2"]})
        result = rerank_candidates("query", hits, top_k=2)

        # 应该 fallback
        assert len(result) == 2
        assert "rerank_score" not in result.columns


class TestGetRerankerSingleton:
    """测试 _get_reranker() 单例模型加载"""

    @patch("scripts.reranker.CrossEncoder")
    @patch("scripts.reranker.index_settings.reranker_params")
    def test_get_reranker_loads_model(self, mock_reranker_params, mock_cross_encoder):
        """测试首次调用加载模型"""
        from scripts import reranker

        # 重置全局单例
        reranker._reranker = None

        # 模拟参数
        mock_reranker_params.return_value = {
            "model": "BAAI/bge-reranker-v2-m3",
            "device": "mps",
            "use_fp16": True,
        }

        # 模拟 CrossEncoder
        mock_model = MagicMock()
        mock_cross_encoder.return_value = mock_model

        result = reranker._get_reranker()

        # 验证模型已加载
        assert result is mock_model
        mock_cross_encoder.assert_called_once_with(
            "BAAI/bge-reranker-v2-m3",
            device="mps",
            model_kwargs={"torch_dtype": torch.float16},
        )

    @patch("scripts.reranker.CrossEncoder")
    @patch("scripts.reranker.index_settings.reranker_params")
    def test_get_reranker_singleton(self, mock_reranker_params, mock_cross_encoder):
        """测试单例行为（第二次调用不重新加载）"""
        from scripts import reranker

        # 重置全局单例
        reranker._reranker = None

        mock_reranker_params.return_value = {
            "model": "model",
            "device": "cpu",
            "use_fp16": False,
        }
        mock_model = MagicMock()
        mock_cross_encoder.return_value = mock_model

        # 第一次调用
        result1 = reranker._get_reranker()
        # 第二次调用
        result2 = reranker._get_reranker()

        # 应该返回同一个实例
        assert result1 is result2
        # CrossEncoder 只应该被调用一次
        assert mock_cross_encoder.call_count == 1

    @patch("scripts.reranker.CrossEncoder")
    @patch("scripts.reranker.index_settings.reranker_params")
    def test_get_reranker_fp16_disabled(self, mock_reranker_params, mock_cross_encoder):
        """测试关闭 fp16 时不传 torch_dtype"""
        from scripts import reranker

        reranker._reranker = None

        mock_reranker_params.return_value = {
            "model": "model",
            "device": "cpu",
            "use_fp16": False,
        }
        mock_model = MagicMock()
        mock_cross_encoder.return_value = mock_model

        reranker._get_reranker()

        # 验证 model_kwargs 为空字典
        call_kwargs = mock_cross_encoder.call_args[1]
        assert call_kwargs["model_kwargs"] == {}

    @patch("scripts.reranker.CrossEncoder")
    @patch("scripts.reranker.index_settings.reranker_params")
    def test_get_reranker_different_devices(self, mock_reranker_params, mock_cross_encoder):
        """测试不同设备配置"""
        from scripts import reranker

        # 测试 MPS 设备
        reranker._reranker = None
        mock_reranker_params.return_value = {
            "model": "model",
            "device": "mps",
            "use_fp16": False,
        }
        mock_cross_encoder.return_value = MagicMock()
        reranker._get_reranker()
        assert mock_cross_encoder.call_args[1]["device"] == "mps"

        # 测试 CUDA 设备
        reranker._reranker = None
        mock_reranker_params.return_value = {
            "model": "model",
            "device": "cuda",
            "use_fp16": True,
        }
        mock_cross_encoder.return_value = MagicMock()
        reranker._get_reranker()
        assert mock_cross_encoder.call_args[1]["device"] == "cuda"


class TestRerankEdgeCases:
    """测试边界情况"""

    @patch("scripts.reranker._get_reranker")
    def test_rerank_single_candidate(self, mock_get_reranker):
        """测试只有一个候选"""
        from scripts.reranker import rerank_candidates

        mock_reranker = MagicMock()
        mock_reranker.predict.return_value = np.array([0.9])
        mock_get_reranker.return_value = mock_reranker

        hits = pd.DataFrame({"text": ["only one"]})
        result = rerank_candidates("query", hits, top_k=5)

        assert len(result) == 1
        assert "rerank_score" in result.columns

    @patch("scripts.reranker._get_reranker")
    def test_rerank_top_k_zero(self, mock_get_reranker):
        """测试 top_k = 0"""
        from scripts.reranker import rerank_candidates

        mock_reranker = MagicMock()
        mock_reranker.predict.return_value = np.array([0.9, 0.8])
        mock_get_reranker.return_value = mock_reranker

        hits = pd.DataFrame({"text": ["p1", "p2"]})
        result = rerank_candidates("query", hits, top_k=0)

        # 应该返回空 DataFrame（但保留列结构）
        assert len(result) == 0
        assert "rerank_score" in result.columns

    @patch("scripts.reranker._get_reranker")
    def test_rerank_identical_scores(self, mock_get_reranker):
        """测试所有候选得分相同"""
        from scripts.reranker import rerank_candidates

        mock_reranker = MagicMock()
        # 所有分数相同（使用 0.0 作为 logit 输入，经过 sigmoid 后应该都是 0.5）
        mock_reranker.predict.return_value = np.array([0.0, 0.0, 0.0])
        mock_get_reranker.return_value = mock_reranker

        hits = pd.DataFrame({"text": ["p1", "p2", "p3"]})
        result = rerank_candidates("query", hits, top_k=2)

        # 应该返回任意 2 条
        assert len(result) == 2
        # 所有 sigmoid(0.0) 都应该约等于 0.5
        assert all(abs(score - 0.5) < 0.01 for score in result["rerank_score"])

    @patch("scripts.reranker._get_reranker")
    def test_rerank_very_long_query(self, mock_get_reranker):
        """测试超长查询"""
        from scripts.reranker import rerank_candidates

        mock_reranker = MagicMock()
        mock_reranker.predict.return_value = np.array([0.7])
        mock_get_reranker.return_value = mock_reranker

        long_query = "test " * 1000  # 超长查询
        hits = pd.DataFrame({"text": ["passage"]})
        result = rerank_candidates(long_query, hits, top_k=1)

        # 应该正常处理
        assert len(result) == 1
        # 验证 query 被正确传递
        call_args = mock_reranker.predict.call_args[0][0]
        assert call_args[0][0] == long_query

    @patch("scripts.reranker._get_reranker")
    def test_rerank_empty_query(self, mock_get_reranker):
        """测试空查询"""
        from scripts.reranker import rerank_candidates

        mock_reranker = MagicMock()
        mock_reranker.predict.return_value = np.array([0.5])
        mock_get_reranker.return_value = mock_reranker

        hits = pd.DataFrame({"text": ["passage"]})
        result = rerank_candidates("", hits, top_k=1)

        # 应该正常处理
        assert len(result) == 1

    @patch("scripts.reranker._get_reranker")
    def test_rerank_special_characters_in_text(self, mock_get_reranker):
        """测试文本中的特殊字符"""
        from scripts.reranker import rerank_candidates

        mock_reranker = MagicMock()
        mock_reranker.predict.return_value = np.array([0.8, 0.7])
        mock_get_reranker.return_value = mock_reranker

        hits = pd.DataFrame(
            {
                "text": [
                    "passage with 中文、标点！@#$%",
                    "passage with emoji 😊🎉",
                ]
            }
        )
        result = rerank_candidates("test", hits, top_k=2)

        # 应该正常处理特殊字符
        assert len(result) == 2
        assert "rerank_score" in result.columns

    @patch("scripts.reranker._get_reranker")
    def test_rerank_score_scalar_handling(self, mock_get_reranker):
        """测试 predict 返回标量而非列表的情况"""
        from scripts.reranker import rerank_candidates

        mock_reranker = MagicMock()
        # 返回标量（当只有一个候选时可能发生）
        mock_reranker.predict.return_value = 0.9  # 标量而非数组
        mock_get_reranker.return_value = mock_reranker

        hits = pd.DataFrame({"text": ["single passage"]})
        result = rerank_candidates("query", hits, top_k=1)

        # 应该正常处理（内部会转换为列表）
        assert len(result) == 1
        assert "rerank_score" in result.columns


class TestRerankIntegration:
    """集成测试（更接近实际使用场景）"""

    @patch("scripts.reranker._get_reranker")
    def test_rerank_realistic_scenario(self, mock_get_reranker):
        """测试真实场景：混合检索后的精排"""
        from scripts.reranker import rerank_candidates

        # 模拟真实的 CrossEncoder 行为
        mock_reranker = MagicMock()

        def mock_predict(pairs, convert_to_numpy=True):
            # 根据 query-passage 相关性模拟分数
            scores = []
            for q, p in pairs:
                # 简单模拟：包含更多匹配词的得分更高
                score = sum(word in p.lower() for word in q.lower().split())
                scores.append(score)
            return np.array(scores, dtype=np.float32)

        mock_reranker.predict = mock_predict
        mock_get_reranker.return_value = mock_reranker

        # 模拟混合检索结果（按 RRF 分数排序）
        hits = pd.DataFrame(
            {
                "text": [
                    "This document is about machine learning",
                    "Deep learning is a subset of AI",
                    "Python is a programming language",
                    "The weather is nice today",
                ],
                "rrf_score": [0.8, 0.7, 0.6, 0.5],
            }
        )

        query = "machine learning deep learning"
        result = rerank_candidates(query, hits, top_k=2)

        # 验证结果
        assert len(result) == 2
        # 前两名应该是包含 query 关键词的文档
        top_texts = result["text"].tolist()
        assert any("machine learning" in t for t in top_texts)
        assert any("Deep learning" in t for t in top_texts)

    @patch("scripts.reranker._get_reranker")
    def test_rerank_preserves_metadata(self, mock_get_reranker):
        """测试保留检索结果的所有元数据"""
        from scripts.reranker import rerank_candidates

        mock_reranker = MagicMock()
        mock_reranker.predict.return_value = np.array([0.9, 0.8, 0.7])
        mock_get_reranker.return_value = mock_reranker

        # 模拟带完整元数据的检索结果
        hits = pd.DataFrame(
            {
                "text": ["p1", "p2", "p3"],
                "raw_text": ["raw1", "raw2", "raw3"],
                "chunk_id": [1, 2, 3],
                "date": ["2024-01", "2024-02", "2024-03"],
                "rrf_score": [0.5, 0.6, 0.4],
                "_distance": [0.1, 0.2, 0.3],
            }
        )

        result = rerank_candidates("query", hits, top_k=3)

        # 验证所有元数据列都保留
        expected_columns = {
            "text",
            "raw_text",
            "chunk_id",
            "date",
            "rrf_score",
            "_distance",
            "rerank_score",
        }
        assert set(result.columns) == expected_columns
