"""构建知识图谱（map-reduce 方式），支持多 workspace。

架构：
- **map**（scripts/session_graph.py）：每份文档各自抽一张细粒度子图 fragment，直接读
  原始文档（而非压缩摘要），按份缓存在 GRAPH_FRAGMENTS_DIR，重跑只抽新增的份。
- **reduce**（scripts/graph_utils.resolve_graph）：把所有 fragment 里"同一概念"按类型内语义相似度
  归并成规范节点、合并 related_dates，并把边重映射去重、合并 evidence_dates。纯本地向量化，无 LLM。
- 最后 compute_centrality 算度/介数中心性。

节点/关系分类由 workspace schema 定义（scripts/graph_schema_loader 加载）。

产物：data/graph.json，供心智地图可视化、问答上下文与图谱引导检索。
新增文档后重跑本脚本（只会抽新增的份，便宜）或点 UI 里的"重新生成"按钮即可。
"""
import json

from config import GRAPH_JSON_PATH
from scripts.graph_schema_loader import load_schema
from scripts.graph_utils import compute_centrality, resolve_graph
from scripts.session_graph import ensure_fragments


def build_graph(force: bool = False, workspace_id: Optional[str] = None) -> dict:
    """
from typing import Optional
map-reduce 建图（workspace 感知）。

    Args:
        force: True = 全量重抽每一份（贵）；False = 只抽没缓存的新文档
        workspace_id: workspace ID（None = 当前 workspace）

    Returns:
        graph dict
    """
    # 加载 workspace schema
    schema = load_schema(workspace_id)

    # Map: 确保每份 fragment 存在
    fragments = ensure_fragments(force=force, workspace_id=workspace_id)

    # Reduce: 归并
    graph = resolve_graph(fragments, schema=schema)

    # 算中心性
    compute_centrality(graph)

    return graph


if __name__ == "__main__":
    import sys
    from collections import Counter

    graph = build_graph(force="--force" in sys.argv)

    graph_path = GRAPH_JSON_PATH()
    graph_path.parent.mkdir(parents=True, exist_ok=True)
    graph_path.write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")

    by_type = Counter(n["type"] for n in graph["nodes"])
    by_relation = Counter(e["relation_type"] for e in graph["edges"])
    print(f"节点数: {len(graph['nodes'])} {dict(by_type)}")
    print(f"边数: {len(graph['edges'])} {dict(by_relation)}")
    print(f"已写入 {graph_path}")
