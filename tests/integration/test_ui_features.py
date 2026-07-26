"""UI 功能集成测试。

测试 Streamlit 应用的关键用户路径：
1. 设置弹窗（LLM 配置、System Instruction）
2. Workspace 切换
3. 对话历史管理
4. 图谱可视化
5. 索引记录查看

约定同 test_full_workflow.py：数据隔离交给 tests/conftest.py 的 autouse fixture（它已经把
WORKSPACES_ROOT / INDEX_SETTINGS_PATH / GEMINI_SETTINGS_PATH 全钉进 tmp_path），
测试内不要再自己 patch PRIVATE_DIR——那个名字是 import-time 求值的，patch 它没有任何作用。
"""
import json
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.integration


class TestSettingsDialogs:
    """设置弹窗功能测试"""

    def test_load_gemini_settings(self, tmp_path):
        """测试加载 Gemini 设置

        注意 provider：设置文件里残留的 "gemini" 已停用（见 settings.DISABLED_PROVIDERS），
        load_for_ui() 会回退到 DEFAULT_PROVIDER，但 dialogue/summary 参数照读不误。
        """
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

            assert result["provider"] == settings.DEFAULT_PROVIDER  # gemini 停用 → 回退
            assert "gemini" in result["disabled_providers"]
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
                provider="hermes",
            )

            # 验证设置文件
            saved = json.loads(settings_file.read_text())
            assert saved["dialogue"]["model"] == "gemini-3.0"
            assert saved["dialogue"]["thinking_level"] == "medium"
            assert saved["provider"] == "hermes"

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

    def test_workspace_selector_state(self):
        """测试 workspace 选择器状态"""
        from scripts.workspace_manager import (
            create_workspace,
            get_current_workspace,
            set_current_workspace,
        )

        create_workspace("ws1", "WS1", "generic", "generic")
        create_workspace("ws2", "WS2", "counseling", "predefined", "counseling.json")

        # set_current_workspace() 写的是 st.session_state（进程级！所以 conftest 在每个测试
        # 前后都会清空它，否则这里选中的 ws2 会泄漏给后面所有测试）
        set_current_workspace("ws2")
        assert get_current_workspace() == "ws2"

        # 切换回 ws1
        set_current_workspace("ws1")
        assert get_current_workspace() == "ws1"

    def test_workspace_status_display(self):
        """测试 workspace 状态显示"""
        from scripts.workspace_manager import (
            create_workspace,
            get_workspace_dir,
            load_workspace_config,
        )

        # create_workspace() 返回 Path，workspace id 是目录名
        ws_dir = create_workspace("test-ws", "测试 WS", "generic", "generic")
        ws_id = ws_dir.name

        # 获取配置
        config = load_workspace_config(ws_id)
        assert config["display_name"] == "测试 WS"
        assert config["domain"] == "generic"

        # 获取目录
        assert get_workspace_dir(ws_id) == ws_dir
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

        # 短问题：原样返回（≤ 24 字，不加省略号）
        short = "这是一个测试问题"
        assert make_title(short) == short

        # 长问题：截到 24 字 + 单字符省略号 "…"（不是三个点 "..."，
        # 侧栏宽度有限，中文标点省 2 个字符的位置）
        long = "这是一个非常长的问题，" * 10
        title = make_title(long)
        assert len(title) == 25
        assert title == long[:24] + "…"
        assert title.endswith("…")

        # 换行会被压成空格（侧栏是单行按钮，换行会把布局撑坏）
        assert make_title("第一行\n第二行") == "第一行 第二行"


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

        # merge_graphs() 的签名是 (therapy_graph, chat_graph) 两个位置参数，
        # 不是 merge_graphs([g1, g2])——它合并的是"真实咨询图谱"和"AI 对话记忆图谱"
        # 这两个**固定角色**，所以来源标签（source）能自动打上。
        therapy = {
            "nodes": [{"id": "n1", "label": "节点1", "type": "concept"}],
            "edges": [],
        }
        chat = {
            "nodes": [{"id": "chat:n2", "label": "节点2", "type": "concept"}],
            "edges": [{"source": "chat:n2", "target": "n1", "relation": "relates_to"}],
        }

        merged = merge_graphs(therapy, chat)

        assert len(merged["nodes"]) == 2
        assert len(merged["edges"]) == 1
        # 来源被打上，UI 靠它区分颜色
        by_id = {n["id"]: n for n in merged["nodes"]}
        assert by_id["n1"]["source"] == "therapy"
        assert by_id["chat:n2"]["source"] == "chat"
        # 合并后重算中心性（UI 拿它定节点大小）。写的是两个具体指标而不是一个
        # 笼统的 "centrality"，而且是在**合并后的图**上算的——跨图的 relates_to 边会改变
        # 连接度，只看半张图算出来的值不代表全局重要程度。
        assert all({"degree_centrality", "betweenness_centrality"} <= set(n)
                   for n in merged["nodes"])
        assert by_id["n1"]["degree_centrality"] > 0  # 被 chat 那条边连上了

        # 优雅降级：只有一张图时直接返回那张，两张都没有时返回 None
        # （UI 在只建过咨询图谱、还没有对话记忆图谱时就是这个状态）
        assert merge_graphs(therapy, None) is therapy
        assert merge_graphs(None, chat) is chat
        assert merge_graphs(None, None) is None

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

    def test_list_indexed_records_empty(self):
        """测试空向量库"""
        from scripts.index_records import list_indexed_records

        # 隔离后的 workspace 里 chunks.jsonl 压根不存在 —— 这正是新建 workspace 的真实状态，
        # 也是 app.py 用来提示"向量库还是空的"的判断依据。
        assert list_indexed_records() == []

        # 存在但为空文件（入库过又清空）也该是空列表，不是崩溃
        from scripts.chunk import CHUNKS_JSONL_PATH

        chunks_path = CHUNKS_JSONL_PATH()
        chunks_path.parent.mkdir(parents=True, exist_ok=True)
        chunks_path.write_text("", encoding="utf-8")
        assert list_indexed_records() == []

    def test_list_indexed_records_with_data(self):
        """测试有数据的向量库"""
        from scripts.chunk import CHUNKS_JSONL_PATH
        from scripts.index_records import list_indexed_records
        from scripts.summarize import SUMMARIES_DIR

        # 直接写进隔离后的真实路径。以前这里 patch 的是 scripts.summarize.SUMMARIES_DIR，
        # 但 has_summary 是 index_records 自己 import 的那个名字算的，patch 邻居模块拦不到。
        chunks_data = [
            {"source_file": "2026-01-01.txt", "session_date": "2026-01-01",
             "chunk_index": 0, "text": "content"},
            {"source_file": "2026-01-01.txt", "session_date": "2026-01-01",
             "chunk_index": 1, "text": "content"},
            {"source_file": "2026-01-02.txt", "session_date": "2026-01-02",
             "chunk_index": 0, "text": "content"},
        ]
        chunks_path = CHUNKS_JSONL_PATH()
        chunks_path.parent.mkdir(parents=True, exist_ok=True)
        chunks_path.write_text("\n".join(json.dumps(c) for c in chunks_data), encoding="utf-8")

        summaries_dir = SUMMARIES_DIR()
        summaries_dir.mkdir(parents=True, exist_ok=True)
        (summaries_dir / "2026-01-01.json").write_text("{}", encoding="utf-8")

        records = list_indexed_records()

        # 按 (session_date, source_file) **倒序**：新的在前，所以 01-02 排第一
        assert len(records) == 2
        assert records[0]["source_file"] == "2026-01-02.txt"
        assert records[0]["n_chunks"] == 1
        assert records[0]["has_summary"] is False
        assert records[1]["source_file"] == "2026-01-01.txt"
        assert records[1]["n_chunks"] == 2
        assert records[1]["has_summary"] is True

    def test_load_change_log(self):
        """测试加载变更记录"""
        from config import INDEX_CHANGELOG_PATH
        from scripts.index_records import load_change_log

        log_entries = [
            {"ts": "2026-01-01T10:00:00", "action": "added", "source_file": "test1.txt"},
            {"ts": "2026-01-02T11:00:00", "action": "added", "source_file": "test2.txt"},
            {"ts": "2026-01-03T12:00:00", "action": "skipped", "source_file": "test3.txt"},
        ]
        changelog_path = INDEX_CHANGELOG_PATH()
        changelog_path.parent.mkdir(parents=True, exist_ok=True)
        changelog_path.write_text("\n".join(json.dumps(e) for e in log_entries), encoding="utf-8")

        log = load_change_log(limit=2)

        # 应该返回最新的 2 条（倒序）
        assert len(log) == 2
        assert log[0]["source_file"] == "test3.txt"
        assert log[1]["source_file"] == "test2.txt"

    def test_change_log_survives_corrupt_line(self):
        """测试变更记录里有坏行时不影响其他行"""
        from config import INDEX_CHANGELOG_PATH
        from scripts.index_records import load_change_log

        changelog_path = INDEX_CHANGELOG_PATH()
        changelog_path.parent.mkdir(parents=True, exist_ok=True)
        changelog_path.write_text(
            json.dumps({"ts": "2026-01-01T10:00:00", "action": "added", "source_file": "ok.txt"})
            + "\n{截断的坏行\n"
            + json.dumps({"ts": "2026-01-02T10:00:00", "action": "added", "source_file": "ok2.txt"}),
            encoding="utf-8",
        )

        # append-only 的审计日志被写坏一行（进程被杀）不该让整个「已索引的记录」页面打不开
        log = load_change_log()
        assert [e["source_file"] for e in log] == ["ok2.txt", "ok.txt"]


class TestIndexSettings:
    """索引设置测试"""

    def test_load_index_settings(self):
        """测试加载索引设置"""
        from scripts import index_settings

        # index_settings 没有 load()，读取入口是六个分组函数 + load_for_ui()。
        # 分组是刻意的：改分块参数只影响新入库的数据，改 reranker 参数下一次问答就生效，
        # 混成一个扁平 dict 就看不出哪些改动需要重建索引。
        # 路径已由 conftest 钉进 tmp_path，这里直接走真实读写通路。
        index_settings.save(
            retrieval={"top_k": 10, "window_expand": 1},
            chunking={"chunk_size": 400, "chunk_overlap": 50},
            embedding=index_settings.embedding_params(),
            fts=index_settings.fts_params(),
            reranker={**index_settings.reranker_params(), "use_reranker": True, "final_top_k": 5},
            graph_evidence=index_settings.graph_evidence_params(),
        )

        ui = index_settings.load_for_ui()
        assert set(ui) == {"retrieval", "chunking", "embedding", "fts", "reranker", "graph_evidence"}
        assert ui["chunking"]["chunk_size"] == 400
        assert ui["retrieval"]["top_k"] == 10
        assert ui["reranker"]["use_reranker"] is True
        assert ui["reranker"]["final_top_k"] == 5

        # 缺失字段用 config.py 常量顶上（不是留 None 让下游炸）
        index_settings.INDEX_SETTINGS_PATH.write_text(
            json.dumps({"chunking": {"chunk_size": 800}}), encoding="utf-8")
        from config import CHUNK_OVERLAP_CHARS

        assert index_settings.chunking_params() == {
            "chunk_size": 800, "chunk_overlap": CHUNK_OVERLAP_CHARS}

    def test_save_index_settings(self):
        """测试保存索引设置"""
        from scripts import index_settings

        # save() 要求六组全给（没有 update() 这种部分更新入口——UI 是整个表单一起提交的，
        # 部分更新会让"某一组悄悄回到默认值"这种 bug 无声无息）
        index_settings.save(
            retrieval={"top_k": 15, "window_expand": 2},
            chunking={"chunk_size": 500, "chunk_overlap": 80},
            embedding=index_settings.embedding_params(),
            fts=index_settings.fts_params(),
            reranker={**index_settings.reranker_params(), "use_reranker": False},
            graph_evidence=index_settings.graph_evidence_params(),
        )

        saved = json.loads(index_settings.INDEX_SETTINGS_PATH.read_text(encoding="utf-8"))
        assert saved["chunking"]["chunk_size"] == 500
        assert saved["retrieval"]["top_k"] == 15
        assert saved["reranker"]["use_reranker"] is False
        # 读回来要一致（写进去的是嵌套结构，不是扁平 key）
        assert index_settings.retrieval_params()["top_k"] == 15
        assert index_settings.reranker_params()["use_reranker"] is False

        # reset() = 删文件，全部回退 config.py 默认值（用户改坏了能一键恢复）
        index_settings.reset()
        assert not index_settings.INDEX_SETTINGS_PATH.exists()
        from config import RETRIEVAL_TOP_K

        assert index_settings.retrieval_params()["top_k"] == RETRIEVAL_TOP_K


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

    def test_corrupt_json_handled(self):
        """测试损坏的 JSON 文件处理"""
        from scripts.workspace_manager import (
            DEFAULT_WORKSPACE_CONFIG,
            create_workspace,
            load_workspace_config,
        )

        ws_dir = create_workspace("broken-ws", "坏配置", "generic", "generic")
        (ws_dir / ".workspace_config.json").write_text("{invalid json", encoding="utf-8")

        # 不抛异常，降级到默认配置。这是刻意的：配置文件被编辑器写坏时，整个 app 不该打不开，
        # 用户至少还能进 UI 把配置改回来（失败可见性由那句 print 警告负责）。
        config = load_workspace_config("broken-ws")
        assert config == DEFAULT_WORKSPACE_CONFIG
        assert config["display_name"] == DEFAULT_WORKSPACE_CONFIG["display_name"]

    def test_missing_workspace_handled(self):
        """测试不存在的 workspace 处理"""
        from scripts.workspace_manager import get_workspace_dir

        with pytest.raises(ValueError, match="Workspace not found"):
            get_workspace_dir("non-existent-workspace")


# Pytest 配置
#
# 这里原本有个 autouse 的 mock_streamlit fixture 做 patch("streamlit.session_state", {})，
# 已删除：它会让 set_current_workspace() 写进一个用完就丢的字典（`{}` 没有属性赋值语义，
# `st.session_state.current_workspace = x` 直接 AttributeError），把 workspace 切换测成假的。
# session_state 的清理已经由 tests/conftest.py 的 isolate_data_root 统一负责。


@pytest.fixture(autouse=True)
def mock_api_keys(monkeypatch):
    """Mock API keys（XAI_API_KEY 是 conftest 的 mock_env_vars 没覆盖的那个）"""
    monkeypatch.setenv("GEMINI_API_KEY", "fake-test-key-" + "a" * 32)
    monkeypatch.setenv("XAI_API_KEY", "fake-xai-key-" + "b" * 32)
