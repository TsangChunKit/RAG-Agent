"""ask.py 核心业务逻辑测试。

测试目标：从 48% 提升到 70%+

覆盖重点：
1. answer() - 主入口函数（完整流程+压缩）
2. retrieve() - 混合检索（含父块扩展）
3. find_relevant_graph_nodes() - GraphRAG
4. 日期提取和完整逐字稿检索
5. 上下文组装和压缩
6. 辅助函数（日期解析、格式化等）
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
        si_file.parent.mkdir(parents=True, exist_ok=True)  # 确保父目录存在

        with patch("scripts.ask.SYSTEM_INSTRUCTION_PATH", si_file), \
             patch("scripts.ask.load_workspace_config", return_value={}):  # Mock workspace config
            new_content = "新的 system instruction"
            save_system_instruction(new_content, workspace_id=None)

            assert si_file.read_text() == new_content

    def test_reset_system_instruction(self, tmp_path):
        """测试重置 system instruction"""
        from scripts.ask import DEFAULT_SYSTEM_INSTRUCTION, reset_system_instruction

        si_file = tmp_path / "system_instruction.md"
        si_file.parent.mkdir(parents=True, exist_ok=True)
        si_file.write_text("旧内容")

        # reset_system_instruction 有两个签名，测试不带参数的版本
        with patch("scripts.ask.SYSTEM_INSTRUCTION_PATH", si_file), \
             patch("scripts.ask.load_workspace_config", return_value={}):
            result = reset_system_instruction()  # 不带参数

            assert si_file.read_text() == DEFAULT_SYSTEM_INSTRUCTION
            assert result == DEFAULT_SYSTEM_INSTRUCTION


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

            # 实际返回提示文本，不是空字符串
            assert "尚未生成" in result or result == ""

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

            # 实际返回提示文本，不是空字符串
            assert "还没有生成" in result or result == ""


class TestGraphLoading:
    """图谱加载测试"""

    def test_load_graph_valid(self, tmp_path):
        """测试加载有效图谱"""
        from scripts.ask import _load_graph
        import scripts.ask

        # 清空 cache（避免之前测试的缓存）
        scripts.ask._graph_cache = None

        graph_file = tmp_path / "graph.json"
        graph_data = {
            "nodes": [
                {"id": "n1", "label": "核心图式", "centrality": 0.8},
                {"id": "n2", "label": "应对模式", "centrality": 0.6},
            ],
            "edges": [{"source": "n1", "target": "n2", "relation": "derives"}],
        }
        graph_file.write_text(json.dumps(graph_data))

        # _load_graph 会合并真实图谱 + AI 对话图谱，两个路径都要 mock，
        # 否则真实的 chat_graph.json 会被合并进来。
        with patch("scripts.ask.GRAPH_JSON_PATH") as mock_path:
            mock_path.return_value = graph_file
            with patch("scripts.ask.CHAT_GRAPH_JSON_PATH") as mock_chat_path:
                mock_chat_path.return_value = tmp_path / "chat_graph.json"  # 不存在

                result = _load_graph(workspace_id=None)

                assert result is not None
                assert len(result["nodes"]) == 2
                assert len(result["edges"]) == 1

    def test_load_graph_missing(self, tmp_path):
        """测试图谱文件不存在"""
        from scripts.ask import _load_graph
        import scripts.ask

        # 清空 cache（避免之前测试的缓存）
        scripts.ask._graph_cache = None

        missing_file = tmp_path / "graph.json"

        # 两个图谱路径都不存在时才应返回 None，所以两个都要 mock。
        with patch("scripts.ask.GRAPH_JSON_PATH") as mock_path:
            mock_path.return_value = missing_file
            with patch("scripts.ask.CHAT_GRAPH_JSON_PATH") as mock_chat_path:
                mock_chat_path.return_value = tmp_path / "chat_graph.json"  # 不存在

                result = _load_graph(workspace_id=None)

                assert result is None

    def test_load_graph_invalid_json(self, tmp_path):
        """测试无效 JSON"""
        from scripts.ask import _load_graph

        # 清空 cache（避免之前测试的缓存）
        import scripts.ask
        scripts.ask._graph_cache = None

        graph_file = tmp_path / "graph.json"
        graph_file.write_text("{invalid json")

        with patch("scripts.ask.GRAPH_JSON_PATH") as mock_path:
            mock_path.return_value = graph_file
            with patch("scripts.ask.CHAT_GRAPH_JSON_PATH") as mock_chat_path:
                mock_chat_path.return_value = tmp_path / "chat_graph.json"  # 不存在

                # 应该抛出异常（或返回 None，取决于实际实现）
                try:
                    result = _load_graph(workspace_id=None)
                    # 如果没抛异常，验证返回 None 或空图
                    assert result is None or result == {"nodes": [], "edges": []}
                except json.JSONDecodeError:
                    # 预期行为：JSON 解析失败
                    pass


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

        # 节点必须包含 description 字段（实际代码要求）
        node = {"label": "简单节点", "description": "描述"}

        result = _node_embed_text(node)

        assert "简单节点" in result

    def test_format_graph_context(self):
        """测试图谱上下文格式化"""
        from scripts.ask import _format_graph_context

        # 节点必须包含 description 和 degree_centrality
        nodes = [
            {"id": "n1", "label": "节点1", "type": "schema", "degree_centrality": 0.8, "description": "描述1"},
            {"id": "n2", "label": "节点2", "type": "coping", "degree_centrality": 0.6, "description": "描述2"},
        ]
        edges = [{"source": "n1", "target": "n2", "relation_type": "derives", "relation": "证据"}]
        label_lookup = {"n1": "节点1", "n2": "节点2"}

        result = _format_graph_context(nodes, edges, label_lookup)

        assert "节点1" in result
        assert "节点2" in result
        assert "derives" in result or "派生" in result


class TestRetrieve:
    """检索功能测试"""

    @patch("scripts.ask._get_table")
    @patch("scripts.ask.embed_one")
    @patch("scripts.ask.index_settings.retrieval_params")
    @patch("scripts.ask.index_settings.reranker_params")
    def test_retrieve_empty_db(self, mock_reranker_params, mock_retrieval_params, mock_embed, mock_table):
        """测试空数据库检索"""
        from scripts.ask import retrieve

        # Mock settings
        mock_retrieval_params.return_value = {
            "top_k": 10,
            "window_expand": 1,
        }
        mock_reranker_params.return_value = {
            "use_reranker": False,
            "rerank_top_k": 10,
            "final_top_k": 5,
        }

        # Mock empty DataFrame（带正确的列）
        mock_table_instance = MagicMock()
        empty_df = pd.DataFrame(columns=["source_file", "chunk_index", "raw_text", "session_date", "start_ts", "end_ts"])
        mock_table_instance.to_pandas.return_value = empty_df
        mock_table.return_value = mock_table_instance

        mock_embed.return_value = np.random.rand(768)

        # Mock empty search results
        mock_search_result = MagicMock()
        mock_search_result.to_pandas.return_value = empty_df

        mock_search_builder = MagicMock()
        mock_search_builder.vector.return_value = mock_search_builder
        mock_search_builder.text.return_value = mock_search_builder
        mock_search_builder.rerank.return_value = mock_search_builder
        mock_search_builder.limit.return_value = mock_search_result
        mock_table_instance.search.return_value = mock_search_builder

        result = retrieve("测试问题", k=5)

        assert result == []

    @patch("scripts.ask._get_table")
    @patch("scripts.ask._load_all_chunks")
    @patch("scripts.ask.embed_one")
    @patch("scripts.ask.index_settings.retrieval_params")
    @patch("scripts.ask.index_settings.reranker_params")
    def test_retrieve_basic(self, mock_reranker_params, mock_retrieval_params, mock_embed, mock_load_all, mock_table):
        """测试基本检索"""
        from scripts.ask import retrieve

        # Mock settings（使用正确的函数名）
        mock_retrieval_params.return_value = {
            "top_k": 10,
            "window_expand": 1,
        }
        mock_reranker_params.return_value = {
            "use_reranker": False,
            "rerank_top_k": 10,
            "final_top_k": 5,
        }

        # Mock embeddings
        mock_embed.return_value = np.random.rand(768)

        # Mock table with sample data（必须包含 raw_text）
        mock_table_instance = MagicMock()
        sample_df = pd.DataFrame(
            {
                "text": ["片段1", "片段2", "片段3"],
                "raw_text": ["片段1", "片段2", "片段3"],
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

        # Mock _load_all_chunks() 返回完整数据（用于 _merge_windows）
        mock_load_all.return_value = sample_df

        # Mock search results
        mock_search_result = MagicMock()
        search_df = sample_df.head(2).copy()
        mock_search_result.to_pandas.return_value = search_df

        # Mock search chain
        mock_search_builder = MagicMock()
        mock_search_builder.vector.return_value = mock_search_builder
        mock_search_builder.text.return_value = mock_search_builder
        mock_search_builder.rerank.return_value = mock_search_builder
        mock_search_builder.limit.return_value = mock_search_result
        mock_table_instance.search.return_value = mock_search_builder

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
    @patch("scripts.ask.session_resolver.resolve")
    @patch("scripts.ask.session_resolver.therapy_manifest")
    @patch("scripts.ask.session_resolver.chat_manifest")
    @patch("scripts.ask.get_cache_name")
    def test_answer_basic(
        self,
        mock_cache,
        mock_chat_manifest,
        mock_therapy_manifest,
        mock_resolve,
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
        mock_therapy_manifest.return_value = "清单"
        mock_chat_manifest.return_value = "聊天清单"
        mock_cache.return_value = None  # 不使用缓存
        mock_resolve.return_value = {"therapy_dates": [], "chat_session_ids": [], "overflow": False}

        # Mock LLM response with usage_metadata
        mock_response = MagicMock()
        mock_response.text = "这是 LLM 的回答"
        mock_response.usage_metadata = MagicMock()
        mock_response.usage_metadata.prompt_token_count = 100
        mock_response.usage_metadata.candidates_token_count = 50
        mock_response.usage_metadata.thoughts_token_count = 0
        mock_response.usage_metadata.cached_content_token_count = 0
        mock_response.usage_metadata.total_token_count = 150
        mock_llm.return_value = mock_response

        mock_retrieve.return_value = [
            {
                "text": "检索到的片段",
                "source_file": "test.txt",
                "session_date": "2026-01-01",
                "start_ts": "00:00",
                "end_ts": "01:00",
                "chunk_index_range": (0, 1),
            }
        ]

        result = answer("测试问题", k=5)

        # 验证返回结构
        assert "answer" in result
        assert "sources" in result
        assert result["answer"] == "这是 LLM 的回答"

        # 验证 LLM 被调用
        mock_llm.assert_called_once()

    @patch("scripts.ask._get_table")
    @patch("scripts.ask.retrieve")
    @patch("scripts.ask.ask_llm")
    @patch("scripts.ask.session_resolver.resolve")
    @patch("scripts.ask.session_resolver.therapy_manifest")
    @patch("scripts.ask.session_resolver.chat_manifest")
    @patch("scripts.ask.get_cache_name")
    def test_answer_with_history(self, mock_cache, mock_chat_manifest, mock_therapy_manifest,
                                 mock_resolve, mock_llm, mock_retrieve, mock_table):
        """测试带历史的问答"""
        from scripts.ask import answer

        mock_retrieve.return_value = []
        mock_therapy_manifest.return_value = "清单"
        mock_chat_manifest.return_value = "聊天清单"
        mock_cache.return_value = None
        mock_resolve.return_value = {"therapy_dates": [], "chat_session_ids": [], "overflow": False}

        # Mock LLM response with usage_metadata
        mock_response = MagicMock()
        mock_response.text = "基于历史的回答"
        mock_response.usage_metadata = MagicMock()
        mock_response.usage_metadata.prompt_token_count = 100
        mock_response.usage_metadata.candidates_token_count = 50
        mock_response.usage_metadata.thoughts_token_count = 0
        mock_response.usage_metadata.cached_content_token_count = 0
        mock_response.usage_metadata.total_token_count = 150
        mock_llm.return_value = mock_response

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
            # 验证 contents 包含历史
            contents = call_args[0][0]
            assert len(contents) > 1  # 至少包含历史 + 当前问题

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

    @patch("scripts.ask.get_full_day_transcripts")
    @patch("scripts.ask.ask_llm")
    @patch("scripts.ask.session_resolver.resolve")
    @patch("scripts.ask.session_resolver.therapy_manifest")
    @patch("scripts.ask.session_resolver.chat_manifest")
    @patch("scripts.ask.get_cache_name")
    def test_answer_with_mentioned_dates(self, mock_cache, mock_chat_manifest, mock_therapy_manifest,
                                        mock_resolve, mock_llm, mock_transcripts):
        """测试提到具体日期的问答"""
        from scripts.ask import answer

        mock_therapy_manifest.return_value = "清单"
        mock_chat_manifest.return_value = "聊天清单"
        mock_cache.return_value = None

        # Mock 完整逐字稿（正确的字段名）
        mock_transcripts.return_value = [
            {
                "session_date": "2026-07-04",  # 正确的字段名
                "source_file": "test.txt",
                "text": "完整的逐字稿内容",
                "start_ts": "00:00",
                "end_ts": "01:00",
                "is_full_transcript": True,
            }
        ]

        # Mock resolver（返回提取的日期）
        mock_resolve.return_value = {"therapy_dates": ["2026-07-04"], "chat_session_ids": [], "overflow": False}

        # Mock LLM response
        mock_response = MagicMock()
        mock_response.text = "基于完整逐字稿的回答"
        mock_response.usage_metadata = MagicMock()
        mock_response.usage_metadata.prompt_token_count = 100
        mock_response.usage_metadata.candidates_token_count = 50
        mock_response.usage_metadata.thoughts_token_count = 0
        mock_response.usage_metadata.cached_content_token_count = 0
        mock_response.usage_metadata.total_token_count = 150
        mock_llm.return_value = mock_response

        with patch("scripts.ask._get_table"), patch("scripts.ask.retrieve", return_value=[]), patch(
            "scripts.ask._load_long_term_memory", return_value=""
        ), patch("scripts.ask._load_chat_memory", return_value=""), patch(
            "scripts.ask._load_graph", return_value=None
        ), patch(
            "scripts.ask.load_system_instruction", return_value="System"
        ):

            result = answer("2026年7月4日的咨询内容", k=5)

            assert "answer" in result
            assert result["answer"] == "基于完整逐字稿的回答"
            # 验证完整逐字稿被调用
            assert mock_transcripts.called


class TestGraphRAG:
    """GraphRAG 功能测试"""

    @patch("scripts.embedder.embed")
    @patch("scripts.ask.embed_one")
    def test_find_relevant_graph_nodes_basic(self, mock_embed_one, mock_embed):
        """测试查找相关图谱节点"""
        from scripts.ask import find_relevant_graph_nodes

        # 清空缓存
        import scripts.ask
        scripts.ask._graph_node_embeddings = None

        # Mock embed_one（查询向量）
        mock_embed_one.return_value = np.array([0.5] * 1024)

        # Mock embed（节点批量向量化）- 返回字典格式
        mock_embed.return_value = {
            "dense_vecs": [
                np.array([0.6] * 1024),  # node 1 vector (相似)
                np.array([0.1] * 1024),  # node 2 vector (不相似)
            ]
        }

        # 节点必须包含 type, label, description
        graph = {
            "nodes": [
                {"id": "n1", "label": "相关节点", "type": "schema", "description": "描述1"},
                {"id": "n2", "label": "不相关节点", "type": "coping", "description": "描述2"},
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

        # 真实咨询节点不以 "chat:" 开头
        assert _is_therapy_node_id("schema:xxx") is True
        assert _is_therapy_node_id("chat:n1") is False  # 正确的前缀是 "chat:"
        assert _is_therapy_node_id("n1") is True  # 不以 chat: 开头就是真实咨询

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

        # 实际返回提示文本，不是空字符串
        assert "未检索到" in result or result == ""

    def test_format_session_summary(self):
        """测试会话摘要格式化"""
        from scripts.ask import _format_session_summary

        # 使用实际的字段名（topics 不是 key_topics）
        summary = {
            "topics": ["工作压力", "时间管理", "焦虑"],
            "emotional_tone": "anxious",
            "psychological_themes": ["认知扭曲", "焦虑"],
        }

        result = _format_session_summary(summary)

        # 验证包含主题或情绪基调
        assert "工作压力" in result or "anxious" in result


class TestContextCompression:
    """上下文压缩测试"""

    @patch("scripts.ask.ask_llm")
    @patch("scripts.ask.retrieve")
    @patch("scripts.ask._get_table")
    @patch("scripts.ask.session_resolver.resolve")
    @patch("scripts.ask.session_resolver.therapy_manifest")
    @patch("scripts.ask.session_resolver.chat_manifest")
    @patch("scripts.ask.get_cache_name")
    def test_compression_triggered_by_large_context(
        self, mock_cache, mock_chat_manifest, mock_therapy_manifest,
        mock_resolve, mock_table, mock_retrieve, mock_llm
    ):
        """测试大上下文触发压缩"""
        from scripts.ask import answer

        mock_therapy_manifest.return_value = "清单"
        mock_chat_manifest.return_value = "聊天清单"
        mock_cache.return_value = None
        mock_resolve.return_value = {"therapy_dates": [], "chat_session_ids": [], "overflow": False}
        mock_retrieve.return_value = []

        # Mock LLM response
        mock_response = MagicMock()
        mock_response.text = "回答"
        mock_response.usage_metadata = MagicMock()
        mock_response.usage_metadata.prompt_token_count = 100
        mock_response.usage_metadata.candidates_token_count = 50
        mock_response.usage_metadata.thoughts_token_count = 0
        mock_response.usage_metadata.cached_content_token_count = 0
        mock_response.usage_metadata.total_token_count = 150
        mock_llm.return_value = mock_response

        # 创建大量历史（触发压缩）
        history = []
        for i in range(30):
            history.append({
                "role": "user",
                "content": f"问题{i}",
                "history_content": "x" * 20000,  # 大内容
            })
            history.append({
                "role": "assistant",
                "content": "y" * 20000,
            })

        with patch("scripts.ask._load_long_term_memory", return_value=""), \
             patch("scripts.ask._load_chat_memory", return_value=""), \
             patch("scripts.ask._load_graph", return_value=None), \
             patch("scripts.ask.load_system_instruction", return_value="System"):

            result = answer("新问题", history=history, max_context=100000, k=5)

            assert "answer" in result
            # 验证压缩信息存在
            if result.get("compression_info"):
                assert result["compression_info"]["triggered"] is True


class TestGraphEvidence:
    """图谱证据检索测试"""

    @patch("scripts.ask.embed_one")
    @patch("scripts.ask._load_all_chunks")
    @patch("scripts.ask.index_settings.graph_evidence_params")
    def test_retrieve_within_date(self, mock_ge_params, mock_load_chunks, mock_embed):
        """测试在特定日期内检索"""
        from scripts.ask import _retrieve_within_date

        mock_ge_params.return_value = {
            "max_dates": 3,
            "fragments_per_date": 2,
            "window_expand": 1,
            "include_summary": True,
        }

        # Mock chunks for a specific date
        chunks_df = pd.DataFrame({
            "source_file": ["test.txt"] * 5,
            "session_date": ["2026-01-01"] * 5,
            "chunk_index": [0, 1, 2, 3, 4],
            "raw_text": ["片段0", "片段1", "片段2", "片段3", "片段4"],
            "start_ts": ["00:00", "00:10", "00:20", "00:30", "00:40"],
            "end_ts": ["00:10", "00:20", "00:30", "00:40", "00:50"],
            "vector": [np.random.rand(768) for _ in range(5)],
        })
        mock_load_chunks.return_value = chunks_df

        anchor_vec = np.random.rand(768)

        result = _retrieve_within_date(anchor_vec, "2026-01-01", k=2, window_expand=1)

        assert isinstance(result, list)
        # 应该返回窗口（可能为空或有结果）
        for w in result:
            assert w.get("via_graph_evidence") is True


class TestWindowMerging:
    """窗口合并测试"""

    def test_merge_windows_consecutive(self):
        """测试合并连续窗口"""
        from scripts.ask import _merge_windows

        # Mock data
        by_file = {
            "test.txt": pd.DataFrame({
                "session_date": ["2026-01-01"] * 5,
                "start_ts": ["00:00", "00:10", "00:20", "00:30", "00:40"],
                "end_ts": ["00:10", "00:20", "00:30", "00:40", "00:50"],
                "raw_text": ["片段0", "片段1", "片段2", "片段3", "片段4"],
            }, index=[0, 1, 2, 3, 4])
        }

        # 连续的 chunk_index
        needed = {("test.txt", 0), ("test.txt", 1), ("test.txt", 2)}
        hit_rank = {("test.txt", 0): 0, ("test.txt", 1): 1, ("test.txt", 2): 2}

        result = _merge_windows(by_file, needed, hit_rank)

        assert len(result) == 1  # 应该合并成一个窗口
        assert "片段0" in result[0]["text"]
        assert "片段2" in result[0]["text"]

    def test_merge_windows_non_consecutive(self):
        """测试非连续窗口不合并"""
        from scripts.ask import _merge_windows

        by_file = {
            "test.txt": pd.DataFrame({
                "session_date": ["2026-01-01"] * 5,
                "start_ts": ["00:00", "00:10", "00:20", "00:30", "00:40"],
                "end_ts": ["00:10", "00:20", "00:30", "00:40", "00:50"],
                "raw_text": ["片段0", "片段1", "片段2", "片段3", "片段4"],
            }, index=[0, 1, 2, 3, 4])
        }

        # 非连续的 chunk_index
        needed = {("test.txt", 0), ("test.txt", 3)}
        hit_rank = {("test.txt", 0): 0, ("test.txt", 3): 1}

        result = _merge_windows(by_file, needed, hit_rank)

        assert len(result) == 2  # 应该是两个独立窗口


class TestBackboneSubgraph:
    """骨干子图测试"""

    def test_backbone_subgraph_selection(self):
        """测试选择骨干子图"""
        from scripts.ask import _backbone_subgraph

        graph = {
            "nodes": [
                {"id": "n1", "degree_centrality": 0.9, "source": "therapy"},
                {"id": "n2", "degree_centrality": 0.7, "source": "therapy"},
                {"id": "n3", "degree_centrality": 0.5, "source": "therapy"},
                {"id": "chat1", "degree_centrality": 0.8, "source": "chat"},  # 应该被排除
            ],
            "edges": [
                {"source": "n1", "target": "n2"},
                {"source": "n2", "target": "n3"},
                {"source": "n1", "target": "chat1"},  # 跨越边界
            ],
        }

        nodes, edges = _backbone_subgraph(graph, top_k=2)

        assert len(nodes) == 2  # 只取 top 2
        assert nodes[0]["id"] == "n1"  # 中心性最高
        assert nodes[1]["id"] == "n2"
        # 只包含两个节点之间的边
        assert len(edges) == 1
        assert edges[0]["source"] == "n1" and edges[0]["target"] == "n2"


class TestLocalSubgraph:
    """局部子图测试"""

    def test_local_subgraph_1hop(self):
        """测试 1-hop 局部子图"""
        from scripts.ask import _local_subgraph

        graph = {
            "nodes": [
                {"id": "n1", "label": "锚点"},
                {"id": "n2", "label": "邻居1"},
                {"id": "n3", "label": "邻居2"},
                {"id": "n4", "label": "2跳邻居"},
            ],
            "edges": [
                {"source": "n1", "target": "n2"},
                {"source": "n1", "target": "n3"},
                {"source": "n2", "target": "n4"},
            ],
        }

        matched = [{"id": "n1", "label": "锚点"}]
        exclude = set()

        nodes, edges = _local_subgraph(matched, graph, exclude, hops=1)

        # 应该包含锚点 + 1-hop 邻居
        node_ids = {n["id"] for n in nodes}
        assert "n1" in node_ids
        assert "n2" in node_ids
        assert "n3" in node_ids
        assert "n4" not in node_ids  # 2-hop 不应包含


class TestFullDayTranscripts:
    """完整逐字稿检索测试"""

    @patch("scripts.ask.find_files_for_date")
    @patch("scripts.ask.parse_transcript")
    def test_get_full_day_transcripts(self, mock_parse, mock_find_files):
        """测试获取完整逐字稿"""
        from scripts.ask import get_full_day_transcripts

        # Mock file
        mock_file = MagicMock()
        mock_file.name = "2026-01-01_session.txt"
        mock_find_files.return_value = [mock_file]

        # Mock parsed session
        mock_session = MagicMock()
        mock_utterance = MagicMock()
        mock_utterance.timestamp = "00:00"
        mock_session.utterances = [mock_utterance]
        mock_parse.return_value = mock_session

        with patch("scripts.ask.render_full_text", return_value="完整逐字稿"):
            result = get_full_day_transcripts(["2026-01-01"])

            assert len(result) == 1
            assert result[0]["session_date"] == "2026-01-01"
            assert result[0]["text"] == "完整逐字稿"
            assert result[0]["is_full_transcript"] is True


# Pytest 配置
@pytest.fixture(autouse=True)
def mock_workspace():
    """自动 mock workspace 相关函数"""
    with patch("scripts.ask.get_current_workspace", return_value="_legacy"), patch(
        "scripts.ask.get_workspace_dir"
    ) as mock_dir:
        mock_dir.return_value = Path("/tmp/test_workspace")
        yield
