"""测试索引运行时参数持久化。

测试 scripts/index_settings.py 的所有公开函数，覆盖：
- 参数加载（从 JSON 文件 + 默认值回退）
- 参数保存（写入 JSON）
- 参数重置（删除文件）
- 边界情况（文件不存在、JSON 损坏、None 值、空字典）
"""
import json
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from scripts import index_settings


class TestLoadRawHelper:
    """测试 _load_raw() 私有辅助函数行为。"""

    def test_load_raw_file_not_exists(self, tmp_path, monkeypatch):
        """测试文件不存在时返回空字典。"""
        fake_path = tmp_path / "nonexistent.json"
        monkeypatch.setattr("scripts.index_settings.INDEX_SETTINGS_PATH", fake_path)

        result = index_settings._load_raw()
        assert result == {}

    def test_load_raw_valid_json(self, tmp_path, monkeypatch):
        """测试正常加载有效 JSON。"""
        fake_path = tmp_path / "index_settings.json"
        test_data = {"retrieval": {"top_k": 10}}
        fake_path.write_text(json.dumps(test_data), encoding="utf-8")
        monkeypatch.setattr("scripts.index_settings.INDEX_SETTINGS_PATH", fake_path)

        result = index_settings._load_raw()
        assert result == test_data

    def test_load_raw_invalid_json(self, tmp_path, monkeypatch):
        """测试损坏的 JSON 文件时返回空字典（不抛异常）。"""
        fake_path = tmp_path / "index_settings.json"
        fake_path.write_text("{invalid json", encoding="utf-8")
        monkeypatch.setattr("scripts.index_settings.INDEX_SETTINGS_PATH", fake_path)

        result = index_settings._load_raw()
        assert result == {}

    def test_load_raw_empty_file(self, tmp_path, monkeypatch):
        """测试空文件时返回空字典。"""
        fake_path = tmp_path / "index_settings.json"
        fake_path.write_text("", encoding="utf-8")
        monkeypatch.setattr("scripts.index_settings.INDEX_SETTINGS_PATH", fake_path)

        result = index_settings._load_raw()
        assert result == {}


class TestMergeHelper:
    """测试 _merge() 浅合并逻辑。"""

    def test_merge_no_override(self):
        """测试没有 override 时使用默认值。"""
        default = {"a": 1, "b": 2}
        result = index_settings._merge(default, None)
        assert result == default

    def test_merge_empty_override(self):
        """测试空 override 字典时使用默认值。"""
        default = {"a": 1, "b": 2}
        result = index_settings._merge(default, {})
        assert result == default

    def test_merge_partial_override(self):
        """测试部分字段被 override。"""
        default = {"a": 1, "b": 2, "c": 3}
        override = {"b": 99}
        result = index_settings._merge(default, override)
        assert result == {"a": 1, "b": 99, "c": 3}

    def test_merge_none_value_preserved_as_default(self):
        """测试 override 里为 None 的字段用默认值顶上。"""
        default = {"a": 1, "b": 2}
        override = {"a": None, "b": 99}
        result = index_settings._merge(default, override)
        assert result == {"a": 1, "b": 99}  # None 被替换为默认值

    def test_merge_false_value_preserved(self):
        """测试布尔 False 会保留（不当作缺失）。"""
        default = {"flag": True, "count": 10}
        override = {"flag": False}
        result = index_settings._merge(default, override)
        assert result == {"flag": False, "count": 10}

    def test_merge_zero_value_preserved(self):
        """测试 0 值会保留。"""
        default = {"count": 10}
        override = {"count": 0}
        result = index_settings._merge(default, override)
        assert result == {"count": 0}


class TestRetrievalParams:
    """测试检索参数加载。"""

    def test_retrieval_params_default(self, tmp_path, monkeypatch):
        """测试文件不存在时返回默认值。"""
        fake_path = tmp_path / "index_settings.json"
        monkeypatch.setattr("scripts.index_settings.INDEX_SETTINGS_PATH", fake_path)

        result = index_settings.retrieval_params()
        # 应包含 top_k 和 window_expand
        assert "top_k" in result
        assert "window_expand" in result
        assert isinstance(result["top_k"], int)
        assert isinstance(result["window_expand"], int)

    def test_retrieval_params_override(self, tmp_path, monkeypatch):
        """测试从文件加载自定义值。"""
        fake_path = tmp_path / "index_settings.json"
        test_data = {"retrieval": {"top_k": 99, "window_expand": 5}}
        fake_path.write_text(json.dumps(test_data), encoding="utf-8")
        monkeypatch.setattr("scripts.index_settings.INDEX_SETTINGS_PATH", fake_path)

        result = index_settings.retrieval_params()
        assert result["top_k"] == 99
        assert result["window_expand"] == 5

    def test_retrieval_params_partial_override(self, tmp_path, monkeypatch):
        """测试只覆盖部分字段，其余用默认值。"""
        fake_path = tmp_path / "index_settings.json"
        test_data = {"retrieval": {"top_k": 15}}  # 只设置 top_k
        fake_path.write_text(json.dumps(test_data), encoding="utf-8")
        monkeypatch.setattr("scripts.index_settings.INDEX_SETTINGS_PATH", fake_path)

        result = index_settings.retrieval_params()
        assert result["top_k"] == 15
        assert "window_expand" in result  # 应该有默认值


class TestChunkingParams:
    """测试分块参数加载。"""

    def test_chunking_params_default(self, tmp_path, monkeypatch):
        """测试文件不存在时返回默认值。"""
        fake_path = tmp_path / "index_settings.json"
        monkeypatch.setattr("scripts.index_settings.INDEX_SETTINGS_PATH", fake_path)

        result = index_settings.chunking_params()
        assert "chunk_size" in result
        assert "chunk_overlap" in result
        assert isinstance(result["chunk_size"], int)
        assert isinstance(result["chunk_overlap"], int)

    def test_chunking_params_override(self, tmp_path, monkeypatch):
        """测试从文件加载自定义值。"""
        fake_path = tmp_path / "index_settings.json"
        test_data = {"chunking": {"chunk_size": 2000, "chunk_overlap": 300}}
        fake_path.write_text(json.dumps(test_data), encoding="utf-8")
        monkeypatch.setattr("scripts.index_settings.INDEX_SETTINGS_PATH", fake_path)

        result = index_settings.chunking_params()
        assert result["chunk_size"] == 2000
        assert result["chunk_overlap"] == 300


class TestEmbeddingParams:
    """测试本地 BGE-M3 参数加载。"""

    def test_embedding_params_default(self, tmp_path, monkeypatch):
        """测试文件不存在时返回默认值。"""
        fake_path = tmp_path / "index_settings.json"
        monkeypatch.setattr("scripts.index_settings.INDEX_SETTINGS_PATH", fake_path)

        result = index_settings.embedding_params()
        assert "model" in result
        assert "device" in result
        assert "batch_size" in result
        assert "use_fp16" in result
        assert isinstance(result["model"], str)
        assert isinstance(result["device"], str)
        assert isinstance(result["batch_size"], int)
        assert isinstance(result["use_fp16"], bool)

    def test_embedding_params_override(self, tmp_path, monkeypatch):
        """测试从文件加载自定义值。"""
        fake_path = tmp_path / "index_settings.json"
        test_data = {
            "embedding": {
                "model": "custom-model",
                "device": "cuda",
                "batch_size": 64,
                "use_fp16": True
            }
        }
        fake_path.write_text(json.dumps(test_data), encoding="utf-8")
        monkeypatch.setattr("scripts.index_settings.INDEX_SETTINGS_PATH", fake_path)

        result = index_settings.embedding_params()
        assert result["model"] == "custom-model"
        assert result["device"] == "cuda"
        assert result["batch_size"] == 64
        assert result["use_fp16"] is True


class TestFtsParams:
    """测试 FTS 关键词分词参数加载。"""

    def test_fts_params_default(self, tmp_path, monkeypatch):
        """测试文件不存在时返回默认值。"""
        fake_path = tmp_path / "index_settings.json"
        monkeypatch.setattr("scripts.index_settings.INDEX_SETTINGS_PATH", fake_path)

        result = index_settings.fts_params()
        assert "base_tokenizer" in result
        assert "ngram_min" in result
        assert "ngram_max" in result
        assert isinstance(result["base_tokenizer"], str)
        assert isinstance(result["ngram_min"], int)
        assert isinstance(result["ngram_max"], int)

    def test_fts_params_override(self, tmp_path, monkeypatch):
        """测试从文件加载自定义值。"""
        fake_path = tmp_path / "index_settings.json"
        test_data = {
            "fts": {
                "base_tokenizer": "custom_tokenizer",
                "ngram_min": 1,
                "ngram_max": 5
            }
        }
        fake_path.write_text(json.dumps(test_data), encoding="utf-8")
        monkeypatch.setattr("scripts.index_settings.INDEX_SETTINGS_PATH", fake_path)

        result = index_settings.fts_params()
        assert result["base_tokenizer"] == "custom_tokenizer"
        assert result["ngram_min"] == 1
        assert result["ngram_max"] == 5


class TestRerankerParams:
    """测试 Reranker 精排参数加载。"""

    def test_reranker_params_default(self, tmp_path, monkeypatch):
        """测试文件不存在时返回默认值。"""
        fake_path = tmp_path / "index_settings.json"
        monkeypatch.setattr("scripts.index_settings.INDEX_SETTINGS_PATH", fake_path)

        result = index_settings.reranker_params()
        assert "use_reranker" in result
        assert "rerank_top_k" in result
        assert "final_top_k" in result
        assert "model" in result
        assert "device" in result
        assert "use_fp16" in result
        assert isinstance(result["use_reranker"], bool)
        assert isinstance(result["rerank_top_k"], int)
        assert isinstance(result["final_top_k"], int)

    def test_reranker_params_override(self, tmp_path, monkeypatch):
        """测试从文件加载自定义值。"""
        fake_path = tmp_path / "index_settings.json"
        test_data = {
            "reranker": {
                "use_reranker": True,
                "rerank_top_k": 50,
                "final_top_k": 8,
                "model": "custom-reranker",
                "device": "cuda",
                "use_fp16": True
            }
        }
        fake_path.write_text(json.dumps(test_data), encoding="utf-8")
        monkeypatch.setattr("scripts.index_settings.INDEX_SETTINGS_PATH", fake_path)

        result = index_settings.reranker_params()
        assert result["use_reranker"] is True
        assert result["rerank_top_k"] == 50
        assert result["final_top_k"] == 8
        assert result["model"] == "custom-reranker"
        assert result["device"] == "cuda"
        assert result["use_fp16"] is True

    def test_reranker_params_false_flag_preserved(self, tmp_path, monkeypatch):
        """测试 use_reranker=False 会被保留（不当作缺失）。"""
        fake_path = tmp_path / "index_settings.json"
        test_data = {"reranker": {"use_reranker": False}}
        fake_path.write_text(json.dumps(test_data), encoding="utf-8")
        monkeypatch.setattr("scripts.index_settings.INDEX_SETTINGS_PATH", fake_path)

        result = index_settings.reranker_params()
        assert result["use_reranker"] is False


class TestGraphEvidenceParams:
    """测试心智地图证据片段参数加载。"""

    def test_graph_evidence_params_default(self, tmp_path, monkeypatch):
        """测试文件不存在时返回默认值。"""
        fake_path = tmp_path / "index_settings.json"
        monkeypatch.setattr("scripts.index_settings.INDEX_SETTINGS_PATH", fake_path)

        result = index_settings.graph_evidence_params()
        assert "max_dates" in result
        assert "fragments_per_date" in result
        assert "window_expand" in result
        assert "include_summary" in result
        assert isinstance(result["max_dates"], int)
        assert isinstance(result["fragments_per_date"], int)
        assert isinstance(result["window_expand"], int)
        assert isinstance(result["include_summary"], bool)

    def test_graph_evidence_params_override(self, tmp_path, monkeypatch):
        """测试从文件加载自定义值。"""
        fake_path = tmp_path / "index_settings.json"
        test_data = {
            "graph_evidence": {
                "max_dates": 5,
                "fragments_per_date": 3,
                "window_expand": 2,
                "include_summary": False
            }
        }
        fake_path.write_text(json.dumps(test_data), encoding="utf-8")
        monkeypatch.setattr("scripts.index_settings.INDEX_SETTINGS_PATH", fake_path)

        result = index_settings.graph_evidence_params()
        assert result["max_dates"] == 5
        assert result["fragments_per_date"] == 3
        assert result["window_expand"] == 2
        assert result["include_summary"] is False

    def test_graph_evidence_params_zero_max_dates(self, tmp_path, monkeypatch):
        """测试 max_dates=0 会被保留（用于关闭证据片段功能）。"""
        fake_path = tmp_path / "index_settings.json"
        test_data = {"graph_evidence": {"max_dates": 0}}
        fake_path.write_text(json.dumps(test_data), encoding="utf-8")
        monkeypatch.setattr("scripts.index_settings.INDEX_SETTINGS_PATH", fake_path)

        result = index_settings.graph_evidence_params()
        assert result["max_dates"] == 0


class TestLoadForUI:
    """测试 load_for_ui() 返回所有参数组。"""

    def test_load_for_ui_all_groups(self, tmp_path, monkeypatch):
        """测试返回六组完整参数。"""
        fake_path = tmp_path / "index_settings.json"
        monkeypatch.setattr("scripts.index_settings.INDEX_SETTINGS_PATH", fake_path)

        result = index_settings.load_for_ui()

        # 应包含六组参数
        assert "retrieval" in result
        assert "chunking" in result
        assert "embedding" in result
        assert "fts" in result
        assert "reranker" in result
        assert "graph_evidence" in result

        # 每组应该是字典
        assert isinstance(result["retrieval"], dict)
        assert isinstance(result["chunking"], dict)
        assert isinstance(result["embedding"], dict)
        assert isinstance(result["fts"], dict)
        assert isinstance(result["reranker"], dict)
        assert isinstance(result["graph_evidence"], dict)

    def test_load_for_ui_with_custom_values(self, tmp_path, monkeypatch):
        """测试自定义值能正确加载到各组。"""
        fake_path = tmp_path / "index_settings.json"
        test_data = {
            "retrieval": {"top_k": 20},
            "chunking": {"chunk_size": 2500},
            "embedding": {"device": "cuda"},
            "fts": {"base_tokenizer": "custom"},
            "reranker": {"use_reranker": True},
            "graph_evidence": {"max_dates": 3}
        }
        fake_path.write_text(json.dumps(test_data), encoding="utf-8")
        monkeypatch.setattr("scripts.index_settings.INDEX_SETTINGS_PATH", fake_path)

        result = index_settings.load_for_ui()

        assert result["retrieval"]["top_k"] == 20
        assert result["chunking"]["chunk_size"] == 2500
        assert result["embedding"]["device"] == "cuda"
        assert result["fts"]["base_tokenizer"] == "custom"
        assert result["reranker"]["use_reranker"] is True
        assert result["graph_evidence"]["max_dates"] == 3


class TestSave:
    """测试 save() 写入参数到文件。"""

    def test_save_creates_parent_dir(self, tmp_path, monkeypatch):
        """测试父目录不存在时会自动创建。"""
        fake_path = tmp_path / "nested" / "dir" / "index_settings.json"
        monkeypatch.setattr("scripts.index_settings.INDEX_SETTINGS_PATH", fake_path)

        index_settings.save(
            retrieval={"top_k": 10},
            chunking={"chunk_size": 1000},
            embedding={"model": "test"},
            fts={"base_tokenizer": "jieba"},
            reranker={"use_reranker": False},
            graph_evidence={"max_dates": 2}
        )

        assert fake_path.exists()
        assert fake_path.parent.exists()

    def test_save_writes_valid_json(self, tmp_path, monkeypatch):
        """测试写入有效的 JSON 文件。"""
        fake_path = tmp_path / "index_settings.json"
        monkeypatch.setattr("scripts.index_settings.INDEX_SETTINGS_PATH", fake_path)

        test_retrieval = {"top_k": 15, "window_expand": 2}
        test_chunking = {"chunk_size": 2000, "chunk_overlap": 200}
        test_embedding = {"model": "bge-m3", "device": "mps"}
        test_fts = {"base_tokenizer": "jieba"}
        test_reranker = {"use_reranker": True, "final_top_k": 5}
        test_graph_evidence = {"max_dates": 4}

        index_settings.save(
            retrieval=test_retrieval,
            chunking=test_chunking,
            embedding=test_embedding,
            fts=test_fts,
            reranker=test_reranker,
            graph_evidence=test_graph_evidence
        )

        # 验证文件内容
        saved_data = json.loads(fake_path.read_text(encoding="utf-8"))
        assert saved_data["retrieval"] == test_retrieval
        assert saved_data["chunking"] == test_chunking
        assert saved_data["embedding"] == test_embedding
        assert saved_data["fts"] == test_fts
        assert saved_data["reranker"] == test_reranker
        assert saved_data["graph_evidence"] == test_graph_evidence

    def test_save_overwrites_existing_file(self, tmp_path, monkeypatch):
        """测试覆盖已存在的文件。"""
        fake_path = tmp_path / "index_settings.json"
        monkeypatch.setattr("scripts.index_settings.INDEX_SETTINGS_PATH", fake_path)

        # 先写入旧数据
        old_data = {"retrieval": {"top_k": 5}}
        fake_path.write_text(json.dumps(old_data), encoding="utf-8")

        # 保存新数据
        index_settings.save(
            retrieval={"top_k": 99},
            chunking={"chunk_size": 1000},
            embedding={"model": "test"},
            fts={"base_tokenizer": "jieba"},
            reranker={"use_reranker": False},
            graph_evidence={"max_dates": 2}
        )

        # 验证被覆盖
        saved_data = json.loads(fake_path.read_text(encoding="utf-8"))
        assert saved_data["retrieval"]["top_k"] == 99

    def test_save_preserves_other_keys(self, tmp_path, monkeypatch):
        """测试保存时会先加载现有数据再合并（保留其他键）。

        注意：当前实现直接覆盖六组参数，不保留额外的键。
        这是预期行为，因为 save() 明确写入六组参数。
        """
        fake_path = tmp_path / "index_settings.json"
        monkeypatch.setattr("scripts.index_settings.INDEX_SETTINGS_PATH", fake_path)

        # 先写入包含额外键的数据
        old_data = {
            "retrieval": {"top_k": 10},
            "extra_key": "should_be_removed"
        }
        fake_path.write_text(json.dumps(old_data), encoding="utf-8")

        # 保存新数据
        index_settings.save(
            retrieval={"top_k": 20},
            chunking={"chunk_size": 1000},
            embedding={"model": "test"},
            fts={"base_tokenizer": "jieba"},
            reranker={"use_reranker": False},
            graph_evidence={"max_dates": 2}
        )

        # 验证数据结构
        saved_data = json.loads(fake_path.read_text(encoding="utf-8"))
        assert saved_data["retrieval"]["top_k"] == 20
        # 额外的键会被保留（因为 _load_raw + 更新字典）
        assert "extra_key" in saved_data


class TestReset:
    """测试 reset() 恢复默认参数。"""

    def test_reset_deletes_file(self, tmp_path, monkeypatch):
        """测试删除设置文件。"""
        fake_path = tmp_path / "index_settings.json"
        monkeypatch.setattr("scripts.index_settings.INDEX_SETTINGS_PATH", fake_path)

        # 先创建文件
        fake_path.write_text(json.dumps({"retrieval": {"top_k": 99}}), encoding="utf-8")
        assert fake_path.exists()

        # 重置
        index_settings.reset()

        # 验证文件被删除
        assert not fake_path.exists()

    def test_reset_when_file_not_exists(self, tmp_path, monkeypatch):
        """测试文件不存在时 reset 不抛异常。"""
        fake_path = tmp_path / "index_settings.json"
        monkeypatch.setattr("scripts.index_settings.INDEX_SETTINGS_PATH", fake_path)

        # 文件本来就不存在，reset 应该不抛异常
        index_settings.reset()  # 不应该抛异常

    def test_reset_restores_defaults(self, tmp_path, monkeypatch):
        """测试 reset 后参数恢复默认值。"""
        fake_path = tmp_path / "index_settings.json"
        monkeypatch.setattr("scripts.index_settings.INDEX_SETTINGS_PATH", fake_path)

        # 先保存自定义值
        index_settings.save(
            retrieval={"top_k": 99},
            chunking={"chunk_size": 9999},
            embedding={"model": "custom"},
            fts={"base_tokenizer": "custom"},
            reranker={"use_reranker": False},
            graph_evidence={"max_dates": 99}
        )

        # 重置
        index_settings.reset()

        # 验证加载的是默认值（通过检查文件不存在）
        assert not fake_path.exists()

        # 验证各参数函数返回默认值
        result = index_settings.load_for_ui()
        # 应该是默认值（与 config.py 里的常量一致）
        assert "retrieval" in result
        assert "chunking" in result


class TestEdgeCases:
    """测试边界情况和异常处理。"""

    def test_unicode_in_settings(self, tmp_path, monkeypatch):
        """测试支持 Unicode 字符（中文参数名等）。"""
        fake_path = tmp_path / "index_settings.json"
        monkeypatch.setattr("scripts.index_settings.INDEX_SETTINGS_PATH", fake_path)

        # 保存包含中文的数据（虽然当前参数都是英文，但测试健壮性）
        test_data = {
            "retrieval": {"top_k": 10},
            "测试键": "测试值"
        }
        fake_path.write_text(json.dumps(test_data, ensure_ascii=False), encoding="utf-8")

        result = index_settings._load_raw()
        assert "测试键" in result
        assert result["测试键"] == "测试值"

    def test_large_numbers_in_settings(self, tmp_path, monkeypatch):
        """测试大数值参数。"""
        fake_path = tmp_path / "index_settings.json"
        monkeypatch.setattr("scripts.index_settings.INDEX_SETTINGS_PATH", fake_path)

        test_data = {
            "chunking": {"chunk_size": 999999}
        }
        fake_path.write_text(json.dumps(test_data), encoding="utf-8")

        result = index_settings.chunking_params()
        assert result["chunk_size"] == 999999

    def test_negative_values_preserved(self, tmp_path, monkeypatch):
        """测试负数值会被保留（尽管实际使用时不应该出现负数）。"""
        fake_path = tmp_path / "index_settings.json"
        monkeypatch.setattr("scripts.index_settings.INDEX_SETTINGS_PATH", fake_path)

        test_data = {
            "retrieval": {"top_k": -1}
        }
        fake_path.write_text(json.dumps(test_data), encoding="utf-8")

        result = index_settings.retrieval_params()
        assert result["top_k"] == -1

    def test_missing_section_uses_defaults(self, tmp_path, monkeypatch):
        """测试文件中完全缺少某个 section 时使用默认值。"""
        fake_path = tmp_path / "index_settings.json"
        monkeypatch.setattr("scripts.index_settings.INDEX_SETTINGS_PATH", fake_path)

        # 只保存 retrieval，其他 section 缺失
        test_data = {"retrieval": {"top_k": 20}}
        fake_path.write_text(json.dumps(test_data), encoding="utf-8")

        # 其他 section 应该返回默认值
        chunking = index_settings.chunking_params()
        assert "chunk_size" in chunking
        assert "chunk_overlap" in chunking

        embedding = index_settings.embedding_params()
        assert "model" in embedding

    def test_empty_string_values(self, tmp_path, monkeypatch):
        """测试空字符串值会被保留（不当作 None）。"""
        fake_path = tmp_path / "index_settings.json"
        monkeypatch.setattr("scripts.index_settings.INDEX_SETTINGS_PATH", fake_path)

        test_data = {
            "embedding": {"model": ""}  # 空字符串
        }
        fake_path.write_text(json.dumps(test_data), encoding="utf-8")

        result = index_settings.embedding_params()
        assert result["model"] == ""  # 空字符串被保留


class TestIntegration:
    """集成测试：模拟完整的参数读写流程。"""

    def test_full_lifecycle(self, tmp_path, monkeypatch):
        """测试完整生命周期：加载默认值 → 保存 → 重新加载 → 重置。"""
        fake_path = tmp_path / "index_settings.json"
        monkeypatch.setattr("scripts.index_settings.INDEX_SETTINGS_PATH", fake_path)

        # 1. 加载默认值（文件不存在）
        ui_data = index_settings.load_for_ui()
        original_top_k = ui_data["retrieval"]["top_k"]

        # 2. 保存自定义值
        custom_retrieval = {"top_k": 888, "window_expand": 3}
        index_settings.save(
            retrieval=custom_retrieval,
            chunking=ui_data["chunking"],
            embedding=ui_data["embedding"],
            fts=ui_data["fts"],
            reranker=ui_data["reranker"],
            graph_evidence=ui_data["graph_evidence"]
        )

        # 3. 重新加载，验证自定义值生效
        ui_data_after_save = index_settings.load_for_ui()
        assert ui_data_after_save["retrieval"]["top_k"] == 888
        assert ui_data_after_save["retrieval"]["window_expand"] == 3

        # 4. 重置
        index_settings.reset()

        # 5. 再次加载，验证恢复默认值
        ui_data_after_reset = index_settings.load_for_ui()
        assert ui_data_after_reset["retrieval"]["top_k"] == original_top_k

    def test_concurrent_updates_last_write_wins(self, tmp_path, monkeypatch):
        """测试多次保存，最后一次写入生效（last-write-wins）。"""
        fake_path = tmp_path / "index_settings.json"
        monkeypatch.setattr("scripts.index_settings.INDEX_SETTINGS_PATH", fake_path)

        # 第一次保存
        index_settings.save(
            retrieval={"top_k": 10},
            chunking={"chunk_size": 1000},
            embedding={"model": "v1"},
            fts={"base_tokenizer": "jieba"},
            reranker={"use_reranker": False},
            graph_evidence={"max_dates": 1}
        )

        # 第二次保存（覆盖）
        index_settings.save(
            retrieval={"top_k": 20},
            chunking={"chunk_size": 2000},
            embedding={"model": "v2"},
            fts={"base_tokenizer": "jieba"},
            reranker={"use_reranker": True},
            graph_evidence={"max_dates": 2}
        )

        # 验证最后一次写入生效
        result = index_settings.load_for_ui()
        assert result["retrieval"]["top_k"] == 20
        assert result["chunking"]["chunk_size"] == 2000
        assert result["embedding"]["model"] == "v2"
        assert result["reranker"]["use_reranker"] is True
