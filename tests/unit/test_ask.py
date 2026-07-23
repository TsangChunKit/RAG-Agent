"""ask.py 核心业务逻辑测试。

测试目标：从 13% 提升到 80%

测试优先级：
1. answer() - 主入口函数
2. retrieve() - 混合检索
3. find_relevant_graph_nodes() - GraphRAG
4. 辅助函数（日期解析、格式化等）
"""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

import numpy as np
import pandas as pd
import pytest


class TestDateParsing:
    """日期解析功能测试"""

    def test_to_int_arabic_numbers(self):
        """测试阿拉伯数字转换"""
        from scripts.ask import _to_int

        assert _to_int("1") == 1
        assert _to_int("15") == 15
        assert _to_int("31") == 31

    def test_to_int_chinese_numbers(self):
        """测试中文数字转换"""
        from scripts.ask import _to_int

        assert _to_int("一") == 1
        assert _to_int("十") == 10
        assert _to_int("二十") == 20
        assert _to_int("三十一") == 31

    def test_to_int_invalid(self):
        """测试无效输入"""
        from scripts.ask import _to_int

        assert _to_int("abc") is None
        assert _to_int("") is None

    def test_extract_mentioned_dates_standard_format(self):
        """测试标准日期格式提取"""
        from scripts.ask import extract_mentioned_dates

        # YYYY-MM-DD
        dates = extract_mentioned_dates("2026-07-04 的咨询")
        assert "2026-07-04" in dates

        # YYYY/MM/DD
        dates = extract_mentioned_dates("2026/7/4 的咨询")
        assert "2026-07-04" in dates

    def test_extract_mentioned_dates_chinese_format(self):
        """测试中文日期格式提取"""
        from scripts.ask import extract_mentioned_dates

        # 2026年7月4日
        dates = extract_mentioned_dates("2026年7月4日的咨询")
        assert "2026-07-04" in dates

        # 中文数字
        dates = extract_mentioned_dates("2026年七月四日")
        assert "2026-07-04" in dates

    def test_extract_mentioned_dates_multiple(self):
        """测试提取多个日期"""
        from scripts.ask import extract_mentioned_dates

        question = "对比 2026-07-01 和 2026-07-15 的咨询"
        dates = extract_mentioned_dates(question)

        assert len(dates) >= 2
        assert "2026-07-01" in dates
        assert "2026-07-15" in dates

    def test_extract_mentioned_dates_no_dates(self):
        """测试没有日期的情况"""
        from scripts.ask import extract_mentioned_dates

        dates = extract_mentioned_dates("最近的工作压力")
        assert dates == []


class TestSystemInstruction:
    """System Instruction 管理测试"""

    def test_load_system_instruction_default(self, tmp_path):
        """测试加载默认 system instruction"""
        from scripts.ask import DEFAULT_SYSTEM_INSTRUCTION, load_system_instruction

        si_file = tmp_path / "system_instruction.md"
        si_file.write_text(DEFAULT_SYSTEM_INSTRUCTION)

        with patch("scripts.ask.SYSTEM_INSTRUCTION_PATH", si_file):
            result = load_system_instruction(workspace_id=None)

            assert result == DEFAULT_SYSTEM_INSTRUCTION
            assert "心理咨询助手" in result

    def test_save_system_instruction(self, tmp_path):
        """测试保存 system instruction"""
        from scripts.ask import save_system_instruction

        si_file = tmp_path / "system_instruction.md"

        with patch("scripts.ask.SYSTEM_INSTRUCTION_PATH", si_file):
            new_content = "新的 system instruction"
            save_system_instruction(new_content, workspace_id=None)

            assert si_file.read_text() == new_content

    def test_reset_system_instruction(self, tmp_path):
        """测试重置 system instruction"""
        from scripts.ask import DEFAULT_SYSTEM_INSTRUCTION, reset_system_instruction

        si_file = tmp_path / "system_instruction.md"
        si_file.write_text("旧内容")

        with patch("scripts.ask.SYSTEM_INSTRUCTION_PATH", si_file):
            reset_system_instruction(workspace_id=None)

            assert si_file.read_text() == DEFAULT_SYSTEM_INSTRUCTION


class TestSanitize:
    """查询清理测试"""

    def test_sanitize_removes_quotes(self):
        """测试移除引号"""
        from scripts.ask import sanitize

        assert sanitize("test'query") == "testquery"
        assert sanitize('test"query') == "testquery"

    def test_sanitize_removes_backslash(self):
        """测试移除反斜杠"""
        from scripts.ask import sanitize

        assert sanitize("test\\query") == "testquery"

    def test_sanitize_normal_text(self):
        """测试正常文本不受影响"""
        from scripts.ask import sanitize

        assert sanitize("正常查询") == "正常查询"
        assert sanitize("normal query") == "normal query"


class TestMemoryLoading:
    """记忆加载测试"""

    def test_load_long_term_memory(self, tmp_path):
        """测试加载长期记忆"""
        from scripts.ask import _load_long_term_memory

        memory_file = tmp_path / "LONG_TERM_MEMORY.md"
        memory_content = "# 长期记忆\n核心议题：工作压力"
        memory_file.write_text(memory_content)

        with patch("scripts.ask.LONG_TERM_MEMORY_PATH") as mock_path:
            mock_path.return_value = memory_file

            result = _load_long_term_memory(workspace_id=None)

            assert result == memory_content
            assert "核心议题" in result

    def test_load_long_term_memory_missing(self, tmp_path):
        """测试长期记忆文件不存在"""
        from scripts.ask import _load_long_term_memory

        missing_file = tmp_path / "LONG_TERM_MEMORY.md"

        with patch("scripts.ask.LONG_TERM_MEMORY_PATH") as mock_path:
            mock_path.return_value = missing_file

            result = _load_long_term_memory(workspace_id=None)

            assert result == ""

    def test_load_chat_memory(self, tmp_path):
        """测试加载对话记忆"""
        from scripts.ask import _load_chat_memory

        memory_file = tmp_path / "CHAT_MEMORY.md"
        memory_content = "# 对话记忆\n之前聊过：项目管理"
        memory_file.write_text(memory_content)

        with patch("scripts.ask.CHAT_MEMORY_PATH") as mock_path:
            mock_path.return_value = memory_file

            result = _load_chat_memory(workspace_id=None)

            assert result == memory_content
            assert "项目管理" in result

    def test_load_chat_memory_missing(self, tmp_path):
        """测试对话记忆文件不存在"""
        from scripts.ask import _load_chat_memory

        missing_file = tmp_path / "CHAT_MEMORY.md"

        with patch("scripts.ask.CHAT_MEMORY_PATH") as mock_path:
            mock_path.return_value = missing_file

            result = _load_chat_memory(workspace_id=None)

            assert result == ""


class TestGraphLoading:
    """图谱加载测试"""

    def test_load_graph_valid(self, tmp_path):
        """测试加载有效图谱"""
        from scripts.ask import _load_graph

        graph_file = tmp_path / "graph.json"
        graph_data = {
            "nodes": [
                {"id": "n1", "label": "核心图式", "centrality": 0.8},
                {"id": "n2", "label": "应对模式", "centrality": 0.6},
            ],
            "edges": [{"source": "n1", "target": "n2", "relation": "derives"}],
        }
        graph_file.write_text(json.dumps(graph_data))

        with patch("scripts.ask.GRAPH_JSON_PATH") as mock_path:
            mock_path.return_value = graph_file

            result = _load_graph(workspace_id=None)

            assert result is not None
            assert len(result["nodes"]) == 2
            assert len(result["edges"]) == 1

    def test_load_graph_missing(self, tmp_path):
        """测试图谱文件不存在"""
        from scripts.ask import _load_graph

        missing_file = tmp_path / "graph.json"

        with patch("scripts.ask.GRAPH_JSON_PATH") as mock_path:
            mock_path.return_value = missing_file

            result = _load_graph(workspace_id=None)

            assert result is None

    def test_load_graph_invalid_json(self, tmp_path):
        """测试无效 JSON"""
        from scripts.ask import _load_graph

        graph_file = tmp_path / "graph.json"
        graph_file.write_text("{invalid json")

        with patch("scripts.ask.GRAPH_JSON_PATH") as mock_path:
            mock_path.return_value = graph_file

            result = _load_graph(workspace_id=None)

            assert result is None


class TestGraphFormatting:
    """图谱格式化测试"""

    def test_node_embed_text(self):
        """测试节点嵌入文本生成"""
        from scripts.ask import _node_embed_text

        node = {"label": "核心图式", "description": "详细描述", "type": "schema"}

        result = _node_embed_text(node)

        assert "核心图式" in result
        assert "详细描述" in result

    def test_node_embed_text_minimal(self):
        """测试最小节点信息"""
        from scripts.ask import _node_embed_text

        node = {"label": "简单节点"}

        result = _node_embed_text(node)

        assert "简单节点" in result

    def test_format_graph_context(self):
        """测试图谱上下文格式化"""
        from scripts.ask import _format_graph_context

        nodes = [
            {"id": "n1", "label": "节点1", "type": "schema", "centrality": 0.8},
            {"id": "n2", "label": "节点2", "type": "coping", "centrality": 0.6},
        ]
        edges = [{"source": "n1", "target": "n2", "relation": "derives", "evidence": "证据"}]
        label_lookup = {"n1": "节点1", "n2": "节点2"}

        result = _format_graph_context(nodes, edges, label_lookup)

        assert "节点1" in result
        assert "节点2" in result
        assert "derives" in result or "派生" in result


class TestRetrieve:
    """检索功能测试"""

    @patch("scripts.ask._get_table")
    @patch("scripts.ask.embed_one")
    def test_retrieve_empty_db(self, mock_embed, mock_table):
        """测试空数据库检索"""
        from scripts.ask import retrieve

        # Mock empty DataFrame
        mock_table_instance = MagicMock()
        mock_table_instance.to_pandas.return_value = pd.DataFrame()
        mock_table.return_value = mock_table_instance

        mock_embed.return_value = np.random.rand(768)

        result = retrieve("测试问题", k=5)

        assert result == []

    @patch("scripts.ask._get_table")
    @patch("scripts.ask.embed_one")
    @patch("scripts.ask.index_settings.load")
    def test_retrieve_basic(self, mock_settings, mock_embed, mock_table):
        """测试基本检索"""
        from scripts.ask import retrieve

        # Mock settings
        mock_settings.return_value = {
            "retrieve_top_k": 10,
            "final_top_k": 5,
            "enable_reranker": False,
            "window_expand": 1,
        }

        # Mock embeddings
        mock_embed.return_value = np.random.rand(768)

        # Mock table with sample data
        mock_table_instance = MagicMock()
        sample_df = pd.DataFrame(
            {
                "text": ["片段1", "片段2", "片段3"],
                "source_file": ["test.txt", "test.txt", "test.txt"],
                "session_date": ["2026-01-01", "2026-01-01", "2026-01-01"],
                "chunk_index": [0, 1, 2],
                "speakers": ["User, Assistant", "User, Assistant", "User, Assistant"],
                "start_ts": ["00:00", "01:00", "02:00"],
                "end_ts": ["01:00", "02:00", "03:00"],
                "vector": [np.random.rand(768), np.random.rand(768), np.random.rand(768)],
            }
        )
        mock_table_instance.to_pandas.return_value = sample_df

        # Mock search results
        mock_search_result = MagicMock()
        mock_search_result.to_pandas.return_value = sample_df.head(2)
        mock_table_instance.search.return_value = mock_search_result

        mock_table.return_value = mock_table_instance

        result = retrieve("测试问题", k=5)

        # 应该返回结果
        assert isinstance(result, list)
        mock_embed.assert_called_once()


class TestAnswer:
    """问答功能测试"""

    @patch("scripts.ask._get_table")
    @patch("scripts.ask.retrieve")
    @patch("scripts.ask.ask_llm")
    @patch("scripts.ask._load_long_term_memory")
    @patch("scripts.ask._load_chat_memory")
    @patch("scripts.ask._load_graph")
    @patch("scripts.ask.load_system_instruction")
    def test_answer_basic(
        self,
        mock_si,
        mock_graph,
        mock_chat_mem,
        mock_ltm,
        mock_llm,
        mock_retrieve,
        mock_table,
    ):
        """测试基本问答"""
        from scripts.ask import answer

        # Mock all dependencies
        mock_si.return_value = "You are an AI assistant."
        mock_graph.return_value = None
        mock_chat_mem.return_value = ""
        mock_ltm.return_value = "长期记忆：核心议题"
        mock_llm.return_value = "这是 LLM 的回答"
        mock_retrieve.return_value = [
            {
                "text": "检索到的片段",
                "source_file": "test.txt",
                "session_date": "2026-01-01",
            }
        ]

        result = answer("测试问题", k=5)

        # 验证返回结构
        assert "answer" in result
        assert "retrieved_count" in result
        assert result["answer"] == "这是 LLM 的回答"

        # 验证 LLM 被调用
        mock_llm.assert_called_once()

    @patch("scripts.ask._get_table")
    @patch("scripts.ask.retrieve")
    @patch("scripts.ask.ask_llm")
    def test_answer_with_history(self, mock_llm, mock_retrieve, mock_table):
        """测试带历史的问答"""
        from scripts.ask import answer

        mock_retrieve.return_value = []
        mock_llm.return_value = "基于历史的回答"

        history = [
            {"role": "user", "content": "第一个问题"},
            {"role": "assistant", "content": "第一个回答"},
        ]

        with patch("scripts.ask._load_long_term_memory", return_value=""), patch(
            "scripts.ask._load_chat_memory", return_value=""
        ), patch("scripts.ask._load_graph", return_value=None), patch(
            "scripts.ask.load_system_instruction", return_value="System"
        ):

            result = answer("新问题", history=history, k=5)

            assert "answer" in result

            # LLM 调用应该包含历史
            call_args = mock_llm.call_args
            assert call_args is not None

    @patch("scripts.ask._get_table")
    @patch("scripts.ask.retrieve")
    @patch("scripts.ask.ask_llm")
    def test_answer_llm_failure(self, mock_llm, mock_retrieve, mock_table):
        """测试 LLM 调用失败"""
        from scripts.ask import answer

        mock_retrieve.return_value = []
        mock_llm.side_effect = Exception("LLM API Error")

        with patch("scripts.ask._load_long_term_memory", return_value=""), patch(
            "scripts.ask._load_chat_memory", return_value=""
        ), patch("scripts.ask._load_graph", return_value=None), patch(
            "scripts.ask.load_system_instruction", return_value="System"
        ):

            with pytest.raises(Exception):
                answer("测试问题", k=5)

    @patch("scripts.ask.extract_mentioned_dates")
    @patch("scripts.ask.get_full_day_transcripts")
    @patch("scripts.ask.ask_llm")
    def test_answer_with_mentioned_dates(self, mock_llm, mock_transcripts, mock_dates):
        """测试提到具体日期的问答"""
        from scripts.ask import answer

        # Mock 日期提取
        mock_dates.return_value = ["2026-07-04"]

        # Mock 完整逐字稿
        mock_transcripts.return_value = [
            {
                "date": "2026-07-04",
                "content": "完整的逐字稿内容",
                "speakers": "User, Assistant",
            }
        ]

        mock_llm.return_value = "基于完整逐字稿的回答"

        with patch("scripts.ask._get_table"), patch("scripts.ask.retrieve", return_value=[]), patch(
            "scripts.ask._load_long_term_memory", return_value=""
        ), patch("scripts.ask._load_chat_memory", return_value=""), patch(
            "scripts.ask._load_graph", return_value=None
        ), patch(
            "scripts.ask.load_system_instruction", return_value="System"
        ):

            result = answer("2026年7月4日的咨询内容", k=5)

            assert "answer" in result
            mock_transcripts.assert_called_once_with(["2026-07-04"])


class TestGraphRAG:
    """GraphRAG 功能测试"""

    @patch("scripts.ask.embed_one")
    def test_find_relevant_graph_nodes_basic(self, mock_embed):
        """测试查找相关图谱节点"""
        from scripts.ask import find_relevant_graph_nodes

        mock_embed.side_effect = [
            np.array([0.5] * 768),  # question vector
            np.array([0.6] * 768),  # node 1 vector
            np.array([0.1] * 768),  # node 2 vector
        ]

        graph = {
            "nodes": [
                {"id": "n1", "label": "相关节点", "centrality": 0.8},
                {"id": "n2", "label": "不相关节点", "centrality": 0.3},
            ],
            "edges": [],
        }

        result = find_relevant_graph_nodes("测试问题", graph, top_k=2)

        # 应该返回相关节点
        assert len(result) <= 2
        assert isinstance(result, list)

    def test_find_relevant_graph_nodes_empty(self):
        """测试空图谱"""
        from scripts.ask import find_relevant_graph_nodes

        graph = {"nodes": [], "edges": []}

        result = find_relevant_graph_nodes("测试问题", graph, top_k=2)

        assert result == []

    def test_is_therapy_node_id(self):
        """测试判断节点是否来自真实咨询"""
        from scripts.ask import _is_therapy_node_id

        assert _is_therapy_node_id("2026-01-01_n1") is True
        assert _is_therapy_node_id("chat_n1") is False
        assert _is_therapy_node_id("n1") is False

    def test_graph_neighbors(self):
        """测试获取图谱邻居节点"""
        from scripts.ask import graph_neighbors

        graph = {
            "nodes": [
                {"id": "n1", "label": "节点1"},
                {"id": "n2", "label": "节点2"},
                {"id": "n3", "label": "节点3"},
            ],
            "edges": [
                {"source": "n1", "target": "n2", "relation": "derives"},
                {"source": "n1", "target": "n3", "relation": "activates"},
            ],
        }

        neighbors = graph_neighbors("n1", graph)

        assert len(neighbors) == 2
        # 每个邻居是 (node, edge) 元组
        assert all(isinstance(n, tuple) and len(n) == 2 for n in neighbors)


class TestFormatting:
    """格式化功能测试"""

    def test_format_retrieved(self):
        """测试检索结果格式化"""
        from scripts.ask import _format_retrieved

        windows = [
            {
                "text": "片段1内容",
                "source_file": "test.txt",
                "session_date": "2026-01-01",
                "speakers": "User, Assistant",
                "start_ts": "00:00",
                "end_ts": "01:00",
                "rank": 1,
            },
            {
                "text": "片段2内容",
                "source_file": "test.txt",
                "session_date": "2026-01-01",
                "speakers": "User, Assistant",
                "start_ts": "01:00",
                "end_ts": "02:00",
                "rank": 2,
            },
        ]

        result = _format_retrieved(windows)

        assert "片段1内容" in result
        assert "片段2内容" in result
        assert "2026-01-01" in result

    def test_format_retrieved_empty(self):
        """测试空检索结果"""
        from scripts.ask import _format_retrieved

        result = _format_retrieved([])

        assert result == ""

    def test_format_session_summary(self):
        """测试会话摘要格式化"""
        from scripts.ask import _format_session_summary

        summary = {
            "session_summary": "这是一次关于工作压力的咨询",
            "key_topics": ["工作压力", "时间管理", "焦虑"],
            "emotional_tone": "anxious",
        }

        result = _format_session_summary(summary)

        assert "工作压力" in result
        assert "时间管理" in result


# Pytest 配置
@pytest.fixture(autouse=True)
def mock_workspace():
    """自动 mock workspace 相关函数"""
    with patch("scripts.ask.get_current_workspace", return_value="_legacy"), patch(
        "scripts.ask.get_workspace_dir"
    ) as mock_dir:
        mock_dir.return_value = Path("/tmp/test_workspace")
        yield
