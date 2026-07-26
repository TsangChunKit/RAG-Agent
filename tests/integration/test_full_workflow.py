"""完整工作流集成测试。

测试范围：
1. Workspace 管理（创建/切换/列表）
2. 文档入库流程（parse → chunk → embed → ingest）
3. 摘要生成
4. 图谱构建（session graph + merge）
5. 问答检索流程
6. UI 关键路径

测试策略：
- 数据隔离由 tests/conftest.py 的 autouse `isolate_data_root` 负责（钉住 WORKSPACES_ROOT
  这个 import-time 常量），测试内不要再自己 patch PRIVATE_DIR——那是无效的。
- LLM 一律 patch **调用方 namespace 里的名字**（scripts.summarize.ask_llm 等）。
  patch 源模块 `scripts.llm.ask_llm` 拦不住 `from scripts.llm import ask_llm` 的绑定，
  历史上这些测试就是这么在"以为 mock 了"的情况下打真实 API 的。
- embedding 用确定性替身（tests/integration/conftest.py 的 deterministic_embed），
  不加载 2GB 的 BGE-M3，也不引入随机性。
- 文件 I/O 用真的，测完整路径。
"""
import json
import shutil
from dataclasses import asdict
from pathlib import Path

import pytest

from tests.conftest import DEFAULT_TEST_WORKSPACE

pytestmark = pytest.mark.integration


# 真实逐字稿格式：`发言人(HH:MM:SS): 文本`，文件名前 14 位是 YYYYMMDDHHMMSS。
TRANSCRIPT = """20260101093000_咨询记录.txt

Andy(00:00:10): 我最近工作压力很大。
咨询师(00:00:40): 听起来你最近承受了很多压力。能具体说说是什么让你感到压力吗？
Andy(00:01:20): 主要是项目进度太紧了，每天都在赶截止日期。
咨询师(00:02:05): 时间紧迫确实容易让人感到焦虑。你有没有尝试过一些减压的方法？
"""


def _write_transcript(tmp_path: Path, text: str = TRANSCRIPT) -> Path:
    raw_file = tmp_path / "20260101093000_咨询记录.txt"
    raw_file.write_text(text, encoding="utf-8")
    return raw_file


class TestWorkspaceManagement:
    """Workspace 管理功能测试"""

    def test_create_workspace(self):
        """测试创建新 workspace"""
        from scripts.workspace_manager import WORKSPACES_ROOT, create_workspace

        ws_path = create_workspace(
            name="test-workspace",
            display_name="测试 Workspace",
            domain="generic",
            graph_schema_mode="generic",
        )

        # create_workspace() 返回的是 Path（workspace 根目录），不是 workspace id
        assert ws_path == WORKSPACES_ROOT / "test-workspace"

        # 验证目录结构
        assert ws_path.exists()
        assert (ws_path / ".workspace_config.json").exists()
        assert (ws_path / "data").exists()
        assert (ws_path / "data" / "raw").exists()
        assert (ws_path / "db").exists()

        # 验证配置内容
        config = json.loads((ws_path / ".workspace_config.json").read_text(encoding="utf-8"))
        assert config["name"] == "test-workspace"
        assert config["domain"] == "generic"
        assert config["graph_schema"]["mode"] == "generic"

        # 同名再建应该被拒绝（不静默覆盖已有数据）
        with pytest.raises(ValueError, match="already exists"):
            create_workspace("test-workspace", "重复", "generic")

    def test_list_workspaces(self, empty_workspaces_root):
        """测试列出所有 workspaces"""
        from scripts.workspace_manager import create_workspace, list_workspaces

        # empty_workspaces_root 清掉了 conftest 预置的 default-ws，这样"一共几个"才可断言
        create_workspace("ws1", "Workspace 1", "generic", "generic")
        create_workspace("ws2", "Workspace 2", "counseling", "predefined", "counseling.json")

        workspaces = list_workspaces()

        assert len(workspaces) == 2
        assert workspaces[0]["name"] == "ws1"
        assert workspaces[1]["name"] == "ws2"
        # 返回项只有 name / display_name / created_at 三个字段（domain 要另外读 config）
        assert workspaces[0]["display_name"] == "Workspace 1"
        assert "domain" not in workspaces[0]

    def test_get_current_workspace(self, empty_workspaces_root, monkeypatch):
        """测试获取当前 workspace"""
        from scripts.workspace_manager import get_current_workspace

        # 情况 1：环境变量指定（优先级仅次于 Streamlit session_state）
        monkeypatch.setenv("CURRENT_WORKSPACE", "test-ws")
        assert get_current_workspace() == "test-ws"

        # 情况 2：无 workspaces 目录、也无旧路径数据 → 兜底 _legacy
        monkeypatch.delenv("CURRENT_WORKSPACE", raising=False)
        shutil.rmtree(empty_workspaces_root)
        assert get_current_workspace() == "_legacy"


class TestIngestWorkflow:
    """文档入库工作流测试"""

    def test_parse_transcript(self, tmp_path):
        """测试解析逐字稿"""
        from scripts.parse import parse_transcript

        raw_file = _write_transcript(tmp_path)

        # parse_transcript() 吃 Path（内部用 path.name / path.read_text）
        result = parse_transcript(raw_file)

        assert result.session_date == "2026-01-01"
        assert result.file_datetime == "20260101093000"
        # 4 条发言（ParsedSession 是发言列表，不是 user/assistant 轮次对）
        assert len(result.utterances) == 4
        assert result.utterances[0].speaker == "Andy"
        assert result.utterances[0].timestamp == "00:00:10"
        assert result.utterances[0].text == "我最近工作压力很大。"
        # 首行的标题行（文件名）不应被当成发言
        assert all("20260101093000" not in u.text for u in result.utterances)

    def test_chunk_session(self, tmp_path):
        """测试分块"""
        from scripts.chunk import chunk_session
        from scripts.parse import parse_transcript

        long_text = TRANSCRIPT + "".join(
            f"Andy(00:{i // 60 + 3:02d}:{i % 60:02d}): 测试内容第 {i} 段，重复叙述以便触发分块。\n"
            for i in range(60)
        )
        raw_file = _write_transcript(tmp_path, long_text)

        session = parse_transcript(raw_file)
        chunks = chunk_session(session, workspace_id=None)

        assert len(chunks) > 1  # 内容够长，必须真的切成多块
        for chunk in chunks:
            assert chunk.text
            assert chunk.source_file == raw_file.name
            assert chunk.session_date == "2026-01-01"
            assert chunk.speakers  # 发言人列表（逗号分隔、去重）
        assert [c.chunk_index for c in chunks] == list(range(len(chunks)))

    def test_ingest_chunks(self, tmp_path, deterministic_embed):
        """测试向量化和入库（真 LanceDB，确定性假向量）"""
        from scripts.chunk import chunk_session
        from scripts.ingest import ingest
        from scripts.parse import parse_transcript

        session = parse_transcript(_write_transcript(tmp_path))
        chunks = chunk_session(session)
        # ingest() 的签名是 (chunks, mode, workspace_id)，chunks 是 list[dict]；
        # 没有 rebuild= 参数——全量重建用 mode="overwrite"（默认）。
        table = ingest([asdict(c) for c in chunks], mode="overwrite")

        assert table.count_rows() == len(chunks)
        df = table.to_pandas()
        assert set(df["source_file"]) == {"20260101093000_咨询记录.txt"}
        assert len(df.iloc[0]["vector"]) == 1024

        # 追加模式应该叠加而不是覆盖
        table = ingest([asdict(c) for c in chunks], mode="append")
        assert table.count_rows() == len(chunks) * 2


class TestSummarization:
    """摘要生成测试"""

    def test_summarize_session(self, tmp_path, monkeypatch, fake_resp):
        """测试单次会话摘要"""
        from scripts import summarize
        from scripts.parse import parse_transcript

        # patch 的是 summarize 模块里绑定的 ask_llm，不是 scripts.llm.ask_llm
        calls = []

        def fake_ask_llm(contents, **kwargs):
            calls.append((contents, kwargs))
            return fake_resp(json.dumps({
                "topics": ["工作压力", "时间管理"],
                "emotional_tone": "焦虑但愿意求助",
                "key_events": ["项目进度落后"],
                "psychological_themes": ["完美主义倾向"],
                "decisions_or_actions": ["尝试减压方法"],
                "quotes_worth_remembering": ["我最近工作压力很大。"],
            }, ensure_ascii=False))

        monkeypatch.setattr(summarize, "ask_llm", fake_ask_llm)

        session = parse_transcript(_write_transcript(tmp_path))
        # summarize_session() 只吃 session 一个参数（无 source_file / force / workspace_id）
        result = summarize.summarize_session(session)

        assert len(calls) == 1
        # 元数据来自文件名解析，不问 LLM（避免它编日期）
        assert result["session_date"] == "2026-01-01"
        assert result["source_file"] == "20260101093000_咨询记录.txt"
        assert result["file_datetime"] == "20260101093000"
        # LLM 提炼的字段被合并进来
        assert result["topics"] == ["工作压力", "时间管理"]
        assert result["emotional_tone"] == "焦虑但愿意求助"
        # 用的是 summary profile（便宜模型）
        assert calls[0][1]["profile"] == "summary"


class TestGraphBuilding:
    """图谱构建测试"""

    def test_build_session_fragment(self, tmp_path, monkeypatch, fake_resp):
        """测试单次会话子图抽取"""
        from scripts import session_graph
        from scripts.parse import parse_transcript

        calls = []

        def fake_ask_llm(contents, **kwargs):
            calls.append(kwargs)
            return fake_resp(json.dumps({
                "nodes": [
                    {"id": "schema:缺陷", "type": "schema", "label": "缺陷/羞耻",
                     "domain": "断裂与羞耻", "description": "觉得自己不够好"},
                    {"id": "coping:过度补偿", "type": "coping", "label": "过度工作",
                     "domain": "", "description": "用加班证明自己"},
                ],
                "edges": [
                    {"source": "schema:缺陷", "target": "coping:过度补偿",
                     "relation_type": "derives", "relation": "图式派生出过度工作"},
                    # 指向未定义节点的坏边，应被丢弃
                    {"source": "schema:缺陷", "target": "emotion:不存在",
                     "relation_type": "evokes", "relation": "坏边"},
                    # 自指边，也应被丢弃
                    {"source": "schema:缺陷", "target": "schema:缺陷",
                     "relation_type": "co_occurs", "relation": "自指"},
                ],
            }, ensure_ascii=False))

        monkeypatch.setattr(session_graph, "ask_llm", fake_ask_llm)

        session = parse_transcript(_write_transcript(tmp_path))
        # build_session_fragment() 的签名是 (session, force, workspace_id)——没有 source_file
        fragment = session_graph.build_session_fragment(session, force=True)

        assert len(calls) == 1
        assert fragment["source_file"] == "20260101093000_咨询记录.txt"
        assert fragment["session_date"] == "2026-01-01"
        assert len(fragment["nodes"]) == 2
        # 日期被注入每个节点（归并时用来合并 related_dates）
        assert all(n["related_dates"] == ["2026-01-01"] for n in fragment["nodes"])
        # 坏边 + 自指边被丢掉，只剩 1 条
        assert len(fragment["edges"]) == 1
        assert fragment["edges"][0]["evidence_dates"] == ["2026-01-01"]

        # 缓存生效：第二次不再调 LLM，内容一致
        cached = session_graph.build_session_fragment(session)
        assert len(calls) == 1
        assert cached == fragment
        assert session_graph.fragment_path(session.source_file).exists()

    def test_resolve_graph(self, deterministic_embed):
        """测试图谱归并"""
        from scripts.graph_utils import resolve_graph

        # resolve_graph() 吃的是 **fragments 列表**（每份 {nodes, edges, session_date}），
        # 不是 (raw_nodes, raw_edges) 两个参数。
        fragments = [
            {
                "session_date": "2026-01-01",
                "source_file": "a.txt",
                "nodes": [
                    {"id": "schema:A", "type": "schema", "label": "概念A",
                     "domain": "", "description": "描述A"},
                    {"id": "schema:B", "type": "schema", "label": "概念B",
                     "domain": "", "description": "描述B"},
                ],
                "edges": [
                    {"source": "schema:A", "target": "schema:B",
                     "relation_type": "co_occurs", "relation": "证据1"},
                ],
            },
            {
                "session_date": "2026-01-02",
                "source_file": "b.txt",
                "nodes": [
                    # 和第一份的「概念A」同名同类型 → 确定性向量下余弦 1.0，必然归并
                    {"id": "s1", "type": "schema", "label": "概念A",
                     "domain": "", "description": "描述A 更长更详细的版本"},
                    {"id": "s2", "type": "schema", "label": "概念B",
                     "domain": "", "description": "描述B"},
                ],
                "edges": [
                    {"source": "s1", "target": "s2",
                     "relation_type": "co_occurs", "relation": "证据2 更长的关系描述"},
                ],
            },
        ]

        result = resolve_graph(fragments)

        # 4 个原始节点归并成 2 个规范节点
        assert len(result["nodes"]) == 2
        by_label = {n["label"]: n for n in result["nodes"]}
        assert set(by_label) == {"概念A", "概念B"}
        # 归并后 related_dates 是两份的并集，description 取最长的那份
        assert by_label["概念A"]["related_dates"] == ["2026-01-01", "2026-01-02"]
        assert by_label["概念A"]["description"] == "描述A 更长更详细的版本"
        assert by_label["概念A"]["source"] == "therapy"
        # 规范 id 格式是 "type:序号"
        assert by_label["概念A"]["id"].startswith("schema:")

        # 两条边重映射到同一对规范 id → 去重成 1 条，evidence_dates 合并，relation 取最长
        assert len(result["edges"]) == 1
        edge = result["edges"][0]
        assert edge["relation_type"] == "co_occurs"
        assert edge["evidence_dates"] == ["2026-01-01", "2026-01-02"]
        assert edge["relation"] == "证据2 更长的关系描述"


class TestRetrievalQA:
    """问答检索测试"""

    def test_answer_with_empty_db(self, monkeypatch, fake_resp):
        """测试向量库为空时的问答"""
        from scripts import ask

        monkeypatch.setattr(ask, "retrieve", lambda question, k=None: [])
        monkeypatch.setattr(
            ask, "ask_llm",
            lambda contents, **kwargs: fake_resp("我理解你的问题，但目前没有相关记录。"),
        )

        result = ask.answer("测试问题", k=5)

        assert result["answer"] == "我理解你的问题，但目前没有相关记录。"
        # 优雅降级：没有片段不是错误，prompt 里明说"未检索到"，让 LLM 不要硬编
        assert result["sources"] == []
        assert "（未检索到相关历史咨询片段。）" in result["api_content"]
        assert result["token_usage"]["input"] == 100
        assert result["token_usage"]["total"] == 150

    def test_answer_with_retrieval(self, monkeypatch, fake_resp):
        """测试正常检索问答"""
        from scripts import ask

        retrieve_calls = []
        window = {
            "source_file": "20260101093000_咨询记录.txt",
            "session_date": "2026-01-01",
            "start_ts": "00:00:10",
            "end_ts": "00:02:05",
            "chunk_index_range": (0, 1),
            "text": "检索到的相关内容",
            "rank": 0,
            "score": 0.85,
            "below_threshold": False,
        }

        def fake_retrieve(question, k=None):
            retrieve_calls.append((question, k))
            return [dict(window)]

        monkeypatch.setattr(ask, "retrieve", fake_retrieve)
        monkeypatch.setattr(
            ask, "ask_llm",
            lambda contents, **kwargs: fake_resp("基于检索到的内容，我的回答是……"),
        )

        result = ask.answer("测试问题", k=5)

        assert retrieve_calls[0] == ("测试问题", 5)
        assert result["answer"] == "基于检索到的内容，我的回答是……"
        # 检索片段进了 prompt，并带上精排分数（让 LLM 自己判断权重）
        assert "检索到的相关内容" in result["api_content"]
        assert "相关性 0.850" in result["api_content"]
        # sources 供 UI 的「引用来源」展示，含分数与是否低于阈值
        assert len(result["sources"]) == 1
        assert result["sources"][0]["source_file"] == "20260101093000_咨询记录.txt"
        assert result["sources"][0]["score"] == 0.85
        assert result["sources"][0]["below_threshold"] is False
        assert result["sources"][0]["full_transcript"] is False
        # 精简版历史（省 token）只含片段 + 问题
        assert "测试问题" in result["history_content"]
        assert result["compression_info"] is None


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
            page_file = Path(__file__).resolve().parents[2] / "pages" / "1_🕸️_心智地图.py"
            assert page_file.exists()

            # 读取检查基本语法
            content = page_file.read_text(encoding="utf-8")
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
    def test_full_ingest_to_query(self, tmp_path, monkeypatch, fake_resp,
                                  deterministic_embed, no_reranker):
        """测试完整流程：文档入库 → 摘要 → 问答（不含建图，太慢）"""
        from scripts import ask, summarize
        from scripts.chunk import chunk_session
        from scripts.ingest import ingest
        from scripts.parse import parse_transcript

        # 1. Parse
        session = parse_transcript(_write_transcript(tmp_path))
        assert len(session.utterances) == 4

        # 2. Chunk
        chunks = chunk_session(session, workspace_id=None)
        assert len(chunks) > 0

        # 3. Ingest（真的建表 + 建 FTS 索引）
        table = ingest([asdict(c) for c in chunks])
        assert table.count_rows() == len(chunks)

        # 4. 摘要落盘到当前 workspace（问答时会被 _load_session_summary 读到）
        monkeypatch.setattr(summarize, "ask_llm", lambda contents, **kw: fake_resp(json.dumps({
            "topics": ["工作压力"], "emotional_tone": "焦虑",
            "key_events": ["项目赶进度"], "psychological_themes": ["自我要求过高"],
            "decisions_or_actions": ["尝试减压"], "quotes_worth_remembering": ["压力很大"],
        }, ensure_ascii=False)))
        summary = summarize.summarize_session(session)
        summary_file = summarize.summary_path(session.source_file)
        summary_file.parent.mkdir(parents=True, exist_ok=True)
        summary_file.write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")

        # 5. 问答：走真的 retrieve（真 LanceDB + FTS），只把 LLM 换成替身
        monkeypatch.setattr(ask, "ask_llm",
                            lambda contents, **kw: fake_resp("你提到过项目进度带来的压力。"))
        result = ask.answer("工作压力", k=3)

        assert result["answer"] == "你提到过项目进度带来的压力。"
        assert len(result["sources"]) > 0
        assert result["sources"][0]["session_date"] == "2026-01-01"
        assert "压力" in result["api_content"]

    @pytest.mark.slow
    def test_workspace_isolation(self, tmp_path, deterministic_embed):
        """测试 workspace 隔离性（连向量库都不该串）"""
        from scripts.chunk import chunk_session
        from scripts.ingest import ingest
        from scripts.parse import parse_transcript
        from scripts.workspace_manager import create_workspace, get_workspace_dir

        ws1_dir = create_workspace("ws1", "WS1", "generic", "generic")
        ws2_dir = create_workspace("ws2", "WS2", "counseling", "predefined", "counseling.json")

        # 验证隔离
        assert get_workspace_dir("ws1") == ws1_dir
        assert get_workspace_dir("ws2") == ws2_dir
        assert ws1_dir != ws2_dir
        assert (ws1_dir / "data").exists()
        assert (ws2_dir / "data").exists()

        # 写入文件到 ws1，不应影响 ws2
        (ws1_dir / "data" / "test.txt").write_text("ws1 data", encoding="utf-8")
        assert not (ws2_dir / "data" / "test.txt").exists()

        # 向量库也是 workspace 独立的：入到 ws1 的数据，ws2 的 db 目录里不该出现
        session = parse_transcript(_write_transcript(tmp_path))
        chunks = chunk_session(session, workspace_id="ws1")
        ingest([asdict(c) for c in chunks], workspace_id="ws1")
        assert (ws1_dir / "db" / "sessions.lance").exists()
        assert not (ws2_dir / "db" / "sessions.lance").exists()
        # 预置的 default-ws 也不该被写到
        from scripts.workspace_manager import WORKSPACES_ROOT
        assert not (WORKSPACES_ROOT / DEFAULT_TEST_WORKSPACE / "db" / "sessions.lance").exists()
