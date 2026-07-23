"""边缘情况和错误处理测试。

测试异常场景：
1. 空输入处理
2. 大文件处理
3. 特殊字符处理
4. 并发访问
5. 资源限制
6. 降级行为
"""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestEmptyInputHandling:
    """空输入处理"""

    def test_parse_empty_file(self, tmp_path):
        """测试解析空文件"""
        from scripts.parse import parse_transcript

        empty_file = tmp_path / "empty.txt"
        empty_file.write_text("")

        with pytest.raises(Exception):  # 应该抛出合适的异常
            parse_transcript(str(empty_file))

    def test_answer_with_empty_question(self):
        """测试空问题"""
        from scripts.ask import answer

        with patch("scripts.ask._get_table"), patch("scripts.llm.ask_llm") as mock_llm:
            mock_llm.return_value = "请提供一个具体的问题。"

            result = answer("", k=5)

            # 应该仍然返回结果，不崩溃
            assert "answer" in result

    def test_retrieve_from_empty_db(self):
        """测试从空向量库检索"""
        from scripts.ask import retrieve

        with patch("scripts.ask._get_table") as mock_table:
            mock_table_instance = MagicMock()
            mock_table_instance.to_pandas.return_value = MagicMock(empty=True)
            mock_table.return_value = mock_table_instance

            result = retrieve("测试问题", k=5, workspace_id=None)

            # 应该返回空列表，不崩溃
            assert result == [] or len(result) == 0


class TestLargeInputHandling:
    """大文件处理"""

    def test_parse_large_transcript(self, tmp_path):
        """测试解析大文件"""
        from scripts.parse import parse_transcript

        large_file = tmp_path / "large_2026-01-01.txt"
        # 生成 10000 行对话
        content = "\n".join([f"User: 问题{i}\nAssistant: 回答{i}" for i in range(5000)])
        large_file.write_text(content)

        result = parse_transcript(str(large_file))

        assert len(result.turns) == 5000

    def test_chunk_large_session(self, tmp_path):
        """测试分块大文件"""
        from scripts.chunk import chunk_session
        from scripts.parse import parse_transcript

        large_file = tmp_path / "large_2026-01-01.txt"
        # 每段回答 1000 字
        content = "\n".join([f"User: 问题{i}\nAssistant: {'回答' * 500}" for i in range(100)])
        large_file.write_text(content)

        session = parse_transcript(str(large_file))
        chunks = chunk_session(session, workspace_id=None)

        # 应该生成多个 chunks
        assert len(chunks) > 100  # 至少比对话轮数多

    @pytest.mark.slow
    def test_answer_with_large_history(self):
        """测试处理长对话历史"""
        from scripts.ask import answer

        # 生成 100 轮对话历史
        history = []
        for i in range(100):
            history.append({"role": "user", "content": f"问题{i}"})
            history.append({"role": "assistant", "content": f"回答{i}" * 100})

        with patch("scripts.ask._get_table"), patch("scripts.llm.ask_llm") as mock_llm:
            mock_llm.return_value = "基于历史的回答"

            result = answer("新问题", history=history, k=5)

            # 应该能处理（可能会压缩历史）
            assert "answer" in result


class TestSpecialCharacterHandling:
    """特殊字符处理"""

    def test_parse_with_special_chars(self, tmp_path):
        """测试解析包含特殊字符的文件"""
        from scripts.parse import parse_transcript

        special_file = tmp_path / "special_2026-01-01.txt"
        content = """
User: 这是一个"引号"测试
Assistant: 回答包含 <标签> 和 & 符号
User: 还有 emoji 😀 和 中文标点：、。！？
Assistant: SQL 注入测试 ' OR 1=1 --
"""
        special_file.write_text(content)

        result = parse_transcript(str(special_file))

        assert len(result.turns) == 2
        assert "😀" in result.turns[1].user_text

    def test_sanitize_query(self):
        """测试查询清理"""
        from scripts.ask import sanitize

        # 特殊字符应该被移除
        assert sanitize("test'query") == "testquery"
        assert sanitize('test"query') == "testquery"
        assert sanitize("test\\query") == "testquery"

    def test_graph_with_special_labels(self, tmp_path):
        """测试图谱节点包含特殊字符"""
        graph_file = tmp_path / "graph.json"
        graph_data = {
            "nodes": [
                {"id": "n1", "label": "节点<标签>", "type": "concept"},
                {"id": "n2", "label": '节点"引号"', "type": "concept"},
            ],
            "edges": [],
        }
        graph_file.write_text(json.dumps(graph_data, ensure_ascii=False))

        # 读取不应该出错
        loaded = json.loads(graph_file.read_text())
        assert loaded["nodes"][0]["label"] == "节点<标签>"


class TestConcurrentAccess:
    """并发访问测试"""

    @pytest.mark.slow
    def test_concurrent_workspace_reads(self, tmp_path):
        """测试并发读取 workspace 配置"""
        from scripts.workspace_manager import create_workspace, load_workspace_config
        import threading

        workspaces_dir = tmp_path / "workspaces"
        workspaces_dir.mkdir()

        with patch("scripts.workspace_manager.PRIVATE_DIR", tmp_path):
            ws_id = create_workspace("test-ws", "Test", "generic", "generic")

            results = []
            errors = []

            def load_config():
                try:
                    config = load_workspace_config(ws_id)
                    results.append(config)
                except Exception as e:
                    errors.append(e)

            # 10 个线程并发读取
            threads = [threading.Thread(target=load_config) for _ in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            # 应该全部成功
            assert len(results) == 10
            assert len(errors) == 0

    @pytest.mark.slow
    @patch("scripts.chat_store.CHAT_SESSIONS_DIR")
    def test_concurrent_session_writes(self, mock_dir, tmp_path):
        """测试并发写入会话（可能冲突）"""
        from scripts.chat_store import new_session_id, save_session
        import threading

        sessions_dir = tmp_path / "chat_sessions"
        sessions_dir.mkdir()
        mock_dir.return_value = sessions_dir

        session_id = new_session_id()
        errors = []

        def save():
            try:
                save_session(
                    session_id,
                    "测试",
                    [{"role": "user", "content": "test"}],
                    workspace_id=None,
                )
            except Exception as e:
                errors.append(e)

        # 5 个线程同时写入同一个 session
        threads = [threading.Thread(target=save) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 最后一次写入应该成功（文件存在）
        session_file = sessions_dir / f"{session_id}.json"
        assert session_file.exists()


class TestResourceLimits:
    """资源限制测试"""

    def test_max_chunks_per_session(self, tmp_path):
        """测试单个会话最大 chunks 数"""
        from scripts.chunk import chunk_session
        from scripts.parse import parse_transcript

        # 生成极大的文件
        huge_file = tmp_path / "huge_2026-01-01.txt"
        content = "User: 问题\nAssistant: " + "回答" * 100000
        huge_file.write_text(content)

        session = parse_transcript(str(huge_file))
        chunks = chunk_session(session, workspace_id=None)

        # 应该被分成很多 chunks，但不应该无限多
        assert len(chunks) > 0
        assert len(chunks) < 10000  # 合理上限

    @pytest.mark.slow
    def test_max_context_length(self):
        """测试最大 context 长度限制"""
        from scripts.ask import answer

        # 超长历史
        history = [{"role": "user", "content": "问题" * 10000} for _ in range(100)]

        with patch("scripts.ask._get_table"), patch("scripts.llm.ask_llm") as mock_llm:
            mock_llm.return_value = "回答"

            result = answer("新问题", history=history, max_context=450000)

            # 应该自动压缩，不崩溃
            assert "answer" in result


class TestGracefulDegradation:
    """降级行为测试"""

    def test_missing_schema_file_fallback(self, tmp_path):
        """测试缺失 schema 文件时的降级"""
        from scripts.graph_schema_loader import load_schema

        with patch("scripts.graph_schema_loader.GRAPH_SCHEMAS_DIR", tmp_path):
            # schema 文件不存在，应该降级到默认值
            schema = load_schema("_legacy")

            # 应该返回默认 schema（hardcoded）
            assert "node_types" in schema
            assert "relation_types" in schema

    @patch("scripts.llm.ask_llm")
    def test_llm_error_handling(self, mock_llm):
        """测试 LLM 调用失败时的处理"""
        from scripts.ask import answer

        mock_llm.side_effect = Exception("API Error")

        with patch("scripts.ask._get_table"):
            with pytest.raises(Exception):
                answer("测试问题", k=5)

    def test_missing_summary_file(self, tmp_path):
        """测试缺失摘要文件的处理"""
        from scripts.summarize import summary_path

        with patch("scripts.summarize.SUMMARIES_DIR") as mock_dir:
            mock_dir.return_value = tmp_path / "summaries"

            # 获取不存在的摘要路径
            path = summary_path("2026-01-01.txt", workspace_id=None)

            # 应该返回路径，但文件不存在
            assert isinstance(path, Path)
            assert not path.exists()

    def test_corrupted_graph_json(self, tmp_path):
        """测试损坏的图谱 JSON"""
        graph_file = tmp_path / "graph.json"
        graph_file.write_text("{invalid json")

        with pytest.raises(json.JSONDecodeError):
            json.loads(graph_file.read_text())

    @patch("scripts.embedder.embed_one")
    def test_embedding_failure_handling(self, mock_embed):
        """测试 embedding 失败处理"""
        from scripts.chunk import Chunk
        from scripts.ingest import ingest

        mock_embed.side_effect = Exception("Embedding API Error")

        chunks = [
            Chunk(
                text="测试",
                chunk_index=0,
                source_file="test.txt",
                session_date="2026-01-01",
                speakers="User",
                start_ts="00:00",
                end_ts="01:00",
            )
        ]

        with patch("scripts.ingest._get_table"):
            with pytest.raises(Exception):
                ingest(chunks, rebuild=True, workspace_id=None)


class TestDataValidation:
    """数据验证测试"""

    def test_invalid_date_format(self, tmp_path):
        """测试无效日期格式"""
        from scripts.parse import parse_transcript

        invalid_file = tmp_path / "invalid_date.txt"
        invalid_file.write_text("User: test\nAssistant: test")

        with pytest.raises(Exception):
            # 文件名没有日期，应该报错
            parse_transcript(str(invalid_file))

    def test_invalid_workspace_config(self, tmp_path):
        """测试无效的 workspace 配置"""
        from scripts.workspace_manager import load_workspace_config

        config_file = tmp_path / ".workspace_config.json"
        config_file.write_text(json.dumps({"name": "test"}))  # 缺少必需字段

        with patch("scripts.workspace_manager.get_workspace_dir", return_value=tmp_path):
            config = load_workspace_config("test")

            # 应该能加载，缺失字段使用默认值
            assert "name" in config

    def test_invalid_graph_schema(self):
        """测试无效的 graph schema"""
        from scripts.graph_utils import resolve_graph

        # 空节点列表
        result = resolve_graph([], [])

        assert result["nodes"] == []
        assert result["edges"] == []

    def test_negative_chunk_size(self):
        """测试负数 chunk size"""
        from scripts import index_settings

        with pytest.raises(ValueError):
            index_settings.update(chunk_size=-1)


class TestBackwardCompatibility:
    """向后兼容性测试"""

    def test_legacy_path_compatibility(self, tmp_path):
        """测试旧路径兼容性"""
        from scripts.workspace_manager import get_current_workspace, get_workspace_dir

        # 模拟旧结构（没有 workspaces/ 目录）
        with patch("scripts.workspace_manager.PRIVATE_DIR", tmp_path):
            # 不创建 workspaces 目录
            ws = get_current_workspace()

            # 应该返回 _legacy
            assert ws == "_legacy"

            # 应该能获取目录（指向 private.nosync 根目录）
            ws_dir = get_workspace_dir("_legacy")
            assert ws_dir == tmp_path

    def test_old_settings_format(self, tmp_path):
        """测试旧设置格式兼容"""
        from scripts import settings

        old_settings = tmp_path / "gemini_settings.json"
        old_settings.write_text(
            json.dumps(
                {
                    "model": "old-model",  # 旧格式
                    "temperature": 0.7,
                }
            )
        )

        with patch("scripts.settings.GEMINI_SETTINGS_PATH", old_settings):
            # 应该能处理旧格式（可能使用默认值）
            result = settings.load_for_ui()

            assert "dialogue" in result
            assert "summary" in result


# Pytest 配置
@pytest.fixture(autouse=True)
def mock_api_keys(monkeypatch):
    """Mock API keys"""
    monkeypatch.setenv("GEMINI_API_KEY", "fake-test-key-" + "a" * 32)


@pytest.fixture(autouse=True)
def isolation(tmp_path, monkeypatch):
    """为所有测试提供隔离环境"""
    # 不在这里 mock，让每个测试自己决定
    pass
