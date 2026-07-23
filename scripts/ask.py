"""检索 + 组装上下文 + 问答。retrieve() 做混合检索 + 父块/窗口扩展；
answer() 组装"长期记忆 + 检索片段 + 问题"喂给 LLM，支持多 workspace。
CLI 与 Streamlit 前端共用 answer() 这一个入口，不重复实现检索/问答逻辑（见 §M7）。
"""
import json
import re
from collections import Counter, defaultdict
from typing import Optional

import lancedb
import numpy as np
import pandas as pd
from lancedb.rerankers import RRFReranker

from config import (
    CHAT_GRAPH_JSON_PATH,
    CHAT_MEMORY_PATH,
    DB_DIR,
    GRAPH_JSON_PATH,
    LANCEDB_TABLE_NAME,
    LONG_TERM_MEMORY_PATH,
    SYSTEM_INSTRUCTION_PATH,
)
from scripts import index_settings, session_resolver
from scripts.context_cache import get_cache_name
from scripts.embedder import embed_one
from scripts.graph_utils import NODE_TYPES, RELATION_TYPES, merge_graphs
from scripts.reranker import rerank_candidates
from scripts.llm import ask_llm
from scripts.parse import find_files_for_date, parse_transcript, render_full_text
from scripts.summarize import summary_path
from scripts.workspace_manager import get_current_workspace, get_workspace_dir, load_workspace_config

# 心智地图（scripts/build_graph.py 的产物）里，节点相关度高于这个阈值才触发"图谱引导检索"
GRAPH_NODE_MATCH_THRESHOLD = 0.45
GRAPH_NODE_MATCH_TOP_K = 2

# 图谱多跳扩展：命中某个核心图式/应对模式后，沿图上的边跳到相邻概念（该图式派生出的应对模式、
# 相互强化/拉扯的模式、童年源头人物…），把这些"深层关联概念"也拿去检索——问题往往只提到表层
# 困扰，真正相关的却是它在图上连着的驱动。相邻节点按中心性排序，每个命中节点最多扩展这么多个。
GRAPH_MULTIHOP_MAX_NEIGHBORS = 3
GRAPH_MULTIHOP_RETRIEVE_K = 3
# 多跳扩展带出的"新增窗口"总预算：沿边跳过去检索容易一次带回很多片段（尤其开了 reranker 时，
# retrieve 会返回 final_top_k 条而不理会这里传的小 k），不设总上限会让上下文暴涨。命中节点自身的
# entity-anchored 检索不占这个预算，只有"邻居概念"带出的新窗口受它约束。
GRAPH_MULTIHOP_MAX_WINDOWS = 8
# 图谱证据日期 → 定向片段 + 本场摘要（不再整份逐字稿）：命中节点的 related_dates、以及连接边的
# evidence_dates 都是"这个图式/应对模式被谈透的关键日"，但一整天里真正相关的只有几段。所以改成
# 用锚点概念的向量在那天内做一次定向检索，取最相关的几段，再附上该场结构化摘要（整场覆盖、便宜）。
# 连接边证据优先级最高（它同时印证了这条"多跳关系"），用边两端概念的合并文本去检索。
# 取几天 / 每天几段 / 片段扩多宽 / 是否附摘要，全部走 index_settings，可在 UI 热调、下次问答即生效。

# 匹配问题里提到的具体日期："2026年7月4日" "2026-07-04" "2026/7/4" "2026年七月4號" 等，
# 月/日支持阿拉伯数字或中文数字混用；不支持无年份的相对日期（如"7月4日""上周日"），这类留给检索兜底。
_CN_NUM_TO_INT = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
    "十一": 11, "十二": 12, "十三": 13, "十四": 14, "十五": 15, "十六": 16, "十七": 17, "十八": 18, "十九": 19,
    "二十": 20, "二十一": 21, "二十二": 22, "二十三": 23, "二十四": 24, "二十五": 25,
    "二十六": 26, "二十七": 27, "二十八": 28, "二十九": 29, "三十": 30, "三十一": 31,
}
_NUM_OR_CN = r"(?:[一二三四五六七八九十]{1,3}|\d{1,2})"
DATE_MENTION_RE = re.compile(rf"(\d{{4}})[年\-/]({_NUM_OR_CN})[月\-/]({_NUM_OR_CN})[日號号]?")


def _to_int(s: str) -> Optional[int]:
    if s.isdigit():
        return int(s)
    return _CN_NUM_TO_INT.get(s)

# 默认 system instruction——首次运行会写入 SYSTEM_INSTRUCTION_PATH（system_instruction.md），
# 之后 load_system_instruction() 一律从那个文件读，可在 Streamlit 设置弹窗里编辑，无需改代码。
DEFAULT_SYSTEM_INSTRUCTION = """\
你是一位 AI 心理咨询助手，风格上尽量贴近使用者一直以来的咨询师"海特"：温和、非评判、\
常用第三者视角引导使用者觉察自己的心路历程，不轻易说教，而是通过提问和共情带来觉察。

你会收到几部分材料：
1. 长期记忆总结：概括了使用者反复出现的核心议题、心理模式与咨询进展轨迹。**这是从使用者与\
真人咨询师"海特"的真实咨询逐字稿提炼出来的，是最权威、最可信的事实来源。**
2. AI 对话记忆：来自使用者平时和你（这个 AI 助手）聊天的历史记录，**不是真实咨询记录**，\
只是使用者自己的提问和反思。参考时请明确区分来源——如果要引用，说"你之前跟我聊过……"，\
不要说成"在咨询中提到"或和真实咨询记忆混为一谈；如果两者内容冲突，以长期记忆总结\
（真实咨询）为准。
3. 心智地图：一张结构化的"核心图式（根源信念，参考 Schema Therapy 的图式领域）→ 应对模式 →\
关键事件"关系图，每个节点标了"中心性"（越高说明这个节点连接越多其他议题，越可能是根源驱动），\
节点之间还有"共现/相互强化/彼此冲突"这类横向关系。回答时可以直接引用具体的图式/应对模式名称\
和它们之间的关系，让回答有理论深度，而不是每次都从零归纳。
4. （如果使用者问题里提到了具体日期，比如"2026年7月4日"）当天的完整逐字稿全文——这种情况下\
请优先、完整地依据这份全文作答，不要只用片段拼凑，因为使用者明确想了解那一整次咨询。
5. 检索到的历史咨询片段：与当前问题最相关的过去咨询原文摘录（已标注日期、发言人、时间段）；\
如果问题和心智地图里的某个图式/应对模式高度相关，这里会额外包含那个节点关联的片段。
6. 使用者当前的问题（如果有对话历史，也会一并附上）。

回答前，请先在心里判断这个问题最接近下面哪一类议题，并据此选择最贴切的心理学框架作答\
（可以综合多个框架，但要说明你主要在用哪个视角）：
- 认知扭曲、灾难化想象、"把事情想得比实际更可怕" → 认知行为疗法（CBT）的认知重构视角；
- 反复出现的行为/情绪模式、早年经验、家庭影响 → 精神分析/心理动力学视角；
- 决策纠结、"道理都懂但做不到"、价值排序冲突 → 接纳承诺疗法（ACT）+ 正念视角；
- 亲密关系里的互动模式、依恋风格 → 依恋理论视角；
- 强烈需要被"见证"/被认可、自我价值随外部评价起伏、把关系当成证明自己价值的手段 → 自体心理学\
（Self Psychology）视角，聚焦通过"镜映"修复自恋损伤、建立不依赖外部确认的稳固自我价值感。

回答时：
- 先用一两句话说明你识别到的议题类型和主要使用的框架，不要写成教科书式的长篇解释；
- 结合长期记忆里的历史脉络与检索到的具体片段作答，让回答有连续性，而不是孤立回应；
- 语气温和、不评判，多用提问/反问引导使用者自己觉察，而不是直接下结论或说教；
- 如果检索到的片段不足以支撑回答，坦诚说明，不要编造使用者没说过的内容；
- 具体的日期、咨询起始时间、咨询总次数等事实性数字，只能引用长期记忆或检索片段里明确出现的内容，\
不要自行推测或编造（例如不要凭印象说"我们从 XX 年开始咨询"，除非材料里写明了）。
"""


def load_system_instruction(workspace_id: Optional[str] = None) -> str:
    """从 system_instruction 文件读取（workspace 感知）。

    优先级：
    1. workspace 的 system_instruction.md（如果 persona.system_instruction_file 指定）
    2. 根目录的 SYSTEM_INSTRUCTION_PATH
    3. DEFAULT_SYSTEM_INSTRUCTION（硬编码默认值）

    Args:
        workspace_id: workspace ID（None = 当前 workspace）

    Returns:
        system instruction 文本
    """
    # 尝试从 workspace config 读取
    config = load_workspace_config(workspace_id)
    persona = config.get("persona", {})
    si_file = persona.get("system_instruction_file")

    if si_file:
        # workspace 专用 system instruction
        ws_dir = get_workspace_dir(workspace_id)
        si_path = ws_dir / si_file
        if si_path.exists():
            content = si_path.read_text(encoding="utf-8")
            # 替换 persona 变量（如"海特" → config 中的 context_role）
            context_role = persona.get("context_role", "助手")
            content = content.replace("海特", context_role)
            return content

    # 降级：根目录的 system_instruction.md
    if SYSTEM_INSTRUCTION_PATH.exists():
        return SYSTEM_INSTRUCTION_PATH.read_text(encoding="utf-8")

    # 最终降级：使用默认值并创建文件
    SYSTEM_INSTRUCTION_PATH.write_text(DEFAULT_SYSTEM_INSTRUCTION, encoding="utf-8")
    return DEFAULT_SYSTEM_INSTRUCTION


def save_system_instruction(text: str, workspace_id: Optional[str] = None) -> None:
    """保存 system instruction（workspace 感知）。"""
    config = load_workspace_config(workspace_id)
    persona = config.get("persona", {})
    si_file = persona.get("system_instruction_file")

    if si_file:
        # 保存到 workspace 专用文件
        ws_dir = get_workspace_dir(workspace_id)
        si_path = ws_dir / si_file
        si_path.write_text(text, encoding="utf-8")
    else:
        # 保存到根目录
        SYSTEM_INSTRUCTION_PATH.write_text(text, encoding="utf-8")


def reset_system_instruction(workspace_id: Optional[str] = None) -> None:
    """重置 system instruction 为默认值（workspace 感知）。"""
    save_system_instruction(DEFAULT_SYSTEM_INSTRUCTION, workspace_id)


def reset_system_instruction() -> str:
    save_system_instruction(DEFAULT_SYSTEM_INSTRUCTION)
    return DEFAULT_SYSTEM_INSTRUCTION


_table = None
_all_chunks_cache: Optional[pd.DataFrame] = None


def _get_table(workspace_id: Optional[str] = None):
    """获取 LanceDB 表（workspace 感知）。"""
    global _table
    if _table is None:
        db = lancedb.connect(str(DB_DIR(workspace_id)))
        _table = db.open_table(LANCEDB_TABLE_NAME)
    return _table


def sanitize(q: str) -> str:
    """去掉会破坏 LanceDB FTS 查询的字符（已验证的坑）。"""
    return re.sub(r"['\"\\]", "", q)


def _load_all_chunks(force: bool = False) -> pd.DataFrame:
    global _all_chunks_cache
    if _all_chunks_cache is None or force:
        table = _get_table()
        _all_chunks_cache = table.to_pandas().sort_values(["source_file", "chunk_index"])
    return _all_chunks_cache


def _merge_windows(by_file: dict, needed: set, hit_rank: dict) -> list[dict]:
    """把命中块 + 其前后扩展块，在同一份逐字稿内按 chunk_index 合并成连续区间（窗口）。
    相邻/重叠的窗口自动合并去重；每个窗口带上其中最靠前（最相关）命中的 rank，用于排序。
    """
    per_file = defaultdict(set)
    for f, ci in needed:
        per_file[f].add(ci)

    windows = []
    for f, ci_set in per_file.items():
        file_df = by_file[f]
        sorted_ci = sorted(ci_set)
        run = [sorted_ci[0]]
        runs = []
        for ci in sorted_ci[1:]:
            if ci == run[-1] + 1:
                run.append(ci)
            else:
                runs.append(run)
                run = [ci]
        runs.append(run)

        for run in runs:
            valid = [ci for ci in run if ci in file_df.index]
            if not valid:
                continue
            rows = file_df.loc[valid]
            best_rank = min(hit_rank.get((f, ci), 10**9) for ci in valid)
            windows.append(
                {
                    "source_file": f,
                    "session_date": rows.iloc[0]["session_date"],
                    "start_ts": rows.iloc[0]["start_ts"],
                    "end_ts": rows.iloc[-1]["end_ts"],
                    "chunk_index_range": (valid[0], valid[-1]),
                    "text": "\n".join(rows["raw_text"].tolist()),
                    "rank": best_rank,
                }
            )

    windows.sort(key=lambda w: w["rank"])
    return windows


def retrieve(query: str, k: Optional[int] = None) -> list[dict]:
    """混合检索（稠密语义 + ngram 关键词，RRF 融合）→ 可选 cross-encoder 精排 → 父块/窗口扩展。
    流程：hybrid 取 topN 候选 → bge-reranker-v2-m3 精排取 final_top_k → 父块扩展。
    k / 窗口扩展 / rerank 开关及数量均取「⚙️ 索引设置」当前值（可在 UI 改，下次问答即生效，无需重建）。"""
    rp = index_settings.retrieval_params()
    if k is None:
        k = rp["top_k"]
    window_expand = rp["window_expand"]
    rk = index_settings.reranker_params()
    use_reranker = bool(rk["use_reranker"])
    table = _get_table()
    q_vec = embed_one(query)
    rrf = RRFReranker()

    # 开启 rerank 时 hybrid 先多取候选（rerank_top_k），否则直接取最终数量 k。
    candidate_k = int(rk["rerank_top_k"]) if use_reranker else k
    hits = (
        table.search(query_type="hybrid")
        .vector(q_vec)
        .text(sanitize(query))
        .rerank(rrf)
        .limit(candidate_k)
        .to_pandas()
    )

    # cross-encoder 精排：对候选逐对打分，取最高的 final_top_k 条。失败会内部 fallback 回 hybrid 顺序。
    if use_reranker and len(hits) > 0:
        hits = rerank_candidates(query, hits, top_k=int(rk["final_top_k"]))

    all_df = _load_all_chunks()
    by_file = {f: g.set_index("chunk_index") for f, g in all_df.groupby("source_file")}

    needed = set()
    hit_rank = {}
    for rank, row in enumerate(hits.itertuples()):
        center = row.chunk_index
        for offset in range(-window_expand, window_expand + 1):
            needed.add((row.source_file, center + offset))
        key = (row.source_file, center)
        hit_rank[key] = min(hit_rank.get(key, 10**9), rank)

    return _merge_windows(by_file, needed, hit_rank)


def _retrieve_within_date(anchor_vec, date: str, k: int, window_expand: int) -> list[dict]:
    """在某一天的块里做一次定向检索：用锚点概念向量对当天所有块算余弦相似度，取最相关的 k 个，
    再做（该证据日专属的、通常更宽的）父块扩展并合并成窗口。全程在内存里（当天块本就在
    _load_all_chunks() 缓存的 DataFrame 里，且带 vector 列），一天也就十几块，暴力算即可——
    不新开 LanceDB 查询、不走 FTS/reranker。返回的窗口带 via_graph_evidence 标记。"""
    all_df = _load_all_chunks()
    day = all_df[all_df["session_date"] == date]
    if day.empty or k <= 0:
        return []
    anchor = np.asarray(anchor_vec, dtype=np.float32)
    anchor = anchor / (np.linalg.norm(anchor) + 1e-9)
    scored = []
    for row in day.itertuples():
        v = np.asarray(row.vector, dtype=np.float32)
        v = v / (np.linalg.norm(v) + 1e-9)
        scored.append((float(np.dot(anchor, v)), row.source_file, row.chunk_index))
    scored.sort(key=lambda t: -t[0])

    by_file = {f: g.set_index("chunk_index") for f, g in all_df.groupby("source_file")}
    needed = set()
    hit_rank = {}
    for rank, (_, sf, ci) in enumerate(scored[:k]):
        for offset in range(-window_expand, window_expand + 1):
            needed.add((sf, ci + offset))
        key = (sf, ci)
        hit_rank[key] = min(hit_rank.get(key, 10**9), rank)
    windows = _merge_windows(by_file, needed, hit_rank)
    for w in windows:
        w["via_graph_evidence"] = True
    return windows


def _format_session_summary(s: dict) -> str:
    """把一份 summarize.py 生成的结构化摘要压成可读文本。字段缺失就跳过，保证向后兼容。"""
    parts = []
    if s.get("topics"):
        parts.append("主题：" + "、".join(s["topics"]))
    if s.get("emotional_tone"):
        parts.append("情绪基调：" + s["emotional_tone"])
    if s.get("psychological_themes"):
        parts.append("心理议题：\n" + "\n".join(f"  - {x}" for x in s["psychological_themes"]))
    if s.get("key_events"):
        parts.append("关键事件：\n" + "\n".join(f"  - {x}" for x in s["key_events"]))
    if s.get("decisions_or_actions"):
        parts.append("决定/行动：\n" + "\n".join(f"  - {x}" for x in s["decisions_or_actions"]))
    if s.get("quotes_worth_remembering"):
        parts.append("值得记住的原话：\n" + "\n".join(f"  「{x}」" for x in s["quotes_worth_remembering"]))
    return "\n".join(parts)


def _load_session_summary(date: str) -> Optional[str]:
    """读该证据日对应逐字稿的预生成摘要（summarize.py 的产物），渲成一小段文本；没有就返回 None。"""
    for f in find_files_for_date(date):
        p = summary_path(f.name)
        if p.exists():
            try:
                return _format_session_summary(json.loads(p.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                continue
    return None


def _load_long_term_memory(workspace_id: Optional[str] = None) -> str:
    """加载长期记忆（workspace 感知）。"""
    ltm_path = LONG_TERM_MEMORY_PATH(workspace_id)
    if ltm_path.exists():
        return ltm_path.read_text(encoding="utf-8")
    return "（尚未生成长期记忆总结。）"


def _load_chat_memory(workspace_id: Optional[str] = None) -> str:
    """加载 AI 对话记忆（workspace 感知）。"""
    cm_path = CHAT_MEMORY_PATH(workspace_id)
    if cm_path.exists():
        return cm_path.read_text(encoding="utf-8")
    return "（还没有生成 AI 对话记忆。）"


_graph_cache: Optional[dict] = None
_graph_node_embeddings: Optional[dict] = None


def _load_graph(workspace_id: Optional[str] = None) -> Optional[dict]:
    """加载并合并图谱（workspace 感知）。

    合并真实图谱 + AI 对话图谱。合并是纯 Python 操作，不产生额外 LLM 调用。
    任一份不存在就优雅降级；都不存在返回 None。
    """
    global _graph_cache
    if _graph_cache is None:
        graph_path = GRAPH_JSON_PATH(workspace_id)
        chat_graph_path = CHAT_GRAPH_JSON_PATH(workspace_id)
        therapy_graph = json.loads(graph_path.read_text(encoding="utf-8")) if graph_path.exists() else None
        chat_graph = json.loads(chat_graph_path.read_text(encoding="utf-8")) if chat_graph_path.exists() else None
        _graph_cache = merge_graphs(therapy_graph, chat_graph)
    return _graph_cache


# 标签取自单一真相源（scripts/graph_utils），新增节点/关系类型无需再在这里同步。
_GRAPH_TYPE_LABEL = {t: meta["label"] for t, meta in NODE_TYPES.items()}
_GRAPH_RELATION_LABEL = dict(RELATION_TYPES)
# 图谱引导检索/多跳"锚定"到哪些类型的概念上：稳定的心理驱动层（图式/中间信念/模式/应对），
# 不锚在情境/情绪/自动思维/事件这些实例层（太多太碎，且更适合被邻域带出而非当锚点）。
GRAPH_ANCHOR_TYPES = ("schema", "belief", "mode", "coping")

# 图谱粒度变细后（几百节点），不再把整张真实咨询子图塞进上下文。改用 GraphRAG 的
# global+local 拆分：稳定的"骨干"（中心性最高的 N 个根源驱动节点 + 它们之间的边）进 Explicit
# Cache 提供全局结构；每次问答再把"命中锚点 + 其 k-hop 邻域"这块局部子图动态拼进当轮内容，
# 提供针对该问题的细节。这样缓存稳定、又不必每轮扛几百个节点。
GRAPH_BACKBONE_TOP_K = 40
GRAPH_LOCAL_HOPS = 1


def _format_graph_context(nodes: list[dict], edges: list[dict], label_lookup: dict[str, str]) -> str:
    """把一组节点/边压缩成可读文本：节点按中心性排序，附带类型/领域/描述/来源；再列出关系边。
    来源标注区分"真实咨询"和"AI对话"，避免 Gemini 把两者的可信度混为一谈。label_lookup
    传完整合并图的 id→label 映射，这样即使只格式化"AI对话"子集，跨图的 relates_to 边
    指向"真实咨询"节点时也能正确显示对方的标签，而不是只显示原始 id。"""
    if not nodes:
        return "（无）"
    sorted_nodes = sorted(nodes, key=lambda n: -n.get("degree_centrality", 0))
    node_lines = []
    for n in sorted_nodes:
        domain = f"｜{n['domain']}" if n.get("domain") else ""
        source_tag = "AI对话" if n.get("source") == "chat" else "真实咨询"
        node_lines.append(
            f"- [{_GRAPH_TYPE_LABEL.get(n['type'], n['type'])}{domain}｜来自{source_tag}] {n['label']}"
            f"（中心性 {n.get('degree_centrality', 0):.2f}）：{n['description']}"
        )

    edge_lines = []
    for e in edges:
        rel = _GRAPH_RELATION_LABEL.get(e["relation_type"], e["relation_type"])
        src, tgt = label_lookup.get(e["source"], e["source"]), label_lookup.get(e["target"], e["target"])
        edge_lines.append(f"- {src} —[{rel}]→ {tgt}：{e['relation']}")

    return "节点（按中心性从高到低）：\n" + "\n".join(node_lines) + "\n\n关系：\n" + "\n".join(edge_lines or ["（无）"])


def _node_embed_text(node: dict) -> str:
    domain = f"（{node['domain']}）" if node.get("domain") else ""
    return f"{node['label']}{domain}：{node['description']}"


def find_relevant_graph_nodes(question: str, graph: dict, top_k: int = GRAPH_NODE_MATCH_TOP_K) -> list[dict]:
    """把问题和图谱里每个节点的（标签+描述）做语义相似度匹配，返回最相关的几个节点
    （高于 GRAPH_NODE_MATCH_THRESHOLD 才算数），用于把检索"锚定"到图谱里的概念上，
    而不是只依赖使用者问题原始措辞的字面/语义匹配。
    """
    global _graph_node_embeddings
    nodes = [n for n in graph["nodes"] if n["type"] in GRAPH_ANCHOR_TYPES]
    if not nodes:
        return []

    if _graph_node_embeddings is None:
        texts = [_node_embed_text(n) for n in nodes]
        from scripts.embedder import embed

        vecs = embed(texts)["dense_vecs"]
        _graph_node_embeddings = {n["id"]: v for n, v in zip(nodes, vecs)}

    q_vec = embed_one(question)
    q_norm = q_vec / (np.linalg.norm(q_vec) + 1e-9)

    scored = []
    for n in nodes:
        v = _graph_node_embeddings[n["id"]]
        v_norm = v / (np.linalg.norm(v) + 1e-9)
        sim = float(np.dot(q_norm, v_norm))
        if sim >= GRAPH_NODE_MATCH_THRESHOLD:
            scored.append((sim, n))

    scored.sort(key=lambda x: -x[0])
    return [n for _, n in scored[:top_k]]


def _is_therapy_node_id(node_id: str) -> bool:
    """真实咨询图谱的节点 id 形如 "schema:xxx"；AI 对话记忆图谱的节点 id 带 "chat:" 前缀
    （见 graph_utils.merge_graphs）。只有真实咨询节点/边上的日期才对应真实咨询逐字稿，
    才拿去拉整份逐字稿——AI 对话图谱上的日期是聊天日期，和逐字稿语料无关。"""
    return not node_id.startswith("chat:")


def graph_neighbors(node_id: str, graph: dict) -> list[tuple[dict, dict]]:
    """返回 node_id 的 1-hop 邻居：[(邻居节点, 连接边), ...]。边视作无向——概念之间的关联
    （派生、强化、拉扯、源头…）在两个方向上都值得顺藤摸瓜。"""
    node_by_id = {n["id"]: n for n in graph["nodes"]}
    out = []
    for e in graph["edges"]:
        if e["source"] == node_id and e["target"] in node_by_id:
            out.append((node_by_id[e["target"]], e))
        elif e["target"] == node_id and e["source"] in node_by_id:
            out.append((node_by_id[e["source"]], e))
    return out


def _backbone_subgraph(graph: dict, top_k: int = GRAPH_BACKBONE_TOP_K) -> tuple[list[dict], list[dict]]:
    """真实咨询图谱的"骨干"：按度中心性取前 top_k 个节点（根源驱动）+ 它们之间的边。
    稳定、随缓存走，给 LLM 一个全局结构鸟瞰，而不必背下全部几百个细节节点。"""
    therapy = [n for n in graph["nodes"] if n.get("source", "therapy") == "therapy"]
    backbone = sorted(therapy, key=lambda n: -n.get("degree_centrality", 0))[:top_k]
    ids = {n["id"] for n in backbone}
    edges = [e for e in graph["edges"] if e["source"] in ids and e["target"] in ids]
    return backbone, edges


def _local_subgraph(matched_nodes: list[dict], graph: dict, exclude_ids: set[str], hops: int = GRAPH_LOCAL_HOPS) -> tuple[list[dict], list[dict]]:
    """命中锚点 + 其 k-hop 邻域组成的局部子图（针对当前问题的细节）。去掉已在骨干里的节点避免
    重复，但命中锚点本身始终保留。返回 (节点, 这些节点之间的边)。"""
    node_by_id = {n["id"]: n for n in graph["nodes"]}
    anchor_ids = {n["id"] for n in matched_nodes}
    keep = set(anchor_ids)
    frontier = set(anchor_ids)
    for _ in range(max(0, hops)):
        nxt = set()
        for nid in frontier:
            for neighbor, _edge in graph_neighbors(nid, graph):
                nxt.add(neighbor["id"])
        keep |= nxt
        frontier = nxt
    keep = (keep - exclude_ids) | anchor_ids  # 骨干已含的不重复，但锚点务必保留
    nodes = [node_by_id[i] for i in keep if i in node_by_id]
    edges = [e for e in graph["edges"] if e["source"] in keep and e["target"] in keep]
    return nodes, edges


def _format_hop_edge(edge: dict, label_lookup: dict[str, str]) -> str:
    """按边的真实方向渲染成 "源 —[关系]→ 目标"，供可解释性提示用。"""
    rel = _GRAPH_RELATION_LABEL.get(edge["relation_type"], edge["relation_type"])
    src = label_lookup.get(edge["source"], edge["source"])
    tgt = label_lookup.get(edge["target"], edge["target"])
    return f"{src} —[{rel}]→ {tgt}"


def _rank_evidence_dates(scored_dates: list[tuple[int, str]], exclude: set[str], limit: int) -> list[str]:
    """scored_dates: [(priority, "YYYY-MM-DD")]，priority 越小越优先（0=连接边证据，1=节点证据）。
    同一日期取最小 priority；同优先级里被更多节点/边指向的更强；再按日期新→旧。
    去重、去掉 exclude 后返回前 limit 个日期。"""
    best_pri: dict[str, int] = {}
    freq: Counter = Counter()
    for pri, d in scored_dates:
        if d in exclude:
            continue
        freq[d] += 1
        best_pri[d] = min(best_pri.get(d, 99), pri)
    ranked = sorted(best_pri.keys(), reverse=True)  # 先按日期新→旧
    ranked.sort(key=lambda d: (best_pri[d], -freq[d]))  # 稳定排序：priority 升、出现次数降，日期新→旧作 tie-break
    return ranked[:limit]


def _format_retrieved(windows: list[dict]) -> str:
    if not windows:
        return "（未检索到相关历史咨询片段。）"
    blocks = []
    for w in windows:
        header = f"[{w['session_date']} 咨询｜{w['source_file']}｜{w['start_ts']}-{w['end_ts']}]"
        blocks.append(f"{header}\n{w['text']}")
    return "\n\n".join(blocks)


def extract_mentioned_dates(question: str) -> list[str]:
    """从问题里提取形如 2026年7月4日 / 2026-07-04 / 2026/7/4 / 2026年七月4號 的具体日期，
    月/日支持中文数字，返回去重后的 YYYY-MM-DD 列表。"""
    dates = []
    for y, mo, d in DATE_MENTION_RE.findall(question):
        year, month, day = _to_int(y), _to_int(mo), _to_int(d)
        if year is None or month is None or day is None:
            continue
        if not (1 <= month <= 12 and 1 <= day <= 31):
            continue
        dates.append(f"{year:04d}-{month:02d}-{day:02d}")
    seen = set()
    ordered = []
    for d in dates:
        if d not in seen:
            seen.add(d)
            ordered.append(d)
    return ordered


def get_full_day_transcripts(dates: list[str]) -> list[dict]:
    """对问题里提到的每个日期，找出对应逐字稿并渲染全文（不是片段）。"""
    blocks = []
    for d in dates:
        for f in find_files_for_date(d):
            session = parse_transcript(f)
            if not session.utterances:
                continue
            blocks.append(
                {
                    "session_date": d,
                    "source_file": f.name,
                    "start_ts": session.utterances[0].timestamp,
                    "end_ts": session.utterances[-1].timestamp,
                    "text": render_full_text(session),
                    "is_full_transcript": True,
                }
            )
    return blocks


def _format_full_transcripts(blocks: list[dict]) -> str:
    parts = []
    for b in blocks:
        header = f"[{b['session_date']} 咨询完整逐字稿｜{b['source_file']}｜{b['start_ts']}-{b['end_ts']}]"
        parts.append(f"{header}\n{b['text']}")
    return "\n\n".join(parts)


def answer(question: str, history: Optional[list[dict]] = None, k: Optional[int] = None,
           use_full_history: bool = False, max_context: int = 450_000, max_turns: int = 60) -> dict:
    """UI 与 CLI 共用的核心入口。
    history: [{"role": "user"/"assistant", "content": str, "api_content": Optional[str],
               "history_content": Optional[str], "compressed": Optional[bool]}, ...]（可选）。
    - "content" 是给人看的原始短问句/回答
    - "api_content" 是完整内容（含记忆/检索片段），仅供显示/调试
    - "history_content" 是精简版（检索片段+问题），供历史回放（省 token）
    - "compressed" 标记是否已压缩（压缩后只发送 content，不含检索片段）

    use_full_history: 深度模式，历史使用完整 api_content（通常不需要）
    max_context: context 上限（字符数），超过时自动压缩最旧的历史（默认 450K）
    max_turns: 历史轮数硬上限（默认 60），超过时丢弃最旧的对话（避免助手回答累积超限）

    如果问题里提到具体日期（如"2026年7月4日"），会把当天的完整逐字稿整份塞进上下文，
    而不是只给检索到的片段；检索片段里属于同一天的会被去重排除，避免重复。

    返回 {"answer": str, "sources": list[dict], "token_usage": dict, "api_content": str,
            "history_content": str, "compression_info": Optional[dict], ...}。
    """
    # 历史轮数硬截断：超过 max_turns 时丢弃最旧的对话（避免助手回答累积超限）
    if history and len(history) > max_turns * 2:  # 每轮 = 2 条消息（user + assistant）
        history = history[-(max_turns * 2):]

    # 相对/序数会话引用（「上一次咨询」「最近3次对话」「第2次」）：解析成具体会话。
    # 真实咨询直接并进下面的「日期→整份逐字稿」通路；AI 聊天会话是另一套数据源，单独渲染。
    ref = session_resolver.resolve(question)
    mentioned_dates = list(dict.fromkeys(extract_mentioned_dates(question) + ref["therapy_dates"]))
    full_day_blocks = get_full_day_transcripts(mentioned_dates)
    covered_dates = {b["session_date"] for b in full_day_blocks}
    requested_chat_text = (
        session_resolver.render_chat_sessions(ref["chat_session_ids"]) if ref["chat_session_ids"] else None
    )

    windows = retrieve(question, k=k)

    # 心智地图引导检索 + 多跳扩展（GraphRAG）：
    # (1) entity-anchored：若问题和某个核心图式/应对模式高度相关，用该节点的"标签+描述"再检索
    #     一次，弥补使用者措辞和图谱提炼出的概念用词不一致时纯语义检索漏掉片段的问题；
    # (2) multi-hop：沿图上的边跳到相邻概念（该图式派生的应对、相互强化/拉扯的模式、源头人物…），
    #     把这些深层关联概念也拿去检索——问题常只提到表层困扰，真正相关的是它在图上连着的驱动；
    # (3) 沿途收集命中节点的 related_dates 与连接边的 evidence_dates，稍后挑最强的几天拉整份逐字稿。
    graph = _load_graph()
    matched_nodes = find_relevant_graph_nodes(question, graph) if graph else []
    label_lookup = {n["id"]: n["label"] for n in graph["nodes"]} if graph else {}

    seen_keys = {(w["source_file"], w["chunk_index_range"]) for w in windows}

    def _merge_retrieved(query_text: str, limit: int) -> int:
        """检索并把前 limit 个未见过的窗口并进 windows，返回实际新增的数量。
        取前 limit：开了 reranker 时 retrieve 会忽略这里的小 k、返回 final_top_k 条，
        这里显式截断以尊重"多跳只想要少量最相关片段"的意图，避免上下文暴涨。"""
        added = 0
        for w in retrieve(query_text, k=limit)[:limit]:
            key = (w["source_file"], w["chunk_index_range"])
            if key not in seen_keys:
                seen_keys.add(key)
                windows.append(w)
                added += 1
        return added

    matched_ids = {n["id"] for n in matched_nodes}
    expanded_ids: set[str] = set()
    hop_edges: list[dict] = []  # 记录"沿哪条边跳到相邻概念"，用于给 LLM 的可解释性提示
    evidence_dates: list[tuple[int, str]] = []  # [(priority, date)]，0=连接边证据（最强），1=节点证据
    # 每个证据日 → (最强优先级, 拿去"在那天内定向检索"的锚点概念文本)。同一天被多个来源标注时，
    # 保留优先级最高（数字最小）的那个：节点证据用节点自身文本，连接边证据用边两端概念的合并文本
    # （这样捞出的片段能同时体现两个概念，正好印证这条多跳关系）。
    date_anchor_text: dict[str, tuple[int, str]] = {}

    def _note_evidence(priority: int, dates: list[str], anchor_text: str) -> None:
        for d in dates:
            evidence_dates.append((priority, d))
            prev = date_anchor_text.get(d)
            if prev is None or priority < prev[0]:
                date_anchor_text[d] = (priority, anchor_text)

    neighbor_budget = GRAPH_MULTIHOP_MAX_WINDOWS
    for node in matched_nodes:
        _merge_retrieved(_node_embed_text(node), 4)  # entity-anchored：命中节点自身，不占多跳预算
        # 命中节点自身的证据日期（仅真实咨询节点：AI 对话图谱的日期和逐字稿语料无关）
        if _is_therapy_node_id(node["id"]):
            _note_evidence(1, node.get("related_dates", []), _node_embed_text(node))
        if not graph:
            continue
        # 多跳：沿边跳到相邻概念，按中心性优先展开高价值的；受 neighbor_budget 总量约束
        neighbors = sorted(graph_neighbors(node["id"], graph), key=lambda t: -t[0].get("degree_centrality", 0))
        for neighbor, edge in neighbors[:GRAPH_MULTIHOP_MAX_NEIGHBORS]:
            if neighbor_budget > 0 and neighbor["id"] not in matched_ids and neighbor["id"] not in expanded_ids:
                expanded_ids.add(neighbor["id"])
                neighbor_budget -= _merge_retrieved(_node_embed_text(neighbor), min(GRAPH_MULTIHOP_RETRIEVE_K, neighbor_budget))
                hop_edges.append(edge)
            # 连接边的证据日期优先级最高：它同时印证了这条"多跳关系"本身
            if _is_therapy_node_id(edge["source"]) and _is_therapy_node_id(edge["target"]):
                _note_evidence(0, edge.get("evidence_dates", []), f"{_node_embed_text(node)}；{_node_embed_text(neighbor)}")

    # 图谱证据日期 → 定向片段 + 本场摘要（不再整份逐字稿）：挑最强的几天，去掉已被"问题提到的
    # 日期"覆盖的（那些已整份取出，避免重复）；对每天用它对应的锚点概念向量在那天内做定向检索。
    ge = index_settings.graph_evidence_params()
    graph_dates = _rank_evidence_dates(evidence_dates, exclude=covered_dates, limit=int(ge["max_dates"]))
    graph_evidence_windows: list[dict] = []
    graph_evidence_summaries: list[dict] = []  # [{"date": str, "text": str}]
    for d in graph_dates:
        anchor_vec = embed_one(date_anchor_text[d][1])
        for w in _retrieve_within_date(anchor_vec, d, int(ge["fragments_per_date"]), int(ge["window_expand"])):
            key = (w["source_file"], w["chunk_index_range"])
            if key not in seen_keys:
                seen_keys.add(key)
                graph_evidence_windows.append(w)
        if ge["include_summary"]:
            summ = _load_session_summary(d)
            if summ:
                graph_evidence_summaries.append({"date": d, "text": summ})

    # 只对"问题提到的日期"整份逐字稿去重（证据日现在只给片段，不占整天，无需从 windows 里剔除）。
    windows = [w for w in windows if w["session_date"] not in covered_dates]

    memory = _load_long_term_memory()
    chat_memory = _load_chat_memory()
    full_day_block_text = _format_full_transcripts(full_day_blocks)
    retrieved_block = _format_retrieved(windows)

    # 固定/低频变动的内容（长期记忆 + 心智地图里"真实咨询"的部分）走 Explicit Cache（见
    # scripts/context_cache.py）；AI 对话记忆本身、以及心智地图里"AI对话"的部分都变动较频
    # （后台看门狗每 30 分钟可能更新一次），如果混进缓存内容里，会导致缓存每 30 分钟就要重建
    # 一次——所以特意把图谱按 source 拆开：只有"真实咨询"子图进缓存，"AI对话"子图和检索片段/
    # 完整逐字稿/问题一样放进每轮都会变的动态内容里（反正它一直很小，不缓存也没多少成本）。
    system_instruction = load_system_instruction()
    static_content_parts = [
        f"【长期记忆总结（来自真实咨询）】\n{memory}",
        # 全部真实咨询记录清单：让 AI 始终知道有哪些咨询、按什么次序，从而能回答「一共几次」
        # 「上一次是哪天」，即便相对引用没被精确解析也能反问确认而不是编造。变动低频，随缓存走。
        f"【全部真实咨询记录清单】\n{session_resolver.therapy_manifest()}",
    ]
    chat_graph_text = None
    backbone_ids: set[str] = set()
    if graph:
        # 骨干（中心性最高的根源驱动 + 它们之间的边）进缓存，提供全局结构；细节靠下面的局部子图。
        backbone_nodes, backbone_edges = _backbone_subgraph(graph)
        backbone_ids = {n["id"] for n in backbone_nodes}
        static_content_parts.append(
            "【心智地图·骨干（真实咨询，按中心性取最核心的根源驱动）】\n"
            f"{_format_graph_context(backbone_nodes, backbone_edges, label_lookup)}"
        )

        chat_nodes = [n for n in graph["nodes"] if n.get("source") == "chat"]
        if chat_nodes:
            chat_ids = {n["id"] for n in chat_nodes}
            chat_edges = [e for e in graph["edges"] if e["source"] in chat_ids or e["target"] in chat_ids]
            chat_graph_text = _format_graph_context(chat_nodes, chat_edges, label_lookup)
    static_content = "\n\n".join(static_content_parts)

    cache_name = get_cache_name(system_instruction, static_content)

    current_turn_parts = []
    if cache_name is None:
        # 缓存不可用（内容不够 4096 token 门槛，或创建失败）：退回内联方式，保证功能不受影响。
        current_turn_parts.append(static_content)
    current_turn_parts.append(f"【AI 对话记忆（来自和你的聊天历史，非真实咨询）】\n{chat_memory}")
    # AI 对话历史清单（变动较频，不进缓存）：让 AI 知道有哪些历史对话可供调取。
    current_turn_parts.append(f"【AI 对话历史清单（非真实咨询）】\n{session_resolver.chat_manifest()}")
    if requested_chat_text:
        current_turn_parts.append(
            f"【使用者要求调取的历史 AI 对话原文（非真实咨询）】\n{requested_chat_text}"
        )
    if ref["overflow"]:
        current_turn_parts.append(
            f"【提示】使用者要求调取的次数超过 {session_resolver.MAX_FULL_SESSIONS} 次，"
            "只完整调取了最近几次的原文，其余请依据上面的清单 / 长期记忆 / 对话记忆概述，并可主动说明。"
        )
    if chat_graph_text:
        current_turn_parts.append(f"【心智地图（AI对话记忆部分，可能含与真实咨询的呼应关系）】\n{chat_graph_text}")
    if matched_nodes:
        matched_labels = "、".join(f"{_GRAPH_TYPE_LABEL.get(n['type'], n['type'])}:{n['label']}" for n in matched_nodes)
        hint = f"【提示】这个问题和心智地图里的以下节点高度相关，已针对性补充了相关片段：{matched_labels}"
        if hop_edges:
            # 可解释性：把"沿哪条关系边跳到了哪个深层概念"显性告诉 LLM，让它能顺着因果链回答，
            # 而不是只看到一堆片段。这正是 GraphRAG 相对纯向量检索的核心价值——连点成线。
            paths = "；".join(_format_hop_edge(e, label_lookup) for e in hop_edges)
            hint += f"\n并沿心智地图的关系边追溯到相关的深层概念（已一并检索）：{paths}"
        current_turn_parts.append(hint)
        # 局部子图（GraphRAG local）：命中锚点 + 其 k-hop 邻域，动态拼进当轮，提供针对本问题的
        # 细节结构（骨干已在缓存里给了全局，这里去掉重复、只补局部）。
        local_nodes, local_edges = _local_subgraph(matched_nodes, graph, exclude_ids=backbone_ids)
        if local_nodes:
            current_turn_parts.append(
                "【心智地图·与本问题相关的局部关系网（命中概念及其邻域）】\n"
                f"{_format_graph_context(local_nodes, local_edges, label_lookup)}"
            )
    if full_day_blocks:
        current_turn_parts.append(f"【使用者提到日期的完整逐字稿】\n{full_day_block_text}")
    if graph_evidence_summaries or graph_evidence_windows:
        # 心智地图关联到的关键证据日：不再整份逐字稿，而是"本场结构化摘要（整场覆盖）+ 该场里
        # 与命中概念最相关的定向片段（逐字证据）"，追溯来龙去脉的同时省下大量 token。
        ge_parts = [
            "（这几天是上面命中的核心图式/应对模式在图上标注的关键证据日期；已取出本场摘要与"
            "最相关的片段以便追溯来龙去脉，而不是整份逐字稿。）"
        ]
        for s in graph_evidence_summaries:
            ge_parts.append(f"[{s['date']} 本场摘要]\n{s['text']}")
        if graph_evidence_windows:
            ge_parts.append(_format_retrieved(graph_evidence_windows))
        current_turn_parts.append("【心智地图关联到的关键咨询证据（本场摘要 + 相关片段）】\n" + "\n\n".join(ge_parts))
    unmatched = [d for d in mentioned_dates if d not in covered_dates]
    if unmatched:
        current_turn_parts.append(f"【提示】使用者提到了以下日期，但语料库中没有找到对应的咨询记录：{', '.join(unmatched)}")
    current_turn_parts.append(f"【检索到的相关历史咨询片段】\n{retrieved_block}")
    current_turn_parts.append(f"【使用者当前问题】\n{question}")
    current_turn = "\n\n".join(current_turn_parts)

    # 为历史回放准备精简版内容（检索片段 + 问题，不含静态/共享动态）
    history_content = f"【检索到的相关历史咨询片段】\n{retrieved_block}\n\n【使用者当前问题】\n{question}"

    # 估算 context 并自动压缩（避免超过 500K 上限）
    compression_info = None
    if history and not use_full_history:
        # 粗略估算 context 大小
        estimated_size = len(current_turn)
        for turn in history:
            if turn.get("role") == "user":
                if turn.get("compressed"):
                    estimated_size += len(turn.get("content", ""))
                else:
                    estimated_size += len(turn.get("history_content", turn.get("content", "")))
            else:
                estimated_size += len(turn.get("content", ""))

        # 超过阈值时迭代压缩，直到低于上限
        if estimated_size > max_context:
            compressed_count = 0
            llm_compressed_count = 0
            original_size = estimated_size

            # 阶段 1：压缩 User 消息（丢弃检索片段，免费快速）
            while estimated_size > max_context:
                # 找出所有未压缩的用户消息
                uncompressed = [i for i, turn in enumerate(history)
                               if turn.get("role") == "user" and not turn.get("compressed")]
                if not uncompressed:
                    break  # User 消息全部已压缩，进入阶段 2

                # 压缩最旧的 1/3（至少 1 条）
                batch_size = max(1, len(uncompressed) // 3)
                for i in uncompressed[:batch_size]:
                    history[i]["compressed"] = True
                    compressed_count += 1

                # 重新估算
                estimated_size = len(current_turn)
                for turn in history:
                    if turn.get("role") == "user":
                        estimated_size += len(turn.get("content", "")) if turn.get("compressed") else len(turn.get("history_content", turn.get("content", "")))
                    else:
                        # Assistant 回答：优先用压缩版（如果已压缩），否则用完整版
                        estimated_size += len(turn.get("compressed_content", turn.get("content", "")))

            # 阶段 2：如果 User 消息全部压缩后仍超限，压缩 Assistant 回答（调用 LLM）
            if estimated_size > max_context:
                # 找出所有未压缩的 assistant 消息（优先压缩最旧的长回答）
                uncompressed_assistant = [
                    (i, len(turn.get("content", "")))
                    for i, turn in enumerate(history)
                    if turn.get("role") == "assistant" and not turn.get("compressed")
                ]
                # 按长度降序排序（先压缩最长的）
                uncompressed_assistant.sort(key=lambda x: -x[1])

                # 逐个压缩直到低于阈值
                for i, original_len in uncompressed_assistant:
                    if estimated_size <= max_context:
                        break  # 已达标，停止压缩

                    original_answer = history[i]["content"]
                    # 只压缩 > 3K 的回答（短回答保留完整）
                    if original_len > 3000:
                        try:
                            # 调用 LLM 压缩（目标：压缩到原长度的 40%）
                            target_chars = max(1000, int(original_len * 0.4))
                            compressed = ask_llm(
                                [{"role": "user", "parts": [{"text":
                                    f"请将以下 AI 助手的回答压缩成约 {target_chars} 字符，保留关键信息和结论：\n\n{original_answer}"
                                }]}],
                                system_instruction="你是一个文本压缩助手，擅长提炼要点。只输出压缩后的文本，不要额外解释。",
                            ).text

                            history[i]["compressed_content"] = compressed
                            history[i]["compressed"] = True
                            llm_compressed_count += 1

                            # 重新估算
                            estimated_size = len(current_turn)
                            for turn in history:
                                if turn.get("role") == "user":
                                    estimated_size += len(turn.get("content", "")) if turn.get("compressed") else len(turn.get("history_content", turn.get("content", "")))
                                else:
                                    estimated_size += len(turn.get("compressed_content", turn.get("content", "")))

                        except Exception as e:
                            # LLM 压缩失败，跳过这条（保留原文）
                            print(f"警告：压缩失败 {e}")
                            continue

            if compressed_count > 0 or llm_compressed_count > 0:
                compression_info = {
                    "triggered": True,
                    "compressed_turns": compressed_count,
                    "llm_compressed_turns": llm_compressed_count,
                    "before": original_size,
                    "after": estimated_size,
                }

    # 组装历史对话
    contents = []
    for turn in history or []:
        role = "model" if turn.get("role") in ("assistant", "model") else "user"

        if role == "user":
            if use_full_history:
                # 深度模式：使用完整 api_content
                text = turn.get("api_content", turn.get("content", ""))
            elif turn.get("compressed"):
                # 已压缩：只用原始问题（不含检索片段）
                text = turn.get("content", "")
            else:
                # 正常：检索片段 + 问题
                text = turn.get("history_content", turn.get("content", ""))
        else:
            # Assistant 回答：优先使用 LLM 压缩版（如果存在），否则用完整版
            if turn.get("compressed") and turn.get("compressed_content"):
                text = turn.get("compressed_content", "")
            else:
                text = turn.get("content", "")

        contents.append({"role": role, "parts": [{"text": text}]})
    contents.append({"role": "user", "parts": [{"text": current_turn}]})

    resp = ask_llm(
        contents,
        system_instruction=None if cache_name else system_instruction,
        cached_content=cache_name,
    )

    sources = [
        {
            "session_date": b["session_date"],
            "source_file": b["source_file"],
            "start_ts": b["start_ts"],
            "end_ts": b["end_ts"],
            "full_transcript": True,
        }
        for b in full_day_blocks
    ] + [
        {
            "session_date": w["session_date"],
            "source_file": w["source_file"],
            "start_ts": w["start_ts"],
            "end_ts": w["end_ts"],
            "full_transcript": False,
            "via_graph_evidence": True,
        }
        for w in graph_evidence_windows
    ] + [
        {
            "session_date": w["session_date"],
            "source_file": w["source_file"],
            "start_ts": w["start_ts"],
            "end_ts": w["end_ts"],
            "full_transcript": False,
        }
        for w in windows
    ]

    usage = resp.usage_metadata
    return {
        "answer": resp.text,
        "sources": sources,
        "input_tokens": usage.prompt_token_count,  # 向后兼容旧字段名
        "token_usage": {
            "input": usage.prompt_token_count or 0,
            "output": usage.candidates_token_count or 0,
            "thinking": usage.thoughts_token_count or 0,
            "cached": usage.cached_content_token_count or 0,
            "total": usage.total_token_count or 0,
        },
        "matched_graph_nodes": [{"type": n["type"], "label": n["label"]} for n in matched_nodes],
        "api_content": current_turn,  # 完整内容（供显示/调试）
        "history_content": history_content,  # 精简版（检索片段+问题，供历史回放，省 token）
        "compression_info": compression_info,  # 压缩信息（如果触发了压缩）
    }


if __name__ == "__main__":
    import sys

    query = sys.argv[1] if len(sys.argv) > 1 else "我哪一次谈到了买游戏电脑的纠结？"

    if "--retrieve-only" in sys.argv:
        results = retrieve(query)
        print(f"查询: {query}")
        print(f"共 {len(results)} 个窗口（已做父块扩展+合并）\n")
        for w in results:
            print(f"--- {w['session_date']} | {w['source_file']} | chunk {w['chunk_index_range']} | {w['start_ts']}-{w['end_ts']} ---")
            print(w["text"][:500])
            print()
    else:
        result = answer(query)
        print(f"问: {query}\n")
        print(f"答: {result['answer']}\n")
        u = result["token_usage"]
        print(f"token 使用：input={u['input']} output={u['output']} thinking={u['thinking']} cached={u['cached']} total={u['total']}")
        print("来源:")
        for s in result["sources"]:
            print(f"  - {s['session_date']} | {s['source_file']} | {s['start_ts']}-{s['end_ts']}")
