"""集成测试：完整 RAG 流程。

测试 parse → chunk → ingest → retrieve 完整流程（真 LanceDB + 确定性假向量，不出网）。

和 tests/unit/ 的分工：单元测试逐个函数验边界，这里只验"接起来还能跑通"——
数据在模块之间传递时的格式契约（Chunk dataclass → dict → LanceDB row → 检索结果）
恰恰是单元测试各自 mock 掉邻居后看不见的地方。

参考：LangChain 的集成测试分层
"""
from dataclasses import asdict

import pytest

from config import EMBEDDING_DIM

# 标记为集成测试（需要 --integration flag）
pytestmark = pytest.mark.integration


class TestFullRAGPipeline:
    """完整 RAG 流程测试。"""

    def test_parse_to_chunk(self, tmp_path, sample_transcript):
        """测试 parse → chunk 流程。"""
        from scripts.chunk import chunk_session
        from scripts.parse import parse_transcript

        # 1. 写入测试文件
        test_file = tmp_path / "20240101120000_test.txt"
        test_file.write_text(sample_transcript, encoding="utf-8")

        # 2. Parse
        session = parse_transcript(test_file)
        assert len(session.utterances) > 0
        assert session.session_date == "2024-01-01"

        # 3. Chunk
        chunks = chunk_session(session)
        assert len(chunks) > 0
        assert chunks[0].source_file == "20240101120000_test.txt"
        # 契约：text 带上下文前缀（喂 embedding/FTS），raw_text 是原始拼接（喂 LLM/展示）
        assert chunks[0].raw_text in chunks[0].text
        assert chunks[0].text != chunks[0].raw_text
        # 链表指针：首块无前驱，块间首尾相接
        assert chunks[0].prev_chunk_id is None
        for prev, nxt in zip(chunks, chunks[1:]):
            assert prev.next_chunk_id == nxt.id
            assert nxt.prev_chunk_id == prev.id

    def test_chunk_to_ingest(self, tmp_path, sample_transcript, deterministic_embed):
        """测试 chunk → ingest 流程（确定性假向量，不加载 BGE-M3）。"""
        from scripts.chunk import chunk_session
        from scripts.ingest import build_rows
        from scripts.parse import parse_transcript

        # 1. 准备数据
        test_file = tmp_path / "20240101120000_test.txt"
        test_file.write_text(sample_transcript, encoding="utf-8")

        session = parse_transcript(test_file)
        chunks = chunk_session(session)

        # 2. Build rows
        #    ⚠️ build_rows() 吃的是 list[dict] 不是 list[Chunk]（它用 c["text"] 下标取值），
        #    生产里的调用方 scripts/ingest_new.py 也是先 asdict() 再传。
        rows = build_rows([asdict(c) for c in chunks])

        assert len(rows) == len(chunks)
        assert "vector" in rows[0]
        assert len(rows[0]["vector"]) == EMBEDDING_DIM
        # LanceDB 不接受 None，build_rows 要把空指针转成空串
        assert rows[0]["prev_chunk_id"] == ""
        # speakers → speaker 的列名改写别丢
        assert rows[0]["speaker"] == chunks[0].speakers

    @pytest.mark.slow
    def test_full_pipeline_without_llm(self, tmp_path, sample_transcript,
                                       deterministic_embed, no_reranker):
        """测试完整流程（真 LanceDB，不调 LLM）。

        parse → chunk → ingest → retrieve（不含 build_graph，太慢）
        """
        from scripts.ask import retrieve
        from scripts.chunk import chunk_session
        from scripts.ingest import ingest
        from scripts.parse import parse_transcript

        # 1. Parse & Chunk
        test_file = tmp_path / "20240101120000_test.txt"
        test_file.write_text(sample_transcript, encoding="utf-8")

        session = parse_transcript(test_file)
        chunks = chunk_session(session)

        # 2. Ingest 到当前（隔离后的）workspace，真的建表 + 建 FTS 索引
        table = ingest([asdict(c) for c in chunks])
        assert table.count_rows() == len(chunks)

        # 3. Retrieve：查询词是语料里的原字串，ngram FTS 那条腿必然命中
        #    （dense 那条腿在假向量下近正交，指望不上——hybrid 的冗余在这里帮了忙）
        results = retrieve("工作压力", k=5)
        assert len(results) > 0
        hit = results[0]
        assert hit["source_file"] == "20240101120000_test.txt"
        assert hit["session_date"] == "2024-01-01"
        assert "压力" in hit["text"]
        assert hit["rank"] == 0  # rank 从 0 起算（越小越相关）
        # 关了 reranker → 没有分数可比，下游不该把 None 当低分
        assert hit["score"] is None
        assert hit["below_threshold"] is False


class TestWorkspaceIsolation:
    """Workspace 隔离测试。"""

    def test_multiple_workspaces_isolated(self):
        """测试多个 workspace 的数据隔离。"""
        from scripts.workspace_manager import create_workspace, get_workspace_dir

        # 路径隔离由 conftest 的 autouse isolate_data_root 负责（钉住 WORKSPACES_ROOT），
        # 测试自己 patch PRIVATE_DIR 是无效的——WORKSPACES_ROOT 是 import-time 求值的常量。
        create_workspace("ws1", "Workspace 1", "generic")
        create_workspace("ws2", "Workspace 2", "generic")

        ws1_dir = get_workspace_dir("ws1")
        ws2_dir = get_workspace_dir("ws2")

        # 验证隔离
        assert ws1_dir != ws2_dir
        assert ws1_dir.exists()
        assert ws2_dir.exists()

        # 写入数据到 ws1
        (ws1_dir / "data" / "test.txt").write_text("ws1 data", encoding="utf-8")

        # ws2 不应该看到 ws1 的数据
        assert not (ws2_dir / "data" / "test.txt").exists()
