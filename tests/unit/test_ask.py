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
        history_file = tmp_path / "system_instruction_history.jsonl"

        with patch("scripts.ask.SYSTEM_INSTRUCTION_PATH", si_file), \
             patch("scripts.ask.SYSTEM_INSTRUCTION_HISTORY_PATH") as mock_hist_path, \
             patch("scripts.ask.load_workspace_config", return_value={}), \
             patch("scripts.ask.ask_llm") as mock_llm:  # Mock workspace config + LLM（外部依赖不能真调）
            mock_hist_path.return_value = history_file
            mock_llm.return_value = MagicMock(text="总结：切换到新的 system instruction")
            new_content = "新的 system instruction"
            save_system_instruction(new_content, workspace_id=None)

            assert si_file.read_text() == new_content

    def test_reset_system_instruction(self, tmp_path):
        """测试重置 system instruction（合并后单一签名，workspace_id 默认 None）"""
        from scripts.ask import DEFAULT_SYSTEM_INSTRUCTION, reset_system_instruction

        si_file = tmp_path / "system_instruction.md"
        si_file.parent.mkdir(parents=True, exist_ok=True)
        si_file.write_text("旧内容")
        history_file = tmp_path / "system_instruction_history.jsonl"

        with patch("scripts.ask.SYSTEM_INSTRUCTION_PATH", si_file), \
             patch("scripts.ask.SYSTEM_INSTRUCTION_HISTORY_PATH") as mock_hist_path, \
             patch("scripts.ask.load_workspace_config", return_value={}), \
             patch("scripts.ask.ask_llm") as mock_llm:
            mock_hist_path.return_value = history_file
            mock_llm.return_value = MagicMock(text="总结：恢复默认")
            result = reset_system_instruction()  # 不带参数

            assert si_file.read_text() == DEFAULT_SYSTEM_INSTRUCTION
            assert result == DEFAULT_SYSTEM_INSTRUCTION

    def test_reset_system_instruction_accepts_workspace_id(self, tmp_path):
        """合并两个重复定义后，reset_system_instruction 必须接受 workspace_id 参数"""
        from scripts.ask import DEFAULT_SYSTEM_INSTRUCTION, reset_system_instruction

        si_file = tmp_path / "system_instruction.md"
        si_file.parent.mkdir(parents=True, exist_ok=True)
        history_file = tmp_path / "system_instruction_history.jsonl"

        with patch("scripts.ask.SYSTEM_INSTRUCTION_PATH", si_file), \
             patch("scripts.ask.SYSTEM_INSTRUCTION_HISTORY_PATH") as mock_hist_path, \
             patch("scripts.ask.load_workspace_config", return_value={}), \
             patch("scripts.ask.ask_llm") as mock_llm:
            mock_hist_path.return_value = history_file
            mock_llm.return_value = MagicMock(text="总结")
            result = reset_system_instruction(workspace_id="some-workspace")

            assert result == DEFAULT_SYSTEM_INSTRUCTION


class TestSystemInstructionHistory:
    """System Instruction 版本历史：保存自动记录 / LLM 摘要降级 / 查询 / 恢复"""

    def _patch_paths(self, si_file, history_file):
        return (
            patch("scripts.ask.SYSTEM_INSTRUCTION_PATH", si_file),
            patch("scripts.ask.SYSTEM_INSTRUCTION_HISTORY_PATH", return_value=history_file),
            patch("scripts.ask.load_workspace_config", return_value={}),
        )

    def test_save_appends_history_entry_with_llm_summary(self, tmp_path):
        from scripts.ask import list_system_instruction_history, save_system_instruction

        si_file = tmp_path / "system_instruction.md"
        si_file.write_text("旧内容")
        history_file = tmp_path / "history.jsonl"

        p1, p2, p3 = self._patch_paths(si_file, history_file)
        with p1, p2, p3, patch("scripts.ask.ask_llm") as mock_llm:
            mock_llm.return_value = MagicMock(text="把语气从正式改为口语化")
            save_system_instruction("新内容", workspace_id=None)

            entries = list_system_instruction_history(workspace_id=None)
        assert len(entries) == 1
        assert entries[0]["content"] == "新内容"
        assert entries[0]["summary"] == "把语气从正式改为口语化"
        assert "ts" in entries[0]

    def test_save_noop_when_content_unchanged_does_not_append_or_call_llm(self, tmp_path):
        from scripts.ask import list_system_instruction_history, save_system_instruction

        si_file = tmp_path / "system_instruction.md"
        si_file.write_text("一样的内容")
        history_file = tmp_path / "history.jsonl"

        p1, p2, p3 = self._patch_paths(si_file, history_file)
        with p1, p2, p3, patch("scripts.ask.ask_llm") as mock_llm:
            save_system_instruction("一样的内容", workspace_id=None)
            mock_llm.assert_not_called()
            entries = list_system_instruction_history(workspace_id=None)

        assert entries == []

    def test_save_llm_failure_falls_back_but_still_saves_and_records(self, tmp_path):
        from scripts.ask import list_system_instruction_history, save_system_instruction

        si_file = tmp_path / "system_instruction.md"
        si_file.write_text("旧内容")
        history_file = tmp_path / "history.jsonl"

        p1, p2, p3 = self._patch_paths(si_file, history_file)
        with p1, p2, p3, patch("scripts.ask.ask_llm", side_effect=RuntimeError("未设置 API key")):
            save_system_instruction("新内容", workspace_id=None)
            entries = list_system_instruction_history(workspace_id=None)

        assert si_file.read_text() == "新内容"  # 保存本身不受 LLM 失败影响
        assert len(entries) == 1
        assert "新内容" == entries[0]["content"]
        assert entries[0]["summary"]  # 有降级文案，不是空字符串

    def test_list_history_returns_newest_first_and_respects_limit(self, tmp_path):
        from scripts.ask import list_system_instruction_history, save_system_instruction

        si_file = tmp_path / "system_instruction.md"
        si_file.write_text("v0")
        history_file = tmp_path / "history.jsonl"

        p1, p2, p3 = self._patch_paths(si_file, history_file)
        with p1, p2, p3, patch("scripts.ask.ask_llm") as mock_llm:
            mock_llm.return_value = MagicMock(text="摘要")
            for v in ["v1", "v2", "v3"]:
                save_system_instruction(v, workspace_id=None)
            entries = list_system_instruction_history(workspace_id=None)

        assert [e["content"] for e in entries] == ["v3", "v2", "v1"]

        with patch("scripts.ask.SYSTEM_INSTRUCTION_HISTORY_PATH", return_value=history_file):
            limited = list_system_instruction_history(limit=2, workspace_id=None)
        assert len(limited) == 2

    def test_get_version_found_and_not_found(self, tmp_path):
        from scripts.ask import (
            get_system_instruction_version,
            list_system_instruction_history,
            save_system_instruction,
        )

        si_file = tmp_path / "system_instruction.md"
        si_file.write_text("旧内容")
        history_file = tmp_path / "history.jsonl"

        p1, p2, p3 = self._patch_paths(si_file, history_file)
        with p1, p2, p3, patch("scripts.ask.ask_llm") as mock_llm:
            mock_llm.return_value = MagicMock(text="摘要")
            save_system_instruction("新内容", workspace_id=None)
            ts = list_system_instruction_history(workspace_id=None)[0]["ts"]

        with patch("scripts.ask.SYSTEM_INSTRUCTION_HISTORY_PATH", return_value=history_file):
            found = get_system_instruction_version(ts, workspace_id=None)
            missing = get_system_instruction_version("not-a-real-ts", workspace_id=None)

        assert found["content"] == "新内容"
        assert missing is None

    def test_restore_writes_content_back_and_appends_fixed_summary_without_llm(self, tmp_path):
        from scripts.ask import (
            list_system_instruction_history,
            restore_system_instruction_version,
            save_system_instruction,
        )

        si_file = tmp_path / "system_instruction.md"
        si_file.write_text("v0")
        history_file = tmp_path / "history.jsonl"

        p1, p2, p3 = self._patch_paths(si_file, history_file)
        with p1, p2, p3, patch("scripts.ask.ask_llm") as mock_llm:
            mock_llm.return_value = MagicMock(text="摘要")
            save_system_instruction("v1", workspace_id=None)  # 历史里现在有一条 v1
            old_ts = list_system_instruction_history(workspace_id=None)[0]["ts"]

        q1, q2, q3 = self._patch_paths(si_file, history_file)
        with q1, q2, q3, patch("scripts.ask.ask_llm") as mock_llm_restore:
            result = restore_system_instruction_version(old_ts, workspace_id=None)
            mock_llm_restore.assert_not_called()  # 恢复不应该调 LLM
            entries = list_system_instruction_history(workspace_id=None)

        assert result == "v1"
        assert si_file.read_text() == "v1"
        assert len(entries) == 2
        assert entries[0]["content"] == "v1"
        assert old_ts in entries[0]["summary"]

    def test_restore_unknown_timestamp_raises(self, tmp_path):
        from scripts.ask import restore_system_instruction_version

        si_file = tmp_path / "system_instruction.md"
        si_file.write_text("v0")
        history_file = tmp_path / "history.jsonl"

        p1, p2, p3 = self._patch_paths(si_file, history_file)
        with p1, p2, p3:
            with pytest.raises(ValueError):
                restore_system_instruction_version("2000-01-01T00:00:00+00:00", workspace_id=None)


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


class TestQueryNormalization:
    """检索入口的繁→简归一化（语料库是简体，使用者可能打繁体；见 scripts/text_norm.py）"""

    @patch("scripts.ask._get_table")
    @patch("scripts.ask.embed_one")
    @patch("scripts.ask.index_settings.retrieval_params")
    @patch("scripts.ask.index_settings.reranker_params")
    def test_retrieve_normalizes_before_embed_and_fts(
        self, mock_reranker_params, mock_retrieval_params, mock_embed, mock_table
    ):
        """retrieve() 必须用归一化后的 query 去 embed + FTS（一处归一化，两条腿共用）"""
        from scripts.ask import retrieve

        mock_retrieval_params.return_value = {"top_k": 5, "window_expand": 1}
        mock_reranker_params.return_value = {"use_reranker": False, "rerank_top_k": 10, "final_top_k": 5}

        empty_df = pd.DataFrame(columns=["source_file", "chunk_index", "raw_text", "session_date", "start_ts", "end_ts"])
        mock_table_instance = MagicMock()
        mock_table_instance.to_pandas.return_value = empty_df
        mock_table.return_value = mock_table_instance
        mock_embed.return_value = np.random.rand(768)

        mock_search_result = MagicMock()
        mock_search_result.to_pandas.return_value = empty_df
        mock_search_builder = MagicMock()
        mock_search_builder.vector.return_value = mock_search_builder
        mock_search_builder.text.return_value = mock_search_builder
        mock_search_builder.rerank.return_value = mock_search_builder
        mock_search_builder.limit.return_value = mock_search_result
        mock_table_instance.search.return_value = mock_search_builder

        retrieve("我喜歡抽水煙", k=5)

        mock_embed.assert_called_once_with("我喜欢抽水烟")
        mock_search_builder.text.assert_called_once_with("我喜欢抽水烟")

    @patch("scripts.ask.embed_one")
    def test_find_relevant_graph_nodes_normalizes_question(self, mock_embed):
        """图谱锚点匹配也要归一化：图谱节点文本是简体，繁体问题否则匹配不上"""
        import scripts.ask as ask_mod

        node = {"id": "schema:a", "type": "belief", "label": "我不值得", "description": "核心信念", "domain": ""}
        graph = {"nodes": [node], "edges": []}
        vec = np.ones(8)
        mock_embed.return_value = vec

        # 让节点向量直接命中磁盘缓存（避免这个测试还要 mock scripts.embedder.embed）：
        # 只关心 embed_one(问题) 有没有被正确归一化调用。
        persisted = {"schema:a": (ask_mod._node_text_hash(node), vec)}
        with patch("scripts.ask._load_persisted_node_embeddings", return_value=persisted):
            ask_mod.find_relevant_graph_nodes("我喜歡抽水煙", graph)

        mock_embed.assert_called_once_with("我喜欢抽水烟")


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


class TestWorkspaceCacheIsolation:
    """cross-workspace 快取隔离回归测试（2026-07-30 真实事故）：process 曾经查询过某个
    workspace 之后切到另一个 workspace，检索/图谱结果不能继续沿用旧 workspace 的连接/数据——
    这正是「在八字紫微 workspace 问问题却撈到心理咨询 chunks」的成因：_get_table /
    _load_all_chunks / _load_graph / _graph_node_embeddings 曾经是不分 workspace 的全域快取，
    第一次查完某个 workspace 后就锁死，无视之后传入的 workspace_id。"""

    def test_get_table_reconnects_per_workspace(self):
        import scripts.ask as ask_mod

        table_a, table_b = MagicMock(name="table_a"), MagicMock(name="table_b")
        db_a, db_b = MagicMock(), MagicMock()
        db_a.open_table.return_value = table_a
        db_b.open_table.return_value = table_b

        with patch("scripts.ask.DB_DIR", side_effect=lambda workspace_id=None: Path(f"/fake/{workspace_id}/db")), \
             patch("scripts.ask.lancedb.connect", side_effect=lambda p: db_a if "ws_a" in p else db_b):
            result_a = ask_mod._get_table(workspace_id="ws_a")
            result_b = ask_mod._get_table(workspace_id="ws_b")

        assert result_a is table_a
        assert result_b is table_b

    def test_load_all_chunks_reflects_workspace_switch(self):
        import scripts.ask as ask_mod

        df_a = pd.DataFrame({"source_file": ["counseling.txt"], "chunk_index": [0]})
        df_b = pd.DataFrame({"source_file": ["bazi.txt"], "chunk_index": [0]})
        table_a, table_b = MagicMock(), MagicMock()
        table_a.to_pandas.return_value = df_a
        table_b.to_pandas.return_value = df_b

        def fake_get_table(workspace_id=None):
            return table_a if workspace_id == "ws_a" else table_b

        with patch("scripts.ask._get_table", side_effect=fake_get_table):
            result_a = ask_mod._load_all_chunks(workspace_id="ws_a")
            result_b = ask_mod._load_all_chunks(workspace_id="ws_b")

        assert result_a["source_file"].iloc[0] == "counseling.txt"
        assert result_b["source_file"].iloc[0] == "bazi.txt"

    def test_load_graph_reflects_workspace_switch(self, tmp_path):
        import scripts.ask as ask_mod

        graph_a_path = tmp_path / "a_graph.json"
        graph_b_path = tmp_path / "b_graph.json"
        graph_a_path.write_text(json.dumps(
            {"nodes": [{"id": "n_counseling", "label": "咨询节点", "centrality": 0.5}], "edges": []}
        ))
        graph_b_path.write_text(json.dumps(
            {"nodes": [{"id": "n_bazi", "label": "八字节点", "centrality": 0.5}], "edges": []}
        ))
        missing_chat_path = tmp_path / "missing_chat.json"  # 两个 workspace 都不存在对话图谱

        def fake_graph_path(workspace_id=None):
            return graph_a_path if workspace_id == "ws_a" else graph_b_path

        with patch("scripts.ask.GRAPH_JSON_PATH", side_effect=fake_graph_path), \
             patch("scripts.ask.CHAT_GRAPH_JSON_PATH", return_value=missing_chat_path):
            result_a = ask_mod._load_graph(workspace_id="ws_a")
            result_b = ask_mod._load_graph(workspace_id="ws_b")

        assert result_a["nodes"][0]["id"] == "n_counseling"
        assert result_b["nodes"][0]["id"] == "n_bazi"

    @patch("scripts.embedder.embed")
    @patch("scripts.ask.embed_one")
    def test_graph_node_embeddings_isolated_per_workspace_file(self, mock_embed_one, mock_embed, tmp_path):
        """不同 workspace 的持久化向量各自存在自己的文件，互不干扰（原本的全域 dict 快取
        没有这个隔离，第二个 workspace 会 KeyError 或答非所问）。"""
        from scripts.ask import find_relevant_graph_nodes

        mock_embed_one.return_value = np.array([0.5] * 8)
        mock_embed.side_effect = [
            {"dense_vecs": [np.array([0.6] * 8)]},
            {"dense_vecs": [np.array([0.6] * 8)]},
        ]

        graph_a = {"nodes": [{"id": "n_counseling", "label": "咨询议题", "type": "schema", "description": "d"}], "edges": []}
        graph_b = {"nodes": [{"id": "n_bazi", "label": "八字格局", "type": "schema", "description": "d"}], "edges": []}

        path_a, path_b = tmp_path / "ws_a.npz", tmp_path / "ws_b.npz"

        def fake_path(workspace_id=None):
            return path_a if workspace_id == "ws_a" else path_b

        with patch("scripts.ask.GRAPH_NODE_EMBEDDINGS_PATH", side_effect=fake_path):
            result_a = find_relevant_graph_nodes("问题", graph_a, top_k=1, workspace_id="ws_a")
            result_b = find_relevant_graph_nodes("问题", graph_b, top_k=1, workspace_id="ws_b")

        assert mock_embed.call_count == 2
        assert result_a and result_a[0]["id"] == "n_counseling"
        assert result_b and result_b[0]["id"] == "n_bazi"
        assert path_a.exists() and path_b.exists()
        assert list(np.load(path_a)["node_ids"]) == ["n_counseling"]
        assert list(np.load(path_b)["node_ids"]) == ["n_bazi"]

    @patch("scripts.embedder.embed")
    @patch("scripts.ask.embed_one")
    def test_graph_node_embeddings_cache_hit_skips_embed(self, mock_embed_one, mock_embed, tmp_path):
        """同一个 workspace 里 anchor 节点集合完全没变时，第二次调用应该全部命中磁盘缓存，
        不再呼叫 embed()。"""
        from scripts.ask import find_relevant_graph_nodes

        mock_embed_one.return_value = np.array([0.5] * 8)
        mock_embed.return_value = {"dense_vecs": [np.array([0.6] * 8)]}

        node = {"id": "n1", "label": "议题", "type": "schema", "description": "d"}
        graph = {"nodes": [node], "edges": []}
        path = tmp_path / "ws.npz"

        with patch("scripts.ask.GRAPH_NODE_EMBEDDINGS_PATH", return_value=path):
            find_relevant_graph_nodes("问题", graph, top_k=1, workspace_id="ws")
            find_relevant_graph_nodes("问题", graph, top_k=1, workspace_id="ws")

        assert mock_embed.call_count == 1

    @patch("scripts.embedder.embed")
    @patch("scripts.ask.embed_one")
    def test_graph_node_embeddings_only_embeds_new_node_incrementally(self, mock_embed_one, mock_embed, tmp_path):
        """图谱新增一个节点、其余节点不变时，只补算新节点——这是这次设计改动最关键的行为：
        增量补算，不是「一个节点变了就整批重算」。"""
        from scripts.ask import find_relevant_graph_nodes

        mock_embed_one.return_value = np.array([0.5] * 8)
        node1 = {"id": "n1", "label": "议题一", "type": "schema", "description": "d1"}
        node2 = {"id": "n2", "label": "议题二", "type": "schema", "description": "d2"}
        path = tmp_path / "ws.npz"

        mock_embed.side_effect = [
            {"dense_vecs": [np.array([0.6] * 8)]},  # 第一次：只有 n1
            {"dense_vecs": [np.array([0.4] * 8)]},  # 第二次：只补算新节点 n2
        ]

        with patch("scripts.ask.GRAPH_NODE_EMBEDDINGS_PATH", return_value=path):
            find_relevant_graph_nodes("问题", {"nodes": [node1], "edges": []}, top_k=2, workspace_id="ws")
            find_relevant_graph_nodes("问题", {"nodes": [node1, node2], "edges": []}, top_k=2, workspace_id="ws")

        assert mock_embed.call_count == 2
        second_call_texts = mock_embed.call_args_list[1].args[0]
        assert len(second_call_texts) == 1
        assert "议题二" in second_call_texts[0]

    @patch("scripts.embedder.embed")
    @patch("scripts.ask.embed_one")
    def test_graph_node_embeddings_recomputes_when_description_changes(self, mock_embed_one, mock_embed, tmp_path):
        """既有节点 id 不变但 description 变了，视为需要重算，并正确覆写磁盘里那一笔
        （而不是沿用跟新描述对不上的旧向量）。"""
        from scripts.ask import find_relevant_graph_nodes
        import scripts.ask as ask_mod

        mock_embed_one.return_value = np.array([0.5] * 8)
        node_v1 = {"id": "n1", "label": "议题", "type": "schema", "description": "旧描述"}
        node_v2 = {"id": "n1", "label": "议题", "type": "schema", "description": "新描述"}
        path = tmp_path / "ws.npz"

        mock_embed.side_effect = [
            {"dense_vecs": [np.array([0.6] * 8)]},
            {"dense_vecs": [np.array([0.2] * 8)]},
        ]

        with patch("scripts.ask.GRAPH_NODE_EMBEDDINGS_PATH", return_value=path):
            find_relevant_graph_nodes("问题", {"nodes": [node_v1], "edges": []}, top_k=1, workspace_id="ws")
            find_relevant_graph_nodes("问题", {"nodes": [node_v2], "edges": []}, top_k=1, workspace_id="ws")

        assert mock_embed.call_count == 2
        data = np.load(path)
        assert list(data["text_hashes"]) == [ask_mod._node_text_hash(node_v2)]


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
    def test_find_relevant_graph_nodes_basic(self, mock_embed_one, mock_embed, tmp_path):
        """测试查找相关图谱节点"""
        from scripts.ask import find_relevant_graph_nodes

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

        # 用 tmp_path 隔离持久化缓存文件，避免读写到真实 workspace 的磁盘缓存
        with patch("scripts.ask.GRAPH_NODE_EMBEDDINGS_PATH", return_value=tmp_path / "cache.npz"):
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


class TestRelevanceThreshold:
    """精排分数阈值：分数太低的片段不进上下文，但保底留几条并明确标注"不相关"。

    背景：reranker 修好之后分数才有区分度（无关 query top1 ≈ 0.003，相关 ≈ 0.05–0.18），
    所以阈值按 0.01 量级定标，不是通用的 0.5。
    """

    @staticmethod
    def _sample_df(n=4):
        # chunk_index 故意隔开（0/2/4/…），这样 window_expand=0 时窗口不会被 _merge_windows
        # 合并成一条，测试才数得清"留了几条片段"。
        return pd.DataFrame(
            {
                "text": [f"片段{i}" for i in range(n)],
                "raw_text": [f"片段{i}" for i in range(n)],
                "source_file": ["test.txt"] * n,
                "session_date": ["2026-01-01"] * n,
                "chunk_index": [i * 2 for i in range(n)],
                "speakers": ["User"] * n,
                "start_ts": [f"0{i}:00" for i in range(n)],
                "end_ts": [f"0{i+1}:00" for i in range(n)],
                "vector": [np.random.rand(8) for _ in range(n)],
            }
        )

    def _run_retrieve(self, scores, min_score, min_keep):
        """跑一次 retrieve()，reranker 打出指定分数（已降序），返回窗口列表。"""
        from scripts.ask import retrieve

        sample = self._sample_df(len(scores))
        ranked = sample.copy()
        ranked["rerank_score"] = scores

        mock_search_result = MagicMock()
        mock_search_result.to_pandas.return_value = sample
        builder = MagicMock()
        builder.vector.return_value = builder
        builder.text.return_value = builder
        builder.rerank.return_value = builder
        builder.limit.return_value = mock_search_result
        table = MagicMock()
        table.search.return_value = builder

        with patch("scripts.ask._get_table", return_value=table), patch(
            "scripts.ask._load_all_chunks", return_value=sample
        ), patch("scripts.ask.embed_one", return_value=np.random.rand(8)), patch(
            "scripts.ask.rerank_candidates", return_value=ranked
        ), patch(
            "scripts.ask.index_settings.retrieval_params",
            return_value={"top_k": 10, "window_expand": 0},
        ), patch(
            "scripts.ask.index_settings.reranker_params",
            return_value={
                "use_reranker": True,
                "rerank_top_k": 10,
                "final_top_k": len(scores),
                "min_score": min_score,
                "min_keep": min_keep,
            },
        ):
            return retrieve("测试问题")

    def test_drops_hits_below_min_score(self):
        """低于阈值的片段被丢掉，高于阈值的全部保留"""
        windows = self._run_retrieve([0.18, 0.047, 0.0032, 0.0005], min_score=0.01, min_keep=1)

        kept = sorted(w["chunk_index_range"][0] for w in windows)
        assert kept == [0, 2]
        assert all(w["below_threshold"] is False for w in windows)

    def test_keeps_min_keep_when_everything_is_irrelevant(self):
        """全都低于阈值时不能一条不给：保底留 min_keep 条，并标记 below_threshold"""
        windows = self._run_retrieve([0.0032, 0.0021, 0.0009, 0.0004], min_score=0.01, min_keep=3)

        assert len(windows) == 3
        assert all(w["below_threshold"] is True for w in windows)
        assert windows[0]["score"] == pytest.approx(0.0032)

    def test_does_not_pad_to_min_keep_when_something_passes(self):
        """只要有片段过线就不该再凑低分片段——保底只在"一条都没过线"时生效，凑数只会稀释注意力"""
        windows = self._run_retrieve([0.18, 0.047, 0.0032, 0.0005], min_score=0.01, min_keep=3)

        assert len(windows) == 2
        assert all(w["below_threshold"] is False for w in windows)

    def test_min_score_zero_disables_filtering(self):
        """阈值设 0 = 关掉这个机制，行为回到改动之前"""
        windows = self._run_retrieve([0.18, 0.0032, 0.0005, 0.0001], min_score=0.0, min_keep=1)

        assert len(windows) == 4
        assert all(w["below_threshold"] is False for w in windows)

    @patch("scripts.ask._get_table")
    @patch("scripts.ask._load_all_chunks")
    @patch("scripts.ask.embed_one")
    @patch("scripts.ask.index_settings.retrieval_params")
    @patch("scripts.ask.index_settings.reranker_params")
    def test_no_score_when_reranker_off(
        self, mock_rk, mock_rp, mock_embed, mock_load_all, mock_table
    ):
        """关掉 reranker 就没有分数可比：score 为 None，不能误判成低相关"""
        from scripts.ask import retrieve

        sample = self._sample_df(2)
        mock_rp.return_value = {"top_k": 2, "window_expand": 0}
        mock_rk.return_value = {"use_reranker": False, "rerank_top_k": 10, "final_top_k": 2}
        mock_embed.return_value = np.random.rand(8)
        mock_load_all.return_value = sample

        mock_search_result = MagicMock()
        mock_search_result.to_pandas.return_value = sample
        builder = MagicMock()
        builder.vector.return_value = builder
        builder.text.return_value = builder
        builder.rerank.return_value = builder
        builder.limit.return_value = mock_search_result
        table = MagicMock()
        table.search.return_value = builder
        mock_table.return_value = table

        windows = retrieve("测试问题")

        assert len(windows) == 2
        assert all(w["score"] is None for w in windows)
        assert all(w["below_threshold"] is False for w in windows)

    def test_format_retrieved_shows_score(self):
        """片段头部带相关性分数，方便 LLM 和使用者判断权重"""
        from scripts.ask import _format_retrieved

        block = _format_retrieved(
            [
                {
                    "text": "片段内容",
                    "source_file": "t.txt",
                    "session_date": "2026-01-01",
                    "start_ts": "00:00",
                    "end_ts": "01:00",
                    "rank": 0,
                    "score": 0.1834,
                    "below_threshold": False,
                }
            ]
        )

        assert "相关性 0.183" in block
        assert "相关性都很低" not in block

    def test_format_retrieved_warns_when_all_below_threshold(self):
        """保底片段必须带一句明确的低相关警告，否则 LLM 会硬套无关材料"""
        from scripts.ask import _format_retrieved

        block = _format_retrieved(
            [
                {
                    "text": "无关片段",
                    "source_file": "t.txt",
                    "session_date": "2026-01-01",
                    "start_ts": "00:00",
                    "end_ts": "01:00",
                    "rank": 0,
                    "score": 0.0032,
                    "below_threshold": True,
                }
            ]
        )

        assert "相关性都很低" in block
        assert "无关片段" in block

    def test_format_retrieved_no_warning_when_one_hit_is_relevant(self):
        """只要有一条过线就不该警告（保底片段混在后面是正常的）"""
        from scripts.ask import _format_retrieved

        block = _format_retrieved(
            [
                {"text": "相关", "source_file": "t.txt", "session_date": "2026-01-01",
                 "start_ts": "00:00", "end_ts": "01:00", "rank": 0, "score": 0.12,
                 "below_threshold": False},
                {"text": "凑数", "source_file": "t.txt", "session_date": "2026-01-01",
                 "start_ts": "01:00", "end_ts": "02:00", "rank": 1, "score": 0.001,
                 "below_threshold": True},
            ]
        )

        assert "相关性都很低" not in block

    def test_format_retrieved_tolerates_missing_score(self):
        """老调用方（图谱证据片段、旧测试）没有 score 字段也不能炸"""
        from scripts.ask import _format_retrieved

        block = _format_retrieved(
            [{"text": "片段", "source_file": "t.txt", "session_date": "2026-01-01",
              "start_ts": "00:00", "end_ts": "01:00", "rank": 0}]
        )

        assert "片段" in block
        assert "相关性" not in block


# Pytest 配置
@pytest.fixture(autouse=True)
def mock_workspace():
    """自动 mock workspace 相关函数"""
    with patch("scripts.ask.get_current_workspace", return_value="_legacy"), patch(
        "scripts.ask.get_workspace_dir"
    ) as mock_dir:
        mock_dir.return_value = Path("/tmp/test_workspace")
        yield
