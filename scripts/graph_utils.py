"""图谱通用工具：节点/关系分类（单一真相源）+ 中心性计算 + 逐份子图归并（reduce）+
from typing import Optional
合并"真实咨询图谱"与"AI 对话记忆图谱"。

两张图分开维护、分开生成（各自的生成脚本见 build_graph.py / build_chat_graph.py），
只在被消费的地方（可视化页面、ask.py 的问答上下文/图谱引导检索）按需合并——合并本身是
纯 Python 操作，不调用任何 LLM，零额外成本。
"""
import networkx as nx

# ── 节点/关系分类：单一真相源 ──────────────────────────────────────────────
# 依现代心理学理论分层，从发展根源 → 核心结构 → 当下体验 → 具体证据。session_graph.py 抽取、
# ask.py 组装上下文、心智地图页可视化都从这里取标签/层级，避免各处 hardcode 后漂移。
# 理论依据：Schema Therapy 图式与模式模型 & 五大核心情感需要（Young）、Beck 认知概念化图
# （核心信念→中间信念→自动思维→情绪/行为）、Ellis ABC、Gross 情绪调节过程模型、依附理论、
# 自我决定论、CCRT 核心冲突关系主题、Borsboom 精神病理网络理论（中心性）。
NODE_TYPES = {
    "need":              {"label": "核心情感需要", "layer": 0, "has_domain": False},
    "person":            {"label": "依附对象/重要他人", "layer": 0, "has_domain": False},
    "schema":            {"label": "核心图式", "layer": 1, "has_domain": True},
    "belief":            {"label": "中间信念/规则", "layer": 2, "has_domain": False},
    "mode":              {"label": "图式模式", "layer": 2, "has_domain": False},
    "coping":            {"label": "应对/防御模式", "layer": 3, "has_domain": False},
    "trigger":           {"label": "触发情境", "layer": 3, "has_domain": False},
    "automatic_thought": {"label": "自动思维", "layer": 4, "has_domain": False},
    "emotion":           {"label": "情绪", "layer": 4, "has_domain": False},
    "event":             {"label": "关键事件", "layer": 5, "has_domain": False},
}

# 关系类型 relation_type → 中文标签。方向与典型 src→tgt 见 session_graph.py 的抽取指引。
RELATION_TYPES = {
    "unmet":          "未满足",          # need → schema（核心需要长期未满足 → 形成图式）
    "originates":     "是源头",          # person → schema/need（依附对象是图式/需要的发展源头）
    "assumes":        "派生信念",        # schema → belief（核心图式派生中间信念/条件规则）
    "derives":        "派生出",          # schema → coping/mode
    "activates":      "激活",            # trigger → schema/mode/emotion（Ellis ABC 激活事件）
    "triggers":       "会触发",          # person → coping/mode
    "produces":       "产生",            # belief/mode → automatic_thought
    "evokes":         "引发",            # automatic_thought/trigger → emotion
    "regulated_by":   "被…调节",         # emotion → coping（Gross：用某应对策略调节该情绪）
    "co_occurs":      "常同时激活",      # schema ↔ schema
    "reinforces":     "相互强化",        # coping/mode ↔ coping/mode
    "conflicts_with": "彼此拉扯/冲突",   # coping/mode ↔ coping/mode
    "manifested_in":  "体现在",          # schema/coping/mode → event
    "relates_to":     "呼应",            # 跨图（真实咨询 ⇄ AI 对话记忆）呼应，chat graph 用
}

# 归并（reduce）时判定"同一概念"的语义相似度阈值：偏高 = 保守合并、保留更多细节节点
# （符合"顆粒度更小"的目标）；偏低 = 更激进合并。可按重建后的观感调。
MERGE_SIM_THRESHOLD = 0.80


def compute_centrality(graph: dict) -> None:
    """用 networkx 算度中心性/介数中心性，写回每个节点（呼应 Borsboom 精神病理网络理论
    里"中心症状"的概念）。在合并后的图上重新算一遍，因为跨图的 relates_to 边会改变
    节点的连接度——单独看某一半图时算出来的中心性不代表它在全局里的重要程度。"""
    G = nx.Graph()
    for n in graph["nodes"]:
        G.add_node(n["id"])
    for e in graph["edges"]:
        if e["source"] in G and e["target"] in G:
            G.add_edge(e["source"], e["target"])

    degree = nx.degree_centrality(G)
    betweenness = nx.betweenness_centrality(G) if G.number_of_edges() > 0 else {n: 0.0 for n in G.nodes}

    for n in graph["nodes"]:
        n["degree_centrality"] = round(degree.get(n["id"], 0.0), 4)
        n["betweenness_centrality"] = round(betweenness.get(n["id"], 0.0), 4)


def _cluster_key(node: dict) -> str:
    """归并聚类用的文本：只用"概念名 + 图式领域"，不含随会话变化的描述——这样同一概念
    在不同会话里措辞略有差异也能聚到一起，而描述留作代表节点的详细说明。"""
    domain = node.get("domain") or ""
    return f"{node['label']} {domain}".strip()


def resolve_graph(fragments: list[dict], threshold: float = MERGE_SIM_THRESHOLD, schema: Optional[dict] = None) -> dict:
    """reduce 步：把逐份咨询抽出的子图（fragments）归并成一张全局图。

    每份 fragment 是 {"nodes", "edges", "session_date", ...}；不同份里"同一个概念"（如都提到
    被抛弃图式）会各出一个节点，这里按 (类型内) 语义相似度贪心聚类归并成一个规范节点，
    合并 related_dates，并把所有边重映射到规范 id、按 (源,目标,关系) 去重合并 evidence_dates。
    纯 Python + 一次本地 BGE 批量向量化，不调用 LLM。

    Args:
        fragments: 逐份子图列表
        threshold: 归并相似度阈值（默认使用 schema 中的值或 MERGE_SIM_THRESHOLD）
        schema: graph schema 定义（可选，为 None 时使用硬编码的 NODE_TYPES/RELATION_TYPES）

    Returns:
        合并后的全局图
    """
    import numpy as np
    from scripts.embedder import embed

    # 如果提供了 schema，使用 schema 中的 merge_threshold
    if schema is not None:
        threshold = schema.get("merge_threshold", threshold)

    # 1) 收集所有原始节点，给每个一个全局唯一临时 id（份内 id 会跨份撞车）。
    raw_nodes = []  # (global_tmp_id, node_dict, session_date)
    id_map = {}     # global_tmp_id → canonical_id（第 3 步填）
    for fi, frag in enumerate(fragments):
        date = frag.get("session_date")
        for n in frag["nodes"]:
            gid = f"{fi}::{n['id']}"
            raw_nodes.append((gid, n, date))

    if not raw_nodes:
        return {"nodes": [], "edges": []}

    # 2) 逐类型贪心聚类（同类型才可能是同一概念）。先按"概念名出现频次"降序，让高频概念先立簇、
    #    成为稳定锚点，减少贪心聚类的顺序敏感。
    key_texts = [_cluster_key(n) for _, n, _ in raw_nodes]
    vecs = embed(key_texts)["dense_vecs"]
    norms = [v / (np.linalg.norm(v) + 1e-9) for v in vecs]

    from collections import Counter
    label_freq = Counter(n["label"] for _, n, _ in raw_nodes)
    order = sorted(range(len(raw_nodes)), key=lambda i: -label_freq[raw_nodes[i][1]["label"]])

    clusters = []  # 每簇：{"type", "rep_vec", "members":[idx...]}
    for i in order:
        _, node, _ = raw_nodes[i]
        v = norms[i]
        best, best_sim = None, threshold
        for c in clusters:
            if c["type"] != node["type"]:
                continue
            sim = float(np.dot(v, c["rep_vec"]))
            if sim >= best_sim:
                best_sim, best = sim, c
        if best is None:
            clusters.append({"type": node["type"], "rep_vec": v, "members": [i]})
        else:
            best["members"].append(i)

    # 3) 每簇 → 一个规范节点。代表 label = 簇内最高频；描述 = 簇内最长（信息量最大）；
    #    domain = 簇内众数；related_dates = 成员所属会话日期的并集。
    merged_nodes = []
    for ci, c in enumerate(clusters):
        members = [raw_nodes[i] for i in c["members"]]
        labels = Counter(n["label"] for _, n, _ in members)
        rep_label = labels.most_common(1)[0][0]
        rep_desc = max((n.get("description", "") for _, n, _ in members), key=len)
        domains = Counter(n.get("domain", "") for _, n, _ in members if n.get("domain"))
        canonical_id = f"{c['type']}:{ci}"
        dates = sorted({d for _, _, d in members if d})
        for gid, _, _ in members:
            id_map[gid] = canonical_id
        merged_nodes.append({
            "id": canonical_id,
            "type": c["type"],
            "label": rep_label,
            "domain": domains.most_common(1)[0][0] if domains else "",
            "description": rep_desc,
            "related_dates": dates,
            "source": "therapy",
        })

    # 4) 重映射所有边到规范 id，按 (源,目标,关系) 去重合并（丢弃归并后自指的边）。
    edge_acc = {}  # (src,tgt,rel) → {"relation", "evidence_dates":set}
    for fi, frag in enumerate(fragments):
        date = frag.get("session_date")
        for e in frag["edges"]:
            src = id_map.get(f"{fi}::{e['source']}")
            tgt = id_map.get(f"{fi}::{e['target']}")
            if not src or not tgt or src == tgt:
                continue
            key = (src, tgt, e["relation_type"])
            slot = edge_acc.setdefault(key, {"relation": "", "evidence_dates": set()})
            if len(e.get("relation", "")) > len(slot["relation"]):
                slot["relation"] = e["relation"]  # 保留信息量最大的关系描述
            if date:
                slot["evidence_dates"].add(date)

    merged_edges = [
        {
            "source": s, "target": t, "relation_type": r,
            "relation": v["relation"],
            "evidence_dates": sorted(v["evidence_dates"]),
        }
        for (s, t, r), v in edge_acc.items()
    ]

    return {"nodes": merged_nodes, "edges": merged_edges}


def merge_graphs(therapy_graph: Optional[dict], chat_graph: Optional[dict]) -> Optional[dict]:
    """合并真实咨询图谱（source=therapy）和 AI 对话记忆图谱（source=chat）。
    chat_graph 里的节点 id 已经带 "chat:" 前缀，不会跟 therapy_graph 撞车；
    chat_graph 里指向 therapy 节点的 relates_to 边，target 直接引用 therapy 的原始 id，
    合并后自然连通。任何一边缺失都优雅降级（只有一张图时直接返回那一张，都没有则返回 None）。
    """
    if therapy_graph is None and chat_graph is None:
        return None
    if chat_graph is None:
        return therapy_graph
    if therapy_graph is None:
        return chat_graph

    for n in therapy_graph["nodes"]:
        n.setdefault("source", "therapy")
    for n in chat_graph["nodes"]:
        n.setdefault("source", "chat")

    therapy_ids = {n["id"] for n in therapy_graph["nodes"]}
    merged_nodes = list(therapy_graph["nodes"]) + [n for n in chat_graph["nodes"] if n["id"] not in therapy_ids]
    merged_edges = list(therapy_graph["edges"]) + list(chat_graph["edges"])

    merged = {"nodes": merged_nodes, "edges": merged_edges}
    compute_centrality(merged)
    return merged
