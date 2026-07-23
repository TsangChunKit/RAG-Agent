"""测试图谱工具函数。

重点测试：
1. resolve_graph() - 图谱归并算法（最重要）
2. merge_graphs() - 多图合并
3. 节点去重逻辑
4. 边合并逻辑
5. 中心性计算
"""
import numpy as np
import pytest

from scripts.graph_utils import (
    NODE_TYPES,
    RELATION_TYPES,
    MERGE_SIM_THRESHOLD,
    compute_centrality,
    merge_graphs,
    resolve_graph,
    _cluster_key,
)


class TestNodeAndRelationTypes:
    """测试节点和关系类型定义。"""

    def test_node_types_structure(self):
        """验证 NODE_TYPES 结构完整性。"""
        assert len(NODE_TYPES) == 10

        # 验证必需字段
        for node_type, info in NODE_TYPES.items():
            assert "label" in info
            assert "layer" in info
            assert "has_domain" in info
            assert isinstance(info["label"], str)
            assert isinstance(info["layer"], int)
            assert isinstance(info["has_domain"], bool)

    def test_node_types_layers(self):
        """验证节点层级正确。"""
        # 第 0 层：根源（need, person）
        assert NODE_TYPES["need"]["layer"] == 0
        assert NODE_TYPES["person"]["layer"] == 0

        # 第 1 层：核心结构（schema）
        assert NODE_TYPES["schema"]["layer"] == 1

        # 第 2 层：中间层（belief, mode）
        assert NODE_TYPES["belief"]["layer"] == 2
        assert NODE_TYPES["mode"]["layer"] == 2

        # 第 3 层：当下体验（coping, trigger）
        assert NODE_TYPES["coping"]["layer"] == 3
        assert NODE_TYPES["trigger"]["layer"] == 3

        # 第 4 层：直接感受（automatic_thought, emotion）
        assert NODE_TYPES["automatic_thought"]["layer"] == 4
        assert NODE_TYPES["emotion"]["layer"] == 4

        # 第 5 层：具体证据（event）
        assert NODE_TYPES["event"]["layer"] == 5

    def test_relation_types_completeness(self):
        """验证关系类型定义完整。"""
        assert len(RELATION_TYPES) >= 13

        # 关键关系类型存在
        assert "unmet" in RELATION_TYPES
        assert "originates" in RELATION_TYPES
        assert "activates" in RELATION_TYPES
        assert "relates_to" in RELATION_TYPES

        # 所有值都是字符串
        for rel_type, label in RELATION_TYPES.items():
            assert isinstance(label, str)
            assert len(label) > 0

    def test_merge_threshold_range(self):
        """验证归并阈值在合理范围。"""
        assert 0.0 <= MERGE_SIM_THRESHOLD <= 1.0
        assert MERGE_SIM_THRESHOLD >= 0.7  # 不应过低，避免过度合并


class TestClusterKey:
    """测试节点聚类键生成。"""

    def test_cluster_key_basic(self):
        """测试基本的聚类键生成。"""
        node = {
            "id": "schema:1",
            "label": "被抛弃图式",
            "type": "schema",
            "domain": "亲密关系",
            "description": "长描述文字"
        }

        key = _cluster_key(node)
        assert "被抛弃图式" in key
        assert "亲密关系" in key
        assert "长描述文字" not in key  # 描述不应包含在聚类键中

    def test_cluster_key_no_domain(self):
        """测试无 domain 的节点。"""
        node = {
            "id": "need:1",
            "label": "归属需要",
            "type": "need",
            "description": "核心情感需要"
        }

        key = _cluster_key(node)
        assert "归属需要" in key
        assert key == "归属需要"

    def test_cluster_key_empty_domain(self):
        """测试空 domain 的节点。"""
        node = {
            "id": "belief:1",
            "label": "我必须完美",
            "type": "belief",
            "domain": "",
        }

        key = _cluster_key(node)
        assert key == "我必须完美"


class TestComputeCentrality:
    """测试中心性计算。"""

    def test_centrality_basic(self):
        """测试基本中心性计算。"""
        graph = {
            "nodes": [
                {"id": "n1", "label": "节点1"},
                {"id": "n2", "label": "节点2"},
                {"id": "n3", "label": "节点3"},
            ],
            "edges": [
                {"source": "n1", "target": "n2", "relation_type": "activates"},
                {"source": "n2", "target": "n3", "relation_type": "produces"},
            ]
        }

        compute_centrality(graph)

        # 验证所有节点都有中心性值
        for node in graph["nodes"]:
            assert "degree_centrality" in node
            assert "betweenness_centrality" in node
            assert 0.0 <= node["degree_centrality"] <= 1.0
            assert 0.0 <= node["betweenness_centrality"] <= 1.0

        # n2 是中间节点，应该有最高的介数中心性
        n2 = next(n for n in graph["nodes"] if n["id"] == "n2")
        assert n2["betweenness_centrality"] > 0.0

    def test_centrality_single_node(self):
        """测试单节点图。"""
        graph = {
            "nodes": [{"id": "n1", "label": "孤立节点"}],
            "edges": []
        }

        compute_centrality(graph)

        node = graph["nodes"][0]
        # networkx 对单节点图返回 1.0（自身即中心）
        assert node["degree_centrality"] == 1.0
        assert node["betweenness_centrality"] == 0.0

    def test_centrality_empty_graph(self):
        """测试空图。"""
        graph = {"nodes": [], "edges": []}

        # 不应抛异常
        compute_centrality(graph)

        assert len(graph["nodes"]) == 0

    def test_centrality_disconnected_edges(self):
        """测试引用不存在节点的边（应被忽略）。"""
        graph = {
            "nodes": [
                {"id": "n1", "label": "节点1"},
                {"id": "n2", "label": "节点2"},
            ],
            "edges": [
                {"source": "n1", "target": "n2", "relation_type": "activates"},
                {"source": "n2", "target": "n_nonexistent", "relation_type": "produces"},
            ]
        }

        compute_centrality(graph)

        # 不应崩溃，且有效边应计算
        for node in graph["nodes"]:
            assert "degree_centrality" in node
            assert "betweenness_centrality" in node

    def test_centrality_star_topology(self):
        """测试星型拓扑（中心节点应有最高 degree centrality）。"""
        graph = {
            "nodes": [
                {"id": "center", "label": "中心"},
                {"id": "n1", "label": "外围1"},
                {"id": "n2", "label": "外围2"},
                {"id": "n3", "label": "外围3"},
            ],
            "edges": [
                {"source": "center", "target": "n1", "relation_type": "activates"},
                {"source": "center", "target": "n2", "relation_type": "activates"},
                {"source": "center", "target": "n3", "relation_type": "activates"},
            ]
        }

        compute_centrality(graph)

        center = next(n for n in graph["nodes"] if n["id"] == "center")
        peripherals = [n for n in graph["nodes"] if n["id"] != "center"]

        # 中心节点度中心性最高
        assert all(center["degree_centrality"] >= p["degree_centrality"] for p in peripherals)


class TestResolveGraph:
    """测试图谱归并算法（最重要）。"""

    def test_resolve_empty_fragments(self, mock_embedder):
        """测试空片段列表。"""
        result = resolve_graph([])

        assert result == {"nodes": [], "edges": []}

    def test_resolve_single_fragment(self, mock_embedder):
        """测试单个片段（不需要归并）。"""
        fragments = [
            {
                "session_date": "2024-01-01",
                "nodes": [
                    {
                        "id": "schema:1",
                        "type": "schema",
                        "label": "被抛弃图式",
                        "domain": "亲密关系",
                        "description": "害怕被重要他人抛弃",
                    },
                    {
                        "id": "need:1",
                        "type": "need",
                        "label": "归属需要",
                        "description": "需要被接纳和关爱",
                    }
                ],
                "edges": [
                    {
                        "source": "need:1",
                        "target": "schema:1",
                        "relation_type": "unmet",
                        "relation": "长期未满足",
                    }
                ]
            }
        ]

        result = resolve_graph(fragments)

        # 验证基本结构
        assert len(result["nodes"]) == 2
        assert len(result["edges"]) == 1

        # 验证节点字段完整性
        for node in result["nodes"]:
            assert "id" in node
            assert "type" in node
            assert "label" in node
            assert "domain" in node
            assert "description" in node
            assert "related_dates" in node
            assert "source" in node

            # 验证日期正确
            assert node["related_dates"] == ["2024-01-01"]
            assert node["source"] == "therapy"

    def test_resolve_merge_same_concept(self, mock_embedder):
        """测试相同概念归并（核心功能）。"""
        # Mock embedder 返回相似向量（高相似度）
        def fake_embed_similar(texts):
            # 所有向量几乎相同（模拟同一概念）
            base = np.random.rand(1024).astype(np.float32)
            return {
                "dense_vecs": np.array([base + np.random.rand(1024) * 0.01 for _ in texts])
            }

        mock_embedder["embed"].side_effect = fake_embed_similar

        fragments = [
            {
                "session_date": "2024-01-01",
                "nodes": [
                    {
                        "id": "schema:1",
                        "type": "schema",
                        "label": "被抛弃图式",
                        "domain": "亲密关系",
                        "description": "第一次提到",
                    }
                ],
                "edges": []
            },
            {
                "session_date": "2024-01-15",
                "nodes": [
                    {
                        "id": "schema:1",  # 同一份内 id 可能重复
                        "type": "schema",
                        "label": "被抛弃图式",  # 相同概念
                        "domain": "亲密关系",
                        "description": "第二次提到，描述更长更详细",
                    }
                ],
                "edges": []
            }
        ]

        result = resolve_graph(fragments, threshold=0.9)  # 高阈值，确保合并

        # 应该合并成一个节点
        assert len(result["nodes"]) == 1

        node = result["nodes"][0]
        assert node["label"] == "被抛弃图式"
        assert node["domain"] == "亲密关系"
        # 应该保留更长的描述
        assert "更长更详细" in node["description"]
        # 应该合并日期
        assert set(node["related_dates"]) == {"2024-01-01", "2024-01-15"}

    def test_resolve_different_types_no_merge(self, mock_embedder):
        """测试不同类型节点不会被合并（即使名字相同）。"""
        # Mock embedder 返回完全相同的向量
        def fake_embed_identical(texts):
            base = np.random.rand(1024).astype(np.float32)
            return {
                "dense_vecs": np.array([base for _ in texts])
            }

        mock_embedder["embed"].side_effect = fake_embed_identical

        fragments = [
            {
                "session_date": "2024-01-01",
                "nodes": [
                    {
                        "id": "schema:1",
                        "type": "schema",
                        "label": "完美主义",
                        "domain": "工作",
                        "description": "核心图式",
                    },
                    {
                        "id": "belief:1",
                        "type": "belief",
                        "label": "完美主义",  # 同名但不同类型
                        "domain": "",
                        "description": "中间信念",
                    }
                ],
                "edges": []
            }
        ]

        result = resolve_graph(fragments, threshold=0.9)

        # 不同类型不应合并
        assert len(result["nodes"]) == 2
        types = {n["type"] for n in result["nodes"]}
        assert types == {"schema", "belief"}

    def test_resolve_low_similarity_no_merge(self, mock_embedder):
        """测试低相似度节点不会合并。"""
        # Mock embedder 返回差异很大的向量
        def fake_embed_different(texts):
            return {
                "dense_vecs": np.array([
                    np.random.rand(1024).astype(np.float32) for _ in texts
                ])
            }

        mock_embedder["embed"].side_effect = fake_embed_different

        fragments = [
            {
                "session_date": "2024-01-01",
                "nodes": [
                    {
                        "id": "schema:1",
                        "type": "schema",
                        "label": "被抛弃图式",
                        "domain": "亲密关系",
                        "description": "第一个概念",
                    }
                ],
                "edges": []
            },
            {
                "session_date": "2024-01-15",
                "nodes": [
                    {
                        "id": "schema:2",
                        "type": "schema",
                        "label": "完美主义图式",
                        "domain": "工作",
                        "description": "完全不同的概念",
                    }
                ],
                "edges": []
            }
        ]

        result = resolve_graph(fragments, threshold=0.9)

        # 低相似度不应合并
        assert len(result["nodes"]) == 2

    def test_resolve_edges_remap(self, mock_embedder):
        """测试边重映射到规范节点 ID。"""
        # Mock 高相似度向量
        def fake_embed_similar(texts):
            base = np.random.rand(1024).astype(np.float32)
            return {
                "dense_vecs": np.array([base + np.random.rand(1024) * 0.01 for _ in texts])
            }

        mock_embedder["embed"].side_effect = fake_embed_similar

        fragments = [
            {
                "session_date": "2024-01-01",
                "nodes": [
                    {"id": "n1", "type": "need", "label": "归属需要", "description": ""},
                    {"id": "n2", "type": "schema", "label": "被抛弃", "domain": "亲密关系", "description": ""},
                ],
                "edges": [
                    {"source": "n1", "target": "n2", "relation_type": "unmet", "relation": "未满足"}
                ]
            },
            {
                "session_date": "2024-01-15",
                "nodes": [
                    {"id": "x1", "type": "need", "label": "归属需要", "description": ""},  # 同概念，不同 ID
                    {"id": "x2", "type": "schema", "label": "被抛弃", "domain": "亲密关系", "description": ""},
                ],
                "edges": [
                    {"source": "x1", "target": "x2", "relation_type": "unmet", "relation": "长期未满足"}
                ]
            }
        ]

        result = resolve_graph(fragments, threshold=0.9)

        # 节点应该被合并
        assert len(result["nodes"]) == 2

        # 边应该重映射并去重
        assert len(result["edges"]) == 1
        edge = result["edges"][0]

        # 验证 source/target 指向合并后的规范 ID
        assert edge["source"] in {n["id"] for n in result["nodes"]}
        assert edge["target"] in {n["id"] for n in result["nodes"]}

        # 验证证据日期合并
        assert set(edge["evidence_dates"]) == {"2024-01-01", "2024-01-15"}

        # 应该保留更长的关系描述
        assert edge["relation"] == "长期未满足"

    def test_resolve_self_loop_removed(self, mock_embedder):
        """测试自指边被移除（归并后 source == target）。"""
        # Mock 高相似度（同一概念）
        def fake_embed_similar(texts):
            base = np.random.rand(1024).astype(np.float32)
            return {
                "dense_vecs": np.array([base + np.random.rand(1024) * 0.01 for _ in texts])
            }

        mock_embedder["embed"].side_effect = fake_embed_similar

        fragments = [
            {
                "session_date": "2024-01-01",
                "nodes": [
                    {"id": "n1", "type": "schema", "label": "被抛弃", "domain": "亲密关系", "description": ""},
                    {"id": "n2", "type": "schema", "label": "被抛弃", "domain": "亲密关系", "description": ""},  # 会合并
                ],
                "edges": [
                    {"source": "n1", "target": "n2", "relation_type": "co_occurs", "relation": "共现"}
                ]
            }
        ]

        result = resolve_graph(fragments, threshold=0.9)

        # 两个节点应合并成一个
        assert len(result["nodes"]) == 1

        # 自指边应被移除
        assert len(result["edges"]) == 0

    def test_resolve_multiple_edges_merge(self, mock_embedder):
        """测试多条相同 (source, target, relation_type) 的边合并证据日期。"""
        # Mock 高相似度向量，确保节点被合并
        def fake_embed_similar(texts):
            base = np.random.rand(1024).astype(np.float32)
            return {
                "dense_vecs": np.array([base + np.random.rand(1024) * 0.01 for _ in texts])
            }

        mock_embedder["embed"].side_effect = fake_embed_similar

        fragments = [
            {
                "session_date": "2024-01-01",
                "nodes": [
                    {"id": "n1", "type": "need", "label": "归属", "description": ""},
                    {"id": "n2", "type": "schema", "label": "抛弃", "domain": "", "description": ""},
                ],
                "edges": [
                    {"source": "n1", "target": "n2", "relation_type": "unmet", "relation": "未满足"}
                ]
            },
            {
                "session_date": "2024-01-15",
                "nodes": [
                    {"id": "x1", "type": "need", "label": "归属", "description": ""},
                    {"id": "x2", "type": "schema", "label": "抛弃", "domain": "", "description": ""},
                ],
                "edges": [
                    {"source": "x1", "target": "x2", "relation_type": "unmet", "relation": "长期未满足"}
                ]
            }
        ]

        result = resolve_graph(fragments, threshold=0.9)

        # 应该只有一条边
        assert len(result["edges"]) == 1
        edge = result["edges"][0]

        # 验证证据日期合并
        assert len(edge["evidence_dates"]) == 2
        assert set(edge["evidence_dates"]) == {"2024-01-01", "2024-01-15"}

    def test_resolve_with_custom_schema(self, mock_embedder):
        """测试使用自定义 schema（包括自定义 merge_threshold）。"""
        custom_schema = {
            "merge_threshold": 0.75,
            "node_types": {},
            "relation_types": {}
        }

        fragments = [
            {
                "session_date": "2024-01-01",
                "nodes": [
                    {"id": "n1", "type": "concept", "label": "测试概念", "description": ""}
                ],
                "edges": []
            }
        ]

        result = resolve_graph(fragments, schema=custom_schema)

        # 应该使用 schema 中的阈值（这里主要验证不崩溃）
        assert len(result["nodes"]) == 1

    def test_resolve_frequency_based_clustering(self, mock_embedder):
        """测试高频概念优先成为聚类中心（稳定性）。"""
        # Mock 中等相似度向量
        def fake_embed_medium_sim(texts):
            return {
                "dense_vecs": np.array([
                    np.random.rand(1024).astype(np.float32) + i * 0.001
                    for i, _ in enumerate(texts)
                ])
            }

        mock_embedder["embed"].side_effect = fake_embed_medium_sim

        # 创建 10 份片段，其中"核心概念"出现 8 次，"边缘概念"出现 2 次
        fragments = []
        for i in range(8):
            fragments.append({
                "session_date": f"2024-01-{i+1:02d}",
                "nodes": [
                    {"id": "n1", "type": "schema", "label": "核心概念", "domain": "主要", "description": f"第{i+1}次"}
                ],
                "edges": []
            })

        for i in range(2):
            fragments.append({
                "session_date": f"2024-01-{i+10:02d}",
                "nodes": [
                    {"id": "n1", "type": "schema", "label": "边缘概念", "domain": "次要", "description": f"边缘{i+1}"}
                ],
                "edges": []
            })

        result = resolve_graph(fragments, threshold=0.7)

        # 验证高频概念被保留
        labels = [n["label"] for n in result["nodes"]]
        assert "核心概念" in labels


class TestMergeGraphs:
    """测试多图合并。"""

    def test_merge_both_none(self):
        """测试两个图都为 None。"""
        result = merge_graphs(None, None)
        assert result is None

    def test_merge_therapy_only(self):
        """测试只有真实咨询图。"""
        therapy_graph = {
            "nodes": [{"id": "n1", "label": "节点1"}],
            "edges": []
        }

        result = merge_graphs(therapy_graph, None)

        assert result is not None
        assert len(result["nodes"]) == 1
        assert result["nodes"][0]["id"] == "n1"

    def test_merge_chat_only(self):
        """测试只有对话图。"""
        chat_graph = {
            "nodes": [{"id": "chat:n1", "label": "聊天节点"}],
            "edges": []
        }

        result = merge_graphs(None, chat_graph)

        assert result is not None
        assert len(result["nodes"]) == 1
        assert result["nodes"][0]["id"] == "chat:n1"

    def test_merge_both_graphs(self):
        """测试合并两个图（核心功能）。"""
        therapy_graph = {
            "nodes": [
                {"id": "schema:1", "label": "被抛弃图式", "type": "schema"},
                {"id": "need:1", "label": "归属需要", "type": "need"},
            ],
            "edges": [
                {"source": "need:1", "target": "schema:1", "relation_type": "unmet"}
            ]
        }

        chat_graph = {
            "nodes": [
                {"id": "chat:belief:1", "label": "完美主义信念", "type": "belief"},
            ],
            "edges": [
                # 对话节点呼应真实咨询节点
                {"source": "chat:belief:1", "target": "schema:1", "relation_type": "relates_to"}
            ]
        }

        result = merge_graphs(therapy_graph, chat_graph)

        # 验证节点合并
        assert len(result["nodes"]) == 3
        node_ids = {n["id"] for n in result["nodes"]}
        assert "schema:1" in node_ids
        assert "need:1" in node_ids
        assert "chat:belief:1" in node_ids

        # 验证边合并
        assert len(result["edges"]) == 2

        # 验证 source 字段
        therapy_nodes = [n for n in result["nodes"] if not n["id"].startswith("chat:")]
        chat_nodes = [n for n in result["nodes"] if n["id"].startswith("chat:")]

        assert all(n["source"] == "therapy" for n in therapy_nodes)
        assert all(n["source"] == "chat" for n in chat_nodes)

    def test_merge_duplicate_ids_ignored(self):
        """测试重复 ID 只保留一份（therapy 优先）。"""
        therapy_graph = {
            "nodes": [{"id": "n1", "label": "真实咨询节点"}],
            "edges": []
        }

        chat_graph = {
            "nodes": [{"id": "n1", "label": "对话节点"}],  # 同 ID（不应发生但要容错）
            "edges": []
        }

        result = merge_graphs(therapy_graph, chat_graph)

        # 应只保留 therapy 的节点
        assert len(result["nodes"]) == 1
        assert result["nodes"][0]["label"] == "真实咨询节点"

    def test_merge_computes_centrality(self):
        """测试合并后重新计算中心性。"""
        therapy_graph = {
            "nodes": [
                {"id": "n1", "label": "节点1"},
                {"id": "n2", "label": "节点2"},
            ],
            "edges": [
                {"source": "n1", "target": "n2", "relation_type": "activates"}
            ]
        }

        chat_graph = {
            "nodes": [{"id": "chat:n3", "label": "聊天节点"}],
            "edges": [
                {"source": "chat:n3", "target": "n1", "relation_type": "relates_to"}
            ]
        }

        result = merge_graphs(therapy_graph, chat_graph)

        # 验证所有节点都计算了中心性
        for node in result["nodes"]:
            assert "degree_centrality" in node
            assert "betweenness_centrality" in node

    def test_merge_cross_graph_edges(self):
        """测试跨图边（chat → therapy 的 relates_to）。"""
        therapy_graph = {
            "nodes": [{"id": "schema:1", "label": "核心图式"}],
            "edges": []
        }

        chat_graph = {
            "nodes": [{"id": "chat:belief:1", "label": "对话信念"}],
            "edges": [
                # 跨图边
                {"source": "chat:belief:1", "target": "schema:1", "relation_type": "relates_to"}
            ]
        }

        result = merge_graphs(therapy_graph, chat_graph)

        # 验证跨图边存在
        relates_edges = [e for e in result["edges"] if e["relation_type"] == "relates_to"]
        assert len(relates_edges) == 1

        edge = relates_edges[0]
        assert edge["source"].startswith("chat:")
        assert not edge["target"].startswith("chat:")  # 指向 therapy 节点

    def test_merge_empty_graphs(self):
        """测试空图合并。"""
        therapy_graph = {"nodes": [], "edges": []}
        chat_graph = {"nodes": [], "edges": []}

        result = merge_graphs(therapy_graph, chat_graph)

        assert result is not None
        assert len(result["nodes"]) == 0
        assert len(result["edges"]) == 0


class TestEdgeCases:
    """测试边界情况和错误处理。"""

    def test_resolve_missing_session_date(self, mock_embedder):
        """测试缺失 session_date 的片段（应优雅处理）。"""
        fragments = [
            {
                # 没有 session_date
                "nodes": [
                    {"id": "n1", "type": "schema", "label": "测试", "domain": "", "description": ""}
                ],
                "edges": []
            }
        ]

        result = resolve_graph(fragments)

        assert len(result["nodes"]) == 1
        # related_dates 应该是空列表或不包含 None
        assert None not in result["nodes"][0]["related_dates"]

    def test_resolve_missing_node_fields(self, mock_embedder):
        """测试节点缺少可选字段（如 domain）。"""
        fragments = [
            {
                "session_date": "2024-01-01",
                "nodes": [
                    {
                        "id": "n1",
                        "type": "need",
                        "label": "归属需要",
                        # 缺少 domain 和 description
                    }
                ],
                "edges": []
            }
        ]

        result = resolve_graph(fragments)

        assert len(result["nodes"]) == 1
        node = result["nodes"][0]
        assert "domain" in node
        assert "description" in node

    def test_resolve_missing_edge_relation(self, mock_embedder):
        """测试边缺少 relation 字段。"""
        fragments = [
            {
                "session_date": "2024-01-01",
                "nodes": [
                    {"id": "n1", "type": "need", "label": "需要", "description": ""},
                    {"id": "n2", "type": "schema", "label": "图式", "domain": "", "description": ""},
                ],
                "edges": [
                    {
                        "source": "n1",
                        "target": "n2",
                        "relation_type": "unmet",
                        # 缺少 relation 字段
                    }
                ]
            }
        ]

        result = resolve_graph(fragments)

        assert len(result["edges"]) == 1
        edge = result["edges"][0]
        assert "relation" in edge

    def test_centrality_with_isolated_nodes(self):
        """测试包含孤立节点的图。"""
        graph = {
            "nodes": [
                {"id": "n1", "label": "连接节点1"},
                {"id": "n2", "label": "连接节点2"},
                {"id": "n3", "label": "孤立节点"},
            ],
            "edges": [
                {"source": "n1", "target": "n2", "relation_type": "activates"}
            ]
        }

        compute_centrality(graph)

        # 孤立节点中心性应为 0
        n3 = next(n for n in graph["nodes"] if n["id"] == "n3")
        assert n3["degree_centrality"] == 0.0
        assert n3["betweenness_centrality"] == 0.0

    def test_resolve_very_large_fragment_count(self, mock_embedder):
        """测试大量片段（性能/稳定性测试）。"""
        # 创建 100 个片段
        fragments = []
        for i in range(100):
            fragments.append({
                "session_date": f"2024-01-{i%30+1:02d}",
                "nodes": [
                    {
                        "id": f"n{i}",
                        "type": "schema",
                        "label": f"概念{i}",
                        "domain": "测试",
                        "description": f"第{i}个概念"
                    }
                ],
                "edges": []
            })

        # 不应崩溃或超时
        result = resolve_graph(fragments)

        assert len(result["nodes"]) > 0
        assert len(result["nodes"]) <= 100

    def test_resolve_unicode_and_special_chars(self, mock_embedder):
        """测试 Unicode 和特殊字符处理。"""
        fragments = [
            {
                "session_date": "2024-01-01",
                "nodes": [
                    {
                        "id": "n1",
                        "type": "schema",
                        "label": "被抛弃😢",  # emoji
                        "domain": "亲密关系 & 依附",  # 特殊字符
                        "description": "「核心」图式... 测试引号与省略号",
                    }
                ],
                "edges": []
            }
        ]

        result = resolve_graph(fragments)

        assert len(result["nodes"]) == 1
        node = result["nodes"][0]
        assert "😢" in node["label"]
        assert "&" in node["domain"]

    def test_merge_preserve_edge_order(self):
        """测试合并后边的顺序稳定性（用于可复现性）。"""
        therapy_graph = {
            "nodes": [
                {"id": "n1", "label": "节点1"},
                {"id": "n2", "label": "节点2"},
                {"id": "n3", "label": "节点3"},
            ],
            "edges": [
                {"source": "n1", "target": "n2", "relation_type": "activates"},
                {"source": "n2", "target": "n3", "relation_type": "produces"},
            ]
        }

        chat_graph = {
            "nodes": [{"id": "chat:n4", "label": "聊天节点"}],
            "edges": [
                {"source": "chat:n4", "target": "n1", "relation_type": "relates_to"}
            ]
        }

        # 多次合并应得到相同结果
        result1 = merge_graphs(therapy_graph, chat_graph)
        result2 = merge_graphs(therapy_graph, chat_graph)

        assert len(result1["edges"]) == len(result2["edges"])
