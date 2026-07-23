"""构建"核心图式 / 应对模式 / 关键事件 / 人物…"多层关系图（mindmap 数据源），
采用 **map-reduce** 逐份抽取再归并的方式生成，粒度远细于旧版一次性全局抽取。

架构（v3）：
- **map**（scripts/session_graph.py）：每份咨询逐字稿各自抽一张细粒度子图 fragment，直接读
  原始逐字稿（而非压缩摘要），按份缓存在 config.GRAPH_FRAGMENTS_DIR，重跑只抽新增的份。
- **reduce**（scripts/graph_utils.resolve_graph）：把所有 fragment 里"同一概念"按类型内语义相似度
  归并成规范节点、合并 related_dates，并把边重映射去重、合并 evidence_dates。纯本地向量化，无 LLM。
- 最后 compute_centrality 算度/介数中心性（Borsboom 网络理论的"根源驱动"）。

节点/关系分类（依现代心理学理论分层）集中定义在 scripts/graph_utils.NODE_TYPES / RELATION_TYPES；
每类的理论依据见 scripts/session_graph.py 顶部与其 SYSTEM_INSTRUCTION。

产物：data/graph.json，供 pages/1_🕸️_心智地图.py 可视化、scripts/ask.py 组装上下文与图谱引导检索。
新增几次咨询后重跑本脚本（只会抽新增的份，便宜）或点 UI 里的"重新生成"按钮即可。
"""
import json

from config import GRAPH_JSON_PATH
from scripts.graph_utils import compute_centrality, resolve_graph
from scripts.session_graph import ensure_fragments


def build_graph(force: bool = False) -> dict:
    """map-reduce 建图：确保每份 fragment 存在（缺的才抽）→ 归并 → 算中心性。
    force=True 会全量重抽每一份（贵，53+ 次 LLM 调用）；默认只抽没缓存的新逐字稿。"""
    fragments = ensure_fragments(force=force)
    graph = resolve_graph(fragments)
    compute_centrality(graph)
    return graph


if __name__ == "__main__":
    import sys
    from collections import Counter

    graph = build_graph(force="--force" in sys.argv)
    GRAPH_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    GRAPH_JSON_PATH.write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")

    by_type = Counter(n["type"] for n in graph["nodes"])
    by_relation = Counter(e["relation_type"] for e in graph["edges"])
    print(f"节点数: {len(graph['nodes'])} {dict(by_type)}")
    print(f"边数: {len(graph['edges'])} {dict(by_relation)}")
    print(f"已写入 {GRAPH_JSON_PATH}")
