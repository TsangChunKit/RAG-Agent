"""边缘情况和错误处理测试。

测试异常场景：
1. 空输入处理
2. 大文件处理
3. 特殊字符处理
4. 并发访问
5. 资源限制
6. 降级行为

约定同 test_full_workflow.py：数据隔离交给 conftest 的 autouse fixture，LLM 一律 patch
调用方 namespace 里的名字（scripts.ask.ask_llm），embedding 用确定性替身。
"""
import json
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.integration


def _write(tmp_path: Path, name: str, body: str) -> Path:
    """写一份文件名合法（前 14 位 = YYYYMMDDHHMMSS）的逐字稿。"""
    f = tmp_path / name
    f.write_text(body, encoding="utf-8")
    return f


class TestEmptyInputHandling:
    """空输入处理"""

    def test_parse_empty_file(self, tmp_path):
        """测试解析空文件"""
        from scripts.parse import parse_transcript

        empty_file = tmp_path / "empty.txt"
        empty_file.write_text("", encoding="utf-8")

        with pytest.raises(Exception):  # 应该抛出合适的异常
            parse_transcript(empty_file)

    def test_answer_with_empty_question(self, monkeypatch, fake_resp):
        """测试空问题"""
        from scripts import ask

        monkeypatch.setattr(ask, "retrieve", lambda question, k=None: [])
        monkeypatch.setattr(ask, "ask_llm",
                            lambda contents, **kw: fake_resp("请提供一个具体的问题。"))

        result = ask.answer("", k=5)

        # 应该仍然返回结果，不崩溃
        assert "answer" in result
        assert result["answer"] == "请提供一个具体的问题。"

    def test_retrieve_from_empty_db(self):
        """测试从空向量库检索"""
        from scripts.ask import retrieve

        # 真实的"空向量库" = 这个 workspace 里连表都还没建（新建 workspace 的初始状态）。
        # 现状是 lancedb 直接抛 ValueError("Table 'sessions' was not found")，而不是返回 []。
        # 这是**有意的失败可见性**：静默返回空会让 UI 显示"没找到相关记录"，把"库是空的"
        # 伪装成"检索不到"。UI 侧的防线在 app.py——它先看 list_indexed_records()，
        # 库空时直接提示"向量库还是空的，把逐字稿放进 data/raw/"，压根不会走到 retrieve()。
        with pytest.raises(ValueError, match="was not found"):
            retrieve("测试问题", k=5)


class TestLargeInputHandling:
    """大文件处理"""

    def test_parse_large_transcript(self, tmp_path):
        """测试解析大文件"""
        from scripts.parse import parse_transcript

        lines = []
        for i in range(5000):
            ts = f"{i // 3600:02d}:{i // 60 % 60:02d}:{i % 60:02d}"
            speaker = "Andy" if i % 2 == 0 else "咨询师"
            lines.append(f"{speaker}({ts}): 第{i}句内容。")
        large_file = _write(tmp_path, "20260101120000_large.txt", "\n".join(lines))

        result = parse_transcript(large_file)

        # ParsedSession 是发言（utterance）列表，没有 turns 字段
        assert len(result.utterances) == 5000
        assert result.utterances[0].speaker == "Andy"
        assert result.utterances[-1].text == "第4999句内容。"

    def test_chunk_large_session(self, tmp_path):
        """测试分块大文件"""
        from scripts.chunk import chunk_session
        from scripts.parse import parse_transcript

        # 100 条发言，每条 1000 字。超过 chunk_size * 1.5（默认 400 × 1.5 = 600）的发言会先被
        # _split_long_utterance() 按句子边界拆成多个 unit，所以块数应该**多于**发言条数。
        lines = []
        for i in range(100):
            ts = f"{i // 60:02d}:{i % 60:02d}:00"
            lines.append(f"Andy({ts}): " + "回答内容。" * 200)
        large_file = _write(tmp_path, "20260101120000_large.txt", "\n".join(lines))

        session = parse_transcript(large_file)
        chunks = chunk_session(session, workspace_id=None)

        # 应该生成多个 chunks（比发言条数还多——超长发言被二次切分）
        assert len(chunks) > 100
        # 块长度受 chunk_size 约束（允许单条不可再分的句子撑破，但不该整篇不切）
        assert max(len(c.raw_text) for c in chunks) < 5000

    @pytest.mark.slow
    def test_answer_with_large_history(self, monkeypatch, fake_resp):
        """测试处理长对话历史"""
        from scripts import ask

        # 生成 100 轮对话历史
        history = []
        for i in range(100):
            history.append({"role": "user", "content": f"问题{i}"})
            history.append({"role": "assistant", "content": f"回答{i}" * 100})

        monkeypatch.setattr(ask, "retrieve", lambda question, k=None: [])
        monkeypatch.setattr(ask, "ask_llm", lambda contents, **kw: fake_resp("基于历史的回答"))

        result = ask.answer("新问题", history=history, k=5)

        # 应该能处理（这个体量还没到 450K 阈值，所以不该触发压缩）
        assert result["answer"] == "基于历史的回答"
        assert result["compression_info"] is None


class TestSpecialCharacterHandling:
    """特殊字符处理"""

    def test_parse_with_special_chars(self, tmp_path):
        """测试解析包含特殊字符的文件"""
        from scripts.parse import parse_transcript

        content = """20260101120000_special.txt

Andy(00:00:01): 这是一个"引号"测试
咨询师(00:00:02): 回答包含 <标签> 和 & 符号
Andy(00:00:03): 还有 emoji 😀 和 中文标点：、。！？
咨询师(00:00:04): SQL 注入测试 ' OR 1=1 --
"""
        special_file = _write(tmp_path, "20260101120000_special.txt", content)

        result = parse_transcript(special_file)

        assert len(result.utterances) == 4
        assert '"引号"' in result.utterances[0].text
        assert "<标签>" in result.utterances[1].text
        assert "😀" in result.utterances[2].text
        # 特殊字符原样保留在解析层，清理只发生在检索查询侧（见 sanitize）
        assert "' OR 1=1 --" in result.utterances[3].text

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
        graph_file.write_text(json.dumps(graph_data, ensure_ascii=False), encoding="utf-8")

        # 读取不应该出错
        loaded = json.loads(graph_file.read_text(encoding="utf-8"))
        assert loaded["nodes"][0]["label"] == "节点<标签>"


class TestConcurrentAccess:
    """并发访问测试"""

    @pytest.mark.slow
    def test_concurrent_workspace_reads(self):
        """测试并发读取 workspace 配置"""
        import threading

        from scripts.workspace_manager import create_workspace, load_workspace_config

        # create_workspace() 返回 Path，workspace id 是它的目录名
        ws_dir = create_workspace("test-ws", "Test", "generic", "generic")
        ws_id = ws_dir.name

        results = []
        errors = []

        def load_config():
            try:
                config = load_workspace_config(ws_id)
                results.append(config)
            except Exception as e:  # pragma: no cover - 只在真出问题时进
                errors.append(e)

        # 10 个线程并发读取
        threads = [threading.Thread(target=load_config) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 应该全部成功且读到同一份配置（纯读取，无锁也安全）
        assert len(results) == 10
        assert not errors
        assert all(c["display_name"] == "Test" for c in results)

    @pytest.mark.slow
    @patch("scripts.chat_store.CHAT_SESSIONS_DIR")
    def test_concurrent_session_writes(self, mock_dir, tmp_path):
        """测试并发写入会话（可能冲突）"""
        import threading

        from scripts.chat_store import new_session_id, save_session

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
            except Exception as e:  # pragma: no cover
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

        # 一条 20 万字的超长发言（模拟整段视频文本被贴进逐字稿）
        huge_file = _write(tmp_path, "20260101120000_huge.txt",
                           "Andy(00:00:01): " + "回答内容。" * 40000)

        session = parse_transcript(huge_file)
        chunks = chunk_session(session, workspace_id=None)

        # 超长发言会按句子边界二次切分，结果应该是"很多但有界"
        assert len(chunks) > 0
        assert len(chunks) < 10000  # 合理上限
        # 每块都带上下文前缀，不会退化成裸文本
        assert all(c.text.startswith("[") for c in chunks)

    @pytest.mark.slow
    def test_max_context_length(self, monkeypatch, fake_resp):
        """测试最大 context 长度限制"""
        from scripts import ask

        # 历史条目的字段形状必须和 app.py 存进 session_state 的一致（app.py:698-712）：
        # user 的 content 是**裸问题**（短），history_content 是"检索片段 + 问题"（长）。
        # 压缩 user 轮 = 丢掉 history_content 改用 content。写成"content 就是那 2 万字"的话，
        # 压缩标记会被打满 100 条却一个字符都省不下来（before == after），测不到任何东西。
        # 30 轮（answer() 的 max_turns=60 是按"轮"算的，30 轮不会被截断），每轮 user 4 万字 /
        # assistant 2 万字：user 全压完仍有 60 万字符 > 450K，才能逼出阶段 2 的 LLM 压缩。
        history = []
        for i in range(30):
            history.append({
                "role": "user",
                "content": f"问题{i}",
                "history_content": f"【检索到的相关历史咨询片段】\n{'片段内容。' * 4000}\n问题{i}",
            })
            history.append({"role": "assistant", "content": f"回答{i}：" + "详细展开。" * 4000})

        monkeypatch.setattr(ask, "retrieve", lambda question, k=None: [])
        monkeypatch.setattr(ask, "ask_llm", lambda contents, **kw: fake_resp("回答"))

        result = ask.answer("新问题", history=history, max_context=450_000)

        # 应该自动压缩到阈值以内，不崩溃
        assert result["answer"] == "回答"
        info = result["compression_info"]
        assert info is not None and info["triggered"] is True
        # 阶段 1：user 轮全部降级成裸问题（免费）
        assert info["compressed_turns"] == 30
        # 阶段 2：仍超限 → 长回答交给 LLM 压缩（这里被 fake_resp 替掉）
        assert info["llm_compressed_turns"] > 0
        assert info["after"] < info["before"]
        assert info["after"] <= 450_000


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

    def test_llm_error_handling(self, monkeypatch):
        """测试 LLM 调用失败时的处理"""
        from scripts import ask

        def boom(*args, **kwargs):
            raise RuntimeError("API Error")

        monkeypatch.setattr(ask, "retrieve", lambda question, k=None: [])
        monkeypatch.setattr(ask, "ask_llm", boom)

        # answer() 不吞 LLM 异常——让调用方（app.py）显示真实错误，
        # 而不是把 API 故障伪装成"我没有相关记录"。
        with pytest.raises(RuntimeError, match="API Error"):
            ask.answer("测试问题", k=5)

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
        graph_file.write_text("{invalid json", encoding="utf-8")

        with pytest.raises(json.JSONDecodeError):
            json.loads(graph_file.read_text(encoding="utf-8"))

    def test_embedding_failure_handling(self, monkeypatch, tmp_path):
        """测试 embedding 失败处理"""
        from scripts import ingest as ingest_module
        from scripts.chunk import Chunk

        def boom(texts):
            raise RuntimeError("Embedding API Error")

        # patch 的是 ingest 模块里绑定的 embed（`from scripts.embedder import embed`），
        # patch scripts.embedder.embed 拦不到它。
        monkeypatch.setattr(ingest_module, "embed", boom)

        chunks = [asdict(Chunk(
            id="test.txt::chunk0000",
            session_date="2026-01-01",
            source_file="test.txt",
            chunk_index=0,
            speakers="Andy",
            start_ts="00:00:00",
            end_ts="00:01:00",
            raw_text="测试",
            text="[2026-01-01 测试]\n测试",
        ))]

        # embedding 挂了就该向上抛，不能写一张半成品表进去
        # （静默吞掉会留下一个"有表但缺行"的索引，比直接失败危险得多）
        with pytest.raises(RuntimeError, match="Embedding API Error"):
            ingest_module.ingest(chunks, mode="overwrite")

        from config import DB_DIR, LANCEDB_TABLE_NAME
        import lancedb
        assert LANCEDB_TABLE_NAME not in lancedb.connect(str(DB_DIR())).table_names()


class TestDataValidation:
    """数据验证测试"""

    def test_invalid_date_format(self, tmp_path):
        """测试无效日期格式"""
        from scripts.parse import parse_transcript

        invalid_file = _write(tmp_path, "invalid_date.txt", "Andy(00:00:01): test")

        with pytest.raises(ValueError):
            # 文件名没有 14 位日期时间前缀，应该报错
            parse_transcript(invalid_file)

    def test_invalid_workspace_config(self, tmp_path):
        """测试无效的 workspace 配置"""
        from scripts.workspace_manager import load_workspace_config

        config_file = tmp_path / ".workspace_config.json"
        config_file.write_text(json.dumps({"name": "test"}), encoding="utf-8")  # 缺少必需字段

        with patch("scripts.workspace_manager.get_workspace_dir", return_value=tmp_path):
            config = load_workspace_config("test")

            # 应该能加载，缺失字段使用默认值
            assert config["name"] == "test"
            assert config["display_name"] == "默认工作空间"  # 来自 DEFAULT_WORKSPACE_CONFIG
            assert "chunk_prefix_template" in config

    def test_invalid_graph_schema(self, deterministic_embed):
        """测试无效的 graph schema"""
        from scripts.graph_utils import resolve_graph

        # resolve_graph() 只吃一个 fragments 列表（第二个位置参数是 threshold，不是 edges）
        result = resolve_graph([])

        assert result["nodes"] == []
        assert result["edges"] == []

        # fragment 存在但节点为空，同样优雅返回空图（不 KeyError）
        result = resolve_graph([{"session_date": "2026-01-01", "nodes": [], "edges": []}])
        assert result == {"nodes": [], "edges": []}

    def test_negative_chunk_size(self):
        """测试负数 chunk size"""
        from scripts import index_settings

        # index_settings 没有 update()，写入口只有 save()/reset()，而且**刻意不做校验**——
        # 它只是个 JSON 读写层，校验放在唯一的写入来源（Streamlit 的 number_input）上：
        assert not hasattr(index_settings, "update")

        index_settings.save(
            retrieval=index_settings.retrieval_params(),
            chunking={"chunk_size": -1, "chunk_overlap": 0},
            embedding=index_settings.embedding_params(),
            fts=index_settings.fts_params(),
            reranker=index_settings.reranker_params(),
            graph_evidence=index_settings.graph_evidence_params(),
        )
        assert index_settings.chunking_params()["chunk_size"] == -1

        # 真正的防线：UI 的下界是 100（> 0），所以负数根本进不来。
        # 这行断言是为了在有人改动这个 widget 时炸掉，而不是让负数悄悄变成可选项。
        app_src = (Path(__file__).resolve().parents[2] / "app.py").read_text(encoding="utf-8")
        assert 'st.number_input("分块大小 chunk_size（字符）", 100, 2000' in app_src

        # 而且即使真被写成负数，分块也只是退化成"每条发言一块"，不会死循环/崩溃（降级而非崩溃）
        from scripts.chunk import chunk_session
        from scripts.parse import ParsedSession, Utterance

        session = ParsedSession(
            source_file="20260101120000_x.txt",
            session_date="2026-01-01",
            file_datetime="20260101120000",
            utterances=[Utterance("Andy", "00:00:01", "第一句。", 1),
                        Utterance("Andy", "00:00:02", "第二句。", 2)],
        )
        chunks = chunk_session(session)
        assert len(chunks) >= 2


class TestBackwardCompatibility:
    """向后兼容性测试"""

    def test_legacy_path_compatibility(self, empty_workspaces_root, isolate_data_root):
        """测试旧路径兼容性"""
        import shutil

        from scripts.workspace_manager import get_current_workspace, get_workspace_dir

        # 模拟旧结构：没有 workspaces/ 目录，只有 private.nosync/data/
        shutil.rmtree(empty_workspaces_root)
        (isolate_data_root / "data").mkdir(parents=True, exist_ok=True)

        # 应该返回 _legacy
        assert get_current_workspace() == "_legacy"

        # 应该能获取目录（指向 private.nosync 根目录）
        assert get_workspace_dir("_legacy") == isolate_data_root

    def test_old_settings_format(self, tmp_path):
        """测试旧设置格式兼容"""
        from scripts import settings

        old_settings = tmp_path / "gemini_settings.json"
        old_settings.write_text(
            json.dumps({"model": "old-model", "temperature": 0.7}),  # 旧格式
            encoding="utf-8",
        )

        with patch("scripts.settings.GEMINI_SETTINGS_PATH", old_settings):
            # 应该能处理旧格式（顶层未知字段被忽略，全部回退 config.py 默认值）
            result = settings.load_for_ui()

            assert "dialogue" in result
            assert "summary" in result
            assert result["dialogue"]["model"] != "old-model"
