"""构建"AI 对话记忆"的心智地图（data/chat_graph.json），随 update_chat_memory 一起自动更新。

刻意设计成"便宜"：喂给 Gemini 的参考资料是已有的 data/graph.json（真实咨询图谱，只有几 KB，
只读、不重新生成），而不是重新处理全部咨询摘要——所以这一步的 token 成本只取决于聊天记录量，
和咨询语料库大小（53+ 份逐字稿）完全无关，多跑几次也不会变贵。

节点沿用和 build_graph.py 相同的四类分类法（schema/coping/event/person），但 id 一律带
"chat:" 前缀，避免和真实咨询图谱撞车；如果某个聊天话题其实就是已有咨询图谱里的某个节点
（比如又聊到"讨好与冲突回避"），就生成一条 relates_to 类型的边，target 直接引用那个已有节点的
原始 id，而不是重复建一个新节点——这样两张图在被合并展示时能自然连通（见 scripts/graph_utils.py）。

产物：data/chat_graph.json，不单独展示，只在可视化页面/ask.py 里和 data/graph.json 合并使用。

支持 workspace：所有函数支持 workspace_id 参数。
"""
import json
from typing import Optional

from config import CHAT_GRAPH_JSON_PATH, GRAPH_JSON_PATH
from scripts.graph_utils import compute_centrality
from scripts.llm import ask_llm
from scripts.settings import summary_max_tokens
from scripts.update_chat_memory import _format_sessions, load_chat_sessions

CHAT_GRAPH_SCHEMA = {
    "type": "object",
    "properties": {
        "nodes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "必须以 'chat:' 开头，如 chat:coping:讨好模式"},
                    "type": {"type": "string", "enum": ["schema", "coping", "event", "person"]},
                    "label": {"type": "string"},
                    "domain": {"type": "string", "description": "仅 type=schema 时填写 Schema Therapy 领域，其他留空"},
                    "description": {"type": "string"},
                    "related_dates": {"type": "array", "items": {"type": "string"}, "description": "聊天发生的日期 YYYY-MM-DD"},
                },
                "required": ["id", "type", "label", "domain", "description", "related_dates"],
            },
        },
        "edges": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "target": {"type": "string"},
                    "relation_type": {
                        "type": "string",
                        "enum": [
                            "derives", "co_occurs", "reinforces", "conflicts_with",
                            "manifested_in", "originates", "triggers", "relates_to",
                        ],
                    },
                    "relation": {"type": "string"},
                    "evidence_dates": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["source", "target", "relation_type", "relation", "evidence_dates"],
            },
        },
    },
    "required": ["nodes", "edges"],
}


def _existing_nodes_reference(therapy_graph: dict | None) -> str:
    if not therapy_graph or not therapy_graph.get("nodes"):
        return "（目前还没有真实咨询的心智地图。）"
    lines = [f"- {n['id']}：{n['label']}（{n['description']}）" for n in therapy_graph["nodes"]]
    return "\n".join(lines)


def _system_instruction(therapy_graph: dict | None) -> str:
    return f"""\
你是心理咨询记录的知识图谱整理助手。使用者有一份"真实咨询"心智地图（下面列出了里面所有的\
节点供你参考，你**不能重复创建**这些已有节点），现在请你从使用者和 AI 助手的聊天记录里，\
提炼出**新的**知识图谱节点/关系。这些聊天不是真实咨询记录，只是使用者的日常反思和探讨。

## 已有的真实咨询图谱节点（只读参考，不要重复创建，可以在边里引用它们的 id）
{_existing_nodes_reference(therapy_graph)}

## 你的任务
从聊天记录里提炼：
1. **新节点**（id 必须以 "chat:" 开头，如 "chat:coping:xxx"）：只有当聊天里出现了上面列表\
里明显没有的图式/应对模式/事件/人物时才创建，类型分类和字段含义与上面的参考节点一致\
（schema=核心图式/根源信念，coping=应对模式，event=关键事件，person=人物）。如果聊天量少，\
新节点也可以很少甚至没有，不要为了凑数硬编。
2. **relates_to 边**：如果聊天里的某个话题，其实就是上面参考列表里已经有的某个节点\
（哪怕聊天里换了个说法），生成一条 relation_type="relates_to" 的边，target 直接写\
**那个已有节点的原始 id**（一字不改），relation 字段说明具体是怎么呼应上的。
3. 其他 relation_type（derives/co_occurs/reinforces/conflicts_with/manifested_in/\
originates/triggers）用来连接你新建的节点之间的关系，规则同真实咨询图谱。

## 严格要求
- 每个新节点和边都要给 related_dates / evidence_dates，且必须是聊天记录里真实出现过的\
日期（YYYY-MM-DD），不能编造；
- 不要编造聊天记录里没出现过的内容；
- 如果聊天记录本身很少或者没有新东西可提炼，nodes 和 edges 都可以返回空数组，不要硬凑。
"""


def _load_therapy_graph(workspace_id: Optional[str] = None) -> dict | None:
    """加载真实咨询图谱（workspace 感知）。"""
    graph_path = GRAPH_JSON_PATH(workspace_id)
    if not graph_path.exists():
        return None
    return json.loads(graph_path.read_text(encoding="utf-8"))


def build_chat_graph(sessions: list[dict] | None = None, therapy_graph: dict | None = None, workspace_id: Optional[str] = None) -> dict:
    """构建 AI 对话图谱（workspace 感知）。"""
    sessions = sessions if sessions is not None else load_chat_sessions(workspace_id)
    therapy_graph = therapy_graph if therapy_graph is not None else _load_therapy_graph(workspace_id)

    if not sessions:
        return {"nodes": [], "edges": []}

    valid_dates = set()
    for s in sessions:
        updated_at = s.get("updated_at", "")
        if updated_at:
            valid_dates.add(updated_at[:10])

    resp = ask_llm(
        _format_sessions(sessions),
        profile="summary",
        system_instruction=_system_instruction(therapy_graph),
        response_schema=CHAT_GRAPH_SCHEMA,
        max_output_tokens=summary_max_tokens("chat_graph"),
    )
    graph = json.loads(resp.text)

    # 校验：新节点 id 必须带 chat: 前缀（防止误创建和真实图谱撞名的节点）
    graph["nodes"] = [n for n in graph["nodes"] if n["id"].startswith("chat:")]
    valid_ids = {n["id"] for n in graph["nodes"]} | {n["id"] for n in (therapy_graph or {}).get("nodes", [])}

    dropped_dates = []
    for n in graph["nodes"]:
        before = n["related_dates"]
        n["related_dates"] = [d for d in before if d in valid_dates]
        dropped_dates += [d for d in before if d not in valid_dates]

    good_edges = []
    for e in graph["edges"]:
        if e["source"] not in valid_ids or e["target"] not in valid_ids:
            continue
        before = e["evidence_dates"]
        e["evidence_dates"] = [d for d in before if d in valid_dates]
        dropped_dates += [d for d in before if d not in valid_dates]
        good_edges.append(e)
    graph["edges"] = good_edges

    if dropped_dates:
        print(f"警告：过滤掉 {len(dropped_dates)} 个不存在于聊天记录的编造日期: {sorted(set(dropped_dates))}")

    for n in graph["nodes"]:
        n["source"] = "chat"

    compute_centrality(graph)
    return graph


if __name__ == "__main__":
    graph = build_chat_graph()
    chat_graph_path = CHAT_GRAPH_JSON_PATH()
    chat_graph_path.parent.mkdir(parents=True, exist_ok=True)
    chat_graph_path.write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")

    by_relation = {}
    for e in graph["edges"]:
        by_relation[e["relation_type"]] = by_relation.get(e["relation_type"], 0) + 1

    print(f"节点数: {len(graph['nodes'])}，边数: {len(graph['edges'])} {by_relation}")
    print(f"已写入 {chat_graph_path}")
