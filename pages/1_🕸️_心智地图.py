"""M7 扩展 v3：核心图式 / 应对模式 / 关键事件 / 人物的多层可交互关系图（mindmap）。

理论依据见 scripts/build_graph.py 顶部注释（Schema Therapy 图式领域、Beck 认知概念化图、
精神病理网络理论的中心性、CCRT 核心冲突关系主题）。这里负责可视化 + 合并两张图：
- data/graph.json：真实咨询心智地图（贵，build_graph.py 生成，只有手动点按钮才重新跑）；
- data/chat_graph.json：AI 对话记忆心智地图（便宜，随聊天自动更新，见 chat_memory_watcher.py）。
两者的合并是纯 Python 操作（scripts/graph_utils.py），不产生额外 Gemini 调用；AI 对话记忆
的节点带虚线边框，和真实咨询的节点区分开，避免把"随口聊的"和"真实咨询的"混为一谈。
"""
import json

import streamlit as st
import streamlit.components.v1 as components
from pyvis.network import Network

from config import CHAT_GRAPH_JSON_PATH, GRAPH_JSON_PATH
from scripts.graph_utils import NODE_TYPES, RELATION_TYPES, merge_graphs

st.set_page_config(page_title="心智地图", page_icon="🕸️", layout="wide")
st.title("🕸️ 核心图式 / 应对模式 / 事件 关系图")
st.caption(
    "基于 Schema Therapy 图式领域 + Beck 认知概念化图 + 精神病理网络理论提炼；"
    "节点大小 = 中心性（越大越像“根源驱动”），可拖拽/缩放/悬停查看依据日期。"
    "虚线边框 = 来自 AI 对话记忆（非真实咨询）。"
)


def _load(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


therapy_graph = _load(GRAPH_JSON_PATH)
chat_graph = _load(CHAT_GRAPH_JSON_PATH)
graph = merge_graphs(therapy_graph, chat_graph)

if graph is None:
    st.warning("还没有生成图谱数据。请先在终端运行：`python -m scripts.build_graph`")
    st.stop()

nodes_by_id = {n["id"]: n for n in graph["nodes"]}

# 颜色/大小/层级：类型标签与层级取自单一真相源 NODE_TYPES；颜色和基础大小是纯可视化选择，
# 按理论层级配色（根源需要/图式=紫蓝，信念/模式=洋红，应对/触发=橙红，当下体验/事件=绿）。
_TYPE_VIS = {
    "need":              {"color": "#16A085", "base_size": 24},
    "person":            {"color": "#4C86C6", "base_size": 18},
    "schema":            {"color": "#8E44AD", "base_size": 26},
    "belief":            {"color": "#6C5CE7", "base_size": 18},
    "mode":              {"color": "#C0399B", "base_size": 20},
    "coping":            {"color": "#E0574B", "base_size": 18},
    "trigger":           {"color": "#E67E22", "base_size": 15},
    "automatic_thought": {"color": "#B7791F", "base_size": 12},
    "emotion":           {"color": "#EB5C8A", "base_size": 14},
    "event":             {"color": "#4CAF6D", "base_size": 13},
}
_FALLBACK_VIS = {"color": "#95a5a6", "base_size": 14}
TYPE_STYLE = {t: {**_TYPE_VIS.get(t, _FALLBACK_VIS), "level": meta["layer"]} for t, meta in NODE_TYPES.items()}
TYPE_LABEL = {t: meta["label"] for t, meta in NODE_TYPES.items()}
SOURCE_LABEL = {"therapy": "真实咨询", "chat": "AI 对话记忆"}

RELATION_STYLE = {
    "derives": {"color": "#8E44AD", "dashes": False, "width": 2},
    "assumes": {"color": "#8E44AD", "dashes": [2, 4], "width": 1.5},
    "co_occurs": {"color": "#8E44AD", "dashes": [2, 4], "width": 2},
    "reinforces": {"color": "#D35400", "dashes": False, "width": 2},
    "conflicts_with": {"color": "#C0392B", "dashes": [6, 3], "width": 3},
    "manifested_in": {"color": "#95a5a6", "dashes": False, "width": 1},
    "originates": {"color": "#2980B9", "dashes": False, "width": 1.5},
    "triggers": {"color": "#2980B9", "dashes": [2, 4], "width": 1.5},
    "unmet": {"color": "#16A085", "dashes": False, "width": 1.5},
    "activates": {"color": "#E67E22", "dashes": [4, 2], "width": 1.5},
    "produces": {"color": "#B7791F", "dashes": False, "width": 1},
    "evokes": {"color": "#EB5C8A", "dashes": False, "width": 1},
    "regulated_by": {"color": "#E0574B", "dashes": [2, 4], "width": 1},
    "relates_to": {"color": "#16A085", "dashes": [4, 2], "width": 1.5},
}
RELATION_LABEL = dict(RELATION_TYPES)

with st.sidebar:
    st.subheader("图例")
    # 用真实 hex 颜色渲染图例圆点（streamlit 内建彩色文本只支持有限几个色名，覆盖不了 10 类）。
    for t in TYPE_STYLE:
        st.markdown(
            f"<span style='color:{TYPE_STYLE[t]['color']};font-size:1.1em'>●</span> {TYPE_LABEL[t]}",
            unsafe_allow_html=True,
        )
    st.caption("节点大小 = 度中心性（连接越多越大）；虚线边框 = 来自 AI 对话记忆")
    st.divider()
    st.caption("连线：紫=图式派生/信念/共现　橙=触发/激活　红=相互强化　红虚线=彼此冲突　灰=体现于事件　蓝=人物　青=核心需要/呼应　粉=情绪")

    # 粒度变细后节点很多；默认显示"驱动层"，实例层（触发情境/自动思维/情绪/事件）按需勾选。
    _DEFAULT_TYPES = ["need", "person", "schema", "belief", "mode", "coping"]
    show_types = st.multiselect(
        "只显示这些类型的节点",
        options=list(TYPE_STYLE),
        default=[t for t in _DEFAULT_TYPES if t in TYPE_STYLE] or list(TYPE_STYLE),
        format_func=lambda t: TYPE_LABEL[t],
    )
    min_centrality = st.slider(
        "只显示中心性 ≥（拖高可聚焦'根源驱动'、减少杂讯）", 0.0, 0.30, 0.0, 0.01
    )
    show_sources = st.multiselect(
        "只显示这些来源的节点",
        options=["therapy", "chat"],
        default=["therapy", "chat"],
        format_func=lambda s: SOURCE_LABEL[s],
    )
    layout_mode = st.radio("布局", options=["力导向（mindmap）", "层级（根源在上）"], index=0)

    st.divider()
    if st.button("🔄 重新生成【真实咨询】图谱（调 Gemini，较贵，几十秒到几分钟）"):
        with st.spinner("正在重新提炼核心图式/应对模式/事件（处理全部咨询摘要）…"):
            from scripts.build_graph import build_graph

            new_graph = build_graph()
            GRAPH_JSON_PATH.write_text(json.dumps(new_graph, ensure_ascii=False, indent=2), encoding="utf-8")
        st.success(f"已更新：{len(new_graph['nodes'])} 个节点，{len(new_graph['edges'])} 条关系")
        st.rerun()

    if st.button("🔄 重新生成【AI对话记忆】图谱（便宜，只处理聊天记录）"):
        with st.spinner("正在从聊天记录提炼新节点…"):
            from scripts.build_chat_graph import build_chat_graph

            new_chat_graph = build_chat_graph()
            CHAT_GRAPH_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
            CHAT_GRAPH_JSON_PATH.write_text(json.dumps(new_chat_graph, ensure_ascii=False, indent=2), encoding="utf-8")
        st.success(f"已更新：{len(new_chat_graph['nodes'])} 个新节点，{len(new_chat_graph['edges'])} 条关系")
        st.rerun()

visible_ids = {
    n["id"]
    for n in graph["nodes"]
    if n["type"] in show_types
    and n.get("source", "therapy") in show_sources
    and n.get("degree_centrality", 0.0) >= min_centrality
}

net = Network(height="680px", width="100%", bgcolor="#ffffff", font_color="#222222", directed=True, cdn_resources="remote")

for n in graph["nodes"]:
    if n["id"] not in visible_ids:
        continue
    style = TYPE_STYLE[n["type"]]
    centrality = n.get("degree_centrality", 0.0)
    size = style["base_size"] + centrality * 60
    dates = "、".join(n.get("related_dates", [])) or "无"
    domain_line = f"图式领域：{n['domain']}\n" if n.get("domain") else ""
    source_label = SOURCE_LABEL.get(n.get("source", "therapy"), "真实咨询")
    title = (
        f"[{TYPE_LABEL[n['type']]}｜来自{source_label}] {n['label']}\n{domain_line}{n['description']}\n"
        f"度中心性：{centrality}\n相关日期：{dates}"
    )
    node_kwargs = {
        "label": n["label"],
        "title": title,
        "color": style["color"],
        "size": size,
        "shapeProperties": {"borderDashes": n.get("source") == "chat"},
        "borderWidth": 3 if n.get("source") == "chat" else 1,
    }
    if layout_mode.startswith("层级"):
        node_kwargs["level"] = style["level"]
    net.add_node(n["id"], **node_kwargs)

for e in graph["edges"]:
    if e["source"] not in visible_ids or e["target"] not in visible_ids:
        continue
    rstyle = RELATION_STYLE.get(e["relation_type"], {"color": "#a0a0a0", "dashes": False, "width": 1})
    dates = "、".join(e.get("evidence_dates", [])) or "无"
    title = f"[{RELATION_LABEL.get(e['relation_type'], e['relation_type'])}] {e['relation']}\n依据日期：{dates}"
    net.add_edge(
        e["source"],
        e["target"],
        title=title,
        color=rstyle["color"],
        dashes=rstyle["dashes"],
        width=rstyle["width"],
    )

if layout_mode.startswith("层级"):
    net.set_options(
        """
        {
          "interaction": {"hover": true, "tooltipDelay": 100},
          "layout": {
            "hierarchical": {
              "enabled": true,
              "direction": "UD",
              "sortMethod": "directed",
              "levelSeparation": 160,
              "nodeSpacing": 140,
              "treeSpacing": 200
            }
          },
          "physics": {"hierarchicalRepulsion": {"nodeDistance": 140}, "solver": "hierarchicalRepulsion"}
        }
        """
    )
else:
    net.barnes_hut(gravity=-4000, spring_length=140)
    net.set_options(
        """
        {
          "interaction": {"hover": true, "tooltipDelay": 100},
          "physics": {"stabilization": {"iterations": 150}}
        }
        """
    )

components.html(net.generate_html(), height=700, scrolling=False)

st.divider()
st.subheader("按节点查看详情")
labeled = {
    n["id"]: f"[{TYPE_LABEL[n['type']]}｜{SOURCE_LABEL.get(n.get('source', 'therapy'))}] {n['label']}"
    for n in graph["nodes"]
    if n["id"] in visible_ids
}
sorted_ids = sorted(labeled, key=lambda k: -nodes_by_id[k].get("degree_centrality", 0))
selected = st.selectbox("选择一个节点（默认按中心性从高到低排序）", options=sorted_ids, format_func=lambda k: labeled[k])

if selected:
    node = nodes_by_id[selected]
    st.markdown(f"### {node['label']}（{TYPE_LABEL[node['type']]}｜{SOURCE_LABEL.get(node.get('source', 'therapy'))}）")
    if node.get("domain"):
        st.caption(f"Schema Therapy 领域：{node['domain']}")
    st.write(node["description"])
    st.caption(
        f"度中心性：{node.get('degree_centrality', 0)}　"
        f"介数中心性：{node.get('betweenness_centrality', 0)}　"
        f"相关日期：{'、'.join(node.get('related_dates', []))}"
    )

    related_edges = [e for e in graph["edges"] if e["source"] == selected or e["target"] == selected]
    if related_edges:
        st.markdown("**相关连接：**")
        for e in related_edges:
            other_id = e["target"] if e["source"] == selected else e["source"]
            other = nodes_by_id.get(other_id, {"label": other_id})
            evidence = "、".join(e.get("evidence_dates", []))
            rel_label = RELATION_LABEL.get(e["relation_type"], e["relation_type"])
            arrow = "→" if e["source"] == selected else "←"
            st.markdown(f"- {arrow} **{other['label']}**［{rel_label}］：{e['relation']}（{evidence}）")
