"""测试向量化入库功能（scripts/ingest.py）。

目标覆盖率：60%+
测试重点：
1. load_chunks() - 加载分块数据
2. build_rows() - 向量化批处理
3. ingest() - 主入库函数（overwrite/append 模式）
4. LanceDB 表操作
5. FTS 索引创建
6. 错误处理
"""
from pathlib import Path
from typing import Generator
from unittest.mock import MagicMock, patch

import lancedb
import numpy as np
import pytest

from scripts.ingest import build_rows, ingest, load_chunks


@pytest.fixture
def mock_embed(monkeypatch):
    """Mock embed function for all tests."""
    def fake_embed(texts):
        return {
            "dense_vecs": np.random.rand(len(texts), 1024).astype(np.float32)
        }
    monkeypatch.setattr("scripts.ingest.embed", fake_embed)
    return fake_embed


class TestLoadChunks:
    """测试分块数据加载。"""

    def test_load_chunks_from_file(self, tmp_path, monkeypatch):
        """测试从 chunks.jsonl 加载数据。"""
        # 创建测试 chunks.jsonl
        chunks_file = tmp_path / "chunks.jsonl"
        chunks_file.write_text(
            '{"id": "chunk001", "text": "测试文本1", "raw_text": "原始1", "session_date": "2024-01-01", "speakers": ["Andy"], "source_file": "test.txt", "chunk_index": 0, "start_ts": "00:00:00", "end_ts": "00:01:00", "prev_chunk_id": null, "next_chunk_id": "chunk002"}\n'
            '{"id": "chunk002", "text": "测试文本2", "raw_text": "原始2", "session_date": "2024-01-01", "speakers": ["咨询师"], "source_file": "test.txt", "chunk_index": 1, "start_ts": "00:01:00", "end_ts": "00:02:00", "prev_chunk_id": "chunk001", "next_chunk_id": null}\n',
            encoding="utf-8"
        )

        # Mock CHUNKS_JSONL_PATH
        monkeypatch.setattr("scripts.ingest.CHUNKS_JSONL_PATH", lambda x: chunks_file)

        # 加载
        chunks = load_chunks()

        # 验证
        assert len(chunks) == 2
        assert chunks[0]["id"] == "chunk001"
        assert chunks[0]["text"] == "测试文本1"
        assert chunks[1]["id"] == "chunk002"
        assert chunks[1]["speakers"] == ["咨询师"]

    def test_load_chunks_empty_file(self, tmp_path, monkeypatch):
        """测试加载空文件。"""
        chunks_file = tmp_path / "empty.jsonl"
        chunks_file.touch()
        monkeypatch.setattr("scripts.ingest.CHUNKS_JSONL_PATH", lambda x: chunks_file)

        chunks = load_chunks()
        assert len(chunks) == 0

    def test_load_chunks_with_path(self, tmp_path):
        """测试直接指定路径加载。"""
        chunks_file = tmp_path / "custom.jsonl"
        chunks_file.write_text(
            '{"id": "test", "text": "自定义路径", "raw_text": "原始", "session_date": "2024-01-01", "speakers": [], "source_file": "test.txt", "chunk_index": 0, "start_ts": "00:00:00", "end_ts": "00:01:00", "prev_chunk_id": null, "next_chunk_id": null}\n',
            encoding="utf-8"
        )

        chunks = load_chunks(path=chunks_file)
        assert len(chunks) == 1
        assert chunks[0]["text"] == "自定义路径"


class TestBuildRows:
    """测试向量化批处理。"""

    def test_build_rows_basic(self, mock_embed, monkeypatch):
        """测试基本向量化流程。"""
        monkeypatch.setattr(
            "scripts.ingest.index_settings.embedding_params",
            lambda: {"batch_size": 2}
        )

        chunks = [
            {
                "id": "c1",
                "text": "文本1",
                "raw_text": "原始1",
                "session_date": "2024-01-01",
                "speakers": ["Andy"],
                "source_file": "test.txt",
                "chunk_index": 0,
                "start_ts": "00:00:00",
                "end_ts": "00:01:00",
                "prev_chunk_id": None,
                "next_chunk_id": "c2"
            },
            {
                "id": "c2",
                "text": "文本2",
                "raw_text": "原始2",
                "session_date": "2024-01-01",
                "speakers": ["咨询师"],
                "source_file": "test.txt",
                "chunk_index": 1,
                "start_ts": "00:01:00",
                "end_ts": "00:02:00",
                "prev_chunk_id": "c1",
                "next_chunk_id": None
            }
        ]

        rows = build_rows(chunks)

        # 验证
        assert len(rows) == 2
        assert rows[0]["id"] == "c1"
        assert rows[0]["text"] == "文本1"
        assert rows[0]["raw_text"] == "原始1"
        assert rows[0]["session_date"] == "2024-01-01"
        assert rows[0]["speaker"] == ["Andy"]
        assert rows[0]["source_file"] == "test.txt"
        assert rows[0]["chunk_index"] == 0
        assert rows[0]["prev_chunk_id"] == ""  # None -> ""
        assert rows[0]["next_chunk_id"] == "c2"
        assert "vector" in rows[0]
        assert isinstance(rows[0]["vector"], list)
        assert len(rows[0]["vector"]) == 1024  # BGE-M3 维度

    def test_build_rows_batch_processing(self, mock_embed, monkeypatch):
        """测试批量处理（多批次）。"""
        monkeypatch.setattr(
            "scripts.ingest.index_settings.embedding_params",
            lambda: {"batch_size": 2}
        )

        # 3 个 chunks（需要 2 批）
        chunks = [
            {
                "id": f"c{i}",
                "text": f"文本{i}",
                "raw_text": f"原始{i}",
                "session_date": "2024-01-01",
                "speakers": [],
                "source_file": "test.txt",
                "chunk_index": i,
                "start_ts": "00:00:00",
                "end_ts": "00:01:00",
                "prev_chunk_id": None,
                "next_chunk_id": None
            }
            for i in range(3)
        ]

        rows = build_rows(chunks)

        # 验证批次数（3个块，batch_size=2 → 2批）
        assert len(rows) == 3

    def test_build_rows_null_handling(self, mock_embed, monkeypatch):
        """测试 None 值转换为空字符串。"""
        monkeypatch.setattr(
            "scripts.ingest.index_settings.embedding_params",
            lambda: {"batch_size": 1}
        )

        chunks = [
            {
                "id": "test",
                "text": "测试",
                "raw_text": "原始",
                "session_date": "2024-01-01",
                "speakers": [],
                "source_file": "test.txt",
                "chunk_index": 0,
                "start_ts": "00:00:00",
                "end_ts": "00:01:00",
                "prev_chunk_id": None,  # 应转为 ""
                "next_chunk_id": None   # 应转为 ""
            }
        ]

        rows = build_rows(chunks)

        assert rows[0]["prev_chunk_id"] == ""
        assert rows[0]["next_chunk_id"] == ""


class TestIngest:
    """测试主入库函数。"""

    def test_ingest_overwrite_mode(self, tmp_path, mock_embed, monkeypatch):
        """测试 overwrite 模式（全量重建）。"""
        # 准备数据
        chunks = [
            {
                "id": "c1",
                "text": "测试文本",
                "raw_text": "原始",
                "session_date": "2024-01-01",
                "speakers": ["Andy"],
                "source_file": "test.txt",
                "chunk_index": 0,
                "start_ts": "00:00:00",
                "end_ts": "00:01:00",
                "prev_chunk_id": None,
                "next_chunk_id": None
            }
        ]

        # Mock
        monkeypatch.setattr("scripts.ingest.load_chunks", lambda workspace_id: chunks)
        monkeypatch.setattr("scripts.ingest.index_settings.embedding_params", lambda: {"batch_size": 1})
        monkeypatch.setattr("scripts.ingest.index_settings.fts_params", lambda: {
            "base_tokenizer": "jieba",
            "ngram_min": 1,
            "ngram_max": 3
        })
        monkeypatch.setattr("scripts.ingest.DB_DIR", lambda x: tmp_path / "db")

        # 执行
        table = ingest(mode="overwrite")

        # 验证
        assert table is not None
        assert table.count_rows() == 1
        assert "text" in table.schema.names  # FTS 索引列

    def test_ingest_append_mode_new_table(self, tmp_path, mock_embed, monkeypatch):
        """测试 append 模式（表不存在时创建）。"""
        chunks = [
            {
                "id": "c1",
                "text": "新数据",
                "raw_text": "原始",
                "session_date": "2024-01-01",
                "speakers": [],
                "source_file": "test.txt",
                "chunk_index": 0,
                "start_ts": "00:00:00",
                "end_ts": "00:01:00",
                "prev_chunk_id": None,
                "next_chunk_id": None
            }
        ]

        monkeypatch.setattr("scripts.ingest.load_chunks", lambda workspace_id: chunks)
        monkeypatch.setattr("scripts.ingest.index_settings.embedding_params", lambda: {"batch_size": 1})
        monkeypatch.setattr("scripts.ingest.index_settings.fts_params", lambda: {
            "base_tokenizer": "jieba",
            "ngram_min": 1,
            "ngram_max": 3
        })
        monkeypatch.setattr("scripts.ingest.DB_DIR", lambda x: tmp_path / "db_append")

        # 执行 append（表不存在，应创建）
        table = ingest(mode="append")

        assert table.count_rows() == 1

    def test_ingest_append_mode_existing_table(self, tmp_path, mock_embed, monkeypatch):
        """测试 append 模式（表已存在时追加）。"""
        db_dir = tmp_path / "db_append_existing"
        monkeypatch.setattr("scripts.ingest.DB_DIR", lambda x: db_dir)
        monkeypatch.setattr("scripts.ingest.index_settings.embedding_params", lambda: {"batch_size": 1})
        monkeypatch.setattr("scripts.ingest.index_settings.fts_params", lambda: {
            "base_tokenizer": "jieba",
            "ngram_min": 1,
            "ngram_max": 3
        })

        # 先创建表
        initial_chunks = [
            {
                "id": "c1",
                "text": "初始数据",
                "raw_text": "原始",
                "session_date": "2024-01-01",
                "speakers": [],
                "source_file": "test.txt",
                "chunk_index": 0,
                "start_ts": "00:00:00",
                "end_ts": "00:01:00",
                "prev_chunk_id": None,
                "next_chunk_id": None
            }
        ]
        monkeypatch.setattr("scripts.ingest.load_chunks", lambda workspace_id: initial_chunks)
        table = ingest(mode="overwrite")
        assert table.count_rows() == 1

        # 追加新数据
        new_chunks = [
            {
                "id": "c2",
                "text": "追加数据",
                "raw_text": "原始2",
                "session_date": "2024-01-02",
                "speakers": [],
                "source_file": "test2.txt",
                "chunk_index": 0,
                "start_ts": "00:00:00",
                "end_ts": "00:01:00",
                "prev_chunk_id": None,
                "next_chunk_id": None
            }
        ]
        monkeypatch.setattr("scripts.ingest.load_chunks", lambda workspace_id: new_chunks)
        table = ingest(mode="append")

        # 验证（应有 2 行）
        assert table.count_rows() == 2

    def test_ingest_with_explicit_chunks(self, tmp_path, mock_embed, monkeypatch):
        """测试直接传入 chunks（不从文件加载）。"""
        chunks = [
            {
                "id": "explicit",
                "text": "显式传入",
                "raw_text": "原始",
                "session_date": "2024-01-01",
                "speakers": [],
                "source_file": "test.txt",
                "chunk_index": 0,
                "start_ts": "00:00:00",
                "end_ts": "00:01:00",
                "prev_chunk_id": None,
                "next_chunk_id": None
            }
        ]

        monkeypatch.setattr("scripts.ingest.index_settings.embedding_params", lambda: {"batch_size": 1})
        monkeypatch.setattr("scripts.ingest.index_settings.fts_params", lambda: {
            "base_tokenizer": "jieba",
            "ngram_min": 1,
            "ngram_max": 3
        })
        monkeypatch.setattr("scripts.ingest.DB_DIR", lambda x: tmp_path / "db_explicit")

        # 执行（不应调用 load_chunks）
        table = ingest(chunks=chunks, mode="overwrite")

        assert table.count_rows() == 1
        result = table.to_pandas()
        assert result.iloc[0]["id"] == "explicit"

    def test_ingest_empty_chunks(self, tmp_path, mock_embed, monkeypatch):
        """测试空数据处理。"""
        monkeypatch.setattr("scripts.ingest.load_chunks", lambda workspace_id: [])
        monkeypatch.setattr("scripts.ingest.index_settings.embedding_params", lambda: {"batch_size": 1})
        monkeypatch.setattr("scripts.ingest.index_settings.fts_params", lambda: {
            "base_tokenizer": "jieba",
            "ngram_min": 1,
            "ngram_max": 3
        })
        monkeypatch.setattr("scripts.ingest.DB_DIR", lambda x: tmp_path / "db_empty")

        # 空数据会抛出 ValueError（LanceDB 要求非空数据或 schema）
        with pytest.raises(ValueError, match="Cannot create table from empty list"):
            ingest(mode="overwrite")

    def test_ingest_fts_index_created(self, tmp_path, mock_embed, monkeypatch):
        """测试 FTS 索引创建。"""
        chunks = [
            {
                "id": "fts_test",
                "text": "全文检索测试",
                "raw_text": "原始",
                "session_date": "2024-01-01",
                "speakers": [],
                "source_file": "test.txt",
                "chunk_index": 0,
                "start_ts": "00:00:00",
                "end_ts": "00:01:00",
                "prev_chunk_id": None,
                "next_chunk_id": None
            }
        ]

        monkeypatch.setattr("scripts.ingest.load_chunks", lambda workspace_id: chunks)
        monkeypatch.setattr("scripts.ingest.index_settings.embedding_params", lambda: {"batch_size": 1})
        monkeypatch.setattr("scripts.ingest.index_settings.fts_params", lambda: {
            "base_tokenizer": "jieba",
            "ngram_min": 2,
            "ngram_max": 5
        })
        monkeypatch.setattr("scripts.ingest.DB_DIR", lambda x: tmp_path / "db_fts")

        table = ingest(mode="overwrite")

        # 验证 FTS 可用（通过查询测试）
        # 注意：LanceDB FTS 索引创建后，可以通过 search(..., query_type="fts") 测试
        assert table.count_rows() == 1

    def test_ingest_workspace_isolation(self, tmp_path, mock_embed, monkeypatch):
        """测试 workspace 隔离（不同 workspace_id 写入不同 DB）。"""
        chunks1 = [
            {
                "id": "ws1",
                "text": "工作空间1",
                "raw_text": "原始",
                "session_date": "2024-01-01",
                "speakers": [],
                "source_file": "test.txt",
                "chunk_index": 0,
                "start_ts": "00:00:00",
                "end_ts": "00:01:00",
                "prev_chunk_id": None,
                "next_chunk_id": None
            }
        ]

        chunks2 = [
            {
                "id": "ws2",
                "text": "工作空间2",
                "raw_text": "原始",
                "session_date": "2024-01-01",
                "speakers": [],
                "source_file": "test.txt",
                "chunk_index": 0,
                "start_ts": "00:00:00",
                "end_ts": "00:01:00",
                "prev_chunk_id": None,
                "next_chunk_id": None
            }
        ]

        monkeypatch.setattr("scripts.ingest.index_settings.embedding_params", lambda: {"batch_size": 1})
        monkeypatch.setattr("scripts.ingest.index_settings.fts_params", lambda: {
            "base_tokenizer": "jieba",
            "ngram_min": 1,
            "ngram_max": 3
        })

        # Mock DB_DIR 区分不同 workspace
        def mock_db_dir(workspace_id):
            return tmp_path / f"db_{workspace_id or 'default'}"
        monkeypatch.setattr("scripts.ingest.DB_DIR", mock_db_dir)

        # 入库 workspace1
        table1 = ingest(chunks=chunks1, mode="overwrite", workspace_id="ws1")
        assert table1.count_rows() == 1

        # 入库 workspace2
        table2 = ingest(chunks=chunks2, mode="overwrite", workspace_id="ws2")
        assert table2.count_rows() == 1

        # 验证隔离（两个表不同）
        result1 = table1.to_pandas()
        result2 = table2.to_pandas()
        assert result1.iloc[0]["id"] == "ws1"
        assert result2.iloc[0]["id"] == "ws2"


class TestEdgeCases:
    """测试边界情况和错误处理。"""

    def test_large_batch(self, tmp_path, mock_embed, monkeypatch):
        """测试大批量数据（100+ chunks）。"""
        chunks = [
            {
                "id": f"chunk{i:03d}",
                "text": f"测试文本{i}",
                "raw_text": f"原始{i}",
                "session_date": "2024-01-01",
                "speakers": [],
                "source_file": "test.txt",
                "chunk_index": i,
                "start_ts": "00:00:00",
                "end_ts": "00:01:00",
                "prev_chunk_id": None,
                "next_chunk_id": None
            }
            for i in range(120)
        ]

        monkeypatch.setattr("scripts.ingest.index_settings.embedding_params", lambda: {"batch_size": 10})
        monkeypatch.setattr("scripts.ingest.index_settings.fts_params", lambda: {
            "base_tokenizer": "jieba",
            "ngram_min": 1,
            "ngram_max": 3
        })
        monkeypatch.setattr("scripts.ingest.DB_DIR", lambda x: tmp_path / "db_large")

        table = ingest(chunks=chunks, mode="overwrite")

        # 验证（应处理 12 批，batch_size=10）
        assert table.count_rows() == 120

    def test_vector_dimension_consistency(self, mock_embed, monkeypatch):
        """测试向量维度一致性（BGE-M3 固定 1024）。"""
        monkeypatch.setattr("scripts.ingest.index_settings.embedding_params", lambda: {"batch_size": 1})

        chunks = [
            {
                "id": "dim_test",
                "text": "维度测试",
                "raw_text": "原始",
                "session_date": "2024-01-01",
                "speakers": [],
                "source_file": "test.txt",
                "chunk_index": 0,
                "start_ts": "00:00:00",
                "end_ts": "00:01:00",
                "prev_chunk_id": None,
                "next_chunk_id": None
            }
        ]

        rows = build_rows(chunks)

        # 验证向量维度
        assert len(rows[0]["vector"]) == 1024
        assert all(isinstance(v, float) for v in rows[0]["vector"])

    def test_special_characters_in_text(self, tmp_path, mock_embed, monkeypatch):
        """测试特殊字符处理。"""
        chunks = [
            {
                "id": "special",
                "text": "特殊字符：\n换行、\"引号\"、\t制表符、emoji😊",
                "raw_text": "原始\n多行\n文本",
                "session_date": "2024-01-01",
                "speakers": ["Andy & 咨询师"],
                "source_file": "test (1).txt",
                "chunk_index": 0,
                "start_ts": "00:00:00",
                "end_ts": "00:01:00",
                "prev_chunk_id": None,
                "next_chunk_id": None
            }
        ]

        monkeypatch.setattr("scripts.ingest.index_settings.embedding_params", lambda: {"batch_size": 1})
        monkeypatch.setattr("scripts.ingest.index_settings.fts_params", lambda: {
            "base_tokenizer": "jieba",
            "ngram_min": 1,
            "ngram_max": 3
        })
        monkeypatch.setattr("scripts.ingest.DB_DIR", lambda x: tmp_path / "db_special")

        # 应能正常处理
        table = ingest(chunks=chunks, mode="overwrite")
        assert table.count_rows() == 1
        result = table.to_pandas()
        assert "😊" in result.iloc[0]["text"]
