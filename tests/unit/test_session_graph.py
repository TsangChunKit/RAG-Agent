"""测试 session_graph.py 单会话图谱抽取功能。

测试目标：从 21% (16/76) 提升到 60%+

重点测试：
1. build_session_fragment() - 单会话子图抽取（核心）
2. _build_session_graph_schema() - LLM Schema 生成
3. _extract() - JSON 解析和验证
4. fragment_path() - 缓存路径管理
5. ensure_fragments() - 批量抽取
6. 证据日期提取和合并
7. 不同 schema 模式（counseling/generic/sutras/solution_arch）
8. JSON 解析错误处理
9. 空结果处理
"""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from scripts.parse import ParsedSession, Utterance
from scripts.session_graph import (
    _build_session_graph_schema,
    _extract,
    build_session_fragment,
    ensure_fragments,
    fragment_path,
)


class TestBuildSessionGraphSchema:
    """测试 LLM Schema 动态生成。"""

    def test_schema_basic_structure(self):
        """测试基本 schema 结构。"""
        node_types = {
            "concept": {"label": "概念", "layer": 0, "has_domain": False},
            "entity": {"label": "实体", "layer": 1, "has_domain": False},
        }
        relation_types = {
            "relates_to": "关联",
            "contains": "包含",
        }

        schema = _build_session_graph_schema(node_types, relation_types)

        # 验证顶层结构
        assert "type" in schema
        assert schema["type"] == "object"
        assert "properties" in schema
        assert "nodes" in schema["properties"]
        assert "edges" in schema["properties"]
        assert schema["required"] == ["nodes", "edges"]

    def test_schema_node_types_enum(self):
        """测试节点类型 enum 正确生成。"""
        node_types = {
            "need": {"label": "需要", "layer": 0, "has_domain": False},
            "schema": {"label": "图式", "layer": 1, "has_domain": True},
        }
        relation_types = {"unmet": "未满足"}

        schema = _build_session_graph_schema(node_types, relation_types)

        # 验证节点类型 enum
        node_schema = schema["properties"]["nodes"]["items"]
        assert "type" in node_schema["properties"]
        assert node_schema["properties"]["type"]["enum"] == ["need", "schema"]

    def test_schema_relation_types_enum(self):
        """测试关系类型 enum（排除 relates_to）。"""
        node_types = {"concept": {"label": "概念", "layer": 0, "has_domain": False}}
        relation_types = {
            "relates_to": "关联",  # 应被排除
            "contains": "包含",
            "produces": "产生",
        }

        schema = _build_session_graph_schema(node_types, relation_types)

        # 验证关系类型 enum（不含 relates_to）
        edge_schema = schema["properties"]["edges"]["items"]
        assert "relation_type" in edge_schema["properties"]
        relation_enum = edge_schema["properties"]["relation_type"]["enum"]
        assert "relates_to" not in relation_enum
        assert "contains" in relation_enum
        assert "produces" in relation_enum

    def test_schema_domain_no_domain_types(self):
        """测试无 domain 字段的节点类型。"""
        node_types = {
            "concept": {"label": "概念", "layer": 0, "has_domain": False},
            "entity": {"label": "实体", "layer": 1, "has_domain": False},
        }
        relation_types = {"relates_to": "关联"}

        schema = _build_session_graph_schema(node_types, relation_types)

        # 验证 domain 描述
        node_schema = schema["properties"]["nodes"]["items"]
        domain_desc = node_schema["properties"]["domain"]["description"]
        assert "留空字符串" in domain_desc

    def test_schema_domain_with_domain_types(self):
        """测试有 domain 字段的节点类型（心理学 schema）。"""
        node_types = {
            "need": {"label": "需要", "layer": 0, "has_domain": False},
            "schema": {"label": "图式", "layer": 1, "has_domain": True},
            "belief": {"label": "信念", "layer": 2, "has_domain": True},
        }
        relation_types = {"unmet": "未满足"}
        schema_domains = ["亲密关系", "成就", "界限", "自主", "自尊"]

        schema = _build_session_graph_schema(node_types, relation_types, schema_domains)

        # 验证 domain 描述（提到需要 domain 的类型）
        node_schema = schema["properties"]["nodes"]["items"]
        domain_desc = node_schema["properties"]["domain"]["description"]
        assert "schema" in domain_desc or "belief" in domain_desc
        assert "填对应领域" in domain_desc

    def test_schema_required_fields(self):
        """测试节点和边的必需字段。"""
        node_types = {"concept": {"label": "概念", "layer": 0, "has_domain": False}}
        relation_types = {"relates_to": "关联"}

        schema = _build_session_graph_schema(node_types, relation_types)

        # 验证节点必需字段
        node_schema = schema["properties"]["nodes"]["items"]
        assert set(node_schema["required"]) == {"id", "type", "label", "domain", "description"}

        # 验证边必需字段
        edge_schema = schema["properties"]["edges"]["items"]
        assert set(edge_schema["required"]) == {"source", "target", "relation_type", "relation"}

    def test_schema_field_descriptions(self):
        """测试字段描述完整性。"""
        node_types = {"concept": {"label": "概念", "layer": 0, "has_domain": False}}
        relation_types = {"contains": "包含"}

        schema = _build_session_graph_schema(node_types, relation_types)

        node_schema = schema["properties"]["nodes"]["items"]
        # 验证各字段都有描述
        assert "description" in node_schema["properties"]["id"]
        assert "description" in node_schema["properties"]["label"]
        assert "description" in node_schema["properties"]["domain"]
        assert "description" in node_schema["properties"]["description"]

        edge_schema = schema["properties"]["edges"]["items"]
        assert "description" in edge_schema["properties"]["relation"]


class TestFragmentPath:
    """测试 fragment 缓存路径管理。"""

    def test_fragment_path_default_workspace(self):
        """测试默认 workspace 的路径。"""
        with patch("scripts.session_graph.GRAPH_FRAGMENTS_DIR") as mock_dir:
            mock_dir.return_value = Path("/test/fragments")

            path = fragment_path("session1.txt")

            assert path == Path("/test/fragments/session1.json")
            mock_dir.assert_called_once_with(None)

    def test_fragment_path_custom_workspace(self):
        """测试自定义 workspace 的路径。"""
        with patch("scripts.session_graph.GRAPH_FRAGMENTS_DIR") as mock_dir:
            mock_dir.return_value = Path("/test/workspaces/my-ws/fragments")

            path = fragment_path("session1.txt", workspace_id="my-ws")

            assert path == Path("/test/workspaces/my-ws/fragments/session1.json")
            mock_dir.assert_called_once_with("my-ws")

    def test_fragment_path_removes_extension(self):
        """测试移除原文件扩展名。"""
        with patch("scripts.session_graph.GRAPH_FRAGMENTS_DIR") as mock_dir:
            mock_dir.return_value = Path("/test/fragments")

            path = fragment_path("20240101120000_session.txt")

            assert path.name == "20240101120000_session.json"


class TestExtract:
    """测试单会话图谱抽取核心逻辑。"""

    @pytest.fixture
    def sample_session(self):
        """创建示例会话数据。"""
        return ParsedSession(
            source_file="20240101120000_test.txt",
            session_date="2024-01-01",
            file_datetime="20240101120000",
            utterances=[
                Utterance(speaker="来访者", timestamp="00:00:15", text="最近工作压力很大", line_no=0),
                Utterance(speaker="咨询师", timestamp="00:01:30", text="能具体说说吗？", line_no=1),
                Utterance(speaker="来访者", timestamp="00:02:45", text="项目截止日期快到了", line_no=2),
            ],
        )

    @pytest.fixture
    def mock_schema_loader(self):
        """Mock graph_schema_loader 函数。"""
        schema = {
            "node_types": {
                "need": {"label": "需要", "layer": 0, "has_domain": False},
                "schema": {"label": "图式", "layer": 1, "has_domain": True},
            },
            "relation_types": {
                "unmet": "未满足",
                "relates_to": "关联",
            },
            "schema_domains": ["亲密关系", "成就"],
            "system_instruction_template": "你是图谱构建专家。",
        }

        with patch("scripts.session_graph.load_schema") as mock_load, \
             patch("scripts.session_graph.get_node_types") as mock_nodes, \
             patch("scripts.session_graph.get_relation_types") as mock_rels, \
             patch("scripts.session_graph.get_schema_domains") as mock_domains, \
             patch("scripts.session_graph.get_system_instruction") as mock_si:

            mock_load.return_value = schema
            mock_nodes.return_value = schema["node_types"]
            mock_rels.return_value = schema["relation_types"]
            mock_domains.return_value = schema["schema_domains"]
            mock_si.return_value = schema["system_instruction_template"]

            yield {
                "load_schema": mock_load,
                "get_node_types": mock_nodes,
                "get_relation_types": mock_rels,
                "get_schema_domains": mock_domains,
                "get_system_instruction": mock_si,
            }

    def test_extract_basic_success(self, sample_session, mock_schema_loader):
        """测试基本抽取成功情况。"""
        # Mock LLM 返回有效 JSON
        llm_response = {
            "nodes": [
                {
                    "id": "need:1",
                    "type": "need",
                    "label": "成就需要",
                    "domain": "",
                    "description": "需要完成工作任务",
                },
                {
                    "id": "schema:1",
                    "type": "schema",
                    "label": "完美主义图式",
                    "domain": "成就",
                    "description": "必须完美完成每个项目",
                },
            ],
            "edges": [
                {
                    "source": "need:1",
                    "target": "schema:1",
                    "relation_type": "unmet",
                    "relation": "长期未满足",
                }
            ],
        }

        mock_llm = MagicMock()
        mock_llm.text = json.dumps(llm_response, ensure_ascii=False)

        with patch("scripts.session_graph.ask_llm", return_value=mock_llm):
            result = _extract(sample_session)

        # 验证基本结构
        assert result["source_file"] == "20240101120000_test.txt"
        assert result["session_date"] == "2024-01-01"
        assert len(result["nodes"]) == 2
        assert len(result["edges"]) == 1

        # 验证节点注入日期
        for node in result["nodes"]:
            assert "related_dates" in node
            assert node["related_dates"] == ["2024-01-01"]

        # 验证边注入日期
        for edge in result["edges"]:
            assert "evidence_dates" in edge
            assert edge["evidence_dates"] == ["2024-01-01"]

    def test_extract_removes_invalid_edges(self, sample_session, mock_schema_loader):
        """测试移除引用不存在节点的边和自指边。"""
        # Mock LLM 返回包含无效边的 JSON
        llm_response = {
            "nodes": [
                {"id": "need:1", "type": "need", "label": "需要1", "domain": "", "description": "测试1"},
                {"id": "need:2", "type": "need", "label": "需要2", "domain": "", "description": "测试2"},
            ],
            "edges": [
                # 有效边
                {"source": "need:1", "target": "need:2", "relation_type": "unmet", "relation": "有效"},
                # 自指边（应被移除）
                {"source": "need:1", "target": "need:1", "relation_type": "unmet", "relation": "自指"},
                # 无效边：引用不存在的节点（应被移除）
                {"source": "need:1", "target": "nonexistent", "relation_type": "unmet", "relation": "无效"},
                {"source": "nonexistent", "target": "need:1", "relation_type": "unmet", "relation": "无效"},
            ],
        }

        mock_llm = MagicMock()
        mock_llm.text = json.dumps(llm_response, ensure_ascii=False)

        with patch("scripts.session_graph.ask_llm", return_value=mock_llm):
            result = _extract(sample_session)

        # 应只保留有效的非自指边
        assert len(result["edges"]) == 1
        assert result["edges"][0]["source"] == "need:1"
        assert result["edges"][0]["target"] == "need:2"

    def test_extract_removes_self_loop_edges(self, sample_session, mock_schema_loader):
        """测试移除自指边（source == target）。"""
        llm_response = {
            "nodes": [
                {"id": "need:1", "type": "need", "label": "需要", "domain": "", "description": "测试"},
                {"id": "need:2", "type": "need", "label": "另一个需要", "domain": "", "description": "测试"},
            ],
            "edges": [
                # 自指边（应被移除）
                {"source": "need:1", "target": "need:1", "relation_type": "unmet", "relation": "自指"},
                # 正常边（应保留）
                {"source": "need:1", "target": "need:2", "relation_type": "unmet", "relation": "正常"},
            ],
        }

        mock_llm = MagicMock()
        mock_llm.text = json.dumps(llm_response, ensure_ascii=False)

        with patch("scripts.session_graph.ask_llm", return_value=mock_llm):
            result = _extract(sample_session)

        # 应只保留正常边
        assert len(result["edges"]) == 1
        edge = result["edges"][0]
        assert edge["source"] == "need:1"
        assert edge["target"] == "need:2"

    def test_extract_empty_nodes(self, sample_session, mock_schema_loader):
        """测试 LLM 返回空节点列表。"""
        llm_response = {"nodes": [], "edges": []}

        mock_llm = MagicMock()
        mock_llm.text = json.dumps(llm_response, ensure_ascii=False)

        with patch("scripts.session_graph.ask_llm", return_value=mock_llm):
            result = _extract(sample_session)

        assert result["nodes"] == []
        assert result["edges"] == []

    def test_extract_uses_schema_system_instruction(self, sample_session, mock_schema_loader):
        """测试使用 schema 中的 system instruction。"""
        mock_llm = MagicMock()
        mock_llm.text = json.dumps({"nodes": [], "edges": []}, ensure_ascii=False)

        with patch("scripts.session_graph.ask_llm", return_value=mock_llm) as mock_ask:
            _extract(sample_session)

            # 验证使用了 schema 中的 system instruction
            call_kwargs = mock_ask.call_args.kwargs
            assert call_kwargs["system_instruction"] == "你是图谱构建专家。"

    def test_extract_fallback_to_default_prompt(self, sample_session):
        """测试降级到默认 prompt（当 schema 不提供时）。"""
        # Mock schema_loader 返回 None system_instruction
        with patch("scripts.session_graph.load_schema") as mock_load, \
             patch("scripts.session_graph.get_node_types") as mock_nodes, \
             patch("scripts.session_graph.get_relation_types") as mock_rels, \
             patch("scripts.session_graph.get_schema_domains") as mock_domains, \
             patch("scripts.session_graph.get_system_instruction") as mock_si:

            mock_load.return_value = {}
            mock_nodes.return_value = {"concept": {"label": "概念", "layer": 0, "has_domain": False}}
            mock_rels.return_value = {"relates_to": "关联"}
            mock_domains.return_value = None
            mock_si.return_value = None  # 无 system instruction

            mock_llm = MagicMock()
            mock_llm.text = json.dumps({"nodes": [], "edges": []}, ensure_ascii=False)

            with patch("scripts.session_graph.ask_llm", return_value=mock_llm) as mock_ask:
                _extract(sample_session)

                # 验证使用了降级 prompt
                call_kwargs = mock_ask.call_args.kwargs
                assert "知识图谱构建专家" in call_kwargs["system_instruction"]

    def test_extract_passes_correct_llm_params(self, sample_session, mock_schema_loader):
        """测试传递正确的 LLM 参数。"""
        mock_llm = MagicMock()
        mock_llm.text = json.dumps({"nodes": [], "edges": []}, ensure_ascii=False)

        with patch("scripts.session_graph.ask_llm", return_value=mock_llm) as mock_ask, \
             patch("scripts.session_graph.summary_max_tokens", return_value=4096) as mock_tokens:

            _extract(sample_session)

            # 验证调用参数
            call_kwargs = mock_ask.call_args.kwargs
            assert call_kwargs["profile"] == "summary"
            assert "response_schema" in call_kwargs
            assert call_kwargs["max_output_tokens"] == 4096
            mock_tokens.assert_called_once_with("therapy_graph")

    def test_extract_json_parse_error(self, sample_session, mock_schema_loader):
        """测试 JSON 解析错误处理。"""
        # Mock LLM 返回无效 JSON
        mock_llm = MagicMock()
        mock_llm.text = "这不是有效的 JSON"

        with patch("scripts.session_graph.ask_llm", return_value=mock_llm):
            with pytest.raises(json.JSONDecodeError):
                _extract(sample_session)

    def test_extract_with_workspace_id(self, sample_session):
        """测试指定 workspace_id 参数传递。"""
        with patch("scripts.session_graph.load_schema") as mock_load, \
             patch("scripts.session_graph.get_node_types") as mock_nodes, \
             patch("scripts.session_graph.get_relation_types") as mock_rels, \
             patch("scripts.session_graph.get_schema_domains") as mock_domains, \
             patch("scripts.session_graph.get_system_instruction") as mock_si:

            mock_load.return_value = {}
            mock_nodes.return_value = {"concept": {"label": "概念", "layer": 0, "has_domain": False}}
            mock_rels.return_value = {"relates_to": "关联"}
            mock_domains.return_value = None
            mock_si.return_value = "测试 prompt"

            mock_llm = MagicMock()
            mock_llm.text = json.dumps({"nodes": [], "edges": []}, ensure_ascii=False)

            with patch("scripts.session_graph.ask_llm", return_value=mock_llm):
                _extract(sample_session, workspace_id="test-workspace")

            # 验证传递了 workspace_id
            mock_load.assert_called_once_with("test-workspace")


class TestBuildSessionFragment:
    """测试单会话 fragment 构建（含缓存）。"""

    @pytest.fixture
    def sample_session(self):
        """创建示例会话。"""
        return ParsedSession(
            source_file="session1.txt",
            session_date="2024-01-01",
            file_datetime="20240101120000",
            utterances=[
                Utterance(speaker="来访者", timestamp="00:00:15", text="测试内容", line_no=0),
            ],
        )

    def test_build_from_cache(self, sample_session, tmp_path):
        """测试从缓存读取 fragment。"""
        # 准备缓存文件
        fragment_data = {
            "source_file": "session1.txt",
            "session_date": "2024-01-01",
            "nodes": [{"id": "n1", "label": "缓存节点"}],
            "edges": [],
        }

        cache_file = tmp_path / "session1.json"
        cache_file.write_text(json.dumps(fragment_data, ensure_ascii=False), encoding="utf-8")

        with patch("scripts.session_graph.fragment_path", return_value=cache_file), \
             patch("scripts.session_graph._extract") as mock_extract:

            result = build_session_fragment(sample_session, force=False)

        # 验证从缓存读取
        assert result == fragment_data
        mock_extract.assert_not_called()

    def test_build_force_reextract(self, sample_session, tmp_path):
        """测试 force=True 强制重新抽取。"""
        # 准备缓存文件（应被忽略）
        cache_file = tmp_path / "session1.json"
        cache_file.write_text(json.dumps({"nodes": [], "edges": []}, ensure_ascii=False), encoding="utf-8")

        new_fragment = {
            "source_file": "session1.txt",
            "session_date": "2024-01-01",
            "nodes": [{"id": "n1", "label": "新节点"}],
            "edges": [],
        }

        with patch("scripts.session_graph.fragment_path", return_value=cache_file), \
             patch("scripts.session_graph._extract", return_value=new_fragment), \
             patch("scripts.session_graph.GRAPH_FRAGMENTS_DIR", return_value=tmp_path):

            result = build_session_fragment(sample_session, force=True)

        # 验证强制重新抽取
        assert result == new_fragment
        assert result["nodes"][0]["label"] == "新节点"

    def test_build_writes_cache(self, sample_session, tmp_path):
        """测试写入缓存文件。"""
        cache_file = tmp_path / "session1.json"
        fragment = {
            "source_file": "session1.txt",
            "session_date": "2024-01-01",
            "nodes": [{"id": "n1", "label": "测试"}],
            "edges": [],
        }

        with patch("scripts.session_graph.fragment_path", return_value=cache_file), \
             patch("scripts.session_graph._extract", return_value=fragment), \
             patch("scripts.session_graph.GRAPH_FRAGMENTS_DIR", return_value=tmp_path):

            build_session_fragment(sample_session, force=False)

        # 验证缓存文件被写入
        assert cache_file.exists()
        cached_data = json.loads(cache_file.read_text(encoding="utf-8"))
        assert cached_data == fragment

    def test_build_creates_fragments_dir(self, sample_session, tmp_path):
        """测试自动创建 fragments 目录。"""
        fragments_dir = tmp_path / "fragments"
        cache_file = fragments_dir / "session1.json"

        fragment = {
            "source_file": "session1.txt",
            "session_date": "2024-01-01",
            "nodes": [],
            "edges": [],
        }

        with patch("scripts.session_graph.fragment_path", return_value=cache_file), \
             patch("scripts.session_graph._extract", return_value=fragment), \
             patch("scripts.session_graph.GRAPH_FRAGMENTS_DIR", return_value=fragments_dir):

            build_session_fragment(sample_session, force=False)

        # 验证目录被创建
        assert fragments_dir.exists()
        assert cache_file.exists()

    def test_build_with_workspace_id(self, sample_session, tmp_path):
        """测试指定 workspace_id。"""
        cache_file = tmp_path / "session1.json"
        fragment = {"source_file": "session1.txt", "session_date": "2024-01-01", "nodes": [], "edges": []}

        with patch("scripts.session_graph.fragment_path", return_value=cache_file) as mock_path, \
             patch("scripts.session_graph._extract", return_value=fragment) as mock_extract, \
             patch("scripts.session_graph.GRAPH_FRAGMENTS_DIR", return_value=tmp_path):

            build_session_fragment(sample_session, force=False, workspace_id="test-ws")

        # 验证传递了 workspace_id
        mock_path.assert_called_once_with("session1.txt", "test-ws")
        mock_extract.assert_called_once_with(sample_session, "test-ws")


class TestEnsureFragments:
    """测试批量 fragment 抽取。"""

    def test_ensure_fragments_empty_raw_files(self):
        """测试没有原始文件的情况。"""
        with patch("scripts.session_graph.iter_raw_files", return_value=[]):
            fragments = ensure_fragments()

        assert fragments == []

    def test_ensure_fragments_skips_empty_sessions(self, tmp_path):
        """测试跳过空会话（无 utterances）。"""
        # 创建空文件
        empty_file = tmp_path / "20240101120000_empty.txt"
        empty_file.write_text("")

        with patch("scripts.session_graph.iter_raw_files", return_value=[empty_file]), \
             patch("scripts.session_graph.parse_transcript") as mock_parse, \
             patch("scripts.session_graph.build_session_fragment") as mock_build:

            # Mock parse 返回空会话
            mock_parse.return_value = ParsedSession(
                source_file="20240101120000_empty.txt",
                session_date="2024-01-01",
                file_datetime="20240101120000",
                utterances=[],
            )

            fragments = ensure_fragments()

        # 验证跳过空会话
        assert fragments == []
        mock_build.assert_not_called()

    def test_ensure_fragments_processes_multiple_files(self, tmp_path):
        """测试处理多个文件。"""
        file1 = tmp_path / "20240101120000_s1.txt"
        file2 = tmp_path / "20240102120000_s2.txt"

        with patch("scripts.session_graph.iter_raw_files", return_value=[file1, file2]), \
             patch("scripts.session_graph.parse_transcript") as mock_parse, \
             patch("scripts.session_graph.build_session_fragment") as mock_build:

            # Mock parse 返回有效会话
            mock_parse.side_effect = [
                ParsedSession(
                    source_file="20240101120000_s1.txt",
                    session_date="2024-01-01",
                    file_datetime="20240101120000",
                    utterances=[Utterance(speaker="来访者", timestamp="00:00:15", text="测试1", line_no=0)],
                ),
                ParsedSession(
                    source_file="20240102120000_s2.txt",
                    session_date="2024-01-02",
                    file_datetime="20240102120000",
                    utterances=[Utterance(speaker="来访者", timestamp="00:00:15", text="测试2", line_no=0)],
                ),
            ]

            # Mock build 返回 fragment
            mock_build.side_effect = [
                {"source_file": "20240101120000_s1.txt", "nodes": [], "edges": []},
                {"source_file": "20240102120000_s2.txt", "nodes": [], "edges": []},
            ]

            fragments = ensure_fragments()

        # 验证处理了两个文件
        assert len(fragments) == 2
        assert mock_parse.call_count == 2
        assert mock_build.call_count == 2

    def test_ensure_fragments_force_reextract(self, tmp_path):
        """测试 force=True 传递给所有 fragment。"""
        file1 = tmp_path / "20240101120000_s1.txt"

        with patch("scripts.session_graph.iter_raw_files", return_value=[file1]), \
             patch("scripts.session_graph.parse_transcript") as mock_parse, \
             patch("scripts.session_graph.build_session_fragment") as mock_build:

            mock_parse.return_value = ParsedSession(
                source_file="20240101120000_s1.txt",
                session_date="2024-01-01",
                file_datetime="20240101120000",
                utterances=[Utterance(speaker="来访者", timestamp="00:00:15", text="测试", line_no=0)],
            )
            mock_build.return_value = {"nodes": [], "edges": []}

            ensure_fragments(force=True)

        # 验证传递了 force=True
        mock_build.assert_called_once()
        call_kwargs = mock_build.call_args.kwargs
        assert call_kwargs["force"] is True

    def test_ensure_fragments_with_workspace_id(self, tmp_path):
        """测试指定 workspace_id。"""
        file1 = tmp_path / "20240101120000_s1.txt"

        with patch("scripts.session_graph.iter_raw_files", return_value=[file1]) as mock_iter, \
             patch("scripts.session_graph.parse_transcript") as mock_parse, \
             patch("scripts.session_graph.build_session_fragment") as mock_build:

            mock_parse.return_value = ParsedSession(
                source_file="20240101120000_s1.txt",
                session_date="2024-01-01",
                file_datetime="20240101120000",
                utterances=[Utterance(speaker="来访者", timestamp="00:00:15", text="测试", line_no=0)],
            )
            mock_build.return_value = {"nodes": [], "edges": []}

            ensure_fragments(force=False, workspace_id="test-ws")

        # 验证传递了 workspace_id
        mock_iter.assert_called_once_with("test-ws")
        call_kwargs = mock_build.call_args.kwargs
        assert call_kwargs["workspace_id"] == "test-ws"

    def test_ensure_fragments_shows_progress(self, tmp_path):
        """测试显示进度条（tqdm）。"""
        file1 = tmp_path / "20240101120000_s1.txt"

        with patch("scripts.session_graph.iter_raw_files", return_value=[file1]), \
             patch("scripts.session_graph.parse_transcript") as mock_parse, \
             patch("scripts.session_graph.build_session_fragment") as mock_build, \
             patch("scripts.session_graph.tqdm") as mock_tqdm:

            mock_parse.return_value = ParsedSession(
                source_file="20240101120000_s1.txt",
                session_date="2024-01-01",
                file_datetime="20240101120000",
                utterances=[Utterance(speaker="来访者", timestamp="00:00:15", text="测试", line_no=0)],
            )
            mock_build.return_value = {"nodes": [], "edges": []}
            mock_tqdm.return_value = [file1]  # tqdm 包装后返回原列表

            ensure_fragments()

        # 验证使用了 tqdm
        mock_tqdm.assert_called_once()
        call_kwargs = mock_tqdm.call_args.kwargs
        assert "desc" in call_kwargs
        assert "图谱" in call_kwargs["desc"]


class TestDifferentSchemas:
    """测试不同 workspace schema 模式。"""

    @pytest.fixture
    def sample_session(self):
        """创建示例会话。"""
        return ParsedSession(
            source_file="test.txt",
            session_date="2024-01-01",
            file_datetime="20240101120000",
            utterances=[Utterance(speaker="测试", timestamp="00:00:15", text="测试内容", line_no=0)],
        )

    def test_counseling_schema(self, sample_session):
        """测试心理咨询 schema（10 种节点类型）。"""
        counseling_schema = {
            "node_types": {
                "need": {"label": "需要", "layer": 0, "has_domain": False},
                "person": {"label": "依附对象", "layer": 0, "has_domain": False},
                "schema": {"label": "图式", "layer": 1, "has_domain": True},
                "belief": {"label": "信念", "layer": 2, "has_domain": True},
                "mode": {"label": "模式", "layer": 2, "has_domain": False},
                "coping": {"label": "应对", "layer": 3, "has_domain": False},
                "trigger": {"label": "触发", "layer": 3, "has_domain": False},
                "automatic_thought": {"label": "自动思维", "layer": 4, "has_domain": False},
                "emotion": {"label": "情绪", "layer": 4, "has_domain": False},
                "event": {"label": "事件", "layer": 5, "has_domain": False},
            },
            "relation_types": {"unmet": "未满足", "originates": "起源于"},
            "schema_domains": ["亲密关系", "成就", "界限", "自主", "自尊"],
            "system_instruction_template": "心理咨询专家 prompt",
        }

        with patch("scripts.session_graph.load_schema", return_value=counseling_schema), \
             patch("scripts.session_graph.get_node_types", return_value=counseling_schema["node_types"]), \
             patch("scripts.session_graph.get_relation_types", return_value=counseling_schema["relation_types"]), \
             patch("scripts.session_graph.get_schema_domains", return_value=counseling_schema["schema_domains"]), \
             patch("scripts.session_graph.get_system_instruction", return_value=counseling_schema["system_instruction_template"]):

            mock_llm = MagicMock()
            mock_llm.text = json.dumps({"nodes": [], "edges": []}, ensure_ascii=False)

            with patch("scripts.session_graph.ask_llm", return_value=mock_llm) as mock_ask:
                _extract(sample_session)

                # 验证 schema 生成
                call_kwargs = mock_ask.call_args.kwargs
                response_schema = call_kwargs["response_schema"]

                # 验证节点类型 enum
                node_types_enum = response_schema["properties"]["nodes"]["items"]["properties"]["type"]["enum"]
                assert len(node_types_enum) == 10
                assert "schema" in node_types_enum
                assert "belief" in node_types_enum

    def test_generic_schema(self, sample_session):
        """测试通用 schema（4 种节点类型）。"""
        generic_schema = {
            "node_types": {
                "concept": {"label": "概念", "layer": 0, "has_domain": False},
                "entity": {"label": "实体", "layer": 1, "has_domain": False},
                "event": {"label": "事件", "layer": 2, "has_domain": False},
                "process": {"label": "过程", "layer": 3, "has_domain": False},
            },
            "relation_types": {"relates_to": "关联", "contains": "包含"},
            "system_instruction_template": "通用图谱专家 prompt",
        }

        with patch("scripts.session_graph.load_schema", return_value=generic_schema), \
             patch("scripts.session_graph.get_node_types", return_value=generic_schema["node_types"]), \
             patch("scripts.session_graph.get_relation_types", return_value=generic_schema["relation_types"]), \
             patch("scripts.session_graph.get_schema_domains", return_value=None), \
             patch("scripts.session_graph.get_system_instruction", return_value=generic_schema["system_instruction_template"]):

            mock_llm = MagicMock()
            mock_llm.text = json.dumps({"nodes": [], "edges": []}, ensure_ascii=False)

            with patch("scripts.session_graph.ask_llm", return_value=mock_llm) as mock_ask:
                _extract(sample_session)

                # 验证 schema 生成
                call_kwargs = mock_ask.call_args.kwargs
                response_schema = call_kwargs["response_schema"]

                # 验证节点类型 enum
                node_types_enum = response_schema["properties"]["nodes"]["items"]["properties"]["type"]["enum"]
                assert len(node_types_enum) == 4
                assert "concept" in node_types_enum
                assert "entity" in node_types_enum

    def test_sutras_schema(self, sample_session):
        """测试佛学经文 schema。"""
        sutras_schema = {
            "node_types": {
                "concept": {"label": "概念", "layer": 0, "has_domain": False},
                "person": {"label": "人物", "layer": 1, "has_domain": False},
                "teaching": {"label": "教义", "layer": 2, "has_domain": False},
                "practice": {"label": "修行", "layer": 3, "has_domain": False},
                "text": {"label": "文本", "layer": 4, "has_domain": False},
            },
            "relation_types": {"teaches": "教导", "practices": "实践"},
            "system_instruction_template": "佛学专家 prompt",
        }

        with patch("scripts.session_graph.load_schema", return_value=sutras_schema), \
             patch("scripts.session_graph.get_node_types", return_value=sutras_schema["node_types"]), \
             patch("scripts.session_graph.get_relation_types", return_value=sutras_schema["relation_types"]), \
             patch("scripts.session_graph.get_schema_domains", return_value=None), \
             patch("scripts.session_graph.get_system_instruction", return_value=sutras_schema["system_instruction_template"]):

            mock_llm = MagicMock()
            mock_llm.text = json.dumps({"nodes": [], "edges": []}, ensure_ascii=False)

            with patch("scripts.session_graph.ask_llm", return_value=mock_llm) as mock_ask:
                _extract(sample_session)

                # 验证使用了 sutras schema
                call_kwargs = mock_ask.call_args.kwargs
                assert call_kwargs["system_instruction"] == "佛学专家 prompt"


class TestErrorHandling:
    """测试错误处理和边界情况。"""

    @pytest.fixture
    def sample_session(self):
        """创建示例会话。"""
        return ParsedSession(
            source_file="test.txt",
            session_date="2024-01-01",
            file_datetime="20240101120000",
            utterances=[Utterance(speaker="测试", timestamp="00:00:15", text="测试", line_no=0)],
        )

    def test_llm_api_error(self, sample_session):
        """测试 LLM API 调用失败。"""
        with patch("scripts.session_graph.load_schema"), \
             patch("scripts.session_graph.get_node_types"), \
             patch("scripts.session_graph.get_relation_types"), \
             patch("scripts.session_graph.get_schema_domains"), \
             patch("scripts.session_graph.get_system_instruction"), \
             patch("scripts.session_graph.ask_llm", side_effect=Exception("API Error")):

            with pytest.raises(Exception, match="API Error"):
                _extract(sample_session)

    def test_invalid_json_response(self, sample_session):
        """测试 LLM 返回无效 JSON。"""
        with patch("scripts.session_graph.load_schema"), \
             patch("scripts.session_graph.get_node_types"), \
             patch("scripts.session_graph.get_relation_types"), \
             patch("scripts.session_graph.get_schema_domains"), \
             patch("scripts.session_graph.get_system_instruction"):

            mock_llm = MagicMock()
            mock_llm.text = "不是 JSON: {nodes: []"

            with patch("scripts.session_graph.ask_llm", return_value=mock_llm):
                with pytest.raises(json.JSONDecodeError):
                    _extract(sample_session)

    def test_missing_nodes_key_in_json(self, sample_session):
        """测试 JSON 缺少必需字段。"""
        with patch("scripts.session_graph.load_schema"), \
             patch("scripts.session_graph.get_node_types"), \
             patch("scripts.session_graph.get_relation_types"), \
             patch("scripts.session_graph.get_schema_domains"), \
             patch("scripts.session_graph.get_system_instruction"):

            mock_llm = MagicMock()
            mock_llm.text = json.dumps({"edges": []}, ensure_ascii=False)  # 缺少 nodes

            with patch("scripts.session_graph.ask_llm", return_value=mock_llm):
                with pytest.raises(KeyError):
                    _extract(sample_session)

    def test_malformed_node_data(self, sample_session):
        """测试节点数据格式错误（缺少必需字段）。"""
        with patch("scripts.session_graph.load_schema"), \
             patch("scripts.session_graph.get_node_types"), \
             patch("scripts.session_graph.get_relation_types"), \
             patch("scripts.session_graph.get_schema_domains"), \
             patch("scripts.session_graph.get_system_instruction"):

            mock_llm = MagicMock()
            # 节点缺少 id 字段
            mock_llm.text = json.dumps({
                "nodes": [{"type": "concept", "label": "测试"}],
                "edges": []
            }, ensure_ascii=False)

            with patch("scripts.session_graph.ask_llm", return_value=mock_llm):
                with pytest.raises(KeyError):
                    result = _extract(sample_session)
                    # 访问 related_dates 时会因为缺少 id 而失败
                    for n in result["nodes"]:
                        n["related_dates"]

    def test_cache_file_creates_directory(self, sample_session, tmp_path):
        """测试缓存文件自动创建目录（不会失败）。"""
        # build_session_fragment 会自动 mkdir(parents=True)，不会抛异常
        cache_file = tmp_path / "new_dir" / "session1.json"
        fragment = {"source_file": "test.txt", "session_date": "2024-01-01", "nodes": [], "edges": []}

        with patch("scripts.session_graph.fragment_path", return_value=cache_file), \
             patch("scripts.session_graph._extract", return_value=fragment), \
             patch("scripts.session_graph.GRAPH_FRAGMENTS_DIR", return_value=tmp_path / "new_dir"):

            # 应该成功创建目录并写入
            result = build_session_fragment(sample_session, force=False)

        assert result == fragment
        assert cache_file.exists()
        assert (tmp_path / "new_dir").exists()
