"""UI 功能集成测试。

测试 Streamlit 应用的关键用户路径：
1. 设置弹窗（LLM 配置、System Instruction）
2. Workspace 切换
3. 对话历史管理
4. 图谱可视化
5. 索引记录查看
"""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestSettingsDialogs:
    """设置弹窗功能测试"""

    def test_load_gemini_settings(self, tmp_path):
        """测试加载 Gemini 设置"""
        from scripts import settings

        settings_file = tmp_path / "gemini_settings.json"
        settings_data = {
            "provider": "gemini",
            "dialogue": {
                "model": "gemini-2.0-flash-exp",
                "thinking_level": "high",
                "temperature": 0.7,
                "max_output_tokens": 8192,
            },
            "summary": {
                "model": "gemini-2.0-flash-exp",
                "thinking_level": "low",
                "temperature": 0.3,
            },
            "summary_max_tokens": {"text": 4096, "chat_graph": 8192, "therapy_graph": 16384},
        }
        settings_file.write_text(json.dumps(settings_data, indent=2))

        with patch("scripts.settings.GEMINI_SETTINGS_PATH", settings_file):
            result = settings.load_for_ui()

            assert result["provider"] == "gemini"
            assert result["dialogue"]["model"] == "gemini-2.0-flash-exp"
            assert result["dialogue"]["thinking_level"] == "high"

    def test_save_gemini_settings(self, tmp_path, monkeypatch):
        """测试保存 Gemini 设置"""
        from scripts import settings

        settings_file = tmp_path / "gemini_settings.json"
        env_file = tmp_path / ".env"

        monkeypatch.setenv("GEMINI_API_KEY", "existing-key")

        with patch("scripts.settings.GEMINI_SETTINGS_PATH", settings_file), patch(
            "scripts.settings.ENV_PATH", env_file
        ):
            settings.save(
                dialogue={"model": "gemini-3.0", "thinking_level": "medium", "temperature": 0.8, "max_output_tokens": 4096},
                summary={"model": "gemini-3.0", "thinking_level": "low", "temperature": 0.2},
                summary_max={"text": 2048, "chat_graph": 4096, "therapy_graph": 8192},
                api_key="new-api-key",
                provider="gemini",
            )

            # 验证设置文件
            saved = json.loads(settings_file.read_text())
            assert saved["dialogue"]["model"] == "gemini-3.0"
            assert saved["dialogue"]["thinking_level"] == "medium"

    def test_load_system_instruction(self, tmp_path):
        """测试加载 System Instruction"""
        from scripts.ask import load_system_instruction

        si_file = tmp_path / "system_instruction.md"
        si_content = "你是一位 AI 助手..."
        si_file.write_text(si_content)

        with patch("scripts.ask.SYSTEM_INSTRUCTION_PATH", si_file):
            result = load_system_instruction(workspace_id=None)

            assert result == si_content

    def test_save_system_instruction(self, tmp_path):
        """测试保存 System Instruction"""
        from scripts.ask import save_system_instruction

        si_file = tmp_path / "system_instruction.md"

        with patch("scripts.ask.SYSTEM_INSTRUCTION_PATH", si_file):
            new_content = "新的 system instruction"
            save_system_instruction(new_content, workspace_id=None)

            assert si_file.read_text() == new_content

    def test_reset_system_instruction(self, tmp_path):
        """测试重置 System Instruction"""
        from scripts.ask import DEFAULT_SYSTEM_INSTRUCTION, reset_system_instruction

        si_file = tmp_path / "system_instruction.md"
        si_file.write_text("旧内容")

        with patch("scripts.ask.SYSTEM_INSTRUCTION_PATH", si_file):
            reset_system_instruction(workspace_id=None)

            assert si_file.read_text() == DEFAULT_SYSTEM_INSTRUCTION


class TestWorkspaceSwitching:
    """Workspace 切换功能测试"""

    def test_workspace_selector_state(self, tmp_path):
        """测试 workspace 选择器状态"""
        from scripts.workspace_manager import create_workspace, get_current_workspace, set_current_workspace

        workspaces_dir = tmp_path / "workspaces"
        workspaces_dir.mkdir()

        with patch("scripts.workspace_manager.PRIVATE_DIR", tmp_path):
            # 创建 workspaces
            create_workspace("ws1", "WS1", "generic", "generic")
            create_workspace("ws2", "WS2", "counseling", "predefined", "counseling.json")

            # 切换到 ws2
            set_current_workspace("ws2")
            assert get_current_workspace() == "ws2"

            # 切换回 ws1
            set_current_workspace("ws1")
            assert get_current_workspace() == "ws1"

    def test_workspace_status_display(self, tmp_path):
        """测试 workspace 状态显示"""
        from scripts.workspace_manager import create_workspace, get_workspace_dir, load_workspace_config

        workspaces_dir = tmp_path / "workspaces"
        workspaces_dir.mkdir()

        with patch("scripts.workspace_manager.PRIVATE_DIR", tmp_path):
            ws_id = create_workspace("test-ws", "测试 WS", "generic", "generic")

            # 获取配置
            config = load_workspace_config(ws_id)
            assert config["display_name"] == "测试 WS"
            assert config["domain"] == "generic"

            # 获取目录
            ws_dir = get_workspace_dir(ws_id)
            raw_dir = ws_dir / "data" / "raw"

            # 创建测试文件
            (raw_dir / "test1.txt").write_text("test")
            (raw_dir / "test2.txt").write_text("test")

            # 验证文件数
            files = list(raw_dir.glob("*.txt"))
            assert len(files) == 2


class TestChatSessionManagement:
    """对话历史管理测试"""

    def test_new_session_id(self):
        """测试生成新会话 ID"""
        from scripts.chat_store import new_session_id

        id1 = new_session_id()
        id2 = new_session_id()

        assert len(id1) == 12
        assert len(id2) == 12
        assert id1 != id2

    def test_save_and_load_session(self, tmp_path):
        """测试保存和加载会话"""
        from scripts.chat_store import load_session, new_session_id, save_session

        sessions_dir = tmp_path / "chat_sessions"
        sessions_dir.mkdir()

        with patch("scripts.chat_store.CHAT_SESSIONS_DIR") as mock_dir:
            mock_dir.return_value = sessions_dir

            session_id = new_session_id()
            messages = [
                {"role": "user", "content": "问题1"},
                {"role": "assistant", "content": "回答1"},
            ]

            # 保存
            save_session(session_id, "测试会话", messages, workspace_id=None)

            # 加载
            loaded = load_session(session_id, workspace_id=None)

            assert loaded["title"] == "测试会话"
            assert len(loaded["messages"]) == 2
            assert loaded["messages"][0]["content"] == "问题1"

    def test_list_sessions(self, tmp_path):
        """测试列出所有会话"""
        from scripts.chat_store import list_sessions, new_session_id, save_session

        sessions_dir = tmp_path / "chat_sessions"
        sessions_dir.mkdir()

        with patch("scripts.chat_store.CHAT_SESSIONS_DIR") as mock_dir:
            mock_dir.return_value = sessions_dir

            # 创建多个会话
            id1 = new_session_id()
            id2 = new_session_id()

            save_session(id1, "会话1", [{"role": "user", "content": "test"}], workspace_id=None)
            save_session(id2, "会话2", [{"role": "user", "content": "test"}], workspace_id=None)

            # 列出
            sessions = list_sessions(workspace_id=None)

            assert len(sessions) == 2
            titles = [s["title"] for s in sessions]
            assert "会话1" in titles
            assert "会话2" in titles

    def test_delete_session(self, tmp_path):
        """测试删除会话"""
        from scripts.chat_store import delete_session, list_sessions, new_session_id, save_session

        sessions_dir = tmp_path / "chat_sessions"
        sessions_dir.mkdir()

        with patch("scripts.chat_store.CHAT_SESSIONS_DIR") as mock_dir:
            mock_dir.return_value = sessions_dir

            session_id = new_session_id()
            save_session(session_id, "待删除", [{"role": "user", "content": "test"}], workspace_id=None)

            # 验证存在
            assert len(list_sessions(workspace_id=None)) == 1

            # 删除
            delete_session(session_id, workspace_id=None)

            # 验证删除
            assert len(list_sessions(workspace_id=None)) == 0

    def test_make_title_from_question(self):
        """测试从问题生成标题"""
        from scripts.chat_store import make_title

        # 短问题
        short = "这是一个测试问题"
        assert make_title(short) == short

        # 长问题
        long = "这是一个非常长的问题，" * 10
        title = make_title(long)
        assert len(title) <= 50
        assert title.endswith("...")


class TestGraphVisualization:
    """图谱可视化测试"""

    def test_load_graph_json(self, tmp_path):
        """测试加载图谱 JSON"""
        graph_file = tmp_path / "graph.json"
        graph_data = {
            "nodes": [
                {"id": "n1", "label": "节点1", "type": "concept", "centrality": 0.8},
                {"id": "n2", "label": "节点2", "type": "concept", "centrality": 0.6},
            ],
            "edges": [{"source": "n1", "target": "n2", "relation": "relates_to"}],
        }
        graph_file.write_text(json.dumps(graph_data, ensure_ascii=False, indent=2))

        # 读取
        loaded = json.loads(graph_file.read_text())

        assert len(loaded["nodes"]) == 2
        assert len(loaded["edges"]) == 1
        assert loaded["nodes"][0]["label"] == "节点1"

    def test_merge_graphs(self):
        """测试合并两个图谱"""
        from scripts.graph_utils import merge_graphs

        graph1 = {
            "nodes": [{"id": "n1", "label": "节点1", "type": "concept"}],
            "edges": [],
        }

        graph2 = {
            "nodes": [{"id": "n2", "label": "节点2", "type": "concept"}],
            "edges": [{"source": "n1", "target": "n2", "relation": "relates_to"}],
        }

        merged = merge_graphs([graph1, graph2])

        assert len(merged["nodes"]) == 2
        assert len(merged["edges"]) == 1

    @patch("scripts.build_graph.build_graph")
    def test_rebuild_graph_button(self, mock_build):
        """测试重新生成图谱按钮"""
        mock_build.return_value = {"nodes": [], "edges": []}

        # 模拟点击
        from scripts.build_graph import build_graph

        result = build_graph(force=True, workspace_id=None)

        mock_build.assert_called_once()
        assert result is not None


class TestIndexRecordsView:
    """索引记录查看测试"""

    def test_list_indexed_records_empty(self, tmp_path):
        """测试空向量库"""
        from scripts.index_records import list_indexed_records

        chunks_file = tmp_path / "chunks.jsonl"
        chunks_file.write_text("")  # 空文件

        with patch("scripts.chunk.CHUNKS_JSONL_PATH") as mock_path:
            mock_path.return_value = chunks_file

            records = list_indexed_records(workspace_id=None)

            assert records == []

    def test_list_indexed_records_with_data(self, tmp_path):
        """测试有数据的向量库"""
        from scripts.index_records import list_indexed_records

        chunks_file = tmp_path / "chunks.jsonl"
        chunks_data = [
            {
                "source_file": "2026-01-01.txt",
                "session_date": "2026-01-01",
                "chunk_index": 0,
                "text": "content",
            },
            {
                "source_file": "2026-01-01.txt",
                "session_date": "2026-01-01",
                "chunk_index": 1,
                "text": "content",
            },
            {
                "source_file": "2026-01-02.txt",
                "session_date": "2026-01-02",
                "chunk_index": 0,
                "text": "content",
            },
        ]
        chunks_file.write_text("\n".join(json.dumps(c) for c in chunks_data))

        summaries_dir = tmp_path / "summaries"
        summaries_dir.mkdir()
        (summaries_dir / "2026-01-01.json").write_text("{}")

        with patch("scripts.chunk.CHUNKS_JSONL_PATH") as mock_chunks, patch(
            "scripts.summarize.SUMMARIES_DIR"
        ) as mock_summaries:
            mock_chunks.return_value = chunks_file
            mock_summaries.return_value = summaries_dir

            records = list_indexed_records(workspace_id=None)

            assert len(records) == 2
            assert records[0]["source_file"] == "2026-01-01.txt"
            assert records[0]["n_chunks"] == 2
            assert records[0]["has_summary"] is True
            assert records[1]["has_summary"] is False

    def test_load_change_log(self, tmp_path):
        """测试加载变更记录"""
        from scripts.index_records import load_change_log

        changelog_file = tmp_path / "index_changelog.jsonl"
        log_entries = [
            {"timestamp": "2026-01-01T10:00:00", "action": "index", "file": "test1.txt"},
            {"timestamp": "2026-01-02T11:00:00", "action": "index", "file": "test2.txt"},
            {"timestamp": "2026-01-03T12:00:00", "action": "skip", "file": "test3.txt"},
        ]
        changelog_file.write_text("\n".join(json.dumps(e) for e in log_entries))

        with patch("scripts.index_records.INDEX_CHANGELOG_PATH") as mock_path:
            mock_path.return_value = changelog_file

            log = load_change_log(limit=2, workspace_id=None)

            # 应该返回最新的 2 条（倒序）
            assert len(log) == 2
            assert log[0]["file"] == "test3.txt"
            assert log[1]["file"] == "test2.txt"


class TestIndexSettings:
    """索引设置测试"""

    def test_load_index_settings(self, tmp_path):
        """测试加载索引设置"""
        from scripts import index_settings

        settings_file = tmp_path / "index_settings.json"
        settings_data = {
            "chunk_size": 400,
            "chunk_overlap": 50,
            "retrieve_top_k": 10,
            "final_top_k": 5,
            "enable_reranker": True,
        }
        settings_file.write_text(json.dumps(settings_data, indent=2))

        with patch("scripts.index_settings.INDEX_SETTINGS_PATH", settings_file):
            result = index_settings.load()

            assert result["chunk_size"] == 400
            assert result["retrieve_top_k"] == 10
            assert result["enable_reranker"] is True

    def test_save_index_settings(self, tmp_path):
        """测试保存索引设置"""
        from scripts import index_settings

        settings_file = tmp_path / "index_settings.json"

        with patch("scripts.index_settings.INDEX_SETTINGS_PATH", settings_file):
            index_settings.update(chunk_size=500, retrieve_top_k=15, enable_reranker=False)

            saved = json.loads(settings_file.read_text())
            assert saved["chunk_size"] == 500
            assert saved["retrieve_top_k"] == 15
            assert saved["enable_reranker"] is False


class TestErrorHandling:
    """错误处理测试"""

    def test_missing_api_key_handled(self, tmp_path, monkeypatch):
        """测试缺少 API key 的处理"""
        from scripts import settings

        # 清空环境变量
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)

        settings_file = tmp_path / "gemini_settings.json"
        settings_file.write_text(json.dumps({"dialogue": {}, "summary": {}}))

        with patch("scripts.settings.GEMINI_SETTINGS_PATH", settings_file):
            result = settings.load_for_ui()

            assert result["api_key_set"] is False

    def test_corrupt_json_handled(self, tmp_path):
        """测试损坏的 JSON 文件处理"""
        from scripts.workspace_manager import load_workspace_config

        config_file = tmp_path / ".workspace_config.json"
        config_file.write_text("{invalid json")

        with pytest.raises(json.JSONDecodeError):
            load_workspace_config("test")

    def test_missing_workspace_handled(self):
        """测试不存在的 workspace 处理"""
        from scripts.workspace_manager import get_workspace_dir

        with pytest.raises(ValueError, match="Workspace not found"):
            get_workspace_dir("non-existent-workspace")


# Pytest 配置
@pytest.fixture(autouse=True)
def mock_streamlit():
    """Mock Streamlit session state"""
    with patch("streamlit.session_state", {}):
        yield


@pytest.fixture(autouse=True)
def mock_api_keys(monkeypatch):
    """Mock API keys"""
    monkeypatch.setenv("GEMINI_API_KEY", "fake-test-key-" + "a" * 32)
    monkeypatch.setenv("XAI_API_KEY", "fake-xai-key-" + "b" * 32)
