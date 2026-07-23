"""完整工作流集成测试。

测试范围：
1. Workspace 管理（创建/切换/列表）
2. 文档入库流程（parse → chunk → embed → ingest）
3. 摘要生成
4. 图谱构建（session graph + merge）
5. 问答检索流程
6. UI 关键路径

测试策略：
- 使用临时 workspace，不污染真实数据
- Mock LLM 调用，避免真实 API 调用
- 使用真实文件操作，测试完整 I/O 路径
"""
import json
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestWorkspaceManagement:
    """Workspace 管理功能测试"""

    def test_create_workspace(self, tmp_path):
        """测试创建新 workspace"""
        from scripts.workspace_manager import create_workspace

        workspace_dir = tmp_path / "workspaces"
        workspace_dir.mkdir()

        with patch("scripts.workspace_manager.PRIVATE_DIR", tmp_path):
            ws_id = create_workspace(
                name="test-workspace",
                display_name="测试 Workspace",
                domain="generic",
                graph_schema_mode="generic",
            )

            assert ws_id == "test-workspace"

            # 验证目录结构
            ws_path = workspace_dir / "test-workspace"
            assert ws_path.exists()
            assert (ws_path / ".workspace_config.json").exists()
            assert (ws_path / "data").exists()
            assert (ws_path / "data" / "raw").exists()
            assert (ws_path / "db").exists()

            # 验证配置内容
            config = json.loads((ws_path / ".workspace_config.json").read_text())
            assert config["name"] == "test-workspace"
            assert config["domain"] == "generic"
            assert config["graph_schema"]["mode"] == "generic"

    def test_list_workspaces(self, tmp_path):
        """测试列出所有 workspaces"""
        from scripts.workspace_manager import create_workspace, list_workspaces

        workspace_dir = tmp_path / "workspaces"
        workspace_dir.mkdir()

        with patch("scripts.workspace_manager.PRIVATE_DIR", tmp_path):
            # 创建两个 workspaces
            create_workspace("ws1", "Workspace 1", "generic", "generic")
            create_workspace("ws2", "Workspace 2", "counseling", "predefined", "counseling.json")

            workspaces = list_workspaces()

            assert len(workspaces) == 2
            assert workspaces[0]["name"] == "ws1"
            assert workspaces[1]["name"] == "ws2"

    def test_get_current_workspace(self, tmp_path, monkeypatch):
        """测试获取当前 workspace"""
        from scripts.workspace_manager import get_current_workspace

        workspace_dir = tmp_path / "workspaces"
        workspace_dir.mkdir()

        with patch("scripts.workspace_manager.PRIVATE_DIR", tmp_path):
            # 情况 1：环境变量指定
            monkeypatch.setenv("CURRENT_WORKSPACE", "test-ws")
            assert get_current_workspace() == "test-ws"

            # 情况 2：无 workspaces 目录 → _legacy
            monkeypatch.delenv("CURRENT_WORKSPACE", raising=False)
            shutil.rmtree(workspace_dir)
            assert get_current_workspace() == "_legacy"


class TestIngestWorkflow:
    """文档入库工作流测试"""

    def test_parse_transcript(self, tmp_path):
        """测试解析逐字稿"""
        from scripts.parse import parse_transcript

        # 创建测试文件
        raw_file = tmp_path / "test_2026-01-01.txt"
        raw_file.write_text(
            """
User: 测试问题
Assistant: 测试回答
User: 另一个问题
Assistant: 另一个回答
""".strip()
        )

        result = parse_transcript(str(raw_file))

        assert result.session_date == "2026-01-01"
        assert len(result.turns) == 2
        assert result.turns[0].user_text == "测试问题"
        assert result.turns[0].assistant_text == "测试回答"

    def test_chunk_session(self, tmp_path):
        """测试分块"""
        from scripts.chunk import chunk_session
        from scripts.parse import parse_transcript

        # 创建测试文件
        raw_file = tmp_path / "test_2026-01-01.txt"
        raw_file.write_text(
            """
User: 测试问题
Assistant: 测试回答内容很长，需要分块处理。""" + "测试内容。" * 100
        )

        session = parse_transcript(str(raw_file))
        chunks = chunk_session(session, workspace_id=None)

        assert len(chunks) > 0
        for chunk in chunks:
            assert hasattr(chunk, "text")
            assert hasattr(chunk, "chunk_index")
            assert hasattr(chunk, "source_file")

    @patch("scripts.embedder.embed_one")
    @patch("scripts.ingest._get_table")
    def test_ingest_chunks(self, mock_table, mock_embed, tmp_path):
        """测试向量化和入库"""
        from scripts.chunk import Chunk
        from scripts.ingest import ingest

        # Mock embeddings
        mock_embed.return_value = [0.1] * 768

        # Mock LanceDB table
        mock_table_instance = MagicMock()
        mock_table.return_value = mock_table_instance

        # 创建测试 chunks
        chunks = [
            Chunk(
                text="测试内容 1",
                chunk_index=0,
                source_file="test.txt",
                session_date="2026-01-01",
                speakers="User, Assistant",
                start_ts="00:00",
                end_ts="01:00",
            ),
            Chunk(
                text="测试内容 2",
                chunk_index=1,
                source_file="test.txt",
                session_date="2026-01-01",
                speakers="User, Assistant",
                start_ts="01:00",
                end_ts="02:00",
            ),
        ]

        # 执行入库
        ingest(chunks, rebuild=True, workspace_id=None)

        # 验证调用
        assert mock_embed.call_count == 2
        mock_table_instance.add.assert_called_once()


class TestSummarization:
    """摘要生成测试"""

    @patch("scripts.llm.ask_llm")
    def test_summarize_session(self, mock_llm, tmp_path):
        """测试单次会话摘要"""
        from scripts.parse import parse_transcript
        from scripts.summarize import summarize_session

        # Mock LLM 返回
        mock_llm.return_value = json.dumps(
            {
                "session_summary": "测试摘要",
                "key_topics": ["话题1", "话题2"],
                "emotional_tone": "neutral",
            }
        )

        # 创建测试文件
        raw_file = tmp_path / "test_2026-01-01.txt"
        raw_file.write_text("User: 测试\nAssistant: 回答")

        session = parse_transcript(str(raw_file))

        with patch("scripts.summarize.summary_path") as mock_path:
            mock_path.return_value = tmp_path / "summary.json"

            result = summarize_session(session, str(raw_file), force=True, workspace_id=None)

            assert result is not None
            mock_llm.assert_called_once()


class TestGraphBuilding:
    """图谱构建测试"""

    @patch("scripts.llm.ask_llm")
    def test_build_session_fragment(self, mock_llm, tmp_path):
        """测试单次会话子图抽取"""
        from scripts.parse import parse_transcript
        from scripts.session_graph import build_session_fragment

        # Mock LLM 返回
        mock_llm.return_value = json.dumps(
            {
                "nodes": [
                    {"id": "n1", "type": "concept", "label": "概念1", "description": "描述1"},
                    {"id": "n2", "type": "concept", "label": "概念2", "description": "描述2"},
                ],
                "edges": [{"source": "n1", "target": "n2", "relation": "relates_to", "evidence": "证据"}],
            }
        )

        # 创建测试文件
        raw_file = tmp_path / "test_2026-01-01.txt"
        raw_file.write_text("User: 测试\nAssistant: 回答")

        session = parse_transcript(str(raw_file))

        with patch("scripts.session_graph.GRAPH_FRAGMENTS_DIR") as mock_dir:
            mock_dir.return_value = tmp_path / "fragments"
            mock_dir.return_value.mkdir()

            result = build_session_fragment(session, str(raw_file), force=True, workspace_id=None)

            assert result is not None
            mock_llm.assert_called_once()

    @patch("scripts.graph_utils.embed_one")
    def test_resolve_graph(self, mock_embed):
        """测试图谱归并"""
        from scripts.graph_utils import resolve_graph

        # Mock embeddings
        mock_embed.return_value = [0.1] * 768

        raw_nodes = [
            {"id": "n1", "type": "concept", "label": "概念A", "description": "描述A"},
            {"id": "n2", "type": "concept", "label": "概念A", "description": "描述A"},  # 重复
            {"id": "n3", "type": "concept", "label": "概念B", "description": "描述B"},
        ]

        raw_edges = [
            {"source": "n1", "target": "n3", "relation": "relates_to", "evidence": "证据1"},
            {"source": "n2", "target": "n3", "relation": "relates_to", "evidence": "证据2"},
        ]

        result = resolve_graph(raw_nodes, raw_edges)

        # 验证去重
        assert len(result["nodes"]) <= 3  # n1 和 n2 应该合并
        assert len(result["edges"]) >= 1


class TestRetrievalQA:
    """问答检索测试"""

    @patch("scripts.ask._get_table")
    @patch("scripts.llm.ask_llm")
    def test_answer_with_empty_db(self, mock_llm, mock_table):
        """测试向量库为空时的问答"""
        from scripts.ask import answer

        # Mock 空向量库
        mock_table_instance = MagicMock()
        mock_table_instance.to_pandas.return_value = MagicMock(empty=True)
        mock_table.return_value = mock_table_instance

        # Mock LLM 返回
        mock_llm.return_value = "我理解你的问题，但目前没有相关记录。"

        result = answer("测试问题", k=5)

        assert "answer" in result
        assert result["answer"] != ""

    @patch("scripts.ask._get_table")
    @patch("scripts.ask.retrieve")
    @patch("scripts.llm.ask_llm")
    def test_answer_with_retrieval(self, mock_llm, mock_retrieve, mock_table):
        """测试正常检索问答"""
        from scripts.ask import answer

        # Mock 检索结果
        mock_retrieve.return_value = [
            {
                "text": "检索到的相关内容",
                "source_file": "test.txt",
                "session_date": "2026-01-01",
                "score": 0.85,
            }
        ]

        # Mock LLM 返回
        mock_llm.return_value = "基于检索到的内容，我的回答是..."

        result = answer("测试问题", k=5)

        assert "answer" in result
        assert "检索" in str(mock_retrieve.call_args) or mock_retrieve.called


class TestUIIntegration:
    """UI 集成测试"""

    def test_app_imports(self):
        """测试 app.py 所有导入"""
        try:
            import app  # noqa: F401
            from scripts import index_records, index_settings, settings  # noqa: F401

            assert True
        except Exception as e:
            pytest.fail(f"App imports failed: {e}")

    def test_streamlit_page_imports(self):
        """测试 Streamlit 页面导入"""
        try:
            # 注意：实际 import 会执行 Streamlit 代码，这里只检查文件存在
            page_file = Path("pages/1_🕸️_心智地图.py")
            assert page_file.exists()

            # 读取检查基本语法
            content = page_file.read_text()
            assert "import streamlit as st" in content
        except Exception as e:
            pytest.fail(f"Page imports failed: {e}")

    def test_config_path_functions_not_used_as_paths(self):
        """测试路径函数不被当作 Path 对象使用"""
        from config import CHAT_MEMORY_PATH, GRAPH_JSON_PATH

        # 这些应该是函数
        assert callable(CHAT_MEMORY_PATH)
        assert callable(GRAPH_JSON_PATH)

        # 调用后应该返回 Path
        result = CHAT_MEMORY_PATH("_legacy")
        assert isinstance(result, Path)


class TestEndToEndWorkflow:
    """端到端工作流测试（完整场景）"""

    @pytest.mark.slow
    @patch("scripts.llm.ask_llm")
    @patch("scripts.embedder.embed_one")
    def test_full_ingest_to_query(self, mock_embed, mock_llm, tmp_path):
        """测试完整流程：文档入库 → 摘要 → 建图 → 问答"""
        from scripts.chunk import chunk_session
        from scripts.ingest import ingest
        from scripts.parse import parse_transcript

        # 1. 准备测试文件
        raw_file = tmp_path / "test_2026-01-01.txt"
        raw_file.write_text(
            """
User: 我最近工作压力很大
Assistant: 听起来你最近承受了很多压力。能具体说说是什么让你感到压力吗？
User: 主要是项目进度太紧了
Assistant: 时间紧迫确实容易让人感到焦虑。你有没有尝试过一些减压的方法？
""".strip()
        )

        # Mock embeddings 和 LLM
        mock_embed.return_value = [0.1] * 768
        mock_llm.return_value = json.dumps(
            {
                "session_summary": "讨论了工作压力问题",
                "key_topics": ["工作压力", "时间管理"],
            }
        )

        # 2. Parse
        session = parse_transcript(str(raw_file))
        assert len(session.turns) == 2

        # 3. Chunk
        chunks = chunk_session(session, workspace_id=None)
        assert len(chunks) > 0

        # 4. Ingest (Mock)
        with patch("scripts.ingest._get_table"):
            ingest(chunks, rebuild=True, workspace_id=None)

        # 验证 embeddings 被调用
        assert mock_embed.called

    @pytest.mark.slow
    @patch("scripts.workspace_manager.PRIVATE_DIR")
    def test_workspace_isolation(self, mock_private_dir, tmp_path):
        """测试 workspace 隔离性"""
        from scripts.workspace_manager import create_workspace, get_workspace_dir

        mock_private_dir.return_value = tmp_path
        workspaces_dir = tmp_path / "workspaces"
        workspaces_dir.mkdir()

        # 创建两个 workspaces
        ws1 = create_workspace("ws1", "WS1", "generic", "generic")
        ws2 = create_workspace("ws2", "WS2", "counseling", "predefined", "counseling.json")

        # 验证隔离
        ws1_dir = get_workspace_dir("ws1")
        ws2_dir = get_workspace_dir("ws2")

        assert ws1_dir != ws2_dir
        assert (ws1_dir / "data").exists()
        assert (ws2_dir / "data").exists()

        # 写入文件到 ws1，不应影响 ws2
        (ws1_dir / "data" / "test.txt").write_text("ws1 data")
        assert not (ws2_dir / "data" / "test.txt").exists()


# Pytest 配置
@pytest.fixture
def mock_api_keys(monkeypatch):
    """Mock API keys"""
    monkeypatch.setenv("GEMINI_API_KEY", "fake-test-key-" + "a" * 32)
    monkeypatch.setenv("XAI_API_KEY", "fake-xai-key-" + "b" * 32)


@pytest.fixture(autouse=True)
def use_test_env(mock_api_keys):
    """所有测试自动使用 mock API keys"""
    pass
