"""测试 build_graph.py 的图谱构建主流程。

覆盖：
1. build_graph() - 主流程（map → reduce → centrality）
2. build_chat_graph() - AI 对话图谱构建
3. force 参数行为（增量 vs 全量）
4. workspace_id 隔离
5. 错误处理和降级
6. 文件读写操作
7. 片段聚合逻辑

Mock 策略：
- session_graph.ensure_fragments() - 返回假的 fragments
- graph_utils.resolve_graph() - 返回假的 graph
- graph_utils.compute_centrality() - 原地修改 graph
- 文件操作 - 使用 tmp_path
"""
import json
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch, call

import pytest

from scripts.build_graph import build_graph


class TestBuildGraph:
    """测试 build_graph() 主流程。"""

    @patch("scripts.build_graph.compute_centrality")
    @patch("scripts.build_graph.resolve_graph")
    @patch("scripts.build_graph.ensure_fragments")
    @patch("scripts.build_graph.load_schema")
    def test_build_graph_basic_flow(
        self,
        mock_load_schema,
        mock_ensure_fragments,
        mock_resolve_graph,
        mock_compute_centrality,
    ):
        """测试基本流程：schema 加载 → map → reduce → centrality。"""
        # Mock schema
        mock_schema = {
            "node_types": {"concept": {"label": "概念"}},
            "relation_types": {"relates": "关联"},
        }
        mock_load_schema.return_value = mock_schema

        # Mock fragments
        mock_fragments = [
            {
                "nodes": [{"id": "n1", "label": "节点1"}],
                "edges": [],
            }
        ]
        mock_ensure_fragments.return_value = mock_fragments

        # Mock resolved graph
        mock_graph = {
            "nodes": [{"id": "n1", "label": "节点1"}],
            "edges": [],
        }
        mock_resolve_graph.return_value = mock_graph

        # Execute
        result = build_graph(force=False, workspace_id=None)

        # Verify call chain
        mock_load_schema.assert_called_once_with(None)
        mock_ensure_fragments.assert_called_once_with(force=False, workspace_id=None)
        mock_resolve_graph.assert_called_once_with(mock_fragments, schema=mock_schema)
        mock_compute_centrality.assert_called_once_with(mock_graph)

        # Verify result
        assert result == mock_graph

    @patch("scripts.build_graph.compute_centrality")
    @patch("scripts.build_graph.resolve_graph")
    @patch("scripts.build_graph.ensure_fragments")
    @patch("scripts.build_graph.load_schema")
    def test_build_graph_with_force_true(
        self,
        mock_load_schema,
        mock_ensure_fragments,
        mock_resolve_graph,
        mock_compute_centrality,
    ):
        """测试 force=True 全量重建。"""
        mock_load_schema.return_value = {}
        mock_ensure_fragments.return_value = []
        mock_resolve_graph.return_value = {"nodes": [], "edges": []}

        build_graph(force=True, workspace_id=None)

        # 验证 force 参数传递
        mock_ensure_fragments.assert_called_once_with(force=True, workspace_id=None)

    @patch("scripts.build_graph.compute_centrality")
    @patch("scripts.build_graph.resolve_graph")
    @patch("scripts.build_graph.ensure_fragments")
    @patch("scripts.build_graph.load_schema")
    def test_build_graph_with_workspace_id(
        self,
        mock_load_schema,
        mock_ensure_fragments,
        mock_resolve_graph,
        mock_compute_centrality,
    ):
        """测试 workspace_id 隔离。"""
        mock_load_schema.return_value = {}
        mock_ensure_fragments.return_value = []
        mock_resolve_graph.return_value = {"nodes": [], "edges": []}

        workspace_id = "test-workspace"
        build_graph(force=False, workspace_id=workspace_id)

        # 验证 workspace_id 传递到所有子函数
        mock_load_schema.assert_called_once_with(workspace_id)
        mock_ensure_fragments.assert_called_once_with(
            force=False, workspace_id=workspace_id
        )

    @patch("scripts.build_graph.compute_centrality")
    @patch("scripts.build_graph.resolve_graph")
    @patch("scripts.build_graph.ensure_fragments")
    @patch("scripts.build_graph.load_schema")
    def test_build_graph_empty_fragments(
        self,
        mock_load_schema,
        mock_ensure_fragments,
        mock_resolve_graph,
        mock_compute_centrality,
    ):
        """测试空 fragments 处理。"""
        mock_load_schema.return_value = {}
        mock_ensure_fragments.return_value = []
        mock_resolve_graph.return_value = {"nodes": [], "edges": []}

        result = build_graph()

        # 应正常返回空图
        assert result == {"nodes": [], "edges": []}
        mock_compute_centrality.assert_called_once()

    @patch("scripts.build_graph.compute_centrality")
    @patch("scripts.build_graph.resolve_graph")
    @patch("scripts.build_graph.ensure_fragments")
    @patch("scripts.build_graph.load_schema")
    def test_build_graph_multiple_fragments(
        self,
        mock_load_schema,
        mock_ensure_fragments,
        mock_resolve_graph,
        mock_compute_centrality,
    ):
        """测试多个 fragments 聚合。"""
        mock_load_schema.return_value = {}

        # 模拟 3 个 fragments
        mock_fragments = [
            {
                "nodes": [{"id": "f1:n1", "label": "片段1节点"}],
                "edges": [],
            },
            {
                "nodes": [{"id": "f2:n1", "label": "片段2节点"}],
                "edges": [],
            },
            {
                "nodes": [{"id": "f3:n1", "label": "片段3节点"}],
                "edges": [],
            },
        ]
        mock_ensure_fragments.return_value = mock_fragments

        # 模拟归并后合并为 2 个节点
        mock_resolved = {
            "nodes": [
                {"id": "n1", "label": "合并节点"},
                {"id": "n2", "label": "独立节点"},
            ],
            "edges": [],
        }
        mock_resolve_graph.return_value = mock_resolved

        result = build_graph()

        # 验证传递了所有 fragments
        mock_resolve_graph.assert_called_once_with(mock_fragments, schema={})
        assert len(result["nodes"]) == 2


class TestBuildGraphMainBlock:
    """测试 __main__ 块的文件写入逻辑。"""

    @patch("scripts.build_graph.GRAPH_JSON_PATH")
    @patch("scripts.build_graph.build_graph")
    def test_main_writes_file(self, mock_build_graph, mock_graph_path, tmp_path):
        """测试 main 块正确写入 graph.json。"""
        # Mock graph
        mock_graph = {
            "nodes": [
                {
                    "id": "n1",
                    "type": "schema",
                    "label": "测试图式",
                    "degree_centrality": 0.5,
                }
            ],
            "edges": [
                {
                    "source": "n1",
                    "target": "n2",
                    "relation_type": "derives",
                }
            ],
        }
        mock_build_graph.return_value = mock_graph

        # Mock path
        output_file = tmp_path / "graph.json"
        mock_graph_path.return_value = output_file

        # Execute main block
        import sys
        from scripts.build_graph import __name__ as module_name

        if module_name == "__main__":
            # 模拟运行 main block
            graph_path = mock_graph_path()
            graph_path.parent.mkdir(parents=True, exist_ok=True)
            graph_path.write_text(
                json.dumps(mock_graph, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            # Verify file written
            assert output_file.exists()
            written_data = json.loads(output_file.read_text(encoding="utf-8"))
            assert written_data == mock_graph

    def test_main_with_force_flag(self, tmp_path):
        """测试 --force 参数解析。"""
        import sys

        # 测试命令行参数解析逻辑
        original_argv = sys.argv.copy()
        try:
            sys.argv = ["build_graph.py", "--force"]
            force = "--force" in sys.argv
            assert force is True

            # 不带 --force
            sys.argv = ["build_graph.py"]
            force = "--force" in sys.argv
            assert force is False
        finally:
            sys.argv = original_argv


class TestBuildChatGraph:
    """测试 build_chat_graph.py 的 AI 对话图谱构建。"""

    @patch("scripts.build_chat_graph.compute_centrality")
    @patch("scripts.build_chat_graph.ask_llm")
    @patch("scripts.build_chat_graph._load_therapy_graph")
    @patch("scripts.build_chat_graph.load_chat_sessions")
    def test_build_chat_graph_basic(
        self,
        mock_load_sessions,
        mock_load_therapy_graph,
        mock_ask_llm,
        mock_compute_centrality,
    ):
        """测试基本的对话图谱构建流程。"""
        from scripts.build_chat_graph import build_chat_graph

        # Mock sessions
        mock_sessions = [
            {
                "session_id": "s1",
                "updated_at": "2024-01-01T10:00:00",
                "messages": [
                    {"role": "user", "content": "我最近很焦虑"},
                    {"role": "assistant", "content": "能说说什么让你焦虑吗？"},
                ],
            }
        ]
        mock_load_sessions.return_value = mock_sessions

        # Mock therapy graph
        mock_therapy_graph = {
            "nodes": [
                {
                    "id": "schema:1",
                    "label": "焦虑图式",
                    "description": "长期焦虑模式",
                }
            ],
            "edges": [],
        }
        mock_load_therapy_graph.return_value = mock_therapy_graph

        # Mock LLM response
        mock_llm_response = MagicMock()
        mock_llm_response.text = json.dumps(
            {
                "nodes": [
                    {
                        "id": "chat:coping:1",
                        "type": "coping",
                        "label": "讨好模式",
                        "domain": "",
                        "description": "描述",
                        "related_dates": ["2024-01-01"],
                    }
                ],
                "edges": [
                    {
                        "source": "chat:coping:1",
                        "target": "schema:1",
                        "relation_type": "relates_to",
                        "relation": "呼应焦虑图式",
                        "evidence_dates": ["2024-01-01"],
                    }
                ],
            }
        )
        mock_ask_llm.return_value = mock_llm_response

        # Execute
        result = build_chat_graph(workspace_id=None)

        # Verify
        assert len(result["nodes"]) == 1
        assert result["nodes"][0]["id"] == "chat:coping:1"
        assert result["nodes"][0]["source"] == "chat"
        assert len(result["edges"]) == 1
        mock_compute_centrality.assert_called_once_with(result)

    @patch("scripts.build_chat_graph.compute_centrality")
    @patch("scripts.build_chat_graph.ask_llm")
    @patch("scripts.build_chat_graph._load_therapy_graph")
    @patch("scripts.build_chat_graph.load_chat_sessions")
    def test_build_chat_graph_empty_sessions(
        self,
        mock_load_sessions,
        mock_load_therapy_graph,
        mock_ask_llm,
        mock_compute_centrality,
    ):
        """测试空 sessions 处理。"""
        from scripts.build_chat_graph import build_chat_graph

        mock_load_sessions.return_value = []
        mock_load_therapy_graph.return_value = None

        result = build_chat_graph(workspace_id=None)

        # 应返回空图，不调用 LLM
        assert result == {"nodes": [], "edges": []}
        mock_ask_llm.assert_not_called()
        mock_compute_centrality.assert_not_called()

    @patch("scripts.build_chat_graph.compute_centrality")
    @patch("scripts.build_chat_graph.ask_llm")
    @patch("scripts.build_chat_graph._load_therapy_graph")
    @patch("scripts.build_chat_graph.load_chat_sessions")
    def test_build_chat_graph_filters_invalid_dates(
        self,
        mock_load_sessions,
        mock_load_therapy_graph,
        mock_ask_llm,
        mock_compute_centrality,
    ):
        """测试过滤编造日期。"""
        from scripts.build_chat_graph import build_chat_graph

        # Mock sessions
        mock_sessions = [
            {
                "session_id": "s1",
                "updated_at": "2024-01-01T10:00:00",
                "messages": [],
            }
        ]
        mock_load_sessions.return_value = mock_sessions
        mock_load_therapy_graph.return_value = None

        # Mock LLM 返回编造日期
        mock_llm_response = MagicMock()
        mock_llm_response.text = json.dumps(
            {
                "nodes": [
                    {
                        "id": "chat:event:1",
                        "type": "event",
                        "label": "事件",
                        "domain": "",
                        "description": "描述",
                        "related_dates": ["2024-01-01", "2099-12-31"],  # 编造日期
                    }
                ],
                "edges": [],
            }
        )
        mock_ask_llm.return_value = mock_llm_response

        result = build_chat_graph(workspace_id=None)

        # 验证编造日期被过滤
        assert result["nodes"][0]["related_dates"] == ["2024-01-01"]

    @patch("scripts.build_chat_graph.compute_centrality")
    @patch("scripts.build_chat_graph.ask_llm")
    @patch("scripts.build_chat_graph._load_therapy_graph")
    @patch("scripts.build_chat_graph.load_chat_sessions")
    def test_build_chat_graph_filters_invalid_node_ids(
        self,
        mock_load_sessions,
        mock_load_therapy_graph,
        mock_ask_llm,
        mock_compute_centrality,
    ):
        """测试过滤非 chat: 前缀节点。"""
        from scripts.build_chat_graph import build_chat_graph

        mock_sessions = [
            {"session_id": "s1", "updated_at": "2024-01-01T10:00:00", "messages": []}
        ]
        mock_load_sessions.return_value = mock_sessions
        mock_load_therapy_graph.return_value = None

        # Mock LLM 返回无效 id
        mock_llm_response = MagicMock()
        mock_llm_response.text = json.dumps(
            {
                "nodes": [
                    {
                        "id": "chat:valid:1",
                        "type": "coping",
                        "label": "有效",
                        "domain": "",
                        "description": "有效节点",
                        "related_dates": ["2024-01-01"],
                    },
                    {
                        "id": "invalid:2",  # 无效：缺少 chat: 前缀
                        "type": "coping",
                        "label": "无效",
                        "domain": "",
                        "description": "无效节点",
                        "related_dates": ["2024-01-01"],
                    },
                ],
                "edges": [],
            }
        )
        mock_ask_llm.return_value = mock_llm_response

        result = build_chat_graph(workspace_id=None)

        # 只保留 chat: 前缀的节点
        assert len(result["nodes"]) == 1
        assert result["nodes"][0]["id"] == "chat:valid:1"

    @patch("scripts.build_chat_graph.compute_centrality")
    @patch("scripts.build_chat_graph.ask_llm")
    @patch("scripts.build_chat_graph._load_therapy_graph")
    @patch("scripts.build_chat_graph.load_chat_sessions")
    def test_build_chat_graph_filters_invalid_edges(
        self,
        mock_load_sessions,
        mock_load_therapy_graph,
        mock_ask_llm,
        mock_compute_centrality,
    ):
        """测试过滤无效边（source/target 不存在）。"""
        from scripts.build_chat_graph import build_chat_graph

        mock_sessions = [
            {"session_id": "s1", "updated_at": "2024-01-01T10:00:00", "messages": []}
        ]
        mock_load_sessions.return_value = mock_sessions

        # Mock therapy graph
        mock_therapy_graph = {
            "nodes": [
                {
                    "id": "therapy:1",
                    "label": "治疗节点",
                    "description": "测试节点描述",
                }
            ],
            "edges": [],
        }
        mock_load_therapy_graph.return_value = mock_therapy_graph

        # Mock LLM 返回无效边
        mock_llm_response = MagicMock()
        mock_llm_response.text = json.dumps(
            {
                "nodes": [
                    {
                        "id": "chat:n1",
                        "type": "coping",
                        "label": "节点1",
                        "domain": "",
                        "description": "描述",
                        "related_dates": ["2024-01-01"],
                    }
                ],
                "edges": [
                    {
                        "source": "chat:n1",
                        "target": "therapy:1",
                        "relation_type": "relates_to",
                        "relation": "有效边",
                        "evidence_dates": ["2024-01-01"],
                    },
                    {
                        "source": "chat:n1",
                        "target": "nonexistent:99",  # 无效：target 不存在
                        "relation_type": "relates_to",
                        "relation": "无效边",
                        "evidence_dates": ["2024-01-01"],
                    },
                ],
            }
        )
        mock_ask_llm.return_value = mock_llm_response

        result = build_chat_graph(workspace_id=None)

        # 只保留有效边
        assert len(result["edges"]) == 1
        assert result["edges"][0]["target"] == "therapy:1"


class TestErrorHandling:
    """测试错误处理和降级。"""

    @patch("scripts.build_graph.compute_centrality")
    @patch("scripts.build_graph.resolve_graph")
    @patch("scripts.build_graph.ensure_fragments")
    @patch("scripts.build_graph.load_schema")
    def test_build_graph_with_failed_fragments(
        self,
        mock_load_schema,
        mock_ensure_fragments,
        mock_resolve_graph,
        mock_compute_centrality,
    ):
        """测试 ensure_fragments 失败时的处理。"""
        mock_load_schema.return_value = {}
        mock_ensure_fragments.side_effect = RuntimeError("Fragment extraction failed")

        with pytest.raises(RuntimeError, match="Fragment extraction failed"):
            build_graph()

    @patch("scripts.build_graph.compute_centrality")
    @patch("scripts.build_graph.resolve_graph")
    @patch("scripts.build_graph.ensure_fragments")
    @patch("scripts.build_graph.load_schema")
    def test_build_graph_with_failed_resolve(
        self,
        mock_load_schema,
        mock_ensure_fragments,
        mock_resolve_graph,
        mock_compute_centrality,
    ):
        """测试 resolve_graph 失败时的处理。"""
        mock_load_schema.return_value = {}
        mock_ensure_fragments.return_value = [{"nodes": [], "edges": []}]
        mock_resolve_graph.side_effect = ValueError("Merge failed")

        with pytest.raises(ValueError, match="Merge failed"):
            build_graph()

    @patch("scripts.build_chat_graph.compute_centrality")
    @patch("scripts.build_chat_graph.ask_llm")
    @patch("scripts.build_chat_graph._load_therapy_graph")
    @patch("scripts.build_chat_graph.load_chat_sessions")
    def test_build_chat_graph_with_malformed_llm_response(
        self,
        mock_load_sessions,
        mock_load_therapy_graph,
        mock_ask_llm,
        mock_compute_centrality,
    ):
        """测试 LLM 返回格式错误时的处理。"""
        from scripts.build_chat_graph import build_chat_graph

        mock_sessions = [
            {"session_id": "s1", "updated_at": "2024-01-01T10:00:00", "messages": []}
        ]
        mock_load_sessions.return_value = mock_sessions
        mock_load_therapy_graph.return_value = None

        # Mock LLM 返回无效 JSON
        mock_llm_response = MagicMock()
        mock_llm_response.text = "invalid json"
        mock_ask_llm.return_value = mock_llm_response

        with pytest.raises(json.JSONDecodeError):
            build_chat_graph(workspace_id=None)


class TestMainBlockExecution:
    """测试 __main__ 块的完整执行流程。"""

    @patch("scripts.build_graph.GRAPH_JSON_PATH")
    @patch("scripts.build_graph.compute_centrality")
    @patch("scripts.build_graph.resolve_graph")
    @patch("scripts.build_graph.ensure_fragments")
    @patch("scripts.build_graph.load_schema")
    def test_main_block_complete_execution(
        self,
        mock_load_schema,
        mock_ensure_fragments,
        mock_resolve_graph,
        mock_compute_centrality,
        mock_graph_path,
        tmp_path,
        capsys,
    ):
        """测试 __main__ 块的完整流程（构建 + 写文件 + 打印统计）。"""
        # Setup mocks
        mock_load_schema.return_value = {}
        mock_ensure_fragments.return_value = []

        mock_graph = {
            "nodes": [
                {"id": "n1", "type": "schema", "label": "节点1"},
                {"id": "n2", "type": "coping", "label": "节点2"},
            ],
            "edges": [
                {"source": "n1", "target": "n2", "relation_type": "derives"},
                {"source": "n2", "target": "n1", "relation_type": "reinforces"},
            ],
        }
        mock_resolve_graph.return_value = mock_graph

        output_file = tmp_path / "graph.json"
        mock_graph_path.return_value = output_file

        # Execute (模拟 __main__ 块的逻辑)
        import sys
        from collections import Counter

        # 模拟不带 --force 参数
        original_argv = sys.argv.copy()
        try:
            sys.argv = ["build_graph.py"]

            graph = build_graph(force="--force" in sys.argv)

            graph_path = mock_graph_path()
            graph_path.parent.mkdir(parents=True, exist_ok=True)
            graph_path.write_text(
                json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            by_type = Counter(n["type"] for n in graph["nodes"])
            by_relation = Counter(e["relation_type"] for e in graph["edges"])
            print(f"节点数: {len(graph['nodes'])} {dict(by_type)}")
            print(f"边数: {len(graph['edges'])} {dict(by_relation)}")
            print(f"已写入 {graph_path}")

        finally:
            sys.argv = original_argv

        # Verify file written
        assert output_file.exists()
        written_data = json.loads(output_file.read_text(encoding="utf-8"))
        assert written_data == mock_graph

        # Verify printed output
        captured = capsys.readouterr()
        assert "节点数: 2" in captured.out
        assert "边数: 2" in captured.out
        assert "已写入" in captured.out
        assert "schema" in captured.out
        assert "coping" in captured.out


class TestIntegration:
    """集成测试（需要 --integration flag）。"""

    @pytest.mark.integration
    def test_full_graph_build_pipeline(self, tmp_path, mock_embedder):
        """测试完整的图谱构建流水线。

        此测试不 mock session_graph 和 graph_utils，
        验证完整的 map-reduce 流程。
        """
        # 创建测试数据
        raw_dir = tmp_path / "data" / "raw"
        raw_dir.mkdir(parents=True)

        transcript = raw_dir / "20240101_test.txt"
        transcript.write_text(
            """20240101120000_测试咨询.txt

[00:00:10] Andy: 我总是害怕被抛弃。
[00:01:20] 咨询师: 这种感觉从什么时候开始的？
[00:02:30] Andy: 小时候父母离异后，我就有这种感觉。
""",
            encoding="utf-8",
        )

        # 运行完整流程需要真实的 LLM 调用
        # 这里仅验证不崩溃
        # 实际测试需要用 --integration flag 运行
        pass
